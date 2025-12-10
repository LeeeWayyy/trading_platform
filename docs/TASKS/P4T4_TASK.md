# P4T4: Phase 5 - Backtest Enhancement

**Task ID:** P4T4
**Phase:** P4 (Advanced Features & Research Infrastructure)
**Timeline:** Phase 3 - Backtest & Core UI (Weeks 10-13)
**Priority:** P0 - Enhanced backtesting with UI and advanced features
**Estimated Effort:** 18-23 days (6 subtasks)
**Status:** 📋 Planning
**Created:** 2025-12-09
**Last Updated:** 2025-12-09

---

## Progress Tracker

| Task | Status | PR | Notes |
|------|--------|-----|-------|
| T5.1 Job Queue | ⏳ Pending | `feat(p4): backtest job queue` | Redis + Celery/RQ |
| T5.2 Result Storage | ⏳ Pending | `feat(p4): backtest result storage` | Postgres schema |
| T5.3 Web UI | ⏳ Pending | `feat(p4): backtest web ui` | Depends on T6.1 Auth |
| T5.4 Walk-Forward | ⏳ Pending | `feat(p4): walk-forward optimization` | |
| T5.5 Monte Carlo | ⏳ Pending | `feat(p4): monte carlo simulation` | |
| T5.6 Regression Harness | ⏳ Pending | `feat(p4): backtest regression harness` | |

**Progress:** 0/6 tasks complete (0%)

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
- T2.5 Alpha Research Framework (P4T2 - COMPLETE) - `libs/alpha/research_platform.py`
- T6.1 Auth/RBAC (Track 6 - Pending) - Required for T5.3 Web UI

**Infrastructure Preconditions:**
- **Redis:** Version 6.0+ with persistence enabled (existing `libs/redis_client/`)
- **PostgreSQL:** Version 13+ recommended. Note: `gen_random_uuid()` is available in PG 13+ core BUT requires pgcrypto extension on PG 12 and some cloud providers. The migration includes a guard to create pgcrypto if not present.
- **DB Migration Ordering:** T5.2 migration must run before T5.3 deployment
- **Docker Services:** New `backtest_worker` service required (see Infrastructure section)
- **Shared Volume:** `backtest_data` volume shared between `web_console` and `backtest_worker`

**Existing Infrastructure to Build Upon:**
- `libs/alpha/research_platform.py` - PITBacktester and BacktestResult
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

**TODO:** Define a base image/entrypoint for backtest workers (current repo has no root `Dockerfile`; compose `dockerfile: Dockerfile` will fail until a worker image is added or an existing service image is reused). Decide image path and entrypoint before implementing the services below.

**New Services Required (T5.1):**
```yaml
# docker-compose.yml additions (confirmed: compose files live in repo root, not infra/)
services:
  backtest_worker_high:
    build:
      context: .
      dockerfile: Dockerfile
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
      dockerfile: Dockerfile
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
      dockerfile: Dockerfile
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
- `requirements.txt` - Add: `rq>=1.16,<2.0.0`, `psutil>=5.9`, `sqlalchemy[asyncio]>=2.0` (if we adopt SQLAlchemy); **keep existing pins** `polars>=1.0.0,<2.0.0` and `redis>=5.0.0,<6.0.0` (do not downgrade).
- **DB access pattern decision:** Current codebase uses psycopg (no SQLAlchemy session/engine wiring). Decide explicitly whether to introduce SQLAlchemy (and add the engine/session factory + tests) or keep psycopg and adjust the plan/code snippets to match.

**Migration Ordering:**
- Existing migrations in `db/migrations`: `0001_extend_orders_for_slicing.sql`, `0004_add_audit_log.sql` (0002/0003 were reserved and later removed).
- Use `0005_create_backtest_jobs.sql` for this task to keep numbering monotonic; run it before T5.3 deployment.
- Alembic revision should depend on latest migration from P4T2

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

```
data/backtest_results/
├── {job_id}/
│   ├── daily_signals.parquet   # [permno, date, signal]
│   ├── daily_weights.parquet   # [permno, date, weight]
│   ├── daily_ic.parquet        # [date, ic, rank_ic]
│   └── metadata.json           # Snapshot reference, config hash
```

```sql
-- db/migrations/0005_create_backtest_jobs.sql  # 0002/0003 skipped; 0004_add_audit_log already present
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

## Task Details

### T5.1: Backtest Job Queue Infrastructure

**Effort:** 3-4 days | **PR:** `feat(p4): backtest job queue`
**Status:** ⏳ Pending
**Priority:** P0 (Foundation - prevents UI blocking)

**Problem:** Long-running backtests block the web process and can be killed mid-run.

**CRITICAL:** Use Redis + RQ only. **DO NOT use file-based queue** (prone to race conditions, locking issues under load).

**Deliverables:**
- Redis-based job queue using RQ (simpler than Celery for single-worker)
- Background worker with progress tracking via Redis keys
- Job prioritization (high/normal/low queues with separate workers)
- Resource limits (max concurrent jobs, memory limits, worker topology)
- Idempotent job execution via job_id hash (with safe NoSuchJobError handling)
- Job timeout handling (configurable, default 1 hour)
- **T5.1a (0.5d, prerequisite step inside same PR/commit):** Modify `libs/alpha/research_platform.py` to extend `PITBacktester.run_backtest` with `progress_callback` and `cancel_check` parameters **before wiring the worker**.
  **CRITICAL: Within the same PR/commit, T5.1a must be implemented and tested first (callbacks added + tests passing) before T5.1b wiring is considered complete.**
  Target signature:
  `def run_backtest(self, alpha, start_date: date, end_date: date, snapshot_id: str | None, weight_method: str, progress_callback: Callable[[int, Optional[date]], None] | None = None, cancel_check: Callable[[], None] | None = None) -> BacktestResult:`
  **T5.1a Acceptance Criteria:**
  - [ ] `PITBacktester.run_backtest` accepts `progress_callback` and `cancel_check` parameters (type-checked)
  - [ ] `progress_callback` is invoked at least every 30 seconds during backtest execution (unit test with mock)
  - [ ] `cancel_check` is invoked at least every 30 seconds; raises `JobCancelled` when flag is set (unit test)
  - [ ] Existing tests pass with callbacks set to `None` (backward compatible)
  - [ ] T5.1a portion is verified (tests passing) before marking T5.1b complete, even if same PR
- **T5.1b (3d, depends on T5.1a completion):** Wire BacktestWorker/RQ enqueueing to the extended PITBacktester callbacks.
- **Blocker:** T5.1b work cannot be considered done until the T5.1a callbacks/tests are in place within the same PR/commit (sequencing enforced).

**Job Status Vocabulary Contract:**
The API uses unified status vocabulary across DB and RQ. Mapping:
| DB Status | RQ Status | Description |
|-----------|-----------|-------------|
| pending | queued | Job enqueued, not yet started |
| running | started | Worker picked up job |
| completed | finished | Job succeeded |
| failed | failed | Job raised exception |
| cancelled | canceled (queued kill) / finished (cooperative return) | Job cancelled (queued jobs use `Job.cancel()` → `canceled`; running jobs return success payload with `cancelled: true`, which RQ marks `finished`) |
| failed | deferred | RQ deferred = failed in DB (requeued by RQ, treat as failed until worker resumes) |
| failed | stopped | RQ stopped state maps to DB failed (manual worker stop) |
| failed (manual override) | queued→failed without run | Pending job missing worker but RQ failure hook fired; DB forces `failed` for visibility |

**Payload disambiguation contract (FORMAL):**
RQ marks cooperative cancellations as `finished`; always inspect `job.result` to disambiguate:
| RQ Status | Payload | DB Status | Notes |
|-----------|---------|-----------|-------|
| `finished` | `{"cancelled": True}` | `cancelled` | Cooperative cancellation |
| `finished` | `{"cancelled": False}` or missing key | `completed` | Normal success |
| `finished` | `None` (payload missing) | `failed` | Error: set `error_message="RQ job finished but payload missing"` |
Helper function `_resolve_rq_finished_status(job)` should encapsulate this logic.

Note: RQ's internal spelling is `canceled` (one “l”). DB continues to use `cancelled`; do not compare DB values to RQ values directly—DB is the source of truth.

CRITICAL: Use DB status as source of truth. RQ 'finished' may be success OR cancellation.

**DB Status Transition Contract:**
```
pending  ─────────────────────────────> cancelled  (queued job cancelled)
    │                                        ▲
    │ worker starts                          │ cooperative cancel
    ▼                                        │
running ────────────────────────────────────┘
    │
    ├──────> completed  (success, sets completed_at)
    │
    └──────> failed     (exception, sets error_message)
```

| Transition | Trigger | Timestamp Set | Notes |
|------------|---------|---------------|-------|
| pending→running | Worker starts | started_at | Worker calls update_db_status |
| pending→cancelled | cancel_job() for queued | completed_at | Immediate, in cancel_job |
| running→completed | Worker finishes | completed_at | Worker calls update_db_status |
| running→failed | Exception raised | completed_at | Worker catches, sets error_message |
| running→cancelled | JobCancelled | completed_at | Worker catches, updates status |

All terminal transitions (completed/failed/cancelled) must set `completed_at` for auditability and retention cleanup.

**Retry Semantics:**
- Automatic retries only: `retry_count` increments inside the RQ retry hook using `func.coalesce(BacktestJob.retry_count, 0) + 1` (no enqueue-side increments).
- User-triggered re-runs (after any terminal state) must pass `is_rerun=True` when recreating the DB row so `retry_count` resets to 0; user reruns should never inherit automated retry counts.
- Heal operations that recreate missing RQ jobs reset `retry_count` to 0 (like user reruns) to avoid double-counting with the RQ retry hook; however, heals log a `heal_count` metric for observability. To prevent infinite re-enqueue loops, the watchdog marks jobs as failed after 3 consecutive heals within 1 hour (tracked via Redis key `backtest:heal_count:{job_id}` with 1h TTL).
- If DB row says `pending`/`running` but RQ job is missing, enqueue heals by recreating the RQ job instead of returning `None`; this counts as an automated retry.
- After 3 automatic retries, job moves to dead-letter queue (`backtest_failed`).

**Progress Tracking Contract:**
- Worker writes progress to Redis key `backtest:progress:{job_id}` (not pub/sub - simple polling)
- Updates emitted every ≤30 seconds during execution
- Progress stages: 0% (started), 10% (loading_data), 20-90% (computing for PITBacktester mapping only), 92% (saving_parquet), 95% (saving_db), 100% (completed)
- Progress payload: `{"pct": int, "stage": str, "current_date": str, "updated_at": iso_timestamp}`
- Key TTL: Dynamic, refreshed on each update. Base TTL = `max(job_timeout, 3600)` seconds
- Redis writes must use a pipeline (`set` + `expire`) to make TTL update atomic and avoid races
- For long jobs (>1h), worker refreshes TTL on each progress update to prevent expiry
- TTL refreshes (progress, heartbeat, cancel flags) must all use the same pipeline pattern; avoid `exists()+expire()` races.
- **DB Progress Sync:** Sync to Postgres every 10% (pct % 10 == 0) to keep coarse progress even if Redis expires. This is a **coarse fallback only**; fine-grained progress lives in Redis. (Note: 90 and 100 are already divisible by 10, so no separate >=90 clause needed.)
- **Redis namespace convention:** all keys are prefixed `backtest:*` (e.g., `backtest:progress:{job_id}`, `backtest:cancel:{job_id}`) to avoid collisions.

