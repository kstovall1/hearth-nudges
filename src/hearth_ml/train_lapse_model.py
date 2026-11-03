# Databricks notebook source
# MAGIC %md
# MAGIC # Hearth lapse-risk model
# MAGIC
# MAGIC Predicts whether a member who was active in the last 14 days will have **no session in the next 7 days**.
# MAGIC The nudge picker uses the score to decide who is worth a push today.
# MAGIC
# MAGIC - Features: `hearth_gold.member_daily_features`, as of the end of each day. No sensitive attributes.
# MAGIC - Label: `hearth_gold.lapse_labels`.
# MAGIC - Split by date with a 7-day gap, so no training label looks into the test period.
# MAGIC - Registered to Unity Catalog. The `champion` alias moves only if the model beats a one-feature baseline,
# MAGIC   is calibrated, and is no worse than the current champion on the same held-out week.

# COMMAND ----------

dbutils.widgets.text("catalog", "katie_febar_catalog")
dbutils.widgets.text("experiment", "")
dbutils.widgets.text("model_name", "lapse_model")

CATALOG = dbutils.widgets.get("catalog")
EXPERIMENT = dbutils.widgets.get("experiment")
FULL_NAME = f"{CATALOG}.hearth_ml.{dbutils.widgets.get('model_name')}"

MIN_HISTORY_DAYS = 28  # trailing 28-day windows are complete from here
LABEL_DAYS = 7
TEST_DAYS = 7
SEED = 7

# Promotion gate for the champion alias
MIN_GAIN_OVER_BASELINE = 0.02  # AUC above ranking by days since the last session
MAX_CALIBRATION_ERROR = 0.02  # mean gap between score and lapse rate across score deciles, weighted by rows
MAX_LOSS_TO_CHAMPION = 0.005  # AUC the new version may give up against the current champion

CATEGORICAL = ["plan", "age_band", "platform", "market_size", "metro"]
NUMERIC = [
    "tenure_days", "days_since_last_session", "sessions_1d", "sessions_7d", "sessions_prev_7d",
    "sessions_trend_7d", "sessions_14d", "sessions_28d", "push_sessions_28d", "profile_views_7d",
    "likes_sent_7d", "likes_received_7d", "matches_14d", "messages_sent_7d", "messages_sent_prev_7d",
    "messages_sent_14d", "messages_received_14d", "reply_ratio_14d", "pushes_7d", "pushes_28d",
    "open_rate_28d", "reachable",
]
FEATURES = CATEGORICAL + NUMERIC

# COMMAND ----------

# MAGIC %md
# MAGIC ## Training data and split
# MAGIC
# MAGIC Uninstalled members are left out. They can't come back, so they would teach the model nothing useful,
# MAGIC and the picker never scores them. `history_days` is not a feature: it describes the data window, not the member.

# COMMAND ----------

from datetime import timedelta

data = spark.sql(f"""
  SELECT f.member_id, f.feature_date, {", ".join(f"f.{c}" for c in FEATURES)}, l.lapsed
  FROM {CATALOG}.hearth_gold.member_daily_features f
  JOIN {CATALOG}.hearth_gold.lapse_labels l USING (member_id, feature_date)
  WHERE f.history_days >= {MIN_HISTORY_DAYS} AND NOT f.uninstalled
""")

first_date, last_date = data.selectExpr("min(feature_date)", "max(feature_date)").first()
test_start = last_date - timedelta(days=TEST_DAYS - 1)
train_end = test_start - timedelta(days=LABEL_DAYS + 1)  # a train label on train_end looks 7 days ahead, up to the day before test_start

pdf = data.toPandas()
pdf["reachable"] = pdf["reachable"].astype(bool)
train = pdf[pdf["feature_date"] <= train_end]
test = pdf[pdf["feature_date"] >= test_start]

