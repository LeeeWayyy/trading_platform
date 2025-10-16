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
- FastAPI service with `/orders`, `/positions`, `/health`.
- Idempotent submit with deterministic client_order_id.
- Webhook endpoint for fills/updates.

**Tech:** FastAPI, HTTPX/Requests, Pydantic, Postgres.

**See:** Appendix E for a runnable skeleton.

---

## Phase 7 — Risk Manager & Capital Allocation
**Goal:** Guardrails before orders go out; size positions sensibly.

**Learn:** Pre‑trade checks (notional limits, per‑symbol caps), portfolio constraints, drawdown stops.

**Deliverables:**
- Pre‑trade risk filter with configurable limits (per strategy & global).
- Allocator translating target weights → child orders.

**Tech:** Pydantic configs, simple optimization (or heuristic sizing).

**Starter rules:** See Appendix G.

---

## Phase 8 — Reconciler & Recovery
**Goal:** On restart or desync, reconcile internal state with broker and heal.

**Learn:** Snapshots, journals, idempotent cancel/resubmit.

**Deliverables:**
- Periodic snapshots of positions, open orders, P&L.
- Boot‑time resync routine (pull broker → diff → heal → unlock new orders).

**Tech:** Postgres snapshots table, "append‑only" event log (can start with DB table; later Kafka/Redpanda).

---

## Phase 9 — Observability (metrics, logs, alerts)
**Goal:** See what’s happening in real time and get paged on trouble.

**Learn:** Metrics, structured logs, SLOs.

**Deliverables:**
- Prometheus metrics endpoints on each service (`/metrics`).
- Grafana dashboards: latency, error rate, slippage, fill %.
- Alerts on: risk breach, data stale, webhook failures, reconcile mismatch.

**Tech:** Prometheus + Grafana, OpenTelemetry logs; Loki (optional).

---

## Phase 10 — Minimal Web Console (optional)
**Goal:** One place to view positions, P&L, risk limits, and toggle strategies.

**Tech option A:** Streamlit app (fast to build).

**Tech option B:** Next.js + FastAPI backend (more control).

**Deliverables:** Live positions, open orders, P&L by strategy, risk toggles.

---

## Phase 11 — Live Rollout & Safety Checklist
**Goal:** Move from paper → live with guardrails.

**Checklist:**
- ✅ Same code path for paper & live.
- ✅ Dry‑run for a week with **recorded** orders and broker **paper** env.
- ✅ Risk limits active (hard caps, blacklist, per‑symbol size).
- ✅ Circuit breakers (max daily loss, data freshness, broker error rate).
- ✅ On‑boot reconciler verified.
- ✅ Kill switch tested.
- ✅ Pager alerts wired.
- ✅ Read‑only credentials for dashboards; secrets in a secret manager.

**Graduated rollout:**
- Day 1–3: live with tiny notional (e.g., $100–$500), max 1–2 symbols.
- Day 4–7: expand symbols, still tiny size.
- Week 2+: step up caps gradually.

---

## 30‑60‑90 Roadmap
**0–30 days:** Phases 0–5; baseline strategy; paper trading + daily reports.

**31–60 days:** Phases 6–9; idempotent execution, risk/reconcile, dashboards; staged live go‑live.

**61–90 days:** Phase 10–11; web console polish; multi‑alpha allocator; advanced backtest realism.

---

## Appendix A — Suggested Folder Structure
```
qlib-alpaca-trader/
  apps/
    signal_service/
    execution_gateway/
    reconciler/
    risk_manager/
    web_console/  # optional
  strategies/
    alpha_baseline/
      features.py
      model.py
      pipeline.py
  infra/
    docker-compose.yml
    grafana/
    prometheus/
  db/
    migrations/
  libs/
    feature_store/
    common/
  data/  # parquet, duckdb catalogs
  artifacts/  # models, backtests
  tests/
  .env
  Makefile
```

---

