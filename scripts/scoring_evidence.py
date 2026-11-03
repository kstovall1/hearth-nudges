"""Evidence for batch lapse scoring: rows and bands per day, and, for scored days whose next 7 days are
now in the data, predicted risk against what happened.

Usage: uv run python scripts/scoring_evidence.py
"""

import argparse

from databricks.sdk import WorkspaceClient
from validate_landing import run


def queries(cat):
    s = f"{cat}.hearth_gold.member_lapse_scores"
    return [
        ("Scores per day", f"""
SELECT feature_date, count(*) AS members, round(avg(lapse_risk), 3) AS mean_risk,
  round(100 * avg(CASE WHEN risk_band = 'high' THEN 1 ELSE 0 END), 1) AS pct_high,
  round(100 * avg(CASE WHEN risk_band = 'medium' THEN 1 ELSE 0 END), 1) AS pct_medium,
  round(100 * avg(CASE WHEN risk_band = 'low' THEN 1 ELSE 0 END), 1) AS pct_low,
  min(model_version) AS model_version
FROM {s} GROUP BY 1 ORDER BY 1"""),
        ("Predicted against actual, on scored days whose outcome is known", f"""
SELECT s.risk_band, count(*) AS member_days, round(avg(s.lapse_risk), 3) AS mean_predicted,
  round(avg(CAST(l.lapsed AS INT)), 3) AS actual_lapse_rate
FROM {s} s JOIN {cat}.hearth_gold.lapse_labels l USING (member_id, feature_date)
GROUP BY 1 ORDER BY 2 DESC"""),
        ("Same, by day", f"""
SELECT s.feature_date, count(*) AS member_days, round(avg(s.lapse_risk), 3) AS mean_predicted,
  round(avg(CAST(l.lapsed AS INT)), 3) AS actual_lapse_rate
FROM {s} s JOIN {cat}.hearth_gold.lapse_labels l USING (member_id, feature_date)
GROUP BY 1 ORDER BY 1"""),
        ("Lapse risk by cohort, latest day (a Genie-style question)", f"""
SELECT f.age_band, f.plan, count(*) AS members, round(avg(s.lapse_risk), 3) AS mean_risk,
  count_if(s.risk_band = 'high') AS high_risk
FROM {s} s JOIN {cat}.hearth_gold.member_daily_features f USING (member_id, feature_date)
WHERE s.feature_date = (SELECT max(feature_date) FROM {s})
GROUP BY 1, 2 ORDER BY 1, 2"""),
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
