#!/bin/bash
# Unit tests for verify_branch_name.sh
# Tests branch naming convention validation

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK_SCRIPT="$REPO_ROOT/scripts/hooks/verify_branch_name.sh"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

# Test counter
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

# Test helper
run_test() {
    local test_name="$1"
    local branch_name="$2"
    local expected_exit="$3"  # 0 for pass, 1 for fail

    TESTS_RUN=$((TESTS_RUN + 1))

    # Create temporary git branch for testing
    git checkout -b "$branch_name" 2>/dev/null || git checkout "$branch_name" 2>/dev/null

    # Run hook script
    if "$HOOK_SCRIPT" >/dev/null 2>&1; then
        actual_exit=0
    else
        actual_exit=1
    fi

    # Check result
    if [ $actual_exit -eq $expected_exit ]; then
        echo -e "${GREEN}✓${NC} $test_name"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        echo -e "${RED}✗${NC} $test_name (expected exit $expected_exit, got $actual_exit)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi

    # Cleanup: go back to original branch
    git checkout - >/dev/null 2>&1
    git branch -D "$branch_name" >/dev/null 2>&1 || true
}

echo "Testing verify_branch_name.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Save current branch
ORIGINAL_BRANCH=$(git branch --show-current)

# Test valid branch names
echo "Valid branch names (should pass):"
run_test "Feature with basic description" "feature/P0T1-initial-setup" 0
run_test "Feature with longer task ID" "feature/P1T11-workflow-optimization" 0
run_test "Feature with subfeature ID" "feature/P1T11-F1-tool-restriction" 0
run_test "Bugfix branch" "bugfix/P0T2-fix-circuit-breaker" 0
run_test "Hotfix branch" "hotfix/P2T5-urgent-fix" 0
run_test "Multi-digit phase" "feature/P10T1-test" 0
run_test "Multi-digit task" "feature/P1T100-test" 0
run_test "Multi-digit subfeature" "feature/P1T11-F99-test" 0
run_test "Numbers in description" "feature/P1T1-add-v2-api" 0
echo ""

# Test invalid branch names
echo "Invalid branch names (should fail):"
run_test "Missing phase/task" "feature/test-branch" 1
run_test "Wrong case in type" "Feature/P1T1-test" 1
run_test "Uppercase in description" "feature/P1T1-Test-Branch" 1
run_test "Spaces in description" "feature/P1T1-test branch" 1
run_test "Missing description" "feature/P1T1" 1
run_test "Wrong separator" "feature/P1T1_test" 1
run_test "Missing T prefix" "feature/P11-test" 1
run_test "Missing P prefix" "feature/1T1-test" 1
run_test "Invalid type" "patch/P1T1-test" 1
echo ""

# Restore original branch
git checkout "$ORIGINAL_BRANCH" >/dev/null 2>&1

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
