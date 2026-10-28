#!/bin/bash
# Wire scripts/check_leaks.py into this clone's repo-local pre-commit hook.
# The managed Databricks pre-commit runs .git/hooks/pre-commit before its own checks.
# Run once after cloning.
set -e
hook="$(git rev-parse --git-common-dir)/hooks/pre-commit"
mkdir -p "$(dirname "$hook")"
cat > "$hook" <<'HOOK'
#!/bin/bash
exec python3 "$(git rev-parse --show-toplevel)/scripts/check_leaks.py"
HOOK
chmod +x "$hook"
echo "Installed $hook"
