"""The Hearth Campaigns Genie Agent, as code. scripts/deploy_genie_space.py builds the serialized space from
this file, checks every SQL statement on the warehouse, then creates or updates the space by title.

Sources are the four metric views in src/hearth_semantic/metric_views.sql plus the nudge test readout. All of
them are aggregates: Genie can't reach a member-level row, PII or a sensitive attribute.
"""

import uuid

CATALOG = "katie_febar_catalog"
G = f"{CATALOG}.hearth_gold"
TITLE = "Hearth Campaigns"
DESCRIPTION = (
    "Answers Hearth's lifecycle team's questions about push campaigns: which nudges bring members back compared "
    "with the holdout, opt-outs and reach, lapse risk by cohort, and what the nudge picker plans to send. "
    "Aggregates only, no member-level data."
)

SAMPLE_QUESTIONS = [
    "Which nudges brought back the most members last week?",
    "What's the opt-out rate by nudge type?",
    "Show lapse risk by cohort",
    "When did opt-outs start rising?",
    "How many members will we nudge tomorrow, and why are the rest held?",
    "Where does a nudge do more harm than good?",
]

TEXT_INSTRUCTIONS = """## PURPOSE
- Answer the lifecycle team's questions about push campaigns for Hearth, a dating app. Users are marketers and growth leaders, not analysts.

## DISAMBIGUATION
- "Brought back", "worked" or "drove returns" means Extra Returns vs Holdout or Uplift vs Holdout from the nudge test, not raw Returns, because most members come back without a push.
- Relative periods like "last week" or "yesterday" are relative to the latest Decision Date in the data. If the period has no data or only part of it, say so and use the latest full period instead.
- For lapse risk "now" or "by cohort", use the latest Score Date only.
- "Tomorrow's plan" means the latest Decision Date in nudge_plan_metrics.

## DATA QUALITY NOTES
- Returns only count decisions whose 72 hours are over, so the last 3 days of decisions have fewer returns than they will.
- Blasts have no holdout. Uplift and Extra Returns vs Holdout are null for blasts, and that's expected.
- The blast went from weekly to three times a week from 2026-10-01.
- The first week in the data starts on 2026-08-28, a Friday, and the latest week is usually partial. Compare full weeks only when describing trends, and say which partial weeks were left out.

## CONSTRAINTS
- There is no member-level data, no names or contact details, and no gender or orientation. Say so if asked, and never guess them.

## Instructions you must follow when providing summaries
- State the date range used.
- Give rates as percentages with one decimal place, and uplift in percentage points.
- When reporting uplift, say it comes from the randomized nudge test against its holdout."""

# (question, sql, usage guidance). One per query shape the agent needs.
EXAMPLE_SQLS = [
    (
        "Which nudges added the most returns over the holdout in the latest full week?",
        f"""WITH latest AS (
  SELECT date_sub(DATE_TRUNC('WEEK', max(decision_date)), 7) AS week_start FROM {G}.campaign_daily_kpis
)
SELECT `Nudge Type`,
  MEASURE(`Extra Returns vs Holdout`) AS extra_returns,
  MEASURE(`Uplift vs Holdout`) AS uplift_pts,
  MEASURE(`Test Treated Decisions`) AS pushes_sent
FROM {G}.campaign_metrics
WHERE `Campaign Type` = 'nudge_test' AND `Decision Week` = (SELECT week_start FROM latest)
GROUP BY ALL
ORDER BY extra_returns DESC""",
        "Ranking nudges by causal effect in a period relative to the latest data.",
    ),
    (
        "Opt-out rate and pushes delivered by campaign type and nudge",
        f"""SELECT `Campaign Type`, `Nudge Type`,
  MEASURE(`Pushes Delivered`) AS pushes_delivered,
  MEASURE(`Opt-out Rate`) AS opt_out_rate
FROM {G}.campaign_metrics
GROUP BY ALL
ORDER BY opt_out_rate DESC""",
        "Rates grouped by dimensions. Rates come from the governed measures, never recomputed from counts.",
    ),
    (
        "Weekly reach, opt-outs and push volume",
        f"""SELECT `Week`,
  MEASURE(`Reachable Members at Start`) AS reachable_at_start,
  MEASURE(`Reachable Members`) AS reachable_at_end,
  MEASURE(`Opt-outs`) AS opt_outs,
  MEASURE(`Pushes per Active Member`) AS pushes_per_active_member
FROM {G}.reach_metrics
GROUP BY ALL
ORDER BY `Week`""",
        "Trends over time for reach and fatigue.",
    ),
    (
        "Average lapse risk and high-risk members by age band and plan, latest score date",
        f"""SELECT `Age Band`, `Plan`,
  MEASURE(`Members Scored`) AS members,
  MEASURE(`Mean Lapse Risk`) AS mean_lapse_risk,
  MEASURE(`High-Risk Members`) AS high_risk_members
FROM {G}.lapse_risk_metrics
WHERE `Score Date` = (SELECT max(feature_date) FROM {G}.member_lapse_scores)
GROUP BY ALL
ORDER BY mean_lapse_risk DESC""",
        "Cohort questions about lapse risk. Always filter to one Score Date.",
    ),
    (
        "What did the picker decide for the latest plan date, by action and reason?",
        f"""SELECT `Decision Date`, `Action`, coalesce(`Hold Reason`, `Nudge Type`) AS reason_or_nudge,
  MEASURE(`Members`) AS members,
  MEASURE(`Expected Extra Comebacks`) AS expected_extra_comebacks
FROM {G}.nudge_plan_metrics
WHERE `Decision Date` = (SELECT max(decision_date) FROM {G}.nudge_decisions)
GROUP BY ALL
ORDER BY `Action` DESC, members DESC""",
        "The picker's plan for the next send day.",
    ),
    (
        "In which segments does a nudge lower the return rate or raise opt-outs?",
        f"""SELECT nudge_effects.nudge_type, nudge_effects.dimension, nudge_effects.segment,
  nudge_effects.uplift_pts, nudge_effects.opt_out_ratio, nudge_effects.n_holdout
FROM {G}.nudge_segment_effects AS nudge_effects
WHERE nudge_effects.chosen AND (nudge_effects.uplift_pts < 0 OR nudge_effects.opt_out_ratio > 1.3)
ORDER BY nudge_effects.uplift_pts""",
        "Segment-level test readout. chosen marks the segment cut the picker uses for each nudge.",
    ),
]

