-- Training data for the uplift model: each nudge test decision, the member's features the day
-- before it, and what happened. treated against holdout, within each nudge_type, is the comparison.
-- Decisions on the first day of data have no features from the day before, so their feature columns are null.
CREATE OR REFRESH MATERIALIZED VIEW hearth_gold.nudge_outcomes
CLUSTER BY AUTO
COMMENT 'One row per nudge test decision with features as of the day before and its outcome. Filter on window_complete before training.'
AS SELECT
  n.decision_id,
  n.member_id,
  n.decision_date,
  n.nudge_type,
  n.arm,
  n.arm = 'treatment' AS treated,
  n.send_hour_local,
  n.copy_variant_id,
  n.days_away,
  n.days_away_bucket,
  n.delivered,
  n.opened,
  n.returned_72h,
  n.return_source,
  n.opted_out,
  n.uninstalled,
  n.upgraded_14d,
  n.window_complete,
  f.feature_date AS features_as_of,
  f.* EXCEPT (member_id, feature_date, uninstalled)  -- test members are always installed the day before
FROM hearth_silver.notification_funnel n
LEFT JOIN hearth_gold.member_daily_features f
  ON f.member_id = n.member_id
  AND f.feature_date = date_sub(n.decision_date, 1)
WHERE n.campaign_type = 'nudge_test';
