-- Governed KPI definitions for Hearth, as Unity Catalog metric views in hearth_gold. Genie, the lifecycle
-- console and any dashboard read these, so a return rate or an opt-out rate means the same thing everywhere.
-- Every view exposes aggregates only: there's no member_id dimension, and nothing reads the members table,
-- so no PII or sensitive attribute is reachable through them.
-- The YAML can't take the :catalog parameter, so sources name katie_febar_catalog directly.
USE CATALOG IDENTIFIER(:catalog);

CREATE OR REPLACE VIEW hearth_gold.campaign_metrics
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
source: katie_febar_catalog.hearth_gold.campaign_daily_kpis
comment: >-
  Push campaign results: blasts and the randomized nudge test. One source row per day, campaign, nudge, arm
  and member segment. Returns count a session within 72 hours of the decision, only for decisions whose 72
  hours are over. Only the nudge test has a holdout, so uplift and extra returns describe the test, never
  the blast.
dimensions:
  - name: Decision Date
    expr: decision_date
    comment: UTC day the messaging tool made the decision, at 07:00 UTC.
    display_name: Decision date
    synonyms: [date, day, send date]
  - name: Decision Week
    expr: "DATE_TRUNC('WEEK', decision_date)"
    comment: Monday of the week the decision was made.
    display_name: Decision week
    synonyms: [week]
  - name: Campaign Type
    expr: campaign_type
    comment: "blast is the weekly we_miss_you push to every reachable member (three times a week from 2026-10-01). nudge_test is the randomized test of five nudges with a 15% holdout."
    display_name: Campaign type
    synonyms: [program, campaign kind, blast or test]
  - name: Campaign
    expr: campaign_id
    comment: "One id per blast send (blast_YYYYMMDD) or nudge_test_v1 for the test."
    display_name: Campaign
    synonyms: [campaign id, campaign name]
  - name: Nudge Type
    expr: nudge_type
    comment: "new_likes, match_waiting, profile_tip, new_people_nearby or we_miss_you. Holdout rows keep the nudge they would have got."
    display_name: Nudge type
    synonyms: [nudge, message type, notification type, push type]
  - name: Arm
    expr: arm
    comment: treatment got the push. holdout was randomly held back as the control. Blasts are all treatment.
    display_name: Arm
    synonyms: [test group, control group, holdout, treatment]
  - name: Days Away
    expr: days_away_bucket
    comment: "Days since the member's last session at decision time: active, 2 days, 3-7 days, 8-14 days or 15+ days."
    display_name: Days away
    synonyms: [days inactive, inactivity, lapse length, days since last session]
  - name: Market Size
    expr: market_size
    comment: Size of the member's metro market, large, medium or small.
    display_name: Market size
    synonyms: [market, metro size]
  - name: Age Band
    expr: age_band
    comment: "18-24, 25-29, 30-34, 35-44 or 45+."
    display_name: Age band
    synonyms: [age, age group]
  - name: Platform
    expr: platform
    comment: ios or android.
    display_name: Platform
    synonyms: [device, os]