**Cancellation Contract:**
- Queued jobs: Immediate cancellation via `job.cancel()`
- Running jobs: Cooperative interruption via Redis flag `backtest:cancel:{job_id}`
- Cancel flag TTL: Dynamic, refreshed to `max(job_timeout, 3600)` on each progress update
- Periodic cancellation checker also refreshes cancel-flag TTL to avoid expiry during long compute loops.
- Worker checks cancel flag: (1) every progress update (≤30s), AND (2) every 10s via periodic check for long compute loops
- Periodic check via `check_cancellation_periodic(job_id, job_timeout)` called from PITBacktester's `cancel_check` callback
- **PITBacktester Contract:** MUST call `cancel_check` callback at least every 30s during compute loops
- **Implementation Note:** T5.1 must extend PITBacktester to add `cancel_check` and `progress_callback` parameters; these callbacks are mandatory in the worker wiring below.
- Acceptance: Cancelled jobs transition to "cancelled" status within 30 seconds (cooperative, not instant)
- Progress is preserved on cancellation (no reset to 0); UI shows the last emitted percentage.

**Resource Limits Design:**
- **Max concurrent jobs:** 2 per queue (configurable via `BACKTEST_MAX_WORKERS`)
- **Memory limit:** Worker process monitored via `psutil`; job killed if RSS > 4GB
- **Worker topology:** 2 workers per priority queue (replicas=2 in docker-compose) → max 2 concurrent jobs per queue, 6 total
- **Starvation prevention:** High-priority queue processed first, but workers cycle to prevent total low-priority starvation
- **Dead-letter queue:** Jobs failing 3x moved to `backtest_failed` queue for inspection

**Serialization:**
- `BacktestJobConfig` uses `to_dict()`/`from_dict()` for JSON serialization (no pickle)

**PITBacktester Dependency Contract:**
Worker initialization requires the following components (per `libs/alpha/research_platform.py`):
| Dependency | Class | Purpose |
|------------|-------|---------|
| `version_manager` | `DatasetVersionManager` | Snapshot management, PIT data access |
| `crsp_provider` | `CRSPLocalProvider` | Price/return data (snapshot-locked) |
| `compustat_provider` | `CompustatLocalProvider` | Fundamental data (snapshot-locked) |
| `metrics_adapter` | `AlphaMetricsAdapter` | IC/ICIR computation (optional, auto-created) |

**PITBacktester contract (updated):**
- `run_backtest(...) -> BacktestResult` (deterministic given snapshot + seed); thread-safe for concurrent reads because providers are read-only, but avoid sharing mutable state across threads when injecting custom adapters.
- Raises: `JobCancelled` (propagated to worker as cooperative cancellation), `ValueError` for invalid config/date ranges, provider-specific exceptions bubble up and are recorded in `error_message`.
- Caller must pass `progress_callback` and `cancel_check`; progress callback may be called from worker thread only and must remain side-effect-free.
- **Reproducibility fields lifecycle:** BacktestResult's `snapshot_id: str` and `dataset_version_ids: dict[str, str]` are **required, non-optional fields** in the dataclass (not `Optional`). PITBacktester MUST populate them before returning. The worker validates these fields via direct attribute access (`result.snapshot_id`, not `getattr` fallbacks) and raises `ValueError` if `None`. Summary metrics (IC/ICIR/coverage/turnover/etc.) are optional/nullable.

T5.1 Extension Required:
- Add `progress_callback: Callable[[int, Optional[date]], None] | None = None` parameter (import `Optional` or enable `from __future__ import annotations`)
- Add `cancel_check: Callable[[], None] | None = None` parameter
- Call `cancel_check()` at least every 30s during compute loops
- Call `progress_callback(pct, current_date)` for progress updates