split = {
    "train_dates": f"{first_date} to {train_end}",
    "test_dates": f"{test_start} to {last_date}",
    "train_rows": len(train),
    "test_rows": len(test),
    "train_lapse_rate": round(float(train["lapsed"].mean()), 4),
    "test_lapse_rate": round(float(test["lapsed"].mean()), 4),
}
print(split)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Model
# MAGIC
# MAGIC Histogram gradient boosting from scikit-learn: handles missing values and categories natively, and has
# MAGIC no extra dependencies to install on the serving endpoint. Early stopping on a random 10% of the training rows.

# COMMAND ----------

import mlflow
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder

PARAMS = {
    "learning_rate": 0.08,
    "max_iter": 500,
    "max_leaf_nodes": 31,
    "min_samples_leaf": 100,
    "l2_regularization": 1.0,
    "early_stopping": True,
    "validation_fraction": 0.1,
    "n_iter_no_change": 20,
}


def build_model():
    # Unknown categories at scoring time become -1, which the classifier treats as missing
    prep = ColumnTransformer(
        [("categories", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1,
                                       encoded_missing_value=-1), CATEGORICAL)],
        remainder="passthrough",
        verbose_feature_names_out=False,
    )
    clf = HistGradientBoostingClassifier(
        **PARAMS,
        categorical_features=[True] * len(CATEGORICAL) + [False] * len(NUMERIC),
        random_state=SEED,
    )
    return Pipeline([("prep", prep), ("clf", clf)])


model = build_model().fit(train[FEATURES], train["lapsed"])
print("boosting iterations:", model.named_steps["clf"].n_iter_)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Evaluation on the held-out week
# MAGIC
# MAGIC The baseline ranks members by days since their last session alone, the rule a lifecycle team would write by hand.

# COMMAND ----------

from sklearn.inspection import permutation_importance
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score

y = test["lapsed"].astype(int).to_numpy()
p = model.predict_proba(test[FEATURES])[:, 1]
base = test["days_since_last_session"].to_numpy()

metrics = {
    "test_auc": roc_auc_score(y, p),
    "test_pr_auc": average_precision_score(y, p),
    "test_brier": brier_score_loss(y, p),
    "test_log_loss": log_loss(y, p),
    "baseline_auc": roc_auc_score(y, base),
    "baseline_pr_auc": average_precision_score(y, base),
}
metrics["auc_gain_over_baseline"] = metrics["test_auc"] - metrics["baseline_auc"]

# Top of the ranking: what the picker sees when it nudges only the riskiest members
order = np.argsort(-p)
for share in (0.1, 0.2):
    top = order[: int(len(p) * share)]
    pct = int(share * 100)
    metrics[f"precision_top_{pct}pct"] = y[top].mean()
    metrics[f"recall_top_{pct}pct"] = y[top].sum() / y.sum()
    metrics[f"lift_top_{pct}pct"] = y[top].mean() / y.mean()

scored = test.assign(score=p, decile=pd.qcut(pd.Series(p).rank(method="first"), 10, labels=False).to_numpy() + 1)
calibration = scored.groupby("decile").agg(rows=("lapsed", "size"), mean_score=("score", "mean"),
                                           lapse_rate=("lapsed", "mean"))
metrics["calibration_error"] = float(
    (calibration["rows"] * (calibration["mean_score"] - calibration["lapse_rate"]).abs()).sum() / calibration["rows"].sum()
)
calibration = calibration.round(3)


def auc_or_none(g):
    return round(roc_auc_score(g["lapsed"], g["score"]), 3) if g["lapsed"].nunique() == 2 else None


segments = pd.concat([
    scored.assign(segment=np.where(scored["tenure_days"] < 30, "first 30 days", "after 30 days")),
    scored.assign(segment=np.where(scored["plan"] == "free", "free", "paid")),
    scored.assign(segment=scored["platform"]),
    scored.assign(segment=scored["market_size"] + " market"),
])
by_segment = segments.groupby("segment").apply(
    lambda g: pd.Series({"rows": len(g), "lapse_rate": round(g["lapsed"].mean(), 3), "auc": auc_or_none(g)}),
    include_groups=False,
)