measures:
  - name: Decisions
    expr: SUM(decisions)
    comment: Send-or-hold decisions, holdouts included.
    display_name: Decisions
  - name: Pushes Delivered
    expr: SUM(delivered)
    comment: Pushes the provider delivered. Holdouts get none.
    display_name: Pushes delivered
    synonyms: [pushes, notifications sent, sends, delivered]
  - name: Opens
    expr: SUM(opened)
    display_name: Opens
    synonyms: [opened pushes]
  - name: Decisions With Complete Window
    expr: SUM(decisions_complete)
    comment: Decisions whose 72 hours are over, the base for return rates.
    display_name: Decisions with a complete 72-hour window
  - name: Returns
    expr: SUM(returned_72h_complete)
    comment: Decisions followed by a session within 72 hours, counted only once the 72 hours are over.
    display_name: Returns within 72 hours
    synonyms: [came back, comebacks, reactivations, returned]
  - name: Return Rate
    expr: "MEASURE(`Returns`) / MEASURE(`Decisions With Complete Window`)"
    comment: Share of decisions followed by a session within 72 hours.
    display_name: Return rate (72 hours)
    synonyms: [comeback rate, reactivation rate]
    format: {type: percentage, decimal_places: {type: max, places: 1}}
  - name: Open Rate
    expr: "MEASURE(`Opens`) / MEASURE(`Pushes Delivered`)"
    display_name: Open rate
    format: {type: percentage, decimal_places: {type: max, places: 1}}
  - name: Opt-outs
    expr: SUM(opted_out)
    comment: Pushes followed by the member turning off push or uninstalling. Either way Hearth can't reach them again.
    display_name: Opt-outs
    synonyms: [unsubscribes, push disabled or uninstalled, lost reach]
  - name: Uninstalls
    expr: SUM(uninstalled)
    display_name: Uninstalls
  - name: Opt-out Rate
    expr: "MEASURE(`Opt-outs`) / MEASURE(`Pushes Delivered`)"
    comment: Opt-outs per delivered push.
    display_name: Opt-out rate
    synonyms: [unsubscribe rate, churn from push]
    format: {type: percentage, decimal_places: {type: max, places: 2}}
  - name: Upgrades
    expr: SUM(upgraded_14d)
    comment: Members who came back within 72 hours and upgraded to a paid plan within 14 days of returning.
    display_name: Upgrades within 14 days
    synonyms: [paid conversions, conversions]
  - name: Test Treated Decisions
    expr: "SUM(decisions_complete) FILTER (WHERE campaign_type = 'nudge_test' AND arm = 'treatment')"
    comment: Nudge test decisions that sent a push and whose 72 hours are over.
    display_name: Test decisions sent
  - name: Treated Return Rate
    expr: "SUM(returned_72h_complete) FILTER (WHERE campaign_type = 'nudge_test' AND arm = 'treatment') / SUM(decisions_complete) FILTER (WHERE campaign_type = 'nudge_test' AND arm = 'treatment')"
    comment: Return rate of nudge test members who got the push.
    display_name: Return rate, sent
    format: {type: percentage, decimal_places: {type: max, places: 1}}
  - name: Holdout Return Rate
    expr: "SUM(returned_72h_complete) FILTER (WHERE campaign_type = 'nudge_test' AND arm = 'holdout') / SUM(decisions_complete) FILTER (WHERE campaign_type = 'nudge_test' AND arm = 'holdout')"
    comment: Return rate of nudge test members randomly held back. What would have happened without the push.
    display_name: Return rate, holdout
    synonyms: [control return rate, baseline return rate]
    format: {type: percentage, decimal_places: {type: max, places: 1}}
  - name: Uplift vs Holdout
    expr: "100 * (MEASURE(`Treated Return Rate`) - MEASURE(`Holdout Return Rate`))"
    comment: Points of return rate the push added over the holdout. The causal effect of the nudge. Null for blasts, which have no holdout.
    display_name: Uplift vs holdout (points)
    synonyms: [uplift, lift, incremental return rate, effect]
    format: {type: number, decimal_places: {type: max, places: 1}}
  - name: Extra Returns vs Holdout
    expr: "(MEASURE(`Treated Return Rate`) - MEASURE(`Holdout Return Rate`)) * MEASURE(`Test Treated Decisions`)"
    comment: Members brought back by the push who would not have come back without it, uplift times decisions sent. Nudge test only.
    display_name: Extra returns vs holdout
    synonyms: [incremental returns, members brought back, incremental comebacks]
    format: {type: number, decimal_places: {type: max, places: 0}}
$$;

CREATE OR REPLACE VIEW hearth_gold.reach_metrics
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
source: katie_febar_catalog.hearth_gold.reach_daily
comment: >-
  How many members Hearth can still reach by push, and how hard it pushes them, by day. Reach only goes
  down: every opt-out or uninstall is a member the lifecycle team can't nudge again. The blast tripled from
  2026-10-01, which is when opt-outs jumped.
