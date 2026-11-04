"""Run a SQL file on the warehouse, statement by statement, with :catalog filled in. For applying
src/hearth_genai/functions.sql by hand. The hearth-daily job runs the same files as SQL tasks.

Usage: uv run python scripts/run_sql_file.py src/hearth_genai/functions.sql
"""

import argparse
import re

from databricks.sdk import WorkspaceClient
from validate_landing import run


def statements(sql):
    """Split on semicolons that end a line, outside quoted strings and $$ blocks. Skips -- comment lines."""
    out, buf, quoted, dollar = [], [], False, False
    lines = [l for l in sql.splitlines(keepends=True) if dollar or not l.strip().startswith("--")]
    text = "".join(lines)
    i = 0
    while i < len(text):
        if text.startswith("$$", i):
            dollar = not dollar
            buf.append("$$")
            i += 2
            continue
        ch = text[i]
        buf.append(ch)
        if ch == "'" and not dollar:
            quoted = not quoted
        elif ch == ";" and not quoted and not dollar and (i + 1 == len(text) or text[i + 1] == "\n"):
            out.append("".join(buf))
            buf = []
        i += 1
    out.append("".join(buf))
    return [s.strip().rstrip(";").strip() for s in out if s.strip().rstrip(";").strip()]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("path")
    p.add_argument("--profile", default="fevm-katie-febar")
    p.add_argument("--catalog", default="katie_febar_catalog")
    p.add_argument("--warehouse", default="Serverless Starter Warehouse")
    args = p.parse_args()
    w = WorkspaceClient(profile=args.profile)
    warehouse_id = next(wh.id for wh in w.warehouses.list() if wh.name == args.warehouse)
    sql = open(args.path).read().replace("IDENTIFIER(:catalog)", args.catalog)
    for s in statements(sql):
        out = run(w, warehouse_id, s)
        head = re.sub(r"\s+", " ", s)[:80]
        print(f"{'FAILED' if out.startswith('FAILED') else 'ok'}: {head}")
        if out.startswith("FAILED"):
            print(out)


if __name__ == "__main__":
    main()
