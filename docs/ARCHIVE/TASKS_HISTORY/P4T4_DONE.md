# P4T4: Phase 5 - Backtest Enhancement

**Task ID:** P4T4
**Phase:** P4 (Advanced Features & Research Infrastructure)
**Timeline:** Phase 3 - Backtest & Core UI (Weeks 10-13)
**Priority:** P0 - Enhanced backtesting with UI and advanced features
**Estimated Effort:** 18-23 days (6 subtasks)
**Status:** ✅ Complete
**Created:** 2025-12-09
**Last Updated:** 2025-12-09

---

## Progress Tracker

| Task | Status | PR | Notes |
|------|--------|-----|-------|
| [T5.1 Job Queue](./P4T4_5.1_DONE.md) | ✅ Complete | #78 | Redis + RQ queue |
| [T5.2 Result Storage](./P4T4_5.2_DONE.md) | ✅ Complete | #80 | Postgres schema |
| [T5.3 Web UI](./P4T4_5.3_DONE.md) | ✅ Complete | #92 | Depends on T6.1 Auth |
| [T5.4 Walk-Forward](./P4T4_5.4_DONE.md) | ✅ Complete | #81 | |
| [T5.5 Monte Carlo](./P4T4_5.5_DONE.md) | ✅ Complete | #85 | |
| [T5.6 Regression Harness](./P4T4_5.6_DONE.md) | ✅ Complete | #86 | |

**Progress:** 6/6 tasks complete (100%)

---

## Executive Summary

Track 5 builds enhanced backtesting infrastructure with web UI and advanced features. This track addresses the critical need for:

1. **Non-blocking backtest execution** via Redis-based job queue
2. **Persistent result storage** for analysis and comparison
3. **Web-based backtest UI** for non-technical users
4. **Advanced analytics** (walk-forward optimization, Monte Carlo simulation)
5. **Regression harness** to prevent strategy drift

**Goal:** Production-ready backtesting platform with web UI, job queue, and advanced analytics.

**Key Deliverables:**
- Redis-based Job Queue (T5.1)
- Backtest Result Storage in Postgres (T5.2)
- Streamlit Web UI for Backtest (T5.3)
- Walk-Forward Optimization Framework (T5.4)
- Monte Carlo Simulation (T5.5)
- Backtest Regression Harness (T5.6)

**Dependencies from Previous Phases:**
- T1.6 Dataset Versioning (P4T1 - COMPLETE)
- T2.5 Alpha Research Framework (P4T2 - COMPLETE) - `libs/trading/alpha/research_platform.py`
- T6.1 Auth/RBAC (Track 6 - Pending) - Required for T5.3 Web UI

**Infrastructure Preconditions:**
- **Redis:** Version 6.0+ with persistence enabled (existing `libs/redis_client/`)
- **PostgreSQL:** Version 13+ recommended. Note: `gen_random_uuid()` is available in PG 13+ core BUT requires pgcrypto extension on PG 12 and some cloud providers. The migration includes a guard to create pgcrypto if not present.
- **DB Migration Ordering:** T5.2 migration must run before T5.3 deployment
- **Docker Services:** New `backtest_worker` service required (see Infrastructure section)
- **Shared Volume:** `backtest_data` volume shared between `web_console` and `backtest_worker`

**Existing Infrastructure to Build Upon:**
- `libs/trading/alpha/research_platform.py` - PITBacktester and BacktestResult
- `libs/redis_client/` - Redis client infrastructure
- `apps/web_console/` - Streamlit-based web console with auth
- `libs/data_quality/versioning.py` - DatasetVersionManager for reproducibility
- `libs/alpha/metrics.py` - AlphaMetricsAdapter (Qlib optional)

---

## Qlib Integration Strategy

### Reuse Matrix

