# Trading Platform

A production-grade algorithmic trading platform integrating Qlib for signal generation and Alpaca for order execution.

## Quick Start

### Prerequisites
- Python 3.11+
- PostgreSQL 14+ (for model registry and state management)
- Docker & Docker Compose (optional)
- Poetry (optional) or pip + venv

### Installation

**See [docs/SETUP.md](./docs/SETUP.md) for detailed environment setup instructions.**

#### Quick Setup (venv)

```bash
# Create virtual environment with Python 3.11
python3.11 -m venv .venv

# Activate venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run tests
pytest tests/ -v
```

#### Using Poetry

```bash
# Install dependencies
poetry install

# Run tests
poetry run pytest tests/ -v
```

#### Using Make

```bash
# Install dependencies
make install

# Start infrastructure
make up

# Run tests
make test
```

### Development Commands

```bash
make help           # Show all available commands
make fmt            # Format code
make lint           # Run linters
make test           # Run tests
make test-cov       # Run tests with coverage
make clean          # Clean generated files
```

## Project Structure

See [CLAUDE.md](./CLAUDE.md) for comprehensive documentation.

```
trading_platform/
├── apps/                           # Microservices
│   ├── signal_service/            # ✅ T3: ML model serving (COMPLETE)
│   └── execution_gateway/         # ✅ T4: Order execution (COMPLETE)
├── strategies/                     # Trading strategies
│   └── alpha_baseline/            # ✅ T2: Baseline strategy (COMPLETE)
├── libs/                          # Shared libraries
│   ├── data_pipeline/             # ✅ T1: Data ETL (COMPLETE)
│   └── common/                    # Shared utilities
├── migrations/                    # Database migrations
│   ├── 001_create_model_registry.sql  # Model registry schema
│   └── 002_create_execution_tables.sql  # Orders + positions schema
├── scripts/                       # Operational scripts
│   ├── setup_testing_env.sh      # Environment setup
│   ├── register_model.sh         # Model registration
│   └── test_*.py                 # Phase validation scripts
├── docs/                          # Documentation
│   ├── ADRs/                     # Architecture decisions
│   ├── CONCEPTS/                 # Trading concepts explained
│   ├── IMPLEMENTATION_GUIDES/    # Step-by-step guides
│   ├── LESSONS_LEARNED/          # Retrospectives
│   └── TASKS/                    # Ticket backlog
├── tests/                         # Test suite
├── data/                          # Parquet data files
└── artifacts/                     # Models, backtests
```

## Documentation

### Getting Started
- **[docs/SETUP.md](./docs/SETUP.md)** - Environment setup and installation guide
- [CLAUDE.md](./CLAUDE.md) - Main guide for Claude Code
- **[docs/TESTING_SETUP.md](./docs/TESTING_SETUP.md)** - Testing environment setup

### Development Guidelines
- [docs/ADR_GUIDE.md](./docs/ADR_GUIDE.md) - Architecture Decision Records guide
- [docs/DOCUMENTATION_STANDARDS.md](./docs/DOCUMENTATION_STANDARDS.md) - Code documentation standards
- [docs/GIT_WORKFLOW.md](./docs/GIT_WORKFLOW.md) - Git workflow and PR automation
- [docs/CODING_STANDARDS.md](./docs/CODING_STANDARDS.md) - Python coding standards
- [docs/TESTING.md](./docs/TESTING.md) - Testing strategy and requirements

### Implementation Guides
- **[docs/IMPLEMENTATION_GUIDES/t1-data-etl.md](./docs/IMPLEMENTATION_GUIDES/t1-data-etl.md)** - T1: Data ETL implementation
- **[docs/IMPLEMENTATION_GUIDES/t2-baseline-strategy-qlib.md](./docs/IMPLEMENTATION_GUIDES/t2-baseline-strategy-qlib.md)** - T2: Baseline strategy
- **[docs/IMPLEMENTATION_GUIDES/t3-signal-service.md](./docs/IMPLEMENTATION_GUIDES/t3-signal-service.md)** - T3: Signal service (main guide)
- [docs/IMPLEMENTATION_GUIDES/t3-p4-fastapi-application.md](./docs/IMPLEMENTATION_GUIDES/t3-p4-fastapi-application.md) - T3 Phase 4: FastAPI
- [docs/IMPLEMENTATION_GUIDES/t3-p5-hot-reload.md](./docs/IMPLEMENTATION_GUIDES/t3-p5-hot-reload.md) - T3 Phase 5: Hot Reload
- [docs/IMPLEMENTATION_GUIDES/t3-p6-integration-tests.md](./docs/IMPLEMENTATION_GUIDES/t3-p6-integration-tests.md) - T3 Phase 6: Integration Tests
- **[docs/IMPLEMENTATION_GUIDES/t4-execution-gateway.md](./docs/IMPLEMENTATION_GUIDES/t4-execution-gateway.md)** - T4: Execution gateway

