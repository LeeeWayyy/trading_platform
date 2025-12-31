---
id: P5T4
title: "NiceGUI Migration - Real-Time Dashboard"
phase: P5
task: T4
priority: P0
owner: "@development-team"
state: PLANNING
created: 2025-12-31
dependencies: [P5T1, P5T2, P5T3]
estimated_effort: "11-16 days"
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P5_PLANNING.md, P5T1_TASK.md, P5T2_TASK.md, P5T3_TASK.md]
features: [T4.1, T4.2, T4.3, T4.4]
---

# P5T4: NiceGUI Migration - Real-Time Dashboard

**Phase:** P5 (Web Console Modernization)
**Status:** PLANNING
**Priority:** P0 (Core Functionality)
**Owner:** @development-team
**Created:** 2025-12-31
**Estimated Effort:** 11-16 days
**Track:** Phase 3 + Phase 3.5 from P5_PLANNING.md
**Dependency:** P5T1 (Foundation), P5T2 (Layout & Auth), P5T3 (HA/Scaling) must be complete

---

## Objective

Implement the main trading dashboard with real-time P&L updates, positions table, orders table, and activity feed. This is the core page users interact with most frequently.

**Success looks like:**
- Dashboard loads in < 500ms (cold) / < 150ms (warm)
- P&L updates push to UI in < 50ms from backend event
- Positions table handles 100+ rows with client-side sorting/filtering
- Orders table with cancel buttons and status badges
- Activity feed shows recent fills in real-time
- Hybrid push/poll strategy for optimal performance
- Security & performance validation gate passed

**Measurable SLAs:**
| Metric | Target | Stretch | Measurement |
|--------|--------|---------|-------------|
| Page load (cold) | < 500ms | < 300ms | Playwright timing |
| Page load (warm) | < 150ms | < 100ms | Playwright timing |
| P&L push update | < 50ms | < 30ms | Backend → UI timing |
| Position grid update | < 100ms | < 50ms | 100 row dataset |
| Concurrent users | 100 | 200 | k6 load test |
| WebSocket stability | 99.95% | 99.99% | 4hr soak test |
| Memory per user | < 25MB | < 15MB | Monitoring under load |

---

## Acceptance Criteria

### T4.1 Real-Time Update Infrastructure

**Push-Based Updates (WebSocket/Redis Pub/Sub):**
- [ ] `RealtimeUpdater` class for push subscriptions
- [ ] Redis Pub/Sub subscription for position updates
- [ ] Redis Pub/Sub subscription for kill switch state changes
- [ ] Redis Pub/Sub subscription for circuit breaker status
- [ ] Debounce: max 10 updates/second per user
- [ ] Backpressure: queue overflow → drop oldest updates
- [ ] Proper cleanup on disconnect (unsubscribe from channels)

**Polling-Based Updates (ui.timer):**
- [ ] Market prices polling (5s interval, backed by market data service)
- [ ] System health metrics polling (10s interval)
- [ ] Non-critical dashboard stats polling (30s interval)
- [ ] Timer cleanup on page navigation

**Update Strategy Documentation:**
| Data Type | Method | Interval | Justification |
|-----------|--------|----------|---------------|
| Position changes | Push | Real-time | Critical for trading decisions |
| P&L updates | Push | Real-time | Directly tied to position changes |
| Kill switch state | Push | Real-time | Safety-critical |
| Circuit breaker | Push | Real-time | Safety-critical |
| Market prices | Poll | 5s | High volume, not user-specific |
| System health | Poll | 10s | Non-critical, low frequency |
| Dashboard stats | Poll | 30s | Aggregated, low priority |

**Testing:**
- [ ] Push update latency < 50ms
- [ ] Debounce correctly limits to 10 updates/sec
- [ ] Backpressure drops oldest when queue > 100
- [ ] Cleanup verified on disconnect (no leaked subscriptions)

---

### T4.2 Dashboard Metric Cards

**Deliverables:**
- [ ] Unrealized P&L card with color coding (green/red)
- [ ] Total positions count card
- [ ] Day's realized P&L card
- [ ] Buying power / margin used card
- [ ] Kill switch status badge (prominent, always visible)
- [ ] Connection status indicator

**UI Requirements:**
- [ ] Cards arranged in responsive row (wrap on mobile)
- [ ] P&L values update via push (no full page refresh)
- [ ] Color transitions smooth (CSS transition)
- [ ] Loading skeleton on initial load
- [ ] Error state handling (stale data indicator > 30s)

**Implementation:**
```python
# apps/web_console_ng/components/metric_card.py
from nicegui import ui
from typing import Callable, Any
import time

class MetricCard:
    """Reusable metric card component with real-time updates."""

    def __init__(
        self,
        title: str,
        initial_value: str = "--",
        format_fn: Callable[[Any], str] | None = None,
        color_fn: Callable[[Any], str] | None = None,
    ):
        self.title = title
        self.format_fn = format_fn or str
        self.color_fn = color_fn
        self._value_label = None
        self._last_update: float | None = None  # Track last update time

        with ui.card().classes("flex-1 min-w-[200px]"):
            ui.label(title).classes("text-gray-500 text-sm")
            self._value_label = ui.label(initial_value).classes("text-2xl font-bold")

    def update(self, value: Any) -> None:
        """Update card value with optional color change."""
        formatted = self.format_fn(value)
        self._value_label.set_text(formatted)

        if self.color_fn:
            color_class = self.color_fn(value)
            # Remove old color classes, add new
            self._value_label.classes(
                color_class,
                remove="text-green-600 text-red-600 text-gray-600"
            )

        # Clear stale indicator on fresh data
        self._value_label.classes(remove="opacity-50")
        self._last_update = time.time()

    def mark_stale(self) -> None:
        """Mark data as stale (> 30s old)."""
        self._value_label.classes("opacity-50")

    def is_stale(self, threshold: float = 30.0) -> bool:
        """Check if data is stale (no update within threshold seconds)."""
        if self._last_update is None:
            return False  # No data yet, not stale
        return (time.time() - self._last_update) > threshold
```