**Implementation:**
```python
# libs/backtest/job_queue.py
from dataclasses import dataclass, field
from datetime import datetime, date, UTC
from enum import Enum
from typing import Any
import hashlib
import json
import structlog

from redis import Redis
from rq import Queue, Retry
from rq.job import Job, NoSuchJobError
from sqlalchemy.orm import Session

class JobPriority(Enum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"

@dataclass
class BacktestJobConfig:
    """Configuration for a backtest job."""
    alpha_name: str
    start_date: date
    end_date: date
    weight_method: str = "zscore"
    extra_params: dict[str, Any] = field(default_factory=dict)

    def compute_job_id(self, created_by: str) -> str:
        """
        Compute idempotent job ID from configuration + user.

        CRITICAL: Includes created_by to ensure multi-tenant separation.
        Same config from different users produces different job_ids.
        """
        content = json.dumps({
            "alpha": self.alpha_name,
            "start": str(self.start_date),
            "end": str(self.end_date),
            "weight": self.weight_method,
            "params": self.extra_params,
            "created_by": created_by,  # Multi-tenant separation
        }, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:32]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict (no pickle)."""
        return {
            "alpha_name": self.alpha_name,
            "start_date": str(self.start_date),
            "end_date": str(self.end_date),
            "weight_method": self.weight_method,
            "extra_params": self.extra_params,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BacktestJobConfig":
        """Deserialize from dict."""
        return cls(
            alpha_name=data["alpha_name"],
            start_date=date.fromisoformat(data["start_date"]),
            end_date=date.fromisoformat(data["end_date"]),
            weight_method=data.get("weight_method", "zscore"),
            extra_params=data.get("extra_params", {}),
        )

class BacktestJobQueue:
    """Redis-based job queue for backtests."""

    DEFAULT_TIMEOUT = 3600  # 1 hour
    MAX_RETRIES = 3

    def __init__(
        self,
        redis_client: Redis,
        db_session: Session,  # Sync session for DB operations
        default_queue: str = "backtest_normal",
    ):
        self.redis = redis_client
        self.db_session = db_session
        self.queues = {
            JobPriority.HIGH: Queue("backtest_high", connection=redis_client),
            JobPriority.NORMAL: Queue("backtest_normal", connection=redis_client),
            JobPriority.LOW: Queue("backtest_low", connection=redis_client),
        }
        self.default_queue = self.queues[JobPriority.NORMAL]
        self.logger = structlog.get_logger(__name__)

    def _safe_fetch_job(self, job_id: str) -> Job | None:
        """Safely fetch job, returning None if not found."""
        try:
            return Job.fetch(job_id, connection=self.redis)
        except NoSuchJobError:
            return None

    def _create_db_job(
        self,
        job_id: str,
        config: BacktestJobConfig,
        created_by: str,
        job_timeout: int,
        *,
        is_rerun: bool = False,  # user-initiated rerun resets retry_count
    ) -> None:
        """
        Create BacktestJob row in Postgres before queueing.

        CRITICAL: Uses upsert pattern to handle re-enqueue after cleanup.
        If job_id already exists, resets ALL state fields to avoid stale data.
        """
        from sqlalchemy import func
        from sqlalchemy.dialects.postgresql import insert
        from libs.backtest.models import BacktestJob

        stmt = insert(BacktestJob).values(
            job_id=job_id,
            status="pending",
            alpha_name=config.alpha_name,
            start_date=config.start_date,
            end_date=config.end_date,
            weight_method=config.weight_method,
            config_json=config.to_dict(),
            created_by=created_by,
            retry_count=0,
            progress_pct=0,
            job_timeout=job_timeout,
        ).on_conflict_do_update(
            index_elements=["job_id"],
            set_={
                # Reset status
                "status": "pending",  # Reset to pending for re-enqueue
                # Automatic retries bump counter via RQ retry hook ONLY (no double counting here)
                "retry_count": 0 if is_rerun else func.coalesce(BacktestJob.retry_count, 0),
                # Clear execution state
                "started_at": None,     # Clear stale timestamps
                "completed_at": None,
                "worker_id": None,      # Clear worker assignment
                "progress_pct": 0,      # Reset progress
                "error_message": None,  # Clear previous error
                "job_timeout": job_timeout,
                # Clear result data
                "result_path": None,    # Clear stale result path
                "mean_ic": None,        # Clear stale metrics
                "icir": None,
                "hit_rate": None,
                "coverage": None,
                "long_short_spread": None,
                "average_turnover": None,
                "decay_half_life": None,
                # CRITICAL: Clear stale reproducibility metadata on rerun/heal.
                # Previous snapshot_id/dataset_version_ids belong to OLD execution;
                # worker MUST populate fresh values on new execution completion.
                # Preserving stale metadata risks incorrect reproducibility claims.
                "snapshot_id": None,
                "dataset_version_ids": None,
            }
        )
        self.db_session.execute(stmt)
        self.db_session.commit()

    def enqueue(
        self,
        config: BacktestJobConfig,
        priority: JobPriority = JobPriority.NORMAL,
        created_by: str = "system",
        timeout: int | None = None,
        *,
        is_rerun: bool = False,  # user-triggered rerun resets retry_count
    ) -> Job:
        """
        Enqueue a backtest job.

        CRITICAL: Creates BacktestJob DB row BEFORE enqueueing to Redis.
        Returns existing job if same config+user already queued/running (idempotent).
        TOCTOU mitigation: guard enqueue with a short-lived Redis `SETNX backtest:lock:{job_id}` (or rely on the DB unique index on job_id); if the lock fails, return the existing job to avoid double-enqueue.
        Timeout guardrails: validate `job_timeout` within [300, 14400] seconds to avoid starving workers or running forever.

        Re-enqueue Policy:
        - If job is queued/started: return existing (no-op)
        - If job is finished/failed: delete from RQ, reset DB, create new RQ job
        - If DB row is pending/running but RQ job is missing: heal by recreating the RQ job (never return None)
        - User-triggered reruns must call enqueue(..., is_rerun=True) to reset retry_count to 0 (automatic retries handled by RQ retry hook)
        - Healing must NOT bump retry_count; only the RQ retry hook increments it to avoid double-counting.
        """
        from libs.backtest.models import BacktestJob

        job_id = config.compute_job_id(created_by)  # Include user in hash

        job_timeout = timeout or self.DEFAULT_TIMEOUT
        if not 300 <= job_timeout <= 14_400:
            raise ValueError("timeout must be between 300 and 14,400 seconds")

        lock_key = f"backtest:lock:{job_id}"
        if not self.redis.set(lock_key, "1", nx=True, ex=10):
            # Another enqueue in-flight; return existing job if present
            existing = self._safe_fetch_job(job_id)
            if existing:
                return existing
            # Lock contention but no RQ job—retry with backoff before failing
            import time
            time.sleep(0.1)  # 100ms backoff
            if not self.redis.set(lock_key, "1", nx=True, ex=10):
                raise RuntimeError("enqueue lock contention after retry; another enqueue in progress")

        try:
            # DB status is source of truth for idempotency (pending/running = active)
            db_job = self.db_session.query(BacktestJob).filter_by(job_id=job_id).first()
            if db_job and db_job.status in ("pending", "running"):
                existing = self._safe_fetch_job(job_id)
                if existing:
                    return existing
                # DB says active but RQ job missing → recreate RQ job deterministically
                # Track heal count in Redis to prevent infinite re-enqueue loops (max 3 per hour)
                heal_key = f"backtest:heal_count:{job_id}"
                heal_count = int(self.redis.get(heal_key) or 0)
                if heal_count >= 3:
                    # Too many heals → fail the job instead of looping forever
                    db_job.status = "failed"
                    db_job.error_message = f"Job healed {heal_count} times in 1h; marked failed to prevent infinite loop"
                    db_job.completed_at = datetime.now(UTC)
                    self.db_session.commit()
                    self.logger.error("heal_loop_breaker", job_id=job_id, heal_count=heal_count)
                    raise RuntimeError(f"Job {job_id} exceeded max heal attempts")
                # Dynamic TTL: use job's configured timeout (min 1h) to align with job lifecycle
                heal_ttl = max(job_timeout, 3600)
                self.redis.setex(heal_key, heal_ttl, str(heal_count + 1))
                self._create_db_job(job_id, config, created_by, job_timeout, is_rerun=True)  # Reset retry_count like user rerun
                queue = self.queues[priority]
                healed_job = queue.enqueue(
                    "libs.backtest.worker.run_backtest",
                    kwargs={
                        "config": config.to_dict(),
                        "created_by": created_by,
                    },
                    job_id=job_id,
                    job_timeout=job_timeout,
                    retry=Retry(max=self.MAX_RETRIES, interval=[60, 300, 900]),
                    result_ttl=86400 * 7,
                    failure_ttl=86400 * 30,
                )
                self.logger.info("healed_missing_rq_job", job_id=job_id, heal_count=heal_count + 1)
                return healed_job

            # Check for existing job (idempotency) - safe lookup in RQ
            existing = self._safe_fetch_job(job_id)
            if existing:
                status = existing.get_status()
                if status in ("queued", "started"):
                    return existing
                else:
                    existing.delete()

            # Create/reset DB row (worker will update status)
            self._create_db_job(job_id, config, created_by, job_timeout, is_rerun=is_rerun)

            queue = self.queues[priority]
            job = queue.enqueue(
                "libs.backtest.worker.run_backtest",
                kwargs={
                    "config": config.to_dict(),
                    "created_by": created_by,
                },
                job_id=job_id,
                job_timeout=job_timeout,
                retry=Retry(max=self.MAX_RETRIES, interval=[60, 300, 900]),
                result_ttl=86400 * 7,  # Keep results 7 days
                failure_ttl=86400 * 30,  # Keep failed job info 30 days
            )
            return job
        finally:
            self.redis.delete(lock_key)

    def get_job_status(self, job_id: str) -> dict[str, Any]:
        """
        Get job status with progress information.

        CRITICAL: Uses DB status as source of truth (not RQ status).
        Falls back to RQ status only if DB row not found.
        """
        from libs.backtest.models import BacktestJob

        # DB is source of truth
        db_job = self.db_session.query(BacktestJob).filter_by(job_id=job_id).first()
        rq_job = self._safe_fetch_job(job_id)
        if not db_job:
            # Fallback to RQ if DB row missing (shouldn't happen in normal flow)
            if not rq_job:
                return {"status": "not_found"}
            payload = rq_job.result if isinstance(rq_job.result, dict) else {}
            cancelled = payload.get("cancelled") is True if isinstance(payload, dict) else False
            effective_status = "cancelled" if cancelled else "unknown"
            return {
                "job_id": job_id,
                "status": effective_status,
                "rq_status": rq_job.get_status(),
                "warning": "DB row missing; derived from RQ payload",
            }

        # RQ marks cooperative cancellations as "finished"; inspect payload to disambiguate
        rq_payload = rq_job.result if rq_job and isinstance(rq_job.result, dict) else {}
        rq_cancelled = rq_payload.get("cancelled") is True if isinstance(rq_payload, dict) else False

        # Get progress from Redis (JSON payload set by worker)
        # Falls back to DB progress_pct if Redis key expired (synced every 10% and >=90%)
        progress_raw = self.redis.get(f"backtest:progress:{job_id}")
        if progress_raw:
            progress = json.loads(progress_raw)
        else:
            # Redis expired - use DB progress as fallback (preserve last-known pct even when cancelled)
            fallback_pct = db_job.progress_pct or 0
            progress = {"pct": fallback_pct, "stage": db_job.status}

        return {
            "job_id": job_id,
            "status": "cancelled" if rq_cancelled else db_job.status,  # DB status as source of truth with RQ payload override for cooperative cancel
            "progress_pct": progress.get("pct", db_job.progress_pct or 0),
            "progress_stage": progress.get("stage", db_job.status),
            "progress_date": progress.get("current_date"),
            "progress_updated_at": progress.get("updated_at"),
            "created_at": db_job.created_at.isoformat() if db_job.created_at else None,
            "started_at": db_job.started_at.isoformat() if db_job.started_at else None,
            "completed_at": db_job.completed_at.isoformat() if db_job.completed_at else None,
            "error_message": db_job.error_message,
            "result_path": db_job.result_path,
        }

    def cancel_job(self, job_id: str, job_timeout: int | None = None) -> bool:
        """
        Cancel a queued or running job.

        CRITICAL: Updates DB status immediately for queued jobs.
        For running jobs, sets cancel flag and lets worker update DB.

        TOCTOU Race Window Note:
        There's a small race window between checking RQ status and updating DB.
        If a job transitions from queued→started between the status check and
        the DB update, the DB update may not execute (status already "running").
        This is acceptable because:
        1. The cooperative cancel flag will still be set for running jobs
        2. Worker will pick up the flag within 30s via progress/cancel checks
        3. Eventual consistency is sufficient for cancellation (not instant)

        For stricter consistency (if needed), use SELECT FOR UPDATE:
            db_job = self.db_session.query(BacktestJob).filter_by(
                job_id=job_id
            ).with_for_update(skip_locked=True).first()
        """
        from libs.backtest.models import BacktestJob

        job = self._safe_fetch_job(job_id)
        db_job = self.db_session.query(BacktestJob).filter_by(job_id=job_id).first()

        # Orphan handling: if RQ job is gone but DB says active, mark cancelled to avoid stuck UI
        if not job:
            if db_job and db_job.status in ("pending", "running"):
                db_job.status = "cancelled"
                db_job.completed_at = datetime.now(UTC)
                self.db_session.commit()
                self.logger.info("cancel_orphan_db_only", job_id=job_id, status=db_job.status)
                return True
            return False

        status = job.get_status()
        if status == "queued":
            # Queued job: immediate cancellation + DB update
            job.cancel()
            # Update DB status immediately (worker won't run)
            # NOTE: If job transitioned to started between status check and here,
            # this condition fails safely; cooperative flag handles running jobs.
            if db_job and db_job.status == "pending":
                db_job.status = "cancelled"
                db_job.completed_at = datetime.now(UTC)
                self.db_session.commit()
            # Also set cancel flag in case of race (job may have started)
            self.redis.setex(f"backtest:cancel:{job_id}", 3600, "1")
            self.logger.info("cancelled_queued_job", job_id=job_id)
            return True
        elif status == "started":
            # Running job: set cooperative cancellation flag
            # Worker checks this flag every progress update (≤30s)
            # Dynamic TTL aligned with the job's timeout (DB/RQ-derived)
            effective_timeout = int(
                (job.timeout if job else None)
                or getattr(db_job, "job_timeout", None)
                or (job_timeout if job_timeout is not None else None)
                or BacktestJobQueue.DEFAULT_TIMEOUT
                or 3600
            )
            ttl = max(effective_timeout, 3600)
            self.redis.setex(f"backtest:cancel:{job_id}", ttl, "1")
            self.logger.info("cancel_flag_set", job_id=job_id, ttl=ttl, status=status)
            return True
        return False

    def watchdog_fail_lost_jobs(self) -> int:
        """
        Mark running jobs as failed if their heartbeat expired (lost worker).

        Called by a periodic watchdog (cron/Streamlit background). Returns
        number of jobs marked failed.
        """
        from libs.backtest.models import BacktestJob

        now_ts = datetime.now(UTC).timestamp()
        running_jobs = self.db_session.query(BacktestJob).filter_by(status="running").all()
        failures = 0
        for job in running_jobs:
            threshold = now_ts - max(int(job.job_timeout), 3600)
            heartbeat_raw = self.redis.get(f"backtest:heartbeat:{job.job_id}")
            try:
                heartbeat_ts = datetime.fromisoformat(heartbeat_raw.decode()).timestamp() if heartbeat_raw else None
            except (ValueError, UnicodeDecodeError):
                # Parse failure intent: treat corrupted heartbeat as "missing" → job will be failed.
                # This is fail-fast behavior: we don't retry parsing because a corrupted heartbeat
                # indicates a bug or manual tampering, and failing immediately is safer than
                # leaving the job in an indeterminate state.
                self.logger.warning("heartbeat_parse_failed", job_id=job.job_id, raw=heartbeat_raw)
                heartbeat_ts = None  # Will trigger failure below
            if heartbeat_ts is None or heartbeat_ts < threshold:
                job.status = "failed"
                job.error_message = "Worker heartbeat lost; marked failed by watchdog"
                job.completed_at = datetime.now(UTC)
                failures += 1
        if failures:
            self.db_session.commit()
        return failures
```

**Worker Entrypoint Contract:**

The `run_backtest` function is the RQ job entrypoint. Contract:
- **Input:** `config: dict` (serialized BacktestJobConfig), `created_by: str`
- **Output:** `dict` with `job_id`, `result_path`, `summary_metrics`
- **Side effects:** Writes progress to Redis, persists results to Postgres (sync) and Parquet
- **Errors:** Raises `JobCancelled`, `MemoryError`, or propagates PITBacktester exceptions

