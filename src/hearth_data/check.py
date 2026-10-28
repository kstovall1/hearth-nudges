"""Measure the planted patterns from the simulated records.

Works only from the rows the generator writes (sessions, decisions, pushes,
billing), the way the pipeline will, not from the simulation's internals.
Usage: hearth-sim-check [--seed N] [--days N]
"""

import argparse
from datetime import date, timedelta

import numpy as np
import pandas as pd

from hearth_data import config as C
from hearth_data.simulate import DAY, HOUR, Simulation


def with_history(sessions, members):
    """Sessions plus each member's last active time from the members export, as silver will see them."""
    seed = members[["member_idx", "last_active_s"]].rename(columns={"last_active_s": "start_s"})
    return pd.concat([seed, sessions[["member_idx", "start_s"]]], ignore_index=True)


def returned_within_72h(decisions, sessions, members):
    """For each decision, whether the member had a session in the 72 hours after it."""
    s = with_history(sessions, members).sort_values("start_s")
    d = decisions.sort_values("decided_s")
    nxt = pd.merge_asof(d, s.rename(columns={"start_s": "next_s"}), left_on="decided_s", right_on="next_s",
                        by="member_idx", direction="forward", allow_exact_matches=False)
    prev = pd.merge_asof(d[["decision_idx", "member_idx", "decided_s"]], s.rename(columns={"start_s": "prev_s"}),
                         left_on="decided_s", right_on="prev_s", by="member_idx", direction="backward")
    nxt = nxt.merge(prev[["decision_idx", "prev_s"]], on="decision_idx")
    nxt["returned"] = (nxt["next_s"] - nxt["decided_s"]) <= 72 * HOUR
    nxt["days_away"] = (nxt["decided_s"] - nxt["prev_s"]) / DAY
    return nxt


def uplift_table(test, members):
    t = test.merge(members, on="member_idx")
    t["tenure_days"] = (t["decided_s"] - t["signup_s"]) / DAY
    segments = {
        "new_likes": np.where(t["age_band"].isin(["18-24", "25-29"]), "ages 18-29",
                              np.where(t["age_band"].isin(["35-44", "45+"]), "ages 35+", "ages 30-34")),
        "profile_tip": np.where(t["tenure_days"] < 30, "first 30 days", "after 30 days"),
        "new_people_nearby": t["market_size"].map({"large": "large market", "medium": "medium market",
                                                   "small": "small market"}),
        "we_miss_you": pd.cut(t["days_away"], [0, 3, 8, 15, 10_000], right=False,
                              labels=["2 days", "3-7 days", "8-14 days", "15+ days"]).astype(str),
    }
    rows = []
    for nudge in C.NUDGES:
        pick = (t["nudge_type"] == nudge).to_numpy()
        sub = t[pick].assign(segment=np.asarray(segments[nudge])[pick] if nudge in segments else "all")
        for seg, g in sub.groupby("segment"):
            tr, ho = g[g["arm"] == "treatment"], g[g["arm"] == "holdout"]
            rows.append({
                "nudge": nudge, "segment": seg, "treated": len(tr), "holdout": len(ho),
                "return_treated": tr["returned"].mean(), "return_holdout": ho["returned"].mean(),
                "uplift_pts": 100 * (tr["returned"].mean() - ho["returned"].mean()),
                "opt_out_rate": tr["opted_out"].mean(),
            })
    return pd.DataFrame(rows)


