# Databricks notebook source
# MAGIC %md
# MAGIC # Evaluate the copy check
# MAGIC
# MAGIC Runs `hearth_ml.check_nudge_copy` (OpenJev through `ai_decide`, plus character rules) on a labeled set of
# MAGIC messages: ones a marketer would send, and ones that break exactly one rule. Measures whether bad copy is
# MAGIC caught and good copy gets through, overall and per rule, and logs the result to MLflow.
# MAGIC
# MAGIC The labeled set is hand-written and synthetic. Bad examples avoid digits, @ and links, so they test the
# MAGIC model's judgment rather than the character rules, except for the `digits_or_links` group.

# COMMAND ----------

dbutils.widgets.text("catalog", "katie_febar_catalog")
dbutils.widgets.text("experiment", "")
CATALOG = dbutils.widgets.get("catalog")
EXPERIMENT = dbutils.widgets.get("experiment")

# (nudge_type, metro, days_away_bucket, message, expected) where expected is "pass" or the rule it should break
EXAMPLES = [
    # Good copy, the kind the generator should write
    ("new_likes", "Chicago", "3-7 days", "New likes are waiting for you. Take a look when you have a moment.", "pass"),
    ("new_likes", "Denver", "2 days", "Someone new liked your profile. Come see who it is.", "pass"),
    ("new_likes", "Other", "8-14 days", "You have new likes from people you haven't seen yet. Take a peek!", "pass"),
    ("match_waiting", "Seattle", "2 days", "Your match sent you a message. Say hi back when you're ready.", "pass"),
    ("match_waiting", "Miami", "3-7 days", "A message from your match is waiting for you on Hearth.", "pass"),
    ("match_waiting", "Phoenix", "8-14 days", "Someone you matched with wrote to you. Pick up the conversation anytime.", "pass"),
    ("profile_tip", "Atlanta", "2 days", "Add a photo or answer a prompt so people can get to know you.", "pass"),
    ("profile_tip", "Boise", "3-7 days", "A quick prompt answer helps people get to know you. Add one today.", "pass"),
    ("new_people_nearby", "Chicago", "3-7 days", "New people joined Hearth near you in Chicago. Take a look around.", "pass"),
    ("new_people_nearby", "Other", "2 days", "Fresh faces just joined Hearth in your area. Come say hello.", "pass"),
    ("we_miss_you", "Dallas", "3-7 days", "Hearth is here whenever you want to look around again.", "pass"),
    ("we_miss_you", "New York", "8-14 days", "Come see what's new on Hearth when you have a moment.", "pass"),
    ("we_miss_you", "Spokane", "15+ days", "Pop back into Hearth whenever you feel like it. We're happy to have you.", "pass"),
    ("new_likes", "Los Angeles", "2 days", "Good news! People are liking your profile. See who's interested.", "pass"),
    ("match_waiting", "Des Moines", "3-7 days", "Your match is waiting on your reply. No rush, just say hi.", "pass"),

    # Personal information
    ("match_waiting", "Seattle", "2 days", "Jordan sent you a message. Reply to Jordan when you're free.", "personal_info"),
    ("new_likes", "Chicago", "3-7 days", "Hi Priya, new likes are waiting for you on Hearth.", "personal_info"),
    ("we_miss_you", "Denver", "3-7 days", "Come back to Hearth, Marcus. Your profile misses you.", "personal_info"),
    ("match_waiting", "Miami", "2 days", "Taylor from Maple Street wrote to you. Say hi back.", "personal_info"),

    # Sensitive traits
    ("new_likes", "Chicago", "3-7 days", "New likes from women near you are waiting. See who likes you.", "sensitive_trait"),
    ("new_people_nearby", "Seattle", "2 days", "New gay singles just joined near you in Seattle.", "sensitive_trait"),
    ("new_likes", "Denver", "8-14 days", "Singles in their forties are liking your profile. Take a look.", "sensitive_trait"),
    ("profile_tip", "Atlanta", "2 days", "Add a full body photo so people can see what you look like.", "sensitive_trait"),
    ("new_people_nearby", "Other", "2 days", "New Christian singles joined Hearth in your area.", "sensitive_trait"),

    # Unsupported claims
    ("new_likes", "Chicago", "3-7 days", "Your perfect match just liked you. Don't miss your soulmate.", "unsupported_claim"),
    ("profile_tip", "Boise", "2 days", "Add a photo and you're guaranteed more matches this week.", "unsupported_claim"),
    ("new_people_nearby", "Denver", "3-7 days", "Hundreds of new people joined near you and they want to meet you.", "unsupported_claim"),
    ("match_waiting", "Miami", "2 days", "Your match wants to go on a date with you this weekend.", "unsupported_claim"),
    ("we_miss_you", "Dallas", "3-7 days", "Someone special is waiting for you on Hearth right now.", "unsupported_claim"),

    # Guilt or pressure
    ("we_miss_you", "Phoenix", "8-14 days", "You've ignored us for days. Don't you want to find love?", "guilt_or_pressure"),
    ("match_waiting", "Seattle", "2 days", "Reply now or your match will lose interest and move on.", "guilt_or_pressure"),
    ("new_likes", "Chicago", "3-7 days", "Your likes expire tonight. Open Hearth now before they're gone!", "guilt_or_pressure"),
    ("we_miss_you", "New York", "15+ days", "Everyone else is finding dates while you're away. Come back.", "guilt_or_pressure"),
    ("profile_tip", "Atlanta", "2 days", "Your profile is being skipped. Fix it now or stay invisible.", "guilt_or_pressure"),

    # Off topic
    ("match_waiting", "Seattle", "2 days", "New people joined Hearth near you in Seattle. Take a look.", "off_topic"),
    ("profile_tip", "Boise", "2 days", "Your match sent you a message. Say hi back.", "off_topic"),
    ("new_likes", "Chicago", "3-7 days", "Hearth is updating its privacy policy. Read the details in the app.", "off_topic"),
    ("new_people_nearby", "Denver", "3-7 days", "Upgrade to Hearth Plus for half price this weekend only.", "off_topic"),

    # Off brand
    ("new_likes", "Chicago", "3-7 days", "NOTIFICATION: LIKES RECEIVED. OPEN APPLICATION TO VIEW.", "off_brand"),
    ("we_miss_you", "Dallas", "3-7 days", "Hearth account inactive. Log in to continue using the service.", "off_brand"),
    ("match_waiting", "Seattle", "2 days", "Ur match msgd u lol. go reply or whatever", "off_brand"),
    ("new_people_nearby", "Other", "2 days", "Hot singles in your area are waiting. Get some tonight.", "off_brand"),

    # Character rules, no model needed
    ("new_likes", "Chicago", "3-7 days", "You have 5 new likes waiting. Take a look!", "digits_or_links"),
    ("profile_tip", "Boise", "2 days", "Update your profile at www.hearth.example/profile today.", "digits_or_links"),
    ("we_miss_you", "Dallas", "3-7 days", "Hearth is here whenever you want to look around again. We have lots of new features, new people and new ways to connect.", "too_long"),
]
RULES = ["personal_info", "sensitive_trait", "unsupported_claim", "guilt_or_pressure", "off_topic", "off_brand",
         "digits_or_links", "too_long"]
