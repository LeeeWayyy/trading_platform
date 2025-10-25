#!/bin/bash
# Master test runner for all hook tests
# Runs unit tests, integration tests, and validates performance

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

# Track overall status
TOTAL_FAILED=0

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Running All Hook Tests${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Run unit test: branch name validation
echo "═══════════════════════════════════════"
echo "Unit Test: Branch Name Validation"
echo "═══════════════════════════════════════"
if "$SCRIPT_DIR/test_verify_branch_name.sh"; then
    echo -e "${GREEN}✓ Branch name validation tests passed${NC}"
else
    echo -e "${RED}✗ Branch name validation tests failed${NC}"
    TOTAL_FAILED=$((TOTAL_FAILED + 1))
fi
echo ""

# Run unit test: TodoWrite state verification
echo "═══════════════════════════════════════"
echo "Unit Test: TodoWrite State Verification"
echo "═══════════════════════════════════════"
if "$SCRIPT_DIR/test_verify_todo.sh"; then
    echo -e "${GREEN}✓ TodoWrite state tests passed${NC}"
else
    echo -e "${RED}✗ TodoWrite state tests failed${NC}"
    TOTAL_FAILED=$((TOTAL_FAILED + 1))
fi
echo ""

# Run integration test: full commit workflow
echo "═══════════════════════════════════════"
echo "Integration Test: Full Commit Workflow"
echo "═══════════════════════════════════════"
if "$SCRIPT_DIR/test_integration_commit_workflow.sh"; then
    echo -e "${GREEN}✓ Integration tests passed${NC}"
else
    echo -e "${RED}✗ Integration tests failed${NC}"
    TOTAL_FAILED=$((TOTAL_FAILED + 1))
fi
echo ""

# Final summary
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
if [ $TOTAL_FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ ALL HOOK TESTS PASSED${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    exit 0
else
    echo -e "${RED}✗ $TOTAL_FAILED TEST SUITE(S) FAILED${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    exit 1
fi
