---
id: REFACTOR-EG
title: "Refactor Execution Gateway for Testability"
phase: Test-Improvement
priority: P0
owner: "@development-team"
state: PENDING_TECH_LEAD
ai_review_status: APPROVED
created: 2026-01-15
dependencies: [TEST_IMPROVEMENT_PLAN]
related_adrs: [ADR-PENDING-EXECUTION-GATEWAY-REFACTOR]
related_docs: [TEST_IMPROVEMENT_PLAN.md]
adr_note: "ADR required before Phase 1 starts - document modular architecture decisions"
---

# REFACTOR-EG: Refactor Execution Gateway for Testability

**Phase:** Test Improvement (Coverage & Parallel CI)
**AI Review Status:** APPROVED (30+ rounds of Codex/Gemini review)
**Implementation Status:** PENDING_TECH_LEAD (requires Tech Lead sign-off before implementation)
**Priority:** P0 (Critical Trading Infrastructure)
**Owner:** @development-team
**Created:** 2026-01-15

---

## Executive Summary

Refactor two large, undertested modules in the execution gateway to improve testability and coverage:

| Module | Current State | Target State |
|--------|---------------|--------------|
| `main.py` | ~5000 lines, 55% coverage | <500 lines, 85%+ coverage |
| `reconciliation.py` | ~1040 lines, 15% coverage | Package structure, 80%+ coverage |

**Rationale:** These modules are in the critical trading path. Low coverage poses trading safety risks. The current monolithic structure makes unit testing impractical.

---

## Objective

Transform the execution gateway from a monolithic architecture into a modular, testable structure while maintaining 100% behavior compatibility.

**Success looks like:**
- `main.py` is a thin composition root (<500 lines)
- Reconciliation logic is split into focused, testable modules
- All routes use FastAPI's `APIRouter` pattern
- Global state replaced with dependency injection
- Coverage improved to 85%+ for main.py, 80%+ for reconciliation
- Zero trading safety regressions
- Transaction boundaries explicitly defined and tested
- Safety gate ordering preserved and verified

---

## Critical Safety Invariants

### Transaction Boundaries (MUST PRESERVE)

The following operations define atomic units. During refactoring, these transaction boundaries MUST NOT be split across module calls.

| Operation | Atomic Unit | Transaction Strategy |
|-----------|-------------|----------------------|
| **Order Submission** | Phase 1: Reservation (Redis Lua) → Idempotency check (DB) → Fat-finger → Phase 2: Broker submit (idempotent) → Phase 3: Persist result (DB txn) | **Reservation-first flow** - Redis atomic reservation BEFORE idempotency; if duplicate found, release reservation and return existing; broker call protected by idempotent client_order_id |
| **Order Fill Processing** | Status update → Fill append → Position update → P&L recalc | Single DB transaction, row lock via `SELECT FOR UPDATE` |
| **Reconciliation Write** | Order status CAS → Fill metadata | Per-order transaction with `source_priority` ordering |
| **Position Sync** | Broker position → DB position snapshot (upsert) | **Separate transaction** from order writes (eventual consistency OK) |
| **Kill-Switch Engage** | Redis state update only | Redis SET only (no DB audit or order cancellation in execution gateway) |

**IMPORTANT: Order Submission Three-Phase Design**

The broker API call is intentionally OUTSIDE the DB transaction because:
1. Network calls inside transactions cause long-held locks
2. The `client_order_id` provides idempotency at the broker level
3. If broker succeeds but DB persist fails, retry re-submits to broker with same `client_order_id`; broker returns existing order; DB persist then succeeds

**Rollback Semantics:**
- Phase 1a (reservation) fails → Position limit exceeded, no side effects
- Phase 1b (idempotency) finds duplicate → Release reservation, return existing order
- Phase 1c (fat-finger) fails → Release reservation, reject order
- Phase 2 (broker) fails → Release reservation, no order at broker
- Phase 3 fails after broker success → Order exists at broker but not in DB; on retry, DB idempotency check finds nothing, broker call returns existing order via broker-level `client_order_id` idempotency, DB persist succeeds (reservation auto-expires via TTL)

**Implementation Rule:** Services use **dependency injection** for DB access (no global connections). Services accept an active `db_connection` or `transaction` object as an argument - they don't create their own. Route handlers own transaction lifecycle. This enables testability (mock DB in tests) and shadow mode isolation (inject ShadowDBConnection).

**Service Layer Boundaries:**
- **Pure helpers** (`helpers.py`): No side effects, no DB/broker/Redis access, 100% testable, stateless functions
- **Services** (`services/*.py`): May have side effects via **injected** dependencies only. Services are NOT "pure" - they coordinate I/O but don't own connections.

**Naming clarification:** The `services/` directory contains coordination logic that uses injected dependencies, NOT pure functions. Pure functions go in `helpers.py` (reconciliation) or route-level helpers. The naming "services" was chosen to distinguish from "pure helpers" - services coordinate, helpers compute.

```python
# CORRECT: Order submission with reservation-first flow (matches actual code)
@router.post("/api/v1/orders")
async def submit_order(order: OrderRequest, ctx: AppContext = Depends(get_context)):
    # Phase 1a: Reserve position FIRST (Redis Lua script - atomic)
    # Position limit check happens atomically via Redis before any DB work
    reservation_result = ctx.position_reservation.reserve(
        symbol=order.symbol,
        side=order.side,
        qty=order.qty,
        max_limit=ctx.config.max_position_per_symbol,
    )
    if not reservation_result.success:
        raise PositionLimitExceeded(reservation_result.error_message)

    # Phase 1b: Check idempotency AFTER reservation (DB query)
    # If duplicate, release reservation and return existing order
    existing = await order_service.check_idempotency(order.client_order_id, ctx.db)
    if existing:
        ctx.position_reservation.release(order.symbol, reservation_result.token)
        return existing  # Idempotent return

    # Phase 1c: Fat-finger validation
    # ... fat finger checks ...

    # Phase 2: Submit to broker (OUTSIDE DB transaction - idempotent via client_order_id)
    try:
        broker_result = await ctx.alpaca.submit_order(order)
    except BrokerError:
        # Rollback: release Redis reservation
        ctx.position_reservation.release(order.symbol, reservation_result.token)
        raise

    # Phase 3: Persist result (DB transaction)
    async with ctx.db.transaction() as conn:
        await order_service.persist_order(order, broker_result, conn)

    return broker_result

# WRONG: Broker call inside transaction (holds locks during network I/O)
async def submit_order_bad(order: OrderRequest, ctx: AppContext):
    async with ctx.db.transaction() as conn:
        # ... checks ...
        broker_result = await ctx.alpaca.submit_order(order)  # BAD: Network in txn!
        # ... persist ...
```

**Required Tests:**
- [ ] `test_order_submission_position_limit_blocks_order` - Verify Redis reservation rejects over-limit orders
- [ ] `test_order_submission_broker_failure_releases_reservation` - Verify Redis reservation token released on broker error
- [ ] `test_order_submission_phase3_failure_idempotent_retry` - Simulate crash after broker submit, verify idempotency on retry
- [ ] `test_order_submission_no_long_held_locks` - Verify no DB transaction spans broker call
- [ ] `test_reconciliation_cas_conflict_no_data_loss` - Concurrent updates preserve higher-priority source
- [ ] `test_fill_processing_atomic_position_update` - Fill + position update in single transaction

---

### Safety Gate Ordering Contract (MUST PRESERVE)

The current `main.py` evaluates safety gates in a specific order. This order MUST be preserved during refactoring.

