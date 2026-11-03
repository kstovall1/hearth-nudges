-- Catalog access and column tags. Runs after the pipeline, so every table it tags exists.
-- Schema grants live in resources/schemas.yml. The catalog isn't a bundle resource, so its grant is here.
USE CATALOG IDENTIFIER(:catalog);

GRANT USE CATALOG ON CATALOG IDENTIFIER(:catalog) TO `hearth_marketing`;
GRANT USE CATALOG ON CATALOG IDENTIFIER(:catalog) TO `hearth_pii_readers`;

-- hearth_class marks every place member PII and sensitive attributes live, raw or masked.
ALTER TABLE hearth_bronze.members ALTER COLUMN first_name SET TAGS ('hearth_class' = 'pii');
ALTER TABLE hearth_bronze.members ALTER COLUMN email SET TAGS ('hearth_class' = 'pii');
ALTER TABLE hearth_bronze.members ALTER COLUMN phone SET TAGS ('hearth_class' = 'pii');
ALTER TABLE hearth_bronze.members ALTER COLUMN gender_identity SET TAGS ('hearth_class' = 'sensitive');
ALTER TABLE hearth_bronze.members ALTER COLUMN seeking SET TAGS ('hearth_class' = 'sensitive');

ALTER TABLE hearth_silver.members ALTER COLUMN first_name SET TAGS ('hearth_class' = 'pii');
ALTER TABLE hearth_silver.members ALTER COLUMN email SET TAGS ('hearth_class' = 'pii');
ALTER TABLE hearth_silver.members ALTER COLUMN phone SET TAGS ('hearth_class' = 'pii');
ALTER TABLE hearth_silver.members ALTER COLUMN gender_identity SET TAGS ('hearth_class' = 'sensitive');
ALTER TABLE hearth_silver.members ALTER COLUMN seeking SET TAGS ('hearth_class' = 'sensitive');

-- The generator's planted answers. Only for checking the uplift model, never a feature or a grant.
ALTER TABLE hearth_ml.generator_truth SET TAGS ('hearth_use' = 'evaluation_only');
