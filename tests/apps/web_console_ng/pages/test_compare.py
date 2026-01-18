"""Unit tests for compare page."""

from __future__ import annotations

import os
import sys
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

os.environ["WEB_CONSOLE_NG_DEBUG"] = "true"
os.environ.setdefault("NICEGUI_STORAGE_SECRET", "test-secret")

from apps.web_console_ng.pages import compare as compare_module
from tests.apps.web_console_ng.pages.ui_test_utils import DummyUI


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    ui = DummyUI()
    monkeypatch.setattr(compare_module, "ui", ui)
    return ui


@pytest.fixture()
def cpu_bound_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _cpu_bound(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(compare_module.run, "cpu_bound", _cpu_bound)


def test_default_date_range_span() -> None:
    start, end = compare_module._default_date_range()
    assert (end - start).days == compare_module.DEFAULT_LOOKBACK_DAYS


@pytest.mark.asyncio()
async def test_fetch_comparison_data_calls_service(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], date, date]] = []

    class DummyService:
        async def get_comparison_data(self, strategy_ids, date_from, date_to):
            calls.append((strategy_ids, date_from, date_to))
            return {"metrics": {}}

    dummy_scoped_module = SimpleNamespace(StrategyScopedDataAccess=lambda **_kwargs: object())
    dummy_service_module = SimpleNamespace(ComparisonService=lambda _scoped: DummyService())

    monkeypatch.setitem(sys.modules, "libs.web_console_data.strategy_scoped_queries", dummy_scoped_module)
    monkeypatch.setitem(sys.modules, "libs.web_console_services.comparison_service", dummy_service_module)

    user = {"user_id": "u1"}
    strategies = ["s1", "s2"]
    start = date(2024, 1, 1)
    end = date(2024, 1, 31)
    pool = object()

    data = await compare_module._fetch_comparison_data(user, strategies, start, end, pool)

    assert data == {"metrics": {}}
    assert calls == [(strategies, start, end)]


def test_render_metrics_table_creates_rows(dummy_ui: DummyUI) -> None:
    metrics = {
        "s1": {"total_return": 10, "volatility": 2, "sharpe": 1.2, "max_drawdown": -3},
        "s2": {"total_return": 5, "volatility": 1, "sharpe": 0.8, "max_drawdown": -2},
    }

    compare_module._render_metrics_table(metrics)

    assert dummy_ui.tables
    _, rows = dummy_ui.tables[-1]
    assert len(rows) == 2


def test_render_equity_comparison_creates_plotly(dummy_ui: DummyUI) -> None:
    curves = [
        {"strategy_id": "s1", "equity": [{"date": "2024-01-01", "equity": 1.0}]},
        {"strategy_id": "s2", "equity": [{"date": "2024-01-01", "equity": 2.0}]},
    ]

    compare_module._render_equity_comparison(curves)

    assert dummy_ui.plotlies


@pytest.mark.asyncio()
async def test_portfolio_simulator_invalid_weights(
    dummy_ui: DummyUI, cpu_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class DummyComparisonService:
        @staticmethod
        def validate_weights(_weights):
            return False, "Weights must sum to 1.0"

        def compute_combined_portfolio(self, _weights, _pnl):
            return {}

    monkeypatch.setitem(
        sys.modules,
        "libs.web_console_services.comparison_service",
        SimpleNamespace(ComparisonService=DummyComparisonService),
    )

    pnl_frame = pd.DataFrame({"s1": [1, 2], "s2": [2, 3]})

    await compare_module._render_portfolio_simulator(["s1", "s2"], {"s1": 0.5, "s2": 0.5}, pnl_frame)

    simulate_btn = next(b for b in dummy_ui.buttons if b.text == "Simulate Portfolio")
    assert simulate_btn.on_click_cb is not None
    await simulate_btn.on_click_cb()

    assert any("Weights must sum" in text for text, _ in dummy_ui.labels)
