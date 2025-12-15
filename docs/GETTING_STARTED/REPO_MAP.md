# Repository Map

**Last Updated:** 2025-11-21

This document provides a comprehensive map of the trading platform repository structure, explaining the purpose of each directory and key files.

---

## Top-Level Structure

```
trading_platform/
├── apps/              # Microservices (FastAPI applications)
├── strategies/        # Trading strategy implementations
├── libs/              # Shared libraries and utilities
├── infra/             # Infrastructure configuration (Docker, Prometheus, Grafana)
├── db/                # Database schema and migrations (Alembic)
├── migrations/        # Legacy SQL migrations (pre-Alembic)
├── scripts/           # Utility scripts and automation tools
├── tests/             # Test suite (mirrors src structure)
├── docs/              # Comprehensive documentation
├── data/              # Market data storage (parquet files, DuckDB catalogs)
├── artifacts/         # ML models, backtests, reports
├── config/            # Configuration files
├── notebooks/         # Jupyter notebooks for research and analysis
├── prompts/           # AI prompts and templates
├── logs/              # Application logs (gitignored)
├── htmlcov/           # Test coverage reports (gitignored)
└── .claude/           # AI workflow state and audit logs
```

---

## apps/ - Microservices

FastAPI-based microservices implementing the trading platform's core functionality.

### apps/signal_service/
**Purpose:** Generates trading signals using ML models
**Key Features:**
- Model hot-reload from model registry
- Redis caching for feature data
- Multi-strategy signal generation
- Model version management

**Key Files:**
- `main.py` - FastAPI application entry point
- `signal_generator.py` - Signal generation logic
- `model_loader.py` - Model registry integration
- `config.py` - Service configuration

### apps/execution_gateway/
**Purpose:** Executes trades via Alpaca API with idempotency guarantees
**Key Features:**
- Idempotent order submission (client_order_id deduplication)
- TWAP order slicing
- Real-time P&L tracking
- Position management
- Dry-run mode support

**Key Files:**
- `main.py` - FastAPI application with order endpoints
- `alpaca_client.py` - Alpaca API integration
- `order_manager.py` - Order lifecycle management
- `twap_slicer.py` - TWAP execution logic
- `schemas.py` - Pydantic request/response models

### apps/market_data_service/
**Purpose:** Real-time market data streaming via WebSocket
**Key Features:**
- Alpaca WebSocket integration
- Auto-subscription management
- Redis caching for latest quotes
- Trade and quote streaming

**Key Files:**
- `main.py` - FastAPI application
- `websocket_client.py` - Alpaca WebSocket client
- `subscription_manager.py` - Symbol subscription logic

### apps/orchestrator/
**Purpose:** Coordinates end-to-end paper trading workflow
**Key Features:**
- Daily signal generation orchestration
- Order execution coordination
- Multi-strategy workflow management

**Key Files:**
- `main.py` - Orchestrator service
- `workflow.py` - Workflow orchestration logic

### apps/web_console/
**Purpose:** Web-based operational dashboard (Streamlit)
**Key Features:**
- Position and P&L dashboard
- Manual order entry
- Kill switch controls
- Audit log viewer
- OAuth2 authentication

**Key Files:**
- `app.py` - Streamlit application
- `auth.py` - Authentication logic
- `Dockerfile` - Container configuration

---

## Ports & Service URLs (reference)

Service ports are configurable via environment variables (see `.env.example`). The repository defaults used in development and scripts are:

- Signal Service: http://localhost:8001  (env var: SIGNAL_SERVICE_URL)
- Execution Gateway: http://localhost:8002 (env var: EXECUTION_GATEWAY_URL)
- Orchestrator: http://localhost:8003     (env var: ORCHESTRATOR_URL)
- Web Console (Streamlit): http://localhost:8501

Note: Some auxiliary services (market-data, test helpers) may use other ports; always consult `.env.example` for the canonical per-environment values.

---

## libs/ - Shared Libraries

Reusable libraries shared across services.

### libs/common/
**Purpose:** Common utilities, exceptions, and helpers
**Key Modules:**
- `logging/` - Centralized structured logging (JSON, Loki integration)
- `exceptions.py` - Custom exception classes
- `utils.py` - General utilities

### libs/data_pipeline/
**Purpose:** Data ETL, corporate actions, quality gates
**Key Features:**
- Alpaca data fetching
- Split/dividend adjustments
- Data freshness checks
- Quality validation

**Key Files:**
- `etl.py` - ETL pipeline
- `corporate_actions.py` - Split/dividend handling
- `quality_gate.py` - Data quality validation
- `freshness.py` - Staleness detection

... (rest of document unchanged)
