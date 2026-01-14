---
id: P6T3
title: "Professional Trading Terminal - Notifications & Hotkeys"
phase: P6
task: T3
priority: P0
owner: "@development-team"
state: TASK
created: 2026-01-13
dependencies: [P6T1]
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P6_PLANNING.md]
features: [T3.1-T3.4]
---

# P6T3: Notifications & Hotkeys

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** TASK (Not Started)
**Priority:** P0 (UX Foundation)
**Owner:** @development-team
**Created:** 2026-01-13
**Track:** Track 3 of 18
**Dependency:** P6T1 (Core Infrastructure) must be complete

---

## Objective

Implement notification management with quiet mode and keyboard hotkeys for professional trading workflows.

**Success looks like:**
- Notification center prevents toast spam during algo execution
- Keyboard hotkeys enable rapid trading (B/S/Enter/Escape)
- State feedback on all trading buttons
- Cell flash updates for price changes

---

## Tasks (4 total)

### T3.1: Notification Center / Quiet Mode - HIGH PRIORITY

**Goal:** Prevent toast notification spam during algo execution.

**Notification Priority Routing:**
- HIGH: ui.notify toast (Risk Reject, Circuit Breaker, Errors)
- MEDIUM: badge + log (Order Filled, Position Changed)
- LOW: log only (Slice Filled, Heartbeat, Data Updates)

**Acceptance Criteria:**
- [ ] Log drawer accessible from header (right side, togglable)
- [ ] Low-priority events don't spam toasts
- [ ] High-priority events still show toasts even in quiet mode
- [ ] Badge shows unread count
- [ ] Quiet mode toggle in header
- [ ] Notification history persists during session

**Files:**
- Create: `apps/web_console_ng/components/log_drawer.py`, `apps/web_console_ng/core/notification_router.py`
- Modify: `apps/web_console_ng/ui/layout.py`

---

### T3.2: Keyboard Hotkeys - HIGH PRIORITY

**Goal:** Enable keyboard-driven trading for rapid execution.

**Hotkey Bindings:**
- `b`: Focus buy quantity
- `s`: Focus sell quantity
- `Escape`: Cancel form
- `Enter`: Submit order
- `/`: Open command palette
- `F1`: Help/hotkey reference

**Acceptance Criteria:**
- [ ] B/S keys focus buy/sell inputs
- [ ] Enter submits order form
- [ ] Escape cancels/clears forms
- [ ] Command palette opens with / or Ctrl+K
- [ ] Hotkey hints visible on hover
- [ ] Configurable hotkey preferences
- [ ] No conflicts with browser/OS shortcuts

**Files:**
- Create: `apps/web_console_ng/core/hotkey_manager.py`, `apps/web_console_ng/components/command_palette.py`

---

### T3.3: State Feedback Loops - HIGH PRIORITY

**Goal:** Immediate visual feedback on all user actions.

**Button States:**
- Default → Sending... → Confirming... → Filled/Failed

**Acceptance Criteria:**
- [ ] All buttons show immediate feedback on click (<50ms)
- [ ] Loading state visible (spinner + "Sending...")
- [ ] Success: green flash + checkmark (2s then reset)
- [ ] Failure: red flash + X mark (keep visible until dismissed)
- [ ] Timeout handling (>5s = "Taking longer than expected...")

**Files:**
- Create: `apps/web_console_ng/components/action_button.py`, `apps/web_console_ng/components/loading_states.py`

---

### T3.4: Cell Flash Updates - MEDIUM PRIORITY

**Goal:** Visual attention on price/P&L changes via cell flashing.

**Dependency:** Requires P6T1.1 (Throttling) to be complete.

**Acceptance Criteria:**
- [ ] Price changes flash green (up) or red (down)
- [ ] P&L columns flash on update
- [ ] Flash duration configurable (default 500ms)
- [ ] No performance degradation with frequent updates
- [ ] Flash disabled automatically when update rate exceeds threshold (backpressure from throttling)

**Files:**
- Modify: `apps/web_console_ng/components/positions_grid.py`, `apps/web_console_ng/components/orders_table.py`

---

## Dependencies

```
P6T1.1 Throttling ──> T3.4 Cell Flash (performance)

T3.1 Notifications ──> Used by all order actions
T3.2 Hotkeys ──> Order entry (P6T4), Order actions (P6T7)
T3.3 Feedback ──> All buttons in P6T4-P6T7
```

---

## Testing Strategy

### Unit Tests
- Notification routing logic
- Hotkey bindings
- Flash animation timing

### Integration Tests
- Notification priority filtering
- Hotkey focus management

### E2E Tests
- Hotkey workflow (B → enter qty → Enter)
- Quiet mode toggle behavior

---

## Definition of Done

- [ ] All 4 tasks implemented
- [ ] Notification center with quiet mode
- [ ] Hotkeys functional (B/S/Enter/Escape)
- [ ] State feedback on all buttons
- [ ] Cell flash updates working
- [ ] Unit tests > 85% coverage
- [ ] E2E tests pass
- [ ] Code reviewed and approved

---

**Last Updated:** 2026-01-13
**Status:** TASK
