#!/bin/bash
# Verify tests pass before committing
# Delegates to existing pre-commit-hook.sh which runs CI checks
#
# Exit codes:
#   0 - Tests passed
#   1 - Tests failed

set -euo pipefail

# Delegate to existing comprehensive CI hook
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Run existing pre-commit hook
"$REPO_ROOT/scripts/pre-commit-hook.sh"

# Exit code propagates from pre-commit-hook.sh
