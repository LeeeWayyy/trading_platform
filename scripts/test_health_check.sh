#!/bin/bash
# Health check script for trading platform testing environment
# Verifies all prerequisites are met before running tests

set -e  # Exit on error

echo "=== Trading Platform Health Check ==="
echo ""

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

success() {
    echo -e "${GREEN}✓${NC} $1"
}

error() {
    echo -e "${RED}✗${NC} $1"
}

warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

# Track failures
FAILURES=0

# ============================================================================
# 1. Check Python version
# ============================================================================
echo "[1/8] Checking Python version..."
if command -v python &> /dev/null; then
    PYTHON_VERSION=$(python --version 2>&1 | awk '{print $2}')
    PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
    PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)

    if [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -ge 11 ]; then
        success "Python $PYTHON_VERSION"
    else
        error "Python $PYTHON_VERSION (need 3.11+)"
        FAILURES=$((FAILURES + 1))
    fi
else
    error "Python not found"
    FAILURES=$((FAILURES + 1))
fi

# ============================================================================
# 2. Check virtual environment
# ============================================================================
echo "[2/8] Checking virtual environment..."
if [ -n "$VIRTUAL_ENV" ]; then
    success "Virtual environment active: $VIRTUAL_ENV"
else
    warning "Virtual environment not activated (run: source .venv/bin/activate)"
fi

# ============================================================================
# 3. Check PostgreSQL
# ============================================================================
echo "[3/8] Checking PostgreSQL..."
if command -v psql &> /dev/null; then
    if psql -U postgres -c "SELECT 1" &> /dev/null; then
        success "PostgreSQL is running"
    else
        error "PostgreSQL not responding (start: brew services start postgresql)"
        FAILURES=$((FAILURES + 1))
    fi
else
    error "PostgreSQL not installed"
    FAILURES=$((FAILURES + 1))
fi

# ============================================================================
# 4. Check databases exist
# ============================================================================
echo "[4/8] Checking databases..."
if psql -U postgres -lqt 2>/dev/null | cut -d \| -f 1 | grep -qw trading_platform; then
    success "Database: trading_platform"
else
    error "Database 'trading_platform' not found (create: createdb -U postgres trading_platform)"
    FAILURES=$((FAILURES + 1))
fi

if psql -U postgres -lqt 2>/dev/null | cut -d \| -f 1 | grep -qw trading_platform_test; then
    success "Database: trading_platform_test"
else
    warning "Database 'trading_platform_test' not found (create: createdb -U postgres trading_platform_test)"
fi

# ============================================================================
# 5. Check model_registry table
# ============================================================================
echo "[5/8] Checking model_registry table..."
if psql -U postgres -d trading_platform -c "\d model_registry" &> /dev/null; then
    MODEL_COUNT=$(psql -U postgres -d trading_platform -t -c "SELECT COUNT(*) FROM model_registry;" 2>/dev/null | tr -d ' ')
    success "Table exists with $MODEL_COUNT models"

    if [ "$MODEL_COUNT" -eq 0 ]; then
        warning "No models registered (run: ./scripts/register_model.sh)"
    fi
else
    error "Table 'model_registry' not found (run: psql -U postgres -d trading_platform -f migrations/001_create_model_registry.sql)"
    FAILURES=$((FAILURES + 1))
fi

# ============================================================================
# 6. Check T1 data directory
# ============================================================================
echo "[6/8] Checking T1 data..."
if [ -d "data/adjusted" ]; then
    NUM_DATES=$(find data/adjusted -maxdepth 1 -type d | wc -l | tr -d ' ')
    NUM_DATES=$((NUM_DATES - 1))  # Subtract parent directory
    success "Data directory exists with $NUM_DATES date folders"

    # Check for specific test date
    if [ -d "data/adjusted/2024-01-15" ]; then
        NUM_FILES=$(find data/adjusted/2024-01-15 -name "*.parquet" | wc -l | tr -d ' ')
        success "Test date (2024-01-15) has $NUM_FILES parquet files"
    else
        warning "Test date (2024-01-15) not found (tests may fail)"
    fi
else
    error "Data directory 'data/adjusted' not found"
    FAILURES=$((FAILURES + 1))
fi

# ============================================================================
# 7. Check model file
# ============================================================================
echo "[7/8] Checking model file..."
if [ -f "artifacts/models/alpha_baseline.txt" ]; then
    MODEL_SIZE=$(ls -lh artifacts/models/alpha_baseline.txt | awk '{print $5}')
    success "Model file exists ($MODEL_SIZE)"
else
    error "Model file 'artifacts/models/alpha_baseline.txt' not found"
    FAILURES=$((FAILURES + 1))
fi

# ============================================================================
# 8. Check Python packages
# ============================================================================
echo "[8/8] Checking Python packages..."
MISSING_PACKAGES=()

for package in pytest psycopg2 lightgbm pandas numpy; do
    if python -c "import $package" 2>/dev/null; then
        :  # Package exists, do nothing
    else
        MISSING_PACKAGES+=($package)
    fi
done

if [ ${#MISSING_PACKAGES[@]} -eq 0 ]; then
    success "All required packages installed"
else
    error "Missing packages: ${MISSING_PACKAGES[*]} (run: pip install -r requirements.txt)"
    FAILURES=$((FAILURES + 1))
fi

# ============================================================================
# Summary
# ============================================================================
echo ""
echo "=========================================="
if [ $FAILURES -eq 0 ]; then
    success "All checks passed! Ready to run tests."
    echo ""
    echo "Run tests:"
    echo "  pytest apps/signal_service/tests/ -v"
    exit 0
else
    error "$FAILURES check(s) failed. Fix issues above before running tests."
    echo ""
    echo "See docs/TESTING_SETUP.md for detailed setup instructions."
    exit 1
fi
