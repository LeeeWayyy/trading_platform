# P3 Planning: Issue Remediation & Strategic Modernization

**Phase:** P3 (Review, Remediation & Modernization)
**Timeline:** Days 121-180 (~60 days estimated)
**Status:** 📋 Planning (T1 Complete - Issues Identified)
**Previous Phase:** P2 (Advanced Features - Complete)
**Last Updated:** 2025-11-29

---

## Executive Summary

With P0-P2 complete, the trading platform is production-ready for paper trading. **P3 focuses on issue remediation** based on triple-reviewer analysis (47+ issues identified).

**Review Status:** ✅ Complete - See [P3_ISSUES.md](./P3_ISSUES.md)

**P3 Philosophy: "Fix Before Extend"**
- All three AI reviewers (Claude, Gemini, Codex) have identified actionable issues
- Each fix includes corresponding test cases
- Workflow modernization first to improve development velocity

---

## 📊 Progress Summary

**Overall:** 25% (1/4 tracks complete)

| Track | Tasks | Progress | Status |
|-------|-------|----------|--------|
| **Track 0: Review & Analysis** | T0.1-T0.2 | 100% | ✅ Complete |
| **Track 1: Workflow Modernization** | T1.1-T1.4 | 0% | 📋 Planning |
| **Track 2: Critical Fixes (P0)** | T2.1-T2.4 | 0% | 📋 Planning |
| **Track 3: High Priority Fixes (P1)** | T3.1-T3.4 | 0% | 📋 Planning |
| **Track 4: Medium Priority Fixes (P2)** | T4.1-T4.4 | 0% | 📋 Planning |

---

## Track 0: Review & Analysis ✅ COMPLETE

### T0.1: Triple-Reviewer Codebase Analysis ✅
- [x] Claude review completed
- [x] Gemini review completed
- [x] Codex review completed

### T0.2: Issue Consolidation & Prioritization ✅
- [x] 47+ issues identified and documented
- [x] Priority matrix created
- [x] Effort estimates provided
- [x] Full report: [P3_ISSUES.md](./P3_ISSUES.md)

---

## Track 1: Workflow Modernization ⭐⭐⭐ FIRST PRIORITY

**Goal:** Replace monolithic workflow_gate.py (4300+ lines) with modular structure. This improves development velocity for all subsequent fixes.

**Target:** `scripts/`  (trading_platform repository)

---

### T1.1: Backup & Copy Workflow Package
**Effort:** 2-4 hours
**Status:** ⏳ Pending

**Tasks:**
- [ ] Backup current workflow_gate.py to `workflow_gate.py.bak`
- [ ] Copy `ai_workflow/` package from source repository
- [ ] Copy new `workflow_gate.py` CLI entry point
- [ ] Verify file structure matches expected layout

**Commands:**
```bash
# Backup
cp scripts/workflow_gate.py scripts/workflow_gate.py.bak

# Copy package (from external source repo - already completed)
# cp -r <source_repo>/scripts/ai_workflow scripts/

# Copy CLI (from external source repo - already completed)
# cp <source_repo>/scripts/workflow_gate.py scripts/
```

**Files to Copy:**
```
ai_workflow/
├── __init__.py
├── config.py
├── constants.py
├── core.py
├── delegation.py
├── git_utils.py
├── hash_utils.py
├── planning.py
├── pr_workflow.py
├── reviewers.py
├── subtasks.py
└── tests/
    ├── conftest.py
    ├── test_core.py
    ├── test_delegation.py
    ├── test_planning.py
    └── test_cli.py
```

**Test Cases:**
- [ ] Verify all files copied successfully
- [ ] Verify no import errors when loading package
- [ ] Run `python -c "from scripts.ai_workflow import core"` to test imports

---

### T1.2: Adapt Configuration & Paths
**Effort:** 4-6 hours
**Status:** ⏳ Pending
**Dependencies:** T1.1

