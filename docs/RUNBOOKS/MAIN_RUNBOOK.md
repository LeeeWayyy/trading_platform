## Complete Operational Runbook

This runbook provides step-by-step instructions for operating the trading platform. All commands are verified against the actual codebase.

---

### Quick Start Checklist

Use this checklist to set up the trading platform from scratch.

#### Phase 1: Prerequisites
- [ ] **Hardware verified** - 4+ vCPUs, 16GB+ RAM, 50GB+ SSD
- [ ] **Python 3.11+ installed** - `python3 --version`
- [ ] **Docker 24.0+ installed** - `docker --version`
- [ ] **Docker Compose 2.20+ installed** - `docker compose version`
- [ ] **Poetry 1.7+ installed** - `poetry --version`
- [ ] **Alpaca account created** - https://alpaca.markets/

#### Phase 2: Environment Setup
- [ ] **Repository cloned** - `git clone && cd trading_platform`
- [ ] **Virtual environment created** - `python3.11 -m venv .venv`
- [ ] **Virtual environment activated** - `source .venv/bin/activate`
- [ ] **Dependencies installed** - `poetry install` or `pip install -r requirements.txt`
- [ ] **Git hooks installed** - `make install-hooks`
- [ ] **Environment file created** - `cp .env.example .env`
- [ ] **Environment file configured** - Edit `.env` with Alpaca credentials
- [ ] **Secrets generated** - `INTERNAL_TOKEN_SECRET`, `ALERT_RECIPIENT_HASH_SECRET`, `SESSION_ENCRYPTION_KEY`

#### Phase 3: Infrastructure
- [ ] **Docker services started** - `make up`
- [ ] **PostgreSQL healthy** - `docker compose ps postgres` shows "(healthy)"
- [ ] **Redis healthy** - `docker compose ps redis` shows "(healthy)"
- [ ] **Prometheus running** - http://localhost:9090
- [ ] **Grafana running** - http://localhost:3000
- [ ] **Loki healthy** - `docker compose ps loki` shows "(healthy)"
- [ ] **Promtail running** - `docker compose ps promtail`

#### Phase 4: Database
- [ ] **Migration 001 applied** - `docker exec -i trading_platform_postgres psql -U trader -d trader < migrations/001_create_model_registry.sql`
- [ ] **Migration 002 applied** - `docker exec -i trading_platform_postgres psql -U trader -d trader < migrations/002_create_execution_tables.sql`
- [ ] **Migration 003 applied** - `docker exec -i trading_platform_postgres psql -U trader -d trader < migrations/003_create_orchestration_tables.sql`
- [ ] **Slicing migration applied** - `docker exec -i trading_platform_postgres psql -U trader -d trader < db/migrations/0001_extend_orders_for_slicing.sql`
- [ ] **Tables verified** - `docker exec -it trading_platform_postgres psql -U trader -d trader -c "\dt"`

#### Phase 5: Verification
- [ ] **Tests pass** - `make test`
- [ ] **CI passes** - `make ci-local`
- [ ] **Alpaca connection works** - Test with `paper_run.py --dry-run`

#### Phase 6: First Run
- [ ] **DRY_RUN=true** in `.env` (safety first!)
- [ ] **Paper run executed** - `PYTHONPATH=. python3 scripts/paper_run.py`
- [ ] **Results reviewed** - Check logs and output

---

### A.0 Local Development Walkthrough (Full Stack)

Use this when you want **all local dev services** running (web console + APIs + workers).

#### Step 1: Ensure JWT keys for Web Console ↔ Execution Gateway
The execution gateway validates web console JWTs. Missing keys cause dashboard 500s.

```bash
source .venv/bin/activate
python3 scripts/ensure_web_console_jwt_keys.py
```

Keys are generated in `apps/web_console_ng/certs` and are **git-ignored**.

#### Step 2: Start all services
```bash
# Full stack (dev APIs + web console + workers)
docker compose --profile dev --profile workers up -d
```

If you only want a single backtest worker (dev default), use:
```bash
docker compose --profile dev up -d
```

Alternatively, `make up-dev` will run the JWT key check and start the dev stack.

#### Step 3: Verify health
```bash
docker compose --profile dev ps
curl -f http://localhost:8002/health
curl -f http://localhost:8001/health
curl -f http://localhost:8003/health
```

#### Step 4: Access services
- Web Console: http://localhost:8080
- Execution Gateway: http://localhost:8002
- Signal Service: http://localhost:8001
- Orchestrator: http://localhost:8003
- Grafana: http://localhost:3000
- Prometheus: http://localhost:9090

#### Step 5: Login (dev auth)
Set these in `.env` and restart the web console if changed:
```
WEB_CONSOLE_USER=admin
WEB_CONSOLE_PASSWORD=changeme
```

#### Step 6: Alpha Explorer (optional)
Alpha Explorer reads models from the local model registry directory.

- **Local model registry (host):** `data/models/`
- **Container path:** `/app/data/models` (mounted from `data/models/`, needs write access for `.registry.lock`)

Enable the feature in `.env`:
```
FEATURE_ALPHA_EXPLORER=true
```

If you override the model registry location, set:
```
MODEL_REGISTRY_DIR=/app/data/models
```

Then restart the web console:
```bash
docker compose --profile dev up -d web_console_dev
```

#### Step 7: Notebook Launcher (optional)
The Notebook Launcher runs JupyterLab sessions inside `web_console_dev`.

Prerequisites:
- `notebooks/templates/` exists (mounted into the container)
- `NOTEBOOK_LAUNCH_COMMAND` and `NOTEBOOK_BASE_URL` set in `.env`

Recommended local dev settings:
```
NOTEBOOK_BASE_URL=http://localhost
NOTEBOOK_LAUNCH_COMMAND=jupyter lab --ip=0.0.0.0 --port={port} --no-browser --NotebookApp.token={token} --NotebookApp.allow_remote_access=True /app/{template_path}
NOTEBOOK_LOG_DIR=artifacts/notebook_logs
```

Then restart the web console:
```bash
docker compose --profile dev up -d web_console_dev
```

Troubleshooting:
- **“NOTEBOOK_LAUNCH_COMMAND is not configured”** → add the env vars above and restart.
- **“Templates directory not found”** → ensure `notebooks/templates/` exists.

---

### A.1 Prerequisites

#### Hardware Requirements
| Component | Minimum | Recommended |
|-----------|---------|-------------|
| **CPU** | 4 vCPUs | 8 vCPUs |
| **RAM** | 16GB | 32GB |
| **Storage** | 50GB SSD | 100GB NVMe SSD |
| **Network** | Stable internet | Low-latency connection to Alpaca |

#### Software Requirements
| Software | Version | Installation |
|----------|---------|--------------|
| **Python** | 3.11+ | `brew install python@3.11` or `apt install python3.11` |
| **Docker** | 24.0+ | https://docs.docker.com/get-docker/ |
| **Docker Compose** | 2.20+ | Included with Docker Desktop |
| **Poetry** | 1.7+ | `curl -sSL https://install.python-poetry.org \| python3 -` |
| **Make** | 4.0+ | Pre-installed on macOS/Linux |
| **Node.js** | 18+ | Required for markdown-link-check (CI) |