**Testing:**
- [ ] Cards render correctly
- [ ] P&L color coding works (green for +, red for -)
- [ ] Updates propagate without page refresh
- [ ] Stale indicator appears after 30s

---

### T4.3 Positions AG Grid Table

**Deliverables:**
- [ ] AG Grid with client-side sorting/filtering
- [ ] Columns: Symbol, Qty, Avg Entry, Current Price, P&L ($), P&L (%)
- [ ] P&L color coding per row (green/red)
- [ ] Close position button per row
- [ ] Real-time row updates (efficient diffing)
- [ ] Row selection for bulk actions

**AG Grid Configuration:**
```python
# apps/web_console_ng/components/positions_grid.py
from nicegui import ui

def create_positions_grid() -> ui.aggrid:
    """Create AG Grid for positions with real-time updates."""

    column_defs = [
        {
            "field": "symbol",
            "headerName": "Symbol",
            "sortable": True,
            "filter": True,
            "pinned": "left",
            "width": 100,
        },
        {
            "field": "qty",
            "headerName": "Qty",
            "sortable": True,
            "type": "numericColumn",
            "width": 80,
        },
        {
            "field": "avg_entry_price",
            "headerName": "Avg Entry",
            "sortable": True,
            "valueFormatter": "x => '$' + x.value.toFixed(2)",
            "type": "numericColumn",
        },
        {
            "field": "current_price",
            "headerName": "Current",
            "sortable": True,
            "valueFormatter": "x => '$' + x.value.toFixed(2)",
            "type": "numericColumn",
        },
        {
            "field": "unrealized_pl",
            "headerName": "P&L ($)",
            "sortable": True,
            "valueFormatter": "x => '$' + x.value.toFixed(2)",
            "cellStyle": {
                "function": "params.value >= 0 ? {color: '#16a34a'} : {color: '#dc2626'}"
            },
            "type": "numericColumn",
        },
        {
            "field": "unrealized_plpc",
            "headerName": "P&L (%)",
            "sortable": True,
            "valueFormatter": "x => (x.value * 100).toFixed(2) + '%'",
            "cellStyle": {
                "function": "params.value >= 0 ? {color: '#16a34a'} : {color: '#dc2626'}"
            },
            "type": "numericColumn",
        },
        {
            "field": "actions",
            "headerName": "Actions",
            "cellRenderer": "agGroupCellRenderer",
            "pinned": "right",
            "width": 100,
            "suppressSorting": True,
        },
    ]

    grid = ui.aggrid({
        "columnDefs": column_defs,
        "rowData": [],
        "domLayout": "autoHeight",
        "defaultColDef": {
            "resizable": True,
            "sortable": True,
        },
        "rowSelection": "multiple",
        "suppressRowClickSelection": True,
        "animateRows": True,
        "getRowId": "data => data.symbol",  # For efficient updates
    }).classes("w-full")

    return grid


async def update_positions_grid(
    grid: ui.aggrid,
    positions: list[dict],
    previous_symbols: set[str] | None = None,
) -> set[str]:
    """
    Update grid with new positions data using AG Grid's applyTransaction.

    Uses getRowId (configured as 'data => data.symbol') for efficient delta updates:
    - No full re-render
    - Preserves scroll position
    - Preserves row selection
    - Only updates changed rows
    - REMOVES closed positions (symbols no longer in snapshot)

    Args:
        grid: The AG Grid instance
        positions: Current positions snapshot from backend
        previous_symbols: Set of symbols from previous update (for remove detection)

    Returns:
        Set of current symbols (pass to next update for remove detection)
    """
    current_symbols = {p["symbol"] for p in positions}

    # Compute removed symbols (closed positions)
    removed_symbols = []
    if previous_symbols is not None:
        removed_symbols = [
            {"symbol": s} for s in (previous_symbols - current_symbols)
        ]

    # Use applyTransaction for efficient partial updates
    # AG Grid will match rows by getRowId (symbol) and only update changed cells
    # 'remove' handles closed positions that no longer appear in snapshot
    await grid.run_grid_method('api.applyTransaction', {
        'update': positions,
        'remove': removed_symbols,
    })

    return current_symbols
```

**Close Position Button:**
```python
# In positions_grid.py
async def on_close_position(symbol: str, qty: int):
    """Handle close position button click."""
    # Show confirmation dialog
    with ui.dialog() as dialog, ui.card().classes("p-4"):
        ui.label(f"Close {symbol} position?").classes("text-lg font-bold")
        ui.label(f"Quantity: {qty} shares")

        with ui.row().classes("gap-4 mt-4"):
            async def confirm():
                client = AsyncTradingClient.get()
                # Generate idempotent client_order_id
                order_id = generate_close_order_id(symbol, qty)
                await client.submit_order({
                    "symbol": symbol,
                    "qty": abs(qty),
                    "side": "sell" if qty > 0 else "buy",
                    "type": "market",
                    "client_order_id": order_id,
                    "reason": f"Manual close via dashboard",
                })
                ui.notify(f"Closing {symbol} position", type="positive")
                dialog.close()

            ui.button("Confirm", on_click=confirm).classes("bg-red-600 text-white")
            ui.button("Cancel", on_click=dialog.close)

    dialog.open()
```

**Testing:**
- [ ] Grid renders with 100+ rows in < 100ms
- [ ] Sorting works (click column header)
- [ ] Filtering works (column filter)
- [ ] P&L color coding correct
- [ ] Close button triggers confirmation dialog
- [ ] Row updates don't reset scroll position
- [ ] Closed positions removed from grid (symbol no longer in snapshot → row removed)

---

### T4.4 Orders Table & Activity Feed

**Orders Table Deliverables:**
- [ ] Open orders table with status badges
- [ ] Columns: Symbol, Side, Qty, Type, Price, Status, Time, Actions
- [ ] Cancel button per row
- [ ] Status badge colors (pending=yellow, filled=green, cancelled=gray)
- [ ] Real-time status updates