**Tasks:**
- [ ] Update `PROJECT_ROOT` to point to trading_platform
- [ ] Update `STATE_FILE` path (`.claude/workflow-state.json`)
- [ ] Update `AUDIT_LOG_FILE` path
- [ ] Adapt `config.json` for trading_platform reviewers
- [ ] Update imports that reference `libs/common/hash_utils`
- [ ] Update any hardcoded paths in modules

**Configuration Changes:**
```python
# config.py - Update these values (paths are now auto-calculated)
PROJECT_ROOT = Path(__file__).parent.parent.parent  # Auto-calculated
STATE_FILE = PROJECT_ROOT / ".ai_workflow" / "workflow-state.json"
AUDIT_LOG_FILE = PROJECT_ROOT / ".ai_workflow" / "workflow-audit.log"
```

**Test Cases:**
- [ ] `test_config_paths.py` - Verify all paths resolve correctly
- [ ] `test_config_loading.py` - Verify config.json loads without errors
- [ ] `test_project_root.py` - Verify PROJECT_ROOT matches actual location

---

### T1.3: Validate CLI Commands
**Effort:** 4-6 hours
**Status:** ⏳ Pending
**Dependencies:** T1.2

**Tasks:**
- [ ] Test `./scripts/workflow_gate.py status`
- [ ] Test `./scripts/workflow_gate.py start-task`
- [ ] Test `./scripts/workflow_gate.py advance` (all transitions)
- [ ] Test `./scripts/workflow_gate.py request-review`
- [ ] Test `./scripts/workflow_gate.py check-commit`
- [ ] Test `./scripts/workflow_gate.py set-components`
- [ ] Verify zen-mcp integration works

**Test Cases:**
- [ ] `test_cli_status.py` - Status command returns valid JSON
- [ ] `test_cli_start_task.py` - Task creation works
- [ ] `test_cli_advance.py` - All state transitions valid
- [ ] `test_cli_review.py` - Review request integrates with zen-mcp
- [ ] `test_cli_check_commit.py` - Pre-commit validation works
- [ ] Integration test: Full 6-step workflow cycle

---

### T1.4: Update Documentation & Hooks
**Effort:** 2-4 hours
**Status:** ⏳ Pending
**Dependencies:** T1.3

**Tasks:**
- [ ] Update CLAUDE.md with new workflow commands
- [ ] Update pre-commit hook if needed
- [ ] Archive old workflow_gate.py.bak
- [ ] Document any breaking changes
- [ ] Update AI/Workflows/ documentation

**Test Cases:**
- [ ] Pre-commit hook integration test
- [ ] Verify CLAUDE.md examples work
- [ ] Run `make ci-local` to verify CI integration

**Acceptance Criteria:**
- [ ] All workflow commands work correctly
- [ ] All workflow tests pass (90%+ coverage)
- [ ] Pre-commit hook enforces gates
- [ ] Documentation updated
- [ ] Old script archived

---

## Track 2: Critical Fixes (P0) ⭐⭐⭐ BLOCK PRODUCTION

**Goal:** Fix 8 critical issues before any production deployment

**Estimated Total Effort:** 2-3 days

---

### T2.1: Security Fixes
**Effort:** 4-6 hours
**Status:** ⏳ Pending
**Dependencies:** T1.4

**Issues Addressed:**
| ID | Issue | Location |
|----|-------|----------|
| C3 | Webhook secret fail-open | `execution_gateway/main.py:2162` |
| C6 | CORS wildcard | `signal_service/main.py:393` |

**Tasks:**
- [ ] C3: Make WEBHOOK_SECRET mandatory in non-dev environments
- [ ] C3: Add startup validation that raises if secret missing
- [ ] C6: Replace `allow_origins=["*"]` with environment-based allowlist
- [ ] C6: Add ALLOWED_ORIGINS environment variable

**Implementation (C3):**
```python
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
ENVIRONMENT = os.getenv("ENVIRONMENT", "dev")

if not WEBHOOK_SECRET and ENVIRONMENT not in ("dev", "test"):
    raise RuntimeError("WEBHOOK_SECRET must be set for production/staging")
```

