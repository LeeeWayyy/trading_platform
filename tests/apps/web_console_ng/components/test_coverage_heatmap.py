"""Tests for coverage_heatmap NiceGUI component (P6T13)."""

from __future__ import annotations

import datetime
from typing import Any

import pytest

from apps.web_console_ng.components import coverage_heatmap as heatmap_module
from libs.data.data_quality.coverage_analyzer import (
    CoverageGap,
    CoverageMatrix,
    CoverageStatus,
    CoverageSummary,
)

# ============================================================================
# Mocks
# ============================================================================


class DummyElement:
    def __init__(self, text: str = "", value: Any = None) -> None:
        self.text = text
        self.value = value
        self._classes: list[str] = []
        self.on_click_cb: Any = None

    def classes(self, c: str) -> DummyElement:
        self._classes.append(c)
        return self

    def props(self, _p: str) -> DummyElement:
        return self

    def __enter__(self) -> DummyElement:
        return self

    def __exit__(self, *a: Any) -> bool:
        return False


class DummyUI:
    def __init__(self) -> None:
        self.labels: list[DummyElement] = []
        self.selects: list[DummyElement] = []
        self.inputs: list[DummyElement] = []
        self.toggles: list[DummyElement] = []
        self.buttons: list[DummyElement] = []
        self.tables: list[Any] = []
        self.cards: list[DummyElement] = []
        self.rows: list[DummyElement] = []
        self.columns: list[DummyElement] = []
        self.plotlies: list[Any] = []
        self.downloads: list[tuple[bytes, str]] = []

    def label(self, text: str = "") -> DummyElement:
        el = DummyElement(text=text)
        self.labels.append(el)
        return el

    def select(self, *, label: str = "", options: Any = None, multiple: bool = False, value: Any = None) -> DummyElement:
        el = DummyElement(value=value)
        self.selects.append(el)
        return el

    def input(self, *, label: str = "", value: str = "") -> DummyElement:
        el = DummyElement(value=value)
        self.inputs.append(el)
        return el

    def toggle(self, options: Any, value: Any = None) -> DummyElement:
        el = DummyElement(value=value)
        self.toggles.append(el)
        return el

    def button(self, text: str = "", **kwargs: Any) -> DummyElement:
        el = DummyElement(text=text)
        if "on_click" in kwargs:
            el.on_click_cb = kwargs["on_click"]
        self.buttons.append(el)
        return el

    def table(self, *, columns: Any = None, rows: Any = None) -> DummyElement:
        self.tables.append({"columns": columns, "rows": rows})
        return DummyElement()

    def card(self) -> DummyElement:
        el = DummyElement()
        self.cards.append(el)
        return el

    def row(self) -> DummyElement:
        el = DummyElement()
        self.rows.append(el)
        return el

    def column(self) -> DummyElement:
        el = DummyElement()
        self.columns.append(el)
        return el

    def plotly(self, fig: Any) -> DummyElement:
        self.plotlies.append(fig)
        return DummyElement()

    def download(self, data: bytes, filename: str) -> None:
        self.downloads.append((data, filename))


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    ui = DummyUI()
    monkeypatch.setattr(heatmap_module, "ui", ui)
    return ui


# ============================================================================
# Fixtures
# ============================================================================


def _make_summary(
    pct: float = 90.0,
    expected: int = 100,
    present: int = 90,
    missing: int = 10,
    suspicious: int = 0,
    gaps: list[CoverageGap] | None = None,
) -> CoverageSummary:
    return CoverageSummary(
        total_expected=expected,
        total_present=present,
        total_missing=missing,
        total_suspicious=suspicious,
        coverage_pct=pct,
        gaps=gaps or [],
    )


def _make_matrix(
    *,
    symbols: list[str] | None = None,
    dates: list[datetime.date] | None = None,
    pct: float = 90.0,
    gaps: list[CoverageGap] | None = None,
    notices: list[str] | None = None,
    skipped: int = 0,
) -> CoverageMatrix:
    syms = symbols if symbols is not None else ["AAPL"]
    dts = dates if dates is not None else [datetime.date(2024, 1, 15)]
    matrix = [
        [CoverageStatus.COMPLETE for _ in dts]
        for _ in syms
    ]
    return CoverageMatrix(
        symbols=syms,
        dates=dts,
        matrix=matrix,
        summary=_make_summary(pct=pct, gaps=gaps or []),
        truncated=False,
        total_symbol_count=len(syms),
        effective_resolution="daily",
        notices=notices or [],
        skipped_file_count=skipped,
    )