#### External Accounts Required
| Service | Purpose | Setup Link |
|---------|---------|------------|
| **Alpaca Markets** | Paper/Live trading API | https://alpaca.markets/ |
| **Auth0** (Optional) | OAuth2 authentication | https://auth0.com/ |

#### Network Requirements
Ensure the following ports are accessible:

| Port | Service | Protocol | Access |
|------|---------|----------|--------|
| 5433 | PostgreSQL | TCP | localhost (mapped from container 5432) |
| 6379 | Redis | TCP | localhost (for local dev; remove in production) |
| 8001 | Signal Service | HTTP | Internal/localhost |
| 8002 | Execution Gateway | HTTP | Internal/localhost |
| 8003 | Orchestrator | HTTP | Internal/localhost |
| 8004 | Market Data Service | HTTP | Internal/localhost |
| 8080 | Web Console (dev) | HTTP | localhost |
| 3000 | Grafana | HTTP | localhost |
| 9090 | Prometheus | HTTP | localhost |
| 3100 | Loki | HTTP | Internal |
| 443 | nginx (mTLS/OAuth2) | HTTPS | External (production) |
| 80 | nginx redirect | HTTP | External (production) |

**Firewall Notes:**
- Database/Redis ports should NEVER be exposed to public internet
- Use Docker internal network for inter-service communication
- Only expose web-facing services (nginx, Grafana) via reverse proxy in production

---

### A.2 Environment Configuration

#### Step 1: Create Environment File
```bash
cp .env.example .env
```

#### Step 2: Configure Required Variables
Edit `.env` with your values:

```ini
# ═══════════════════════════════════════════════════════════════════
# REQUIRED: Alpaca API Credentials
# ═══════════════════════════════════════════════════════════════════
# Get from: https://app.alpaca.markets/paper/dashboard/overview
ALPACA_API_KEY_ID=PK...your_key...
ALPACA_API_SECRET_KEY=...your_secret...
ALPACA_BASE_URL=https://paper-api.alpaca.markets  # Paper trading (recommended)
# ALPACA_BASE_URL=https://api.alpaca.markets      # Live trading (CAUTION!)
# Optional: Backfill fills from Alpaca account activities (FILL)
ALPACA_FILLS_BACKFILL_ENABLED=false
ALPACA_FILLS_BACKFILL_INITIAL_LOOKBACK_HOURS=24
ALPACA_FILLS_BACKFILL_PAGE_SIZE=100
ALPACA_FILLS_BACKFILL_MAX_PAGES=5

# ═══════════════════════════════════════════════════════════════════
# REQUIRED: Database Configuration
# ═══════════════════════════════════════════════════════════════════
# NOTE: Use postgresql:// (not postgresql+psycopg://) for psycopg3 compatibility
# Port 5433 avoids conflict with local PostgreSQL installations on port 5432
DATABASE_URL=postgresql://trader:trader@localhost:5433/trader
POSTGRES_USER=trader
POSTGRES_PASSWORD=trader
POSTGRES_DB=trader
# Optional override for containers (used by docker-compose worker profiles)
DATABASE_URL_DOCKER=postgresql://trader:trader@postgres:5432/trader

# ═══════════════════════════════════════════════════════════════════
# REQUIRED: Redis Configuration
# ═══════════════════════════════════════════════════════════════════
# NOTE: Redis is exposed on localhost:6379 for local development.
# Services inside Docker network use: redis://redis:6379/0
# Default DB 1 is reserved for OAuth2/web console sessions.
REDIS_URL=redis://localhost:6379/1
# Optional override for containers (used by docker-compose worker profiles)
REDIS_URL_DOCKER=redis://redis:6379/0

# ═══════════════════════════════════════════════════════════════════
# APPLICATION CONFIGURATION
# ═══════════════════════════════════════════════════════════════════
# Strategy
STRATEGY_ID=alpha_baseline

# Safety Mode (IMPORTANT!)
DRY_RUN=true  # true = simulate orders, false = real orders to Alpaca

# Capital & Position Limits
CAPITAL=100000
MAX_POSITION_SIZE=20000

# Data Quality
DATA_FRESHNESS_MINUTES=30
OUTLIER_THRESHOLD=0.30

# ═══════════════════════════════════════════════════════════════════
# SERVICE URLS (Internal)
# ═══════════════════════════════════════════════════════════════════
SIGNAL_SERVICE_URL=http://localhost:8001
EXECUTION_GATEWAY_URL=http://localhost:8002
ORCHESTRATOR_URL=http://localhost:8003

# ═══════════════════════════════════════════════════════════════════
# SLICE RECOVERY SETTINGS (Execution Gateway)
# ═══════════════════════════════════════════════════════════════════
MISFIRE_GRACE_SECONDS=60
STALE_SLICE_EXPIRY_SECONDS=86400  # 24h default, 0 disables expiry

# ═══════════════════════════════════════════════════════════════════
# API AUTHENTICATION (C6)
# ═══════════════════════════════════════════════════════════════════
# SECURITY: Defaults to enforce (fail-closed). Only use log_only for staged rollout.
API_AUTH_MODE=enforce  # enforce | log_only

# ═══════════════════════════════════════════════════════════════════
# INTERNAL SERVICE AUTH (X-Internal-Token)
# ═══════════════════════════════════════════════════════════════════
# SECURITY: Defaults to true (fail-closed). Set to false ONLY for development.
INTERNAL_TOKEN_REQUIRED=true  # true | false
# Generate with: openssl rand -hex 32
INTERNAL_TOKEN_SECRET=your-hex-secret-here-min-64-chars
INTERNAL_TOKEN_TIMESTAMP_TOLERANCE_SECONDS=300

# ═══════════════════════════════════════════════════════════════════
# PAPER RUN DEFAULTS
# ═══════════════════════════════════════════════════════════════════
PAPER_RUN_SYMBOLS=AAPL,MSFT,GOOGL
PAPER_RUN_CAPITAL=100000
PAPER_RUN_MAX_POSITION_SIZE=20000

# ═══════════════════════════════════════════════════════════════════
# MLFLOW / QLIB
# ═══════════════════════════════════════════════════════════════════
QLIB_DATA_DIR=./data/qlib_data
MLFLOW_TRACKING_URI=file:./artifacts/mlruns
MLFLOW_EXPERIMENT_NAME=alpha_baseline

# ═══════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════
LOG_LEVEL=INFO  # DEBUG, INFO, WARNING, ERROR

# ═══════════════════════════════════════════════════════════════════
# ALERT DELIVERY SERVICE (required if alert_worker is running)
# ═══════════════════════════════════════════════════════════════════
# Generate with: python3 -c "import secrets; print(secrets.token_hex(32))"
ALERT_RECIPIENT_HASH_SECRET=your-secret-here-min-32-chars

# ═══════════════════════════════════════════════════════════════════
# OAUTH2 / OIDC (Optional - for web console)
# ═══════════════════════════════════════════════════════════════════
AUTH0_DOMAIN=your-tenant.us.auth0.com
AUTH0_CLIENT_ID=your_client_id_here
AUTH0_CLIENT_SECRET=your_client_secret_here
AUTH0_AUDIENCE=https://api.trading-platform.local
# Generate with: python3 -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
SESSION_ENCRYPTION_KEY=your_base64_encoded_32_byte_key_here

# ═══════════════════════════════════════════════════════════════════
# WEB CONSOLE (Optional - for development)
# ═══════════════════════════════════════════════════════════════════
WEB_CONSOLE_USER=admin
WEB_CONSOLE_PASSWORD=changeme  # CHANGE THIS!
WEB_CONSOLE_DEV_ROLE=admin
WEB_CONSOLE_DEV_USER_ID=admin
WEB_CONSOLE_DEV_STRATEGIES=

# Operations Dashboard Dev Auth (P4T5 - Track 7)
# WARNING: NEVER set to true in production/staging
# Only allowed in: development, dev, local, test, ci
OPERATIONS_DEV_AUTH=false
```