```python
# libs/backtest/worker.py
import os
import json
import time
import shutil
from datetime import datetime, date, UTC
from pathlib import Path
from typing import Any, Callable, Optional

import psutil
import structlog
from redis import Redis
from rq import get_current_job
from sqlalchemy import create_engine, update, func
from sqlalchemy.orm import Session

from libs.alpha.research_platform import PITBacktester, BacktestResult
from libs.backtest.job_queue import BacktestJobConfig, BacktestJobQueue
from libs.backtest.models import BacktestJob  # SQLAlchemy ORM model

class JobCancelled(Exception):
    """Raised when job cancellation is requested."""
    pass

class BacktestWorker:
    """Worker with cooperative cancellation and memory monitoring."""

    MAX_RSS_BYTES = int(os.getenv("BACKTEST_JOB_MEMORY_LIMIT", 4 * 1024 * 1024 * 1024))  # 4GB default
    CANCEL_CHECK_INTERVAL = 10  # Check cancel flag every 10s even without progress

    def __init__(self, redis: Redis, db_session: Session):
        self.redis = redis
        self.db_session = db_session  # Sync session for RQ worker
        self.process = psutil.Process()
        self._last_cancel_check = 0.0
        self.logger = structlog.get_logger(__name__)

    def check_cancellation(self, job_id: str) -> None:
        """Check if cancellation requested; raise if so."""
        if self.redis.exists(f"backtest:cancel:{job_id}"):
            # Clean up cancel flag
            self.redis.delete(f"backtest:cancel:{job_id}")
            raise JobCancelled(f"Job {job_id} cancelled by user")

    def check_cancellation_periodic(self, job_id: str, job_timeout: int) -> None:
        """Check cancellation AND memory on interval, for long loops without progress updates."""
        now = time.monotonic()
        if now - self._last_cancel_check >= self.CANCEL_CHECK_INTERVAL:
            self.check_cancellation(job_id)
            # Keep cancel flag alive during long compute loops to avoid TTL expiry races
            ttl = max(int(job_timeout or 3600), 3600)
            pipe = self.redis.pipeline()
            # Refresh heartbeat even when no progress events fire (prevents false watchdog alerts)
            pipe.set(f"backtest:heartbeat:{job_id}", datetime.now(UTC).isoformat())
            pipe.expire(f"backtest:heartbeat:{job_id}", ttl)
            pipe.expire(f"backtest:cancel:{job_id}", ttl)
            pipe.execute()
            self.check_memory()  # Also check memory during long compute loops
            self._last_cancel_check = now

    def check_memory(self) -> None:
        """Kill job if memory exceeds limit."""
        rss = self.process.memory_info().rss
        if rss > self.MAX_RSS_BYTES:
            raise MemoryError(f"Job exceeded {self.MAX_RSS_BYTES // 1e9:.0f}GB limit")

    def update_progress(
        self,
        job_id: str,
        pct: int,
        stage: str,
        current_date: str | None = None,
        job_timeout: int = 3600,
        *,
        skip_cancel_check: bool = False,
        skip_memory_check: bool = False,
    ) -> None:
        """
        Update progress and check cancellation/memory.

        Progress is stored in Redis for fast UI polling.
        Sync to DB every 10% and at/above 90% so coarse progress survives Redis key expiry.
        """
        if not skip_cancel_check:
            self.check_cancellation(job_id)
        if not skip_memory_check:
            self.check_memory()
        self._last_cancel_check = time.monotonic()

        # Dynamic TTL: max(job_timeout, 3600) to support long-running jobs
        ttl = max(job_timeout, 3600)
        payload = json.dumps({
            "pct": pct,
            "stage": stage,
            "current_date": current_date,
            "updated_at": datetime.now(UTC).isoformat(),
        })

        # Atomic write+expire to avoid TTL race between set and expire
        pipe = self.redis.pipeline()
        pipe.set(f"backtest:progress:{job_id}", payload)
        pipe.expire(f"backtest:progress:{job_id}", ttl)
        # Heartbeat for watchdog to detect stuck/lost workers
        pipe.set(f"backtest:heartbeat:{job_id}", datetime.now(UTC).isoformat())
        pipe.expire(f"backtest:heartbeat:{job_id}", ttl)
        # Also refresh cancel flag TTL if it exists.
        # NOTE: Redis EXPIRE on a non-existent key is a safe no-op (returns 0, no error).
        # This is intentional: cancel flag is only set by cancel_job(); refreshing during
        # progress is defensive but not critical. We accept the no-op rather than adding
        # EXISTS check overhead.
        pipe.expire(f"backtest:cancel:{job_id}", ttl)
        pipe.execute()

        # Sync to DB at coarse checkpoints (every 10%) for resilience
        if self.should_sync_db_progress(pct):
            self.update_db_progress(job_id, pct)

    def update_db_status(self, job_id: str, status: str, **kwargs: Any) -> None:
        """Update job status in Postgres (sync)."""
        job = self.db_session.query(BacktestJob).filter_by(job_id=job_id).first()
        if job:
            TERMINAL_STATES = {"completed", "failed", "cancelled"}
            if job.status in TERMINAL_STATES:
                return  # never transition OUT of terminal states
            # Special case: only allow cancellation from active states (pending, running); terminal states are immutable
            if status == "cancelled" and job.status not in ("running", "pending"):
                return  # avoid clobbering terminal status during races
            job.status = status
            for key, value in kwargs.items():
                if hasattr(job, key):
                    setattr(job, key, value)
            self.db_session.commit()

    def update_db_progress(self, job_id: str, pct: int) -> None:
        """
        Persist progress to DB periodically for fallback when Redis expires.
        """
        job = self.db_session.query(BacktestJob).filter_by(job_id=job_id).first()
        if job:
            job.progress_pct = pct
            self.db_session.commit()

    def should_sync_db_progress(self, pct: int) -> bool:
        """
        Determine if DB progress sync is needed.

        Syncs at 0, 10, 20, ..., 90, 100 (every 10%).
        No separate >=90 clause needed since 90 and 100 are divisible by 10.
        """
        return pct % 10 == 0


_RETRY_ENGINE = None


def _get_retry_engine():
    """
    Lazily create a shared engine for retry hook to avoid per-retry pools.

    IMPORTANT: This engine is a global singleton for the worker process lifetime.
    - DATABASE_URL must be set before any retry occurs; if missing, the hook fails loudly.
    - The engine is NOT disposed automatically; it persists until worker shutdown.
    - For testing, mock DATABASE_URL before importing this module or use monkeypatch.

    Worker startup should validate DATABASE_URL is set:
        if not os.getenv("DATABASE_URL"):
            raise RuntimeError("DATABASE_URL required for backtest worker")
    """
    global _RETRY_ENGINE
    if _RETRY_ENGINE is None:
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            raise RuntimeError("DATABASE_URL not set; cannot create retry hook engine")
        _RETRY_ENGINE = create_engine(db_url)
    return _RETRY_ENGINE


def record_retry(job, *exc_info):
    """RQ retry hook: increment retry_count for automated retries."""
    engine = _get_retry_engine()
    with Session(engine) as session:
        session.execute(
            update(BacktestJob)
            .where(BacktestJob.job_id == job.id)
            .values(retry_count=func.coalesce(BacktestJob.retry_count, 0) + 1)
        )
        session.commit()
    return False  # allow default exception handling to continue


# Worker bootstrap must register retry handler
# worker = Worker(["backtest_high", "backtest_normal", "backtest_low"], connection=redis)
# worker.push_exc_handler(record_retry)


def run_backtest(config: dict[str, Any], created_by: str) -> dict[str, Any]:
    """
    RQ job entrypoint for backtest execution.

    Contract:
    - Input: config dict (from BacktestJobConfig.to_dict()), created_by string
    - Output: dict with job_id, result_path, summary_metrics
    - Progress: Redis key backtest:progress:{job_id} updated every ≤30s
    - Persistence: Results saved to Postgres (sync) and Parquet files
    - Cancellation: JobCancelled caught and returns {"cancelled": True} (NOT raised)

    CRITICAL: JobCancelled must NOT be raised - RQ marks raised exceptions as 'failed'.
    Instead, catch JobCancelled, update DB to 'cancelled', and return success with cancelled flag.
    """
    redis = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))
    engine = create_engine(os.getenv("DATABASE_URL"))

    with Session(engine) as db_session:
        job_config = BacktestJobConfig.from_dict(config)
        job_id = job_config.compute_job_id(created_by)  # Include user in hash
        current_job = get_current_job()
        job_timeout = int(current_job.timeout or BacktestJobQueue.DEFAULT_TIMEOUT) if current_job else BacktestJobQueue.DEFAULT_TIMEOUT
        worker = BacktestWorker(redis, db_session)

        # Fail closed on dependency init: initialize all dependencies FIRST; only mark running after successful init
        try:
            # Initialize backtester with required dependencies
            worker.update_progress(job_id, 5, "init_dependencies", job_timeout=job_timeout)

            # PITBacktester Initialization Contract (per libs/alpha/research_platform.py):
            # - version_manager: DatasetVersionManager for snapshot management and PIT data access
            # - crsp_provider: CRSPLocalProvider for price/return data (snapshot-locked)
            # - compustat_provider: CompustatLocalProvider for fundamental data (snapshot-locked)
            # - metrics_adapter: AlphaMetricsAdapter for IC/ICIR computation (optional, auto-created)
            from libs.data_quality.versioning import DatasetVersionManager
            from libs.data_providers.crsp_local_provider import CRSPLocalProvider
            from libs.data_providers.compustat_local_provider import CompustatLocalProvider
            from libs.alpha.metrics import AlphaMetricsAdapter

            version_manager = DatasetVersionManager()
            crsp_provider = CRSPLocalProvider()
            compustat_provider = CompustatLocalProvider()
            metrics_adapter = AlphaMetricsAdapter()

            backtester = PITBacktester(
                version_manager=version_manager,
                crsp_provider=crsp_provider,
                compustat_provider=compustat_provider,
                metrics_adapter=metrics_adapter,
            )

            # Mark as running only after dependencies are up
            worker.update_db_status(job_id, "running", started_at=datetime.now(UTC))
            worker.update_progress(job_id, 0, "started", job_timeout=job_timeout)
            worker.update_progress(job_id, 10, "loading_data", job_timeout=job_timeout)

            # Get snapshot_id from config if provided (for reproducibility)
            snapshot_id = job_config.extra_params.get("snapshot_id")

            # Load alpha definition from registry
            from libs.alpha.registry import get_alpha_by_name
            alpha = get_alpha_by_name(job_config.alpha_name)

            # Run backtest
            # PITBacktester.run_backtest signature AFTER T5.1 (must be implemented):
            #   run_backtest(
            #       alpha,
            #       start_date,
            #       end_date,
            #       snapshot_id,
            #       weight_method,
            #       progress_callback: Callable[[int, Optional[date]], None] | None = None,
            #       cancel_check: Callable[[], None] | None = None,
            #   )
            # Progress callback maps PIT progress [0-100] → UI range [20-90]
            # and ensures cooperative cancellation checks run during compute loops.
            result = backtester.run_backtest(
                alpha=alpha,
                start_date=job_config.start_date,
                end_date=job_config.end_date,
                snapshot_id=snapshot_id,
                weight_method=job_config.weight_method,
                progress_callback=lambda pct, d: worker.update_progress(
                    job_id,
                    20 + round(pct * 0.7),   # stretch to 20-90 band while computing (PITBacktester only)
                    "computing",
                    str(d) if d else None,
                    job_timeout=job_timeout,
                ),
                cancel_check=lambda: worker.check_cancellation_periodic(job_id, job_timeout),
            )

            # Save results
            worker.update_progress(job_id, 90, "saving_parquet", job_timeout=job_timeout)
            result_path = _save_parquet_artifacts(job_id, result)

            worker.update_progress(job_id, 95, "saving_db", job_timeout=job_timeout)
            _save_result_to_db(db_session, job_id, result, result_path)

            worker.update_progress(job_id, 100, "completed", job_timeout=job_timeout)
            worker.update_db_status(job_id, "completed", completed_at=datetime.now(UTC))

            return {
                "job_id": job_id,
                "result_path": str(result_path),
                "summary_metrics": {
                    "mean_ic": result.mean_ic,
                    "icir": result.icir,
                    "hit_rate": result.hit_rate,
                },
            }

        except JobCancelled:
            # CRITICAL: Do NOT re-raise - RQ would mark as 'failed'
            # Instead, return success with cancelled flag while preserving last progress
            last_progress = redis.get(f"backtest:progress:{job_id}")
            last_pct = json.loads(last_progress)["pct"] if last_progress else 0
            shutil.rmtree(Path("data/backtest_results") / job_id, ignore_errors=True)
            worker.update_db_status(job_id, "cancelled", completed_at=datetime.now(UTC))
            worker.update_progress(
                job_id,
                last_pct,
                "cancelled",
                skip_cancel_check=True,  # avoid re-raising JobCancelled inside handler
                skip_memory_check=True,
            )
            return {"job_id": job_id, "cancelled": True}

        except Exception as e:
            worker.update_db_status(
                job_id,
                "failed",
                error_message=str(e),
                completed_at=datetime.now(UTC),
            )
            raise


def _save_parquet_artifacts(job_id: str, result: BacktestResult) -> Path:
    """
    Save bulk time-series data to Parquet files.

    Parquet Schema Contract (explicit types for round-trip correctness):
    - daily_signals.parquet:
      - date: Date (UTC-naive, YYYY-MM-DD)
      - permno: Int64
      - signal: Float64 (NaN preserved)
    - daily_weights.parquet:
      - date: Date
      - permno: Int64
      - weight: Float64
    - daily_ic.parquet:
      - date: Date
      - ic: Float64
      - rank_ic: Float64

    Serialization Rules:
    - Validate required columns exist before casting; raise ValueError if any are missing or typed incorrectly.
    - All dates stored as pl.Date (not Datetime) for consistency
    - Float precision: stored as Float64, tested to 6 decimal places
    - NaN values preserved (not converted to null)
    - Compression: snappy (fast read/write)
    """
    import polars as pl

    def _validate_schema(df: pl.DataFrame, required: dict[str, pl.datatypes.DataType]) -> None:
        missing_cols = set(required.keys()) - set(df.columns)
        if missing_cols:
            raise ValueError(f"missing columns: {missing_cols}")
        for col, dtype in required.items():
            if df[col].dtype != dtype:
                raise ValueError(f"column {col} has type {df[col].dtype}, expected {dtype}")

    result_dir = Path("data/backtest_results") / job_id
    result_dir.mkdir(parents=True, exist_ok=True)

    required_signal_schema = {"date": pl.Date, "permno": pl.Int64, "signal": pl.Float64}
    required_weight_schema = {"date": pl.Date, "permno": pl.Int64, "weight": pl.Float64}
    required_ic_schema = {"date": pl.Date, "ic": pl.Float64, "rank_ic": pl.Float64}

    _validate_schema(result.daily_signals, required_signal_schema)
    _validate_schema(result.daily_weights, required_weight_schema)
    if result.daily_ic is None:
        raise ValueError("daily_ic DataFrame must be populated before parquet export")
    _validate_schema(result.daily_ic, required_ic_schema)

    # Validation runs post-compute (late) by design:
    # - BacktestResult schema is determined by PITBacktester output, which is only known after compute.
    # - Early validation would require duplicating the schema definition, risking drift.
    # - The alternative (add __post_init__ validation to BacktestResult) was considered but rejected
    #   because BacktestResult is defined in research_platform.py and modifying it for queue concerns
    #   would violate separation of concerns.
    # - Late validation is acceptable: schema violations are extremely unlikely if PITBacktester is correct,
    #   and the error message clearly identifies the issue when it does occur.

    # Save signals with explicit schema cast
    result.daily_signals.select(["date", "permno", "signal"]).cast(required_signal_schema).write_parquet(
        result_dir / "daily_signals.parquet",
        compression="snappy"
    )

    # Save weights with explicit schema cast
    result.daily_weights.select(["date", "permno", "weight"]).cast(required_weight_schema).write_parquet(
        result_dir / "daily_weights.parquet",
        compression="snappy"
    )

    # Save IC time series with validation
    result.daily_ic.select(["date", "ic", "rank_ic"]).cast(required_ic_schema).write_parquet(
        result_dir / "daily_ic.parquet",
        compression="snappy"
    )

    _write_summary_json(result_dir, result)

    return result_dir


def _write_summary_json(result_dir: Path, result: BacktestResult) -> None:
    """Persist summary metrics and reproducibility metadata alongside Parquet artifacts."""
    import json

    summary = {
        "mean_ic": result.mean_ic,
        "icir": result.icir,
        "hit_rate": result.hit_rate,
        "snapshot_id": result.snapshot_id,
        "dataset_version_ids": result.dataset_version_ids,
    }
    (result_dir / "summary.json").write_text(json.dumps(summary, default=str, indent=2))


def _save_result_to_db(session: Session, job_id: str, result: BacktestResult, result_path: Path) -> None:
    """Save summary metrics to Postgres."""
    job = session.query(BacktestJob).filter_by(job_id=job_id).first()
    if job:
        # Hard fail if reproducibility metadata is missing.
        # BacktestResult.snapshot_id and .dataset_version_ids are required (non-Optional) fields;
        # PITBacktester MUST populate them. Use direct attribute access, not getattr fallback.
        if result.snapshot_id is None or result.dataset_version_ids is None:
            raise ValueError("BacktestResult must include snapshot_id and dataset_version_ids for reproducibility")
        job.result_path = str(result_path)
        job.mean_ic = result.mean_ic
        job.icir = result.icir
        job.hit_rate = result.hit_rate
        # ... other metrics
        # Reproducibility fields (must be set for every run)
        job.snapshot_id = result.snapshot_id
        job.dataset_version_ids = result.dataset_version_ids
        session.commit()
```