**Activity Feed Deliverables:**
- [ ] Recent fills/events feed (last 20 items)
- [ ] Timestamp, symbol, side, qty, price, status
- [ ] New items appear at top with animation
- [ ] Auto-scroll to show new items
- [ ] Click to expand details

**Implementation:**
```python
# apps/web_console_ng/components/orders_table.py
from nicegui import ui

def create_orders_table() -> ui.aggrid:
    """Create AG Grid for open orders."""

    column_defs = [
        {"field": "symbol", "headerName": "Symbol", "width": 100},
        {
            "field": "side",
            "headerName": "Side",
            "width": 80,
            "cellStyle": {
                "function": "params.value === 'buy' ? {color: '#16a34a'} : {color: '#dc2626'}"
            },
        },
        {"field": "qty", "headerName": "Qty", "width": 80},
        {"field": "type", "headerName": "Type", "width": 80},
        {
            "field": "limit_price",
            "headerName": "Price",
            "valueFormatter": "x => x.value ? '$' + x.value.toFixed(2) : 'MKT'",
        },
        {
            "field": "status",
            "headerName": "Status",
            "cellRenderer": "statusBadgeRenderer",  # Custom renderer
            "width": 100,
        },
        {
            "field": "created_at",
            "headerName": "Time",
            "valueFormatter": "x => new Date(x.value).toLocaleTimeString()",
        },
        {
            "field": "actions",
            "headerName": "",
            "width": 80,
            "cellRenderer": "cancelButtonRenderer",
        },
    ]

    return ui.aggrid({
        "columnDefs": column_defs,
        "rowData": [],
        "domLayout": "autoHeight",
        "getRowId": "data => data.client_order_id",
    }).classes("w-full")


# apps/web_console_ng/components/activity_feed.py
from nicegui import ui
from collections import deque

class ActivityFeed:
    """Real-time activity feed with auto-scroll."""

    MAX_ITEMS = 20

    def __init__(self):
        self.items: deque = deque(maxlen=self.MAX_ITEMS)
        self._container = None

        with ui.card().classes("w-full h-64 overflow-y-auto") as card:
            self._container = card
            ui.label("Recent Activity").classes("text-lg font-bold mb-2")
            self._items_column = ui.column().classes("w-full gap-1")

    def add_item(self, event: dict) -> None:
        """Add new item to feed (appears at top)."""
        self.items.appendleft(event)
        self._render_items()

    def _render_items(self) -> None:
        """Re-render all items (newest first)."""
        self._items_column.clear()
        with self._items_column:
            for item in self.items:
                self._render_item(item)

    def _render_item(self, event: dict) -> None:
        """Render single activity item."""
        side_color = "text-green-600" if event["side"] == "buy" else "text-red-600"
        status_color = {
            "filled": "bg-green-100 text-green-800",
            "cancelled": "bg-gray-100 text-gray-800",
            "pending": "bg-yellow-100 text-yellow-800",
        }.get(event["status"], "bg-gray-100")

        with ui.row().classes("w-full items-center gap-2 p-2 hover:bg-gray-50 rounded"):
            ui.label(event["time"]).classes("text-xs text-gray-500 w-20")
            ui.label(event["symbol"]).classes("font-mono w-16")
            ui.label(event["side"].upper()).classes(f"{side_color} w-12")
            ui.label(str(event["qty"])).classes("w-12 text-right")
            ui.label(f"${event['price']:.2f}").classes("w-20 text-right")
            ui.label(event["status"]).classes(f"px-2 py-0.5 rounded text-xs {status_color}")
```

**Cancel Order Button:**
```python
async def on_cancel_order(client_order_id: str, symbol: str):
    """Handle cancel order button click."""
    with ui.dialog() as dialog, ui.card().classes("p-4"):
        ui.label(f"Cancel order for {symbol}?").classes("text-lg font-bold")
        ui.label(f"Order ID: {client_order_id[:8]}...")

        with ui.row().classes("gap-4 mt-4"):
            async def confirm():
                client = AsyncTradingClient.get()
                await client.cancel_order(client_order_id)
                ui.notify(f"Order cancelled", type="positive")
                dialog.close()

            ui.button("Confirm", on_click=confirm).classes("bg-red-600 text-white")
            ui.button("Cancel", on_click=dialog.close)

    dialog.open()
```

**Testing:**
- [ ] Orders table shows open orders
- [ ] Status badges render correctly
- [ ] Cancel button works
- [ ] Activity feed shows recent events
- [ ] New events appear at top

---

### T4.5 Security & Performance Validation Gate (Phase 3.5)

**CRITICAL:** This gate MUST pass before proceeding to P5T5 (Manual Trading Controls).

**Security Validation Checklist:**

*Authentication & Session:*
- [ ] All 4 auth flows tested (dev, basic, mTLS, OAuth2)
- [ ] Session store encryption verified
- [ ] CSRF protection functional
- [ ] Device binding working with proxies
- [ ] MFA step-up tested
- [ ] Session timeout enforced (15min idle, 4hr absolute)

*Authorization:*
- [ ] Per-user position/order access verified (no cross-user data leakage)
- [ ] Role-based UI controls working (viewer vs trader vs admin)
- [ ] Order submission blocked for viewer role
- [ ] Admin-only features protected

*WebSocket Security:*
- [ ] WS Origin header validation (reject unauthorized origins)
- [ ] WS session binding verified (user can only see their own data)
- [ ] WS reconnection validates existing session
- [ ] WS tenant isolation (no cross-user message leakage)

*Request Security:*
- [ ] Rate limiting on order/cancel endpoints
- [ ] Rate limiting on login attempts
- [ ] Anti-replay for order submission (idempotent client_order_id)
- [ ] Input validation on all trading parameters

*Headers & CSP:*
- [ ] CSP configured for NiceGUI WebSocket paths
- [ ] X-Frame-Options: DENY
- [ ] X-Content-Type-Options: nosniff
- [ ] Strict-Transport-Security header