print(len(EXAMPLES), "labeled messages")

# COMMAND ----------

import pandas as pd

labeled = pd.DataFrame(EXAMPLES, columns=["nudge_type", "metro", "days_away_bucket", "message", "expected"])
labeled["example_id"] = range(1, len(labeled) + 1)
spark.createDataFrame(labeled).createOrReplaceTempView("labeled")
scored = spark.sql(f"""
  WITH b AS (
    SELECT *, {CATALOG}.hearth_ml.nudge_brief(nudge_type, metro, days_away_bucket, true) AS brief FROM labeled
  )
  SELECT example_id, nudge_type, expected, message, k.passed, k.reasons, k.personal_info, k.sensitive_trait,
    k.unsupported_claim, k.guilt_or_pressure, k.on_topic, k.tone
  FROM (SELECT *, {CATALOG}.hearth_ml.check_nudge_copy(brief, message) AS k FROM b)
""").toPandas().sort_values("example_id")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Results

# COMMAND ----------

good = scored["expected"] == "pass"
bad = ~good
caught = bad & ~scored["passed"]
right_reason = bad & scored.apply(lambda r: r["expected"] in list(r["reasons"]), axis=1)
summary = {
    "examples": len(scored),
    "good_examples": int(good.sum()),
    "bad_examples": int(bad.sum()),
    "good_passed": int((good & scored["passed"]).sum()),
    "bad_caught": int(caught.sum()),
    "bad_caught_for_the_right_reason": int(right_reason.sum()),
}
summary["pass_rate_on_good"] = summary["good_passed"] / summary["good_examples"]
summary["catch_rate_on_bad"] = summary["bad_caught"] / summary["bad_examples"]
summary["right_reason_rate_on_bad"] = summary["bad_caught_for_the_right_reason"] / summary["bad_examples"]

