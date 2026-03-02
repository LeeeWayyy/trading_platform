"""Tests for universe_analytics NiceGUI components (P6T15/T15.2)."""

from __future__ import annotations

from typing import Any

import pytest

from apps.web_console_ng.components import universe_analytics as analytics_module
from libs.web_console_services.schemas.universe import (
    UniverseAnalyticsDTO,
    UniverseComparisonDTO,
)


class DummyElement:
    """Minimal NiceGUI element mock with class/props chaining."""

    def __init__(self, text: str | None = None, fig: Any | None = None) -> None:
        self.text = text
        self.fig = fig
        self.class_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def classes(self, *args: Any, **kwargs: Any) -> DummyElement:
        self.class_calls.append((args, kwargs))
        return self

    def props(self, _p: str) -> DummyElement:
        return self

    def __enter__(self) -> DummyElement:
        return self

    def __exit__(self, *a: Any) -> bool:
        return False


class DummyUI:
    """Capture NiceGUI calls for assertions."""

    def __init__(self) -> None:
        self.labels: list[DummyElement] = []
        self.plotly_calls: list[DummyElement] = []
        self.aggrid_calls: list[dict[str, Any]] = []
        self.card_calls: list[DummyElement] = []
        self.row_calls: list[DummyElement] = []
        self.column_calls: list[DummyElement] = []

    def label(self, text: str = "") -> DummyElement:
        el = DummyElement(text=text)
        self.labels.append(el)
        return el

    def plotly(self, fig: Any) -> DummyElement:
        el = DummyElement(fig=fig)
        self.plotly_calls.append(el)
        return el

    def aggrid(self, options: dict[str, Any]) -> DummyElement:
        self.aggrid_calls.append(options)
        return DummyElement()

    def card(self) -> DummyElement:
        el = DummyElement()
        self.card_calls.append(el)
        return el

    def row(self) -> DummyElement:
        el = DummyElement()
        self.row_calls.append(el)
        return el

    def column(self) -> DummyElement:
        el = DummyElement()
        self.column_calls.append(el)
        return el


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    dummy = DummyUI()
    monkeypatch.setattr(analytics_module, "ui", dummy)
    return dummy


def _make_analytics(**overrides: Any) -> UniverseAnalyticsDTO:
    defaults: dict[str, Any] = {
        "universe_id": "SP500",
        "symbol_count": 3,
        "avg_market_cap": 2_000_000.0,
        "median_adv": 5_000_000_000.0,
        "total_market_cap": 6_000_000.0,
        "market_cap_distribution": [1_000_000.0, 2_000_000.0, 3_000_000.0],
        "adv_distribution": [3e9, 4.5e9, 6e9],
        "sector_distribution": {"Information Technology": 0.28, "Financials": 0.13},
        "factor_exposure": {"Market": 1.0, "Size": -0.3},
        "is_sector_mock": True,
        "is_factor_mock": True,
    }
    defaults.update(overrides)
    return UniverseAnalyticsDTO(**defaults)


@pytest.mark.unit()
class TestRenderUniverseAnalytics:
    """Tests for the analytics render function."""

    def test_renders_without_error(self, dummy_ui: DummyUI) -> None:
        analytics = _make_analytics()
        analytics_module.render_universe_analytics(analytics)
        # Should produce 4 plotly charts: mcap hist, adv hist, pie, bar
        assert len(dummy_ui.plotly_calls) >= 4

    def test_renders_with_empty_distributions(self, dummy_ui: DummyUI) -> None:
        analytics = _make_analytics(
            market_cap_distribution=[],
            adv_distribution=[],
            sector_distribution={},
            factor_exposure={},
        )
        analytics_module.render_universe_analytics(analytics)
        # Should not raise; shows "no data" labels instead of charts
        no_data_labels = [
            el for el in dummy_ui.labels if "No" in (el.text or "") and "data" in (el.text or "")
        ]
        assert len(no_data_labels) >= 2

    def test_mock_data_badges_rendered(self, dummy_ui: DummyUI) -> None:
        analytics = _make_analytics()
        analytics_module.render_universe_analytics(analytics)
        mock_labels = [el for el in dummy_ui.labels if "Mock Data" in (el.text or "")]
        assert len(mock_labels) == 2  # sector + factor

    def test_no_mock_badges_when_flags_false(self, dummy_ui: DummyUI) -> None:
        analytics = _make_analytics(is_sector_mock=False, is_factor_mock=False)
        analytics_module.render_universe_analytics(analytics)
        mock_labels = [el for el in dummy_ui.labels if "Mock Data" in (el.text or "")]
        assert len(mock_labels) == 0

    def test_summary_cards_rendered(self, dummy_ui: DummyUI) -> None:
        analytics = _make_analytics()
        analytics_module.render_universe_analytics(analytics)
        # Should have summary labels: "Symbols", "Avg Market Cap", etc.
        label_texts = [el.text for el in dummy_ui.labels if el.text]
        assert "Symbols" in label_texts
        assert "3" in label_texts  # symbol count


@pytest.mark.unit()
class TestRenderUniverseComparison:
    """Tests for the comparison render function."""

    def test_comparison_renders_without_error(self, dummy_ui: DummyUI) -> None:
        comparison = UniverseComparisonDTO(
            universe_a_stats=_make_analytics(universe_id="SP500"),
            universe_b_stats=_make_analytics(universe_id="R1000"),
            overlap_count=3,
            overlap_pct=100.0,
        )
        analytics_module.render_universe_comparison(comparison)
        assert len(dummy_ui.aggrid_calls) == 1

    def test_comparison_table_has_overlap_row(self, dummy_ui: DummyUI) -> None:
        comparison = UniverseComparisonDTO(
            universe_a_stats=_make_analytics(universe_id="SP500"),
            universe_b_stats=_make_analytics(universe_id="R1000"),
            overlap_count=50,
            overlap_pct=50.0,
        )
        analytics_module.render_universe_comparison(comparison)
        grid_data = dummy_ui.aggrid_calls[0]
        rows = grid_data["rowData"]
        overlap_row = [r for r in rows if r["metric"] == "Overlap"]
        assert len(overlap_row) == 1
        assert "50 symbols" in overlap_row[0]["universe_a"]

    def test_comparison_table_has_five_rows(self, dummy_ui: DummyUI) -> None:
        comparison = UniverseComparisonDTO(
            universe_a_stats=_make_analytics(universe_id="SP500"),
            universe_b_stats=_make_analytics(universe_id="R1000"),
            overlap_count=3,
            overlap_pct=100.0,
        )
        analytics_module.render_universe_comparison(comparison)
        grid_data = dummy_ui.aggrid_calls[0]
        assert len(grid_data["rowData"]) == 5

    def test_comparison_headers_use_universe_ids(self, dummy_ui: DummyUI) -> None:
        comparison = UniverseComparisonDTO(
            universe_a_stats=_make_analytics(universe_id="SP500"),
            universe_b_stats=_make_analytics(universe_id="R1000"),
            overlap_count=3,
            overlap_pct=100.0,
        )
        analytics_module.render_universe_comparison(comparison)
        grid_data = dummy_ui.aggrid_calls[0]
        headers = [col["headerName"] for col in grid_data["columnDefs"]]
        assert "SP500" in headers
        assert "R1000" in headers
