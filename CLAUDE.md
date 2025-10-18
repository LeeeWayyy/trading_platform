# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **Qlib + Alpaca trading platform** designed for algorithmic trading. The system produces signals using Qlib-based models and executes trades via Alpaca's API with emphasis on safety, idempotency, and parity between research and production.

**Key principles:**
- Idempotency first for all order paths
- Feature parity: research and production feature definitions must share code
- Never produce duplicate orders
- Circuit breakers override signals
- **Every architectural change requires an ADR** (see `/docs/STANDARDS/ADR_GUIDE.md`)
- **All code must be educational and well-documented for learning** (see `/docs/STANDARDS/DOCUMENTATION_STANDARDS.md`)

## Repository Structure

- `apps/` — Microservices (FastAPI-based)
  - `signal_service/` — Loads models from registry, emits target weights
  - `execution_gateway/` — Alpaca client with idempotent orders, dry-run flag, webhooks
  - `reconciler/` — Compares DB vs broker state, heals drift
  - `risk_manager/` — Pre/post-trade checks, circuit breaker integration
  - `cli/` — Operational scripts (status, breakers, kill switch)
- `strategies/` — Strategy implementations
  - `alpha_baseline/` — Features, model, and pipeline (Qlib-based)
- `infra/` — Infrastructure configs (docker-compose, prometheus, grafana)
- `db/` — Database migrations (Alembic or SQL)
- `libs/` — Shared libraries
  - `feature_store/` — Feature computation and storage
  - `common/` — Shared utilities and models
- `docs/` — Documentation and specifications
  - `INDEX.md` — **START HERE**: Canonical entry point for all documentation
  - `AI_GUIDE.md` — Quick-start guide for AI assistants
  - `STANDARDS/` — **Normative guidance (MUST follow)**
    - `CODING_STANDARDS.md` — Python style, type hints, error handling
    - `DOCUMENTATION_STANDARDS.md` — Docstring and comment requirements
    - `GIT_WORKFLOW.md` — Commit messages, PR process
    - `TESTING.md` — Test structure, coverage requirements
    - `ADR_GUIDE.md` — When/how to write ADRs
  - `GETTING_STARTED/` — Setup and orientation
    - `SETUP.md` — Environment setup
    - `TESTING_SETUP.md` — Test environment configuration
    - `PROJECT_STATUS.md` — Current implementation status
    - `REPO_MAP.md` — Directory structure and module ownership
    - `GLOSSARY.md` — Trading and ML terminology
  - `API/*.openapi.yaml` — API contracts (strict, require ADR for changes)
  - `DB/*.sql` — Database schemas (strict)
  - `TASKS/*.md` — Ticket backlog with acceptance criteria
  - `ADRs/` — Architecture Decision Records (REQUIRED)
  - `RUNBOOKS/` — Operational procedures
  - `CONCEPTS/` — Trading concepts explained for beginners
  - `IMPLEMENTATION_GUIDES/` — Step-by-step implementation details
  - `LESSONS_LEARNED/` — Post-implementation retrospectives
- `prompts/` — Guidance for AI coding tools
- `data/` — Parquet files, DuckDB catalogs
- `artifacts/` — Models, backtests, reports
- `tests/` — Test suite

## Common Commands

### Development
```bash
make up          # Start infrastructure (Postgres, Redis, Grafana, Prometheus)
make down        # Stop infrastructure
make fmt         # Format code (black + ruff)
make lint        # Run linters (mypy, ruff)
make test        # Run test suite
```

### Paper Trading
```bash
make paper-run   # Execute end-to-end paper trading for today
```

### Operations
```bash
make status       # Check positions, open orders, P&L
make circuit-trip # Manually trip circuit breaker
make kill-switch  # Cancel all orders, flatten positions, block new signals
```

### Git & Pull Requests
```bash
git checkout -b feature/task-name     # Create feature branch
git commit -m "Progressive commit"    # Commit often (every 30-60 min)
git push -u origin feature/task-name  # Push regularly
gh pr create                          # Create pull request
gh pr comment <PR> --body "@codex"    # Request automated review
```

**IMPORTANT:** See `/docs/STANDARDS/GIT_WORKFLOW.md` for:
- Progressive committing requirements (commit every 30-60 min)
- PR creation workflow and templates
- Codex review requirements (MUST @codex on all PRs)
- Branch naming conventions

## Development Workflow

