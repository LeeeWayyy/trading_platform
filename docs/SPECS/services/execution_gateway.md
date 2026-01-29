# Execution Gateway

<!-- Last reviewed: 2026-01-29 - Added parent_order_id filter to /orders/pending endpoint -->

## Identity
- **Type:** Service
- **Port:** 8002
- **Container:** N/A

## Interface
### Public API Endpoints
| Endpoint | Method | Parameters | Returns |
|----------|--------|------------|---------|
| `/` | GET | None | Service metadata JSON |
| `/health` | GET | None | `HealthResponse` |
| `/api/v1/config` | GET | None | `ConfigResponse` |
| `/api/v1/fat-finger/thresholds` | GET | None | `FatFingerThresholdsResponse` |
| `/api/v1/fat-finger/thresholds` | PUT | `FatFingerThresholdsUpdateRequest` | `FatFingerThresholdsResponse` |
| `/api/v1/strategies` | GET | None | `StrategiesListResponse` |
| `/api/v1/strategies/{strategy_id}` | GET | Path `strategy_id` | `StrategyStatusResponse` |
| `/api/v1/kill-switch/engage` | POST | `KillSwitchEngageRequest` | Kill-switch status JSON |
| `/api/v1/kill-switch/disengage` | POST | `KillSwitchDisengageRequest` | Kill-switch status JSON |
| `/api/v1/kill-switch/status` | GET | None | Kill-switch status JSON |
| `/api/v1/reconciliation/status` | GET | None | Reconciliation status JSON |
| `/api/v1/reconciliation/run` | POST | None | Status JSON |
| `/api/v1/reconciliation/force-complete` | POST | `ReconciliationForceCompleteRequest` | Status JSON |
| `/api/v1/orders` | POST | `OrderRequest` | `OrderResponse` |
| `/api/v1/orders/{client_order_id}` | GET | Path `client_order_id` | `OrderDetail` |
| `/api/v1/orders/{client_order_id}/cancel` | POST | Path `client_order_id` | Cancel result JSON |
| `/api/v1/orders/slice` | POST | `SlicingRequest` | `SlicingPlan` |
| `/api/v1/orders/{parent_id}/slices` | GET | Path `parent_id` | `list[OrderDetail]` |
| `/api/v1/orders/{parent_id}/slices` | DELETE | Path `parent_id` | Delete result JSON |
| `/api/v1/positions` | GET | Query filters | `PositionsResponse` |
| `/api/v1/positions/pnl/realtime` | GET | Query filters | `RealtimePnLResponse` |
| `/api/v1/performance/daily` | GET | Query filters | `DailyPerformanceResponse` |
| `/api/v1/webhooks/orders` | POST | Alpaca webhook payload | Status JSON |
| `/api/v1/orders/{order_id}/cancel` | POST | Manual controls | `CancelOrderResponse` |
| `/api/v1/orders/cancel-all` | POST | Manual controls | `CancelAllOrdersResponse` |
| `/api/v1/positions/{symbol}/close` | POST | Manual controls | `ClosePositionResponse` |
| `/api/v1/positions/{symbol}/adjust` | POST | Manual controls | `AdjustPositionResponse` |
| `/api/v1/positions/flatten-all` | POST | Manual controls | `FlattenAllResponse` |
| `/api/v1/orders/pending` | GET | Manual controls | `PendingOrdersResponse` |
| `/api/v1/orders/recent-fills` | GET | Manual controls | `RecentFillsResponse` |
| `/api/v1/manual/orders` | POST | `ManualOrderRequest` | `OrderResponse` |
| `/api/v1/reconciliation/fills-backfill` | POST | `ReconciliationFillsBackfillRequest` | Status JSON |
| `/metrics` | GET | None | Prometheus metrics |

## Behavioral Contracts
### Key Functions
#### submit_order(order: OrderRequest) -> OrderResponse
**Purpose:** Submit idempotent orders with safety checks and optional broker execution.

**Preconditions:**
- Kill-switch and circuit breaker are not tripped (fail-closed when unavailable).
- Order parameters pass fat-finger and liquidity checks.

