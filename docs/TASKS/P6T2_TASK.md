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

**Implementation Details:**
1. Create `HeaderMetrics` component class with compact UI (badges/labels, NOT cards)
2. Use existing `AsyncTradingClient.fetch_account_info()` for portfolio_value (NLV)
3. Calculate gross leverage = Sum(abs(market_value)) / NLV
   - **Prefer `market_value`**: Use position's `market_value` when available (more accurate)
   - **Fallback**: Only use `qty * current_price` if `market_value` is missing
   - **Guard against zero/negative NLV**: If NLV <= 0, show "—" instead of leverage value
   - **Missing data handling**: Skip positions without `market_value` AND `current_price`, mark leverage as "partial" only if any skipped
4. Integrate with layout.py header row between Title and Kill Switch controls
5. Reuse existing 5-second `update_global_status()` timer (no new timer needed)
6. **Critical: Isolation from Kill Switch updates**
   - **Execution order**: Kill switch + circuit breaker updates MUST run first, THEN metrics update
   - **Separate try/except**: Metrics update must have its own isolated try/except block OUTSIDE the kill switch try/except
   - **Never affect connection state**: Metrics failures/timeouts only mark metrics stale; they NEVER set connection badge to Disconnected or kill switch to UNKNOWN
   - **Catch TimeoutError explicitly**: `asyncio.TimeoutError` and `asyncio.CancelledError` from `wait_for` must be caught in metrics path, not bubble up
   - Use `asyncio.gather(return_exceptions=True)` for parallel account/positions fetch within metrics block
   - **Auth headers**: Pass `user_id`, `role`, `strategies` to all API calls for production compatibility
   - **Timeout guard**: Wrap metrics fetch with `asyncio.wait_for(timeout=4.0)` to avoid blocking the poll lock
7. **Day change persistence**:
   - Store baseline NLV in `app.storage.user` with date key (format: `nlv_baseline_YYYY-MM-DD`)
   - Use `ZoneInfo("America/New_York")` for ET boundary (handles DST)
   - Reset baseline at 00:00 ET trading day boundary
   - Show "Day Chg" label to indicate trading-day delta
   - **Note**: `app.storage.user` is session-scoped; baseline resets on page refresh (acceptable for v1)
   - **Future TODO**: Migrate to backend-provided `prev_close_equity` when available for persistence across sessions
8. **Stale indicator**: Mark metrics stale if no update in 30s (use `time.monotonic()` for tracking)
9. **Type normalization**: Parse all numeric fields to float, handle None/missing gracefully
10. **Compact format**: "NLV $1.24M | Lev 1.8x | Day +$12k"

**Data Sources:**
- `GET /api/v1/account` returns `portfolio_value`, `buying_power`, `cash`
- `GET /api/v1/positions` for position market values
- Gross Leverage ratio = Sum(abs(position_market_value)) / portfolio_value

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

**Implementation Details:**
1. Create `LatencyMonitor` class as singleton with async ping mechanism
2. Measure HTTP request/response round-trip time to `/api/v1/health` endpoint (lightweight)
3. Track rolling average over last 10 measurements for stability
4. Store historical latencies in memory (last 100) for debugging
5. Expose `get_current_latency()`, `get_latency_status()` methods
6. Integrate with header: small badge showing "24ms" with color background
7. Use existing timer infrastructure for 5-second measurement interval

**Latency Badge Format:**
- `"24ms"` with green/orange/red/gray background
- Tooltip shows: "API Latency: 24ms (avg: 28ms)"

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

**Implementation Details:**
1. Create `ConnectionMonitor` class extending existing connection_badge functionality
2. Track connection state: CONNECTED, DEGRADED (slow), DISCONNECTED, RECONNECTING
3. Implement read-only mode via global state flag checked by order components
4. Auto-reconnect with exponential backoff (1s, 2s, 4s, max 30s)
5. Visual countdown in header: "Reconnecting in 5s..."
6. Stale data detection: mark components stale if no update in 30s
7. Expose `is_read_only()`, `get_connection_state()` for gating order surfaces

**State Machine:**
```
CONNECTED -> DEGRADED (latency > 300ms for 3 consecutive)
CONNECTED -> DISCONNECTED (API failure)
DISCONNECTED -> RECONNECTING (auto-retry timer started)
RECONNECTING -> CONNECTED (success)
RECONNECTING -> DISCONNECTED (max retries exceeded)
```

**Read-Only Mode Gating:**
- Inject `connection_state` into NiceGUI client context
- Order buttons check state before enabling
- Show tooltip "Connection lost - read-only mode" when disabled

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

**Implementation Details:**
1. Create `StatusBar` component as a thin persistent bar above content
2. Status bar shows system state summary: "TRADING ACTIVE" (green) or "TRADING HALTED" (red)
3. Refactor existing kill switch dialog for 2-step disengage confirmation
4. ENGAGE: Single button with reason modal (existing pattern)
5. DISENGAGE: Type "RESUME" confirmation to prevent accidental resume
6. Kill switch state stored in NiceGUI client context for order gating
7. All order-entry surfaces check `kill_switch_state` before enabling
8. Audit logging already exists via `engage_kill_switch` / `disengage_kill_switch` API calls

**Status Bar Layout:**
```
┌──────────────────────────────────────────────────────────┐
│ [TRADING ACTIVE ✓]  or  [⚠️ TRADING HALTED - Click to Resume] │
└──────────────────────────────────────────────────────────┘
```

**2-Step Disengage Flow:**
1. Click "Resume Trading" button
2. Dialog appears: "Type RESUME to confirm trading will resume"
3. Input field validates exact match
4. Only then call `disengage_kill_switch` API

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

**Implementation Details:**
1. Add `exchange_calendars` library to requirements.txt for NYSE/NASDAQ calendars
2. Create `libs/common/market_hours.py` with reusable market session logic
3. Create `MarketClock` component for header display
4. Support multiple exchanges: NYSE (equities), CME (futures), crypto (24/7)
5. Timezone handling: store user preference, default to market timezone
6. Update every minute (market state changes are infrequent)

**MarketHours Service API:**
```python
class MarketHours:
    def get_session_state(exchange: str) -> SessionState  # OPEN, PRE_MARKET, POST_MARKET, CLOSED
    def get_next_transition(exchange: str) -> datetime    # When state will change
    def time_to_next_transition(exchange: str) -> timedelta
    def is_trading_day(exchange: str, date: date) -> bool
```

**Header Display Format:**
- `"NYSE: OPEN (closes 4:00 PM)"` - Green background
- `"NYSE: PRE-MKT (opens 9:30 AM)"` - Yellow background
- `"NYSE: CLOSED (opens Mon 9:30 AM)"` - Gray background
- `"CRYPTO: 24/7"` - Blue background

**Crypto Handling:**
- Always show "24/7 OPEN" for crypto
- Optional: show next funding rate countdown for perpetual futures

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