**Generate required secrets (local dev):**
```bash
# Internal service auth (64+ hex chars)
openssl rand -hex 32

# Alert recipient hash secret (32+ hex chars)
openssl rand -hex 32

# Session encryption key (base64 32 bytes)
openssl rand -base64 32
```

#### Variable Reference (Comprehensive)

**Legend:** 🔐 = Secret (never log/commit), ⚠️ = Critical (wrong value = financial risk), 💰 = Financial impact

| Variable | Required | Valid Values | Security | Notes |
|----------|----------|--------------|----------|-------|
| **Alpaca API** |||||
| `ALPACA_API_KEY_ID` | ✅ | 20+ char string from Alpaca | 🔐 Secret | Never log or commit |
| `ALPACA_API_SECRET_KEY` | ✅ | 40+ char secret from Alpaca | 🔐 Secret | Store in secrets manager |
| `ALPACA_BASE_URL` | ✅ | `https://paper-api.alpaca.markets` (paper) or `https://api.alpaca.markets` (live) | ⚠️ Critical | Wrong URL = real money trades! |
| `ALPACA_FILLS_BACKFILL_ENABLED` | ❌ | `true` / `false` | - | Enables Alpaca FILL activity backfill |
| `ALPACA_FILLS_BACKFILL_INITIAL_LOOKBACK_HOURS` | ❌ | Integer (default: 24) | - | Lookback window on first run |
| `ALPACA_FILLS_BACKFILL_PAGE_SIZE` | ❌ | Integer (default: 100) | - | Alpaca activity page size |
| `ALPACA_FILLS_BACKFILL_MAX_PAGES` | ❌ | Integer (default: 5) | - | Max pages per reconciliation run |
| **Database** |||||
| `DATABASE_URL` | ✅ | `postgresql://user:pass@host:port/db` | 🔐 Secret | Prefer TLS, non-superuser (local uses port 5433) |
| `POSTGRES_USER` | ✅ | String | 🔐 Secret | Default: `trader` |
| `POSTGRES_PASSWORD` | ✅ | String | 🔐 Secret | Rotate regularly |
| **Redis** |||||
| `REDIS_URL` | ✅ | `redis://host:port/db_index` | 🔐 Secret | Local default `db=1` for sessions; enable AUTH/TLS in production |
| **Trading Safety** |||||
| `DRY_RUN` | ✅ | `true` / `false` | ⚠️ Critical | `true` = no real orders |
| `CAPITAL` | ✅ | Positive integer (USD) | 💰 Financial | Match broker account equity |
| `MAX_POSITION_SIZE` | ✅ | Positive integer (shares) | 💰 Financial | Hard limit per position |
| `DATA_FRESHNESS_MINUTES` | ❌ | Integer (default: 30) | ⚠️ Critical | Too high = stale signals |
| **Strategy** |||||
| `STRATEGY_ID` | ✅ | Slug string (e.g., `alpha_baseline`) | - | Affects order IDs |
| `OUTLIER_THRESHOLD` | ❌ | 0-1 decimal (default: 0.30) | - | Data quality filter |
| **Service URLs** |||||
| `SIGNAL_SERVICE_URL` | ✅ | `http://localhost:8001` | - | Internal service |
| `EXECUTION_GATEWAY_URL` | ✅ | `http://localhost:8002` | - | Controls order path |
| `ORCHESTRATOR_URL` | ✅ | `http://localhost:8003` | - | Workflow controller |
| **Slice Recovery** |||||
| `MISFIRE_GRACE_SECONDS` | ❌ | Integer (default: 60) | - | Scheduler grace window before misfire |
| `STALE_SLICE_EXPIRY_SECONDS` | ❌ | Integer (default: 86400) | - | 0 disables stale slice expiry |
| **API Authentication** |||||
| `API_AUTH_MODE` | ❌ | `enforce` / `log_only` | ⚠️ Critical | Fail-closed default; only use log_only for staged rollout |
| **Internal Service Auth** |||||
| `INTERNAL_TOKEN_REQUIRED` | ❌ | `true` / `false` | ⚠️ Critical | Default true; never disable in production |
| `INTERNAL_TOKEN_SECRET` | ✅ | 64+ hex chars | 🔐 Secret | Required when INTERNAL_TOKEN_REQUIRED=true |
| `INTERNAL_TOKEN_TIMESTAMP_TOLERANCE_SECONDS` | ❌ | Integer (default: 300) | - | Allowed clock skew for tokens |
| **OAuth2 (Optional)** |||||
| `AUTH0_DOMAIN` | ❌ | `tenant.us.auth0.com` | - | Required if auth_type=oauth2 |
| `AUTH0_CLIENT_ID` | ❌ | UUID string | - | Non-secret but sensitive |
| `AUTH0_CLIENT_SECRET` | ❌ | Confidential string | 🔐 Secret | Never commit |
| `AUTH0_AUDIENCE` | ❌ | URL string | - | API audience for OAuth2 |
| `SESSION_ENCRYPTION_KEY` | ❌ | Base64 32-byte key | 🔐 Secret | Generate: `openssl rand -base64 32` |
| **Web Console** |||||
| `WEB_CONSOLE_USER` | ✅ | String | 🔐 Secret | Required for dev auth |
| `WEB_CONSOLE_PASSWORD` | ✅ | String | 🔐 Secret | Change before deploy |
| `WEB_CONSOLE_DEV_ROLE` | ❌ | `admin`, `operator`, `viewer` | - | Dev-only RBAC role for gateway headers |
| `WEB_CONSOLE_DEV_USER_ID` | ❌ | String | - | Dev-only user ID (defaults to WEB_CONSOLE_USER) |
| `WEB_CONSOLE_DEV_STRATEGIES` | ❌ | Comma-separated strategy IDs | - | Optional for non-admin roles |
| `OPERATIONS_DEV_AUTH` | ❌ | `true` / `false` | ⚠️ Critical | NEVER enable in production/staging |
| **Logging** |||||
| `LOG_LEVEL` | ❌ | `DEBUG`, `INFO`, `WARNING`, `ERROR` | - | Avoid DEBUG in prod (PII risk) |
| **Alerts** |||||
| `ALERT_RECIPIENT_HASH_SECRET` | ✅ | 32+ hex chars | 🔐 Secret | Required for `alert_worker` rate limiting |

