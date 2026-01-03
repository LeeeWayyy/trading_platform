# Repository Map

**Last Updated:** 2026-01-02

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
├── config/            # Configuration files
├── .github/           # GitHub configuration (workflows, actions)
└── .ai_workflow/      # AI workflow state and audit logs
```

---

## apps/ - Microservices

FastAPI-based microservices implementing the trading platform's core functionality.

### apps/alert_worker/
**Purpose:** RQ worker that delivers alert notifications (email, Slack, SMS) with retry scheduling and rate limiting.

**Key Files:**
- `entrypoint.py` - RQ worker entrypoint and delivery execution

### apps/auth_service/
**Purpose:** FastAPI OAuth2 service handling login, callback, refresh, and logout flows with PKCE and CSP protections.

**Key Files:**
- `main.py` - FastAPI app and OAuth2 endpoints

### apps/backtest_worker/
**Purpose:** RQ worker entrypoint that validates environment/Redis and processes prioritized backtest queues with retry tracking.

**Key Files:**
- `entrypoint.py` - Worker startup and queue processing

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

### apps/model_registry/
**Purpose:** FastAPI read-only model registry API for model metadata retrieval, listing, and validation with auth enforced.

**Key Files:**
- `main.py` - FastAPI application and registry bootstrap

### apps/orchestrator/
**Purpose:** Coordinates end-to-end paper trading workflow
**Key Features:**
- Daily signal generation orchestration
- Order execution coordination
- Multi-strategy workflow management

**Key Files:**
- `main.py` - Orchestrator service
- `workflow.py` - Workflow orchestration logic

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

### apps/web_console_ng/
**Purpose:** NiceGUI-based web console with auth/session middleware, admission control, and health endpoints.

**Key Files:**
- `main.py` - NiceGUI app entrypoint and middleware setup

---

## strategies/ - Trading Strategies

Trading strategy implementations following the Qlib framework.

### strategies/alpha_baseline/
**Purpose:** Baseline alpha strategy using Alpha158 features + LightGBM
**Performance:** IC=0.082, Sharpe=1.45, 55% win rate

**Key Files:**
- `features.py` - Alpha158 feature definitions
- `model.py` - LightGBM model training
- `pipeline.py` - End-to-end training pipeline
- `backtest.py` - Backtesting logic

### strategies/backtest/
**Purpose:** Signal-based backtesting framework for evaluating strategy performance on historical data.

**Key Files:**
- `__init__.py` - Backtest evaluator, metrics, and configuration exports

### strategies/ensemble/
**Purpose:** Framework for combining multiple strategy signals via weighted or voting ensembles.

**Key Files:**
- `__init__.py` - Ensemble combiner, configuration, and weighting helpers

### strategies/mean_reversion/
**Purpose:** Mean reversion strategy using oversold/overbought indicators for entry and exit signals.

**Key Files:**
- `README.md` - Strategy overview and indicator definitions

### strategies/momentum/
**Purpose:** Momentum strategy that trades persistent trends using MA/MACD/ADX/ROC signals.

**Key Files:**
- `README.md` - Strategy overview and indicator definitions

---

## libs/ - Shared Libraries

Reusable libraries shared across services.

### libs/admin/
**Purpose:** Admin utilities for API key generation, hashing, validation, and revocation tracking.

### libs/alerts/
**Purpose:** Alert rules, delivery models, and PII masking helpers for notification workflows.

### libs/allocation/
**Purpose:** Portfolio allocation and rebalancing
**Key Features:**
- Multi-strategy capital allocation
- Portfolio optimization
- Rebalancing logic

**Key Files:**
- `allocator.py` - Capital allocation logic
- `optimizer.py` - Portfolio optimization

### libs/alpha/
**Purpose:** Alpha research framework with PIT-correct backtesting, canonical alphas, and metrics adapters.

### libs/analytics/
**Purpose:** Analytics tools for microstructure, event studies, volatility modeling, and factor attribution.

### libs/backtest/
**Purpose:** Backtest jobs, Monte Carlo analysis, walk-forward optimization, and RQ queue/worker utilities.

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

### libs/data_providers/
**Purpose:** WRDS/CRSP/Compustat/Fama-French/yfinance providers with unified fetcher and sync tooling.

### libs/data_quality/
**Purpose:** Data quality framework for sync manifests, validation, schema drift, and dataset versioning.

### libs/factors/
**Purpose:** Factor construction and analytics framework with PIT correctness and canonical factor definitions.

### libs/health/
**Purpose:** Health check and Prometheus latency clients with cached, staleness-aware responses.

### libs/market_data/
**Purpose:** Market data fetching and caching
**Key Features:**
- Historical data retrieval
- Real-time quote caching
- Symbol universe management

**Key Files:**
- `provider.py` - Market data provider interface
- `cache.py` - Data caching layer

### libs/models/
**Purpose:** Model registry for versioned artifacts with metadata, manifests, and promotion gates.

### libs/redis_client/
**Purpose:** Redis connection pool and utilities
**Key Features:**
- Feature caching
- Event pub/sub
- Circuit breaker state storage

**Key Files:**
- `client.py` - Redis client with connection pooling
- `cache.py` - Caching utilities

### libs/risk/
**Purpose:** Risk analytics for factor covariance, specific risk, portfolio optimization, and stress testing.

### libs/risk_management/
**Purpose:** Pre-trade and post-trade risk checks
**Key Features:**
- Circuit breaker logic
- Position limit checks
- Notional limit validation
- Drawdown monitoring

**Key Files:**
- `circuit_breaker.py` - Circuit breaker implementation
- `risk_checker.py` - Pre-trade risk validation
- `monitor.py` - Post-trade monitoring

### libs/secrets/
**Purpose:** Secrets management with pluggable backends
**Key Features:**
- Vault backend integration
- AWS Secrets Manager backend
- Environment variable backend (dev only)
- 90-day secret rotation

**Key Files:**
- `manager.py` - Secret manager interface
- `vault_backend.py` - HashiCorp Vault integration
- `aws_backend.py` - AWS Secrets Manager integration
- `env_backend.py` - Environment variable backend

### libs/tax/
**Purpose:** Tax lot tracking, wash sale detection, and Form 8949 export utilities.

### libs/web_console_auth/
**Purpose:** JWT/mTLS auth library for web console sessions, roles, and rate limiting.

---

## infra/ - Infrastructure

Infrastructure configuration for local development and deployment.

### infra/alertmanager/
**Purpose:** Alertmanager routing and notification configuration.
**Files:**
- `config.yml` - Alertmanager routing and receiver settings

### infra/docker-compose.yml
**Purpose:** Local development stack
**Services:**
- PostgreSQL (port 5432)
- Redis (port 6379)
- Prometheus (port 9090)
- Grafana (port 3000)
- Loki (port 3100)
- Promtail (log collector)
- All microservices (ports 8000-8003, 8501)

### infra/grafana/
**Purpose:** Visualization and dashboards
**Files:**
- `dashboards/` - 3 preconfigured dashboards
  - `trading_overview.json` - Positions, P&L, orders
  - `service_health.json` - Service metrics
  - `performance.json` - Latency and throughput
- `provisioning/` - Datasource and dashboard provisioning

### infra/loki/
**Purpose:** Centralized logging
**Files:**
- `loki-config.yml` - Loki configuration
- `promtail-config.yml` - Log scraping configuration

### infra/nginx/
**Purpose:** Nginx reverse proxy configuration for the web console.
**Files:**
- `nicegui-cluster.conf` - Upstream cluster configuration
- `nicegui-location.conf` - Route/location configuration

### infra/prometheus/
**Purpose:** Metrics collection configuration
**Files:**
- `prometheus.yml` - Prometheus scrape configuration
- `alerts.yml` - Alert rules (30+ alerts)

### infra/promtail/
**Purpose:** Promtail log shipping configuration for Loki.
**Files:**
- `promtail-config.yml` - Promtail scrape configuration

---

## db/ - Database Migrations (Alembic)

Alembic-managed database schema migrations.

**Key Files:**
- `alembic.ini` - Alembic configuration
- `env.py` - Migration environment setup
- `versions/` - Migration scripts (timestamped)

**Schema Tables:**
- `model_registry` - ML model versions and metadata
- `orders` - Order history and status
- `positions` - Position tracking
- `audit_log` - Manual action audit trail

---

## migrations/ - Legacy SQL Migrations

Direct SQL migration scripts (pre-Alembic).

**Files:**
- `001_create_model_registry.sql` - Model registry table
- `002_create_execution_tables.sql` - Orders and positions
- `003_create_risk_tables.sql` - Risk management tables
- `004_add_audit_log.sql` - Audit logging table

---

## scripts/ - Utility Scripts

Automation scripts and operational tools.

### Core Workflow Scripts
- `workflow_gate.py` - AI workflow gate enforcement (6-step pattern)
- `context_checkpoint.py` - Context management for AI sessions
- `tasks.py` - Task lifecycle management

### Trading Operations
- `paper_run.py` - Execute end-to-end paper trading for a date
- `quick_train_test.py` - Fast model training for testing

### Data Management
- `generate_test_data.py` - Generate synthetic test data

### Testing & CI
- `test_health_check.sh` - Verify system health before tests
- `run_manual_tests.sh` - Execute manual integration tests

---

## tests/ - Test Suite

Comprehensive test suite mirroring source structure.

**Structure:**
```
tests/
├── apps/                  # Service tests
│   ├── signal_service/
│   ├── execution_gateway/
│   ├── market_data_service/
│   └── web_console/
├── libs/                  # Library tests
│   ├── common/
│   ├── data_pipeline/
│   ├── risk_management/
│   └── secrets/
├── strategies/            # Strategy tests
│   └── alpha_baseline/
├── scripts/               # Script tests
│   └── test_workflow_gate.py
└── integration/           # End-to-end tests
    └── test_paper_run.py
