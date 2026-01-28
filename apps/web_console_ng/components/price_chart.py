"""Interactive price chart with execution markers.

SUBSCRIPTION OWNERSHIP: PriceChart does NOT subscribe to Redis.
OrderEntryContext owns all subscriptions and dispatches updates via callbacks.

Data Flow: Redis → RealtimeUpdater → OrderEntryContext → PriceChart.set_price_data()
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from nicegui import ui

from apps.web_console_ng.ui.lightweight_charts import (
    CHART_INIT_JS,
    LightweightChartsLoader,
)
from apps.web_console_ng.utils.time import parse_iso_timestamp

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.web_console_ng.core.client import AsyncTradingClient

logger = logging.getLogger(__name__)

# Staleness thresholds
REALTIME_STALE_THRESHOLD_S = 60  # Show warning if no updates for 60s
REALTIME_FALLBACK_THRESHOLD_S = 180  # Show fallback chart after 3 minutes


@dataclass
class CandleData:
    """Single candle data point."""

    time: int  # Unix timestamp
    open: float
    high: float
    low: float
    close: float
    volume: int | None = None


@dataclass
class ExecutionMarker:
    """Execution marker for chart overlay."""

    time: int  # Unix timestamp
    price: float
    side: Literal["buy", "sell"]
    quantity: int
    order_id: str


class PriceChartComponent:
    """Interactive price chart with execution markers.

    SUBSCRIPTION OWNERSHIP: This component does NOT subscribe to Redis directly.
    All price updates come via set_price_data() callback from OrderEntryContext.
    """

    DEFAULT_TIMEFRAME = "1D"  # 1 day of data
    CANDLE_INTERVAL = "5m"  # 5-minute candles

    def __init__(
        self,
        trading_client: AsyncTradingClient,
    ) -> None:
        """Initialize PriceChart.

        Args:
            trading_client: HTTP client for API calls.
        """
        self._client = trading_client
        self._current_symbol: str | None = None
        self._chart_id: str = f"chart_{id(self)}"
        self._container_id: str = f"container_{id(self)}"
        self._candles: list[CandleData] = []
        self._markers: list[ExecutionMarker] = []
        self._timer_tracker: Callable[[ui.timer], None] | None = None
        self._last_realtime_update: datetime | None = None
        self._symbol_changed_at: datetime | None = None  # Track when symbol changed for staleness
        self._width: int = 600  # Default, overridden by create()
        self._height: int = 300  # Default, overridden by create()
        self._disposed: bool = False
        self._staleness_timer: ui.timer | None = None

        # Track pending update tasks for cleanup on dispose (prevent task leaks)
        self._pending_update_tasks: set[asyncio.Task[None]] = set()

    async def initialize(self, timer_tracker: Callable[[ui.timer], None]) -> None:
        """Initialize the chart component with timer tracking.

        Args:
            timer_tracker: Callback to register timers with OrderEntryContext
                          for lifecycle management.

        CRITICAL: Timers are created HERE (not in create()) to ensure timer_tracker
        is available for registration. create() only builds UI elements.
        """
        self._timer_tracker = timer_tracker

        # Initialize chart via one-shot timer (tracked)
        async def init_chart() -> None:
            if self._disposed:
                return
            try:
                await LightweightChartsLoader.ensure_loaded()
                await ui.run_javascript(
                    CHART_INIT_JS.format(
                        container_id=self._container_id,
                        chart_id=self._chart_id,
                        width=self._width,
                        height=self._height,
                    )
                )
            except Exception as exc:
                logger.warning(f"Failed to initialize chart: {exc}")

        init_timer = ui.timer(0.1, init_chart, once=True)
        timer_tracker(init_timer)

        # Start realtime staleness monitor (tracked via timer_tracker)
        self._start_realtime_staleness_monitor(timer_tracker)

    def create(self, width: int = 600, height: int = 300) -> ui.html:
        """Create the chart container.

        NOTE: Does NOT start timers. Timer for chart initialization is started
        in initialize() to ensure timer_tracker is available.
        """
        # Store dimensions for initialization
        self._width = width
        self._height = height

        # Container div
        container = ui.html(
            f'<div id="{self._container_id}" ' f'style="width:100%;height:{height}px;"></div>'
        )

        return container

    # ================= Symbol Management =================

    async def on_symbol_changed(self, symbol: str | None) -> None:
        """Called by OrderEntryContext when selected symbol changes.

        NOTE: PriceChart does NOT subscribe to Redis directly.
        It receives price updates via set_price_data() callback from OrderEntryContext.

        SUBSCRIPTION OWNERSHIP: OrderEntryContext owns all subscriptions.

        CRITICAL: Must reset realtime update timestamp to prevent stale
        badge state from previous symbol affecting new symbol display.

        RACE PREVENTION: After each async operation, validate symbol is still current
        before updating state/UI to prevent stale fetches from overwriting newer data.
        """
        self._current_symbol = symbol

        # CRITICAL: Reset realtime update timestamp for staleness tracking
        # Without this, staleness badge would show wrong age for new symbol
        self._last_realtime_update = None
        # Track when symbol changed to detect dead feed (no valid updates ever)
        self._symbol_changed_at = datetime.now(UTC) if symbol else None

        if not symbol:
            await self._clear_chart()
            return

        # Fetch historical data
        candles = await self._fetch_candle_data(symbol)

        # RACE CHECK: Ensure symbol hasn't changed during candle fetch
        if symbol != self._current_symbol:
            return  # Stale - symbol changed during fetch

        self._candles = candles

        # Fetch execution history for markers
        markers = await self._fetch_execution_markers(symbol)

        # RACE CHECK: Ensure symbol hasn't changed during marker fetch
        if symbol != self._current_symbol:
            return  # Stale - symbol changed during fetch

        self._markers = markers

        # Update chart (only if symbol still current)
        await self._update_chart_data()

        # NOTE: No direct Redis subscription here!
        # Real-time updates come via set_price_data() callback from OrderEntryContext

    # ================= Price Data Callbacks =================

    def set_price_data(self, data: dict[str, Any]) -> None:
        """Called by OrderEntryContext when price data updates.

        This is the ONLY way PriceChart receives real-time price updates.
        PriceChart does NOT subscribe directly to price.updated.{symbol} channel.

        STALENESS TRACKING: Only update _last_realtime_update AFTER validating
        that price data is present and parseable. Invalid updates should NOT
        suppress stale/fallback overlays.
        """
        if self._disposed:
            return

        # TYPE GUARD: Validate payload structure
        if not isinstance(data, dict):
            logger.warning(f"PriceChart received invalid data type: {type(data).__name__}")
            return  # Silently ignore malformed data (display-only component)

        # Check symbol matches current selection
        data_symbol = data.get("symbol")
        if data_symbol and data_symbol != self._current_symbol:
            return  # Ignore data for different symbol

        # VALIDATE price before updating staleness timestamp
        # This ensures invalid ticks don't mask stale data
        raw_price = data.get("price")
        if raw_price is None:
            logger.debug("PriceChart: tick missing price, not updating staleness")
            return

        try:
            price = float(raw_price)
            # float() doesn't reject inf/nan by default, so check explicitly
            if not math.isfinite(price) or price <= 0:
                logger.warning(
                    f"PriceChart: invalid/non-finite price {price}, not updating staleness"
                )
                return
        except (ValueError, TypeError):
            logger.warning(f"PriceChart: unparseable price {raw_price!r}, not updating staleness")
            return

        # Only update staleness timestamp AFTER validating price is valid
        # FAIL-CLOSED: REQUIRE SERVER TIMESTAMP - do NOT use datetime.now() as fallback.
        # If timestamp is missing/invalid, set to None and keep/show stale overlay.
        raw_timestamp = data.get("timestamp")
        if raw_timestamp:
            try:
                self._last_realtime_update = parse_iso_timestamp(str(raw_timestamp))
            except (ValueError, TypeError):
                # Invalid timestamp format - FAIL-CLOSED: treat as missing
                logger.debug(f"PriceChart: unparseable timestamp {raw_timestamp!r}, keeping stale")
                self._last_realtime_update = None  # Will show stale overlay
        else:
            # No timestamp in payload - FAIL-CLOSED: treat as stale
            logger.debug("PriceChart: missing timestamp, keeping stale overlay")
            self._last_realtime_update = None

        # Process the price update
        # Track task for cleanup on dispose (avoid task leaks)
        task = asyncio.create_task(self._handle_price_update(price))
        self._pending_update_tasks.add(task)
        task.add_done_callback(lambda t: self._pending_update_tasks.discard(t))

    async def _handle_price_update(self, price: float) -> None:
        """Process incoming price update and update chart."""
        if self._disposed or not self._candles:
            return

        # Hide stale overlay on valid update
        if self._last_realtime_update:
            await self._hide_stale_overlay()

        # Update the last candle's close (simplified real-time update)
        last_candle = self._candles[-1]
        updated_candle = {
            "time": last_candle.time,
            "open": last_candle.open,
            "high": max(last_candle.high, price),
            "low": min(last_candle.low, price),
            "close": price,
        }

        # Update internal state
        self._candles[-1] = CandleData(
            time=last_candle.time,
            open=last_candle.open,
            high=max(last_candle.high, price),
            low=min(last_candle.low, price),
            close=price,
            volume=last_candle.volume,
        )

        try:
            await ui.run_javascript(
                f"""
                const chartRef = window.__charts['{self._chart_id}'];
                if (chartRef) {{
                    chartRef.candlestickSeries.update({json.dumps(updated_candle)});
                }}
            """
            )
        except Exception as exc:
            logger.debug(f"Failed to update chart: {exc}")

    # ================= Data Fetching =================

    async def _fetch_candle_data(self, symbol: str) -> list[CandleData]:
        """Fetch historical candle data.

        NOTE: This is a stub implementation. Full implementation requires
        adding fetch_historical_bars() to AsyncTradingClient and exposing
        an endpoint that proxies to Alpaca bars API.

        Returns empty list if API not available, triggering fallback UI.
        """
        if self._disposed:
            return []

        # TODO: Implement when fetch_historical_bars is available
        # For now, return empty to trigger fallback UI
        logger.debug(f"Historical bars API not implemented, showing fallback for {symbol}")

        # Show fallback UI since we can't fetch data
        await self._show_fallback_chart(symbol)
        return []

    async def _fetch_execution_markers(self, symbol: str) -> list[ExecutionMarker]:
        """Fetch today's executions for chart markers.

        NOTE: Uses fetch_recent_fills which returns all recent fills.
        We filter client-side by symbol since the API doesn't support symbol filter.
        """
        if self._disposed:
            return []

        try:
            # Check if fetch_recent_fills exists on client
            if not hasattr(self._client, "fetch_recent_fills"):
                logger.debug("fetch_recent_fills not available, skipping markers")
                return []

            # Use existing fetch_recent_fills (GET /api/v1/orders/recent-fills)
            fills_resp = await self._client.fetch_recent_fills(limit=100)
            fills = fills_resp.get("fills", [])

            markers: list[ExecutionMarker] = []
            for fill in fills:
                if fill.get("symbol") != symbol:
                    continue

                try:
                    marker = ExecutionMarker(
                        time=int(parse_iso_timestamp(fill["filled_at"]).timestamp()),
                        price=float(fill["price"]),
                        side=fill["side"],
                        quantity=int(fill["qty"]),
                        order_id=fill.get("client_order_id", ""),
                    )
                    markers.append(marker)
                except (KeyError, ValueError, TypeError) as exc:
                    logger.debug(f"Skipping invalid fill data: {exc}")
                    continue

            return markers
        except Exception as exc:
            logger.warning(f"Failed to fetch execution markers for {symbol}: {exc}")
            return []

    # ================= Chart Updates =================

    async def _update_chart_data(self) -> None:
        """Update chart with candles and markers."""
        if self._disposed or not self._candles:
            return

        # Format candle data for JS
        candle_data = [
            {
                "time": c.time,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
            }
            for c in self._candles
        ]

        # Format markers for JS
        marker_data = [
            {
                "time": m.time,
                "position": "aboveBar" if m.side == "sell" else "belowBar",
                "color": "#ef5350" if m.side == "sell" else "#26a69a",
                "shape": "arrowDown" if m.side == "sell" else "arrowUp",
                "text": f"{m.side.upper()} {m.quantity}",
            }
            for m in self._markers
        ]

        try:
            await ui.run_javascript(
                f"""
                const chartRef = window.__charts['{self._chart_id}'];
                if (chartRef) {{
                    chartRef.candlestickSeries.setData({json.dumps(candle_data)});
                    chartRef.candlestickSeries.setMarkers({json.dumps(marker_data)});
                }}
            """
            )
        except Exception as exc:
            logger.debug(f"Failed to update chart data: {exc}")

    async def _clear_chart(self) -> None:
        """Clear the chart display."""
        if self._disposed:
            return

        self._candles = []
        self._markers = []

        try:
            await ui.run_javascript(
                f"""
                const chartRef = window.__charts['{self._chart_id}'];
                if (chartRef) {{
                    chartRef.candlestickSeries.setData([]);
                    chartRef.candlestickSeries.setMarkers([]);
                }}
            """
            )
        except Exception as exc:
            logger.debug(f"Failed to clear chart: {exc}")

    # ================= VWAP/TWAP Overlays =================

    async def add_vwap_overlay(self, vwap_data: list[dict[str, Any]]) -> None:
        """Add VWAP line overlay to chart.

        VWAP = Cumulative(Price * Volume) / Cumulative(Volume)
        """
        if self._disposed or not vwap_data:
            return

        # Format for line series
        line_data = [{"time": d["time"], "value": d["vwap"]} for d in vwap_data]

        try:
            await ui.run_javascript(
                f"""
                const chartRef = window.__charts['{self._chart_id}'];
                if (chartRef) {{
                    // Remove existing VWAP if present
                    if (chartRef.vwapSeries) {{
                        chartRef.chart.removeSeries(chartRef.vwapSeries);
                    }}

                    // Add VWAP line series
                    chartRef.vwapSeries = chartRef.chart.addLineSeries({{
                        color: '#2196F3',
                        lineWidth: 2,
                        title: 'VWAP',
                    }});
                    chartRef.vwapSeries.setData({json.dumps(line_data)});
                }}
            """
            )
        except Exception as exc:
            logger.debug(f"Failed to add VWAP overlay: {exc}")

    async def add_twap_overlay(self, twap_data: list[dict[str, Any]]) -> None:
        """Add TWAP line overlay to chart.

        TWAP = Simple average of prices over time intervals
        """
        if self._disposed or not twap_data:
            return

        # Format for line series
        line_data = [{"time": d["time"], "value": d["twap"]} for d in twap_data]

        try:
            await ui.run_javascript(
                f"""
                const chartRef = window.__charts['{self._chart_id}'];
                if (chartRef) {{
                    // Remove existing TWAP if present
                    if (chartRef.twapSeries) {{
                        chartRef.chart.removeSeries(chartRef.twapSeries);
                    }}

                    // Add TWAP line series (orange to distinguish from VWAP)
                    chartRef.twapSeries = chartRef.chart.addLineSeries({{
                        color: '#FF9800',
                        lineWidth: 2,
                        lineStyle: 1,  // Dashed
                        title: 'TWAP',
                    }});
                    chartRef.twapSeries.setData({json.dumps(line_data)});
                }}
            """
            )
        except Exception as exc:
            logger.debug(f"Failed to add TWAP overlay: {exc}")

    def calculate_vwap_from_candles(self) -> list[dict[str, Any]]:
        """Calculate VWAP from candle data."""
        if not self._candles:
            return []

        vwap_data = []
        cumulative_volume = 0
        cumulative_pv = 0.0  # Price * Volume

        for candle in self._candles:
            typical_price = (candle.high + candle.low + candle.close) / 3
            volume = candle.volume or 0

            cumulative_volume += volume
            cumulative_pv += typical_price * volume

            if cumulative_volume > 0:
                vwap = cumulative_pv / cumulative_volume
                vwap_data.append({"time": candle.time, "vwap": vwap})

        return vwap_data

    def calculate_twap_from_candles(self) -> list[dict[str, Any]]:
        """Calculate TWAP from candle data (simple time-weighted average)."""
        if not self._candles:
            return []

        twap_data = []
        cumulative_price = 0.0
        count = 0

        for candle in self._candles:
            count += 1
            cumulative_price += candle.close
            twap = cumulative_price / count
            twap_data.append({"time": candle.time, "twap": twap})

        return twap_data

    # ================= Staleness Detection =================

    def _start_realtime_staleness_monitor(self, tracker: Callable[[ui.timer], None]) -> None:
        """Start timer to check for realtime feed staleness.

        Args:
            tracker: Callback to register timer with OrderEntryContext for lifecycle.
        """

        def check_staleness_tracked() -> None:
            """Create tracked task for staleness check (prevent task leaks)."""
            if self._disposed:
                return
            task = asyncio.create_task(self._check_realtime_staleness())
            self._pending_update_tasks.add(task)
            task.add_done_callback(lambda t: self._pending_update_tasks.discard(t))

        self._staleness_timer = ui.timer(
            10.0,  # Check every 10 seconds
            check_staleness_tracked,
        )
        tracker(self._staleness_timer)  # Register for cleanup

    async def _check_realtime_staleness(self) -> None:
        """Check if realtime feed is stale and update UI accordingly.

        Uses _last_realtime_update if valid data has been received,
        otherwise falls back to _symbol_changed_at to detect dead feeds
        that never deliver valid timestamps.
        """
        if self._disposed or not self._current_symbol:
            return

        # Use last valid update time, or symbol change time as fallback
        # This ensures we can detect dead feeds that never send valid data
        reference_time = self._last_realtime_update or self._symbol_changed_at
        if not reference_time:
            return

        age_s = (datetime.now(UTC) - reference_time).total_seconds()

        if age_s > REALTIME_FALLBACK_THRESHOLD_S:
            # Feed is dead - show fallback
            await self._show_stale_fallback_overlay()
        elif age_s > REALTIME_STALE_THRESHOLD_S:
            # Feed is stale - show warning overlay
            await self._show_stale_overlay(int(age_s))

    async def _show_stale_overlay(self, age_s: int) -> None:
        """Show stale data warning overlay on chart."""
        if self._disposed:
            return

        try:
            await ui.run_javascript(
                f"""
                const container = document.getElementById('{self._container_id}');
                if (!container) return;
                let overlay = container.querySelector('.stale-overlay');
                if (!overlay) {{
                    overlay = document.createElement('div');
                    overlay.className = 'stale-overlay';
                    overlay.style.cssText = 'position:absolute;top:0;left:0;right:0;background:rgba(255,165,0,0.9);color:#000;padding:4px;text-align:center;font-size:12px;z-index:100;';
                    container.appendChild(overlay);
                }}
                overlay.textContent = 'Real-time feed stale ({age_s}s) - Data may be outdated';
                overlay.style.display = 'block';
            """
            )
        except Exception as exc:
            logger.debug(f"Failed to show stale overlay: {exc}")

    async def _hide_stale_overlay(self) -> None:
        """Hide stale data warning overlay."""
        if self._disposed:
            return

        try:
            await ui.run_javascript(
                f"""
                const container = document.getElementById('{self._container_id}');
                const overlay = container?.querySelector('.stale-overlay');
                if (overlay) overlay.style.display = 'none';
                const fallback = container?.querySelector('.fallback-overlay');
                if (fallback) fallback.style.display = 'none';
            """
            )
        except Exception as exc:
            logger.debug(f"Failed to hide stale overlay: {exc}")

    async def _show_stale_fallback_overlay(self) -> None:
        """Show fallback UI when realtime feed is dead.

        Uses DOM construction (not innerHTML) for consistent XSS safety.
        """
        if self._disposed:
            return

        try:
            await ui.run_javascript(
                f"""
                const container = document.getElementById('{self._container_id}');
                if (!container) return;
                let overlay = container.querySelector('.fallback-overlay');
                if (!overlay) {{
                    overlay = document.createElement('div');
                    overlay.className = 'fallback-overlay';
                    overlay.style.cssText = 'position:absolute;top:0;left:0;right:0;bottom:0;background:rgba(30,30,30,0.95);display:flex;align-items:center;justify-content:center;z-index:200;';

                    // Use DOM construction (not innerHTML) for XSS safety
                    const contentDiv = document.createElement('div');
                    contentDiv.style.cssText = 'text-align:center;color:#888;';

                    const iconDiv = document.createElement('div');
                    iconDiv.style.cssText = 'font-size:32px;margin-bottom:8px;';
                    iconDiv.textContent = '📊';

                    const titleDiv = document.createElement('div');
                    titleDiv.style.fontSize = '14px';
                    titleDiv.textContent = 'Real-time feed unavailable';

                    const subtitleDiv = document.createElement('div');
                    subtitleDiv.style.cssText = 'font-size:12px;color:#666;margin-top:4px;';
                    subtitleDiv.textContent = 'Chart data may be outdated';

                    const dismissBtn = document.createElement('button');
                    dismissBtn.style.cssText = 'margin-top:12px;padding:4px 12px;cursor:pointer;';
                    dismissBtn.textContent = 'Dismiss';
                    dismissBtn.onclick = () => overlay.style.display = 'none';

                    contentDiv.appendChild(iconDiv);
                    contentDiv.appendChild(titleDiv);
                    contentDiv.appendChild(subtitleDiv);
                    contentDiv.appendChild(dismissBtn);
                    overlay.appendChild(contentDiv);
                    container.appendChild(overlay);
                }}
                overlay.style.display = 'flex';
            """
            )
        except Exception as exc:
            logger.debug(f"Failed to show fallback overlay: {exc}")

    async def _show_fallback_chart(self, symbol: str) -> None:
        """Show static chart when historical data unavailable.

        SECURITY: Symbol is JSON-encoded for safe JS interpolation.
        """
        if self._disposed:
            return

        # JSON-encode for safe JS interpolation (escapes backticks, ${}, etc.)
        js_safe_symbol = json.dumps(symbol)

        try:
            await ui.run_javascript(
                f"""
                const container = document.getElementById('{self._container_id}');
                const symbolForDisplay = {js_safe_symbol};
                if (container) {{
                    // Use textContent for the symbol to avoid innerHTML XSS
                    const messageDiv = document.createElement('div');
                    messageDiv.style.cssText = `
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        height: 100%;
                        background: #1e1e1e;
                        color: #888;
                        font-size: 14px;
                    `;
                    const contentDiv = document.createElement('div');
                    contentDiv.style.textAlign = 'center';

                    const iconDiv = document.createElement('div');
                    iconDiv.style.cssText = 'font-size: 24px; margin-bottom: 8px;';
                    iconDiv.textContent = '📊';

                    const symbolDiv = document.createElement('div');
                    symbolDiv.textContent = 'Chart unavailable for ' + symbolForDisplay;

                    const feedDiv = document.createElement('div');
                    feedDiv.style.cssText = 'font-size: 12px; color: #666;';
                    feedDiv.textContent = 'Real-time feed not connected';

                    contentDiv.appendChild(iconDiv);
                    contentDiv.appendChild(symbolDiv);
                    contentDiv.appendChild(feedDiv);
                    messageDiv.appendChild(contentDiv);

                    container.innerHTML = '';
                    container.appendChild(messageDiv);
                }}
            """
            )
        except Exception as exc:
            logger.debug(f"Failed to show fallback chart: {exc}")

    # ================= Getters =================

    def get_current_price(self) -> float | None:
        """Get current price from last candle."""
        if self._candles:
            return self._candles[-1].close
        return None

    def is_data_stale(self) -> bool:
        """Check if current data is stale."""
        if not self._last_realtime_update:
            return True
        age_s = (datetime.now(UTC) - self._last_realtime_update).total_seconds()
        return age_s > REALTIME_STALE_THRESHOLD_S

    # ================= Cleanup =================

    async def dispose(self) -> None:
        """Clean up chart component resources.

        Called by OrderEntryContext on page unload.
        Clears chart rendering, cancels pending tasks, and clears state.
        """
        self._disposed = True

        # Cancel staleness timer
        if self._staleness_timer:
            self._staleness_timer.cancel()

        # Cancel any pending update tasks to prevent state updates after dispose
        for task in list(self._pending_update_tasks):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._pending_update_tasks.clear()

        # Clear chart by removing from DOM if needed
        if self._container_id:
            try:
                await ui.run_javascript(
                    f"""
                    const container = document.getElementById('{self._container_id}');
                    if (container) {{ container.innerHTML = ''; }}
                    if (window.__charts && window.__charts['{self._chart_id}']) {{
                        delete window.__charts['{self._chart_id}'];
                    }}
                """
                )
            except Exception:
                pass  # Best effort cleanup

        # Clear internal state
        self._current_symbol = None
        self._candles = []
        self._markers = []


__all__ = [
    "PriceChartComponent",
    "CandleData",
    "ExecutionMarker",
    "REALTIME_STALE_THRESHOLD_S",
    "REALTIME_FALLBACK_THRESHOLD_S",
]
