"""Tests for var_chart NiceGUI components."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from apps.web_console_ng.components import var_chart as var_chart_module


class DummyElement:
    """Minimal NiceGUI element mock with class chaining."""

    def __init__(self, text: str | None = None, fig: Any | None = None) -> None:
        self.text = text
        self.fig = fig
        self.class_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.props_calls: list[str] = []
        self.tooltip_text: str | None = None

    def classes(self, *args: Any, **kwargs: Any) -> DummyElement:
        self.class_calls.append((args, kwargs))
        return self

    def props(self, value: str) -> DummyElement:
        self.props_calls.append(value)
        return self

    def tooltip(self, text: str) -> DummyElement:
        self.tooltip_text = text
        return self


class DummyContext(DummyElement):
    """Context manager for row/card UI containers."""

    def __enter__(self) -> DummyContext:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class DummyUI:
    """Capture NiceGUI calls for assertions."""

    def __init__(self) -> None:
        self.labels: list[DummyElement] = []
        self.plotly_calls: list[DummyElement] = []
        self.cards: list[DummyContext] = []
        self.rows: list[DummyContext] = []
        self.icons: list[DummyElement] = []

    def label(self, text: str) -> DummyElement:
        element = DummyElement(text=text)
        self.labels.append(element)
        return element

    def plotly(self, fig: Any) -> DummyElement:
        element = DummyElement(fig=fig)
        self.plotly_calls.append(element)
        return element

    def card(self) -> DummyContext:
        element = DummyContext()
        self.cards.append(element)
        return element

    def row(self) -> DummyContext:
        element = DummyContext()
        self.rows.append(element)
        return element

    def icon(self, name: str, size: str = "") -> DummyElement:
        element = DummyElement(text=name)
        self.icons.append(element)
        return element


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    dummy = DummyUI()
    monkeypatch.setattr(var_chart_module, "ui", dummy)
    return dummy


@pytest.fixture()
def validate_metrics_true(monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, Any]], bool]:
    def _validate(_: dict[str, Any]) -> bool:
        return True

    monkeypatch.setattr(var_chart_module, "validate_var_metrics", _validate)
    return _validate


@pytest.fixture()
def passthrough_history(monkeypatch: pytest.MonkeyPatch) -> Callable[[list[dict[str, Any]]], list[dict[str, Any]]]:
    def _passthrough(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return values

    monkeypatch.setattr(var_chart_module, "validate_var_history", _passthrough)
    return _passthrough


def test_render_var_metrics_no_data_shows_message(dummy_ui: DummyUI) -> None:
    var_chart_module.render_var_metrics(None)

    assert len(dummy_ui.labels) == 1
    assert "VaR metrics not available" in dummy_ui.labels[0].text
    assert dummy_ui.plotly_calls == []


def test_render_var_metrics_renders_metrics_and_gauge(
    dummy_ui: DummyUI,
    validate_metrics_true: Callable[[dict[str, Any]], bool],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    risk_data = {"var_95": 0.03, "var_99": 0.05, "cvar_95": 0.07}

    captured: dict[str, Any] = {}

    def _capture_gauge(var_value: float, var_limit: float, warning_threshold: float) -> None:
        captured["args"] = (var_value, var_limit, warning_threshold)

    monkeypatch.setattr(var_chart_module, "render_var_gauge", _capture_gauge)

    var_chart_module.render_var_metrics(risk_data)

    label_texts = [label.text for label in dummy_ui.labels if label.text]
    assert "VaR 95% (Daily)" in label_texts
    assert "VaR 99% (Daily)" in label_texts
    assert "CVaR 95% (Expected Shortfall)" in label_texts
    assert "3.00%" in label_texts
    assert "5.00%" in label_texts
    assert "7.00%" in label_texts

    assert captured["args"][0] == pytest.approx(0.03)


def test_render_var_metrics_skips_gauge_when_var95_invalid(
    dummy_ui: DummyUI,
    validate_metrics_true: Callable[[dict[str, Any]], bool],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    risk_data = {"var_95": None, "var_99": 0.05, "cvar_95": 0.07}

    called = {"gauge": False}

    def _capture_gauge(_: float, __: float, ___: float) -> None:
        called["gauge"] = True

    monkeypatch.setattr(var_chart_module, "render_var_gauge", _capture_gauge)

    var_chart_module.render_var_metrics(risk_data)

    assert called["gauge"] is False
    assert any(label.text == "N/A" for label in dummy_ui.labels)


def test_render_var_gauge_color_thresholds(dummy_ui: DummyUI) -> None:
    var_chart_module.render_var_gauge(0.02, var_limit=0.05, warning_threshold=0.8)
    fig = dummy_ui.plotly_calls[-1].fig
    assert fig.data[0].gauge["bar"]["color"] == var_chart_module.COLOR_GREEN

    var_chart_module.render_var_gauge(0.045, var_limit=0.05, warning_threshold=0.8)
    fig = dummy_ui.plotly_calls[-1].fig
    assert fig.data[0].gauge["bar"]["color"] == var_chart_module.COLOR_ORANGE

    var_chart_module.render_var_gauge(0.06, var_limit=0.05, warning_threshold=0.8)
    fig = dummy_ui.plotly_calls[-1].fig
    assert fig.data[0].gauge["bar"]["color"] == var_chart_module.COLOR_RED


def test_render_var_history_sorts_and_renders_chart(
    dummy_ui: DummyUI,
    passthrough_history: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> None:
    history = [
        {"date": "2024-01-02", "var_95": 0.04},
        {"date": "bad-date", "var_95": 0.02},
        {"date": "2024-01-01", "var_95": 0.03},
        {"date": None, "var_95": 0.01},
        {"date": "2024-01-03", "var_95": None},
    ]

    var_chart_module.render_var_history(history, var_limit=0.0)

    assert len(dummy_ui.plotly_calls) == 1
    fig = dummy_ui.plotly_calls[0].fig

    # bad-date should sort first (datetime.min), then 2024-01-01, 2024-01-02
    assert list(fig.data[0].x) == ["bad-date", "2024-01-01", "2024-01-02"]
    assert list(fig.data[0].y) == pytest.approx([0.02, 0.03, 0.04])

    # Default limit applied when var_limit <= 0
    assert fig.layout.annotations[0].text == "Limit (5.0%)"


def test_render_var_history_no_valid_data_shows_message(
    dummy_ui: DummyUI,
    passthrough_history: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> None:
    history = [
        {"date": "2024-01-01", "var_95": None},
        {"date": "2024-01-02", "var_95": float("nan")},
    ]

    var_chart_module.render_var_history(history)

    assert len(dummy_ui.labels) == 1
    assert "No valid VaR history data" in dummy_ui.labels[0].text
    assert dummy_ui.plotly_calls == []
