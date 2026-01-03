# backtest_worker

## Identity
- **Type:** Service (RQ worker)
- **Port:** N/A (no HTTP server)
- **Container:** `apps/backtest_worker/Dockerfile`

## Interface
### RQ Worker
| Component | Description |
|----------|-------------|
| `Worker(queues)` | RQ worker processing backtest queues, with retry handler registration. |

## Behavioral Contracts
### Worker startup (`main`)
**Purpose:** Validate environment, verify Redis connectivity, register retry hook, start worker.

**Preconditions:**
- `REDIS_URL` and `DATABASE_URL` are set.
- Redis is reachable.

**Postconditions:**
- Worker processes queues with registered retry handler.

**Behavior:**
1. Validate `REDIS_URL` and `DATABASE_URL` are set.
2. Ping Redis; exit on failure.
3. Determine queues from `RQ_QUEUES` or default to priority queues.
4. Register `record_retry` as exception handler.
5. Start worker loop.

**Raises:**
- `SystemExit` if required env vars are missing or Redis is unreachable.

## Data Flow
```
RQ backtest queue (high/normal/low)
  -> Worker executes backtest jobs
  -> retry handler records retries in DB
```

## Dependencies
- **Internal:** `libs.backtest.worker.record_retry`
- **External:** Redis, RQ, structlog

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `REDIS_URL` | Yes | N/A | Redis connection for RQ. |
| `DATABASE_URL` | Yes | N/A | Required for retry handler DB writes. |
| `RQ_QUEUES` | No | `backtest_high,backtest_normal,backtest_low` | Comma-separated queues to process. |

## Observability
- **Logs:** structlog for startup and errors.
- **Metrics:** None in worker entrypoint.

## Security
- No HTTP surface area.
- Relies on secure Redis and DB credentials.

## Testing
- **Test Files:** N/A (no dedicated tests found under `tests/apps/backtest_worker/`).
- **Run Tests:** N/A

## Usage Examples
### Example 1: Start the worker
```bash
export REDIS_URL=redis://localhost:6379/0
export DATABASE_URL=postgresql://trader:trader@localhost:5433/trader
python apps/backtest_worker/entrypoint.py
```

### Example 2: Override queues
```bash
RQ_QUEUES=backtest_high,backtest_normal   python apps/backtest_worker/entrypoint.py
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Missing env vars | `REDIS_URL` or `DATABASE_URL` unset | Worker exits before processing jobs. |
| Redis unreachable | Connection timeout | Worker exits with error. |
| Empty RQ_QUEUES | `RQ_QUEUES=""` | Falls back to default queues. |

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Related Specs
- `docs/SPECS/libs/backtest.md`

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `apps/backtest_worker/entrypoint.py`
- **ADRs:** N/A