# (question, checked SQL). Different wording and shapes from the examples above.
BENCHMARKS = [
    ("What's the uplift of match_waiting over the holdout?",
     f"SELECT MEASURE(`Uplift vs Holdout`) AS uplift_pts FROM {G}.campaign_metrics WHERE `Nudge Type` = 'match_waiting'"),
    ("Total opt-outs by week",
     f"SELECT `Week`, MEASURE(`Opt-outs`) AS opt_outs FROM {G}.reach_metrics GROUP BY ALL ORDER BY `Week`"),
    ("Which age band has the highest average lapse risk on the latest score date?",
     f"SELECT `Age Band`, MEASURE(`Mean Lapse Risk`) AS mean_lapse_risk FROM {G}.lapse_risk_metrics "
     f"WHERE `Score Date` = (SELECT max(feature_date) FROM {G}.member_lapse_scores) GROUP BY ALL ORDER BY mean_lapse_risk DESC LIMIT 1"),
    ("How many sends did the picker plan for 2026-10-28?",
     f"SELECT MEASURE(`Sends`) AS sends FROM {G}.nudge_plan_metrics WHERE `Decision Date` = DATE'2026-10-28'"),
    ("Compare the opt-out rate of blasts with the nudge test",
     f"SELECT `Campaign Type`, MEASURE(`Opt-out Rate`) AS opt_out_rate FROM {G}.campaign_metrics GROUP BY ALL ORDER BY `Campaign Type`"),
    ("Holdout return rate for we_miss_you by days away",
     f"SELECT `Days Away`, MEASURE(`Holdout Return Rate`) AS holdout_return_rate FROM {G}.campaign_metrics "
     f"WHERE `Nudge Type` = 'we_miss_you' AND `Campaign Type` = 'nudge_test' GROUP BY ALL ORDER BY `Days Away`"),
    ("How many members could we reach by push at the end of October 2026?",
     f"SELECT MEASURE(`Reachable Members`) AS reachable_members FROM {G}.reach_metrics WHERE `Day` <= DATE'2026-10-31'"),
    ("In which market size does new_people_nearby hurt returns?",
     f"SELECT segment, uplift_pts FROM {G}.nudge_segment_effects WHERE nudge_type = 'new_people_nearby' AND dimension = 'market_size' AND uplift_pts < 0"),
    ("Pushes per active member per day, by week",
     f"SELECT `Week`, MEASURE(`Pushes per Active Member`) AS pushes_per_active_member FROM {G}.reach_metrics GROUP BY ALL ORDER BY `Week`"),
    ("How many free members are at high lapse risk right now?",
     f"SELECT MEASURE(`High-Risk Members`) AS high_risk_members FROM {G}.lapse_risk_metrics "
     f"WHERE `Plan` = 'free' AND `Score Date` = (SELECT max(feature_date) FROM {G}.member_lapse_scores)"),
    ("What share of members is the picker nudging on its latest plan?",
     f"SELECT MEASURE(`Send Rate`) AS send_rate FROM {G}.nudge_plan_metrics "
     f"WHERE `Decision Date` = (SELECT max(decision_date) FROM {G}.nudge_decisions)"),
    ("Extra returns vs holdout by nudge type in October 2026",
     f"SELECT `Nudge Type`, MEASURE(`Extra Returns vs Holdout`) AS extra_returns FROM {G}.campaign_metrics "
     f"WHERE `Campaign Type` = 'nudge_test' AND `Decision Date` BETWEEN DATE'2026-10-01' AND DATE'2026-10-31' "
     f"GROUP BY ALL ORDER BY extra_returns DESC"),
]

