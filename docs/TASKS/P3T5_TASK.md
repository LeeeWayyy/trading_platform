# P3T5: External Review Findings - Risk Management Fixes

**Phase:** P3 (Issue Remediation)
**Track:** 5 (External Review Findings)
**Status:** 🔄 In Progress
**Priority:** P0 (Critical) / P1 (High)
**Effort Estimate:** 16-24 hours
**Source:** 2 External AI Reviewers (2025-12-01)

---

## Executive Summary

This task addresses 6 critical and high-priority issues identified by external AI code reviewers. These issues were verified against the actual codebase and represent genuine vulnerabilities in the trading platform's risk management, data integrity, and order idempotency systems.

**Critical (P0) Issues:** 4
- T5.1: Kill Switch Bypass in RiskChecker
- T5.2: Position Limit Race Condition
- T5.3: Circuit Breaker Fail-Open
- T5.5: Order ID Collision (missing order_type/TIF)

**High (P1) Issues:** 2
- T5.4: Order ID Timezone Bug
- T5.6: Data Freshness Partial Staleness

---

## Plan Review Feedback (2025-12-01)

### Gemini Review Summary
- **Status:** APPROVED
- Priority order confirmed correct (Kill Switch → Circuit Breaker → Position Limits)
- **Use Redis Lua** (not DB transactions) for position reservation - lower latency
- ~~Add version=2 parameter for order ID hash~~ (not needed - system not yet in production)
- **Add performance regression testing** for Redis reservation latency (<5ms target)

### Codex Review Summary
- **Status:** APPROVED with additions
- Enumerate ALL RiskChecker call sites (done: only in tests, not yet in apps)
- **Specify Redis key schema, TTL, rollback contract** for position reservation
- Add fail-closed breaker with **operator runbook** update
- ~~Versioned client_order_id~~ (not needed - system not yet in production)
- **Consider ADR** for PositionReservation component if deemed architectural

### User Clarification
- **No backwards compatibility needed** - system has not been run yet
- Can implement new versions directly without migration/versioning

---

## Issues Overview

### T5.1: Kill Switch Bypass Risk ⚠️ CRITICAL
**Effort:** 2-4 hours | **Priority:** P0 | **Status:** ✅ IMPLEMENTED

**Problem:** `RiskChecker` class checks CircuitBreaker and Blacklists but does NOT check KillSwitch. If developers rely solely on `RiskChecker.validate_order()` as the "single source of truth" for trade safety, the Kill Switch will be effectively bypassed.

**Location:** `libs/risk_management/checker.py`
- Line 34: Only imports CircuitBreaker, not KillSwitch
- Lines 68-84: Constructor only accepts CircuitBreaker
- Lines 146-152: Only checks `self.breaker.is_tripped()`, no kill switch check

**Fix:** Inject KillSwitch into RiskChecker and check `is_engaged()` as the FIRST step in `validate_order()`.

**Implementation Notes:**
- Added `KillSwitch` import
- Added optional `kill_switch` parameter to `__init__` (backwards compatible)
- Kill switch check is step 0 (before circuit breaker)
- Uses `logger.critical` for kill switch blocks (highest severity)

---

### T5.2: Position Limit Race Condition ⚠️ CRITICAL
**Effort:** 6-8 hours | **Priority:** P0 | **Status:** ✅ IMPLEMENTED

**Problem:** `RiskChecker.validate_order` is stateless and suffers from a Check-Then-Act race condition. Two concurrent order requests can both pass the position limit check before either order executes, resulting in the combined position exceeding limits.

**Location:** `libs/risk_management/checker.py`
- Lines 91-92: `current_position` passed as argument
- Lines 161-175: Stateless check with no atomic locking

**Fix:** Use Redis Lua scripts to atomically check-and-reserve position limits before order execution.

**Redis Design (from reviewer feedback):**
```
Key Schema:
  position_reservation:{symbol} → current reserved position (integer)

TTL:
  60 seconds (matches order submission timeout)
  Auto-cleanup for failed/abandoned reservations

Lua Script Contract:
  KEYS[1] = position_reservation:{symbol}
  ARGV[1] = delta (positive for buy, negative for sell)
  ARGV[2] = max_position_limit
  ARGV[3] = reservation_token (for rollback)

  Returns: {1, token} on success, {0, "LIMIT_EXCEEDED"} on failure

Rollback Hook:
  On broker rejection or timeout, call release_reservation(symbol, token)
  Decrements reserved position atomically
```

