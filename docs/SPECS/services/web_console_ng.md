# web_console_ng

## Identity
- **Type:** Service (NiceGUI + FastAPI endpoints)
- **Port:** `WEB_CONSOLE_NG_PORT` (default 8080)
- **Container:** `apps/web_console_ng/Dockerfile`

## Interface
### Public API Endpoints (NiceGUI / FastAPI)
| Endpoint | Method | Parameters | Returns |
|----------|--------|------------|---------|
| `/login` | GET | Query `next` (optional) | Login UI (NiceGUI page). |
| `/auth/callback` | GET | `code`, `state` | OAuth2 callback handler; sets auth cookies and redirects. |
| `/mfa-verify` | GET/POST | Page params | MFA verification UI. |
| `/forgot-password` | GET | None | Password recovery UI. |
| `/dashboard` | GET | None | Real-time trading dashboard (P5T4). |
| `/kill-switch` | GET | None | Kill switch management page (P5T5). |
| `/manual-order` | GET | None | Manual order entry page (P5T5). |
| `/position-management` | GET | None | Position management with bulk actions (P5T5). |
| `/risk` | GET | None | Risk analytics dashboard with VaR, factor exposures, stress tests (P5T6). |
| `/healthz` | GET | None | Liveness probe (always 200 unless process unhealthy). |
| `/readyz` | GET | Internal probe headers optional | Readiness probe (checks Redis/backend). |
| `/metrics` | GET | Internal probe headers optional | Prometheus metrics. |
| `/spike/request` | GET | N/A | Experimental spike page (dev only). |

## Behavioral Contracts
### Startup (`main.py`)
**Purpose:** Initialize core services, middleware, and connection handlers.

**Behavior:**
1. Initialize optional DB pool and audit logger.
2. Initialize session store, state manager, and async trading client.
3. Add middleware in order: TrustedHost -> Admission -> Session -> Auth.
4. Register health endpoints and lifecycle hooks.
5. On startup: open DB pool, start trading client and audit logger, inject disconnect overlay.
6. On shutdown: close trading client, audit logger, DB pool, state manager, and Redis.

### Admission + Session Middleware
**Purpose:** Enforce connection limits and session validation before WebSocket upgrades.

**Behavior:**
- Admission control checks connection limits (global and per-session).
- Session middleware validates cookies and device binding before auth middleware.

### Health + Metrics
**Purpose:** Expose internal liveness/readiness and Prometheus metrics.

**Behavior:**
- `/healthz` returns liveness status without dependency checks.
- `/readyz` returns 503 during drain or if Redis/backends are unavailable.
- `/metrics` is protected by internal probe checks unless ingress is secured.

### Real-Time Dashboard (P5T4)
**Purpose:** Display live positions, orders, and account metrics with real-time updates.

**Behavior:**
- Positions grid uses AG Grid with `getRowId` for efficient delta updates via `applyTransaction`.
- Orders table displays open orders with cancel functionality.
- Account metrics (equity, buying power, P&L) update in real-time.
- WebSocket connection with automatic reconnection and disconnect overlay.
- `RealtimeUpdater` subscribes to Redis pub/sub channels for live data.
- `ClientLifecycleManager` handles cleanup on disconnect.

**Components:**
- `components/positions_grid.py` - AG Grid for positions with close position action.
- `components/orders_table.py` - AG Grid for open orders with cancel action.
- `core/realtime.py` - WebSocket subscription management via Redis pub/sub.
- `core/client_lifecycle.py` - Client connection lifecycle and cleanup.
- `core/synthetic_id.py` - Deterministic synthetic ID generation for orders missing client_order_id.

### Kill Switch Management (P5T5)
**Purpose:** Emergency trading halt with real-time status and safety confirmations.

**Behavior:**
- Displays current kill switch state (ENGAGED/DISENGAGED/UNKNOWN).
- Real-time status updates via Redis pub/sub subscription.
- Engage requires reason (min 10 chars) and single confirmation.
- Disengage requires admin role, resolution notes, and two-factor confirmation (type "CONFIRM").
- Unknown state disables both buttons for safety (fail-closed).
- All actions are audit-logged.

**Safety Pattern:**
- Double-check pattern: verify kill switch state at both preview and confirmation.
- Fail-closed: unknown states block action until verified.

### Manual Order Entry (P5T5)
**Purpose:** Submit manual orders with safety checks and audit trail.

**Behavior:**
- Form validation: symbol required, qty >= 1 (whole numbers), reason >= 10 chars.
- Supports market and limit orders with time-in-force options (day, gtc, ioc, fok).
- Kill switch check at preview AND confirmation (double-check pattern).
- Order preview dialog shows all order details before submission.
- Backend generates deterministic `client_order_id` for idempotency.
- All submissions are audit-logged with order details and reason.

**Access Control:**
- Viewers cannot submit orders (redirected to home).
- Traders and admins can submit orders.

