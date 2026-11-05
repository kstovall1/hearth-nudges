"""Let the lifecycle console's service principal read the synced tables in Lakebase (schema hearth).

Synced tables are owned by the sync pipeline, so the app can't grant itself access. Run this as the project owner
after the app is first deployed, and again if the app is recreated. Reads only: SELECT, never INSERT or UPDATE.

Usage: uv run python scripts/grant_lakebase_reader.py
"""

import sys
from pathlib import Path

from databricks.sdk import WorkspaceClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "hearth_serving"))
from notification_simulator import connect  # noqa: E402

w = WorkspaceClient(profile="fevm-katie-febar")
role = w.apps.get("hearth-console").service_principal_client_id
conn, _ = connect(w, "hearth-nudges", "dev", "primary", "databricks_postgres")
cur = conn.cursor()
cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
if not cur.fetchone():
    sys.exit("The app's Postgres role doesn't exist yet. Deploy the app with its postgres resource first.")
for statement in [
    f'GRANT USAGE ON SCHEMA hearth TO "{role}"',
    f'GRANT SELECT ON ALL TABLES IN SCHEMA hearth TO "{role}"',
    f'ALTER DEFAULT PRIVILEGES IN SCHEMA hearth GRANT SELECT ON TABLES TO "{role}"',
]:
    cur.execute(statement)
    print("ok:", statement.replace(role, "<app service principal>"))
cur.execute("""SELECT table_name, privilege_type FROM information_schema.role_table_grants
               WHERE grantee = %s AND table_schema = 'hearth' ORDER BY 1""", (role,))
for table, privilege in cur.fetchall():
    print(f"  {privilege} on hearth.{table}")
conn.close()