### Source of Truth (in order of priority)

**📖 First Time? Start Here:**
1. `/docs/INDEX.md` — **Canonical entry point** with reading order for all docs
2. `/docs/AI_GUIDE.md` — Quick-start workflow for AI assistants

**🎯 Normative Standards (MUST follow):**
3. `/docs/STANDARDS/CODING_STANDARDS.md` — Python style, patterns, error handling
4. `/docs/STANDARDS/DOCUMENTATION_STANDARDS.md` — Docstring requirements
5. `/docs/STANDARDS/GIT_WORKFLOW.md` — Commit messages, PR process
6. `/docs/STANDARDS/TESTING.md` — Test structure, coverage requirements
7. `/docs/STANDARDS/ADR_GUIDE.md` — When/how to write ADRs

**📋 Project Context:**
8. `/docs/GETTING_STARTED/REPO_MAP.md` — Directory purpose and module ownership
9. `/docs/GETTING_STARTED/GLOSSARY.md` — Project-specific terminology
10. `/docs/GETTING_STARTED/PROJECT_STATUS.md` — Current implementation status

**🔧 Implementation References:**
11. `/docs/API/*.openapi.yaml` — API contracts (strict)
12. `/docs/DB/*.sql` — Database schemas (strict)
13. `/docs/TASKS/*.md` — Ticket backlog and acceptance criteria
14. `/docs/ADRs/*.md` — All architectural decisions (MANDATORY)
15. `/docs/CONCEPTS/*.md` — Trading concepts for learning
16. `/docs/IMPLEMENTATION_GUIDES/*.md` — Detailed implementation steps

### Implementation Process (STRICT ORDER)

1. **Pre-Implementation Phase**
   - Read ticket in `/docs/TASKS/`
   - If architectural change needed → Create ADR FIRST (see `/docs/STANDARDS/ADR_GUIDE.md`)
   - If trading concept involved → Document in `/docs/CONCEPTS/` FIRST

2. **Documentation Phase**
   - Create/update implementation guide in `/docs/IMPLEMENTATION_GUIDES/`
   - Document expected behavior, edge cases, examples

3. **Test-Driven Development Phase**
   - Write tests per `/docs/STANDARDS/TESTING.md` (TDD: red first)
   - Run `make test` → should FAIL (red)

4. **Implementation Phase**
   - Implement code following `/docs/STANDARDS/DOCUMENTATION_STANDARDS.md`
   - Add comprehensive docstrings (see standards doc for examples)
   - Implement only what's in the ticket (no scope creep)

5. **Validation Phase**
   - Run `make test` → should PASS (green)
   - Run `make lint` → should PASS
   - Manual testing in DRY_RUN mode

6. **Documentation Update Phase**
   - Update GETTING_STARTED/REPO_MAP.md if structure changed
   - Update OpenAPI specs if API changed
   - Update DB schemas if database changed
   - Add lessons learned to `/docs/LESSONS_LEARNED/`

7. **Pull Request Phase**
   - Reference ADR if applicable
   - Include checklist from `/docs/STANDARDS/TESTING.md`
   - Describe educational value

### Decision-Making
- **No silent scope changes** — If ticket lacks detail, propose ADR or add TODOs with rationale
- Never change OpenAPI contracts without PR + ADR
- Do not create new services; extend existing ones
- If architectural change needed, see `/docs/STANDARDS/ADR_GUIDE.md`

## Documentation Requirements

### **CRITICAL: All functions must follow strict documentation standards**

See `/docs/STANDARDS/DOCUMENTATION_STANDARDS.md` for comprehensive examples and requirements.

**Quick reference:**
- Every function MUST have Google-style docstring with examples
- Complex logic MUST have inline comments explaining "why"
- Trading concepts MUST be documented in `/docs/CONCEPTS/` before implementation
- Implementation steps MUST be documented in `/docs/IMPLEMENTATION_GUIDES/`

