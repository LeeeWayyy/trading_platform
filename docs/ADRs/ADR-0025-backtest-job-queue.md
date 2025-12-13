# ADR 0025: Backtest Job Queue Infrastructure

- Status: Accepted
- Date: 2025-12-12

## Context

The Alpha Research Platform (PITBacktester) executes CPU-intensive backtests that can take minutes to hours. Currently, backtests run synchronously in the main process, blocking the web console and preventing users from:
- Running multiple backtests concurrently
- Monitoring progress during execution
- Cancelling long-running jobs
- Recovering from worker failures

We need a robust job queue infrastructure for async backtest execution with progress tracking, cooperative cancellation, and failure recovery.

## Decision

### Technology Choices

1. **RQ (Redis Queue) over Celery** - Simpler, Redis-native, sufficient for our scale
2. **Postgres for job state, Redis for progress** - DB is source of truth, Redis for fast polling
3. **psycopg with ConnectionPool** - Direct psycopg (not SQLAlchemy) per project standards
4. **Cooperative cancellation via JobCancelled exception** - Clean shutdown, not SIGKILL

### Architecture

```
┌─────────────────┐     ┌───────────────┐     ┌──────────────────┐
│  Web Console    │────▶│  Redis + RQ   │────▶│  Backtest Worker │
│  (enqueue)      │     │  (3 queues)   │     │  (3 replicas)    │
└─────────────────┘     └───────────────┘     └──────────────────┘
                               │                       │
                               ▼                       ▼
                        ┌───────────────┐     ┌──────────────────┐
                        │  Progress     │     │  PITBacktester   │
                        │  (Redis keys) │     │  (with callbacks)│
                        └───────────────┘     └──────────────────┘
```

### Key Components

1. **libs/backtest/job_queue.py** - BacktestJobQueue with enqueue/cancel/status
2. **libs/backtest/worker.py** - BacktestWorker with progress tracking and memory monitoring
3. **apps/backtest_worker/** - Dockerized RQ worker with multi-stage build
4. **db/migrations/0008_create_backtest_jobs.sql** - Job state schema

### Priority Queues

Three priority levels to prevent starvation:
- `backtest_high` - User-triggered interactive backtests
- `backtest_normal` - Standard scheduled backtests
- `backtest_low` - Bulk/batch backtests

### Idempotency

Job ID = SHA256(alpha_name + dates + weight_method + params + created_by)[:32]
- Same config from same user returns existing job (no duplicates)
- Different users get separate jobs for same config

### Progress Tracking

- Redis key `backtest:progress:{job_id}` with JSON payload (pct, stage, current_date)
- 30-second throttling via time.monotonic() to reduce Redis writes
- DB sync every 10% for fallback when Redis keys expire
- Heartbeat key for watchdog to detect lost workers

### Cooperative Cancellation

1. User calls cancel_job() → sets Redis flag `backtest:cancel:{job_id}`
2. Worker's cancel_check callback detects flag
3. Worker raises JobCancelled exception
4. try/finally ensures cache cleanup
5. DB status updated to 'cancelled'

### Memory Guardrails

- psutil monitors RSS usage (default 4GB limit)
- MemoryError raised if limit exceeded
- Worker container has memory limits in docker-compose

### Failure Recovery

- RQ retry with exponential backoff: [60s, 300s, 900s]
- Watchdog cron marks jobs failed if heartbeat lost
- Heal-loop breaker: max 3 heals/hour per job to prevent infinite requeue

## Consequences

### Benefits

- Users can run multiple backtests concurrently
- Real-time progress visibility in web console
- Clean cancellation without data corruption
- Automatic retry for transient failures
- Horizontal scaling via worker replicas

### Risks

- Redis is single point of failure for progress (mitigated by DB fallback)
- Memory limit may kill large backtests (configurable per container)
- RQ has limited advanced features vs Celery (acceptable for our scale)

### Migration

- New jobs use job queue; existing code paths unaffected
- PITBacktester callbacks are optional (backward compatible)
- Gradual rollout: start with web console, then add scheduled jobs

### Follow-ups

- T5.2: Web Console Job Management API
- T5.3: Streamlit backtest dashboard with progress bars
- T5.4: Scheduled backtest automation
