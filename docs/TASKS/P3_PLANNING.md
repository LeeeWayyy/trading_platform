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

**Overall:** 20% (1/5 tracks complete)

| Track | Tasks | Progress | Status |
|-------|-------|----------|--------|
| **Track 0: Review & Analysis** | T0.1-T0.2 | 100% | ✅ Complete |
| **Track 1: Workflow Modernization** | T1.1-T1.4 | 0% | 📋 Planning |
| **Track 2: Critical Fixes (P0)** | T2.1-T2.4 | 0% | 📋 Planning |
| **Track 3: High Priority Fixes (P1)** | T3.1-T3.4 | 0% | 📋 Planning |
| **Track 4: Medium Priority Fixes (P2)** | T4.1-T4.4 | 0% | 📋 Planning |
| **Track 5: External Review Findings** | T5.1-T5.6 | 0% | 📋 Verified (2025-12-01, 2 reviewers) |

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

## Track 5: External Review Findings (2025-12-01)

**Source:** External AI Reviewer Analysis
**Status:** 📋 Verified & Queued for Implementation

### New Critical Issues Identified (P0)

These issues were verified against the actual codebase and confirmed as genuine vulnerabilities:

---

### T5.1: Kill Switch Bypass Risk ⚠️ CRITICAL
**Effort:** 2-4 hours
**Status:** ⏳ Pending
**Priority:** P0 (MUST FIX BEFORE PRODUCTION)

**Finding:** `RiskChecker` class (`libs/risk_management/checker.py`) checks CircuitBreaker and Blacklists but does NOT check KillSwitch.

**Impact:** If developers rely solely on `RiskChecker.validate_order()` as the "single source of truth" for trade safety, the Kill Switch will be effectively bypassed.

**Evidence:**
- `checker.py:34` - Only imports CircuitBreaker, not KillSwitch
- `checker.py:68-84` - Constructor only accepts CircuitBreaker
- `checker.py:146-152` - Only checks `self.breaker.is_tripped()`, no kill switch check

**Fix:** Inject KillSwitch into RiskChecker and check `is_engaged()` as the FIRST step in `validate_order()`.

**Implementation:**
```python
from libs.risk_management.kill_switch import KillSwitch

class RiskChecker:
    def __init__(self, config: RiskConfig, breaker: CircuitBreaker, kill_switch: KillSwitch | None = None):
        self.config = config
        self.breaker = breaker
        self.kill_switch = kill_switch

    def validate_order(self, ...):
        # 0. Kill switch check (HIGHEST priority - absolute stop)
        if self.kill_switch and self.kill_switch.is_engaged():
            reason = "Kill switch ENGAGED: All trading halted"
            logger.critical(f"Order blocked by kill switch: {symbol} {side} {qty}")
            return (False, reason)

        # 1. Circuit breaker check (second priority)
        if self.breaker.is_tripped():
            # ... existing code
```

**Test Cases:**
- [ ] `test_kill_switch_blocks_order.py` - Verify order blocked when engaged
- [ ] `test_kill_switch_allows_order.py` - Verify order proceeds when active
- [ ] `test_risk_checker_integration.py` - Full integration with all risk checks

---

### T5.2: Position Limit Race Condition ⚠️ CRITICAL
**Effort:** 6-8 hours
**Status:** ⏳ Pending
**Priority:** P0 (FINANCIAL RISK)

**Finding:** `RiskChecker.validate_order` is stateless and suffers from a Check-Then-Act race condition.

**Scenario:**
1. Process A reads current position (0) and requests to buy 100 shares (Limit 150). Check passes.
2. Process B reads current position (0) BEFORE Process A's trade executes. Check passes.
3. Both trades execute. Resulting position: 200 (Exceeds limit of 150).

**Evidence:**
- `checker.py:91-92` - `current_position` passed as argument
- `checker.py:161-175` - Stateless check with no atomic locking

**Fix:** Use Redis to implement a "reservation" system or use Lua scripts to atomically check-and-update position limits.

**Implementation:**
```python
def validate_order_atomic(self, symbol: str, side: str, qty: int) -> tuple[bool, str]:
    """Atomic position validation using Redis reservation."""
    reservation_key = f"position_reservation:{symbol}"

    lua_script = """
    local current = tonumber(redis.call('GET', KEYS[1]) or '0')
    local delta = tonumber(ARGV[1])
    local limit = tonumber(ARGV[2])
    local new_position = current + delta

    if math.abs(new_position) > limit then
        return {0, 'LIMIT_EXCEEDED'}
    end

    redis.call('SET', KEYS[1], new_position)
    return {1, 'OK'}
    """

    delta = qty if side == "buy" else -qty
    result = self.redis.eval(lua_script, 1, reservation_key, delta, self.config.max_position)

    if result[0] == 0:
        return (False, f"Position limit exceeded: {result[1]}")
    return (True, "")
```

**Test Cases:**
- [ ] `test_position_race_condition.py` - Simulate concurrent submissions
- [ ] `test_atomic_reservation.py` - Verify Lua script atomicity
- [ ] `test_reservation_rollback.py` - Verify cleanup on failed orders

