#!/bin/bash
# Pre-commit hook to run CI checks locally before allowing commits.
# This eliminates the gap between local and CI testing.
#
# To install this hook, run: make install-hooks
# To bypass the hook temporarily, use: git commit --no-verify

set -euo pipefail  # Exit on error, undefined vars, and pipe failures

echo "🔍 Running pre-commit checks (mirroring CI)..."
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored status
print_status() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}✓${NC} $2"
    else
        echo -e "${RED}✗${NC} $2"
    fi
}

# Track overall status
FAILED=0

# Step 1: Run mypy type checking (matches CI exactly)
echo "1️⃣  Running mypy type checking..."
if poetry run mypy libs/ apps/ strategies/ --strict 2>&1 | tee /tmp/mypy-output.txt; then
    print_status 0 "mypy type checking passed"
else
    print_status 1 "mypy type checking failed"
    echo ""
    echo -e "${YELLOW}Fix mypy errors before committing. Run: make lint${NC}"
    FAILED=1
fi
echo ""

# Step 2: Run ruff linter (matches CI exactly)
echo "2️⃣  Running ruff linter..."
if poetry run ruff check libs/ apps/ strategies/ 2>&1 | tee /tmp/ruff-output.txt; then
    print_status 0 "ruff linting passed"
else
    print_status 1 "ruff linting failed"
    echo ""
    echo -e "${YELLOW}Fix ruff errors before committing. Many can be auto-fixed with: make fmt${NC}"
    FAILED=1
fi
echo ""

# Step 3: Run fast unit tests (skip integration and e2e tests like CI does)
echo "3️⃣  Running unit tests (integration and e2e tests skipped)..."
if PYTHONPATH=. poetry run pytest -m "not integration and not e2e" -q 2>&1 | tee /tmp/pytest-output.txt; then
    print_status 0 "unit tests passed"
else
    print_status 1 "unit tests failed"
    echo ""
    echo -e "${YELLOW}Fix failing tests before committing. Run: make test${NC}"
    FAILED=1
fi
echo ""

# Final result
if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}✓ All pre-commit checks passed!${NC}"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "💡 Tip: Your commit will now proceed. CI should pass without issues."
    exit 0
else
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${RED}✗ Pre-commit checks failed!${NC}"
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "❌ Commit blocked. Please fix the errors above and try again."
    echo ""
    echo "Quick fixes:"
    echo "  • Format code:     make fmt"
    echo "  • Run all checks:  make ci-local"
    echo "  • Skip hook:       git commit --no-verify (NOT RECOMMENDED)"
    echo ""
    exit 1
fi
