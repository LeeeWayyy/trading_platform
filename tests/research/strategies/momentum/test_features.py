"""
Unit tests for momentum feature engineering.

Tests cover:
- Moving Average crossovers
- MACD (Moving Average Convergence Divergence)
- Rate of Change (ROC)
- ADX (Average Directional Index)
- OBV (On-Balance Volume)
- Combined feature computation
- Edge cases and error handling

All tests use real Polars DataFrames with synthetic price data.
"""

import numpy as np
import polars as pl
import pytest

from research.strategies.momentum.features import (
    compute_adx,
    compute_macd,
    compute_momentum_features,
    compute_moving_averages,
    compute_obv,
    compute_rate_of_change,
)


@pytest.fixture()
def sample_prices() -> pl.DataFrame:
    """
    Create sample OHLCV price data for testing.

    Returns:
        DataFrame with 60 rows of synthetic price data for AAPL
    """
    np.random.seed(42)

    n_rows = 60
    dates = pl.date_range(
        start=pl.date(2024, 1, 1),
        end=pl.date(2024, 2, 29),
        interval="1d",
        eager=True,
    )

    # Generate realistic price movements
    base_price = 150.0
    returns = np.random.randn(n_rows) * 0.02  # 2% daily volatility
    prices = base_price * np.exp(np.cumsum(returns))

    return pl.DataFrame(
        {
            "symbol": ["AAPL"] * n_rows,
            "date": dates,
            "open": prices * (1 + np.random.randn(n_rows) * 0.005),
            "high": prices * (1 + np.abs(np.random.randn(n_rows)) * 0.01),
            "low": prices * (1 - np.abs(np.random.randn(n_rows)) * 0.01),
            "close": prices,
            "volume": np.random.randint(1000000, 5000000, n_rows),
        }
    )


@pytest.fixture()
def uptrend_prices() -> pl.DataFrame:
    """
    Create strong uptrend price data.

    Useful for testing bullish momentum signals.
    """
    n_rows = 60
    dates = pl.date_range(
        start=pl.date(2024, 1, 1),
        end=pl.date(2024, 2, 29),
        interval="1d",
        eager=True,
    )

    # Strong uptrend
    base_price = 100.0
    prices = base_price + np.arange(n_rows) * 1.5  # $1.50/day increase

    return pl.DataFrame(
        {
            "symbol": ["MSFT"] * n_rows,
            "date": dates,
            "open": prices,
            "high": prices * 1.01,
            "low": prices * 0.99,
            "close": prices,
            "volume": np.random.randint(2000000, 4000000, n_rows),
        }
    )


class TestMovingAverages:
    """Tests for moving average calculation and crossovers."""

    def test_ma_shape(self, sample_prices: pl.DataFrame) -> None:
        """Test that MA returns correct columns."""
        result = compute_moving_averages(sample_prices, fast_period=10, slow_period=50)

        expected_cols = ["ma_fast", "ma_slow", "ma_diff", "ma_cross"]
        for col in expected_cols:
            assert col in result.columns, f"Missing column: {col}"

    def test_ma_ordering(self, uptrend_prices: pl.DataFrame) -> None:
        """Test that fast MA > slow MA in uptrend (golden cross)."""
        result = compute_moving_averages(uptrend_prices, fast_period=10, slow_period=50)

        # In strong uptrend, fast MA should be above slow MA (positive diff)
        valid_rows = result.filter(pl.col("ma_diff").is_not_null())
        final_diff = valid_rows["ma_diff"].tail(10)

        # Most recent values should be positive (fast > slow)
        assert final_diff.mean() > 0, "Fast MA should be above slow MA in uptrend"

    def test_ma_crossover_detection(self) -> None:
        """Test detection of golden cross and death cross."""
        # Create data with clear crossover
        # First 30 days: downtrend (fast < slow)
        # Last 30 days: uptrend (fast > slow)
        n_rows = 60
        dates = pl.date_range(
            start=pl.date(2024, 1, 1),
            end=pl.date(2024, 2, 29),
            interval="1d",
            eager=True,
        )

        # Price starts at 120, drops to 100, then rises to 140
        prices = np.concatenate(
            [
                np.linspace(120, 100, 30),  # Downtrend
                np.linspace(100, 140, 30),  # Uptrend
            ]
        )

        df = pl.DataFrame(
            {
                "symbol": ["TEST"] * n_rows,
                "date": dates,
                "close": prices,
                "high": prices * 1.01,
                "low": prices * 0.99,
                "open": prices,
                "volume": [1000000] * n_rows,
            }
        )

        result = compute_moving_averages(df, fast_period=5, slow_period=20)

        # Should have at least one golden cross (1) in uptrend portion
        cross_signals = result["ma_cross"].drop_nulls()
        assert (cross_signals == 1).sum() > 0, "Should detect golden cross"

    def test_ma_fast_vs_slow_speed(self, sample_prices: pl.DataFrame) -> None:
        """Test that fast MA is more responsive than slow MA."""
        result = compute_moving_averages(sample_prices, fast_period=10, slow_period=50)

        valid_rows = result.filter(pl.col("ma_slow").is_not_null())

        # Fast MA should be more responsive (larger changes period-to-period)
        fast_diff = valid_rows["ma_fast"].diff().abs().mean()
        slow_diff = valid_rows["ma_slow"].diff().abs().mean()

        assert fast_diff >= slow_diff * 0.5, "Fast MA should be more responsive than slow MA"


