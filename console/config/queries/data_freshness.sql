-- How current the gold data is, for the freshness note on each KPI.
SELECT
  (SELECT max(decision_date) FROM katie_febar_catalog.hearth_gold.campaign_daily_kpis) AS decisions_through,
  (SELECT max(feature_date) FROM katie_febar_catalog.hearth_gold.member_lapse_scores) AS scores_through,
  (SELECT max(day) FROM katie_febar_catalog.hearth_gold.reach_daily) AS reach_through