*Audit & Monitoring:*
- [ ] Audit logging capturing all trade actions
- [ ] Audit logging for auth events (login, logout, session timeout)
- [ ] WS connect/disconnect events logged
- [ ] Suspicious activity alerts configured

**Performance Validation Checklist:**

*Measurement Definitions:*
| Metric | Measurement Point | Environment | Dataset |
|--------|-------------------|-------------|---------|
| Page load (cold) | `DOMContentLoaded` event in Playwright | Staging, no cache | First load after restart |
| Page load (warm) | `DOMContentLoaded` event | Staging, warm cache | Second load |
| P&L push update | Server `publish()` → client `performance.mark('pnl_updated')` | Staging | Single position update |
| Position grid update | `grid.update()` call → render complete | Local | 100 rows, 5 updates/sec |
| Memory per user | `psutil.Process().memory_info().rss / active_users` | Staging | 50 concurrent users |

*Validation Table:*
| Metric | Target | Test Method | Pass? |
|--------|--------|-------------|-------|
| Page load (cold) | < 500ms | Playwright timing | [ ] |
| Page load (warm) | < 150ms | Playwright timing | [ ] |
| P&L push update | < 50ms | Instrumented perf marks | [ ] |
| Position grid update | < 100ms | 100 row dataset test | [ ] |
| Concurrent users | 100 | k6 load test (30min) | [ ] |
| WebSocket stability | 99.95% | 4hr soak test | [ ] |
| Memory per user | < 25MB | Prometheus during load test | [ ] |
| P99 latency (all ops) | < 300ms | APM/Prometheus histogram | [ ] |
| Order submission E2E | < 200ms | Playwright click → API response | [ ] |

*Instrumentation for P&L Push Latency:*
```python
# Server-side: add timestamp to published data
await redis.publish(channel, json.dumps({
    **data,
    "_server_ts": time.time() * 1000  # epoch ms timestamp
}))
```

```javascript
// Client-side: measure on receipt (via custom JS injection)
// IMPORTANT: Use Date.now() (epoch ms) to match server timestamp
document.addEventListener('pnl_update', (e) => {
    const latency = Date.now() - e.detail._server_ts;
    console.log(`P&L update latency: ${latency}ms`);
    // Report to Prometheus/APM if latency > threshold
    if (latency > 50) {
        reportLatencyMetric('pnl_push', latency);
    }
});
```

**Failure Mode UX Validation:**
- [ ] "Connection Lost" banner displays correctly
- [ ] Read-only mode during disconnection
- [ ] Stale data indicators show when data > 30s old
- [ ] Backend down → graceful error, no crash
- [ ] Kill switch engaged → order buttons disabled

**Load Test Plan:**
```bash
# k6 load test script
k6 run --vus 50 --duration 30m tests/load/web_console_dashboard.js

# Metrics to capture:
# - http_req_duration (p95 < 500ms)
# - ws_connecting_duration (p95 < 100ms)
# - ws_sessions (stable over time)
# - memory_usage (no unbounded growth)
```

**Exit Criteria:** ALL checkboxes complete, ALL metrics met. BLOCK P5T5 until passed.

---

## Prerequisites Checklist

**Must verify before starting implementation:**

- [ ] **P5T1 complete:** Foundation, async client, session store
- [ ] **P5T2 complete:** Layout, auth flows, session management
- [ ] **P5T3 complete:** HA/scaling, observability, metrics
- [ ] **Redis Pub/Sub available:** For real-time push updates
- [ ] **Backend endpoints available:**
  - [ ] `GET /api/v1/positions` - Current positions
  - [ ] `GET /api/v1/orders` - Open orders
  - [ ] `GET /api/v1/orders/history` - Recent fills
  - [ ] `GET /api/v1/realtime_pnl` - P&L summary
  - [ ] `GET /api/v1/kill_switch_status` - Kill switch state
  - [ ] `POST /api/v1/orders/{id}/cancel` - Cancel order
- [ ] **AG Grid license:** Verify community edition sufficient, or obtain license
- [ ] **k6 installed:** For load testing

---

## Approach

### High-Level Plan

1. **C0: Real-Time Infrastructure** (2-3 days)
   - RealtimeUpdater class with Redis Pub/Sub
   - Debounce and backpressure implementation
   - Push/poll strategy implementation

2. **C1: Dashboard Metric Cards** (2-3 days)
   - MetricCard component
   - Dashboard page layout
   - Real-time updates wiring

3. **C2: Positions AG Grid** (2-3 days)
   - Grid configuration and styling
   - Close position button
   - Real-time row updates

4. **C3: Orders Table & Activity Feed** (2-3 days)
   - Orders table with cancel button
   - Activity feed component
   - Real-time status updates

5. **C4: Security & Performance Gate** (3-4 days)
   - Security validation tests
   - Performance benchmarking
   - Load testing
   - Failure mode UX validation

---

## Component Breakdown

### C0: Real-Time Infrastructure

**Files to Create:**
```
apps/web_console_ng/core/
├── realtime.py                  # RealtimeUpdater class
├── debounce.py                  # Debounce utility
tests/apps/web_console_ng/
├── test_realtime.py
└── test_debounce.py
```

