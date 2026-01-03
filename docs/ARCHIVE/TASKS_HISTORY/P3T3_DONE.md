---
id: P3T3
title: "High Priority Fixes (P1) - Financial Precision, Concurrency, Reliability"
phase: P3
task: T3
priority: P1
owner: "@development-team"
state: TASK
created: 2025-11-30
dependencies: ["P3T2"]
estimated_effort: "5-7 days"
related_adrs: []
related_docs: ["P3_PLANNING.md", "P3_ISSUES.md"]
features: ["T3.1-FinancialPrecision", "T3.2-Concurrency", "T3.3-ServiceReliability", "T3.4-CodeQuality"]
---

# P3T3: High Priority Fixes (P1) - Financial Precision, Concurrency, Reliability

**Phase:** P3 (Review, Remediation & Modernization)
**Status:** ✅ Complete
**Priority:** P1 (Fix in Next Sprint)
**Owner:** @development-team
**Created:** 2025-11-30
**Estimated Effort:** 5-7 days

---

## Objective

Fix 11 high-priority issues identified by triple-reviewer analysis that affect production stability, performance, and trading efficiency.

**Success looks like:**
- All 11 P1 high-priority issues resolved
- Connection pooling implemented for 10x performance gain
- No blocking operations in async code
- Thread-safe global state management
- Comprehensive test coverage for each fix
- CI passes with all new tests

---

## Issues to Address

| ID | Issue | Location | Reviewer |
|----|-------|----------|----------|
| H1 | float vs Decimal for financial math | `execution_gateway/alpaca_client.py:140` | Gemini |
| H2 | Missing DB connection pooling | `execution_gateway/database.py` | Claude+Gemini |
| H3 | Global state race condition | `execution_gateway/main.py:154` | Claude |
| H4 | Blocking sleep in async | `slice_scheduler.py:574` | Claude |
| H5 | Subscription ref-counting | `market_data_service/position_sync.py:95` | Gemini |
| H6 | TWAP front-loaded distribution | `order_slicer.py:158` | Claude |
| H7 | Model reload without model | `signal_service/main.py:119` | Claude |
| H8 | Temp SignalGenerator per request | `signal_service/main.py:933` | Claude |
| H10 | 176+ print() statements | Throughout codebase | Claude |
| H11 | Bare except clause | `libs/common/logging/formatter.py` | Claude |

---

## Acceptance Criteria

- [ ] **AC1:** H1 - All monetary values use Decimal, no float precision errors
- [ ] **AC2:** H2 - Connection pooling implemented (min=5, max=20)
- [ ] **AC3:** H3 - Global state protected by threading.Lock or Event
- [ ] **AC4:** H4 - All async functions use asyncio.sleep() not time.sleep()
- [ ] **AC5:** H5 - Subscription manager uses ref-counting
- [ ] **AC6:** H6 - TWAP remainder randomly distributed, not front-loaded
- [ ] **AC7:** H7 - Model reload task skipped when model not loaded
- [ ] **AC8:** H8 - SignalGenerators cached by (top_n, bottom_n) tuple
- [ ] **AC9:** H10 - Zero print() statements in production code
- [ ] **AC10:** H11 - No bare except clauses, all use specific exceptions
- [ ] **AC11:** All new tests pass
- [ ] **AC12:** `make ci-local` passes
- [ ] **AC13:** Performance benchmark shows 10x improvement with pooling

---

## Approach

### Logical Components (6-step pattern each)

**Component 1: T3.1-FinancialPrecision (H1, H2)**
- H1: Audit and convert all float usage for monetary values to Decimal
- H2: Implement psycopg_pool.ConnectionPool for all DB clients
- Tests: `test_decimal_precision.py`, `test_connection_pool.py`

**Component 2: T3.2-Concurrency (H3, H4)**
- H3: Add threading.Lock for kill_switch_unavailable and similar globals
- H4: Replace time.sleep() with asyncio.sleep() in async contexts
- Tests: `test_kill_switch_thread_safety.py`, `test_async_sleep.py`

**Component 3: T3.3-ServiceReliability (H5, H7, H8)**
- H5: Implement subscription source tracking with ref-counting
- H7: Add guard to skip reload task when model not loaded
- H8: Cache SignalGenerators by configuration tuple
- Tests: `test_subscription_refcount.py`, `test_model_reload_skip.py`, `test_signal_generator_cache.py`

**Component 4: T3.4-CodeQuality (H6, H10, H11)**
- H6: Randomize TWAP remainder distribution
- H10: Replace all print() with structured logger calls
- H11: Replace bare except with specific exception types
- Tests: `test_twap_distribution_random.py`, `test_no_print_statements.py`, `test_specific_exceptions.py`

---

## Technical Details

### Files to Modify

**T3.1 Financial Precision:**
- `apps/execution_gateway/alpaca_client.py` - Convert float to Decimal
- `apps/execution_gateway/database.py` - Add ConnectionPool
- `apps/signal_service/model_registry.py` - Add ConnectionPool
- `apps/orchestrator/orchestrator.py` - Add ConnectionPool

**T3.2 Concurrency:**
- `apps/execution_gateway/main.py` - Add threading.Lock for global state
- `apps/execution_gateway/slice_scheduler.py` - Replace time.sleep() with asyncio.sleep()

