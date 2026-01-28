# Repository Map

**Last Updated:** 2026-01-27

This document provides a comprehensive map of the trading platform repository structure, explaining the purpose of each directory and key files.

---

## Top-Level Structure

```
trading_platform/
├── apps/              # Microservices (FastAPI applications)
├── strategies/        # Production trading strategy implementations
├── research/          # Experimental code (lenient CI checks, no deployment)
├── libs/              # Shared libraries and utilities
├── infra/             # Infrastructure configuration (Docker, Prometheus, Grafana)
├── db/                # Database schema and migrations (Alembic + legacy)
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

### apps/web_console_ng/
**Purpose:** NiceGUI-based web console (primary UI application)
**Related:** [ADR-0031](../ADRs/ADR-0031-nicegui-migration.md)

**Key Features:**
- Position and P&L dashboard with real-time updates
- Manual order entry and kill switch controls
- Circuit breaker management
- Risk analytics and performance charts
- Alpha signal explorer and strategy comparison
- Research notebooks and scheduled reports
- Admin dashboard with API key management

**Key Directories:**
- `auth/` - Authentication middleware, session store
- `components/` - Reusable UI components
- `core/` - Infrastructure (database, redis, client lifecycle)
- `pages/` - Page handlers (@ui.page decorated)
- `services/` - Page-specific services
- `ui/` - Layout, disconnect overlay

**Key Files:**
- `main.py` - NiceGUI app entrypoint and middleware setup
- `config.py` - Configuration and feature flags

---

## strategies/ - Trading Strategies

Production trading strategy implementations following the Qlib framework. All strategies here pass strict CI checks and are deployment-ready.

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

---

## research/ - Experimental Code

**Purpose:** Separate experimental code from production with lenient quality standards. See [research/README.md](../../research/README.md) for detailed guidelines.

**Key Characteristics:**
- ✅ Lenient CI checks (no strict mypy/ruff/test coverage)
- ❌ Never deployed to production
- 🎯 Focus on rapid iteration and learning
- 📈 Successful research graduates to production with full standards

### research/strategies/

Experimental strategy implementations being developed and tested.

**Current Strategies:**
- `momentum/` - Momentum strategy using MA/MACD/ADX/ROC signals (experimental)
- `mean_reversion/` - Mean reversion strategy using oversold/overbought indicators (experimental)

**Graduation Criteria:**
- Sharpe ratio >1.5 in backtests
- Risk management checks pass
- Full test coverage (>80%)
- Code review approved
- Comprehensive documentation

### research/notebooks/

Jupyter notebooks for exploratory data analysis and experimentation.

**Guidelines:**
- Name with date prefix: `YYYY-MM-DD_description.ipynb`
- Document analysis goals and findings
- Export key visualizations to `artifacts/visualizations/`

### research/experiments/

One-off scripts and proof-of-concept implementations.

**Purpose:** Quick prototypes to answer specific questions

### research/data_exploration/

Data quality analysis, schema exploration, and feature engineering.

**Purpose:** Validate data quality and test feature ideas before production

---

## libs/ - Shared Libraries

Reusable libraries shared across services, organized into logical subdirectories:

```
libs/
├── core/                    # Core infrastructure (common, health, redis_client)
├── data/                    # Data pipeline and providers
├── trading/                 # Trading logic (allocation, alpha, backtest, risk)
├── models/                  # Model registry and factors
├── platform/                # Platform services (admin, alerts, analytics, secrets, tax, web_console_auth)
├── web_console_data/        # Web console data layer (strategy-scoped queries)
└── web_console_services/    # Web console backend services (migrated from apps/web_console)
```

### libs/core/ - Core Infrastructure

#### libs/core/common/
**Purpose:** Common utilities, exceptions, and helpers
**Key Modules:**
- `logging/` - Centralized structured logging (JSON, Loki integration)
- `exceptions.py` - Custom exception classes
- `utils.py` - General utilities
- `db.py` - Database connection helpers
- `db_pool.py` - PostgreSQL connection pooling (psycopg async)
- `sync_db_pool.py` - PostgreSQL connection pooling (psycopg sync)
- `validators.py` - Input validation utilities

#### libs/core/health/
**Purpose:** Health check and Prometheus latency clients with cached, staleness-aware responses.

#### libs/core/redis_client/
**Purpose:** Redis connection pool and utilities
**Key Features:**
- Feature caching
- Event pub/sub
- Circuit breaker state storage

**Key Files:**
- `client.py` - Redis client with connection pooling
- `cache.py` - Caching utilities

### libs/data/ - Data Pipeline and Providers

#### libs/data/data_pipeline/
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

#### libs/data/data_providers/
**Purpose:** WRDS/CRSP/Compustat/Fama-French/yfinance providers with unified fetcher and sync tooling.

#### libs/data/data_quality/
**Purpose:** Data quality framework for sync manifests, validation, schema drift, and dataset versioning.

#### libs/data/market_data/
**Purpose:** Market data fetching and caching
**Key Features:**
- Historical data retrieval
- Real-time quote caching
- Symbol universe management

**Key Files:**
- `provider.py` - Market data provider interface
- `cache.py` - Data caching layer

### libs/trading/ - Trading Logic

#### libs/trading/allocation/
**Purpose:** Portfolio allocation and rebalancing
**Key Features:**
- Multi-strategy capital allocation
- Portfolio optimization
- Rebalancing logic

**Key Files:**
- `allocator.py` - Capital allocation logic
- `optimizer.py` - Portfolio optimization

#### libs/trading/alpha/
**Purpose:** Alpha research framework with PIT-correct backtesting, canonical alphas, and metrics adapters.

#### libs/trading/backtest/
**Purpose:** Backtest jobs, Monte Carlo analysis, walk-forward optimization, and RQ queue/worker utilities.

#### libs/trading/risk/
**Purpose:** Risk analytics for factor covariance, specific risk, portfolio optimization, and stress testing.

#### libs/trading/risk_management/
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

### libs/models/ - Model Registry and Factors

#### libs/models/factors/
**Purpose:** Factor construction and analytics framework with PIT correctness and canonical factor definitions.

#### libs/models/models/
**Purpose:** Model registry for versioned artifacts with metadata, manifests, and promotion gates.

### libs/platform/ - Platform Services

#### libs/platform/admin/
**Purpose:** Admin utilities for API key generation, hashing, validation, and revocation tracking.

#### libs/platform/alerts/
**Purpose:** Alert rules, delivery models, and PII masking helpers for notification workflows.

#### libs/platform/analytics/
**Purpose:** Analytics tools for microstructure, event studies, volatility modeling, and factor attribution.

### libs/web_console_data/ - Web Console Data Layer

**Purpose:** Data access layer for web console with strategy-scoped queries
**Key Features:**
- Strategy-scoped data access with encryption
- User authorization and data isolation
- Query result caching

**Key Files:**
- `strategy_scoped_queries.py` - Strategy-scoped database queries

### libs/web_console_services/ - Web Console Services

**Purpose:** Backend services for web console application (migrated from apps/web_console)
**Key Services:**
- `alert_service.py` - Alert configuration and management
- `alpha_explorer_service.py` - Alpha signal exploration
- `cb_service.py` - Circuit breaker control and monitoring
- `comparison_service.py` - Strategy comparison analytics
- `data_explorer_service.py` - Dataset exploration with SQL validation
- `data_quality_service.py` - Data quality monitoring and alerts
- `data_sync_service.py` - Data synchronization scheduling
- `health_service.py` - System health monitoring
- `notebook_launcher_service.py` - Jupyter notebook launcher
- `risk_service.py` - Risk analytics and position monitoring
- `scheduled_reports_service.py` - Report scheduling and generation
- `sql_validator.py` - SQL query validation and sanitization

**Key Directories:**
- `schemas/` - Pydantic models for service DTOs (data_management.py, health.py, risk.py)
- `config.py` - Service configuration (database URLs, endpoints)
- `cb_metrics.py` - Circuit breaker Prometheus metrics

#### libs/platform/secrets/
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

#### libs/platform/tax/
**Purpose:** Tax lot tracking, wash sale detection, and Form 8949 export utilities.

#### libs/platform/web_console_auth/
**Purpose:** JWT/mTLS authentication and authorization library for web console
**Key Features:**
- OAuth2 flow with PKCE
- JWT validation and JWKS integration
- Role-based access control (RBAC)
- Session management and invalidation
- Rate limiting
- Audit logging
- API client authentication

**Key Modules:**
- `oauth2_flow.py` - OAuth2 flow handler with PKCE
- `jwks_validator.py` - JWT validation and JWKS fetching
- `permissions.py` - Role and permission definitions
- `session_store.py` - Redis session management
- `rate_limiter.py` - Rate limiting utilities
- `audit_log.py` - Audit logger for security events
- `api_client.py` - Authenticated API client
- `helpers.py` - Authentication helper functions

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

## db/ - Database Migrations

**Location:** `db/`

**Current System:** Alembic migrations in `db/migrations/`
**Legacy System:** Archived in `db/legacy/migrations_pre_alembic/`

See `db/README.md` for migration workflow and history.

**Key Files:**
- `db/migrations/` - Active Alembic migrations (0001, 0004, 0005, ...)
- `db/legacy/migrations_pre_alembic/` - Pre-Alembic SQL scripts (001, 002, 003)
- `db/README.md` - Migration guide and troubleshooting

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
├── libs/                  # Library tests (grouped by domain)
│   ├── core/              # Core infrastructure tests
│   │   ├── common/
│   │   ├── health/
│   │   └── redis_client/
│   ├── data/              # Data layer tests
│   │   ├── data_pipeline/
│   │   ├── data_providers/
│   │   ├── data_quality/
│   │   └── market_data/
│   ├── models/            # Model management tests
│   │   ├── factors/
│   │   └── models/
│   ├── platform/          # Platform services tests
│   │   ├── admin/
│   │   ├── alerts/
│   │   ├── analytics/
│   │   ├── secrets/
│   │   ├── tax/
│   │   ├── web_console_auth/
│   │   ├── web_console_data/
│   │   └── web_console_services/
│   └── trading/           # Trading logic tests
│       ├── allocation/
│       ├── alpha/
│       ├── backtest/
│       ├── risk/
│       └── risk_management/
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
- `ARCHIVE/TASKS_HISTORY/P0_TASKS_DONE.md`, `ARCHIVE/TASKS_HISTORY/P1_PLANNING_DONE.md`, `ARCHIVE/TASKS_HISTORY/P2_PLANNING_DONE.md` - Phase planning (P0/P1/P2 archived)
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
- Risk checks: `libs/trading/risk_management/`
- Market data: `apps/market_data_service/`, `libs/data/market_data/`
- Web console services: `libs/web_console_services/`
- Web console auth: `libs/platform/web_console_auth/`
- Web console data: `libs/web_console_data/`
- Secrets: `libs/platform/secrets/`
- Logging: `libs/core/common/logging/`

**By Technology:**
- FastAPI: `apps/*/main.py`
- Pydantic: `apps/*/schemas.py`
- Redis: `libs/core/redis_client/`
- PostgreSQL: `db/migrations/` (Alembic), `db/legacy/` (archived)
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

**Document Version:** 2.1 (Web Console Migration Complete)
**Last Updated:** 2026-01-27
**Maintained By:** Development Team
