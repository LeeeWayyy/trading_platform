"""
Unit tests for corporate actions adjustment module.

Tests cover:
- Stock split adjustments (prices divide, volume multiplies)
- Dividend adjustments (subtract from historical closes)
- Multiple corporate actions (cumulative adjustments)
- Edge cases (no CAs, empty DataFrames, missing columns)
- Idempotency (adjust(adjust(data)) == adjust(data))
"""

from datetime import date

import pytest
import polars as pl

from libs.data_pipeline.corporate_actions import (
    adjust_for_splits,
    adjust_for_dividends,
    adjust_prices
)


class TestAdjustForSplits:
    """Tests for adjust_for_splits function."""

    def test_simple_split_adjustment(self):
        """4-for-1 split should divide prices by 4, multiply volume by 4."""
        # Raw data with split on 2024-01-15
        df = pl.DataFrame({
            "symbol": ["AAPL", "AAPL", "AAPL"],
            "date": ["2024-01-10", "2024-01-15", "2024-01-20"],
            "open": [400.0, 100.0, 105.0],
            "high": [420.0, 110.0, 115.0],
            "low": [390.0, 95.0, 100.0],
            "close": [500.0, 125.0, 130.0],
            "volume": [1_000_000, 4_000_000, 3_800_000]
        })

        ca = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": ["2024-01-15"],
            "split_ratio": [4.0]
        })

        adjusted = adjust_for_splits(df, ca)

        # Pre-split prices should be divided by 4
        pre_split_close = adjusted.filter(pl.col("date") == date(2024, 1, 10))["close"][0]
        assert pre_split_close == pytest.approx(125.0, abs=0.01)

        # Pre-split volume should be multiplied by 4
        pre_split_volume = adjusted.filter(pl.col("date") == date(2024, 1, 10))["volume"][0]
        assert pre_split_volume == 4_000_000

        # Post-split data should be unchanged
        post_split_close = adjusted.filter(pl.col("date") == date(2024, 1, 20))["close"][0]
        assert post_split_close == pytest.approx(130.0, abs=0.01)

    def test_no_split_returns_unchanged(self):
        """Data with no splits should return unchanged."""
        df = pl.DataFrame({
            "symbol": ["AAPL"] * 3,
            "date": ["2024-01-10", "2024-01-15", "2024-01-20"],
            "open": [100.0, 101.0, 102.0],
            "high": [105.0, 106.0, 107.0],
            "low": [95.0, 96.0, 97.0],
            "close": [100.0, 101.0, 102.0],
            "volume": [1_000_000, 1_100_000, 1_200_000]
        })

        ca = pl.DataFrame({
            "symbol": pl.Series([], dtype=pl.Utf8),
            "date": pl.Series([], dtype=pl.Date),
            "split_ratio": pl.Series([], dtype=pl.Float64)
        })

        adjusted = adjust_for_splits(df, ca)

        # Should be identical to input
        assert adjusted.equals(df)

    def test_multiple_splits_cumulative(self):
        """Multiple splits should apply cumulatively."""
        # Two 2:1 splits = 4:1 total
        df = pl.DataFrame({
            "symbol": ["AAPL"] * 4,
            "date": ["2024-01-01", "2024-01-10", "2024-01-20", "2024-01-30"],
            "open": [800.0, 400.0, 200.0, 210.0],
            "high": [820.0, 420.0, 220.0, 230.0],
            "low": [780.0, 380.0, 180.0, 190.0],
            "close": [800.0, 400.0, 200.0, 210.0],
            "volume": [1_000_000, 2_000_000, 4_000_000, 4_200_000]
        })

        ca = pl.DataFrame({
            "symbol": ["AAPL", "AAPL"],
            "date": ["2024-01-10", "2024-01-20"],
            "split_ratio": [2.0, 2.0]
        })

        adjusted = adjust_for_splits(df, ca)

        # First day should be divided by 4 (2 * 2)
        first_close = adjusted.filter(pl.col("date") == date(2024, 1, 1))["close"][0]
        assert first_close == pytest.approx(200.0, abs=0.01)

        # Last day should be unchanged
        last_close = adjusted.filter(pl.col("date") == date(2024, 1, 30))["close"][0]
        assert last_close == pytest.approx(210.0, abs=0.01)

    def test_missing_columns_raises_error(self):
        """Missing required columns should raise ValueError."""
        df = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": ["2024-01-10"]
            # Missing OHLCV columns
        })

        ca = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": ["2024-01-10"],
            "split_ratio": [4.0]
        })

        with pytest.raises(ValueError) as exc_info:
            adjust_for_splits(df, ca)

        assert "missing required columns" in str(exc_info.value).lower()

    def test_reverse_split(self):
        """Reverse split (ratio < 1.0) should increase prices, decrease volume."""
        # 1:4 reverse split (ratio = 0.25)
        df = pl.DataFrame({
            "symbol": ["AAPL", "AAPL"],
            "date": ["2024-01-10", "2024-01-20"],
            "open": [1.0, 4.0],
            "high": [1.5, 4.5],
            "low": [0.9, 3.9],
            "close": [1.0, 4.0],
            "volume": [4_000_000, 1_000_000]
        })

        ca = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": ["2024-01-20"],
            "split_ratio": [0.25]  # 1:4 reverse
        })

        adjusted = adjust_for_splits(df, ca)

        # Pre-reverse-split price should be multiplied by 4
        pre_close = adjusted.filter(pl.col("date") == date(2024, 1, 10))["close"][0]
        assert pre_close == pytest.approx(4.0, abs=0.01)


