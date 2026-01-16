"""Market clock component for header display."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from nicegui import ui

from libs.common.market_hours import MarketHours, SessionState

logger = logging.getLogger(__name__)

UPDATE_INTERVAL_SECONDS = 60.0


def _format_timedelta(delta: timedelta) -> str:
    total_seconds = int(delta.total_seconds())
    if total_seconds <= 0:
        return "0m"
    minutes = total_seconds // 60
    hours = minutes // 60
    days = hours // 24
    minutes = minutes % 60
    hours = hours % 24
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def _format_time_label(dt: datetime) -> str:
    try:
        return dt.strftime("%-I:%M %p")
    except ValueError:
        # Windows compatibility (no %-I)
        return dt.strftime("%I:%M %p").lstrip("0")


class MarketClock:
    """Header market clock showing session state and countdown."""

    def __init__(self, exchanges: list[str] | None = None) -> None:
        self._exchanges = exchanges or ["NYSE"]
        self._labels: dict[str, ui.label] = {}
        self._last_update: float | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        with ui.row().classes("items-center gap-2 shrink-0"):
            for exchange in self._exchanges:
                label = ui.label("--").classes(
                    "text-xs font-medium px-2 py-1 rounded bg-slate-700 text-white shrink-0"
                )
                self._labels[exchange] = label

    def update(self, *, force: bool = False) -> None:
        """Update market clock display (throttled to 60s)."""
        now_monotonic = time.monotonic()
        if not force and self._last_update is not None:
            if (now_monotonic - self._last_update) < UPDATE_INTERVAL_SECONDS:
                return

        current_utc = datetime.now(ZoneInfo("UTC"))

        for exchange, label in self._labels.items():
            try:
                state = MarketHours.get_session_state(exchange, now=current_utc)
                next_transition = MarketHours.get_next_transition(exchange, now=current_utc)
                delta = MarketHours.time_to_next_transition(exchange, now=current_utc)

                # All possible color classes to remove when switching states
                _remove_colors = (
                    "bg-slate-700 bg-blue-600 bg-green-600 bg-yellow-500 bg-gray-600 "
                    "text-white text-black"
                )

                if exchange.upper() == "CRYPTO":
                    label.set_text("CRYPTO: 24/7")
                    label.classes(add="bg-blue-600 text-white", remove=_remove_colors)
                    continue

                if state == SessionState.OPEN:
                    countdown = _format_timedelta(delta) if delta else "--"
                    label.set_text(f"{exchange}: OPEN · Closes in {countdown}")
                    label.classes(add="bg-green-600 text-white", remove=_remove_colors)
                elif state == SessionState.PRE_MARKET:
                    countdown = _format_timedelta(delta) if delta else "--"
                    label.set_text(f"{exchange}: PRE-MKT · Opens in {countdown}")
                    label.classes(add="bg-yellow-500 text-black", remove=_remove_colors)
                elif state == SessionState.POST_MARKET:
                    countdown = _format_timedelta(delta) if delta else "--"
                    label.set_text(f"{exchange}: POST-MKT · Closes in {countdown}")
                    label.classes(add="bg-yellow-500 text-black", remove=_remove_colors)
                else:
                    if next_transition is not None:
                        open_time = _format_time_label(next_transition)
                        label.set_text(f"{exchange}: CLOSED · Opens {open_time}")
                    else:
                        label.set_text(f"{exchange}: CLOSED")
                    label.classes(add="bg-gray-600 text-white", remove=_remove_colors)

                if next_transition is not None:
                    label.tooltip(
                        f"Next: {_format_time_label(next_transition)} {next_transition.tzname() or ''}"
                    )
                else:
                    label.tooltip("Next: --")

            except Exception as exc:
                logger.warning(
                    "market_clock_update_failed",
                    extra={"exchange": exchange, "error": type(exc).__name__},
                )
                label.set_text(f"{exchange}: --")
                # Use same removal pattern for error state
                _remove_colors_err = (
                    "bg-slate-700 bg-blue-600 bg-green-600 bg-yellow-500 bg-gray-600 "
                    "text-white text-black"
                )
                label.classes(add="bg-gray-600 text-white", remove=_remove_colors_err)

        self._last_update = now_monotonic


__all__ = ["MarketClock"]