dimensions:
  - name: Day
    expr: day
    comment: UTC day.
    display_name: Day
    synonyms: [date]
  - name: Week
    expr: "DATE_TRUNC('WEEK', day)"
    comment: Monday of the week.
    display_name: Week
measures:
  - name: Reachable Members
    expr: MAX_BY(reachable, day)
    comment: Members who allow push and haven't turned it off or uninstalled, on the last day of the period.
    display_name: Reachable members (end of period)
    synonyms: [reach, addressable members, push audience]
    format: {type: number, decimal_places: {type: max, places: 0}}
  - name: Reachable Members at Start
    expr: MIN_BY(reachable, day)
    comment: Reachable members on the first day of the period.
    display_name: Reachable members (start of period)
  - name: Opt-outs
    expr: SUM(opted_out_today)
    comment: Members who turned off push or uninstalled during the period.
    display_name: Opt-outs
    synonyms: [unsubscribes, lost reach]
  - name: Pushes Delivered
    expr: SUM(pushes_delivered)
    display_name: Pushes delivered
    synonyms: [pushes, notifications sent]
  - name: Blast Pushes
    expr: SUM(blast_pushes)
    display_name: Blast pushes
  - name: Average Daily Active Members
    expr: AVG(active_members)
    comment: Members with at least one session, averaged over the days in the period.
    display_name: Average daily active members
    synonyms: [DAU, daily actives]
  - name: Pushes per Active Member
    expr: SUM(pushes_delivered) / SUM(active_members)
    comment: Pushes delivered per active member per day. Lower is better if returns hold up.
    display_name: Pushes per active member per day
    synonyms: [push intensity, notifications per active member]
    format: {type: number, decimal_places: {type: max, places: 2}}
$$;

CREATE OR REPLACE VIEW hearth_gold.lapse_risk_metrics
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
source: katie_febar_catalog.hearth_gold.member_lapse_scores
joins:
  - name: dim_features
    source: katie_febar_catalog.hearth_gold.member_daily_features
    'on': source.member_id = dim_features.member_id AND source.feature_date = dim_features.feature_date
comment: >-
  Lapse risk from the lapse model: the chance a member active in the last 14 days has no session in the next
  7. One source row per member per scored day, so count members on a single Score Date, usually the latest.
dimensions:
  - name: Score Date
    expr: source.feature_date
    comment: The day the features describe. The risk is for the 7 days after it. Pick one date when counting members.
    display_name: Score date
    synonyms: [date, as of]
  - name: Risk Band
    expr: source.risk_band
    comment: "high (0.30 and up), medium (0.12 to 0.30) or low."
    display_name: Risk band
    synonyms: [risk level, risk tier]
  - name: Age Band
    expr: dim_features.age_band
    display_name: Age band
    synonyms: [age, age group, cohort]
  - name: Plan
    expr: dim_features.plan
    comment: free, plus or premium on that day.
    display_name: Plan
    synonyms: [subscription, tier, paid or free]
  - name: Market Size
    expr: dim_features.market_size
    display_name: Market size
  - name: Platform
    expr: dim_features.platform
    display_name: Platform
  - name: Tenure Group
    expr: "CASE WHEN dim_features.tenure_days < 30 THEN 'first 30 days' ELSE 'after 30 days' END"
    comment: Whether the member joined in the last 30 days.
    display_name: Tenure group
    synonyms: [new members, tenure]
