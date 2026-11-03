"""Evidence for Unity Catalog governance: masks, tags, grants and lineage, plus what the caller sees.

Usage: uv run python scripts/governance_evidence.py [--masks-only]
--masks-only runs just the caller checks, for repeating them after a group membership change.
Shows member PII only as counts and the masked or fake values UC returns, never anything real.
"""

import argparse

from databricks.sdk import WorkspaceClient
from validate_landing import run

GROUPS = ["hearth_marketing", "hearth_pii_readers"]


def caller_queries(cat):
    t = f"{cat}.hearth_silver.members"
    return [
        ("Caller's groups", "SELECT " + ", ".join(f"is_account_group_member('{g}') AS in_{g}" for g in GROUPS)),
        ("What the caller sees in hearth_silver.members", f"""
SELECT count(*) AS members,
  count_if(first_name = '***') AS first_name_masked,
  count_if(email = '***') AS email_masked,
  count_if(email RLIKE '@example[.](com|net|org)$') AS email_visible,
  count_if(phone = '***') AS phone_masked,
  count_if(gender_identity = '***') AS gender_identity_masked,
  count_if(seeking = '***') AS seeking_masked,
  count_if(age_band IS NOT NULL) AS age_band_visible
FROM {t}"""),
        ("One row as the caller sees it (all values are synthetic)", f"""
SELECT member_id, first_name, email, phone, gender_identity, seeking, age_band, metro
FROM {t} ORDER BY member_id LIMIT 1"""),
    ]


def setup_queries(cat):
    grants = " UNION ALL ".join(
        f"SELECT '{s}' AS securable, grantee, privilege_type FROM {cat}.information_schema.schema_privileges "
        f"WHERE schema_name = '{s}' AND grantee IN ('hearth_marketing', 'hearth_pii_readers')"
        for s in ["hearth_raw", "hearth_bronze", "hearth_silver", "hearth_gold", "hearth_ml", "hearth_serving"]
    )
    return [
        ("Column masks", f"""
SELECT table_schema, table_name, column_name, mask_name
FROM {cat}.information_schema.column_masks
WHERE table_schema LIKE 'hearth_%' ORDER BY 1, 2, 3"""),
        ("Column tags", f"""
SELECT schema_name, table_name, column_name, tag_name, tag_value
FROM {cat}.information_schema.column_tags
WHERE schema_name LIKE 'hearth_%' ORDER BY 1, 2, 5, 3"""),
        ("Table tags", f"""
SELECT schema_name, table_name, tag_name, tag_value
FROM {cat}.information_schema.table_tags WHERE schema_name LIKE 'hearth_%' ORDER BY 1, 2"""),
        ("Catalog grants to the Hearth groups", f"""
SELECT grantee, privilege_type FROM {cat}.information_schema.catalog_privileges
WHERE grantee IN ('hearth_marketing', 'hearth_pii_readers') ORDER BY 1, 2"""),
        ("Schema grants to the Hearth groups (raw, bronze, ml and serving should have none)",
         f"SELECT * FROM ({grants}) ORDER BY 1, 2, 3"),
        ("Lineage into and out of the members tables", f"""
SELECT DISTINCT source_table_full_name AS source, target_table_full_name AS target
FROM system.access.table_lineage
WHERE (source_table_full_name LIKE '{cat}.hearth_%members' OR target_table_full_name LIKE '{cat}.hearth_%members')
  AND target_table_full_name IS NOT NULL AND source_table_full_name IS NOT NULL
ORDER BY 1, 2"""),
        # System lineage doesn't record the landing files as a source, so bronze carries the file per row
        ("Landing files behind hearth_bronze.members", f"""
SELECT count(DISTINCT _source_file) AS files, min(regexp_extract(_source_file, '(members/dt=[0-9-]+)', 1)) AS first_folder,
  max(regexp_extract(_source_file, '(members/dt=[0-9-]+)', 1)) AS last_folder, count(*) AS rows
FROM {cat}.hearth_bronze.members"""),
    ]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--profile", default="fevm-katie-febar")
    p.add_argument("--catalog", default="katie_febar_catalog")
    p.add_argument("--warehouse", default="Serverless Starter Warehouse")
    p.add_argument("--masks-only", action="store_true")
    args = p.parse_args()
    w = WorkspaceClient(profile=args.profile)
    warehouse_id = next(wh.id for wh in w.warehouses.list() if wh.name == args.warehouse)
    sections = caller_queries(args.catalog) + ([] if args.masks_only else setup_queries(args.catalog))
    for title, sql in sections:
        print(f"## {title}\n")
        print(run(w, warehouse_id, sql.strip()))
        print()


if __name__ == "__main__":
    main()