## Appendix B — Postgres Schema (DDL)
```sql
-- orders
CREATE TABLE IF NOT EXISTS orders (
  client_order_id TEXT PRIMARY KEY,
  strategy_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  side TEXT CHECK (side IN ('buy','sell')) NOT NULL,
  qty NUMERIC NOT NULL,
  order_type TEXT DEFAULT 'market',
  limit_price NUMERIC,
  time_in_force TEXT DEFAULT 'day',
  status TEXT NOT NULL,
  broker_order_id TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol);

-- positions
CREATE TABLE IF NOT EXISTS positions (
  symbol TEXT PRIMARY KEY,
  qty NUMERIC NOT NULL,
  avg_price NUMERIC NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- snapshots (portfolio state)
CREATE TABLE IF NOT EXISTS snapshots (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL,
  nav NUMERIC,
  pnl NUMERIC,
  cash NUMERIC,
  details JSONB
);

-- events (append-only journal)
CREATE TABLE IF NOT EXISTS events (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ DEFAULT now(),
  kind TEXT NOT NULL, -- e.g., signal, order_submitted, fill, reconcile
  payload JSONB NOT NULL
);

-- risk_limits
CREATE TABLE IF NOT EXISTS risk_limits (
  id BIGSERIAL PRIMARY KEY,
  strategy_id TEXT NOT NULL,
  max_notional NUMERIC,
  max_pos_per_symbol NUMERIC,
  blacklist TEXT[] DEFAULT '{}',
  daily_loss_limit NUMERIC,
  enabled BOOLEAN DEFAULT TRUE
);
```

---

## Appendix C — `docker-compose.yml` (starter)
```yaml
version: "3.9"
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: trader
      POSTGRES_PASSWORD: trader
      POSTGRES_DB: trader
    ports: ["5432:5432"]
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: redis:7
    ports: ["6379:6379"]

  grafana:
    image: grafana/grafana:10.4.2
    ports: ["3000:3000"]
    depends_on: [postgres]

  prometheus:
    image: prom/prometheus:latest
    ports: ["9090:9090"]
    volumes:
      - ./infra/prometheus:/etc/prometheus

volumes:
  pgdata:
```

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
```python
# apps/execution_gateway/main.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os, time, hashlib
import httpx
from datetime import date

ALPACA_BASE = os.getenv("ALPACA_BASE_URL")
ALPACA_KEY = os.getenv("ALPACA_API_KEY_ID")
ALPACA_SECRET = os.getenv("ALPACA_API_SECRET_KEY")

app = FastAPI(title="Execution Gateway")

class OrderIn(BaseModel):
    symbol: str
    side: str  # 'buy' or 'sell'
    qty: float
    order_type: str = "market"
    limit_price: float | None = None
    time_in_force: str = "day"
    strategy_id: str = os.getenv("STRATEGY_ID", "alpha_baseline")


def deterministic_id(o: OrderIn) -> str:
    today = date.today().isoformat()
    raw = f"{o.symbol}|{o.side}|{o.qty}|{o.limit_price}|{o.strategy_id}|{today}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


@app.post("/orders")
async def place_order(o: OrderIn):
    client_order_id = deterministic_id(o)
    payload = {
        "symbol": o.symbol,
        "side": o.side,
        "qty": o.qty,
        "type": o.order_type,
        "time_in_force": o.time_in_force,
        **({"limit_price": o.limit_price} if o.limit_price else {}),
        "client_order_id": client_order_id,
    }
    headers = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}

    # Safe retry once on timeout; idempotent via client_order_id
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.post(f"{ALPACA_BASE}/v2/orders", json=payload, headers=headers)
            if r.status_code == 409:
                return {"status": "duplicate_ok", "client_order_id": client_order_id}
            r.raise_for_status()
            return r.json()
    except httpx.RequestError:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(f"{ALPACA_BASE}/v2/orders", json=payload, headers=headers)
            r.raise_for_status()
            return r.json()


@app.get("/health")
async def health():
    return {"ok": True}
```

---

## Appendix F — Qlib Baseline (very small example)
```python
# strategies/alpha_baseline/pipeline.py
import pandas as pd
import numpy as np

# Simple momentum signal (replace with Qlib constructs in real pipeline)
def momentum_signal(df: pd.DataFrame, lookback=20):
    # df has columns [symbol, date, close]
    df = df.sort_values(["symbol", "date"])  # ensure ordering
    df["ret"] = df.groupby("symbol")["close"].pct_change(lookback)
    df["score"] = df["ret"].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return df[["symbol", "date", "score"]]

# Convert scores to target weights (top-k long, bottom-k short)
def allocate(df_scores: pd.DataFrame, k=10):
    out = []
    for d, daydf in df_scores.groupby("date"):
        daydf = daydf.dropna(subset=["score"])
        top = daydf.nlargest(k, "score")
        bot = daydf.nsmallest(k, "score")
        w = []
        if not top.empty:
            w += [(r.symbol, +1.0/len(top)) for r in top.itertuples()]
        if not bot.empty:
            w += [(r.symbol, -1.0/len(bot)) for r in bot.itertuples()]
        out += [(d, s, wt) for s, wt in w]
    return pd.DataFrame(out, columns=["date", "symbol", "weight"]).sort_values(["date","symbol"])
```

---

