"""Tests for ic_chart NiceGUI component."""

from __future__ import annotations

from typing import Any

import polars as pl
import pytest

from apps.web_console_ng.components import ic_chart as ic_chart_module


class DummyElement:
    """Minimal NiceGUI element mock with class chaining."""

    def __init__(self, text: str | None = None, fig: Any | None = None) -> None:
        self.text = text
        self.fig = fig
        self.class_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def classes(self, *args: Any, **kwargs: Any) -> "DummyElement":
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
    monkeypatch.setattr(ic_chart_module, "ui", dummy)
    return dummy


def test_render_ic_chart_no_data_shows_message(dummy_ui: DummyUI) -> None:
    ic_chart_module.render_ic_chart(None)

    assert len(dummy_ui.labels) == 1
    assert "No IC data" in dummy_ui.labels[0].text
    assert dummy_ui.plotly_calls == []


def test_render_ic_chart_missing_columns(dummy_ui: DummyUI) -> None:
    daily_ic = pl.DataFrame({"date": ["2024-01-01"], "ic": [0.1]})

    ic_chart_module.render_ic_chart(daily_ic)

    assert len(dummy_ui.labels) == 1
    assert "missing columns" in dummy_ui.labels[0].text
    assert dummy_ui.plotly_calls == []


def test_render_ic_chart_builds_traces_and_layout(dummy_ui: DummyUI) -> None:
    daily_ic = pl.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-02"],
            "ic": [0.1, -0.05],
            "rank_ic": [0.12, -0.01],
            "rolling_ic_20d": [0.08, 0.02],
        }
    )

    ic_chart_module.render_ic_chart(daily_ic, title="IC", height=360)

    assert dummy_ui.labels == []
    assert len(dummy_ui.plotly_calls) == 1

    fig = dummy_ui.plotly_calls[0].fig
    assert len(fig.data) == 3
    assert fig.data[0].name == "Rank IC"
    assert fig.data[1].name == "Rolling 20d Rank IC"
    assert fig.data[2].name == "Pearson IC"
    assert list(fig.data[0].x) == ["2024-01-01", "2024-01-02"]
    assert fig.layout.title.text == "IC"
    assert fig.layout.height == 360


def test_render_ic_chart_without_rolling_column(dummy_ui: DummyUI) -> None:
    daily_ic = pl.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-02"],
            "ic": [0.1, -0.05],
            "rank_ic": [0.12, -0.01],
        }
    )

    ic_chart_module.render_ic_chart(daily_ic)

    fig = dummy_ui.plotly_calls[0].fig
    assert len(fig.data) == 2
    assert [trace.name for trace in fig.data] == ["Rank IC", "Pearson IC"]


def test_render_ic_chart_handles_invalid_data(dummy_ui: DummyUI) -> None:
    class BadDailyIC:
        columns = ["date", "ic", "rank_ic"]

        def is_empty(self) -> bool:
            return False

        def __getitem__(self, key: str) -> Any:
            raise IndexError("boom")

    ic_chart_module.render_ic_chart(BadDailyIC())

    assert len(dummy_ui.labels) == 1
    assert "Chart unavailable" in dummy_ui.labels[0].text
    assert dummy_ui.plotly_calls == []