**Current Gate Evaluation Order (Order Submission):**
```
1. Authentication (FastAPI dependency)
2. Rate Limiting (FastAPI dependency)
3. Request Validation (Pydantic)
4. Kill-Switch Unavailable Check (fail-closed, line ~2744)
5. Circuit Breaker Unavailable Check (fail-closed, line ~2763)
6. Position Reservation Unavailable Check (fail-closed, line ~2786)
7. Kill-Switch Engaged Check (line ~2812)
8. Circuit Breaker Tripped Check (line ~2861)
9. Quarantine Check (fail-closed, line ~2932) - blocks trading on quarantined symbols
10. Reconciliation Gate Check (_require_reconciliation_ready_or_reduce_only, line ~2933)
11. Position Reservation via Redis (line ~2935) - atomic position limit check
12. Idempotency Check (line ~3048) - returns existing order if duplicate
13. Fat-Finger Validation (line ~3078)
14. Order Submission
```

**Note:** Position reservation happens BEFORE idempotency check. If an order already exists,
the reservation is released and the existing order is returned (idempotent). This ordering
ensures position limits are checked atomically even for concurrent duplicate submissions.

**CRITICAL: Kill-Switch and Circuit Breaker checks MUST come BEFORE Reconciliation Gate.**
This ensures fail-closed safety: if Redis is unavailable, orders are blocked immediately
rather than proceeding through reconciliation checks that might also fail.

**CRITICAL:** Gates 4-9 MUST remain INSIDE the route handler, AFTER authentication. Moving these to FastAPI `Depends()` is NOT allowed because:
1. Auth logging must occur before gate checks (security audit requirement)
2. Gate rejections should not trigger for unauthenticated requests
3. The required test `test_unauthenticated_request_rejected_before_gate_checks` enforces this

**Gate Ordering for Other Endpoints (MUST ALSO PRESERVE):**

| Endpoint | Gate Order | Notes |
|----------|------------|-------|
| **Order Cancel** | Auth → Cancel | **No trading gates** - current behavior allows cancel anytime |
| **TWAP/Slice** | Auth → Scheduler Unavail → KS Unavail → Kill-Switch → Quarantine → Recon Gate → Liquidity → Slice | **No CB gates** - current behavior |
| **Admin/Config** | Auth → Admin Permission → (no trading gates) | Admin bypasses trading gates |
| **Kill-Switch Engage** | Auth → Kill-Switch Operator Permission → Engage | Operator permission required |
| **Webhooks** | Signature Auth ONLY → Process | **NO trading gates** - webhooks must always process |
| **Reconciliation Admin** | Auth → Admin Permission → (no trading gates) | Admin bypasses trading gates |

**Legend:** KS Unavail = Kill-Switch Unavailable Check

**Note on Cancel Behavior:** The current implementation allows cancels without trading gates (kill-switch, circuit breaker, reconciliation). This is intentional - operators should always be able to cancel orders even when trading is halted. Preserving this behavior during refactor.

**CRITICAL: Webhooks MUST NOT be gated by kill-switch/circuit-breaker.**
Webhooks receive broker updates (fills, status changes) and must always process,
even when trading is halted. Blocking webhooks would cause state drift.

**Unavailable Check Tests (REQUIRED for gated trading endpoints - order submit + slice):**
```python
def test_order_submit_blocked_when_redis_unavailable():
    """Fail-closed: orders blocked if kill-switch state unknown."""
    disconnect_redis()
    response = client.post("/api/v1/orders", json=ORDER, headers=AUTH)
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"].lower()

def test_slice_blocked_when_redis_unavailable():
    """Fail-closed: TWAP/slice blocked if kill-switch state unknown."""
    disconnect_redis()
    response = client.post("/api/v1/orders/slice", json=TWAP_ORDER, headers=AUTH)
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"].lower()
```

**Gate Ordering Tests for All Routes (REQUIRED):**
```python
def test_cancel_no_trading_gates():
    """Verify cancel has no trading gates - always allowed."""
    engage_kill_switch()
    # Cancel should succeed even with kill-switch engaged
    response = client.post("/api/v1/orders/{client_order_id}/cancel", headers=AUTH)
    assert response.status_code in [200, 404]  # Success or order not found

def test_slice_gate_order():
    """Verify TWAP/slice follows correct gate order (no CB gates)."""
    engage_kill_switch()
    response = client.post("/api/v1/orders/slice", json=TWAP_ORDER, headers=AUTH)
    assert response.status_code == 503
    assert "kill_switch" in response.json()["detail"].lower()

def test_webhook_not_gated_by_kill_switch():
    """CRITICAL: Webhooks MUST process even when kill-switch engaged."""
    engage_kill_switch()
    response = client.post("/api/v1/webhooks/orders", json=FILL_WEBHOOK, headers=WEBHOOK_SIG)
    assert response.status_code == 200  # MUST succeed - webhooks are never gated

def test_webhook_not_gated_by_circuit_breaker():
    """CRITICAL: Webhooks MUST process even when circuit-breaker tripped."""
    trip_circuit_breaker()
    response = client.post("/api/v1/webhooks/orders", json=FILL_WEBHOOK, headers=WEBHOOK_SIG)
    assert response.status_code == 200  # MUST succeed - webhooks are never gated
```

**Gate Ordering Tests (REQUIRED):**
```python
# tests/apps/execution_gateway/test_gate_ordering.py

def test_unauthenticated_request_rejected_before_gate_checks():
    """Verify auth failure doesn't trigger kill-switch/breaker logs."""
    response = client.post("/api/v1/orders", json=ORDER, headers={})
    assert response.status_code == 401
    assert "kill_switch_checked" not in logs

def test_kill_switch_blocks_before_broker_call():
    """Verify kill-switch prevents any broker API call."""
    engage_kill_switch()
    with mock_alpaca() as alpaca:
        response = client.post("/api/v1/orders", json=ORDER, headers=AUTH)
        assert response.status_code == 503
        assert alpaca.submit_order.call_count == 0

def test_gate_order_matches_specification():
    """Verify gates execute in documented order.

    Note: Pydantic validation occurs before route handler entry and is NOT
    logged here. This test verifies only the gates that execute INSIDE
    the route handler after request parsing.
    """
    gate_log = []
    # Inject logging into each gate
    response = client.post("/api/v1/orders", json=ORDER, headers=AUTH)
    # Gates 1-2 (auth, rate_limit) as FastAPI dependencies, gates 3+ in handler
    assert gate_log == [
        "auth", "rate_limit",
        "kill_switch_unavailable", "circuit_breaker_unavailable",
        "position_reservation_unavailable",
        "kill_switch_engaged", "circuit_breaker_tripped",
        "quarantine", "recon_gate", "position_reservation", "idempotency", "fat_finger", "submit"
    ]
```

---

## Current Architecture Analysis

### main.py (~5000 lines, 55% coverage)

**Identified Concerns:**
1. **Mixed Responsibilities:** Routes, middleware, helpers, configuration all in one file
2. **Global State:** `db_client`, `redis_client`, `alpaca_client`, `reconciliation_service` as module globals
3. **Untestable Helpers:** Business logic embedded in route handlers
4. **Metrics Side Effects:** Prometheus metrics defined at module level