---

### T5.3: Circuit Breaker Fail-Open Risk ⚠️ CRITICAL
**Effort:** 2-4 hours | **Priority:** P0 | **Status:** ✅ IMPLEMENTED

**Problem:** CircuitBreaker defaults to OPEN if Redis state is missing (e.g., after Redis crash/flush). KillSwitch correctly fails closed. This inconsistency means a Redis restart could silently allow trading to resume during a risk event.

**Location:** `libs/risk_management/breaker.py`
- Lines 159-163: `get_state()` calls `_initialize_state()` which defaults to OPEN
- Compare with `kill_switch.py:155-166` which correctly raises RuntimeError

**Fix:** Modify `CircuitBreaker.get_state()` to raise an error if Redis state is missing (fail-closed pattern matching KillSwitch).

**Operator Runbook Update Required:**
- Add recovery procedure for "Circuit breaker state missing" error
- Document manual verification steps before re-initialization
- Add Grafana alert for circuit breaker state anomalies

---

### T5.4: Order ID Timezone Bug
**Effort:** 1-2 hours | **Priority:** P1 | **Status:** ✅ IMPLEMENTED

**Problem:** `order_id_generator.py` uses `date.today()` which uses system local time, not UTC. Servers in different timezones or around midnight transitions could generate different client_order_ids for the same logical order.

**Location:** `apps/execution_gateway/order_id_generator.py:115`
```python
order_date = as_of_date or date.today()  # Uses local time!
```

**Fix:** Use `datetime.now(UTC).date()` instead.

---

### T5.5: Order ID Collision - Missing Order Type/TIF ⚠️ CRITICAL
**Effort:** 2-3 hours | **Priority:** P0 | **Status:** ✅ IMPLEMENTED

**Problem:** `generate_client_order_id()` omits `order_type` and `time_in_force` fields from the hash. Two orders with identical parameters but different order semantics (e.g., DAY vs GTC) will get the same client_order_id.

**Location:** `apps/execution_gateway/order_id_generator.py:107-129`
- Lines 107-111: Comment explicitly states order_type/time_in_force NOT included
- Lines 122-129: Hash only includes: symbol, side, qty, limit_price, stop_price, strategy_id, date

**Fix:** Include `order_type` and `time_in_force` in the hash computation. Update the comment to reflect the new hash fields.

---

### T5.6: Data Freshness Partial Staleness Risk
**Effort:** 3-4 hours | **Priority:** P1 | **Status:** ✅ IMPLEMENTED

**Problem:** `check_freshness()` only inspects the latest timestamp (`max()`), allowing mixed fresh/stale datasets to pass validation. If 999 rows are 2 hours old but 1 row is current, the check passes.

**Location:** `libs/data_pipeline/freshness.py:70`
```python
latest_timestamp = df["timestamp"].max()  # Only checks max!
```

**Fix:** Add check modes: "oldest", "median", "per_symbol" to catch partially stale batches.

**Per-Symbol Mode Requirements:**
- Requires `symbol` column in DataFrame
- Default threshold: 90% of symbols must be fresh
- Configurable via `min_fresh_pct` parameter

---

## Implementation Plan

### Component 1: Kill Switch Integration (T5.1) ✅ DONE
**Files:**
- `libs/risk_management/checker.py`

**Changes:**
1. ✅ Add KillSwitch import
2. ✅ Add optional `kill_switch` parameter to `__init__`
3. ✅ Add kill switch check as FIRST step (step 0) in `validate_order()`
4. N/A - No call sites in apps yet (only tests)

**Tests Required:**
- `test_kill_switch_blocks_order.py`
- `test_kill_switch_allows_order.py`
- `test_kill_switch_none_allowed.py` (backwards compat - None kill_switch)

---

### Component 2: Position Limit Atomicity (T5.2)
**Files:**
- `libs/risk_management/checker.py`
- `libs/risk_management/position_reservation.py` (new)

**Changes:**
1. Create `PositionReservation` class with Redis Lua script
2. Implement key schema: `position_reservation:{symbol}`
3. Implement TTL: 60 seconds
4. Implement rollback hook for failed orders
5. Add atomic position check to validate_order flow

