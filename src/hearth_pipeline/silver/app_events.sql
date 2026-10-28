-- Clean app events: typed, one row per event_id, and only events the app can actually send.
-- A materialized view, because removing duplicates in a stream needs a watermark, and clock-skewed
-- events years in the future would push that watermark ahead and silently drop real events.
CREATE OR REFRESH MATERIALIZED VIEW hearth_silver.app_events (
  CONSTRAINT has_member EXPECT (member_id IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT valid_timestamp EXPECT (event_ts IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT not_in_future EXPECT (event_ts <= ingested_at + INTERVAL 1 DAY) ON VIOLATION DROP ROW,
  CONSTRAINT known_event_type EXPECT (event_type IN (
    'session_start', 'session_end', 'profile_view', 'like_sent', 'like_received',
    'match_created', 'message_sent', 'message_received'
  )) ON VIOLATION DROP ROW
)
CLUSTER BY AUTO
COMMENT 'App events, typed and deduplicated on event_id. Drops events with no member, a future timestamp or an unknown type. Future and unknown ones are kept in app_events_quarantine.'
AS SELECT
  event_id,
  member_id,
  session_id,
  CAST(event_ts AS TIMESTAMP) AS event_ts,
  to_date(CAST(event_ts AS TIMESTAMP)) AS event_date,
  event_type,
  platform,
  properties:source::STRING AS source,
  properties:other_member_id::STRING AS other_member_id,
  properties:duration_s::INT AS duration_s,
  app_version,
  _ingested_at AS ingested_at
FROM hearth_bronze.app_events
-- At-least-once delivery repeats events with the same id
QUALIFY row_number() OVER (PARTITION BY event_id ORDER BY _ingested_at, _source_file) = 1;
