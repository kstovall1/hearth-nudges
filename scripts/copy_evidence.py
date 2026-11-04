"""Evidence for nudge copy: how many messages passed the check, why the rest failed, and sample messages.

Usage: uv run python scripts/copy_evidence.py
"""

import argparse

from databricks.sdk import WorkspaceClient
from validate_landing import run


def queries(cat):
    c = f"{cat}.hearth_gold.nudge_copy"
    latest = f"(SELECT max(decision_date) FROM {c})"
    nba = f"{cat}.hearth_serving.next_best_action"
    return [
        ("Copy status per send in the serving table", f"""
SELECT decision_date, coalesce(copy_status, 'no send') AS copy_status, count(*) AS members
FROM {nba} GROUP BY 1, 2 ORDER BY 3 DESC"""),
        ("Attempts and pass rate", f"""
SELECT attempt, count(*) AS messages, count_if(passed) AS passed, round(100 * avg(CAST(passed AS INT)), 1) AS pct_passed,
  round(avg(length(copy_text)), 1) AS avg_length, max(length(copy_text)) AS max_length
FROM {c} WHERE decision_date = {latest} GROUP BY 1 ORDER BY 1"""),
        ("Why messages failed, by attempt", f"""
SELECT attempt, reason, count(*) AS messages
FROM {c} LATERAL VIEW explode(reasons) r AS reason
WHERE decision_date = {latest} GROUP BY 1, 2 ORDER BY 1, 3 DESC"""),
        ("Pass rate by nudge, final message per send", f"""
SELECT nudge_type, count(*) AS sends, count_if(passed) AS passed, round(100 * avg(CAST(passed AS INT)), 1) AS pct_passed,
  round(avg(tone), 2) AS mean_tone
FROM (SELECT * FROM {c} WHERE decision_date = {latest}
      QUALIFY row_number() OVER (PARTITION BY member_id ORDER BY passed DESC, attempt DESC) = 1)
GROUP BY 1 ORDER BY 2 DESC"""),
        ("Sample of passed messages, two per nudge", f"""
SELECT nudge_type, copy_text, round(tone, 2) AS tone
FROM {c} WHERE decision_date = {latest} AND passed
QUALIFY row_number() OVER (PARTITION BY nudge_type ORDER BY hash(member_id)) <= 2
ORDER BY 1"""),
        ("Every distinct failed message (these wait for marketer review)", f"""
SELECT nudge_type, attempt, array_join(reasons, ',') AS reasons, copy_text, count(*) AS times
FROM {c} WHERE decision_date = {latest} AND NOT passed
GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC, 1 LIMIT 25"""),
        ("Distinct messages per nudge (how varied the copy is)", f"""
SELECT nudge_type, count(*) AS messages, count(DISTINCT copy_text) AS distinct_messages
FROM {c} WHERE decision_date = {latest} AND attempt = 1 GROUP BY 1 ORDER BY 2 DESC"""),
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