### Position Management (P5T5)
**Purpose:** Bulk position operations with safety confirmations.

**Behavior:**
- Close individual positions via positions grid (uses `close_position` endpoint).
- Cancel All Orders: requires confirmation, uses `cancel_all_orders` endpoint.
- Flatten All Positions: requires confirmation + type "FLATTEN", uses `flatten_all_positions`.
- Kill switch blocks close-position but allows risk-reducing cancels.
- Circuit breaker tripped state shows warning but allows risk-reducing closes.

**Safety Checks:**
- Kill switch state checked before close position dialog opens.
- Fresh kill switch check at confirmation time (double-check pattern).
- Synthetic order IDs (`unknown_*`) block cancel attempts with support message.

### Risk Analytics Dashboard (P5T6)
**Purpose:** Display portfolio risk analytics including VaR/CVaR, factor exposures, and stress test results.

**Behavior:**
- Displays risk overview metrics (total risk, factor risk, specific risk).
- VaR/CVaR metrics with risk budget utilization gauge.
- 30-day VaR history chart with risk limit threshold line.
- Factor exposure bar chart with canonical factor ordering.
- Stress test results table with factor contribution waterfall for worst-case scenario.
- Auto-refresh every 60 seconds with refresh lock to prevent concurrent calls.
- Placeholder warning banner when using demo data (risk model artifacts unavailable).
- Error state banner with retry option when data load fails.

**Data Handling:**
- Safe float conversion for all numeric values (rejects NaN/inf as invalid).
- Division by zero protection in drawdown calculations.
- Proper date sorting using datetime parsing (handles ISO formats and timezone normalization).
- Section-specific validators for VaR vs overview metrics.
- Skip entries with invalid data rather than displaying misleading 0.0 values.

**Components:**
- `components/drawdown_chart.py` - Drawdown visualization with division-by-zero protection.
- `components/equity_curve_chart.py` - Cumulative returns chart with non-finite filtering.
- `components/pnl_chart.py` - P&L equity curve and drawdown charts with date sorting.
- `components/var_chart.py` - VaR metrics, gauge, and history chart.
- `components/factor_exposure_chart.py` - Factor exposure bar chart with display names.
- `components/stress_test_results.py` - Stress test table and factor waterfall chart.

**Access Control:**
- Requires `VIEW_PNL` permission.
- Requires at least one authorized strategy.

## Data Flow
```
Browser
  -> NiceGUI pages (/login, /mfa-verify, /dashboard, /kill-switch, /manual-order, /position-management, /risk)
  -> Session store (Redis)
  -> Execution Gateway (AsyncTradingClient)
  -> Audit log (Postgres, optional)
  -> Metrics/Health endpoints for probes

Real-Time Updates (P5T4/P5T5):
  Redis Pub/Sub channels
    -> RealtimeUpdater (WebSocket)
    -> NiceGUI UI updates (positions, orders, kill switch status)
    -> ClientLifecycleManager (cleanup on disconnect)

Trading Actions Flow:
  User action (close position, cancel order, submit order)
    -> Kill switch check (fail-closed)
    -> Confirmation dialog (double-check pattern)
    -> Fresh kill switch check at confirmation
    -> AsyncTradingClient API call
    -> Audit log
    -> UI notification

Risk Analytics Flow (P5T6):
  /risk page load
    -> RiskService.get_risk_dashboard_data()
    -> StrategyScopedDataAccess (risk_metrics, factor_exposures, stress_tests, var_history)
    -> Chart components (var_chart, factor_exposure_chart, stress_test_results, pnl_chart)
    -> Plotly visualizations in NiceGUI
    -> Auto-refresh timer (60s) with refresh lock
```

