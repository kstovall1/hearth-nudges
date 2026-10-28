-- One row per app session, built from its events.
CREATE OR REFRESH MATERIALIZED VIEW hearth_silver.sessions (
  CONSTRAINT has_start EXPECT (session_start_ts IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT ends_after_start EXPECT (session_end_ts IS NULL OR session_end_ts >= session_start_ts)
)
CLUSTER BY AUTO
COMMENT 'App sessions with their event counts. source is push when the session started from opening a notification. start_hour_local is in the member''s timezone.'
AS WITH s AS (
  SELECT
    session_id,
    member_id,
    min(CASE WHEN event_type = 'session_start' THEN event_ts END) AS session_start_ts,
    max(CASE WHEN event_type = 'session_end' THEN event_ts END) AS session_end_ts,
    max(CASE WHEN event_type = 'session_start' THEN source END) AS source,
    max(duration_s) AS duration_s,
    count_if(event_type = 'profile_view') AS profile_views,
    count_if(event_type = 'like_sent') AS likes_sent,
    count_if(event_type = 'like_received') AS likes_received,
    count_if(event_type = 'match_created') AS matches,
    count_if(event_type = 'message_sent') AS messages_sent,
    count_if(event_type = 'message_received') AS messages_received
  FROM hearth_silver.app_events
  WHERE session_id IS NOT NULL
  GROUP BY session_id, member_id
)
SELECT
  s.*,
  to_date(s.session_start_ts) AS session_date,
  hour(from_utc_timestamp(s.session_start_ts, m.timezone)) AS start_hour_local
FROM s
LEFT JOIN hearth_silver.members m USING (member_id);
