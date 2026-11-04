"""Evidence for the daily nudge picker: what it decided for the next day, from the tables it wrote.

Usage: uv run python scripts/picker_evidence.py
"""

import argparse

from databricks.sdk import WorkspaceClient
from validate_landing import run


def queries(cat):
    nba = f"{cat}.hearth_serving.next_best_action"
    dec = f"{cat}.hearth_gold.nudge_decisions"
    return [
        ("Decisions by action and reason, latest day", f"""
SELECT decision_date, action, coalesce(hold_reason, nudge_type) AS reason_or_nudge, count(*) AS members,
  round(100 * count(*) / sum(count(*)) OVER (PARTITION BY decision_date), 1) AS pct
FROM {dec} WHERE decision_date = (SELECT max(decision_date) FROM {dec})
GROUP BY 1, 2, 3 ORDER BY 2 DESC, 4 DESC"""),
        ("Sends by nudge and segment, with the estimates behind them", f"""
SELECT nudge_type, segment, count(*) AS sends, round(avg(estimated_uplift_pts), 2) AS est_uplift_pts,
  round(avg(opt_out_risk_pct), 2) AS est_opt_out_pct, round(avg(net_value_pts), 2) AS net_value_pts,
  round(avg(lapse_risk), 3) AS mean_lapse_risk
FROM {dec} WHERE decision_date = (SELECT max(decision_date) FROM {dec}) AND action = 'send'
GROUP BY 1, 2 ORDER BY 3 DESC"""),
        ("Sends by local hour and where the hour came from", f"""
SELECT send_hour_local, count(*) AS sends, count_if(hour_source = 'own sessions') AS from_own_sessions
FROM {dec} WHERE decision_date = (SELECT max(decision_date) FROM {dec}) AND action = 'send'
GROUP BY 1 ORDER BY 1"""),
        ("Sends by days away and lapse risk", f"""
SELECT days_away_bucket, coalesce(risk_band, 'unscored (lapsed)') AS risk_band, count(*) AS sends
FROM {dec} WHERE decision_date = (SELECT max(decision_date) FROM {dec}) AND action = 'send'
GROUP BY 1, 2 ORDER BY 1, 2"""),
        ("The serving table the notification service will read", f"""
SELECT decision_date, count(*) AS members, count_if(action = 'send') AS sends,
  count_if(copy_status = 'pending') AS copy_pending, count(DISTINCT member_id) = count(*) AS one_row_per_member,
  min(send_at_utc) AS first_send_utc, max(send_at_utc) AS last_send_utc
FROM {nba} GROUP BY 1"""),
        ("Five rows from the serving table (synthetic members)", f"""
SELECT member_id, action, hold_reason, nudge_type, send_at_utc, send_hour_local, timezone, estimated_uplift_pts,
  net_value_pts, risk_band, copy_status
FROM {nba} WHERE action = 'send' ORDER BY net_value_pts DESC LIMIT 5"""),
        ("Nudge test readout cells the picker uses", f"""
SELECT nudge_type, dimension, segment, n_treated, n_holdout, uplift_pts, uplift_low_pts, opt_out_treated_pct, opt_out_ratio
FROM {cat}.hearth_gold.nudge_segment_effects WHERE chosen ORDER BY 1, 3"""),
    ]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--profile", default="fevm-katie-febar")
    p.add_argument("--catalog", default="katie_febar_catalog")
    p.add_argument("--warehouse", default="Serverless Starter Warehouse")
    args = p.parse_args()
    w = WorkspaceClient(profile=args.profile)
    warehouse_id = next(wh.id for wh in w.warehouses.list() if wh.name == args.warehouse)
    for title, sql in queries(args.catalog):
        print(f"## {title}\n")
        print(run(w, warehouse_id, sql.strip()))
        print()


if __name__ == "__main__":
    main()
