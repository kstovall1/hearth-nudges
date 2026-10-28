"""Member-day simulation in NumPy.

Steps through the window one UTC day at a time. Each day: new signups, natural
sessions, the messaging tool's decisions (blast and nudge test), reactions to
pushes, events received while away, state changes and billing. Every member is
a row in a set of arrays, so each day is a handful of vectorized operations.

The nudge test decides each decision's 72-hour outcome when the decision is
made: natural return with the member's baseline chance, plus the nudge's uplift
if it was sent. Sessions inside the window are then forced to agree. That makes
treatment and holdout comparable by construction, which is what the uplift
model has to recover.
"""

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from hearth_data import config as C

DAY = 86_400
HOUR = 3_600
INF = np.iinfo(np.int64).max

ENGAGED, DECAY, DORMANT = 0, 1, 2
TIMEZONES = sorted({m[2] for m in C.METROS if m[2]} | set(C.OTHER_TIMEZONES))
MARKETS = ["large", "medium", "small"]
# Share of sessions by local hour for the part not tied to the preferred hour
BASE_HOURS = np.array([3, 2, 1, 1, 1, 1, 2, 4, 5, 5, 5, 6, 8, 7, 6, 6, 7, 8, 10, 12, 14, 14, 12, 7], float)
BASE_HOURS /= BASE_HOURS.sum()

MEMBER_STATIC = [
    "signup_s", "age", "gender", "seeking", "metro", "market", "tz", "platform", "plan_start",
    "push_opt_in", "rate", "pref_hour", "match_p", "like_rate",
]
MEMBER_DYNAMIC = [
    "state", "decay_t", "decay_len", "ever_decay", "last_session_s", "push_on", "installed",
    "uninstall_s", "plan", "renew_dom", "trial_end_s", "pending_upgrade_s", "ret_start_s",
    "ret_until_s", "ret_matched", "unseen_likes", "waiting", "recent_matches", "last_test_day",
    "block_start_s", "block_end_s", "forced_s", "forced_src", "push_hist",
]


@dataclass
class Records:
    """Rows produced by the simulation, as lists of column dicts to concatenate."""

    sessions: list = field(default_factory=list)
    away: list = field(default_factory=list)
    decisions: list = field(default_factory=list)
    notifications: list = field(default_factory=list)
    subscriptions: list = field(default_factory=list)
    truth: list = field(default_factory=list)

    def frames(self):
        def cat(parts):
            if not parts:
                return pd.DataFrame()
            return pd.concat([pd.DataFrame(p) for p in parts], ignore_index=True)

        return {name: cat(getattr(self, name)) for name in
                ["sessions", "away", "decisions", "notifications", "subscriptions", "truth"]}


