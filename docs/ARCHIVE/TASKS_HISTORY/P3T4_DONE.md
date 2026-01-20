# P3T4: Medium Priority Fixes

**Phase:** P3 (Issue Remediation)
**Track:** Track 4 - Medium Priority (P2)
**Status:** ✅ Complete
**Dependencies:** P3T3 Complete (✅ PR #69)
**Estimated Effort:** 22-32 hours (5-7 days)

---

## Overview

This task addresses 11 medium-priority issues identified during the triple-reviewer analysis in P3T0. These fixes improve code quality, type safety, lifecycle management, and performance without blocking production deployment.

**Reference:** [P3_ISSUES_DONE.md](./P3_ISSUES_DONE.md) (M1-M11)

---

## Scope Clarification

**In Scope (M1-M11):**
- M1-M6: New issues from Round 2 review (detailed in P3_ISSUES_DONE.md)
- M7: Web console connection pooling (from Round 1)
- M8-M11: Code cleanup items (TODOs, zen_review, rate limiting, validation)

**Out of Scope (Deferred):**
The following items mentioned in P3_ISSUES_DONE.md as "Previous Medium Issues" from Round 1 are deferred to a future sprint or already addressed in earlier tracks:
- Redis coupling (architectural, requires larger refactor)
- Single strategy limitation (P4 feature scope)
- Magic numbers cleanup (overlaps with TODO audit)
- Inconsistent test organization (addressed in P3T1 workflow modernization)

**Rationale:** Focus on the 11 most actionable medium issues that directly improve safety and performance. Architectural changes deferred to avoid scope creep.

---

## Issues Addressed

| ID | Issue | Location | Effort |
|----|-------|----------|--------|
| M1 | Price cache float/Decimal mismatch | `orchestrator/orchestrator.py:739` | 1-2h |
| M2 | Position sync task not cancelled | `market_data_service/position_sync.py:68` | 1-2h |
| M3 | Mid-price TypeError | `execution_gateway/alpaca_client.py:315` | 1h |
| M4 | Orchestration 200 on failure | `orchestrator/main.py:530` | 1-2h |
| M5 | Feature generation blocks | `signal_service/signal_generator.py:320` | 6-8h |
| M6 | TRUSTED_PROXY_IPS empty | `auth_service/main.py:177` | 30min |
| M7 | Web console connection pooling | `web_console/app.py:662` | 2-4h |
| M8 | 270+ TODO comments | Repository-wide | 3-4h |
| M9 | Deprecated zen_review field | `workflow_gate.py:87` (old) | 30min |
| M10 | Rate limiting | `execution_gateway/main.py:922` | 2-4h |
| M11 | Kill-switch reason validation | `execution_gateway/main.py:750` | 1h |

---

## Components

### Component 1: T4.1 - Type Safety & Validation
**Issues:** M1, M3, M6
**Effort:** 4-6 hours

**Tasks:**
- [ ] M1: Normalize price cache to use Decimal consistently
- [ ] M3: Add None checks before mid-price calculation
- [ ] M6: Set safe defaults for TRUSTED_PROXY_IPS in dev

**Files:**
- `apps/orchestrator/orchestrator.py`
- `apps/execution_gateway/alpaca_client.py`
- `apps/auth_service/main.py`

**Test Cases:**
- `test_price_cache_decimal.py` - Cache stores and returns Decimal
- `test_price_cache_float_input.py` - Float input converted to Decimal correctly (no precision loss)
- `test_price_cache_string_input.py` - String input handled via Decimal(str(price))
- `test_midprice_none_handling.py` - Graceful None handling for bid/ask
- `test_midprice_partial_none.py` - One of bid/ask is None returns None
- `test_midprice_valid_calculation.py` - Both valid returns correct mid price
- `test_proxy_ips_default_dev.py` - Dev has safe defaults (127.0.0.1, ::1)
- `test_proxy_ips_default_prod.py` - Prod requires explicit configuration
- `test_proxy_ips_filter_empty.py` - Empty strings filtered from split result

---

### Component 2: T4.2 - Lifecycle & Shutdown
**Issues:** M2, M4
**Effort:** 4-6 hours

**Tasks:**
- [ ] M2: Store task handle and cancel on shutdown
- [ ] M2: Use asyncio.Event instead of long sleeps
- [ ] M4: Return 500 for unexpected exceptions instead of 200

**Files:**
- `apps/market_data_service/position_sync.py`
- `apps/orchestrator/main.py`

**Test Cases:**
- `test_shutdown_cancels_tasks.py` - Clean shutdown cancels background tasks
- `test_shutdown_active_sleep.py` - Task in long sleep is cancelled promptly
- `test_shutdown_event_signal.py` - asyncio.Event used for clean cancellation
- `test_error_status_codes_500.py` - Unexpected exceptions return 500
- `test_error_status_codes_explicit.py` - Known errors return appropriate codes (400, 404, etc.)
- `test_orchestration_exception_logging.py` - Exceptions logged with correlation ID

---

### Component 3: T4.3 - Performance Optimization
**Issues:** M5, M7
**Effort:** 6-8 hours

**Tasks:**
- [ ] M5: Enable Redis feature cache in production
- [ ] M5: Add option to pre-compute features at day start
- [ ] M7: Add connection pooling to web console database client

**Files:**
- `apps/signal_service/signal_generator.py`
- `apps/web_console/app.py`

**Test Cases:**
- `test_feature_cache_hit.py` - Cache reduces disk I/O
- `test_feature_cache_miss.py` - Cache miss falls back to disk correctly
- `test_feature_cache_latency.py` - Cache hit latency < 10ms (perf guard)
- `test_feature_precompute.py` - Pre-computation at startup works
- `test_web_console_pool.py` - Connection reuse works correctly
- `test_web_console_pool_exhaustion.py` - Graceful handling at pool limit

---

### Component 4: T4.4 - Code Cleanup
**Issues:** M8, M9, M10, M11
**Effort:** 4-6 hours

**Tasks:**
- [ ] M8: Audit TODOs, create issues for actionable items, remove stale ones
- [ ] M9: Remove deprecated zen_review field if present in new ai_workflow
- [ ] M10: Add rate limiting with slowapi
- [ ] M11: Add max_length validation on kill-switch reason field

**Files:**
- Repository-wide (TODO audit)
- `scripts/ai_workflow/` (zen_review removal)
- `apps/execution_gateway/main.py` (rate limiting, reason validation)

**Test Cases:**
- `test_todo_count.py` - Less than 50 actionable TODOs in apps/ and libs/
- `test_rate_limiting_happy_path.py` - Normal requests within limit succeed
- `test_rate_limiting_exceeded.py` - Requests over limit return 429 with Retry-After
- `test_rate_limiting_health_exempt.py` - Health/metrics endpoints exempt from rate limiting
- `test_rate_limiting_per_client.py` - Rate limits are per-IP, not global
- `test_reason_validation_max.py` - Reason at max length (256 chars) accepted
- `test_reason_validation_over_max.py` - Reason over max length (257+ chars) rejected with 422
- `test_reason_validation_boundary.py` - Boundary cases (255, 256, 257 chars)

---

## Acceptance Criteria

- [ ] All 11 medium-priority issues (M1-M11) resolved
- [ ] All test cases pass (unit + integration)
- [ ] TODO count reduced to <50 actionable items in apps/ and libs/
- [ ] No regression in existing tests
- [ ] CI passes (`make ci-local`)
- [ ] Code review approved (Gemini + Codex via zen-mcp)
- [ ] Each component follows 6-step pattern: plan → plan-review → implement → test → review → commit
- [ ] Workflow gates enforced via `workflow_gate.py`

---

## Implementation Notes

### M1: Price Cache Decimal
```python
# Before
self.price_cache[symbol] = float(price)

# After - IMPORTANT: Always convert via str() to avoid float precision loss
# float(0.1) → 0.1000000000000000055511151231257827021181583404541015625
# Decimal(str(0.1)) → Decimal('0.1') (exact)
self.price_cache[symbol] = Decimal(str(price))

# For cache reads, ensure type consistency
def get_price(self, symbol: str) -> Decimal:
    """Returns Decimal price from cache, raises if not found."""
    price = self.price_cache.get(symbol)
    if price is None:
        raise PriceUnavailableError(f"No price for {symbol}")
    return price  # Already Decimal
```

### M3: Mid-Price None Check
```python
# Before
mid_price = (ask_price + bid_price) / 2

# After
if ask_price is None or bid_price is None:
    logger.warning(f"Missing bid/ask for mid-price: bid={bid_price}, ask={ask_price}")
    return None
mid_price = (ask_price + bid_price) / 2
```

### M6: Proxy IPs Default
```python
# Before
TRUSTED_PROXY_IPS = os.getenv("TRUSTED_PROXY_IPS", "").split(",")

# After - with empty string filtering
default_ips = "127.0.0.1,::1" if ENVIRONMENT in ("dev", "test") else ""
raw_ips = os.getenv("TRUSTED_PROXY_IPS", default_ips).split(",")
TRUSTED_PROXY_IPS = [ip.strip() for ip in raw_ips if ip.strip()]  # Filter empties
```

### M10: Rate Limiting with slowapi
```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware

# Initialize limiter with in-memory storage (or Redis for production)
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200/minute"],  # Global default
    storage_uri="memory://",  # Use "redis://localhost:6379" for production
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Exempt health endpoints
@app.get("/health")
@limiter.exempt
async def health():
    return {"status": "ok"}

# Rate limit order endpoints
@app.post("/api/v1/orders")
@limiter.limit("100/minute")
async def submit_order(request: Request, order: OrderRequest):
    ...
```

### M11: Kill-Switch Reason Validation
```python
from pydantic import Field

class KillSwitchRequest(BaseModel):
    reason: str = Field(..., max_length=256, description="Reason for kill-switch activation")
```

---

## Related Documents

- [P3_PLANNING_DONE.md](./P3_PLANNING_DONE.md) - Full P3 planning
- [P3_ISSUES_DONE.md](./P3_ISSUES_DONE.md) - Complete issue list
- [P3T1_DONE.md](./P3T1_DONE.md) - Workflow Modernization (Complete)
- [P3T2_DONE.md](./P3T2_DONE.md) - Critical Fixes (Complete)
- [P3T3_DONE.md](./P3T3_DONE.md) - High Priority Fixes (Complete)

---

**Created:** 2025-11-30
**Author:** Claude Code