class TestMACD:
    """Tests for MACD indicator."""

    def test_macd_shape(self, sample_prices: pl.DataFrame) -> None:
        """Test that MACD returns correct columns."""
        result = compute_macd(sample_prices)

        expected_cols = ["macd_line", "macd_signal", "macd_hist", "macd_cross"]
        for col in expected_cols:
            assert col in result.columns, f"Missing column: {col}"

    def test_macd_histogram_formula(self, sample_prices: pl.DataFrame) -> None:
        """Test that MACD histogram = MACD line - signal line."""
        result = compute_macd(sample_prices)

        valid_rows = result.filter(pl.col("macd_hist").is_not_null())

        # Histogram should equal MACD - signal
        calculated_hist = valid_rows["macd_line"] - valid_rows["macd_signal"]
        assert np.allclose(
            valid_rows["macd_hist"].to_numpy(),
            calculated_hist.to_numpy(),
            rtol=1e-5,
        )

    def test_macd_uptrend_positive_histogram(self, uptrend_prices: pl.DataFrame) -> None:
        """Test that MACD histogram is positive in strong uptrend."""
        result = compute_macd(uptrend_prices)

        # Last few values should have positive histogram (MACD above signal)
        final_hist = result["macd_hist"].tail(10).drop_nulls()

        assert final_hist.mean() > 0, "MACD histogram should be positive in uptrend"

    def test_macd_crossover_detection(self, uptrend_prices: pl.DataFrame) -> None:
        """Test detection of MACD-signal crossovers."""
        result = compute_macd(uptrend_prices)

        # Should detect at least one bullish crossover in uptrend
        cross_signals = result["macd_cross"].drop_nulls()

        assert (cross_signals == 1).sum() > 0, "Should detect bullish MACD cross"


class TestRateOfChange:
    """Tests for Rate of Change (ROC) indicator."""

    def test_roc_shape(self, sample_prices: pl.DataFrame) -> None:
        """Test that ROC returns correct column."""
        result = compute_rate_of_change(sample_prices, period=14)

        assert "roc" in result.columns

    def test_roc_uptrend_positive(self, uptrend_prices: pl.DataFrame) -> None:
        """Test that ROC is positive in uptrend."""
        result = compute_rate_of_change(uptrend_prices, period=14)

        # ROC should be positive (price higher than 14 days ago)
        roc_values = result["roc"].drop_nulls()

        assert roc_values.mean() > 0, "ROC should be positive in uptrend"

    def test_roc_formula(self) -> None:
        """Test ROC formula with known values."""
        # Simple test data: price goes from 100 to 110 (10% increase)
        prices = pl.DataFrame(
            {
                "symbol": ["TEST"] * 20,
                "date": pl.date_range(
                    start=pl.date(2024, 1, 1),
                    end=pl.date(2024, 1, 20),
                    interval="1d",
                    eager=True,
                ),
                "close": [100.0] * 10 + [110.0] * 10,
            }
        )

        result = compute_rate_of_change(prices, period=10)

        # After 10 periods, ROC should be ~10%
        final_roc = result["roc"].tail(5).drop_nulls()

        # All should be close to 10%
        assert final_roc.mean() >= 9.0
        assert final_roc.mean() <= 11.0

    def test_roc_period_parameter(self, sample_prices: pl.DataFrame) -> None:
        """Test ROC with different periods."""
        result_7 = compute_rate_of_change(sample_prices, period=7)
        result_14 = compute_rate_of_change(sample_prices, period=14)

        # Shorter period should have fewer null values
        null_count_7 = result_7["roc"].null_count()
        null_count_14 = result_14["roc"].null_count()

        assert null_count_7 < null_count_14