---

### T5.3: Circuit Breaker Fail-Open Risk ⚠️ CRITICAL
**Effort:** 2-4 hours
**Status:** ⏳ Pending
**Priority:** P0 (SAFETY CRITICAL)

**Finding:** CircuitBreaker defaults to OPEN if Redis state is missing (e.g., after Redis crash/flush). KillSwitch correctly fails closed.

**Evidence:**
- `breaker.py:159-163` - `get_state()` calls `_initialize_state()` which defaults to OPEN
- `kill_switch.py:155-166` - Correctly raises RuntimeError (fail-closed pattern)

**Impact:** If the system is in a TRIPPED state and Redis restarts, the breaker silently resets to OPEN, potentially allowing trading to resume during a risk event.

**Fix:** Modify `CircuitBreaker.get_state()` to raise an error or default to TRIPPED if Redis state is missing.

**Implementation:**
```python
def get_state(self) -> CircuitBreakerState:
    state_json = self.redis.get(self.state_key)
    if not state_json:
        # FAIL CLOSED: Do not auto-reinitialize to OPEN
        logger.error(
            "CRITICAL: Circuit breaker state missing from Redis (possible flush/restart). "
            "Failing closed to prevent unsafe trading resumption."
        )
        raise RuntimeError(
            "Circuit breaker state missing from Redis. System must fail closed - "
            "operator must verify safety and manually reset if appropriate."
        )
    # ... rest of existing logic
```

**Test Cases:**
- [ ] `test_breaker_missing_state_fails_closed.py` - Verify exception raised
- [ ] `test_breaker_redis_restart.py` - Simulate Redis restart scenario
- [ ] `test_breaker_manual_recovery.py` - Verify manual recovery workflow

---

### T5.4: Order ID Timezone Normalization
**Effort:** 1-2 hours
**Status:** ⏳ Pending
**Priority:** P1 (DATA INTEGRITY)

**Finding:** `order_id_generator.py` uses `date.today()` which uses system local time, not UTC.

**Evidence:**
- `order_id_generator.py:115` - `order_date = as_of_date or date.today()`

**Impact:** If servers run in different timezones, or around midnight transitions, client_order_id generation becomes non-deterministic, potentially causing duplicate orders.

**Fix:** Use `datetime.now(UTC).date()` instead.

**Implementation:**
```python
from datetime import UTC, datetime

def generate_client_order_id(order: OrderRequest, strategy_id: str, as_of_date: date | None = None) -> str:
    # Use provided date or default to TODAY IN UTC
    order_date = as_of_date or datetime.now(UTC).date()
    # ... rest unchanged
```

**Test Cases:**
- [ ] `test_order_id_utc.py` - Verify UTC date used
- [ ] `test_order_id_midnight_transition.py` - Verify consistent IDs across midnight

---

### T5.5: Order ID Collision - Missing Order Type/TIF ⚠️ CRITICAL
**Effort:** 2-3 hours
**Status:** ⏳ Pending
**Priority:** P0 (IDEMPOTENCY VIOLATION)
**Source:** External Reviewer #2 (2025-12-01)

**Finding:** `generate_client_order_id()` omits `order_type` and `time_in_force` fields from the hash, causing materially different orders to collide.

**Evidence:**
- `order_id_generator.py:107-111` - Explicitly states: "order_type and time_in_force are NOT included because they don't affect order uniqueness for our use case"
- `order_id_generator.py:122-129` - Hash only includes: symbol, side, qty, limit_price, stop_price, strategy_id, date

**Impact:** Two orders with identical parameters but different order semantics will get the SAME client_order_id:
- Order A: AAPL, buy, 100, limit $150, `order_type=limit`, `time_in_force=day`
- Order B: AAPL, buy, 100, limit $150, `order_type=limit`, `time_in_force=gtc`
- Both generate identical ID → Alpaca rejects second order as duplicate

**Scenario:**
1. Submit limit order for AAPL, 100 shares at $150, DAY
2. Order expires at market close (not filled)
3. Next morning (same calendar day), submit SAME order as GTC
4. Alpaca rejects as duplicate client_order_id
5. Trading opportunity missed

**Fix:** Include `order_type` and `time_in_force` in the hash.

**Implementation:**
```python
def generate_client_order_id(
    order: OrderRequest, strategy_id: str, as_of_date: date | None = None
) -> str:
    # Use provided date or default to TODAY IN UTC
    order_date = as_of_date or datetime.now(UTC).date()

    # Convert prices to strings (quantize to fixed precision for idempotency)
    limit_price_str = _format_price_for_id(order.limit_price)
    stop_price_str = _format_price_for_id(order.stop_price)

    # Build raw string with all order parameters INCLUDING order_type and time_in_force
    raw = (
        f"{order.symbol}|"
        f"{order.side}|"
        f"{order.qty}|"
        f"{limit_price_str}|"
        f"{stop_price_str}|"
        f"{order.order_type}|"      # NEW: Include order type
        f"{order.time_in_force}|"   # NEW: Include time in force
        f"{strategy_id}|"
        f"{order_date.isoformat()}"
    )

    # Hash with SHA256 and take first 24 characters
    hash_obj = hashlib.sha256(raw.encode("utf-8"))
    client_order_id = hash_obj.hexdigest()[:24]

    return client_order_id
```

