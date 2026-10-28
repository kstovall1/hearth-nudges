-- One row per member per day, with features that only use data up to the end of that day.
-- gender_identity and seeking are left out on purpose: they are sensitive and must not drive nudges.
-- Trailing windows are short in the first 4 weeks of data, so history_days says how much history a row has.
CREATE OR REFRESH MATERIALIZED VIEW hearth_gold.member_daily_features
CLUSTER BY AUTO
COMMENT 'Member features as of the end of feature_date (UTC). Inputs to the lapse model and the nudge picker. No sensitive attributes.'
AS WITH bounds AS (
  SELECT min(activity_date) AS first_date, max(activity_date) AS last_date
  FROM hearth_silver.member_daily_activity
),
spine AS (
  SELECT m.member_id, explode(sequence(greatest(to_date(m.signup_ts), b.first_date), b.last_date)) AS feature_date
  FROM hearth_silver.members m
  CROSS JOIN bounds b
),
pushes AS (
  SELECT member_id, to_date(delivered_ts) AS push_date, count(*) AS pushes, count_if(opened) AS opens
  FROM hearth_silver.notification_funnel
  WHERE delivered
  GROUP BY member_id, to_date(delivered_ts)
),
opt_outs AS (
  SELECT member_id, min(to_date(opted_out_ts)) AS opted_out_date, bool_or(uninstalled) AS uninstalled
  FROM hearth_silver.notification_funnel
  WHERE opted_out
  GROUP BY member_id
),
plan_changes AS (
  -- The plan after the last billing event of the day. Trials don't change the plan.
  SELECT
    member_id,
    to_date(event_ts) AS change_date,
    max_by(CASE WHEN event_type = 'cancel' THEN 'free' ELSE plan END, event_ts) AS plan_after
  FROM hearth_silver.subscription_events
  WHERE event_type IN ('upgrade', 'renew', 'cancel')
  GROUP BY member_id, to_date(event_ts)
),
daily AS (
  SELECT
    s.member_id,
    s.feature_date,
    coalesce(a.sessions, 0) AS sessions,
    coalesce(a.push_sessions, 0) AS push_sessions,
    coalesce(a.profile_views, 0) AS profile_views,
    coalesce(a.likes_sent, 0) AS likes_sent,
    coalesce(a.likes_received, 0) AS likes_received,
    coalesce(a.matches, 0) AS matches,
    coalesce(a.messages_sent, 0) AS messages_sent,
    coalesce(a.messages_received, 0) AS messages_received,
    coalesce(p.pushes, 0) AS pushes,
    coalesce(p.opens, 0) AS opens,
    a.last_session_ts,
    pc.plan_after
  FROM spine s
  LEFT JOIN hearth_silver.member_daily_activity a
    ON a.member_id = s.member_id AND a.activity_date = s.feature_date
  LEFT JOIN pushes p
    ON p.member_id = s.member_id AND p.push_date = s.feature_date
  LEFT JOIN plan_changes pc
    ON pc.member_id = s.member_id AND pc.change_date = s.feature_date
),
rolled AS (
  SELECT
    member_id,
    feature_date,
    sessions AS sessions_1d,
    sum(sessions) OVER w7 AS sessions_7d,
    sum(sessions) OVER prev7 AS sessions_prev_7d,
    sum(sessions) OVER w14 AS sessions_14d,
    sum(sessions) OVER w28 AS sessions_28d,
    sum(push_sessions) OVER w28 AS push_sessions_28d,
    sum(profile_views) OVER w7 AS profile_views_7d,
    sum(likes_sent) OVER w7 AS likes_sent_7d,
    sum(likes_received) OVER w7 AS likes_received_7d,
    sum(matches) OVER w14 AS matches_14d,
    sum(messages_sent) OVER w14 AS messages_sent_14d,
    sum(messages_received) OVER w14 AS messages_received_14d,
    sum(messages_sent) OVER w7 AS messages_sent_7d,
    sum(messages_sent) OVER prev7 AS messages_sent_prev_7d,
    sum(pushes) OVER w7 AS pushes_7d,
    sum(opens) OVER w28 AS opens_28d,
    sum(pushes) OVER w28 AS pushes_28d,
    max(last_session_ts) OVER upto AS last_session_ts,
    last_value(plan_after, true) OVER upto AS plan_change
  FROM daily
  WINDOW
    w7 AS (PARTITION BY member_id ORDER BY feature_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW),
    prev7 AS (PARTITION BY member_id ORDER BY feature_date ROWS BETWEEN 13 PRECEDING AND 7 PRECEDING),
    w14 AS (PARTITION BY member_id ORDER BY feature_date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW),
    w28 AS (PARTITION BY member_id ORDER BY feature_date ROWS BETWEEN 27 PRECEDING AND CURRENT ROW),
    upto AS (PARTITION BY member_id ORDER BY feature_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
)
SELECT
  r.member_id,
  r.feature_date,
  datediff(r.feature_date, b.first_date) + 1 AS history_days,
  datediff(r.feature_date, to_date(m.signup_ts)) AS tenure_days,
  datediff(r.feature_date, to_date(greatest(r.last_session_ts, m.last_active_ts))) AS days_since_last_session,
  r.sessions_1d,
  r.sessions_7d,
  coalesce(r.sessions_prev_7d, 0) AS sessions_prev_7d,
  r.sessions_7d - coalesce(r.sessions_prev_7d, 0) AS sessions_trend_7d,
  r.sessions_14d,
  r.sessions_28d,
  r.push_sessions_28d,
  r.profile_views_7d,
  r.likes_sent_7d,
  r.likes_received_7d,
  r.matches_14d,
  r.messages_sent_7d,
  coalesce(r.messages_sent_prev_7d, 0) AS messages_sent_prev_7d,
  r.messages_sent_14d,
  r.messages_received_14d,
  round(r.messages_sent_14d / greatest(r.messages_received_14d, 1), 3) AS reply_ratio_14d,
  r.pushes_7d,
  r.pushes_28d,
  round(r.opens_28d / greatest(r.pushes_28d, 1), 3) AS open_rate_28d,
  m.push_opt_in AND (o.opted_out_date IS NULL OR o.opted_out_date > r.feature_date) AS reachable,
  coalesce(o.uninstalled AND o.opted_out_date <= r.feature_date, false) AS uninstalled,
  coalesce(r.plan_change, m.plan_at_export) AS plan,
  m.age_band,
  m.platform,
  m.metro,
  m.market_size,
  m.timezone
FROM rolled r
JOIN hearth_silver.members m USING (member_id)
LEFT JOIN opt_outs o USING (member_id)
CROSS JOIN bounds b;