**Section Breakdown:**
| Section | Lines | Description |
|---------|-------|-------------|
| Configuration | ~160 | Env parsing, settings |
| Prometheus Metrics | ~150 | Counter/Gauge/Histogram definitions |
| Global State | ~50 | Client instances |
| Lifespan/Startup | ~275 | App lifecycle |
| Auth Middleware | ~200 | Request authentication |
| Helper Functions | ~400 | Fat finger, reconciliation checks, idempotency |
| Exception Handlers | ~50 | Error responses |
| PnL Calculations | ~150 | Position P&L logic |
| Performance Cache | ~200 | Daily performance caching |
| Health Endpoints | ~100 | /, /health |
| Config Endpoints | ~100 | /api/v1/config |
| Strategy Endpoints | ~200 | Strategy management |
| Kill-Switch Endpoints | ~300 | Emergency controls |
| Reconciliation Admin | ~100 | Manual recon triggers |
| Order Submission | ~800 | /api/v1/orders |
| TWAP/Slicing | ~700 | Order slicing logic |
| Position/Performance | ~300 | Position queries |
| Webhooks | ~230 | Alpaca callbacks |

### reconciliation.py (~1040 lines, 15% coverage)

**Identified Concerns:**
1. **God Class:** Single class handling multiple responsibilities
2. **Complex Methods:** `_run_reconciliation` is 120+ lines
3. **Mixed Sync/Async:** Thread locks + async locks
4. **Embedded Business Logic:** Fill calculations, orphan detection, position sync

**Method Breakdown:**
| Method | Lines | Responsibility |
|--------|-------|----------------|
| `run_reconciliation_once` | ~15 | Orchestrator |
| `_run_reconciliation` | ~120 | Main reconciliation logic |
| `_backfill_alpaca_fills` | ~170 | Fill backfill from Alpaca API |
| `_apply_broker_update` | ~50 | Apply broker state to DB |
| `_calculate_synthetic_fill` | ~60 | Pure calculation logic |
| `_backfill_fill_metadata` | ~60 | Fill metadata enrichment |
| `_reconcile_missing_orders` | ~50 | Handle missing orders |
| `_handle_orphan_order` | ~40 | Orphan detection |
| `_reconcile_positions` | ~30 | Position sync |
| Startup/State methods | ~100 | Gate management |

---

## Proposed Architecture

### Target Structure for main.py

```
apps/execution_gateway/
├── main.py                    # Thin composition root only (~200 lines)
├── app_factory.py             # NEW: Wires config, clients, routers, middleware
├── app_context.py             # NEW: AppContext dataclass for dependency injection
├── config.py                  # NEW: Centralized env parsing
├── dependencies.py            # NEW: FastAPI Depends providers
├── metrics.py                 # NEW: Prometheus metrics definitions
├── startup.py                 # NEW: Lifespan startup logic
├── shutdown.py                # NEW: Lifespan shutdown logic
├── exception_handlers.py      # NEW: Exception handlers
├── routes/                    # NEW: APIRouter modules
│   ├── __init__.py
│   ├── orders.py              # /api/v1/orders endpoints (~400 lines)
│   ├── slicing.py             # /api/v1/orders/slice, TWAP logic (~350 lines)
│   ├── positions.py           # /api/v1/positions, /pnl (~200 lines)
│   ├── webhooks.py            # /api/v1/webhooks (~150 lines)
│   ├── admin.py               # Kill-switch, config, strategies (~300 lines)
│   ├── reconciliation.py      # /api/v1/reconciliation admin endpoints (~100 lines)
│   └── health.py              # /, /health (~80 lines)
└── services/                  # NEW: Coordination logic, injected dependencies (NOT pure)
    ├── __init__.py
    ├── pnl_calculator.py      # PnL calculation logic (~150 lines)
    ├── performance_cache.py   # Performance caching (~150 lines)
    ├── order_helpers.py       # Idempotency, fat-finger context (~200 lines)
    └── auth_helpers.py        # Auth context building (~100 lines)
```

### Target Structure for reconciliation.py

```
apps/execution_gateway/reconciliation/
├── __init__.py                # Re-export ReconciliationService
├── service.py                 # Orchestrator (facade) (~150 lines)
├── state.py                   # Gate + override state management (~100 lines)
├── context.py                 # ReconciliationContext for DI (~50 lines)
├── orders.py                  # Order sync, missing order handling (~150 lines)
├── positions.py               # Position reconciliation (~80 lines)
├── fills.py                   # Fill backfill + P&L recalcs (~200 lines)
├── orphans.py                 # Orphan order detection/handling (~100 lines)
└── helpers.py                 # Pure functions (_calculate_synthetic_fill) (~100 lines)
```

---

## Components (Phased Implementation)

### Phase 0: Infrastructure Setup (6h)

**Goal:** Create foundational modules and app factory without changing behavior.

**Tasks:**
- [ ] Create `app_context.py` with `AppContext` dataclass
- [ ] Create `app_factory.py` for clean app composition (enables integration testing from Phase 1)
- [ ] Create `config.py` and move all env parsing
- [ ] **Audit config.py for default value consistency** - Verify all defaults match original main.py exactly
- [ ] Create `metrics.py` and move all Prometheus definitions
- [ ] **Add metrics contract test** - Verify metric names/labels unchanged
- [ ] Create `dependencies.py` with FastAPI Depends providers
- [ ] Update `main.py` to import from new modules
- [ ] Verify CI passes with no behavior changes

**Files Created:**
- `apps/execution_gateway/app_context.py`
- `apps/execution_gateway/app_factory.py`
- `apps/execution_gateway/config.py`
- `apps/execution_gateway/metrics.py`
- `apps/execution_gateway/dependencies.py`

**Configuration Drift Audit Checklist:**
- [ ] `FAT_FINGER_MAX_QTY` default matches original
- [ ] `FAT_FINGER_MAX_NOTIONAL` default matches original
- [ ] `DRY_RUN` default is `true` (fail-safe)
- [ ] `RECONCILIATION_INTERVAL_SECONDS` default matches original
- [ ] All decimal parsing uses same precision/rounding
- [ ] Boolean parsing logic identical (`"true".lower() == "true"`)

**Metrics Contract Test:**
```python
# tests/apps/execution_gateway/test_metrics_contract.py
def test_metrics_names_unchanged():
    """Verify metric names match original to prevent dashboard breakage."""
    from apps.execution_gateway.metrics import METRICS_REGISTRY
    expected_names = [
        "execution_gateway_orders_submitted_total",
        "execution_gateway_orders_filled_total",
        "execution_gateway_order_latency_seconds",
        # ... all original metric names
    ]
    actual_names = [m.name for m in METRICS_REGISTRY]
    assert set(actual_names) == set(expected_names)
```

**Acceptance Criteria:**
- [ ] All tests pass
- [ ] No API behavior changes
- [ ] AppContext stored on `app.state`
- [ ] app_factory.py enables `create_app()` for testing
- [ ] Config audit checklist completed
- [ ] Metrics contract test passes

---

### Phase 1: Extract Pure Helpers + Middleware (8h)

**Goal:** Extract pure business logic and middleware into testable modules.

**Tasks:**
- [ ] Create `services/pnl_calculator.py` with PnL calculation functions
- [ ] Create `services/performance_cache.py` with caching logic
- [ ] Create `services/order_helpers.py` with idempotency/fat-finger helpers
- [ ] Create `services/auth_helpers.py` with auth context building
- [ ] **Extract `middleware.py`** with auth middleware (~200 lines)
- [ ] **Add middleware ordering tests** - Verify auth → rate-limit → request processing order
- [ ] Add comprehensive unit tests for each service module (target: 90% coverage)
- [ ] Update main.py to use service modules

**Files Created:**
- `apps/execution_gateway/services/__init__.py`
- `apps/execution_gateway/services/pnl_calculator.py`
- `apps/execution_gateway/services/performance_cache.py`
- `apps/execution_gateway/services/order_helpers.py`
- `apps/execution_gateway/services/auth_helpers.py`
- `apps/execution_gateway/middleware.py`