## Appendix G — Risk Checks (starter)
```python
from dataclasses import dataclass

@dataclass
class RiskLimits:
    max_notional: float = 1000.0
    max_pos_per_symbol: float = 300.0
    blacklist: set[str] = None
    daily_loss_limit: float = 50.0


def pretrade_check(order, portfolio_value, limits: RiskLimits) -> tuple[bool, str]:
    if limits.blacklist and order.symbol in limits.blacklist:
        return False, "blacklisted"
    notional = (order.limit_price or order.mkt_price) * order.qty
    if notional > limits.max_notional:
        return False, "exceeds max_notional"
    if abs(order.target_pos_for_symbol) > limits.max_pos_per_symbol:
        return False, "exceeds per-symbol cap"
    if portfolio_value.drawdown_today < -limits.daily_loss_limit:
        return False, "daily loss limit"
    return True, "ok"
```

---

## Appendix H — Glossary (plain‑English)
- **Alpha**: Your model’s signal predicting future returns.
- **Slippage**: The difference between expected and actual execution price.
- **TWAP/VWAP**: Time/Volume‑weighted average price execution styles.
- **Idempotent**: You can safely retry the same request without unintended duplicates.
- **Client Order ID**: A unique ID you set per order; the broker uses it to dedupe.
- **Reconcile**: Compare your internal positions/orders with the broker’s and fix mismatches.
- **Circuit breaker**: Automatic rules that stop or reduce trading when conditions look dangerous.
- **Paper trading**: Simulated trading against a broker’s demo environment (real APIs, fake money).



---

# v2 Plan — Incorporating Trader A & B Feedback (Production-Ready)

Below is a revised, more explicit plan that folds in the excellent suggestions from both traders. It keeps the original phases but **adds new phases, deliverables, and concrete guardrails** so your live system is safe, debuggable, and auditable.

## v2 Table of Contents
1. Phase 0 — Prereqs & Environment (unchanged)
2. Phase 1 — Project Bootstrap & Repo Skeleton (unchanged)
3. Phase 2 — Data Layer (expanded): Corporate Actions, Quality Gates, Multi‑Source, Replayability, Survivorship‑Free Universe
4. Phase 3 — Qlib Research Pipeline (baseline) (unchanged scope)
5. Phase 4 — Strategy Packaging & Experiment Tracking (expanded Release Gates + MODEL_CARD)
6. Phase 4.5 — Backtesting Validation & Leakage Detection (new)
7. Phase 5 — Paper Trading Path + Walk‑Forward Automation (expanded)
8. Phase 6 — Execution Gateway + Smart Order Routing (Order Slicer)
9. Phase 7 — Risk Manager: Pre‑ & Post‑Trade + Circuit Breakers
10. Phase 7.5 — Multi‑Alpha Orchestration & Allocation (new)
11. Phase 8 — Reconciler: Drift Detection & Alerts (expanded)
12. Phase 8.5 — Scenario & Stress Testing (new)
13. Phase 9 — Observability: Attribution & Benchmarking (expanded)
14. Phase 10 — Web Console: Manual Overrides + Kill Switch (expanded)
15. Phase 11 — Live Rollout: Secrets Management & Brokerage Checks (expanded)
16. Phase 12 — Tax & Compliance Reporting (new)
17. Appendices I–O — Templates & Skeletons

---

## Phase 2 — Data Layer (expanded)
**Goal:** Trustworthy data for research/live with explicit corporate‑action handling, quality gates, multi‑source failover, replayability, and survivorship‑free universe.

**New Deliverables:**
- **Corporate Action Adjuster**: Adjust OHLCV for splits/dividends **before** writing Parquet.
- **Data Quality Ingestion Gate**: Validate outliers (>30% daily change w/o CA), staleness, schema; quarantine bad rows.
- **Primary + Fallback Feeds**: Pluggable sources (e.g., SourceA → SourceB → SourceC) with per‑source health checks.
- **Raw Tick/Bar Recorder**: Immutable Parquet logs (append‑only) of **raw** inputs with ingest timestamps.
- **Survivorship‑Bias‑Free Universe**: Table tracking tradability per symbol and date (listing/delist, halts).

**Tech:** Polars, DuckDB, Redis, Parquet; simple health checks (HTTP ping + freshness thresholds); S3/MinIO for raw archive.

**DDL additions:**
```sql
CREATE TABLE IF NOT EXISTS universe (
  symbol TEXT,
  start_date DATE,
  end_date DATE,
  tradable BOOLEAN DEFAULT TRUE,
  PRIMARY KEY(symbol, start_date)
);

CREATE TABLE IF NOT EXISTS quarantine (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ DEFAULT now(),
  reason TEXT,
  payload JSONB
);
```