```

**Key Test Files:**
- `conftest.py` - Pytest fixtures and configuration
- `pytest.ini` - Pytest configuration

**Test Categories:**
- Unit tests: Pure functions, isolated logic
- Integration tests: Database, API, service interactions
- E2E tests: Full paper trading workflow

---

## docs/ - Documentation

Comprehensive project documentation organized by purpose.

### docs/GETTING_STARTED/
**Purpose:** Onboarding and setup guides
**Key Files:**
- `PROJECT_STATUS.md` - Current project status and progress
- `REPO_MAP.md` - This file
- `SETUP.md` - Development environment setup
- `TESTING_SETUP.md` - Test environment setup
- `GLOSSARY.md` - Trading and system terminology
- `LOGGING_GUIDE.md` - Centralized logging usage

### docs/STANDARDS/
**Purpose:** Coding and process standards
**Key Files:**
- `CODING_STANDARDS.md` - Python code standards
- `DOCUMENTATION_STANDARDS.md` - Documentation guidelines
- `GIT_WORKFLOW.md` - Git branching and commit standards
- `TESTING.md` - Testing requirements and patterns
- `ADR_GUIDE.md` - Architecture Decision Record template

### docs/ADRs/
**Purpose:** Architecture Decision Records (17+ ADRs)
**Key ADRs:**
- `0001-microservices-architecture.md`
- `0005-centralized-logging-architecture.md`
- `0007-circuit-breaker-design.md`
- `0017-secrets-management.md`

### docs/CONCEPTS/
**Purpose:** Trading and system concept explanations
**Topics:**
- Idempotency
- Circuit breakers
- Model registry
- TWAP execution
- Workflow optimization

### docs/TASKS/
**Purpose:** Task planning and completion tracking
**Files:**
- `INDEX.md` - Task index and status
- `ARCHIVE/TASKS_HISTORY/P0_TASKS_DONE.md`, `ARCHIVE/TASKS_HISTORY/P1_PLANNING_DONE.md`, `P2_PLANNING.md` - Phase planning (P0/P1 archived)
- `PxTy_DONE.md` - Completed task documentation (30+ files)
- `PxTy_TASK.md` - Active/future tasks

### docs/AI/
**Purpose:** AI-assisted development guidance
**Subdirectories:**
- `Workflows/` - AI development workflows (8 workflows)
- `Prompts/` - Reusable AI prompts
- `Examples/` - Example interactions and PR guidelines
- `Research/` - AI capability research findings
- `Implementation/` - Implementation guides
- `Audits/` - Code audit reports
- `Analysis/` - Code analysis checklists

**Key Files:**
- `AI_GUIDE.md` - Comprehensive AI assistant guide (symlinked as CLAUDE.md, AGENTS.md)
- `README.md` - AI documentation index

### docs/RUNBOOKS/
**Purpose:** Operational runbooks and procedures
**Key Files:**
- `ops.md` - Operational procedures
- `logging-queries.md` - LogQL query examples
- `secrets-migration.md` - Secrets backend migration
- `secret-rotation.md` - 90-day rotation procedure
- `web-console-user-guide.md` - Web console usage

### docs/LESSONS_LEARNED/
**Purpose:** Post-implementation retrospectives
**Files:**
- `AUDIT_REPORT_2025-10-25.md` - Code audit findings

---

## data/ - Market Data Storage

Market data organized by date and adjustment type.

**Structure:**
```
data/
├── raw/                   # Unadjusted data from Alpaca
│   └── YYYY-MM-DD/
│       └── {SYMBOL}.parquet
├── adjusted/              # Split/dividend adjusted data
│   └── YYYY-MM-DD/
│       └── {SYMBOL}.parquet
└── catalog.duckdb         # DuckDB catalog for fast queries
```

Note: Generated output directories (artifacts, notebooks, logs, htmlcov) are created on demand and may be absent in a clean checkout.

---

## config/ - Configuration Files

Application configuration files (not secrets).

**Files:**
- `symbols.txt` - Tradable universe
- `strategies.yml` - Strategy configurations
- `risk_limits.yml` - Risk limit parameters

---

## .github/ - GitHub Configuration

GitHub Actions workflows and shared action definitions.

**Subdirectories:**
- `workflows/` - CI and automation workflows
- `actions/` - Shared action implementations (includes `wait-for-services`)

---

## .ai_workflow/ - AI Workflow State (gitignored)

AI workflow automation state and audit logs.

**Files:**
- `config.json` - Workflow configuration
- `.workflow-state.lock` - Workflow state lock file
- `workflow-state.json` - Workflow gate state
- `workflow-audit.log` - Review audit trail (JSON lines)
- `plans/` - Plan artifacts

---

## Key Configuration Files

### Root Level
- `pyproject.toml` - Python project metadata and dependencies
- `requirements.txt` - Python package requirements
- `pytest.ini` - Pytest configuration
- `.env.template` - Environment variable template
- `.env` - Local environment variables (gitignored)
- `.gitignore` - Git ignore rules
- `Makefile` - Common development commands
- `CLAUDE.md`, `AGENTS.md` - Symlinks to docs/AI/AI_GUIDE.md

---

## Quick Reference

### Common Commands
```bash
# Development
make up          # Start infrastructure
make down        # Stop infrastructure
make test        # Run test suite
make ci-local    # Run full CI locally

