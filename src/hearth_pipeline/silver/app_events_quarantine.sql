-- App events held back for review instead of silently lost: device clocks set ahead, and event
-- types the app shouldn't send. Events with no member are only dropped, since nothing can use them.
CREATE OR REFRESH STREAMING TABLE hearth_silver.app_events_quarantine
COMMENT 'App events kept out of silver: timestamps more than a day after ingestion, or unknown event types.'
AS SELECT
  *,
  CASE
    WHEN CAST(event_ts AS TIMESTAMP) > _ingested_at + INTERVAL 1 DAY THEN 'future_timestamp'
    ELSE 'unknown_event_type'
  END AS quarantine_reason
FROM STREAM(hearth_bronze.app_events)
WHERE CAST(event_ts AS TIMESTAMP) > _ingested_at + INTERVAL 1 DAY
  OR event_type NOT IN (
    'session_start', 'session_end', 'profile_view', 'like_sent', 'like_received',
    'match_created', 'message_sent', 'message_received'
  );
