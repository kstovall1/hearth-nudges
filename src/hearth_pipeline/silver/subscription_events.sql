-- Billing events, typed.
CREATE OR REFRESH STREAMING TABLE hearth_silver.subscription_events (
  CONSTRAINT has_member EXPECT (member_id IS NOT NULL) ON VIOLATION FAIL UPDATE,
  CONSTRAINT known_event_type EXPECT (event_type IN ('trial_start', 'upgrade', 'renew', 'cancel')),
  CONSTRAINT paid_events_have_price EXPECT (event_type NOT IN ('upgrade', 'renew') OR price_usd > 0)
)
COMMENT 'One row per billing event. Prices are fictional: plus 29.99, premium 49.99 a month.'
AS SELECT
  subscription_event_id,
  member_id,
  CAST(event_ts AS TIMESTAMP) AS event_ts,
  event_type,
  plan,
  CAST(price_usd AS DECIMAL(6, 2)) AS price_usd,
  _ingested_at AS ingested_at
FROM STREAM(hearth_bronze.subscription_events);