# ============================================================================
# render_coverage_controls
# ============================================================================


class TestRenderCoverageControls:
    def test_no_tickers_shows_message(self, dummy_ui: DummyUI) -> None:
        heatmap_module.render_coverage_controls(
            available_tickers=[],
            on_analyze=lambda *a: None,
        )
        assert any("No adjusted data" in el.text for el in dummy_ui.labels)
        assert dummy_ui.buttons == []

    def test_creates_form_elements(self, dummy_ui: DummyUI) -> None:
        heatmap_module.render_coverage_controls(
            available_tickers=["AAPL", "MSFT"],
            on_analyze=lambda *a: None,
        )
        assert len(dummy_ui.selects) == 1  # symbol multi-select
        assert len(dummy_ui.inputs) == 2  # start + end date
        assert len(dummy_ui.toggles) == 1  # resolution toggle
        assert len(dummy_ui.buttons) == 1  # analyze button


# ============================================================================
# render_coverage_heatmap
# ============================================================================


class TestRenderCoverageHeatmap:
    def test_high_coverage_green(self, dummy_ui: DummyUI) -> None:
        heatmap_module.render_coverage_heatmap(_make_matrix(pct=96.0))
        texts = [el.text for el in dummy_ui.labels]
        assert any("96.0%" in t for t in texts)

    def test_medium_coverage_amber(self, dummy_ui: DummyUI) -> None:
        heatmap_module.render_coverage_heatmap(_make_matrix(pct=85.0))
        texts = [el.text for el in dummy_ui.labels]
        assert any("85.0%" in t for t in texts)

    def test_low_coverage_red(self, dummy_ui: DummyUI) -> None:
        heatmap_module.render_coverage_heatmap(_make_matrix(pct=50.0))
        texts = [el.text for el in dummy_ui.labels]
        assert any("50.0%" in t for t in texts)

    def test_heatmap_chart_created(self, dummy_ui: DummyUI) -> None:
        heatmap_module.render_coverage_heatmap(_make_matrix())
        assert len(dummy_ui.plotlies) == 1

    def test_empty_matrix_no_chart(self, dummy_ui: DummyUI) -> None:
        heatmap_module.render_coverage_heatmap(
            _make_matrix(symbols=[], dates=[])
        )
        assert dummy_ui.plotlies == []
        assert any("No data to display" in el.text for el in dummy_ui.labels)

    def test_notices_displayed(self, dummy_ui: DummyUI) -> None:
        heatmap_module.render_coverage_heatmap(
            _make_matrix(notices=["Truncated to 200 symbols"])
        )
        assert any("Truncated" in el.text for el in dummy_ui.labels)

    def test_skipped_files_warning(self, dummy_ui: DummyUI) -> None:
        heatmap_module.render_coverage_heatmap(_make_matrix(skipped=3))
        assert any("3 file(s)" in el.text for el in dummy_ui.labels)

    def test_gaps_table_shown(self, dummy_ui: DummyUI) -> None:
        gap = CoverageGap(
            symbol="AAPL",
            start_date=datetime.date(2024, 1, 10),
            end_date=datetime.date(2024, 1, 12),
            gap_days=3,
        )
        heatmap_module.render_coverage_heatmap(
            _make_matrix(gaps=[gap])
        )
        assert len(dummy_ui.tables) == 1

    def test_no_gaps_no_table(self, dummy_ui: DummyUI) -> None:
        heatmap_module.render_coverage_heatmap(_make_matrix())
        assert dummy_ui.tables == []


# ============================================================================
# render_coverage_export
# ============================================================================


class TestRenderCoverageExport:
    def test_creates_toggle_and_button(self, dummy_ui: DummyUI) -> None:
        heatmap_module.render_coverage_export(
            _make_matrix(),
            analyzer=None,  # type: ignore[arg-type]
        )
        assert len(dummy_ui.toggles) == 1
        assert len(dummy_ui.buttons) == 1


__all__: list[str] = []