**Middleware Ordering Tests:**
```python
# tests/apps/execution_gateway/test_middleware_ordering.py
def test_auth_middleware_runs_before_route():
    """Verify authentication happens before route handler."""
    ...

def test_rate_limit_runs_after_auth():
    """Verify rate limiting uses authenticated user context."""
    ...

def test_middleware_order_matches_specification():
    """Verify middleware executes in documented order."""
    middleware_order = []
    # Inject order tracking
    response = client.get("/health")
    assert middleware_order == ["proxy_headers", "auth", "rate_limit"]
```

**CRITICAL: Webhook Auth Bypass Strategy:**

Webhooks MUST use signature authentication ONLY (no bearer token auth). The global auth
middleware extracted in this phase MUST NOT process webhook routes.

**Implementation Strategy:**
```python
# apps/execution_gateway/middleware.py
class AuthMiddleware:
    """Global auth middleware with explicit bypass for webhook routes."""

    # Routes that bypass bearer token auth (use signature auth instead)
    BYPASS_PATHS = frozenset([
        "/api/v1/webhooks/orders",
        "/api/v1/webhooks/",  # Prefix match for all webhook routes
    ])

    async def __call__(self, request: Request, call_next):
        # Skip bearer token auth for webhook routes
        if any(request.url.path.startswith(p) for p in self.BYPASS_PATHS):
            # Webhook routes use signature auth in route handler, not middleware
            return await call_next(request)

        # Standard bearer token authentication for all other routes
        auth_header = request.headers.get("Authorization")
        # ... validate bearer token ...
        return await call_next(request)
```

**Alternative: Router-Level Segregation:**
```python
# apps/execution_gateway/app_factory.py
def create_app():
    app = FastAPI()

    # Standard routes WITH auth middleware
    authenticated_app = FastAPI()
    authenticated_app.add_middleware(AuthMiddleware)
    authenticated_app.include_router(orders_router)
    authenticated_app.include_router(positions_router)
    # ...

    # Webhook routes WITHOUT auth middleware (use signature auth in handler)
    app.include_router(webhooks_router)  # No middleware
    app.mount("/api/v1", authenticated_app)  # Other routes with middleware
```

**Webhook Auth Bypass Tests (REQUIRED):**
```python
# tests/apps/execution_gateway/test_webhook_auth.py

def test_webhook_bypasses_bearer_token_auth():
    """CRITICAL: Webhooks MUST NOT require bearer token authentication."""
    # No Authorization header - only webhook signature
    response = client.post(
        "/api/v1/webhooks/orders",
        json=FILL_WEBHOOK,
        headers={"X-Alpaca-Signature": VALID_SIGNATURE},  # Signature only
    )
    assert response.status_code == 200  # MUST succeed without bearer token

def test_webhook_rejects_invalid_signature():
    """Webhooks MUST validate signature."""
    response = client.post(
        "/api/v1/webhooks/orders",
        json=FILL_WEBHOOK,
        headers={"X-Alpaca-Signature": "invalid"},
    )
    assert response.status_code == 401

def test_global_auth_middleware_not_invoked_for_webhooks():
    """Verify auth middleware is completely bypassed for webhook routes."""
    auth_log = []
    # Inject auth middleware logging
    response = client.post("/api/v1/webhooks/orders", json=FILL_WEBHOOK, headers=WEBHOOK_SIG)
    assert "auth_middleware" not in auth_log  # Middleware must NOT run

def test_non_webhook_routes_require_bearer_token():
    """Other routes MUST require bearer token auth."""
    response = client.get("/api/v1/positions")  # No auth header
    assert response.status_code == 401
```

**Acceptance Criteria:**
- [ ] Each service module has >90% branch coverage
- [ ] Pure functions have no side effects
- [ ] Middleware extracted and tested in isolation
- [ ] Middleware ordering tests pass
- [ ] **Webhook auth bypass implemented (path bypass OR router segregation)**
- [ ] **Webhook auth bypass tests pass (4 required tests)**
- [ ] All tests pass

---

### Phase 2: Extract Routes (12h)

**Goal:** Split monolithic main.py into APIRouter modules with clean dependency injection.

**Status:** IN PROGRESS - Routers extracted but architecture needs improvement based on code review.

**Phase 2A: Initial Extraction (COMPLETED)**
- [x] Create `routes/health.py` with `/`, `/health` endpoints (180 lines)
- [x] Create `routes/admin.py` with kill-switch, config, strategy endpoints (554 lines)
- [x] Create `routes/orders.py` with order submission/cancellation (636 lines)
- [x] Create `routes/slicing.py` with TWAP/slice endpoints (1,080 lines)
- [x] Create `routes/positions.py` with position/performance endpoints (563 lines)
- [x] Create `routes/webhooks.py` with webhook handlers (345 lines)
- [x] Create `routes/reconciliation.py` with admin recon endpoints (166 lines)
- [x] Verify webhook signature uses constant-time comparison (`hmac.compare_digest`)
- [x] Verify safety gate ordering preserved (13 gates in exact order)
- [x] Verify webhook isolation correct (no bearer token, no trading gates)

**Results:**
- 7 routers created (3,524 lines total, 26 endpoints)
- main.py reduced from 4,862 to 1,593 lines (67% reduction)
- All API paths and responses unchanged
- Safety-critical behavior preserved

**Phase 2A Implementation Notes:**
- Used **factory pattern** with closure for dependency injection
- Routers mounted **in lifespan()** after dependencies initialized
- Solves chicken-and-egg problem but creates non-standard FastAPI architecture

**Phase 2B: Architecture Improvements (IN PROGRESS) - Based on Gemini Code Review**

**Code Review Findings:**
1. **Factory Pattern** - Valid but non-idiomatic FastAPI
   - Pros: Explicit dependencies, testable
   - Cons: Forces lifespan mounting complexity, prevents module-level router definition
   - Recommendation: Refactor to FastAPI's native `Depends()` system

2. **Lifespan-Based Router Mounting** - Non-standard anti-pattern
   - Makes API structure dynamic instead of static
   - Reduces readability and tooling support
   - Root cause: Symptom of factory pattern choice

3. **main.py Size** - Still too large (1,593 lines)
   - Should be < 200 lines (thin entry point)
   - Bulk comes from lifespan setup and dependency initialization
   - Action: Extract lifespan logic to separate module

**Phase 2B Tasks:**
- [x] Create dependency provider functions (get_db_client, get_redis_client, etc.)
  - Created get_context(), get_config(), get_version(), get_metrics()
  - Created individual component providers for granular injection
  - All functions pass mypy --strict type checking
- [x] Refactor health.py to use `Depends()` pattern (first router completed)
  - Router defined at module level
  - Dependencies injected via Depends() in route handlers
  - 185 lines total, clean and testable
- [x] Refactor reconciliation.py to use `Depends()` pattern (second router completed)
  - Router defined at module level
  - Dependencies injected via Depends() in route handlers
  - 238 lines total, uses RateLimitConfig dependency
  - Added fills_backfill config to ExecutionGatewayConfig
  - Extended ReconciliationServiceProtocol with all required methods
- [x] Update main.py to support module-level mounting
  - Initialize app.state with version, config, context, metrics in lifespan
  - Import and mount health router at module level (line ~860)
  - Import and mount reconciliation router at module level (line ~863)
  - Remove old factory-based router mounting from lifespan
- [ ] Refactor remaining 5 routers to use `Depends()` pattern
  - admin.py (554 lines)
  - orders.py (636 lines)
  - slicing.py (1,080 lines)
  - positions.py (563 lines)
  - webhooks.py (345 lines)
