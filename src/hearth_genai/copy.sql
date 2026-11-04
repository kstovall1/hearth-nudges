-- Writes and checks the copy for every send in hearth_serving.next_best_action. Runs after the picker.
-- 1. Brief: the facts a message may use, from the nudge type, metro and days away. No PII or sensitive attributes.
-- 2. Write with Claude Haiku (hearth_ml.write_nudge_copy), then check with OpenJev (hearth_ml.check_nudge_copy).
-- 3. Messages that fail get one rewrite and a second check.
-- 4. Passed messages are ready to send. The rest wait for a marketer in the lifecycle console.
-- Each step is saved to a table before the next reads it, so the check sees exactly the message that is stored.
USE CATALOG IDENTIFIER(:catalog);

CREATE OR REPLACE TABLE hearth_ml.copy_run_briefs AS
SELECT a.member_id, a.decision_date, a.nudge_type,
  hearth_ml.nudge_brief(a.nudge_type, m.metro, d.days_away_bucket, f.tenure_days < 30) AS brief
FROM hearth_serving.next_best_action a
JOIN hearth_gold.nudge_decisions d USING (member_id, decision_date)
JOIN hearth_gold.member_daily_features f ON f.member_id = a.member_id AND f.feature_date = d.features_as_of
JOIN hearth_silver.members m ON m.member_id = a.member_id
WHERE a.action = 'send';

CREATE OR REPLACE TABLE hearth_ml.copy_run_written1 AS
SELECT *, 1 AS attempt, hearth_ml.write_nudge_copy(brief) AS copy_text FROM hearth_ml.copy_run_briefs;

CREATE OR REPLACE TABLE hearth_ml.copy_run_checked1 AS
SELECT *, hearth_ml.check_nudge_copy(brief, copy_text) AS copy_check FROM hearth_ml.copy_run_written1;

CREATE OR REPLACE TABLE hearth_ml.copy_run_written2 AS
SELECT member_id, decision_date, nudge_type, brief, 2 AS attempt, hearth_ml.write_nudge_copy(brief) AS copy_text
FROM hearth_ml.copy_run_checked1 WHERE NOT copy_check.passed;

CREATE OR REPLACE TABLE hearth_ml.copy_run_checked2 AS
SELECT *, hearth_ml.check_nudge_copy(brief, copy_text) AS copy_check FROM hearth_ml.copy_run_written2;

-- Every attempt, kept for review and for the eval
CREATE TABLE IF NOT EXISTS hearth_gold.nudge_copy (
  member_id STRING NOT NULL, decision_date DATE NOT NULL, attempt INT NOT NULL, nudge_type STRING, brief STRING,
  copy_text STRING, passed BOOLEAN, reasons ARRAY<STRING>, personal_info DOUBLE, sensitive_trait DOUBLE,
  unsupported_claim DOUBLE, guilt_or_pressure DOUBLE, on_topic DOUBLE, tone DOUBLE, copy_model STRING,
  check_model STRING, checked_at TIMESTAMP,
  CONSTRAINT nudge_copy_pk PRIMARY KEY (member_id, decision_date, attempt)
)
CLUSTER BY (decision_date)
COMMENT 'Every generated nudge message and its check, one row per attempt. Written by the hearth-daily job. Messages use only nudge facts, never PII or sensitive attributes.';

-- A rerun replaces the day's attempts. Merging would leave an earlier run's second attempts behind.
DELETE FROM hearth_gold.nudge_copy WHERE decision_date IN (SELECT DISTINCT decision_date FROM hearth_ml.copy_run_briefs);

INSERT INTO hearth_gold.nudge_copy
SELECT member_id, decision_date, attempt, nudge_type, brief, copy_text, copy_check.passed, copy_check.reasons,
  copy_check.personal_info, copy_check.sensitive_trait, copy_check.unsupported_claim, copy_check.guilt_or_pressure,
  copy_check.on_topic, copy_check.tone,
  'system.ai.claude-haiku-5-5' AS copy_model, 'ai_decide (OpenJev)' AS check_model, current_timestamp() AS checked_at
FROM (SELECT * FROM hearth_ml.copy_run_checked1 UNION ALL SELECT * FROM hearth_ml.copy_run_checked2);

-- The message each send goes out with: the first attempt that passed, otherwise the last attempt, for review
MERGE INTO hearth_serving.next_best_action t
USING (
  SELECT member_id, decision_date, copy_text, copy_check.passed AS passed
  FROM (SELECT * FROM hearth_ml.copy_run_checked1 UNION ALL SELECT * FROM hearth_ml.copy_run_checked2)
  QUALIFY row_number() OVER (PARTITION BY member_id ORDER BY copy_check.passed DESC, attempt DESC) = 1
) s
ON t.member_id = s.member_id AND t.decision_date = s.decision_date
WHEN MATCHED THEN UPDATE SET
  copy_text = s.copy_text,
  copy_status = CASE WHEN s.passed THEN 'passed' ELSE 'needs_review' END,
  copy_checked_at = current_timestamp();

DROP TABLE hearth_ml.copy_run_briefs;
DROP TABLE hearth_ml.copy_run_written1;
DROP TABLE hearth_ml.copy_run_checked1;
DROP TABLE hearth_ml.copy_run_written2;
DROP TABLE hearth_ml.copy_run_checked2;
