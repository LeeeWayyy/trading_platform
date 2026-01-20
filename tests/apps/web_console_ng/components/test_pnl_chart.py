"""Tests for pnl_chart NiceGUI components."""

from __future__ import annotations

from typing import Any

import pytest

from apps.web_console_ng.components import pnl_chart as pnl_chart_module


class DummyElement:
    """Minimal NiceGUI element mock with class chaining."""

    def __init__(self, text: str | None = None, fig: Any | None = None) -> None:
        self.text = text
        self.fig = fig
        self.class_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def classes(self, *args: Any, **kwargs: Any) -> DummyElement:
        self.class_calls.append((args, kwargs))
        return self


class DummyUI:
    """Capture NiceGUI calls for assertions."""

    def __init__(self) -> None:
        self.labels: list[DummyElement] = []
        self.plotly_calls: list[DummyElement] = []

    def label(self, text: str) -> DummyElement:
        element = DummyElement(text=text)
        self.labels.append(element)
        return element

    def plotly(self, fig: Any) -> DummyElement:
        element = DummyElement(fig=fig)
        self.plotly_calls.append(element)
        return element


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    dummy = DummyUI()
    monkeypatch.setattr(pnl_chart_module, "ui", dummy)
    return dummy


def test_render_pnl_equity_curve_no_data_shows_message(dummy_ui: DummyUI) -> None:
    pnl_chart_module.render_pnl_equity_curve([])

    assert len(dummy_ui.labels) == 1
    assert "No performance data available" in dummy_ui.labels[0].text
    assert dummy_ui.plotly_calls == []


def test_render_pnl_equity_curve_warns_on_skipped_and_sorts(dummy_ui: DummyUI) -> None:
    daily_pnl = [
        {"date": "2024-01-02", "cumulative_realized_pl": 100.0, "drawdown_pct": -0.05},
        {"date": "2024-01-01", "cumulative_realized_pl": 50.0, "drawdown_pct": -0.02},
        {"date": "2024-01-03", "cumulative_realized_pl": None, "drawdown_pct": -0.03},
    ]

    pnl_chart_module.render_pnl_equity_curve(daily_pnl)

    assert any("skipped" in label.text for label in dummy_ui.labels)
    assert len(dummy_ui.plotly_calls) == 1

    fig = dummy_ui.plotly_calls[0].fig
    assert list(fig.data[0].x) == ["2024-01-01", "2024-01-02"]
    assert list(fig.data[0].y) == pytest.approx([50.0, 100.0])


def test_render_pnl_drawdown_chart_no_data_shows_message(dummy_ui: DummyUI) -> None:
    pnl_chart_module.render_pnl_drawdown_chart([])

    assert len(dummy_ui.labels) == 1
    assert "No drawdown data available" in dummy_ui.labels[0].text
    assert dummy_ui.plotly_calls == []


def test_render_pnl_drawdown_chart_handles_invalid_drawdown(dummy_ui: DummyUI) -> None:
    daily_pnl = [
        {"date": "2024-01-01", "cumulative_realized_pl": 50.0, "drawdown_pct": -0.02},
        {"date": "2024-01-02", "cumulative_realized_pl": 100.0, "drawdown_pct": None},
    ]

    pnl_chart_module.render_pnl_drawdown_chart(daily_pnl)

    assert len(dummy_ui.plotly_calls) == 1
    fig = dummy_ui.plotly_calls[0].fig
    assert list(fig.data[0].y) == [-0.02, None]
