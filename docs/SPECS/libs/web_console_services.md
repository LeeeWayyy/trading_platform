# web_console_services

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `AlertConfigService` | db_pool, redis | service | Alert configuration and rule management. |
| `AlphaExplorerService` | db_pool, redis | service | Alpha signal exploration and backtesting. |
| `CircuitBreakerService` | redis, db_pool | service | Circuit breaker control, monitoring, and trip/reset operations. |
| `ComparisonService` | db_pool | service | Strategy comparison and performance analytics. |
| `DataExplorerService` | db_pool, redis | service | Dataset exploration with SQL validation and query execution. |
| `DataQualityService` | db_pool, redis | service | Data quality monitoring, alerts, and anomaly detection. |
| `DataSyncService` | db_pool, redis | service | Data synchronization scheduling and status tracking. |
| `HealthMonitorService` | redis, prometheus | service | System health monitoring and service status aggregation. |
| `NotebookLauncherService` | config | service | Jupyter notebook launcher for research workflows. |
| `RiskService` | db_pool, redis | service | Risk analytics, VaR calculation, and stress testing. |
| `ScheduledReportsService` | db_pool, user, trading_client_factory | service | Report scheduling, generation, and delivery. Uses DI for trading client. |
| `SQLValidator` | - | validator | SQL query validation and sanitization for data explorer. |
| `TaxLotService` | db_pool | service | Tax lot tracking and wash sale detection. |
| `UserManagement` | db_pool, redis | service | User management and role assignment. |

## Behavioral Contracts
### CircuitBreakerService
**Purpose:** Manage circuit breaker state and enforce trading halts.

**Key Operations:**
- `get_status()` - Get current breaker state (OPEN/TRIPPED)
- `trip(reason, user)` - Manually trip breaker (requires TRIP_CIRCUIT permission)
- `reset(reason, user)` - Reset breaker after conditions cleared (requires RESET_CIRCUIT permission)
- `get_history(limit)` - Retrieve trip/reset history

**RBAC:**
- TRIP_CIRCUIT: operator, admin
- RESET_CIRCUIT: operator, admin
- VIEW_STATUS: all roles

**Rate Limiting:**
- Reset operations: max 1 per minute (global)

### DataExplorerService
**Purpose:** Execute validated SQL queries against datasets with user authorization.

**Features:**
- SQL injection prevention via SQLValidator
- Strategy-scoped access control
- Query result pagination
- DuckDB integration for analytics

### RiskService
**Purpose:** Real-time risk analytics and portfolio monitoring.

**Metrics:**
- Value at Risk (VaR) calculation
- Portfolio stress testing
- Factor exposure analysis
- Position concentration monitoring

### Invariants
- All service operations enforce RBAC permissions
- Circuit breaker state changes are audited
- SQL queries are validated before execution
- User authorization checked for dataset access

## Data Flow
```
user request -> RBAC check -> service operation -> database/redis -> response
```
- **Input format:** User credentials, service-specific parameters (strategy IDs, queries, configurations).
- **Output format:** Service-specific DTOs (risk metrics, alert configs, comparison results).
- **Side effects:** Database writes, Redis caching, audit logging, Prometheus metrics.

## Usage Examples
### Example 1: Circuit breaker operations
```python
from libs.web_console_services import CircuitBreakerService

cb_service = CircuitBreakerService(redis, db_pool)
status = cb_service.get_status()  # {"state": "OPEN", "trip_count_today": 0}

# Trip breaker (requires operator/admin role)
user = {"user_id": "admin", "role": "admin"}
cb_service.trip("MANUAL", user, acknowledged=True)

# Reset after conditions cleared
cb_service.reset("Conditions normalized, verified system health", user, acknowledged=True)
```

### Example 2: Data quality alerts
```python
from libs.web_console_services import DataQualityService

dq_service = DataQualityService()
alerts = await dq_service.get_active_alerts(user)
await dq_service.acknowledge_alert(user, alert_id="alert-123", reason="Investigating data gap")
```

