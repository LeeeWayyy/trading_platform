"""Tests for data quality gate and outlier detection."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from libs.core.common.exceptions import OutlierError
from libs.data.data_pipeline.quality_gate import check_quality, detect_outliers


def _price_df(prices: list[float]) -> pl.DataFrame:
    dates = [date(2026, 1, 10 + idx) for idx in range(len(prices))]
    return pl.DataFrame(
        {
            "symbol": ["AAPL"] * len(prices),
            "date": dates,
            "close": prices,
        }
    )


def test_detect_outliers_missing_columns() -> None:
    df = pl.DataFrame({"symbol": ["AAPL"], "date": [date(2026, 1, 10)]})
    with pytest.raises(ValueError, match="missing required columns"):
        detect_outliers(df)


def test_detect_outliers_empty_df_returns_empty_quarantine() -> None:
    df = pl.DataFrame({"symbol": [], "date": [], "close": []})
    good, bad = detect_outliers(df)
    assert good.is_empty()
    assert bad.is_empty()


def test_detect_outliers_flags_large_move_without_ca() -> None:
    df = _price_df([100.0, 160.0, 162.0])
    good, bad = detect_outliers(df, threshold=0.30)

    assert good.height == 2
    assert bad.height == 1
    assert bad["symbol"][0] == "AAPL"
    assert bad["reason"][0].startswith("outlier_daily_return_")


def test_detect_outliers_ignores_large_move_with_ca() -> None:
    df = _price_df([100.0, 160.0, 162.0])
    ca_df = pl.DataFrame({"symbol": ["AAPL"], "date": [date(2026, 1, 11)]})

    good, bad = detect_outliers(df, ca_df=ca_df, threshold=0.30)

    assert good.height == 3
    assert bad.is_empty()


def test_detect_outliers_first_row_never_flagged() -> None:
    df = _price_df([100.0, 200.0])
    good, bad = detect_outliers(df, threshold=0.30)

    assert good.height == 1
    assert bad.height == 1
    assert bad["date"][0] == date(2026, 1, 11)


def test_check_quality_filters_outliers() -> None:
    df = _price_df([100.0, 160.0, 162.0])
    clean = check_quality(df, threshold=0.30, raise_on_outliers=False)

    assert clean.height == 2


def test_check_quality_raises_when_configured() -> None:
    df = _price_df([100.0, 160.0, 162.0])
    with pytest.raises(OutlierError) as exc:
        check_quality(df, threshold=0.30, raise_on_outliers=True)

    assert "Detected" in str(exc.value)
    assert "Threshold" in str(exc.value)
