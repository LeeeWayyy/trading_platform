"""Unit tests for strategy exposure table row construction."""

from __future__ import annotations

from typing import Any

import pytest

from apps.web_console_ng.components import strategy_exposure as strategy_exposure_module
from apps.web_console_ng.components.strategy_exposure import build_exposure_rows
from libs.web_console_services.schemas.exposure import StrategyExposureDTO, TotalExposureDTO


def test_build_exposure_rows_omits_synthetic_total_before_first_fetch() -> None:
    """Do not render a synthetic TOTAL row for all-zero initial placeholders."""
    total = TotalExposureDTO(
        long_total=0.0,
        short_total=0.0,
        gross_total=0.0,
        net_total=0.0,
        net_pct=0.0,
        strategy_count=0,
    )

    assert build_exposure_rows([], total) == []


def test_build_exposure_rows_includes_total_with_strategy_data() -> None:
    """Render per-strategy rows plus a TOTAL summary row when data exists."""
    total = TotalExposureDTO(
        long_total=1600.0,
        short_total=300.0,
        gross_total=1900.0,
        net_total=1300.0,
        net_pct=68.4,
        strategy_count=2,
    )
    rows = build_exposure_rows(
        [
            StrategyExposureDTO(
                strategy="alpha_a",
                long_notional=1000.0,
                short_notional=100.0,
                gross_notional=1100.0,
                net_notional=900.0,
                net_pct=47.4,
                position_count=3,
            ),
            StrategyExposureDTO(
                strategy="alpha_b",
                long_notional=600.0,
                short_notional=200.0,
                gross_notional=800.0,
                net_notional=400.0,
                net_pct=21.0,
                position_count=2,
            ),
        ],
        total,
    )

    assert len(rows) == 3
    assert rows[-1]["strategy"] == "TOTAL"
    assert rows[-1]["positions"] == 5
    assert rows[-1]["net"] == 1300.0


def test_build_exposure_rows_can_skip_total_row() -> None:
    """Allow callers to suppress TOTAL row for initial empty-state rendering."""
    total = TotalExposureDTO(
        long_total=1200.0,
        short_total=100.0,
        gross_total=1300.0,
        net_total=1100.0,
        net_pct=84.6,
        strategy_count=1,
    )
    rows = build_exposure_rows(
        [
            StrategyExposureDTO(
                strategy="alpha_a",
                long_notional=1200.0,
                short_notional=100.0,
                gross_notional=1300.0,
                net_notional=1100.0,
                net_pct=84.6,
                position_count=4,
            )
        ],
        total,
        include_total=False,
    )

    assert len(rows) == 1
    assert rows[0]["strategy"] == "alpha_a"


class _DummyGridElement:
    def classes(self, _value: str) -> _DummyGridElement:
        return self


class _DummyUI:
    def __init__(self) -> None:
        self.grid_options: dict[str, Any] | None = None

    def aggrid(self, options: dict[str, Any]) -> _DummyGridElement:
        self.grid_options = options
        return _DummyGridElement()


@pytest.mark.usefixtures("monkeypatch")
def test_render_exposure_grid_sets_position_width_and_fit_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dummy_ui = _DummyUI()
    monkeypatch.setattr(strategy_exposure_module, "ui", dummy_ui)

    total = TotalExposureDTO(
        long_total=1200.0,
        short_total=100.0,
        gross_total=1300.0,
        net_total=1100.0,
        net_pct=84.6,
        strategy_count=1,
    )

    strategy_exposure_module.render_exposure_grid(
        exposures=[
            StrategyExposureDTO(
                strategy="alpha_a",
                long_notional=1200.0,
                short_notional=100.0,
                gross_notional=1300.0,
                net_notional=1100.0,
                net_pct=84.6,
                position_count=4,
            )
        ],
        total=total,
    )

    assert dummy_ui.grid_options is not None
    position_col = next(
        col for col in dummy_ui.grid_options["columnDefs"] if col.get("field") == "positions"
    )
    assert position_col["minWidth"] >= 96
    assert "maxWidth" not in position_col
    assert "gridSizeChanged" in str(dummy_ui.grid_options[":onGridReady"])
    assert "__wcExposureFitBound" in str(dummy_ui.grid_options[":onGridReady"])
    assert "sizeColumnsToFit" in str(dummy_ui.grid_options[":onFirstDataRendered"])
