# Databricks notebook source
# MAGIC %md
# MAGIC # Pick tomorrow's nudges
# MAGIC
# MAGIC Decides, for every reachable member, whether to nudge tomorrow, which nudge, and at what hour, using
# MAGIC the rules in `picker.py`. Runs in the `hearth-daily` job after lapse scoring.
# MAGIC
# MAGIC - Decisions are for the day after the last complete day of data, made at 07:00 UTC like the messaging tool's batch.
# MAGIC - Uplift estimates come from the nudge test readout, every decision with a complete 72-hour window.
# MAGIC - Writes `hearth_gold.nudge_segment_effects` (the readout), `hearth_gold.nudge_decisions` (history) and
# MAGIC   `hearth_serving.next_best_action` (today's decision per member, synced to Lakebase).

# COMMAND ----------

dbutils.widgets.text("catalog", "katie_febar_catalog")
dbutils.widgets.text("value_of_reach", "2.0")

CATALOG = dbutils.widgets.get("catalog")

import picker as P

RULES = P.Rules(value_of_reach=float(dbutils.widgets.get("value_of_reach")))
print(RULES)

# COMMAND ----------

from datetime import datetime, timedelta, timezone

as_of = spark.sql(f"SELECT max(feature_date) FROM {CATALOG}.hearth_gold.member_daily_features").first()[0]
decision_date = as_of + timedelta(days=1)
decided_at = datetime(decision_date.year, decision_date.month, decision_date.day, 7)  # UTC
print(f"features as of {as_of}, deciding for {decision_date} at {decided_at} UTC")

# COMMAND ----------

# MAGIC %md
# MAGIC ## The nudge test readout

# COMMAND ----------

outcomes = spark.sql(f"""
  SELECT nudge_type, treated, returned_72h, opted_out, tenure_days, age_band, market_size, days_away_bucket
  FROM {CATALOG}.hearth_gold.nudge_outcomes
  WHERE window_complete AND features_as_of IS NOT NULL
""").toPandas()
effects = P.segment_effects(outcomes, RULES)
chosen, tests = P.pick_dimensions(effects, RULES)

deliveries = spark.sql(f"""
  SELECT f.pushes_7d, n.opted_out
  FROM {CATALOG}.hearth_silver.notification_funnel n
  JOIN {CATALOG}.hearth_gold.member_daily_features f
    ON f.member_id = n.member_id AND f.feature_date = date_sub(n.decision_date, 1)
  WHERE n.campaign_type = 'nudge_test' AND n.delivered
""").toPandas()
fatigue = P.fatigue_rates(deliveries)

print("segment dimension per nudge:", chosen)
print("opt-out rate per delivered nudge by pushes in the 7 days before:", {k: round(v, 4) for k, v in fatigue.items()})
print(tests.sort_values(["nudge_type", "p_value"]).round(4).to_string(index=False))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Members as of the last complete day
# MAGIC
# MAGIC Eligibility comes from events: `new_likes` needs likes that arrived while the member was away, after
# MAGIC their last session. `match_waiting` needs a message that arrived while away, after the member last sent one.
# MAGIC Members quiet since before the data starts can't show either, so they're treated as not eligible.

# COMMAND ----------

