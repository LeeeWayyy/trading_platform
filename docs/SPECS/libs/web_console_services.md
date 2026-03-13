# web_console_services

<!-- Last reviewed: 2026-03-13 - P6T16+P6T17: TaxLotService, UserManagement, ModelRegistryBrowserService, StrategyService, AlertConfigService -->

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `AlertConfigService` | db_pool, redis | service | Alert configuration and rule management with pagination, filtering, bulk acknowledge, and PagerDuty support (P6T17). |
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
| `TaxLotService` | db_pool, user | service | CRUD service for tax lots with user-scoped queries, cost basis method management (FIFO/LIFO/specific_id), open/closed status tracking, and cross-user access via `all_users` flag. RBAC: VIEW_TAX_LOTS, MANAGE_TAX_LOTS, MANAGE_TAX_SETTINGS (P6T16). |
| `UserManagement` (module-level functions) | db_pool, audit_logger, admin_user_id | varies | User provisioning, role changes with self-edit and last-admin guards, strategy grant/revoke with existence checks, and bulk operations. All mutations use explicit transactions and audit all denial paths (P6T16). |
| `AttributionService` | data_access, ff_provider, crsp_provider | service | Fama-French factor attribution (FF3/FF5/FF6 models) for portfolio returns. |
| `BacktestAnalyticsService` | data_access, storage | service | Backtest analytics with quantile analysis, universe signal loading, and portfolio return retrieval (net→gross fallback). |
| `SqlExplorerService` | rate_limiter | service | Defense-in-depth SQL query execution with DuckDB sandbox, AST validation, sensitive table blocking, and audit logging (P6T14). |
| `DataSourceStatusService` | redis_client_factory, db_pool_factory | service | Data source freshness monitoring with Redis-backed refresh locking and distributed status aggregation (P6T14). |
| `ShadowResultsService` | data_root | service | Shadow/paper trading results browser with Parquet file discovery and comparison metrics (P6T14). |
| `ExposureService` | - | service | Strategy net exposure aggregation with mock fallback, bias warnings, and partial data detection (P6T15). |
| `UniverseService` | manager | service | Async, permission-aware universe management wrapping synchronous UniverseManager. List, detail, preview, create, delete universes; analytics (summary stats, distributions, mock sector/factor data) and side-by-side comparison with overlap metrics. RBAC (P6T15). |
| `ModelRegistryBrowserService` | db_pool | service | Model registry browser with RBAC, activate/deactivate model operations (P6T17). |
| `StrategyService` | db_pool, audit_logger | service | Strategy management with RBAC, admin-only toggle active/inactive, and audit logging (P6T17). |

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

### TaxLotService
**Purpose:** CRUD service for tax lots with user-scoped data isolation and cost basis management.

**Key Operations:**
- `list_lots(user_id, all_users, open_only, limit)` - List lots (user-scoped, filterable by open status, max 500)
- `get_lot(lot_id, user_id, all_users)` - Fetch single lot by ID (user-scoped)
- `create_lot(symbol, quantity, cost_basis, acquisition_date, strategy_id, status, user_id)` - Create new lot (requires MANAGE_TAX_LOTS)
- `update_lot(lot_id, updates, user_id, all_users)` - Update lot fields with SELECT FOR UPDATE concurrency control
- `close_lot(lot_id, user_id, all_users)` - Close lot by zeroing remaining quantity
- `get_cost_basis_method(user_id)` / `set_cost_basis_method(method, user_id)` - Manage per-user cost basis method (fifo/lifo/specific_id)

**RBAC:**
- VIEW_TAX_LOTS: list and get operations (own lots)
- MANAGE_TAX_LOTS: create, update, close, and cross-user access
- MANAGE_TAX_SETTINGS: get/set cost basis method for other users