| Component | Decision | Rationale |
|-----------|----------|-----------|
| `qlib.contrib.evaluate` | **REUSE (optional)** | Metrics/plots (IC/RankIC/ICIR, risk_analysis) mature; safe on PITBacktester outputs after Polars→Pandas |
| `qlib.contrib.report` | **REUSE (optional)** | Performance charts for offline reports; export PNG/HTML artifacts |
| `qlib.workflow.rolling` | **AVOID** | Assumes Qlib DataHandler; breaks PIT snapshot guarantees and Polars-first path |
| `qlib.backtest` | **AVOID** | Coupled to Qlib data loader; our PITBacktester already covers needs |
| `qlib.contrib.strategy` | **AVOID** | Tied to Qlib order simulator; redundant with our execution/risk stack |
| `qlib.data.cache` | **REUSE pattern** | DiskExpressionCache pattern already adopted per ADR-0022 |

### Integration Principles

1. **PITBacktester is authoritative** - All backtest execution goes through our `PITBacktester`, never Qlib's backtest engine
2. **Qlib as optional post-processor** - Use Qlib metrics/reports only on PITBacktester outputs after Polars→Pandas conversion
3. **Graceful degradation** - All Qlib features gated behind `try/except ImportError`; fallback to local implementations
4. **No data-plane replacement** - Never use Qlib DataHandler/Dataset; preserve DatasetVersionManager PIT guarantees

### Optional Qlib Report Export

When Qlib is installed, export additional analysis artifacts:
```
data/backtest_results/{job_id}/
├── daily_signals.parquet
├── daily_weights.parquet
├── qlib_reports/              # Optional, when qlib installed
│   ├── ic_analysis.json
│   ├── risk_analysis.json
│   └── performance_chart.png
```

Graceful Qlib import pattern (fallback when not installed):
```python
import structlog

try:
    from qlib.contrib import evaluate as qlib_evaluate
except ImportError:
    qlib_evaluate = None  # degrade gracefully; core flow must still succeed

# Partial import guard: modules can fail independently; warn but keep core flow alive
logger = structlog.get_logger(__name__)
try:
    from qlib.contrib import report as qlib_report
except Exception as exc:  # noqa: BLE001
    qlib_report = None
    logger.warning(
        "qlib_partial_import",
        evaluate_loaded=qlib_evaluate is not None,
        report_loaded=False,
        error=repr(exc),
    )
```

---

## Architecture Overview

### Docker Infrastructure

**Worker Image Decision:** Create `apps/backtest_worker/Dockerfile` following the established multi-stage build pattern from `apps/web_console/Dockerfile`. The worker image will:
- Use `python:3.11-slim` base image (consistent with other services)
- Install runtime dependencies (postgresql-client, curl for healthchecks)
- Copy `libs/` for shared code access
- Run as non-root `appuser` (UID 1000) for security
- Entrypoint: `rq worker` command with queue names

**Worker Dockerfile (apps/backtest_worker/Dockerfile):**
```dockerfile
# Backtest Worker Dockerfile - Multi-stage Build
FROM python:3.11-slim as builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*
COPY apps/backtest_worker/requirements.txt .
RUN pip install --no-cache-dir --target=/build/packages -r requirements.txt

FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client curl && rm -rf /var/lib/apt/lists/*
RUN groupadd -r appuser && useradd -r -g appuser -u 1000 appuser
COPY --from=builder /build/packages /usr/local/lib/python3.11/site-packages
COPY libs /app/libs
COPY apps/backtest_worker /app/apps/backtest_worker
RUN mkdir -p /app/data/backtest_results && chown -R appuser:appuser /app
USER appuser
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import redis; r=redis.from_url('${REDIS_URL}'); r.ping()" || exit 1
# Entrypoint set via docker-compose command
CMD ["python", "-m", "apps.backtest_worker.entrypoint"]
```

