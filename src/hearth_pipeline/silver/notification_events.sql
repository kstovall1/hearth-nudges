-- Push provider callbacks, typed and deduplicated. Providers retry, so the same callback can arrive twice.
CREATE OR REFRESH MATERIALIZED VIEW hearth_silver.notification_events (
  CONSTRAINT has_decision EXPECT (decision_id IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT known_event_type EXPECT (event_type IN ('delivered', 'opened', 'push_disabled', 'uninstalled')) ON VIOLATION DROP ROW
)
CLUSTER BY AUTO
COMMENT 'Push callbacks, one row per notification_event_id.'
AS SELECT
  notification_event_id,
  decision_id,
  member_id,
  CAST(event_ts AS TIMESTAMP) AS event_ts,
  event_type,
  _ingested_at AS ingested_at
FROM hearth_bronze.notification_events
QUALIFY row_number() OVER (PARTITION BY notification_event_id ORDER BY _ingested_at, _source_file) = 1;