members = spark.sql(f"""
  WITH f AS (
    SELECT * FROM {CATALOG}.hearth_gold.member_daily_features
    WHERE feature_date = DATE'{as_of}' AND reachable AND NOT uninstalled
  ),
  last_session AS (
    SELECT member_id, max(session_start_ts) AS ts FROM {CATALOG}.hearth_silver.sessions
    WHERE session_date <= DATE'{as_of}' GROUP BY member_id
  ),
  events AS (
    SELECT e.member_id,
      count_if(e.session_id IS NULL AND e.event_type = 'like_received' AND e.event_ts > coalesce(l.ts, TIMESTAMP'1900-01-01'))
        AS unseen_likes,
      max(CASE WHEN e.session_id IS NULL AND e.event_type = 'message_received' THEN e.event_ts END) AS last_away_message_ts,
      max(CASE WHEN e.event_type = 'message_sent' THEN e.event_ts END) AS last_message_sent_ts
    FROM {CATALOG}.hearth_silver.app_events e
    LEFT JOIN last_session l USING (member_id)
    WHERE e.event_date <= DATE'{as_of}' AND e.event_type IN ('like_received', 'message_received', 'message_sent')
    GROUP BY e.member_id
  ),
  last_nudge AS (
    SELECT member_id, max(delivered_ts) AS ts FROM {CATALOG}.hearth_silver.notification_funnel
    WHERE campaign_type <> 'blast' AND delivered AND delivered_ts < TIMESTAMP'{decided_at}' GROUP BY member_id
  )
  SELECT
    f.member_id, f.tenure_days, f.age_band, f.market_size, f.platform, f.timezone, f.pushes_7d, f.plan,
    (unix_timestamp(TIMESTAMP'{decided_at}') - unix_timestamp(greatest(l.ts, m.last_active_ts))) / 86400 AS days_away,
    (unix_timestamp(TIMESTAMP'{decided_at}') - unix_timestamp(n.ts)) / 3600 AS hours_since_last_nudge,
    s.lapse_risk, s.risk_band,
    coalesce(e.unseen_likes, 0) > 0 AS eligible_new_likes,
    coalesce(e.last_away_message_ts > coalesce(e.last_message_sent_ts, TIMESTAMP'1900-01-01'), false) AS eligible_match_waiting
  FROM f
  JOIN {CATALOG}.hearth_silver.members m USING (member_id)
  LEFT JOIN last_session l USING (member_id)
  LEFT JOIN events e USING (member_id)
  LEFT JOIN last_nudge n USING (member_id)
  LEFT JOIN {CATALOG}.hearth_gold.member_lapse_scores s ON s.member_id = f.member_id AND s.feature_date = f.feature_date
""").toPandas()

# The picker's own sends count toward the cooldown once it has run before
if spark.catalog.tableExists(f"{CATALOG}.hearth_gold.nudge_decisions"):
    prior = spark.sql(f"""
      SELECT member_id, max(send_at_utc) AS ts FROM {CATALOG}.hearth_gold.nudge_decisions
      WHERE action = 'send' AND decision_date < DATE'{decision_date}' GROUP BY member_id
    """).toPandas()
    hours = (decided_at - prior.set_index("member_id")["ts"]).dt.total_seconds() / 3600
    own = members["member_id"].map(hours)
    members["hours_since_last_nudge"] = members["hours_since_last_nudge"].where(
        members["hours_since_last_nudge"] <= own.fillna(float("inf")), own)

hour_counts = spark.sql(f"""
  SELECT member_id, start_hour_local AS hour, count(*) AS sessions
  FROM {CATALOG}.hearth_silver.sessions WHERE session_date <= DATE'{as_of}'
  GROUP BY member_id, start_hour_local
""").toPandas()
hours, default_hour = P.preferred_hours(hour_counts, RULES)
members = members.merge(hours, on="member_id", how="left")
members["hour_source"] = members["hour_source"].fillna("default")
members["preferred_hour_local"] = members["preferred_hour_local"].fillna(default_hour).astype(int)
print(f"{len(members):,} reachable members; default send hour {default_hour}:00")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Decide

# COMMAND ----------

import pandas as pd

d = P.decide(members, effects, chosen, fatigue, RULES)
d = d.merge(members[["member_id", "timezone", "hour_source"]], on="member_id")
sends = d["action"] == "send"
d["send_at_utc"] = pd.NaT
d.loc[sends, "send_at_utc"] = P.send_times(decision_date, d.loc[sends, "send_hour_local"].astype(int),
                                           d.loc[sends, "timezone"])
d["decision_date"] = decision_date
d["features_as_of"] = as_of
d["decided_at"] = decided_at
d["policy_version"] = RULES.version
d["value_of_reach"] = RULES.value_of_reach

summary = d.groupby(["action", d["hold_reason"].fillna(d["nudge_type"])]).size().rename("members")
print(summary.to_string())

# COMMAND ----------