**Pseudo‑code — Corporate Actions & Quality Gate:**
```python
# adjust_and_validate.py
from datetime import datetime
import polars as pl

CA = pl.DataFrame(  # corporate actions
    {"symbol": ["AAPL"], "date": ["2020-08-31"], "split": [4.0], "div": [0.0]}
)

RAW = pl.read_parquet("raw/2020-*.parquet")
DF = RAW.join(CA, on=["symbol","date"], how="left") \
       .with_columns([
          pl.col("split").fill_null(1.0),
          pl.col("div").fill_null(0.0)
       ])
# Adjust prices/volumes
DF = DF.with_columns([
  (pl.col("close")/pl.col("split") - pl.col("div")).alias("adj_close"),
  (pl.col("open")/pl.col("split")).alias("adj_open"),
  (pl.col("high")/pl.col("split")).alias("adj_high"),
  (pl.col("low")/pl.col("split")).alias("adj_low"),
  (pl.col("volume")*pl.col("split")).alias("adj_volume"),
])

# Quality checks
def is_outlier(df: pl.DataFrame) -> pl.Series:
    ret = df.sort(["symbol","date"]) \
            .group_by("symbol") \
            .agg(pl.col("adj_close").pct_change().alias("ret")).explode("ret")
    return ret["ret"].abs() > 0.30

mask_bad = is_outlier(DF) & DF["split"].eq(1.0)
QUAR = DF.filter(mask_bad)
GOOD = DF.filter(~mask_bad)

GOOD.write_parquet("parquet/ohlcv_adj/*.parquet")
QUAR.write_parquet("parquet/quarantine/*.parquet")
```

---

## Phase 4 — Strategy Packaging & Experiment Tracking (expanded)
**New Deliverable: MODEL_CARD.md generated per model**
- MLflow run ID (reproducibility), train/val/test ranges, metrics (Sharpe, DD, turnover), feature list/importance, drift notes, slippage assumptions.
- Store with the model artifact; gate promotion on thresholds.

**Appendix I** contains a ready‑to‑use template and a script to auto‑render from MLflow.

---

## Phase 4.5 — Backtesting Validation & Leakage Detection (new)
**Goal:** Prevent unrealistic backtests from reaching production.

**Deliverables:**
- **Look‑Ahead Bias Checks**: Assert features only use data available at the bar close you trade on; verify split of dates has no overlap.
- **Slippage Model Calibration**: Compare assumed vs realized slippage (historical executions); adjust.
- **Realistic Constraints**: Tick size/lot size, halts, borrow availability/costs.

**Output:** Validation report saved with each backtest; promotion blocked if failed.

---

## Phase 5 — Paper Trading + Walk‑Forward Automation (expanded)
**New Deliverable: Scheduled Retraining Pipeline**
- Weekly/monthly DAG (cron/Airflow): retrain → validate on holdout → auto‑generate MODEL_CARD → promote to **staging** if metrics pass → alert on degradation.

**Tech:** Airflow (or GitHub Actions + cron), MLflow, Qlib, Postgres for approvals.

---

## Phase 6 — Execution Gateway + Smart Order Routing
**New Deliverable: Order Slicer Module**
- Split large parent orders into **N** child slices (TWAP/VWAP‑ish), respect min lot/tick.
- Track parent↔child hierarchy; handle partial fills; configurable slice interval.

**Appendix K** includes a Python skeleton and DB columns.

---

## Phase 7 — Risk Manager: Pre‑ & Post‑Trade + Circuit Breakers
**Expanded Deliverables:**
- **Post‑Trade Monitor**: Continuously compute portfolio DD, realized vol, exposure; publish metrics.
- **Circuit Breakers** (state in Redis):
  - Trip on: intraday DD breach, broker reject/error rate spike, data staleness.
  - **SAFE mode**: block new entries; allow risk‑reducing exits; visible in UI.

**Appendix L** shows Redis flag schema and integration points.

---

## Phase 7.5 — Multi‑Alpha Orchestration & Allocation (new)
**Goal:** Blend multiple strategies and allocate capital with controls.

**Deliverables:**
- **Allocator Module**: mean‑variance, risk‑parity, or rank aggregation; per‑strategy caps.
- **Signal Blending**: combine alphas pre‑allocation.
- **Correlation Monitor**: alert on rising inter‑strategy correlation.

---

## Phase 8 — Reconciler (expanded)
**New Deliverable: Drift Detection & Alerts**
- Alert if |broker_pos − internal_pos| > N shares or $X; flag stale orders (>15m w/o update); page on repeated reconcile failures.

---

## Phase 8.5 — Scenario & Stress Testing (new)
**Goal:** Prove resilience under shocks and failures.