**Test Cases:**
- [ ] `test_order_id_different_order_type.py` - Verify market vs limit orders get different IDs
- [ ] `test_order_id_different_tif.py` - Verify DAY vs GTC orders get different IDs
- [ ] `test_order_id_backwards_compat.py` - Document/migrate existing order ID format

---

### T5.6: Data Freshness Partial Staleness Risk
**Effort:** 3-4 hours
**Status:** ⏳ Pending
**Priority:** P1 (DATA INTEGRITY)
**Source:** External Reviewer #2 (2025-12-01)

**Finding:** `check_freshness()` only inspects the latest timestamp (`max()`), allowing mixed fresh/stale datasets to pass validation.

**Evidence:**
- `libs/data_pipeline/freshness.py:70` - `latest_timestamp = df["timestamp"].max()`

**Impact:** If a batch contains:
- 999 rows with timestamps from 2 hours ago (stale)
- 1 row with timestamp from now (fresh)
The check passes because max() returns the fresh timestamp, but 99.9% of the data is stale.

**Scenario:**
1. Market data pipeline partially fails, only updates 1 symbol
2. AAPL data is 2 hours old, MSFT data is current
3. Freshness check passes (MSFT is fresh)
4. Signals generated for AAPL using 2-hour-old data
5. Trading decision based on stale prices → potential loss

**Fix:** Validate minimum/median timestamps or per-symbol max-age to catch partially stale batches.

**Implementation:**
```python
def check_freshness(
    df: pl.DataFrame,
    max_age_minutes: int = 30,
    check_mode: str = "latest",  # "latest", "oldest", "median", "per_symbol"
    min_fresh_pct: float = 0.9,  # For "per_symbol" mode: 90% of symbols must be fresh
) -> None:
    """
    Validate that data is fresh enough for trading.

    Args:
        df: DataFrame with 'timestamp' column (must be timezone-aware UTC)
        max_age_minutes: Maximum acceptable age in minutes (default: 30)
        check_mode: Validation strategy:
            - "latest": Only check most recent timestamp (original behavior, fast)
            - "oldest": Check oldest timestamp (strictest, catches any staleness)
            - "median": Check median timestamp (balanced, resilient to outliers)
            - "per_symbol": Check each symbol's latest timestamp (recommended)
        min_fresh_pct: For "per_symbol" mode, minimum percentage of symbols
                       that must be fresh (default: 90%)

    Raises:
        StalenessError: If freshness check fails
    """
    if check_mode == "oldest":
        check_timestamp = df["timestamp"].min()
        mode_desc = "oldest"
    elif check_mode == "median":
        check_timestamp = df["timestamp"].median()
        mode_desc = "median"
    elif check_mode == "per_symbol":
        # Check per-symbol freshness
        symbol_latest = df.group_by("symbol").agg(
            pl.col("timestamp").max().alias("latest")
        )
        now = datetime.now(UTC)

        stale_symbols = []
        for row in symbol_latest.iter_rows(named=True):
            age_minutes = (now - row["latest"]).total_seconds() / 60
            if age_minutes > max_age_minutes:
                stale_symbols.append((row["symbol"], age_minutes))

        fresh_pct = 1 - (len(stale_symbols) / len(symbol_latest))
        if fresh_pct < min_fresh_pct:
            raise StalenessError(
                f"Only {fresh_pct*100:.1f}% of symbols are fresh "
                f"(threshold: {min_fresh_pct*100:.1f}%). "
                f"Stale symbols: {stale_symbols[:5]}..."
            )
        return
    else:  # "latest" (default)
        check_timestamp = df["timestamp"].max()
        mode_desc = "latest"

    # ... existing age check logic using check_timestamp
```

**Test Cases:**
- [ ] `test_freshness_mixed_stale.py` - Verify fails when most data is stale but one row is fresh
- [ ] `test_freshness_per_symbol.py` - Verify per-symbol checking works correctly
- [ ] `test_freshness_oldest_mode.py` - Verify strictest check catches any staleness
- [ ] `test_freshness_backwards_compat.py` - Verify default "latest" mode maintains existing behavior

---

### Verified Positive Findings ✅

The reviewer also correctly identified these as properly implemented:

| Component | Finding | Evidence |
|-----------|---------|----------|
| **Webhook Security** | Uses `hmac.compare_digest` for constant-time comparison | `webhook_security.py:61` |
| **Secret Redaction** | Explicitly forbids logging secret values | `manager.py:26-28,68-70` |
| **KillSwitch Fail-Closed** | Raises RuntimeError on missing state | `kill_switch.py:155-166` |
| **Lua Scripts for Atomicity** | KillSwitch uses Lua for atomic state transitions | `kill_switch.py:220-268` |

---

### Updated Priority Matrix