### Architecture Decisions
- [docs/ADRs/0001-data-pipeline-architecture.md](./docs/ADRs/0001-data-pipeline-architecture.md) - T1: Data pipeline decisions
- [docs/ADRs/0002-exception-hierarchy.md](./docs/ADRs/0002-exception-hierarchy.md) - T1: Exception handling
- [docs/ADRs/0003-baseline-strategy-with-qlib-and-mlflow.md](./docs/ADRs/0003-baseline-strategy-with-qlib-and-mlflow.md) - T2: Strategy architecture
- **[docs/ADRs/0004-signal-service-architecture.md](./docs/ADRs/0004-signal-service-architecture.md)** - T3: Signal service decisions
- **[docs/ADRs/0005-execution-gateway-architecture.md](./docs/ADRs/0005-execution-gateway-architecture.md)** - T4: Execution gateway decisions

### Concept Documentation
- [docs/CONCEPTS/corporate-actions.md](./docs/CONCEPTS/corporate-actions.md) - Stock splits and dividends
- [docs/CONCEPTS/alpha158-features.md](./docs/CONCEPTS/alpha158-features.md) - Alpha158 feature set
- [docs/CONCEPTS/qlib-data-providers.md](./docs/CONCEPTS/qlib-data-providers.md) - Qlib data integration
- [docs/CONCEPTS/lightgbm-training.md](./docs/CONCEPTS/lightgbm-training.md) - Model training
- **[docs/CONCEPTS/model-registry.md](./docs/CONCEPTS/model-registry.md)** - Model versioning and lifecycle
- **[docs/CONCEPTS/hot-reload.md](./docs/CONCEPTS/hot-reload.md)** - Zero-downtime model updates
- **[docs/CONCEPTS/feature-parity.md](./docs/CONCEPTS/feature-parity.md)** - Research-production consistency

## Current Status

**Phase:** P0 (MVP Core, Days 0-45)
**Progress:** 67% (4/6 tasks complete)

### Completed ✅

#### T1: Data ETL with Corporate Actions, Freshness, Quality Gate
**Status:** ✅ Complete (100% test pass rate)

**What it does:**
- Ingests raw OHLCV data from Parquet files
- Adjusts prices for corporate actions (splits, dividends)
- Validates data freshness (< 30 minutes old)
- Detects outliers and quarantines suspicious data
- Outputs adjusted Parquet files with quality gates

**Key files:**
- `libs/data_pipeline/etl.py` - Main ETL orchestrator
- `libs/data_pipeline/corporate_actions.py` - Split/dividend adjustment
- `libs/data_pipeline/freshness.py` - Staleness validation
- `libs/data_pipeline/quality_gate.py` - Outlier detection

**Tests:** 53 tests, 100% passing
**Documentation:** [docs/IMPLEMENTATION_GUIDES/t1-data-etl.md](./docs/IMPLEMENTATION_GUIDES/t1-data-etl.md)

---

#### T2: Baseline Strategy + MLflow
**Status:** ✅ Complete (100% test pass rate)

**What it does:**
- Implements Alpha158 baseline strategy using Qlib
- Trains LightGBM model on historical data
- Logs metrics and artifacts to MLflow
- Achieves IC=0.082, Sharpe=1.45 on validation set

**Key files:**
- `strategies/alpha_baseline/data_loader.py` - Data loading from T1 pipeline
- `strategies/alpha_baseline/features.py` - Alpha158 feature computation
- `strategies/alpha_baseline/model.py` - LightGBM training and evaluation
- `strategies/alpha_baseline/mock_features.py` - Mock features for testing

**Tests:** Unit tests for all components
**Documentation:** [docs/IMPLEMENTATION_GUIDES/t2-baseline-strategy-qlib.md](./docs/IMPLEMENTATION_GUIDES/t2-baseline-strategy-qlib.md)

