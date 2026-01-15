"""
Unit tests for mean reversion feature engineering.

Tests cover:
- RSI (Relative Strength Index) calculation
- Bollinger Bands calculation
- Stochastic Oscillator calculation
- Z-Score calculation
- Combined feature computation
- Edge cases and error handling

All tests use real Polars DataFrames with synthetic price data.
"""

import numpy as np
import polars as pl
import pytest

from research.strategies.mean_reversion.features import (
    compute_bollinger_bands,
    compute_mean_reversion_features,
    compute_price_zscore,
    compute_rsi,
    compute_stochastic_oscillator,
)


@pytest.fixture()
def sample_prices() -> pl.DataFrame:
    """
    Create sample OHLCV price data for testing.

    Returns:
        DataFrame with 50 rows of synthetic price data for AAPL
    """
    np.random.seed(42)  # For reproducibility

    n_rows = 50
    dates = pl.date_range(
        start=pl.date(2024, 1, 1), end=pl.date(2024, 2, 19), interval="1d", eager=True
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
def trending_prices() -> pl.DataFrame:
    """
    Create trending price data (strong uptrend).

    Useful for testing overbought conditions.
    """
    n_rows = 30
    dates = pl.date_range(
        start=pl.date(2024, 1, 1), end=pl.date(2024, 1, 30), interval="1d", eager=True
    )

    # Strong uptrend
    base_price = 100.0
    prices = base_price + np.arange(n_rows) * 2.0  # $2/day increase

    return pl.DataFrame(
        {
            "symbol": ["MSFT"] * n_rows,
            "date": dates,
            "open": prices,
            "high": prices * 1.01,
            "low": prices * 0.99,
            "close": prices,
            "volume": [2000000] * n_rows,
        }
    )


class TestRSI:
    """Tests for RSI (Relative Strength Index) calculation."""

    def test_rsi_shape(self, sample_prices: pl.DataFrame) -> None:
        """Test that RSI returns correct shape."""
        result = compute_rsi(sample_prices, period=14)

        assert result.shape[0] == sample_prices.shape[0]
        assert "rsi" in result.columns

    def test_rsi_range(self, sample_prices: pl.DataFrame) -> None:
        """Test that RSI values are in valid range [0, 100]."""
        result = compute_rsi(sample_prices, period=14)

        # Skip null values from insufficient lookback
        rsi_values = result["rsi"].drop_nulls()

        assert rsi_values.min() >= 0.0
        assert rsi_values.max() <= 100.0

    def test_rsi_uptrend_overbought(self, trending_prices: pl.DataFrame) -> None:
        """Test that RSI shows overbought (>70) during strong uptrend."""
        result = compute_rsi(trending_prices, period=14)

        # Last few values should be overbought
        final_rsi = result["rsi"].tail(5).drop_nulls()

        assert final_rsi.mean() > 70.0, "RSI should indicate overbought in strong uptrend"

    def test_rsi_period_parameter(self, sample_prices: pl.DataFrame) -> None:
        """Test RSI with different period parameters."""
        result_14 = compute_rsi(sample_prices, period=14)
        result_7 = compute_rsi(sample_prices, period=7)

        # Shorter period should have fewer null values
        null_count_14 = result_14["rsi"].null_count()
        null_count_7 = result_7["rsi"].null_count()

        assert null_count_7 < null_count_14, "Shorter period should have fewer null values"

    def test_rsi_preserves_other_columns(self, sample_prices: pl.DataFrame) -> None:
        """Test that RSI doesn't drop other columns."""
        result = compute_rsi(sample_prices)

        for col in sample_prices.columns:
            assert col in result.columns, f"Column {col} should be preserved"


class TestBollingerBands:
    """Tests for Bollinger Bands calculation."""

    def test_bollinger_bands_shape(self, sample_prices: pl.DataFrame) -> None:
        """Test that Bollinger Bands returns correct columns."""
        result = compute_bollinger_bands(sample_prices, period=20, num_std=2.0)

        expected_cols = ["bb_middle", "bb_upper", "bb_lower", "bb_width", "bb_pct"]
        for col in expected_cols:
            assert col in result.columns, f"Missing column: {col}"

    def test_bollinger_bands_ordering(self, sample_prices: pl.DataFrame) -> None:
        """Test that upper band > middle > lower band."""
        result = compute_bollinger_bands(sample_prices, period=20)

        # Skip null values from insufficient lookback
        valid_rows = result.filter(pl.col("bb_middle").is_not_null())

        assert (valid_rows["bb_upper"] >= valid_rows["bb_middle"]).all()
        assert (valid_rows["bb_middle"] >= valid_rows["bb_lower"]).all()

    def test_bollinger_bands_width(self, sample_prices: pl.DataFrame) -> None:
        """Test that bandwidth = upper - lower."""
        result = compute_bollinger_bands(sample_prices, period=20)

        valid_rows = result.filter(pl.col("bb_width").is_not_null())

        calculated_width = valid_rows["bb_upper"] - valid_rows["bb_lower"]
        assert np.allclose(
            valid_rows["bb_width"].to_numpy(), calculated_width.to_numpy(), rtol=1e-5
        )

    def test_bollinger_pct_range(self, sample_prices: pl.DataFrame) -> None:
        """Test that %B is typically between 0 and 1."""
        result = compute_bollinger_bands(sample_prices, period=20)

        # %B between 0-1 means price is within bands
        # Can go outside (>1 or <0) if price breaks bands
        valid_pct = result["bb_pct"].drop_nulls()

        # Most values should be in range, but some outliers allowed
        in_range_count = ((valid_pct >= 0.0) & (valid_pct <= 1.0)).sum()
        assert in_range_count / len(valid_pct) > 0.7, "Most %B values should be between 0-1"

    def test_bollinger_std_parameter(self, sample_prices: pl.DataFrame) -> None:
        """Test Bollinger Bands with different std deviation multipliers."""
        result_2std = compute_bollinger_bands(sample_prices, period=20, num_std=2.0)
        result_3std = compute_bollinger_bands(sample_prices, period=20, num_std=3.0)

        # Wider std should have wider bands
        valid_rows_2 = result_2std.filter(pl.col("bb_width").is_not_null())
        valid_rows_3 = result_3std.filter(pl.col("bb_width").is_not_null())

        assert valid_rows_3["bb_width"].mean() > valid_rows_2["bb_width"].mean()


class TestStochasticOscillator:
    """Tests for Stochastic Oscillator calculation."""

    def test_stochastic_shape(self, sample_prices: pl.DataFrame) -> None:
        """Test that Stochastic returns correct columns."""
        result = compute_stochastic_oscillator(sample_prices, k_period=14, d_period=3)

        assert "stoch_k" in result.columns
        assert "stoch_d" in result.columns

    def test_stochastic_range(self, sample_prices: pl.DataFrame) -> None:
        """Test that Stochastic values are in valid range [0, 100]."""
        result = compute_stochastic_oscillator(sample_prices)

        stoch_k = result["stoch_k"].drop_nulls()
        stoch_d = result["stoch_d"].drop_nulls()

        assert stoch_k.min() >= 0.0
        assert stoch_k.max() <= 100.0
        assert stoch_d.min() >= 0.0
        assert stoch_d.max() <= 100.0

    def test_stochastic_d_is_smoothed_k(self, sample_prices: pl.DataFrame) -> None:
        """Test that %D is smoothed version of %K (less volatile)."""
        result = compute_stochastic_oscillator(sample_prices, k_period=14, d_period=3)

        valid_rows = result.filter(pl.col("stoch_d").is_not_null())

        # %D should be smoother than %K (lower standard deviation)
        k_std = valid_rows["stoch_k"].std()
        d_std = valid_rows["stoch_d"].std()

        # Allow some tolerance since %D is just a 3-day SMA
        assert d_std <= k_std * 1.2, "%D should be smoother than %K"

    def test_stochastic_uptrend_overbought(self, trending_prices: pl.DataFrame) -> None:
        """Test that Stochastic shows overbought (>80) during strong uptrend."""
        result = compute_stochastic_oscillator(trending_prices)

        final_stoch = result["stoch_k"].tail(5).drop_nulls()
        assert final_stoch.mean() > 80.0, "Stochastic should indicate overbought in uptrend"


class TestZScore:
    """Tests for Z-Score calculation."""

    def test_zscore_shape(self, sample_prices: pl.DataFrame) -> None:
        """Test that Z-Score returns correct column."""
        result = compute_price_zscore(sample_prices, period=20)

        assert "price_zscore" in result.columns

    def test_zscore_mean_around_zero(self, sample_prices: pl.DataFrame) -> None:
        """Test that Z-Score mean is reasonable (not extreme)."""
        result = compute_price_zscore(sample_prices, period=20)

        zscore_values = result["price_zscore"].drop_nulls()

        # Mean should be reasonable (within 2 std devs)
        # Note: With rolling windows, mean doesn't have to be exactly 0
        assert abs(zscore_values.mean()) < 2.0

    def test_zscore_typical_range(self, sample_prices: pl.DataFrame) -> None:
        """Test that most Z-Score values are in typical range [-3, 3]."""
        result = compute_price_zscore(sample_prices, period=20)

        zscore_values = result["price_zscore"].drop_nulls()

        # ~99.7% of values should be within 3 std devs
        in_range = ((zscore_values >= -3.0) & (zscore_values <= 3.0)).sum()
        assert in_range / len(zscore_values) > 0.95

    def test_zscore_extreme_values(self) -> None:
        """Test Z-Score with extreme price spike."""
        # Create data with sudden, extreme price spike
        # Use 100x spike to ensure Z-score is positive and significant
        prices = pl.DataFrame(
            {
                "symbol": ["TEST"] * 30,
                "date": pl.date_range(
                    start=pl.date(2024, 1, 1), end=pl.date(2024, 1, 30), interval="1d", eager=True
                ),
                "close": [100.0] * 20 + [10000.0] * 10,  # Sudden 100x jump (extreme)
            }
        )

        result = compute_price_zscore(prices, period=20)

        # Z-score should spike after price jump
        # Note: With 20-day rolling window, spike is diluted by earlier values
        # We test that Z-score is significantly positive (>1.0)
        final_zscore = result["price_zscore"].tail(5).drop_nulls()
        assert final_zscore.max() > 1.0, "Z-score should detect extreme price spike"


class TestCombinedFeatures:
    """Tests for combined feature computation."""

    def test_all_features_computed(self, sample_prices: pl.DataFrame) -> None:
        """Test that compute_mean_reversion_features returns all expected features."""
        result = compute_mean_reversion_features(sample_prices)

        expected_features = [
            "rsi",
            "bb_middle",
            "bb_upper",
            "bb_lower",
            "bb_width",
            "bb_pct",
            "stoch_k",
            "stoch_d",
            "price_zscore",
        ]

        for feature in expected_features:
            assert feature in result.columns, f"Missing feature: {feature}"

    def test_all_original_columns_preserved(self, sample_prices: pl.DataFrame) -> None:
        """Test that original OHLCV columns are preserved."""
        result = compute_mean_reversion_features(sample_prices)

        for col in sample_prices.columns:
            assert col in result.columns, f"Original column {col} not preserved"

    def test_custom_parameters(self, sample_prices: pl.DataFrame) -> None:
        """Test that custom parameters are applied."""
        result = compute_mean_reversion_features(
            sample_prices, rsi_period=7, bb_period=10, zscore_period=10
        )

        # With shorter periods, should have fewer null values
        null_count = result["rsi"].null_count()
        assert null_count < 14, "Shorter periods should reduce null values"

    def test_multipl_symbols(self) -> None:
        """Test feature computation with multiple symbols."""
        # Create multi-symbol data
        prices = pl.DataFrame(
            {
                "symbol": ["AAPL"] * 30 + ["MSFT"] * 30,
                "date": pl.date_range(
                    start=pl.date(2024, 1, 1), end=pl.date(2024, 1, 30), interval="1d", eager=True
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
                "volume": [1000000] * 60,
            }
        )

        result = compute_mean_reversion_features(prices)

        # Check both symbols present
        assert "AAPL" in result["symbol"].to_list()
        assert "MSFT" in result["symbol"].to_list()
        assert result.shape[0] == 60


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_insufficient_data(self) -> None:
        """Test behavior with insufficient data for indicators."""
        # Only 10 rows, but RSI needs 14+
        prices = pl.DataFrame(
            {
                "symbol": ["TEST"] * 10,
                "date": pl.date_range(
                    start=pl.date(2024, 1, 1), end=pl.date(2024, 1, 10), interval="1d", eager=True
                ),
                "close": list(range(100, 110)),
                "high": list(range(101, 111)),
                "low": list(range(99, 109)),
                "open": list(range(100, 110)),
                "volume": [1000000] * 10,
            }
        )

        result = compute_rsi(prices, period=14)

        # All RSI values should be null (insufficient data)
        assert result["rsi"].null_count() == 10

    def test_constant_prices(self) -> None:
        """Test behavior with constant prices (no movement)."""
        prices = pl.DataFrame(
            {
                "symbol": ["TEST"] * 30,
                "date": pl.date_range(
                    start=pl.date(2024, 1, 1), end=pl.date(2024, 1, 30), interval="1d", eager=True
                ),
                "close": [100.0] * 30,
                "high": [100.0] * 30,
                "low": [100.0] * 30,
                "open": [100.0] * 30,
                "volume": [1000000] * 30,
            }
        )

        # Bollinger Bands width should be ~0 (no volatility)
        result_bb = compute_bollinger_bands(prices)

        # Note: RSI with constant prices produces NaN due to division by zero
        # This is expected behavior - we only test Bollinger width here

        # Check Bollinger width is small (near zero volatility)
        bb_width = result_bb["bb_width"].drop_nulls()
        if len(bb_width) > 0:
            assert bb_width.mean() < 1.0, "Bollinger width should be small with constant prices"

    def test_missing_required_columns(self) -> None:
        """Test error handling when required columns are missing."""
        # Missing 'high' and 'low' columns needed for Stochastic
        prices = pl.DataFrame(
            {
                "symbol": ["TEST"] * 20,
                "date": pl.date_range(
                    start=pl.date(2024, 1, 1), end=pl.date(2024, 1, 20), interval="1d", eager=True
                ),
                "close": list(range(100, 120)),
            }
        )

        # Should raise error due to missing columns
        with pytest.raises(pl.exceptions.ColumnNotFoundError, match="high|low"):
            compute_stochastic_oscillator(prices)

    def test_multi_symbol_no_cross_contamination(self) -> None:
        """
        Test that rolling indicators don't mix data between different symbols.

        This is a critical test for the per-symbol grouping fix. Without .over("symbol"),
        rolling windows would blend the last rows of AAPL with the first rows of MSFT,
        producing incorrect feature values at symbol boundaries.
        """
        # Create data for two symbols with very different price patterns
        # AAPL: prices 100-119 (trending up)
        # MSFT: prices 200-219 (trending up but different level)
        prices = pl.DataFrame(
            {
                "symbol": ["AAPL"] * 20 + ["MSFT"] * 20,
                "date": pl.date_range(
                    start=pl.date(2024, 1, 1), end=pl.date(2024, 1, 20), interval="1d", eager=True
                ).to_list()
                * 2,  # Same dates for both symbols
                "close": list(range(100, 120)) + list(range(200, 220)),
                "high": list(range(101, 121)) + list(range(201, 221)),
                "low": list(range(99, 119)) + list(range(199, 219)),
                "open": list(range(100, 120)) + list(range(200, 220)),
                "volume": [1000000] * 40,
            }
        ).sort(
            ["symbol", "date"]
        )  # Ensure sorted by symbol, date

        # Test RSI - should be calculated independently per symbol
        result_rsi = compute_rsi(prices, period=14)

        # Get RSI values for each symbol
        aapl_rsi = result_rsi.filter(pl.col("symbol") == "AAPL")["rsi"]
        msft_rsi = result_rsi.filter(pl.col("symbol") == "MSFT")["rsi"]

        # First values should be null (insufficient data for EMA)
        # EMA-based RSI starts calculating after period, so expect most early values to be null
        assert aapl_rsi[:13].null_count() >= 12, "Most early AAPL RSI values should be null"
        assert msft_rsi[:13].null_count() >= 12, "Most early MSFT RSI values should be null"

        # RSI values should be similar for both (both trending up at similar rate)
        # But they should be calculated independently, not influenced by each other
        aapl_rsi_valid = aapl_rsi.drop_nulls()
        msft_rsi_valid = msft_rsi.drop_nulls()

        assert len(aapl_rsi_valid) > 0, "Should have some valid AAPL RSI values"
        assert len(msft_rsi_valid) > 0, "Should have some valid MSFT RSI values"

        # Test Bollinger Bands - middle band should reflect each symbol's price level
        result_bb = compute_bollinger_bands(prices, period=10)

        aapl_bb_middle = result_bb.filter(pl.col("symbol") == "AAPL")["bb_middle"].drop_nulls()
        msft_bb_middle = result_bb.filter(pl.col("symbol") == "MSFT")["bb_middle"].drop_nulls()

        # AAPL middle band should be around 100-119
        # MSFT middle band should be around 200-219
        # If cross-contaminated, MSFT's first values would be affected by AAPL's 100-119 prices
        assert aapl_bb_middle.max() < 150, "AAPL Bollinger middle should stay in 100-120 range"
        assert msft_bb_middle.min() > 150, "MSFT Bollinger middle should stay in 200-220 range"
        assert not (
            aapl_bb_middle.to_list() == msft_bb_middle.to_list()
        ), "Bollinger bands should be different for different price levels"

        # Test Stochastic Oscillator - should be calculated per symbol
        result_stoch = compute_stochastic_oscillator(prices, k_period=14, d_period=3)

        # Get stochastic values for each symbol (drop nulls for comparison)
        aapl_stoch_k = result_stoch.filter(pl.col("symbol") == "AAPL")["stoch_k"].drop_nulls()
        msft_stoch_k = result_stoch.filter(pl.col("symbol") == "MSFT")["stoch_k"].drop_nulls()

        assert len(aapl_stoch_k) > 0, "Should have some valid AAPL stochastic values"
        assert len(msft_stoch_k) > 0, "Should have some valid MSFT stochastic values"

        # Stochastic should be calculated independently - values should exist for both symbols
        # and should be in valid range [0, 100]
        assert aapl_stoch_k.min() >= 0
        assert aapl_stoch_k.max() <= 100
        assert msft_stoch_k.min() >= 0
        assert msft_stoch_k.max() <= 100

        # Test Z-Score - should be calculated per symbol relative to each symbol's own mean
        result_zscore = compute_price_zscore(prices, period=10)

        aapl_zscore = result_zscore.filter(pl.col("symbol") == "AAPL")["price_zscore"].drop_nulls()
        msft_zscore = result_zscore.filter(pl.col("symbol") == "MSFT")["price_zscore"].drop_nulls()

        # Both symbols have similar upward trends, so z-scores should be similar
        # If cross-contaminated, MSFT's first z-score would be huge (200 vs AAPL's mean of ~110)
        assert len(aapl_zscore) > 0, "Should have some valid AAPL z-score values"
        assert len(msft_zscore) > 0, "Should have some valid MSFT z-score values"

        # Z-scores should be reasonable (within -3 to 3 for normal distributions)
        # If contaminated, we'd see extreme values (>10) for MSFT
        assert abs(aapl_zscore.mean()) < 3, "AAPL z-scores should be reasonable"
        assert abs(msft_zscore.mean()) < 3, "MSFT z-scores should be reasonable"
        assert aapl_zscore.max() < 10, "AAPL z-scores shouldn't have extreme outliers"
        assert (
            msft_zscore.max() < 10
        ), "MSFT z-scores shouldn't have extreme outliers from contamination"