**New Services Required (T5.1):**
```yaml
# docker-compose.yml additions (confirmed: compose files live in repo root, not infra/)
services:
  backtest_worker_high:
    build:
      context: .
      dockerfile: apps/backtest_worker/Dockerfile
    command: ["rq", "worker", "backtest_high", "--url", "${REDIS_URL}"]
    environment:
      - REDIS_URL=${REDIS_URL}
      - DATABASE_URL=${DATABASE_URL}
      - BACKTEST_JOB_MEMORY_LIMIT=${BACKTEST_JOB_MEMORY_LIMIT:-4294967296}
    volumes:
      - backtest_data:/app/data/backtest_results
    depends_on:
      - redis
      - postgres
    # NOTE: deploy.replicas is ONLY honored in Docker Swarm mode.
    # For classic docker-compose (non-Swarm), use `docker-compose up --scale backtest_worker_high=2`
    # or define separate service entries (backtest_worker_high_1, backtest_worker_high_2).
    deploy:
      replicas: 2  # Max 2 concurrent high-priority jobs (Swarm only)

  backtest_worker_normal:
    build:
      context: .
      dockerfile: apps/backtest_worker/Dockerfile
    command: ["rq", "worker", "backtest_normal", "--url", "${REDIS_URL}"]
    environment:
      - REDIS_URL=${REDIS_URL}
      - DATABASE_URL=${DATABASE_URL}
      - BACKTEST_JOB_MEMORY_LIMIT=${BACKTEST_JOB_MEMORY_LIMIT:-4294967296}
    volumes:
      - backtest_data:/app/data/backtest_results
    depends_on:
      - redis
      - postgres
    deploy:
      replicas: 2  # Max 2 concurrent normal-priority jobs

  backtest_worker_low:
    build:
      context: .
      dockerfile: apps/backtest_worker/Dockerfile
    command: ["rq", "worker", "backtest_low", "--url", "${REDIS_URL}"]
    environment:
      - REDIS_URL=${REDIS_URL}
      - DATABASE_URL=${DATABASE_URL}
      - BACKTEST_JOB_MEMORY_LIMIT=${BACKTEST_JOB_MEMORY_LIMIT:-4294967296}
    volumes:
      - backtest_data:/app/data/backtest_results
    depends_on:
      - redis
      - postgres
    deploy:
      replicas: 2  # Max 2 concurrent low-priority jobs

  web_console:
    # existing service - add volume mount
    volumes:
      - backtest_data:/app/data/backtest_results:ro  # Read-only for UI

volumes:
  backtest_data:
    driver: local

**Required environment variables (backtest services):**
- `REDIS_URL` (all workers + web_console)
- `DATABASE_URL` (all workers + web_console)
- `BACKTEST_JOB_MEMORY_LIMIT` (workers, bytes; default 4GB)
- `BACKTEST_JOB_TIMEOUT` (workers, seconds; validates [300, 14400]; read by `BacktestJobQueue.enqueue()` as default timeout and by worker for TTL calculations)
- `BACKTEST_MAX_WORKERS` (informational only; actual concurrency is controlled by compose replicas or Swarm deploy config; this env var is for documentation and future programmatic scaling)
- `BACKTEST_DEV_AUTH` (web_console only; must be `false` in staging/prod)
```

**Files to Modify:**
- `docker-compose.yml` - Add `backtest_worker_{high,normal,low}` services and `backtest_data` volume
- `docker-compose.staging.yml` - Modify existing staging file with the same additions
- `docker-compose.ci.yml` - Modify existing CI file with the same additions
- `requirements.txt` - Add: `rq>=1.16,<2.0.0`, `psutil>=5.9`; **keep existing pins** `polars>=1.0.0,<2.0.0`, `redis>=5.0.0,<6.0.0`, and `psycopg[binary]>=3.1.0` (do not downgrade).
- `apps/backtest_worker/requirements.txt` - Worker-specific deps: `rq>=1.16,<2.0.0`, `psutil>=5.9`, `redis>=5.0.0`, `psycopg[binary]>=3.1.0`, `polars>=1.0.0`, `structlog>=24.0.0`

**DB Access Pattern Decision: Use psycopg (NOT SQLAlchemy)**

The codebase uses `psycopg[binary]>=3.1.0` across all services (orchestrator, signal_service, execution_gateway, web_console). To maintain consistency:
- **Decision:** Use psycopg with raw SQL queries and connection pooling via `psycopg_pool`
- **Rationale:** Consistent with existing patterns; avoids introducing new ORM layer; psycopg3 async support is sufficient
- **Implementation:** Use `psycopg_pool.ConnectionPool` for worker connections; code snippets below already use psycopg with parameterized SQL.