**Postconditions:**
- Order is recorded in DB with deterministic `client_order_id`.
- If `DRY_RUN=false`, an Alpaca order is submitted.

**Behavior:**
1. Build deterministic `client_order_id` from order params + date.
2. Enforce RBAC permission `SUBMIT_ORDER` and rate limit.
3. Validate risk limits, liquidity, and fat-finger thresholds.
4. Persist order and submit to Alpaca (unless DRY_RUN).

**Raises:**
- `HTTPException 400/422` for validation/rejection errors.
- `HTTPException 503` for broker or safety service unavailability.

#### slice_order(request: SlicingRequest) -> SlicingPlan
**Purpose:** Produce TWAP slicing plan (and optional scheduling) for large orders.

**Preconditions:**
- Same safety checks as `submit_order`.

**Postconditions:**
- Returns plan and (when enabled) schedules slices for execution.

#### submit_manual_order(request: ManualOrderRequest) -> OrderResponse
**Purpose:** Submit manual orders with circuit breaker checks and audit trail.

**Preconditions:**
- Circuit breaker is not tripped (double-check: before DB AND before broker).
- User has `SUBMIT_ORDER` permission and authorized strategies.

**Postconditions:**
- Order is recorded in DB with `pending_new` status before broker submission.
- If broker submission succeeds, status updated to broker response status.
- If broker submission fails, status updated to `failed` with error message.

**Behavior:**
1. Permission check, rate limit, and strategy scope validation.
2. Circuit breaker check (first check).
3. Create order in DB with `pending_new` status.
4. Circuit breaker check (second check - TOCTOU mitigation).
5. Submit to Alpaca broker.
6. Update order status based on broker response.

**Raises:**
- `HTTPException 403` for permission/strategy authorization failures.
- `HTTPException 409` for duplicate order (idempotent retry returns existing order).
- `HTTPException 422` for validation errors.
- `HTTPException 503` for circuit breaker tripped or broker unavailable.

#### run_fills_backfill(request: ReconciliationFillsBackfillRequest) -> dict
**Purpose:** Backfill fills from Alpaca account activities API.

**Preconditions:**
- User has `MANAGE_RECONCILIATION` permission.
- Not in DRY_RUN mode.

**Postconditions:**
- Fills are appended to order metadata with idempotent deduplication.
- Trades table populated from fills.
- Realized P&L recalculated for affected symbols.

**Behavior:**
1. Fetch FILL activities from Alpaca with pagination overlap.
2. Match fills to local orders by broker_order_id.
3. Insert fills and trades in single atomic transaction.
4. Recalculate P&L within same transaction (rollback on failure).

#### webhook_handler(payload) -> dict
**Purpose:** Ingest Alpaca order updates and reconcile order/position state.

**Preconditions:**
- If `WEBHOOK_SECRET` is configured, HMAC signature must validate.

**Postconditions:**
- Order status updates persisted with source priority `webhook`.

**Behavior:**
1. Validate signature (HMAC SHA256) if secret configured.
2. Parse event and update DB state.
3. Trigger reconciliation and safety updates.

### Invariants
- `client_order_id` is deterministic per (symbol, side, qty, type, price, tif, date).
- Kill-switch and circuit breaker checks run before order submission.
- `DRY_RUN=true` never submits broker orders.
- Webhook signatures are required when `WEBHOOK_SECRET` is configured.
- During startup reconciliation gating, reduce-only orders are allowed (per ADR-0020).

### Database Pooling & Reconciliation Loop
- Database pools are initialized with `open=False` to avoid eager connections during startup/tests.
- Periodic reconciliation waits on the stop event to exit promptly between polls.

## Data Flow
```
OrderRequest -> Auth/RL -> Risk Checks -> DB Write -> Alpaca Submit (if DRY_RUN=false)
                                              |
                                              v
                                     Webhook Updates -> DB Reconcile
```
- **Input format:** JSON order/position requests, Alpaca webhook payloads.
- **Output format:** JSON order/position responses and status summaries.
- **Side effects:** Postgres writes, Redis safety state, Alpaca API calls.

