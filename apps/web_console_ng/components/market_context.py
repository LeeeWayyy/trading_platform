"""Real-time Level 1 market data display.

SUBSCRIPTION OWNERSHIP: MarketContext does NOT subscribe to Redis.
OrderEntryContext owns all subscriptions and dispatches updates via callbacks.

Data Flow: Redis → RealtimeUpdater → OrderEntryContext → MarketContext.set_price_data()
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from nicegui import ui

from apps.web_console_ng.utils.time import parse_iso_timestamp

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from apps.web_console_ng.core.client import AsyncTradingClient

logger = logging.getLogger(__name__)

# Default thresholds
STALE_THRESHOLD_S = 30  # Data older than this is considered stale
UPDATE_THROTTLE_MS = 100  # Minimum UI update interval


@dataclass
class MarketDataSnapshot:
    """Point-in-time market data snapshot."""

    symbol: str
    bid_price: Decimal | None = None
    ask_price: Decimal | None = None
    bid_size: int | None = None
    ask_size: int | None = None
    last_price: Decimal | None = None
    prev_close: Decimal | None = None
    volume: int | None = None
    timestamp: datetime | None = None

    @property
    def mid_price(self) -> Decimal | None:
        """Calculate mid price from bid/ask."""
        if self.bid_price is not None and self.ask_price is not None:
            return (self.bid_price + self.ask_price) / 2
        return None

    @property
    def spread_bps(self) -> Decimal | None:
        """Calculate spread in basis points."""
        mid = self.mid_price
        if self.bid_price is not None and self.ask_price is not None and mid:
            spread = self.ask_price - self.bid_price
            return (spread / mid) * 10000
        return None

    @property
    def change(self) -> Decimal | None:
        """Calculate price change from previous close."""
        if self.last_price is not None and self.prev_close is not None and self.prev_close > 0:
            return self.last_price - self.prev_close
        return None

    @property
    def change_pct(self) -> Decimal | None:
        """Calculate percentage change from previous close."""
        if self.last_price is not None and self.prev_close is not None and self.prev_close > 0:
            return ((self.last_price - self.prev_close) / self.prev_close) * 100
        return None


class MarketContextComponent:
    """Real-time Level 1 market data display.

    SUBSCRIPTION OWNERSHIP: This component does NOT subscribe to Redis directly.
    All price updates come via set_price_data() callback from OrderEntryContext.
    """

    def __init__(
        self,
        trading_client: AsyncTradingClient,
        on_price_updated: (
            Callable[[str, Decimal | None, datetime | None], Awaitable[None]] | None
        ) = None,
    ) -> None:
        """Initialize MarketContext.

        Args:
            trading_client: HTTP client for API calls.
            on_price_updated: Optional callback when price updates (for OrderTicket).
        """
        self._client = trading_client
        self._on_price_updated = on_price_updated
        self._current_symbol: str | None = None
        self._data: MarketDataSnapshot | None = None
        self._last_ui_update: float = 0
        self._timer_tracker: Callable[[ui.timer], None] | None = None
        self._staleness_timer: ui.timer | None = None
        self._disposed: bool = False

        # UI elements
        self._symbol_label: ui.label | None = None
        self._bid_price_label: ui.label | None = None
        self._bid_size_label: ui.label | None = None
        self._ask_price_label: ui.label | None = None
        self._ask_size_label: ui.label | None = None
        self._spread_label: ui.label | None = None
        self._last_price_label: ui.label | None = None
        self._change_badge: ui.badge | None = None
        self._staleness_badge: ui.badge | None = None
        self._volume_label: ui.label | None = None

    async def initialize(self, timer_tracker: Callable[[ui.timer], None]) -> None:
        """Initialize with timer tracking.

        Args:
            timer_tracker: Callback to register timers for lifecycle management.
        """
        self._timer_tracker = timer_tracker

        # Start periodic staleness badge update timer
        # This ensures the badge ages even without new price updates
        self._staleness_timer = ui.timer(1.0, self._update_staleness_from_timer)
        timer_tracker(self._staleness_timer)

    def create(self) -> ui.card:
        """Create the market context UI card."""
        with ui.card().classes("p-3 w-full") as card:
            # Symbol header with staleness badge
            with ui.row().classes("justify-between items-center mb-2 w-full"):
                self._symbol_label = ui.label("--").classes("text-lg font-bold")
                self._staleness_badge = ui.badge("No data").classes(
                    "text-xs bg-gray-500 text-white"
                )

            # Bid/Ask grid
            with ui.grid(columns=2).classes("gap-2 w-full"):
                # Bid column
                with ui.column().classes("items-start"):
                    ui.label("BID").classes("text-xs text-gray-500")
                    self._bid_price_label = ui.label("--").classes(
                        "text-xl font-mono text-green-500"
                    )
                    self._bid_size_label = ui.label("").classes("text-xs text-gray-400")

                # Ask column
                with ui.column().classes("items-end"):
                    ui.label("ASK").classes("text-xs text-gray-500")
                    self._ask_price_label = ui.label("--").classes("text-xl font-mono text-red-500")
                    self._ask_size_label = ui.label("").classes("text-xs text-gray-400")

            # Spread row
            with ui.row().classes("justify-center mt-2"):
                ui.label("Spread:").classes("text-xs text-gray-500")
                self._spread_label = ui.label("--").classes("text-xs font-mono ml-1")

            # Last price & change
            ui.separator().classes("my-2")
            with ui.row().classes("justify-between items-center w-full"):
                with ui.column().classes("items-start"):
                    ui.label("Last").classes("text-xs text-gray-500")
                    self._last_price_label = ui.label("--").classes("text-lg font-mono")
                self._change_badge = ui.badge("N/A").classes("text-sm bg-gray-500 text-white")

            # Volume
            with ui.row().classes("justify-start mt-2"):
                ui.label("Vol:").classes("text-xs text-gray-500")
                self._volume_label = ui.label("--").classes("text-xs font-mono ml-1")

        return card

    # ================= Symbol Management =================

    async def on_symbol_changed(self, symbol: str | None) -> None:
        """Called by OrderEntryContext when selected symbol changes.

        NOTE: MarketContext does NOT subscribe to Redis directly.
        It receives price updates via set_price_data() callback.

        RACE PREVENTION: After async operations, validate symbol is still current
        before updating state/UI to prevent stale fetches from overwriting newer data.
        """
        self._current_symbol = symbol

        if not symbol:
            self._data = None
            self._update_ui_no_data()
            return

        # Fetch initial data from API (fallback if Redis cache empty)
        await self._fetch_initial_data(symbol)

        # RACE CHECK: Ensure symbol hasn't changed during async fetch
        if symbol != self._current_symbol:
            return  # Stale - symbol changed during fetch

    async def _fetch_initial_data(self, symbol: str) -> None:
        """Fetch initial market data from API.

        Called when symbol changes to populate UI before real-time updates arrive.

        NOTE: Initial data fetch is optional - real-time updates via set_price_data()
        will populate the data. This method is a placeholder for future API integration
        when a quote endpoint is available.
        """
        if self._disposed:
            return

        # Real-time updates will populate data via set_price_data() callback
        # No direct API call needed - OrderEntryContext will provide initial data
        logger.debug(f"Waiting for price data for {symbol} via callback")

    # ================= Price Data Callbacks =================

    def set_price_data(self, data: dict[str, Any]) -> None:
        """Called by OrderEntryContext when price data updates.

        This is the ONLY way MarketContext receives real-time price updates.
        MarketContext does NOT subscribe directly to price channels.
        """
        # TYPE GUARD: Validate payload structure
        if not isinstance(data, dict):
            logger.warning(f"MarketContext received invalid data type: {type(data).__name__}")
            return  # Silently ignore malformed data (display-only component)

        # Check symbol matches current selection
        data_symbol = data.get("symbol")
        if data_symbol and data_symbol != self._current_symbol:
            return  # Ignore data for different symbol

        # Always update internal state (even when UI throttled)
        # This ensures _data always has the latest values
        self._update_data_from_payload(data)

        # Throttle UI updates
        now = time.time()
        if now - self._last_ui_update < UPDATE_THROTTLE_MS / 1000:
            return  # Skip UI update but data was updated above
        self._last_ui_update = now

        # Update UI and trigger callbacks
        self._update_ui()
        self._notify_price_updated()

    def _update_data_from_payload(self, data: dict[str, Any]) -> None:
        """Parse price data payload and update internal _data.

        SAFE PARSING: All Decimal/timestamp conversions wrapped in try/except.
        On malformed data, set field to None and continue (display-only component).
        """

        def safe_decimal(key: str) -> Decimal | None:
            raw = data.get(key)
            if raw is None:
                return None
            try:
                dec = Decimal(str(raw))
                if not dec.is_finite():
                    logger.warning(f"MarketContext: Non-finite {key}: {raw!r}")
                    return None
                return dec
            except (InvalidOperation, ValueError, TypeError):
                logger.warning(f"MarketContext: Invalid {key}: {raw!r}")
                return None

        def safe_int(key: str) -> int | None:
            raw = data.get(key)
            if raw is None:
                return None
            try:
                return int(raw)
            except (ValueError, TypeError):
                return None

        def safe_timestamp(key: str) -> datetime | None:
            raw = data.get(key)
            if raw is None:
                return None
            try:
                return parse_iso_timestamp(str(raw))
            except (ValueError, TypeError):
                logger.warning(f"MarketContext: Invalid {key}: {raw!r}")
                return None

        # Build snapshot with safe parsing
        symbol = data.get("symbol") or self._current_symbol or ""
        self._data = MarketDataSnapshot(
            symbol=symbol,
            bid_price=safe_decimal("bid") or safe_decimal("bid_price"),
            ask_price=safe_decimal("ask") or safe_decimal("ask_price"),
            bid_size=safe_int("bid_size"),
            ask_size=safe_int("ask_size"),
            last_price=safe_decimal("price") or safe_decimal("last_price"),
            prev_close=safe_decimal("prev_close"),
            volume=safe_int("volume"),
            timestamp=safe_timestamp("timestamp"),
        )

    def _notify_price_updated(self) -> None:
        """Notify callback of price update with proper error handling.

        Creates async task with done_callback to log any exceptions,
        preventing "Task exception was never retrieved" warnings.
        """
        if not self._on_price_updated or not self._data:
            return

        import asyncio

        def _handle_task_exception(task: asyncio.Task[None]) -> None:
            """Log any exception from the callback task."""
            try:
                exc = task.exception()
                if exc:
                    logger.error(
                        f"MarketContext on_price_updated callback failed: {exc}",
                        exc_info=exc,
                    )
            except asyncio.CancelledError:
                pass  # Task was cancelled, not an error

        coro = self._on_price_updated(
            self._data.symbol,
            self._data.last_price,
            self._data.timestamp,
        )
        task: asyncio.Task[None] = asyncio.create_task(coro)  # type: ignore[arg-type]
        task.add_done_callback(_handle_task_exception)

    # ================= UI Updates =================

    def _update_ui(self) -> None:
        """Update all UI elements from current data."""
        data = self._data
        if not data:
            self._update_ui_no_data()
            return

        # Symbol
        if self._symbol_label:
            self._symbol_label.set_text(data.symbol)

        # Bid
        if self._bid_price_label:
            if data.bid_price is not None:
                self._bid_price_label.set_text(f"${data.bid_price:.2f}")
            else:
                self._bid_price_label.set_text("--")

        if self._bid_size_label:
            if data.bid_size is not None:
                self._bid_size_label.set_text(f"x {data.bid_size}")
            else:
                self._bid_size_label.set_text("")

        # Ask
        if self._ask_price_label:
            if data.ask_price is not None:
                self._ask_price_label.set_text(f"${data.ask_price:.2f}")
            else:
                self._ask_price_label.set_text("--")

        if self._ask_size_label:
            if data.ask_size is not None:
                self._ask_size_label.set_text(f"x {data.ask_size}")
            else:
                self._ask_size_label.set_text("")

        # Spread
        if self._spread_label:
            if data.spread_bps is not None:
                self._spread_label.set_text(f"{data.spread_bps:.2f} bps")
            else:
                self._spread_label.set_text("--")

        # Last price
        if self._last_price_label:
            if data.last_price is not None:
                self._last_price_label.set_text(f"${data.last_price:.2f}")
            else:
                self._last_price_label.set_text("--")

        # Change badge
        if self._change_badge:
            if data.change_pct is not None:
                sign = "+" if data.change_pct >= 0 else ""
                self._change_badge.set_text(f"{sign}{data.change_pct:.2f}%")
                if data.change_pct >= 0:
                    self._change_badge.classes(remove="bg-red-500 bg-gray-500")
                    self._change_badge.classes(add="bg-green-500 text-white")
                else:
                    self._change_badge.classes(remove="bg-green-500 bg-gray-500")
                    self._change_badge.classes(add="bg-red-500 text-white")
            else:
                self._change_badge.set_text("N/A")
                self._change_badge.classes(remove="bg-green-500 bg-red-500")
                self._change_badge.classes(add="bg-gray-500 text-white")

        # Volume
        if self._volume_label:
            if data.volume is not None:
                self._volume_label.set_text(f"{data.volume:,}")
            else:
                self._volume_label.set_text("--")

        # Staleness badge
        self._update_staleness_badge(data.timestamp)

    def _update_ui_no_data(self) -> None:
        """Update UI to show no data state."""
        if self._symbol_label:
            self._symbol_label.set_text("--")
        if self._bid_price_label:
            self._bid_price_label.set_text("--")
        if self._bid_size_label:
            self._bid_size_label.set_text("")
        if self._ask_price_label:
            self._ask_price_label.set_text("--")
        if self._ask_size_label:
            self._ask_size_label.set_text("")
        if self._spread_label:
            self._spread_label.set_text("--")
        if self._last_price_label:
            self._last_price_label.set_text("--")
        if self._change_badge:
            self._change_badge.set_text("N/A")
            self._change_badge.classes(remove="bg-green-500 bg-red-500")
            self._change_badge.classes(add="bg-gray-500 text-white")
        if self._volume_label:
            self._volume_label.set_text("--")
        if self._staleness_badge:
            self._staleness_badge.set_text("No data")
            self._staleness_badge.classes(remove="bg-green-500 bg-yellow-500 bg-red-500")
            self._staleness_badge.classes(add="bg-gray-500 text-white")

    # ================= Staleness =================

    def _update_staleness_from_timer(self) -> None:
        """Periodic callback to update staleness badge based on elapsed time.

        Called by timer every 1 second to ensure the staleness badge
        continues to age even when no new price updates arrive.
        """
        if self._disposed:
            return
        if self._data and self._data.timestamp:
            self._update_staleness_badge(self._data.timestamp)

    def _update_staleness_badge(self, timestamp: datetime | None) -> None:
        """Update staleness badge based on data age.

        Badge states:
        - Live (green): <5s old
        - Xms ago (yellow): 5-30s old
        - Stale (red): >30s old
        - No data (gray): No timestamp
        """
        if not self._staleness_badge:
            return

        if not timestamp:
            self._staleness_badge.set_text("No data")
            self._staleness_badge.classes(remove="bg-green-500 bg-yellow-500 bg-red-500")
            self._staleness_badge.classes(add="bg-gray-500 text-white")
            return

        age_s = (datetime.now(UTC) - timestamp).total_seconds()

        if age_s < 5:
            self._staleness_badge.set_text("Live")
            self._staleness_badge.classes(remove="bg-yellow-500 bg-red-500 bg-gray-500")
            self._staleness_badge.classes(add="bg-green-500 text-white")
        elif age_s < STALE_THRESHOLD_S:
            self._staleness_badge.set_text(f"{int(age_s)}s ago")
            self._staleness_badge.classes(remove="bg-green-500 bg-red-500 bg-gray-500")
            self._staleness_badge.classes(add="bg-yellow-500 text-black")
        else:
            self._staleness_badge.set_text(f"Stale ({int(age_s)}s)")
            self._staleness_badge.classes(remove="bg-green-500 bg-yellow-500 bg-gray-500")
            self._staleness_badge.classes(add="bg-red-500 text-white")

    def is_data_stale(self) -> bool:
        """Check if current data is stale (>30s old)."""
        if not self._data or not self._data.timestamp:
            return True
        age_s = (datetime.now(UTC) - self._data.timestamp).total_seconds()
        return age_s > STALE_THRESHOLD_S

    def get_current_price(self) -> Decimal | None:
        """Get current last price (for OrderTicket calculations)."""
        if self._data:
            return self._data.last_price
        return None

    def get_price_timestamp(self) -> datetime | None:
        """Get current price timestamp (for staleness checks)."""
        if self._data:
            return self._data.timestamp
        return None

    # ================= Cleanup =================

    async def dispose(self) -> None:
        """Clean up component resources."""
        self._disposed = True
        if self._staleness_timer:
            self._staleness_timer.cancel()


__all__ = ["MarketContextComponent", "MarketDataSnapshot", "STALE_THRESHOLD_S"]