def lapse_rate(sessions, members, n_days, t0):
    """Share of member-days with a session in the 14 days up to d and none in the 7 after."""
    day = ((sessions["start_s"] - t0) // DAY).astype(int)
    active = np.zeros((len(members), n_days), bool)
    active[sessions["member_idx"].to_numpy(), day.to_numpy()] = True
    c = np.concatenate([np.zeros((len(members), 1), int), active.cumsum(axis=1)], axis=1)
    rates = []
    for d in range(13, n_days - 7):
        before = c[:, d + 1] - c[:, d - 13] > 0
        after = c[:, d + 8] - c[:, d + 1] > 0
        rates.append((before & ~after).sum() / max(before.sum(), 1))
    return float(np.mean(rates))


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--days", type=int, default=C.N_DAYS)
    p.add_argument("--start-date", default=None, help="YYYY-MM-DD, default ends the window 2 days ago")
    args = p.parse_args(argv)
    start = date.fromisoformat(args.start_date) if args.start_date else date.today() - timedelta(days=args.days + 1)

    sim = Simulation(args.seed, start)
    f = sim.run(args.days)
    members = sim.members()
    sessions, decisions, notif, subs, away = (f[k] for k in
                                              ["sessions", "decisions", "notifications", "subscriptions", "away"])
    t0 = sim.t0
    pd.set_option("display.width", 160, "display.max_columns", 20, "display.precision", 3)

    n_events = int(2 * len(sessions) + sessions[["profile_view", "like_sent", "match_created", "message_sent",
                                                  "message_received", "like_received"]].sum().sum() + len(away))
    print(f"window: {start} for {args.days} days, seed {args.seed}")
    print(f"members {len(members):,} (signed up in window {int((members['signup_s'] >= t0).sum()):,})")
    print(f"sessions {len(sessions):,}  app events before bad data {n_events:,}")
    print(f"decisions {len(decisions):,} (blast {int((decisions['campaign_type'] == 'blast').sum()):,}, "
          f"nudge test {int((decisions['campaign_type'] == 'nudge_test').sum()):,})")
    print(f"notification events {len(notif):,}: {notif['event_type'].value_counts().to_dict()}")
    print(f"subscription events {len(subs):,}: {subs['event_type'].value_counts().to_dict()}")

    # 1. Lapse
    print(f"\n1. members ever on a lapse path: {members['ever_lapse_path'].mean():.1%}")
    print(f"   lapse label positive rate: {lapse_rate(sessions, members, args.days, t0):.1%}")

    # 2. Nudge uplift against the holdout, decisions with a full 72-hour window only
    test = decisions[decisions["campaign_type"] == "nudge_test"]
    test = test[test["decided_s"] < t0 + (args.days - 3) * DAY]
    test = returned_within_72h(test, sessions, members)
    outs = notif[notif["event_type"].isin(["push_disabled", "uninstalled"])]
    test["opted_out"] = test["decision_idx"].isin(outs["decision_idx"])
    print("\n2. 72-hour return, treatment against holdout")
    print(uplift_table(test, members).to_string(index=False))
    base = test[test["arm"] == "holdout"]
    base_bins = pd.cut(base["days_away"], [2, 4, 7, 15, 10_000], right=False,
                       labels=["2-3 days", "4-6 days", "7-14 days", "15+ days"])
    print("   holdout return by days away:", base.groupby(base_bins, observed=True)["returned"].mean().round(3).to_dict())

    # 3 and 6. Opt-outs and reach by week
    week = lambda s: ((s - t0) // (7 * DAY)).astype(int)
    weekly = pd.DataFrame({
        "pushes_delivered": notif[notif["event_type"] == "delivered"].groupby(week(notif["ts"])).size(),
        "opt_outs": outs.groupby(week(outs["ts"])).size(),
        "uninstalls": outs[outs["event_type"] == "uninstalled"].groupby(week(outs["ts"])).size(),
    }).fillna(0).astype(int)
    hist = with_history(sessions, members).sort_values(["member_idx", "start_s"])
    gap = hist.groupby("member_idx")["start_s"].diff()
    react = hist[gap >= 7 * DAY]
    weekly["reactivations_7d"] = react.groupby(week(react["start_s"])).size()
    signup_week = week(members["signup_s"]).clip(lower=0)
    opted_in = members[members["push_opt_in"]].groupby(signup_week).size().reindex(weekly.index, fill_value=0)
    weekly["reachable_end"] = opted_in.cumsum() - weekly["opt_outs"].cumsum()
    print("\n3/6. by week (incident from day 35, week 5)")
    print(weekly.to_string())
    # Week 0 has no 7-day history yet, so the baseline is weeks 1-3
    for col in ["opt_outs", "reactivations_7d"]:
        ratio = weekly.loc[5:7, col].mean() / weekly.loc[1:3, col].mean()
        print(f"   {col}, weeks 5-7 against weeks 1-3: {ratio:.2f}x")

    # 4. Send hour and fatigue against opens
    deliv = notif[notif["event_type"] == "delivered"][["decision_idx", "member_idx", "ts"]]
    opened = set(notif.loc[notif["event_type"] == "opened", "decision_idx"])
    deliv = deliv.merge(members[["member_idx", "timezone", "preferred_hour_local"]], on="member_idx")
    local = pd.to_datetime(deliv["ts"], unit="s", utc=True)
    deliv["local_hour"] = 0
    for tz, g in deliv.groupby("timezone"):
        deliv.loc[g.index, "local_hour"] = local[g.index].dt.tz_convert(tz).dt.hour
    diff = (deliv["local_hour"] - deliv["preferred_hour_local"]).abs()
    diff = np.minimum(diff, 24 - diff)
    night = (deliv["local_hour"] >= 23) | (deliv["local_hour"] < 7)
    deliv["hour_bucket"] = np.where(diff <= 2, "within 2h of preferred", np.where(night, "23:00-07:00", "other daytime"))
    deliv["opened"] = deliv["decision_idx"].isin(opened)
    print("\n4. open rate by send hour:", deliv.groupby("hour_bucket")["opened"].mean().round(4).to_dict())

    # 5. Paying
    free = members.loc[members["plan"] == "free", "member_idx"]
    s = sessions[sessions["member_idx"].isin(free)]
    s = pd.concat([members.loc[members["member_idx"].isin(free), ["member_idx", "last_active_s"]]
                  .rename(columns={"last_active_s": "start_s"}).assign(match_created=0), s])
    s = s.sort_values(["member_idx", "start_s"])
    gap = s.groupby("member_idx")["start_s"].diff()
    rets = s[gap >= 2 * DAY][["member_idx", "start_s"]].drop_duplicates("member_idx")
    m = s.merge(rets.rename(columns={"start_s": "ret_s"}), on="member_idx")
    m = m[(m["start_s"] >= m["ret_s"]) & (m["start_s"] < m["ret_s"] + 72 * HOUR)]
    matched = m.groupby("member_idx")["match_created"].sum() > 0
    rets = rets.set_index("member_idx").join(matched.rename("matched")).fillna({"matched": False})
    ups = subs[subs["event_type"] == "upgrade"][["member_idx", "ts"]]
    rets = rets.reset_index().merge(ups, on="member_idx", how="left")
    rets["upgraded_14d"] = (rets["ts"] > rets["start_s"]) & (rets["ts"] <= rets["start_s"] + 14 * DAY)
    rets = rets.groupby("member_idx").agg(matched=("matched", "first"), upgraded=("upgraded_14d", "any"),
                                          start=("start_s", "first"))
    rets = rets[rets["start"] < t0 + (args.days - 14) * DAY]
    print("\n5. free returners upgrading within 14 days:",
          rets.groupby("matched")["upgraded"].mean().round(4).to_dict(), rets.groupby("matched").size().to_dict())


if __name__ == "__main__":
    main()