measures:
  - name: Members Scored
    expr: COUNT(1)
    comment: Member-days scored. One per member when filtered to a single Score Date.
    display_name: Members scored
    synonyms: [members, scored members]
  - name: Mean Lapse Risk
    expr: AVG(source.lapse_risk)
    display_name: Average lapse risk
    synonyms: [lapse risk, churn risk, risk]
    format: {type: percentage, decimal_places: {type: max, places: 1}}
  - name: High-Risk Members
    expr: "COUNT(1) FILTER (WHERE source.risk_band = 'high')"
    display_name: High-risk members
    synonyms: [at-risk members, likely to lapse]
  - name: Share High Risk
    expr: "MEASURE(`High-Risk Members`) / MEASURE(`Members Scored`)"
    display_name: Share of members at high risk
    format: {type: percentage, decimal_places: {type: max, places: 1}}
  - name: Expected Lapses
    expr: SUM(source.lapse_risk)
    comment: Members expected to go quiet in the next 7 days, the sum of their risks.
    display_name: Expected lapses
    synonyms: [predicted lapses, expected churn]
    format: {type: number, decimal_places: {type: max, places: 0}}
$$;

CREATE OR REPLACE VIEW hearth_gold.nudge_plan_metrics
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
source: katie_febar_catalog.hearth_gold.nudge_decisions
comment: >-
  What the nudge picker decided for each reachable member: send a nudge at an hour, or hold with a reason.
  One source row per member per decision day. Estimated uplift comes from the nudge test, so expected extra
  comebacks is the picker's own forecast, not an outcome.
dimensions:
  - name: Decision Date
    expr: decision_date
    comment: The day the decisions are for.
    display_name: Decision date
    synonyms: [date, plan date, tomorrow]
  - name: Action
    expr: action
    comment: send or hold.
    display_name: Action
  - name: Hold Reason
    expr: hold_reason
    comment: "Why a member is held: active (used the app in the last 2 days), cooldown (nudged in the last 72 hours), likely_back (low lapse risk) or not_worth_it (no nudge's uplift beats its opt-out risk). Null for sends."
    display_name: Hold reason
    synonyms: [why held, reason]
  - name: Nudge Type
    expr: nudge_type
    comment: The nudge being sent. Null for holds.
    display_name: Nudge type
    synonyms: [nudge, message type]
  - name: Risk Band
    expr: risk_band
    comment: "Lapse risk band, or null for members already away too long to score."
    display_name: Risk band
  - name: Days Away
    expr: days_away_bucket
    display_name: Days away
    synonyms: [days inactive]
  - name: Send Hour
    expr: send_hour_local
    comment: Local hour the push goes out, 8 to 22, from the member's usual session time.
    display_name: Send hour (local)
    synonyms: [send time, hour]
  - name: Policy Version
    expr: policy_version
    display_name: Picker version
measures:
  - name: Members
    expr: COUNT(1)
    display_name: Members
  - name: Sends
    expr: "COUNT(1) FILTER (WHERE action = 'send')"
    display_name: Sends planned
    synonyms: [pushes planned, nudges]
  - name: Holds
    expr: "COUNT(1) FILTER (WHERE action = 'hold')"
    display_name: Holds
    synonyms: [held, not sent]
  - name: Send Rate
    expr: "MEASURE(`Sends`) / MEASURE(`Members`)"
    comment: Share of reachable members the picker sends a nudge to. Use this rather than dividing Sends by Members.
    display_name: Share of members nudged
    synonyms: [share nudged, percent nudged, share of members nudged, nudge rate, send share]
    format: {type: percentage, decimal_places: {type: max, places: 1}}
  - name: Expected Extra Comebacks
    expr: "SUM(estimated_uplift_pts) FILTER (WHERE action = 'send') / 100"
    comment: The picker's estimate of members its sends will bring back who wouldn't have come back anyway.
    display_name: Expected extra comebacks
    synonyms: [forecast comebacks, expected incremental returns]
    format: {type: number, decimal_places: {type: max, places: 0}}
  - name: Average Estimated Uplift
    expr: "AVG(estimated_uplift_pts) FILTER (WHERE action = 'send')"
    comment: Average estimated uplift of the planned sends, in points of 72-hour return rate.
    display_name: Average estimated uplift (points)
    format: {type: number, decimal_places: {type: max, places: 1}}
$$;