**RealtimeUpdater Implementation:**
```python
# apps/web_console_ng/core/realtime.py
import asyncio
import json
import redis.asyncio as redis
from nicegui import Client
from typing import Callable, Any
import time
from apps.web_console_ng import config
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
import logging

logger = logging.getLogger(__name__)

class RealtimeUpdater:
    """
    Push real-time updates to connected clients via Redis Pub/Sub.

    Architecture:
    - Listener task: receives messages from Redis, puts in bounded queue (non-blocking)
    - Worker task: processes queue, throttles, delivers to UI callback
    - Decoupled: slow callbacks don't block message reception

    Features:
    - Throttle: max 10 updates/second per channel with trailing edge flush
    - Backpressure: bounded queue, drop oldest when full
    - Automatic cleanup on disconnect
    - NiceGUI client context enforcement
    - Redis connection retry on failure
    """

    MAX_UPDATES_PER_SECOND = 10
    MAX_QUEUE_SIZE = 100
    RECONNECT_DELAY = 1.0  # seconds

    def __init__(self, client_id: str, nicegui_client: Client):
        self.client_id = client_id
        self.nicegui_client = nicegui_client  # NiceGUI client for context binding
        self.redis = redis.from_url(config.REDIS_URL)
        self.subscriptions: dict[str, asyncio.Task] = {}  # Listener tasks
        self.workers: dict[str, asyncio.Task] = {}  # Worker tasks
        self.pubsubs: dict[str, redis.client.PubSub] = {}  # Track for cleanup
        # Use asyncio.Queue instead of deque+Event to avoid race conditions
        self.queues: dict[str, asyncio.Queue] = {}
        self.last_update_times: dict[str, float] = {}

    async def subscribe(
        self,
        channel: str,
        callback: Callable[[dict], Any],
    ) -> None:
        """
        Subscribe to a Redis Pub/Sub channel.

        Creates two tasks:
        1. Listener: receives from Redis, puts in queue (non-blocking)
        2. Worker: processes queue with throttling, delivers to callback
        """
        if channel in self.subscriptions:
            return  # Already subscribed

        self.last_update_times[channel] = 0
        # Use asyncio.Queue with maxsize for backpressure (race-free)
        self.queues[channel] = asyncio.Queue(maxsize=self.MAX_QUEUE_SIZE)

        # Start listener task
        listener_task = asyncio.create_task(self._listener(channel))
        self.subscriptions[channel] = listener_task

        # Start worker task
        worker_task = asyncio.create_task(self._worker(channel, callback))
        self.workers[channel] = worker_task

        # Register both for cleanup on disconnect
        lifecycle = ClientLifecycleManager.get()
        lifecycle.register_task(self.client_id, listener_task)
        lifecycle.register_task(self.client_id, worker_task)

    async def _listener(self, channel: str) -> None:
        """
        Listener task: receives messages from Redis Pub/Sub.

        Non-blocking: uses put_nowait with backpressure handling.
        Reconnects on connection failure.
        """
        pubsub: redis.client.PubSub | None = None

        while True:  # Retry loop for reconnection
            try:
                # Create fresh pubsub on each reconnection attempt
                pubsub = self.redis.pubsub()
                await pubsub.subscribe(channel)
                self.pubsubs[channel] = pubsub

                async for message in pubsub.listen():
                    if message["type"] == "message":
                        data = json.loads(message["data"])
                        queue = self.queues[channel]

                        # Backpressure: if queue full, drop oldest and add new
                        if queue.full():
                            try:
                                queue.get_nowait()  # Drop oldest
                            except asyncio.QueueEmpty:
                                pass

                        try:
                            queue.put_nowait(data)  # Non-blocking add
                        except asyncio.QueueFull:
                            pass  # Queue was emptied by worker, skip

            except asyncio.CancelledError:
                break  # Clean shutdown
            except redis.ConnectionError as e:
                logger.warning(f"Redis connection lost for {channel}: {e}")
            except Exception as e:
                logger.error(f"Listener error for {channel}: {e}")
            finally:
                # Cleanup pubsub before retry to avoid resource leak
                # Also remove from self.pubsubs to prevent double-close in unsubscribe()
                if pubsub:
                    self.pubsubs.pop(channel, None)  # Remove to prevent double-close
                    try:
                        await pubsub.unsubscribe(channel)
                        await pubsub.close()
                    except Exception:
                        pass
                    pubsub = None

            await asyncio.sleep(self.RECONNECT_DELAY)

    async def _worker(self, channel: str, callback: Callable[[dict], Any]) -> None:
        """
        Worker task: processes queued messages with throttling.

        Uses asyncio.Queue.get() for race-free waiting.
        Decoupled from listener - slow callbacks don't block message reception.
        """
        min_interval = 1.0 / self.MAX_UPDATES_PER_SECOND
        queue = self.queues[channel]

        while True:
            try:
                # Wait for first item (blocks until data available - race-free)
                latest_data = await queue.get()

                # Drain remaining items, keep only latest (conflation)
                while not queue.empty():
                    try:
                        latest_data = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                # Throttle: ensure min interval between deliveries
                now = time.time()
                last_update = self.last_update_times.get(channel, 0)
                wait_time = min_interval - (now - last_update)
                if wait_time > 0:
                    await asyncio.sleep(wait_time)

                # Deliver to callback
                await self._deliver_update(channel, latest_data, callback)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker error for {channel}: {e}")

    async def _deliver_update(
        self,
        channel: str,
        data: dict,
        callback: Callable[[dict], Any],
    ) -> None:
        """
        Deliver update to callback with NiceGUI client context.

        CRITICAL: Must use client context to ensure UI updates
        go to the correct browser session.
        """
        self.last_update_times[channel] = time.time()

        try:
            # Bind to correct NiceGUI client context
            async with self.nicegui_client:
                result = callback(data)
                if asyncio.iscoroutine(result):
                    await result
        except Exception as e:
            logger.error(f"Realtime callback error for {channel}: {e}")

    async def unsubscribe(self, channel: str) -> None:
        """Unsubscribe from a channel and cleanup resources."""
        # Cancel listener task
        listener_task = self.subscriptions.pop(channel, None)
        if listener_task and not listener_task.done():
            listener_task.cancel()
            try:
                await listener_task
            except asyncio.CancelledError:
                pass

        # Cancel worker task
        worker_task = self.workers.pop(channel, None)
        if worker_task and not worker_task.done():
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

        # Close pubsub connection (may already be closed by listener's finally)
        pubsub = self.pubsubs.pop(channel, None)
        if pubsub:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.close()
            except Exception:
                pass  # Already closed by listener's finally block

        # Cleanup queue
        self.queues.pop(channel, None)

    async def cleanup(self) -> None:
        """Cleanup all subscriptions and connections (called on disconnect)."""
        for channel in list(self.subscriptions.keys()):
            await self.unsubscribe(channel)

        # Close Redis connection pool
        await self.redis.close()
        await self.redis.connection_pool.disconnect()


# Channel names
def position_channel(user_id: str) -> str:
    return f"positions:{user_id}"

def kill_switch_channel() -> str:
    return "kill_switch:state"

def circuit_breaker_channel() -> str:
    return "circuit_breaker:state"
```