**Example minimum standard:**
```python
def compute_momentum(df: pl.DataFrame, lookback: int = 20) -> pl.DataFrame:
    """
    Calculate momentum signal based on percentage change.

    Momentum is a trend-following indicator that measures rate of change
    in price. Positive = uptrend, negative = downtrend.

    Args:
        df: DataFrame with [symbol, date, close]. Must be sorted.
        lookback: Periods for calculation. Default 20 (1 trading month).

    Returns:
        DataFrame with 'momentum' column. Range -1.0 (100% drop) to infinity.

    Raises:
        ValueError: If required columns missing or lookback < 1

    Example:
        >>> df = pl.DataFrame({"symbol": ["AAPL"], "close": [150, 153]})
        >>> compute_momentum(df, lookback=1)["momentum"].to_list()
        [None, 0.02]  # 2% gain

    Notes:
        - First lookback rows = null
        - Handles symbol groups independently
        - Replaces inf with null (div by zero protection)

    See Also:
        - /docs/CONCEPTS/momentum-signals.md
    """
    # Implementation with inline comments...
```

## Code Architecture

### Multi-Service Design
Microservices pattern with FastAPI communicating via:
- Redis Streams/pub-sub for events
- Postgres for persistent state (orders, positions, snapshots)
- Redis for circuit breaker state and online features

### Key Data Flows

**Signal Generation:**
```
Model Registry → Signal Service → Target Weights → Risk Manager → Execution Gateway
```

**Order Execution:**
```
Execution Gateway → Alpaca API → Webhook → Position Tracker → Reconciler
```

**Circuit Breaker:**
```
Post-Trade Monitor → Redis Breaker State → All Services Check Before Action
```

### Idempotency Pattern
Deterministic `client_order_id` generation:
```python
client_order_id = hash(symbol + side + qty + price + strategy + date)[:24]
```
Safe retries without duplicates. Alpaca returns 409 for duplicates.

### Model Registry & Hot Reload
Signal service polls `model_registry` table:
```sql
SELECT * FROM v_latest_prod WHERE model_name = 'alpha_baseline'
```
Reloads on version change within seconds.

### Circuit Breakers
Trip on: drawdown breach, broker errors, data staleness (>30min)

**When TRIPPED:**
- Block new entries
- Allow risk-reducing exits only
- Recovery requires: conditions normalized + manual approval + quiet period

### Reconciliation
Boot-time and periodic:
1. Pull broker positions/orders
2. Diff against DB
3. Heal discrepancies (cancel stale >15m, adjust positions)
4. Alert on failures
5. Unlock after successful reconciliation

## Coding Standards

**Language:** Python 3.11, type hints required, `mypy --strict` must pass

**Key patterns:**
- Pydantic for all config/models
- Structured logging (JSON) with `strategy_id`, `client_order_id`
- Never swallow exceptions
- Always UTC timezone-aware timestamps
- Parameterized queries only (no SQL injection)
- Migrations for schema changes (Alembic)
- Async FastAPI + httpx for HTTP

**See `/docs/STANDARDS/CODING_STANDARDS.md` for detailed patterns**

## Testing Strategy

### Test Pyramid
- **Unit:** Pure functions, features, allocators (many, fast)
- **Integration:** API endpoints, database workflows (some, medium)
- **E2E:** Full paper run, backtest replay (few, slow)

### Must-Haves
- Reproducible `paper_run` for any date (backtest parity)
- Circuit breaker tests (trip + recovery)
- Stale order cleanup test (>15m → cancel)
- Backtest replay test (same signals from saved data)

**See `/docs/STANDARDS/TESTING.md` for detailed requirements**

## Environment Modes

- **DRY_RUN=true** (default): Logs orders, doesn't submit to broker
- **Paper Trading:** `DRY_RUN=false` + paper API (real API, fake money)
- **Live Trading:** Live API credentials (graduated rollout required)

## Data Quality & Freshness

- **Corporate actions:** Adjust OHLCV for splits/dividends before storage
- **Quality gates:** Reject outliers (>30% change w/o CA), quarantine bad data
- **Freshness:** Must be <30min old, trips breaker if stale
- **Survivorship bias:** `universe` table tracks tradability per symbol/date

## Critical Patterns

### Symbol Universe Filtering
```python
TRADABLE_SYMBOLS = ["AAPL", "MSFT", "GOOGL"]  # MVP: explicit list

def filter_universe(df):
    return df.filter(pl.col("symbol").is_in(TRADABLE_SYMBOLS))
```

### Order Placement
```python
# Always check breaker first
if redis.get("cb:state") == b"TRIPPED":
    raise CircuitBreakerTripped()

# Always check risk
if abs(current_pos + order.qty) > limits.max_pos_per_symbol:
    raise RiskViolation()
```

