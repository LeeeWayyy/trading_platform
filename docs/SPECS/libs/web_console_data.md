# web_console_data

<!-- Last reviewed: 2026-02-01 - P6T10 mypy type annotation fixes for strategy_scoped_queries.py -->

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `StrategyScopedDataAccess` | user, db_pool | accessor | Strategy-scoped database queries with encryption and authorization. |

## Behavioral Contracts
### StrategyScopedDataAccess
**Purpose:** Provide data access layer for web console with strategy-scoped authorization.

**Features:**
- Strategy-scoped data access with encryption
- User authorization checks based on role permissions
- Data isolation per user/strategy
- Query result caching
- Database connection management

### Invariants
- Users can only access strategies they're authorized for
- All queries respect strategy boundaries
- Sensitive data is encrypted at rest

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
- **Last Updated:** 2026-01-15
- **Source Files:** `libs/web_console_data/__init__.py`, `libs/web_console_data/strategy_scoped_queries.py`
- **ADRs:** N/A
