---
id: P3T2
title: "Critical Fixes (P0) - Security, Trading Safety, Data Integrity"
phase: P3
task: T2
priority: P0
owner: "@development-team"
state: TASK
created: 2025-11-30
dependencies: ["P3T1"]
estimated_effort: "2-3 days"
related_adrs: []
related_docs: ["P3_PLANNING.md", "P3_ISSUES.md"]
features: ["T2.1-Security", "T2.2-TradingSafety", "T2.3-DataIntegrity", "T2.4-APIContracts"]
---

# P3T2: Critical Fixes (P0) - Security, Trading Safety, Data Integrity

**Phase:** P3 (Review, Remediation & Modernization)
**Status:** TASK (Not Started)
**Priority:** P0 (Must Fix Before Production)
**Owner:** @development-team
**Created:** 2025-11-30
**Estimated Effort:** 2-3 days

---

## Objective

Fix 8 critical issues identified by triple-reviewer analysis (Claude, Gemini, Codex) that MUST be resolved before any production deployment.

**Success looks like:**
- All 8 P0 critical issues resolved
- Comprehensive test coverage for each fix
- No regression in existing functionality
- CI passes with all new tests

---

## Issues to Address

| ID | Issue | Location | Reviewer |
|----|-------|----------|----------|
| C1 | P&L calculation verification | `execution_gateway/database.py:889` | Claude |
| C2 | Hardcoded $100 fallback price | `orchestrator/orchestrator.py:500` | Gemini |
| C3 | Webhook secret fail-open | `execution_gateway/main.py:2162` | All 3 |
| C4 | Idempotency race condition | `execution_gateway/main.py:1088` | Codex |
| C5 | Timezone bugs (naive datetime) | `execution_gateway/main.py:351+` | Claude |
| C6 | CORS wildcard | `signal_service/main.py:393` | Claude+Gemini |
| C7 | Missing circuit breaker check | `execution_gateway/main.py:922` | Codex |
| C8 | COALESCE bug | `execution_gateway/database.py:769` | Claude |

---

## Acceptance Criteria

- [ ] **AC1:** C3 - Webhook secret is mandatory in non-dev environments
- [ ] **AC2:** C6 - CORS uses environment-based allowlist, not wildcard
- [ ] **AC3:** C2 - No hardcoded price fallback; raises error when price unavailable
- [ ] **AC4:** C7 - Circuit breaker checked before order submission
- [ ] **AC5:** C1 - P&L calculation has comprehensive test coverage
- [ ] **AC6:** C4 - Idempotency race handled gracefully (no 500 errors)
- [ ] **AC8:** C8 - Error messages can be cleared properly
- [ ] **AC7:** C5 - All datetime.now() calls use UTC timezone
- [ ] **AC9:** All new tests pass
- [ ] **AC10:** `make ci-local` passes

---

## Approach

### Logical Components (6-step pattern each)

**Component 1: T2.1-Security (C3, C6)**
- C3: Make WEBHOOK_SECRET mandatory in non-dev environments
- C6: Replace CORS wildcard with environment-based allowlist
- Tests: `test_webhook_secret_required.py`, `test_cors_allowlist.py`

**Component 2: T2.2-TradingSafety (C2, C7)**
- C2: Remove $100 default price, add PriceUnavailableError
- C7: Add circuit breaker and risk validation before order submission
- Tests: `test_price_unavailable_error.py`, `test_circuit_breaker_blocks_order.py`

**Component 3: T2.3-DataIntegrity (C1, C4, C8)**
- C1: Add comprehensive P&L calculation tests
- C4: Handle UniqueViolation race condition gracefully
- C8: Fix COALESCE to allow clearing error messages
- Tests: `test_pnl_*.py`, `test_idempotency_race.py`, `test_error_message_clear.py`

**Component 4: T2.4-APIContracts (C5)**
- C5: Replace all `datetime.now()` with `datetime.now(UTC)`
- Tests: `test_api_timestamps_utc.py`

---

## Technical Details

### Files to Modify

**T2.1 Security:**
- `apps/execution_gateway/main.py:2162` - Webhook secret validation
- `apps/signal_service/main.py:393` - CORS configuration

**T2.2 Trading Safety:**
- `apps/orchestrator/orchestrator.py:500` - Remove hardcoded price
- `apps/execution_gateway/main.py:922` - Add circuit breaker check

**T2.3 Data Integrity:**
- `apps/execution_gateway/database.py:889` - P&L tests (no code change needed)
- `apps/execution_gateway/main.py:1088` - Handle UniqueViolation
- `apps/execution_gateway/database.py:769` - Fix COALESCE

**T2.4 API Contracts:**
- Multiple files in `apps/` - datetime.now() → datetime.now(UTC)

### New Test Files
- `tests/apps/execution_gateway/test_c3_webhook_secret.py`
- `tests/apps/signal_service/test_c6_cors_allowlist.py`
- `tests/apps/orchestrator/test_c2_price_unavailable.py`
- `tests/apps/execution_gateway/test_c7_circuit_breaker_order.py`
- `tests/apps/execution_gateway/test_c1_pnl_calculation.py`
- `tests/apps/execution_gateway/test_c4_idempotency_race.py`
- `tests/apps/execution_gateway/test_c8_error_message.py`
- `tests/apps/execution_gateway/test_c5_timestamps_utc.py`

---

## Dependencies

**Blockers (must complete before starting):**
- P3T1: Workflow Modernization ✅ (Complete)

**Blocks (other tasks waiting on this):**
- P3T3: High Priority Fixes (P1)
- Production deployment

---

## Risks & Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| Regression in order flow | High | Medium | Comprehensive test coverage |
| Breaking change to API | Medium | Low | Keep backwards compatible |
| Performance impact from checks | Low | Low | Profile critical paths |

---

## Testing Strategy

### Test Coverage Needed
- **Unit tests:** Each fix isolated
- **Integration tests:** Order flow with circuit breaker, webhook verification
- **Regression tests:** Existing order/position functionality

### Manual Testing
- [ ] Verify webhook rejection without secret in prod mode
- [ ] Verify CORS blocks unauthorized origins
- [ ] Verify orders blocked when circuit breaker tripped
- [ ] Verify P&L calculations for long/short/partial positions

---

## Related

**Planning:**
- [P3_PLANNING.md](./P3_PLANNING.md) - Phase 3 planning
- [P3_ISSUES.md](./P3_ISSUES.md) - Full issue list

**Tasks:**
- Depends on: [P3T1](./P3T1_TASK.md) ✅
- Blocks: P3T3 (High Priority Fixes)

---

## Notes

This task addresses all 8 P0 (Critical) issues that must be fixed before any production deployment. The issues were identified through triple-reviewer analysis using Claude, Gemini, and Codex.

---