---

### A.3 Installation & Setup

#### Step 1: Clone Repository
```bash
git clone https://github.com/LeeeWayyy/trading_platform.git
cd trading_platform
```

#### Step 2: Create Virtual Environment
```bash
# Create virtual environment
python3.11 -m venv .venv

# Activate virtual environment (REQUIRED before any Python command)
source .venv/bin/activate

# Verify activation
which python3
# Should output: /path/to/trading_platform/.venv/bin/python3
```

#### Step 3: Install Dependencies
```bash
# Option A: Using Poetry (recommended)
poetry install

# Option B: Using pip
pip install -r requirements.txt
```

#### Step 4: Install Git Hooks
```bash
make install-hooks
```

#### Step 5: Start Infrastructure Services
```bash
# Start PostgreSQL, Redis, Prometheus, Grafana, Loki, Promtail
make up

# Optional workers (backtest + alert) - enable explicitly
# NOTE: If BuildKit snapshot errors occur, disable BuildKit for the build.
DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 docker compose --profile workers up -d

# Verify all services are running
docker compose ps
```

#### Step 5.1: Start Core APIs (required for Web Console)
```bash
# In a shell with the venv active and .env loaded
source .venv/bin/activate
set -a; source .env; set +a

# Execution Gateway (port 8002) — required by web console
PYTHONUNBUFFERED=1 poetry run uvicorn apps.execution_gateway.main:app \
  --host 0.0.0.0 --port 8002 > logs/execution_gateway.log 2>&1 &

# Optional for full local stack:
# Signal Service (port 8001)
PYTHONUNBUFFERED=1 poetry run uvicorn apps.signal_service.main:app \
  --host 0.0.0.0 --port 8001 > logs/signal_service.log 2>&1 &

# Orchestrator (port 8003)
PYTHONUNBUFFERED=1 poetry run uvicorn apps.orchestrator.main:app \
  --host 0.0.0.0 --port 8003 > logs/orchestrator.log 2>&1 &
```

**Local dev note (Web Console auth):**
- For local dev without internal auth tokens, set `INTERNAL_TOKEN_REQUIRED=false`
  in `.env` and restart the gateway so the web console headers are trusted.
- If you still see `401 Unauthorized`, set `API_AUTH_MODE=log_only` in `.env` (dev only)
  and restart the gateway.

Expected output:
```
NAME                           STATUS          PORTS
trading_platform_postgres      Up (healthy)    0.0.0.0:5433->5432/tcp
trading_platform_redis         Up (healthy)    0.0.0.0:6379->6379/tcp
trading_platform_prometheus    Up              0.0.0.0:9090->9090/tcp
trading_platform_grafana       Up              0.0.0.0:3000->3000/tcp
trading_platform_loki          Up (healthy)    0.0.0.0:3100->3100/tcp
trading_platform_promtail      Up              -
```

**Note:** Redis is exposed on localhost:6379 for local development. In production, remove the host port mapping. Access Redis via:
```bash
docker exec -it trading_platform_redis redis-cli
```

#### Step 6: Apply Database Migrations
```bash
# Connect to PostgreSQL and run migrations
docker exec -i trading_platform_postgres psql -U trader -d trader < migrations/001_create_model_registry.sql
docker exec -i trading_platform_postgres psql -U trader -d trader < migrations/002_create_execution_tables.sql
docker exec -i trading_platform_postgres psql -U trader -d trader < migrations/003_create_orchestration_tables.sql
docker exec -i trading_platform_postgres psql -U trader -d trader < db/migrations/0001_extend_orders_for_slicing.sql
```

**Note:** Rerunning migrations may print "already exists" warnings; those are safe in local dev.

#### Step 7: Verify Installation
```bash
# Run tests (should all pass)
make test

# Run full CI suite
make ci-local
```

---

### A.4 Starting the Trading System

#### Service Startup Sequence

**Important:** Services must be started in order due to dependencies.

```bash
# Step 1: Ensure virtual environment is active
source .venv/bin/activate

# Step 2: Verify infrastructure is running
docker compose ps

# Step 3: Start Signal Service (port 8001)
PYTHONPATH=. poetry run uvicorn apps.signal_service.main:app --host 0.0.0.0 --port 8001 &

# Step 4: Start Execution Gateway (port 8002)
PYTHONPATH=. poetry run uvicorn apps.execution_gateway.main:app --host 0.0.0.0 --port 8002 &

# Step 5: Start Orchestrator (port 8003)
PYTHONPATH=. poetry run uvicorn apps.orchestrator.main:app --host 0.0.0.0 --port 8003 &

# Step 6: Start Market Data Service (port 8004) - Optional
make market-data &
```

#### Service Health Verification
```bash
# Check Signal Service
curl http://localhost:8001/health

# Check Execution Gateway
curl http://localhost:8002/health

# Check Orchestrator
curl http://localhost:8003/health
```

Expected response for each:
```json
{"status": "healthy", "service": "...", "timestamp": "..."}
```

#### Starting Web Console (Optional)
```bash
# Development mode (port 8080)
docker compose --profile dev up -d web_console_dev

# Access at: http://localhost:8080
```

---

### A.5 Daily Operations

#### Pre-Market Checklist (Before 9:30 AM ET)
```bash
# 1. Verify infrastructure is healthy
docker compose ps

# 2. Check service health
curl http://localhost:8001/health  # Signal Service
curl http://localhost:8002/health  # Execution Gateway
curl http://localhost:8003/health  # Orchestrator

# 3. Verify data freshness (should be < 30 minutes old)
PYTHONPATH=. python3 -c "
from libs.data.data_pipeline.freshness import check_data_freshness
result = check_data_freshness()
print(f'Data age: {result.age_minutes} minutes')
print(f'Fresh: {result.is_fresh}')
"

# 4. Check circuit breaker state (should be OPEN)
docker exec trading_platform_redis redis-cli GET circuit_breaker:state

# 5. Check kill switch state (should be ACTIVE)
docker exec trading_platform_redis redis-cli GET kill_switch:state

# 6. Verify Alpaca connection
PYTHONPATH=. python3 -c "
from apps.execution_gateway.alpaca_client import AlpacaExecutor
executor = AlpacaExecutor()
print(f'Account status: {executor.get_account_status()}')
print(f'Buying power: ${executor.get_buying_power()}')
"
```

#### Running Paper Trading
```bash
# Basic run with defaults from .env
PYTHONPATH=. python3 scripts/paper_run.py

# Custom symbols
PYTHONPATH=. python3 scripts/paper_run.py --symbols AAPL MSFT GOOGL AMZN

# Custom capital and position size
PYTHONPATH=. python3 scripts/paper_run.py --capital 50000 --max-position-size 10000

# Dry run (check dependencies without executing)
PYTHONPATH=. python3 scripts/paper_run.py --dry-run

# Save results to JSON
PYTHONPATH=. python3 scripts/paper_run.py --output results_$(date +%Y%m%d).json

# Verbose mode for debugging
PYTHONPATH=. python3 scripts/paper_run.py --verbose
```

#### Checking System Status
```bash
# View current positions, orders, P&L
make status

# View logs from all services
make logs

# View logs from specific service
docker compose logs -f loki
```