**Files to Create:**
- `libs/backtest/__init__.py`
- `libs/backtest/job_queue.py`
- `libs/backtest/worker.py`
- `libs/backtest/progress.py` (progress tracking helpers)
- `tests/libs/backtest/__init__.py`
- `tests/libs/backtest/test_job_queue.py`
- `docs/ADRs/ADR-0024-backtest-job-architecture.md`

**Files to Modify:**
- `docker-compose.yml` - Add `backtest_worker` services and `backtest_data` volume
- `docker-compose.staging.yml` - Modify existing staging file with the same additions
- `docker-compose.ci.yml` - Modify existing CI file with the same additions

**ADR Content (ADR-0024):**
- Decision: Use RQ over Celery (simpler, single-worker sufficient)
- Decision: Redis-only queue (no file-based - race conditions)
- Decision: Idempotent job IDs via config hash
- Decision: Cooperative cancellation via Redis flag (not SIGTERM)
- Decision: Memory monitoring via psutil (RQ lacks native support)
- Trade-off: RQ lacks Celery's workflow chains, but backtests are independent

---

### T5.2: Backtest Result Storage

**Effort:** 2-3 days | **PR:** `feat(p4): backtest result storage`
**Status:** ⏳ Pending
**Dependencies:** T5.1

**Deliverables:**
- Postgres result storage schema (see Architecture section)
- Result serialization (BacktestResult → JSONB)
- Result retrieval with filtering
- Cancellation and resume support
- Result retention policy (configurable, default 90 days)

**Sync vs Async Decision:**
RQ workers run synchronously. Use sync SQLAlchemy Session for worker persistence (BacktestWorker uses sync Session). Async storage (BacktestResultStorage) is for web layer queries only.

**Implementation:**
```python
# libs/backtest/models.py
from datetime import datetime
from sqlalchemy import Column, String, Integer, Date, DateTime, JSON, Float, Index, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class BacktestJob(Base):
    __tablename__ = "backtest_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    job_id = Column(String(64), nullable=False, unique=True, index=True)  # idempotency key
    status = Column(String(32), nullable=False, index=True)
    alpha_name = Column(String(128), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    weight_method = Column(String(32), nullable=False)
    config_json = Column(JSON, nullable=False)
    created_by = Column(String(64), nullable=False, index=True)
    retry_count = Column(Integer, nullable=False, default=0)
    progress_pct = Column(Integer, nullable=False, default=0)
    result_path = Column(String, nullable=True)
    mean_ic = Column(Float, nullable=True)
    icir = Column(Float, nullable=True)
    hit_rate = Column(Float, nullable=True)
    coverage = Column(Float, nullable=True)
    long_short_spread = Column(Float, nullable=True)
    average_turnover = Column(Float, nullable=True)
    decay_half_life = Column(Float, nullable=True)
    snapshot_id = Column(String, nullable=True)
    dataset_version_ids = Column(JSON, nullable=True)
    job_timeout = Column(Integer, nullable=False, default=3600)
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "idx_backtest_jobs_user_status",
            "created_by",
            "status",
            "created_at",
            postgresql_using="btree",
            postgresql_ops={"created_at": "DESC"},
        ),
    )

# Alembic/SQL migration must also create the composite index:
# CREATE INDEX idx_backtest_jobs_user_status ON backtest_jobs(created_by, status, created_at DESC);
```

```python
# libs/backtest/result_storage.py
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import polars as pl
from sqlalchemy import select, delete
from sqlalchemy.orm import Session

from libs.alpha.research_platform import BacktestResult
from libs.backtest.models import BacktestJob

PARQUET_BASE_DIR = Path("data/backtest_results")

class BacktestResultStorage:
    """
    Persistent storage for backtest results (sync, used by web layer + workers).

    Async callers should wrap in threadpool executor; core storage remains sync to
    match the rest of the codebase.
    """

    DEFAULT_RETENTION_DAYS = 90

    def __init__(self, session: Session):
        self.session = session

    def get_result(self, job_id: str) -> BacktestResult | None:
        """Retrieve backtest result by job ID, loading Parquet artifacts."""
        result = self.session.execute(
            select(BacktestJob).where(BacktestJob.job_id == job_id)
        )
        job = result.scalar_one_or_none()
        if not job or not job.result_path:
            return None

        # Load Parquet files and reconstruct BacktestResult
        return self._load_result_from_path(Path(job.result_path))

    def list_jobs(
        self,
        created_by: str | None = None,
        alpha_name: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List jobs with filtering."""
        query = select(BacktestJob)
        if created_by:
            query = query.where(BacktestJob.created_by == created_by)
        if alpha_name:
            query = query.where(BacktestJob.alpha_name == alpha_name)
        if status:
            query = query.where(BacktestJob.status == status)
        query = query.order_by(BacktestJob.created_at.desc()).offset(offset).limit(limit)

        result = self.session.execute(query)
        return [self._job_to_dict(job) for job in result.scalars()]

    def cleanup_old_results(
        self,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ) -> int:
        """
        Delete results older than retention period.

        CRITICAL: Only deletes TERMINAL jobs (completed, failed, cancelled).
        Never deletes pending/running jobs to prevent orphaning active work.
        Deletes Parquet artifacts first, then DB rows to keep DB/source of truth
        aligned with on-disk state.
        Returns count of jobs deleted.
        """
        cutoff = datetime.now() - timedelta(days=retention_days)
        terminal_statuses = ("completed", "failed", "cancelled")

        # First, get TERMINAL jobs to delete (need result_path for artifact cleanup)
        result = self.session.execute(
            select(BacktestJob).where(
                BacktestJob.created_at < cutoff,
                BacktestJob.status.in_(terminal_statuses)  # Only terminal jobs!
            )
        )
        jobs_to_delete = result.scalars().all()
        artifact_paths = [Path(job.result_path) for job in jobs_to_delete if job.result_path]

        # Delete Parquet artifacts first (ensures disk cleanup even if DB delete fails later)
        for artifact_path in artifact_paths:
            if artifact_path.exists():
                shutil.rmtree(artifact_path)

        # Then delete DB records
        self.session.execute(
            delete(BacktestJob).where(
                BacktestJob.created_at < cutoff,
                BacktestJob.status.in_(terminal_statuses)
            )
        )
        self.session.commit()

        return len(jobs_to_delete)

    def _load_result_from_path(self, path: Path) -> BacktestResult | None:
        """
        Load BacktestResult from Parquet artifacts.

        Raises ValueError if reproducibility metadata (snapshot_id, dataset_version_ids)
        is missing from summary.json. These fields are required for any valid result.
        """
        if not path.exists():
            return None
        import json
        import polars as pl

        signals = pl.read_parquet(path / "daily_signals.parquet")
        weights = pl.read_parquet(path / "daily_weights.parquet")
        ic = pl.read_parquet(path / "daily_ic.parquet")

        summary_path = path / "summary.json"
        if not summary_path.exists():
            raise ValueError(f"Missing summary.json in {path}; cannot reconstruct BacktestResult")
        summary = json.loads(summary_path.read_text())

        # Reproducibility fields are REQUIRED - raise clear error if missing
        snapshot_id = summary.get("snapshot_id")
        dataset_version_ids = summary.get("dataset_version_ids")
        if snapshot_id is None or dataset_version_ids is None:
            raise ValueError(
                f"Missing reproducibility metadata in {path}/summary.json: "
                f"snapshot_id={snapshot_id}, dataset_version_ids={dataset_version_ids}. "
                "These fields are required for valid BacktestResult reconstruction."
            )

        return BacktestResult(
            daily_signals=signals,
            daily_weights=weights,
            daily_ic=ic,
            mean_ic=summary.get("mean_ic", float(ic["ic"].mean())),
            icir=summary.get("icir", float(ic["ic"].mean() / ic["ic"].std())) if float(ic["ic"].std()) != 0 else 0.0,
            hit_rate=summary.get("hit_rate"),
            snapshot_id=snapshot_id,  # Required, not None
            dataset_version_ids=dataset_version_ids,  # Required, not None
        )

    def _job_to_dict(self, job: BacktestJob) -> dict[str, Any]:
        """Convert ORM model to dict."""
        return {
            "job_id": job.job_id,
            "status": job.status,
            "alpha_name": job.alpha_name,
            "start_date": str(job.start_date),
            "end_date": str(job.end_date),
            "created_by": job.created_by,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "mean_ic": job.mean_ic,
            "icir": job.icir,
        }
```

