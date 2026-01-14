---
id: P6T7
title: "Professional Trading Terminal - Order Actions"
phase: P6
task: T7
priority: P0
owner: "@development-team"
state: TASK
created: 2026-01-13
dependencies: [P6T2, P6T6]
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P6_PLANNING.md]
features: [T7.1-T7.4]
---

# P6T7: Order Actions

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** TASK (Not Started)
**Priority:** P0 (Core Trading)
**Owner:** @development-team
**Created:** 2026-01-13
**Track:** Track 7 of 18
**Dependency:** P6T2 (kill switch gating), P6T6 (fat finger validation)

---

## Objective

Implement critical order actions: flatten controls, cancel all, order replay, and one-click trading with comprehensive safety controls.

**Success looks like:**
- Quick emergency flatten from dashboard
- One-click cancel all with filtering
- One-click trading for rapid execution (with safety controls)
- Order replay for quick re-entry

---

## Tasks (4 total)

### T7.1: Flatten Strategy/Symbol Button - HIGH PRIORITY

**Goal:** Quick emergency flatten from dashboard.

**Prerequisite:** P6T2.4 (Kill Switch) must be complete - flatten is a safety-critical operation.

**Actions:**
- Per-position: [Close] [Flatten Symbol] [Reverse]
- Header: [FLATTEN ALL] [CANCEL ALL ORDERS]

**Acceptance Criteria:**
- [ ] Flatten buttons on position rows
- [ ] Reverse button (close + open opposite in one action)
- [ ] Header flatten all with confirmation dialog
- [ ] Two-step confirmation (type "FLATTEN" to confirm)
- [ ] Audit log entry for each flatten action
- [ ] **Gated by:** Kill switch state (block if already halted)
- [ ] **Gated by:** Connection state (block if disconnected)
- [ ] Respects fat finger limits (warn if flatten exceeds limits)

**Files:**
- Create: `apps/web_console_ng/components/flatten_controls.py`, `apps/web_console_ng/components/emergency_actions.py`

---

### T7.2: Cancel All Orders Button - MEDIUM PRIORITY

**Goal:** One-click cancel all with filtering.

**Acceptance Criteria:**
- [ ] "Cancel All" button in order blotter header
- [ ] Filter options (by symbol, by side, by strategy)
- [ ] Confirmation dialog showing order count
- [ ] Execute bulk cancel via backend
- [ ] Audit log entry

---

### T7.3: Order Replay/Duplicate - LOW PRIORITY

**Goal:** Quick replay of previous orders.

**Acceptance Criteria:**
- [ ] "Replay" button on filled/cancelled orders
- [ ] Pre-fill order form with previous values
- [ ] Generate new client_order_id (never reuse)
- [ ] Still subject to fat finger validation

---

### T7.4: One-Click Trading (Shift+Click) - HIGH PRIORITY

**Goal:** Enable instant order placement without confirmation dialogs.

**Prerequisite:** P6T2.4 (Kill Switch) must be complete - one-click is a safety-critical feature.

**One-Click Modes:**
- Normal Click: Opens order form pre-filled
- Shift+Click: Instant limit order at clicked price
- Ctrl+Click: Instant market order
- Alt+Click: Cancel order at level

**Acceptance Criteria:**
- [ ] Shift+Click places limit order instantly
- [ ] Ctrl+Click places market order instantly
- [ ] **Feature disabled by default** (opt-in in settings)
- [ ] Brief confirmation toast shown (not dialog)
- [ ] Works on both DOM ladder and Chart
- [ ] **Gated by:** Kill switch state (block if halted)
- [ ] **Gated by:** Connection state (block if disconnected)
- [ ] **Safety: Fat finger thresholds still apply** (block if exceeded)
- [ ] **Safety: Daily notional cap** (configurable, default $500k)
- [ ] **Safety: Cooldown** (prevent accidental double-click, 500ms)
- [ ] **Safety: First-use confirmation** per session (explain risks)
- [ ] **Role-gated:** Require TRADER role or higher
- [ ] **Audit trail:** Log all one-click orders with mode used

**Files:**
- Create: `apps/web_console_ng/components/one_click_handler.py`
- Modify: `apps/web_console_ng/components/dom_ladder.py`, `apps/web_console_ng/components/price_chart.py`

---

## Dependencies

```
P6T2.3 Connection ──> All order actions (gating)
P6T2.4 Kill Switch ──> T7.1 Flatten, T7.4 One-Click (gating)
P6T6.3 Fat Finger ──> T7.1 Flatten, T7.3 Replay, T7.4 One-Click (validation)
```

---

## Testing Strategy

### Unit Tests
- Flatten order generation
- One-click cooldown timing
- Cancel filter logic

### Integration Tests
- Flatten all with confirmation
- One-click with fat finger rejection

### E2E Tests
- Flatten workflow
- One-click trading workflow (opt-in, execute, verify)

---

## Definition of Done

- [ ] All 4 tasks implemented
- [ ] Flatten controls functional with safety gates
- [ ] Cancel all with filtering working
- [ ] One-click trading (opt-in, role-gated, with safety controls)
- [ ] All actions respect kill switch state
- [ ] All actions respect connection state
- [ ] Unit tests > 85% coverage
- [ ] E2E tests pass
- [ ] Code reviewed and approved

---

**Last Updated:** 2026-01-13
**Status:** TASK