**Implementation (C6):**
```python
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "").split(",")
if not ALLOWED_ORIGINS or ALLOWED_ORIGINS == [""]:
    if ENVIRONMENT in ("dev", "test"):
        ALLOWED_ORIGINS = ["http://localhost:8501", "http://127.0.0.1:8501"]
    else:
        raise RuntimeError("ALLOWED_ORIGINS must be set for production")
```

**Test Cases:**
- [ ] `test_webhook_secret_required.py` - Verify startup fails without secret in prod
- [ ] `test_webhook_secret_optional_dev.py` - Verify dev mode allows missing secret
- [ ] `test_cors_allowlist.py` - Verify only allowed origins accepted
- [ ] `test_cors_rejects_unknown.py` - Verify unknown origins rejected
- [ ] Integration: Webhook with valid/invalid signatures

---

### T2.2: Trading Safety Fixes
**Effort:** 6-8 hours
**Status:** ⏳ Pending
**Dependencies:** T1.4

**Issues Addressed:**
| ID | Issue | Location |
|----|-------|----------|
| C2 | Hardcoded $100 fallback price | `orchestrator/orchestrator.py:500` |
| C7 | Missing circuit breaker check | `execution_gateway/main.py:922` |

**Tasks:**
- [ ] C2: Remove $100 default price, raise error when price unavailable
- [ ] C2: Add PriceUnavailableError exception class
- [ ] C2: Update callers to handle missing prices gracefully
- [ ] C7: Add circuit breaker check before order submission
- [ ] C7: Add risk limit validation before Alpaca call
- [ ] C7: Return 503 when circuit breaker tripped

**Implementation (C2):**
```python
class PriceUnavailableError(Exception):
    """Raised when price data is not available for a symbol."""
    pass

def get_price(self, symbol: str) -> Decimal:
    if symbol in self.price_cache:
        return Decimal(str(self.price_cache[symbol]))

    # Try to fetch from market data service
    price = self._fetch_price_from_service(symbol)
    if price is None:
        logger.error(f"Price unavailable for {symbol}")
        raise PriceUnavailableError(f"No price available for {symbol}")
    return price
```

**Implementation (C7):**
```python
@app.post("/api/v1/orders")
async def submit_order(order: OrderRequest):
    # Existing kill-switch check...

    # ADD: Circuit breaker check
    if circuit_breaker.is_tripped():
        logger.warning("Order blocked: circuit breaker tripped",
                      extra={"symbol": order.symbol, "qty": order.qty})
        raise HTTPException(status_code=503, detail="Circuit breaker active")

    # ADD: Risk validation
    risk_result = await validate_order_risk(order)
    if not risk_result.is_valid:
        raise HTTPException(status_code=409, detail=risk_result.reason)

    # Continue with order submission...
```

**Test Cases:**
- [ ] `test_price_unavailable_error.py` - Verify error raised when no price
- [ ] `test_price_fetch_fallback.py` - Verify service fetch attempted
- [ ] `test_circuit_breaker_blocks_order.py` - Verify 503 when tripped
- [ ] `test_circuit_breaker_allows_order.py` - Verify order proceeds when open
- [ ] `test_risk_validation.py` - Verify risk limits enforced
- [ ] Integration: Order flow with circuit breaker states

---

### T2.3: Data Integrity Fixes
**Effort:** 4-6 hours
**Status:** ⏳ Pending
**Dependencies:** T1.4

**Issues Addressed:**
| ID | Issue | Location |
|----|-------|----------|
| C1 | P&L calculation verification | `execution_gateway/database.py:889` |
| C4 | Idempotency race condition | `execution_gateway/main.py:1088` |
| C8 | COALESCE bug | `execution_gateway/database.py:769` |

