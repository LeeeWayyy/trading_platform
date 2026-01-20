"""Focused tests for corporate action adjustments in data pipeline."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from libs.data.data_pipeline.corporate_actions import (
    adjust_for_dividends,
    adjust_for_splits,
    adjust_prices,
)


def test_adjust_for_splits_handles_multiple_symbols_independently() -> None:
    df = pl.DataFrame(
        {
            "symbol": ["MSFT", "AAPL", "AAPL", "MSFT"],
            "date": ["2024-01-10", "2024-01-10", "2024-01-15", "2024-01-15"],
            "open": [300.0, 400.0, 100.0, 302.0],
            "high": [305.0, 420.0, 110.0, 307.0],
            "low": [295.0, 390.0, 95.0, 297.0],
            "close": [300.0, 500.0, 125.0, 301.0],
            "volume": [2_000_000, 1_000_000, 4_000_000, 2_100_000],
        }
    )

    ca = pl.DataFrame(
        {
            "symbol": ["AAPL"],
            "date": ["2024-01-15"],
            "split_ratio": [4.0],
        }
    )

    adjusted = adjust_for_splits(df, ca)

    aapl_pre = adjusted.filter(
        (pl.col("symbol") == "AAPL") & (pl.col("date") == date(2024, 1, 10))
    )["close"][0]
    assert aapl_pre == pytest.approx(125.0, abs=0.01)

    msft_pre = adjusted.filter(
        (pl.col("symbol") == "MSFT") & (pl.col("date") == date(2024, 1, 10))
    )["close"][0]
    assert msft_pre == pytest.approx(300.0, abs=0.01)


def test_adjust_for_splits_does_not_change_split_date() -> None:
    df = pl.DataFrame(
        {
            "symbol": ["AAPL", "AAPL"],
            "date": ["2024-01-10", "2024-01-15"],
            "open": [400.0, 100.0],
            "high": [420.0, 110.0],
            "low": [390.0, 95.0],
            "close": [500.0, 125.0],
            "volume": [1_000_000, 4_000_000],
        }
    )

    ca = pl.DataFrame({"symbol": ["AAPL"], "date": ["2024-01-15"], "split_ratio": [4.0]})

    adjusted = adjust_for_splits(df, ca)

    split_close = adjusted.filter(pl.col("date") == date(2024, 1, 15))["close"][0]
    assert split_close == pytest.approx(125.0, abs=0.01)


def test_adjust_for_dividends_handles_multiple_symbols() -> None:
    df = pl.DataFrame(
        {
            "symbol": ["AAPL", "MSFT", "AAPL", "MSFT"],
            "date": ["2024-01-10", "2024-01-10", "2024-01-15", "2024-01-15"],
            "close": [150.0, 200.0, 151.0, 198.0],
        }
    )

    ca = pl.DataFrame({"symbol": ["MSFT"], "date": ["2024-01-15"], "dividend": [2.0]})

    adjusted = adjust_for_dividends(df, ca)

    msft_pre = adjusted.filter(
        (pl.col("symbol") == "MSFT") & (pl.col("date") == date(2024, 1, 10))
    )["close"][0]
    assert msft_pre == pytest.approx(198.0, abs=0.01)

    aapl_pre = adjusted.filter(
        (pl.col("symbol") == "AAPL") & (pl.col("date") == date(2024, 1, 10))
    )["close"][0]
    assert aapl_pre == pytest.approx(150.0, abs=0.01)


def test_adjust_prices_raises_on_missing_dividend_column() -> None:
    df = pl.DataFrame(
        {
            "symbol": ["AAPL"],
            "date": ["2024-01-10"],
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.0],
            "volume": [1_000_000],
        }
    )

    bad_dividends = pl.DataFrame({"symbol": ["AAPL"], "date": ["2024-01-10"]})

    with pytest.raises(ValueError, match="Corporate actions DataFrame missing columns"):
        adjust_prices(df, dividends_df=bad_dividends)
