---
id: P6T6
title: "Professional Trading Terminal - Advanced Orders"
phase: P6
task: T6
priority: P0
owner: "@development-team"
state: TASK
created: 2026-01-13
dependencies: [P6T1, P6T2, P6T4]
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P6_PLANNING.md]
features: [T6.1-T6.4]
---

# P6T6: Advanced Orders

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** TASK (Not Started)
**Priority:** P0 (Core Trading)
**Owner:** @development-team
**Created:** 2026-01-13
**Track:** Track 6 of 18
**Dependency:** P6T1 (throttling), P6T2 (connection/kill switch gating), P6T4 (order ticket)

---

## Objective

Expose hidden backend execution capabilities: stop orders, TWAP algorithmic execution, fat finger validation, and order modification.

**Success looks like:**
- Stop/Stop-Limit orders accessible from UI
- TWAP algorithmic execution configurable
- Fat finger validation pre-submission
- Order modification without cancel/replace

---

## Tasks (4 total)

### T6.1: Advanced Order Types - HIGH PRIORITY

**Goal:** Expose stop and stop-limit order types from backend.

**Current State:**
- Order form only shows `["market", "limit"]`
- Backend supports `stop`, `stop_limit` (per `OrderRequest` schema)

**Acceptance Criteria:**
- [ ] Stop and stop-limit orders can be placed
- [ ] Stop price field appears for stop order types
- [ ] Validation prevents invalid stop price (buy stop > current, sell stop < current)
- [ ] Order blotter shows stop/trigger prices
- [ ] Respects kill switch state (P6T2.4) and connection state (P6T2.3)

**Files:**
- Modify: `apps/web_console_ng/pages/manual_order.py`, `apps/web_console_ng/components/order_form.py`

---

### T6.2: TWAP Execution Controls - HIGH PRIORITY

**Goal:** Enable algorithmic execution from the UI.

**Current State:**
- Backend has `TWAPSlicer` in `order_slicer.py`
- No UI to configure or trigger TWAP

**UI Layout:**
```
Execution Style: [Instant] [TWAP]

When TWAP selected:
- Duration: [30] minutes (min: 5, max: 480)
- Interval: [60] seconds (min: 30, max: 300)
- Preview: 30 slices of 33 shares
- Est. Impact: 2-5 bps
```

**Acceptance Criteria:**
- [ ] TWAP option visible in order form
- [ ] Duration and interval configurable with min/max bounds
- [ ] Preview shows expected slice count and size
- [ ] Submission creates parent + child orders
- [ ] Progress visible in hierarchical blotter (P6T5.1)
- [ ] **Validation:** Duration/interval combinations must result in ≥2 slices
- [ ] **Validation:** Minimum slice size enforced (e.g., 10 shares or $500)
- [ ] **Partial fills:** Handle gracefully, update parent progress
- [ ] **Cancel behavior:** Cancel parent cancels all pending children
- [ ] **Idempotency:** Parent/child orders have idempotent client_order_ids

**Files:**
- Create: `apps/web_console_ng/components/twap_config.py`, `apps/web_console_ng/components/execution_style_selector.py`
- Modify: `apps/web_console_ng/pages/manual_order.py`

---

### T6.3: Fat Finger Pre-Validation - HIGH PRIORITY

**Goal:** Show risk warnings before order submission.

**Current State:**
- Backend `FatFingerValidator` rejects after submission
- No pre-submission warning to user

**Acceptance Criteria:**
- [ ] Thresholds fetched from backend on page load
- [ ] Real-time validation as user types
- [ ] Clear error messages for each breach type (qty, notional, concentration)
- [ ] Submit blocked for invalid orders
- [ ] Admin override capability (with audit log entry)
- [ ] Show remaining capacity (e.g., "Max: 5,000 more shares")

**Files:**
- Create: `apps/web_console_ng/components/fat_finger_validator.py`
- Modify: `apps/web_console_ng/pages/manual_order.py`

---

### T6.4: Order Modification - HIGH PRIORITY

**Goal:** Modify working orders without cancel/replace workflow.

**Acceptance Criteria:**
- [ ] "Modify" button on working orders
- [ ] Modification dialog with current values pre-filled
- [ ] Submit order modification to backend
- [ ] Track modification history in order details
- [ ] Respects kill switch and connection state

**Files:**
- Create: `apps/web_console_ng/components/order_modify_dialog.py`

---

## Dependencies

```
P6T2.3 Connection ──> All order actions (gating)
P6T2.4 Kill Switch ──> All order actions (gating)
P6T4.1 Order Ticket ──> T6.1-T6.3 (order form integration)
P6T5.1 Hierarchical Blotter ──> T6.2 TWAP (progress display)

T6.3 Fat Finger ──> P6T7 (flatten, one-click need validation)
```

---

## Backend Integration Points

| Feature | Backend Location | Frontend Action |
|---------|-----------------|-----------------|
| Stop Orders | `apps/execution_gateway/schemas.py` | Add to order type dropdown |
| TWAP | `apps/execution_gateway/order_slicer.py` | Create config panel |
| Fat Finger | `libs/risk/fat_finger_validator.py` | Pre-validate in form |

---

## Testing Strategy

### Unit Tests
- Order type validation
- TWAP slice calculation (edge cases: min slices, partial fills)
- Fat finger threshold checks

### Integration Tests
- TWAP order submission flow (parent + children created)
- Order modification round-trip

### E2E Tests
- Full TWAP order lifecycle
- Stop order placement and trigger

---

## Definition of Done

- [ ] All 4 tasks implemented
- [ ] Advanced order types accessible (stop, stop-limit)
- [ ] TWAP configurable and functional
- [ ] Fat finger validation working (pre-submission)
- [ ] Order modification working
- [ ] Unit tests > 85% coverage
- [ ] E2E tests pass
- [ ] Code reviewed and approved

---

**Last Updated:** 2026-01-13
**Status:** TASK