**Deliverables:**
- **Backtest Scenarios**: ×3 slippage, +50ms latency, ×2 fees.
- **Live Sim Scenarios** (paper env):
  - Data drop (stop ingestor for 5m) → should trip staleness breaker.
  - Broker 500s (inject in gateway) → should retry idempotently.
  - Position desync (edit DB) → reconciler heals on next cycle.

---

## Phase 9 — Observability: Attribution & Benchmarking (expanded)
**Deliverables:**
- **Daily Attribution Report**: P&L by strategy, factor bucket, and execution costs.
- **Live vs Backtest Dashboard**: realized Sharpe vs backtest, realized vs assumed slippage, turnover; alert on divergence.

**Appendix N** has a minimal schema + job outline.

---

## Phase 10 — Web Console (expanded)
**Features:** Manual order entry (with audit), per‑strategy enable/disable, **emergency kill switch** (cancel all, flatten, block new signals) with confirmation + logged reason, audit log viewer.

---

## Phase 11 — Live Rollout (expanded)
**Additions:**
- **Secrets Migrated**: Use Vault/AWS Secrets Manager/Doppler; remove creds from `.env`.
- **Brokerage Account Monitoring**: Verify buying power, margin, shorting eligibility, and supported order types; enforce market hours (and explicit extended‑hours policy).

---

## Phase 12 — Tax & Compliance Reporting (new)
**Deliverables:** Trade blotter CSV daily, tax lot tracking (FIFO/LIFO), realized P&L (short‑ vs long‑term), PDT monitoring, wash sale detection, position limit checks.

**Tech:** Postgres (lots table), scheduled report job; export CSVs for accounting.

---

## Updated 30‑60‑90
**0–30 days:** Phases 0–5 + data quality gates + corporate actions + raw recorder + survivorship universe + walk‑forward automation.

**31–60 days:** Phases 6–8 + order slicer + circuit breakers + multi‑alpha; start stress‑testing.

**61–90 days:** Phases 9–12 + attribution dashboards + tax reporting + disaster recovery docs; staged notional ramp.

---

# Appendices I–O (new)

## Appendix I — MODEL_CARD.md Template
```markdown
# Model Card — {{model_name}} (Run: {{mlflow_run_id}})
- Train Range: {{train_start}} → {{train_end}}
- Valid/Test Range: {{valid_start}} → {{test_end}}
- Metrics: Sharpe={{sharpe}}, MaxDD={{max_dd}}, Turnover={{turnover}}
- Features: {{feature_list}}
- Feature Importance: {{feature_importance_table}}
- Slippage Assumption: {{slippage_model}}
- Notes on Drift: {{drift_notes}}
- Promotion Decision: {{approved|rejected}} (reason)
```

## Appendix J — Data Quality Gate & Corporate Action Adjuster (ref impl)
- **Pipelines**: `raw → adjust → validate → parquet_adj`, with quarantine on failure.
- **Staleness rule**: reject bars if gap > N×expected interval.

## Appendix K — Order Slicer (skeleton)
```python
class OrderSlicer:
    def __init__(self, parent_id, qty, horizon_s, min_lot=1):
        self.parent_id = parent_id
        self.qty = qty
        self.min_lot = min_lot
        self.horizon_s = horizon_s

    def plan(self, now, n_slices=6):
        slice_qty = max(self.min_lot, int(self.qty / n_slices))
        schedule = [now + i*(self.horizon_s//n_slices) for i in range(n_slices)]
        return [(self.parent_id, i, slice_qty, t) for i, t in enumerate(schedule)]
```

## Appendix L — Circuit Breakers in Redis
```python
# redis keys
CB_KEY = "cb:state"            # values: OPEN|TRIPPED
CB_REASON = "cb:reason"        # last trigger reason
CB_UPDATED = "cb:ts"           # last change ts

# set breaker
r.set(CB_KEY, "TRIPPED")
r.set(CB_REASON, "drawdown")

# in signal/execution services
if r.get(CB_KEY) == b"TRIPPED":
    block_new_entries()
```

## Appendix M — Walk‑Forward Retraining (Airflow DAG sketch)
```python
with DAG("walk_forward", schedule="@weekly", start_date=..., catchup=False) as dag:
    t1 = PythonOperator(task_id="ingest_latest", ...)
    t2 = PythonOperator(task_id="train_model", ...)
    t3 = PythonOperator(task_id="validate_holdout", ...)
    t4 = PythonOperator(task_id="render_model_card", ...)
    t5 = BranchPythonOperator(task_id="promote_if_pass", ...)
    t1 >> t2 >> t3 >> [t4, t5]
```