# Paper Trading
make paper-run   # Execute paper trading for today

# Operations
make status      # Check positions, orders, P&L
make kill-switch # Emergency stop

# Workflow
./scripts/workflow_gate.py advance plan-review  # Request plan review
./scripts/workflow_gate.py advance implement    # Start implementation
./scripts/workflow_gate.py run-ci commit        # Run CI before commit
```

### Finding Code

**By Feature:**
- Signal generation: `apps/signal_service/`
- Order execution: `apps/execution_gateway/`
- Risk checks: `libs/risk_management/`
- Market data: `apps/market_data_service/`, `libs/market_data/`
- Secrets: `libs/secrets/`
- Logging: `libs/common/logging/`

**By Technology:**
- FastAPI: `apps/*/main.py`
- Pydantic: `apps/*/schemas.py`
- Redis: `libs/redis_client/`
- PostgreSQL: `db/`, `migrations/`
- DuckDB: `libs/duckdb_catalog.py`
- Docker: `infra/docker-compose.yml`
- Prometheus: `infra/prometheus/`
- Grafana: `infra/grafana/`

---

## Related Documentation

- [Project Status](./PROJECT_STATUS.md) - Current progress and milestones
- [Setup Guide](./SETUP.md) - Development environment setup
- [AI Guide](../AI/AI_GUIDE.md) - AI-assisted development workflows
- [Task Index](../TASKS/INDEX.md) - All tasks and their status

---

**Document Version:** 2.0 (Comprehensive Restructure)
**Last Updated:** 2026-01-02
**Maintained By:** Development Team