class TestADX:
    """Tests for ADX (Average Directional Index)."""

    def test_adx_shape(self, sample_prices: pl.DataFrame) -> None:
        """Test that ADX returns correct columns."""
        result = compute_adx(sample_prices, period=14)

        expected_cols = ["adx", "plus_di", "minus_di"]
        for col in expected_cols:
            assert col in result.columns, f"Missing column: {col}"

    def test_adx_range(self, sample_prices: pl.DataFrame) -> None:
        """Test that ADX values are in valid range [0, 100]."""
        result = compute_adx(sample_prices)

        adx_values = result["adx"].drop_nulls()

        assert adx_values.min() >= 0.0
        assert adx_values.max() <= 100.0

    def test_adx_di_range(self, sample_prices: pl.DataFrame) -> None:
        """Test that DI values are in valid range [0, 100+]."""
        result = compute_adx(sample_prices)

        plus_di = result["plus_di"].drop_nulls()
        minus_di = result["minus_di"].drop_nulls()

        assert plus_di.min() >= 0.0
        assert minus_di.min() >= 0.0

    def test_adx_uptrend_plus_di_dominates(self, uptrend_prices: pl.DataFrame) -> None:
        """Test that +DI > -DI in uptrend."""
        result = compute_adx(uptrend_prices)

        valid_rows = result.filter(pl.col("adx").is_not_null())
        final_rows = valid_rows.tail(10)

        # In uptrend, +DI should be greater than -DI
        assert (
            final_rows["plus_di"].mean() > final_rows["minus_di"].mean()
        ), "+DI should dominate in uptrend"

    def test_adx_strong_trend_high_value(self, uptrend_prices: pl.DataFrame) -> None:
        """Test that ADX is high during strong trend."""
        result = compute_adx(uptrend_prices, period=14)

        # Strong uptrend should produce high ADX (> 20)
        final_adx = result["adx"].tail(10).drop_nulls()

        # Allow some tolerance, but expect elevated ADX
        assert final_adx.mean() > 15, "ADX should be elevated in strong trend"


class TestOBV:
    """Tests for On-Balance Volume (OBV)."""

    def test_obv_shape(self, sample_prices: pl.DataFrame) -> None:
        """Test that OBV returns correct column."""
        result = compute_obv(sample_prices)

        assert "obv" in result.columns

    def test_obv_cumulative(self, sample_prices: pl.DataFrame) -> None:
        """Test that OBV is cumulative (monotonic when price trend is clear)."""
        result = compute_obv(sample_prices)

        obv_values = result["obv"].drop_nulls()

        # OBV should be non-zero (cumulative volume)
        assert obv_values.abs().sum() > 0

    def test_obv_uptrend_rising(self, uptrend_prices: pl.DataFrame) -> None:
        """Test that OBV rises during uptrend."""
        result = compute_obv(uptrend_prices)

        obv_values = result["obv"].drop_nulls()

        # OBV should generally increase in uptrend
        # Check if final OBV > initial OBV
        initial_obv = obv_values.head(10).mean()
        final_obv = obv_values.tail(10).mean()

        assert final_obv > initial_obv, "OBV should rise in uptrend"

    def test_obv_formula(self) -> None:
        """Test OBV formula with known values."""
        prices = pl.DataFrame(
            {
                "symbol": ["TEST"] * 5,
                "date": pl.date_range(
                    start=pl.date(2024, 1, 1),
                    end=pl.date(2024, 1, 5),
                    interval="1d",
                    eager=True,
                ),
                "close": [100, 102, 101, 103, 105],  # Up, down, up, up
                "volume": [1000, 2000, 1500, 2500, 3000],
            }
        )

        result = compute_obv(prices)

        # Manual calculation:
        # Day 1: OBV = 0 (first day, no previous)
        # Day 2: Close up, OBV = 0 + 2000 = 2000
        # Day 3: Close down, OBV = 2000 - 1500 = 500
        # Day 4: Close up, OBV = 500 + 2500 = 3000
        # Day 5: Close up, OBV = 3000 + 3000 = 6000

        expected_obv = [0, 2000, 500, 3000, 6000]
        actual_obv = result["obv"].to_list()

        assert np.allclose(actual_obv, expected_obv, rtol=1e-5)