**Files to Create:**
- `libs/backtest/result_storage.py`
- `libs/backtest/models.py` (SQLAlchemy models)
- `db/migrations/0005_create_backtest_jobs.sql`  # canonical filename for P4T4 (0002/0003 skipped; 0004 exists)
- `tests/libs/backtest/test_result_storage.py`
- `docs/CONCEPTS/backtest-result-storage.md`

---

### T5.3: Backtest Web UI

**Effort:** 4-5 days | **PR:** `feat(p4): backtest web ui`
**Status:** ⏳ Pending
**Dependencies:** T5.1, T5.2, T6.1 (Auth)

**Auth Dependency Strategy:**
- **If T6.1 complete:** Use production OAuth2 auth via `@requires_auth`
- **If T6.1 pending:** Use dev-mode auth stub with `BACKTEST_DEV_AUTH=true` env var
- **Dev-mode stub:** Returns fixed user `{"username": "dev_user", "role": "operator"}`
- **CI enforcement:** Test `test_no_dev_auth_in_prod` fails if `BACKTEST_DEV_AUTH` is set in production config

**Auth Stub Rollback Path (when T6.1 ships):**
1. Remove `BACKTEST_DEV_AUTH=true` from all non-local env files (`.env.prod`, `docker-compose.prod.yml`, Helm/infra values if present)
2. Replace `@backtest_requires_auth` with standard `@requires_auth` **and update imports explicitly:** replace `from apps.web_console.auth.backtest_auth import backtest_requires_auth` with `from apps.web_console.auth.streamlit_helpers import requires_auth` in both `apps/web_console/pages/backtest.py` and `apps/web_console/app.py` (no wrapper re-exports)
3. Delete `apps/web_console/auth/backtest_auth.py`
4. Update `test_auth_governance.py` to verify no `BACKTEST_DEV_AUTH` references remain
5. Add a CI governance check that fails if `backtest_requires_auth` is referenced after T6.1 ships (guards manual import regressions)
6. CI will auto-fail if any stub references persist
7. If Helm/Kubernetes deployment is used (e.g., `helm/values.yaml`, `k8s/*.yaml`), also verify those files don't contain `BACKTEST_DEV_AUTH=true`

**Auth Stub Governance:**
```python
# tests/apps/web_console/test_auth_governance.py
import os
from pathlib import Path
import pytest

def load_prod_environment() -> dict[str, str]:
    """Load environment variables from production config files."""
    prod_env = {}
    # Check .env.prod if exists
    env_prod = Path(".env.prod")
    if env_prod.exists():
        for line in env_prod.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                prod_env[key.strip()] = value.strip().strip('"')
    # Also check docker-compose.prod.yml environment section (in project root)
    prod_compose = Path("docker-compose.prod.yml")
    if prod_compose.exists():
        import yaml
        with open(prod_compose) as f:
            config = yaml.safe_load(f)
        for service in config.get("services", {}).values():
            for env_entry in service.get("environment", []):
                if isinstance(env_entry, str) and "=" in env_entry:
                    key, value = env_entry.split("=", 1)
                    prod_env[key] = value
    return prod_env

def test_no_dev_auth_in_prod():
    """CI guard: dev auth stub must not be enabled in production."""
    prod_env = load_prod_environment()
    assert prod_env.get("BACKTEST_DEV_AUTH", "false").lower() != "true", (
        "BACKTEST_DEV_AUTH=true is set in production config! "
        "This must be removed before T5.3 goes to prod."
    )


def test_no_auth_stub_references_after_t61():
    """
    CI guard: After T6.1 ships, no code should reference backtest_requires_auth.

    This test detects manual import regressions where developers accidentally
    import the stub decorator instead of the real @requires_auth after T6.1.
    """
    import subprocess

    # Check if T6.1 has shipped (streamlit_helpers.py exists with full auth)
    t61_marker = Path("apps/web_console/auth/streamlit_helpers.py")
    if not t61_marker.exists():
        pytest.skip("T6.1 not yet shipped; auth stub is expected")

    # grep for any references to backtest_requires_auth
    result = subprocess.run(
        ["grep", "-r", "backtest_requires_auth", "apps/"],
        capture_output=True,
        text=True,
    )

    # grep returns 0 if matches found, 1 if no matches, 2+ on error
    if result.returncode == 0:
        pytest.fail(
            f"Found backtest_requires_auth references after T6.1 shipped! "
            f"These must be replaced with @requires_auth:\n{result.stdout}"
        )
    # returncode 1 = no matches = test passes
```

```python
# apps/web_console/auth/backtest_auth.py
import os
from apps.web_console.auth.streamlit_helpers import requires_auth

def backtest_requires_auth(func):
    """Auth decorator with dev-mode fallback for T5.3."""
    if os.getenv("BACKTEST_DEV_AUTH", "false").lower() == "true":
        # Dev mode: return stub user
        def wrapper(*args, **kwargs):
            import streamlit as st
            st.session_state["user"] = {"username": "dev_user", "role": "operator"}
            return func(*args, **kwargs)
        return wrapper
    else:
        # Production: use real auth
        return requires_auth(func)
```

**Deliverables:**
- Backtest configuration form (alpha selection, date range, weight method)
- Job status polling with progress bar
- Job status polling with progress bar **using progressive backoff** (e.g., 2s → 5s → 10s while pending/running; 30s after terminal)
- Results visualization (equity curve, drawdown, IC time series)
- Strategy comparison view (side-by-side metrics)
- Export functionality (CSV, JSON)

**Implementation:**
```python
# apps/web_console/pages/backtest.py
import os
import json
from contextlib import contextmanager

import streamlit as st
from streamlit_autorefresh import st_autorefresh
from redis import Redis
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.web_console.auth.backtest_auth import backtest_requires_auth
from libs.backtest.job_queue import BacktestJobQueue, BacktestJobConfig, JobPriority
from libs.backtest.result_storage import BacktestResultStorage
from libs.alpha.registry import get_registered_alphas  # Alpha registry

# Engine initialization (singleton per process - thread-safe)
@st.cache_resource
def get_db_engine():
    """Get database engine (connection pool, thread-safe)."""
    return create_engine(
        os.getenv("DATABASE_URL"),
        pool_size=5,
        max_overflow=10,
        pool_recycle=3600,  # Recycle connections after 1h
    )

@st.cache_resource
def get_redis_client() -> Redis:
    """Get Redis client (thread-safe)."""
    return Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))

@contextmanager
def get_job_queue() -> BacktestJobQueue:
    """
    Get job queue with fresh DB session.

    CRITICAL: Creates new Session per request and ensures it is closed to
    avoid connection leaks in Streamlit's long-running process. Do NOT close the
    cached Redis client returned by st.cache_resource; it lives for the process
    lifetime and Streamlit shuts it down via the global teardown hook.
    """
    redis = get_redis_client()
    engine = get_db_engine()
    session = Session(engine)
    queue = BacktestJobQueue(redis, session)
    try:
        yield queue
    finally:
        session.close()

def get_available_alphas() -> list[str]:
    """Get list of registered alpha names from alpha registry."""
    return get_registered_alphas()

VALID_STATUSES = {"pending", "running", "completed", "failed", "cancelled"}


def get_poll_interval_ms(elapsed_seconds: float) -> int:
    """Progressive polling: start fast, then back off for long/terminal jobs."""
    if elapsed_seconds < 30:
        return 2000
    if elapsed_seconds < 60:
        return 5000
    if elapsed_seconds < 300:
        return 10_000
    return 30_000

def get_user_jobs(created_by: str, status: list[str]) -> list[dict]:
    """
    Query jobs for a user with given statuses.

    Uses sync query against Postgres backtest_jobs table.

    CRITICAL: Use DB status vocabulary (pending, running, completed, failed, cancelled),
    NOT RQ vocabulary (queued, started, finished).
    """
    invalid = set(status) - VALID_STATUSES
    if invalid:
        raise ValueError(f"Invalid statuses: {invalid}. Valid: {VALID_STATUSES}")

    engine = get_db_engine()
    with Session(engine) as session:
        from libs.backtest.models import BacktestJob
        jobs = session.query(BacktestJob).filter(
            BacktestJob.created_by == created_by,
            BacktestJob.status.in_(status)  # Use DB statuses: pending, running, etc.
        ).order_by(BacktestJob.created_at.desc()).limit(50).all()

        # Fetch progress from Redis for each job (reuse cached client)
        redis = get_redis_client()
        result = []
        for job in jobs:
            progress_raw = redis.get(f"backtest:progress:{job.job_id}")
            progress = json.loads(progress_raw) if progress_raw else {"pct": 0}
            result.append({
                "job_id": job.job_id,
                "alpha_name": job.alpha_name,
                "start_date": str(job.start_date),
                "end_date": str(job.end_date),
                "progress_pct": progress.get("pct", 0),
            })
        return result

def get_current_username() -> str:
    """Get username from session, with fallback for dev mode."""
    user = st.session_state.get("user")
    if not user:
        st.info("No authenticated user in session; using anonymous fallback")
        return "anonymous"
    return user.get("username", "anonymous")

@backtest_requires_auth
def render_backtest_page():
    """Backtest configuration and results page."""
    st.header("Backtest Runner")

    tab1, tab2, tab3 = st.tabs(["New Backtest", "Running Jobs", "Results"])

    with tab1:
        render_backtest_form()

    with tab2:
        render_running_jobs()

    with tab3:
        render_backtest_results()

def render_backtest_form():
    """Render backtest configuration form."""
    with st.form("backtest_config"):
        col1, col2 = st.columns(2)

        with col1:
            alpha_name = st.selectbox(
                "Alpha Signal",
                options=get_available_alphas(),
                help="Select the alpha signal to backtest"
            )
            start_date = st.date_input("Start Date")
            end_date = st.date_input("End Date")

        with col2:
            weight_method = st.selectbox(
                "Weight Method",
                options=["zscore", "rank", "equal"],
                help="How to convert signals to portfolio weights"
            )
            priority_str = st.selectbox(
                "Priority",
                options=["normal", "high", "low"],
            )

        submitted = st.form_submit_button("Run Backtest", type="primary")

        if submitted:
            # Validate date range
            if end_date <= start_date:
                st.error("End date must be after start date")
                return

            # Validate priority enum
            try:
                priority = JobPriority(priority_str)
            except ValueError:
                st.error(f"Invalid priority: {priority_str}")
                return

            config = BacktestJobConfig(
                alpha_name=alpha_name,
                start_date=start_date,
                end_date=end_date,
                weight_method=weight_method,
            )

            # Pass created_by from authenticated session
            created_by = get_current_username()
            with get_job_queue() as queue:
                job = queue.enqueue(config, priority=priority, created_by=created_by)
            st.success(f"Backtest queued! Job ID: {job.id}")
            st.rerun()

def render_running_jobs():
    """Render list of running/queued jobs with status."""
    created_by = get_current_username()

    # Progressive polling with st_autorefresh
    elapsed = st.session_state.get("backtest_poll_elapsed", 0.0)
    interval_ms = get_poll_interval_ms(elapsed)
    st_autorefresh(interval=interval_ms, key="backtest_poll")
    st.session_state["backtest_poll_elapsed"] = elapsed + interval_ms / 1000

    # Fetch jobs for current user only (use DB statuses, not RQ)
    # Progressive polling: refresh every 2s initially, back off to 5s after 30s, 10s after 60s.
    jobs = get_user_jobs(created_by=created_by, status=["pending", "running"])

    if not jobs:
        st.session_state["backtest_poll_elapsed"] = 0

    for job in jobs:
        with st.container():
            col1, col2, col3 = st.columns([3, 1, 1])
            with col1:
                st.write(f"**{job['alpha_name']}** ({job['start_date']} to {job['end_date']})")
            with col2:
                st.progress(job['progress_pct'] / 100)
            with col3:
                if st.button("Cancel", key=f"cancel_{job['job_id']}"):
                    with get_job_queue() as queue:
                        queue.cancel_job(job['job_id'])
                    st.rerun()

def render_backtest_results():
    """Render completed backtest results with visualization."""
    # Result selection, metrics display, charts
    ...
```

