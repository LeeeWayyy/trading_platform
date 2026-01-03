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
related_docs: [P5_PLANNING.md, P5T1_DONE.md, P5T2_DONE.md, P5T3_DONE.md]
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
- [ ] Throttle: max 10 updates/second per channel per user (Rev 11: clarified scope)
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
- [ ] Throttle correctly limits to 10 updates/sec per channel (Rev 11: clarified scope)
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
- [ ] Connection status indicator (implemented in shared layout header - see layout.py)

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
    """Reusable metric card component with real-time updates.

    Rev 8: Tracks current color class to ensure exact cleanup (per Gemini iteration 3).
    """

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
        self._current_color_class: str | None = None  # Rev 8: Track current color for cleanup

        with ui.card().classes("flex-1 min-w-[200px]"):
            ui.label(title).classes("text-gray-500 text-sm")
            self._value_label = ui.label(initial_value).classes("text-2xl font-bold")

    def update(self, value: Any) -> None:
        """Update card value with optional color change."""
        formatted = self.format_fn(value)
        self._value_label.set_text(formatted)

        if self.color_fn:
            new_color_class = self.color_fn(value)
            # Rev 8: Remove exactly the previous color class (not hardcoded list)
            if self._current_color_class and self._current_color_class != new_color_class:
                self._value_label.classes(remove=self._current_color_class)
            self._value_label.classes(new_color_class)
            self._current_color_class = new_color_class

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

**Connection Status Indicator:**
```python
# Rev 17: Connection status indicator is implemented in the shared layout (layout.py)
# It appears in the header next to the kill switch badge and updates based on
# the kill-switch polling success/failure:
#
# connection_badge = ui.label("Connected").classes(
#     "px-2 py-1 rounded text-xs bg-green-500 text-white"
# )
#
# In update_global_status():
#   - On successful fetch: connection_badge.set_text("Connected") with green bg
#   - On failure: connection_badge.set_text("Disconnected") with red bg
#
# This provides a visual indicator for connection health across all dashboard pages.
```

**Testing:**
- [ ] Cards render correctly
- [ ] P&L color coding works (green for +, red for -)
- [ ] Updates propagate without page refresh
- [ ] Stale indicator appears after 30s
- [ ] Connection status indicator shows green when connected, red on disconnect

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
import logging

from nicegui import ui

