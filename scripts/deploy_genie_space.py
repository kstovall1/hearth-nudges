"""Check and deploy the Hearth Campaigns Genie Agent from src/hearth_semantic/genie_space.py.

1. Runs every example and benchmark SQL statement on the warehouse. Stops if any fails or returns no rows.
2. Creates the space, or updates it if one with the same title exists. Finds it by title, so no space id is stored.
3. Gives hearth_marketing CAN RUN on the space and CAN USE on the warehouse Genie queries through.

Usage: uv run python scripts/deploy_genie_space.py [--check-only]
"""

import argparse
import json
import sys
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import iam, sql
from validate_landing import run

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "hearth_semantic"))
import genie_space as G  # noqa: E402


def check_sql(w, warehouse_id):
    failed = 0
    statements = [("example", q, s) for q, s, _ in G.EXAMPLE_SQLS] + [("benchmark", q, s) for q, s in G.BENCHMARKS]
    for kind, question, statement in statements:
        out = run(w, warehouse_id, statement)
        rows = len(out.splitlines()) - 1
        ok = not out.startswith("FAILED") and rows > 0
        failed += not ok
        print(f"{'ok' if ok else 'FAILED'}  {kind:<9} {rows:>3} rows  {question}")
        if not ok:
            print("   ", out[:300])
    return failed


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--profile", default="fevm-katie-febar")
    p.add_argument("--warehouse", default="Serverless Starter Warehouse")
    p.add_argument("--check-only", action="store_true")
    args = p.parse_args()
    w = WorkspaceClient(profile=args.profile)
    warehouse_id = next(wh.id for wh in w.warehouses.list() if wh.name == args.warehouse)

    print("## SQL checks\n")
    if check_sql(w, warehouse_id):
        sys.exit("Some SQL failed. Nothing deployed.")
    if args.check_only:
        return

    payload = json.dumps(G.serialized_space())
    existing = [s for s in w.genie.list_spaces().spaces or [] if s.title == G.TITLE]
    if existing:
        space = w.genie.update_space(existing[0].space_id, serialized_space=payload, description=G.DESCRIPTION,
                                     warehouse_id=warehouse_id)
        action = "updated"
    else:
        parent = f"/Workspace/Users/{w.current_user.me().user_name}/hearth-nudges"
        w.workspace.mkdirs(parent)
        space = w.genie.create_space(warehouse_id=warehouse_id, serialized_space=payload, title=G.TITLE,
                                     description=G.DESCRIPTION, parent_path=parent)
        action = "created"

    w.permissions.update("genie", space.space_id, access_control_list=[
        iam.AccessControlRequest(group_name="hearth_marketing", permission_level=iam.PermissionLevel.CAN_RUN)])
    w.warehouses.update_permissions(warehouse_id, access_control_list=[
        sql.WarehouseAccessControlRequest(group_name="hearth_marketing", permission_level=sql.WarehousePermissionLevel.CAN_USE)])
    s = G.serialized_space()
    print(f"\n## Space {action}\n")
    print(f"title: {G.TITLE}")
    print(f"sources: {[m['identifier'].split('.')[-1] for m in s['data_sources']['metric_views']]} + "
          f"{[t['identifier'].split('.')[-1] for t in s['data_sources']['tables']]}")
    print(f"sample questions {len(G.SAMPLE_QUESTIONS)}, example SQL {len(G.EXAMPLE_SQLS)}, benchmarks {len(G.BENCHMARKS)}, "
          f"text instructions {len(G.TEXT_INSTRUCTIONS)} characters")
    print("hearth_marketing: CAN RUN on the space, CAN USE on the warehouse")


if __name__ == "__main__":
    main()
