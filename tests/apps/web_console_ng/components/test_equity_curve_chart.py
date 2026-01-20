"""Tests for equity_curve_chart NiceGUI component."""

from __future__ import annotations

from typing import Any

import polars as pl
import pytest

from apps.web_console_ng.components import equity_curve_chart as equity_curve_chart_module


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
    monkeypatch.setattr(equity_curve_chart_module, "ui", dummy)
    return dummy


def test_render_equity_curve_no_data_shows_message(dummy_ui: DummyUI) -> None:
    equity_curve_chart_module.render_equity_curve(None)

    assert len(dummy_ui.labels) == 1
    assert "No return data available" in dummy_ui.labels[0].text
    assert dummy_ui.plotly_calls == []


def test_render_equity_curve_missing_columns_shows_schema_error(dummy_ui: DummyUI) -> None:
    df = pl.DataFrame({"date": ["2024-01-01"]})

    equity_curve_chart_module.render_equity_curve(df)

    assert len(dummy_ui.labels) == 1
    assert "missing columns" in dummy_ui.labels[0].text
    assert dummy_ui.plotly_calls == []


def test_render_equity_curve_filters_invalid_returns_and_plots(dummy_ui: DummyUI) -> None:
    df = pl.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-02", "2024-01-03"],
            "return": [0.1, float("nan"), 0.05],
        }
    )

    equity_curve_chart_module.render_equity_curve(df)

    # Warning label for filtered invalid values
    assert any("invalid return" in label.text for label in dummy_ui.labels)
    assert len(dummy_ui.plotly_calls) == 1

    fig = dummy_ui.plotly_calls[0].fig
    y_values = list(fig.data[0].y)

    assert y_values == pytest.approx([10.0, 15.5])


def test_render_equity_curve_data_error_shows_fallback(dummy_ui: DummyUI) -> None:
    df = pl.DataFrame(
        {
            "date": ["2024-01-01"],
            "return": ["bad"],
        }
    )

    equity_curve_chart_module.render_equity_curve(df)

    assert any("Chart unavailable" in label.text for label in dummy_ui.labels)
    assert dummy_ui.plotly_calls == []
