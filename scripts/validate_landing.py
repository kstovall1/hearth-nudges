"""Check the landed raw files with SQL, the way the pipeline will first see them.

Usage: uv run python scripts/validate_landing.py [--profile fevm-katie-febar] [--catalog katie_febar_catalog]
Prints each query and its result. Finds the SQL warehouse by name, so no IDs live in the repo.
"""

import argparse
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

EVENT_TYPES = "'session_start','session_end','profile_view','like_sent','like_received','match_created'," \
              "'message_sent','message_received'"


def queries(cat):
    land = f"/Volumes/{cat}/hearth_raw/landing"
    dt = "regexp_extract(_metadata.file_path, 'dt=([0-9-]+)', 1)"

    def src(entity, fmt, hints=""):
        opts = "format => 'csv', header => true" if fmt == "csv" else "format => 'json'"
        if hints:
            opts += f", schemaHints => '{hints}'"
        return f"read_files('{land}/{entity}', {opts})"

    per_entity = " UNION ALL ".join(
        f"SELECT '{e}' AS entity, count(*) AS rows, count(DISTINCT {dt}) AS days, min({dt}) AS first_dt, "
        f"max({dt}) AS last_dt, count(DISTINCT _metadata.file_path) AS files FROM {src(e, f)}"
        for e, f in [("members", "csv"), ("app_events", "json"), ("notification_decisions", "json"),
                     ("notification_events", "json"), ("subscription_events", "csv")]
    )
    ev = src("app_events", "json", "app_version STRING")
    return [
        ("Rows, days and files per entity", per_entity),
        ("Bad rows in app_events, as a percent of all rows", f"""
SELECT count(*) AS rows,
  round(100 * avg(CASE WHEN member_id IS NULL THEN 1 ELSE 0 END), 3) AS pct_null_member,
  round(100 * (count(*) - count(DISTINCT event_id)) / count(*), 3) AS pct_duplicate_event_id,
  round(100 * avg(CASE WHEN CAST(event_ts AS TIMESTAMP) > current_timestamp() THEN 1 ELSE 0 END), 3) AS pct_future_ts,
  round(100 * avg(CASE WHEN event_type NOT IN ({EVENT_TYPES}) THEN 1 ELSE 0 END), 3) AS pct_unknown_type,
  min(CASE WHEN app_version IS NOT NULL THEN {dt} END) AS first_dt_with_app_version,
  max(CASE WHEN app_version IS NULL AND member_id IS NOT NULL THEN {dt} END) AS last_dt_without_app_version
FROM {ev}"""),
        ("app_events by type", f"SELECT event_type, count(*) AS rows FROM {ev} GROUP BY 1 ORDER BY 2 DESC"),
        ("Decisions by campaign and arm", f"""
SELECT campaign_type, arm, count(*) AS rows, count(scheduled_send_ts) AS with_send_ts
FROM {src('notification_decisions', 'json')} GROUP BY 1, 2 ORDER BY 1, 2"""),
        ("Blasts per week, before and after the incident", f"""
SELECT date_trunc('week', CAST(decided_ts AS TIMESTAMP)) AS week, count(DISTINCT campaign_id) AS blasts,
  count(*) AS decisions
FROM {src('notification_decisions', 'json')} WHERE campaign_type = 'blast' GROUP BY 1 ORDER BY 1"""),
        ("Push callbacks by type", f"""
SELECT event_type, count(*) AS rows, count(DISTINCT notification_event_id) AS distinct_ids
FROM {src('notification_events', 'json')} GROUP BY 1 ORDER BY 2 DESC"""),
        ("Orphans: rows whose parent is missing", f"""
SELECT 'decisions without a member' AS check, count(*) AS rows
FROM {src('notification_decisions', 'json')} d LEFT ANTI JOIN {src('members', 'csv')} m USING (member_id)
UNION ALL
SELECT 'callbacks without a decision', count(*)
FROM {src('notification_events', 'json')} n LEFT ANTI JOIN {src('notification_decisions', 'json')} d USING (decision_id)
UNION ALL
SELECT 'app events without a member (null ids excluded)', count(*)
FROM (SELECT member_id FROM {ev} WHERE member_id IS NOT NULL) e LEFT ANTI JOIN {src('members', 'csv')} m USING (member_id)"""),
        ("Fake PII shape", f"""
SELECT substring_index(email, '@', -1) AS email_domain, count(*) AS members,
  sum(CASE WHEN phone RLIKE '^[+]1-[0-9]{{3}}-555-01[0-9]{{2}}$' THEN 1 ELSE 0 END) AS phones_in_555_01xx
FROM {src('members', 'csv')} GROUP BY 1 ORDER BY 1"""),
        ("Members by plan and push opt-in", f"""
SELECT plan, round(100 * count(*) / sum(count(*)) OVER (), 1) AS pct_members,
  round(100 * avg(CASE WHEN push_opt_in THEN 1 ELSE 0 END), 1) AS pct_push_opt_in
FROM {src('members', 'csv')} GROUP BY 1 ORDER BY 2 DESC"""),
        ("Subscription events", f"""
SELECT event_type, plan, count(*) AS rows, min(price_usd) AS min_price, max(price_usd) AS max_price
FROM {src('subscription_events', 'csv')} GROUP BY 1, 2 ORDER BY 1, 2"""),
        ("Generator truth against nudge test decisions", f"""
SELECT (SELECT count(*) FROM {cat}.hearth_ml.generator_truth) AS truth_rows,
  (SELECT count(*) FROM {src('notification_decisions', 'json')} WHERE campaign_type = 'nudge_test') AS test_decisions"""),
    ]


def run(w, warehouse_id, sql):
    r = w.statement_execution.execute_statement(warehouse_id=warehouse_id, statement=sql, wait_timeout="50s")
    while r.status.state in (StatementState.PENDING, StatementState.RUNNING):
        time.sleep(3)
        r = w.statement_execution.get_statement(r.statement_id)
    if r.status.state != StatementState.SUCCEEDED:
        return f"FAILED: {r.status.error.message}"
    cols = [c.name for c in r.manifest.schema.columns]
    rows = [cols] + [["" if v is None else v for v in row] for row in (r.result.data_array or [])]
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(cols))]
    return "\n".join("  ".join(str(v).ljust(widths[i]) for i, v in enumerate(row)).rstrip() for row in rows)


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
