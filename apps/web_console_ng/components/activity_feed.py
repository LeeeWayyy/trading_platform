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
        self._container: ui.card | None = None
        self._items_column: ui.column | None = None
        self._item_elements: deque[ui.row] = deque(maxlen=self.MAX_ITEMS)

        with ui.card().classes("w-full h-64 overflow-y-auto") as card:
            self._container = card
            ui.label("Recent Activity").classes("text-lg font-bold mb-2")
            self._items_column = ui.column().classes("w-full gap-1")

    async def add_item(self, event: dict[str, Any]) -> None:
        """Add new item to feed (appears at top with animation).

        Efficient incremental rendering: inserts new element at top,
        removes oldest if MAX_ITEMS exceeded. Avoids re-rendering entire list.
        """
        self.items.appendleft(event)
        self._insert_item_at_top(event, highlight=True)
        await self._scroll_to_top()

    async def add_items(
        self,
        events: list[dict[str, Any]],
        *,
        highlight: bool = False,
    ) -> None:
        """Add multiple items without re-rendering the whole feed."""
        if not events:
            return

        # Insert oldest first so newest ends up at the top.
        for event in reversed(events):
            self.items.appendleft(event)
            self._insert_item_at_top(event, highlight=highlight)

        await self._scroll_to_top()

    async def _scroll_to_top(self) -> None:
        """Scroll the activity feed container to top."""
        if self._container is None:
            return
        try:
            await self._container.run_method(
                "scrollTo",
                {"top": 0, "behavior": "smooth"},
                timeout=2,
            )
        except TimeoutError:
            logger.warning("activity_feed_scroll_timeout")
        except Exception as exc:
            logger.warning(
                "activity_feed_scroll_failed",
                extra={"error": type(exc).__name__, "detail": str(exc)},
            )

    def _insert_item_at_top(self, event: dict[str, Any], highlight: bool = False) -> None:
        """Insert new item at top of feed (efficient incremental update).

        Only creates one new UI element and removes the oldest if needed,
        instead of re-rendering the entire list.
        """
        if self._items_column is None:
            return

        # Remove oldest element if at capacity
        if len(self._item_elements) >= self.MAX_ITEMS:
            oldest = self._item_elements.pop()
            oldest.delete()

        # Create new element and insert at top
        with self._items_column:
            row = self._render_item(event, highlight=highlight)
            if row is not None:
                # Move to top of column (NiceGUI renders in DOM order)
                row.move(target_index=0)
                self._item_elements.appendleft(row)

    def _render_item(self, event: dict[str, Any], highlight: bool = False) -> ui.row | None:
        """Render single activity item.

        Returns:
            The created row element, or None if event was malformed.
        """
        try:
            side = str(event.get("side", "unknown")).lower()
            status = str(event.get("status", "unknown")).lower()
            time_str = str(event.get("time", ""))
            symbol = str(event.get("symbol", "???"))
            qty = event.get("qty", 0)
            price = event.get("price", 0.0)
        except (TypeError, AttributeError, KeyError) as exc:
            # TypeError: event is not dict-like (e.g., None, string)
            # AttributeError: event lacks .get() method
            # KeyError: shouldn't occur with .get() but guard against edge cases
            logger.warning(
                "activity_feed_malformed_event",
                extra={"error": type(exc).__name__, "detail": str(exc), "event": str(event)[:100]},
            )
            return None

        side_color = "text-green-600" if side == "buy" else "text-red-600"
        status_color = {
            "filled": "bg-green-100 text-green-800",
            "cancelled": "bg-gray-100 text-gray-800",
            "pending": "bg-yellow-100 text-yellow-800",
        }.get(status, "bg-gray-100")

        row_classes = "w-full items-center gap-2 p-2 hover:bg-gray-50 rounded"
        if highlight:
            row_classes += f" bg-blue-100 animate-[fadeHighlight_{self.NEW_ITEM_HIGHLIGHT_DURATION}s_ease-out_forwards]"

        with ui.row().classes(row_classes) as row:
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

        return row


__all__ = ["ActivityFeed"]