---

### A.6 Monitoring & Observability

#### Dashboards

| Dashboard | URL | Purpose |
|-----------|-----|---------|
| **Grafana** | http://localhost:3000 | Metrics, logs, alerts |
| **Prometheus** | http://localhost:9090 | Raw metrics, queries |
| **Loki** | (via Grafana) | Centralized logging |

**Grafana Login:**
- Username: `admin`
- Password: `admin` (or as set in `.env`)

#### Key Metrics to Monitor
| Metric | Location | Alert Threshold |
|--------|----------|-----------------|
| Order latency | Grafana → Trading Dashboard | > 500ms |
| Signal generation time | Grafana → Signal Service | > 5s |
| Open orders count | Grafana → Execution Dashboard | > 10 |
| Circuit breaker state | Redis `circuit_breaker:state` | `TRIPPED` |
| Kill switch state | Redis `kill_switch:state` | `ENGAGED` |

#### Log Queries (Loki via Grafana)
```logql
# All errors in last hour
{job="trading_platform"} |= "ERROR"

# Signal generation logs
{service="signal_service"} |~ "signal"

# Order submissions
{service="execution_gateway"} |~ "order"

# Circuit breaker events
{job="trading_platform"} |~ "circuit_breaker"
```

#### Log Architecture
```
Services → stdout/stderr → Docker → Promtail → Loki → Grafana
```
- **Promtail** scrapes Docker container logs automatically
- **Loki** aggregates and indexes logs for fast searching
- **Grafana** provides the UI to search and visualize

#### Accessing Logs via Grafana (Recommended)
1. Open http://localhost:3000
2. Login (default: `admin`/`admin`)
3. Go to **Explore** (compass icon on left sidebar)
4. Select **Loki** as the data source
5. Use LogQL queries:
   - `{container_name="trading_platform_web_console_dev"}` - Web Console logs
   - `{container_name="trading_platform_postgres"}` - PostgreSQL logs
   - `{container_name=~"trading_platform.*"}` - All platform logs

#### Accessing Logs via Docker (Quick Access)
```bash
# View specific service logs
docker logs trading_platform_web_console_dev      # Web Console
docker logs trading_platform_postgres             # PostgreSQL
docker logs trading_platform_redis                # Redis
docker logs trading_platform_loki                 # Loki (log aggregator)
docker logs trading_platform_grafana              # Grafana

# Follow logs in real-time (add -f flag)
docker logs -f trading_platform_web_console_dev

# View last N lines
docker logs --tail 100 trading_platform_postgres

# View all compose service logs together
docker compose logs -f

# Filter by service in compose
docker compose logs -f postgres redis
```

#### Local Service Logs (Non-Docker)
Services running locally via `uvicorn` output to the terminal where they were started.
Use `PYTHONPATH=. poetry run uvicorn ... 2>&1 | tee service.log` to save to file.

---

### A.7 Risk Management Operations

#### Circuit Breaker Management
```bash
# Check current state
docker exec trading_platform_redis redis-cli GET circuit_breaker:state

# View trip reason (if tripped)
docker exec trading_platform_redis redis-cli GET circuit_breaker:trip_reason

# Manual reset (CAUTION - verify conditions first!)
docker exec trading_platform_redis redis-cli SET circuit_breaker:state \
  '{"state": "OPEN", "reset_by": "operator", "reset_at": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}'
```

#### Kill Switch Operations
```bash
# Check current state
docker exec trading_platform_redis redis-cli GET kill_switch:state

# ENGAGE Kill Switch (EMERGENCY - stops ALL trading)
PYTHONPATH=. python3 -c "
from libs.core.redis_client import RedisClient
from libs.trading.risk_management.kill_switch import KillSwitch
redis = RedisClient()
ks = KillSwitch(redis)
ks.engage(reason='Manual engagement - <describe reason>', operator='<your_name>')
print('🔴 KILL SWITCH ENGAGED')
"

# DISENGAGE Kill Switch (resume trading)
PYTHONPATH=. python3 -c "
from libs.core.redis_client import RedisClient
from libs.trading.risk_management.kill_switch import KillSwitch
redis = RedisClient()
ks = KillSwitch(redis)
ks.disengage(operator='<your_name>', notes='Conditions normalized')
print('✅ Kill switch disengaged')
"

# View kill switch history
docker exec trading_platform_redis redis-cli LRANGE kill_switch:history -10 -1
```

---

### A.8 Model Management

#### Training New Models
```bash
# Train Alpha158 baseline model
PYTHONPATH=. python3 strategies/alpha_baseline/train.py

# Train with custom date range
PYTHONPATH=. python3 strategies/alpha_baseline/train.py \
  --start-date 2023-01-01 \
  --end-date 2024-01-01

# Register model in MLflow
PYTHONPATH=. python3 strategies/alpha_baseline/train.py --register
```

#### Model Registry Operations
```bash
# List registered models
PYTHONPATH=. python3 -c "
import mlflow
mlflow.set_tracking_uri('file:./artifacts/mlruns')
client = mlflow.tracking.MlflowClient()
for rm in client.search_registered_models():
    print(f'{rm.name}: {rm.latest_versions}')
"

# Get active model version
docker exec -i trading_platform_postgres psql -U trader -d trader -c \
  "SELECT strategy_name, version, status, created_at FROM model_registry WHERE status = 'active';"
```

#### Hot Reload Model (Zero Downtime)
```bash
# Trigger model reload via API
curl -X POST http://localhost:8001/api/v1/models/reload \
  -H "Content-Type: application/json" \
  -d '{"model_name": "alpha_baseline", "version": "latest"}'
```

---

### A.9 Emergency Procedures

#### 🚨 EMERGENCY: Stop All Trading
```bash
# Step 1: ENGAGE KILL SWITCH (immediate effect)
PYTHONPATH=. python3 -c "
from libs.core.redis_client import RedisClient
from libs.trading.risk_management.kill_switch import KillSwitch
redis = RedisClient()
ks = KillSwitch(redis)
ks.engage(reason='EMERGENCY: <describe situation>', operator='<your_name>')
print('🔴 KILL SWITCH ENGAGED - All trading stopped')
"

# Step 2: Cancel all open orders
curl -X POST http://localhost:8002/api/v1/orders/cancel-all

# Step 3: Verify no open orders
curl http://localhost:8002/api/v1/orders?status=open
```

#### 🚨 EMERGENCY: Flatten All Positions
```bash
# Step 1: Engage kill switch first (prevent new orders)
# ... (see above)

# Step 2: Close all positions
curl -X POST http://localhost:8002/api/v1/positions/close-all

# Step 3: Verify positions closed
curl http://localhost:8002/api/v1/positions
```

#### 🚨 EMERGENCY: Full System Shutdown
```bash
# Step 1: Engage kill switch
# ... (see above)

# Step 2: Cancel all orders
curl -X POST http://localhost:8002/api/v1/orders/cancel-all

# Step 3: Stop application services
pkill -f "uvicorn apps"  # Stop all FastAPI services

# Step 4: Stop infrastructure (preserves data)
make down

# Step 5: Stop infrastructure AND remove data (DESTRUCTIVE)
# make down-v  # Only if you want to reset everything
```

