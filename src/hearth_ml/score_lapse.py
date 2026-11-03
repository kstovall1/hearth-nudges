# Databricks notebook source
# MAGIC %md
# MAGIC # Score lapse risk
# MAGIC
# MAGIC Scores every member the lapse model is meant for (active in the last 14 days, still installed) with the
# MAGIC `champion` version, and merges the scores into `hearth_gold.member_lapse_scores`. The daily job runs this
# MAGIC after the pipeline. `days` scores more than the latest day, to backfill or to check the model against
# MAGIC outcomes that are now known. Reruns replace a day's scores, so they are safe.

# COMMAND ----------

dbutils.widgets.text("catalog", "katie_febar_catalog")
dbutils.widgets.text("model_name", "lapse_model")
dbutils.widgets.text("days", "1")

CATALOG = dbutils.widgets.get("catalog")
FULL_NAME = f"{CATALOG}.hearth_ml.{dbutils.widgets.get('model_name')}"
DAYS = int(dbutils.widgets.get("days"))
TARGET = f"{CATALOG}.hearth_gold.member_lapse_scores"

# Cut points from the calibration on the held-out week, where 0.12 and 0.30 sit near deciles 4 and 8
BANDS = [(0.30, "high"), (0.12, "medium"), (0.0, "low")]

# COMMAND ----------

import mlflow
from mlflow.tracking import MlflowClient

mlflow.set_registry_uri("databricks-uc")
champion = MlflowClient(registry_uri="databricks-uc").get_model_version_by_alias(FULL_NAME, "champion")
model = mlflow.sklearn.load_model(f"models:/{FULL_NAME}@champion")
features = list(model.feature_names_in_)
print("champion version", champion.version, "with", len(features), "features")

# COMMAND ----------

last_date = spark.sql(f"SELECT max(feature_date) FROM {CATALOG}.hearth_gold.member_daily_features").first()[0]
rows = spark.sql(f"""
  SELECT member_id, feature_date, {", ".join(features)}
  FROM {CATALOG}.hearth_gold.member_daily_features
  WHERE feature_date > date_sub(DATE'{last_date}', {DAYS})
    AND sessions_14d > 0 AND NOT uninstalled
""").toPandas()
rows["reachable"] = rows["reachable"].astype(bool)
print(f"{len(rows):,} member-days to score, {rows['feature_date'].nunique()} days up to {last_date}")

# COMMAND ----------

import numpy as np
import pandas as pd

risk = model.predict_proba(rows[features])[:, 1]
scores = pd.DataFrame({
    "member_id": rows["member_id"],
    "feature_date": rows["feature_date"],
    "lapse_risk": risk.round(4),
    "risk_band": np.select([risk >= b for b, _ in BANDS[:-1]], [n for _, n in BANDS[:-1]], BANDS[-1][1]),
    "model_version": int(champion.version),
})

spark.sql(f"""
  CREATE TABLE IF NOT EXISTS {TARGET} (
    member_id STRING NOT NULL,
    feature_date DATE NOT NULL COMMENT 'Features as of the end of this UTC day. The risk is for the 7 days after it',
    lapse_risk DOUBLE COMMENT 'Chance of no session in the next 7 days, from the lapse model',
    risk_band STRING COMMENT 'high (0.30 and up), medium (0.12 to 0.30) or low',
    model_version INT COMMENT 'Version of {FULL_NAME} that scored the row',
    scored_at TIMESTAMP,
    CONSTRAINT member_lapse_scores_pk PRIMARY KEY (member_id, feature_date)
  )
  CLUSTER BY (feature_date)
  COMMENT 'Lapse risk for members active in the last 14 days and still installed, one row per member per scored day. Written by the hearth-daily job.'
""")

spark.createDataFrame(scores).createOrReplaceTempView("new_scores")
spark.sql(f"""
  MERGE INTO {TARGET} t
  USING (SELECT member_id, feature_date, lapse_risk, risk_band, CAST(model_version AS INT) AS model_version,
           current_timestamp() AS scored_at FROM new_scores) s
  ON t.member_id = s.member_id AND t.feature_date = s.feature_date
  WHEN MATCHED THEN UPDATE SET *
  WHEN NOT MATCHED THEN INSERT *
""")

summary = spark.sql(f"""
  SELECT feature_date, count(*) AS members, round(avg(lapse_risk), 3) AS mean_risk,
    count_if(risk_band = 'high') AS high, count_if(risk_band = 'medium') AS medium, count_if(risk_band = 'low') AS low
  FROM {TARGET} WHERE feature_date > date_sub(DATE'{last_date}', {DAYS})
  GROUP BY 1 ORDER BY 1
""").toPandas()
print(summary.to_string(index=False))

# COMMAND ----------

import json

dbutils.notebook.exit(json.dumps({
    "model_version": int(champion.version), "rows": len(scores), "last_date": str(last_date),
    "summary": summary.to_string(index=False),
}))
