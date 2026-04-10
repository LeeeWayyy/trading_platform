"""Terminal-style inline log tail for unified execution workspace."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from nicegui import ui


class LogTailPanel:
    """Compact mono log tail with ActivityFeed-compatible API."""

    MAX_ITEMS = 160

    def __init__(self, *, max_items: int = MAX_ITEMS) -> None:
        self._max_items = max(50, max_items)
        self._log: ui.log | None = None

    def create(self, *, title: str = "Tail Logs") -> ui.card:
        """Create terminal-style log panel."""
        with ui.card().classes("workspace-v2-panel workspace-v2-log-tail") as card:
            ui.label(title).classes("workspace-v2-panel-title")
            self._log = ui.log(max_lines=self._max_items).classes("workspace-v2-log-output")
        return card

    async def add_item(self, event: dict[str, Any]) -> None:
        """Append single event line."""
        self._push_line(self._format_line(event))

    async def add_items(self, events: list[dict[str, Any]], *, highlight: bool = False) -> None:
        """Append a batch of event lines (oldest to newest)."""
        _ = highlight
        for event in events:
            self._push_line(self._format_line(event))

    def _push_line(self, line: str) -> None:
        if self._log is not None:
            self._log.push(line)

    def _format_line(self, event: dict[str, Any]) -> str:
        ts = self._format_timestamp(event.get("timestamp") or event.get("executed_at") or event.get("time"))
        event_type = str(event.get("type") or event.get("status") or "event").upper()
        symbol = str(event.get("symbol") or "--").upper()
        side = str(event.get("side") or "").upper()
        qty = event.get("qty")
        price = event.get("price")
        message = str(event.get("message") or "").strip()

        qty_text = "-"
        if qty is not None and str(qty).strip() != "":
            qty_text = str(qty)
        price_text = "-"
        if price is not None and str(price).strip() != "":
            price_text = str(price)

        parts = [ts, event_type, symbol]
        if side:
            parts.append(side)
        if qty_text != "-" or price_text != "-":
            parts.append(f"qty={qty_text}")
            parts.append(f"price={price_text}")
        if message:
            parts.append(message)
        return " | ".join(parts)

    def _format_timestamp(self, value: Any) -> str:
        if value is None:
            return datetime.now(UTC).strftime("%H:%M:%S")
        if isinstance(value, datetime):
            dt = value
        else:
            raw = str(value)
            try:
                if raw.endswith("Z"):
                    raw = raw[:-1] + "+00:00"
                dt = datetime.fromisoformat(raw)
            except ValueError:
                if len(raw) <= 10:
                    return raw
                return datetime.now(UTC).strftime("%H:%M:%S")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).strftime("%H:%M:%S")


__all__ = ["LogTailPanel"]