logger = logging.getLogger(__name__)


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
            "cellRenderer": "closePositionRenderer",  # Rev 11: Fixed - was agGroupCellRenderer
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
        # Rev 18k: Row ID uses symbol only - ASSUMES positions are aggregated per symbol
        # If positions are per-strategy/account, use composite ID: "data => `${data.symbol}|${data.strategy_id}`"
        "getRowId": "data => data.symbol",  # For efficient updates
        # Capture API for trading_state_change refresh (per Codex review)
        "onGridReady": "params => { window._positionsGridApi = params.api; }",
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
    - ADDS new positions (symbols not in previous snapshot)
    - REMOVES closed positions (symbols no longer in snapshot)

    Rev 18: Fixed to use setRowData on first load and add/update/remove for deltas
    (per Codex review - HIGH)

    Args:
        grid: The AG Grid instance
        positions: Current positions snapshot from backend
        previous_symbols: Set of symbols from previous update (for remove detection)

    Returns:
        Set of current symbols (pass to next update for remove detection)

    Rev 18h: Filter malformed entries (per Codex review - LOW)
    """
    # Rev 18h: Filter out malformed entries without symbol key to prevent KeyError
    # This ensures real-time updates continue for valid positions even if one is malformed
    valid_positions = [p for p in positions if p.get("symbol")]
    if len(valid_positions) < len(positions):
        malformed_count = len(positions) - len(valid_positions)
        logger.warning(
            "update_positions_grid_malformed_entries",
            extra={"malformed_count": malformed_count, "total_count": len(positions)},
        )

    current_symbols = {p["symbol"] for p in valid_positions}

    if previous_symbols is None:
        # First load - use setRowData for initial population
        await grid.run_grid_method('api.setRowData', valid_positions)
        return current_symbols

    # Compute added symbols (new positions)
    added_positions = [p for p in valid_positions if p["symbol"] not in previous_symbols]

    # Compute updated positions (existing symbols)
    updated_positions = [p for p in valid_positions if p["symbol"] in previous_symbols]

    # Compute removed symbols (closed positions)
    removed_symbols = [{"symbol": s} for s in (previous_symbols - current_symbols)]

    # Use applyTransaction for efficient partial updates
    # AG Grid will match rows by getRowId (symbol) and only update changed cells
    await grid.run_grid_method('api.applyTransaction', {
        'add': added_positions,
        'update': updated_positions,
        'remove': removed_symbols,
    })

    return current_symbols
```

**Close Position Button:**
```python
# In positions_grid.py
import hashlib

# Rev 8: Fixed idempotency key generation (per Gemini iteration 3 review)
# Previous version used date-only, which blocked valid sequential trading
# Rev 13: Added user_id to hash to prevent collisions in shared accounts (per Codex iteration 8 - LOW)
# Rev 14: Added delimiters and normalized symbol (per Codex iteration 9 - LOW)
def generate_close_order_id(symbol: str, qty: int, dialog_nonce: str, user_id: str) -> str:
    """Generate deterministic client_order_id for position close.

    Pattern: hash(user_id|symbol|qty|close|nonce)[:24]

    Uses '|' delimiter to prevent ambiguous concatenations (e.g., user="ab", symbol="c"
    vs user="a", symbol="bc" would produce same hash without delimiters).

    The dialog_nonce is generated when the close dialog OPENS (full uuid4().hex)
    and persisted for that specific dialog interaction. This ensures:
    - Retries of the same dialog submission use the same ID (idempotent)
    - New dialog opens get new IDs (allows sequential trading)
    - Different users in shared accounts get different IDs

    IMPORTANT - Server-Side Idempotency Required:
    This client-side nonce provides defense-in-depth but is NOT sufficient alone.
    If the user refreshes after a network error, a new nonce generates a new order ID.
    The backend MUST implement server-side idempotency protection:
    - Option A: Track position state (position_id + last_close_ts) and reject duplicate closes
    - Option B: Use a server-issued action token tied to position snapshot
    - Option C: Implement at-most-once delivery with order deduplication window
    See execution_gateway order submission for the authoritative implementation.

    Args:
        symbol: The trading symbol
        qty: Position quantity to close
        dialog_nonce: Unique ID generated when dialog opens (full uuid4().hex)
        user_id: User ID to ensure uniqueness in shared account scenarios

    Returns:
        24-character deterministic order ID
    """
    # Normalize symbol to uppercase for consistency
    normalized_symbol = symbol.upper().strip()
    # Use delimiter to prevent ambiguous concatenations
    raw = f"{user_id}|{normalized_symbol}|{abs(qty)}|close|{dialog_nonce}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


async def on_close_position(symbol: str, qty: int, user_id: str, user_role: str):
    """Handle close position button click.

    Trading Safety Policy:
    - Kill Switch ENGAGED: Block new entries AND closes (emergency stop-all)
    - Circuit Breaker TRIPPED: Block new entries, ALLOW closes (risk-reducing)
    - Cancels: ALWAYS allowed (they only reduce exposure)

    Role-based gating:
    - viewer: Cannot execute any trades (read-only)
    - trader: Can execute trades within limits
    - admin: Full access

    SECURITY NOTE: Frontend validation is defense-in-depth only.
    Backend MUST validate:
    - User owns the position being closed
    - qty does not exceed current position size
    - User has permission to trade this symbol
    - Order passes risk limits
    The backend submit_order endpoint enforces these server-side.
    """
    import uuid
    client = AsyncTradingClient.get()

    # Validate qty is non-zero (can't close zero shares)
    if qty == 0:
        ui.notify("Cannot close position with zero quantity", type="warning")
        return

    # Role-based authorization check
    if user_role == "viewer":
        ui.notify("Viewers cannot execute trades", type="warning")
        return

    # Server-side safety checks
    # Kill switch blocks ALL trading (including risk-reducing closes)
    # Circuit breaker TRIPPED allows closes (they reduce risk)
    try:
        ks_status = await client.fetch_kill_switch_status(user_id, role=user_role)
        if ks_status.get("state") == "ENGAGED":
            logger.info(
                "close_blocked_kill_switch",
                extra={"user_id": user_id, "symbol": symbol, "qty": qty, "strategy_id": "manual"},
            )
            ui.notify("Cannot close position: Kill Switch is ENGAGED", type="negative")
            return

        # Note: Circuit breaker check not needed here - closes are allowed when TRIPPED
        # The backend will validate, but UI should not block risk-reducing actions
    except httpx.HTTPStatusError as e:
        logger.warning(
            "close_position_safety_check_failed",
            extra={"user_id": user_id, "symbol": symbol, "qty": qty, "status": e.response.status_code, "strategy_id": "manual"},
        )
        ui.notify(f"Safety check failed: HTTP {e.response.status_code}", type="negative")
        return
    except httpx.RequestError as e:
        logger.warning(
            "close_position_safety_check_failed",
            extra={"user_id": user_id, "symbol": symbol, "qty": qty, "error": type(e).__name__, "strategy_id": "manual"},
        )
        ui.notify("Cannot reach safety service - try again", type="negative")
        return

    # Rev 8: Generate dialog nonce when dialog opens (for idempotent order ID)
    # This allows retries within the same dialog to use the same order ID,
    # while new dialog opens get new IDs (supporting sequential trading)
    dialog_nonce = uuid.uuid4().hex  # Full 32-char hex for collision resistance

    # Show confirmation dialog
    with ui.dialog() as dialog, ui.card().classes("p-4"):
        ui.label(f"Close {symbol} position?").classes("text-lg font-bold")
        ui.label(f"Quantity: {qty} shares")

        # Rev 16: In-flight guard to prevent duplicate submissions (per Codex review)
        submitting = False
        confirm_button = None  # Reference for enabling/disabling

        with ui.row().classes("gap-4 mt-4"):
            async def confirm():
                nonlocal submitting
                if submitting:
                    return  # Already in flight
                submitting = True
                if confirm_button:
                    confirm_button.disable()

                # Re-check kill switch at submission time (double-check pattern)
                # Fail-closed: if we can't verify, block the close for safety
                try:
                    ks = await client.fetch_kill_switch_status(user_id, role=user_role)
                    if ks.get("state") == "ENGAGED":
                        ui.notify("Order blocked: Kill Switch engaged", type="negative")
                        dialog.close()
                        return
                except httpx.HTTPStatusError as e:
                    logger.warning(
                        "close_confirm_kill_switch_check_failed",
                        extra={"user_id": user_id, "symbol": symbol, "status": e.response.status_code, "strategy_id": "manual"},
                    )
                    ui.notify("Cannot verify safety status - order blocked", type="negative")
                    dialog.close()
                    return
                except httpx.RequestError as e:
                    logger.warning(
                        "close_confirm_kill_switch_check_failed",
                        extra={"user_id": user_id, "symbol": symbol, "error": type(e).__name__, "strategy_id": "manual"},
                    )
                    ui.notify("Cannot reach safety service - order blocked", type="negative")
                    dialog.close()
                    return

                # Generate idempotent client_order_id using dialog nonce
                order_id = generate_close_order_id(symbol, qty, dialog_nonce, user_id)

                try:
                    # Rev 15: Add reduce_only to prevent flipping exposure (per Codex review)
                    # If position changed between dialog open and submit, reduce_only ensures
                    # we don't accidentally open new exposure in the opposite direction
                    # Rev 18j: Pass user_id/role for auth context (per Codex review - HIGH)
                    await client.submit_order(
                        {
                            "symbol": symbol,
                            "qty": abs(qty),
                            "side": "sell" if qty > 0 else "buy",
                            "type": "market",
                            "client_order_id": order_id,
                            "reduce_only": True,  # CRITICAL: Prevents position flip
                            "reason": f"Manual close via dashboard",
                        },
                        user_id=user_id,
                        role=user_role,
                    )
                    logger.info(
                        "close_position_submitted",
                        extra={"user_id": user_id, "symbol": symbol, "qty": qty, "client_order_id": order_id, "strategy_id": "manual"},
                    )
                    ui.notify(f"Closing {symbol} position", type="positive")
                    dialog.close()
                except httpx.HTTPStatusError as e:
                    logger.warning(
                        "close_position_submit_failed",
                        extra={"user_id": user_id, "symbol": symbol, "client_order_id": order_id, "status": e.response.status_code, "strategy_id": "manual"},
                    )
                    ui.notify(f"Close failed: HTTP {e.response.status_code}", type="negative")
                    # Keep dialog open so user can retry with same order_id (idempotent)
                except httpx.RequestError as e:
                    logger.warning(
                        "close_position_submit_failed",
                        extra={"user_id": user_id, "symbol": symbol, "client_order_id": order_id, "error": type(e).__name__, "strategy_id": "manual"},
                    )
                    ui.notify("Close failed: network error - please retry", type="negative")
                    # Keep dialog open so user can retry with same order_id (idempotent)
                finally:
                    # Re-enable button on any error (except success which closes dialog)
                    submitting = False
                    if confirm_button:
                        confirm_button.enable()

            confirm_button = ui.button("Confirm", on_click=confirm).classes("bg-red-600 text-white")
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
import logging

from nicegui import ui

logger = logging.getLogger(__name__)


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
            // Rev 17: Use null/undefined check instead of falsy to allow price=0 (per Codex review)
            "valueFormatter": "x => (x.value !== null && x.value !== undefined) ? '$' + x.value.toFixed(2) : 'MKT'",
        },
        {
            "field": "status",
            "headerName": "Status",
            "cellRenderer": "statusBadgeRenderer",  # Custom renderer
            "width": 100,
        },
        {
            "field": "created_at",
            "headerName": "Time (UTC)",
            # Rev 15: Use UTC timezone per domain requirements (per Codex review)
            "valueFormatter": "x => new Date(x.value).toLocaleTimeString('en-US', {timeZone: 'UTC', hour12: false})",
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
        # Capture API for trading_state_change refresh (per Codex review)
        "onGridReady": "params => { window._ordersGridApi = params.api; }",
    }).classes("w-full")


async def update_orders_table(
    grid: ui.aggrid,
    orders: list[dict],
    previous_order_ids: set[str] | None = None,
    notified_missing_ids: set[str] | None = None,
    synthetic_id_map: dict[str, str] | None = None,
) -> set[str]:
    """
    Update orders grid using AG Grid's applyTransaction.

    Uses getRowId (configured as 'data => data.client_order_id') for efficient updates:
    - Adds newly created orders
    - Updates existing orders (status changes, partial fills)
    - Removes filled/cancelled orders no longer in snapshot
    - Preserves scroll position and row selection

    Args:
        grid: The AG Grid instance
        orders: Current open orders snapshot from backend
        previous_order_ids: Set of order IDs from previous update (for add/remove detection)
        notified_missing_ids: Set of synthetic IDs already notified (mutated in place to dedupe)
        synthetic_id_map: Dict mapping order fingerprints to stable synthetic IDs (mutated in place)

    Returns:
        Set of current order IDs (pass to next update for add/remove detection)

    Note:
        Rev 17: Added notified_missing_ids to prevent spamming users with persistent
        notifications for the same missing-ID orders on every update cycle.
        Rev 18: Added synthetic_id_map for stable ID assignment across refreshes.
        Rev 18g: Copy orders before UI mutation to prevent metadata leakage (per Codex review - LOW)
    """
    # Rev 18g: Copy orders before augmenting with UI-only fields to prevent metadata leakage
    # If original dicts are reused (cached, logged, etc.), UI fields like _broker_order_id
    # and _missing_* flags would leak into other contexts
    orders = [order.copy() for order in orders]

    # Handle orders missing client_order_id - use broker order_id as fallback
    # This ensures all active orders are visible to prevent hidden exposure (per Codex review)
    # IMPORTANT: Preserve broker_order_id separately for cancel operations (per Codex review)
    for order in orders:
        # Always preserve broker order ID for cancel fallback
        broker_id = order.get("id") or order.get("order_id")
        if broker_id:
            order["_broker_order_id"] = broker_id

        if not order.get("client_order_id"):
            if broker_id:
                # Rev 18g: Use reserved prefix to prevent collision (per Codex review - MEDIUM)
                # "__ng_fallback_" is a reserved UI-only prefix that the backend never issues
                order["client_order_id"] = f"__ng_fallback_{broker_id}"
                order["_missing_client_order_id"] = True  # Flag for UI warning badge
                logger.warning(
                    "order_missing_client_order_id_using_fallback",
                    extra={"broker_order_id": broker_id, "symbol": order.get("symbol")},
                )
            else:
                # Rev 16: Create synthetic ID from available fields to prevent hidden exposure (per Codex review)
                # Rev 18: Use stable per-session map instead of UUID to prevent row churn (per Codex review)
                # Rev 18b: Use only STABLE fields in fingerprint (per Codex review - MEDIUM)
                # Rev 18c: Track multiple orders per fingerprint to prevent collapse (per Codex review - MEDIUM)
                import hashlib
                # Create deterministic fingerprint from STABLE order fields only
                # symbol + side + created_at are immutable for the order's lifetime
                fingerprint_fields = [
                    order.get("symbol", ""),
                    order.get("side", ""),
                    order.get("created_at", ""),
                    # account_id if available for multi-account disambiguation
                    order.get("account_id", ""),
                ]
                fingerprint = "|".join(fingerprint_fields)

                # Rev 18c: Use extended fingerprint with mutable fields for distinct order detection
                # This allows us to detect if the same base fingerprint represents different orders
                extended_fields = fingerprint_fields + [
                    str(order.get("qty", "")),
                    order.get("type", ""),
                    str(order.get("limit_price", "")),
                    order.get("status", ""),
                ]
                extended_fingerprint = "|".join(extended_fields)

                # Check if we've already assigned a stable ID for this EXTENDED fingerprint
                # This ensures distinct orders (even with same stable fields) get unique IDs
                if synthetic_id_map is not None and extended_fingerprint in synthetic_id_map:
                    synthetic_id = synthetic_id_map[extended_fingerprint]
                else:
                    # Generate new synthetic ID with collision handling
                    base_hash = hashlib.sha256(fingerprint.encode()).hexdigest()[:12]
                    synthetic_id = f"unknown_{base_hash}"

                    # Handle collisions by adding suffix if ID already used
                    if synthetic_id_map is not None:
                        existing_ids = set(synthetic_id_map.values())
                        suffix = 0
                        while synthetic_id in existing_ids:
                            suffix += 1
                            synthetic_id = f"unknown_{base_hash}_{suffix}"
                        # Store with extended fingerprint as key for future lookups
                        synthetic_id_map[extended_fingerprint] = synthetic_id
                order["client_order_id"] = synthetic_id
                order["_missing_all_ids"] = True  # Critical warning - cancel disabled
                order["_missing_client_order_id"] = True
                logger.error(
                    "order_missing_all_ids_using_synthetic",
                    extra={"symbol": order.get("symbol"), "side": order.get("side"), "synthetic_id": synthetic_id},
                )
                # Rev 17: Dedupe notifications - only alert once per synthetic ID per session
                if notified_missing_ids is not None:
                    if synthetic_id not in notified_missing_ids:
                        notified_missing_ids.add(synthetic_id)
                        ui.notify(
                            f"⚠️ Order for {order.get('symbol', 'unknown')} has no ID - contact support",
                            type="negative",
                            timeout=0  # Don't auto-dismiss
                        )
                else:
                    # No tracking set provided - always notify (backwards compatible)
                    ui.notify(
                        f"⚠️ Order for {order.get('symbol', 'unknown')} has no ID - contact support",
                        type="negative",
                        timeout=0  # Don't auto-dismiss
                    )

    valid_orders = [o for o in orders if o.get("client_order_id")]
    current_ids = {o["client_order_id"] for o in valid_orders}

    if previous_order_ids is None:
        # First load - use setRowData for initial population
        await grid.run_grid_method('api.setRowData', valid_orders)
        return current_ids

    # Compute added orders (new since last update)
    added_orders = [o for o in valid_orders if o["client_order_id"] not in previous_order_ids]

    # Compute updated orders (existed before, still exist)
    updated_orders = [o for o in valid_orders if o["client_order_id"] in previous_order_ids]

    # Compute removed orders (filled, cancelled, or expired)
    removed_orders = [
        {"client_order_id": oid} for oid in (previous_order_ids - current_ids)
    ]

    # Use applyTransaction for efficient partial updates
    await grid.run_grid_method('api.applyTransaction', {
        'add': added_orders,
        'update': updated_orders,
        'remove': removed_orders,
    })

    return current_ids


# Rev 6 (Updated): AG Grid Custom Renderer Registration - CSP Compliant (per Codex review)
# NiceGUI ui.aggrid requires JS component registration for custom renderers
# IMPORTANT: Use external static JS file instead of inline script to comply with CSP
#
# Note on ADR: This is a standard NiceGUI pattern for custom components (not a novel
# architectural decision). The CSP-compliance refinement follows existing NiceGUI
# documentation and doesn't introduce new frameworks or cross-service dependencies.
# No ADR required per STANDARDS/ADR_GUIDE.md (implementation detail, not architectural change).
#
# File: apps/web_console_ng/static/js/aggrid_renderers.js
# Register in main.py: app.add_static_files('/static', 'apps/web_console_ng/static')
#
# apps/web_console_ng/components/aggrid_renderers.py
def register_aggrid_renderers():
    """Register custom AG Grid cell renderers via external JS file.

    CSP-compliant: Loads external script instead of inline <script> tag.
    Call this in main.py during app startup.

    Usage in main.py:
        from apps.web_console_ng.components.aggrid_renderers import register_aggrid_renderers
        app.add_static_files('/static', 'apps/web_console_ng/static')
        ui.add_head_html(register_aggrid_renderers())
    """
    # External script reference - CSP compliant (no inline JS)
    return '<script src="/static/js/aggrid_renderers.js"></script>'


# External JS file to create:
# apps/web_console_ng/static/js/aggrid_renderers.js
# Contents:
"""
// AG Grid custom renderers for NiceGUI trading console
// Loaded as external script for CSP compliance
// Rev 9: Updated for CSP-safe event-based state updates (per Codex iteration 4)

