"""Unit tests for strategy exposure table row construction."""

from __future__ import annotations

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
