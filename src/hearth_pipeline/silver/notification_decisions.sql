-- Every send-or-hold decision from the messaging tool, typed.
CREATE OR REFRESH STREAMING TABLE hearth_silver.notification_decisions (
  CONSTRAINT has_ids EXPECT (decision_id IS NOT NULL AND member_id IS NOT NULL) ON VIOLATION FAIL UPDATE,
  CONSTRAINT known_arm EXPECT (arm IN ('treatment', 'holdout')),
  CONSTRAINT known_nudge EXPECT (nudge_type IN ('new_likes', 'match_waiting', 'profile_tip', 'new_people_nearby', 'we_miss_you')),
  CONSTRAINT holdout_never_sent EXPECT ((arm = 'holdout') = (scheduled_send_ts IS NULL))
)
COMMENT 'One row per decision. Holdout rows keep the nudge they would have got and have no send time.'
AS SELECT
  decision_id,
  member_id,
  CAST(decided_ts AS TIMESTAMP) AS decided_ts,
  to_date(CAST(decided_ts AS TIMESTAMP)) AS decision_date,
  campaign_id,
  campaign_type,
  nudge_type,
  arm,
  CAST(scheduled_send_ts AS TIMESTAMP) AS scheduled_send_ts,
  copy_variant_id,
  copy_text,
  _ingested_at AS ingested_at
FROM STREAM(hearth_bronze.notification_decisions);