// Global trading state - controls button enable/disable
// Rev 10: Corrected policy for cancels/closes (per Codex iteration 5)
window._tradingState = {
    killSwitchEngaged: false,
    circuitBreakerTripped: false
};

// CSP-safe: Listen for state updates via custom events (no inline JS execution)
// Server dispatches 'trading_state_change' events with updated state
window.addEventListener('trading_state_change', function(event) {
    const detail = event.detail || {};
    if ('killSwitch' in detail) {
        window._tradingState.killSwitchEngaged = detail.killSwitch;
    }
    if ('circuitBreaker' in detail) {
        window._tradingState.circuitBreakerTripped = detail.circuitBreaker;
    }
    // Trigger grid refresh to update button states
    if (window._positionsGridApi) window._positionsGridApi.refreshCells();
    if (window._ordersGridApi) window._ordersGridApi.refreshCells();
});

// Trading Safety Policy (AUTHORITATIVE):
// - Kill Switch ENGAGED: Block new entries AND closes (emergency stop-all)
// - Circuit Breaker TRIPPED: Block new entries, ALLOW closes (risk-reducing)
// - Cancels: ALWAYS allowed regardless of state (only reduce exposure)
//
// Rationale: Cancels never increase risk, so always permitted.
// Kill switch = emergency stop - only cancels allowed.
// Circuit breaker = protective trip - cancels AND closes allowed.

function isClosePositionDisabled() {
    // Only kill switch blocks closes (they reduce risk)
    // Circuit breaker TRIPPED still allows risk-reducing exits
    return window._tradingState.killSwitchEngaged;
}

function isCancelOrderDisabled() {
    // Cancels are ALWAYS allowed - they reduce risk
    // Neither kill switch nor circuit breaker blocks cancels
    return false;
}

function isNewEntryDisabled() {
    // Both kill switch and circuit breaker block new entries
    return window._tradingState.killSwitchEngaged ||
           window._tradingState.circuitBreakerTripped;
}

// Status badge renderer for order status
// Rev 16: Also show warning icon for orders with missing client_order_id (per Codex review)
// Rev 18: Escape status text to prevent XSS (per Codex review - MEDIUM)
window.statusBadgeRenderer = function(params) {
    const colors = {
        'pending': 'bg-yellow-100 text-yellow-800',
        'new': 'bg-blue-100 text-blue-800',
        'partial': 'bg-orange-100 text-orange-800',
        'filled': 'bg-green-100 text-green-800',
        'cancelled': 'bg-gray-100 text-gray-800',
        'rejected': 'bg-red-100 text-red-800',
    };
    const colorClass = colors[params.value?.toLowerCase()] || 'bg-gray-100';
    // Rev 18: Sanitize status text to prevent XSS - escape HTML special chars
    const rawStatus = params.value || '';
    const escapedStatus = rawStatus
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    const statusBadge = '<span class="px-2 py-0.5 rounded text-xs ' + colorClass + '">' +
           escapedStatus + '</span>';
    // Rev 16: Show critical warning for orders missing ALL IDs (per Codex review)
    let warningBadge = '';
    if (params.data?._missing_all_ids) {
        warningBadge = ' <span class="text-red-600 font-bold" title="CRITICAL: Order has no valid ID - cancel disabled, contact support">🚫</span>';
    } else if (params.data?._missing_client_order_id) {
        warningBadge = ' <span class="text-yellow-600" title="Order using broker ID (missing client_order_id)">⚠️</span>';
    }
    return statusBadge + warningBadge;
};

// Cancel button renderer - emits custom event for NiceGUI handler
// Cancels ALWAYS allowed (risk-reducing) per trading safety policy
// Rev 16: Disable for orders with _missing_all_ids (no valid ID to cancel with)
window.cancelButtonRenderer = function(params) {
    // Guard against missing data during row teardown/initialization
    if (!params.data) return document.createElement('span');

    const btn = document.createElement('button');
    // Disable if: (1) global cancel disabled OR (2) order has no valid ID
    const disabled = isCancelOrderDisabled() || params.data?._missing_all_ids;
    btn.className = disabled
        ? 'px-2 py-1 text-xs bg-gray-400 text-gray-200 rounded cursor-not-allowed'
        : 'px-2 py-1 text-xs bg-red-500 text-white rounded hover:bg-red-600';
    btn.textContent = 'Cancel';
    btn.disabled = disabled;
    if (!disabled) {
        btn.onclick = function() {
            // Include broker_order_id for orders with synthetic client_order_id (per Codex review)
            window.dispatchEvent(new CustomEvent('cancel_order', {
                detail: {
                    client_order_id: params.data?.client_order_id || '',
                    symbol: params.data?.symbol || '',
                    broker_order_id: params.data?._broker_order_id || ''
                }
            }));
        };
    }
    return btn;
};

// Close position button renderer for positions grid
// Only kill switch blocks closes (circuit breaker allows risk-reducing exits)
window.closePositionRenderer = function(params) {
    // Guard against missing data during row teardown/initialization
    if (!params.data) return document.createElement('span');

    const btn = document.createElement('button');
    const disabled = isClosePositionDisabled();  // Only true when kill switch engaged
    btn.className = disabled
        ? 'px-2 py-1 text-xs bg-gray-400 text-gray-200 rounded cursor-not-allowed'
        : 'px-2 py-1 text-xs bg-red-500 text-white rounded hover:bg-red-600';
    btn.textContent = 'Close';
    btn.disabled = disabled;
    if (!disabled) {
        btn.onclick = function() {
            window.dispatchEvent(new CustomEvent('close_position', {
                detail: {
                    symbol: params.data?.symbol || '',
                    qty: params.data?.qty || 0
                }
            }));
        };
    }
    return btn;
};
"""


# Rev 18: Static CSS for animations (per Codex review - LOW)
# File: apps/web_console_ng/static/css/custom.css
# Register in main.py: app.add_static_files('/static', 'apps/web_console_ng/static')
# Include via: ui.add_head_html('<link rel="stylesheet" href="/static/css/custom.css">')
"""
/* Fade highlight animation for activity feed new items */
@keyframes fadeHighlight {
    from {
        background-color: rgb(191, 219, 254); /* bg-blue-100 */
    }
    to {
        background-color: transparent;
    }
}

