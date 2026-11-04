"""Rules for picking each member's next nudge and send hour.

Plain pandas, shared by the daily picker (pick_nudges.py) and the backtest (backtest_picker.py), so both
run exactly the same rules. Nothing here reads the generator's truth.

The rules, in order:
1. Hold members who were active in the last 2 days. The nudge test only covered members away 2+ days.
2. Hold members nudged in the last 72 hours (the test's cooldown).
3. Hold low lapse-risk members. Most are back within the week anyway, so a 72-hour comeback is worth little.
4. For each nudge the member is eligible for, estimate its uplift from the nudge test, in the segment
   where that nudge's effect varies most, and its opt-out risk from the member's recent pushes.
   Keep nudges whose uplift is clearly above zero and whose net value is positive:
       net value = uplift - value_of_reach x opt-out risk
5. Send the nudge with the highest net value at the member's usual session hour. Otherwise hold.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from scipy.stats import chi2

NUDGES = ["new_likes", "match_waiting", "profile_tip", "new_people_nearby", "we_miss_you"]
ALWAYS_ELIGIBLE = {"profile_tip", "new_people_nearby", "we_miss_you"}
# Segment dimensions the test readout is cut by. "all" is the pooled effect.
DIMENSIONS = ["all", "age_band", "tenure_group", "market_size", "days_away_bucket"]
FATIGUE_BUCKETS = [(0, "0-1"), (2, "2-3"), (4, "4+")]  # pushes in the 7 days before


@dataclass
class Rules:
    value_of_reach: float = 2.0  # one opt-out costs as much as this many comebacks. An estimate, not measured
    z: float = 1.645  # uplift must be above zero at 95% one-sided confidence
    min_holdout: int = 200  # smallest holdout a segment needs before its estimate is used
    max_p_value: float = 0.05  # a segment dimension is used only if effects really differ across it
    hold_low_risk: bool = True
    active_days: float = 2.0
    cooldown_hours: float = 72.0
    min_hour_sessions: int = 3  # fewer sessions than this and the send hour falls back to the default
    send_window: tuple = (8, 22)  # local hours a push may go out, inclusive
    version: str = "rules-v1"


def tenure_group(days):
    return np.where(np.asarray(days) < 30, "first 30 days", "after 30 days")


def days_away_bucket(days):
    """Same buckets as hearth_silver.notification_funnel."""
    d = np.asarray(days, dtype=float)
    return np.select([d < 2, d < 3, d < 8, d < 15], ["active", "2 days", "3-7 days", "8-14 days"], "15+ days")


def fatigue_bucket(pushes_7d):
    p = np.asarray(pushes_7d)
    return np.select([p >= 4, p >= 2], ["4+", "2-3"], "0-1")


def add_segments(df):
    out = df.copy()
    out["all"] = "all"
    out["tenure_group"] = tenure_group(out["tenure_days"])
    if "days_away_bucket" not in out:
        out["days_away_bucket"] = days_away_bucket(out["days_away"])
    return out


def segment_effects(outcomes, rules=Rules()):
    """The nudge test readout: treated against holdout for each nudge, cut by each dimension.

    outcomes needs nudge_type, treated, returned_72h, opted_out, tenure_days, age_band, market_size and
    days_away_bucket, one row per test decision with a complete 72-hour window.
    """
    o = add_segments(outcomes)
    o["returned_72h"] = o["returned_72h"].astype(float)
    o["opted_out"] = o["opted_out"].astype(float)
    parts = []
    for dim in DIMENSIONS:
        keys = ["nudge_type", dim]
        t = o[o["treated"]].groupby(keys).agg(
            n_treated=("returned_72h", "size"), return_treated=("returned_72h", "mean"),
            opt_out_treated=("opted_out", "mean"))
        h = o[~o["treated"]].groupby(keys).agg(n_holdout=("returned_72h", "size"), return_holdout=("returned_72h", "mean"))
        parts.append(t.join(h, how="inner").reset_index().rename(columns={dim: "segment"}).assign(dimension=dim))
    e = pd.concat(parts, ignore_index=True)
    e = e[e["n_holdout"] >= rules.min_holdout].copy()
    e["uplift"] = e["return_treated"] - e["return_holdout"]
    e["se"] = np.sqrt(e["return_treated"] * (1 - e["return_treated"]) / e["n_treated"]
                      + e["return_holdout"] * (1 - e["return_holdout"]) / e["n_holdout"])
    e["uplift_low"] = e["uplift"] - rules.z * e["se"]
    e["opt_out_ratio"] = e["opt_out_treated"] / o.loc[o["treated"], "opted_out"].mean()
    return e[["nudge_type", "dimension", "segment", "n_treated", "n_holdout", "return_treated", "return_holdout",
              "uplift", "se", "uplift_low", "opt_out_treated", "opt_out_ratio"]].reset_index(drop=True)


def pick_dimensions(effects, rules=Rules()):
    """For each nudge, the dimension its effect varies most across: the smallest p-value of a standard
    heterogeneity test (Cochran's Q). Falls back to the pooled effect when no dimension clears max_p_value."""
    rows = []
    for (nudge, dim), g in effects[effects["dimension"] != "all"].groupby(["nudge_type", "dimension"]):
        w = 1 / g["se"] ** 2
        pooled = (w * g["uplift"]).sum() / w.sum()
        q = float((w * (g["uplift"] - pooled) ** 2).sum())
        df = len(g) - 1
        rows.append({"nudge_type": nudge, "dimension": dim, "segments": len(g), "q": q,
                     "p_value": float(chi2.sf(q, df)) if df > 0 else 1.0})
    tests = pd.DataFrame(rows)
    chosen = {}
    for nudge in NUDGES:
        t = tests[tests["nudge_type"] == nudge].sort_values("p_value")
        chosen[nudge] = t["dimension"].iloc[0] if len(t) and t["p_value"].iloc[0] <= rules.max_p_value else "all"
    return chosen, tests


def fatigue_rates(deliveries):
    """Opt-out rate per delivered nudge by pushes in the 7 days before. deliveries: pushes_7d, opted_out."""
    d = deliveries.assign(bucket=fatigue_bucket(deliveries["pushes_7d"]), opted_out=deliveries["opted_out"].astype(float))
    return d.groupby("bucket")["opted_out"].mean().to_dict()


def preferred_hours(hour_counts, rules=Rules()):
    """Each member's usual session hour, inside the send window. hour_counts: member_id, hour, sessions.

    Counts are smoothed over neighbouring hours, so a member who opens the app between 21:00 and 23:00
    gets 22:00, not a night send at 23:00. Members with too few sessions get the most common hour overall.
    """
    pivot = hour_counts.pivot_table(index="member_id", columns="hour", values="sessions", aggfunc="sum", fill_value=0)
    pivot = pivot.reindex(columns=range(24), fill_value=0)
    counts = pivot.to_numpy()
    smooth = counts + np.roll(counts, 1, axis=1) + np.roll(counts, -1, axis=1)
    lo, hi = rules.send_window
    inside = (np.arange(24) >= lo) & (np.arange(24) <= hi)
    default = int(np.where(inside, smooth.sum(axis=0), -1).argmax())
    own = np.where(inside, smooth, -1).argmax(axis=1)
    enough = counts.sum(axis=1) >= rules.min_hour_sessions
    out = pd.DataFrame({
        "member_id": pivot.index,
        "preferred_hour_local": np.where(enough, own, default),
        "hour_sessions": counts.sum(axis=1),
        "hour_source": np.where(enough, "own sessions", "default"),
    })
    return out, default


def send_times(decision_date, hours_local, timezones):
    """UTC send time for a local hour on decision_date in each member's timezone."""
    lookup = {}
    for tz, h in set(zip(timezones, hours_local)):
        local = datetime(decision_date.year, decision_date.month, decision_date.day, int(h), tzinfo=ZoneInfo(tz))
        lookup[(tz, h)] = local.astimezone(timezone.utc).replace(tzinfo=None)
    return [lookup[(tz, h)] for tz, h in zip(timezones, hours_local)]


def decide(members, effects, chosen, fatigue, rules=Rules()):
    """One decision per member. members needs member_id, tenure_days, age_band, market_size, days_away,
    hours_since_last_nudge (NaN if none), risk_band (NaN if unscored), pushes_7d, eligible_new_likes,
    eligible_match_waiting and preferred_hour_local."""
    m = add_segments(members.reset_index(drop=True))
    base_risk = pd.Series(fatigue_bucket(m["pushes_7d"])).map(fatigue).to_numpy()

    nets, uplifts, lows, risks, segments, oks, eligible = [], [], [], [], [], [], []
    for nudge in NUDGES:
        dim = chosen[nudge]
        cells = effects[(effects["nudge_type"] == nudge) & (effects["dimension"] == dim)].set_index("segment")
        seg = m[dim].astype(str)
        uplift = seg.map(cells["uplift"]).to_numpy(dtype=float)
        low = seg.map(cells["uplift_low"]).to_numpy(dtype=float)
        risk = base_risk * seg.map(cells["opt_out_ratio"]).to_numpy(dtype=float)
        net = uplift - rules.value_of_reach * risk
        elig = np.ones(len(m), bool) if nudge in ALWAYS_ELIGIBLE else m[f"eligible_{nudge}"].fillna(False).to_numpy(bool)
        ok = elig & ~np.isnan(uplift) & (low > 0) & (net > 0)
        for lst, v in [(nets, np.where(ok, net, -np.inf)), (uplifts, uplift), (lows, low), (risks, risk), (oks, ok),
                       (eligible, elig)]:
            lst.append(v)
        segments.append(np.where(dim == "all", "all", dim + "=" + seg.to_numpy()))

    nets = np.column_stack(nets)
    best = nets.argmax(axis=1)
    rows = np.arange(len(m))
    pick = lambda arrs: np.column_stack(arrs)[rows, best]
    any_ok = np.column_stack(oks).any(axis=1)

    reason = np.select(
        [m["days_away"].to_numpy() < rules.active_days,
         m["hours_since_last_nudge"].fillna(np.inf).to_numpy() < rules.cooldown_hours,
         rules.hold_low_risk & (m["risk_band"].to_numpy() == "low"),
         ~any_ok],
        ["active", "cooldown", "likely_back", "not_worth_it"],
        "",
    )
    send = reason == ""
    elig_list = [",".join(n for n, e in zip(NUDGES, row) if e) for row in np.column_stack(eligible)]
    return pd.DataFrame({
        "member_id": m["member_id"],
        "action": np.where(send, "send", "hold"),
        "hold_reason": np.where(send, None, reason),
        "nudge_type": np.where(send, np.array(NUDGES, dtype=object)[best], None),
        "segment": np.where(send, pick(segments), None),
        "estimated_uplift_pts": np.where(send, np.round(100 * pick(uplifts), 2), np.nan),
        "uplift_low_pts": np.where(send, np.round(100 * pick(lows), 2), np.nan),
        "opt_out_risk_pct": np.where(send, np.round(100 * pick(risks), 3), np.nan),
        "net_value_pts": np.where(send, np.round(100 * nets[rows, best], 2), np.nan),
        "send_hour_local": np.where(send, m["preferred_hour_local"], np.nan),
        "eligible_nudges": elig_list,
        "days_away": m["days_away"].round(2),
        "days_away_bucket": m["days_away_bucket"],
        "pushes_7d": m["pushes_7d"],
        "lapse_risk": m.get("lapse_risk"),
        "risk_band": m["risk_band"],
    })
