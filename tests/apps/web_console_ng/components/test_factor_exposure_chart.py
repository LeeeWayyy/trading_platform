"""Tests for factor_exposure_chart NiceGUI components."""

from __future__ import annotations

from typing import Any, Callable

import math
import pytest

from apps.web_console_ng.components import factor_exposure_chart as factor_chart_module


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
    monkeypatch.setattr(factor_chart_module, "ui", dummy)
    return dummy


@pytest.fixture()
def passthrough_validate(monkeypatch: pytest.MonkeyPatch) -> Callable[[list[dict[str, Any]]], list[dict[str, Any]]]:
    def _passthrough(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return values

    monkeypatch.setattr(factor_chart_module, "validate_exposures", _passthrough)
    return _passthrough


def test_render_factor_exposure_no_data_shows_message(
    dummy_ui: DummyUI,
    passthrough_validate: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> None:
    factor_chart_module.render_factor_exposure(None)

    assert len(dummy_ui.labels) == 1
    assert "No factor exposure data available" in dummy_ui.labels[0].text
    assert dummy_ui.plotly_calls == []


def test_render_factor_exposure_orders_colors_and_warns_missing(
    dummy_ui: DummyUI,
    passthrough_validate: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> None:
    exposures = [
        {"factor_name": "log_market_cap", "exposure": 0.10},
        {"factor_name": "book_to_market", "exposure": -0.02},
        {"factor_name": "custom_factor", "exposure": 0.03},
    ]

    factor_chart_module.render_factor_exposure(exposures)

    assert len(dummy_ui.plotly_calls) == 1
    fig = dummy_ui.plotly_calls[0].fig

    # Expected order: follow module ordering (canonical + default), then extras, then reversed
    exposure_map = {
        "log_market_cap": 0.10,
        "book_to_market": -0.02,
        "custom_factor": 0.03,
    }
    expected_pairs: list[tuple[str, float]] = []
    for factor_name in factor_chart_module._chart_factor_order:
        if factor_name in exposure_map:
            expected_pairs.append(
                (factor_chart_module._get_display_name(factor_name), exposure_map[factor_name])
            )
    # Append extra factor not in canonical list
    if "custom_factor" not in factor_chart_module._chart_factor_order:
        expected_pairs.append(("custom_factor", exposure_map["custom_factor"]))

    expected_pairs = list(reversed(expected_pairs))

    expected_factors = [pair[0] for pair in expected_pairs]
    expected_values = [pair[1] for pair in expected_pairs]
    expected_colors = [
        factor_chart_module._get_exposure_color(value) for value in expected_values
    ]

    assert list(fig.data[0].y) == expected_factors
    assert list(fig.data[0].x) == pytest.approx(expected_values)

    # Colors reflect exposure sign
    assert list(fig.data[0].marker.color) == expected_colors

    # Text labels format as percentage with sign
    assert list(fig.data[0].text) == [f"{v:+.2%}" for v in expected_values]

    # Warning for missing canonical factors
    warning_labels = [label.text for label in dummy_ui.labels if label.text]
    assert any("Data unavailable for" in text for text in warning_labels)


def test_render_factor_exposure_all_invalid_values_shows_message(
    dummy_ui: DummyUI,
    passthrough_validate: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> None:
    exposures = [
        {"factor_name": "roe", "exposure": float("nan")},
        {"factor_name": "momentum_12_1", "exposure": "bad"},
    ]

    factor_chart_module.render_factor_exposure(exposures)

    assert len(dummy_ui.labels) == 1
    assert "No valid factor exposure data" in dummy_ui.labels[0].text
    assert dummy_ui.plotly_calls == []


def test_get_exposure_color_handles_positive_and_negative() -> None:
    assert factor_chart_module._get_exposure_color(0.01) == factor_chart_module.COLOR_GREEN
    assert factor_chart_module._get_exposure_color(-0.01) == factor_chart_module.COLOR_RED
    assert factor_chart_module._get_exposure_color(0.0) == factor_chart_module.COLOR_GREEN


def test_get_display_name_fallback() -> None:
    assert factor_chart_module._get_display_name("unknown_factor") == "unknown_factor"
    assert (
        factor_chart_module._get_display_name("momentum_12_1")
        == factor_chart_module.FACTOR_DISPLAY_NAMES["momentum_12_1"]
    )


def test_render_factor_exposure_skips_non_finite_values(
    dummy_ui: DummyUI,
    passthrough_validate: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> None:
    exposures = [
        {"factor_name": "log_market_cap", "exposure": math.inf},
        {"factor_name": "book_to_market", "exposure": -0.01},
    ]

    factor_chart_module.render_factor_exposure(exposures)

    assert len(dummy_ui.plotly_calls) == 1
    fig = dummy_ui.plotly_calls[0].fig
    assert list(fig.data[0].x) == pytest.approx([-0.01])
