"""Write the bundle variables that are workspace ids into the gitignored .databricks/bundle/dev/variable-overrides.json,
found by name, so they never land in the repo. Run after deploy_genie_space.py and before bundle deploy.

Usage: uv run python scripts/set_bundle_overrides.py
"""

import json
import sys
from pathlib import Path

from databricks.sdk import WorkspaceClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "hearth_semantic"))
import genie_space as G  # noqa: E402

w = WorkspaceClient(profile="fevm-katie-febar")
space = next(s.space_id for s in w.genie.list_spaces().spaces if s.title == G.TITLE)
path = Path(__file__).resolve().parents[1] / ".databricks" / "bundle" / "dev" / "variable-overrides.json"
path.parent.mkdir(parents=True, exist_ok=True)
values = json.loads(path.read_text()) if path.exists() else {}
values["genie_space_id"] = space
path.write_text(json.dumps(values, indent=2))
print(f"wrote genie_space_id to {path.relative_to(path.parents[3])}")