## Appendix N — Attribution & Benchmarking
- **Tables**: `executions`, `positions`, `pnl_attribution (by strategy, factor, costs)`.
- Daily job produces CSV + chart; dashboard shows realized vs backtest metrics.

## Appendix O — Disaster Recovery Playbook (outline)
- **Trading hours outage**: Use broker UI to flatten; steps + contacts.
- **Data feed failure**: switch to fallback provider; how‑to.
- **DB restore**: from latest snapshot; commands.
- **Accidental position**: detect via reconciler alert; unwind procedure.
- **Model rollback**: criteria + exact commands to roll back model artifact.



---

# v2.1 — Lean, Prioritized Plan (Scope-Aware)

This addendum applies ruthless prioritization per your feedback so you can ship a reliable end‑to‑end system first, then harden/scale.

## Priority Bands
- **P0 (MVP Core)** — must have for a usable paper‑trading system.
- **P1 (Hardening & Automation)** — reliability, safety, observability.
- **P2 (Advanced/Scale)** — performance, sophistication, and nice‑to‑haves.

## 30‑60‑90 (Re‑sequenced & Realistic)

### 🎯 0–30 Days — P0: Core End‑to‑End
- **Phase 0–1:** Dev env + repo skeleton (done once).
- **Phase 2 (Core):** Data pipeline with **corporate‑action adjustments** + **quality gates**. Use a single provider to start; **no** survivorship or multi‑source yet.
- **Phase 3–4:** Baseline Qlib strategy + MLflow tracking; package as `strategies/alpha_baseline`.
- **Phase 5 (Manual):** Wire paper‑trading flow end‑to‑end; run **manually** via a single command.
- **Phase 6 (Core):** Idempotent execution gateway (no slicer yet).

**Success Criterion:** One command runs: ingest → features → signal → risk (basic) → **paper order** via Alpaca paper API → daily P&L report.

**Make target idea:**
```make
paper-run:  ## end-to-end manual run
	poetry run python pipelines/paper_run.py --date $$(date +%F)
```

### 🎯 31–60 Days — P1: Hardening, Safety & Automation
- **Phase 7:** Pre‑ & post‑trade risk; **Redis circuit breakers** (DD, broker error rate, data staleness).
- **Phase 8:** Reconciler + on‑boot recovery.
- **Phase 9:** Basic Prometheus/Grafana dashboards & alerts.
- **Phase 5 (Automation):** Walk‑forward retraining DAG (Airflow or cron) for the core pipeline.
- **Phase 8.5:** Stress tests (paper env) to validate breakers, retries, and recon.

### 🎯 61–90 Days — P2: Advanced Features
- **Phase 6 (Advanced):** Add **TWAP slicer** (keep VWAP out of scope for now).
- **Phase 7.5:** Multi‑alpha allocator using **rank aggregation** or **inverse‑vol weighting** first; defer mean‑variance/risk‑parity.
- **Phase 10–11:** Web console MVP + secrets manager for live rollout.
- **Phase 12:** Initial tax‑lot tracking & reports.
- **Phase 2 (Advanced):** Revisit **multi‑source** ingest and evaluate licensing a **survivorship‑bias‑free** dataset (Sharadar/Polygon/etc.).

---

## Data Sourcing Notes (Phase 2)
- Start with your current provider data; focus on **CA adjuster** + **quality gates**.
- Plan to **license survivorship‑free universes** later; building it solo is a long project.
- Keep raw (immutable) input parquet logs so you can **replay** later even before multi‑source is added.

---

## Model Promotion & Live Reload (Phase 5/Registry)
**Keep it simple**: use a single Postgres table as a lightweight **model registry** that the signal‑service polls.

**DDL:**
```sql
CREATE TABLE IF NOT EXISTS model_registry (
  id BIGSERIAL PRIMARY KEY,
  model_name TEXT NOT NULL,
  version TEXT NOT NULL,
  stage TEXT CHECK (stage IN ('staging','production','archived')) NOT NULL,
  artifact_uri TEXT NOT NULL,        -- e.g., file path or S3/MLflow URI
  metrics JSONB,                     -- sharpe, dd, etc.
  approved_by TEXT,
  approved_at TIMESTAMPTZ,
  UNIQUE(model_name, version)
);
-- convenience view: latest approved production model per name
CREATE OR REPLACE VIEW v_latest_prod AS
SELECT DISTINCT ON (model_name) model_name, version, artifact_uri
FROM model_registry
WHERE stage='production'
ORDER BY model_name, approved_at DESC;
```

