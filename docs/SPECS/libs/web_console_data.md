# web_console_data

<!-- Last reviewed: 2026-02-28 - P6T15: ExposureQueries adapter with REPEATABLE READ isolation, combined fallback, best-effort reset -->

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `StrategyScopedDataAccess` | user, db_pool | accessor | Strategy-scoped database queries with encryption and authorization. |
| `get_strategy_positions` | strategies, db_pool | ExposureQueryResult | Fail-closed strategy-to-position mapping with ambiguous symbol exclusion (P6T15). |

## Behavioral Contracts
### StrategyScopedDataAccess
**Purpose:** Provide data access layer for web console with strategy-scoped authorization.

**Features:**
- Strategy-scoped data access with encryption
- User authorization checks based on role permissions
- Data isolation per user/strategy
- Query result caching
- Database connection management

### ExposureQueries (P6T15)
**Purpose:** Provide fail-closed strategy-to-position mapping for exposure calculations.

**Features:**
- Replicates `DatabaseClient.get_positions_for_strategies()` SQL in `libs/` layer (avoids libs→apps circular dependency)
- `HAVING COUNT(DISTINCT strategy_id) = 1` ensures ambiguous symbols (traded by multiple strategies) are excluded
- `BOOL_OR(strategy_id = ANY(%s))` scopes excluded-symbol counting across authorized and unauthorized strategies
- Combined CTE query (`symbol_strategy`, `ambiguous`, `unmapped`) returns positions and counts in a single statement
- Transaction runs under `REPEATABLE READ` so all statements (main CTE + optional fallback) share the same database snapshot; isolation level is best-effort reset to server default in a `finally` block (logs warning on reset failure)
- Returns `ExposureQueryResult` NamedTuple with `positions: list[dict]`, `excluded_symbol_count: int`, and `unmapped_position_count: int`
- Unmapped positions: non-zero positions with no order-to-strategy mapping tracked via `unmapped` CTE
- Single combined fallback query fires when the main CTE returns zero rows (ensures excluded/unmapped counts are still available)

### Invariants
- Users can only access strategies they're authorized for
- All queries respect strategy boundaries
- Sensitive data is encrypted at rest
- `verify_job_ownership()` uses `created_by` column (not `strategy_id`) with `user_id → username` fallback for identity matching
- Generic "Access denied" error for both not-found and unauthorized jobs (prevents enumeration)

## Data Flow
```
user + strategy -> authorization check -> database query -> encrypted results
```
- **Input format:** User credentials, strategy IDs, query parameters.
- **Output format:** Strategy-specific data (positions, orders, P&L, etc.).
- **Side effects:** Database reads, result caching.

## Usage Examples
### Example 1: Query strategy data
```python
from libs.web_console_data import StrategyScopedDataAccess

accessor = StrategyScopedDataAccess(user, db_pool)
positions = await accessor.get_positions(strategy_id="alpha_baseline")
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Unauthorized strategy | user lacks permission | `PermissionError` raised |
| Missing strategy | non-existent strategy_id | Empty result set |
| Database timeout | slow query | Timeout with graceful error |

## Dependencies
- **Internal:** `libs.core.common.db`, `libs.platform.web_console_auth.permissions`
- **External:** PostgreSQL, psycopg

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | No | `postgresql://trader:trader@localhost:5433/trader` | PostgreSQL connection string. |
| `DATABASE_CONNECT_TIMEOUT` | No | `2` | Database connection timeout (seconds). |

## Error Handling
- `PermissionError` for unauthorized access
- `DatabaseError` for connection/query failures

## Security
- Role-based access control enforced
- Strategy-scoped data isolation
- Encrypted sensitive data

## Testing
- **Test Files:** `tests/libs/web_console_data/`
- **Run Tests:** `pytest tests/libs/web_console_data -v`
- **Coverage:** N/A

## Related Specs
- `web_console_auth.md` - Authentication and authorization
- `web_console_services.md` - Services that use this data layer

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-02-28 (P6T15 - ExposureQueries adapter with REPEATABLE READ isolation, combined fallback, best-effort reset)
- **Source Files:** `libs/web_console_data/__init__.py`, `libs/web_console_data/strategy_scoped_queries.py`, `libs/web_console_data/exposure_queries.py`
- **ADRs:** N/A
