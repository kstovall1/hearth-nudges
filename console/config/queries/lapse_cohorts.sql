-- Lapse risk by age band and plan on the latest score date, from the governed lapse_risk_metrics view.
SELECT
  `Age Band` AS age_band,
  `Plan` AS plan,
  MEASURE(`Members Scored`) AS members,
  MEASURE(`Mean Lapse Risk`) AS mean_lapse_risk,
  MEASURE(`High-Risk Members`) AS high_risk_members
FROM katie_febar_catalog.hearth_gold.lapse_risk_metrics
WHERE `Score Date` = (SELECT max(feature_date) FROM katie_febar_catalog.hearth_gold.member_lapse_scores)
GROUP BY ALL
ORDER BY age_band, plan
