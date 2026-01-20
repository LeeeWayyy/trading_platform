"""Tests for correlation_matrix NiceGUI component."""

from __future__ import annotations

from typing import Any

import polars as pl
import pytest

from apps.web_console_ng.components import correlation_matrix as correlation_matrix_module


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
    monkeypatch.setattr(correlation_matrix_module, "ui", dummy)
    return dummy


def test_render_correlation_matrix_no_data_shows_message(dummy_ui: DummyUI) -> None:
    correlation_matrix_module.render_correlation_matrix(None)

    assert len(dummy_ui.labels) == 1
    assert "Not enough data" in dummy_ui.labels[0].text
    assert dummy_ui.plotly_calls == []


def test_render_correlation_matrix_empty_frame_shows_message(dummy_ui: DummyUI) -> None:
    correlation_matrix_module.render_correlation_matrix(pl.DataFrame())

    assert len(dummy_ui.labels) == 1
    assert "Not enough data" in dummy_ui.labels[0].text
    assert dummy_ui.plotly_calls == []


def test_render_correlation_matrix_missing_signal_column(dummy_ui: DummyUI) -> None:
    corr_matrix = pl.DataFrame({"alpha": [1.0], "beta": [0.2]})

    correlation_matrix_module.render_correlation_matrix(corr_matrix)

    assert len(dummy_ui.labels) == 1
    assert "missing 'signal'" in dummy_ui.labels[0].text
    assert dummy_ui.plotly_calls == []


def test_render_correlation_matrix_builds_heatmap(dummy_ui: DummyUI) -> None:
    corr_matrix = pl.DataFrame(
        {
            "signal": ["alpha", "beta"],
            "alpha": [1.0, -0.5],
            "beta": [-0.5, 1.0],
        }
    )

    correlation_matrix_module.render_correlation_matrix(corr_matrix, title="Corr", height=320)

    assert dummy_ui.labels == []
    assert len(dummy_ui.plotly_calls) == 1

    fig = dummy_ui.plotly_calls[0].fig
    heatmap = fig.data[0]
    assert list(heatmap.x) == ["alpha", "beta"]
    assert list(heatmap.y) == ["alpha", "beta"]
    assert [list(row) for row in heatmap.z] == [[1.0, -0.5], [-0.5, 1.0]]
    assert heatmap.zmin == -1
    assert heatmap.zmax == 1
    assert heatmap.text[0][0] == "1.00"
    assert fig.layout.title.text == "Corr"
    assert fig.layout.height == 320


def test_render_correlation_matrix_handles_invalid_data(dummy_ui: DummyUI) -> None:
    class BadMatrix:
        columns = ["signal", "alpha"]

        def is_empty(self) -> bool:
            return False

        def to_pandas(self) -> None:
            raise ValueError("boom")

    correlation_matrix_module.render_correlation_matrix(BadMatrix())

    assert len(dummy_ui.labels) == 1
    assert "Chart unavailable" in dummy_ui.labels[0].text
    assert dummy_ui.plotly_calls == []