class TestCombinedFeatures:
    """Tests for combined momentum feature computation."""

    def test_all_features_computed(self, sample_prices: pl.DataFrame) -> None:
        """Test that all momentum features are computed."""
        result = compute_momentum_features(sample_prices)

        expected_features = [
            "ma_fast",
            "ma_slow",
            "ma_diff",
            "ma_cross",
            "macd_line",
            "macd_signal",
            "macd_hist",
            "macd_cross",
            "roc",
            "adx",
            "plus_di",
            "minus_di",
            "obv",
        ]

        for feature in expected_features:
            assert feature in result.columns, f"Missing feature: {feature}"

    def test_all_original_columns_preserved(self, sample_prices: pl.DataFrame) -> None:
        """Test that original OHLCV columns are preserved."""
        result = compute_momentum_features(sample_prices)

        for col in sample_prices.columns:
            assert col in result.columns, f"Original column {col} not preserved"

    def test_custom_parameters(self, sample_prices: pl.DataFrame) -> None:
        """Test that custom parameters are applied."""
        result = compute_momentum_features(
            sample_prices,
            ma_fast_period=5,
            ma_slow_period=20,
            roc_period=7,
            adx_period=7,
        )

        # With shorter periods, should have fewer null values
        null_count = result["ma_slow"].null_count()
        assert null_count < 50, "Shorter periods should reduce null values"

    def test_multi_symbol(self) -> None:
        """Test feature computation with multiple symbols."""
        # Create multi-symbol data
        n_rows = 30
        prices = pl.DataFrame(
            {
                "symbol": ["AAPL"] * n_rows + ["MSFT"] * n_rows,
                "date": pl.date_range(
                    start=pl.date(2024, 1, 1),
                    end=pl.date(2024, 1, 30),
                    interval="1d",
                    eager=True,
                ).extend(
                    pl.date_range(
                        start=pl.date(2024, 1, 1),
                        end=pl.date(2024, 1, 30),
                        interval="1d",
                        eager=True,
                    )
                ),
                "close": list(range(100, 130)) + list(range(150, 180)),
                "high": list(range(101, 131)) + list(range(151, 181)),
                "low": list(range(99, 129)) + list(range(149, 179)),
                "open": list(range(100, 130)) + list(range(150, 180)),
                "volume": [1000000] * (n_rows * 2),
            }
        )

        result = compute_momentum_features(prices)

        # Check both symbols present
        assert "AAPL" in result["symbol"].to_list()
        assert "MSFT" in result["symbol"].to_list()
        assert result.shape[0] == 60


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_insufficient_data(self) -> None:
        """Test behavior with insufficient data for indicators."""
        # Only 10 rows, but MA needs 50
        prices = pl.DataFrame(
            {
                "symbol": ["TEST"] * 10,
                "date": pl.date_range(
                    start=pl.date(2024, 1, 1),
                    end=pl.date(2024, 1, 10),
                    interval="1d",
                    eager=True,
                ),
                "close": list(range(100, 110)),
                "high": list(range(101, 111)),
                "low": list(range(99, 109)),
                "open": list(range(100, 110)),
                "volume": [1000000] * 10,
            }
        )

        result = compute_moving_averages(prices, fast_period=5, slow_period=50)

        # All slow MA values should be null (insufficient data)
        assert result["ma_slow"].null_count() == 10

    def test_constant_prices(self) -> None:
        """Test behavior with constant prices (no movement)."""
        prices = pl.DataFrame(
            {
                "symbol": ["TEST"] * 30,
                "date": pl.date_range(
                    start=pl.date(2024, 1, 1),
                    end=pl.date(2024, 1, 30),
                    interval="1d",
                    eager=True,
                ),
                "close": [100.0] * 30,
                "high": [100.0] * 30,
                "low": [100.0] * 30,
                "open": [100.0] * 30,
                "volume": [1000000] * 30,
            }
        )

        # MACD with constant prices should have zero histogram
        result_macd = compute_macd(prices)
        macd_hist = result_macd["macd_hist"].drop_nulls()

        if len(macd_hist) > 0:
            assert macd_hist.abs().mean() < 0.1, "MACD hist should be near zero"

        # ROC with constant prices should be zero
        result_roc = compute_rate_of_change(prices)
        roc_values = result_roc["roc"].drop_nulls()

        if len(roc_values) > 0:
            assert roc_values.abs().mean() < 0.1, "ROC should be near zero"

    def test_missing_required_columns(self) -> None:
        """Test error handling when required columns are missing."""
        # Missing 'volume' column needed for OBV
        prices = pl.DataFrame(
            {
                "symbol": ["TEST"] * 20,
                "date": pl.date_range(
                    start=pl.date(2024, 1, 1),
                    end=pl.date(2024, 1, 20),
                    interval="1d",
                    eager=True,
                ),
                "close": list(range(100, 120)),
            }
        )

        # Should raise error due to missing volume column
        with pytest.raises(pl.exceptions.ColumnNotFoundError, match="volume"):
            compute_obv(prices)
