# Databricks notebook source
# MAGIC %md
# MAGIC # Notification service simulator, in region
# MAGIC
# MAGIC Runs `notification_simulator.py` on serverless compute in the workspace's region, so the latency is what a
# MAGIC notification service running next to Lakebase would see. The laptop run adds an internet round trip.

# COMMAND ----------

dbutils.widgets.text("lookups", "500")

import json

from databricks.sdk import WorkspaceClient

import notification_simulator as sim

report = sim.main(["--lookups", dbutils.widgets.get("lookups")], client=WorkspaceClient(), where="serverless job in region")
dbutils.notebook.exit(json.dumps({"report": report}))