## Dependencies
- **Internal:** `apps/execution_gateway/*`, `libs.risk_management`, `libs.redis_client`, `libs.common.api_auth_dependency`, `libs.common.rate_limit_dependency`, `libs.common.secrets`, `libs.web_console_auth`.
- **External:** Alpaca API, Postgres, Redis, Prometheus.

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ALPACA_API_KEY_ID` | Yes | none | Alpaca API key (secret) |
| `ALPACA_API_SECRET_KEY` | Yes | none | Alpaca secret (secret) |
| `ALPACA_BASE_URL` | No | `https://paper-api.alpaca.markets` | Broker base URL |
| `DATABASE_URL` | Yes | `postgresql://trader:trader@localhost:5433/trader` | Postgres |
| `STRATEGY_ID` | No | `alpha_baseline` | Strategy identifier |
| `DRY_RUN` | No | `true` | Log only, no broker submit |
| `ALPACA_PAPER` | No | `true` | Paper trading flag |
| `CIRCUIT_BREAKER_ENABLED` | No | `true` | Enable breaker checks |
| `LIQUIDITY_CHECK_ENABLED` | No | `true` | Enable liquidity checks |
| `MAX_SLICE_PCT_OF_ADV` | No | `0.01` | Slice size cap |
| `FAT_FINGER_MAX_NOTIONAL` | No | `100000` | Max notional |
| `FAT_FINGER_MAX_QTY` | No | `10000` | Max quantity |
| `FAT_FINGER_MAX_ADV_PCT` | No | `0.05` | Max ADV percent |
| `FAT_FINGER_MAX_PRICE_AGE_SECONDS` | No | `30` | Price staleness |
| `ENVIRONMENT` | No | `dev` | Environment label |
| `REDIS_HOST` | No | `localhost` | Redis host |
| `REDIS_PORT` | No | `6379` | Redis port |
| `REDIS_DB` | No | `0` | Redis DB |
| `REDIS_AUTH_REQUIRED` | No | `false` | Require Redis auth |
| `PERFORMANCE_CACHE_TTL` | No | `300` | Performance cache TTL |
| `MAX_PERFORMANCE_DAYS` | No | `90` | Performance window |
| `FEATURE_PERFORMANCE_DASHBOARD` | No | `false` | Feature dashboard flag |
| `REDUCE_ONLY_LOCK_TIMEOUT_SECONDS` | No | `30` | Reduce-only lock timeout |
| `REDUCE_ONLY_LOCK_BLOCKING_SECONDS` | No | `10` | Lock wait timeout |
| `STRATEGY_ACTIVITY_THRESHOLD_SECONDS` | No | `86400` | Strategy active window |
| `TRUSTED_PROXY_HOSTS` | No | `127.0.0.1` | Proxy allowlist |
| `ORDER_SUBMIT_RATE_LIMIT` | No | `40` | Submit rate limit |
| `ORDER_SLICE_RATE_LIMIT` | No | `10` | Slice rate limit |
| `ORDER_CANCEL_RATE_LIMIT` | No | `100` | Cancel rate limit |
| `WEBHOOK_SECRET` | Cond. | none | Alpaca webhook HMAC secret |
| `INTERNAL_TOKEN_SECRET` | No | empty | HMAC secret for internal headers |
| `INTERNAL_TOKEN_REQUIRED` | No | `true` | Require internal token |
| `INTERNAL_TOKEN_TIMESTAMP_TOLERANCE_SECONDS` | No | `300` | Clock skew |

## Observability (Services only)
### Health Check
- **Endpoint:** `/health`
- **Checks:** DB connectivity, Redis connectivity, Alpaca connectivity (if not DRY_RUN), recovery manager status.

### Metrics
- `execution_gateway_*` counters/histograms for orders, Alpaca calls, health, reconciliation, performance.