---

### A.9.1 Web Console JWT Keys (Generate/Rotate)
The web console uses RS256 JWTs for service calls (manual controls, alert manager).
Generate key material before starting the web console container.

Generate keys locally:
```bash
source .venv/bin/activate
python scripts/generate_certs.py
```

Expected output files:
```bash
ls -1 apps/web_console_ng/certs/jwt_private.key apps/web_console_ng/certs/jwt_public.pem
```

Rebuild the web console to pick up the keys:
```bash
docker compose --profile dev up -d --build web_console_dev
```

---

### A.10 Troubleshooting Guide

#### Service Won't Start
```bash
# Check if port is already in use
lsof -i :8001  # Signal Service
lsof -i :8002  # Execution Gateway
lsof -i :8003  # Orchestrator

# Kill process on port
kill -9 $(lsof -t -i:8001)

# Check for import errors
PYTHONPATH=. python3 -c "from apps.signal_service.main import app"
```

#### Web Console: `API Error: pnl_realtime - 401 Unauthorized`
Symptom: NiceGUI shows `401 Unauthorized` for
`/api/v1/positions/pnl/realtime` (and dashboard widgets fail to load).

Cause: Execution Gateway requires `X-User-Role` + `X-User-Id` headers.
If the web console session lacks RBAC context, the headers are empty and the
gateway returns 401.

Fix (dev auth):
```bash
# Ensure dev auth has RBAC defaults
grep WEB_CONSOLE_DEV_ .env
# Expected (example):
# WEB_CONSOLE_DEV_ROLE=admin
# WEB_CONSOLE_DEV_USER_ID=admin

# Restart the web console container so it reloads env vars
docker compose --profile dev up -d --build web_console_dev
```

Verify:
```bash
curl -H "X-User-Role: admin" -H "X-User-Id: admin" \
  http://localhost:8002/api/v1/positions/pnl/realtime
```

If you see `401` with `Invalid or missing internal authentication token`,
set `INTERNAL_TOKEN_REQUIRED=false` (dev only) and restart the gateway.

#### Web Console: Auth0 Login Loops Back to Login Page
Symptom: Clicking "Login with Auth0" refreshes and returns to the same page.

Cause (most common):
- `WEB_CONSOLE_AUTH_TYPE=dev` (Auth0 flow is not active).
- `OAUTH2_LOGIN_URL` not set (defaults to `/login`, which just reloads the login page).
- Auth0 values are placeholders and auth_service/nginx OAuth2 stack is not running.

Fix (choose one):
```bash
# Option A: Dev auth (recommended for local dev)
# Keep WEB_CONSOLE_AUTH_TYPE=dev and use the normal dev login on the main page.
# Do NOT use the Auth0 login page in dev mode.

# Option B: Enable OAuth2 stack (Auth0)
# 1) Configure real Auth0 env vars + local callback URLs
# 2) Start oauth2 profile services
docker compose --profile oauth2 up -d web_console_oauth2 auth_service nginx_oauth2
```

Notes:
- If using OAuth2 on localhost (http), set `COOKIE_SECURE=false` in `.env`.
- Ensure Auth0 "Allowed Callback URLs" match `OAUTH2_REDIRECT_URI`.

#### Web Console: Panels Empty / "No strategy access"
Symptom: Strategy list, positions, or performance panels are blank and API
returns `403 No strategy access`.

#### Trade Journal: Empty / Missing Trades
Symptom: `/journal` loads but shows no trades (or "relation trades does not exist").

Checklist:
```bash
# 1) Ensure trades table exists
docker exec -i trading_platform_postgres psql -U trader -d trader -c "\\d trades"

# 2) Ensure fills exist in orders metadata (backfill source)
docker exec -i trading_platform_postgres psql -U trader -d trader \
  -c "SELECT client_order_id, metadata->'fills' FROM orders WHERE metadata ? 'fills' LIMIT 1"
```

If fills are missing (webhooks down or missed), trigger Alpaca fills backfill:
```bash
curl -X POST http://localhost:8002/api/v1/reconciliation/fills-backfill \
  -H "X-User-Role: admin" -H "X-User-Id: admin"
```

Optional: force a wider lookback (hours) and recalculate realized P&L:
```bash
curl -X POST http://localhost:8002/api/v1/reconciliation/fills-backfill \
  -H "X-User-Role: admin" -H "X-User-Id: admin" \
  -H "Content-Type: application/json" \
  -d '{"lookback_hours": 72, "recalc_all_trades": true}'
```

If the endpoint returns `status=disabled`, ensure in `.env`:
```
ALPACA_FILLS_BACKFILL_ENABLED=true
```
Then restart:
```bash
docker compose --profile dev up -d --build execution_gateway
```

Cause: The web console did not send `X-User-Strategies` (missing strategy scope).

Fix:
```bash
# Option A: set explicit dev strategies
export WEB_CONSOLE_DEV_STRATEGIES=alpha_baseline

# Option B: rely on STRATEGY_ID fallback (recommended)
grep STRATEGY_ID .env
# Ensure docker-compose passes STRATEGY_ID into web_console and rebuild:
docker compose --profile dev up -d --build web_console_dev
```

#### Web Console: Manual Controls Panel Blank / `session_version` Missing
Symptom: Manual controls page is blank or shows an exception like
`User session missing session_version - cannot call backend API`.

Cause: Manual controls API requires `X-Session-Version`. Dev auth sets it in
session state, but the web console must pass it through to API headers.

Fix:
```bash
# Ensure dev session version env is set
grep WEB_CONSOLE_DEV_SESSION_VERSION .env
# Example:
# WEB_CONSOLE_DEV_SESSION_VERSION=1

# Rebuild/restart the web console to reload the code + env
docker compose --profile dev up -d --build web_console_dev
```

Verify:
```bash
docker logs --tail=50 trading_platform_web_console_dev
```

#### Web Console: ModuleNotFoundError (aiosmtplib / rq / boto3 / hvac)
Symptom: NiceGUI page errors like:
`ModuleNotFoundError: No module named 'aiosmtplib'` (alerts),
`No module named 'rq'` (backtest),
`No module named 'boto3'` or `hvac` (secrets backends).

Fix:
```bash
# Rebuild web console image to pick up updated requirements
docker compose --profile dev up -d --build web_console_dev
```

#### Web Console: `pnl_realtime` Network Error (`host.docker.internal` unreachable)
Symptom: `Network is unreachable` from the web console container when calling
`http://host.docker.internal:8002/...`.

Fix:
```bash
# Ensure Execution Gateway is running on the host
lsof -i :8002

# On Linux, Docker may not resolve host.docker.internal by default.
# Use host-gateway to add it, or run the gateway inside Docker instead.
# Example: add this to the web_console service if needed:
# extra_hosts:
#   - "host.docker.internal:host-gateway"
```

#### PostgreSQL Port Conflict (Local PostgreSQL Installed)
If you have a local PostgreSQL installation on macOS (via Homebrew or installer), it may conflict with Docker PostgreSQL. Symptoms include "role does not exist" errors when connecting to Docker PostgreSQL.

