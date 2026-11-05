-- Copies the lifecycle console's decisions from Lakebase into Delta, so analytics and Genie can see them next to
-- campaign results. Reads through the hearth_lakebase catalog, which registers the Lakebase dev branch in Unity
-- Catalog. The console owns the source tables in schema console. They hold reviewer emails, no member PII.
USE CATALOG IDENTIFIER(:catalog);

CREATE TABLE IF NOT EXISTS hearth_gold.campaign_controls (
  decision_date DATE NOT NULL,
  nudge_type STRING NOT NULL,
  status STRING COMMENT 'approved or paused by a marketer in the lifecycle console',
  note STRING,
  reviewer STRING,
  updated_at TIMESTAMP,
  copied_at TIMESTAMP,
  CONSTRAINT campaign_controls_pk PRIMARY KEY (decision_date, nudge_type)
)
COMMENT 'Marketer approvals and pauses of a nudge type for a decision date, copied daily from the lifecycle console in Lakebase.';

MERGE INTO hearth_gold.campaign_controls t
USING (
  SELECT decision_date, nudge_type, status, note, reviewer, CAST(updated_at AS TIMESTAMP) AS updated_at,
    current_timestamp() AS copied_at
  FROM hearth_lakebase.console.campaign_controls
) s
ON t.decision_date = s.decision_date AND t.nudge_type = s.nudge_type
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *;

CREATE TABLE IF NOT EXISTS hearth_gold.copy_reviews (
  review_id BIGINT NOT NULL,
  member_id STRING,
  decision_date DATE,
  nudge_type STRING,
  original_copy STRING COMMENT 'What Claude Haiku wrote',
  final_copy STRING COMMENT 'What the marketer approved or rejected, edited or not',
  decision STRING COMMENT 'approved or rejected',
  check_passed BOOLEAN COMMENT 'The governed copy check on final_copy. Approval requires a pass',
  check_reasons ARRAY<STRING>,
  reviewer STRING,
  created_at TIMESTAMP,
  copied_at TIMESTAMP,
  CONSTRAINT copy_reviews_pk PRIMARY KEY (review_id)
)
COMMENT 'Every copy review a marketer made in the lifecycle console, copied daily from Lakebase. The latest review per member and day is the one that counts.';

-- Reviews are only ever inserted, so new review ids are the whole change
MERGE INTO hearth_gold.copy_reviews t
USING (
  SELECT review_id, member_id, decision_date, nudge_type, original_copy, final_copy, decision, check_passed,
    check_reasons, reviewer, CAST(created_at AS TIMESTAMP) AS created_at, current_timestamp() AS copied_at
  FROM hearth_lakebase.console.copy_reviews
) s
ON t.review_id = s.review_id
WHEN NOT MATCHED THEN INSERT *;