**Files to Create:**
- `apps/web_console/pages/backtest.py`
- `apps/web_console/auth/backtest_auth.py`
- `apps/web_console/components/backtest_form.py`
- `apps/web_console/components/backtest_results.py`
- `apps/web_console/components/equity_curve_chart.py`
- `apps/web_console/components/ic_timeseries_chart.py`
- `tests/apps/web_console/test_backtest_page.py`
- `tests/apps/web_console/test_backtest_job_status.py`
- `tests/apps/web_console/test_auth_governance.py` (CI guard for dev auth)
- `docs/CONCEPTS/backtest-web-ui.md`
- `docs/ADRs/ADR-0025-backtest-ui-worker-contract.md`

---

### T5.4: Walk-Forward Optimization

**Effort:** 3-4 days | **PR:** `feat(p4): walk-forward optimization`
**Status:** ⏳ Pending
**Dependencies:** T5.1, T5.2

**⚠️ Qlib Decision:** Use custom `WalkForwardOptimizer` over PITBacktester. **DO NOT use `qlib.workflow.rolling`** - it assumes Qlib DataHandler and would bypass our DatasetVersionManager PIT guarantees.

**Deliverables:**
- Rolling train/test window framework
- Parameter optimization per window (grid search, random search)
- Out-of-sample performance aggregation
- Overfitting prevention metrics (train/test performance gap)
- Overlap policy: enforce `step_months >= test_months` so evaluation windows never overlap; document that overlapping train windows are allowed but test windows must be disjoint
- Emit a warning when `step_months < train_months` to make overlapping train windows explicit to operators
- Documentation: update `docs/CONCEPTS/walk-forward-optimization.md` to state explicitly that train windows may overlap while test windows must remain disjoint to avoid information leakage
- Optional: Export per-window Pandas returns for `qlib.contrib.evaluate` post-processing

**Implementation:**
```python
# libs/backtest/walk_forward.py
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Any
from dateutil.relativedelta import relativedelta

import polars as pl
import structlog

from libs.alpha.research_platform import PITBacktester, BacktestResult

@dataclass
class WalkForwardConfig:
    """Configuration for walk-forward optimization."""
    train_months: int = 12
    test_months: int = 3
    step_months: int = 3  # How much to advance each window
    min_train_samples: int = 252  # Minimum trading days in train

@dataclass
class WindowResult:
    """Result for a single walk-forward window."""
    window_id: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    best_params: dict[str, Any]
    train_ic: float
    test_ic: float
    test_result: BacktestResult

@dataclass
class WalkForwardResult:
    """Complete walk-forward optimization result."""
    windows: list[WindowResult]
    aggregated_test_ic: float
    aggregated_test_icir: float
    overfitting_ratio: float  # train_ic / test_ic (>2 suggests overfit)

    @property
    def is_overfit(self) -> bool:
        return self.overfitting_ratio > 2.0

class WalkForwardOptimizer:
    """Walk-forward optimization framework."""

    def __init__(
        self,
        backtester: PITBacktester,
        config: WalkForwardConfig,
    ):
        self.backtester = backtester
        self.config = config
        self.logger = structlog.get_logger(__name__)

    def generate_windows(
        self,
        start_date: date,
        end_date: date,
    ) -> list[tuple[date, date, date, date]]:
        """Generate (train_start, train_end, test_start, test_end) tuples."""
        if self.config.step_months < self.config.test_months:
            raise ValueError(
                "step_months must be >= test_months to prevent information leakage from "
                "overlapping evaluation periods. Train overlap is allowed for rolling windows "
                "(see docs/CONCEPTS/walk-forward-optimization.md)."
            )
        if self.config.step_months < self.config.train_months:
            self.logger.warning(
                "walk_forward_train_overlap",
                step_months=self.config.step_months,
                train_months=self.config.train_months,
                overlap_months=self.config.train_months - self.config.step_months,
                message="Train windows will overlap (intended for rolling optimization); test windows remain disjoint. Overlap is safe for train data but operators should be aware.",
            )

        windows: list[tuple[date, date, date, date]] = []
        cursor = start_date

        while True:
            train_start = cursor
            train_end = (train_start + relativedelta(months=self.config.train_months)) - timedelta(days=1)
            test_start = train_end + timedelta(days=1)
            test_end = (test_start + relativedelta(months=self.config.test_months)) - timedelta(days=1)

            if test_end > end_date:
                break  # would exceed requested range

            if (train_end - train_start).days + 1 < self.config.min_train_samples:
                raise ValueError("train window shorter than min_train_samples")

            windows.append((train_start, train_end, test_start, test_end))
            cursor = cursor + relativedelta(months=self.config.step_months)

        return windows

    def optimize_window(
        self,
        alpha_factory: Callable[..., AlphaDefinition],
        param_grid: dict[str, list[Any]],
        train_start: date,
        train_end: date,
    ) -> tuple[dict[str, Any], float]:
        """Find best params on training window. Returns (best_params, train_ic)."""
        ...

    def run(
        self,
        alpha_factory: Callable[..., AlphaDefinition],
        param_grid: dict[str, list[Any]],
        start_date: date,
        end_date: date,
        snapshot_id: str | None = None,
    ) -> WalkForwardResult:
        """Run complete walk-forward optimization."""
        windows = self.generate_windows(start_date, end_date)
        results = []

        for i, (train_start, train_end, test_start, test_end) in enumerate(windows):
            # 1. Optimize on training window
            best_params, train_ic = self.optimize_window(
                alpha_factory, param_grid, train_start, train_end
            )

            # 2. Evaluate on test window (out-of-sample)
            alpha = alpha_factory(**best_params)
            test_result = self.backtester.run_backtest(
                alpha, test_start, test_end, snapshot_id=snapshot_id
            )

            results.append(WindowResult(
                window_id=i,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                best_params=best_params,
                train_ic=train_ic,
                test_ic=test_result.mean_ic,
                test_result=test_result,
            ))

        return self._aggregate_results(results)
```

All window backtests (train + test) must forward `snapshot_id` to `run_backtest` to keep PIT determinism across windows.

**Files to Create:**
- `libs/backtest/walk_forward.py`
- `libs/backtest/param_search.py` (grid search, random search utilities)
- `tests/libs/backtest/test_walk_forward.py`
- `docs/CONCEPTS/walk-forward-optimization.md`

---

### T5.5: Monte Carlo Simulation

**Effort:** 3-4 days | **PR:** `feat(p4): monte carlo simulation`
**Status:** ⏳ Pending
**Dependencies:** T5.1, T5.2

**⚠️ Qlib Decision:** Qlib has **no bootstrap/resampling utilities**. Implement NumPy-based simulation. Optionally use `qlib.contrib.evaluate.risk_analysis` for per-simulation metrics when Qlib is installed.

**Deliverables:**
- Trade resampling (bootstrap with replacement) - NumPy implementation
- Return shuffling (path simulation) - NumPy implementation
- Confidence intervals for key metrics (Sharpe, max drawdown, etc.)
- Distribution visualization
- Optional: Per-path metrics via `qlib.contrib.evaluate.risk_analysis`

**Implementation:**
```python
# libs/backtest/monte_carlo.py
from dataclasses import dataclass, field
from typing import Literal
import numpy as np
import structlog
import polars as pl

from libs.alpha.research_platform import BacktestResult

@dataclass
class MonteCarloConfig:
    """Configuration for Monte Carlo simulation."""
    n_simulations: int = 1000
    method: Literal["bootstrap", "shuffle"] = "bootstrap"
    confidence_levels: list[float] = field(default_factory=lambda: [0.05, 0.50, 0.95])
    random_seed: int | None = None

@dataclass
class ConfidenceInterval:
    """Confidence interval for a metric."""
    metric_name: str
    observed: float
    lower_5: float
    median: float
    upper_95: float

    @property
    def is_significant(self) -> bool:
        """True if observed value is above median of simulations."""
        return self.observed > self.median

@dataclass
class MonteCarloResult:
    """Complete Monte Carlo simulation result."""
    config: MonteCarloConfig
    n_simulations: int

    # Confidence intervals for key metrics
    sharpe_ci: ConfidenceInterval
    max_drawdown_ci: ConfidenceInterval
    mean_ic_ci: ConfidenceInterval
    hit_rate_ci: ConfidenceInterval

    # Full distributions (for visualization)
    sharpe_distribution: np.ndarray
    max_drawdown_distribution: np.ndarray

    # Statistical significance
    p_value_sharpe: float  # Probability of observing Sharpe by chance

class MonteCarloSimulator:
    """Monte Carlo simulation for backtest robustness analysis."""

    def __init__(self, config: MonteCarloConfig):
        self.config = config
        self.rng = np.random.default_rng(config.random_seed)
        self.logger = structlog.get_logger(__name__)
        if config.random_seed is None:
            self.logger.warning(
                "monte_carlo_unseeded",
                message="Monte Carlo running without fixed random_seed; results are non-reproducible",
            )

    def run_bootstrap(
        self,
        result: BacktestResult,
    ) -> MonteCarloResult:
        """
        Bootstrap resampling of daily returns.

        Preserves return distribution but breaks temporal structure.
        """
        daily_returns = self._extract_daily_returns(result)
        n_days = len(daily_returns)

        sharpes = []
        drawdowns = []

        for _ in range(self.config.n_simulations):
            # Resample with replacement
            indices = self.rng.integers(0, n_days, size=n_days)
            simulated_returns = daily_returns[indices]

            # Compute metrics on simulated path
            sharpe = self._compute_sharpe(simulated_returns)
            max_dd = self._compute_max_drawdown(simulated_returns)

            sharpes.append(sharpe)
            drawdowns.append(max_dd)

        self.logger.info(
            "monte_carlo_bootstrap_complete",
            simulations=self.config.n_simulations,
            n_days=n_days,
        )

        return self._build_result(result, np.array(sharpes), np.array(drawdowns))

    def run_shuffle(
        self,
        result: BacktestResult,
    ) -> MonteCarloResult:
        """
        Shuffle returns across time (permutation test).

        Tests if observed performance could arise by chance.
        """
        ...

    def _compute_confidence_interval(
        self,
        observed: float,
        simulated: np.ndarray,
        metric_name: str,
    ) -> ConfidenceInterval:
        """Compute confidence interval from simulated distribution."""
        return ConfidenceInterval(
            metric_name=metric_name,
            observed=observed,
            lower_5=np.percentile(simulated, 5),
            median=np.percentile(simulated, 50),
            upper_95=np.percentile(simulated, 95),
        )
```

