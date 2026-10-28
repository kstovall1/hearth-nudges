-- Members export from the user database, one file of new members a night, exactly as landed.
-- Auto Loader reads only new files on each run. Everything stays a string here and gets typed in silver.
CREATE OR REFRESH STREAMING TABLE hearth_bronze.members
COMMENT 'Members export from the user database, one file of new members a night, as landed. All fields are strings. Loaded incrementally by Auto Loader from the landing Volume.'
AS SELECT
  *,
  _metadata.file_path AS _source_file,
  current_timestamp() AS _ingested_at
FROM STREAM read_files(
  '${landing_path}/members',
  format => 'csv', header => true,
  inferColumnTypes => false
);