Example pattern used throughout the snippets:
```python
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

pool = ConnectionPool(conninfo=os.environ["DATABASE_URL"], open=False)
pool.open()

with pool.connection() as conn:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM backtest_jobs WHERE job_id = %s", (job_id,))
        row = cur.fetchone()
```

**Migration Ordering:**
- Existing migrations in `db/migrations`: `0001_extend_orders_for_slicing.sql`, `0004_add_audit_log.sql`, `0005_update_audit_log_schema.sql`, `0006_create_rbac_tables.sql`, `0007_strategy_session_version_triggers.sql`
- Use the next available migration number after checking `db/migrations/` (e.g., `0008_create_backtest_jobs.sql` if that slot is open); run it before T5.3 deployment
- **Dependency:** T5.2 migration depends on `0007_strategy_session_version_triggers.sql` from P4T3

### Job Queue Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    BACKTEST JOB QUEUE ARCHITECTURE                       │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────────┐         ┌─────────────────┐                        │
│  │   Web Console   │ ──────▶ │  Redis Queue    │                        │
│  │  (Streamlit)    │         │  (RQ/Celery)    │                        │
│  └─────────────────┘         └────────┬────────┘                        │
│         │                             │                                  │
│         │ status polling              │ job dispatch                     │
│         ▼                             ▼                                  │
│  ┌─────────────────┐         ┌─────────────────┐                        │
│  │  Result Storage │ ◀────── │  Worker Process │                        │
│  │   (Postgres)    │         │  (Background)   │                        │
│  └─────────────────┘         └────────┬────────┘                        │
│                                       │                                  │
│                                       ▼                                  │
│                              ┌─────────────────┐                        │
│                              │  PITBacktester  │                        │
│                              │ (research_plat) │                        │
│                              └─────────────────┘                        │
│                                                                          │
│  CRITICAL DESIGN DECISIONS:                                             │
│  1. Redis + RQ (NOT file-based queue) - prevents race conditions        │
│  2. Idempotent job execution via job_id hash                            │
│  3. Progress tracking via Redis key polling (no pub/sub fanout)          │
│  4. Result storage in Postgres with retention policy                    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Result Storage Schema (Hybrid: Postgres + Parquet)

**Design Decision:** Store metadata and summary metrics in Postgres, bulk time-series data (daily_signals, daily_weights) in Parquet files. This prevents database bloat while maintaining query flexibility.

**Storage Durability & Backup Strategy:**

| Storage Type | Durability | Backup Strategy | Retention |
|--------------|------------|-----------------|-----------|
| **Postgres (metadata)** | High | Standard DB backups (pg_dump, point-in-time recovery) | Per retention policy (90d default) |
| **Parquet (time-series)** | Medium | Docker volume on local disk; NOT backed up by default | Per retention policy (90d default) |

**Durability Guarantees:**
- **Local Development/CI:** Parquet files stored in Docker volume; ephemeral, not backed up. Job can be re-run to regenerate.
- **Staging/Production:** Options (choose one during deployment):
  1. **Local Volume (default):** Fast I/O, no backup. Acceptable if jobs are reproducible (re-runnable with same snapshot_id).
  2. **NFS/Shared Volume:** Shared across workers; backup via NFS host.

**Disaster Recovery:**
- If Parquet artifacts are lost but Postgres metadata exists: job status shows `completed` but `result_path` points to missing directory.
- Recovery: Re-run backtest with same config + snapshot_id for deterministic reproduction.
- CI tests verify this path: `test_result_path_missing_graceful_degradation`

**Retention Cleanup:**
- `BacktestResultStorage.cleanup_old_results()` deletes Parquet artifacts FIRST, then DB rows
- Only TERMINAL jobs (completed/failed/cancelled) are deleted; active jobs (pending/running) are never touched
- Disk usage monitoring via Prometheus metric: `backtest_parquet_storage_bytes_total`