### Example 3: Risk analytics
```python
from libs.web_console_services import RiskService

risk_service = RiskService(db_pool, redis)
var = await risk_service.calculate_var(strategy_id="alpha_baseline", confidence=0.95)
stress_results = await risk_service.run_stress_test(strategy_id="alpha_baseline", scenario="market_crash")
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Unauthorized operation | user lacks permission | `PermissionError` (HTTP 403) |
| Rate limit exceeded | too many reset attempts | `RateLimitExceeded` error |
| Invalid SQL query | SQL injection attempt | `ValidationError` from SQLValidator |
| Missing dataset | non-existent dataset_id | Empty result set or `DatasetNotFoundError` |

## Dependencies
- **Internal:** `libs.platform.web_console_auth`, `libs.platform.web_console_data`, `libs.core.common`, `libs.trading.risk_management.breaker`
- **External:** PostgreSQL, Redis, DuckDB, prometheus_client

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | No | `postgresql://trader:trader@localhost:5433/trader` | PostgreSQL connection string. |
| `DATABASE_CONNECT_TIMEOUT` | No | `2` | Database connection timeout (seconds). |
| `EXECUTION_GATEWAY_URL` | No | `http://localhost:8002` | Execution gateway API URL. |
| `REDIS_URL` | No | `redis://localhost:6379` | Redis connection string. |

## Error Handling
- `PermissionError` (RBACViolation) - User lacks required permission
- `ValidationError` - Invalid input (short reset reason, invalid SQL, etc.)
- `RateLimitExceeded` - Rate limit hit (circuit breaker resets)
- `CircuitBreakerError` - Breaker state change failures

## Observability
### Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| `cb_status_checks_total` | Counter | - | Circuit breaker status check count |
| `cb_trip_total` | Counter | reason | Circuit breaker trip count by reason |
| `cb_reset_total` | Counter | - | Circuit breaker reset count |
| `data_quality_alerts_total` | Counter | severity, dataset | Data quality alert count |
| `risk_calculations_total` | Counter | metric_type | Risk calculation count |

## Security
- Role-based access control (RBAC) enforced for all operations
- Audit logging for sensitive operations (circuit breaker, user management)
- SQL injection prevention via query validation
- Rate limiting for destructive operations

## Testing
- **Test Files:** `tests/apps/web_console/services/`
- **Run Tests:** `pytest tests/apps/web_console/services -v`
- **Coverage:** >90% for all service modules

## Related Specs
- `web_console_auth.md` - Authentication and authorization
- `web_console_data.md` - Data access layer
- `web_console_ng.md` - Web console UI that consumes these services

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| Runtime import | LOW | scheduled_reports_service has runtime import of apps.web_console_ng (inside try/except) | Migration cleanup |

## Metadata
- **Last Updated:** 2026-01-14
- **Source Files:** `libs/web_console_services/__init__.py`, `libs/web_console_services/alert_service.py`, `libs/web_console_services/alpha_explorer_service.py`, `libs/web_console_services/cb_metrics.py`, `libs/web_console_services/cb_rate_limiter.py`, `libs/web_console_services/cb_service.py`, `libs/web_console_services/comparison_service.py`, `libs/web_console_services/config.py`, `libs/web_console_services/data_explorer_service.py`, `libs/web_console_services/data_quality_service.py`, `libs/web_console_services/data_sync_service.py`, `libs/web_console_services/duckdb_connection.py`, `libs/web_console_services/health_service.py`, `libs/web_console_services/notebook_launcher_service.py`, `libs/web_console_services/risk_service.py`, `libs/web_console_services/scheduled_reports_service.py`, `libs/web_console_services/sql_validator.py`, `libs/web_console_services/tax_lot_service.py`, `libs/web_console_services/user_management.py`, `libs/web_console_services/schemas/`
- **ADRs:** N/A
