# alert_worker

## Identity
- **Type:** Service (RQ worker)
- **Port:** N/A (no HTTP server)
- **Container:** `apps/alert_worker/Dockerfile`

## Interface
### RQ Job Entry Point
| Function | Parameters | Returns | Description |
|----------|------------|---------|-------------|
| `execute_delivery_job` | `delivery_id`, `channel`, `recipient`, `subject`, `body`, `attempt=0` | `dict[str, str | bool | None]` | RQ job wrapper that runs async delivery executor in an event loop. |

## Behavioral Contracts
### `execute_delivery_job(...)`
**Purpose:** Execute a single alert delivery attempt and record result.

**Preconditions:**
- `REDIS_URL` and `DATABASE_URL` are set.
- `channel` is one of `email`, `slack`, `sms` (validated by `ChannelType`).

**Postconditions:**
- Delivery result is returned as a dict suitable for RQ serialization.
- Async resources (DB pool, Redis) are closed even if delivery fails.

**Behavior:**
1. Builds per-job async resources (DB pool, Redis client, poison queue, rate limiter).
2. Builds `DeliveryExecutor` with channel handlers and retry scheduler.
3. Executes delivery and returns result payload.
4. Always closes async resources; raises if cleanup fails.

**Raises:**
- `ValueError` if `channel` is invalid.
- Propagates exceptions from delivery or cleanup.

### Worker startup (`main`)
**Purpose:** Validate environment and start an RQ worker for the alerts queue.

**Preconditions:**
- `REDIS_URL` and `DATABASE_URL` are set and reachable.

**Postconditions:**
- Worker starts and processes `alerts` queue (or `RQ_QUEUES` override).

**Behavior:**
1. Validate required env vars.
2. Verify DB connectivity (`SELECT 1`) and Redis ping.
3. Sync poison queue count and queue depth metrics.
4. Start RQ worker with scheduler enabled.

## Data Flow
```
RQ job (alerts queue)
  -> execute_delivery_job
    -> DeliveryExecutor
      -> Channel (Email/Slack/SMS)
      -> DB writes (delivery status, poison queue)
      -> Redis (rate limiting, queue depth)
      -> Optional retry scheduling (enqueue_in)
```

## Dependencies
- **Internal:** `libs.alerts.*`, `libs.web_console_auth.rate_limiter`, `libs.common.exceptions`
- **External:** Redis, Postgres (psycopg/psycopg_pool), RQ, Twilio (SMS channel), Slack/email providers

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `REDIS_URL` | Yes | N/A | Redis connection for RQ and rate limiting. |
| `DATABASE_URL` | Yes | N/A | Postgres connection for delivery state and poison queue. |
| `RQ_QUEUES` | No | `alerts` | Comma-separated queue list. |
| `TWILIO_ACCOUNT_SID` | No | N/A | Required for SMS channel. |
| `TWILIO_AUTH_TOKEN` | No | N/A | Required for SMS channel. |
| `TWILIO_FROM_NUMBER` | No | N/A | Required for SMS channel. |

## Observability
- **Logs:** Structured logs on startup failures, delivery attempts, and warnings.
- **Metrics:** Startup syncs poison queue count and queue depth into Redis/DB via `QueueDepthManager`.

## Security
- Rate limiting enforced via `RateLimiter` backed by Redis.
- SMS channel disabled if Twilio secrets are missing.

## Testing
- **Test Files:** `tests/apps/alert_worker/`
- **Run Tests:** `pytest tests/apps/alert_worker -v`

## Usage Examples
### Example 1: Start the worker
```bash
export REDIS_URL=redis://localhost:6379/0
export DATABASE_URL=postgresql://trader:trader@localhost:5433/trader
python apps/alert_worker/entrypoint.py
```

### Example 2: Enqueue a delivery job
```python
from redis import Redis
from rq import Queue
from apps.alert_worker.entrypoint import execute_delivery_job

redis = Redis.from_url("redis://localhost:6379/0")
queue = Queue("alerts", connection=redis)
queue.enqueue(
    execute_delivery_job,
    "delivery-1",
    "email",
    "user@example.com",
    "Alert Subject",
    "Alert body text",
)
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Missing env vars | `REDIS_URL` or `DATABASE_URL` unset | Worker exits before processing jobs. |
| Invalid channel | `channel="pager"` | `ValueError` raised; job fails. |
| Redis unavailable | Connection failure on startup | Worker fails fast; logs error. |

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Related Specs
- `docs/SPECS/libs/alerts.md`
- `docs/SPECS/libs/web_console_auth.md`
- `docs/SPECS/libs/redis_client.md`

## Metadata
- **Last Updated:** 2026-01-09
- **Source Files:** `apps/alert_worker/entrypoint.py`
- **ADRs:** N/A
