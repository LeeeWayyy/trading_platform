# Qlib + Alpaca Trading Platform — Step‑by‑Step Plan (Beginner‑Friendly)

A pragmatic, stepwise path to build a robust trading platform where **Qlib** produces signals/strategies and **Alpaca** executes trades. Each step lists goals, concepts to learn, deliverables, tech choices, and concrete commands/snippets.

---

## Table of Contents
1. Phase 0 — Prereqs & Environment
2. Phase 1 — Project Bootstrap & Repo Skeleton
3. Phase 2 — Data Layer (offline + online)
4. Phase 3 — Qlib Research Pipeline (baseline strategy)
5. Phase 4 — Strategy Packaging & Experiment Tracking
6. Phase 5 — Paper Trading Path (parity with live)
7. Phase 6 — Execution Gateway (Alpaca) with Idempotency
8. Phase 7 — Risk Manager & Capital Allocation
9. Phase 8 — Reconciler & Recovery
10. Phase 9 — Observability (metrics, logs, alerts)
11. Phase 10 — Minimal Web Console (optional)
12. Phase 11 — Live Rollout & Safety Checklist
13. 30‑60‑90 Roadmap
14. Appendix A — Folder Structure
15. Appendix B — Postgres Schema (DDL)
16. Appendix C — `docker-compose.yml` (starter)
17. Appendix D — `.env` (example)
18. Appendix E — FastAPI: Execution Gateway Skeleton
19. Appendix F — Qlib Baseline Example
20. Appendix G — Risk Checks (starter)
21. Appendix H — Glossary (plain‑English)

---

## Phase 0 — Prereqs & Environment
**Goal:** A reproducible dev environment so you can run code, tests, and containers easily.

**Learn:** Python packaging, virtual envs, Docker, basic SQL.

**Tech:** Python 3.11, Poetry (or uv/pip), Docker, Docker Compose, Git, pre‑commit, Makefile.

**Deliverables:**
- Local Python env + Poetry project.
- Docker running Postgres + Redis (and optional MinIO for S3‑like storage).
- Pre‑commit for lint/format.

**Commands:**
```bash
# System packages (macOS examples)
brew install python@3.11 poetry git redis

# Project init
mkdir qlib-alpaca-trader && cd $_
poetry init -n
poetry add fastapi uvicorn[standard] pydantic[dotenv] polars duckdb psycopg[binary] redis qlib numpy pandas scikit-learn
poetry add --group dev black ruff mypy pytest pytest-asyncio pre-commit

# Pre-commit
cat > .pre-commit-config.yaml << 'YAML'
repos:
  - repo: https://github.com/psf/black
    rev: 24.8.0
    hooks: [{id: black}]
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.8
    hooks: [{id: ruff}]
YAML
pre-commit install
```

---

## Phase 1 — Project Bootstrap & Repo Skeleton
**Goal:** A clean, opinionated layout that separates **research**, **services**, and **infra**.

**Learn:** Module boundaries, basic CI, env management.

**Deliverables:** Bootstrapped repo structure (see Appendix A) + Makefile tasks.

**Tech:** FastAPI, Pydantic, Poetry, Docker Compose.

**Makefile (excerpt):**
```make
.PHONY: up down logs fmt test
up: ## start infra
	docker compose up -d
	sleep 2
	docker compose ps

down: ## stop infra
	docker compose down -v

fmt: ## format code
	poetry run black . && poetry run ruff --fix .

test:
	poetry run pytest -q
```

---

## Phase 2 — Data Layer (offline + online)
**Goal:** Store historical data/features for research and a small online store for live features.

**Learn:** Parquet, DuckDB/Polars basics, Redis usage, schema discipline.

**Deliverables:**
- Offline store: Parquet in `./data/` or MinIO/S3; DuckDB catalog for ad‑hoc queries.
- Online store: Redis for last‑N bars & features.
- Data schemas for bars, features.

**Tech:** Polars, DuckDB, Parquet, Redis.

**Notes:**
- Keep **feature definitions** as Python functions (versioned) to guarantee parity offline/online.

---

## Phase 3 — Qlib Research Pipeline (baseline strategy)
**Goal:** End‑to‑end: ingest → features → model → backtest → metrics.

**Learn:** Qlib Dataset/Workflow, factor engineering, backtesting concepts.

**Deliverables:**
- A **baseline strategy** (e.g., simple momentum/mean‑reversion) trained with Qlib.
- Backtest report (returns, Sharpe, drawdown) saved to `artifacts/`.

**Tech:** Qlib, NumPy/Pandas/Scikit, Polars.

**Checklist:**
- Use split: train (YYYY‑1..YYYY‑3), valid (YYYY‑2), test (YYYY‑1), or walk‑forward.
- Add slippage/fees in backtest.

---

## Phase 4 — Strategy Packaging & Experiment Tracking
**Goal:** Make your strategy shippable and reproducible.

**Learn:** Packaging a strategy as a Python module; tracking experiments.

**Deliverables:**
- `strategies/alpha_baseline/` with `features.py`, `model.py`, `pipeline.py`.
- Experiment tracking via MLflow (local) with params/metrics/artifacts.

**Tech:** MLflow (local), Pydantic for config.

**Command:**
```bash
poetry add mlflow
mlflow ui --backend-store-uri sqlite:///mlruns.db --default-artifact-root ./mlruns
```

---

## Phase 5 — Paper Trading Path (parity with live)
**Goal:** Run the **exact** code path you’ll use in prod, but write orders to a **paper** broker or dry‑run logger.

