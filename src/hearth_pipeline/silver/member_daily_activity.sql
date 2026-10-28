-- What each member did each day they did anything, in UTC days. Days with no activity have no row.
-- Includes likes and messages that arrived while the member was away.
CREATE OR REFRESH MATERIALIZED VIEW hearth_silver.member_daily_activity
CLUSTER BY AUTO
COMMENT 'One row per member per UTC day with any activity.'
AS SELECT
  member_id,
  event_date AS activity_date,
  count_if(event_type = 'session_start') AS sessions,
  count_if(event_type = 'session_start' AND source = 'push') AS push_sessions,
  count_if(event_type = 'profile_view') AS profile_views,
  count_if(event_type = 'like_sent') AS likes_sent,
  count_if(event_type = 'like_received') AS likes_received,
  count_if(event_type = 'match_created') AS matches,
  count_if(event_type = 'message_sent') AS messages_sent,
  count_if(event_type = 'message_received') AS messages_received,
  min(CASE WHEN event_type = 'session_start' THEN event_ts END) AS first_session_ts,
  max(CASE WHEN event_type = 'session_start' THEN event_ts END) AS last_session_ts
FROM hearth_silver.app_events
GROUP BY member_id, event_date;
