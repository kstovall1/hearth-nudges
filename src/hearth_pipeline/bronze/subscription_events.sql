-- Billing system events: trial_start, upgrade, renew, cancel, exactly as landed.
-- Auto Loader reads only new files on each run. Everything stays a string here and gets typed in silver.
CREATE OR REFRESH STREAMING TABLE hearth_bronze.subscription_events
COMMENT 'Billing system events: trial_start, upgrade, renew, cancel, as landed. All fields are strings. Loaded incrementally by Auto Loader from the landing Volume.'
AS SELECT
  *,
  _metadata.file_path AS _source_file,
  current_timestamp() AS _ingested_at
FROM STREAM read_files(
  '${landing_path}/subscription_events',
  format => 'csv', header => true,
  inferColumnTypes => false
);
