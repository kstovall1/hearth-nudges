-- How many members Hearth can still reach by push, and how many are active, each day.
-- This is where the cost of over-sending shows up: reach only ever goes down.
CREATE OR REFRESH MATERIALIZED VIEW hearth_gold.reach_daily
COMMENT 'One row per UTC day. reachable is members who opted in to push and have not turned it off or uninstalled.'
AS WITH days AS (
  SELECT DISTINCT activity_date AS day FROM hearth_silver.member_daily_activity
),
opt_outs AS (
  SELECT member_id, min(to_date(opted_out_ts)) AS opted_out_date, bool_or(uninstalled) AS uninstalled
  FROM hearth_silver.notification_funnel
  WHERE opted_out
  GROUP BY member_id
),
counts AS (
  SELECT
    d.day,
    count_if(to_date(m.signup_ts) <= d.day) AS members,
    count_if(to_date(m.signup_ts) <= d.day AND m.push_opt_in) AS opted_in,
    count_if(o.opted_out_date <= d.day) AS opted_out_to_date,
    count_if(o.opted_out_date <= d.day AND o.uninstalled) AS uninstalled_to_date,
    count_if(o.opted_out_date = d.day) AS opted_out_today
  FROM days d
  CROSS JOIN hearth_silver.members m
  LEFT JOIN opt_outs o USING (member_id)
  GROUP BY d.day
),
activity AS (
  SELECT activity_date AS day, count_if(sessions > 0) AS active_members, sum(sessions) AS sessions
  FROM hearth_silver.member_daily_activity
  GROUP BY activity_date
),
pushes AS (
  SELECT to_date(delivered_ts) AS day, count(*) AS pushes_delivered, count_if(campaign_type = 'blast') AS blast_pushes
  FROM hearth_silver.notification_funnel
  WHERE delivered
  GROUP BY to_date(delivered_ts)
)
SELECT
  m.day,
  m.members,
  m.opted_in,
  m.opted_in - m.opted_out_to_date AS reachable,
  m.opted_out_today,
  m.opted_out_to_date,
  m.uninstalled_to_date,
  a.active_members,
  a.sessions,
  coalesce(p.pushes_delivered, 0) AS pushes_delivered,
  coalesce(p.blast_pushes, 0) AS blast_pushes
FROM counts m
LEFT JOIN activity a USING (day)
LEFT JOIN pushes p USING (day);
