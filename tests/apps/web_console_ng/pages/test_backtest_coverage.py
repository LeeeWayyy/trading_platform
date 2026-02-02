"""Comprehensive coverage tests for backtest.py page (target: 85%+ coverage).

This file extends test_backtest.py to achieve 85%+ branch coverage by testing:
1. Yahoo-specific rendering paths (universe, charts, trades, downloads)
2. Permission-gated export functionality
3. Edge cases in data processing and formatting
4. Chart rendering with various data states
5. Trade computation and P&L calculation
6. Symbol filtering and signal visualization
7. Error handling in result loading
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date
from types import SimpleNamespace
from typing import Any

import polars as pl
import pytest

from apps.web_console_ng.pages import backtest as backtest_module


class DummyElement:
    """Mock UI element supporting common NiceGUI operations."""

    def __init__(self, ui: DummyUI, kind: str, **kwargs: Any) -> None:
        self.ui = ui
        self.kind = kind
        self.kwargs = kwargs
        self.label = kwargs.get("label")
        self.value = kwargs.get("value")
        self.text = kwargs.get("text", "")
        self.visible = True
        self._on_click: Callable[..., Any] | None = kwargs.get("on_click")
        self._on_change: Callable[..., Any] | None = kwargs.get("on_change")
        self._classes = ""

    def __enter__(self) -> DummyElement:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def classes(self, value: str = "") -> DummyElement:
        self._classes += f" {value}"
        return self

    def props(self, value: str = "") -> DummyElement:
        return self

    def on_click(self, fn: Callable[..., Any] | None) -> DummyElement:
        self._on_click = fn
        return self

    def on(self, event: str, handler: Callable[..., Any]) -> DummyElement:
        """Mock event handler registration."""
        return self

    def on_value_change(self, handler: Callable[..., Any]) -> DummyElement:
        """Mock value change handler registration."""
        return self

    def set_visibility(self, value: bool) -> None:
        self.visible = value

    def set_text(self, text: str) -> None:
        """Mock text setter."""
        self.text = text

    def bind_value_to(self, target: Any, name: str) -> DummyElement:
        """Mock value binding."""
        return self


class DummyUI:
    """Mock NiceGUI ui module."""

    def __init__(self) -> None:
        self.labels: list[DummyElement] = []
        self.buttons: list[DummyElement] = []
        self.selects: list[DummyElement] = []
        self.inputs: list[DummyElement] = []
        self.checkboxes: list[DummyElement] = []
        self.dates: list[DummyElement] = []
        self.timers: list[DummyElement] = []
        self.downloads: list[dict[str, Any]] = []
        self.plotlies: list[Any] = []
        self.tables: list[dict[str, Any]] = []
        self.notifications: list[dict[str, Any]] = []
        self.separators: list[DummyElement] = []
        self.expansions: list[DummyElement] = []
        self.context = SimpleNamespace(client=SimpleNamespace(storage={"client_id": "test_client"}))

    def label(self, text: str = "") -> DummyElement:
        el = DummyElement(self, "label", text=text)
        self.labels.append(el)
        return el

    def button(
        self,
        label: str,
        on_click: Callable[..., Any] | None = None,
        **kwargs: Any,
    ) -> DummyElement:
        el = DummyElement(self, "button", label=label, on_click=on_click, **kwargs)
        self.buttons.append(el)
        return el

    def select(
        self,
        label: str | None = None,
        options: list[str] | dict[str, Any] | None = None,
        value: Any = None,
        on_change: Callable[..., Any] | None = None,
        **kwargs: Any,
    ) -> DummyElement:
        el = DummyElement(
            self,
            "select",
            label=label,
            options=options,
            value=value,
            on_change=on_change,
            **kwargs,
        )
        self.selects.append(el)
        return el

    def input(
        self,
        label: str | None = None,
        placeholder: str | None = None,
        value: Any = None,
    ) -> DummyElement:
        el = DummyElement(self, "input", label=label, placeholder=placeholder, value=value)
        self.inputs.append(el)
        return el

    def date(self, value: Any = None) -> DummyElement:
        el = DummyElement(self, "date", value=value)
        self.dates.append(el)
        return el

    def checkbox(self, text: str = "", value: bool = False) -> DummyElement:
        el = DummyElement(self, "checkbox", text=text, value=value)
        self.checkboxes.append(el)
        return el

    def card(self) -> DummyElement:
        return DummyElement(self, "card")

    def row(self) -> DummyElement:
        return DummyElement(self, "row")

    def column(self) -> DummyElement:
        return DummyElement(self, "column")

    def separator(self) -> DummyElement:
        el = DummyElement(self, "separator")
        self.separators.append(el)
        return el

    def expansion(self, text: str = "") -> DummyElement:
        el = DummyElement(self, "expansion", text=text)
        self.expansions.append(el)
        return el

    def timer(self, interval: float, callback: Callable[..., Any]) -> DummyElement:
        el = DummyElement(self, "timer", interval=interval)
        el.callback = callback  # type: ignore[attr-defined]
        self.timers.append(el)
        return el

    def linear_progress(self, *_: Any, **__: Any) -> DummyElement:
        return DummyElement(self, "linear_progress")

    def icon(self, *_: Any, **__: Any) -> DummyElement:
        return DummyElement(self, "icon")

    def download(self, data: bytes, filename: str) -> None:
        self.downloads.append({"data": data, "filename": filename})

    def plotly(self, fig: Any) -> DummyElement:
        el = DummyElement(self, "plotly")
        self.plotlies.append(fig)
        return el

    def table(
        self,
        columns: list[dict[str, Any]],
        rows: list[dict[str, Any]],
        row_key: str | None = None,
    ) -> DummyElement:
        el = DummyElement(self, "table")
        self.tables.append({"columns": columns, "rows": rows, "row_key": row_key})
        return el

    def notify(self, text: str, type: str | None = None) -> None:
        self.notifications.append({"text": text, "type": type})

    def refreshable(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        wrapper.refresh = lambda: None  # type: ignore[attr-defined]
        return wrapper


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    """Fixture providing mocked NiceGUI ui module."""
    ui = DummyUI()
    monkeypatch.setattr(backtest_module, "ui", ui)
    return ui


async def _call(cb: Callable[..., Any] | None, *args: Any) -> None:
    """Helper to call sync or async callback."""
    if cb is None:
        return
    if asyncio.iscoroutinefunction(cb):
        await cb(*args)
    else:
        cb(*args)


# ==========================
# YAHOO BACKTEST DETAILS TESTS
# ==========================


@pytest.mark.asyncio()
async def test_render_yahoo_details_with_prices_and_signals(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _render_yahoo_backtest_details renders complete visualization."""
    prices_df = pl.DataFrame(
        {
            "symbol": ["AAPL", "AAPL", "MSFT"],
            "date": [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 1)],
            "price": [150.0, 151.0, 300.0],
            "permno": [1, 1, 2],
        }
    )
    signals_df = pl.DataFrame(
        {
            "permno": [1, 1, 2],
            "date": [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 1)],
            "signal": [1.0, -1.0, 0.5],
        }
    )
    returns_df = pl.DataFrame(
        {
            "permno": [1, 1, 2],
            "date": [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 1)],
            "return": [0.01, -0.02, 0.015],
        }
    )
    weights_df = pl.DataFrame(
        {
            "permno": [1, 1, 2],
            "date": [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 1)],
            "weight": [0.5, 0.3, 0.2],
        }
    )

    result = SimpleNamespace(
        backtest_id="b1",
        daily_prices=prices_df,
        daily_signals=signals_df,
        daily_returns=returns_df,
        daily_weights=weights_df,
    )

    monkeypatch.setattr(backtest_module, "has_permission", lambda user, perm: True)

    backtest_module._render_yahoo_backtest_details(result, {"user_id": "u1"})

    # Should render universe section
    assert any("Universe" in label.text for label in dummy_ui.labels)
    assert any("2 symbols" in label.text for label in dummy_ui.labels)

    # Should render signal details section
    assert any("Signal Details" in label.text for label in dummy_ui.labels)

    # Should render symbol selector
    assert any(s.label == "Symbol" for s in dummy_ui.selects)

    # Should show download buttons (user has EXPORT_DATA permission)
    assert any("Download Signals CSV" in b.label for b in dummy_ui.buttons)
    assert any("Download Trades CSV" in b.label for b in dummy_ui.buttons)

    # Should render price chart section
    assert any("Price + Signal Triggers" in label.text for label in dummy_ui.labels)

    # Should render trade P&L section
    assert any("Trade P&L" in label.text for label in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_render_yahoo_details_signal_without_symbol_column(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test signal processing when signals lack symbol column (joins with prices)."""
    prices_df = pl.DataFrame(
        {
            "symbol": ["AAPL", "AAPL"],
            "date": [date(2025, 1, 1), date(2025, 1, 2)],
            "price": [150.0, 151.0],
            "permno": [1, 1],
        }
    )
    signals_df = pl.DataFrame(
        {
            "permno": [1, 1],
            "date": [date(2025, 1, 1), date(2025, 1, 2)],
            "signal": [1.0, -1.0],
        }
    )  # No symbol column

    result = SimpleNamespace(
        backtest_id="b1",
        daily_prices=prices_df,
        daily_signals=signals_df,
        daily_returns=None,
        daily_weights=None,
    )

    monkeypatch.setattr(backtest_module, "has_permission", lambda user, perm: False)

    backtest_module._render_yahoo_backtest_details(result, {"user_id": "u1"})

    # Should still render universe (symbols extracted from prices)
    assert any("1 symbols" in label.text for label in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_download_signals_csv_no_export_permission(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test signal CSV download blocked without EXPORT_DATA permission."""
    prices_df = pl.DataFrame(
        {
            "symbol": ["AAPL"],
            "date": [date(2025, 1, 1)],
            "price": [150.0],
            "permno": [1],
        }
    )
    signals_df = pl.DataFrame(
        {
            "permno": [1],
            "date": [date(2025, 1, 1)],
            "signal": [1.0],
        }
    )

    result = SimpleNamespace(
        backtest_id="b1",
        daily_prices=prices_df,
        daily_signals=signals_df,
        daily_returns=pl.DataFrame({"permno": [1], "date": [date(2025, 1, 1)], "return": [0.01]}),
        daily_weights=pl.DataFrame({"permno": [1], "date": [date(2025, 1, 1)], "weight": [0.5]}),
    )

    # User lacks EXPORT_DATA permission
    monkeypatch.setattr(backtest_module, "has_permission", lambda user, perm: False)

    backtest_module._render_yahoo_backtest_details(result, {"user_id": "u1"})

    # Download buttons should not appear
    assert not any("Download Signals CSV" in b.label for b in dummy_ui.buttons)
    assert not any("Download Trades CSV" in b.label for b in dummy_ui.buttons)


@pytest.mark.asyncio()
async def test_download_signals_csv_no_events(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test signal CSV download shows warning when no events to export."""
    prices_df = pl.DataFrame(
        {
            "symbol": ["AAPL"],
            "date": [date(2025, 1, 1)],
            "price": [150.0],
            "permno": [1],
        }
    )
    signals_df = pl.DataFrame(
        {
            "permno": [1],
            "date": [date(2025, 1, 1)],
            "signal": [0.0],  # FLAT signal (filtered out)
        }
    )

    result = SimpleNamespace(
        backtest_id="b1",
        daily_prices=prices_df,
        daily_signals=signals_df,
        daily_returns=pl.DataFrame({"permno": [1], "date": [date(2025, 1, 1)], "return": [0.0]}),
        daily_weights=pl.DataFrame({"permno": [1], "date": [date(2025, 1, 1)], "weight": [0.5]}),
    )

    monkeypatch.setattr(backtest_module, "has_permission", lambda user, perm: True)

    backtest_module._render_yahoo_backtest_details(result, {"user_id": "u1"})

    # Find and trigger download button
    download_btn = next(b for b in dummy_ui.buttons if "Download Signals CSV" in b.label)
    await _call(download_btn._on_click)

    # Should show warning notification
    assert any("No signal events to export" in n["text"] for n in dummy_ui.notifications)
    assert any(n["type"] == "warning" for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_download_trades_csv_no_trades(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test trades CSV download shows warning when no trades to export."""
    prices_df = pl.DataFrame(
        {
            "symbol": ["AAPL"],
            "date": [date(2025, 1, 1)],
            "price": [150.0],
            "permno": [1],
        }
    )
    signals_df = pl.DataFrame(
        {
            "permno": [1],
            "date": [date(2025, 1, 1)],
            "signal": [0.0],  # FLAT signal (no trades)
        }
    )

    result = SimpleNamespace(
        backtest_id="b1",
        daily_prices=prices_df,
        daily_signals=signals_df,
        daily_returns=None,
        daily_weights=None,
    )

    monkeypatch.setattr(backtest_module, "has_permission", lambda user, perm: True)

    backtest_module._render_yahoo_backtest_details(result, {"user_id": "u1"})

    # Find and trigger download button
    download_btn = next(b for b in dummy_ui.buttons if "Download Trades CSV" in b.label)
    await _call(download_btn._on_click)

    # Should show warning notification
    assert any("No trades to export" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_render_price_chart_no_data(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test price chart rendering handles missing data gracefully."""
    result = SimpleNamespace(
        backtest_id="b1",
        daily_prices=pl.DataFrame(),  # Empty prices
        daily_signals=None,
        daily_returns=None,
        daily_weights=None,
    )

    monkeypatch.setattr(backtest_module, "has_permission", lambda user, perm: True)

    backtest_module._render_yahoo_backtest_details(result, {"user_id": "u1"})

    # Should not crash, but no charts or sections rendered
    # No symbols means no universe section
    assert not any("Universe" in label.text for label in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_compute_trades_with_position_changes(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test trade computation with BUY → SELL → BUY sequence."""
    prices_df = pl.DataFrame(
        {
            "symbol": ["AAPL", "AAPL", "AAPL", "AAPL"],
            "date": [
                date(2025, 1, 1),
                date(2025, 1, 2),
                date(2025, 1, 3),
                date(2025, 1, 4),
            ],
            "price": [100.0, 105.0, 103.0, 108.0],
            "permno": [1, 1, 1, 1],
        }
    )
    signals_df = pl.DataFrame(
        {
            "permno": [1, 1, 1, 1],
            "date": [
                date(2025, 1, 1),
                date(2025, 1, 2),
                date(2025, 1, 3),
                date(2025, 1, 4),
            ],
            "signal": [1.0, -1.0, 1.0, 0.0],  # BUY → SELL → BUY → FLAT
        }
    )

    result = SimpleNamespace(
        backtest_id="b1",
        daily_prices=prices_df,
        daily_signals=signals_df,
        daily_returns=None,
        daily_weights=None,
    )

    monkeypatch.setattr(backtest_module, "has_permission", lambda user, perm: True)

    backtest_module._render_yahoo_backtest_details(result, {"user_id": "u1"})

    # Should render trade P&L with computed trades
    assert any("Trade P&L" in label.text for label in dummy_ui.labels)

    # Should have at least one table (trades table)
    assert len(dummy_ui.tables) > 0


@pytest.mark.asyncio()
async def test_render_backtest_result_export_permission_denied(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _render_backtest_result shows export denial message without permission."""
    result = SimpleNamespace(
        backtest_id="b1",
        alpha_name="alpha1",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 2, 1),
        n_days=31,
        n_symbols_avg=100.0,
        mean_ic=0.05,
        icir=1.5,
        hit_rate=0.55,
        coverage=0.95,
        turnover_result=SimpleNamespace(average_turnover=0.25),
        daily_ic=None,
        dataset_version_ids=None,
        daily_prices=None,
        daily_signals=None,
        daily_returns=None,
        daily_weights=None,
    )

    monkeypatch.setattr(backtest_module, "has_permission", lambda user, perm: False)

    backtest_module._render_backtest_result(result, {"user_id": "u1"})

    # Should show permission denial message
    assert any("Export requires EXPORT_DATA permission" in label.text for label in dummy_ui.labels)
    # Should not show download buttons
    assert not any("Download" in b.label for b in dummy_ui.buttons)


@pytest.mark.asyncio()
async def test_render_backtest_result_with_export_permission(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _render_backtest_result shows export buttons with permission."""
    result = SimpleNamespace(
        backtest_id="b1",
        alpha_name="alpha1",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 2, 1),
        n_days=31,
        n_symbols_avg=100.0,
        mean_ic=0.05,
        icir=1.5,
        hit_rate=0.55,
        coverage=0.95,
        turnover_result=SimpleNamespace(average_turnover=0.25),
        daily_ic=None,
        dataset_version_ids={"provider": "yfinance"},
        daily_prices=None,
        daily_signals=None,
        daily_returns=None,
        daily_weights=None,
    )

    monkeypatch.setattr(backtest_module, "has_permission", lambda user, perm: True)

    backtest_module._render_backtest_result(result, {"user_id": "u1"})

    # Should show export section
    assert any("Export Data" in label.text for label in dummy_ui.labels)
    # Should show download buttons
    assert any("Download Metrics JSON" in b.label for b in dummy_ui.buttons)


@pytest.mark.asyncio()
async def test_render_backtest_result_ic_note_displayed(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test IC unavailability note displayed when daily_ic has no valid values."""
    # Create daily_ic with no valid values
    daily_ic_df = pl.DataFrame(
        {
            "rank_ic": [float("nan"), float("inf"), None],
        }
    )

    result = SimpleNamespace(
        backtest_id="b1",
        alpha_name="alpha1",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 2, 1),
        n_days=31,
        n_symbols_avg=100.0,
        mean_ic=None,
        icir=None,
        hit_rate=0.55,
        coverage=0.95,
        turnover_result=None,
        daily_ic=daily_ic_df,
        dataset_version_ids=None,
        daily_prices=None,
        daily_signals=None,
        daily_returns=None,
        daily_weights=None,
    )

    monkeypatch.setattr(backtest_module, "has_permission", lambda user, perm: False)

    backtest_module._render_backtest_result(result, {"user_id": "u1"})

    # Should display IC unavailability note
    assert any(
        "IC/ICIR unavailable" in label.text for label in dummy_ui.labels
    ), f"Labels: {[label.text for label in dummy_ui.labels]}"


@pytest.mark.asyncio()
async def test_download_metrics_json_triggered(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test download metrics JSON button triggers download."""
    result = SimpleNamespace(
        backtest_id="b1",
        alpha_name="alpha1",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 2, 1),
        n_days=31,
        n_symbols_avg=100.0,
        mean_ic=0.05,
        icir=1.5,
        hit_rate=0.55,
        coverage=0.95,
        turnover_result=SimpleNamespace(average_turnover=0.25),
        daily_ic=None,
        dataset_version_ids=None,
        daily_prices=None,
        daily_signals=None,
        daily_returns=None,
        daily_weights=None,
    )

    monkeypatch.setattr(backtest_module, "has_permission", lambda user, perm: True)

    backtest_module._render_backtest_result(result, {"user_id": "u1"})

    # Find and trigger download button
    download_btn = next(b for b in dummy_ui.buttons if "Download Metrics JSON" in b.label)
    await _call(download_btn._on_click)

    # Should trigger download
    assert len(dummy_ui.downloads) == 1
    assert "metrics_b1.json" in dummy_ui.downloads[0]["filename"]


@pytest.mark.asyncio()
async def test_render_price_chart_with_buy_sell_signals(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test price chart renders with BUY/SELL markers."""
    prices_df = pl.DataFrame(
        {
            "symbol": ["AAPL", "AAPL", "AAPL"],
            "date": [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)],
            "price": [150.0, 151.0, 149.0],
            "permno": [1, 1, 1],
        }
    )
    signals_df = pl.DataFrame(
        {
            "permno": [1, 1, 1],
            "date": [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)],
            "signal": [1.0, -1.0, 0.5],  # BUY, SELL, BUY
        }
    )

    result = SimpleNamespace(
        backtest_id="b1",
        daily_prices=prices_df,
        daily_signals=signals_df,
        daily_returns=None,
        daily_weights=None,
    )

    monkeypatch.setattr(backtest_module, "has_permission", lambda user, perm: True)

    backtest_module._render_yahoo_backtest_details(result, {"user_id": "u1"})

    # Should render plotly chart
    assert len(dummy_ui.plotlies) > 0


@pytest.mark.asyncio()
async def test_compute_trades_mark_to_market_open_position(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test trade computation marks to market an open position at series end."""
    prices_df = pl.DataFrame(
        {
            "symbol": ["AAPL", "AAPL", "AAPL"],
            "date": [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)],
            "price": [100.0, 105.0, 110.0],
            "permno": [1, 1, 1],
        }
    )
    signals_df = pl.DataFrame(
        {
            "permno": [1, 1],
            "date": [date(2025, 1, 1), date(2025, 1, 2)],
            "signal": [1.0, 1.0],  # BUY and hold (open position)
        }
    )

    result = SimpleNamespace(
        backtest_id="b1",
        daily_prices=prices_df,
        daily_signals=signals_df,
        daily_returns=None,
        daily_weights=None,
    )

    monkeypatch.setattr(backtest_module, "has_permission", lambda user, perm: True)

    backtest_module._render_yahoo_backtest_details(result, {"user_id": "u1"})

    # Should render trade P&L (includes mark-to-market of open position)
    assert any("Trade P&L" in label.text for label in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_render_comparison_table_with_null_turnover(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test comparison table handles None turnover_result gracefully."""
    results = [
        SimpleNamespace(
            alpha_name="alpha1",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 2, 1),
            mean_ic=0.05,
            icir=1.5,
            hit_rate=0.55,
            coverage=0.95,
            n_days=31,
            turnover_result=None,  # No turnover data
        )
    ]

    backtest_module._render_comparison_table(results)

    # Should handle gracefully (less than 2 results message)
    assert any("at least 2 backtests" in label.text for label in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_render_backtest_result_no_provider_info(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test result rendering without provider information."""
    result = SimpleNamespace(
        backtest_id="b1",
        alpha_name="alpha1",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 2, 1),
        n_days=31,
        n_symbols_avg=100.0,
        mean_ic=0.05,
        icir=1.5,
        hit_rate=0.55,
        coverage=0.95,
        turnover_result=None,
        daily_ic=None,
        dataset_version_ids=None,  # No provider info
        daily_prices=None,
        daily_signals=None,
        daily_returns=None,
        daily_weights=None,
    )

    monkeypatch.setattr(backtest_module, "has_permission", lambda user, perm: False)

    backtest_module._render_backtest_result(result, {"user_id": "u1"})

    # Should render without crashing
    assert any("alpha1" in label.text for label in dummy_ui.labels)
    # Provider label should not appear
    assert not any("Data Source:" in label.text for label in dummy_ui.labels)


# ==========================
# ADDITIONAL COVERAGE TESTS
# ==========================


@pytest.mark.asyncio()
async def test_render_backtest_results_comparison_mode_with_jobs(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _render_backtest_results comparison mode with multiple completed jobs."""
    jobs = [
        {
            "job_id": "j1",
            "alpha_name": "alpha1",
            "start_date": "2025-01-01",
            "end_date": "2025-02-01",
            "status": "completed",
            "provider": "crsp",
            "mean_ic": 0.05,
            "icir": 1.5,
            "hit_rate": 0.55,
            "result_path": "/path/to/result1",
        },
        {
            "job_id": "j2",
            "alpha_name": "alpha2",
            "start_date": "2025-01-01",
            "end_date": "2025-02-01",
            "status": "completed",
            "provider": "yfinance",
            "mean_ic": 0.03,
            "icir": 1.2,
            "hit_rate": 0.52,
            "result_path": "/path/to/result2",
        },
    ]
    monkeypatch.setattr(backtest_module, "_get_user_jobs_sync", lambda *_args, **_kwargs: jobs)

    async def io_bound(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    monkeypatch.setattr(backtest_module.run, "io_bound", io_bound)

    class FakePool:
        pass

    class FakeRedis:
        pass

    await backtest_module._render_backtest_results(
        {"user_id": "u1"}, FakePool(), FakeRedis()  # type: ignore[arg-type]
    )

    # Should have comparison checkbox
    assert len(dummy_ui.checkboxes) == 1


@pytest.mark.asyncio()
async def test_render_backtest_results_with_failed_and_cancelled_jobs(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _render_backtest_results handles failed/cancelled job statuses."""
    jobs = [
        {
            "job_id": "j1",
            "alpha_name": "alpha1",
            "start_date": "2025-01-01",
            "end_date": "2025-02-01",
            "status": "failed",
            "provider": "crsp",
            "error_message": "Test error",
            "result_path": None,
        },
        {
            "job_id": "j2",
            "alpha_name": "alpha2",
            "start_date": "2025-01-01",
            "end_date": "2025-02-01",
            "status": "cancelled",
            "provider": "yfinance",
            "result_path": None,
        },
    ]
    monkeypatch.setattr(backtest_module, "_get_user_jobs_sync", lambda *_args, **_kwargs: jobs)

    async def io_bound(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    monkeypatch.setattr(backtest_module.run, "io_bound", io_bound)

    class FakePool:
        pass

    class FakeRedis:
        pass

    await backtest_module._render_backtest_results(
        {"user_id": "u1"}, FakePool(), FakeRedis()  # type: ignore[arg-type]
    )

    # Should render without crashing - contains expansion items


@pytest.mark.asyncio()
async def test_cancel_job_success(dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test successful job cancellation."""
    jobs = [
        {
            "job_id": "j1",
            "alpha_name": "alpha1",
            "start_date": "2025-01-01",
            "end_date": "2025-02-01",
            "status": "running",
            "progress_pct": 50.0,
            "provider": "crsp",
            "created_at": "2025-01-01T12:00:00",
        }
    ]
    monkeypatch.setattr(backtest_module, "_get_user_jobs_sync", lambda *_args, **_kwargs: jobs)
    # Ownership check passes
    monkeypatch.setattr(backtest_module, "_verify_job_ownership", lambda *_args: True)

    class DummyQueue:
        def cancel_job(self, job_id: str) -> None:
            pass  # Success - no exception

    monkeypatch.setattr(backtest_module, "_get_job_queue", lambda: DummyQueue())

    async def io_bound(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    monkeypatch.setattr(backtest_module.run, "io_bound", io_bound)

    class MockLifecycleMgr:
        @staticmethod
        async def register_cleanup_callback(client_id: str, callback: Callable) -> None:
            pass

        @staticmethod
        def get():
            return MockLifecycleMgr()

    monkeypatch.setattr(backtest_module, "ClientLifecycleManager", MockLifecycleMgr)

    class FakePool:
        pass

    class FakeRedis:
        pass

    await backtest_module._render_running_jobs(
        {"user_id": "u1"}, FakePool(), FakeRedis()  # type: ignore[arg-type]
    )

    cancel_button = next(b for b in dummy_ui.buttons if b.label == "Cancel")
    await _call(cancel_button._on_click)

    # Should show success notification
    assert any("cancelled" in n["text"].lower() for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_cancel_job_connection_error(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test job cancellation handles connection errors."""
    jobs = [
        {
            "job_id": "j1",
            "alpha_name": "alpha1",
            "start_date": "2025-01-01",
            "end_date": "2025-02-01",
            "status": "running",
            "progress_pct": 50.0,
            "provider": "crsp",
            "created_at": "2025-01-01T12:00:00",
        }
    ]
    monkeypatch.setattr(backtest_module, "_get_user_jobs_sync", lambda *_args, **_kwargs: jobs)
    monkeypatch.setattr(backtest_module, "_verify_job_ownership", lambda *_args: True)

    class FailingQueue:
        def cancel_job(self, job_id: str) -> None:
            raise ConnectionError("DB connection failed")

    monkeypatch.setattr(backtest_module, "_get_job_queue", lambda: FailingQueue())

    async def io_bound(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    monkeypatch.setattr(backtest_module.run, "io_bound", io_bound)

    class MockLifecycleMgr:
        @staticmethod
        async def register_cleanup_callback(client_id: str, callback: Callable) -> None:
            pass

        @staticmethod
        def get():
            return MockLifecycleMgr()

    monkeypatch.setattr(backtest_module, "ClientLifecycleManager", MockLifecycleMgr)

    class FakePool:
        pass

    class FakeRedis:
        pass

    await backtest_module._render_running_jobs(
        {"user_id": "u1"}, FakePool(), FakeRedis()  # type: ignore[arg-type]
    )

    cancel_button = next(b for b in dummy_ui.buttons if b.label == "Cancel")
    await _call(cancel_button._on_click)

    # Should show error notification
    assert any("Database connection error" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_cancel_job_value_error(dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test job cancellation handles value errors."""
    jobs = [
        {
            "job_id": "j1",
            "alpha_name": "alpha1",
            "start_date": "2025-01-01",
            "end_date": "2025-02-01",
            "status": "running",
            "progress_pct": 50.0,
            "provider": "crsp",
            "created_at": "2025-01-01T12:00:00",
        }
    ]
    monkeypatch.setattr(backtest_module, "_get_user_jobs_sync", lambda *_args, **_kwargs: jobs)
    monkeypatch.setattr(backtest_module, "_verify_job_ownership", lambda *_args: True)

    class FailingQueue:
        def cancel_job(self, job_id: str) -> None:
            raise ValueError("Invalid job state")

    monkeypatch.setattr(backtest_module, "_get_job_queue", lambda: FailingQueue())

    async def io_bound(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    monkeypatch.setattr(backtest_module.run, "io_bound", io_bound)

    class MockLifecycleMgr:
        @staticmethod
        async def register_cleanup_callback(client_id: str, callback: Callable) -> None:
            pass

        @staticmethod
        def get():
            return MockLifecycleMgr()

    monkeypatch.setattr(backtest_module, "ClientLifecycleManager", MockLifecycleMgr)

    class FakePool:
        pass

    class FakeRedis:
        pass

    await backtest_module._render_running_jobs(
        {"user_id": "u1"}, FakePool(), FakeRedis()  # type: ignore[arg-type]
    )

    cancel_button = next(b for b in dummy_ui.buttons if b.label == "Cancel")
    await _call(cancel_button._on_click)

    # Should show error notification
    assert any("Invalid operation" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_render_running_jobs_pending_status(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test running jobs tab shows pending jobs with hourglass icon."""
    jobs = [
        {
            "job_id": "j1",
            "alpha_name": "alpha1",
            "start_date": "2025-01-01",
            "end_date": "2025-02-01",
            "status": "pending",  # Pending status
            "progress_pct": 0.0,
            "provider": "yfinance",
            "created_at": "2025-01-01T12:00:00",
        }
    ]
    monkeypatch.setattr(backtest_module, "_get_user_jobs_sync", lambda *_args, **_kwargs: jobs)

    async def io_bound(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    monkeypatch.setattr(backtest_module.run, "io_bound", io_bound)

    class MockLifecycleMgr:
        @staticmethod
        async def register_cleanup_callback(client_id: str, callback: Callable) -> None:
            pass

        @staticmethod
        def get():
            return MockLifecycleMgr()

    monkeypatch.setattr(backtest_module, "ClientLifecycleManager", MockLifecycleMgr)

    class FakePool:
        pass

    class FakeRedis:
        pass

    await backtest_module._render_running_jobs(
        {"user_id": "u1"}, FakePool(), FakeRedis()  # type: ignore[arg-type]
    )

    # Should show YFINANCE provider
    assert any("YFINANCE" in label.text for label in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_fmt_float_with_string_input(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _fmt_float handles string inputs gracefully."""
    # Invalid string that can't be converted to float
    result = backtest_module._fmt_float("invalid", "{:.2f}")  # type: ignore[arg-type]
    assert result == "N/A"


@pytest.mark.asyncio()
async def test_fmt_pct_with_string_input(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _fmt_pct handles string inputs gracefully."""
    # Invalid string that can't be converted to float
    result = backtest_module._fmt_pct("invalid", "{:.1f}%")  # type: ignore[arg-type]
    assert result == "N/A"


@pytest.mark.asyncio()
async def test_render_yahoo_details_signals_from_signals_df(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _render_yahoo_backtest_details extracts symbols from signals when prices is None."""
    # Signals with symbol column but no prices
    signals_df = pl.DataFrame(
        {
            "symbol": ["AAPL", "MSFT"],
            "permno": [1, 2],
            "date": [date(2025, 1, 1), date(2025, 1, 1)],
            "signal": [1.0, -1.0],
        }
    )

    result = SimpleNamespace(
        backtest_id="b1",
        daily_prices=None,
        daily_signals=signals_df,
        daily_returns=None,
        daily_weights=None,
    )

    monkeypatch.setattr(backtest_module, "has_permission", lambda user, perm: False)

    backtest_module._render_yahoo_backtest_details(result, {"user_id": "u1"})

    # Should extract symbols from signals
    assert any("2 symbols" in label.text for label in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_render_comparison_table_with_two_results(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test comparison table renders correctly with exactly 2 results."""
    results = [
        SimpleNamespace(
            alpha_name="alpha1",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 2, 1),
            mean_ic=0.05,
            icir=1.5,
            hit_rate=0.55,
            coverage=0.95,
            n_days=31,
            turnover_result=SimpleNamespace(average_turnover=0.25),
        ),
        SimpleNamespace(
            alpha_name="alpha2",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 2, 1),
            mean_ic=None,  # Null mean_ic
            icir=None,  # Null icir
            hit_rate=None,  # Null hit_rate
            coverage=None,  # Null coverage
            n_days=31,
            turnover_result=None,  # Null turnover
        ),
    ]

    backtest_module._render_comparison_table(results)

    # Should render table
    assert len(dummy_ui.tables) == 1
    table = dummy_ui.tables[0]
    assert len(table["rows"]) == 2
    # Second row should have N/A for null values
    assert table["rows"][1]["mean_ic"] == "N/A"
    assert table["rows"][1]["icir"] == "N/A"
    assert table["rows"][1]["turnover"] == "N/A"


@pytest.mark.asyncio()
async def test_render_backtest_result_with_daily_ic_using_ic_column(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _render_backtest_result with daily_ic using 'ic' column instead of 'rank_ic'."""
    # Create daily_ic with 'ic' column (not 'rank_ic')
    daily_ic_df = pl.DataFrame(
        {
            "ic": [0.1, 0.2, float("nan")],
        }
    )

    result = SimpleNamespace(
        backtest_id="b1",
        alpha_name="alpha1",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 2, 1),
        n_days=31,
        n_symbols_avg=100.0,
        mean_ic=0.15,
        icir=1.5,
        hit_rate=0.55,
        coverage=0.95,
        turnover_result=None,
        daily_ic=daily_ic_df,
        dataset_version_ids=None,
        daily_prices=None,
        daily_signals=None,
        daily_returns=None,
        daily_weights=None,
    )

    monkeypatch.setattr(backtest_module, "has_permission", lambda user, perm: False)

    backtest_module._render_backtest_result(result, {"user_id": "u1"})

    # Should render without IC unavailability note
    assert any("0.15" in label.text for label in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_render_yahoo_details_with_short_signals(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _render_yahoo_backtest_details with short signals (negative values)."""
    prices_df = pl.DataFrame(
        {
            "symbol": ["AAPL", "AAPL", "AAPL"],
            "date": [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)],
            "price": [100.0, 105.0, 102.0],
            "permno": [1, 1, 1],
        }
    )
    signals_df = pl.DataFrame(
        {
            "permno": [1, 1, 1],
            "date": [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)],
            "signal": [-1.0, 0.0, -0.5],  # SHORT, FLAT, SHORT
        }
    )

    result = SimpleNamespace(
        backtest_id="b1",
        daily_prices=prices_df,
        daily_signals=signals_df,
        daily_returns=None,
        daily_weights=None,
    )

    monkeypatch.setattr(backtest_module, "has_permission", lambda user, perm: True)

    backtest_module._render_yahoo_backtest_details(result, {"user_id": "u1"})

    # Should render trade P&L with short trades
    assert any("Trade P&L" in label.text for label in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_render_yahoo_details_with_mixed_signals(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _render_yahoo_backtest_details with mixed long/short signals across symbols."""
    prices_df = pl.DataFrame(
        {
            "symbol": ["AAPL", "AAPL", "MSFT", "MSFT"],
            "date": [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 1), date(2025, 1, 2)],
            "price": [100.0, 105.0, 200.0, 195.0],
            "permno": [1, 1, 2, 2],
        }
    )
    signals_df = pl.DataFrame(
        {
            "permno": [1, 1, 2, 2],
            "date": [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 1), date(2025, 1, 2)],
            "signal": [1.0, -1.0, -1.0, 1.0],  # AAPL: long then sell, MSFT: short then cover
        }
    )

    result = SimpleNamespace(
        backtest_id="b1",
        daily_prices=prices_df,
        daily_signals=signals_df,
        daily_returns=None,
        daily_weights=None,
    )

    monkeypatch.setattr(backtest_module, "has_permission", lambda user, perm: True)

    backtest_module._render_yahoo_backtest_details(result, {"user_id": "u1"})

    # Should render universe section showing 2 symbols
    assert any("2 symbols" in label.text for label in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_render_yahoo_details_all_flat_signals(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _render_yahoo_backtest_details when all signals are flat (no trades)."""
    prices_df = pl.DataFrame(
        {
            "symbol": ["AAPL", "AAPL"],
            "date": [date(2025, 1, 1), date(2025, 1, 2)],
            "price": [100.0, 101.0],
            "permno": [1, 1],
        }
    )
    signals_df = pl.DataFrame(
        {
            "permno": [1, 1],
            "date": [date(2025, 1, 1), date(2025, 1, 2)],
            "signal": [0.0, 0.0],  # All flat - no trades
        }
    )

    result = SimpleNamespace(
        backtest_id="b1",
        daily_prices=prices_df,
        daily_signals=signals_df,
        daily_returns=None,
        daily_weights=None,
    )

    monkeypatch.setattr(backtest_module, "has_permission", lambda user, perm: True)

    backtest_module._render_yahoo_backtest_details(result, {"user_id": "u1"})

    # Should still render universe section
    assert any("1 symbols" in label.text for label in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_render_yahoo_details_download_signals_csv_success(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test successful signal CSV download."""
    prices_df = pl.DataFrame(
        {
            "symbol": ["AAPL", "AAPL"],
            "date": [date(2025, 1, 1), date(2025, 1, 2)],
            "price": [150.0, 151.0],
            "permno": [1, 1],
        }
    )
    signals_df = pl.DataFrame(
        {
            "permno": [1, 1],
            "date": [date(2025, 1, 1), date(2025, 1, 2)],
            "signal": [1.0, -1.0],  # BUY and SELL signals
        }
    )
    returns_df = pl.DataFrame(
        {
            "permno": [1, 1],
            "date": [date(2025, 1, 1), date(2025, 1, 2)],
            "return": [0.01, -0.02],
        }
    )
    weights_df = pl.DataFrame(
        {
            "permno": [1, 1],
            "date": [date(2025, 1, 1), date(2025, 1, 2)],
            "weight": [0.5, 0.3],
        }
    )

    result = SimpleNamespace(
        backtest_id="b1",
        daily_prices=prices_df,
        daily_signals=signals_df,
        daily_returns=returns_df,
        daily_weights=weights_df,
    )

    monkeypatch.setattr(backtest_module, "has_permission", lambda user, perm: True)

    backtest_module._render_yahoo_backtest_details(result, {"user_id": "u1"})

    # Find and trigger download button
    download_btn = next(b for b in dummy_ui.buttons if "Download Signals CSV" in b.label)
    await _call(download_btn._on_click)

    # Should trigger download
    assert len(dummy_ui.downloads) == 1
    assert "signals_b1" in dummy_ui.downloads[0]["filename"]


@pytest.mark.asyncio()
async def test_render_yahoo_details_download_trades_csv_success(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test successful trades CSV download."""
    prices_df = pl.DataFrame(
        {
            "symbol": ["AAPL", "AAPL", "AAPL"],
            "date": [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)],
            "price": [100.0, 105.0, 110.0],
            "permno": [1, 1, 1],
        }
    )
    signals_df = pl.DataFrame(
        {
            "permno": [1, 1, 1],
            "date": [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)],
            "signal": [1.0, -1.0, 0.0],  # BUY, SELL, FLAT
        }
    )

    result = SimpleNamespace(
        backtest_id="b1",
        daily_prices=prices_df,
        daily_signals=signals_df,
        daily_returns=None,
        daily_weights=None,
    )

    monkeypatch.setattr(backtest_module, "has_permission", lambda user, perm: True)

    backtest_module._render_yahoo_backtest_details(result, {"user_id": "u1"})

    # Find and trigger download button
    download_btn = next(b for b in dummy_ui.buttons if "Download Trades CSV" in b.label)
    await _call(download_btn._on_click)

    # Should trigger download
    assert len(dummy_ui.downloads) == 1
    assert "trades_b1" in dummy_ui.downloads[0]["filename"]