**Tests Required:**
- `test_position_race_condition.py` - Simulate concurrent submissions
- `test_atomic_reservation.py` - Verify Lua script atomicity
- `test_reservation_rollback.py` - Verify cleanup on failure
- `test_reservation_ttl_expiry.py` - Verify auto-cleanup

**Performance Benchmark:**
- Target: <5ms for reserve + validate operation
- Test with 100 concurrent requests

---

### Component 3: Circuit Breaker Fail-Closed (T5.3)
**Files:**
- `libs/risk_management/breaker.py`
- `docs/RUNBOOKS/ops.md` (update)

**Changes:**
1. Modify `get_state()` to raise RuntimeError on missing state
2. Match KillSwitch fail-closed pattern
3. Add operator recovery procedure to runbook

**Tests Required:**
- `test_breaker_missing_state_fails_closed.py`
- `test_breaker_redis_restart.py`
- `test_breaker_manual_recovery.py`

---

### Component 4: Order ID Fixes (T5.4 + T5.5)
**Files:**
- `apps/execution_gateway/order_id_generator.py`

**Changes:**
1. Replace `date.today()` with `datetime.now(UTC).date()`
2. Add `order_type` and `time_in_force` to hash computation
3. Update comment documenting hash fields

**Tests Required:**
- `test_order_id_utc.py`
- `test_order_id_different_order_type.py`
- `test_order_id_different_tif.py`

---

### Component 5: Data Freshness Enhancement (T5.6)
**Files:**
- `libs/data_pipeline/freshness.py`

**Changes:**
1. Add `check_mode` parameter: "latest", "oldest", "median", "per_symbol"
2. Add `min_fresh_pct` parameter for per-symbol mode (default: 0.9)
3. Default mode remains "latest" for existing callers

**Tests Required:**
- `test_freshness_mixed_stale.py`
- `test_freshness_per_symbol.py`
- `test_freshness_oldest_mode.py`
- `test_freshness_default_mode.py`

---

## Test Strategy

### Unit Tests
Each component requires isolated unit tests verifying:
1. Correct behavior under normal conditions
2. Correct behavior under edge cases
3. Error handling for invalid inputs

### Integration Tests
1. Full risk check flow with all components
2. Concurrent order submission race condition test
3. Redis failure/recovery scenarios

### Performance Regression Tests (NEW - from reviewer feedback)
1. Benchmark Redis reservation latency (<5ms target)
2. Benchmark under load (100 concurrent requests)
3. Compare before/after for validate_order() latency

---

## Acceptance Criteria

- [ ] All 6 issues (T5.1-T5.6) resolved
- [ ] All unit tests pass (new tests added)
- [ ] All integration tests pass
- [ ] Performance benchmarks pass (<5ms reservation)
- [ ] No regression in existing tests
- [ ] CI passes (`make ci-local`)
- [ ] Code review approved (Gemini + Codex)
- [ ] Type checking passes (`mypy --strict`)
- [ ] Operator runbook updated for circuit breaker recovery

---

## Dependencies

- **Prerequisite:** None (can start immediately)
- **Blocks:** Production deployment

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Breaking existing risk checks | Medium | High | Comprehensive test coverage |
| Redis Lua script errors | Low | High | Thorough testing, rollback mechanism |
| Performance impact from atomic checks | Medium | Medium | Benchmark before/after, <5ms target |
| Fail-closed may halt trading after Redis loss | Medium | Medium | Operator runbook, noisy logging |

---

## Architectural Decision

**Consider ADR:** PositionReservation introduces a new Redis-based component for atomic position management. If this is deemed an architectural change, create ADR documenting:
- Why Redis Lua scripts over database transactions
- Key schema and TTL design
- Rollback contract
- Performance requirements

---

## References

- [P3_PLANNING.md](./P3_PLANNING.md) - Track 5 details
- [P3_ISSUES.md](./P3_ISSUES.md) - Full issue list
- External Reviewer Reports (2025-12-01)
- Gemini continuation_id: 568e458a-6714-46fd-9129-91605e656953
- Codex continuation_id: 08270114-1174-4b57-96c0-d86466d3640a

---

**Created:** 2025-12-01
**Author:** Claude Code
**Version:** 2.0 (Updated with reviewer feedback)
