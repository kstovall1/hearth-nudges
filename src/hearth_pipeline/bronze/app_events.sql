-- Mobile app events from the event stream, exactly as landed.
-- Auto Loader reads only new files on each run. Everything stays a string here and gets typed in silver.
CREATE OR REFRESH STREAMING TABLE hearth_bronze.app_events
COMMENT 'Mobile app events from the event stream, as landed. All fields are strings. Loaded incrementally by Auto Loader from the landing Volume.'
AS SELECT
  *,
  _metadata.file_path AS _source_file,
  current_timestamp() AS _ingested_at
FROM STREAM read_files(
  '${landing_path}/app_events',
  format => 'json',
  inferColumnTypes => false
);
