#!/bin/bash
# Unit tests for verify_todo.sh
# Tests TodoWrite state verification logic

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK_SCRIPT="$REPO_ROOT/scripts/hooks/verify_todo.sh"
TODO_FILE="$REPO_ROOT/.claude/state/current-todo.json"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

# Test counter
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

# Backup existing todo file if it exists
if [ -f "$TODO_FILE" ]; then
    cp "$TODO_FILE" "$TODO_FILE.backup"
    BACKUP_EXISTS=true
else
    BACKUP_EXISTS=false
fi

# Test helper
run_test() {
    local test_name="$1"
    local todo_content="$2"
    local expected_exit="$3"  # 0 for success, 1 for warning, 2 for hard failure

    TESTS_RUN=$((TESTS_RUN + 1))

    # Setup: write test todo file
    if [ "$todo_content" = "NONE" ]; then
        rm -f "$TODO_FILE"
    else
        echo "$todo_content" > "$TODO_FILE"
    fi

    # Run hook script and capture exact exit code
    set +e
    "$HOOK_SCRIPT" >/dev/null 2>&1
    actual_exit=$?
    set -e

    # Check result
    if [ $actual_exit -eq $expected_exit ]; then
        echo -e "${GREEN}✓${NC} $test_name"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        echo -e "${RED}✗${NC} $test_name (expected exit $expected_exit, got $actual_exit)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi

    # Cleanup
    rm -f "$TODO_FILE"
}

echo "Testing verify_todo.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Test cases that should succeed (exit 0)
echo "Valid TodoWrite states (exit 0 - success):"

run_test "Has pending todos" '[{"content":"Task 1","status":"pending","activeForm":"Working on task 1"}]' 0

run_test "Has in_progress todos" '[{"content":"Task 1","status":"in_progress","activeForm":"Working on task 1"}]' 0

run_test "Mix of pending and completed" '[{"content":"Task 1","status":"completed","activeForm":"Working on task 1"},{"content":"Task 2","status":"pending","activeForm":"Working on task 2"}]' 0

run_test "Mix of in_progress and completed" '[{"content":"Task 1","status":"completed","activeForm":"Working on task 1"},{"content":"Task 2","status":"in_progress","activeForm":"Working on task 2"}]' 0

echo ""

# Test cases that should warn (exit 1)
echo "Warning cases (exit 1 - warning only):"

run_test "Missing todo file (warning only)" "NONE" 1

echo ""

# Test cases that should fail hard (exit 2)
echo "Invalid TodoWrite states (exit 2 - hard failure):"

run_test "All todos completed" '[{"content":"Task 1","status":"completed","activeForm":"Working on task 1"}]' 2

run_test "Multiple todos all completed" '[{"content":"Task 1","status":"completed","activeForm":"Working on task 1"},{"content":"Task 2","status":"completed","activeForm":"Working on task 2"}]' 2

run_test "Empty array" '[]' 2

run_test "Invalid JSON" 'not-valid-json' 2

echo ""

# Restore backup if it existed
if [ "$BACKUP_EXISTS" = true ]; then
    mv "$TODO_FILE.backup" "$TODO_FILE"
fi

# Summary
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Tests run: $TESTS_RUN"
echo -e "Passed: ${GREEN}$TESTS_PASSED${NC}"
echo -e "Failed: ${RED}$TESTS_FAILED${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}Some tests failed!${NC}"
    exit 1
fi