sample = test.sample(n=min(30_000, len(test)), random_state=SEED)
imp = permutation_importance(model, sample[FEATURES], sample["lapsed"], scoring="roc_auc",
                             n_repeats=3, random_state=SEED)
importance = pd.Series(imp.importances_mean, index=FEATURES).sort_values(ascending=False).round(4)

# The current champion, scored on the same held-out week
from mlflow.tracking import MlflowClient

mlflow.set_registry_uri("databricks-uc")
client = MlflowClient(registry_uri="databricks-uc")
try:
    champion = client.get_model_version_by_alias(FULL_NAME, "champion")
    champion_model = mlflow.sklearn.load_model(f"models:/{FULL_NAME}@champion")
    metrics["champion_auc"] = roc_auc_score(y, champion_model.predict_proba(test[FEATURES])[:, 1])
    champion_version = champion.version
except Exception as e:  # no champion yet
    print("no champion to compare with:", type(e).__name__)
    champion_version = None

checks = {
    f"AUC at least {MIN_GAIN_OVER_BASELINE} above the baseline": metrics["auc_gain_over_baseline"] >= MIN_GAIN_OVER_BASELINE,
    f"calibration error at most {MAX_CALIBRATION_ERROR}": metrics["calibration_error"] <= MAX_CALIBRATION_ERROR,
    f"AUC no more than {MAX_LOSS_TO_CHAMPION} below champion version {champion_version}":
        champion_version is None or metrics["test_auc"] >= metrics["champion_auc"] - MAX_LOSS_TO_CHAMPION,
}
promote = all(checks.values())
print({k: round(float(v), 4) for k, v in metrics.items()}, checks, "promote:", promote)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Log, register and promote

# COMMAND ----------

from mlflow.models import infer_signature

mlflow.set_experiment(EXPERIMENT)

report = "\n\n".join([
    "Lapse-risk model evaluation on the held-out week",
    "Split\n" + "\n".join(f"  {k}: {v}" for k, v in split.items()),
    "Metrics\n" + "\n".join(f"  {k}: {float(v):.4f}" for k, v in metrics.items()),
    "Promotion gate\n" + "\n".join(f"  {'pass' if ok else 'FAIL'}: {name}" for name, ok in checks.items())
    + f"\n  champion alias moved to the new version: {promote}",
    "Calibration by score decile (10 is riskiest)\n" + calibration.to_string(),
    "By segment\n" + by_segment.to_string(),
    "Permutation importance (drop in AUC when the feature is shuffled)\n" + importance.head(12).to_string(),
])

with mlflow.start_run(run_name="lapse-model") as run:
    mlflow.log_params({**PARAMS, "min_history_days": MIN_HISTORY_DAYS, "features": ",".join(FEATURES),
                       "boosting_iterations": int(model.named_steps["clf"].n_iter_), **split})
    mlflow.log_metrics({k: float(v) for k, v in metrics.items()})
    mlflow.log_text(report, "evaluation.txt")
    example = test[FEATURES].head(5)
    info = mlflow.sklearn.log_model(
        model,
        name="model",
        signature=infer_signature(example, model.predict_proba(example)),
        input_example=example,
        pyfunc_predict_fn="predict_proba",  # column 1 is the chance of lapsing
        registered_model_name=FULL_NAME,
    )

version = info.registered_model_version
for key, value in {"test_auc": f"{metrics['test_auc']:.4f}", "baseline_auc": f"{metrics['baseline_auc']:.4f}",
                   "train_dates": split["train_dates"], "test_dates": split["test_dates"]}.items():
    client.set_model_version_tag(FULL_NAME, version, key, value)
if promote:
    client.set_registered_model_alias(FULL_NAME, "champion", version)

print(report)

# COMMAND ----------

import json

dbutils.notebook.exit(json.dumps({
    "model": FULL_NAME, "version": version, "champion": promote, "mlflow_run_id": run.info.run_id,
    "report": report,
}))
