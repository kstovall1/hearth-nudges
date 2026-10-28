"""Evidence for a pipeline run: the update, rows per table, expectation results, and the planted
patterns recomputed from gold. They should match docs/evidence/generator-patterns.txt.

Usage: uv run python scripts/pipeline_evidence.py [--profile fevm-katie-febar] [--catalog katie_febar_catalog]
Finds the pipeline and warehouse by name, so no IDs are printed.
"""

import argparse

from databricks.sdk import WorkspaceClient
from validate_landing import run

TABLES = {
    "hearth_bronze": ["members", "app_events", "notification_decisions", "notification_events", "subscription_events"],
    "hearth_silver": ["members", "app_events", "app_events_quarantine", "notification_decisions", "notification_events",
                      "subscription_events", "sessions", "member_daily_activity", "notification_funnel"],
    "hearth_gold": ["member_daily_features", "lapse_labels", "nudge_outcomes", "campaign_daily_kpis", "reach_daily"],
}


def queries(cat, pipeline_id):
    log = f"event_log('{pipeline_id}')"
    latest = f"(SELECT origin.update_id FROM {log} WHERE event_type = 'create_update' ORDER BY timestamp DESC LIMIT 1)"
    counts = " UNION ALL ".join(
        f"SELECT '{s}.{t}' AS table_name, count(*) AS rows FROM {cat}.{s}.{t}" for s, tables in TABLES.items() for t in tables
    )
    return [
        ("Latest update", f"""
SELECT min(timestamp) AS started, max(timestamp) AS finished,
  max_by(details:update_progress.state::STRING, timestamp) AS final_state
FROM {log} WHERE event_type = 'update_progress' AND origin.update_id = {latest}"""),
        ("Rows per table", counts),
        ("Rows written per flow in the latest update", f"""
SELECT origin.flow_name AS flow, sum(details:flow_progress.metrics.num_output_rows::BIGINT) AS rows_written
FROM {log}
WHERE event_type = 'flow_progress' AND origin.update_id = {latest}
  AND details:flow_progress.metrics.num_output_rows IS NOT NULL
GROUP BY 1 ORDER BY 1"""),
        ("Expectations in the latest update", f"""
SELECT e.dataset, e.name AS expectation, sum(e.passed_records) AS passed, sum(e.failed_records) AS failed
FROM {log}
LATERAL VIEW explode(from_json(details:flow_progress.data_quality.expectations::STRING,
  'array<struct<name:string,dataset:string,passed_records:bigint,failed_records:bigint>>')) x AS e
WHERE event_type = 'flow_progress' AND origin.update_id = {latest}
GROUP BY 1, 2 ORDER BY 1, 2"""),
        ("Quarantined app events", f"""
SELECT quarantine_reason, count(*) AS rows FROM {cat}.hearth_silver.app_events_quarantine GROUP BY 1 ORDER BY 1"""),
        ("Bronze to silver app events", f"""
SELECT
  (SELECT count(*) FROM {cat}.hearth_bronze.app_events) AS bronze_rows,
  (SELECT count(*) - count(DISTINCT event_id) FROM {cat}.hearth_bronze.app_events) AS duplicate_rows,
  (SELECT count_if(member_id IS NULL) FROM {cat}.hearth_bronze.app_events) AS no_member_rows,
  (SELECT count(*) FROM {cat}.hearth_silver.app_events_quarantine) AS quarantined_rows,
  (SELECT count(*) FROM {cat}.hearth_silver.app_events) AS silver_rows"""),
        ("Pattern 1: lapse label positive rate", f"""
SELECT count(*) AS member_days, round(100 * avg(CAST(lapsed AS INT)), 1) AS pct_lapsed
FROM {cat}.hearth_gold.lapse_labels"""),
        ("Pattern 2: 72-hour return, treated against holdout, by nudge and segment", f"""
WITH o AS (
  SELECT *,
    CASE nudge_type
      WHEN 'new_likes' THEN CASE WHEN age_band IN ('18-24', '25-29') THEN 'ages 18-29'
                                 WHEN age_band = '30-34' THEN 'ages 30-34' ELSE 'ages 35+' END
      WHEN 'profile_tip' THEN CASE WHEN tenure_days < 30 THEN 'first 30 days' ELSE 'after 30 days' END
      WHEN 'new_people_nearby' THEN market_size || ' market'
      WHEN 'we_miss_you' THEN days_away_bucket
      ELSE 'all'
    END AS segment
  FROM {cat}.hearth_gold.nudge_outcomes
  WHERE window_complete AND features_as_of IS NOT NULL  -- the first day's decisions have no prior features
)
SELECT nudge_type, segment, count_if(treated) AS treated, count_if(NOT treated) AS holdout,
  round(100 * avg(CASE WHEN treated THEN CAST(returned_72h AS INT) END), 1) AS pct_return_treated,
  round(100 * avg(CASE WHEN NOT treated THEN CAST(returned_72h AS INT) END), 1) AS pct_return_holdout,
  round(100 * (avg(CASE WHEN treated THEN CAST(returned_72h AS INT) END)
             - avg(CASE WHEN NOT treated THEN CAST(returned_72h AS INT) END)), 1) AS uplift_pts,
  round(100 * avg(CASE WHEN treated THEN CAST(opted_out AS INT) END), 2) AS pct_opt_out_treated
FROM o GROUP BY 1, 2 ORDER BY 1, 2"""),
        ("Pattern 6: the incident, by week (the blast triples from day 35)", f"""
SELECT floor(datediff(day, (SELECT min(day) FROM {cat}.hearth_gold.reach_daily)) / 7) AS week,
  min(day) AS week_start, sum(pushes_delivered) AS pushes, sum(blast_pushes) AS blast_pushes,
  sum(opted_out_today) AS opt_outs, min_by(reachable, day) AS reachable_at_start, max_by(reachable, day) AS reachable_at_end
FROM {cat}.hearth_gold.reach_daily GROUP BY 1 ORDER BY 1"""),
        ("Campaign KPIs: return within 72 hours by campaign and arm", f"""
SELECT campaign_type, arm, sum(decisions_complete) AS decisions,
  round(100 * sum(returned_72h_complete) / sum(decisions_complete), 1) AS pct_returned_72h,
  round(100 * sum(opted_out) / sum(decisions), 2) AS pct_opted_out
FROM {cat}.hearth_gold.campaign_daily_kpis GROUP BY 1, 2 ORDER BY 1, 2"""),
    ]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--profile", default="fevm-katie-febar")
    p.add_argument("--catalog", default="katie_febar_catalog")
    p.add_argument("--pipeline", default="hearth-pipeline")
    p.add_argument("--warehouse", default="Serverless Starter Warehouse")
    args = p.parse_args()
    w = WorkspaceClient(profile=args.profile)
    warehouse_id = next(wh.id for wh in w.warehouses.list() if wh.name == args.warehouse)
    pipeline_id = next(pl.pipeline_id for pl in w.pipelines.list_pipelines() if pl.name == args.pipeline)
    for title, sql in queries(args.catalog, pipeline_id):
        print(f"## {title}\n")
        print(run(w, warehouse_id, sql.strip()))
        print()


if __name__ == "__main__":
    main()
