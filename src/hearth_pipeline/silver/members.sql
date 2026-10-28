-- Members, typed. The export only adds new members, so this streams.
-- first_name, email and phone are PII, and gender_identity and seeking are sensitive. See docs/data-design.md.
CREATE OR REFRESH STREAMING TABLE hearth_silver.members (
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
