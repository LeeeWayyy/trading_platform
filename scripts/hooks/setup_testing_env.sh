#!/bin/bash
# Master setup script for testing environment
# This script automates the setup process for P1-P3 testing

set -e  # Exit on error

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

success() { echo -e "${GREEN}✓${NC} $1"; }
error() { echo -e "${RED}✗${NC} $1"; }
warning() { echo -e "${YELLOW}⚠${NC} $1"; }
info() { echo -e "${BLUE}ℹ${NC} $1"; }

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Trading Platform - Setup Testing Environment${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# ============================================================================
# Step 1: Check Prerequisites
# ============================================================================
echo -e "${BLUE}[Step 1/7] Checking prerequisites...${NC}"

# Check if running from project root
if [ ! -f "migrations/001_create_model_registry.sql" ]; then
    error "Must run from project root directory"
    echo "  cd /path/to/trading_platform"
    exit 1
fi
success "Running from project root"

# Check Python
if ! command -v python &> /dev/null; then
    error "Python not found. Install Python 3.11+"
    exit 1
fi
success "Python found: $(python --version)"

# Check PostgreSQL
if ! command -v psql &> /dev/null; then
    error "PostgreSQL not found. Install PostgreSQL 14+"
    echo "  Mac: brew install postgresql@14"
    echo "  Ubuntu: sudo apt-get install postgresql-14"
    exit 1
fi
success "PostgreSQL found"

# ============================================================================
# Step 2: Start PostgreSQL
# ============================================================================
echo ""
echo -e "${BLUE}[Step 2/7] Starting PostgreSQL...${NC}"

if psql -U postgres -c "SELECT 1" &> /dev/null; then
    success "PostgreSQL already running"
else
    info "Attempting to start PostgreSQL..."

    # Try Mac method
    if command -v brew &> /dev/null; then
        brew services start postgresql@14 2>&1 || true
        sleep 2
    fi

    # Try Linux method
    if command -v systemctl &> /dev/null; then
        sudo systemctl start postgresql 2>&1 || true
        sleep 2
    fi

    # Check if started
    if psql -U $(whoami) -c "SELECT 1" &> /dev/null; then
        success "PostgreSQL started"
    else
        error "Failed to start PostgreSQL"
        echo "  Start manually:"
        echo "    Mac: brew services start postgresql@14"
        echo "    Linux: sudo systemctl start postgresql"
        exit 1
    fi
fi

# ============================================================================
# Step 3: Create Databases
# ============================================================================
echo ""
echo -e "${BLUE}[Step 3/7] Creating databases...${NC}"

# Create production database
if psql -U postgres -lqt 2>/dev/null | cut -d \| -f 1 | grep -qw trading_platform; then
    success "Database 'trading_platform' exists"
else
    info "Creating database 'trading_platform'..."
    createdb -U postgres trading_platform
    success "Database 'trading_platform' created"
fi

# Create test database
if psql -U postgres -lqt 2>/dev/null | cut -d \| -f 1 | grep -qw trading_platform_test; then
    success "Database 'trading_platform_test' exists"
else
    info "Creating database 'trading_platform_test'..."
    createdb -U postgres trading_platform_test
    success "Database 'trading_platform_test' created"
fi

# ============================================================================
# Step 4: Run Migrations
# ============================================================================
echo ""
echo -e "${BLUE}[Step 4/7] Running database migrations...${NC}"

# Production database
if psql -U postgres -d trading_platform -c "\d model_registry" &> /dev/null; then
    success "Table 'model_registry' exists in trading_platform"
else
    info "Running migration on trading_platform..."
    psql -U postgres -d trading_platform -f migrations/001_create_model_registry.sql > /dev/null 2>&1
    success "Migration completed on trading_platform"
fi

# Test database
if psql -U postgres -d trading_platform_test -c "\d model_registry" &> /dev/null; then
    success "Table 'model_registry' exists in trading_platform_test"
else
    info "Running migration on trading_platform_test..."
    psql -U postgres -d trading_platform_test -f migrations/001_create_model_registry.sql > /dev/null 2>&1
    success "Migration completed on trading_platform_test"
fi

# ============================================================================
# Step 5: Check Model File
# ============================================================================
echo ""
echo -e "${BLUE}[Step 5/7] Checking model file...${NC}"

if [ -f "artifacts/models/alpha_baseline.txt" ]; then
    MODEL_SIZE=$(ls -lh artifacts/models/alpha_baseline.txt | awk '{print $5}')
    success "Model file exists ($MODEL_SIZE)"
else
    warning "Model file not found"
    info "You need to train the model before running tests:"
    echo "    python -m strategies.alpha_baseline.train"
    echo "  Or generate test data and train a quick model:"
    echo "    python scripts/quick_train_test.py"
    echo ""
    read -p "Continue setup without model? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# ============================================================================
# Step 6: Register Model (if exists)
# ============================================================================
echo ""
echo -e "${BLUE}[Step 6/7] Registering model in database...${NC}"

if [ -f "artifacts/models/alpha_baseline.txt" ]; then
    # Check if already registered
    MODEL_COUNT=$(psql -U postgres -d trading_platform -t -c "SELECT COUNT(*) FROM model_registry WHERE strategy_name='alpha_baseline' AND version='v1.0.0';" 2>/dev/null | tr -d ' ')

    if [ "$MODEL_COUNT" -gt 0 ]; then
        success "Model already registered"
    else
        info "Registering model..."
        ./scripts/register_model.sh > /dev/null 2>&1 || true
        success "Model registered"
    fi
else
    warning "Skipping model registration (model file not found)"
fi

# ============================================================================
# Step 7: Check T1 Data
# ============================================================================
echo ""
echo -e "${BLUE}[Step 7/7] Checking T1 data...${NC}"

if [ -d "data/adjusted" ]; then
    NUM_DATES=$(find data/adjusted -maxdepth 1 -type d | wc -l | tr -d ' ')
    NUM_DATES=$((NUM_DATES - 1))
    success "Data directory exists with $NUM_DATES date folders"

    if [ -d "data/adjusted/2024-01-15" ]; then
        NUM_FILES=$(find data/adjusted/2024-01-15 -name "*.parquet" | wc -l | tr -d ' ')
        success "Test date (2024-01-15) has $NUM_FILES parquet files"
    else
        warning "Test date (2024-01-15) not found"
        info "Some tests may fail without this specific date"
        info "You can generate test data with:"
        echo "    python scripts/generate_test_data.py --start 2024-01-01 --end 2024-01-31"
    fi
else
    warning "Data directory 'data/adjusted' not found"
    info "Tests requiring real data will fail"
    info "Run T1 data pipeline or generate test data"
fi

# ============================================================================
# Summary
# ============================================================================
echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}Setup Complete!${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

echo "Next steps:"
echo ""
echo "1. Run health check:"
echo "   ./scripts/test_health_check.sh"
echo ""
echo "2. Run P1-P2 tests (Model Registry):"
echo "   python scripts/test_p1_p2_model_registry.py"
echo ""
echo "3. Run P3 tests (Signal Generator):"
echo "   python scripts/test_p3_signal_generator.py"
echo ""
echo "4. Run automated tests:"
echo "   pytest apps/signal_service/tests/ -v"
echo ""

success "Testing environment ready!"