**Files to Create:**
- `libs/backtest/monte_carlo.py`
- `tests/libs/backtest/test_monte_carlo.py`
- `docs/CONCEPTS/monte-carlo-backtesting.md`

---

### T5.6: Backtest Regression Harness

**Effort:** 2-3 days | **PR:** `feat(p4): backtest regression harness`
**Status:** ⏳ Pending
**Priority:** P1 (Prevents strategy drift)
**Dependencies:** T1.6 (Dataset Versioning), T5.1, T5.2

**Deliverables:**
- Golden backtest results with fixed seeds and dataset versions
- Automated regression tests in CI
- Alert on metric drift > threshold
- Dataset version pinning via DatasetVersionManager

**Golden Data Governance:**
```
tests/regression/golden_results/
├── manifest.json                      # Version info, regeneration history
├── momentum_2020_2022.json            # Golden metrics
├── momentum_2020_2022_config.json     # Alpha config + seed
├── value_2020_2022.json
├── value_2020_2022_config.json
└── README.md                          # Governance documentation
```

**Manifest Schema:**
```json
{
  "version": "v1.0.0",
  "created_at": "2025-12-09T00:00:00Z",
  "dataset_snapshot_id": "golden_v1.0.0",
  "regeneration_triggers": [
    "Major alpha logic change",
    "Dataset schema change",
    "Quarterly refresh (optional)"
  ],
  "last_regenerated": "2025-12-09T00:00:00Z",
  "regenerated_by": "scripts/generate_golden_results.py",
  "storage_size_mb": 0.5,
  "golden_files": [
    {"name": "momentum_2020_2022.json", "checksum": "sha256:abc123..."},
    {"name": "value_2020_2022.json", "checksum": "sha256:def456..."}
  ]
}
```

`storage_size_mb` = sum of all `golden_files` sizes in **decimal megabytes** (1 MB = 1,000,000 bytes, NOT 1,048,576 binary), rounded to 2 decimal places using Python `round()` (banker's rounding / half-even). Formula: `round(sum(os.path.getsize(f) for f in golden_files) / 1_000_000, 2)`

**Note:** Decimal MB (SI units) differs from binary MB displayed by `ls -lh` (uses 1,048,576). This is intentional for cross-platform consistency. Add `"storage_size_note": "Decimal MB (1MB = 1,000,000 bytes)"` to manifest if clarity is needed.

**Governance Rules:**
1. **Naming:** `{alpha_name}_{start_year}_{end_year}.json`
2. **Regeneration triggers:** Major alpha logic change, dataset schema change
3. **Storage limit:** <10MB total (summary metrics only, no time-series)
4. **Review process:** Golden regeneration requires PR review
5. **Staleness alert:** CI warns if manifest >90 days old

**Implementation:**
```python
# tests/regression/test_backtest_golden.py
import pytest
from datetime import date
from pathlib import Path

from libs.alpha.research_platform import PITBacktester, BacktestResult
from libs.data_quality.versioning import DatasetVersionManager

GOLDEN_RESULTS_DIR = Path(__file__).parent / "golden_results"
METRIC_TOLERANCE = 0.001  # 0.1% tolerance for floating point

@pytest.fixture
def pinned_backtester(version_manager: DatasetVersionManager):
    """Backtester pinned to golden dataset version."""
    # Pin to specific snapshot for reproducibility
    version_manager.set_active_snapshot("golden_v1.0.0")
    return PITBacktester(version_manager, ...)

class TestBacktestGoldenResults:
    """Regression tests against golden backtest results."""

    def test_momentum_alpha_metrics(self, pinned_backtester):
        """Verify momentum alpha produces expected metrics."""
        golden = load_golden_result("momentum_2020_2022.json")

        result = pinned_backtester.run_backtest(
            alpha=MomentumAlpha(lookback=20),
            start_date=date(2020, 1, 1),
            end_date=date(2022, 12, 31),
            weight_method="zscore",
        )

        assert_metrics_match(result, golden, tolerance=METRIC_TOLERANCE)

    def test_value_alpha_metrics(self, pinned_backtester):
        """Verify value alpha produces expected metrics."""
        ...

def assert_metrics_match(
    actual: BacktestResult,
    expected: dict,
    tolerance: float,
) -> None:
    """Assert all key metrics match within tolerance."""
    metrics = ["mean_ic", "icir", "hit_rate", "coverage", "long_short_spread"]

    for metric in metrics:
        actual_val = getattr(actual, metric)
        expected_val = expected[metric]
        diff = abs(actual_val - expected_val)

        assert diff <= tolerance, (
            f"Metric {metric} drifted: expected {expected_val}, got {actual_val} "
            f"(diff={diff:.6f}, tolerance={tolerance})"
        )

def load_golden_result(filename: str) -> dict:
    """Load golden result from fixture file."""
    path = GOLDEN_RESULTS_DIR / filename
    with open(path) as f:
        return json.load(f)


# scripts/generate_golden_results.py (one-time regeneration)
def _hash_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(results_dir: Path) -> None:
    """Write manifest with per-file checksums for reproducibility validation."""
    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "golden_files": [],
    }
    for file_path in sorted(results_dir.glob("*.json")):
        manifest["golden_files"].append({
            "file": file_path.name,
            "sha256": _hash_file(file_path),
        })
    (results_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))


# tests/regression/test_backtest_golden.py (validation hook)
def test_golden_manifest_checksums():
    manifest = json.loads((GOLDEN_RESULTS_DIR / "manifest.json").read_text())
    for entry in manifest["golden_files"]:
        path = GOLDEN_RESULTS_DIR / entry["file"]
        assert _hash_file(path) == entry["sha256"], "Golden result checksum mismatch"
```

**Files to Create:**
- `tests/regression/__init__.py`
- `tests/regression/test_backtest_golden.py`
- `tests/regression/conftest.py` (fixtures for pinned data)
- `tests/regression/golden_results/momentum_2020_2022.json`
- `tests/regression/golden_results/value_2020_2022.json`
- `scripts/generate_golden_results.py` (one-time generation script)
- `docs/CONCEPTS/backtest-regression.md`

**CI Integration:**
```yaml
# .github/workflows/backtest-regression.yml
name: Backtest Regression

on:
  push:
    paths:
      - 'libs/alpha/**'
      - 'libs/backtest/**'
      - 'libs/factors/**'

jobs:
  regression:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Setup Python
        uses: actions/setup-python@v5
      - name: Fail if golden manifest is stale (>90 days)
        run: |
          python - <<'PY'
          import json, datetime, pathlib, sys
          manifest = pathlib.Path('tests/regression/golden_results/manifest.json')
          data = json.loads(manifest.read_text())
          last = datetime.datetime.fromisoformat(data['last_regenerated'].replace('Z','+00:00'))
          if (datetime.datetime.now(datetime.timezone.utc) - last).days > 90:
              print('::error::Golden manifest older than 90 days; regenerate fixtures')
              sys.exit(1)
          PY
      - name: Run regression tests
        run: |
          pytest tests/regression/ -v --tb=short
      - name: Alert on drift
        if: failure()
        run: |
          echo "::error::Backtest regression detected! Review metric changes."
```

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

---

## Acceptance Criteria (Measurable)

### T5.1 Job Queue
- [ ] Jobs can be enqueued with 3 priority levels (high/normal/low)
- [ ] Same configuration produces identical job ID (unit test)
- [ ] Worker processes high-priority jobs before low (integration test)
- [ ] Progress updates written to Redis every ≤30 seconds during execution
- [ ] Queued jobs cancelled immediately; running jobs cancelled within 30 seconds (cooperative)
- [ ] Failed jobs retry 3 times with intervals [60s, 300s, 900s]
- [ ] Jobs exceeding 4GB RSS are killed by worker memory monitor

### T5.2 Result Storage
- [ ] Results persist across Postgres restart (integration test)
- [ ] Can filter by user, alpha, date range, status (query test with 100+ records)
- [ ] Retention cleanup deletes jobs older than N days (N=90 default, verified by test)
- [ ] BacktestResult → Parquet → BacktestResult round-trip produces identical metrics

### T5.3 Web UI
- [ ] Form validation prevents invalid date ranges (end < start)
- [ ] UI polls Redis every 5 seconds; progress reflects worker updates (≤30s emit cadence)
- [ ] Equity curve renders for backtests with 1000+ daily points
- [ ] Side-by-side comparison shows ≥5 metrics for 2+ backtests
- [ ] Failed/expired jobs display error message clearly

### T5.4 Walk-Forward
- [ ] Window generator produces non-overlapping test periods
- [ ] Train period ≥ min_train_samples (252) for each window
- [ ] Overfitting ratio = mean(train_ic) / mean(test_ic) computed correctly
- [ ] Edge case: handles backtests spanning <2 windows gracefully

### T5.5 Monte Carlo
- [ ] 1000 simulations complete in <10 seconds for 500-day backtest
- [ ] Confidence intervals: lower_5 < median < upper_95 (invariant)
- [ ] Fixed seed=42 produces identical CI vectors across runs (unit test)
- [ ] P-value in [0, 1] range with documented interpretation

### T5.6 Regression
- [ ] Golden manifest includes dataset_snapshot_id and checksum per file
- [ ] Tests fail when any metric drifts > 0.001 (0.1%)
- [ ] CI runs only on changes to libs/alpha/, libs/backtest/, libs/factors/
- [ ] GitHub Actions annotation emitted on failure with drift details
- [ ] Staleness warning if manifest.last_regenerated > 90 days old

---

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

---

## Related Documents

- [P4_PLANNING.md](./P4_PLANNING.md) - Overall P4 planning
- [P4T2_TASK.md](./P4T2_TASK.md) - Alpha research framework (dependency)
- [docs/CONCEPTS/execution-algorithms.md](../CONCEPTS/execution-algorithms.md) - Execution algorithms and trade quality concepts
- [libs/alpha/research_platform.py](../../libs/alpha/research_platform.py) - PITBacktester implementation

---

**Last Updated:** 2025-12-09
**Status:** 📋 Planning
**Next Step:** Review and approval, then start T5.1