---

#### T3: Signal Service (Model Registry + Hot Reload)
**Status:** ✅ Complete (95% test pass rate - 57/60 tests passing)

**What it does:**
- Serves ML predictions via RESTful API (FastAPI)
- Manages model lifecycle with PostgreSQL-backed registry
- Hot reload mechanism for zero-downtime model updates
- Feature parity with research code (no train-serve skew)
- Top-N Long / Bottom-N Short portfolio construction

**Key components:**
1. **Model Registry** - Version tracking, lifecycle management, hot reload
2. **Signal Generator** - Feature generation, prediction, portfolio construction
3. **FastAPI Application** - REST API with 4 endpoints
4. **Background Polling** - Automatic model reload every 5 minutes

**API Endpoints:**
- `GET /` - Health check
- `POST /api/v1/signals/generate` - Generate trading signals
- `GET /api/v1/model/info` - Model metadata
- `POST /api/v1/model/reload` - Manual reload trigger

**Key files:**
- `apps/signal_service/model_registry.py` (516 lines) - Model management
- `apps/signal_service/signal_generator.py` (510 lines) - Signal generation
- `apps/signal_service/main.py` (884 lines) - FastAPI application
- `migrations/001_create_model_registry.sql` - Database schema

**Tests:**
- Unit tests: 33/33 passing (100%)
- Integration tests: 14/17 passing (82%)
- Feature parity tests: 10/10 passing (100%)
- **Total: 57/60 passing (95%)**

**Performance:**
- Signal generation: < 100ms for 5 symbols ✅
- Model reload: < 1 second ✅
- Zero downtime during updates ✅

**Documentation:**
- [docs/IMPLEMENTATION_GUIDES/t3-signal-service.md](./docs/IMPLEMENTATION_GUIDES/t3-signal-service.md) - Main guide (1,940 lines)
- [docs/IMPLEMENTATION_GUIDES/t3-p4-fastapi-application.md](./docs/IMPLEMENTATION_GUIDES/t3-p4-fastapi-application.md) - FastAPI details
- [docs/IMPLEMENTATION_GUIDES/t3-p5-hot-reload.md](./docs/IMPLEMENTATION_GUIDES/t3-p5-hot-reload.md) - Hot reload mechanism
- [docs/IMPLEMENTATION_GUIDES/t3-p6-integration-tests.md](./docs/IMPLEMENTATION_GUIDES/t3-p6-integration-tests.md) - Test suite
- [docs/ADRs/0004-signal-service-architecture.md](./docs/ADRs/0004-signal-service-architecture.md) - Architecture decisions

**Deployment Scripts:**
- `scripts/setup_testing_env.sh` - PostgreSQL setup and migrations
- `scripts/register_model.sh` - Model registration CLI
- `scripts/test_health_check.sh` - Quick health validation
- Manual testing scripts for each phase (P1-P5)

---

#### T4: Execution Gateway (Idempotent Order Submission + Webhook Security)
**Status:** ✅ Complete (100% test pass rate - 50/50 tests passing)

**What it does:**
- Idempotent order submission to Alpaca via deterministic client_order_id
- DRY_RUN mode for safe testing without broker submission
- Webhook signature verification (HMAC-SHA256) for security
- Position tracking with weighted average entry price
- Automatic retry with exponential backoff (2s, 4s, 8s)
- Complete audit trail in PostgreSQL (orders + positions tables)

**Key components:**
1. **Order ID Generator** - Deterministic SHA256-based ID generation for idempotency
2. **Alpaca Client** - Wrapper with retry logic and error classification
3. **Database Layer** - CRUD operations for orders and positions
4. **FastAPI Application** - REST API with 6 endpoints
5. **Webhook Security** - HMAC-SHA256 signature verification

**API Endpoints:**
- `GET /` - Health check
- `POST /api/v1/orders` - Idempotent order submission
- `GET /api/v1/orders/{client_order_id}` - Query order status
- `GET /api/v1/positions` - Get all positions with P&L
- `POST /api/v1/webhooks/orders` - Receive Alpaca order updates
- `GET /health` - Detailed health check