**Behavioral Notes:**
- All queries are user-scoped by default; `all_users=True` requires MANAGE_TAX_LOTS
- Status derived from `remaining_quantity` and `closed_at` columns (not stored directly)
- `strategy_id` is NOT persisted to DB; carried in returned `TaxLot` for UI display only
- Updates use `SELECT ... FOR UPDATE` to prevent concurrent overwrites
- Quantity-only updates (without cost_basis) preserve total_cost and recalculate cost_per_share (logged as warning)
- Quantity updates cap `remaining_quantity` to the new quantity value (prevents remaining > total invariant violation)

### UserManagement (module)
**Purpose:** User provisioning, role management, and strategy access control with full audit trail.

**Key Operations:**
- `list_users(db_pool)` - List all users with strategy counts
- `change_user_role(db_pool, user_id, new_role, admin_user_id, audit_logger, reason)` - Change role with guards
- `ensure_user_provisioned(db_pool, user_id, default_role, admin_user_id, audit_logger)` - Bootstrap user_roles row (ON CONFLICT DO NOTHING)
- `list_strategies(db_pool)` / `get_user_strategies(db_pool, user_id)` - Strategy listing
- `grant_strategy(db_pool, user_id, strategy_id, admin_user_id, audit_logger)` - Grant with existence check
- `revoke_strategy(db_pool, user_id, strategy_id, admin_user_id, audit_logger)` - Revoke with existence check
- `bulk_change_roles(...)` / `bulk_grant_strategy(...)` / `bulk_revoke_strategy(...)` - Bulk variants (each operation independent)

**Guards (P6T16):**
- **Self-edit guard:** Admins cannot change their own role
- **Last-admin guard:** Cannot demote the sole remaining admin (locks all admin rows + target row ordered by user_id to prevent deadlocks)

**Audit Trail:**
- All denial paths (invalid role, user not found, no-op, self-edit, last-admin, already granted, not assigned, strategy not found) emit audit log entries
- Success paths use `audit_logger.log_admin_change()`
- DB errors are caught, audit-logged, and returned as `(False, message)`

**Session Invalidation:**
- Role changes increment `session_version` in the UPDATE statement
- Strategy grant/revoke session invalidation handled by DB trigger (`0007_strategy_session_version_triggers.sql`)

### Invariants
- All service operations enforce RBAC permissions
- Circuit breaker state changes are audited
- SQL queries are validated before execution
- User authorization checked for dataset access
- TaxLotService queries are user-scoped by default (cross-user requires explicit flag + permission)
- UserManagement mutations use explicit transactions for atomicity
- All denied user management attempts are audit-logged

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
- **Last Updated:** 2026-03-13 (P6T16+P6T17)
- **Source Files:** `libs/web_console_services/__init__.py`, `libs/web_console_services/alert_service.py`, `libs/web_console_services/alpha_explorer_service.py`, `libs/web_console_services/attribution_service.py`, `libs/web_console_services/backtest_analytics_service.py`, `libs/web_console_services/cb_metrics.py`, `libs/web_console_services/cb_rate_limiter.py`, `libs/web_console_services/cb_service.py`, `libs/web_console_services/comparison_service.py`, `libs/web_console_services/config.py`, `libs/web_console_services/data_explorer_service.py`, `libs/web_console_services/data_quality_service.py`, `libs/web_console_services/data_source_status_service.py`, `libs/web_console_services/data_sync_service.py`, `libs/web_console_services/duckdb_connection.py`, `libs/web_console_services/exposure_service.py`, `libs/web_console_services/health_service.py`, `libs/web_console_services/model_registry_browser_service.py`, `libs/web_console_services/notebook_launcher_service.py`, `libs/web_console_services/risk_service.py`, `libs/web_console_services/scheduled_reports_service.py`, `libs/web_console_services/shadow_results_service.py`, `libs/web_console_services/sql_explorer_service.py`, `libs/web_console_services/sql_validator.py`, `libs/web_console_services/strategy_service.py`, `libs/web_console_services/tax_lot_service.py`, `libs/web_console_services/universe_service.py`, `libs/web_console_services/user_management.py`, `libs/web_console_services/schemas/`, `libs/web_console_services/schemas/universe.py`
- **ADRs:** N/A
