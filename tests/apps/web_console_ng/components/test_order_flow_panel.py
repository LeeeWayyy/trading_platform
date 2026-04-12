"""Tests for OrderFlowPanel ordering behavior."""

from __future__ import annotations

from unittest.mock import MagicMock

from apps.web_console_ng.components.order_flow_panel import OrderFlowPanel


def test_add_trades_keeps_newest_trade_at_top() -> None:
    """Batch inserts preserve newest-first ordering in the panel deque."""
    panel = OrderFlowPanel()
    panel._refresh_summary = MagicMock()
    panel._render_rows = MagicMock()

    panel.add_trades(
        [
            {
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "price": "100.00",
                "timestamp": "2026-04-12T10:00:00Z",
            },
            {
                "symbol": "AAPL",
                "side": "buy",
                "qty": 20,
                "price": "101.00",
                "timestamp": "2026-04-12T10:00:01Z",
            },
            {
                "symbol": "AAPL",
                "side": "sell",
                "qty": 30,
                "price": "102.00",
                "timestamp": "2026-04-12T10:00:02Z",
            },
        ]
    )

    assert [trade["time"] for trade in panel._trades] == ["10:00:02", "10:00:01", "10:00:00"]