```bash
# Check if local PostgreSQL is running on port 5432
lsof -i :5432

# If you see both Docker and local PostgreSQL, the platform uses port 5433
# to avoid conflicts. Verify DATABASE_URL in .env uses port 5433:
grep DATABASE_URL .env
# Should show: DATABASE_URL=postgresql://trader:trader@localhost:5433/trader

# Important: Use postgresql:// (not postgresql+psycopg://) for psycopg3
# The +psycopg driver suffix causes connection errors with psycopg3

# Test Docker PostgreSQL connection on port 5433
psql "postgresql://trader:trader@localhost:5433/trader" -c "SELECT 1;"
```

#### Database Connection Failed
```bash
# Check PostgreSQL is running
docker compose ps postgres

# Test connection (note: uses port 5433, not 5432)
docker exec trading_platform_postgres psql -U trader -d trader -c "SELECT 1;"

# Check logs
docker compose logs postgres

# Restart if needed
docker compose restart postgres
```

#### Worker DB Error: `connection refused` to `localhost:5433`
Symptom: Worker logs (alert_worker/backtest_worker) show
`connection refused` to `localhost:5433`.

Cause: Containers must use `postgres:5432` (the Docker network hostname),
but an old container or mis-set env is pointing at host `localhost:5433`.

Fix:
```bash
# Recreate worker container to pick up correct env
docker compose --profile workers up -d --force-recreate alert_worker

# Verify inside container
docker exec trading_platform-alert_worker-1 env | rg DATABASE_URL
# Should be: postgresql://trader:trader@postgres:5432/trader
```

#### Redis Connection Failed
```bash
# Check Redis is running
docker compose ps redis

# Test connection
docker exec trading_platform_redis redis-cli ping
# Should return: PONG

# Check if state keys exist
docker exec trading_platform_redis redis-cli KEYS "*"

# Restart if needed
docker compose restart redis
```

#### Debug Runbook
For troubleshooting scenarios (empty pages, service startup failures, build/cache errors),
see `docs/RUNBOOKS/debug-runbook.md`.

#### Web Console UI E2E Smoke Tests (Playwright)
Use Playwright to exercise core panels via a real browser.

Prerequisites:
```bash
# Start web console (dev auth)
docker compose --profile dev up -d web_console_dev
```

Install dev dependencies and browser:
```bash
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m playwright install chromium
```

Run the e2e smoke tests:
```bash
# Requires dev auth; uses WEB_CONSOLE_USER/PASSWORD from .env
RUN_E2E=1 WEB_CONSOLE_URL=http://localhost:8080 pytest tests/e2e/web_console_ng -v -m e2e
```

#### Circuit Breaker Won't Reset
```bash
# Check current state and trip reason
docker exec trading_platform_redis redis-cli GET circuit_breaker:state
docker exec trading_platform_redis redis-cli GET circuit_breaker:trip_reason

# Common causes:
# - DATA_STALE: Run ETL pipeline to refresh data
# - DAILY_LOSS_EXCEEDED: Wait until next day or manually reset
# - BROKER_ERROR: Check Alpaca API status

# Force reset (ONLY if conditions are verified safe)
docker exec trading_platform_redis redis-cli SET circuit_breaker:state \
  '{"state": "OPEN", "reset_by": "operator", "reset_at": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'", "note": "Manual reset after verification"}'
```

#### Alpaca API Errors
```bash
# Test API credentials
PYTHONPATH=. python3 -c "
from apps.execution_gateway.alpaca_client import AlpacaExecutor
executor = AlpacaExecutor()
try:
    account = executor.get_account()
    print(f'Account ID: {account.id}')
    print(f'Status: {account.status}')
    print(f'Buying Power: \${account.buying_power}')
except Exception as e:
    print(f'ERROR: {e}')
"

# Check Alpaca status page: https://status.alpaca.markets/
```

#### Web Console Docker Container Errors
If the Web Console starts but shows `ModuleNotFoundError` when accessing http://localhost:8080:

```bash
# Check container logs for import errors
docker logs trading_platform_web_console_dev 2>&1 | tail -30

# Common error 1: ModuleNotFoundError: No module named 'prometheus_client'
# Solution: Dependency should be in requirements.txt - rebuild the image
docker compose --profile dev up -d --build web_console_dev

# Common error 2: ModuleNotFoundError: No module named 'libs'
# The Dockerfile must include COPY libs /app/libs to include shared libraries
# The libs folder is required for get_trusted_proxy_ips() in config.py

# Common error 3: ModuleNotFoundError: No module named 'fastapi'
# The libs/common/network_utils.py uses lazy imports for FastAPI
# If you see this error, the network_utils.py needs TYPE_CHECKING imports
# FastAPI is only imported at runtime when FastAPI-specific functions are called

# Verify container is healthy after rebuild
curl -s http://localhost:8080/healthz  # Should return JSON with status: "alive"

# If you see uvicorn command not found:
# The Dockerfile uses "python -m uvicorn" instead of "uvicorn" directly
# because pip --target doesn't install scripts to PATH
```

---

### A.11 Backup & Recovery

#### Database Backup
```bash
# Create backup
docker exec trading_platform_postgres pg_dumpall -U trader > backup_$(date +%Y%m%d_%H%M%S).sql

# Restore from backup
docker exec -i trading_platform_postgres psql -U trader < backup_YYYYMMDD_HHMMSS.sql
```

#### Redis State Backup
```bash
# Redis AOF is enabled by default, but you can trigger manual save
docker exec trading_platform_redis redis-cli BGSAVE

# Backup Redis data directory
docker cp trading_platform_redis:/data ./redis_backup_$(date +%Y%m%d)
```

#### Configuration Backup
```bash
# Backup all configuration
tar -czvf config_backup_$(date +%Y%m%d).tar.gz \
  .env \
  docker-compose.yml \
  infra/ \
  migrations/
```

---

### A.12 Quick Reference Commands

| Action | Command |
|--------|---------|
| **Start infrastructure** | `make up` |
| **Stop infrastructure** | `make down` |
| **View logs** | `make logs` |
| **Run tests** | `make test` |
| **Run CI locally** | `make ci-local` |
| **Format code** | `make fmt` |
| **Check linting** | `make lint` |
| **Check status** | `make status` |
| **Run paper trading** | `PYTHONPATH=. python3 scripts/paper_run.py` |
| **Start Signal Service** | `PYTHONPATH=. poetry run uvicorn apps.signal_service.main:app --port 8001` |
| **Start Execution Gateway** | `PYTHONPATH=. poetry run uvicorn apps.execution_gateway.main:app --port 8002` |
| **Start Orchestrator** | `PYTHONPATH=. poetry run uvicorn apps.orchestrator.main:app --port 8003` |
| **Start Market Data** | `make market-data` |

---

### A.13 Cloud Deployment Guide

This section covers key considerations when deploying the trading platform to cloud environments.

#### Cloud vs Local: Key Differences