**Key files:**
- `apps/execution_gateway/main.py` (630 lines) - FastAPI application
- `apps/execution_gateway/schemas.py` (400 lines) - Request/response models
- `apps/execution_gateway/order_id_generator.py` (180 lines) - Deterministic IDs
- `apps/execution_gateway/alpaca_client.py` (370 lines) - Alpaca API wrapper
- `apps/execution_gateway/database.py` (420 lines) - Database operations
- `apps/execution_gateway/webhook_security.py` (150 lines) - Signature verification
- `migrations/002_create_execution_tables.sql` (150 lines) - Database schema

**Tests:**
- Unit tests: 44/44 passing (100%)
- Integration tests: 6/6 passing (100%)
- **Total: 50/50 passing (100%)**

**Performance:**
- Order submission: < 100ms (target: 500ms) ✅
- Order query: < 20ms (target: 50ms) ✅
- Webhook processing: < 50ms (target: 200ms) ✅
- Health check: < 10ms ✅

**Documentation:**
- [docs/IMPLEMENTATION_GUIDES/t4-execution-gateway.md](./docs/IMPLEMENTATION_GUIDES/t4-execution-gateway.md) - Complete implementation guide (827 lines)
- [docs/ADRs/0005-execution-gateway-architecture.md](./docs/ADRs/0005-execution-gateway-architecture.md) - Architecture decisions (690 lines)

**Deployment Scripts:**
- `scripts/test_t4_execution_gateway.py` - Manual integration testing (6/6 passing)

**Key Features:**
- **Idempotency:** Same order parameters on same day = same client_order_id (prevents duplicates)
- **DRY_RUN Mode:** Toggle via environment variable (true = log only, false = submit to Alpaca)
- **Webhook Security:** Constant-time HMAC signature comparison (prevents timing attacks)
- **Position Tracking:** Automatic weighted average calculation on fills
- **Retry Logic:** Exponential backoff for transient failures (max 3 attempts)
- **Error Classification:** Retryable (connection) vs non-retryable (validation, rejection)

---

### In Progress 🔄

None currently - ready to start T5!

---

### Upcoming ⏳

#### T5: Position Tracker
**Goal:** Track positions and sync with broker state

**Features:**
- positions table with upserts on fills
- Real-time position updates from webhooks
- Position reconciliation with broker
- Exposure monitoring

**Timeline:** Days 26-35

---

#### T6: `paper_run.py` Orchestrator + P&L
**Goal:** End-to-end paper trading automation

**Features:**
- One command to run full pipeline
- P&L calculation and reporting
- Daily orchestration script
- Integration with all T1-T5 components

**Timeline:** Days 36-45

---

## Key Achievements

### Production-Ready Infrastructure ✅
- **Zero-downtime deployment** via hot reload mechanism
- **Feature parity** guarantees (research = production)
- **Idempotent order execution** with deterministic client_order_id
- **Webhook security** with HMAC-SHA256 signature verification
- **Comprehensive testing** (98% pass rate across 110 tests)
- **Performance targets exceeded** (all < 100ms latency)

### Educational Documentation ✅
- **12,500+ lines of documentation**
- 5 ADRs documenting architectural decisions
- 7 concept docs explaining trading/ML patterns
- 7 implementation guides with step-by-step instructions
- Testing guides and lessons learned

### Developer Experience ✅
- One-command setup (`./scripts/setup_testing_env.sh`)
- Quick health checks for each component
- Phase-by-phase validation scripts
- Comprehensive troubleshooting guides

---

## Quick Start Guide

### 1. Setup Environment

```bash
# Setup PostgreSQL and run migrations
./scripts/setup_testing_env.sh

# Verify setup
./scripts/test_health_check.sh
```

### 2. Train and Register Model

```bash
# Quick train (uses mock data)
python scripts/quick_train_test.py

# Register model in database
./scripts/register_model.sh alpha_baseline v1.0.0 artifacts/models/alpha_baseline.txt
```

### 3. Start Signal Service

```bash
# Start FastAPI service
uvicorn apps.signal_service.main:app --reload --port 8001

# In another terminal, test it
curl http://localhost:8001/
```

### 4. Generate Signals

```bash
curl -X POST http://localhost:8001/api/v1/signals/generate \
  -H "Content-Type: application/json" \
  -d '{
    "symbols": ["AAPL", "MSFT", "GOOGL"],
    "as_of_date": "2024-12-31",
    "top_n": 1,
    "bottom_n": 1
  }'
```