- [ ] Extract lifespan logic to `startup.py` and `shutdown.py`
- [ ] Reduce main.py to < 200 lines (thin composition root)
- [ ] Add integration tests for router mounting
- [ ] Add tests for dependency injection

**Progress Summary:**
- ✅ First router (health.py) successfully refactored to Depends() pattern
- ✅ Second router (reconciliation.py) successfully refactored to Depends() pattern
- ✅ Dependency injection infrastructure complete and type-safe
- ✅ Module-level mounting working for health and reconciliation routers
- ✅ Added fills_backfill configuration to ExecutionGatewayConfig
- ✅ Extended ReconciliationServiceProtocol for type safety
- 🔄 Remaining: 5 routers to refactor + lifespan extraction + tests

**Phase 2B Target Pattern:**
```python
# apps/execution_gateway/dependencies.py
async def get_db_client() -> DatabaseClient:
    """Dependency provider for database client."""
    return app.state.context.db_client

# apps/execution_gateway/routes/orders.py
@router.post("/api/v1/orders")
async def submit_order(
    order: OrderRequest,
    db_client: Annotated[DatabaseClient, Depends(get_db_client)],
    redis_client: Annotated[RedisClient, Depends(get_redis_client)],
    recovery_manager: Annotated[RecoveryManager, Depends(get_recovery_manager)],
    ...
):
    # Route logic with injected dependencies
    ...

# apps/execution_gateway/main.py (module level)
from apps.execution_gateway.routes.orders import router as orders_router

app = FastAPI(lifespan=lifespan)
app.include_router(orders_router)  # Module-level mounting
```

**Files Created:**
- `apps/execution_gateway/routes/__init__.py`
- `apps/execution_gateway/routes/health.py`
- `apps/execution_gateway/routes/admin.py`
- `apps/execution_gateway/routes/orders.py`
- `apps/execution_gateway/routes/slicing.py`
- `apps/execution_gateway/routes/positions.py`
- `apps/execution_gateway/routes/webhooks.py`
- `apps/execution_gateway/routes/reconciliation.py`

**Files To Create (Phase 2B):**
- `apps/execution_gateway/startup.py` (lifespan startup logic)
- `apps/execution_gateway/shutdown.py` (lifespan shutdown logic)

**Acceptance Criteria:**
- [x] All API paths unchanged
- [x] All API responses unchanged
- [x] Safety gate ordering preserved
- [x] Webhook isolation correct
- [x] Webhook signature uses constant-time comparison
- [ ] Routers use `Depends()` pattern (FastAPI idiomatic)
- [ ] Routers mounted at module level (not in lifespan)
- [ ] main.py reduced to < 200 lines
- [ ] Lifespan logic extracted to startup.py/shutdown.py
- [ ] Route tests added for each router
- [ ] Dependency injection tests added

---

### Phase 3: Refactor Reconciliation (8h)

**Goal:** Split reconciliation into focused modules with clear interfaces. **Shadow mode validation is MANDATORY.**

**Tasks:**
- [ ] Create `reconciliation/` package structure
- [ ] Extract `reconciliation/helpers.py` with pure functions first
- [ ] Extract `reconciliation/state.py` with gate management
- [ ] Extract `reconciliation/fills.py` with backfill logic
- [ ] Extract `reconciliation/orders.py` with order sync
- [ ] Extract `reconciliation/positions.py` with position sync
- [ ] Extract `reconciliation/orphans.py` with orphan handling
- [ ] Create `reconciliation/context.py` for DI
- [ ] Update `reconciliation/service.py` as orchestrator
- [ ] **MANDATORY: Implement Shadow Mode validation script**
- [ ] **MANDATORY: Complete shadow mode validation pass before cutover**
- [ ] Add comprehensive tests (target: 80% coverage)

**Files Created:**
- `apps/execution_gateway/reconciliation/__init__.py`
- `apps/execution_gateway/reconciliation/service.py`
- `apps/execution_gateway/reconciliation/state.py`
- `apps/execution_gateway/reconciliation/context.py`
- `apps/execution_gateway/reconciliation/orders.py`
- `apps/execution_gateway/reconciliation/positions.py`
- `apps/execution_gateway/reconciliation/fills.py`
- `apps/execution_gateway/reconciliation/orphans.py`
- `apps/execution_gateway/reconciliation/helpers.py`
- `scripts/reconciliation_shadow_mode.py`
- `scripts/generate_expected_writes.py`
- `scripts/validate_shadow_run.py`

**Shadow Mode Implementation (REQUIRED):**

**Purpose:** Validate refactored reconciliation produces correct results before production cutover.

> **IMPORTANT: Keep It Simple**
> Shadow mode uses the SAME code paths as production. No special abstractions, no overlays,
> no fixture clients. Just run the real code in a transaction that gets rolled back.

**Simple Approach:**
1. Run against **test database** (copy of production schema with test data)
2. Wrap reconciliation in a **transaction**
3. Run reconciliation with **real queries** (identical to production)
4. **Capture writes** during execution
5. **Rollback** transaction (no mutations persist)
6. Compare captured writes against **expected outputs**

```python
# scripts/reconciliation_shadow_mode.py
def run_shadow_validation(test_db_url: str, test_redis_url: str | None = None):
    """Run reconciliation in shadow mode with transaction rollback.

    Note: Uses sync DB calls like the current codebase. The async API
    (run_reconciliation_once) wraps sync DB via asyncio.to_thread().

    Uses constructor injection - creates a new ReconciliationService instance
    with test dependencies. No changes to _run_reconciliation signature needed.
    """
    captured_writes = []

    # Wrap DatabaseClient to capture writes (not raw connection)
    # ReconciliationService expects DatabaseClient interface
    class CapturingDatabaseClient:
        def __init__(self, real_db_client: DatabaseClient):
            self._db = real_db_client

        def execute(self, sql, params=None):
            if sql.strip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
                captured_writes.append({"sql": sql, "params": params})
            return self._db.execute(sql, params)

        def __getattr__(self, name):
            # Delegate all other methods to real DatabaseClient
            # (transaction, get_order_for_update, update_order_status, etc.)
            return getattr(self._db, name)

    # Create real DatabaseClient pointing to test DB
    real_db = DatabaseClient(test_db_url)

    # Redis: use isolated test instance or None to skip Redis writes
    test_redis = RedisClient(test_redis_url) if test_redis_url else None

    # Wrap in transaction for rollback
    with real_db.transaction() as txn:
        capturing_db = CapturingDatabaseClient(real_db)

        # Create service instance with test dependencies (constructor injection)
        service = ReconciliationService(
            db_client=capturing_db,
            redis_client=test_redis,  # None = skip Redis writes
            alpaca_client=mock_alpaca_client(),
        )
        service._run_reconciliation("shadow")

        # Rollback - no DB mutations persist
        txn.rollback()

    # Validate captured writes
    expected = load_expected_writes("tests/fixtures/expected_writes.json")
    assert_writes_match(captured_writes, expected)
```

**What This Tests:**
- Business logic correctness (same code as production)
- SQL query correctness (real queries executed)
- State transitions (validated against expected outputs)

**What This Does NOT Do (intentionally):**
- No separate "shadow" code paths
- No overlay abstractions
- No fixture-based broker/Redis clients
- No complex snapshot pinning

