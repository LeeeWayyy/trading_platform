"""Tests for decay_curve NiceGUI component."""

from __future__ import annotations

from typing import Any

import polars as pl
import pytest

from apps.web_console_ng.components import decay_curve as decay_curve_module


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
    monkeypatch.setattr(decay_curve_module, "ui", dummy)
    return dummy


def test_render_decay_curve_no_data_shows_message(dummy_ui: DummyUI) -> None:
    decay_curve_module.render_decay_curve(None)

    assert len(dummy_ui.labels) == 1
    assert "No decay curve data" in dummy_ui.labels[0].text
    assert dummy_ui.plotly_calls == []


def test_render_decay_curve_missing_columns(dummy_ui: DummyUI) -> None:
    decay_curve = pl.DataFrame({"horizon": [1, 2], "ic": [0.1, 0.2]})

    decay_curve_module.render_decay_curve(decay_curve)

    assert len(dummy_ui.labels) == 1
    assert "missing columns" in dummy_ui.labels[0].text
    assert dummy_ui.plotly_calls == []


def test_render_decay_curve_builds_traces_and_layout(dummy_ui: DummyUI) -> None:
    decay_curve = pl.DataFrame(
        {
            "horizon": [1, 2, 3],
            "ic": [0.1, 0.05, -0.02],
            "rank_ic": [0.12, 0.08, -0.01],
        }
    )

    decay_curve_module.render_decay_curve(decay_curve, title="Decay", height=420)

    assert dummy_ui.labels == []
    assert len(dummy_ui.plotly_calls) == 1

    fig = dummy_ui.plotly_calls[0].fig
    assert len(fig.data) == 2
    assert list(fig.data[0].x) == [1, 2, 3]
    assert list(fig.data[0].y) == [0.12, 0.08, -0.01]
    assert list(fig.data[1].y) == [0.1, 0.05, -0.02]
    assert fig.layout.title.text == "Decay"
    assert fig.layout.height == 420


def test_render_decay_curve_adds_half_life_annotation(dummy_ui: DummyUI) -> None:
    decay_curve = pl.DataFrame(
        {
            "horizon": [1, 2],
            "ic": [0.1, 0.2],
            "rank_ic": [0.11, 0.22],
        }
    )

    decay_curve_module.render_decay_curve(decay_curve, half_life=2.5)

    fig = dummy_ui.plotly_calls[0].fig
    assert fig.layout.annotations
    assert any("Half-life" in annotation.text for annotation in fig.layout.annotations)


def test_render_decay_curve_handles_invalid_data(dummy_ui: DummyUI) -> None:
    class BadDecay:
        columns = ["horizon", "ic", "rank_ic"]

        def is_empty(self) -> bool:
            return False

        def __getitem__(self, key: str) -> Any:
            raise TypeError("bad data")

    decay_curve_module.render_decay_curve(BadDecay())

    assert len(dummy_ui.labels) == 1
    assert "Chart unavailable" in dummy_ui.labels[0].text
    assert dummy_ui.plotly_calls == []