**Signal‑service reload loop (pseudo‑code):**
```python
import time, hashlib
from db import fetch_latest_prod

loaded_sig = None
loaded_hash = None

while True:
    rec = fetch_latest_prod("alpha_baseline")  # SELECT * FROM v_latest_prod WHERE model_name=...
    h = hashlib.sha1((rec.version + rec.artifact_uri).encode()).hexdigest()
    if h != loaded_hash:
        model = load_model(rec.artifact_uri)
        loaded_sig = model
        loaded_hash = h
        logger.info("reloaded model %s@%s", rec.model_name, rec.version)
    time.sleep(15)  # poll; later upgrade to LISTEN/NOTIFY
```

**Airflow promotion step:** write a new row with `stage='production'`, bumping `approved_at`. The signal‑service auto‑reloads within seconds.

*(Nice later: use Postgres `LISTEN/NOTIFY` or MLflow Registry webhook instead of polling.)*

---

## Allocator (Phase 7.5) — Start Simple
- **Rank aggregation**: average normalized ranks across strategies, then map to weights.
- **Inverse volatility weighting**: compute 20‑day rolling vol per strategy and set weight ∝ 1/vol.
- Defer **mean‑variance** and **risk‑parity** until you have stable covariance estimates and more telemetry.

---

## Execution (Phase 6) — TWAP First
- Implement **TWAP slicer** only (fixed intervals). Skip VWAP until you can model intraday volume distribution.
- Keep parent→child tracking in DB now so VWAP becomes a drop‑in later.

---

## P0 Backlog (Ticket‑sized)
1. **ETL**: raw→adjust→validate→parquet (CA adjuster + quality gate + quarantine).
2. **Strategy**: baseline Qlib pipeline + MLflow logging.
3. **Signal service**: load model, compute target weights, publish intents.
4. **Risk (basic)**: per‑symbol and total notional caps.
5. **Exec gateway**: Alpaca orders with **client_order_id** idempotency + `/health`.
6. **Paper runner**: `pipelines/paper_run.py` orchestrator, produces blotter + P&L.

**Definition of Done (P0):** successful paper trades for a day, no crashes, reproducible runs.

---

## P1 Backlog (Safety/Automation)
- Redis circuit breakers; post‑trade monitor; reconciler + boot recovery; Prometheus metrics; Airflow weekly retrain + promotion; stress‑tests in paper.

## P2 Backlog (Advanced)
- TWAP slicer; allocator (rank/inv‑vol); web console + secrets mgmt; tax lots; multi‑source ingest; survivorship‑free licensed dataset.



---

# v2.2 — Final, Scope‑Aware Plan (All Changes Included)

This version integrates the final suggestions: **data freshness check, dry‑run gateway flag, minimal position tracker, circuit‑breaker recovery logic, backtest replay test, CLI ops tools, symbol‑universe config, stale‑order cleanup**, plus a **reality‑checked 0‑45 / 46‑90 / 91‑120** timeline and ultra‑minimal P0 schemas.

## P0 Additions (Core, 0–45 days)

### 1) Data Freshness Check (Phase 2 core)
Add an explicit freshness guard to the **quality gate** before writing adjusted bars:
```python
from datetime import datetime, timezone
import polars as pl

class StalenessError(Exception):
    ...

def check_freshness(df: pl.DataFrame, max_age_minutes: int = 30):
    # df must include a UTC timestamp column named 'timestamp'
    latest = df["timestamp"].max()
    now = datetime.now(timezone.utc)
    age = (now - latest).total_seconds()
    if age > max_age_minutes * 60:
        raise StalenessError(f"Data is {age/60:.1f}m old, exceeds {max_age_minutes}m")
```
Wire `check_freshness` into the ETL just after CA‑adjustment and before quality gate writes.

### 2) Dry‑Run Mode in Execution Gateway (Phase 6 core)
Start safer: prove the end‑to‑end without touching Alpaca, then flip **DRY_RUN=false**.
```python
# apps/execution_gateway/main.py
import os, logging
logger = logging.getLogger(__name__)

DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

@app.post("/orders")
async def place_order(o: OrderIn):
    if DRY_RUN:
        logger.info("DRY_RUN: would submit %s", o)
        return {"status": "dry_run", "order": o.model_dump()}
    # ... actual Alpaca call (unchanged)
```

### 3) Minimal Position Tracker (Phase 6 core)
Track fills → positions so sizing & risk know exposure.
- **On fill webhook**: upsert `(symbol, qty, avg_price)`.
- **Use positions table in risk check** before creating new orders.

_P0 schema provided in Appendix Q._

### 4) Symbol Universe Config (Phase 3/Signal service)
Constrain MVP to a tiny, explicit list.
```python
# config/universe.py
TRADABLE_SYMBOLS = ["AAPL", "MSFT", "GOOGL"]

def filter_universe(df):
    return df.filter(pl.col("symbol").is_in(TRADABLE_SYMBOLS))
```
Apply immediately after generating candidate signals.

