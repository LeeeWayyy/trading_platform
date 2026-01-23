"""Persistent watchlist with real-time updates and quick actions.

SUBSCRIPTION OWNERSHIP: Watchlist does NOT subscribe to Redis.
It requests OrderEntryContext to manage subscriptions via callbacks.

Data Flow: Watchlist requests → OrderEntryContext subscribes → price updates dispatched back.
"""

from __future__ import annotations

import asyncio
import html
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from nicegui import ui

from apps.web_console_ng.utils.time import parse_iso_timestamp

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from apps.web_console_ng.core.client import AsyncTradingClient

logger = logging.getLogger(__name__)


def validate_and_normalize_symbol(symbol: str) -> str:
    """Validate and normalize a stock symbol.

    Args:
        symbol: Raw symbol string.

    Returns:
        Normalized uppercase symbol.

    Raises:
        ValueError: If symbol is invalid.
    """
    if not symbol or not isinstance(symbol, str):
        raise ValueError("Symbol must be a non-empty string")

    normalized = symbol.strip().upper()

    # Empty after strip
    if not normalized:
        raise ValueError("Symbol must be a non-empty string")

    # Basic validation - alphanumeric, 1-5 characters
    if not normalized.isalnum():
        raise ValueError(f"Symbol contains invalid characters: {symbol}")
    if len(normalized) > 5:
        raise ValueError(f"Symbol length must be 1-5 characters: {symbol}")

    return normalized


@dataclass
class WatchlistItem:
    """Single watchlist item with price data."""

    symbol: str
    last_price: Decimal | None = None
    prev_close: Decimal | None = None
    change: Decimal | None = None
    change_pct: Decimal | None = None
    sparkline_data: list[float] = field(default_factory=list)
    timestamp: datetime | None = None