## Dependencies
- **Internal:** `apps/web_console_ng/auth/*`, `apps/web_console_ng/core/*`, `apps/web_console_ng/ui/*`, `apps/web_console_ng/components/*`, `apps/web_console_ng/pages/*`, `apps/web_console/services/risk_service.py`, `apps/web_console/data/strategy_scoped_queries.py`, `apps/web_console/utils/validators.py`
- **External:** Redis (session + pub/sub), Postgres (optional, audit), Execution Gateway API, NiceGUI, AG Grid, Plotly, Prometheus

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `WEB_CONSOLE_NG_HOST` | No | `0.0.0.0` | Bind host. |
| `WEB_CONSOLE_NG_PORT` | No | `8080` | Bind port. |
| `WEB_CONSOLE_NG_DEBUG` | No | `false` | Debug mode; allows dev auth defaults. |
| `WEB_CONSOLE_AUTH_TYPE` | Yes (prod) | N/A | Auth type (`dev`, `basic`, `mtls`, `oauth2`). |
| `EXECUTION_GATEWAY_URL` | No | `http://localhost:8002` | Backend API base URL. |
| `REDIS_URL` | No | `redis://localhost:6379/1` | Session store Redis. |
| `REDIS_USE_SENTINEL` | No | `false` | Enable Redis Sentinel. |
| `ALLOWED_HOSTS` | No | `localhost,127.0.0.1,::1` | TrustedHost middleware. |
| `TRUSTED_PROXY_IPS` | No | N/A | Trusted proxies for client IP.
| `SESSION_IDLE_TIMEOUT_MINUTES` | No | `15` | Session idle timeout.
| `SESSION_ABSOLUTE_TIMEOUT_HOURS` | No | `4` | Absolute session timeout.
| `SESSION_COOKIE_*` | No | Varies | Cookie security settings.
| `INTERNAL_PROBE_TOKEN` | No | N/A | Internal probe auth for /metrics and /readyz.
| `HEALTH_CHECK_BACKEND_ENABLED` | No | `false` | Enable backend health checks in readiness.
| `WS_MAX_CONNECTIONS` | No | `1000` | Max WebSocket connections per pod.
| `WS_MAX_CONNECTIONS_PER_SESSION` | No | `2` | Max connections per session.
| `DB_POOL_MIN_SIZE` | No | `1` | Async DB pool min size.
| `DB_POOL_MAX_SIZE` | No | `5` | Async DB pool max size.
| `FEATURE_RISK_DASHBOARD` | No | `true` | Enable/disable risk analytics dashboard.
| `RISK_BUDGET_VAR_LIMIT` | No | `0.05` | Maximum VaR limit for risk budget gauge (5%).
| `RISK_BUDGET_WARNING_THRESHOLD` | No | `0.8` | Warning threshold for risk utilization (80%).

## Observability
- **Health:** `GET /healthz` (liveness), `GET /readyz` (readiness).
- **Metrics:** `GET /metrics` (internal/protected).
- **Logs:** Auth events, connection handling, Redis/backend health warnings.

## Security
- Auth middleware supports dev/basic/mTLS/OAuth2.
- Session cookies include secure/HttpOnly options and CSRF cookie where applicable.
- Trusted proxies and allowed hosts enforced.
- Admission control prevents connection exhaustion.
- Internal probe token protects /metrics and /readyz (configurable).

## Testing
- **Test Files:** `tests/apps/web_console_ng/`
- **Run Tests:** `pytest tests/apps/web_console_ng -v`

## Usage Examples
### Example 1: Liveness check
```bash
curl -s http://localhost:8080/healthz
```

### Example 2: Readiness check (internal probe)
```bash
curl -s -H "X-Internal-Probe: $INTERNAL_PROBE_TOKEN" http://localhost:8080/readyz
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Missing probe token | `GET /readyz` without header | 401/403 or 503 (if internal check fails). |
| Redis unavailable | Session store down | `/readyz` returns 503. |
| Connection limit exceeded | Too many WS connections | Admission control rejects. |
| Kill switch ENGAGED | Close position attempt | Action blocked with error notification. |
| Kill switch UNKNOWN | Any trading action | Both engage/disengage disabled; action blocked. |
| Viewer role | Access `/manual-order` | Redirected to home with warning. |
| Synthetic order ID | Cancel `unknown_*` order | Cancel blocked; "contact support" message. |
| Fractional quantity | Submit order with 1.5 qty | Validation error; whole numbers required. |
| Circuit breaker TRIPPED | Close position | Warning shown; close allowed (risk reduction). |
| Order missing IDs | Order without client_order_id or broker_id | Synthetic ID generated; user notified to contact support. |

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Related Specs
- `docs/SPECS/services/execution_gateway.md`
- `docs/SPECS/services/auth_service.md`
- `docs/SPECS/libs/web_console_auth.md`

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `apps/web_console_ng/main.py`, `apps/web_console_ng/config.py`, `apps/web_console_ng/core/health.py`, `apps/web_console_ng/core/metrics.py`, `apps/web_console_ng/core/realtime.py`, `apps/web_console_ng/core/client_lifecycle.py`, `apps/web_console_ng/core/client.py`, `apps/web_console_ng/core/audit.py`, `apps/web_console_ng/core/synthetic_id.py`, `apps/web_console_ng/auth/routes.py`, `apps/web_console_ng/components/positions_grid.py`, `apps/web_console_ng/components/orders_table.py`, `apps/web_console_ng/components/drawdown_chart.py`, `apps/web_console_ng/components/equity_curve_chart.py`, `apps/web_console_ng/components/pnl_chart.py`, `apps/web_console_ng/components/var_chart.py`, `apps/web_console_ng/components/factor_exposure_chart.py`, `apps/web_console_ng/components/stress_test_results.py`, `apps/web_console_ng/pages/dashboard.py`, `apps/web_console_ng/pages/kill_switch.py`, `apps/web_console_ng/pages/manual_order.py`, `apps/web_console_ng/pages/position_management.py`, `apps/web_console_ng/pages/risk.py`
- **ADRs:** N/A
- **Tasks:** P5T4 (Real-Time Dashboard), P5T5 (Manual Trading Controls), P5T6 (Charts & Analytics)
