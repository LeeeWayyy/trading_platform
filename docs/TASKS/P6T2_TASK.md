---
id: P6T2
title: "Professional Trading Terminal - Header & Status Bar"
phase: P6
task: T2
priority: P0
owner: "@development-team"
state: TASK
created: 2026-01-13
dependencies: [P6T1]
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P6_PLANNING.md]
features: [T2.1-T2.5]
---

# P6T2: Header & Status Bar

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** TASK (Not Started)
**Priority:** P0 (Core Trading Infrastructure)
**Owner:** @development-team
**Created:** 2026-01-13
**Track:** Track 2 of 18
**Dependency:** P6T1 (Core Infrastructure) must be complete

---

## Objective

Build the always-visible header with critical trading information: NLV, leverage, connection status, latency indicator, kill switch, and market clock.

**Success looks like:**
- NLV and leverage ratio always visible in header
- Connection latency indicator (ping) in header
- Kill switch gates all order-entry surfaces
- Market clock showing session state

---

## Tasks (5 total)

### T2.1: Net Liquidation Value (NLV) Display - HIGH PRIORITY

**Goal:** Display total account value prominently for leverage awareness.

**Acceptance Criteria:**
- [ ] NLV visible in header at all times
- [ ] Updates in real-time with positions
- [ ] Leverage ratio displayed (color-coded: green <2x, yellow 2-3x, red >3x)
- [ ] Day change shown (+$X / +X%)
- [ ] Calculation: NLV = Cash + Sum(Position Market Value)

**Files:**
- Modify: `apps/web_console_ng/ui/layout.py`, `apps/web_console_ng/components/header_metrics.py`

---

### T2.2: Connection Latency Indicator - HIGH PRIORITY

**Goal:** Display WebSocket ping to build trust in connection quality.

**Latency Colors:**
- Green: < 100ms
- Orange: 100-300ms
- Red: > 300ms
- Gray: Disconnected

**Acceptance Criteria:**
- [ ] Ping displayed in header (e.g., "24ms")
- [ ] Color changes based on latency thresholds
- [ ] Disconnected state clearly visible
- [ ] Historical latency logged for debugging
- [ ] Ping measurement every 5 seconds

**Files:**
- Create: `apps/web_console_ng/core/latency_monitor.py`
- Modify: `apps/web_console_ng/ui/layout.py`

---

### T2.3: Connection Status & Graceful Degradation - HIGH PRIORITY

**Goal:** Clear connection status with read-only fallback.

**Acceptance Criteria:**
- [ ] Connection status indicator in header (green/yellow/red/gray)
- [ ] Read-only mode during disconnection
- [ ] "Stale data" warning when data > 30s old
- [ ] Auto-reconnect with visual countdown
- [ ] **Read-only mode disables:** submit order, cancel order, modify order, flatten position
- [ ] **Read-only mode allows:** view positions, view orders, view charts, export data
- [ ] Pending local actions reconciled on reconnect (show "reconnecting..." state)
- [ ] Connection state gates all order-entry surfaces

**Files:**
- Modify: `apps/web_console_ng/ui/layout.py`
- Create: `apps/web_console_ng/core/connection_monitor.py`

---

### T2.4: Kill Switch "Panic Button" UX - HIGH PRIORITY

**Goal:** Prominent emergency controls with visual state indication.

**Requirements:**
- Persistent status bar (not header border to avoid layout shift)
- "TRADING HALTED" text when engaged
- All order buttons disabled with visual indication

**Acceptance Criteria:**
- [ ] Status bar visible at top/bottom of screen
- [ ] Color changes based on system state (green=normal, red=halted)
- [ ] "HALT TRADING" button always visible in header
- [ ] 2-step confirmation for disengage (type "RESUME" to confirm)
- [ ] Kill switch state gates all order-entry surfaces
- [ ] Audit log entry when engaged/disengaged

**Files:**
- Create: `apps/web_console_ng/components/status_bar.py`
- Modify: `apps/web_console_ng/ui/layout.py`, `apps/web_console_ng/pages/kill_switch.py`

---

### T2.5: Market Clock & Session State - MEDIUM PRIORITY

**Goal:** Display market hours and session countdown.

**Display States:**
- Green: Market open
- Yellow: Pre/Post market
- Gray: Closed

**Acceptance Criteria:**
- [ ] Market state visible in header
- [ ] Countdown to next state change (e.g., "Closes in 2h 15m")
- [ ] Different displays for asset classes (equities, crypto, forex)
- [ ] Timezone-aware calculations (user timezone preference)
- [ ] **Market calendar source:** NYSE/NASDAQ holiday calendar from `exchange_calendars` library
- [ ] **Crypto handling:** Show "24/7" with next funding rate countdown if applicable
- [ ] **Cross-asset dashboards:** Show multiple market states if trading multiple asset classes

**Files:**
- Create: `apps/web_console_ng/components/market_clock.py`, `libs/common/market_hours.py`

---

## Dependencies

```
P6T1 (Core) ──> T2.1-T2.5 (all header components)

T2.3 Connection Status ──> Gates order entry in P6T4, P6T6, P6T7
T2.4 Kill Switch ──> Gates flatten (P6T7), one-click (P6T7)
```

---

## Testing Strategy

### Unit Tests
- NLV calculation
- Latency threshold colors
- Market clock timezone calculations

### Integration Tests
- Kill switch state propagation
- Latency monitor WebSocket

### E2E Tests
- Header metrics visibility
- Kill switch engage/disengage flow

---

## Definition of Done

- [ ] All 5 tasks implemented
- [ ] NLV and leverage always visible
- [ ] Connection status with graceful degradation
- [ ] Kill switch gates order entry
- [ ] Market clock showing session state
- [ ] Unit tests > 85% coverage
- [ ] E2E tests pass
- [ ] Code reviewed and approved

---

**Last Updated:** 2026-01-13
**Status:** TASK