```
data/backtest_results/
├── {job_id}/
│   ├── daily_signals.parquet   # [permno, date, signal]
│   ├── daily_weights.parquet   # [permno, date, weight]
│   ├── daily_ic.parquet        # [date, ic, rank_ic]
│   └── summary.json            # Snapshot reference, config hash, metrics
```

**Parquet Schema Contract (Authoritative):**

T5.1 (writer) and T5.2 (reader) MUST use these schemas for round-trip correctness:

| File | Columns | Types |
|------|---------|-------|
| `daily_signals.parquet` | date, permno, signal | Date, Int64, Float64 |
| `daily_weights.parquet` | date, permno, weight | Date, Int64, Float64 |
| `daily_ic.parquet` | date, ic, rank_ic | Date, Float64, Float64 |

Serialization rules:
- Dates: `pl.Date` (not Datetime), UTC-naive, YYYY-MM-DD
- Floats: Float64, tested to 6 decimal places, NaN preserved (not null)
- Compression: snappy

**summary.json Contract:**
```json
{
  "mean_ic": float,
  "icir": float,
  "hit_rate": float,
  "snapshot_id": string,       // REQUIRED for reproducibility
  "dataset_version_ids": dict  // REQUIRED for reproducibility
}
```

```sql
-- db/migrations/0008_create_backtest_jobs.sql  # Depends on 0007_strategy_session_version_triggers.sql
-- Requires: PostgreSQL >= 13 recommended; pgcrypto extension for UUID on PG 12

-- Extension guard: create pgcrypto if not present (safe no-op on PG 13+ where gen_random_uuid is builtin)
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE backtest_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id VARCHAR(64) UNIQUE NOT NULL,  -- Idempotency key
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CONSTRAINT status_vocabulary CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    -- Status: pending, running, completed, failed, cancelled

    -- Configuration
    alpha_name VARCHAR(255) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    weight_method VARCHAR(50) NOT NULL,
    config_json JSONB NOT NULL,  -- Full configuration

    -- Execution
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    worker_id VARCHAR(255),
    progress_pct SMALLINT DEFAULT 0 CHECK (progress_pct BETWEEN 0 AND 100),
    job_timeout INTEGER NOT NULL DEFAULT 3600
        CONSTRAINT timeout_bounds CHECK (job_timeout BETWEEN 300 AND 14400),

    -- Results (summary only - bulk data in Parquet)
    result_path VARCHAR(512),  -- Path to data/backtest_results/{job_id}/
    mean_ic FLOAT,
    icir FLOAT,
    hit_rate FLOAT,
    coverage FLOAT,
    long_short_spread FLOAT,
    average_turnover FLOAT,
    decay_half_life FLOAT,

    -- Reproducibility
    snapshot_id VARCHAR(255),
    dataset_version_ids JSONB,

    -- Error handling
    error_message TEXT,
    retry_count SMALLINT DEFAULT 0,

    -- Indexes
    created_by VARCHAR(255) NOT NULL
);

CREATE INDEX idx_backtest_jobs_status ON backtest_jobs(status);
CREATE INDEX idx_backtest_jobs_created_at ON backtest_jobs(created_at);
CREATE INDEX idx_backtest_jobs_alpha_name ON backtest_jobs(alpha_name);
CREATE INDEX idx_backtest_jobs_created_by ON backtest_jobs(created_by);
CREATE INDEX idx_backtest_jobs_snapshot_id ON backtest_jobs(snapshot_id);
CREATE INDEX idx_backtest_jobs_user_status ON backtest_jobs(created_by, status, created_at DESC);
```

Metrics note: `mean_ic`, `icir`, `hit_rate`, `coverage`, `long_short_spread`, `average_turnover`, and `decay_half_life` are optional; populate available fields and leave others NULL.
Reproducibility note: `snapshot_id` and `dataset_version_ids` stay NULL while compute is running but **must be populated before marking a job completed**; they remain optional in the DDL to allow streaming writes.