**Debounce Utility:**
```python
# apps/web_console_ng/core/debounce.py
import asyncio
from typing import Callable, Any
import time

class Debouncer:
    """
    Debounce function calls - only execute after delay with no new calls.

    Useful for UI updates that shouldn't fire too frequently.
    """

    def __init__(self, delay: float = 0.1):
        self.delay = delay
        self._task: asyncio.Task | None = None
        self._last_call: float = 0

    async def call(self, func: Callable[[], Any]) -> None:
        """Call function after delay, cancelling previous pending call."""
        if self._task and not self._task.done():
            self._task.cancel()

        async def delayed_call():
            await asyncio.sleep(self.delay)
            result = func()
            if asyncio.iscoroutine(result):
                await result

        self._task = asyncio.create_task(delayed_call())
```

**Testing:**
- [ ] Subscribe receives messages via listener → asyncio.Queue → worker
- [ ] Throttle limits to 10 updates/sec (worker side)
- [ ] Backpressure: asyncio.Queue drops oldest when full (maxsize=MAX_QUEUE_SIZE)
- [ ] Listener doesn't block when worker is slow (decoupled via asyncio.Queue)
- [ ] Worker delivers latest data even when multiple items queued (conflation)
- [ ] Cleanup cancels both listener and worker tasks
- [ ] Redis reconnection works after connection loss
- [ ] Pubsub cleanup on retry (no resource leak)

---

### C1: Dashboard Metric Cards

**Files to Create:**
```
apps/web_console_ng/components/
├── metric_card.py               # MetricCard component
apps/web_console_ng/pages/
├── dashboard.py                 # Main dashboard page
tests/apps/web_console_ng/
├── test_metric_card.py
└── test_dashboard.py
```

**Dashboard Page Implementation:**
```python
# apps/web_console_ng/pages/dashboard.py
from nicegui import ui
from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng.core.realtime import RealtimeUpdater, position_channel, kill_switch_channel
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console_ng.auth.middleware import requires_auth
from apps.web_console_ng.components.metric_card import MetricCard
from apps.web_console_ng.components.positions_grid import create_positions_grid, update_positions_grid
from apps.web_console_ng.components.orders_table import create_orders_table
from apps.web_console_ng.components.activity_feed import ActivityFeed

@ui.page("/")
@requires_auth
@main_layout
async def dashboard(client: Client) -> None:
    """Main trading dashboard with real-time updates."""
    trading_client = AsyncTradingClient.get()
    user_id = get_current_user_id()
    client_id = client.storage.get("client_id")

    # Initialize realtime updater with NiceGUI client for context binding
    realtime = RealtimeUpdater(client_id, client)

    # Track timers for cleanup on navigation/disconnect
    lifecycle = ClientLifecycleManager.get()
    timers: list[ui.timer] = []

    # ===== Metric Cards Row =====
    with ui.row().classes("w-full gap-4 mb-6 flex-wrap"):
        pnl_card = MetricCard(
            title="Unrealized P&L",
            format_fn=lambda v: f"${v:,.2f}",
            color_fn=lambda v: "text-green-600" if v >= 0 else "text-red-600",
        )
        positions_card = MetricCard(
            title="Positions",
            format_fn=lambda v: str(v),
        )
        realized_card = MetricCard(
            title="Realized (Today)",
            format_fn=lambda v: f"${v:,.2f}",
            color_fn=lambda v: "text-green-600" if v >= 0 else "text-red-600",
        )
        bp_card = MetricCard(
            title="Buying Power",
            format_fn=lambda v: f"${v:,.2f}",
        )

    # ===== Positions Grid =====
    with ui.card().classes("w-full mb-6"):
        ui.label("Positions").classes("text-lg font-bold mb-2")
        positions_grid = create_positions_grid()

    # ===== Orders & Activity Row =====
    with ui.row().classes("w-full gap-4"):
        # Orders table
        with ui.card().classes("flex-1"):
            ui.label("Open Orders").classes("text-lg font-bold mb-2")
            orders_table = create_orders_table()

        # Activity feed
        with ui.card().classes("w-80"):
            activity_feed = ActivityFeed()

    # Track symbols for remove detection (closed positions)
    position_symbols: set[str] = set()

    # ===== Initial Data Load =====
    async def load_initial_data():
        nonlocal position_symbols
        try:
            # Fetch all data in parallel
            pnl_data, positions, orders = await asyncio.gather(
                trading_client.fetch_realtime_pnl(user_id),
                trading_client.fetch_positions(user_id),
                trading_client.fetch_open_orders(user_id),
            )

            # Update UI
            pnl_card.update(pnl_data.get("total_unrealized_pl", 0))
            positions_card.update(pnl_data.get("total_positions", 0))
            realized_card.update(pnl_data.get("realized_pl_today", 0))
            bp_card.update(pnl_data.get("buying_power", 0))

            # Initial load: no previous symbols, returns current symbols
            position_symbols = await update_positions_grid(
                positions_grid, positions.get("positions", [])
            )
            orders_table.options["rowData"] = orders.get("orders", [])
            orders_table.update()

        except Exception as e:
            ui.notify(f"Failed to load dashboard: {e}", type="negative")

    await load_initial_data()

    # ===== Real-Time Subscriptions =====
    async def on_position_update(data: dict):
        """Handle real-time position update."""
        nonlocal position_symbols
        pnl_card.update(data.get("total_unrealized_pl", 0))
        positions_card.update(data.get("total_positions", 0))
        realized_card.update(data.get("realized_pl_today", 0))
        # Pass previous symbols for remove detection, get back current symbols
        position_symbols = await update_positions_grid(
            positions_grid, data.get("positions", []), position_symbols
        )

        # Add to activity feed if there's a new event
        if "event" in data:
            activity_feed.add_item(data["event"])

    async def on_kill_switch_update(data: dict):
        """Handle kill switch state change - critical safety update."""
        # Global kill switch badge is updated by layout, but we should
        # also disable order buttons when engaged
        if data.get("state") == "ENGAGED":
            ui.notify("Kill Switch ENGAGED - Trading disabled", type="warning")

    await realtime.subscribe(position_channel(user_id), on_position_update)
    await realtime.subscribe(kill_switch_channel(), on_kill_switch_update)

    # ===== Polling for Market Data =====
    async def update_market_data():
        """Poll market prices (not user-specific, high volume)."""
        try:
            prices = await trading_client.fetch_market_prices()
            # Use AG Grid's applyTransaction for efficient partial updates
            # Only send changed rows, not full dataset
            changed_rows = [
                {"symbol": p["symbol"], "current_price": p["price"]}
                for p in prices
            ]
            await positions_grid.run_grid_method(
                'api.applyTransaction',
                {'update': changed_rows}
            )
        except Exception:
            pass  # Don't spam errors for background polling

    # Create and track timers for cleanup
    market_timer = ui.timer(5.0, update_market_data)
    timers.append(market_timer)

    # ===== Stale Data Detection =====
    async def check_stale_data():
        """Mark data as stale if no updates in 30s."""
        for card in [pnl_card, positions_card, realized_card, bp_card]:
            if card.is_stale(threshold=30.0):
                card.mark_stale()

    stale_timer = ui.timer(10.0, check_stale_data)
    timers.append(stale_timer)

    # ===== Cleanup on navigation/disconnect =====
    def cleanup_timers():
        for timer in timers:
            timer.cancel()

    lifecycle.register_cleanup_callback(client_id, cleanup_timers)
    lifecycle.register_cleanup_callback(client_id, realtime.cleanup)
```