### 5) Makefile CLI for Ops (replacing early web console)
Fast operational control without UI:
```make
status:  ## positions, open orders, P&L today
	poetry run python cli/status.py

circuit-trip: ## manually trip breaker
	poetry run python cli/circuit.py --trip

kill-switch: ## cancel all, flatten, block new signals
	poetry run python cli/kill.py
```

### 6) Reality‑Checked Timeline for a Solo Developer
- **0–45 days (P0):** Phases 0–6 core → one successful **paper** day with real Alpaca paper API.
- **46–90 days (P1):** Phases 7–9 + reconciler + stress tests → two weeks autonomous paper trading, breakers proven.
- **91–120 days (P2 + Live):** Allocator (rank/inv‑vol), secrets, (optional) web console, graduated live rollout with tiny notional.


## P1 Additions (Hardening & Automation, 46–90 days)

### 7) Circuit Breaker Recovery Procedure (Phase 7)
Prevent “stuck SAFE mode.”
```python
# recovery.py
from datetime import datetime, timezone, timedelta

def check_recovery(r, quiet_minutes=10):
    if r.get("cb:state") != b"TRIPPED":
        return
    # your all_clear() computes DD < thresh, data fresh, broker errors normal
    if all_clear() and manual_approval_exists():
        last = r.get("cb:ts")
        if last and (datetime.now(timezone.utc) - parse_ts(last)) > timedelta(minutes=quiet_minutes):
            r.set("cb:state", "OPEN")
            r.set("cb:reason", "")
```
Expose a CLI to set/clear manual approval.

### 8) Stale‑Order Cleanup (Phase 6/7)
Background job every 5 minutes: find `status in ('new','accepted','partially_filled')` and `age > 15m` → attempt cancel via Alpaca, log anomaly.

### 9) Backtest Replay Test (Phase 8.5)
- Take one day’s **raw** parquet from paper trading.
- Replay through prod code path (signal→risk→exec in DRY_RUN).
- Assert: same signals & orders (allow tiny timestamp deltas), write a parity report.


## P2 Notes (91–120 days)
- **Execution:** TWAP slicer only; VWAP deferred.
- **Allocator:** start with **rank aggregation** or **inverse volatility**; defer MVO/risk‑parity until covariances stabilize.
- **Web Console:** push after live rollout; use CLI in P1.


## Updated Roadmap (Consolidated)
- **P0 (0–45):** core ETL (CA + quality + freshness), baseline model + MLflow, manual paper run, idempotent gateway with DRY_RUN, minimal positions, symbol universe, CLI ops.
- **P1 (46–90):** risk (pre/post) + breakers + recovery, reconciler + boot resync, stale order cleanup, observability (Prom/Grafana), walk‑forward automation, backtest replay tests.
- **P2 (91–120):** TWAP slicer, simple allocator, secrets manager, optional web console, tax lots, live ramp.


## Appendices (new)

### Appendix P — **P0 Acceptance Criteria**
```markdown
## P0 Done When:
- [ ] Raw data ingested with corporate action adjustments
- [ ] Quality gate rejects outliers, logs to quarantine
- [ ] Freshness check blocks stale data
- [ ] Baseline strategy trains, logs to MLflow
- [ ] Signal service loads model, emits weights (filtered to symbol universe)
- [ ] Risk check blocks oversized orders using positions
- [ ] Execution gateway (DRY_RUN=false) submits to Alpaca paper API
- [ ] Orders fill (verified in Alpaca dashboard)
- [ ] Daily P&L report generated
- [ ] Can replay yesterday's run with same results
```

### Appendix Q — **P0 Minimal Schemas**
```sql
-- P0 only needs these 3 tables
CREATE TABLE positions (
  symbol TEXT PRIMARY KEY,
  qty NUMERIC,
  avg_price NUMERIC
);

CREATE TABLE orders (
  client_order_id TEXT PRIMARY KEY,
  symbol TEXT,
  qty NUMERIC,
  status TEXT,
  broker_order_id TEXT
);

CREATE TABLE pnl (
  date DATE PRIMARY KEY,
  realized NUMERIC,
  unrealized NUMERIC
);
```

> Defer `snapshots`, `events`, `risk_limits` to P1 as previously outlined.

---

**Discipline reminders** (from Critical Success Factors):
- Timebox ruthlessly (≤3 days per P0 task); ship ugly, refactor in P1.
- Hit the **real paper API by day 15** to discover real‑world quirks.
- Keep a running **DECISIONS.md**.
- One strategy first; allocator later.
- Spend **~40% on data quality**; bad data breaks everything.

