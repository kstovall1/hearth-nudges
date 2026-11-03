-- Column mask functions for member PII and sensitive attributes. Runs before the pipeline, which
-- attaches them to hearth_silver.members in its CREATE statement (pipeline tables can't be altered).
-- Real values are only for hearth_pii_readers. Everyone else, marketing included, sees '***'.
-- Membership is checked for whoever runs the query. During a pipeline refresh the check uses the
-- pipeline owner, which is why bronze, the pipeline's source, carries no mask and no grants.
USE CATALOG IDENTIFIER(:catalog);

CREATE OR REPLACE FUNCTION hearth_silver.mask_contact(value STRING)
RETURNS STRING
COMMENT 'Column mask for contact PII: first_name, email, phone. Real values for hearth_pii_readers only.'
RETURN CASE WHEN is_account_group_member('hearth_pii_readers') THEN value ELSE '***' END;

CREATE OR REPLACE FUNCTION hearth_silver.mask_sensitive(value STRING)
RETURNS STRING
COMMENT 'Column mask for sensitive attributes: gender_identity, seeking. Real values for hearth_pii_readers only, for fairness checks. Never a model feature or copy input.'
RETURN CASE WHEN is_account_group_member('hearth_pii_readers') THEN value ELSE '***' END;