class WatchlistComponent:
    """Persistent watchlist with real-time updates and quick actions.

    SUBSCRIPTION OWNERSHIP: This component does NOT subscribe to Redis directly.
    All subscription management is done via callbacks to OrderEntryContext.
    """

    WORKSPACE_KEY = "watchlist.main"
    MAX_SYMBOLS = 20  # Limit to prevent excessive subscriptions
    SPARKLINE_POINTS = 20  # Number of historical points for sparkline

    def __init__(
        self,
        trading_client: AsyncTradingClient,
        on_symbol_selected: Callable[[str | None], Awaitable[None]] | None = None,
        on_subscribe_symbol: Callable[[str], Awaitable[None]] | None = None,
        on_unsubscribe_symbol: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """Initialize Watchlist.

        Args:
            trading_client: HTTP client for API calls.
            on_symbol_selected: Callback when user selects a symbol (None clears selection).
            on_subscribe_symbol: Callback to request OrderEntryContext to subscribe.
            on_unsubscribe_symbol: Callback to request OrderEntryContext to unsubscribe.
        """
        self._client = trading_client
        self._on_symbol_selected = on_symbol_selected
        self._on_subscribe_symbol = on_subscribe_symbol
        self._on_unsubscribe_symbol = on_unsubscribe_symbol

        # State
        self._items: dict[str, WatchlistItem] = {}
        self._symbol_order: list[str] = []
        self._selected_symbol: str | None = None
        self._disposed: bool = False

        # Pending selection task (for race condition prevention)
        self._pending_selection_task: asyncio.Task[None] | None = None

        # THROTTLING STATE: Per-row updates throttled to 2Hz (500ms)
        self._row_render_interval: float = 0.5  # 500ms = 2Hz max per symbol
        self._last_row_render: dict[str, float] = {}
        self._pending_row_renders: set[str] = set()
        self._render_batch_handle: asyncio.TimerHandle | None = None

        # UI elements
        self._list_container: ui.column | None = None
        self._add_input: ui.input | None = None
        self._timer_tracker: Callable[[ui.timer], None] | None = None

    # ================= Initialization =================

    async def initialize(
        self,
        timer_tracker: Callable[[ui.timer], None],
        initial_symbols: list[str] | None = None,
    ) -> None:
        """Initialize watchlist with timer tracking and initial symbols.

        Args:
            timer_tracker: Callback to register timers for lifecycle management.
            initial_symbols: Optional list of initial symbols (default: SPY, QQQ, AAPL, MSFT, TSLA).
        """
        self._timer_tracker = timer_tracker

        # Use provided symbols or default
        symbols = initial_symbols or ["SPY", "QQQ", "AAPL", "MSFT", "TSLA"]

        # Validate and deduplicate symbols
        seen: set[str] = set()
        for raw_symbol in symbols[: self.MAX_SYMBOLS]:
            try:
                symbol = validate_and_normalize_symbol(raw_symbol)
                if symbol in seen:
                    continue
                seen.add(symbol)
                self._symbol_order.append(symbol)
                self._items[symbol] = WatchlistItem(symbol=symbol)
                await self._request_subscribe(symbol)
            except ValueError as exc:
                logger.warning(f"Invalid watchlist symbol: {raw_symbol!r} - {exc}")

    def create(self) -> ui.card:
        """Create the watchlist UI card."""
        with ui.card().classes("p-2 w-64") as card:
            # Header
            with ui.row().classes("justify-between items-center mb-2"):
                ui.label("Watchlist").classes("text-sm font-bold")

            # Add symbol input
            with ui.row().classes("gap-1 mb-2"):
                self._add_input = ui.input(placeholder="Add symbol").classes(
                    "flex-grow"
                ).props("dense outlined")
                ui.button(
                    icon="add",
                    on_click=self._add_symbol_from_input,
                ).classes("w-8 h-8").props("flat dense")

            # Symbol list
            self._list_container = ui.column().classes("gap-1 w-full")

            self._render_items()

        return card

    # ================= UI Rendering =================

    def _render_items(self) -> None:
        """Render all watchlist items."""
        if not self._list_container:
            return

        self._list_container.clear()

        with self._list_container:
            for symbol in self._symbol_order:
                item = self._items.get(symbol)
                if item:
                    self._render_item(item)

    def _render_item(self, item: WatchlistItem) -> None:
        """Render single watchlist item.

        SECURITY: Symbol is escaped when used in HTML attributes.
        """
        is_selected = item.symbol == self._selected_symbol

        # SECURITY: Escape symbol for use in HTML attribute (defense-in-depth)
        escaped_symbol = html.escape(item.symbol, quote=True)
        row_id = f"watchlist-row-{escaped_symbol}"

        with ui.row().classes(
            f"w-full p-2 rounded cursor-pointer hover:bg-gray-700 "
            f"{'bg-blue-900' if is_selected else ''}"
        ).props(f'id="{row_id}" data-symbol="{escaped_symbol}"') as row:
            row.on("click", lambda s=item.symbol: self._select_symbol(s))

            # Symbol and price column
            with ui.column().classes("flex-grow"):
                with ui.row().classes("justify-between"):
                    ui.label(item.symbol).classes("font-bold text-sm")
                    if item.last_price:
                        ui.label(f"${item.last_price:.2f}").classes(
                            "price text-sm font-mono"
                        )
                    else:
                        ui.label("—").classes("price text-sm text-gray-500")

                with ui.row().classes("justify-between items-center"):
                    # Change badge
                    if item.change_pct is not None:
                        sign = "+" if item.change_pct >= 0 else ""
                        color = (
                            "text-green-400" if item.change_pct >= 0 else "text-red-400"
                        )
                        ui.label(f"{sign}{item.change_pct:.2f}%").classes(
                            f"change text-xs {color}"
                        )
                    else:
                        ui.label("--").classes("change text-xs text-gray-500")

                    # Sparkline
                    if item.sparkline_data:
                        self._render_sparkline(item.sparkline_data, item.change_pct)

            # Remove button
            ui.button(
                icon="close",
                on_click=lambda s=item.symbol: self._on_remove_clicked(s),
            ).classes("w-6 h-6 opacity-50 hover:opacity-100").props("flat dense")

    def _render_sparkline(
        self, data: list[float], change_pct: Decimal | None
    ) -> None:
        """Render inline sparkline SVG.

        SECURITY: Validates and sanitizes data points before rendering.
        """
        # Validate and filter data
        validated_data: list[float] = []
        for val in data:
            try:
                float_val = float(val)
                if math.isfinite(float_val):
                    validated_data.append(float_val)
            except (TypeError, ValueError):
                continue

        if len(validated_data) < 2:
            return

        width = 50
        height = 20
        color = "#4ade80" if (change_pct or 0) >= 0 else "#f87171"

        # Normalize data to fit in height
        min_val = min(validated_data)
        max_val = max(validated_data)
        range_val = max_val - min_val or 1

        points = []
        for i, val in enumerate(validated_data):
            x = i * width / (len(validated_data) - 1)
            y = height - ((val - min_val) / range_val * height)
            points.append(f"{x:.1f},{y:.1f}")

        polyline = " ".join(points)

        ui.html(f"""
            <svg width="{width}" height="{height}" class="ml-2">
                <polyline
                    points="{polyline}"
                    fill="none"
                    stroke="{color}"
                    stroke-width="1.5"
                />
            </svg>
        """)

    # ================= Symbol Management =================

    async def _add_symbol_from_input(self) -> None:
        """Add symbol from input field."""
        if self._disposed or not self._add_input:
            return

        raw_symbol = self._add_input.value.strip()
        self._add_input.value = ""

        if not raw_symbol:
            return

        # Validate and normalize symbol
        try:
            symbol = validate_and_normalize_symbol(raw_symbol)
        except ValueError as exc:
            ui.notify(f"Invalid symbol: {raw_symbol}", type="negative")
            return

        if symbol in self._items:
            ui.notify(f"{symbol} already in watchlist", type="warning")
            return

        if len(self._items) >= self.MAX_SYMBOLS:
            ui.notify(f"Maximum {self.MAX_SYMBOLS} symbols allowed", type="warning")
            return

        # Add to watchlist
        self._items[symbol] = WatchlistItem(symbol=symbol)
        self._symbol_order.append(symbol)

        # Request subscription
        await self._request_subscribe(symbol)

        # Re-render
        self._render_items()
        ui.notify(f"Added {symbol}", type="positive")

    def _on_remove_clicked(self, symbol: str) -> None:
        """Handle remove button click (schedules async removal)."""
        if self._disposed:
            return
        asyncio.create_task(self._remove_symbol(symbol))

    async def _remove_symbol(self, symbol: str) -> None:
        """Remove symbol from watchlist."""
        if self._disposed or symbol not in self._items:
            return

        # Request unsubscribe
        await self._request_unsubscribe(symbol)

        # Remove from state
        del self._items[symbol]
        self._symbol_order.remove(symbol)

        # Clear selection if removed AND notify
        if self._selected_symbol == symbol:
            self._selected_symbol = None
            await self._notify_selection_changed(None)

        # Re-render
        self._render_items()
        ui.notify(f"Removed {symbol}", type="info")

    def _select_symbol(self, symbol: str) -> None:
        """Select symbol and notify OrderEntryContext.

        RACE PREVENTION: Cancels pending selection before starting new one.
        """
        if self._disposed:
            return

        self._selected_symbol = symbol
        self._render_items()  # Re-render to show selection

        # Cancel pending selection task
        if self._pending_selection_task and not self._pending_selection_task.done():
            self._pending_selection_task.cancel()

        # Notify via task
        self._pending_selection_task = asyncio.create_task(
            self._notify_selection_changed(symbol)
        )
        self._pending_selection_task.add_done_callback(self._log_task_exception)

    async def _notify_selection_changed(self, symbol: str | None) -> None:
        """Notify callback of selection change."""
        if self._on_symbol_selected:
            await self._on_symbol_selected(symbol)

    # ================= Subscription Callbacks =================

    async def _request_subscribe(self, symbol: str) -> None:
        """Request OrderEntryContext to subscribe to a symbol."""
        if self._on_subscribe_symbol:
            await self._on_subscribe_symbol(symbol)

    async def _request_unsubscribe(self, symbol: str) -> None:
        """Request OrderEntryContext to unsubscribe from a symbol."""
        if self._on_unsubscribe_symbol:
            await self._on_unsubscribe_symbol(symbol)

    # ================= Price Data Callbacks =================

    def set_symbol_price_data(self, symbol: str, data: dict[str, Any]) -> None:
        """Called by OrderEntryContext when price data updates.

        This is the ONLY way Watchlist receives real-time price updates.
        THROTTLED: Per-row updates capped at 2Hz to prevent UI jank.
        """
        if self._disposed:
            return

        # TYPE GUARD: Validate payload structure
        if not isinstance(data, dict):
            logger.warning(
                f"Watchlist received invalid data type for {symbol}: {type(data).__name__}"
            )
            return

        item = self._items.get(symbol)
        if not item:
            return

        # Parse price
        raw_price = data.get("price")
        if raw_price is not None:
            try:
                parsed_price = Decimal(str(raw_price))
                if parsed_price.is_finite() and parsed_price > 0:
                    item.last_price = parsed_price
                else:
                    item.last_price = None
            except (InvalidOperation, ValueError, TypeError):
                item.last_price = None
        else:
            item.last_price = None

        # Parse timestamp
        raw_timestamp = data.get("timestamp")
        if raw_timestamp is not None:
            try:
                item.timestamp = parse_iso_timestamp(str(raw_timestamp))
            except (ValueError, TypeError):
                item.timestamp = None
        else:
            item.timestamp = None

        # Parse prev_close (needed for change calculation)
        raw_prev_close = data.get("prev_close") or data.get("previous_close")
        if raw_prev_close is not None:
            try:
                parsed_prev = Decimal(str(raw_prev_close))
                if parsed_prev.is_finite() and parsed_prev > 0:
                    item.prev_close = parsed_prev
                # Don't clear prev_close if invalid - keep previous value
            except (InvalidOperation, ValueError, TypeError):
                pass  # Keep previous prev_close value

        # Calculate change
        if item.last_price is None:
            item.change = None
            item.change_pct = None
        elif (
            item.prev_close is not None
            and item.prev_close.is_finite()
            and item.prev_close > 0
        ):
            item.change = item.last_price - item.prev_close
            item.change_pct = (item.change / item.prev_close) * 100
        else:
            item.change = None
            item.change_pct = None

        # Update sparkline
        if item.last_price:
            try:
                price_float = float(item.last_price)
                if math.isfinite(price_float) and price_float > 0:
                    item.sparkline_data.append(price_float)
                    if len(item.sparkline_data) > self.SPARKLINE_POINTS:
                        item.sparkline_data = item.sparkline_data[-self.SPARKLINE_POINTS :]
            except (TypeError, ValueError):
                pass

        # Schedule throttled render
        self._schedule_row_render(symbol)

    def _schedule_row_render(self, symbol: str) -> None:
        """Schedule a throttled per-row render."""
        now = time.monotonic()
        last_render = self._last_row_render.get(symbol, 0.0)
        time_since_last = now - last_render

        if time_since_last >= self._row_render_interval:
            # Outside throttle window - render immediately
            self._last_row_render[symbol] = now
            self._render_single_item(symbol)
        else:
            # Inside throttle window - schedule for later
            self._pending_row_renders.add(symbol)
            if self._render_batch_handle is None or self._render_batch_handle.cancelled():
                delay = self._row_render_interval - time_since_last
                try:
                    loop = asyncio.get_running_loop()
                    self._render_batch_handle = loop.call_later(
                        delay, self._flush_pending_renders
                    )
                except RuntimeError:
                    # No running event loop
                    pass

    def _flush_pending_renders(self) -> None:
        """Render all pending row updates."""
        self._render_batch_handle = None

        if not self._pending_row_renders:
            return

        now = time.monotonic()
        symbols_to_render = list(self._pending_row_renders)
        self._pending_row_renders.clear()

        for symbol in symbols_to_render:
            self._last_row_render[symbol] = now
            self._render_single_item(symbol)

    def _render_single_item(self, symbol: str) -> None:
        """Render a single watchlist row without re-rendering entire list."""
        item = self._items.get(symbol)
        if not item or not self._list_container:
            return

        row_id = f"watchlist-row-{symbol}"

        # Format display values
        price_display = f"${item.last_price:.2f}" if item.last_price else "—"
        change_display = f"{item.change:+.2f}" if item.change is not None else "—"
        change_pct_display = (
            f"({item.change_pct:+.1f}%)" if item.change_pct is not None else ""
        )
        change_color = "green" if (item.change or 0) >= 0 else "red"

        # Update row via JavaScript
        try:
            ui.run_javascript(f'''
                const row = document.getElementById("{row_id}");
                if (row) {{
                    const priceEl = row.querySelector(".price");
                    if (priceEl) priceEl.textContent = "{price_display}";
                    const changeEl = row.querySelector(".change");
                    if (changeEl) {{
                        changeEl.textContent = "{change_display} {change_pct_display}";
                        changeEl.style.color = "{change_color}";
                    }}
                }}
            ''')
        except Exception:
            pass  # Best effort

    # ================= Getters =================

    def get_selected_symbol(self) -> str | None:
        """Get currently selected symbol."""
        return self._selected_symbol

    def get_symbols(self) -> list[str]:
        """Get list of watchlist symbols in order."""
        return list(self._symbol_order)

    # ================= Helpers =================

    def _log_task_exception(self, task: asyncio.Task[None]) -> None:
        """Log exceptions from async tasks."""
        try:
            exc = task.exception()
            if exc is not None:
                logger.error(f"Watchlist task failed: {exc}", exc_info=exc)
        except asyncio.CancelledError:
            pass

    # ================= Cleanup =================

    async def dispose(self) -> None:
        """Clean up watchlist component resources."""
        self._disposed = True

        # Cancel pending selection task
        if self._pending_selection_task and not self._pending_selection_task.done():
            self._pending_selection_task.cancel()
            try:
                await self._pending_selection_task
            except asyncio.CancelledError:
                pass

        # Cancel pending render batch
        if self._render_batch_handle is not None:
            self._render_batch_handle.cancel()
            self._render_batch_handle = None

        self._pending_row_renders.clear()
        self._last_row_render.clear()
        self._items.clear()
        self._symbol_order.clear()
        self._selected_symbol = None


__all__ = [
    "WatchlistComponent",
    "WatchlistItem",
    "validate_and_normalize_symbol",
]