CATEGORICAL = {
    f"{G}.campaign_metrics": ["Age Band", "Arm", "Campaign Type", "Days Away", "Market Size", "Nudge Type", "Platform"],
    f"{G}.lapse_risk_metrics": ["Age Band", "Market Size", "Plan", "Platform", "Risk Band", "Tenure Group"],
    f"{G}.nudge_plan_metrics": ["Action", "Days Away", "Hold Reason", "Nudge Type", "Risk Band"],
    f"{G}.reach_metrics": [],
}

READOUT_COLUMNS = [
    {"column_name": "chosen", "description": ["True for the segment cut the picker uses for this nudge."]},
    {"column_name": "computed_at", "exclude": True},
    {"column_name": "dimension", "description": ["How the test is cut: all, age_band, tenure_group, market_size or days_away_bucket."],
     "enable_format_assistance": True, "enable_entity_matching": True},
    {"column_name": "n_holdout", "description": ["Holdout decisions in the segment."]},
    {"column_name": "n_treated", "description": ["Decisions that sent the push in the segment."]},
    {"column_name": "nudge_type", "enable_format_assistance": True, "enable_entity_matching": True},
    {"column_name": "opt_out_ratio", "description": ["Opt-out rate in the segment divided by the test's average. Above 1 means the nudge annoys this segment more."]},
    {"column_name": "opt_out_treated_pct", "description": ["Opt-outs per delivered push in the segment, in percent."]},
    {"column_name": "return_holdout_pct", "description": ["72-hour return rate of the holdout, in percent."]},
    {"column_name": "return_treated_pct", "description": ["72-hour return rate of members who got the push, in percent."]},
    {"column_name": "segment", "description": ["The segment value, like 18-24 or small."],
     "enable_format_assistance": True, "enable_entity_matching": True},
    {"column_name": "uplift_low_pts", "description": ["Lower bound of the uplift at 95% one-sided confidence."]},
    {"column_name": "uplift_pts", "description": ["Return-rate points the push added over the holdout in this segment. Negative means it did harm."],
     "synonyms": ["uplift", "lift", "effect"]},
]


def _id(text):
    return uuid.uuid5(uuid.NAMESPACE_URL, f"hearth-genie/{text}").hex


def serialized_space():
    metric_views = [{
        "identifier": mv,
        "column_configs": [{"column_name": c, "enable_format_assistance": True, "enable_entity_matching": True}
                           for c in sorted(cols)],
    } for mv, cols in sorted(CATEGORICAL.items())]
    for mv in metric_views:
        if not mv["column_configs"]:
            del mv["column_configs"]
    return {
        "version": 2,
        "config": {"sample_questions": sorted(
            [{"id": _id("q/" + q), "question": [q]} for q in SAMPLE_QUESTIONS], key=lambda x: x["id"])},
        "data_sources": {
            "metric_views": metric_views,
            "tables": [{"identifier": f"{G}.nudge_segment_effects", "column_configs": READOUT_COLUMNS}],
        },
        "instructions": {
            "text_instructions": [{"id": _id("text"), "content": [TEXT_INSTRUCTIONS]}],
            "example_question_sqls": sorted([
                {"id": _id("ex/" + q), "question": [q], "sql": [sql], "usage_guidance": [guide]}
                for q, sql, guide in EXAMPLE_SQLS], key=lambda x: x["id"]),
        },
        "benchmarks": {"questions": sorted([
            {"id": _id("bench/" + q), "question": [q], "answer": [{"format": "SQL", "content": [sql]}]}
            for q, sql in BENCHMARKS], key=lambda x: x["id"])},
    }