# MAGIC %md
# MAGIC ## Write

# COMMAND ----------

from pyspark.sql import functions as F

computed_at = datetime.now(timezone.utc).replace(tzinfo=None)
readout = effects.assign(
    chosen=[chosen[n] == dim for n, dim in zip(effects["nudge_type"], effects["dimension"])],
    uplift_pts=(100 * effects["uplift"]).round(2), uplift_low_pts=(100 * effects["uplift_low"]).round(2),
    opt_out_treated_pct=(100 * effects["opt_out_treated"]).round(3), return_treated_pct=(100 * effects["return_treated"]).round(2),
    return_holdout_pct=(100 * effects["return_holdout"]).round(2), opt_out_ratio=effects["opt_out_ratio"].round(3),
    computed_at=computed_at,
)[["nudge_type", "dimension", "segment", "chosen", "n_treated", "n_holdout", "return_treated_pct", "return_holdout_pct",
   "uplift_pts", "uplift_low_pts", "opt_out_treated_pct", "opt_out_ratio", "computed_at"]]
(spark.createDataFrame(readout).write.mode("overwrite").option("overwriteSchema", True)
 .saveAsTable(f"{CATALOG}.hearth_gold.nudge_segment_effects"))
spark.sql(f"""COMMENT ON TABLE {CATALOG}.hearth_gold.nudge_segment_effects IS
  'Nudge test readout: 72-hour return for treated against holdout, by nudge and segment, from decisions with a complete window. chosen marks the segment dimension the picker uses for that nudge. Written by the hearth-daily job.'""")

cols = ["member_id", "decision_date", "action", "hold_reason", "nudge_type", "send_at_utc", "send_hour_local", "timezone",
        "hour_source", "segment", "estimated_uplift_pts", "uplift_low_pts", "opt_out_risk_pct", "net_value_pts",
        "eligible_nudges", "days_away", "days_away_bucket", "pushes_7d", "lapse_risk", "risk_band", "features_as_of",
        "decided_at", "policy_version", "value_of_reach"]
out = spark.createDataFrame(d[cols].astype({"send_hour_local": "Int64"}))
out = out.withColumn("send_at_utc", F.col("send_at_utc").cast("timestamp"))

spark.sql(f"""
  CREATE TABLE IF NOT EXISTS {CATALOG}.hearth_gold.nudge_decisions (
    member_id STRING NOT NULL, decision_date DATE NOT NULL, action STRING, hold_reason STRING, nudge_type STRING,
    send_at_utc TIMESTAMP, send_hour_local BIGINT, timezone STRING, hour_source STRING, segment STRING,
    estimated_uplift_pts DOUBLE, uplift_low_pts DOUBLE, opt_out_risk_pct DOUBLE, net_value_pts DOUBLE,
    eligible_nudges STRING, days_away DOUBLE, days_away_bucket STRING, pushes_7d BIGINT, lapse_risk DOUBLE,
    risk_band STRING, features_as_of DATE, decided_at TIMESTAMP, policy_version STRING, value_of_reach DOUBLE,
    CONSTRAINT nudge_decisions_pk PRIMARY KEY (member_id, decision_date)
  )
  CLUSTER BY (decision_date)
  COMMENT 'Every picker decision, one row per reachable member per day: send a nudge at an hour, or hold with a reason. Written by the hearth-daily job.'
""")
out.createOrReplaceTempView("new_decisions")
spark.sql(f"""
  MERGE INTO {CATALOG}.hearth_gold.nudge_decisions t USING new_decisions s
  ON t.member_id = s.member_id AND t.decision_date = s.decision_date
  WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *
""")