**Track 5 Issues (New - Verified):**
| ID | Issue | Severity | Effort | Location | Source |
|----|-------|----------|--------|----------|--------|
| T5.1 | Kill Switch Bypass | 🔴 P0 | 2-4h | `libs/risk_management/checker.py` | Reviewer #1 |
| T5.2 | Position Limit Race | 🔴 P0 | 6-8h | `libs/risk_management/checker.py` | Reviewer #1, #2 |
| T5.3 | Breaker Fail-Open | 🔴 P0 | 2-4h | `libs/risk_management/breaker.py` | Reviewer #1, #2 |
| T5.4 | Timezone Bug | 🟡 P1 | 1-2h | `execution_gateway/order_id_generator.py` | Reviewer #1 |
| T5.5 | Order ID Collision | 🔴 P0 | 2-3h | `execution_gateway/order_id_generator.py` | Reviewer #2 |
| T5.6 | Partial Staleness | 🟡 P1 | 3-4h | `libs/data_pipeline/freshness.py` | Reviewer #2 |

**Updated Totals:**
- P0 (Critical): 8 existing + 4 new = **12 issues**
- P1 (High): 11 existing + 2 new = **13 issues**

**Recommended Test Priorities (from Reviewer #2):**
1. Circuit breaker fail-closed behavior tests
2. Atomic position limit tests
3. Order ID uniqueness across order types tests
4. Per-symbol freshness validation tests

---

## Appendix A: Complete Operational Runbook

This runbook provides step-by-step instructions for operating the trading platform. All commands are verified against the actual codebase.

---

### A.1 Prerequisites

#### Hardware Requirements
| Component | Minimum | Recommended |
|-----------|---------|-------------|
| **CPU** | 4 vCPUs | 8 vCPUs |
| **RAM** | 16GB | 32GB |
| **Storage** | 50GB SSD | 100GB NVMe SSD |
| **Network** | Stable internet | Low-latency connection to Alpaca |

#### Software Requirements
| Software | Version | Installation |
|----------|---------|--------------|
| **Python** | 3.11+ | `brew install python@3.11` or `apt install python3.11` |
| **Docker** | 24.0+ | https://docs.docker.com/get-docker/ |
| **Docker Compose** | 2.20+ | Included with Docker Desktop |
| **Poetry** | 1.7+ | `curl -sSL https://install.python-poetry.org \| python3 -` |
| **Make** | 4.0+ | Pre-installed on macOS/Linux |
| **Node.js** | 18+ | Required for markdown-link-check (CI) |

#### External Accounts Required
| Service | Purpose | Setup Link |
|---------|---------|------------|
| **Alpaca Markets** | Paper/Live trading API | https://alpaca.markets/ |
| **Auth0** (Optional) | OAuth2 authentication | https://auth0.com/ |

#### Network Requirements
Ensure the following ports are accessible:

| Port | Service | Protocol | Access |
|------|---------|----------|--------|
| 5432 | PostgreSQL | TCP | Internal only |
| 6379 | Redis | TCP | Internal only (no host mapping by default) |
| 8001 | Signal Service | HTTP | Internal/localhost |
| 8002 | Execution Gateway | HTTP | Internal/localhost |
| 8003 | Orchestrator | HTTP | Internal/localhost |
| 8004 | Market Data Service | HTTP | Internal/localhost |
| 8501 | Web Console (dev) | HTTP | localhost |
| 3000 | Grafana | HTTP | localhost |
| 9090 | Prometheus | HTTP | localhost |
| 3100 | Loki | HTTP | Internal |
| 443 | nginx (mTLS/OAuth2) | HTTPS | External (production) |
| 80 | nginx redirect | HTTP | External (production) |

**Firewall Notes:**
- Database/Redis ports should NEVER be exposed to public internet
- Use Docker internal network for inter-service communication
- Only expose web-facing services (nginx, Grafana) via reverse proxy in production

---

### A.2 Environment Configuration

#### Step 1: Create Environment File
```bash
cp .env.example .env
```

#### Step 2: Configure Required Variables
Edit `.env` with your values:

```ini
# ═══════════════════════════════════════════════════════════════════
# REQUIRED: Alpaca API Credentials
# ═══════════════════════════════════════════════════════════════════
# Get from: https://app.alpaca.markets/paper/dashboard/overview
ALPACA_API_KEY_ID=PK...your_key...
ALPACA_API_SECRET_KEY=...your_secret...
ALPACA_BASE_URL=https://paper-api.alpaca.markets  # Paper trading (recommended)
# ALPACA_BASE_URL=https://api.alpaca.markets      # Live trading (CAUTION!)

# ═══════════════════════════════════════════════════════════════════
# REQUIRED: Database Configuration
# ═══════════════════════════════════════════════════════════════════
DATABASE_URL=postgresql+psycopg://trader:trader@localhost:5432/trader
POSTGRES_USER=trader
POSTGRES_PASSWORD=trader
POSTGRES_DB=trader

# ═══════════════════════════════════════════════════════════════════
# REQUIRED: Redis Configuration
# ═══════════════════════════════════════════════════════════════════
REDIS_URL=redis://localhost:6379/0

# ═══════════════════════════════════════════════════════════════════
# APPLICATION CONFIGURATION
# ═══════════════════════════════════════════════════════════════════
# Strategy
STRATEGY_ID=alpha_baseline

# Safety Mode (IMPORTANT!)
DRY_RUN=true  # true = simulate orders, false = real orders to Alpaca

# Capital & Position Limits
CAPITAL=100000
MAX_POSITION_SIZE=20000

# Data Quality
DATA_FRESHNESS_MINUTES=30
OUTLIER_THRESHOLD=0.30

# ═══════════════════════════════════════════════════════════════════
# SERVICE URLS (Internal)
# ═══════════════════════════════════════════════════════════════════
SIGNAL_SERVICE_URL=http://localhost:8001
EXECUTION_GATEWAY_URL=http://localhost:8002
ORCHESTRATOR_URL=http://localhost:8003

# ═══════════════════════════════════════════════════════════════════
# PAPER RUN DEFAULTS
# ═══════════════════════════════════════════════════════════════════
PAPER_RUN_SYMBOLS=AAPL,MSFT,GOOGL
PAPER_RUN_CAPITAL=100000
PAPER_RUN_MAX_POSITION_SIZE=20000

# ═══════════════════════════════════════════════════════════════════
# MLFLOW / QLIB
# ═══════════════════════════════════════════════════════════════════
QLIB_DATA_DIR=./data/qlib_data
MLFLOW_TRACKING_URI=file:./artifacts/mlruns
MLFLOW_EXPERIMENT_NAME=alpha_baseline

# ═══════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════
LOG_LEVEL=INFO  # DEBUG, INFO, WARNING, ERROR

# ═══════════════════════════════════════════════════════════════════
# WEB CONSOLE (Optional - for development)
# ═══════════════════════════════════════════════════════════════════
WEB_CONSOLE_USER=admin
WEB_CONSOLE_PASSWORD=changeme  # CHANGE THIS!
```

#### Variable Reference (Comprehensive)

**Legend:** 🔐 = Secret (never log/commit), ⚠️ = Critical (wrong value = financial risk), 💰 = Financial impact

| Variable | Required | Valid Values | Security | Notes |
|----------|----------|--------------|----------|-------|
| **Alpaca API** |||||
| `ALPACA_API_KEY_ID` | ✅ | 20+ char string from Alpaca | 🔐 Secret | Never log or commit |
| `ALPACA_API_SECRET_KEY` | ✅ | 40+ char secret from Alpaca | 🔐 Secret | Store in secrets manager |
| `ALPACA_BASE_URL` | ✅ | `https://paper-api.alpaca.markets` (paper) or `https://api.alpaca.markets` (live) | ⚠️ Critical | Wrong URL = real money trades! |
| **Database** |||||
| `DATABASE_URL` | ✅ | `postgresql+psycopg://user:pass@host:port/db` | 🔐 Secret | Prefer TLS, non-superuser |
| `POSTGRES_USER` | ✅ | String | 🔐 Secret | Default: `trader` |
| `POSTGRES_PASSWORD` | ✅ | String | 🔐 Secret | Rotate regularly |
| **Redis** |||||
| `REDIS_URL` | ✅ | `redis://host:port/db_index` | 🔐 Secret | Enable AUTH in production |
| **Trading Safety** |||||
| `DRY_RUN` | ✅ | `true` / `false` | ⚠️ Critical | `true` = no real orders |
| `CAPITAL` | ✅ | Positive integer (USD) | 💰 Financial | Match broker account equity |
| `MAX_POSITION_SIZE` | ✅ | Positive integer (shares) | 💰 Financial | Hard limit per position |
| `DATA_FRESHNESS_MINUTES` | ❌ | Integer (default: 30) | ⚠️ Critical | Too high = stale signals |
| **Strategy** |||||
| `STRATEGY_ID` | ✅ | Slug string (e.g., `alpha_baseline`) | - | Affects order IDs |
| `OUTLIER_THRESHOLD` | ❌ | 0-1 decimal (default: 0.30) | - | Data quality filter |
| **Service URLs** |||||
| `SIGNAL_SERVICE_URL` | ✅ | `http://localhost:8001` | - | Internal service |
| `EXECUTION_GATEWAY_URL` | ✅ | `http://localhost:8002` | - | Controls order path |
| `ORCHESTRATOR_URL` | ✅ | `http://localhost:8003` | - | Workflow controller |
| **OAuth2 (Optional)** |||||
| `AUTH0_DOMAIN` | ❌ | `tenant.us.auth0.com` | - | Required if auth_type=oauth2 |
| `AUTH0_CLIENT_ID` | ❌ | UUID string | - | Non-secret but sensitive |
| `AUTH0_CLIENT_SECRET` | ❌ | Confidential string | 🔐 Secret | Never commit |
| `SESSION_ENCRYPTION_KEY` | ❌ | Base64 32-byte key | 🔐 Secret | Generate: `python3 -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"` |
| **Logging** |||||
| `LOG_LEVEL` | ❌ | `DEBUG`, `INFO`, `WARNING`, `ERROR` | - | Avoid DEBUG in prod (PII risk) |

---

### A.3 Installation & Setup

#### Step 1: Clone Repository
```bash
git clone https://github.com/LeeeWayyy/trading_platform.git
cd trading_platform
```

#### Step 2: Create Virtual Environment
```bash
# Create virtual environment
python3.11 -m venv .venv

# Activate virtual environment (REQUIRED before any Python command)
source .venv/bin/activate

# Verify activation
which python3
# Should output: /path/to/trading_platform/.venv/bin/python3
```

#### Step 3: Install Dependencies
```bash
# Option A: Using Poetry (recommended)
poetry install

# Option B: Using pip
pip install -r requirements.txt
```

#### Step 4: Install Git Hooks
```bash
make install-hooks
```

#### Step 5: Start Infrastructure Services
```bash
# Start PostgreSQL, Redis, Prometheus, Grafana, Loki, Promtail
make up

# Verify all services are running
docker compose ps
```

Expected output:
```
NAME                           STATUS          PORTS
trading_platform_postgres      Up (healthy)    5432/tcp
trading_platform_redis         Up (healthy)    6379/tcp
trading_platform_prometheus    Up              9090/tcp
trading_platform_grafana       Up              3000/tcp
trading_platform_loki          Up (healthy)    3100/tcp
trading_platform_promtail      Up              -
```

#### Step 6: Apply Database Migrations
```bash
# Connect to PostgreSQL and run migrations
docker exec -i trading_platform_postgres psql -U trader -d trader < migrations/001_create_model_registry.sql
docker exec -i trading_platform_postgres psql -U trader -d trader < migrations/002_create_execution_tables.sql
docker exec -i trading_platform_postgres psql -U trader -d trader < migrations/003_create_orchestration_tables.sql
```

#### Step 7: Verify Installation
```bash
# Run tests (should all pass)
make test

# Run full CI suite
make ci-local
```

---

### A.4 Starting the Trading System

#### Service Startup Sequence

**Important:** Services must be started in order due to dependencies.

```bash
# Step 1: Ensure virtual environment is active
source .venv/bin/activate

# Step 2: Verify infrastructure is running
docker compose ps

# Step 3: Start Signal Service (port 8001)
PYTHONPATH=. poetry run uvicorn apps.signal_service.main:app --host 0.0.0.0 --port 8001 &

# Step 4: Start Execution Gateway (port 8002)
PYTHONPATH=. poetry run uvicorn apps.execution_gateway.main:app --host 0.0.0.0 --port 8002 &

# Step 5: Start Orchestrator (port 8003)
PYTHONPATH=. poetry run uvicorn apps.orchestrator.main:app --host 0.0.0.0 --port 8003 &

# Step 6: Start Market Data Service (port 8004) - Optional
make market-data &
```

#### Service Health Verification
```bash
# Check Signal Service
curl http://localhost:8001/health

# Check Execution Gateway
curl http://localhost:8002/health

# Check Orchestrator
curl http://localhost:8003/health
```

Expected response for each:
```json
{"status": "healthy", "service": "...", "timestamp": "..."}
```

#### Starting Web Console (Optional)
```bash
# Development mode (port 8501)
docker compose --profile dev up -d web_console_dev

# Access at: http://localhost:8501
```

---

### A.5 Daily Operations

#### Pre-Market Checklist (Before 9:30 AM ET)
```bash
# 1. Verify infrastructure is healthy
docker compose ps

# 2. Check service health
curl http://localhost:8001/health  # Signal Service
curl http://localhost:8002/health  # Execution Gateway
curl http://localhost:8003/health  # Orchestrator

# 3. Verify data freshness (should be < 30 minutes old)
PYTHONPATH=. python3 -c "
from libs.data_pipeline.freshness import check_data_freshness
result = check_data_freshness()
print(f'Data age: {result.age_minutes} minutes')
print(f'Fresh: {result.is_fresh}')
"

# 4. Check circuit breaker state (should be OPEN)
docker exec trading_platform_redis redis-cli GET circuit_breaker:state

# 5. Check kill switch state (should be ACTIVE)
docker exec trading_platform_redis redis-cli GET kill_switch:state

# 6. Verify Alpaca connection
PYTHONPATH=. python3 -c "
from apps.execution_gateway.alpaca_client import AlpacaExecutor
executor = AlpacaExecutor()
print(f'Account status: {executor.get_account_status()}')
print(f'Buying power: ${executor.get_buying_power()}')
"
```

#### Running Paper Trading
```bash
# Basic run with defaults from .env
PYTHONPATH=. python3 scripts/paper_run.py

# Custom symbols
PYTHONPATH=. python3 scripts/paper_run.py --symbols AAPL MSFT GOOGL AMZN

# Custom capital and position size
PYTHONPATH=. python3 scripts/paper_run.py --capital 50000 --max-position-size 10000

# Dry run (check dependencies without executing)
PYTHONPATH=. python3 scripts/paper_run.py --dry-run

# Save results to JSON
PYTHONPATH=. python3 scripts/paper_run.py --output results_$(date +%Y%m%d).json

# Verbose mode for debugging
PYTHONPATH=. python3 scripts/paper_run.py --verbose
```

#### Checking System Status
```bash
# View current positions, orders, P&L
make status

# View logs from all services
make logs

# View logs from specific service
docker compose logs -f loki
```

---

### A.6 Monitoring & Observability

#### Dashboards

| Dashboard | URL | Purpose |
|-----------|-----|---------|
| **Grafana** | http://localhost:3000 | Metrics, logs, alerts |
| **Prometheus** | http://localhost:9090 | Raw metrics, queries |
| **Loki** | (via Grafana) | Centralized logging |

**Grafana Login:**
- Username: `admin`
- Password: `admin` (or as set in `.env`)

#### Key Metrics to Monitor
| Metric | Location | Alert Threshold |
|--------|----------|-----------------|
| Order latency | Grafana → Trading Dashboard | > 500ms |
| Signal generation time | Grafana → Signal Service | > 5s |
| Open orders count | Grafana → Execution Dashboard | > 10 |
| Circuit breaker state | Redis `circuit_breaker:state` | `TRIPPED` |
| Kill switch state | Redis `kill_switch:state` | `ENGAGED` |

#### Log Queries (Loki via Grafana)
```logql
# All errors in last hour
{job="trading_platform"} |= "ERROR"

# Signal generation logs
{service="signal_service"} |~ "signal"

# Order submissions
{service="execution_gateway"} |~ "order"

# Circuit breaker events
{job="trading_platform"} |~ "circuit_breaker"
```

---

### A.7 Risk Management Operations

#### Circuit Breaker Management
```bash
# Check current state
docker exec trading_platform_redis redis-cli GET circuit_breaker:state

# View trip reason (if tripped)
docker exec trading_platform_redis redis-cli GET circuit_breaker:trip_reason

# Manual reset (CAUTION - verify conditions first!)
docker exec trading_platform_redis redis-cli SET circuit_breaker:state \
  '{"state": "OPEN", "reset_by": "operator", "reset_at": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}'
```

#### Kill Switch Operations
```bash
# Check current state
docker exec trading_platform_redis redis-cli GET kill_switch:state

# ENGAGE Kill Switch (EMERGENCY - stops ALL trading)
PYTHONPATH=. python3 -c "
from libs.redis_client import RedisClient
from libs.risk_management.kill_switch import KillSwitch
redis = RedisClient()
ks = KillSwitch(redis)
ks.engage(reason='Manual engagement - <describe reason>', operator='<your_name>')
print('🔴 KILL SWITCH ENGAGED')
"

# DISENGAGE Kill Switch (resume trading)
PYTHONPATH=. python3 -c "
from libs.redis_client import RedisClient
from libs.risk_management.kill_switch import KillSwitch
redis = RedisClient()
ks = KillSwitch(redis)
ks.disengage(operator='<your_name>', notes='Conditions normalized')
print('✅ Kill switch disengaged')
"

# View kill switch history
docker exec trading_platform_redis redis-cli LRANGE kill_switch:history -10 -1
```

---

### A.8 Model Management

#### Training New Models
```bash
# Train Alpha158 baseline model
PYTHONPATH=. python3 strategies/alpha_baseline/train.py

# Train with custom date range
PYTHONPATH=. python3 strategies/alpha_baseline/train.py \
  --start-date 2023-01-01 \
  --end-date 2024-01-01

# Register model in MLflow
PYTHONPATH=. python3 strategies/alpha_baseline/train.py --register
```

#### Model Registry Operations
```bash
# List registered models
PYTHONPATH=. python3 -c "
import mlflow
mlflow.set_tracking_uri('file:./artifacts/mlruns')
client = mlflow.tracking.MlflowClient()
for rm in client.search_registered_models():
    print(f'{rm.name}: {rm.latest_versions}')
"

# Get active model version
docker exec -i trading_platform_postgres psql -U trader -d trader -c \
  "SELECT strategy_name, version, status, created_at FROM model_registry WHERE status = 'active';"
```

#### Hot Reload Model (Zero Downtime)
```bash
# Trigger model reload via API
curl -X POST http://localhost:8001/api/v1/models/reload \
  -H "Content-Type: application/json" \
  -d '{"model_name": "alpha_baseline", "version": "latest"}'
```

---

### A.9 Emergency Procedures

#### 🚨 EMERGENCY: Stop All Trading
```bash
# Step 1: ENGAGE KILL SWITCH (immediate effect)
PYTHONPATH=. python3 -c "
from libs.redis_client import RedisClient
from libs.risk_management.kill_switch import KillSwitch
redis = RedisClient()
ks = KillSwitch(redis)
ks.engage(reason='EMERGENCY: <describe situation>', operator='<your_name>')
print('🔴 KILL SWITCH ENGAGED - All trading stopped')
"

# Step 2: Cancel all open orders
curl -X POST http://localhost:8002/api/v1/orders/cancel-all

# Step 3: Verify no open orders
curl http://localhost:8002/api/v1/orders?status=open
```

#### 🚨 EMERGENCY: Flatten All Positions
```bash
# Step 1: Engage kill switch first (prevent new orders)
# ... (see above)

# Step 2: Close all positions
curl -X POST http://localhost:8002/api/v1/positions/close-all

# Step 3: Verify positions closed
curl http://localhost:8002/api/v1/positions
```

#### 🚨 EMERGENCY: Full System Shutdown
```bash
# Step 1: Engage kill switch
# ... (see above)

# Step 2: Cancel all orders
curl -X POST http://localhost:8002/api/v1/orders/cancel-all

# Step 3: Stop application services
pkill -f "uvicorn apps"  # Stop all FastAPI services

# Step 4: Stop infrastructure (preserves data)
make down

# Step 5: Stop infrastructure AND remove data (DESTRUCTIVE)
# make down-v  # Only if you want to reset everything
```

---

### A.10 Troubleshooting Guide

#### Service Won't Start
```bash
# Check if port is already in use
lsof -i :8001  # Signal Service
lsof -i :8002  # Execution Gateway
lsof -i :8003  # Orchestrator

# Kill process on port
kill -9 $(lsof -t -i:8001)

# Check for import errors
PYTHONPATH=. python3 -c "from apps.signal_service.main import app"
```

#### Database Connection Failed
```bash
# Check PostgreSQL is running
docker compose ps postgres

# Test connection
docker exec trading_platform_postgres psql -U trader -d trader -c "SELECT 1;"

# Check logs
docker compose logs postgres

# Restart if needed
docker compose restart postgres
```

#### Redis Connection Failed
```bash
# Check Redis is running
docker compose ps redis

# Test connection
docker exec trading_platform_redis redis-cli ping
# Should return: PONG

# Check if state keys exist
docker exec trading_platform_redis redis-cli KEYS "*"

# Restart if needed
docker compose restart redis
```

#### Circuit Breaker Won't Reset
```bash
# Check current state and trip reason
docker exec trading_platform_redis redis-cli GET circuit_breaker:state
docker exec trading_platform_redis redis-cli GET circuit_breaker:trip_reason

# Common causes:
# - DATA_STALE: Run ETL pipeline to refresh data
# - DAILY_LOSS_EXCEEDED: Wait until next day or manually reset
# - BROKER_ERROR: Check Alpaca API status

# Force reset (ONLY if conditions are verified safe)
docker exec trading_platform_redis redis-cli SET circuit_breaker:state \
  '{"state": "OPEN", "reset_by": "operator", "reset_at": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'", "note": "Manual reset after verification"}'
```

#### Alpaca API Errors
```bash
# Test API credentials
PYTHONPATH=. python3 -c "
from apps.execution_gateway.alpaca_client import AlpacaExecutor
executor = AlpacaExecutor()
try:
    account = executor.get_account()
    print(f'Account ID: {account.id}')
    print(f'Status: {account.status}')
    print(f'Buying Power: \${account.buying_power}')
except Exception as e:
    print(f'ERROR: {e}')
"

# Check Alpaca status page: https://status.alpaca.markets/
```

---

### A.11 Backup & Recovery

#### Database Backup
```bash
# Create backup
docker exec trading_platform_postgres pg_dumpall -U trader > backup_$(date +%Y%m%d_%H%M%S).sql

# Restore from backup
docker exec -i trading_platform_postgres psql -U trader < backup_YYYYMMDD_HHMMSS.sql
```

#### Redis State Backup
```bash
# Redis AOF is enabled by default, but you can trigger manual save
docker exec trading_platform_redis redis-cli BGSAVE

# Backup Redis data directory
docker cp trading_platform_redis:/data ./redis_backup_$(date +%Y%m%d)
```

#### Configuration Backup
```bash
# Backup all configuration
tar -czvf config_backup_$(date +%Y%m%d).tar.gz \
  .env \
  docker-compose.yml \
  infra/ \
  migrations/
```

---

### A.12 Quick Reference Commands

| Action | Command |
|--------|---------|
| **Start infrastructure** | `make up` |
| **Stop infrastructure** | `make down` |
| **View logs** | `make logs` |
| **Run tests** | `make test` |
| **Run CI locally** | `make ci-local` |
| **Format code** | `make fmt` |
| **Check linting** | `make lint` |
| **Check status** | `make status` |
| **Run paper trading** | `PYTHONPATH=. python3 scripts/paper_run.py` |
| **Start Signal Service** | `PYTHONPATH=. poetry run uvicorn apps.signal_service.main:app --port 8001` |
| **Start Execution Gateway** | `PYTHONPATH=. poetry run uvicorn apps.execution_gateway.main:app --port 8002` |
| **Start Orchestrator** | `PYTHONPATH=. poetry run uvicorn apps.orchestrator.main:app --port 8003` |
| **Start Market Data** | `make market-data` |

---

**Last Updated:** 2025-12-01
**Author:** Claude Code
**Version:** 5.2 (Added T5.5/T5.6 from Reviewer #2, enhanced runbook with network requirements and detailed env var table)
