# common

<!-- Last reviewed: 2026-01-16 - MarketHours type hint fix for pandas Timestamp.date() return -->

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `TradingPlatformError` | message | exception | Base exception type. |
| `RateLimitConfig` | limits | model | Rate limit configuration. |
| `rate_limit` | request, config | response | Dependency for FastAPI rate limiting. |
| `TimestampSerializerMixin` | model | mixin | JSON serialization for datetimes. |
| `get_required_secret` | key | str | Fetch secret (raises if missing). |
| `get_optional_secret` | key | str | Fetch secret (optional). |
| `hash_file_sha256` | path | str | Hash file contents. |
| `acquire_connection` | db_pool | connection | Acquire database connection from pool (context manager). |
| `get_db_pool` | - | AsyncConnectionPool | Get or create async database connection pool. |
| `get_sync_db_pool` | - | ConnectionPool | Get or create sync database connection pool. |
| `validate_symbol` | symbol | bool | Validate stock symbol format. |
| `validate_date` | date_str | bool | Validate date string format. |
| `validate_email` | email | bool | Validate email address format. |
| `MarketHours` | - | class | Market session state and timing service. |
| `SessionState` | - | enum | Market session states (OPEN, PRE_MARKET, POST_MARKET, CLOSED). |

## Behavioral Contracts
### rate_limit(...)
**Purpose:** Apply Redis-backed rate limiting in API routes.

### Secrets helpers
**Purpose:** Provide a unified wrapper around `libs.secrets` and caching.

### Database connection pooling
**Purpose:** Manage PostgreSQL connection pools for both async and sync operations.

**Features:**
- Automatic pool creation and management
- Connection timeout handling
- Environment-based configuration
- Both async (psycopg) and sync connection pools

### Validation utilities
**Purpose:** Input validation for common data types (symbols, dates, emails).

### MarketHours (P6T2)
**Purpose:** Determine market session state and timing for trading UI components.

**Features:**
- Session state detection: OPEN, PRE_MARKET, POST_MARKET, CLOSED
- Next transition time calculation
- Support for NYSE/NASDAQ (via exchange_calendars) and crypto (24/7)
- Timezone-aware calculations with US/Eastern

**Methods:**
- `get_session_state(exchange)` - Returns current SessionState enum
- `get_next_transition(exchange)` - Returns datetime of next state change
- `time_to_next_transition(exchange)` - Returns timedelta to next change
- `is_trading_day(exchange, date)` - Returns bool for trading day check

### Invariants
- Secrets must be fetched via shared manager to avoid backend duplication.
- Database pools are singletons per process.
- Connections must be released after use (via context manager).

## Data Flow
```
request -> rate limiter -> allow/deny
secret key -> secret manager -> value
app -> db_pool -> postgres connection
input -> validator -> bool (valid/invalid)
```
- **Input format:** FastAPI requests, secret identifiers, database queries, validation inputs.
- **Output format:** rate-limit responses, secret values, database connections, validation results.
- **Side effects:** Redis counters, secret cache updates, database connection pooling.

## Usage Examples
### Example 1: Rate limit dependency
```python
from libs.core.common import rate_limit, RateLimitConfig

config = RateLimitConfig(per_minute=30)
app.get("/path", dependencies=[Depends(rate_limit(config))])
```

### Example 2: Fetch secret
```python
from libs.core.common import get_required_secret

api_key = get_required_secret("alpaca/api_key_id")
```

### Example 3: Database connection pooling
```python
from libs.core.common.db import acquire_connection
from libs.core.common.db_pool import get_db_pool

# Async usage
db_pool = await get_db_pool()
async with acquire_connection(db_pool) as conn:
    async with conn.cursor() as cur:
        await cur.execute("SELECT * FROM orders WHERE id = %s", (order_id,))
        result = await cur.fetchone()
```

### Example 4: Input validation
```python
from libs.core.common.validators import validate_symbol, validate_email

if not validate_symbol("AAPL"):
    raise ValueError("Invalid symbol")

if not validate_email("user@example.com"):
    raise ValueError("Invalid email")
```

### Example 5: Market hours service
```python
from libs.common.market_hours import MarketHours, SessionState

market = MarketHours()
state = market.get_session_state("NYSE")

if state == SessionState.OPEN:
    print("Market is open")
elif state == SessionState.CLOSED:
    next_open = market.get_next_transition("NYSE")
    print(f"Market opens at {next_open}")
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Redis timeout | rate limit store down | Default to allow with metric increment. |
| Missing secret | key not found | `SecretNotFoundError` raised. |
| Invalid file path | hash missing | `FileNotFoundError`. |

## Dependencies
- **Internal:** `libs.secrets`, `libs.platform.secrets`
- **External:** Redis (rate limiting), PostgreSQL (database connections), psycopg (async/sync drivers), exchange_calendars (market hours)

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | No | `postgresql://trader:trader@localhost:5433/trader` | PostgreSQL connection string. |
| `DATABASE_CONNECT_TIMEOUT` | No | `2` | Database connection timeout (seconds). |

## Error Handling
- Custom exceptions in `libs.common.exceptions`.

## Security
- Secrets are fetched via centralized manager; values not logged.

## Testing
- **Test Files:** `tests/libs/core/common/`
- **Run Tests:** `pytest tests/libs/common -v`
- **Coverage:** N/A

## Related Specs
- `secrets.md`
- `redis_client.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-15 (P6T2: Added MarketHours service for market session state)
- **Source Files:** `libs/core/common/__init__.py`, `libs/core/common/rate_limit_dependency.py`, `libs/core/common/secrets.py`, `libs/core/common/db.py`, `libs/core/common/db_pool.py`, `libs/core/common/sync_db_pool.py`, `libs/core/common/validators.py`, `libs/common/market_hours.py`
- **ADRs:** N/A
- **Tasks:** P6T2 (Market Clock & Session State)