**T3.3 Service Reliability:**
- `apps/market_data_service/position_sync.py` - Implement ref-counting
- `apps/signal_service/main.py` - Skip reload when not loaded, cache generators

**T3.4 Code Quality:**
- `apps/execution_gateway/order_slicer.py` - Randomize TWAP distribution
- Multiple files - Replace print() with logger
- `libs/common/logging/formatter.py` - Fix bare except

### New Test Files
- `tests/apps/execution_gateway/test_h1_decimal_precision.py`
- `tests/apps/execution_gateway/test_h2_connection_pool.py`
- `tests/apps/execution_gateway/test_h3_thread_safety.py`
- `tests/apps/execution_gateway/test_h4_async_sleep.py`
- `tests/apps/market_data_service/test_h5_subscription_refcount.py`
- `tests/apps/execution_gateway/test_h6_twap_distribution.py`
- `tests/apps/signal_service/test_h7_model_reload_skip.py`
- `tests/apps/signal_service/test_h8_generator_cache.py`
- `tests/test_h10_no_print.py`
- `tests/libs/test_h11_specific_exceptions.py`

---

## Implementation Details

### H1: float vs Decimal

```python
# Before
"qty": float(alpaca_order.qty or 0),
limit_price=float(order.limit_price),

# After
from decimal import Decimal
"qty": Decimal(str(alpaca_order.qty or 0)),
limit_price=Decimal(str(order.limit_price)),
```

### H2: Connection Pooling

```python
from psycopg_pool import ConnectionPool

class DatabaseClient:
    def __init__(self, db_conn_string: str):
        self.pool = ConnectionPool(
            db_conn_string,
            min_size=5,
            max_size=20,
            timeout=30,
        )

    def execute(self, query: str, params: tuple = None):
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return cur.fetchall()
```

### H3: Thread-Safe Global State

```python
import threading

_kill_switch_lock = threading.Lock()
_kill_switch_unavailable = False

def is_kill_switch_unavailable() -> bool:
    with _kill_switch_lock:
        return _kill_switch_unavailable

def set_kill_switch_unavailable(value: bool) -> None:
    with _kill_switch_lock:
        global _kill_switch_unavailable
        _kill_switch_unavailable = value
```

### H4: Async Sleep

```python
# Before
time.sleep(wait_time)  # BLOCKING

# After
await asyncio.sleep(wait_time)  # Non-blocking
```

### H5: Subscription Ref-Counting

```python
class SubscriptionManager:
    def __init__(self):
        self._subscriptions: dict[str, set[str]] = {}  # symbol -> set of sources

    def subscribe(self, symbol: str, source: str) -> None:
        if symbol not in self._subscriptions:
            self._subscriptions[symbol] = set()
            await self.stream.subscribe(symbol)
        self._subscriptions[symbol].add(source)

    def unsubscribe(self, symbol: str, source: str) -> None:
        if symbol in self._subscriptions:
            self._subscriptions[symbol].discard(source)
            if not self._subscriptions[symbol]:
                del self._subscriptions[symbol]
                await self.stream.unsubscribe(symbol)
```

### H6: Randomized TWAP Distribution

```python
import random

def distribute_slices(total_qty: int, num_slices: int) -> list[int]:
    """Distribute quantity across slices with randomized remainder."""
    base_qty = total_qty // num_slices
    remainder = total_qty % num_slices

    slice_qtys = [base_qty] * num_slices

    # Randomly assign remainder to different slices
    remainder_indices = random.sample(range(num_slices), remainder)
    for i in remainder_indices:
        slice_qtys[i] += 1

    return slice_qtys
```

---

## Dependencies

**Blockers (must complete before starting):**
- P3T2: Critical Fixes (P0) ✅ (Complete - PR #68 merged)

**Blocks (other tasks waiting on this):**
- P3T4: Medium Priority Fixes (P2)

---

## Risks & Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| Connection pool config issues | High | Medium | Test with load benchmarks |
| Thread lock deadlocks | High | Low | Keep lock scope minimal |
| Async migration breaks flow | Medium | Medium | Careful testing of all paths |
| Print removal breaks debugging | Low | Low | Ensure proper log levels |

---

## Testing Strategy

### Performance Testing
- Benchmark with 100 req/sec before/after pooling
- Verify event loop not blocked under load

### Concurrency Testing
- Multi-threaded access to global state
- Race condition stress tests

### Unit Tests
- Each fix isolated
- Decimal precision verification
- Subscription ref-count scenarios

---

## Related

**Planning:**
- [P3_PLANNING.md](./P3_PLANNING.md) - Phase 3 planning
- [P3_ISSUES.md](./P3_ISSUES.md) - Full issue list

**Tasks:**
- Depends on: [P3T2](./P3T2_TASK.md) ✅
- Blocks: P3T4 (Medium Priority Fixes)

---

## Notes

This task addresses all 11 P1 (High Priority) issues that should be fixed for production stability. Key focus areas are:
1. **Financial Precision** - Prevent precision errors in monetary calculations
2. **Performance** - 10x improvement through connection pooling
3. **Concurrency** - Thread-safe operations and non-blocking async
4. **Reliability** - Proper resource management and caching

---