**Tasks:**
- [ ] C1: Add comprehensive P&L calculation tests (long/short, partial/full)
- [ ] C1: Document P&L formulas with examples
- [ ] C4: Catch UniqueViolation and return existing order
- [ ] C4: Consider INSERT ON CONFLICT for atomic idempotency
- [ ] C8: Fix COALESCE to allow clearing error messages

**Implementation (C4):**
```python
try:
    db_client.create_order(
        client_order_id=client_order_id,
        symbol=order.symbol,
        # ...
    )
except psycopg.errors.UniqueViolation:
    # Race condition - another request created the order
    logger.info(f"Idempotent return for {client_order_id} (race condition)")
    existing = db_client.get_order_by_client_id(client_order_id)
    if existing:
        return existing
    raise  # Re-raise if still not found (shouldn't happen)
```

**Implementation (C8):**
```python
# Option 1: Use empty string instead of None to clear
# Option 2: Conditional update
if error_message is not None:
    cur.execute("""
        UPDATE orders
        SET error_message = %s, updated_at = NOW()
        WHERE client_order_id = %s
    """, (error_message, client_order_id))
else:
    cur.execute("""
        UPDATE orders
        SET updated_at = NOW()
        WHERE client_order_id = %s
    """, (client_order_id,))
```

**Test Cases:**
- [ ] `test_pnl_long_close.py` - Long position closing P&L
- [ ] `test_pnl_short_close.py` - Short position closing P&L
- [ ] `test_pnl_partial_close.py` - Partial position reduction
- [ ] `test_idempotency_race.py` - Concurrent submissions same ID
- [ ] `test_error_message_clear.py` - Verify error can be cleared
- [ ] `test_error_message_update.py` - Verify error can be updated

---

### T2.4: API Contract Fixes
**Effort:** 3-4 hours
**Status:** ⏳ Pending
**Dependencies:** T1.4

**Issues Addressed:**
| ID | Issue | Location |
|----|-------|----------|
| C5 | Timezone bugs (naive datetime) | `execution_gateway/main.py:351+` |

**Tasks:**
- [ ] Find all `datetime.now()` calls without timezone
- [ ] Replace with `datetime.now(UTC)`
- [ ] Add `from datetime import UTC` imports
- [ ] Verify database timestamps are timezone-aware
- [ ] Update API response models to enforce timezone

**Search Command:**
```bash
grep -rn "datetime.now()" apps/ --include="*.py" | grep -v "UTC"
```

**Implementation:**
```python
from datetime import datetime, UTC

# Before
timestamp = datetime.now()

# After
timestamp = datetime.now(UTC)
```

**Test Cases:**
- [ ] `test_api_timestamps_utc.py` - All API responses have UTC timestamps
- [ ] `test_db_timestamps_aware.py` - DB timestamps are timezone-aware
- [ ] `test_timestamp_comparison.py` - Comparisons work correctly

**Acceptance Criteria (Track 2):**
- [ ] All 8 critical issues resolved
- [ ] All test cases pass
- [ ] No regression in existing tests
- [ ] CI passes

---

## Track 3: High Priority Fixes (P1)

**Goal:** Fix 11 high-priority issues for production stability

**Estimated Total Effort:** 5-7 days

---

### T3.1: Financial Precision & Performance
**Effort:** 8-10 hours
**Status:** ⏳ Pending
**Dependencies:** T2.4

**Issues Addressed:**
| ID | Issue | Location |
|----|-------|----------|
| H1 | float vs Decimal | `execution_gateway/alpaca_client.py:140` |
| H2 | Missing DB connection pooling | `execution_gateway/database.py` |

**Tasks:**
- [ ] H1: Audit all float usage for monetary values
- [ ] H1: Replace with Decimal throughout
- [ ] H1: Update Pydantic models to use Decimal
- [ ] H2: Implement psycopg_pool.ConnectionPool
- [ ] H2: Add pool to execution_gateway, signal_service, orchestrator
- [ ] H2: Configure min_size=5, max_size=20