---

### C2: Positions AG Grid

See T4.3 acceptance criteria for implementation details.

**Files to Create:**
```
apps/web_console_ng/components/
├── positions_grid.py            # Positions AG Grid
tests/apps/web_console_ng/
└── test_positions_grid.py
```

---

### C3: Orders Table & Activity Feed

See T4.4 acceptance criteria for implementation details.

**Files to Create:**
```
apps/web_console_ng/components/
├── orders_table.py              # Orders AG Grid
├── activity_feed.py             # Activity feed component
tests/apps/web_console_ng/
├── test_orders_table.py
└── test_activity_feed.py
```

---

### C4: Security & Performance Gate

**Files to Create:**
```
tests/e2e/
├── test_auth_flows.py           # All 4 auth flow tests
├── test_security_validation.py  # CSRF, device binding, etc.
tests/load/
├── web_console_dashboard.js     # k6 load test
├── web_console_soak.js          # 4hr soak test
docs/RUNBOOKS/
└── nicegui-performance.md       # Performance tuning guide
```

**Load Test Script:**
```javascript
// tests/load/web_console_dashboard.js
import http from 'k6/http';
import ws from 'k6/ws';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '5m', target: 50 },   // Ramp up
    { duration: '20m', target: 100 }, // Hold at 100 users
    { duration: '5m', target: 0 },    // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'],  // 95% of requests < 500ms
    ws_connecting: ['p(95)<100'],      // 95% of WS connections < 100ms
  },
};

export default function () {
  // Login
  const loginRes = http.post(`${__ENV.BASE_URL}/auth/login`, {
    username: 'loadtest',
    password: 'loadtest123',
  });
  check(loginRes, { 'login success': (r) => r.status === 200 });

  // Load dashboard
  const dashRes = http.get(`${__ENV.BASE_URL}/`);
  check(dashRes, { 'dashboard loads': (r) => r.status === 200 });

  // WebSocket connection
  const wsUrl = `${__ENV.WS_URL}/_nicegui_ws`;
  const res = ws.connect(wsUrl, {}, (socket) => {
    socket.on('open', () => console.log('WS connected'));
    socket.on('message', (data) => console.log('WS message'));

    // Keep connection open for duration
    sleep(30);
    socket.close();
  });
  check(res, { 'ws connected': (r) => r && r.status === 101 });

  sleep(1);
}
```

---

## Testing Strategy

### Unit Tests (CI - Automated)
- `test_realtime.py`: Pub/Sub subscription, debounce, backpressure
- `test_debounce.py`: Debounce utility
- `test_metric_card.py`: Card rendering, updates
- `test_positions_grid.py`: Grid configuration, updates
- `test_orders_table.py`: Order display, cancel action
- `test_activity_feed.py`: Feed updates, max items

### Integration Tests (CI - Docker)
- `test_dashboard_integration.py`: Full dashboard load with mocked backend
- `test_realtime_integration.py`: Redis Pub/Sub flow

### E2E Tests (CI - Playwright)
- `test_dashboard_e2e.py`: Full user flow with real backend
- `test_auth_flows.py`: All 4 auth types

### Load Tests (Manual - Pre-Release)
- `web_console_dashboard.js`: 100 concurrent users
- `web_console_soak.js`: 4hr stability test

### Security Tests (Manual - Pre-Release)
- `test_security_validation.py`: CSRF, session, device binding

---

## Dependencies

### External
- `nicegui>=2.0`: UI framework
- `httpx>=0.25`: Async HTTP client
- `redis>=5.0`: Redis Pub/Sub
- `k6`: Load testing tool (installed separately)
- `playwright`: E2E testing