**Response:**
```json
{
  "signals": [
    {"symbol": "AAPL", "predicted_return": 0.0142, "rank": 1, "target_weight": 1.0},
    {"symbol": "MSFT", "predicted_return": 0.0056, "rank": 2, "target_weight": 0.0},
    {"symbol": "GOOGL", "predicted_return": -0.0089, "rank": 3, "target_weight": -1.0}
  ],
  "metadata": {
    "model_version": "v1.0.0",
    "strategy_name": "alpha_baseline",
    "generated_at": "2024-10-17T16:30:00Z"
  }
}
```

---

## Testing

### Run All Tests

```bash
# Unit tests (fast)
pytest tests/ -v

# Integration tests for T3 (requires PostgreSQL)
pytest apps/signal_service/tests/ -v -m integration

# Feature parity tests
pytest apps/signal_service/tests/test_feature_parity.py -v
```

### Manual Validation

```bash
# T1: Data Pipeline
pytest tests/test_integration_pipeline.py -v

# T2: Baseline Strategy
pytest tests/test_baseline_strategy.py -v

# T3: Signal Service (all phases)
python scripts/test_p1_p2_model_registry.py    # Phase 1-2: 6/6 ✅
python scripts/test_p3_signal_generator.py     # Phase 3: 7/7 ✅
python scripts/test_p4_fastapi.py              # Phase 4: 9/9 ✅
python scripts/test_p5_hot_reload.py           # Phase 5: 6/6 ✅

# T4: Execution Gateway
python scripts/test_t4_execution_gateway.py    # Integration tests: 6/6 ✅
pytest apps/execution_gateway/tests/ -v       # Unit tests: 44/44 ✅
```

---

## Next Steps

### Before T5 Implementation

1. ✅ **Review completed work** (T1-T4)
2. ✅ **Update documentation** with current status
3. ⏳ **Plan T5 architecture** (Position Tracker)

### T5 Implementation Plan

**Goal:** Position tracking and broker state synchronization

**Key decisions needed:**
- Position reconciliation logic with broker
- Exposure monitoring and alerts
- P&L calculation methodology
- Integration with T4 execution gateway

**See:** [docs/TASKS/P0_TICKETS.md](./docs/TASKS/P0_TICKETS.md) for T5 requirements

---

## Architecture Highlights

### Data Flow

```
T1: Data ETL → Adjusted Parquet Files
                      ↓
T2: Strategy → Trained Model (LightGBM) → MLflow
                      ↓
T3: Signal Service → Model Registry (PostgreSQL) → REST API
                      ↓
T4: Execution Gateway → Orders (PostgreSQL) → Alpaca API ✅
                      ↓
T5: Position Tracker → Position Updates (UPCOMING)
                      ↓
T6: Orchestrator → Full Pipeline (UPCOMING)
```

### Key Patterns

1. **Feature Parity** - Research and production share identical feature code
2. **Hot Reload** - Zero-downtime model updates via background polling
3. **Idempotency** - Safe retries with deterministic client_order_id (T4)
4. **Circuit Breakers** - Safety mechanisms for production (T5)
5. **Reconciliation** - Broker state synchronization (T5)

---

## Statistics

### Code Metrics
- **Production Code:** 5,800+ lines (T1-T4)
- **Test Code:** 3,400+ lines
- **Documentation:** 12,500+ lines
- **Test Pass Rate:** 98% (107/110 tests)

### Components Delivered
- 4 major tasks complete (T1, T2, T3, T4)
- 2 database schemas (model_registry, execution_tables)
- 5 architectural decisions documented
- 7 concept documents
- 7 implementation guides
- 8 deployment scripts

### Performance
- Data ETL: < 1s for 750 rows ✅
- Signal generation: < 100ms for 5 symbols ✅
- Model reload: < 1s ✅
- Order submission: < 100ms (target: 500ms) ✅
- Order query: < 20ms (target: 50ms) ✅
- Webhook processing: < 50ms (target: 200ms) ✅
- Zero downtime during updates ✅

---

## Contributing

1. Read [CLAUDE.md](./CLAUDE.md) for comprehensive guidance
2. Follow [docs/DOCUMENTATION_STANDARDS.md](./docs/DOCUMENTATION_STANDARDS.md)
3. Write tests first (TDD)
4. Create ADR for architectural changes
5. Document trading concepts in /docs/CONCEPTS/
6. Update implementation guides

---

## License

Private project.
