#!/bin/bash
# Zen Pre-Commit Orchestrator
# Version-controlled git hook that enforces workflow quality gates
#
# Quality Gates (Hard Blocks):
#   1. Branch naming convention
#   2. TodoWrite state verification (with warning fallback if jq missing)
#   3. Tests must pass (mypy, ruff, pytest)
#
# Exit codes:
#   0 - All gates passed
#   1 - One or more gates failed

set -euo pipefail

# Setup
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_DIR="$REPO_ROOT/logs"
EVENT_LOG="$LOG_DIR/zen_hooks_events.jsonl"
OVERRIDE_LOG="$LOG_DIR/zen_hooks_overrides.jsonl"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Ensure log directory exists
mkdir -p "$LOG_DIR"

# Portable timer function (works on macOS and Linux)
get_timestamp_ms() {
    python3 -c 'import time; print(int(time.time() * 1000))'
}

# Start time for metrics
START_TIME=$(get_timestamp_ms)

# Log event
log_event() {
    local gate="$1"
    local status="$2"
    local duration="$3"
    local timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    echo "{\"timestamp\":\"$timestamp\",\"gate\":\"$gate\",\"status\":\"$status\",\"duration_ms\":$duration}" >> "$EVENT_LOG"
}

# Log override usage (JSON-safe)
log_override() {
    local reason="$1"
    local timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    local branch=$(git branch --show-current || echo "")
    local user=$(git config user.name || echo "unknown")

    # Use Python to properly escape JSON fields (prevents injection)
    python3 -c "import json, sys; print(json.dumps({
        'timestamp': sys.argv[1],
        'user': sys.argv[2],
        'branch': sys.argv[3],
        'reason': sys.argv[4]
    }))" "$timestamp" "$user" "$branch" "$reason" >> "$OVERRIDE_LOG"
}

# Print header
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}🔍 QUALITY GATE 0: CI Checks${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Track failures
FAILED=0

# ============================================================================
# Emergency Override
# ============================================================================
# Check for PRE_COMMIT_OVERRIDE environment variable
if [ "${PRE_COMMIT_OVERRIDE:-0}" = "1" ]; then
    log_override "PRE_COMMIT_OVERRIDE=1"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}⚠  PRE_COMMIT_OVERRIDE=1 detected${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "🚨 Bypassing all quality gates!"
    echo ""
    echo "This should ONLY be used in emergency situations:"
    echo "  • Bootstrap scenario (hooks catching pre-existing issues)"
    echo "  • Critical hotfix deployment"
    echo "  • Infrastructure failure preventing normal workflow"
    echo ""
    echo "⚠️  WARNING: Override usage is logged to:"
    echo "   $OVERRIDE_LOG"
    echo ""
    exit 0
fi

# ============================================================================
# GATE 1: Branch Naming Convention
# ============================================================================
echo "🏷️  Checking branch naming convention..."
GATE_START=$(get_timestamp_ms)

# Temporarily disable set -e to capture exit code without aborting
set +e
"$SCRIPT_DIR/verify_branch_name.sh"
BRANCH_EXIT_CODE=$?
set -e

GATE_END=$(get_timestamp_ms)
DURATION=$((GATE_END - GATE_START))

if [ $BRANCH_EXIT_CODE -eq 0 ]; then
    log_event "branch_name" "pass" "$DURATION"
    echo -e "${GREEN}✓${NC} Branch naming convention valid"
else
    log_event "branch_name" "fail" "$DURATION"
    echo -e "${RED}✗${NC} Branch naming convention failed"
    FAILED=1
fi
echo ""

# ============================================================================
# GATE 2: TodoWrite State (Hard Block)
# ============================================================================
echo "📝 Checking TodoWrite state..."
GATE_START=$(get_timestamp_ms)

# Temporarily disable set -e to capture exit code without aborting
set +e
"$SCRIPT_DIR/verify_todo.sh"
TODO_EXIT_CODE=$?
set -e

GATE_END=$(get_timestamp_ms)
DURATION=$((GATE_END - GATE_START))

if [ $TODO_EXIT_CODE -eq 0 ]; then
    # Success: has active todos
    log_event "todo_state" "pass" "$DURATION"
    echo -e "${GREEN}✓${NC} TodoWrite state valid"
elif [ $TODO_EXIT_CODE -eq 1 ]; then
    # Warning only: jq not installed or missing todo file
    log_event "todo_state" "warn" "$DURATION"
    echo -e "${YELLOW}⚠${NC}  TodoWrite check skipped (see warning above)"
else
    # Hard failure (exit code 2): no active todos when file exists
    log_event "todo_state" "fail" "$DURATION"
    echo -e "${RED}✗${NC} TodoWrite state invalid"
    FAILED=1
fi
echo ""

# ============================================================================
# GATE 3: Tests Pass (CI Checks)
# ============================================================================
# Note: This calls scripts/pre-commit-hook.sh which runs:
#   - mypy type checking
#   - ruff linting
#   - pytest unit tests

if "$SCRIPT_DIR/verify_tests.sh"; then
    : # Tests passed (logged by pre-commit-hook.sh)
else
    FAILED=1
    # Error message already printed by pre-commit-hook.sh
fi

# ============================================================================
# Final Result
# ============================================================================
END_TIME=$(get_timestamp_ms)
TOTAL_DURATION=$((END_TIME - START_TIME))

if [ $FAILED -eq 0 ]; then
    log_event "overall" "pass" "$TOTAL_DURATION"

    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}✓ All quality gates passed!${NC}"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "⏱️  Total time: ${TOTAL_DURATION}ms"
    echo "💡 Tip: Your commit will now proceed. CI should pass without issues."
    echo ""
    exit 0
else
    log_event "overall" "fail" "$TOTAL_DURATION"

    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${RED}✗ Pre-commit checks failed!${NC}"
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "⏱️  Total time: ${TOTAL_DURATION}ms"
    echo ""
    exit 1
fi