/* Animation class for Tailwind arbitrary animation syntax */
/* Usage: animate-[fadeHighlight_2s_ease-out_forwards] */
"""


# apps/web_console_ng/components/activity_feed.py
import logging
from collections import deque

from nicegui import ui

logger = logging.getLogger(__name__)


class ActivityFeed:
    """Real-time activity feed with auto-scroll.

    Rev 6: Added auto-scroll behavior per Codex review.
    - New items appear at top with highlight animation
    - Container scrolls to top automatically when new item added
    - Uses CSS animation for visual feedback
    """

    MAX_ITEMS = 20
    NEW_ITEM_HIGHLIGHT_DURATION = 2.0  # seconds

    def __init__(self):
        self.items: deque = deque(maxlen=self.MAX_ITEMS)
        self._container = None

        with ui.card().classes("w-full h-64 overflow-y-auto") as card:
            self._container = card
            ui.label("Recent Activity").classes("text-lg font-bold mb-2")
            self._items_column = ui.column().classes("w-full gap-1")

    async def add_item(self, event: dict) -> None:
        """Add new item to feed (appears at top with animation).

        Rev 6: Made async and added auto-scroll + highlight behavior.
        - Scrolls container to top to show new item
        - Highlights new item briefly for visual feedback
        """
        self.items.appendleft(event)
        self._render_items(highlight_first=True)
        # Auto-scroll container to top to show new item
        await self._scroll_to_top()

    async def _scroll_to_top(self) -> None:
        """Scroll the activity feed container to top."""
        # Use JavaScript to scroll the container element to top
        await self._container.run_method('scrollTo', {'top': 0, 'behavior': 'smooth'})

    def _render_items(self, highlight_first: bool = False) -> None:
        """Re-render all items (newest first)."""
        self._items_column.clear()
        with self._items_column:
            for idx, item in enumerate(self.items):
                # Highlight the first (newest) item if requested
                is_new = highlight_first and idx == 0
                self._render_item(item, highlight=is_new)

    def _render_item(self, event: dict, highlight: bool = False) -> None:
        """Render single activity item.

        Args:
            event: The activity event data
            highlight: If True, add highlight animation for new items (Rev 6)

        Rev 17: Defensive validation - uses get() with defaults to handle
        malformed events gracefully without breaking the feed.
        """
        # Rev 17: Defensive field extraction with safe defaults
        try:
            side = str(event.get("side", "unknown")).lower()
            status = str(event.get("status", "unknown")).lower()
            time_str = str(event.get("time", ""))
            symbol = str(event.get("symbol", "???"))
            qty = event.get("qty", 0)
            price = event.get("price", 0.0)
        except Exception as e:
            logger.warning("activity_feed_malformed_event", extra={"error": str(e), "event": str(event)[:100]})
            return  # Skip malformed event

        side_color = "text-green-600" if side == "buy" else "text-red-600"
        status_color = {
            "filled": "bg-green-100 text-green-800",
            "cancelled": "bg-gray-100 text-gray-800",
            "pending": "bg-yellow-100 text-yellow-800",
        }.get(status, "bg-gray-100")

        # Rev 6: Add highlight animation for new items
        # Rev 16: Use one-shot fade animation instead of infinite pulse (per Codex review)
        row_classes = "w-full items-center gap-2 p-2 hover:bg-gray-50 rounded"
        if highlight:
            # One-shot animation: fade from highlight to transparent over duration
            # Uses Tailwind's animate-[fade] with CSS custom animation defined in static CSS
            row_classes += " bg-blue-100 animate-[fadeHighlight_2s_ease-out_forwards]"

        with ui.row().classes(row_classes):
            # Rev 16: Append " UTC" suffix for timezone clarity (per Codex review)
            # Server emits ISO8601 with Z suffix; we display as-is with explicit label
            time_display = f"{time_str} UTC" if time_str and not time_str.endswith("UTC") else (time_str or "??:??")
            ui.label(time_display).classes("text-xs text-gray-500 w-24")
            ui.label(symbol).classes("font-mono w-16")
            ui.label(side.upper()).classes(f"{side_color} w-12")
            ui.label(str(qty)).classes("w-12 text-right")
            # Safely format price - handle non-numeric values
            try:
                price_display = f"${float(price):.2f}"
            except (TypeError, ValueError):
                price_display = "$?.??"
            ui.label(price_display).classes("w-20 text-right")
            ui.label(status).classes(f"px-2 py-0.5 rounded text-xs {status_color}")
```

**Cancel Order Button:**
```python
async def on_cancel_order(
    client_order_id: str,
    symbol: str,
    user_id: str,
    user_role: str,
    broker_order_id: str | None = None,
):
    """Handle cancel order button click.

    Args:
        client_order_id: The client order ID (may be synthetic "broker_xxx" for broker-only orders)
        symbol: The order symbol
        user_id: The user ID
        user_role: The user's role
        broker_order_id: The broker's order ID (used for cancels when client_order_id is synthetic)

    Role-based gating:
    - viewer: Cannot cancel orders (read-only)
    - trader/admin: Can cancel their own orders

    SECURITY NOTE: Frontend validation is defense-in-depth only.
    Backend MUST validate:
    - Order (by client_order_id or broker_order_id) belongs to this user
    - Order is in a cancellable state
    - User has permission for this symbol/account
    The backend cancel_order endpoint enforces these server-side.
    """
    client = AsyncTradingClient.get()

    # Rev 11: Role-based authorization check
    if user_role == "viewer":
        ui.notify("Viewers cannot cancel orders", type="warning")
        return

    # Log kill switch state for audit (cancels always allowed, just logging)
    try:
        ks_status = await client.fetch_kill_switch_status(user_id, role=user_role)
        if ks_status.get("state") == "ENGAGED":
            logger.info(
                "cancel_order_during_kill_switch",
                extra={"user_id": user_id, "client_order_id": client_order_id, "symbol": symbol, "strategy_id": "manual"},
            )
    except httpx.HTTPStatusError as e:
        logger.warning(
            "cancel_order_safety_check_failed",
            extra={"user_id": user_id, "client_order_id": client_order_id, "status": e.response.status_code, "strategy_id": "manual"},
        )
        # Don't block - cancels always allowed, just log the issue
    except httpx.RequestError as e:
        logger.warning(
            "cancel_order_safety_check_failed",
            extra={"user_id": user_id, "client_order_id": client_order_id, "error": type(e).__name__, "strategy_id": "manual"},
        )
        # Don't block - cancels always allowed, just log the issue

    # Rev 14: Determine which ID to use for cancel (per Codex review)
    # Rev 18g: Use reserved prefix "__ng_fallback_" to detect synthetic IDs (per Codex review - MEDIUM)
    # This prefix is UI-only and guaranteed not to conflict with legitimate client_order_ids
    is_synthetic = client_order_id.startswith("__ng_fallback_")

    # Rev 15: Hard-fail if synthetic but no broker_order_id (per Codex review)
    # Without a valid ID, the cancel will fail server-side anyway - fail early with clear message
    if is_synthetic and not broker_order_id:
        logger.error(
            "cancel_order_missing_broker_id",
            extra={"user_id": user_id, "client_order_id": client_order_id, "symbol": symbol, "strategy_id": "manual"},
        )
        ui.notify("Cannot cancel: order has no valid ID (missing broker_order_id)", type="negative")
        return

    cancel_id = broker_order_id if is_synthetic else client_order_id
    display_id = client_order_id[:8] if not is_synthetic else f"(broker) {broker_order_id[:8]}"

    # In-flight guard to prevent duplicate submissions
    submitting = False
    confirm_button = None

    with ui.dialog() as dialog, ui.card().classes("p-4"):
        ui.label(f"Cancel order for {symbol}?").classes("text-lg font-bold")
        ui.label(f"Order ID: {display_id}...")
        if is_synthetic:
            ui.label("Note: This order has no client_order_id, using broker ID").classes("text-xs text-yellow-600")

        with ui.row().classes("gap-4 mt-4"):
            async def confirm():
                nonlocal submitting
                if submitting:
                    return
                submitting = True
                if confirm_button:
                    confirm_button.disable()
                try:
                    # Use cancel_id (broker_order_id for synthetic orders, client_order_id otherwise)
                    # Rev 18j: Pass user_id/role for auth context (per Codex review - HIGH)
                    await client.cancel_order(
                        cancel_id,
                        use_broker_id=is_synthetic,
                        user_id=user_id,
                        role=user_role,
                    )
                    logger.info(
                        "order_cancelled",
                        extra={
                            "user_id": user_id,
                            "client_order_id": client_order_id,
                            "broker_order_id": broker_order_id,
                            "cancel_id_used": cancel_id,
                            "symbol": symbol,
                            "strategy_id": "manual",
                        },
                    )
                    ui.notify(f"Order cancelled", type="positive")
                except httpx.HTTPStatusError as e:
                    logger.warning(
                        "order_cancel_failed",
                        extra={
                            "user_id": user_id,
                            "client_order_id": client_order_id,
                            "broker_order_id": broker_order_id,
                            "cancel_id_used": cancel_id,
                            "symbol": symbol,
                            "status": e.response.status_code,
                            "strategy_id": "manual",
                        },
                    )
                    ui.notify(f"Cancel failed: HTTP {e.response.status_code}", type="negative")
                except httpx.RequestError as e:
                    logger.warning(
                        "order_cancel_failed",
                        extra={
                            "user_id": user_id,
                            "client_order_id": client_order_id,
                            "broker_order_id": broker_order_id,
                            "cancel_id_used": cancel_id,
                            "symbol": symbol,
                            "error": type(e).__name__,
                            "strategy_id": "manual",
                        },
                    )
                    ui.notify("Cancel failed: network error", type="negative")
                finally:
                    submitting = False
                    if confirm_button:
                        confirm_button.enable()
                dialog.close()

            confirm_button = ui.button("Confirm", on_click=confirm).classes("bg-red-600 text-white")
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
- [ ] Circuit breaker TRIPPED → order buttons disabled, warning banner shown (Rev 9)
- [ ] Circuit breaker OPEN → buttons re-enabled, banner dismissed (Rev 9)

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

**Rev 6: Auth Mode Limitations (per Codex review):**
- Load test script uses `/auth/login` with username/password
- This only works for `dev` and `basic` auth modes
- mTLS requires client certificates (not supported in k6)
- OAuth2 requires browser flow (not supported in k6)
- **Workaround:** Run load tests with `AUTH_TYPE=dev` or `AUTH_TYPE=basic`
- For mTLS/OAuth2 environments, use manual testing or Playwright for auth flow + k6 for authenticated session load

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
  - [ ] `GET /api/v1/kill-switch/status` - Kill switch state (Rev 10: fixed hyphen)
  - [ ] `POST /api/v1/orders/{id}/cancel` - Cancel order
  - [ ] `GET /api/v1/market_prices` - Market prices for polling (Rev 8)
  - [ ] `GET /api/v1/circuit-breaker/status` - Circuit breaker state (Rev 10)
- [ ] **AsyncTradingClient methods required (Rev 11, Rev 18h):**
  - `fetch_positions(user_id: str, *, role: str, strategies: list[str])` - ✅ Exists
  - `fetch_kill_switch_status(user_id: str, *, role: str, strategies: list[str])` - ✅ Exists
  - `engage_kill_switch(user_id: str, *, role: str)` - ✅ Exists
  - `disengage_kill_switch(user_id: str, *, role: str)` - ✅ Exists
  - `fetch_open_orders(user_id: str, *, role: str)` - ❌ Add in C0 implementation
  - `fetch_realtime_pnl(user_id: str)` - ❌ Add in C0 implementation
  - `fetch_market_prices()` - ❌ Add in C0 implementation (no user context needed)
  - `cancel_order(order_id: str, *, use_broker_id: bool = False, user_id: str, role: str)` - ❌ Add in C0 implementation
  - `submit_order(order_data: dict, *, user_id: str, role: str)` - ❌ Add in C0 implementation
  - `fetch_circuit_breaker_status(user_id: str, *, role: str)` - ❌ Add in C0 implementation
- [ ] **AG Grid license:** Verify community edition sufficient, or obtain license
- [ ] **k6 installed:** For load testing

---

## Approach

### High-Level Plan

1. **C0: Real-Time Infrastructure** (2-3 days)
   - RealtimeUpdater class with Redis Pub/Sub
   - Throttle and backpressure implementation
   - Push/poll strategy implementation
   - **Client lifecycle integration in layout.py** (Rev 10)

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

**Files to Create/Modify:**
```
apps/web_console_ng/core/
├── realtime.py                  # RealtimeUpdater class
├── debounce.py                  # Debouncer utility for rate-limiting updates
apps/web_console_ng/ui/
├── layout.py                    # MODIFY: Add client lifecycle hooks (Rev 10)
tests/apps/web_console_ng/
├── test_realtime.py
├── test_debounce.py             # Rev 16: Fixed naming consistency (per Codex review)
└── test_client_lifecycle.py     # Test cleanup on disconnect
```

**Rev 11: Client Lifecycle Integration - USE EXISTING (per Codex iteration 6 - HIGH):**

**⚠️ IMPORTANT:** Do NOT add new lifecycle hooks to layout.py. The client lifecycle is ALREADY handled by `apps/web_console_ng/core/connection_events.py`:

```python
# EXISTING in connection_events.py - DO NOT DUPLICATE in layout.py:
@app.on_connect
async def on_client_connect(client: Client) -> None:
    lifecycle = ClientLifecycleManager.get()
    client_id = lifecycle.generate_client_id()
    client.storage["client_id"] = client_id  # <-- Uses client.storage, NOT app.storage.user
    await lifecycle.register_client(client_id)

@app.on_disconnect
async def on_client_disconnect(client: Client) -> None:
    lifecycle = ClientLifecycleManager.get()
    client_id = client.storage.get("client_id")
    if isinstance(client_id, str):
        await lifecycle.cleanup_client(client_id)
```

**Dashboard pages should:**
1. READ `client_id` from `client.storage` (set by connection_events.py)
2. Register cleanup callbacks with that client_id
3. NOT generate a new client_id or register disconnect handlers

```python
# In dashboard.py - READ existing client_id, don't create new one:
client_id = client.storage.get("client_id")
if not client_id:
    ui.notify("Session error - please refresh", type="negative")
    return

# Register cleanup callbacks with existing client_id
lifecycle = ClientLifecycleManager.get()
await lifecycle.register_cleanup_callback(client_id, cleanup_timers)
await lifecycle.register_cleanup_callback(client_id, realtime.cleanup)
```

**Testing for Client Lifecycle:**
- [ ] client_id is read from client.storage (NOT generated in dashboard)
- [ ] client.storage contains client_id (set by connection_events.py's @app.on_connect)
- [ ] cleanup_client is called by connection_events.py's @app.on_disconnect
- [ ] Dashboard's cleanup callbacks (timers, realtime) are executed on disconnect

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
        # IMPORTANT: Use get_redis_store() for HA/Sentinel support (Rev 6 fix)
        from apps.web_console_ng.core.redis_ha import get_redis_store
        self._redis_store = get_redis_store()
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

        # Register both for cleanup on disconnect (Rev 6: await async methods)
        lifecycle = ClientLifecycleManager.get()
        await lifecycle.register_task(self.client_id, listener_task)
        await lifecycle.register_task(self.client_id, worker_task)

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
                # Use HA-aware Redis client (Rev 6 fix)
                redis_client = await self._redis_store.get_master()
                pubsub = redis_client.pubsub()
                await pubsub.subscribe(channel)
                self.pubsubs[channel] = pubsub

                async for message in pubsub.listen():
                    if message["type"] == "message":
                        # Rev 17: Handle JSON decode errors per-message to avoid unnecessary reconnects
                        try:
                            data = json.loads(message["data"])
                        except json.JSONDecodeError as e:
                            logger.warning(
                                "pubsub_json_decode_error",
                                extra={"channel": channel, "error": str(e)},
                            )
                            continue  # Skip malformed message, don't reconnect

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
        """Cleanup all subscriptions and connections (called on disconnect).

        Rev 8: IMPORTANT - Do NOT close the Redis store here!
        get_redis_store() returns a singleton shared across all users.
        Closing it here would terminate connections for ALL active users.
        The Redis store should only be closed during application shutdown
        (in FastAPI lifespan handler), not during user session cleanup.
        """
        for channel in list(self.subscriptions.keys()):
            await self.unsubscribe(channel)
        # Pubsub connections are cleaned up in unsubscribe() - no further cleanup needed


# Channel names
def position_channel(user_id: str) -> str:
    return f"positions:{user_id}"

def kill_switch_channel() -> str:
    """Global kill switch state channel.

    NOTE: Intentionally NOT namespaced per-user. Kill switch is a SYSTEM-LEVEL
    safety mechanism that applies globally across all accounts. When engaged,
    it blocks all new entries and close positions for the entire platform.

    Access control is enforced at the authentication layer - only authenticated
    users with valid sessions can subscribe to this channel via RealtimeUpdater.
    """
    return "kill_switch:state"

def circuit_breaker_channel() -> str:
    """Global circuit breaker state channel.

    NOTE: Intentionally NOT namespaced per-user. Circuit breaker is a SYSTEM-LEVEL
    safety mechanism that monitors drawdown, volatility, and system health.
    When tripped, it blocks new entries for the entire platform.

    Access control is enforced at the authentication layer - only authenticated
    users with valid sessions can subscribe to this channel via RealtimeUpdater.
    """
    return "circuit_breaker:state"

# Rev 6: Added channels for orders/fills per Codex review
def orders_channel(user_id: str) -> str:
    """Channel for order status updates (new, partial, filled, cancelled)."""
    return f"orders:{user_id}"

def fills_channel(user_id: str) -> str:
    """Channel for fill/execution events (for activity feed)."""
    return f"fills:{user_id}"
```

**Debounce Utility (Rev 11: clarified - separate from RealtimeUpdater's throttle):**
```python
# apps/web_console_ng/core/debounce.py
# NOTE: This is a DEBOUNCE utility for UI updates (wait for quiet period).
# The RealtimeUpdater uses THROTTLE (limit rate, send immediately).
# These are different patterns:
# - Debounce: Wait until activity stops (used for search inputs, resize events)
# - Throttle: Send immediately, then rate-limit (used for real-time streams)
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
        # Rev 18: Removed unused _last_call field (per Codex review)

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
import asyncio
import json

from nicegui import ui, Client
from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng.core.realtime import (
    RealtimeUpdater, position_channel, kill_switch_channel, circuit_breaker_channel,
    orders_channel, fills_channel
)
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console_ng.auth.middleware import requires_auth
from apps.web_console_ng.components.metric_card import MetricCard
from apps.web_console_ng.components.positions_grid import create_positions_grid, update_positions_grid
from apps.web_console_ng.components.orders_table import create_orders_table, update_orders_table
from apps.web_console_ng.components.activity_feed import ActivityFeed
import time


# Rev 18: Module-level cache for market prices to prevent N× duplicate backend calls
class MarketPriceCache:
    """Shared cache for market prices across all client sessions.

    Market prices are not user-specific, so all connected clients can share
    the same cached data. This reduces backend load from N calls (one per client)
    to ~1 call per TTL period.

    Thread-safety: Uses asyncio.Lock for concurrent access protection.

    IMPORTANT - Entitlement Assumption (Rev 18i):
    ---------------------------------------------
    This global cache ASSUMES market prices are entitlement-neutral - i.e., the
    `/api/v1/market_prices` endpoint returns the same data for all authenticated
    users regardless of account, subscription tier, or exchange entitlements.

    If market data is entitlement-based (e.g., some users see delayed data,
    different symbol sets, or different exchange feeds), this shared cache will
    LEAK data across users. In that case:
    1. Key cache by user_id or entitlement_group
    2. Use separate cache instances per entitlement tier
    3. Move price fetching to per-client context

    For this implementation, market prices are assumed to be publicly available
    real-time quotes from the broker (Alpaca), which are the same for all users.

    IMPORTANT - Scaling Considerations:
    -----------------------------------
    This in-memory cache assumes single-process deployment (workers=1).

    For multi-worker production deployments:
    1. Move cache to Redis: Use SETEX with TTL for atomic cache entries
    2. Use server-side broadcast: Single poller publishes to Redis Pub/Sub,
       all workers subscribe and forward to their clients
    3. Use a shared service: Dedicated market-data service with its own cache

    Current implementation is suitable for:
    - Development/testing
    - Single-worker production deployments
    - Initial rollout before scaling

    P5T3 HA configuration uses workers=1 for NiceGUI, so this cache is valid.
    If multi-worker is needed, implement Redis-based caching before scaling.
    """

    _prices: list[dict] = []
    _last_fetch: float = 0.0
    _last_error: float = 0.0  # Rev 18k: Track last error for backoff
    _ttl: float = 4.0  # seconds (slightly less than 5s poll interval)
    _error_cooldown: float = 10.0  # Rev 18k: Backoff on errors to prevent thundering herd
    _lock = asyncio.Lock()

    @classmethod
    async def get_prices(cls, client: AsyncTradingClient) -> list[dict]:
        """Get market prices, using cache if fresh.

        Rev 18k: Returns a copy to prevent cross-session mutation.
        Rev 18k: Implements failure backoff to prevent thundering herd on outages.
        """
        async with cls._lock:
            now = time.time()

            # Check if still in error cooldown
            if cls._last_error and (now - cls._last_error) < cls._error_cooldown:
                # Return stale cache during cooldown to prevent thundering herd
                return list(cls._prices) if cls._prices else []

            if (now - cls._last_fetch) < cls._ttl and cls._prices:
                return list(cls._prices)  # Return copy to prevent mutation

            # Cache miss or expired - fetch from backend
            try:
                cls._prices = await client.fetch_market_prices()
                cls._last_fetch = now
                cls._last_error = 0.0  # Clear error state on success
                return list(cls._prices)  # Return copy to prevent mutation
            except Exception:
                cls._last_error = now  # Set error timestamp for backoff
                # Return stale cache if available
                return list(cls._prices) if cls._prices else []


@ui.page("/")
@requires_auth
@main_layout
async def dashboard(client: Client) -> None:
    """Main trading dashboard with real-time updates.

    Rev 9: Client ID Lifecycle (per Codex iteration 4):
    Rev 11: Corrected to use connection_events.py pattern (per Codex iteration 6):
    -------------------------------------------------
    The `client_id` is critical for tracking resources per browser session.

    Lifecycle (handled by apps/web_console_ng/core/connection_events.py):
    1. CREATION: Generated in @app.on_connect handler (NOT in layout or dashboard)
       - `client_id = ClientLifecycleManager.get().generate_client_id()`
       - Stored in `client.storage["client_id"]` (per-WebSocket storage)
    2. REGISTRATION: Called in @app.on_connect before page renders
       - `await ClientLifecycleManager.get().register_client(client_id)`
    3. USAGE: Retrieved here via `client.storage["client_id"]` for subscriptions
    4. CLEANUP: Triggered by @app.on_disconnect in connection_events.py
       - `await ClientLifecycleManager.get().cleanup_client(client_id)`

    IMPORTANT: Do NOT generate client_id in dashboard or layout - it's already
    handled by connection_events.py. Just READ it from client.storage.
    """
    trading_client = AsyncTradingClient.get()
    user_id = get_current_user_id()
    user_role = get_current_user_role()  # Rev 11: Need role for safety checks

    # Rev 11: Read client_id from client.storage (set by connection_events.py)
    # NOT app.storage.user - that's session storage, not per-WebSocket storage
    client_id = client.storage.get("client_id")
    if not client_id:
        ui.notify("Session error - please refresh", type="negative")
        return

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

    # Track symbols/IDs for remove detection (closed positions, completed orders)
    position_symbols: set[str] = set()
    order_ids: set[str] = set()  # Rev 6: Track order IDs for applyTransaction
    # Rev 17: Track notified missing-ID orders to prevent notification spam
    notified_missing_ids: set[str] = set()
    # Rev 18: Stable per-session map for synthetic order IDs (fingerprint -> ID)
    synthetic_id_map: dict[str, str] = {}
    # Rev 14: Lock to serialize grid updates between push and reconcile (per Codex review)
    # Prevents race conditions where slower reconcile could revert to stale state
    grid_update_lock = asyncio.Lock()
    # Rev 18f: Track safety state for notification deduplication (per Codex review - LOW)
    # Only notify on state transitions, not on every push update
    last_kill_switch_engaged: bool | None = None
    last_circuit_breaker_tripped: bool | None = None

    # ===== Initial Data Load =====
    async def load_initial_data():
        nonlocal position_symbols, order_ids
        try:
            # Fetch all data in parallel
            # Rev 18j: Pass role/strategies for auth context (per Codex review - MEDIUM)
            pnl_data, positions, orders = await asyncio.gather(
                trading_client.fetch_realtime_pnl(user_id),
                trading_client.fetch_positions(user_id, role=user_role, strategies=user_strategies),
                trading_client.fetch_open_orders(user_id, role=user_role),
            )

            # Update UI
            pnl_card.update(pnl_data.get("total_unrealized_pl", 0))
            positions_card.update(pnl_data.get("total_positions", 0))
            realized_card.update(pnl_data.get("realized_pl_today", 0))
            bp_card.update(pnl_data.get("buying_power", 0))

            # Initial load: no previous symbols/IDs, returns current sets
            position_symbols = await update_positions_grid(
                positions_grid, positions.get("positions", [])
            )
            # Rev 6: Use applyTransaction for orders too (per Codex review)
            # Rev 17: Pass notified_missing_ids for notification deduplication
            # Rev 18: Pass synthetic_id_map for stable synthetic IDs
            order_ids = await update_orders_table(
                orders_table, orders.get("orders", []),
                notified_missing_ids=notified_missing_ids,
                synthetic_id_map=synthetic_id_map,
            )

        except httpx.HTTPStatusError as e:
            logger.exception(
                "load_initial_data_failed",
                extra={"client_id": client_id, "user_id": user_id, "status": e.response.status_code},
            )
            ui.notify(f"Failed to load dashboard: HTTP {e.response.status_code}", type="negative")
        except httpx.RequestError as e:
            logger.exception(
                "load_initial_data_failed",
                extra={"client_id": client_id, "user_id": user_id, "error": type(e).__name__},
            )
            ui.notify("Failed to load dashboard: network error", type="negative")

    await load_initial_data()

    # ===== Real-Time Subscriptions =====
    async def on_position_update(data: dict):
        """Handle real-time position update.

        Rev 15: Uses grid_update_lock to serialize with reconcile (per Codex review).
        Rev 16: Also update buying power if present (per Codex review).
        Rev 17: Only update cards when fields exist - prevents incorrect zeroing on
        partial updates (per Codex review).
        """
        nonlocal position_symbols
        # Only update metrics when the field is present in the payload
        # Prevents incorrect zeroing on partial updates (e.g., positions-only)
        if "total_unrealized_pl" in data:
            pnl_card.update(data["total_unrealized_pl"])
        if "total_positions" in data:
            positions_card.update(data["total_positions"])
        if "realized_pl_today" in data:
            realized_card.update(data["realized_pl_today"])
        if "buying_power" in data:
            bp_card.update(data["buying_power"])
        # Rev 17: Only update grid when positions key is present (full snapshot)
        # Prevents wiping the grid on partial updates (e.g., metrics-only payloads)
        if "positions" in data:
            async with grid_update_lock:
                position_symbols = await update_positions_grid(
                    positions_grid, data["positions"], position_symbols
                )

        # Add to activity feed if there's a new event (Rev 6: await async method)
        if "event" in data:
            await activity_feed.add_item(data["event"])

    async def on_kill_switch_update(data: dict):
        """Handle kill switch state change - critical safety update.

        Rev 8: Update JS global state to disable action buttons (per Codex iteration 3).
        Rev 9: Use CSP-safe custom event dispatch (per Codex iteration 4).
        Rev 11: Sync header badge with real-time update (per Codex iteration 6 - MEDIUM).
        Rev 18: Fail-closed - treat missing/unknown state as restrictive (per Codex review - HIGH)
        Rev 18f: Only notify on state transition (per Codex review - LOW)
        """
        nonlocal last_kill_switch_engaged
        raw_state = data.get("state")
        if raw_state is None or raw_state not in ("ENGAGED", "DISENGAGED"):
            # Rev 18: Fail-closed - treat unknown state as ENGAGED for safety
            logger.warning(
                "kill_switch_update_malformed_state",
                extra={"raw_state": raw_state, "defaulting_to": "ENGAGED"},
            )
            state = "ENGAGED"
        else:
            state = raw_state
        engaged = state == "ENGAGED"

        # Rev 9: Use custom event dispatch (CSP-safe) instead of inline JS execution
        # The static aggrid_renderers.js listens for this event
        # Rev 11 (MEDIUM-3): CSP Note - NiceGUI's run_javascript uses WebSocket messages
        # to execute JS in the client context, which bypasses script-src restrictions.
        # This is acceptable because:
        # 1. The JS is minimal (dispatchEvent with literal values)
        # 2. No user input is interpolated (engaged is a boolean from trusted backend)
        # 3. The event listener in aggrid_renderers.js validates the payload structure
        await client.run_javascript(
            f"window.dispatchEvent(new CustomEvent('trading_state_change', "
            f"{{detail: {{killSwitch: {str(engaged).lower()}}}}}))"
        )

        # Rev 11: Sync header badge with push update (complements layout.py's 5s poll)
        # The header badge (kill_switch_badge) is defined in layout.py and has its own
        # timer-based updater. To sync with real-time push, emit a custom event that
        # layout.py can optionally listen to, or just let the 5s poll catch up.
        # For immediate sync, the dashboard page should store a reference to the badge.
        # NOTE: This is handled by the layout.py timer - push is faster, but both converge.

        # Rev 18f: Only notify on state transition to prevent spam (per Codex review - LOW)
        if engaged and last_kill_switch_engaged is not True:
            ui.notify("Kill Switch ENGAGED - Trading disabled", type="warning")
        last_kill_switch_engaged = engaged

    # Rev 9: Circuit breaker handler with UI enforcement (per Codex iteration 4)
    # Rev 11: Fixed comment to match trading safety policy (per Codex iteration 6 - HIGH)
    async def on_circuit_breaker_update(data: dict):
        """Handle circuit breaker state change - safety critical.

        Circuit breaker states:
        - OPEN: Normal operation, all trading allowed
        - TRIPPED: Triggered by drawdown/error - block NEW ENTRIES only, allow exits

        UI enforcement:
        - Show prominent warning banner when TRIPPED
        - Keep "Close Position" buttons ENABLED (closes reduce risk, always allowed)
        - Keep "Cancel Order" buttons ENABLED (cancels reduce risk, always allowed)
        - Disable "New Order" / "Buy" / "Sell" buttons (new entries blocked)
        - Show read-only indicator for new entry forms

        Rev 18: Fail-closed - treat missing/unknown state as restrictive (per Codex review - HIGH)
        Rev 18f: Only notify on state transition (per Codex review - LOW)
        """
        nonlocal last_circuit_breaker_tripped
        raw_state = data.get("state")
        if raw_state is None or raw_state not in ("OPEN", "TRIPPED"):
            # Rev 18: Fail-closed - treat unknown state as TRIPPED for safety
            logger.warning(
                "circuit_breaker_update_malformed_state",
                extra={"raw_state": raw_state, "defaulting_to": "TRIPPED"},
            )
            state = "TRIPPED"
        else:
            state = raw_state
        tripped = state == "TRIPPED"

        # Update JS global state for button disable (shares mechanism with kill switch)
        await client.run_javascript(
            f"window.dispatchEvent(new CustomEvent('trading_state_change', "
            f"{{detail: {{circuitBreaker: {str(tripped).lower()}}}}}))"
        )

        # Rev 18f: Only notify on state transition to prevent spam (per Codex review - LOW)
        if tripped and last_circuit_breaker_tripped is not True:
            ui.notify(
                "CIRCUIT BREAKER TRIPPED - New entries blocked, exits only",
                type="negative",
                timeout=0  # Persistent until dismissed
            )
        elif not tripped and last_circuit_breaker_tripped is True:
            # CB returned to OPEN - notify only when transitioning from TRIPPED
            ui.notify(
                "Circuit breaker reset - normal trading resumed",
                type="positive",
                timeout=5000  # Auto-dismiss after 5s
            )
        last_circuit_breaker_tripped = tripped

    # Add order updates handler
    async def on_orders_update(data: dict):
        """Handle real-time order status updates.

        Rev 14: Uses grid_update_lock to serialize with reconcile (per Codex review).
        Rev 17: Only update table when orders key is present (full snapshot).
        """
        nonlocal order_ids
        # Only update table when orders key is present (full snapshot)
        # Prevents wiping the table on delta-only payloads (e.g., fill events)
        if "orders" in data:
            async with grid_update_lock:
                order_ids = await update_orders_table(
                    orders_table, data["orders"], order_ids, notified_missing_ids, synthetic_id_map
                )

        # Rev 18d: Removed fill handling here to prevent double-posts (per Codex review - MEDIUM)
        # Fills are handled exclusively by on_fill_event via the fills channel
        # If both channels emitted fills, users would see duplicates in the activity feed

    await realtime.subscribe(position_channel(user_id), on_position_update)
    await realtime.subscribe(kill_switch_channel(), on_kill_switch_update)
    # Rev 9: Subscribe to circuit breaker channel (per Codex iteration 4)
    await realtime.subscribe(circuit_breaker_channel(), on_circuit_breaker_update)
    # Rev 6: Subscribe to orders channel for real-time updates
    await realtime.subscribe(orders_channel(user_id), on_orders_update)

    # Rev 8: Subscribe to fills channel for activity feed (per Codex iteration 3)
    # This provides dedicated fill events separate from order status updates
    async def on_fill_event(data: dict):
        """Handle fill/execution events for activity feed."""
        await activity_feed.add_item(data)

    await realtime.subscribe(fills_channel(user_id), on_fill_event)

    # ===== JS Event Bridge for Action Buttons (Rev 12 - HIGH, Rev 13 - Fixed) =====
    # The AG Grid cell renderers emit custom events (cancel_order, close_position)
    # which need to be bridged to Python handlers. NiceGUI's ui.on() handles this.
    #
    # Rev 13: Fixed payload extraction (per Codex iteration 8 - HIGH)
    # NiceGUI passes the CustomEvent's detail object as e.args when using ui.on()
    # The detail is directly in e.args, not e.args.get("detail")
    # Must validate required fields before invoking handlers.

    async def handle_cancel_order_event(e):
        """Bridge JS cancel_order event to Python handler.

        Rev 13: Validate required fields, log malformed events.
        Rev 14: Include broker_order_id for orders with synthetic client_order_id (per Codex review)
        """
        # NiceGUI passes CustomEvent.detail as e.args dict
        detail = e.args if isinstance(e.args, dict) else {}

        client_order_id = detail.get("client_order_id")
        symbol = detail.get("symbol")
        broker_order_id = detail.get("broker_order_id")  # For orders with synthetic IDs

        # Validate required fields
        if not client_order_id or not symbol:
            logger.warning(
                "malformed_cancel_order_event",
                extra={"client_id": client_id, "args": str(e.args)[:100]},
            )
            return

        await on_cancel_order(
            client_order_id=client_order_id,
            symbol=symbol,
            user_id=user_id,
            user_role=user_role,
            broker_order_id=broker_order_id,  # Pass broker ID for synthetic order cancels
        )

    async def handle_close_position_event(e):
        """Bridge JS close_position event to Python handler.

        Rev 13: Validate required fields, log malformed events.
        Rev 14: Handle fractional qty properly (per Codex iteration 9 - MEDIUM)
        """
        # NiceGUI passes CustomEvent.detail as e.args dict
        detail = e.args if isinstance(e.args, dict) else {}

        symbol = detail.get("symbol")
        qty_raw = detail.get("qty")

        # Validate required fields (qty can be negative for short positions)
        if not symbol or qty_raw is None:
            logger.warning(
                "malformed_close_position_event",
                extra={"client_id": client_id, "args": str(e.args)[:100]},
            )
            return

        # Strict integer qty validation - reject any fractional shares
        try:
            qty_float = float(qty_raw)
            # Reject fractional shares strictly (must be whole number)
            if qty_float != int(qty_float):
                ui.notify(f"Fractional shares ({qty_raw}) not supported", type="warning")
                logger.warning(
                    "fractional_qty_rejected",
                    extra={"client_id": client_id, "qty_raw": qty_raw, "symbol": symbol},
                )
                return
            qty = int(qty_float)
        except (ValueError, TypeError):
            logger.warning(
                "invalid_qty_close_position",
                extra={"client_id": client_id, "qty_raw": str(qty_raw)[:20]},
            )
            ui.notify("Invalid quantity value", type="negative")
            return

        await on_close_position(
            symbol=symbol,
            qty=qty,
            user_id=user_id,
            user_role=user_role,
        )

    # Register event listeners for JS custom events
    # NiceGUI bridges window events via ui.on() with 'window:eventname' syntax
    # Rev 18k: Use client.storage to prevent duplicate registration in multi-tab (per Codex review - MEDIUM)
    # app.storage.user is shared across tabs; client storage is per WebSocket connection
    handlers_key = "_ng_handlers_registered"
    if not client.storage.get(handlers_key, False):
        ui.on('window:cancel_order', handle_cancel_order_event)
        ui.on('window:close_position', handle_close_position_event)
        client.storage[handlers_key] = True

    # ===== Polling for Market Data =====
    # Rev 13: Added rate-limited logging (per Codex iteration 8 - MEDIUM)
    # Rev 18: Use shared cache to prevent N× duplicate backend calls (per Codex review)
    # Market prices are NOT user-specific, so all clients can share the same data
    market_data_error_count = 0
    MARKET_DATA_LOG_INTERVAL = 6  # Log once per 30s at 5s interval

    async def update_market_data():
        """Poll market prices (not user-specific, high volume).

        Rev 15: Uses grid_update_lock to serialize with other grid updates (per Codex review).
        Rev 18: Uses module-level MarketPriceCache to prevent duplicate backend calls.

        NOTE: MarketPriceCache is a module-level singleton that caches market prices
        with a 4s TTL. All clients share the same cache, so at scale (100 users),
        we make ~1 backend call per 4s instead of 100 calls per 5s.
        """
        nonlocal market_data_error_count
        try:
            # Use shared cache - returns cached data if fresh, fetches if expired
            prices = await MarketPriceCache.get_prices(trading_client)
            # Rev 18d: Use cell-level updates to avoid wiping other row fields (per Codex review - HIGH)
            # applyTransaction with partial rows can null other fields like qty/P&L/avg_entry
            # Rev 18e: Batch all price updates into single JS call (per Codex review - HIGH/MEDIUM)
            # - Uses json.dumps for safe serialization (prevents JS injection)
            # - Single JS execution instead of N sequential run_grid_method calls
            # Rev 18f: Use client.run_javascript for correct session binding (per Codex review - MEDIUM)
            # ui.run_javascript can broadcast to wrong session; client context ensures correct scoping
            async with grid_update_lock:
                # Rev 18g: Normalize symbols to uppercase for consistent matching (per Codex review - MEDIUM)
                # Positions may use different casing than market data source
                # Rev 18k: Filter for finite numeric prices (per Codex review - MEDIUM)
                # Non-numeric values would break the formatter's toFixed() call
                price_map = {
                    p['symbol'].upper(): p['price']
                    for p in prices
                    if p.get('symbol') and isinstance(p.get('price'), (int, float))
                }
                price_map_json = json.dumps(price_map)
                # Rev 18f: Guard for grid API readiness to prevent timer errors (per Codex review - LOW)
                await client.run_javascript(f'''
                    const priceMap = {price_map_json};
                    const gridEl = getElement({positions_grid.id});
                    const api = gridEl?.gridOptions?.api;
                    if (api) {{
                        api.forEachNode(node => {{
                            // Rev 18g: Normalize symbol to uppercase for consistent matching
                            const symbol = (node.data?.symbol || '').toUpperCase();
                            // Rev 18k: Only update if price is a finite number
                            const price = priceMap[symbol];
                            if (Number.isFinite(price)) {{
                                node.setDataValue('current_price', price);
                            }}
                        }});
                    }}
                ''')
            market_data_error_count = 0  # Reset on success
        except httpx.HTTPStatusError as e:
            market_data_error_count += 1
            if market_data_error_count % MARKET_DATA_LOG_INTERVAL == 1:
                logger.warning(
                    "market_data_fetch_failed",
                    extra={"client_id": client_id, "status": e.response.status_code},
                )
        except httpx.RequestError as e:
            market_data_error_count += 1
            if market_data_error_count % MARKET_DATA_LOG_INTERVAL == 1:
                logger.warning(
                    "market_data_fetch_failed",
                    extra={"client_id": client_id, "error": type(e).__name__},
                )

    # Create and track timers for cleanup
    market_timer = ui.timer(5.0, update_market_data)
    timers.append(market_timer)

    # Rev 10: Periodic reconciliation fallback (per Codex iteration 5 - HIGH)
    # Rev 13: Added rate-limited logging (per Codex iteration 8 - MEDIUM)
    # Hybrid push/poll: Push is primary, poll is fallback for missed messages
    reconcile_error_count = 0
    RECONCILE_LOG_INTERVAL = 2  # Log once per minute at 30s interval

    async def reconcile_positions_orders():
        """Periodic full refresh to catch any missed Pub/Sub messages.

        Runs every 30s as a safety net. This ensures:
        - State converges even if Pub/Sub messages are lost
        - UI recovers from any drift without page refresh
        - Stale detection remains accurate

        Rev 14: Uses grid_update_lock to serialize with push updates (per Codex review).
        Rev 16: Also refresh P&L/metric cards to prevent stale data (per Codex review).
        """
        nonlocal position_symbols, order_ids, reconcile_error_count
        try:
            # Rev 16: Include P&L fetch to keep metric cards in sync
            # Rev 18j: Pass role/strategies for auth context (per Codex review - MEDIUM)
            pnl_data, positions, orders = await asyncio.gather(
                trading_client.fetch_realtime_pnl(user_id),
                trading_client.fetch_positions(user_id, role=user_role, strategies=user_strategies),
                trading_client.fetch_open_orders(user_id, role=user_role),
            )
            # Update metric cards
            pnl_card.update(pnl_data.get("total_unrealized_pl", 0))
            positions_card.update(pnl_data.get("total_positions", 0))
            realized_card.update(pnl_data.get("realized_pl_today", 0))
            bp_card.update(pnl_data.get("buying_power", 0))

            # Serialize with push updates to prevent race conditions
            async with grid_update_lock:
                position_symbols = await update_positions_grid(
                    positions_grid, positions.get("positions", []), position_symbols
                )
                order_ids = await update_orders_table(
                    orders_table, orders.get("orders", []), order_ids, notified_missing_ids, synthetic_id_map
                )
            reconcile_error_count = 0  # Reset on success
        except httpx.HTTPStatusError as e:
            reconcile_error_count += 1
            if reconcile_error_count % RECONCILE_LOG_INTERVAL == 1:
                logger.warning(
                    "reconcile_positions_orders_failed",
                    extra={"client_id": client_id, "status": e.response.status_code},
                )
        except httpx.RequestError as e:
            reconcile_error_count += 1
            if reconcile_error_count % RECONCILE_LOG_INTERVAL == 1:
                logger.warning(
                    "reconcile_positions_orders_failed",
                    extra={"client_id": client_id, "error": type(e).__name__},
                )

    reconcile_timer = ui.timer(30.0, reconcile_positions_orders)
    timers.append(reconcile_timer)

    # Periodic safety state reconciliation
    # Ensures kill switch / circuit breaker state is correct even if Pub/Sub missed
    # FAIL-SAFE: On fetch errors, default to RESTRICTIVE state (block new entries)
    safety_state_error_count = 0  # Rate-limit logging
    safety_state_notified = False  # Track if user already notified (per Codex review - LOW)
    SAFETY_STATE_LOG_INTERVAL = 12  # Log once per minute at 15s interval
    # Fail-safe immediately on first failure - trading safety is critical
    # If we can't verify safety state, assume most restrictive (kill switch engaged)
    SAFETY_STATE_UNKNOWN_THRESHOLD = 1

    async def reconcile_safety_state():
        """Poll safety state to catch missed Pub/Sub updates.

        FAIL-SAFE BEHAVIOR: When safety state cannot be determined:
        - Set killSwitch=true AND circuitBreaker=true (most restrictive)
        - Block new entries (blocked by either)
        - Block closes (blocked by kill switch)
        - Allow cancels only (always safe, never blocked)
        This matches the kill-switch-engaged state as the safest default.
        """
        nonlocal safety_state_error_count, safety_state_notified

        try:
            # Fetch both safety states in parallel
            ks_result, cb_result = await asyncio.gather(
                trading_client.fetch_kill_switch_status(user_id, role=user_role),
                trading_client.fetch_circuit_breaker_status(user_id, role=user_role),
                return_exceptions=True,  # Don't fail if one endpoint errors
            )

            # Check for failures - if EITHER fails, go to most restrictive state
            ks_failed = isinstance(ks_result, Exception)
            cb_failed = isinstance(cb_result, Exception)
            any_failed = ks_failed or cb_failed

            # FAIL-CLOSED: If ANY safety endpoint fails, set BOTH to most restrictive
            # This matches the docstring: "Set killSwitch=true AND circuitBreaker=true"
            if any_failed:
                safety_state_error_count += 1
                if safety_state_error_count % SAFETY_STATE_LOG_INTERVAL == 1:
                    failed_endpoints = []
                    if ks_failed:
                        failed_endpoints.append(f"kill_switch:{ks_result}")
                    if cb_failed:
                        failed_endpoints.append(f"circuit_breaker:{cb_result}")
                    logger.warning(
                        "safety_state_fetch_failed",
                        extra={"client_id": client_id, "endpoints": ", ".join(failed_endpoints)},
                    )
                # MOST RESTRICTIVE: Block entries AND closes (only cancels allowed)
                await client.run_javascript(
                    "window.dispatchEvent(new CustomEvent('trading_state_change', "
                    "{detail: {killSwitch: true, circuitBreaker: true, stateUnknown: true}}))"
                )
            else:
                # Both succeeded - apply actual states
                # Rev 18h: Fail-closed validation - unknown state defaults to restrictive (per Codex review - HIGH)
                # If state is missing or unexpected, treat as ENGAGED/TRIPPED for safety
                ks_state = ks_result.get("state")
                cb_state = cb_result.get("state")

                if ks_state not in ("ENGAGED", "DISENGAGED"):
                    logger.warning(
                        "kill_switch_reconcile_invalid_state",
                        extra={"state": ks_state, "defaulting_to": "ENGAGED"},
                    )
                    ks_engaged = True  # Fail-closed
                else:
                    ks_engaged = ks_state == "ENGAGED"

                if cb_state not in ("OPEN", "TRIPPED"):
                    logger.warning(
                        "circuit_breaker_reconcile_invalid_state",
                        extra={"state": cb_state, "defaulting_to": "TRIPPED"},
                    )
                    cb_tripped = True  # Fail-closed
                else:
                    cb_tripped = cb_state == "TRIPPED"

                await client.run_javascript(
                    f"window.dispatchEvent(new CustomEvent('trading_state_change', "
                    f"{{detail: {{killSwitch: {str(ks_engaged).lower()}, circuitBreaker: {str(cb_tripped).lower()}}}}}))"
                )

            # Notify user if either endpoint is down - only on transition (per Codex review)
            if ks_failed or cb_failed:
                if not safety_state_notified:
                    ui.notify("Safety status partially unavailable - defaulting to restrictive", type="warning")
                    safety_state_notified = True
            else:
                # Reset counter and notification flag only if BOTH succeeded
                safety_state_error_count = 0
                safety_state_notified = False

        except httpx.HTTPStatusError as e:
            safety_state_error_count += 1
            if safety_state_error_count % SAFETY_STATE_LOG_INTERVAL == 1:
                logger.warning(
                    "safety_state_reconcile_error",
                    extra={"client_id": client_id, "status": e.response.status_code},
                )
            # FAIL-SAFE on repeated failures - block ALL trading when state unknown
            if safety_state_error_count >= SAFETY_STATE_UNKNOWN_THRESHOLD:
                await client.run_javascript(
                    "window.dispatchEvent(new CustomEvent('trading_state_change', "
                    "{detail: {killSwitch: true, circuitBreaker: true, stateUnknown: true}}))"
                )
        except httpx.RequestError as e:
            safety_state_error_count += 1
            if safety_state_error_count % SAFETY_STATE_LOG_INTERVAL == 1:
                logger.warning(
                    "safety_state_reconcile_error",
                    extra={"client_id": client_id, "error": type(e).__name__},
                )
            # FAIL-SAFE on repeated failures - block ALL trading when state unknown
            if safety_state_error_count >= SAFETY_STATE_UNKNOWN_THRESHOLD:
                await client.run_javascript(
                    "window.dispatchEvent(new CustomEvent('trading_state_change', "
                    "{detail: {killSwitch: true, circuitBreaker: true, stateUnknown: true}}))"
                )

    safety_timer = ui.timer(15.0, reconcile_safety_state)
    timers.append(safety_timer)
    # Rev 17: Fetch safety state immediately on load to prevent stale UI gating
    # Without this, buttons may show as enabled until first Pub/Sub event or timer
    await reconcile_safety_state()

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

    def cleanup_handler_flag():
        """Rev 18k: Clear handler registration flag on disconnect (per Codex review - MEDIUM).

        The flag prevents duplicate registration during page re-render within same session.
        client.storage is per-WebSocket, so flag auto-clears on disconnect, but explicit
        cleanup ensures it's reset for any navigation within the same session.
        """
        handlers_key = "_ng_handlers_registered"
        client.storage.pop(handlers_key, None)

    # Rev 6: await async lifecycle methods (per Codex review)
    # Rev 18: Rely solely on per-client lifecycle callbacks for cleanup (per Codex review)
    # Using app.on_disconnect is app-wide and risks handler accumulation across page renders.
    # ClientLifecycleManager tracks per-client and handles both disconnect and navigation.
    await lifecycle.register_cleanup_callback(client_id, cleanup_timers)
    await lifecycle.register_cleanup_callback(client_id, cleanup_handler_flag)
    await lifecycle.register_cleanup_callback(client_id, realtime.cleanup)
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
// Rev 8: Fixed to pass session cookies to WebSocket (per Codex iteration 3)
import http from 'k6/http';
import ws from 'k6/ws';
import { check, sleep, fail } from 'k6';

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

// Rev 8: Use http.CookieJar to persist session cookies across requests
const jar = http.cookieJar();

export default function () {
  // Login - cookies are automatically captured in the jar
  // Credentials from environment variables (never hardcode in scripts)
  const loginRes = http.post(
    `${__ENV.BASE_URL}/auth/login`,
    JSON.stringify({
      username: __ENV.LOAD_TEST_USERNAME || 'loadtest',
      password: __ENV.LOAD_TEST_PASSWORD || ''
    }),
    { headers: { 'Content-Type': 'application/json' } }
  );
  check(loginRes, { 'login success': (r) => r.status === 200 });

  // Extract session cookie for WebSocket
  // NiceGUI uses 'nicegui-session' cookie by default
  // Rev 18i: cookiesForURL returns arrays of cookie objects, use [0]?.value (per Codex review - MEDIUM)
  const cookies = jar.cookiesForURL(`${__ENV.BASE_URL}/`);
  const sessionCookieValue = (cookies['nicegui-session'] || cookies['session'])?.[0]?.value;

  // Rev 17: Assert session cookie is present to ensure we test authenticated WS behavior
  // Without this, load tests could silently test unauthenticated connections
  if (!sessionCookieValue) {
    console.error('FATAL: Session cookie not found - aborting iteration to prevent false-positive test results');
    fail('Session cookie required for authenticated WebSocket testing');
  }

  // Load dashboard (uses session from jar)
  const dashRes = http.get(`${__ENV.BASE_URL}/`);
  check(dashRes, { 'dashboard loads': (r) => r.status === 200 });

  // WebSocket connection with session cookie (Rev 8 fix)
  const wsUrl = `${__ENV.WS_URL}/_nicegui_ws`;
  const wsParams = {
    headers: {
      // Pass session cookie for authenticated WS connection
      // Rev 18i: Use extracted value string, not cookie object (per Codex review - MEDIUM)
      'Cookie': `nicegui-session=${sessionCookieValue}`,
    },
  };

  const res = ws.connect(wsUrl, wsParams, (socket) => {
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
- `test_orders_table.py`: Order display, cancel action, fallback for missing client_order_id
- `test_activity_feed.py`: Feed updates, max items
- `test_dashboard_timers.py`: Timer registration and cleanup on navigation (Rev 6)
- `test_safety_state.py`: Safety-state reconciliation and fail-closed behavior (per Codex review)
  - Kill switch fetch failure → set BOTH killSwitch=true, circuitBreaker=true
  - Circuit breaker fetch failure → set BOTH killSwitch=true, circuitBreaker=true
  - Both succeed → apply actual states
  - Notification only on transition (not every poll interval)
- `test_event_bridge.py`: JS event bridge validation (per Codex review)
  - Malformed cancel_order payloads (missing client_order_id)
  - Malformed close_position payloads (missing symbol, invalid qty)
  - Zero-qty close attempts logged and rejected
  - Kill switch blocks close, allows cancel

### Integration Tests (CI - Docker)
- `test_dashboard_integration.py`: Full dashboard load with mocked backend
- `test_realtime_integration.py`: Redis Pub/Sub flow
- `test_timer_cleanup.py`: Verify timers cancelled on client disconnect (Rev 6)

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

**Last Updated:** 2026-01-02
**Status:** PLANNING