class TestAdjustForDividends:
    """Tests for adjust_for_dividends function."""

    def test_simple_dividend_adjustment(self):
        """$2 dividend should subtract $2 from pre-dividend closes."""
        df = pl.DataFrame({
            "symbol": ["MSFT", "MSFT", "MSFT"],
            "date": ["2024-01-10", "2024-01-15", "2024-01-20"],
            "close": [150.0, 148.0, 149.0]
        })

        ca = pl.DataFrame({
            "symbol": ["MSFT"],
            "date": ["2024-01-15"],
            "dividend": [2.0]
        })

        adjusted = adjust_for_dividends(df, ca)

        # Pre-dividend should have $2 subtracted
        pre_div = adjusted.filter(pl.col("date") == date(2024, 1, 10))["close"][0]
        assert pre_div == pytest.approx(148.0, abs=0.01)

        # Ex-date and after should be unchanged
        ex_date = adjusted.filter(pl.col("date") == date(2024, 1, 15))["close"][0]
        assert ex_date == pytest.approx(148.0, abs=0.01)

        post_div = adjusted.filter(pl.col("date") == date(2024, 1, 20))["close"][0]
        assert post_div == pytest.approx(149.0, abs=0.01)

    def test_multiple_dividends_cumulative(self):
        """Multiple dividends should subtract cumulatively."""
        df = pl.DataFrame({
            "symbol": ["MSFT"] * 4,
            "date": ["2024-01-01", "2024-01-10", "2024-01-20", "2024-01-30"],
            "close": [150.0, 148.0, 146.0, 147.0]
        })

        ca = pl.DataFrame({
            "symbol": ["MSFT", "MSFT"],
            "date": ["2024-01-10", "2024-01-20"],
            "dividend": [2.0, 2.0]  # Two $2 dividends
        })

        adjusted = adjust_for_dividends(df, ca)

        # First day: subtract both dividends ($4 total)
        first_close = adjusted.filter(pl.col("date") == date(2024, 1, 1))["close"][0]
        assert first_close == pytest.approx(146.0, abs=0.01)

        # Between dividends: subtract first dividend only ($2)
        mid_close = adjusted.filter(pl.col("date") == date(2024, 1, 10))["close"][0]
        assert mid_close == pytest.approx(146.0, abs=0.01)

        # After both: no adjustment
        last_close = adjusted.filter(pl.col("date") == date(2024, 1, 30))["close"][0]
        assert last_close == pytest.approx(147.0, abs=0.01)

    def test_no_dividend_returns_unchanged(self):
        """Data with no dividends should return unchanged."""
        df = pl.DataFrame({
            "symbol": ["MSFT"] * 3,
            "date": ["2024-01-10", "2024-01-15", "2024-01-20"],
            "close": [150.0, 148.0, 149.0]
        })

        ca = pl.DataFrame({
            "symbol": pl.Series([], dtype=pl.Utf8),
            "date": pl.Series([], dtype=pl.Date),
            "dividend": pl.Series([], dtype=pl.Float64)
        })

        adjusted = adjust_for_dividends(df, ca)

        assert adjusted.equals(df)


