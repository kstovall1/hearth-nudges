-- The lapse model's target. A member is lapsed on feature_date if they had a session in the 14 days
-- up to it and none in the 7 days after. Only members active in those 14 days are scored, and only
-- days whose next 7 days are fully in the data.
CREATE OR REFRESH MATERIALIZED VIEW hearth_gold.lapse_labels
CLUSTER BY AUTO
COMMENT 'One row per member per day for members active in the 14 days up to feature_date. lapsed means no session in the 7 days after. Join to member_daily_features on member_id and feature_date.'
AS WITH f AS (
  SELECT
    member_id,
    feature_date,
    sessions_14d,
    sum(sessions_1d) OVER (PARTITION BY member_id ORDER BY feature_date ROWS BETWEEN 1 FOLLOWING AND 7 FOLLOWING)
      AS sessions_next_7d,
    count(*) OVER (PARTITION BY member_id ORDER BY feature_date ROWS BETWEEN 1 FOLLOWING AND 7 FOLLOWING)
      AS days_ahead
  FROM hearth_gold.member_daily_features
)
SELECT
  member_id,
  feature_date,
  sessions_next_7d = 0 AS lapsed,
  sessions_next_7d
FROM f
WHERE sessions_14d > 0
  AND days_ahead = 7;