### Feature Parity
```python
# strategies/alpha_baseline/features.py
def compute_features(df: pl.DataFrame) -> pl.DataFrame:
    """Shared by offline research AND online signal service."""
    # Never duplicate this logic
```

## Operational Guardrails

### Pre-Trade Checks
- Per-symbol position limits
- Total notional limits
- Blacklist enforcement
- Daily loss limits

### Post-Trade Monitoring
- Continuous drawdown calculation
- Realized volatility tracking
- Exposure monitoring
- Metrics to Prometheus

### Stale Order Cleanup
Background job every 5min: cancel orders in `('new', 'accepted', 'partially_filled')` with age >15m

**See `/docs/RUNBOOKS/ops.md` for recovery procedures**

## Development Priorities

### P0 (MVP, 0-45 days)
- Data ETL (CA, quality gates, freshness)
- Baseline Qlib strategy + MLflow
- Signal service + model registry
- Idempotent execution gateway + DRY_RUN
- Position tracker
- Basic risk checks
- CLI tools
- One-command paper trading

### P1 (Hardening, 46-90 days)
- Circuit breakers + recovery
- Reconciler + boot resync
- Stale order cleanup
- Prometheus/Grafana
- Walk-forward automation
- Backtest replay validation
- Stress testing

### P2 (Advanced, 91-120 days)
- TWAP slicer
- Multi-alpha allocator
- Secrets management
- Web console
- Tax tracking
- Live rollout

**See `/docs/TASKS/P0_TICKETS.md` for detailed tickets**

## Key Terminology

See `/docs/GETTING_STARTED/GLOSSARY.md` for full definitions:

- **Alpha** — Model signal predicting returns
- **Circuit Breaker** — Safety mechanism (OPEN/TRIPPED)
- **Client Order ID** — Unique ID for idempotent submissions
- **Dry-Run** — Simulated orders (no broker submission)
- **Idempotent** — Safe to retry without side effects
- **Paper Trading** — Real APIs, simulated money
- **Reconciler** — Syncs DB vs broker state
- **TWAP** — Time-Weighted Average Price execution

## Anti-Patterns to Avoid

- **No duplicate feature logic** — Share code between research/production
- **No in-memory state** — Use DB for positions/orders/breakers
- **No silent failures** — Always log and raise with context
- **No scattered configs** — Centralize in Pydantic Settings
- **No unvalidated data** — Quality gate before storage
- **No untested order paths** — Require backtest replay parity
- **No live without paper** — Paper validation required first

## When Making Changes

1. Check existing docs (GETTING_STARTED/REPO_MAP, API specs, DB schemas)
2. **Propose ADR for architectural changes** (see `/docs/STANDARDS/ADR_GUIDE.md`)
3. **Document trading concepts** (see `/docs/STANDARDS/DOCUMENTATION_STANDARDS.md`)
4. Write tests first (TDD enforced)
5. Add comprehensive docstrings (see standards doc)
6. Update all affected docs
7. Run `make test && make lint`
8. Follow PR checklist from `/docs/STANDARDS/TESTING.md`

## Essential Documentation

**📖 First Time? Start Here:**
1. `/docs/INDEX.md` — **Canonical entry point** with complete navigation guide
2. `/docs/AI_GUIDE.md` — Quick-start workflow for AI assistants

**⚠️ Before coding anything, read these STANDARDS (MUST follow):**
1. `/docs/STANDARDS/ADR_GUIDE.md` — When and how to write ADRs (MANDATORY for arch changes)
2. `/docs/STANDARDS/DOCUMENTATION_STANDARDS.md` — Docstring requirements with examples
3. `/docs/STANDARDS/CODING_STANDARDS.md` — Python patterns and standards
4. `/docs/STANDARDS/TESTING.md` — Test pyramid and requirements
5. `/docs/STANDARDS/GIT_WORKFLOW.md` — Commit messages and PR process

**During implementation:**
- Create concept docs in `/docs/CONCEPTS/` for trading-specific features
- Create implementation guides in `/docs/IMPLEMENTATION_GUIDES/` from tickets
- Follow docstring examples in STANDARDS/DOCUMENTATION_STANDARDS.md

**Additional resources:**
- Project status: `/docs/GETTING_STARTED/PROJECT_STATUS.md`
- Implementation plan: `/docs/trading_platform_realization_plan.md`
- AI assistant rules: `prompts/assistant_rules.md`
- Execution Gateway API: `docs/API/execution_gateway.openapi.yaml`