### Internal
- `apps/web_console_ng/core/client.py`: Async trading client (P5T1)
- `apps/web_console_ng/auth/`: Auth middleware (P5T2)
- `apps/web_console_ng/core/redis_ha.py`: Redis HA (P5T3)
- `apps/web_console_ng/core/client_lifecycle.py`: Task cleanup (P5T3)

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| AG Grid performance with 100+ rows | Medium | Medium | Virtual scrolling, efficient updates |
| Pub/Sub message loss | Low | Medium | Periodic polling fallback, stale detection |
| WebSocket disconnection during trading | Medium | High | Reconnection logic, state recovery (P5T3) |
| Memory growth from subscriptions | Medium | Medium | Proper cleanup, task tracking |
| Load test reveals bottleneck | Medium | Medium | Profile and optimize before release |
| Security validation fails | Low | Critical | Address issues before proceeding |

---

## Implementation Notes

**Address during development:**

1. **AG Grid License:** ✅ ADDRESSED IN DOCUMENT
   - Verify community edition supports required features
   - If enterprise features needed, obtain license
   - **Use `applyTransaction` for partial updates** (Rev 2)

2. **Pub/Sub Message Format:**
   - Standardize JSON schema for position/order updates
   - Include `_server_ts` for latency measurement
   - Document in API specification

3. **Listener/Worker Decoupling:** ✅ ADDRESSED IN DOCUMENT (Rev 4)
   - Listener task: receives from Redis, puts in asyncio.Queue (non-blocking)
   - Worker task: awaits queue.get(), throttles, delivers to UI callback
   - Decoupled: slow callbacks don't block message reception
   - Fixes "listener blocks on callback" issue from Rev 2 review
   - **Rev 4:** Changed from deque+Event to asyncio.Queue to fix race condition
     (Event.set() could be called between Event.wait() return and deque read)

4. **Backpressure Implementation:** ✅ ADDRESSED IN DOCUMENT (Rev 4)
   - Uses `asyncio.Queue(maxsize=MAX_QUEUE_SIZE)` with drop-oldest logic
   - On full queue: `get_nowait()` to drop oldest, then `put_nowait()` new
   - Worker consumes all queued messages via conflation, delivers only latest
   - Meets acceptance test: "drops oldest when queue > 100"
   - **Rev 4:** asyncio.Queue provides race-free signaling (await queue.get())

5. **NiceGUI Client Context:** ✅ ADDRESSED IN DOCUMENT (Rev 2)
   - All realtime callbacks wrapped with `async with self.nicegui_client`
   - Dashboard passes NiceGUI `Client` to `RealtimeUpdater`
   - Critical for multi-session correctness

6. **Resource Cleanup:** ✅ ADDRESSED IN DOCUMENT (Rev 4)
   - Listener and worker tasks both cancelled in `unsubscribe()`
   - Redis pubsub connections closed
   - Redis connection pool disconnected in `cleanup()`
   - Queue cleaned up in `unsubscribe()`
   - **Rev 4:** Added `finally` block in listener for pubsub cleanup on retry
     (prevents resource leak when reconnecting after connection loss)

7. **Redis Reconnection:** ✅ ADDRESSED IN DOCUMENT (Rev 3)
   - Listener has `while True` retry loop
   - Creates fresh pubsub on each reconnection attempt
   - Catches `redis.ConnectionError` and reconnects
   - Logs warning on connection loss

8. **Latency Instrumentation:** ✅ ADDRESSED IN DOCUMENT (Rev 3)
   - Server: `time.time() * 1000` (epoch ms)
   - Client: `Date.now()` (epoch ms) - matches server timestamp
   - Fixed clock mismatch issue from Rev 2 review

9. **Stale Indicator:** ✅ ADDRESSED IN DOCUMENT (Rev 3)
   - `MetricCard.update()` removes `opacity-50` class on fresh data
   - Added `is_stale()` helper method for safe stale check
   - Handles `None` case when no data has been received yet

10. **Kill Switch UI Behavior:**
    - When engaged, disable all order submission buttons
    - Show prominent warning banner
    - Allow read-only dashboard viewing

11. **Stale Data UX:**
    - After 30s without update, show opacity:50% on affected data
    - After 60s, show "Data may be stale" warning
    - After 120s, prompt user to refresh

12. **Cancel/Close Error Handling (from Rev 2 review):**
    - Wrap order cancel/close in try/except
    - Show negative notification on failure
    - Disable button during in-flight request
    - Re-enable on success/failure

13. **Positions Grid Efficient Updates:** ✅ ADDRESSED IN DOCUMENT (Rev 5)
    - Uses `grid.run_grid_method('api.applyTransaction', {'update': positions, 'remove': removed})`
    - NOT `grid.options["rowData"] = positions; grid.update()` (resets scroll)
    - Requires `getRowId: 'data => data.symbol'` in grid config (already set)
    - Preserves scroll position, row selection, and only updates changed cells
    - **Rev 5:** Added `remove` handling for closed positions
      - Track `previous_symbols` set in dashboard state
      - Compute `removed = previous_symbols - current_symbols` on each update
      - Pass `{remove: [{symbol: s} for s in removed]}` to applyTransaction

14. **Pubsub Double-Close Prevention:** ✅ ADDRESSED IN DOCUMENT (Rev 5)
    - Listener's `finally` block removes from `self.pubsubs` before closing
    - `unsubscribe()` uses try/except guard when closing pubsub
    - Prevents "already closed" exceptions during cleanup path
    - Ensures clean shutdown even with concurrent reconnection attempts

---

## Definition of Done

- [ ] All acceptance criteria met
- [ ] Unit tests pass with >90% coverage
- [ ] Integration tests pass
- [ ] E2E tests pass (Playwright)
- [ ] Load test passes (100 concurrent users)
- [ ] Soak test passes (4hr stability)
- [ ] Security validation complete
- [ ] Performance validation complete
- [ ] No regressions in P5T1/P5T2/P5T3 tests
- [ ] Code reviewed and approved
- [ ] Merged to feature branch

---

**Last Updated:** 2025-12-31 (Rev 5)
**Status:** PLANNING