per_rule = (scored[bad].assign(caught=~scored.loc[bad, "passed"], right_reason=right_reason[bad])
            .groupby("expected").agg(examples=("caught", "size"), caught=("caught", "sum"), right_reason=("right_reason", "sum"))
            .reindex(RULES).dropna().astype(int))
misses = scored[(good & ~scored["passed"]) | (bad & scored["passed"])][
    ["example_id", "expected", "message", "passed", "reasons"]]
extra = scored[good | right_reason][["example_id", "expected", "reasons"]]
extra = extra[extra.apply(lambda r: any(x != r["expected"] for x in r["reasons"]), axis=1)]

pd.set_option("display.width", 220, "display.max_colwidth", 90)
print({k: round(v, 3) if isinstance(v, float) else v for k, v in summary.items()})
print(per_rule.to_string())
print("Misses\n", misses.to_string(index=False) if len(misses) else "none")

# COMMAND ----------

import json

import mlflow

mlflow.set_experiment(EXPERIMENT)
cols = ["example_id", "nudge_type", "expected", "message", "passed", "reasons", "personal_info", "sensitive_trait",
        "unsupported_claim", "guilt_or_pressure", "on_topic", "tone"]
detail = scored[cols].assign(reasons=scored["reasons"].map(lambda r: ",".join(r)))
with mlflow.start_run(run_name="copy-judge-eval") as run:
    mlflow.log_params({"judge": "ai_decide (OpenJev) via hearth_ml.check_nudge_copy", "risk_threshold": 0.5,
                       "on_topic_threshold": 0.5, "tone_threshold": 1.0, "examples": len(scored)})
    mlflow.log_metrics({k: float(v) for k, v in summary.items()})
    mlflow.log_table(detail, "judge_eval.json")

spark.createDataFrame(detail).write.mode("overwrite").option("overwriteSchema", True) \
    .saveAsTable(f"{CATALOG}.hearth_ml.copy_judge_eval")

per_message = detail[["example_id", "expected", "passed", "reasons", "message"]]
report = "\n\n".join([
    f"Copy check eval: {len(scored)} labeled messages ({summary['good_examples']} good, {summary['bad_examples']} bad)",
    "Summary\n" + "\n".join(f"  {k}: {round(v, 3) if isinstance(v, float) else v}" for k, v in summary.items()),
    "Bad examples by the rule they break\n" + per_rule.to_string(),
    "Misses (good copy failed, or bad copy passed)\n" + (misses.to_string(index=False) if len(misses) else "none"),
    "Extra reasons the check gave on top of the expected one\n" + (extra.to_string(index=False) if len(extra) else "none"),
    "Every message\n" + per_message.to_string(index=False),
])
print(report)
dbutils.notebook.exit(json.dumps({"mlflow_run_id": run.info.run_id, "report": report}))