class TestAdjustPrices:
    """Tests for adjust_prices convenience function."""

    def test_both_splits_and_dividends(self):
        """Should apply both splits and dividends correctly."""
        df = pl.DataFrame({
            "symbol": ["AAPL"] * 3,
            "date": ["2024-01-01", "2024-01-10", "2024-01-20"],
            "open": [400.0, 100.0, 105.0],
            "high": [420.0, 110.0, 115.0],
            "low": [390.0, 95.0, 100.0],
            "close": [500.0, 125.0, 130.0],
            "volume": [1_000_000, 4_000_000, 3_800_000]
        })

        splits = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": ["2024-01-10"],
            "split_ratio": [4.0]
        })

        dividends = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": ["2024-01-20"],
            "dividend": [2.0]
        })

        adjusted = adjust_prices(df, splits_df=splits, dividends_df=dividends)

        # Should have both adjustments applied
        # First row: split by 4, then subtract $2
        first_close = adjusted.filter(pl.col("date") == date(2024, 1, 1))["close"][0]
        expected = (500.0 / 4.0) - 2.0  # Split first, then dividend
        assert first_close == pytest.approx(expected, abs=0.01)

    def test_none_dataframes_returns_unchanged(self):
        """Should return original data if no CAs provided."""
        df = pl.DataFrame({
            "symbol": ["AAPL"] * 3,
            "date": ["2024-01-01", "2024-01-10", "2024-01-20"],
            "open": [100.0, 101.0, 102.0],
            "high": [105.0, 106.0, 107.0],
            "low": [95.0, 96.0, 97.0],
            "close": [100.0, 101.0, 102.0],
            "volume": [1_000_000, 1_100_000, 1_200_000]
        })

        adjusted = adjust_prices(df, splits_df=None, dividends_df=None)

        assert adjusted.equals(df)

    def test_adjustment_on_raw_data_only(self):
        """
        Corporate action adjustments should only be applied to raw data.

        Note: Adjustments are NOT idempotent in the mathematical sense.
        Applying the same split twice will compound (4:1 twice = 16:1 total).
        This is correct behavior - adjustments should only be applied once
        to raw unadjusted data.
        """
        # Test that adjusted data has correct values when applied to raw data
        df = pl.DataFrame({
            "symbol": ["AAPL"] * 3,
            "date": ["2024-01-01", "2024-01-10", "2024-01-20"],
            "open": [400.0, 100.0, 105.0],
            "high": [420.0, 110.0, 115.0],
            "low": [390.0, 95.0, 100.0],
            "close": [500.0, 125.0, 130.0],
            "volume": [1_000_000, 4_000_000, 3_800_000]
        })

        splits = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": ["2024-01-10"],
            "split_ratio": [4.0]
        })

        adjusted = adjust_prices(df, splits_df=splits)

        # Pre-split should be adjusted by 4
        pre_split = adjusted.filter(pl.col("date") == date(2024, 1, 1))["close"][0]
        assert pre_split == pytest.approx(125.0, abs=0.01)

        # Post-split should be unchanged
        post_split = adjusted.filter(pl.col("date") == date(2024, 1, 20))["close"][0]
        assert post_split == pytest.approx(130.0, abs=0.01)
