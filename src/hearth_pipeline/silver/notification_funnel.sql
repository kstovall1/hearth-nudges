-- Every decision followed through: delivered, opened, came back within 72 hours, opted out, upgraded.
-- "Came back" is a session in the 72 hours after decided_ts, for both arms, so the holdout compares
-- like with like. Days away counts from the last session, or from last_active_ts in the members export
-- for members quiet since before the data starts.
CREATE OR REFRESH MATERIALIZED VIEW hearth_silver.notification_funnel (
  CONSTRAINT opened_only_if_delivered EXPECT (opened_ts IS NULL OR delivered_ts IS NOT NULL),
  CONSTRAINT holdout_never_delivered EXPECT (arm = 'treatment' OR delivered_ts IS NULL)
)
CLUSTER BY AUTO
COMMENT 'One row per decision with its outcome. Use window_complete to leave out decisions whose 72 hours are not over yet.'
AS WITH callbacks AS (
  SELECT
    decision_id,
    min(CASE WHEN event_type = 'delivered' THEN event_ts END) AS delivered_ts,
    min(CASE WHEN event_type = 'opened' THEN event_ts END) AS opened_ts,
    min(CASE WHEN event_type IN ('push_disabled', 'uninstalled') THEN event_ts END) AS opted_out_ts,
    bool_or(event_type = 'uninstalled') AS uninstalled
  FROM hearth_silver.notification_events
  GROUP BY decision_id
),
history AS (
  SELECT member_id, session_start_ts AS ts, source FROM hearth_silver.sessions
  UNION ALL
  SELECT member_id, last_active_ts, 'export' FROM hearth_silver.members
),
around AS (
  SELECT
    d.decision_id,
    max(CASE WHEN h.ts < d.decided_ts THEN h.ts END) AS last_active_before_ts,
    min(CASE WHEN h.ts > d.decided_ts AND h.ts <= d.decided_ts + INTERVAL 72 HOURS THEN h.ts END) AS returned_ts,
    min_by(h.source, CASE WHEN h.ts > d.decided_ts AND h.ts <= d.decided_ts + INTERVAL 72 HOURS THEN h.ts END)
      AS return_source
  FROM hearth_silver.notification_decisions d
  JOIN history h ON h.member_id = d.member_id
  GROUP BY d.decision_id
),
upgrades AS (
  SELECT a.decision_id, min(u.event_ts) AS upgraded_ts
  FROM around a
  JOIN hearth_silver.notification_decisions d USING (decision_id)
  JOIN hearth_silver.subscription_events u
    ON u.member_id = d.member_id
    AND u.event_type = 'upgrade'
    AND u.event_ts > a.returned_ts
    AND u.event_ts <= a.returned_ts + INTERVAL 14 DAYS
  GROUP BY a.decision_id
),
data_through AS (
  SELECT max(session_start_ts) AS ts FROM hearth_silver.sessions
)
SELECT
  d.decision_id,
  d.member_id,
  d.decided_ts,
  d.decision_date,
  d.campaign_id,
  d.campaign_type,
  d.nudge_type,
  d.arm,
  d.scheduled_send_ts,
  hour(from_utc_timestamp(d.scheduled_send_ts, m.timezone)) AS send_hour_local,
  d.copy_variant_id,
  round((unix_timestamp(d.decided_ts) - unix_timestamp(a.last_active_before_ts)) / 86400, 2) AS days_away,
  CASE
    WHEN a.last_active_before_ts IS NULL THEN 'new'
    WHEN d.decided_ts < a.last_active_before_ts + INTERVAL 2 DAYS THEN 'active'
    WHEN d.decided_ts < a.last_active_before_ts + INTERVAL 3 DAYS THEN '2 days'
    WHEN d.decided_ts < a.last_active_before_ts + INTERVAL 8 DAYS THEN '3-7 days'
    WHEN d.decided_ts < a.last_active_before_ts + INTERVAL 15 DAYS THEN '8-14 days'
    ELSE '15+ days'
  END AS days_away_bucket,
  c.delivered_ts,
  c.opened_ts,
  c.opted_out_ts,
  coalesce(c.uninstalled, false) AS uninstalled,
  c.delivered_ts IS NOT NULL AS delivered,
  c.opened_ts IS NOT NULL AS opened,
  c.opted_out_ts IS NOT NULL AS opted_out,
  a.returned_ts,
  a.return_source,
  a.returned_ts IS NOT NULL AS returned_72h,
  u.upgraded_ts IS NOT NULL AS upgraded_14d,
  d.decided_ts + INTERVAL 72 HOURS <= t.ts AS window_complete
FROM hearth_silver.notification_decisions d
LEFT JOIN hearth_silver.members m USING (member_id)
LEFT JOIN callbacks c USING (decision_id)
LEFT JOIN around a USING (decision_id)
LEFT JOIN upgrades u USING (decision_id)
CROSS JOIN data_through t;