# Today's decisions only, for the notification service. Copy is added by the copy step.
spark.sql(f"""
  CREATE TABLE IF NOT EXISTS {CATALOG}.hearth_serving.next_best_action (
    member_id STRING NOT NULL, decision_date DATE, action STRING, hold_reason STRING, nudge_type STRING,
    send_at_utc TIMESTAMP, send_hour_local BIGINT, timezone STRING, estimated_uplift_pts DOUBLE,
    net_value_pts DOUBLE, lapse_risk DOUBLE, risk_band STRING, policy_version STRING, decided_at TIMESTAMP,
    copy_text STRING, copy_status STRING, copy_checked_at TIMESTAMP,
    CONSTRAINT next_best_action_pk PRIMARY KEY (member_id)
  )
  TBLPROPERTIES (delta.enableChangeDataFeed = true)
  COMMENT 'Next best action per reachable member for decision_date: send nudge_type at send_at_utc, or hold. The notification service reads it from Lakebase by member_id. copy_status is pending until the copy step writes and checks the message.'
""")
spark.sql(f"""
  MERGE INTO {CATALOG}.hearth_serving.next_best_action t
  USING (SELECT member_id, decision_date, action, hold_reason, nudge_type, send_at_utc, send_hour_local, timezone,
                estimated_uplift_pts, net_value_pts, lapse_risk, risk_band, policy_version, decided_at,
                CAST(NULL AS STRING) AS copy_text,
                CASE WHEN action = 'send' THEN 'pending' END AS copy_status,
                CAST(NULL AS TIMESTAMP) AS copy_checked_at
         FROM new_decisions) s
  ON t.member_id = s.member_id
  WHEN MATCHED THEN UPDATE SET *
  WHEN NOT MATCHED THEN INSERT *
  WHEN NOT MATCHED BY SOURCE THEN DELETE
""")

# Who each reachable member is, without PII, for segment counts and lookups in the lifecycle console.
# Synced to Lakebase next to next_best_action. No names, contact details or sensitive attributes.
segments = members[["member_id", "age_band", "market_size", "platform", "plan", "tenure_days", "pushes_7d",
                    "lapse_risk", "risk_band", "preferred_hour_local", "hour_source", "timezone"]].copy()
segments["tenure_group"] = P.tenure_group(segments["tenure_days"])
segments["days_away"] = members["days_away"].round(2)
segments["days_away_bucket"] = P.days_away_bucket(members["days_away"])
segments["as_of"] = as_of
spark.createDataFrame(segments.astype({"tenure_days": "int64", "pushes_7d": "int64", "preferred_hour_local": "int64"})) \
    .createOrReplaceTempView("new_segments")
spark.sql(f"""
  CREATE TABLE IF NOT EXISTS {CATALOG}.hearth_serving.member_segments (
    member_id STRING NOT NULL, age_band STRING, market_size STRING, platform STRING, plan STRING, tenure_days BIGINT,
    tenure_group STRING, days_away DOUBLE, days_away_bucket STRING, pushes_7d BIGINT, lapse_risk DOUBLE, risk_band STRING,
    preferred_hour_local BIGINT, hour_source STRING, timezone STRING, as_of DATE,
    CONSTRAINT member_segments_pk PRIMARY KEY (member_id)
  )
  TBLPROPERTIES (delta.enableChangeDataFeed = true)
  COMMENT 'Each reachable member as of the last complete day, for segments in the lifecycle console: age band, market, platform, plan, tenure, days away, recent pushes and lapse risk. No PII or sensitive attributes. Synced to Lakebase.'
""")
spark.sql(f"""
  MERGE INTO {CATALOG}.hearth_serving.member_segments t
  USING (SELECT member_id, age_band, market_size, platform, plan, tenure_days, tenure_group, days_away, days_away_bucket,
                pushes_7d, lapse_risk, risk_band, preferred_hour_local, hour_source, timezone, as_of FROM new_segments) s
  ON t.member_id = s.member_id
  WHEN MATCHED THEN UPDATE SET *
  WHEN NOT MATCHED THEN INSERT *
  WHEN NOT MATCHED BY SOURCE THEN DELETE
""")

# COMMAND ----------

import json

dbutils.notebook.exit(json.dumps({
    "decision_date": str(decision_date), "members": len(d), "sends": int(sends.sum()),
    "chosen_dimensions": chosen, "fatigue": {k: round(v, 4) for k, v in fatigue.items()},
    "default_hour": default_hour, "summary": summary.to_string(),
}))
