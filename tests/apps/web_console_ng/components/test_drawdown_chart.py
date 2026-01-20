"""Tests for drawdown_chart NiceGUI component."""

from __future__ import annotations

from typing import Any

import polars as pl
import pytest

from apps.web_console_ng.components import drawdown_chart as drawdown_chart_module


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
        self.notifications: list[tuple[str, str | None]] = []

    def label(self, text: str) -> DummyElement:
        element = DummyElement(text=text)
        self.labels.append(element)
        return element

    def plotly(self, fig: Any) -> DummyElement:
        element = DummyElement(fig=fig)
        self.plotly_calls.append(element)
        return element

    def notify(self, text: str, type: str | None = None) -> None:
        self.notifications.append((text, type))


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    dummy = DummyUI()
    monkeypatch.setattr(drawdown_chart_module, "ui", dummy)
    return dummy


def test_render_drawdown_chart_no_data_shows_message(dummy_ui: DummyUI) -> None:
    drawdown_chart_module.render_drawdown_chart(None)

    assert len(dummy_ui.labels) == 1
    assert "No return data available" in dummy_ui.labels[0].text
    assert dummy_ui.plotly_calls == []


def test_render_drawdown_chart_missing_columns_notifies(dummy_ui: DummyUI) -> None:
    df = pl.DataFrame({"date": ["2024-01-01"]})

    drawdown_chart_module.render_drawdown_chart(df)

    assert len(dummy_ui.notifications) == 1
    assert "Missing columns" in dummy_ui.notifications[0][0]
    assert dummy_ui.plotly_calls == []


def test_render_drawdown_chart_filters_invalid_and_adds_annotation(dummy_ui: DummyUI) -> None:
    df = pl.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-02", "2024-01-03"],
            "return": [0.0, float("nan"), -0.1],
        }
    )

    drawdown_chart_module.render_drawdown_chart(df)

    assert any("invalid return" in label.text for label in dummy_ui.labels)
    assert len(dummy_ui.plotly_calls) == 1

    fig = dummy_ui.plotly_calls[0].fig
    y_values = list(fig.data[0].y)
    assert y_values == pytest.approx([0.0, -10.0])

    assert fig.layout.annotations
    assert "Max DD" in fig.layout.annotations[0].text