class Simulation:
    def __init__(self, seed, start_date):
        self.rng = np.random.default_rng(seed)
        self.seed = seed
        self.t0 = int(datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc).timestamp())
        self.day = 0  # next day to simulate
        self.next_session = 0
        self.next_decision = 0
        self.next_away = 0
        self.next_notification = 0
        self.next_subscription = 0
        self.m = {}
        self.pending_notifications = {}  # push callbacks after the last simulated day
        self._init_members(C.N_MEMBERS_START, signup_day=None)

    # ---------- members ----------

    def _new_members(self, n, signup_s, state, last_session_s):
        rng = self.rng
        metro = rng.choice(len(C.METROS), n, p=[m[3] for m in C.METROS])
        tz_names = np.array([C.METROS[i][2] or "" for i in metro], dtype=object)
        other = tz_names == ""
        tz_names[other] = rng.choice(C.OTHER_TIMEZONES, other.sum(), p=C.OTHER_TZ_WEIGHTS)
        gender = rng.choice(len(C.GENDERS), n, p=C.GENDER_WEIGHTS)
        seeking = np.array([rng.choice(3, p=C.SEEKING_WEIGHTS[C.GENDERS[g]]) for g in gender])
        groups = rng.choice(len(C.PREF_HOUR_GROUPS), n, p=[g[1] for g in C.PREF_HOUR_GROUPS])
        lo = np.array([C.PREF_HOUR_GROUPS[g][0][0] for g in groups])
        hi = np.array([C.PREF_HOUR_GROUPS[g][0][1] for g in groups])
        age = rng.choice(len(C.AGE_BANDS), n, p=C.AGE_WEIGHTS)
        # Younger members are a little more active
        age_mult = np.array([1.2, 1.1, 1.0, 0.85, 0.7])[age]
        rate = C.SESSION_RATE_MEDIAN * age_mult * rng.lognormal(0, C.SESSION_RATE_SIGMA, n)
        cols = {
            "signup_s": signup_s.astype(np.int64),
            "age": age,
            "gender": gender,
            "seeking": seeking,
            "metro": metro,
            "market": np.array([MARKETS.index(C.METROS[i][1]) for i in metro]),
            "tz": np.array([TIMEZONES.index(t) for t in tz_names]),
            "platform": rng.choice(2, n, p=C.PLATFORM_WEIGHTS),
            "push_opt_in": rng.random(n) < C.PUSH_OPT_IN,
            "rate": np.clip(rate, C.SESSION_RATE_FLOOR, 6.0),
            "pref_hour": rng.integers(lo, hi + 1) % 24,
            "match_p": rng.beta(2, 14, n),
            "like_rate": rng.lognormal(0, 0.6, n),
            "state": state.astype(np.int8),
            "decay_t": np.zeros(n, np.int16),
            "decay_len": rng.integers(C.DECAY_DAYS[0], C.DECAY_DAYS[1] + 1, n).astype(np.int16),
            "ever_decay": state == DECAY,
            "last_session_s": last_session_s.astype(np.int64),
            "uninstall_s": np.full(n, INF, np.int64),
            "trial_end_s": np.full(n, INF, np.int64),
            "pending_upgrade_s": np.full(n, INF, np.int64),
            "ret_start_s": np.full(n, INF, np.int64),
            "ret_until_s": np.full(n, INF, np.int64),
            "ret_matched": np.zeros(n, bool),
            "unseen_likes": np.zeros(n, np.int32),
            "waiting": np.zeros(n, bool),
            "recent_matches": np.zeros(n),
            "last_test_day": np.full(n, -99, np.int32),
            "block_start_s": np.full(n, INF, np.int64),
            "block_end_s": np.zeros(n, np.int64),
            "forced_s": np.full(n, INF, np.int64),
            "forced_src": np.zeros(n, np.int8),
            "push_hist": np.zeros((n, 7), np.int16),
            "renew_dom": rng.integers(1, 29, n).astype(np.int8),
        }
        cols["push_on"] = cols["push_opt_in"].copy()
        cols["installed"] = np.ones(n, bool)
        return cols

    def _init_members(self, n, signup_day):
        rng = self.rng
        # Tenure before the window: a recent cohort plus a long tail
        recent = rng.random(n) < 0.25
        tenure_days = np.where(recent, rng.uniform(1, 90, n), 90 + rng.exponential(320, n))
        signup_s = self.t0 - (tenure_days * DAY).astype(np.int64)
        state = rng.choice(3, n, p=[C.INITIAL_STATE_WEIGHTS[k] for k in ["engaged", "decay", "dormant"]])
        away_days = np.where(
            state == DORMANT,
            2 + rng.lognormal(np.log(12), 0.9, n),
            rng.exponential(1.5, n),
        )
        away_days = np.minimum(away_days, tenure_days)
        cols = self._new_members(n, signup_s, state, self.t0 - (away_days * DAY).astype(np.int64))
        cols["plan_start"] = rng.choice(3, n, p=C.PLAN_WEIGHTS).astype(np.int8)
        cols["plan"] = cols["plan_start"].copy()
        cols["decay_t"] = np.where(state == DECAY, rng.integers(0, 7, n), 0).astype(np.int16)
        cols["unseen_likes"] = np.where(state == DORMANT, rng.poisson(2, n), 0).astype(np.int32)
        cols["waiting"] = (state == DORMANT) & (rng.random(n) < 0.1)
        cols["recent_matches"] = np.where(state == DORMANT, rng.exponential(0.2, n), rng.exponential(1.0, n))
        cols["push_hist"] = np.where(cols["push_opt_in"][:, None],
                                     rng.poisson(C.PUSHES_PER_WEEK_BEFORE / 7, (n, 7)), 0).astype(np.int16)
        self.m = cols
        self.initial_last_s = cols["last_session_s"].copy()

    def _add_signups(self, t0, weekend):
        rng = self.rng
        n = rng.poisson(C.SIGNUPS_PER_DAY * (1.25 if weekend else 0.9))
        signup_s = t0 + np.sort(self._sample_hours(n, rng.integers(0, len(TIMEZONES), n), None))
        cols = self._new_members(n, signup_s, np.full(n, ENGAGED), signup_s)
        cols["plan_start"] = np.zeros(n, np.int8)
        cols["plan"] = np.zeros(n, np.int8)
        cols["forced_s"] = signup_s.astype(np.int64)  # the signup session
        start = len(self.m["signup_s"])
        for k, v in cols.items():
            self.m[k] = np.concatenate([self.m[k], v])
        self.initial_last_s = np.concatenate([self.initial_last_s, signup_s.astype(np.int64)])
        return np.arange(start, start + n)

    # ---------- time helpers ----------

    def _offsets(self, day):
        """UTC offset in seconds for each timezone on this day, daylight time included."""
        noon = datetime.fromtimestamp(self.t0 + day * DAY + 12 * HOUR, tz=timezone.utc)
        return np.array([int(noon.astimezone(ZoneInfo(t)).utcoffset().total_seconds()) for t in TIMEZONES])

    def _sample_hours(self, n, tz_idx, pref):
        """Seconds into the UTC day for n events, following each member's local hours."""
        rng = self.rng
        if pref is None:
            h = rng.choice(24, n, p=BASE_HOURS)
        else:
            near = rng.random(n) < C.SESSION_NEAR_PREF
            h = np.where(near, (pref + np.rint(rng.normal(0, 1.2, n))) % 24, rng.choice(24, n, p=BASE_HOURS))
        local = h * HOUR + rng.integers(0, HOUR, n)
        return ((local - self._day_offsets[tz_idx]) % DAY).astype(np.int64)

    def _local_hour(self, ts, idx):
        return ((ts + self._day_offsets[self.m["tz"][idx]]) // HOUR) % 24

    def _hour_factor(self, local_h, idx):
        diff = np.abs(local_h - self.m["pref_hour"][idx])
        diff = np.minimum(diff, 24 - diff)
        night = (local_h >= 23) | (local_h < 7)
        return np.where(diff <= 2, C.HOUR_FACTOR_NEAR, np.where(night, C.HOUR_FACTOR_NIGHT, C.HOUR_FACTOR_DAY))

    # ---------- behavior ----------

    def _session_rate(self, idx, weekend):
        m = self.m
        lam = m["rate"][idx] * np.where(m["plan"][idx] > 0, C.PAID_ACTIVITY_MULT, 1.0)
        lam = lam * (C.WEEKEND_MULT if weekend else 1.0)
        decay = 0.5 ** (m["decay_t"][idx] / C.DECAY_HALF_LIFE)
        return np.where(m["state"][idx] == ENGAGED, lam, np.where(m["state"][idx] == DECAY, lam * decay, 0.0))

    @staticmethod
    def _dormant_return(days_away):
        h = np.zeros_like(days_away, dtype=float)
        for min_days, p in C.DORMANT_RETURN:
            h = np.where(days_away >= min_days, p, h)
        return h

    def _baseline_72h(self, idx, days_away, weekend):
        """Chance of a session in the next 72 hours with no push."""
        lam = self._session_rate(idx, weekend)
        dormant = self.m["state"][idx] == DORMANT
        return np.where(dormant, 1 - (1 - self._dormant_return(days_away)) ** 3, 1 - np.exp(-3 * lam))

    def _effect(self, nudge, idx, days_away, t):
        """True uplift of a nudge for these members at their preferred hour."""
        m = self.m
        if nudge == "match_waiting":
            return np.full(len(idx), C.EFFECT_MATCH_WAITING)
        if nudge == "new_likes":
            return np.array([C.EFFECT_NEW_LIKES[b] for b in C.AGE_BANDS])[m["age"][idx]]
        if nudge == "profile_tip":
            new = t - m["signup_s"][idx] < 30 * DAY
            return np.where(new, C.EFFECT_PROFILE_TIP_NEW, C.EFFECT_PROFILE_TIP_OTHER)
        if nudge == "new_people_nearby":
            return np.array([C.EFFECT_NEARBY[k] for k in MARKETS])[m["market"][idx]]
        return self._wmy(days_away)[0]

    @staticmethod
    def _wmy(days_away):
        effect = np.zeros(len(days_away))
        opt_out = np.ones(len(days_away))
        for min_days, e, o in C.EFFECT_WE_MISS_YOU:
            hit = days_away >= min_days
            effect = np.where(hit, e, effect)
            opt_out = np.where(hit, o, opt_out)
        return effect, opt_out

    def _opt_out_mult(self, nudge, idx, days_away, t):
        m = self.m
        if nudge == "profile_tip":
            new = t - m["signup_s"][idx] < 30 * DAY
            return np.where(new, 1.0, C.PROFILE_TIP_OTHER_OPT_OUT)
        if nudge == "new_people_nearby":
            return np.where(m["market"][idx] == 2, C.NEARBY_SMALL_OPT_OUT, 1.0)
        if nudge == "we_miss_you":
            return self._wmy(days_away)[1]
        return np.ones(len(idx))

    # ---------- pushes ----------

    def _send(self, idx, decision_ids, send_s, nudge_mult, rec):
        """Deliver pushes, then draw opt-outs and uninstalls. Returns delivered mask, fatigue and hour factor."""
        rng, m = self.rng, self.m
        n = len(idx)
        delivered = rng.random(n) < C.DELIVERY_RATE
        self._today_pushes[idx] += delivered
        k = m["push_hist"][idx].sum(axis=1) + self._today_pushes[idx]  # pushes in 7 days, this one included
        fatigue = C.FATIGUE_OPEN_DECAY ** np.maximum(k - 1, 0)
        local_h = self._local_hour(send_s, idx)
        hour_f = self._hour_factor(local_h, idx)
        night = (local_h >= 23) | (local_h < 7)
        mult = np.ones(n)
        for min_k, f in C.OPT_OUT_FATIGUE[::-1]:
            mult = np.where(k >= min_k, f, mult)
        p_react = C.OPT_OUT_BASE * mult * np.where(night, C.OPT_OUT_NIGHT_MULT, 1.0) * nudge_mult
        react = delivered & (rng.random(n) < p_react)
        uninstall = react & (rng.random(n) < C.UNINSTALL_SHARE)
        deliver_s = send_s + rng.integers(1, 30, n)
        react_s = deliver_s + rng.exponential(40 * 60, n).astype(np.int64)
        rec.notifications.append({
            "decision_idx": decision_ids[delivered], "member_idx": idx[delivered],
            "ts": deliver_s[delivered], "event_type": "delivered",
        })
        rec.notifications.append({
            "decision_idx": decision_ids[react], "member_idx": idx[react], "ts": react_s[react],
            "event_type": np.where(uninstall[react], "uninstalled", "push_disabled"),
        })
        m["push_on"][idx[react]] = False
        un = idx[uninstall]
        m["installed"][un] = False
        m["uninstall_s"][un] = react_s[uninstall]
        late = m["forced_s"][un] > m["uninstall_s"][un]
        m["forced_s"][un[late]] = INF
        return delivered, deliver_s, fatigue, hour_f

    def _record_opens(self, idx, decision_ids, open_s, rec):
        rec.notifications.append({
            "decision_idx": decision_ids, "member_idx": idx, "ts": open_s, "event_type": "opened",
        })

    def _decision_rows(self, idx, decided_s, campaign_type, campaign_id, nudges, holdout, send_s, rec):
        rng, m = self.rng, self.m
        n = len(idx)
        ids = np.arange(self.next_decision, self.next_decision + n)
        self.next_decision += n
        variant = rng.integers(0, 2, n)
        copy = [
            C.COPY[nu][v].format(n=max(int(m["unseen_likes"][i]), 1),
                                 metro=C.METROS[m["metro"][i]][0] if C.METROS[m["metro"][i]][0] != "Other" else "your area")
            for nu, v, i in zip(nudges, variant, idx)
        ]
        rec.decisions.append({
            "decision_idx": ids, "member_idx": idx, "decided_s": np.full(n, decided_s, np.int64),
            "campaign_type": campaign_type, "campaign_id": campaign_id, "nudge_type": nudges,
            "arm": np.where(holdout, "holdout", "treatment"),
            "send_s": np.where(holdout, -1, send_s),
            "copy_variant_id": [f"{nu}_v{v + 1}" for nu, v in zip(nudges, variant)],
            "copy_text": copy,
        })
        return ids

    def _blast(self, t0, day, last_s, weekend, rec):
        rng, m = self.rng, self.m
        idx = np.flatnonzero(m["installed"] & m["push_on"] & (m["signup_s"] < t0))
        if len(idx) == 0:
            return
        decided_s = t0 + C.DECISION_UTC_HOUR * HOUR
        send_s = np.full(len(idx), t0 + C.BLAST_UTC_HOUR * HOUR, np.int64) + rng.integers(0, 600, len(idx))
        nudges = np.full(len(idx), "we_miss_you", dtype=object)
        date = datetime.fromtimestamp(t0, tz=timezone.utc)
        ids = self._decision_rows(idx, decided_s, "blast", f"blast_{date:%Y%m%d}", nudges,
                                  np.zeros(len(idx), bool), send_s, rec)
        days_away = (send_s - last_s[idx]) / DAY
        effect, opt_mult = self._wmy(days_away)
        delivered, deliver_s, fatigue, hour_f = self._send(idx, ids, send_s, opt_mult, rec)
        open_s = deliver_s + rng.exponential(15 * 60, len(idx)).astype(np.int64)
        free = m["block_end_s"][idx] <= send_s  # the nudge test outcome wins inside its window
        active = days_away < C.ELIGIBLE_DAYS_AWAY
        p0 = self._baseline_72h(idx, days_away, weekend)
        lift = np.clip(effect * hour_f / np.maximum(1 - p0, 1e-6), 0, 1)
        induced = delivered & free & ~active & m["installed"][idx] & (rng.random(len(idx)) < lift)
        opened_active = delivered & free & active & m["installed"][idx] & (
            rng.random(len(idx)) < C.OPEN_RATE_ACTIVE * hour_f * fatigue)
        opened = induced | opened_active
        self._record_opens(idx[opened], ids[opened], open_s[opened], rec)
        # An open starts a session from the push
        go = idx[opened]
        earlier = open_s[opened] + 3 < m["forced_s"][go]
        m["forced_s"][go[earlier]] = open_s[opened][earlier] + 3
        m["forced_src"][go[earlier]] = 1

    def _nudge_test(self, t0, day, last_s, weekend, rec):
        rng, m = self.rng, self.m
        decided_s = t0 + C.DECISION_UTC_HOUR * HOUR
        days_away_all = (decided_s - last_s) / DAY
        idx = np.flatnonzero(
            m["installed"] & m["push_on"] & (m["signup_s"] < decided_s)
            & (days_away_all >= C.ELIGIBLE_DAYS_AWAY)
            & (day - m["last_test_day"] >= C.TEST_COOLDOWN_DAYS)
            & (m["block_end_s"] <= decided_s)
        )
        n = len(idx)
        if n == 0:
            return
        m["last_test_day"][idx] = day
        days_away = days_away_all[idx]
        eligible = np.column_stack([
            m["unseen_likes"][idx] > 0, m["waiting"][idx], np.ones(n, bool), np.ones(n, bool), np.ones(n, bool),
        ])
        keys = np.where(eligible, rng.random((n, 5)), -1.0)
        choice = keys.argmax(axis=1)
        nudges = np.array(C.NUDGES, dtype=object)[choice]
        holdout = rng.random(n) < C.TEST_HOLDOUT
        local_h = rng.integers(C.TEST_SEND_HOURS[0], C.TEST_SEND_HOURS[1] + 1, n)
        send_s = t0 + local_h * HOUR + rng.integers(0, HOUR, n) - self._day_offsets[m["tz"][idx]]
        ids = self._decision_rows(idx, decided_s, "nudge_test", "nudge_test_v1", nudges, holdout, send_s, rec)

        effects = np.column_stack([self._effect(nu, idx, days_away, decided_s) for nu in C.NUDGES])
        effect = effects[np.arange(n), choice]
        opt_mult = np.ones(n)
        for j, nu in enumerate(C.NUDGES):
            pick = choice == j
            opt_mult[pick] = self._opt_out_mult(nu, idx[pick], days_away[pick], decided_s)
        p0 = self._baseline_72h(idx, days_away, weekend)
        rec.truth.append({
            "decision_idx": ids, "member_idx": idx, "baseline_return_72h": p0,
            **{f"true_uplift_{nu}": np.where(eligible[:, j], effects[:, j], np.nan) for j, nu in enumerate(C.NUDGES)},
        })

        treat = idx[~holdout]
        delivered = np.zeros(n, bool)
        deliver_s = send_s.copy()
        fatigue = np.ones(n)
        hour_f = np.ones(n)
        if len(treat):
            d, ds, f, h = self._send(treat, ids[~holdout], send_s[~holdout], opt_mult[~holdout], rec)
            delivered[~holdout], deliver_s[~holdout], fatigue[~holdout], hour_f[~holdout] = d, ds, f, h

        # Outcome over the 72 hours after the decision
        lift = np.where(delivered, effect * hour_f, 0.0)
        lift = np.clip(lift, -p0, 1 - p0)
        natural = rng.random(n) < np.where(lift < 0, p0 + lift, p0)
        induced = ~natural & (lift > 0) & (rng.random(n) < lift / np.maximum(1 - p0, 1e-6))
        open_s = deliver_s + rng.exponential(15 * 60, n).astype(np.int64)
        via_push = induced | (natural & delivered & (rng.random(n) < C.OPEN_RATE_RETURNER * hour_f * fatigue))
        # A natural return happens some time in the window, following the member's hours
        offset_day = rng.integers(0, 3, n)
        natural_s = t0 + offset_day * DAY + self._sample_hours(n, m["tz"][idx], m["pref_hour"][idx])
        natural_s = np.clip(natural_s, decided_s + 60, decided_s + 72 * HOUR - 60)
        returned = natural | induced
        return_s = np.where(via_push, open_s + 3, natural_s)
        self._record_opens(idx[via_push], ids[via_push], open_s[via_push], rec)

        alive = m["installed"][idx]
        m["block_start_s"][idx] = decided_s
        m["block_end_s"][idx] = np.where(returned, return_s, decided_s + 72 * HOUR)
        go = returned & alive & (return_s < m["uninstall_s"][idx])
        m["forced_s"][idx[go]] = return_s[go]
        m["forced_src"][idx[go]] = via_push[go].astype(np.int8)

    # ---------- the day ----------

    def step(self, rec):
        rng, m = self.rng, self.m
        day = self.day
        t0 = self.t0 + day * DAY
        t1 = t0 + DAY
        weekend = datetime.fromtimestamp(t0, tz=timezone.utc).weekday() >= 5
        self._day_offsets = self._offsets(day)
        self._add_signups(t0, weekend)
        n = len(m["signup_s"])
        self._today_pushes = np.zeros(n, np.int16)
        prev_last = m["last_session_s"].copy()

        # Natural sessions
        alive = m["installed"] & (m["signup_s"] < t1)
        lam = self._session_rate(np.arange(n), weekend)
        counts = rng.poisson(lam)
        dormant = m["state"] == DORMANT
        days_away = (t0 - m["last_session_s"]) / DAY
        back = dormant & (rng.random(n) < self._dormant_return(days_away))
        counts[back] = 1 + rng.poisson(m["rate"][back] * 0.5)
        counts[~alive] = 0
        s_idx = np.repeat(np.arange(n), counts)
        s_ts = t0 + self._sample_hours(len(s_idx), m["tz"][s_idx], m["pref_hour"][s_idx])

        def keep(idx, ts):
            blocked = (ts >= m["block_start_s"][idx]) & (ts < m["block_end_s"][idx])
            return ~blocked & (ts < m["uninstall_s"][idx]) & (ts > m["signup_s"][idx])

        ok = keep(s_idx, s_ts)
        s_idx, s_ts = s_idx[ok], s_ts[ok]

        # Sessions before the 07:00 UTC batch count toward eligibility
        decided_s = t0 + C.DECISION_UTC_HOUR * HOUR
        last_s = m["last_session_s"].copy()
        early = s_ts < decided_s
        np.maximum.at(last_s, s_idx[early], s_ts[early])

        if day < C.INCIDENT_DAY:
            blast_today = day % 7 == 0
        else:
            blast_today = (day - C.INCIDENT_DAY) % 7 in (0, 2, 4)
        if blast_today:
            self._blast(t0, day, last_s, weekend, rec)
        self._nudge_test(t0, day, last_s, weekend, rec)

        # Today's decisions can block later sessions, and reactions can uninstall
        ok = keep(s_idx, s_ts)
        s_idx, s_ts = s_idx[ok], s_ts[ok]
        src = np.zeros(len(s_idx), np.int8)
        due = np.flatnonzero((m["forced_s"] >= t0) & (m["forced_s"] < t1))
        s_idx = np.concatenate([s_idx, due])
        s_ts = np.concatenate([s_ts, m["forced_s"][due]])
        src = np.concatenate([src, m["forced_src"][due]])
        m["forced_s"][due] = INF
        order = np.lexsort((s_ts, s_idx))
        s_idx, s_ts, src = s_idx[order], s_ts[order], src[order]
        self._sessions(s_idx, s_ts, src, prev_last, t0, t1, rec)
        self._away_events(s_idx, t0, rec)
        self._transitions(t0, day)
        self._billing(s_idx, s_ts, t0, t1, rec)
        m["push_hist"][:, day % 7] = self._today_pushes
        self.day += 1

    def _sessions(self, s_idx, s_ts, src, prev_last, t0, t1, rec):
        rng, m = self.rng, self.m
        k = len(s_idx)
        duration = np.clip(rng.lognormal(np.log(C.SESSION_MEDIAN_SECONDS), 0.8, k), 20, 5400).astype(np.int64)
        views = rng.poisson(duration / 60 * C.PROFILE_VIEWS_PER_MINUTE)
        likes = rng.binomial(views, 0.35)
        matches = rng.binomial(likes, m["match_p"][s_idx])
        recent = np.minimum(m["recent_matches"][s_idx], 3)
        reply = np.where(m["state"][s_idx] == DECAY, 0.5 ** (m["decay_t"][s_idx] / C.REPLY_HALF_LIFE), 1.0)
        msg_sent = rng.poisson(0.9 * reply * recent + 0.1)
        msg_recv = rng.poisson(0.5 * recent)
        likes_recv = rng.poisson(0.4 * m["like_rate"][s_idx])
        ids = np.arange(self.next_session, self.next_session + k)
        self.next_session += k
        rec.sessions.append({
            "session_idx": ids, "member_idx": s_idx, "start_s": s_ts, "duration_s": duration,
            "source": np.where(src == 1, "push", "organic"), "profile_view": views, "like_sent": likes,
            "match_created": matches, "message_sent": msg_sent, "message_received": msg_recv,
            "like_received": likes_recv,
        })

        # First session of the day after 2+ days away is a return
        first = np.ones(k, bool)
        first[1:] = s_idx[1:] != s_idx[:-1]
        f_idx, f_ts = s_idx[first], s_ts[first]
        is_return = (f_ts - prev_last[f_idx]) >= C.ELIGIBLE_DAYS_AWAY * DAY
        start = is_return & (m["plan"][f_idx] == 0) & (m["ret_until_s"][f_idx] == INF)
        track = f_idx[start]
        m["ret_start_s"][track] = f_ts[start]
        m["ret_until_s"][track] = m["ret_start_s"][track] + 72 * HOUR
        m["ret_matched"][track] = False
        hit = (matches > 0) & (s_ts < m["ret_until_s"][s_idx]) & (s_ts >= m["ret_start_s"][s_idx])
        m["ret_matched"][s_idx[hit]] = True

        last = np.zeros(len(m["signup_s"]), np.int64)
        np.maximum.at(last, s_idx, s_ts)
        seen = np.flatnonzero(last > 0)
        m["last_session_s"][seen] = np.maximum(m["last_session_s"][seen], last[seen])
        back = seen[m["state"][seen] == DORMANT]
        m["state"][back] = ENGAGED
        m["decay_t"][back] = 0
        m["unseen_likes"][seen] = 0
        replied = np.zeros(len(m["signup_s"]), bool)
        replied[s_idx[msg_sent > 0]] = True
        m["waiting"][replied] = False
        np.add.at(m["recent_matches"], s_idx, matches)

    def _away_events(self, s_idx, t0, rec):
        rng, m = self.rng, self.m
        n = len(m["signup_s"])
        had = np.zeros(n, bool)
        had[s_idx] = True
        idx = np.flatnonzero(~had & m["installed"] & (m["signup_s"] < t0))
        days_away = (t0 - m["last_session_s"][idx]) / DAY
        likes = rng.poisson(m["like_rate"][idx] * 0.3 * np.exp(-days_away / 10))
        msgs = rng.poisson(0.25 * np.minimum(m["recent_matches"][idx], 3) * np.exp(-days_away / 5))
        for counts, kind in [(likes, "like_received"), (msgs, "message_received")]:
            e_idx = np.repeat(idx, counts)
            if len(e_idx):
                rec.away.append({
                    "member_idx": e_idx, "ts": t0 + rng.integers(0, DAY, len(e_idx)), "event_type": kind,
                })
        m["unseen_likes"][idx] += likes.astype(np.int32)
        m["waiting"][idx[msgs > 0]] = True

    def _transitions(self, t0, day):
        rng, m = self.rng, self.m
        n = len(m["signup_s"])
        m["recent_matches"] *= np.exp(-1 / 14)
        engaged = m["state"] == ENGAGED
        new = t0 - m["signup_s"] < 14 * DAY
        hazard = np.where(new, C.LAPSE_HAZARD_NEW, C.LAPSE_HAZARD) * np.where(m["plan"] > 0, C.PAID_LAPSE_MULT, 1.0)
        start = engaged & (m["signup_s"] < t0) & (rng.random(n) < hazard)
        m["state"][start] = DECAY
        m["decay_t"][start] = 0
        m["ever_decay"][start] = True
        decaying = (m["state"] == DECAY) & ~start
        m["decay_t"][decaying] += 1
        done = decaying & (m["decay_t"] >= m["decay_len"])
        m["state"][done] = DORMANT

    def _billing(self, s_idx, s_ts, t0, t1, rec):
        rng, m = self.rng, self.m
        n = len(m["signup_s"])
        date = datetime.fromtimestamp(t0, tz=timezone.utc)
        last_dom = (date.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        rows = []

        def add(idx, ts, kind, plan, price):
            if len(idx):
                rows.append({"member_idx": idx, "ts": ts, "event_type": kind, "plan": plan, "price_usd": price})

        plan_names = np.array(C.PLANS, dtype=object)
        prices = np.array([np.nan, C.PRICE_USD["plus"], C.PRICE_USD["premium"]])

        # Monthly renewals for paid members on their billing day
        due_dom = (m["renew_dom"] == date.day) | ((m["renew_dom"] > last_dom.day) & (date.day == last_dom.day))
        due = np.flatnonzero((m["plan"] > 0) & due_dom & (m["signup_s"] < t0) & (m["trial_end_s"] == INF))
        ts = t0 + rng.integers(0, 6 * HOUR, len(due))
        cancel = rng.random(len(due)) < C.MONTHLY_CANCEL
        add(due[cancel], ts[cancel], "cancel", plan_names[m["plan"][due[cancel]]], np.nan)
        add(due[~cancel], ts[~cancel], "renew", plan_names[m["plan"][due[~cancel]]], prices[m["plan"][due[~cancel]]])
        m["plan"][due[cancel]] = 0

        def upgrade(idx, ts):
            plan = np.where(rng.random(len(idx)) < C.UPGRADE_PLUS_SHARE, 1, 2).astype(np.int8)
            add(idx, ts, "upgrade", plan_names[plan], prices[plan])
            m["plan"][idx] = plan
            m["renew_dom"][idx] = min(date.day, 28)

        # Trials that end today
        ending = np.flatnonzero((m["trial_end_s"] >= t0) & (m["trial_end_s"] < t1))
        convert = rng.random(len(ending)) < C.TRIAL_CONVERT
        upgrade(ending[convert], m["trial_end_s"][ending[convert]])
        add(ending[~convert], m["trial_end_s"][ending[~convert]], "cancel", "plus", np.nan)
        m["trial_end_s"][ending] = INF

        # Free members active today: some start a trial, a few upgrade outright
        first = np.ones(len(s_idx), bool)
        first[1:] = s_idx[1:] != s_idx[:-1]
        a_idx, a_ts = s_idx[first], s_ts[first]
        free = (m["plan"][a_idx] == 0) & (m["trial_end_s"][a_idx] == INF)
        trial = free & (rng.random(len(a_idx)) < C.TRIAL_HAZARD)
        add(a_idx[trial], a_ts[trial] + 120, "trial_start", "plus", 0.0)
        m["trial_end_s"][a_idx[trial]] = a_ts[trial] + C.TRIAL_DAYS * DAY
        direct = free & ~trial & (rng.random(len(a_idx)) < C.UPGRADE_ACTIVE_DAILY)
        upgrade(a_idx[direct], a_ts[direct] + 300)

        # Returners whose 72 hours are over: a match in that time makes paying likelier
        over = np.flatnonzero(m["ret_until_s"] < t1)
        p = np.where(m["ret_matched"][over], C.UPGRADE_RETURN_MATCH, C.UPGRADE_RETURN_NO_MATCH)
        win = over[rng.random(len(over)) < p]
        m["pending_upgrade_s"][win] = m["ret_until_s"][win] + rng.integers(0, 11 * DAY, len(win))
        m["ret_until_s"][over] = INF
        m["ret_start_s"][over] = INF

        pend = np.flatnonzero((m["pending_upgrade_s"] >= t0) & (m["pending_upgrade_s"] < t1))
        pend = pend[(m["plan"][pend] == 0) & (m["trial_end_s"][pend] == INF)]
        upgrade(pend, m["pending_upgrade_s"][pend])
        m["pending_upgrade_s"][(m["pending_upgrade_s"] >= t0) & (m["pending_upgrade_s"] < t1)] = INF
        rec.subscriptions.extend(rows)

    # ---------- running ----------

    def run(self, n_days):
        """Simulate the next n_days. Returns the rows for those days as DataFrames."""
        rec = Records()
        for _ in range(n_days):
            self.step(rec)
        frames = rec.frames()
        for name, col, counter in [("away", "away_idx", "next_away"),
                                   ("notifications", "notification_idx", "next_notification"),
                                   ("subscriptions", "subscription_idx", "next_subscription")]:
            start = getattr(self, counter)
            frames[name][col] = np.arange(start, start + len(frames[name]))
            setattr(self, counter, start + len(frames[name]))
        # Push callbacks after the last day wait for the next run, so each day's folder is complete
        notif = pd.concat([pd.DataFrame(self.pending_notifications), frames["notifications"]], ignore_index=True)
        later = notif["ts"] >= self.t0 + self.day * DAY
        self.pending_notifications = notif[later].to_dict("list")
        frames["notifications"] = notif[~later].reset_index(drop=True)
        return frames

    def members(self):
        m = self.m
        n = len(m["signup_s"])
        return pd.DataFrame({
            "member_idx": np.arange(n),
            "signup_s": m["signup_s"],
            "age_band": np.array(C.AGE_BANDS, dtype=object)[m["age"]],
            "gender_identity": np.array(C.GENDERS, dtype=object)[m["gender"]],
            "seeking": np.array(C.SEEKING, dtype=object)[m["seeking"]],
            "metro": np.array([x[0] for x in C.METROS], dtype=object)[m["metro"]],
            "market_size": np.array(MARKETS, dtype=object)[m["market"]],
            "timezone": np.array(TIMEZONES, dtype=object)[m["tz"]],
            "platform": np.array(C.PLATFORMS, dtype=object)[m["platform"]],
            "plan": np.array(C.PLANS, dtype=object)[m["plan_start"]],
            "push_opt_in": m["push_opt_in"],
            "last_active_s": self.initial_last_s,  # as of the export: window start, or signup
            "preferred_hour_local": m["pref_hour"],
            "ever_lapse_path": m["ever_decay"],
        })

    # ---------- state for drip mode ----------

    def save(self, path_prefix):
        arrays = {f"m_{k}": v for k, v in self.m.items()}
        arrays["initial_last_s"] = self.initial_last_s
        pend = {f"p_{k}": np.asarray(v) for k, v in self.pending_notifications.items()}
        # Volumes don't support the seeks a zip write needs, so write locally and copy
        with tempfile.TemporaryDirectory() as tmp:
            np.savez_compressed(f"{tmp}/sim.npz", **arrays, **pend)
            shutil.copyfile(f"{tmp}/sim.npz", f"{path_prefix}.npz")
        meta = {
            "seed": self.seed, "t0": self.t0, "day": self.day, "next_session": self.next_session,
            "next_decision": self.next_decision, "next_away": self.next_away,
            "next_notification": self.next_notification, "next_subscription": self.next_subscription,
            "rng": self.rng.bit_generator.state,
        }
        with open(f"{path_prefix}.json", "w") as f:
            json.dump(meta, f)

    @classmethod
    def load(cls, path_prefix):
        with open(f"{path_prefix}.json") as f:
            meta = json.load(f)
        sim = cls.__new__(cls)
        sim.seed, sim.t0, sim.day = meta["seed"], meta["t0"], meta["day"]
        for k in ["next_session", "next_decision", "next_away", "next_notification", "next_subscription"]:
            setattr(sim, k, meta[k])
        sim.rng = np.random.default_rng()
        sim.rng.bit_generator.state = meta["rng"]
        data = np.load(f"{path_prefix}.npz", allow_pickle=True)
        sim.m = {k[2:]: data[k] for k in data.files if k.startswith("m_")}
        sim.initial_last_s = data["initial_last_s"]
        pend = {k[2:]: data[k] for k in data.files if k.startswith("p_")}
        sim.pending_notifications = pend
        return sim