**Implementation (H2):**
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

**Test Cases:**
- [ ] `test_decimal_precision.py` - Verify no float precision errors
- [ ] `test_decimal_serialization.py` - JSON serialization works
- [ ] `test_connection_pool.py` - Pool reuses connections
- [ ] `test_connection_pool_exhaustion.py` - Graceful handling at limit
- [ ] Performance: Benchmark 100 req/sec with pooling

---

### T3.2: Concurrency & Thread Safety
**Effort:** 6-8 hours
**Status:** ⏳ Pending
**Dependencies:** T2.4

**Issues Addressed:**
| ID | Issue | Location |
|----|-------|----------|
| H3 | Global state race condition | `execution_gateway/main.py:154` |
| H4 | Blocking sleep in async | `execution_gateway/slice_scheduler.py:574` |

**Tasks:**
- [ ] H3: Add threading.Lock for kill_switch_unavailable
- [ ] H3: Or convert to threading.Event for cleaner API
- [ ] H4: Replace time.sleep() with asyncio.sleep()
- [ ] H4: Ensure all async functions use async I/O

**Implementation (H3):**
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

**Implementation (H4):**
```python
# Before
time.sleep(wait_time)

# After
await asyncio.sleep(wait_time)
```

**Test Cases:**
- [ ] `test_kill_switch_thread_safety.py` - Concurrent read/write
- [ ] `test_async_sleep.py` - Non-blocking sleep verification
- [ ] `test_event_loop_not_blocked.py` - Event loop remains responsive
- [ ] Load test: 100 concurrent requests with state changes

---

### T3.3: Service Reliability
**Effort:** 6-8 hours
**Status:** ⏳ Pending
**Dependencies:** T2.4

**Issues Addressed:**
| ID | Issue | Location |
|----|-------|----------|
| H5 | Subscription ref-counting | `market_data_service/position_sync.py:95` |
| H7 | Model reload without model | `signal_service/main.py:119` |
| H8 | Temp SignalGenerator per request | `signal_service/main.py:933` |

**Tasks:**
- [ ] H5: Implement subscription source tracking (manual vs position)
- [ ] H5: Only unsubscribe when ref count reaches 0
- [ ] H7: Skip reload task when model not loaded
- [ ] H8: Cache SignalGenerators by (top_n, bottom_n) tuple

**Implementation (H5):**
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

**Test Cases:**
- [ ] `test_subscription_refcount.py` - Multiple sources same symbol
- [ ] `test_subscription_partial_unsubscribe.py` - One source removed
- [ ] `test_model_reload_skip.py` - No reload when not loaded
- [ ] `test_signal_generator_cache.py` - Generators cached correctly

---

### T3.4: Code Quality & Trading Efficiency
**Effort:** 6-8 hours
**Status:** ⏳ Pending
**Dependencies:** T2.4

**Issues Addressed:**
| ID | Issue | Location |
|----|-------|----------|
| H6 | TWAP front-loaded distribution | `execution_gateway/order_slicer.py:158` |
| H10 | 176+ print() statements | Throughout codebase |
| H11 | Bare except clause | `libs/common/logging/formatter.py` |

**Tasks:**
- [ ] H6: Randomize or center-weight TWAP remainder distribution
- [ ] H6: Document distribution strategy in docstring
- [ ] H10: Replace all print() with structured logger calls
- [ ] H10: Add correlation IDs where missing
- [ ] H11: Replace bare except with specific exceptions

**Implementation (H6):**
```python
import random

def distribute_slices(total_qty: int, num_slices: int) -> list[int]:
    """Distribute quantity across slices with randomized remainder.

    Uses randomized distribution to reduce market impact pattern detection.
    """
    base_qty = total_qty // num_slices
    remainder = total_qty % num_slices

    slice_qtys = [base_qty] * num_slices

    # Randomly assign remainder to different slices
    remainder_indices = random.sample(range(num_slices), remainder)
    for i in remainder_indices:
        slice_qtys[i] += 1

    return slice_qtys
```

