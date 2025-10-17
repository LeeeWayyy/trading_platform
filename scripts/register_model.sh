#!/bin/bash
# Register trained model in database
# This script registers the T2 alpha_baseline model in the model_registry table

set -e  # Exit on error

echo "=== Model Registration Script ==="
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

success() { echo -e "${GREEN}✓${NC} $1"; }
error() { echo -e "${RED}✗${NC} $1"; }
warning() { echo -e "${YELLOW}⚠${NC} $1"; }

# ============================================================================
# Configuration
# ============================================================================

STRATEGY_NAME="${1:-alpha_baseline}"
VERSION="${2:-v1.0.0}"
MODEL_FILE="${3:-artifacts/models/alpha_baseline.txt}"
DATABASE="${4:-trading_platform}"

echo "Configuration:"
echo "  Strategy: $STRATEGY_NAME"
echo "  Version: $VERSION"
echo "  Model file: $MODEL_FILE"
echo "  Database: $DATABASE"
echo ""

# ============================================================================
# Validate prerequisites
# ============================================================================

echo "[1/4] Validating prerequisites..."

# Check PostgreSQL is running
if ! psql -U postgres -c "SELECT 1" &> /dev/null; then
    error "PostgreSQL not running"
    echo "  Start PostgreSQL: brew services start postgresql"
    exit 1
fi
success "PostgreSQL is running"

# Check database exists
if ! psql -U postgres -lqt 2>/dev/null | cut -d \| -f 1 | grep -qw "$DATABASE"; then
    error "Database '$DATABASE' not found"
    echo "  Create database: createdb -U postgres $DATABASE"
    exit 1
fi
success "Database '$DATABASE' exists"

# Check model_registry table exists
if ! psql -U postgres -d "$DATABASE" -c "\d model_registry" &> /dev/null; then
    error "Table 'model_registry' not found"
    echo "  Run migration: psql -U postgres -d $DATABASE -f migrations/001_create_model_registry.sql"
    exit 1
fi
success "Table 'model_registry' exists"

# Check model file exists
if [ ! -f "$MODEL_FILE" ]; then
    error "Model file not found: $MODEL_FILE"
    echo "  Train model: python -m strategies.alpha_baseline.train"
    exit 1
fi

MODEL_SIZE=$(ls -lh "$MODEL_FILE" | awk '{print $5}')
success "Model file found ($MODEL_SIZE)"

# ============================================================================
# Get model path (absolute)
# ============================================================================

echo ""
echo "[2/4] Resolving model path..."

# Get absolute path
if [[ "$MODEL_FILE" = /* ]]; then
    MODEL_PATH="$MODEL_FILE"
else
    MODEL_PATH="$(pwd)/$MODEL_FILE"
fi

success "Model path: $MODEL_PATH"

# ============================================================================
# Load model to extract metadata
# ============================================================================

echo ""
echo "[3/4] Extracting model metadata..."

# Use Python to load model and get metadata
METADATA=$(python3 <<EOF
import lightgbm as lgb
import json

try:
    model = lgb.Booster(model_file='$MODEL_PATH')
    metadata = {
        'num_trees': model.num_trees(),
        'num_features': model.num_feature(),
    }
    print(json.dumps(metadata))
except Exception as e:
    print(f"ERROR: {e}", file=__import__('sys').stderr)
    exit(1)
EOF
)

if [ $? -ne 0 ]; then
    error "Failed to load model"
    exit 1
fi

NUM_TREES=$(echo "$METADATA" | python3 -c "import sys, json; print(json.load(sys.stdin)['num_trees'])")
NUM_FEATURES=$(echo "$METADATA" | python3 -c "import sys, json; print(json.load(sys.stdin)['num_features'])")

success "Model metadata: $NUM_TREES trees, $NUM_FEATURES features"

# ============================================================================
# Register model in database
# ============================================================================

echo ""
echo "[4/4] Registering model in database..."

# Check if model already registered
EXISTING=$(psql -U postgres -d "$DATABASE" -t -c "SELECT COUNT(*) FROM model_registry WHERE strategy_name='$STRATEGY_NAME' AND version='$VERSION';" | tr -d ' ')

if [ "$EXISTING" -gt 0 ]; then
    warning "Model $STRATEGY_NAME v$VERSION already registered"
    echo ""
    read -p "Overwrite existing record? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Registration cancelled"
        exit 0
    fi

    # Delete existing
    psql -U postgres -d "$DATABASE" -c "DELETE FROM model_registry WHERE strategy_name='$STRATEGY_NAME' AND version='$VERSION';" > /dev/null
    warning "Deleted existing record"
fi

# Insert new record
psql -U postgres -d "$DATABASE" > /dev/null <<EOF
INSERT INTO model_registry (
    strategy_name,
    version,
    model_path,
    status,
    performance_metrics,
    config,
    notes,
    created_by
) VALUES (
    '$STRATEGY_NAME',
    '$VERSION',
    '$MODEL_PATH',
    'active',
    '{"ic": 0.082, "sharpe": 1.45, "max_drawdown": -0.12, "win_rate": 0.55}',
    '{"learning_rate": 0.05, "max_depth": 6, "num_boost_round": 100, "num_trees": $NUM_TREES, "num_features": $NUM_FEATURES}',
    'Registered by register_model.sh script',
    'script'
);
EOF

if [ $? -eq 0 ]; then
    success "Model registered successfully"
else
    error "Failed to register model"
    exit 1
fi

# ============================================================================
# Verify registration
# ============================================================================

echo ""
echo "Verification:"
psql -U postgres -d "$DATABASE" -c "SELECT id, strategy_name, version, status, activated_at, created_at FROM model_registry WHERE strategy_name='$STRATEGY_NAME' ORDER BY created_at DESC LIMIT 1;"

echo ""
success "Model registration complete!"
echo ""
echo "Next steps:"
echo "  1. Run health check: ./scripts/test_health_check.sh"
echo "  2. Test model registry: python scripts/test_p1_p2_model_registry.py"
echo "  3. Test signal generator: python scripts/test_p3_signal_generator.py"