## Security
- **Auth Required:** Yes for trading and control endpoints
- **Auth Method:** `libs.common.api_auth_dependency` + RBAC permissions
- **Data Sensitivity:** Confidential (orders, positions)
- **RBAC Roles:** `SUBMIT_ORDER`, `CANCEL_ORDER`, `VIEW_POSITIONS`, `VIEW_PNL`, `MANAGE_STRATEGIES`, `MANAGE_RECONCILIATION`
- **Webhook Security:** HMAC SHA256 signature via `X-Alpaca-Signature` when `WEBHOOK_SECRET` is set

## Testing
- **Test Files:** `tests/apps/execution_gateway/`
- **Run Tests:** `pytest tests/apps/execution_gateway -v`
- **Coverage:** N/A

## Usage Examples
### Example 1: Health check
```bash
curl -s http://localhost:8002/health
```

### Example 2: Submit a market order
```bash
curl -s -X POST http://localhost:8002/api/v1/orders   -H 'Content-Type: application/json'   -d '{"symbol":"AAPL","side":"buy","qty":1,"type":"market","time_in_force":"day"}'
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Kill-switch engaged | `POST /api/v1/orders` | 503 with safety failure. |
| DRY_RUN enabled | `DRY_RUN=true` | Order recorded, no broker submit. |
| Invalid order params | `qty<=0` or invalid `type` | 400/422 validation error. |
| Reconciliation gating (reduce-only) | Sell when long, buy when short | Order allowed (per ADR-0020). |
| Reconciliation gating (increase) | Buy when long, sell when short | 503, blocked until reconciliation completes. |
| Reconciliation gating (broker unavailable) | Any order, Alpaca unreachable | 503, fail-closed. |

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Related Specs
- `signal_service.md`
- `orchestrator.md`
- `../libs/risk_management.md`
- `../libs/redis_client.md`
- `../libs/web_console_auth.md`

## Metadata
- **Last Updated:** 2026-01-19 (Reconciliation package references updated after legacy file removal)
- **Source Files:**
  - `apps/execution_gateway/main.py`
  - `apps/execution_gateway/app_factory.py`
  - `apps/execution_gateway/app_context.py`
  - `apps/execution_gateway/config.py`
  - `apps/execution_gateway/dependencies.py`
  - `apps/execution_gateway/lifespan.py`
  - `apps/execution_gateway/middleware.py`
  - `apps/execution_gateway/metrics.py`
  - `apps/execution_gateway/database.py`
  - `apps/execution_gateway/reconciliation/` (package - refactored from legacy reconciliation module)
    - `__init__.py` (backward-compatible exports)
    - `service.py` (ReconciliationService orchestrator)
    - `state.py` (startup gate and override state)
    - `context.py` (dependency injection context)
    - `orders.py` (order sync and CAS updates)
    - `fills.py` (fill backfill logic)
    - `positions.py` (position reconciliation)
    - `orphans.py` (orphan detection and quarantine)
    - `helpers.py` (pure utility functions)
  - `apps/execution_gateway/alpaca_client.py`
  - `apps/execution_gateway/fat_finger_validator.py`
  - `apps/execution_gateway/liquidity_service.py`
  - `apps/execution_gateway/order_id_generator.py`
  - `apps/execution_gateway/order_slicer.py`
  - `apps/execution_gateway/slice_scheduler.py`
  - `apps/execution_gateway/recovery_manager.py`
  - `apps/execution_gateway/webhook_security.py`
  - `apps/execution_gateway/routes/__init__.py`
  - `apps/execution_gateway/routes/health.py`
  - `apps/execution_gateway/routes/orders.py`
  - `apps/execution_gateway/routes/positions.py`
  - `apps/execution_gateway/routes/reconciliation.py`
  - `apps/execution_gateway/routes/slicing.py`
  - `apps/execution_gateway/routes/webhooks.py`
  - `apps/execution_gateway/routes/admin.py`
  - `apps/execution_gateway/schemas.py`
  - `apps/execution_gateway/schemas_manual_controls.py`
  - `config/settings.py`
- **ADRs:** `docs/ADRs/0014-execution-gateway-architecture.md`
