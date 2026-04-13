"""Tests for OrderFlowPanel ordering behavior."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from apps.web_console_ng.components import order_flow_panel as order_flow_panel_module
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


def test_add_trade_drops_overflow_qty_payload() -> None:
    """Overflow-like qty values are ignored instead of raising."""
    panel = OrderFlowPanel()
    panel._refresh_summary = MagicMock()
    panel._render_rows = MagicMock()

    panel.add_trade(
        {
            "symbol": "AAPL",
            "side": "buy",
            "qty": "1e309",
            "price": "100.00",
            "timestamp": "2026-04-12T10:00:00Z",
        }
    )

    assert len(panel._trades) == 0


def test_add_trade_drops_infinite_qty_payload() -> None:
    """Infinite qty metadata must not crash order-flow rendering."""
    panel = OrderFlowPanel()
    panel._refresh_summary = MagicMock()
    panel._render_rows = MagicMock()

    panel.add_trade(
        {
            "symbol": "AAPL",
            "side": "buy",
            "qty": "inf",
            "price": "100.00",
            "timestamp": "2026-04-12T10:00:00Z",
        }
    )

    assert len(panel._trades) == 0


def test_request_render_coalesces_high_frequency_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated updates should schedule a single deferred render until flush."""
    panel = OrderFlowPanel()
    panel._rows_container = MagicMock()
    panel._render_rows = MagicMock()

    scheduled_callbacks: list[object] = []

    def fake_timer(
        interval: float, callback: object, *, once: bool = False
    ) -> object:  # pragma: no cover - tiny adapter
        assert interval == 0.08
        assert once is True
        scheduled_callbacks.append(callback)
        return object()

    monkeypatch.setattr(order_flow_panel_module.ui, "timer", fake_timer)

    panel._request_render()
    panel._request_render()

    assert len(scheduled_callbacks) == 1
    flush_callback = scheduled_callbacks[0]
    assert callable(flush_callback)
    flush_callback()
    panel._render_rows.assert_called_once()
