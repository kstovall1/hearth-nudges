# Databricks notebook source
# MAGIC %md
# MAGIC # Backtest the nudge picker
# MAGIC
# MAGIC Runs the picker's rules on two weeks of nudge test decisions it has never seen, and compares them with
# MAGIC the blast and with the random test, member for member.
# MAGIC
# MAGIC - **No leakage.** The readout, fatigue rates and send hours come only from data before the window.
# MAGIC - **Scored with `hearth_ml.generator_truth`**, the planted effect of every nudge for every test decision.
# MAGIC   That table is for evaluation only: the rules never read it. Eligibility also comes from it here, since
# MAGIC   the daily picker's eligibility from events is checked separately at the end.
# MAGIC - **Opt-outs are estimates** from the measured rates, the same way the picker estimates them.

# COMMAND ----------

dbutils.widgets.text("catalog", "katie_febar_catalog")
dbutils.widgets.text("value_of_reach", "2.0")
CATALOG = dbutils.widgets.get("catalog")

import numpy as np
import pandas as pd

import picker as P

RULES = P.Rules(value_of_reach=float(dbutils.widgets.get("value_of_reach")))
SEED = 7

from datetime import timedelta

start, end = spark.sql(f"""
  SELECT (SELECT date_add(min(feature_date), 1) FROM {CATALOG}.hearth_gold.member_lapse_scores),
         (SELECT max(decision_date) FROM {CATALOG}.hearth_gold.nudge_outcomes)
""").first()
known_through = start - timedelta(days=4)  # every 72-hour window up to here closed before the backtest starts
print(f"backtest decisions {start} to {end}; rules learn from decisions up to {known_through}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Rules, learned from before the window

# COMMAND ----------

past = spark.sql(f"""
  SELECT nudge_type, treated, returned_72h, opted_out, tenure_days, age_band, market_size, days_away_bucket
  FROM {CATALOG}.hearth_gold.nudge_outcomes
  WHERE features_as_of IS NOT NULL AND decision_date <= DATE'{known_through}'
""").toPandas()
effects = P.segment_effects(past, RULES)
chosen, tests = P.pick_dimensions(effects, RULES)
fatigue = P.fatigue_rates(spark.sql(f"""
  SELECT f.pushes_7d, n.opted_out FROM {CATALOG}.hearth_silver.notification_funnel n
  JOIN {CATALOG}.hearth_gold.member_daily_features f ON f.member_id = n.member_id AND f.feature_date = date_sub(n.decision_date, 1)
  WHERE n.campaign_type = 'nudge_test' AND n.delivered AND n.decision_date <= DATE'{known_through}'
""").toPandas())
hours, default_hour = P.preferred_hours(spark.sql(f"""
  SELECT member_id, start_hour_local AS hour, count(*) AS sessions FROM {CATALOG}.hearth_silver.sessions
  WHERE session_date < DATE'{start}' GROUP BY 1, 2
""").toPandas(), RULES)

# COMMAND ----------

# MAGIC %md
# MAGIC ## The decisions to replay

# COMMAND ----------

nudge_cols = ", ".join(f"t.true_uplift_{n}" for n in P.NUDGES)
rows = spark.sql(f"""
  SELECT o.decision_id, o.member_id, o.decision_date, o.nudge_type AS test_nudge, o.treated, o.send_hour_local AS test_hour,
    o.days_away, o.days_away_bucket, o.tenure_days, o.age_band, o.market_size, o.timezone, o.pushes_7d,
    s.lapse_risk, s.risk_band, t.preferred_hour_local AS true_hour, t.baseline_return_72h, {nudge_cols}
  FROM {CATALOG}.hearth_gold.nudge_outcomes o
  JOIN {CATALOG}.hearth_ml.generator_truth t USING (decision_id)
  LEFT JOIN {CATALOG}.hearth_gold.member_lapse_scores s ON s.member_id = o.member_id AND s.feature_date = o.features_as_of
  WHERE o.decision_date BETWEEN DATE'{start}' AND DATE'{end}' AND o.features_as_of IS NOT NULL
""").toPandas()
rows = rows.merge(hours, on="member_id", how="left")
rows["hour_source"] = rows["hour_source"].fillna("default")
rows["preferred_hour_local"] = rows["preferred_hour_local"].fillna(default_hour).astype(int)
rows["eligible_new_likes"] = rows["true_uplift_new_likes"].notna()
rows["eligible_match_waiting"] = rows["true_uplift_match_waiting"].notna()
rows["hours_since_last_nudge"] = np.nan  # the test already spaces decisions 3 days apart
print(f"{len(rows):,} test decisions to replay")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Scoring a policy
# MAGIC
# MAGIC The planted uplift applies in full within 2 hours of the member's true preferred hour, at 60% at other
# MAGIC daytime hours and 30% at night, and can't push the chance of coming back past 100%.

# COMMAND ----------

from datetime import datetime
from zoneinfo import ZoneInfo


def hour_factor(local_hour, true_hour):
    diff = np.abs(np.asarray(local_hour) - np.asarray(true_hour))
    diff = np.minimum(diff, 24 - diff)
    night = (np.asarray(local_hour) >= 23) | (np.asarray(local_hour) < 7)
    return np.where(diff <= 2, 1.0, np.where(night, 0.3, 0.6))


def utc_to_local_hour(dates, utc_hour, timezones):
    cache = {}
    out = []
    for d, tz in zip(dates, timezones):
        if (d, tz) not in cache:
            cache[(d, tz)] = datetime(d.year, d.month, d.day, utc_hour, tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(tz)).hour
        out.append(cache[(d, tz)])
    return np.array(out)


def opt_out_ratio(nudges, frame):
    """The picker's own opt-out ratio for each send's nudge and segment."""
    seg = P.add_segments(frame)
    ratio = np.full(len(frame), np.nan)
    for n in P.NUDGES:
        cells = effects[(effects["nudge_type"] == n) & (effects["dimension"] == chosen[n])].set_index("segment")
        pick = np.asarray(nudges) == n
        ratio[pick] = seg.loc[pick, chosen[n]].astype(str).map(cells["opt_out_ratio"]).fillna(1.0).to_numpy()
    return ratio


TRUE = rows[[f"true_uplift_{n}" for n in P.NUDGES]].to_numpy(dtype=float)
NUDGE_INDEX = {n: i for i, n in enumerate(P.NUDGES)}


def score(name, send, nudges, local_hours, frame=rows):
    send = np.asarray(send, bool)
    col = np.array([NUDGE_INDEX.get(n, 0) for n in nudges])
    true = np.where(send, TRUE[np.arange(len(frame)), col], np.nan)
    p0 = frame["baseline_return_72h"].to_numpy()
    lift = np.clip(np.nan_to_num(true) * hour_factor(local_hours, frame["true_hour"]), -p0, 1 - p0)
    comebacks = np.where(send, lift, 0.0)
    night = (np.asarray(local_hours) >= 23) | (np.asarray(local_hours) < 7)
    base = pd.Series(P.fatigue_bucket(frame["pushes_7d"])).map(fatigue).to_numpy()
    opt_outs = np.where(send, base * opt_out_ratio(nudges, frame) * np.where(night, 1.5, 1.0), 0.0)
    focus = frame["risk_band"].isin(["high", "medium"]).to_numpy() | frame["risk_band"].isna().to_numpy()
    return {
        "policy": name,
        "sends": int(send.sum()),
        "pct_of_members": round(100 * send.mean(), 1),
        "comebacks": round(comebacks.sum(), 1),
        "comebacks_per_1000_sends": round(1000 * comebacks.sum() / max(send.sum(), 1), 1),
        "est_opt_outs": round(opt_outs.sum(), 1),
        "net_comebacks": round(comebacks.sum() - RULES.value_of_reach * opt_outs.sum(), 1),
        # Comebacks from members likely to lapse (medium or high risk) or already lapsed (unscored)
        "at_risk_comebacks": round(comebacks[focus].sum(), 1),
        "net_at_risk_comebacks": round(comebacks[focus].sum() - RULES.value_of_reach * opt_outs.sum(), 1),
    }


# COMMAND ----------

# MAGIC %md
# MAGIC ## Policies

# COMMAND ----------

results = []
everyone = np.ones(len(rows), bool)

blast_hour = utc_to_local_hour(rows["decision_date"], 13, rows["timezone"])
results.append(score("blast: we_miss_you to everyone at 13:00 UTC", everyone, ["we_miss_you"] * len(rows), blast_hour))

rng = np.random.default_rng(SEED)
test_hour = np.where(rows["treated"], rows["test_hour"].fillna(-1), rng.integers(8, 23, len(rows))).astype(int)
results.append(score("nudge test: random nudge at a random hour, to everyone", everyone, rows["test_nudge"], test_hour))

loose = P.Rules(value_of_reach=0.0, z=-1e9, hold_low_risk=False, version="best nudge to everyone")
d_all = P.decide(rows, effects, chosen, fatigue, loose)
results.append(score("best estimated nudge to everyone it might help, at their usual hour", d_all["action"] == "send",
                     d_all["nudge_type"], rows["preferred_hour_local"]))

d = P.decide(rows, effects, chosen, fatigue, RULES)
results.append(score(f"picker (rules-v1, value of reach {RULES.value_of_reach})", d["action"] == "send",
                     d["nudge_type"], rows["preferred_hour_local"]))

true_mat = TRUE
best_true = np.nanargmax(np.where(np.isnan(true_mat), -np.inf, true_mat), axis=1)
oracle_send = np.nanmax(np.where(np.isnan(true_mat), -np.inf, true_mat), axis=1) > 0
results.append(score("oracle: true best nudge at the true hour, where it helps", oracle_send,
                     np.array(P.NUDGES)[best_true], rows["true_hour"]))

policies = pd.DataFrame(results)
print(policies.to_string(index=False))

# COMMAND ----------

# MAGIC %md
# MAGIC ## How the value of reach moves the picker

# COMMAND ----------

SENS_COLS = ["policy", "sends", "comebacks", "comebacks_per_1000_sends", "est_opt_outs", "at_risk_comebacks",
             "net_at_risk_comebacks"]
sens = []
for r in [0.5, 1.0, 2.0, 3.0, 5.0]:
    rr = P.Rules(value_of_reach=r)
    dd = P.decide(rows, effects, chosen, fatigue, rr)
    s = score(f"value of reach {r}", dd["action"] == "send", dd["nudge_type"], rows["preferred_hour_local"])
    sens.append({k: s[k] for k in SENS_COLS})
no_gate = P.decide(rows, effects, chosen, fatigue, P.Rules(value_of_reach=RULES.value_of_reach, hold_low_risk=False))
s = score("value of reach 2.0, low-risk members not held", no_gate["action"] == "send", no_gate["nudge_type"],
          rows["preferred_hour_local"])
sens.append({k: s[k] for k in SENS_COLS})
sensitivity = pd.DataFrame(sens)
print(sensitivity.to_string(index=False))

# COMMAND ----------

# MAGIC %md
# MAGIC ## What the picker decided, and how well it guessed send hours

# COMMAND ----------

mix = d.assign(what=d["hold_reason"].fillna(d["nudge_type"])).groupby(["action", "what"]).size().rename("decisions")
diff = np.abs(rows["preferred_hour_local"] - rows["true_hour"])
rows["hour_ok"] = np.minimum(diff, 24 - diff) <= 2
hour_accuracy = rows.groupby("hour_source")["hour_ok"].agg(["size", "mean"]).rename(
    columns={"size": "decisions", "mean": "within_2h_of_true_hour"}).round(3)
dims = pd.DataFrame([{"nudge_type": n, "segment_dimension": chosen[n]} for n in P.NUDGES])
print(mix.to_string()); print(hour_accuracy.to_string()); print(dims.to_string(index=False))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Check: eligibility from events, against the generator
# MAGIC
# MAGIC The daily picker works out `new_likes` and `match_waiting` eligibility from events. For the last day's test
# MAGIC decisions, derive it from events up to the day before, as the daily picker would, and compare.

# COMMAND ----------

check = spark.sql(f"""
  WITH d AS (
    SELECT o.decision_id, o.member_id, t.true_uplift_new_likes IS NOT NULL AS gen_new_likes,
      t.true_uplift_match_waiting IS NOT NULL AS gen_match_waiting
    FROM {CATALOG}.hearth_gold.nudge_outcomes o JOIN {CATALOG}.hearth_ml.generator_truth t USING (decision_id)
    WHERE o.decision_date = DATE'{end}'
  ),
  last_session AS (
    SELECT member_id, max(session_start_ts) AS ts FROM {CATALOG}.hearth_silver.sessions
    WHERE session_date < DATE'{end}' GROUP BY 1
  ),
  ev AS (
    SELECT e.member_id,
      count_if(e.session_id IS NULL AND e.event_type = 'like_received' AND e.event_ts > coalesce(l.ts, TIMESTAMP'1900-01-01')) AS unseen_likes,
      max(CASE WHEN e.session_id IS NULL AND e.event_type = 'message_received' THEN e.event_ts END) AS last_away_msg,
      max(CASE WHEN e.event_type = 'message_sent' THEN e.event_ts END) AS last_sent
    FROM {CATALOG}.hearth_silver.app_events e LEFT JOIN last_session l USING (member_id)
    WHERE e.event_date < DATE'{end}' AND e.member_id IN (SELECT member_id FROM d) GROUP BY 1
  )
  SELECT CASE WHEN l.ts IS NULL THEN 'no session in the data' ELSE 'had a session in the data' END AS members,
    count(*) AS decisions,
    round(avg(CASE WHEN (coalesce(ev.unseen_likes, 0) > 0) = d.gen_new_likes THEN 1 ELSE 0 END), 4) AS new_likes_agree,
    round(avg(CASE WHEN coalesce(ev.last_away_msg > coalesce(ev.last_sent, TIMESTAMP'1900-01-01'), false) = d.gen_match_waiting
      THEN 1 ELSE 0 END), 4) AS match_waiting_agree,
    count_if(d.gen_new_likes) AS gen_new_likes, count_if(d.gen_match_waiting) AS gen_match_waiting
  FROM d LEFT JOIN last_session l USING (member_id) LEFT JOIN ev USING (member_id)
  GROUP BY 1 ORDER BY 1
""").toPandas()
print(check.to_string(index=False))

# COMMAND ----------

import json

report = "\n\n".join([
    f"Nudge picker backtest on test decisions {start} to {end} ({len(rows):,} member-days)",
    f"Rules learned from decisions up to {known_through}. Comebacks are expected extra returns within 72 hours, "
    "scored with the planted effects. Opt-outs are estimated from measured rates. "
    f"Net comebacks = comebacks - {RULES.value_of_reach} x opt-outs. At-risk comebacks count only members with "
    "medium or high lapse risk, or already lapsed: a low-risk member is usually back within the week anyway.",
    "Policies, same members and days\n" + policies.to_string(index=False),
    "Picker sensitivity\n" + sensitivity.to_string(index=False),
    "Picker decisions\n" + mix.to_string(),
    "Segment dimension the readout picked for each nudge (planted: new_likes age, profile_tip tenure, "
    "new_people_nearby market size, we_miss_you days away, match_waiting none)\n" + dims.to_string(index=False)
    + "\n\nHeterogeneity tests\n" + tests.sort_values(["nudge_type", "p_value"]).round(4).to_string(index=False),
    "Send hour within 2 hours of the true preferred hour\n" + hour_accuracy.to_string(),
    f"Eligibility from events against the generator, decisions on {end}\n" + check.to_string(index=False),
])
print(report)
dbutils.notebook.exit(json.dumps({"report": report}))