| Aspect | Local Development | Cloud Production |
|--------|------------------|------------------|
| **Loki user** | `user: "0"` (root - for volume permissions) | Non-root UID 10001 with proper PV permissions |
| **Redis** | localhost:6379 (dev only) | Private subnet, Redis AUTH enabled |
| **PostgreSQL** | localhost:5433 | Managed service (RDS, Cloud SQL, Azure DB) |
| **Secrets** | `.env` file | Secrets Manager (AWS SM, HashiCorp Vault) |
| **TLS** | Optional (self-signed) | Required (Let's Encrypt / ACM) |
| **Scaling** | Single instance | Horizontal scaling with load balancer |

#### Recommended Cloud Architecture

```
                    ┌─────────────────────────────────────────────────┐
                    │                  Cloud VPC                       │
                    │                                                  │
    Internet ──────►│   ┌──────────────┐    ┌──────────────────────┐  │
                    │   │   ALB/NLB    │───►│  ECS/GKE/AKS         │  │
                    │   │   (HTTPS)    │    │  ┌────────────────┐  │  │
                    │   └──────────────┘    │  │ Signal Service │  │  │
                    │                       │  │ Exec Gateway   │  │  │
                    │                       │  │ Orchestrator   │  │  │
                    │                       │  │ Web Console    │  │  │
                    │                       │  └────────────────┘  │  │
                    │                       └──────────────────────┘  │
                    │                              │                   │
                    │            ┌─────────────────┴─────────────────┐ │
                    │            ▼                                   ▼ │
                    │   ┌────────────────┐              ┌───────────┐  │
                    │   │ Managed        │              │ Managed   │  │
                    │   │ PostgreSQL     │              │ Redis     │  │
                    │   │ (RDS/Cloud SQL)│              │(ElastiCache)│ │
                    │   └────────────────┘              └───────────┘  │
                    │                                                  │
                    │   ┌────────────────────────────────────────────┐ │
                    │   │        Grafana Cloud / CloudWatch          │ │
                    │   │   (Prometheus, Loki, Grafana - managed)    │ │
                    │   └────────────────────────────────────────────┘ │
                    └─────────────────────────────────────────────────┘
```

#### Cloud Service Recommendations

| Component | AWS | GCP | Azure |
|-----------|-----|-----|-------|
| **Compute** | ECS Fargate / EKS | Cloud Run / GKE | AKS / Container Apps |
| **PostgreSQL** | RDS PostgreSQL | Cloud SQL | Azure Database for PostgreSQL |
| **Redis** | ElastiCache Redis | Memorystore | Azure Cache for Redis |
| **Monitoring** | CloudWatch / Grafana Cloud | Cloud Monitoring | Azure Monitor / Grafana Cloud |
| **Secrets** | Secrets Manager | Secret Manager | Key Vault |
| **Load Balancer** | ALB/NLB | Cloud Load Balancing | Azure Load Balancer |
| **Container Registry** | ECR | Artifact Registry | ACR |

#### Cloud Deployment Checklist

##### Security
- [ ] **Remove `user: "0"`** from Loki (use init container for volume permissions)
- [ ] **Enable Redis AUTH** with strong password
- [ ] **Enable PostgreSQL TLS** (require `sslmode=verify-full`)
- [ ] **Use Secrets Manager** instead of `.env` files
- [ ] **Enable VPC/private subnets** for databases (no public access)
- [ ] **Configure WAF** for web-facing services
- [ ] **Enable audit logging** for all services
- [ ] **Set up IAM roles** (principle of least privilege)

##### Networking
- [ ] **Use private subnets** for databases and Redis
- [ ] **Configure security groups** (allow only necessary ports)
- [ ] **Enable TLS everywhere** (inter-service communication)
- [ ] **Set up VPC peering** if multi-region
- [ ] **Configure NAT Gateway** for outbound internet (Alpaca API)

##### Reliability
- [ ] **Enable multi-AZ** for PostgreSQL and Redis
- [ ] **Configure auto-scaling** for compute
- [ ] **Set up health checks** with proper thresholds
- [ ] **Enable automated backups** (daily, 30-day retention)
- [ ] **Configure disaster recovery** (cross-region replication)

##### Observability
- [ ] **Use Grafana Cloud** or managed Prometheus/Loki
- [ ] **Set up alerting** (PagerDuty, Opsgenie integration)
- [ ] **Configure log retention** (CloudWatch Logs, S3 archival)
- [ ] **Enable distributed tracing** (X-Ray, Jaeger)
- [ ] **Set up dashboards** for trading metrics

##### Cost Optimization
- [ ] **Right-size instances** (start small, scale up)
- [ ] **Use spot/preemptible** for non-critical workloads
- [ ] **Enable auto-scaling** to scale down during off-hours
- [ ] **Set up budget alerts** (CloudWatch Billing, GCP Budgets)
- [ ] **Review reserved instance options** for steady-state workloads

#### Docker Compose Changes for Cloud

When deploying to cloud, make these changes to `docker-compose.yml`:

```yaml
# REMOVE for cloud (local dev only):
loki:
  user: "0"  # ❌ Remove - security risk in production

# CHANGE for cloud:
redis:
  command: redis-server --appendonly yes --requirepass ${REDIS_PASSWORD}
  # Add port mapping only if needed for debugging
  # ports:
  #   - "6379:6379"  # ❌ Keep internal only

# ADD for cloud:
services:
  all-services:
    environment:
      - DATABASE_URL=postgresql://user:pass@rds-endpoint:5432/trader?sslmode=verify-full
      - REDIS_URL=rediss://:${REDIS_PASSWORD}@elasticache-endpoint:6379/0
```

#### Kubernetes (EKS/GKE/AKS) Considerations

If deploying to Kubernetes:

```yaml
# Example: Loki with proper security context
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: loki
spec:
  template:
    spec:
      securityContext:
        runAsUser: 10001
        runAsGroup: 10001
        fsGroup: 10001
      initContainers:
        - name: fix-permissions
          image: busybox
          command: ['sh', '-c', 'chown -R 10001:10001 /tmp/loki']
          volumeMounts:
            - name: loki-data
              mountPath: /tmp/loki
      containers:
        - name: loki
          image: grafana/loki:3.0.0
          # No user override needed - uses default non-root
```

#### Environment Variables for Cloud

```ini
# Cloud-specific additions to .env

# Database (use managed service endpoint)
DATABASE_URL=postgresql://trader:${DB_PASSWORD}@my-rds-instance.region.rds.amazonaws.com:5432/trader?sslmode=verify-full

# Redis (use managed service with TLS)
REDIS_URL=rediss://:${REDIS_PASSWORD}@my-elasticache.region.cache.amazonaws.com:6379/0

# Secrets (reference from Secrets Manager)
# In ECS/Fargate, use secrets injection instead of plain text:
# secrets:
#   - name: ALPACA_API_SECRET_KEY
#     valueFrom: arn:aws:secretsmanager:region:account:secret:trading-platform/alpaca

# Logging (use CloudWatch or Grafana Cloud)
LOKI_URL=https://logs-prod-us-central1.grafana.net/loki/api/v1/push
PROMETHEUS_REMOTE_WRITE_URL=https://prometheus-prod-us-central1.grafana.net/api/prom/push
```

---

**Last Updated:** 2025-12-03
**Author:** Claude Code
**Version:** 5.4 (Added quick start checklist, cloud deployment guide)
