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

## Data Flow
```
Browser
  -> NiceGUI pages (/login, /mfa-verify, /dashboard, /kill-switch, /manual-order, /position-management)
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
```

## Dependencies
- **Internal:** `apps/web_console_ng/auth/*`, `apps/web_console_ng/core/*`, `apps/web_console_ng/ui/*`, `apps/web_console_ng/components/*`, `apps/web_console_ng/pages/*`
- **External:** Redis (session + pub/sub), Postgres (optional, audit), Execution Gateway API, NiceGUI, AG Grid, Prometheus

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
- **Source Files:** `apps/web_console_ng/main.py`, `apps/web_console_ng/config.py`, `apps/web_console_ng/core/health.py`, `apps/web_console_ng/core/metrics.py`, `apps/web_console_ng/core/realtime.py`, `apps/web_console_ng/core/client_lifecycle.py`, `apps/web_console_ng/core/client.py`, `apps/web_console_ng/core/audit.py`, `apps/web_console_ng/core/synthetic_id.py`, `apps/web_console_ng/auth/routes.py`, `apps/web_console_ng/components/positions_grid.py`, `apps/web_console_ng/components/orders_table.py`, `apps/web_console_ng/pages/dashboard.py`, `apps/web_console_ng/pages/kill_switch.py`, `apps/web_console_ng/pages/manual_order.py`, `apps/web_console_ng/pages/position_management.py`
- **ADRs:** N/A
- **Tasks:** P5T4 (Real-Time Dashboard), P5T5 (Manual Trading Controls)
