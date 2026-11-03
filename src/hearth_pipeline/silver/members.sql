-- Members, typed. The export only adds new members, so this streams.
-- first_name, email and phone are PII, and gender_identity and seeking are sensitive. See docs/data-design.md.
-- This is the first table anyone but the pipeline owner can read, so the masks live here. The mask functions
-- come from src/hearth_governance/masks.sql, which the daily job runs before the pipeline.
CREATE OR REFRESH STREAMING TABLE hearth_silver.members (
  member_id STRING COMMENT 'm_000001 style',
  signup_ts TIMESTAMP,
  first_name STRING MASK ${catalog}.hearth_silver.mask_contact COMMENT 'PII. Masked unless the reader is in hearth_pii_readers',
  email STRING MASK ${catalog}.hearth_silver.mask_contact COMMENT 'PII, fake. Masked unless the reader is in hearth_pii_readers',
  phone STRING MASK ${catalog}.hearth_silver.mask_contact COMMENT 'PII, fake. Masked unless the reader is in hearth_pii_readers',
  age_band STRING,
  gender_identity STRING MASK ${catalog}.hearth_silver.mask_sensitive COMMENT 'Sensitive. Masked unless the reader is in hearth_pii_readers. Never a model feature',
  seeking STRING MASK ${catalog}.hearth_silver.mask_sensitive COMMENT 'Sensitive. Masked unless the reader is in hearth_pii_readers. Never a model feature',
  metro STRING,
  market_size STRING COMMENT 'large, medium or small',
  timezone STRING COMMENT 'IANA name',
  platform STRING,
  plan_at_export STRING COMMENT 'free, plus or premium on the day of the export',
  push_opt_in BOOLEAN,
  last_active_ts TIMESTAMP COMMENT 'Last session as of the export',
  ingested_at TIMESTAMP,
  CONSTRAINT has_member_id EXPECT (member_id IS NOT NULL) ON VIOLATION FAIL UPDATE,
  CONSTRAINT known_age_band EXPECT (age_band IN ('18-24', '25-29', '30-34', '35-44', '45+')),
  CONSTRAINT has_timezone EXPECT (timezone IS NOT NULL),
  CONSTRAINT active_after_signup EXPECT (last_active_ts >= signup_ts)
)
COMMENT 'One row per member as of signup or the start of the window. plan_at_export is the plan on that day; later changes are in subscription_events.'
AS SELECT
  member_id,
  CAST(signup_ts AS TIMESTAMP) AS signup_ts,
  first_name,
  email,
  phone,
  age_band,
  gender_identity,
  seeking,
  metro,
  market_size,
  timezone,
  platform,
  plan AS plan_at_export,
  CAST(push_opt_in AS BOOLEAN) AS push_opt_in,
  CAST(last_active_ts AS TIMESTAMP) AS last_active_ts,
  _ingested_at AS ingested_at
FROM STREAM(hearth_bronze.members);