**Environment:**
- Run against **isolated test environment** (not production)
- Test DB: Copy of production schema with test data
- Test Redis: Isolated instance (writes are acceptable since it's test-only)
- Broker: Mock or test/paper instance

**Redis Writes in Shadow Mode:**
The reconciliation service writes to Redis (quarantine flags, orphan exposure). Since shadow mode
runs against an **isolated test Redis instance**, these writes are acceptable - they don't affect
production. Alternatively, pass `redis_client=None` to skip Redis writes entirely during validation
(the reconciliation service handles `None` gracefully with early returns).

**Expected Outputs:**
Store expected writes in `tests/fixtures/expected_writes.json`:
```json
{
  "writes": [
    {"table": "orders", "operation": "UPDATE", "where": {"id": "order_123"}, "set": {"status": "filled"}},
    {"table": "positions", "operation": "UPDATE", "where": {"symbol": "AAPL"}, "set": {"qty": 100}}
  ]
}
```

**Validation Criteria:**

| Validation | Pass Criteria |
|------------|---------------|
| **No Exceptions** | 0 unhandled exceptions |
| **Writes Match** | Captured writes match expected_writes.json |
| **Idempotency** | Run twice → identical writes |

**Generating Expected Outputs:**
Run pre-refactor code once to capture expected writes:
```bash
python scripts/generate_expected_writes.py --output tests/fixtures/expected_writes.json
```
**Shadow Mode Acceptance Criteria:**
- [ ] Shadow script runs reconciliation in transaction-rollback mode
- [ ] Captured writes match expected_writes.json
- [ ] Run twice produces identical writes (idempotency)
- [ ] No mutations persist after rollback

**Acceptance Criteria:**
- [ ] ReconciliationService maintains same public interface
- [ ] Each module has >80% branch coverage
- [ ] Pure functions in helpers.py have 95% coverage
- [ ] Shadow mode validation complete
- [ ] All tests pass

---

### Phase 4: Cleanup, Lifecycle Tests, and Finalize (6h)

**Goal:** Complete migration, add lifecycle tests, and remove legacy code.

**Tasks:**
- [ ] Create `startup.py` and `shutdown.py` for lifecycle
- [ ] Create `exception_handlers.py`
- [ ] Remove globals from main.py (use AppContext)
- [ ] Update all imports to use new module structure
- [ ] **Add lifecycle/background task tests** (see below)
- [ ] **Add gate ordering integration tests**
- [ ] Add integration tests for critical paths
- [ ] Update documentation

**Files Created:**
- `apps/execution_gateway/startup.py`
- `apps/execution_gateway/shutdown.py`
- `apps/execution_gateway/exception_handlers.py`

**Lifecycle/Background Task Tests (REQUIRED):**

Background tasks (reconciliation loop, slice scheduler) must maintain correct startup/shutdown ordering and "gate open" semantics.

```python
# tests/apps/execution_gateway/test_lifecycle.py

async def test_startup_order():
    """Verify startup order: DB → Redis → Alpaca → Reconciliation → Routes."""
    startup_order = []
    app = create_test_app(startup_tracker=startup_order)
    async with app.lifespan():
        assert startup_order == [
            "db_connect", "redis_connect", "alpaca_connect",
            "reconciliation_start", "routes_ready"
        ]

async def test_shutdown_order():
    """Verify shutdown order: Routes → Background tasks → Clients."""
    shutdown_order = []
    app = create_test_app(shutdown_tracker=shutdown_order)
    async with app.lifespan():
        pass  # Normal shutdown
    assert shutdown_order == [
        "routes_drain", "reconciliation_stop", "slice_scheduler_stop",
        "alpaca_disconnect", "redis_disconnect", "db_disconnect"
    ]

async def test_reconciliation_gate_blocks_orders_until_ready():
    """Verify orders rejected until reconciliation gate opens."""
    app = create_test_app(block_reconciliation=True)
    async with app.lifespan():
        # NOTE: AUTH required - gates run AFTER auth passes
        response = await client.post("/api/v1/orders", json=ORDER, headers=AUTH)
        assert response.status_code == 503
        assert "reconciliation" in response.json()["detail"].lower()

async def test_background_task_crash_triggers_graceful_degradation():
    """Verify background task failure doesn't crash the app."""
    app = create_test_app(crash_reconciliation_loop=True)
    async with app.lifespan():
        # App should still serve health checks
        response = await client.get("/health")
        assert response.status_code == 200
        # But orders should be blocked (AUTH required for gate test)
        response = await client.post("/api/v1/orders", json=ORDER, headers=AUTH)
        assert response.status_code == 503
```

**Gate Ordering Integration Tests:**

```python
# tests/apps/execution_gateway/test_gate_ordering_integration.py

async def test_full_gate_sequence_happy_path():
    """Verify all gates pass in correct order for valid order."""
    gate_log = GateLogger()
    app = create_test_app(gate_logger=gate_log)
    # NOTE: AUTH headers required - gates must run AFTER auth passes
    response = await client.post("/api/v1/orders", json=VALID_ORDER, headers=AUTH)
    assert response.status_code == 200
    # Note: Kill-switch and circuit-breaker checks come BEFORE recon_gate
    assert gate_log.sequence == [
        "auth:pass", "rate_limit:pass",
        "kill_switch_avail:pass", "circuit_breaker_avail:pass", "position_reservation_avail:pass",
        "kill_switch:pass", "circuit_breaker:pass",
        "quarantine:pass", "recon_gate:pass",
        "position_reservation:pass", "idempotency:pass", "fat_finger:pass", "submit:success"
    ]

async def test_kill_switch_short_circuits():
    """Verify kill-switch blocks before fat-finger check."""
    gate_log = GateLogger()
    engage_kill_switch()
    # NOTE: AUTH headers required - gates must run AFTER auth passes
    response = await client.post("/api/v1/orders", json=ORDER, headers=AUTH)
    assert response.status_code == 503
    assert "fat_finger" not in gate_log.sequence
    assert gate_log.sequence[-1] == "kill_switch:block"
```

**Acceptance Criteria:**
- [ ] main.py is composition root only (<500 lines, stretch goal: <200 lines)
- [ ] No mutable module-level globals except: metrics registry in `metrics.py`, config constants in `config.py`, app composition in `main.py`
- [ ] No service/client/connection globals anywhere (use AppContext DI)
- [ ] Overall coverage >85%
- [ ] Lifecycle tests pass (startup/shutdown ordering)
- [ ] Background task invariants tested
- [ ] Gate ordering integration tests pass
- [ ] All integration tests pass

---

## Key Design Decisions

### 1. AppContext for Dependency Injection

```python
# apps/execution_gateway/app_context.py
from dataclasses import dataclass
from typing import Protocol

class DatabaseClientProtocol(Protocol):
    """Protocol for database operations."""
    ...

class RedisClientProtocol(Protocol):
    """Protocol for Redis operations."""
    ...

@dataclass
class AppContext:
    """Central context for all application dependencies."""
    db_client: DatabaseClientProtocol
    redis_client: RedisClientProtocol
    alpaca_client: AlpacaExecutor
    reconciliation_service: ReconciliationService
    config: AppConfig
    metrics: AppMetrics
```

**Rationale:** Enables easy mocking in tests, makes dependencies explicit.

### 2. Protocol Interfaces for Clients

```python
# Use typing.Protocol for client interfaces
class AlpacaClientProtocol(Protocol):
    def submit_order(self, order: OrderRequest) -> OrderResponse: ...
    def cancel_order(self, order_id: str) -> bool: ...
    def get_positions(self) -> list[Position]: ...
```

**Rationale:** Allows cheap fakes in unit tests without real API calls.

### 3. Time Injection for Deterministic Tests

```python
# apps/execution_gateway/reconciliation/context.py
from datetime import datetime
from typing import Callable

@dataclass
class ReconciliationContext:
    db: DatabaseClient
    redis: RedisClient
    alpaca: AlpacaExecutor
    now: Callable[[], datetime] = lambda: datetime.now(UTC)  # Injectable
```

**Rationale:** Enables deterministic time-based tests.

### 4. Pure Functions for Calculations

```python
# apps/execution_gateway/reconciliation/helpers.py
def calculate_synthetic_fill(
    filled_qty: Decimal,
    filled_avg_price: Decimal,
    existing_fills: list[dict],
    timestamp: datetime,
    source: str,
) -> dict | None:
    """Pure function - no side effects, fully testable."""
    ...
```

**Rationale:** Pure functions are trivial to unit test with table-driven tests.

---

## Migration Strategy

### Safety-First Approach

1. **Preserve Routes/Paths/Response Schemas Exactly**
   - Add contract tests before moving code
   - Use pytest-httpx to snapshot API responses
   - Verify all response schemas with Pydantic validation

2. **Refactor in Thin Slices**
   - Move helper functions first (lowest risk)
   - Then middleware (medium risk)
   - Then routers (medium risk)
   - Then reconciliation internals (higher risk)

3. **Shadow Mode for Reconciliation (MANDATORY)**
   - Run new logic with full isolation in staging (no old logic comparison)
   - Capture all would-be writes without executing them
   - **Required shadow mode validation pass before cutover**
   - **0 errors/exceptions required for approval**
   - Validate captured writes against expected invariants
   - Tech Lead sign-off on shadow report

4. **Keep Safety Gates Untouched Initially**
   - Kill-switch, circuit-breaker, reduce-only logic unchanged
   - Refactor only after test coverage validates safety
   - **Add gate ordering tests BEFORE moving gate logic**

5. **Transaction Boundary Preservation**
   - Document all atomic operations before refactoring
   - Add failure-mode tests for each transaction
   - Verify rollback semantics in integration tests

### Rollback Strategy

Each phase is independently deployable:
- Phase 0: Revert new module imports, restore inline config
- Phase 1: Revert middleware/service imports individually
- Phase 2: Router imports can be reverted individually
- Phase 3: Old reconciliation.py can be restored from git
- **Rollback trigger:** Any HIGH severity production incident

### Cutover Checklist

Before each phase goes to production:
- [ ] All tests pass (unit + integration)
- [ ] Coverage targets met
- [ ] Contract tests validate API compatibility
- [ ] Shadow mode validation complete (Phase 3)
- [ ] Rollback procedure documented and tested
- [ ] On-call briefed on changes

---

## Testing Strategy

### Unit Tests (Per Module)

| Module | Target Coverage | Test Focus |
|--------|-----------------|------------|
| `services/pnl_calculator.py` | 95% | Decimal precision, edge cases |
| `services/order_helpers.py` | 95% | Idempotency, fat-finger |
| `reconciliation/helpers.py` | 95% | Synthetic fill calculation |
| `reconciliation/fills.py` | 85% | Backfill logic |
| `reconciliation/orders.py` | 85% | Order sync, CAS |
| `routes/*` | 80% | Request/response validation |
| `middleware.py` | 90% | Auth, rate limiting, ordering |
| `config.py` | 100% | Default values, parsing |

### Transaction/Invariant Tests (REQUIRED)

| Test | Description | Priority |
|------|-------------|----------|
| `test_order_submission_rollback_on_broker_failure` | Verify reservation released on broker error | P0 |
| `test_order_submission_partial_failure_no_duplicate` | Simulate crash after broker submit, verify idempotency | P0 |
| `test_reconciliation_cas_conflict_no_data_loss` | Concurrent updates preserve higher-priority source | P0 |
| `test_fill_processing_atomic_position_update` | Fill + position update in single transaction | P0 |

### Gate Ordering Tests (REQUIRED)

**Order Submission Gates:**

| Test | Description | Priority |
|------|-------------|----------|
| `test_gate_order_matches_specification` | Verify gates execute in documented order | P0 |
| `test_unauthenticated_request_rejected_before_gate_checks` | Auth failure doesn't trigger gate logs | P0 |
| `test_kill_switch_blocks_before_broker_call` | Kill-switch prevents any broker API call | P0 |
| `test_full_gate_sequence_happy_path` | All gates pass in correct order for valid order | P0 |

**Cancel/Slice/Admin Gates (ALSO REQUIRED):**

| Test | Description | Priority |
|------|-------------|----------|
| `test_cancel_no_trading_gates` | Verify cancel has no trading gates (Auth → Cancel only) | P0 |
| `test_slice_gate_order` | Verify TWAP/slice follows Auth → Scheduler → KS Unavail → Kill-Switch → Quarantine → Recon Gate → Liquidity | P0 |
| `test_slice_blocked_on_redis_unavailable` | Slice fails-closed when Redis unreachable (kill-switch state unknown) | P0 |
| `test_admin_bypasses_trading_gates` | Admin routes bypass kill-switch/circuit-breaker | P0 |
| `test_kill_switch_engage_requires_operator_permission` | Engage requires special permission | P0 |

**Webhook Gate Isolation (CRITICAL - ALSO REQUIRED):**

| Test | Description | Priority |
|------|-------------|----------|
| `test_webhook_not_gated_by_kill_switch` | Webhooks MUST process when kill-switch engaged | P0 |
| `test_webhook_not_gated_by_circuit_breaker` | Webhooks MUST process when circuit-breaker tripped | P0 |
| `test_webhook_bypasses_bearer_token_auth` | Webhooks use signature auth only | P0 |
| `test_global_auth_middleware_not_invoked_for_webhooks` | Auth middleware completely bypassed | P0 |

### Lifecycle Tests (REQUIRED)

| Test | Description | Priority |
|------|-------------|----------|
| `test_startup_order` | DB → Redis → Alpaca → Reconciliation → Routes | P0 |
| `test_shutdown_order` | Routes → Background tasks → Clients | P0 |
| `test_reconciliation_gate_blocks_orders_until_ready` | Orders rejected until gate opens | P0 |
| `test_background_task_crash_triggers_graceful_degradation` | Background failure doesn't crash app | P1 |

### Integration Tests

| Test | Description | Priority |
|------|-------------|----------|
| Order Submission E2E | Submit → Webhook → Position update | P0 |
| Reconciliation Cycle | Start → Run → Complete gate | P0 |
| Kill-Switch Enforcement | Engage → Block orders → Disengage | P0 |
| Middleware Ordering | Auth → Rate limit → Route | P1 |

### Contract Tests

```python
# tests/apps/execution_gateway/test_api_contracts.py
def test_submit_order_response_schema(client):
    """Ensure response schema unchanged after refactor."""
    response = client.post("/api/v1/orders", json=SAMPLE_ORDER, headers=AUTH)
    assert response.status_code == 200
    # Validate against Pydantic schema
    OrderResponse.model_validate(response.json())

def test_all_endpoints_response_schemas():
    """Validate all endpoint response schemas unchanged."""
    # (requires_auth, method, path, schema)
    endpoints = [
        (False, "GET", "/health", HealthResponse),
        (True, "GET", "/api/v1/config", ConfigResponse),
        (True, "POST", "/api/v1/orders", OrderResponse),
        (True, "GET", "/api/v1/positions", PositionsResponse),
        # ... all endpoints
    ]
    for requires_auth, method, path, schema in endpoints:
        headers = AUTH if requires_auth else {}
        response = client.request(method, path, headers=headers, ...)
        schema.model_validate(response.json())
```

### Metrics Contract Tests

```python
# tests/apps/execution_gateway/test_metrics_contract.py
def test_metrics_names_unchanged():
    """Verify metric names match original to prevent dashboard breakage."""
    expected_names = load_expected_metrics()
    actual_names = get_registered_metrics()
    assert set(actual_names) == set(expected_names)

def test_metrics_labels_unchanged():
    """Verify metric labels match original."""
    for metric_name in expected_metrics:
        expected_labels = get_expected_labels(metric_name)
        actual_labels = get_actual_labels(metric_name)
        assert expected_labels == actual_labels
```

---

## Risk Assessment

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Breaking API contracts | High | Low | Contract tests + careful review |
| Circular imports | Medium | Medium | Careful module boundaries, TYPE_CHECKING |
| Trading safety regression | Critical | Low | Keep gates unchanged, shadow mode (mandatory) |
| Transaction boundary violation | Critical | Medium | Explicit atomic units, failure-mode tests |
| Gate ordering change | Critical | Medium | Gate ordering contract, integration tests |
| Test flakiness | Medium | Medium | Isolated fixtures, frozen time |
| Configuration drift | High | Low | Config audit checklist, default value tests |
| Metrics dashboard breakage | Medium | Low | Metrics contract tests |
| Middleware ordering change | High | Medium | Middleware ordering tests |
| Incomplete migration | Low | Low | Phased approach, each phase deployable |
| Background task invariant violation | High | Low | Lifecycle tests, graceful degradation tests |

---

## Success Criteria

| Metric | Current | Target |
|--------|---------|--------|
| main.py lines | ~5000 | <500 |
| main.py coverage | 55% | 85%+ |
| reconciliation.py coverage | 15% | 80%+ |
| API contract tests | 0 | 100% endpoints |
| Pure function coverage | N/A | 95% |
| Integration tests | Minimal | Critical paths covered |
| Transaction boundary tests | 0 | 4 required tests |
| Gate ordering tests (order submit) | 0 | 4 required tests |
| Gate ordering tests (cancel/slice/admin) | 0 | 4 required tests |
| Gate ordering tests (webhooks) | 0 | 4 required tests |
| Lifecycle tests | 0 | 4 required tests |
| Shadow mode validation | N/A | captured writes match expected |
| Metrics contract tests | 0 | 100% metrics |
| Config drift audit | N/A | Complete |

---

## Definition of Done

### Code Quality
- [ ] main.py reduced to composition root (<500 lines)
- [ ] All routes extracted to APIRouter modules
- [ ] Middleware extracted and tested in isolation
- [ ] Pure business logic in services/ with 95% coverage
- [ ] Reconciliation split into package with 80% coverage
- [ ] AppContext replaces global state
- [ ] All existing tests pass

### Safety Verification
- [ ] Transaction boundary tests pass (4 required)
- [ ] Gate ordering tests pass (4 required)
- [ ] Lifecycle tests pass (4 required)
- [ ] Shadow mode validation complete (captured writes match expected)
- [ ] Config drift audit checklist completed
- [ ] Metrics contract tests pass

### Integration & Documentation
- [ ] New unit tests added for extracted modules
- [ ] Contract tests validate API compatibility
- [ ] Integration tests cover critical trading paths
- [ ] Documentation updated
- [ ] Code reviewed and approved
- [ ] Tech Lead sign-off on shadow report

---

## Timeline Estimate

| Phase | Duration | Dependencies |
|-------|----------|--------------|
| Phase 0: Infrastructure + App Factory | 6h | None |
| Phase 1: Pure Helpers + Middleware | 8h | Phase 0 |
| Phase 2: Routes | 8h | Phase 1 |
| Phase 3: Reconciliation + Shadow Mode | 8h + shadow validation | Phase 0 |
| Phase 4: Cleanup + Lifecycle Tests | 6h | Phase 2, 3 |
| **Total** | **36h + shadow validation** | |

**Note:** Phases 2 and 3 can run in parallel after Phase 0/1 complete. Shadow mode runs in staging, doesn't block development work.

---

## Appendix: Transaction Boundary Reference

### Order Submission: Reservation-First Flow (Redis Reservation → DB Idempotency → Broker Call)

**Phase 1a: Position Reservation FIRST (Redis Lua Script - Atomic)**
```lua
-- PositionReservation.reserve() executes this atomically
-- Happens BEFORE idempotency check to ensure atomic position limits
local current = redis.call("GET", position_key) or 0
local new_position = current + delta
if abs(new_position) > limit then
  return {success=false, error="position_limit_exceeded"}
end
redis.call("SET", position_key, new_position)
redis.call("SET", token_key, delta)
redis.call("EXPIRE", token_key, ttl)  -- Auto-cleanup on crash
return {success=true, token=token_key}
```

**Phase 1b: Idempotency Check AFTER Reservation (DB Query)**
```
SELECT * FROM orders WHERE client_order_id = ?  -- Read-only check
IF exists:
  redis.call("DEL", token_key)  -- Release reservation for duplicate
  RETURN existing (idempotent)
```

**Phase 1c: Fat-Finger Validation**
```
-- Validates order size against thresholds
-- If breached: release reservation and reject
```

**Phase 2: Broker Submit (Outside Transaction - Idempotent)**
```
-- No DB transaction held during network call --
broker_result = alpaca.submit_order(client_order_id, ...)
-- If fails: release Redis reservation and raise error --
redis.call("DEL", token_key)  -- PositionReservation.release()
```

**Phase 3: Persist Result (DB Transaction)**
```
BEGIN TRANSACTION
  INSERT order with broker_result
COMMIT
-- Reservation token expires via TTL (no explicit DB cleanup needed) --
```

**Failure Scenarios & Rollback:**
- Phase 1a (reservation) fails → Position limit exceeded, no side effects
- Phase 1b (idempotency) finds duplicate → Release reservation, return existing order
- Phase 1c (fat-finger) fails → Release reservation, reject order
- Phase 2 fails → Release Redis reservation token, no order at broker
- Phase 3 fails after broker success → Retry re-submits to broker (same `client_order_id`), broker returns existing order, DB persist succeeds (reservation auto-expires via TTL)

**Key Invariants:**
- No DB transaction spans the broker API call
- `client_order_id` provides idempotency at broker level
- Redis Lua scripts ensure atomic position limit checks
- Reservation tokens auto-expire via TTL for crash recovery

### Order Fill Processing Atomic Unit (Row Lock Pattern)
```
BEGIN TRANSACTION
  1. SELECT ... FROM orders WHERE client_order_id = ? FOR UPDATE  -- Row lock
  2. UPDATE orders SET
       status = :new_status,
       filled_qty = :filled_qty,
       filled_avg_price = :avg_price,
       metadata = metadata || :fill_data
     WHERE client_order_id = :id
  3. UPDATE position snapshot
  4. Trigger P&L recalculation
COMMIT
```

**Note:** Uses pessimistic locking (SELECT FOR UPDATE) to prevent concurrent
updates to the same order during webhook processing.

### Reconciliation Write Atomic Unit (CAS Pattern)
```
BEGIN TRANSACTION (per order)
  1. SELECT version, source_priority FROM orders WHERE client_order_id = ?
  2. UPDATE orders SET
       status = :new_status,
       metadata = metadata || :fill_metadata,
       version = version + 1
     WHERE client_order_id = :id
       AND version = :expected_version  -- CAS guard
       AND source_priority <= :new_source_priority  -- Higher priority wins
  3. IF rowcount=0: SKIP (lower priority or concurrent update)
COMMIT
-- Position sync in separate transaction (eventual consistency OK) --
```

**Note:** Reconciliation uses CAS pattern per the "CAS refactor required before
shadow mode" requirement (see SELECT FOR UPDATE / Locking Handling section).

---

**Last Updated:** 2026-01-15
**AI Review Status:** APPROVED (30+ rounds of iterative Codex/Gemini review)
**Implementation Status:** PENDING_TECH_LEAD (requires Tech Lead sign-off before implementation)
