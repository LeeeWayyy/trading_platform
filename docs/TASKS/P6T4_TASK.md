---
id: P6T4
title: "Professional Trading Terminal - Order Entry Context"
phase: P6
task: T4
priority: P0
owner: "@development-team"
state: TASK
created: 2026-01-13
dependencies: [P6T1, P6T2, P6T3]
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P6_PLANNING.md]
features: [T4.1-T4.4]
---

# P6T4: Order Entry Context

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** TASK (Not Started)
**Priority:** P0 (Core Trading)
**Owner:** @development-team
**Created:** 2026-01-13
**Track:** Track 4 of 18
**Dependency:** P6T1 (Core), P6T2 (Header - connection/kill switch gating), P6T3 (Hotkeys)

---

## Objective

Build the order entry context with always-visible order ticket, real-time market data, price charts, and watchlist.

**Success looks like:**
- Order ticket visible on dashboard without navigation
- Level 1 market data displayed when entering orders
- TradingView chart integrated for visual context
- Watchlist with quick action capability

---

## Tasks (4 total)

### T4.1: Always-Visible Order Ticket - HIGH PRIORITY

**Goal:** Embed order entry directly on dashboard for rapid execution.

**Acceptance Criteria:**
- [ ] Order ticket visible on dashboard without navigation
- [ ] Position for selected symbol shown in ticket
- [ ] One-click quantity presets functional
- [ ] Buying power impact calculated before submit
- [ ] Order ticket respects connection state (disabled when disconnected)
- [ ] Order ticket respects kill switch state (disabled when halted)

**Files:**
- Create: `apps/web_console_ng/components/order_ticket.py`, `apps/web_console_ng/components/quantity_presets.py`
- Modify: `apps/web_console_ng/pages/dashboard.py`

---

### T4.2: Real-Time Market Context - MEDIUM PRIORITY

**Goal:** Display Level 1 market data when entering orders.

**Acceptance Criteria:**
- [ ] Bid/Ask spread visible for selected symbol
- [ ] Last price with change displayed
- [ ] Position context shown
- [ ] Real-time updates via WebSocket
- [ ] Fallback behavior when data feed unavailable (show "N/A", not error)
- [ ] Data staleness indicator (>30s = warning)

**Files:**
- Create: `apps/web_console_ng/components/market_context.py`, `apps/web_console_ng/components/level1_display.py`

---

### T4.3: TradingView Chart Integration - MEDIUM PRIORITY

**Goal:** Embed price charts for visual context during trading.

**Acceptance Criteria:**
- [ ] Lightweight Charts integrated via CDN
- [ ] Candlestick chart renders for selected symbol
- [ ] Execution prices marked on chart
- [ ] VWAP/TWAP overlays for algo orders
- [ ] Fallback to static chart if real-time feed unavailable
- [ ] Chart data source documented (licensing considerations noted)

**Files:**
- Create: `apps/web_console_ng/components/price_chart.py`, `apps/web_console_ng/ui/lightweight_charts.py`

---

### T4.4: Watchlist Component - MEDIUM PRIORITY

**Goal:** Compact symbol list with quick action capability.

**Acceptance Criteria:**
- [ ] Watchlist displays last price, change, sparkline
- [ ] Click to select updates order ticket
- [ ] Drag to reorder
- [ ] Add/remove symbols
- [ ] Watchlist persists via workspace persistence (P6T1.4)

**Files:**
- Create: `apps/web_console_ng/components/watchlist.py`, `apps/web_console_ng/components/sparkline.py`

---

## Dependencies

```
P6T1.4 Workspace ──> T4.4 Watchlist (persistence)
P6T2.3 Connection ──> T4.1 Order Ticket (gating)
P6T2.4 Kill Switch ──> T4.1 Order Ticket (gating)
P6T3.2 Hotkeys ──> T4.1 Order Ticket (B/S keys)
```

---

## Testing Strategy

### Unit Tests
- Order ticket validation
- Market context data mapping
- Watchlist state management

### Integration Tests
- Order ticket with market context
- Watchlist symbol selection

### E2E Tests
- Order entry workflow
- Chart rendering
- Watchlist persistence

---

## Definition of Done

- [ ] All 4 tasks implemented
- [ ] Order ticket always visible on dashboard
- [ ] Market context displayed for selected symbol
- [ ] Chart integrated with execution markers
- [ ] Watchlist functional and persisted
- [ ] Unit tests > 85% coverage
- [ ] E2E tests pass
- [ ] Code reviewed and approved

---

**Last Updated:** 2026-01-13
**Status:** TASK
