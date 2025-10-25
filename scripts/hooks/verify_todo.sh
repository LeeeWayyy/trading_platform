#!/bin/bash
# Verify TodoWrite state is active (prevents commits without tracked work)
#
# Checks:
#   - .claude/state/current-todo.json exists
#   - Has at least one non-completed todo
#
# Exit codes:
#   0 - TodoWrite state valid (active todos exist) OR missing todo file (warning only)
#   1 - TodoWrite state invalid (no active todos) OR jq not installed (warning only)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TODO_FILE="$REPO_ROOT/.claude/state/current-todo.json"

# Check if jq is installed (required for JSON parsing)
if ! command -v jq &> /dev/null; then
    echo "⚠️  Warning: jq not installed - skipping TodoWrite state check"
    echo ""
    echo "To enable TodoWrite verification, install jq:"
    echo "  • macOS: brew install jq"
    echo "  • Ubuntu/Debian: sudo apt-get install jq"
    echo "  • CentOS/RHEL: sudo yum install jq"
    echo ""
    # Exit non-zero to signal warning (orchestrator handles as warning-only, not blocking)
    exit 1
fi

# Check if todo file exists
if [ ! -f "$TODO_FILE" ]; then
    echo "⚠️  Warning: No TodoWrite state found (.claude/state/current-todo.json)"
    echo ""
    echo "You're committing without tracked todos. This is allowed but not recommended."
    echo ""
    echo "💡 Use TodoWrite tool to track your work:"
    echo "   - Break down your task into logical components"
    echo "   - Follow 4-step pattern: Implement → Test → Review → Commit"
    echo ""
    # Allow commit without todo (warning only)
    exit 0
fi

# Parse JSON and check for active todos
# Active = status is "in_progress" or "pending"
ACTIVE_COUNT=$(jq '[.[] | select(.status == "in_progress" or .status == "pending")] | length' "$TODO_FILE" 2>/dev/null || echo "0")

if [ "$ACTIVE_COUNT" -gt 0 ]; then
    # Valid state: has active todos
    exit 0
else
    echo "❌ No active todos found in TodoWrite state"
    echo ""
    echo "All todos are completed. Please:"
    echo "  1. Add new todos for your next work, OR"
    echo "  2. Clear the todo list if work is complete"
    echo ""
    echo "💡 This ensures you're following the 4-step pattern and tracking progress"
    echo ""
    echo "To bypass this check: git commit --no-verify"
    echo ""
    exit 1
fi
