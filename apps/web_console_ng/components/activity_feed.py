"""Activity feed component for recent fills and events."""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

from nicegui import ui

logger = logging.getLogger(__name__)


class ActivityFeed:
    """Real-time activity feed with auto-scroll."""

    MAX_ITEMS = 20
    NEW_ITEM_HIGHLIGHT_DURATION = 2.0

    def __init__(self) -> None:
        self.items: deque[dict[str, Any]] = deque(maxlen=self.MAX_ITEMS)
        self._container = None
        self._items_column = None

        with ui.card().classes("w-full h-64 overflow-y-auto") as card:
            self._container = card
            ui.label("Recent Activity").classes("text-lg font-bold mb-2")
            self._items_column = ui.column().classes("w-full gap-1")

    async def add_item(self, event: dict[str, Any]) -> None:
        """Add new item to feed (appears at top with animation)."""
        self.items.appendleft(event)
        self._render_items(highlight_first=True)
        await self._scroll_to_top()

    async def _scroll_to_top(self) -> None:
        """Scroll the activity feed container to top."""
        if self._container is None:
            return
        await self._container.run_method("scrollTo", {"top": 0, "behavior": "smooth"})

    def _render_items(self, highlight_first: bool = False) -> None:
        """Re-render all items (newest first)."""
        if self._items_column is None:
            return
        self._items_column.clear()
        with self._items_column:
            for idx, item in enumerate(self.items):
                is_new = highlight_first and idx == 0
                self._render_item(item, highlight=is_new)

    def _render_item(self, event: dict[str, Any], highlight: bool = False) -> None:
        """Render single activity item."""
        try:
            side = str(event.get("side", "unknown")).lower()
            status = str(event.get("status", "unknown")).lower()
            time_str = str(event.get("time", ""))
            symbol = str(event.get("symbol", "???"))
            qty = event.get("qty", 0)
            price = event.get("price", 0.0)
        except Exception as exc:
            logger.warning(
                "activity_feed_malformed_event",
                extra={"error": str(exc), "event": str(event)[:100]},
            )
            return

        side_color = "text-green-600" if side == "buy" else "text-red-600"
        status_color = {
            "filled": "bg-green-100 text-green-800",
            "cancelled": "bg-gray-100 text-gray-800",
            "pending": "bg-yellow-100 text-yellow-800",
        }.get(status, "bg-gray-100")

        row_classes = "w-full items-center gap-2 p-2 hover:bg-gray-50 rounded"
        if highlight:
            row_classes += " bg-blue-100 animate-[fadeHighlight_2s_ease-out_forwards]"

        with ui.row().classes(row_classes):
            time_display = (
                f"{time_str} UTC"
                if time_str and not time_str.endswith("UTC")
                else (time_str or "??:??")
            )
            ui.label(time_display).classes("text-xs text-gray-500 w-24")
            ui.label(symbol).classes("font-mono w-16")
            ui.label(side.upper()).classes(f"{side_color} w-12")
            ui.label(str(qty)).classes("w-12 text-right")
            try:
                price_display = f"${float(price):.2f}"
            except (TypeError, ValueError):
                price_display = "$?.??"
            ui.label(price_display).classes("w-20 text-right")
            ui.label(status).classes(f"px-2 py-0.5 rounded text-xs {status_color}")


__all__ = ["ActivityFeed"]