---
## Subtask Documents
- [P4T4_5.1_DONE.md](./P4T4_5.1_DONE.md) — Job Queue Infrastructure
- [P4T4_5.2_DONE.md](./P4T4_5.2_DONE.md) — Backtest Result Storage
- [P4T4_5.3_DONE.md](./P4T4_5.3_DONE.md) — Backtest Web UI
- [P4T4_5.4_DONE.md](./P4T4_5.4_DONE.md) — Walk-Forward Optimization
- [P4T4_5.5_DONE.md](./P4T4_5.5_DONE.md) — Monte Carlo Simulation
- [P4T4_5.6_DONE.md](./P4T4_5.6_DONE.md) — Backtest Regression Harness

---
## Track 5 Definition of Done (E2E Acceptance)

P4T4 is complete when ALL of the following E2E flows pass:

**Happy Path (E2E Integration Test):**
- [ ] User submits backtest via Web UI → job queued in Redis → worker picks up and runs → progress visible in UI → results stored in Postgres + Parquet → results viewable in UI with metrics and charts

**Failure/Retry Path:**
- [ ] Job exceeds memory limit → worker kills job → DB status = "failed" with error message → UI shows failure state
- [ ] Worker dies mid-job → watchdog detects heartbeat loss within 60s → DB status = "failed" → user can resubmit

**Cancellation Path:**
- [ ] User cancels running job → cancel flag set in Redis → worker detects within 30s → job cancelled cleanly → no partial artifacts

**Reproducibility Path:**
- [ ] Re-running same config + snapshot_id produces identical metrics (regression test)

**Security Path:**
- [ ] Auth stub is NOT enabled in prod/staging configs (CI-enforced)

---
## Track 5 Summary

| Task | Effort | Deliverable | Dependencies |
|------|--------|-------------|--------------|
| T5.1 Job Queue | 3-4d | Redis + RQ queue | - |
| T5.2 Result Storage | 2-3d | Postgres schema | T5.1 |
| T5.3 Web UI | 4-5d | Streamlit pages | T5.1, T5.2, T6.1 |
| T5.4 Walk-Forward | 3-4d | Rolling optimization | T5.1, T5.2 |
| T5.5 Monte Carlo | 3-4d | Bootstrap simulation | T5.1, T5.2 |
| T5.6 Regression | 2-3d | Golden result tests | T1.6, T5.1, T5.2 |

**Total Track 5:** 18-23 days

## Risk Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| T6.1 Auth not ready for T5.3 | Blocks Web UI | Stub auth with `BACKTEST_DEV_AUTH=true` env var; CI blocks stub in prod |
| RQ scalability limits | Can't run many jobs | Monitor queue depth; upgrade to Celery if >10 concurrent needed |
| Large result data | Slow queries, DB bloat | Hybrid storage: Postgres for metadata, Parquet for bulk time-series |
| Golden results become stale | False positive regressions | Governance manifest with 90-day staleness warning; PR-reviewed regeneration |
| Long jobs consume shared resources | WRDS/DuckDB contention | Use read-only snapshots during backtests; respect domain locks |
| Worker memory exhaustion | OOM kills | psutil monitoring (RSS > 4GB kills job); job-level timeout |
| Redis unavailable | Queue operations fail | Health check in worker startup; graceful degradation to sync mode |
| Qlib rolling/backtest misuse | PIT violation | Explicit AVOID in task doc; use only Qlib evaluate/report on PITBacktester outputs |

## Related Documents

- [P4_PLANNING_DONE.md](./P4_PLANNING_DONE.md) - Overall P4 planning
- [P4T2_DONE.md](./P4T2_DONE.md) - Alpha research framework (dependency)
- [docs/CONCEPTS/execution-algorithms.md](../../CONCEPTS/execution-algorithms.md) - Execution algorithms and trade quality concepts
- [libs/trading/alpha/research_platform.py](../../../libs/trading/alpha/research_platform.py) - PITBacktester implementation

---

**Last Updated:** 2025-12-09
**Status:** ✅ Complete
**Next Step:** Completed (all T5.1-T5.6 delivered)