**Test Cases:**
- [ ] `test_twap_distribution_sum.py` - Total equals input
- [ ] `test_twap_distribution_random.py` - Remainder not front-loaded
- [ ] `test_no_print_statements.py` - grep finds zero print()
- [ ] `test_structured_logging.py` - All logs use logger
- [ ] `test_specific_exceptions.py` - No bare except clauses

**Acceptance Criteria (Track 3):**
- [ ] All 11 high-priority issues resolved
- [ ] All test cases pass
- [ ] No regression in existing tests
- [ ] CI passes
- [ ] Performance benchmarks meet targets

---

## Track 4: Medium Priority Fixes (P2)

**Goal:** Fix 16 medium-priority issues for code quality

**Estimated Total Effort:** 5-7 days

---

### T4.1: Type Safety & Validation
**Effort:** 4-6 hours
**Status:** ⏳ Pending
**Dependencies:** T3.4

**Issues Addressed:**
| ID | Issue | Location |
|----|-------|----------|
| M1 | Price cache float/Decimal mismatch | `orchestrator/orchestrator.py:739` |
| M3 | Mid-price TypeError | `execution_gateway/alpaca_client.py:315` |
| M6 | TRUSTED_PROXY_IPS empty | `auth_service/main.py:177` |

**Tasks:**
- [ ] M1: Normalize price cache to Decimal
- [ ] M3: Add None checks before mid-price calculation
- [ ] M6: Use localhost default in dev environments

**Test Cases:**
- [ ] `test_price_cache_decimal.py` - Cache stores Decimal
- [ ] `test_midprice_none_handling.py` - Graceful None handling
- [ ] `test_proxy_ips_default.py` - Dev has safe defaults

---

### T4.2: Lifecycle & Shutdown
**Effort:** 4-6 hours
**Status:** ⏳ Pending
**Dependencies:** T3.4

**Issues Addressed:**
| ID | Issue | Location |
|----|-------|----------|
| M2 | Position sync task not cancelled | `market_data_service/position_sync.py:68` |
| M4 | Orchestration 200 on failure | `orchestrator/main.py:530` |

**Tasks:**
- [ ] M2: Store task handle, cancel on shutdown
- [ ] M2: Use asyncio.Event instead of long sleeps
- [ ] M4: Return 500 for unexpected exceptions

**Test Cases:**
- [ ] `test_shutdown_cancels_tasks.py` - Clean shutdown
- [ ] `test_error_status_codes.py` - 500 for failures

---

### T4.3: Performance Optimization
**Effort:** 6-8 hours
**Status:** ⏳ Pending
**Dependencies:** T3.4

**Issues Addressed:**
| ID | Issue | Location |
|----|-------|----------|
| M5 | Feature generation blocks | `signal_service/signal_generator.py:320` |
| M7 | Web console connection pooling | `web_console/app.py:662` |

**Tasks:**
- [ ] M5: Enable Redis feature cache in production
- [ ] M5: Pre-compute features at day start
- [ ] M7: Add connection pooling to web console

**Test Cases:**
- [ ] `test_feature_cache_hit.py` - Cache reduces disk I/O
- [ ] `test_web_console_pool.py` - Connection reuse

---

### T4.4: Code Cleanup
**Effort:** 4-6 hours
**Status:** ⏳ Pending
**Dependencies:** T3.4

**Issues Addressed:**
| ID | Issue | Location |
|----|-------|----------|
| M8 | 270+ TODO comments | Repository-wide |
| M9 | Deprecated zen_review field | `workflow_gate.py:87` |
| M10 | Rate limiting | `execution_gateway/main.py:922` |
| M11 | Kill-switch reason validation | `execution_gateway/main.py:750` |

**Tasks:**
- [ ] M8: Audit TODOs, create issues for actionable items
- [ ] M8: Remove stale/completed TODOs
- [ ] M9: Remove deprecated zen_review field
- [ ] M10: Add rate limiting with slowapi
- [ ] M11: Add max_length validation on reason field

