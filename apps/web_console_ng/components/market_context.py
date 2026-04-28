"""Real-time Level 1 market data display.

SUBSCRIPTION OWNERSHIP: MarketContext does NOT subscribe to Redis.
OrderEntryContext owns all subscriptions and dispatches updates via callbacks.

Data Flow: Redis → RealtimeUpdater → OrderEntryContext → MarketContext.set_price_data()
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from nicegui import ui

from apps.web_console_ng.components.market_data_calls import call_market_data_client
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
        user_id: str | None = None,
        role: str | None = None,
        strategies: list[str] | None = None,
    ) -> None:
        """Initialize MarketContext.

        Args:
            trading_client: HTTP client for API calls.
            on_price_updated: Optional callback when price updates (for OrderTicket).
        """
        self._client = trading_client
        self._on_price_updated = on_price_updated
        self._user_id = user_id
        self._role = role
        self._strategies = strategies or []
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
        with ui.card().classes(
            "workspace-v2-panel workspace-v2-market-context-card p-2 w-full h-full overflow-hidden"
        ) as card:
            # Symbol header with staleness badge
            with ui.row().classes("w-full items-center justify-between mb-1"):
                self._symbol_label = ui.label("--").classes(
                    "workspace-v2-data-mono workspace-v2-market-symbol"
                )
                self._staleness_badge = ui.badge("No data").classes(
                    "workspace-v2-pill workspace-v2-pill-muted workspace-v2-market-badge"
                )

            # Bid/Ask grid
            with ui.grid(columns=2).classes("w-full gap-1"):
                # Bid column
                with ui.column().classes("items-start"):
                    ui.label("BID").classes("workspace-v2-field-label")
                    self._bid_price_label = ui.label("--").classes(
                        "workspace-v2-data-mono workspace-v2-market-price workspace-v2-market-price-bid"
                    )
                    self._bid_size_label = ui.label("").classes("workspace-v2-kv workspace-v2-data-mono")

                # Ask column
                with ui.column().classes("items-end"):
                    ui.label("ASK").classes("workspace-v2-field-label")
                    self._ask_price_label = ui.label("--").classes(
                        "workspace-v2-data-mono workspace-v2-market-price workspace-v2-market-price-ask"
                    )
                    self._ask_size_label = ui.label("").classes("workspace-v2-kv workspace-v2-data-mono")

            # Spread row
            with ui.row().classes("w-full items-center justify-between mt-1"):
                ui.label("Spread").classes("workspace-v2-field-label")
                self._spread_label = ui.label("--").classes("workspace-v2-kv workspace-v2-data-mono")

            # Last price & change
            ui.separator().classes("my-1")
            with ui.row().classes("w-full items-center justify-between"):
                with ui.column().classes("items-start"):
                    ui.label("Last").classes("workspace-v2-field-label")
                    self._last_price_label = ui.label("--").classes(
                        "workspace-v2-data-mono workspace-v2-market-price workspace-v2-market-price-last"
                    )
                self._change_badge = ui.badge("N/A").classes(
                    "workspace-v2-pill workspace-v2-pill-muted workspace-v2-market-badge"
                )

            # Volume
            with ui.row().classes("w-full items-center justify-between mt-1"):
                ui.label("Volume").classes("workspace-v2-field-label")
                self._volume_label = ui.label("--").classes("workspace-v2-kv workspace-v2-data-mono")

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
        """
        if self._disposed:
            return

        quote_snapshot, bar_snapshot = await self._fetch_initial_snapshots(symbol)
        snapshot = self._merge_snapshots(symbol, quote_snapshot, bar_snapshot)
        if snapshot is not None and symbol == self._current_symbol:
            self._data = snapshot
            self._update_ui()
            return

        logger.debug(f"Waiting for price data for {symbol} via callback")

    async def _fetch_initial_snapshots(
        self, symbol: str
    ) -> tuple[MarketDataSnapshot | None, MarketDataSnapshot | None]:
        """Fetch real top-of-book and OHLCV seeds without blocking on one source."""
        quote_result, bar_result = await asyncio.gather(
            self._fetch_latest_quote_snapshot(symbol),
            self._fetch_latest_bar_snapshot(symbol),
            return_exceptions=True,
        )
        if isinstance(quote_result, Exception):
            logger.debug(
                "market_context_quote_seed_failed",
                extra={"symbol": symbol, "error_type": type(quote_result).__name__},
            )
        if isinstance(bar_result, Exception):
            logger.debug(
                "market_context_bar_seed_failed",
                extra={"symbol": symbol, "error_type": type(bar_result).__name__},
            )
        quote_snapshot = quote_result if isinstance(quote_result, MarketDataSnapshot) else None
        bar_snapshot = bar_result if isinstance(bar_result, MarketDataSnapshot) else None
        return quote_snapshot, bar_snapshot

    @staticmethod
    def _merge_snapshots(
        symbol: str,
        quote_snapshot: MarketDataSnapshot | None,
        bar_snapshot: MarketDataSnapshot | None,
    ) -> MarketDataSnapshot | None:
        """Merge quote and bar seeds into a single Level 1 display snapshot."""
        if quote_snapshot is None and bar_snapshot is None:
            return None
        return MarketDataSnapshot(
            symbol=symbol,
            bid_price=quote_snapshot.bid_price if quote_snapshot is not None else None,
            ask_price=quote_snapshot.ask_price if quote_snapshot is not None else None,
            bid_size=quote_snapshot.bid_size if quote_snapshot is not None else None,
            ask_size=quote_snapshot.ask_size if quote_snapshot is not None else None,
            last_price=bar_snapshot.last_price if bar_snapshot is not None else None,
            prev_close=bar_snapshot.prev_close if bar_snapshot is not None else None,
            volume=bar_snapshot.volume if bar_snapshot is not None else None,
            timestamp=(
                quote_snapshot.timestamp
                if quote_snapshot is not None and quote_snapshot.timestamp is not None
                else bar_snapshot.timestamp
                if bar_snapshot is not None
                else None
            ),
        )

    async def _fetch_latest_quote_snapshot(self, symbol: str) -> MarketDataSnapshot | None:
        """Use latest real quote to seed bid/ask/spread before live ticks arrive."""
        fetch_latest_quote = getattr(self._client, "fetch_latest_quote", None)
        if fetch_latest_quote is None or not self._user_id:
            return None

        response = await call_market_data_client(
            fetch_latest_quote,
            request_kwargs={"symbol": symbol},
            user_id=self._user_id,
            role=self._role,
            strategies=self._strategies,
            logger=logger,
            operation="market_context_latest_quote",
            symbol=symbol,
        )
        if response is None:
            return None

        if not isinstance(response, dict):
            return None

        try:
            bid_raw = self._mapping_value(response, "bid_price", "bid")
            ask_raw = self._mapping_value(response, "ask_price", "ask")
            bid = Decimal(str(bid_raw)) if bid_raw is not None else None
            ask = Decimal(str(ask_raw)) if ask_raw is not None else None
            if bid is not None and (not bid.is_finite() or bid <= 0):
                bid = None
            if ask is not None and (not ask.is_finite() or ask <= 0):
                ask = None
            if bid is None and ask is None:
                return None
            bid_size_raw = response.get("bid_size")
            ask_size_raw = response.get("ask_size")
            timestamp_raw = response.get("timestamp")
            timestamp = parse_iso_timestamp(str(timestamp_raw)) if timestamp_raw else None
        except (InvalidOperation, ValueError, TypeError) as exc:
            logger.debug(
                "market_context_quote_parse_failed",
                extra={"symbol": symbol, "error_type": type(exc).__name__},
            )
            return None

        return MarketDataSnapshot(
            symbol=symbol,
            bid_price=bid,
            ask_price=ask,
            bid_size=int(bid_size_raw) if bid_size_raw is not None else None,
            ask_size=int(ask_size_raw) if ask_size_raw is not None else None,
            last_price=None,
            timestamp=timestamp,
        )

    @staticmethod
    def _mapping_value(data: dict[str, Any], primary_key: str, fallback_key: str) -> Any:
        """Return primary mapping value, preserving valid falsy numeric values."""
        value = data.get(primary_key)
        return value if value is not None else data.get(fallback_key)

    async def _fetch_latest_bar_snapshot(self, symbol: str) -> MarketDataSnapshot | None:
        """Use bars to seed last traded price and daily volume before live ticks arrive."""
        intraday_response, daily_response = await asyncio.gather(
            self._fetch_latest_bar_response(symbol, "5Min"),
            self._fetch_latest_bar_response(symbol, "1Day"),
        )
        intraday_bar = self._latest_bar_from_response(intraday_response)
        daily_bar = self._latest_bar_from_response(daily_response)
        if intraday_bar is None and daily_bar is None:
            return None

        try:
            close, timestamp = self._parse_bar_price(intraday_bar, symbol)
            volume_raw = daily_bar.get("volume") if daily_bar is not None else None
            volume = int(volume_raw) if volume_raw is not None else None
        except (InvalidOperation, ValueError, TypeError) as exc:
            logger.debug(
                "market_context_bar_parse_failed",
                extra={"symbol": symbol, "error_type": type(exc).__name__},
            )
            return None

        return MarketDataSnapshot(
            symbol=symbol,
            last_price=close,
            volume=volume,
            timestamp=timestamp,
        )

    async def _fetch_latest_bar_response(self, symbol: str, timeframe: str) -> dict[str, Any] | None:
        """Fetch one latest bar for a timeframe using shared auth fallback."""
        fetch_historical_bars = getattr(self._client, "fetch_historical_bars", None)
        if fetch_historical_bars is None or not self._user_id:
            return None
        response = await call_market_data_client(
            fetch_historical_bars,
            request_kwargs={"symbol": symbol, "timeframe": timeframe, "limit": 1},
            user_id=self._user_id,
            role=self._role,
            strategies=self._strategies,
            logger=logger,
            operation="market_context_latest_bar",
            symbol=symbol,
            extra={"timeframe": timeframe},
        )
        return response if isinstance(response, dict) else None

    @staticmethod
    def _latest_bar_from_response(response: dict[str, Any] | None) -> dict[str, Any] | None:
        """Extract the latest bar dict from a web-console bars response."""
        bars = response.get("bars") if isinstance(response, dict) else None
        if not isinstance(bars, list) or not bars:
            return None
        latest = bars[-1]
        return latest if isinstance(latest, dict) else None

    @staticmethod
    def _parse_bar_price(bar: dict[str, Any] | None, symbol: str) -> tuple[Decimal | None, datetime | None]:
        """Parse last trade proxy from an intraday bar."""
        if bar is None:
            return None, None
        close_raw = bar.get("close")
        timestamp_raw = bar.get("timestamp")
        if close_raw is None or timestamp_raw is None:
            return None, None
        close = Decimal(str(close_raw))
        if not close.is_finite() or close <= 0:
            logger.debug(
                "market_context_bar_non_positive_close",
                extra={"symbol": symbol},
            )
            return None, None
        return close, parse_iso_timestamp(str(timestamp_raw))

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
        existing = self._data
        def first_decimal(primary_key: str, fallback_key: str) -> Decimal | None:
            value = safe_decimal(primary_key)
            return value if value is not None else safe_decimal(fallback_key)

        bid_price = first_decimal("bid", "bid_price")
        ask_price = first_decimal("ask", "ask_price")
        bid_size = safe_int("bid_size")
        ask_size = safe_int("ask_size")
        last_price = first_decimal("price", "last_price")
        prev_close = safe_decimal("prev_close")
        parsed_volume = safe_int("volume")
        timestamp = safe_timestamp("timestamp")
        same_symbol_existing = existing if existing is not None and existing.symbol == symbol else None
        self._data = MarketDataSnapshot(
            symbol=symbol,
            bid_price=(
                bid_price
                if bid_price is not None
                else (same_symbol_existing.bid_price if same_symbol_existing else None)
            ),
            ask_price=(
                ask_price
                if ask_price is not None
                else (same_symbol_existing.ask_price if same_symbol_existing else None)
            ),
            bid_size=(
                bid_size
                if bid_size is not None
                else (same_symbol_existing.bid_size if same_symbol_existing else None)
            ),
            ask_size=(
                ask_size
                if ask_size is not None
                else (same_symbol_existing.ask_size if same_symbol_existing else None)
            ),
            last_price=(
                last_price
                if last_price is not None
                else (same_symbol_existing.last_price if same_symbol_existing else None)
            ),
            prev_close=(
                prev_close
                if prev_close is not None
                else (same_symbol_existing.prev_close if same_symbol_existing else None)
            ),
            volume=(
                parsed_volume
                if parsed_volume is not None
                else (same_symbol_existing.volume if same_symbol_existing else None)
            ),
            timestamp=(
                timestamp
                if timestamp is not None
                else (same_symbol_existing.timestamp if same_symbol_existing else None)
            ),
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
                    self._set_badge_tone(self._change_badge, tone="positive")
                else:
                    self._set_badge_tone(self._change_badge, tone="negative")
            else:
                self._change_badge.set_text("N/A")
                self._set_badge_tone(self._change_badge, tone="muted")

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
            self._set_badge_tone(self._change_badge, tone="muted")
        if self._volume_label:
            self._volume_label.set_text("--")
        if self._staleness_badge:
            self._staleness_badge.set_text("No data")
            self._set_badge_tone(self._staleness_badge, tone="muted")

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
            self._set_badge_tone(self._staleness_badge, tone="muted")
            return

        age_s = (datetime.now(UTC) - timestamp).total_seconds()

        if age_s < 5:
            self._staleness_badge.set_text("Live")
            self._set_badge_tone(self._staleness_badge, tone="positive")
        elif age_s < STALE_THRESHOLD_S:
            self._staleness_badge.set_text(f"{int(age_s)}s ago")
            self._set_badge_tone(self._staleness_badge, tone="warning")
        else:
            self._staleness_badge.set_text(f"Stale ({int(age_s)}s)")
            self._set_badge_tone(self._staleness_badge, tone="negative")

    def _set_badge_tone(self, badge: ui.badge | None, *, tone: str) -> None:
        """Apply workspace pill tone to a badge."""
        if badge is None:
            return
        badge.classes(
            remove=(
                "workspace-v2-pill-positive workspace-v2-pill-warning "
                "workspace-v2-pill-negative workspace-v2-pill-muted "
                "bg-green-500 bg-yellow-500 bg-red-500 bg-gray-500 text-black text-white"
            )
        )
        if tone == "positive":
            badge.classes(add="workspace-v2-pill-positive")
        elif tone == "negative":
            badge.classes(add="workspace-v2-pill-negative")
        elif tone == "warning":
            badge.classes(add="workspace-v2-pill-warning")
        else:
            badge.classes(add="workspace-v2-pill-muted")

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
