#!/bin/bash
# Verify tests pass before committing
# Runs quick lint checks (full test suite is handled by make ci-local)
#
# Exit codes:
#   0 - Checks passed
#   1 - Checks failed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$REPO_ROOT"

# Quick lint checks only — full test suite runs via make ci-local
source .venv/bin/activate 2>/dev/null || true

echo "Running ruff check..."
python3 -m ruff check --select E,F,W --quiet . || exit 1

echo "Running mypy on staged files..."
# Pipe git diff -z directly to xargs -0 to preserve NUL delimiters.
# Bash variables strip NUL bytes, so we must NOT store the output in a variable.
if git diff --cached --name-only --diff-filter=ACM -- '*.py' | grep -q .; then
    git diff --cached --name-only --diff-filter=ACM -z -- '*.py' \
        | xargs -0 -r python3 -m mypy --ignore-missing-imports --no-error-summary || exit 1
fi