**Test Cases:**
- [ ] `test_todo_count.py` - Less than 50 TODOs remain
- [ ] `test_rate_limiting.py` - Limits enforced
- [ ] `test_reason_validation.py` - Max length enforced

**Acceptance Criteria (Track 4):**
- [ ] All 16 medium-priority issues resolved
- [ ] TODO count reduced to <50
- [ ] All test cases pass
- [ ] CI passes

---

## Execution Timeline

### Week 1: Workflow + Critical Start (Days 121-127)
| Day | Tasks | Focus |
|-----|-------|-------|
| 1-2 | T1.1, T1.2 | Copy workflow, adapt config |
| 3 | T1.3 | Validate CLI commands |
| 4 | T1.4 | Documentation, hooks |
| 5-7 | T2.1, T2.2 | Security + Trading safety |

**Milestone:** Workflow modernized, critical security fixed

### Week 2: Critical + High Start (Days 128-134)
| Day | Tasks | Focus |
|-----|-------|-------|
| 1-2 | T2.3, T2.4 | Data integrity + API contracts |
| 3-5 | T3.1 | Financial precision + DB pooling |
| 6-7 | T3.2 | Concurrency fixes |

**Milestone:** All P0 issues resolved

### Week 3: High Priority (Days 135-141)
| Day | Tasks | Focus |
|-----|-------|-------|
| 1-3 | T3.3 | Service reliability |
| 4-6 | T3.4 | Code quality + trading efficiency |

**Milestone:** All P1 issues resolved

### Week 4+: Medium Priority (Days 142-160)
| Day | Tasks | Focus |
|-----|-------|-------|
| 1-2 | T4.1 | Type safety |
| 3-4 | T4.2 | Lifecycle + shutdown |
| 5-7 | T4.3 | Performance |
| 8-10 | T4.4 | Code cleanup |

**Milestone:** All P2 issues resolved, P3 complete

---

## Success Metrics

| Metric | Before | Target | Status |
|--------|--------|--------|--------|
| **P0 Issues** | 8 | 0 | 🔴 |
| **P1 Issues** | 11 | 0 | 🔴 |
| **P2 Issues** | 16 | 0 | 🔴 |
| **workflow_gate.py lines** | 4,300+ | <300 | 🔴 |
| **print() statements** | 176+ | 0 | 🔴 |
| **Test coverage** | 81% | >85% | 🟡 |
| **Type coverage** | 100% | 100% | 🟢 |

---

## Test Strategy

### Test Requirements Per Fix
Every issue fix MUST include:
1. **Unit tests** - Test the specific fix in isolation
2. **Regression tests** - Ensure existing behavior not broken
3. **Integration tests** - Test interaction with other components (where applicable)

### Test Naming Convention
```
tests/apps/{service}/test_{issue_id}_{description}.py

Examples:
- test_c3_webhook_secret_required.py
- test_h2_connection_pooling.py
- test_m1_price_cache_decimal.py
```

### CI Integration
- All tests run on PR
- Coverage must not decrease
- Type checking must pass

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Workflow copy breaks processes | Low | High | Keep backup, thorough T1.3 validation |
| Critical fixes cause regressions | Medium | High | Comprehensive test cases per fix |
| Scope creep | High | Medium | Strict adherence to issue list |
| Timeline slip | Medium | Medium | Track 4 is buffer for delays |

---

## Related Documents

- [P3_ISSUES.md](./P3_ISSUES.md) - Full issue list (47+ issues)
- [P1_PLANNING.md](./P1_PLANNING.md) - Phase 1 (completed)
- [P2_PLANNING.md](./P2_PLANNING.md) - Phase 2 (completed)

---

**Last Updated:** 2025-11-29
**Author:** Claude Code
**Version:** 4.0 (Reorganized with workflow first, 4 subtasks per track, test cases)
