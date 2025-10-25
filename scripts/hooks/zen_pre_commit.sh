#!/bin/bash
# Zen Pre-Commit Orchestrator
# Version-controlled git hook that enforces workflow quality gates
#
# Quality Gates (Hard Blocks):
#   1. Branch naming convention
#   2. Tests must pass (mypy, ruff, pytest)
#   3. TodoWrite state verification (warning only)
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

# Log override usage
log_override() {
    local reason="$1"
    local timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    local branch=$(git branch --show-current)
    local user=$(git config user.name || echo "unknown")

    echo "{\"timestamp\":\"$timestamp\",\"user\":\"$user\",\"branch\":\"$branch\",\"reason\":\"$reason\"}" >> "$OVERRIDE_LOG"
}

# Print header
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}🔍 QUALITY GATE 0: CI Checks${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Track failures
FAILED=0

# ============================================================================
# GATE 1: Branch Naming Convention
# ============================================================================
echo "🏷️  Checking branch naming convention..."
GATE_START=$(get_timestamp_ms)

if "$SCRIPT_DIR/verify_branch_name.sh"; then
    GATE_END=$(get_timestamp_ms)
    DURATION=$((GATE_END - GATE_START))
    log_event "branch_name" "pass" "$DURATION"
    echo -e "${GREEN}✓${NC} Branch naming convention valid"
else
    GATE_END=$(get_timestamp_ms)
    DURATION=$((GATE_END - GATE_START))
    log_event "branch_name" "fail" "$DURATION"
    echo -e "${RED}✗${NC} Branch naming convention failed"
    FAILED=1
fi
echo ""

# ============================================================================
# GATE 2: TodoWrite State (Warning Only)
# ============================================================================
echo "📝 Checking TodoWrite state..."
GATE_START=$(get_timestamp_ms)

if "$SCRIPT_DIR/verify_todo.sh"; then
    GATE_END=$(get_timestamp_ms)
    DURATION=$((GATE_END - GATE_START))
    log_event "todo_state" "pass" "$DURATION"
    echo -e "${GREEN}✓${NC} TodoWrite state valid"
else
    GATE_END=$(get_timestamp_ms)
    DURATION=$((GATE_END - GATE_START))
    log_event "todo_state" "warn" "$DURATION"
    # Note: verify_todo.sh returns non-zero but we don't block
    # It's handled as warning-only
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
