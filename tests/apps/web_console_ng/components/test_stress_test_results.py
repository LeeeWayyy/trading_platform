"""Tests for stress_test_results NiceGUI components."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from apps.web_console_ng.components import stress_test_results as stress_module


class DummyElement:
    """Minimal NiceGUI element mock with class chaining."""

    def __init__(self, text: str | None = None, fig: Any | None = None) -> None:
        self.text = text
        self.fig = fig
        self.class_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.props_calls: list[str] = []
        self.columns: list[dict[str, Any]] | None = None
        self.rows: list[dict[str, Any]] | None = None

    def classes(self, *args: Any, **kwargs: Any) -> DummyElement:
        self.class_calls.append((args, kwargs))
        return self

    def props(self, value: str) -> DummyElement:
        self.props_calls.append(value)
        return self


class DummyUI:
    """Capture NiceGUI calls for assertions."""

    def __init__(self) -> None:
        self.labels: list[DummyElement] = []
        self.tables: list[DummyElement] = []
        self.plotly_calls: list[DummyElement] = []
        self.separators: int = 0

    def label(self, text: str) -> DummyElement:
        element = DummyElement(text=text)
        self.labels.append(element)
        return element

    def table(self, columns: list[dict[str, Any]], rows: list[dict[str, Any]], row_key: str) -> DummyElement:
        element = DummyElement()
        element.columns = columns
        element.rows = rows
        self.tables.append(element)
        return element

    def plotly(self, fig: Any) -> DummyElement:
        element = DummyElement(fig=fig)
        self.plotly_calls.append(element)
        return element

    def separator(self) -> DummyElement:
        self.separators += 1
        return DummyElement()


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    dummy = DummyUI()
    monkeypatch.setattr(stress_module, "ui", dummy)
    return dummy


@pytest.fixture()
def passthrough_validate(monkeypatch: pytest.MonkeyPatch) -> Callable[[list[dict[str, Any]]], list[dict[str, Any]]]:
    def _passthrough(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return values

    monkeypatch.setattr(stress_module, "validate_stress_tests", _passthrough)
    return _passthrough


def test_render_stress_tests_no_data_shows_message(
    dummy_ui: DummyUI,
    passthrough_validate: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> None:
    stress_module.render_stress_tests(None)

    assert len(dummy_ui.labels) == 1
    assert "No stress test results available" in dummy_ui.labels[0].text
    assert dummy_ui.tables == []
    assert dummy_ui.plotly_calls == []


def test_render_scenario_table_orders_and_formats(
    dummy_ui: DummyUI,
) -> None:
    results = [
        {
            "scenario_name": "COVID_2020",
            "portfolio_pnl": 0.05,
            "scenario_type": "historical",
        },
        {
            "scenario_name": "GFC_2008",
            "portfolio_pnl": None,
            "scenario_type": "historical",
        },
        {
            "scenario_name": "CUSTOM_SCENARIO",
            "portfolio_pnl": -0.02,
            "scenario_type": None,
        },
    ]

    stress_module.render_scenario_table(results)

    # Warning for invalid P&L values
    assert any("missing or invalid P&L" in label.text for label in dummy_ui.labels)

    assert len(dummy_ui.tables) == 1
    table = dummy_ui.tables[0]
    assert table.rows is not None

    # Ordered scenarios: predefined order first
    scenario_names = [row["scenario"] for row in table.rows]
    assert scenario_names[0].startswith("Global Financial Crisis")
    assert scenario_names[1].startswith("COVID-19 Crash")
    assert scenario_names[2] == "CUSTOM_SCENARIO"

    # Formatting and scenario type normalization
    gfc_row = table.rows[0]
    covid_row = table.rows[1]
    custom_row = table.rows[2]

    assert gfc_row["pnl"] == "N/A"
    assert covid_row["pnl"] == "+5.00%"
    assert custom_row["pnl"] == "-2.00%"
    assert gfc_row["type"] == "Historical"
    assert custom_row["type"] == "Unknown"

    # data-testid prop for E2E
    assert any("data-testid=\"stress-results-table\"" in prop for prop in table.props_calls)


def test_render_factor_waterfall_invalid_format_shows_warning(dummy_ui: DummyUI) -> None:
    stress_module.render_factor_waterfall({"factor_impacts": [1, 2, 3]})

    assert len(dummy_ui.labels) == 1
    assert "Invalid factor contribution data format" in dummy_ui.labels[0].text
    assert dummy_ui.plotly_calls == []


def test_render_factor_waterfall_renders_chart(dummy_ui: DummyUI) -> None:
    scenario = {
        "scenario_name": "RATE_SHOCK",
        "factor_impacts": {
            "unknown_factor": -0.10,
            "momentum_12_1": 0.05,
        },
    }

    stress_module.render_factor_waterfall(scenario)

    assert len(dummy_ui.plotly_calls) == 1
    fig = dummy_ui.plotly_calls[0].fig

    # Ordered by absolute impact (unknown_factor first)
    x_labels = list(fig.data[0].x)
    assert x_labels[0] == "unknown_factor"
    assert x_labels[1] == "Momentum (12-1)"
    assert x_labels[-1] == "Total"

    y_values = list(fig.data[0].y)
    assert y_values[-1] == pytest.approx(sum(y_values[:-1]))


def test_render_stress_tests_selects_worst_case(
    dummy_ui: DummyUI,
    passthrough_validate: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = [
        {"scenario_name": "GFC_2008", "portfolio_pnl": -0.15, "factor_impacts": {}},
        {"scenario_name": "COVID_2020", "portfolio_pnl": "bad", "factor_impacts": {}},
        {"scenario_name": "RATE_SHOCK", "portfolio_pnl": 0.02, "factor_impacts": {}},
    ]

    captured: dict[str, Any] = {}

    def _capture_render_factor_waterfall(scenario: dict[str, Any]) -> None:
        captured["scenario"] = scenario

    monkeypatch.setattr(stress_module, "render_factor_waterfall", _capture_render_factor_waterfall)

    stress_module.render_stress_tests(results)

    assert captured["scenario"]["scenario_name"] == "GFC_2008"
    assert any("Factor Impact: Global Financial Crisis" in label.text for label in dummy_ui.labels)


def test_render_stress_tests_handles_no_valid_pnl(
    dummy_ui: DummyUI,
    passthrough_validate: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = [
        {"scenario_name": "GFC_2008", "portfolio_pnl": None, "factor_impacts": {}},
        {"scenario_name": "COVID_2020", "portfolio_pnl": "bad", "factor_impacts": {}},
    ]

    called = {"waterfall": False}

    def _capture_render_factor_waterfall(_: dict[str, Any]) -> None:
        called["waterfall"] = True

    monkeypatch.setattr(stress_module, "render_factor_waterfall", _capture_render_factor_waterfall)

    stress_module.render_stress_tests(results)

    assert called["waterfall"] is False
    assert any("Insufficient P&L data" in label.text for label in dummy_ui.labels)
