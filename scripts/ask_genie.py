"""Ask the Hearth Campaigns Genie Agent questions through the Conversation API and print what it answered:
its text, the SQL it wrote and the first rows of the result. Evidence that marketers' questions get answers.

Usage: uv run python scripts/ask_genie.py                 # the agent's sample questions
       uv run python scripts/ask_genie.py "question" ...   # your own
"""

import argparse
import sys
import time
from pathlib import Path

from databricks.sdk import WorkspaceClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "hearth_semantic"))
import genie_space as G  # noqa: E402


def ask(w, space_id, question):
    t = time.perf_counter()
    msg = w.genie.start_conversation_and_wait(space_id, question)
    seconds = time.perf_counter() - t
    lines = [f"## {question}", "", f"status {msg.status.value}, {seconds:.0f} s"]
    for a in msg.attachments or []:
        if a.text and a.text.content:
            lines += ["", "answer:", a.text.content.strip()]
        if a.query:
            lines += ["", "sql:", a.query.query.strip()]
            if a.query.description:
                lines += ["", "what it did: " + a.query.description.strip()]
            r = w.genie.get_message_attachment_query_result(space_id, msg.conversation_id, msg.message_id, a.attachment_id)
            res = r.statement_response
            if res and res.manifest and res.result:
                cols = [c.name for c in res.manifest.schema.columns]
                rows = res.result.data_array or []
                lines += ["", f"result ({res.manifest.total_row_count} rows):", "  " + " | ".join(cols)]
                lines += ["  " + " | ".join("" if v is None else str(v) for v in row) for row in rows[:12]]
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("questions", nargs="*")
    p.add_argument("--profile", default="fevm-katie-febar")
    args = p.parse_args()
    w = WorkspaceClient(profile=args.profile)
    space_id = next(s.space_id for s in w.genie.list_spaces().spaces if s.title == G.TITLE)
    for q in args.questions or G.SAMPLE_QUESTIONS:
        print(ask(w, space_id, q))
        print()


if __name__ == "__main__":
    main()