**Learn:** Scheduling, state mgmt, API boundaries.

**Deliverables:**
- A `signal-service` that loads the model and emits target weights.
- An `execution-gateway` dry‑run mode recording orders.
- Daily paper P&L report.

**Tech:** FastAPI services, Redis pub/sub or Redis Streams (event bus), Postgres for orders/positions.

---

## Phase 6 — Execution Gateway (Alpaca) with Idempotency
**Goal:** Safely place/track orders via Alpaca, favoring **idempotency** and safe retries.

**Learn:** Client‑order‑id, retries, deduping, handling rejects/timeouts.

**Deliverables:**
- FastAPI service with `/api/v1/orders`, `/api/v1/orders/slice`, `/api/v1/webhooks/orders`, `/api/v1/positions`, `/health`.
- Idempotent submit with deterministic client_order_id.
- Webhook endpoint for fills/updates with signature verification when configured.

**Tech:** FastAPI, HTTPX/Requests, Pydantic, Postgres.

**See:** Appendix E for a runnable skeleton (updated to match current gateway features).

---

## Phase 7 — Risk Manager & Capital Allocation
... (rest of document unchanged up to appendices)

---

## Appendix D — `.env` (example)
```
# Alpaca
ALPACA_API_KEY_ID=your_key
ALPACA_API_SECRET_KEY=your_secret
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# DB / Cache
DATABASE_URL=postgresql+psycopg://trader:trader@localhost:5432/trader
REDIS_URL=redis://localhost:6379/0

# App
STRATEGY_ID=alpha_baseline
```

---

## Appendix E — FastAPI Execution Gateway (skeleton)

Below is an expanded, practical skeleton that captures the essential features implemented in the Execution Gateway used in this project. It intentionally shows the service-level guards you should include in a production-ish gateway: DRY_RUN mode, deterministic client_order_id for idempotency, webhook signature verification (when a secret is configured), Prometheus metrics mounting, and additional operational endpoints (kill-switch, TWAP slicing, /metrics).

```python
# apps/execution_gateway/main.py (simplified skeleton)
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app
from pydantic import BaseModel
import os, hashlib
from datetime import date

# Configuration from environment
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()  # required in prod when DRY_RUN=false
ALPACA_BASE = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

app = FastAPI(title="Execution Gateway")

# Mount Prometheus metrics at /metrics
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

class OrderIn(BaseModel):
    symbol: str
    side: str  # 'buy' or 'sell'
    qty: int
    order_type: str = "market"
    limit_price: float | None = None
    time_in_force: str = "day"


def deterministic_id(o: OrderIn, strategy_id: str = "alpha_baseline") -> str:
    # deterministic client_order_id (includes date so retries on same day dedupe)
    today = date.today().isoformat()
    raw = f"{o.symbol}|{o.side}|{o.qty}|{o.limit_price}|{strategy_id}|{today}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]

@app.post("/api/v1/orders")
async def place_order(o: OrderIn):
    client_order_id = deterministic_id(o)

    # Fail closed / safety checks would normally run here (kill-switch, circuit-breaker)

    # DRY_RUN mode logs to DB and returns a dry_run status instead of sending to Alpaca
    if DRY_RUN:
        # persist order to DB with status 'dry_run'
        return {"client_order_id": client_order_id, "status": "dry_run", "message": "Order logged (DRY_RUN mode)"}

    # Live submission path: build payload and submit to broker (idempotent via client_order_id)
    # On transient failures retry with backoff; on UniqueViolation return existing order (idempotent)
    try:
        # Submit to Alpaca using a wrapped client that handles retries
        # save order record in DB with broker_order_id and status
        return {"client_order_id": client_order_id, "status": "submitted", "broker_order_id": "..."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/orders/slice")
async def submit_sliced_order(request: Request):
    # TWAP order planner + scheduler integration
    # checks: scheduler available, kill-switch not engaged, idempotency via parent client_order_id
    return {"parent_order_id": "twap_parent_abc123", "slices": []}


@app.post("/api/v1/webhooks/orders")
async def order_webhook(request: Request):
    # When WEBHOOK_SECRET is configured, verify HMAC signature header to prevent spoofing
    # Parse payload, update order status and positions, append fills to order metadata
    # In production, a missing secret should be treated as a configuration error (see startup checks)
    if WEBHOOK_SECRET:
        signature = request.headers.get("X-Alpaca-Signature")
        # verify signature with constant-time compare
    # process webhook payload and persist fills (transactional with row-locks)
    return {"status": "ok"}


@app.get("/health")
async def health_check():
    # Return DB/Redis/Alpaca connectivity and safety subsystem status
    return {"service": "execution_gateway", "status": "healthy", "dry_run": DRY_RUN}

# Additional endpoints you will commonly want:
# - GET /api/v1/orders/{client_order_id}  (query order)
# - GET /api/v1/positions                 (current positions + P&L)
# - GET /api/v1/kill-switch/status        (kill-switch state)
# - POST /api/v1/kill-switch/engage       (operator engages kill-switch)
# - POST /api/v1/kill-switch/disengage    (operator disengages kill-switch)
```

Notes:
- The real gateway implemented in the repo includes additional safety guards (circuit breaker, position reservation), per‑slice scheduling, performance endpoints, and Prometheus metrics. Use this skeleton as a minimal, secure reference.
- Important operational env var: when DRY_RUN=false in non-dev environments, configure WEBHOOK_SECRET to enable webhook signature verification and prevent spoofed webhooks.

---

## Appendix F — Qlib Baseline (very small example)
... (rest of document unchanged)
