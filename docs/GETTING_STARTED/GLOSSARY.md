# Glossary

**Last Updated:** 2025-11-21

Quick reference guide for trading platform terminology. Terms are organized by category for easy navigation.

---

## 📚 Quick Navigation

- [Core Trading Concepts](#core-trading-concepts)
- [Market Data & Pricing](#market-data--pricing)
- [Risk & Safety](#risk--safety)
- [Orders & Execution](#orders--execution)
- [Performance Metrics](#performance-metrics)
- [Machine Learning & Signals](#machine-learning--signals)
- [System Architecture](#system-architecture)
- [Infrastructure & Tools](#infrastructure--tools)
- [Security & Compliance](#security--compliance)
- [Development & Testing](#development--testing)

---

## Core Trading Concepts

### Alpha
Expected excess returns above a benchmark. Our baseline strategy achieves IC=0.082.

**See also:** [Signal](#signal), [Information Coefficient](#information-coefficient)

### Circuit Breaker
Safety mechanism that stops trading when risk limits are violated.

**States:**
- **OPEN** - Normal trading
- **TRIPPED** - Trading stopped

**Triggers:** Drawdown breach, broker errors, stale data (>30min)

**Location:** `libs/risk_management/circuit_breaker.py`

### Dry-Run Mode
Simulation mode - orders logged but not sent to broker.

**Set via:** `DRY_RUN=true` environment variable

**See also:** [Paper Trading](#paper-trading)

### Idempotent
Safe to repeat without side effects. Critical for order submissions.

**Example:** Submitting same order twice → only 1 execution

**Implementation:** `client_order_id` deduplication

### Paper Trading
Trading with simulated money using real APIs and market data.

**Mode:** `DRY_RUN=false` + Alpaca paper credentials

**Command:** `make paper-run`

### Position
Current holdings of a security.

**Attributes:** Symbol, quantity, average price, unrealized P&L

**Table:** `positions` in PostgreSQL

### Strategy
Complete trading system (signals + allocation + execution).

**Current:** `alpha_baseline` (Alpha158 + LightGBM)

**Location:** `strategies/`

---

## Market Data & Pricing

### Ask Price
Lowest price a seller will accept.

### Bid Price
Highest price a buyer will pay.

### Fill
Completed order execution at broker. Updates positions and triggers P&L calculations.

### OHLCV
**O**pen, **H**igh, **L**ow, **C**lose, **V**olume - fundamental price metrics.

**Source:** Alpaca API

**Storage:** `data/adjusted/` (Parquet files)

### Spread
Difference between bid and ask price (liquidity cost).

**Example:** Bid=$100.00, Ask=$100.05 → Spread = $0.05 (5 bps)

### Symbol
Ticker identifier for a tradable security (e.g., "AAPL", "MSFT").

**Universe:** `config/symbols.txt`

### Volume
Number of shares traded in a time period. Indicates liquidity.

### VWAP
**Volume-Weighted Average Price** - benchmark for execution quality.

**Formula:** `sum(price × volume) / sum(volume)`

---

## Risk & Safety

### Corporate Actions
Events affecting security pricing (splits, dividends). Must be adjusted in historical data.

**Location:** `libs/data_pipeline/corporate_actions.py`

### Drawdown
Peak-to-trough decline in portfolio value.

**Example:** $100k → $95k = 5% drawdown

**Monitoring:** Continuous by post-trade monitor

### Kill Switch
Emergency stop - cancels all orders, flattens positions, blocks new signals.

**Trigger:** Manual via `make kill-switch` or web console

**API:** `POST /api/v1/kill-switch/engage`

### Position Limits
Maximum holdings allowed per symbol or total portfolio.

**Enforced:** Pre-trade checks

### Reconciler
Service syncing positions/orders between database and broker.

**Runs:** Boot-time + every 15 minutes

**Actions:** Cancel stale orders, adjust positions, alert on discrepancies

**Location:** `apps/reconciler/`

### Risk Management
System of checks preventing excessive losses.

**Pre-Trade:**
- Position limits
- Notional limits
- Blacklist enforcement
- Circuit breaker check

**Post-Trade:**
- Drawdown monitoring
- Volatility tracking
- Exposure monitoring

**Location:** `libs/risk_management/`

---

## Orders & Execution

### Client Order ID
Unique order identifier ensuring idempotency.

**Format:** `hash(symbol + side + qty + price + strategy + date)[:24]`

### Market Order
Immediate execution at best available price (no price guarantee).

### TWAP
**Time-Weighted Average Price** - splits large orders into smaller slices over time.

**Example:** 1000 shares over 30 min = 10 slices of 100 shares every 3 min

**Purpose:** Minimize market impact

**Location:** `apps/execution_gateway/twap_slicer.py`

---

## Performance Metrics

### Cost Basis
Original purchase price of security (for tax calculations).

### Information Coefficient (IC)
Correlation between predicted and actual returns. Measures signal quality.

**Baseline:** 0.082

**Good:** |IC| > 0.05 for daily signals

### P&L (Profit & Loss)
**Unrealized:** Mark-to-market value of open positions
- Formula: `(current_price - avg_cost) × quantity`

**Realized:** Profit/loss from closed positions

**API:** `GET /api/v1/pnl/realtime`

### Sharpe Ratio
Risk-adjusted return metric.

**Formula:** `(mean_return - risk_free_rate) / std_deviation`

**Baseline:** 1.45

**Good:** >1.0, **Excellent:** >2.0

### Wash Sale
IRS rule: Loss disallowed if you buy substantially identical security within 30 days.

**Tracking:** Required for tax reporting (P2T3)

---

## Machine Learning & Signals

### Alpha158
Standard Qlib feature set with 158 technical indicators from OHLCV data.

**Includes:** Momentum, volatility, price patterns

**Location:** `strategies/alpha_baseline/features.py`

### Backtest
Strategy simulation using historical data.

**Tools:** Qlib framework

**Output:** Sharpe, IC, drawdown, win rate

### Feature Parity
Research and production must share identical feature code.

**Why:** Prevents backtest/live divergence

**Implementation:** Single `features.py` used by both

### Model Registry
Database storing ML model versions, metrics, and promotion stages.

**Table:** `model_registry` in PostgreSQL

**Stages:** dev → staging → prod

**Location:** `apps/signal_service/model_loader.py`

### Model Versioning
Tracking model versions with metadata (performance, config, status).

### Signal
Trading recommendation (buy/sell/hold) from ML model.

**Generation:** Signal Service → loads model → computes features → generates signals

**API:** `POST /api/v1/signals/generate`

---

## System Architecture

### ADR
**Architecture Decision Record** - documents important architectural decisions.

**Location:** `docs/ADRs/`

**Examples:** Microservices, Logging, Secrets Management

### Event Bus
Message passing using Redis pub/sub for inter-service communication.

**Use Cases:**
- Order fill notifications
- Circuit breaker state changes
- Model registry updates

### FastAPI
Modern Python web framework for building APIs. Used for all microservices.

**Services:** Signal Service, Execution Gateway, Market Data, Orchestrator, Web Console

**Ports:** 8000-8003, 8501

### Microservices
Architecture splitting application into small, independent services.

**Services:**
- Signal Service (port 8000)
- Execution Gateway (port 8001)
- Market Data Service (port 8002)
- Orchestrator (port 8003)
- Web Console (port 8501)

### Migration
Versioned database schema change (managed by Alembic).

**Location:** `db/versions/`

**Commands:**
```bash
alembic upgrade head    # Apply all
alembic downgrade -1    # Rollback one
```

### Pydantic
Data validation library using Python type hints.

**Used for:** API schemas, configuration

**Files:** `apps/*/schemas.py`, `apps/*/config.py`

### REST API
Web service architecture using HTTP methods.

**Documentation:** http://localhost:800X/docs (OpenAPI/Swagger)

---

## Infrastructure & Tools

### Alembic
Database migration tool for Python.

**Directory:** `db/`

**Config:** `db/alembic.ini`

### Docker
Containerization platform for all services.

**Compose:** `infra/docker-compose.yml`

**Services:** PostgreSQL, Redis, Prometheus, Grafana, Loki, Promtail, all microservices

### DuckDB
Embedded analytics database for fast Parquet queries.

**Catalog:** `data/catalog.duckdb`

**Location:** `libs/duckdb_catalog.py`

### Grafana
Visualization platform for metrics and logs.

**URL:** http://localhost:3000 (admin/admin)

**Dashboards:** Trading Overview, Service Health, Performance

**Config:** `infra/grafana/`

### Loki
Log aggregation system storing structured JSON logs.

**URL:** http://localhost:3100

**Query:** LogQL language

**Config:** `infra/loki/loki-config.yml`

### Parquet
Columnar storage format for analytics.

**Location:** `data/adjusted/`, `data/raw/`

**Benefits:** Fast queries, excellent compression, schema evolution

### PostgreSQL
Primary database for persistent state.

**Tables:** model_registry, orders, positions, audit_log

**Port:** 5432

### Prometheus
Metrics collection and alerting.

**URL:** http://localhost:9090

**Metrics:** 33 custom metrics across 7 services

**Config:** `infra/prometheus/prometheus.yml`

### Promtail
Log scraping agent shipping logs to Loki.

**Config:** `infra/loki/promtail-config.yml`

### Redis
In-memory data store for caching and events.

**URL:** redis://localhost:6379

**Use Cases:**
- Feature caching (10-min TTL)
- Circuit breaker state
- Event pub/sub
- Quote caching

**Location:** `libs/redis_client/`

### Streamlit
Python framework for web UIs (used for web console).

**Port:** 8501

**Features:** Auto-refresh dashboards, form inputs, charts

**Location:** `apps/web_console/app.py`

---

## Security & Compliance

### Audit Log
Permanent record of all manual actions.

**Table:** `audit_log` in PostgreSQL

**Logged:** Manual orders, kill switch, strategy changes, auth attempts

### Authentication
User identity verification (web console).

**Modes:**
- **Dev:** Basic auth (isolated only)
- **Paper:** Mutual TLS + JWT
- **Production:** OAuth2/OIDC (required)

**Session:** 15-min idle, 4-hour absolute timeout

### OAuth2/OIDC
Industry-standard authentication protocol (required for production).

**Providers:** Auth0, Okta, AWS Cognito, Google Identity

### Secret Management
Secure storage and rotation of credentials.

**Backends:**
- EnvSecretManager (dev - reads `.env`)
- VaultSecretManager (HashiCorp Vault)
- AWSSecretsManager (AWS Secrets Manager)

**Rotation:** 90-day mandatory cycle

**Location:** `libs/secrets/`

### Session Management
Tracking authenticated user sessions with timeout enforcement.

**Timeouts:** 15-min idle, 4-hour absolute

**Storage:** Server-side in Redis

### Vault
HashiCorp secrets management with encryption at rest.

**Production:** Staging and production environments

**Dev Mode:** Local only (not secure - in-memory data)

**Location:** `libs/secrets/vault_backend.py`

---

## Development & Testing

### Backtest
See [Machine Learning & Signals](#backtest)

### CI/CD
**Continuous Integration/Continuous Deployment** - automated testing and deployment.

**Platform:** GitHub Actions

**Features:** Smart test selection, linting, coverage (>80%), Docker builds

**Config:** `.github/workflows/`

### Context Checkpoint
Snapshot of AI coding session for resuming after delegation.

**Storage:** `.claude/checkpoints/`

**Script:** `scripts/context_checkpoint.py`

### Continuation ID
Unique identifier for multi-turn AI conversations.

**Format:** UUIDv4 or provider-specific

**Usage:** Zen-MCP code reviews

### JSON Lines (JSONL)
Text format with one JSON object per line.

**Example:**
```
{"timestamp": "2025-11-21T10:00:00Z", "action": "order_submit"}
{"timestamp": "2025-11-21T10:05:00Z", "action": "kill_switch"}
```

**Files:** `.claude/workflow-audit.log`

### Jupyter Notebook
Interactive computing for research and analysis.

**Location:** `notebooks/`

**Use:** Feature experiments, performance analysis, data quality checks

### Structured Logging
JSON-formatted logs enabling powerful querying.

**Format:**
```json
{
  "timestamp": "2025-11-21T10:00:00Z",
  "level": "INFO",
  "service": "execution_gateway",
  "trace_id": "abc-123",
  "message": "Order submitted",
  "context": {"symbol": "AAPL", "quantity": 100}
}
```

**Location:** `libs/common/logging/`

### Trace ID
Unique identifier propagated across service calls.

**Header:** `X-Trace-ID`

**Format:** UUIDv4

**Purpose:** Distributed tracing and debugging

### WebSocket
Bidirectional real-time communication protocol.

**Provider:** Alpaca WebSocket API

**Events:** Trades, quotes, bars

**Location:** `apps/market_data_service/websocket_client.py`

### Workflow Gate
Enforcement checkpoint in AI development workflow.

**Gates:**
1. Planning artifacts exist
2. Plan reviewed
3. Tests pass
4. Code reviewed
5. CI passes
6. No pending delegations

**Script:** `scripts/workflow_gate.py`

### Zen-MCP
Message Control Protocol for AI agent delegation.

**Tool:** `mcp__zen__clink`

**Agents:** Gemini (architecture), Codex (code quality)

---

## Common Acronyms

| Acronym | Full Name |
|---------|-----------|
| **AC** | Acceptance Criteria |
| **ADR** | Architecture Decision Record |
| **API** | Application Programming Interface |
| **AWS** | Amazon Web Services |
| **CI/CD** | Continuous Integration/Continuous Deployment |
| **CLI** | Command Line Interface |
| **DB** | Database |
| **ETL** | Extract, Transform, Load |
| **HTTP** | Hypertext Transfer Protocol |
| **IC** | Information Coefficient |
| **JSON** | JavaScript Object Notation |
| **JSONL** | JSON Lines |
| **JWT** | JSON Web Token |
| **MCP** | Message Control Protocol |
| **ML** | Machine Learning |
| **MVP** | Minimum Viable Product |
| **OHLCV** | Open, High, Low, Close, Volume |
| **OIDC** | OpenID Connect |
| **P&L** | Profit and Loss |
| **REST** | Representational State Transfer |
| **SQL** | Structured Query Language |
| **TDD** | Test-Driven Development |
| **TTL** | Time To Live |
| **TWAP** | Time-Weighted Average Price |
| **UI** | User Interface |
| **UTC** | Coordinated Universal Time |
| **UUID** | Universally Unique Identifier |
| **VWAP** | Volume-Weighted Average Price |
| **WS** | WebSocket |

---

## See Also

- **[Project Status](./PROJECT_STATUS.md)** - Current project progress
- **[Repo Map](./REPO_MAP.md)** - Repository structure guide
- **[Setup Guide](./SETUP.md)** - Development environment setup
- **[Concepts](../CONCEPTS/)** - Detailed technical explanations

---

**Document Version:** 3.0 (Reorganized for Readability)
**Last Updated:** 2025-11-21
**Maintained By:** Development Team
