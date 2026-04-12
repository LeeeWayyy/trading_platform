"""Compact order flow / time-and-sales panel for dashboard microstructure context."""

from __future__ import annotations

import logging
from collections import deque
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from nicegui import ui

from apps.web_console_ng.utils.time import parse_iso_timestamp, validate_and_normalize_symbol

logger = logging.getLogger(__name__)


class OrderFlowPanel:
    """Render recent trade flow with compact notional imbalance summary."""

    DEFAULT_MAX_ROWS = 14

    def __init__(self, max_rows: int = DEFAULT_MAX_ROWS) -> None:
        self._max_rows = max(5, max_rows)
        self._symbol: str | None = None
        self._trades: deque[dict[str, Any]] = deque(maxlen=250)

        self._symbol_label: ui.label | None = None
        self._buy_label: ui.label | None = None
        self._sell_label: ui.label | None = None
        self._imbalance_label: ui.label | None = None
        self._rows_container: ui.column | None = None

    def create(self) -> ui.card:
        """Create and return panel card."""
        with ui.card().classes("workspace-v2-panel p-2 w-full h-full overflow-hidden") as card:
            with ui.row().classes("w-full items-center justify-between mb-1"):
                ui.label("Order Flow / Time & Sales").classes("workspace-v2-panel-title")
                self._symbol_label = ui.label("ALL").classes(
                    "workspace-v2-kv workspace-v2-data-mono"
                )

            with ui.row().classes("w-full items-center gap-3 mb-2 text-xs"):
                self._buy_label = ui.label("Buy: --").classes("text-profit workspace-v2-data-mono")
                self._sell_label = ui.label("Sell: --").classes("text-loss workspace-v2-data-mono")
                self._imbalance_label = ui.label("Imb: --").classes(
                    "workspace-v2-kv workspace-v2-data-mono"
                )

            with ui.scroll_area().classes("w-full flex-1 min-h-0"):
                self._rows_container = ui.column().classes("w-full gap-1 pr-1")

        self._refresh_summary()
        self._render_rows()
        return card

    def set_symbol(self, symbol: str | None) -> None:
        """Set optional symbol filter for displayed order flow."""
        normalized: str | None = None
        if symbol:
            try:
                normalized = validate_and_normalize_symbol(symbol)
            except ValueError:
                logger.debug("order_flow_invalid_symbol_filter", extra={"symbol": symbol})
                normalized = None

        if normalized == self._symbol:
            return

        self._symbol = normalized
        if self._symbol_label is not None:
            self._symbol_label.text = normalized or "ALL"
        self._refresh_summary()
        self._render_rows()

    def clear(self) -> None:
        """Clear all trades from panel."""
        self._trades.clear()
        self._refresh_summary()
        self._render_rows()

    def add_trade(self, trade: dict[str, Any]) -> None:
        """Add a single trade to panel."""
        normalized = self._normalize_trade(trade)
        if normalized is None:
            return
        self._trades.appendleft(normalized)
        self._refresh_summary()
        self._render_rows()

    def add_trades(self, trades: list[dict[str, Any]]) -> None:
        """Add multiple trades to panel."""
        if not trades:
            return
        # Source list is oldest -> newest; appendleft keeps newest rows at the top.
        for trade in trades:
            normalized = self._normalize_trade(trade)
            if normalized is not None:
                self._trades.appendleft(normalized)
        self._refresh_summary()
        self._render_rows()

    def _filtered_trades(self) -> list[dict[str, Any]]:
        if self._symbol is None:
            return list(self._trades)
        return [trade for trade in self._trades if trade.get("symbol") == self._symbol]

    def _refresh_summary(self) -> None:
        trades = self._filtered_trades()
        buy_notional = Decimal("0")
        sell_notional = Decimal("0")
        for trade in trades:
            price = trade.get("price")
            qty = trade.get("qty")
            side = trade.get("side")
            if not isinstance(price, Decimal) or not isinstance(qty, int):
                continue
            notional = price * Decimal(qty)
            if side == "buy":
                buy_notional += notional
            elif side == "sell":
                sell_notional += notional

        imbalance = buy_notional - sell_notional
        if self._buy_label is not None:
            self._buy_label.text = f"Buy: ${buy_notional:,.0f}"
        if self._sell_label is not None:
            self._sell_label.text = f"Sell: ${sell_notional:,.0f}"
        if self._imbalance_label is not None:
            self._imbalance_label.text = f"Imb: ${imbalance:+,.0f}"
            if imbalance > 0:
                self._imbalance_label.classes(add="text-profit", remove="text-loss text-text-secondary")
            elif imbalance < 0:
                self._imbalance_label.classes(add="text-loss", remove="text-profit text-text-secondary")
            else:
                self._imbalance_label.classes(
                    add="text-text-secondary", remove="text-profit text-loss"
                )

    def _render_rows(self) -> None:
        if self._rows_container is None:
            return
        self._rows_container.clear()

        trades = self._filtered_trades()[: self._max_rows]
        if not trades:
            with self._rows_container:
                ui.label("No trades yet").classes("text-xs text-text-secondary")
            return

        with self._rows_container:
            for trade in trades:
                side = str(trade.get("side") or "unknown")
                side_class = "text-profit" if side == "buy" else "text-loss" if side == "sell" else "text-text-secondary"
                time_text = str(trade.get("time") or "--:--:--")
                symbol = str(trade.get("symbol") or "???")
                qty = trade.get("qty")
                price = trade.get("price")
                qty_text = str(qty) if isinstance(qty, int) else "?"
                price_text = f"{price:,.2f}" if isinstance(price, Decimal) else "--.--"

                with ui.row().classes("w-full items-center justify-between gap-2"):
                    with ui.row().classes("items-center gap-2 min-w-0"):
                        ui.label(time_text).classes("text-[11px] text-text-secondary font-mono-numbers")
                        ui.label(symbol).classes("text-[11px] text-text-primary font-mono-numbers")
                        ui.label(side.upper()).classes(f"text-[11px] font-mono-numbers {side_class}")
                    ui.label(f"{qty_text} @ {price_text}").classes(
                        "text-[11px] text-text-secondary font-mono-numbers"
                    )

    def _normalize_trade(self, trade: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(trade, dict):
            return None

        raw_symbol = trade.get("symbol")
        if not isinstance(raw_symbol, str) or not raw_symbol.strip():
            return None
        try:
            symbol = validate_and_normalize_symbol(raw_symbol)
        except ValueError:
            return None

        raw_side = str(trade.get("side") or "").lower()
        side = "buy" if raw_side == "buy" else "sell" if raw_side == "sell" else "unknown"

        qty = self._parse_qty(trade.get("qty") or trade.get("quantity"))
        if qty is None:
            return None

        price = self._parse_price(trade.get("price"))
        if price is None:
            return None

        timestamp = (
            trade.get("executed_at")
            or trade.get("timestamp")
            or trade.get("time")
            or trade.get("ts")
        )

        return {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": price,
            "time": self._format_time(timestamp),
        }

    def _parse_qty(self, raw_qty: Any) -> int | None:
        try:
            parsed = int(float(raw_qty))
        except (TypeError, ValueError):
            return None
        if parsed <= 0:
            return None
        return parsed

    def _parse_price(self, raw_price: Any) -> Decimal | None:
        try:
            parsed = Decimal(str(raw_price))
        except (InvalidOperation, TypeError, ValueError):
            return None
        if not parsed.is_finite() or parsed <= 0:
            return None
        return parsed

    def _format_time(self, raw_timestamp: Any) -> str:
        if raw_timestamp is None:
            return "--:--:--"
        if isinstance(raw_timestamp, datetime):
            dt = raw_timestamp
        else:
            try:
                dt = parse_iso_timestamp(str(raw_timestamp))
            except (TypeError, ValueError):
                text = str(raw_timestamp)
                # Keep already short human-readable forms.
                if len(text) <= 12:
                    return text
                return "--:--:--"
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).strftime("%H:%M:%S")


__all__ = ["OrderFlowPanel"]
