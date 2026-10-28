#!/usr/bin/env python3
"""Pre-commit check for things the public repo must never contain.

Scans lines added in the staged diff for workspace hosts, IDs, tokens,
internal links and Databricks emails, plus the literal values in the
gitignored .local/denylist.txt. The managed Databricks hook already runs
gitleaks for secrets. This covers what it misses.

Usage: check_leaks.py           scan staged changes (the pre-commit hook)
       check_leaks.py FILE...   scan whole files (an audit of tracked files)
"""

import os
import re
import subprocess
import sys

PATTERNS = {
    "workspace_host": re.compile(
        r"[a-z0-9-]+\.(cloud\.databricks\.com|azuredatabricks\.net|gcp\.databricks\.com|databricksapps\.com)"
        r"|adb-\d+\.\d+\.\w+",
        re.IGNORECASE,
    ),
    "uuid": re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE),
    "databricks_token": re.compile(r"\bdapi[0-9a-f]{32}\b", re.IGNORECASE),
    # Warehouse IDs are 16 hex characters. Only flag them next to a warehouse mention.
    "warehouse_id": re.compile(r"warehouse\S*[\s\"':=/]+[0-9a-f]{16}\b|\b[0-9a-f]{16}\b(?=.*warehouse)", re.IGNORECASE),
    "slack_link": re.compile(r"[a-z0-9-]+\.slack\.com/archives/\S+", re.IGNORECASE),
    "google_doc_link": re.compile(r"docs\.google\.com/\S+", re.IGNORECASE),
    "go_link": re.compile(r"(?<![\w./-])go/[\w-]+", re.IGNORECASE),
    "databricks_email": re.compile(r"\b[\w.+-]+@databricks\.com\b", re.IGNORECASE),
}

# Paths that must never be committed, whatever they contain.
BLOCKED_PATHS = re.compile(r"(^|/)CLAUDE\.local\.md$|^\.local/")

ALLOW_MARKER = "leak-check: allow"


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def load_denylist(repo_root):
    path = os.path.join(repo_root, ".local", "denylist.txt")
    if not os.path.exists(path):
        print("leak-check: warning: .local/denylist.txt not found, running generic patterns only", file=sys.stderr)
        return []
    with open(path) as f:
        return [line.strip().lower() for line in f if line.strip() and not line.strip().startswith("#")]


def redact(value):
    if len(value) <= 8:
        return "*" * len(value)
    return value[:3] + "*" * (len(value) - 6) + value[-3:]


def find_leaks(text, denylist):
    """Return (rule, matched value) pairs for one line."""
    if ALLOW_MARKER in text:
        return []
    hits = [(rule, m.group(0)) for rule, pattern in PATTERNS.items() for m in pattern.finditer(text)]
    lower = text.lower()
    for value in denylist:
        start = lower.find(value)
        if start >= 0:
            hits.append(("denylist", text[start:start + len(value)]))
    return hits


def staged_lines():
    """Yield (path, line number, text) for every line added in the staged diff."""
    path, line_no = None, 0
    for line in git("diff", "--cached", "--unified=0", "--no-color", "--diff-filter=d").splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            line_no = int(re.search(r"\+(\d+)", line).group(1))
        elif line.startswith("+") and path:
            yield path, line_no, line[1:]
            line_no += 1


def file_lines(paths):
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                for line_no, text in enumerate(f, 1):
                    yield path, line_no, text.rstrip("\n")
        except (UnicodeDecodeError, IsADirectoryError):
            continue  # binary or not a file


def main(argv):
    repo_root = git("rev-parse", "--show-toplevel").strip()
    os.chdir(repo_root)
    denylist = load_denylist(repo_root)

    if argv:
        paths, lines = argv, file_lines(argv)
    else:
        paths = git("diff", "--cached", "--name-only", "--diff-filter=d").splitlines()
        lines = staged_lines()  # git shows binary files without added lines, so they skip themselves

    problems = [f"{p}: this file must never be committed" for p in paths if BLOCKED_PATHS.search(p)]
    for path, line_no, text in lines:
        if BLOCKED_PATHS.search(path):
            continue
        for rule, value in find_leaks(text, denylist):
            problems.append(f"{path}:{line_no}: {rule}: {redact(value)}")

    if problems:
        print("leak-check: blocked. Remove these, or add 'leak-check: allow' to a line that is a false positive:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
