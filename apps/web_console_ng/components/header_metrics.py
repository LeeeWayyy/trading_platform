"""Header metrics component for NLV, leverage, and day change display.

This component displays critical account metrics in a compact format for the header bar.
It is designed to be isolated from kill switch/circuit breaker updates - failures here
must NEVER affect connection state or critical safety controls.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from nicegui import app, ui

if TYPE_CHECKING:
    from apps.web_console_ng.core.client import AsyncTradingClient

logger = logging.getLogger(__name__)

# Timezone for trading day boundary
ET_TIMEZONE = ZoneInfo("America/New_York")

# Thresholds
LEVERAGE_GREEN_MAX = 2.0  # < 2x = green
LEVERAGE_YELLOW_MAX = 3.0  # 2-3x = yellow, > 3x = red
STALE_THRESHOLD_SECONDS = 30.0
METRICS_FETCH_TIMEOUT = 4.0


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Safely convert a value to float, handling None and strings."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _format_currency(value: float) -> str:
    """Format currency value with appropriate suffix (K, M, B)."""
    abs_value = abs(value)
    sign = "-" if value < 0 else ""
    if abs_value >= 1_000_000_000:
        return f"{sign}${abs_value / 1_000_000_000:.2f}B"
    if abs_value >= 1_000_000:
        return f"{sign}${abs_value / 1_000_000:.2f}M"
    if abs_value >= 1_000:
        return f"{sign}${abs_value / 1_000:.1f}K"
    return f"{sign}${abs_value:.0f}"


def _format_day_change(value: float, pct: float | None) -> str:
    """Format day change with value and percentage."""
    sign = "+" if value >= 0 else "-"
    # Format absolute value to avoid string manipulation of sign
    formatted_value = _format_currency(abs(value))
    if pct is not None:
        # Percentage sign: + for positive (negative already has - from the number)
        pct_sign = "+" if pct >= 0 else ""
        return f"{sign}{formatted_value} ({pct_sign}{pct:.1f}%)"
    return f"{sign}{formatted_value}"


def _get_trading_date_key() -> str:
    """Get the current trading date key in ET timezone.

    Returns format: nlv_baseline_YYYY-MM-DD
    Resets at 00:00 ET (handles DST automatically via ZoneInfo).
    """
    now_et = datetime.now(ET_TIMEZONE)
    return f"nlv_baseline_{now_et.strftime('%Y-%m-%d')}"


class HeaderMetrics:
    """Compact header metrics display for NLV, leverage, and day change.

    This component is designed for isolation - all errors are caught internally
    and only affect the metrics display (mark as stale), never the connection
    state or kill switch/circuit breaker badges.

    Usage:
        metrics = HeaderMetrics()
        # In update loop (AFTER kill switch updates):
        try:
            await metrics.update(client, user_id, role, strategies)
        except Exception:
            pass  # Already handled internally
    """

    def __init__(self) -> None:
        """Initialize the header metrics UI elements."""
        self._nlv_label: ui.label | None = None
        self._leverage_label: ui.label | None = None
        self._day_change_label: ui.label | None = None
        self._last_update: float | None = None
        self._current_leverage_class: str = ""

        # Build compact UI
        self._build_ui()

    def _build_ui(self) -> None:
        """Build the compact metrics UI for the header."""
        with ui.row().classes("items-center gap-3 shrink-0"):
            # NLV display
            with ui.row().classes("items-center gap-1"):
                ui.label("NLV").classes("text-xs text-gray-400")
                self._nlv_label = ui.label("--").classes(
                    "text-sm font-medium text-white"
                )

            # Separator
            ui.label("|").classes("text-gray-500 text-xs")

            # Leverage display
            with ui.row().classes("items-center gap-1"):
                ui.label("Lev").classes("text-xs text-gray-400")
                self._leverage_label = ui.label("--").classes(
                    "text-sm font-medium px-1.5 py-0.5 rounded"
                )

            # Separator
            ui.label("|").classes("text-gray-500 text-xs")

            # Day change display
            with ui.row().classes("items-center gap-1"):
                ui.label("Day").classes("text-xs text-gray-400")
                self._day_change_label = ui.label("--").classes(
                    "text-sm font-medium text-white"
                )

    def _get_leverage_color_class(self, leverage: float) -> str:
        """Get the appropriate color class for leverage value."""
        if leverage < LEVERAGE_GREEN_MAX:
            return "bg-green-600 text-white"
        if leverage < LEVERAGE_YELLOW_MAX:
            return "bg-yellow-500 text-black"
        return "bg-red-600 text-white"

    def _calculate_leverage(
        self, positions: list[dict[str, Any]], nlv: float
    ) -> tuple[float | None, bool]:
        """Calculate gross leverage from positions.

        Args:
            positions: List of position dicts with market_value or qty/current_price
            nlv: Net liquidation value (portfolio_value)

        Returns:
            Tuple of (leverage_ratio, is_partial)
            - leverage_ratio is None if NLV <= 0
            - leverage_ratio is 0.0 if positions list is empty
            - is_partial is True if any positions were skipped due to missing data
        """
        if nlv <= 0:
            return None, False

        total_exposure = 0.0
        skipped_count = 0

        for pos in positions:
            # Prefer market_value if available
            market_value = _safe_float(pos.get("market_value"))
            if market_value != 0.0:
                total_exposure += abs(market_value)
                continue

            # Fallback: qty * current_price
            qty = _safe_float(pos.get("qty"))
            current_price = _safe_float(pos.get("current_price"))
            if qty != 0.0 and current_price != 0.0:
                total_exposure += abs(qty * current_price)
            else:
                # Skip this position - missing data
                skipped_count += 1

        is_partial = skipped_count > 0 and len(positions) > 0
        leverage = total_exposure / nlv if nlv > 0 else None

        return leverage, is_partial

    def _get_or_set_baseline_nlv(self, current_nlv: float) -> float:
        """Get the baseline NLV for today, or set it if not present.

        Uses app.storage.user with a date-keyed entry.
        Note: app.storage.user is session-scoped and resets on page refresh.
        This is acceptable for v1; future: use backend prev_close_equity.
        """
        date_key = _get_trading_date_key()

        try:
            # Get existing baseline for today
            baseline = app.storage.user.get(date_key)
            if baseline is not None:
                return _safe_float(baseline, current_nlv)

            # Set baseline for today (first update of the trading day)
            app.storage.user[date_key] = current_nlv
            return current_nlv
        except Exception as e:
            # Storage access failed - use current NLV as baseline
            logger.warning(
                "Failed to access user storage for NLV baseline",
                extra={"error": str(e), "error_type": type(e).__name__},
            )
            return current_nlv

    def mark_stale(self) -> None:
        """Mark all metrics as stale (visual indicator)."""
        if self._nlv_label:
            self._nlv_label.classes("opacity-50")
        if self._leverage_label:
            self._leverage_label.classes("opacity-50")
        if self._day_change_label:
            self._day_change_label.classes("opacity-50")

    def _clear_stale(self) -> None:
        """Clear stale indicator from all metrics."""
        if self._nlv_label:
            self._nlv_label.classes(remove="opacity-50")
        if self._leverage_label:
            self._leverage_label.classes(remove="opacity-50")
        if self._day_change_label:
            self._day_change_label.classes(remove="opacity-50")

    def is_stale(self) -> bool:
        """Check if metrics data is stale (no update in 30s)."""
        if self._last_update is None:
            return False
        return (time.monotonic() - self._last_update) > STALE_THRESHOLD_SECONDS

    async def update(
        self,
        client: AsyncTradingClient,
        user_id: str,
        role: str | None = None,
        strategies: list[str] | None = None,
    ) -> None:
        """Update all header metrics.

        This method is fully isolated - all exceptions are caught internally.
        Failures only mark metrics as stale, never affect other UI components.

        Args:
            client: AsyncTradingClient instance
            user_id: User ID for API calls
            role: User role for auth headers
            strategies: User strategies for auth headers
        """
        try:
            # Fetch account and positions in parallel with timeout
            account_task = client.fetch_account_info(user_id, role, strategies)
            positions_task = client.fetch_positions(user_id, role, strategies)

            results = await asyncio.wait_for(
                asyncio.gather(account_task, positions_task, return_exceptions=True),
                timeout=METRICS_FETCH_TIMEOUT,
            )

            account_result, positions_result = results

            # Check for exceptions in results
            if isinstance(account_result, BaseException):
                raise account_result
            if isinstance(positions_result, BaseException):
                raise positions_result

            # Extract data - safe to cast after exception checks above
            account_info = dict(account_result) if account_result else {}

            # Get NLV (portfolio_value)
            nlv = _safe_float(account_info.get("portfolio_value"))

            # Get positions list (handle both list and dict wrapper formats)
            # Note: Don't call dict() on positions_result - it may already be a list
            positions: list[dict[str, Any]]
            if isinstance(positions_result, list):
                positions = positions_result
            elif isinstance(positions_result, dict):
                positions = positions_result.get("positions", [])
                if not isinstance(positions, list):
                    positions = []
            elif positions_result is not None:
                # Try dict conversion for other mapping types
                try:
                    positions_dict = dict(positions_result)
                    positions = positions_dict.get("positions", [])
                    if not isinstance(positions, list):
                        positions = []
                except (TypeError, ValueError):
                    positions = []
            else:
                positions = []

            # Update NLV display
            if self._nlv_label:
                if nlv > 0:
                    self._nlv_label.set_text(_format_currency(nlv))
                else:
                    self._nlv_label.set_text("--")

            # Calculate and update leverage
            leverage, is_partial = self._calculate_leverage(positions, nlv)
            if self._leverage_label:
                if leverage is not None:
                    leverage_text = f"{leverage:.1f}x"
                    if is_partial:
                        leverage_text += "*"  # Indicate partial data
                    self._leverage_label.set_text(leverage_text)

                    # Update color class
                    new_class = self._get_leverage_color_class(leverage)
                    if self._current_leverage_class != new_class:
                        if self._current_leverage_class:
                            self._leverage_label.classes(
                                remove=self._current_leverage_class
                            )
                        self._leverage_label.classes(new_class)
                        self._current_leverage_class = new_class
                else:
                    self._leverage_label.set_text("--")
                    # Reset to neutral color
                    if self._current_leverage_class:
                        self._leverage_label.classes(remove=self._current_leverage_class)
                    self._leverage_label.classes("bg-gray-600 text-white")
                    self._current_leverage_class = "bg-gray-600 text-white"

            # Calculate and update day change
            if self._day_change_label and nlv > 0:
                baseline_nlv = self._get_or_set_baseline_nlv(nlv)
                day_change = nlv - baseline_nlv
                day_change_pct = (
                    (day_change / baseline_nlv * 100) if baseline_nlv > 0 else None
                )

                self._day_change_label.set_text(
                    _format_day_change(day_change, day_change_pct)
                )

                # Color based on positive/negative
                if day_change >= 0:
                    self._day_change_label.classes(
                        "text-green-400", remove="text-red-400"
                    )
                else:
                    self._day_change_label.classes(
                        "text-red-400", remove="text-green-400"
                    )
            elif self._day_change_label:
                self._day_change_label.set_text("--")
                # Clear any previous color classes when showing placeholder
                self._day_change_label.classes(remove="text-green-400 text-red-400")

            # Update successful - clear stale and record time
            self._clear_stale()
            self._last_update = time.monotonic()

        except TimeoutError:
            logger.warning(
                "Header metrics fetch timed out",
                extra={"user_id": user_id, "timeout": METRICS_FETCH_TIMEOUT},
            )
            self.mark_stale()
        except asyncio.CancelledError:
            logger.debug("Header metrics fetch cancelled")
            self.mark_stale()
        except Exception as e:
            # Catch all other exceptions - mark stale but don't propagate
            logger.warning(
                "Header metrics update failed",
                extra={
                    "user_id": user_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
            )
            self.mark_stale()


__all__ = ["HeaderMetrics"]
