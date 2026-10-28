-- Campaign results by day, keeping the dimensions marketers slice by. Counts only: rates are
-- computed at query time so they add up correctly across any grouping.
CREATE OR REFRESH MATERIALIZED VIEW hearth_gold.campaign_daily_kpis
CLUSTER BY AUTO
COMMENT 'Decision counts and outcomes per day, campaign, nudge, arm and member segment. For a return rate use returned_72h_complete / decisions_complete, which leaves out decisions whose 72 hours are not over.'
AS SELECT
  n.decision_date,
  n.campaign_type,
  n.campaign_id,
  n.nudge_type,
  n.arm,
  n.days_away_bucket,
  m.market_size,
  m.age_band,
  m.platform,
  count(*) AS decisions,
  count_if(n.delivered) AS delivered,
  count_if(n.opened) AS opened,
  count_if(n.returned_72h) AS returned_72h,
  count_if(n.window_complete) AS decisions_complete,
  count_if(n.returned_72h AND n.window_complete) AS returned_72h_complete,
  count_if(n.opted_out) AS opted_out,
  count_if(n.opted_out AND NOT n.uninstalled) AS push_disabled,
  count_if(n.uninstalled) AS uninstalled,
  count_if(n.upgraded_14d) AS upgraded_14d
FROM hearth_silver.notification_funnel n
JOIN hearth_silver.members m USING (member_id)
GROUP BY ALL;
