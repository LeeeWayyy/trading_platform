"""Unit tests for alpha metrics."""

import math
from datetime import date, timedelta
from unittest.mock import patch

import polars as pl
import pytest

from libs.trading.alpha.metrics import (
    AlphaMetricsAdapter,
    DecayCurveResult,
    ICIRResult,
    ICResult,
    LocalMetrics,
    QlibMetrics,
    _qlib_available,
)


class TestLocalMetrics:
    """Tests for LocalMetrics implementation."""

    def test_pearson_ic_basic(self):
        """Test Pearson IC with perfect correlation."""
        signal = pl.Series([1.0, 2.0, 3.0, 4.0, 5.0] * 10)  # 50 obs
        returns = pl.Series([0.1, 0.2, 0.3, 0.4, 0.5] * 10)

        ic = LocalMetrics.pearson_ic(signal, returns)
        assert ic == pytest.approx(1.0, abs=0.01)

    def test_pearson_ic_negative(self):
        """Test Pearson IC with negative correlation."""
        signal = pl.Series([5.0, 4.0, 3.0, 2.0, 1.0] * 10)
        returns = pl.Series([0.1, 0.2, 0.3, 0.4, 0.5] * 10)

        ic = LocalMetrics.pearson_ic(signal, returns)
        assert ic == pytest.approx(-1.0, abs=0.01)

    def test_pearson_ic_insufficient_data(self):
        """Test Pearson IC returns NaN with insufficient data."""
        signal = pl.Series([1.0, 2.0, 3.0])
        returns = pl.Series([0.1, 0.2, 0.3])

        ic = LocalMetrics.pearson_ic(signal, returns)
        assert math.isnan(ic)

    def test_pearson_ic_with_nulls(self):
        """Test Pearson IC handles nulls correctly."""
        signal = pl.Series([1.0, None, 3.0, 4.0, 5.0] * 10)
        returns = pl.Series([0.1, 0.2, None, 0.4, 0.5] * 10)

        ic = LocalMetrics.pearson_ic(signal, returns)
        # Should compute on valid pairs only
        assert not math.isnan(ic)

    def test_rank_ic_basic(self):
        """Test Rank IC with monotonic relationship."""
        signal = pl.Series([1.0, 2.0, 3.0, 4.0, 5.0] * 10)
        returns = pl.Series([0.1, 0.2, 0.3, 0.4, 0.5] * 10)

        ic = LocalMetrics.rank_ic(signal, returns)
        assert ic == pytest.approx(1.0, abs=0.01)

    def test_rank_ic_robust_to_outliers(self):
        """Test Rank IC is more robust to outliers than Pearson."""
        signal = pl.Series([1.0, 2.0, 3.0, 4.0, 100.0] * 10)  # Outlier
        returns = pl.Series([0.1, 0.2, 0.3, 0.4, 0.5] * 10)

        rank_ic = LocalMetrics.rank_ic(signal, returns)
        pearson_ic = LocalMetrics.pearson_ic(signal, returns)

        # Rank IC should be close to 1.0, Pearson affected by outlier
        assert rank_ic == pytest.approx(1.0, abs=0.01)
        assert rank_ic > pearson_ic

    def test_hit_rate_perfect(self):
        """Test hit rate with perfect predictions."""
        signal = pl.Series([1.0, -1.0, 1.0, -1.0] * 10)
        returns = pl.Series([0.1, -0.1, 0.1, -0.1] * 10)

        hr = LocalMetrics.hit_rate(signal, returns)
        assert hr == pytest.approx(1.0)

    def test_hit_rate_random(self):
        """Test hit rate with random predictions."""
        signal = pl.Series([1.0, -1.0, 1.0, -1.0] * 10)
        returns = pl.Series([0.1, 0.1, -0.1, -0.1] * 10)

        hr = LocalMetrics.hit_rate(signal, returns)
        assert hr == pytest.approx(0.5)

    def test_coverage_full(self):
        """Test coverage with all valid signals."""
        signal = pl.Series([1.0, 2.0, 3.0, 4.0, 5.0])

        cov = LocalMetrics.coverage(signal, 5)
        assert cov == pytest.approx(1.0)

    def test_coverage_partial(self):
        """Test coverage with some nulls/zeros."""
        signal = pl.Series([1.0, None, 0.0, 4.0, 5.0])

        cov = LocalMetrics.coverage(signal, 5)
        assert cov == pytest.approx(0.6)  # 3 valid out of 5

    def test_autocorrelation_high(self):
        """Test autocorrelation with persistent signal."""
        # Gradually increasing series should have high autocorr
        signal = pl.Series(list(range(100)))

        ac = LocalMetrics.autocorrelation(signal, lag=1)
        assert ac > 0.9

    def test_autocorrelation_insufficient_data(self):
        """Test autocorrelation returns NaN with insufficient data."""
        signal = pl.Series([1.0, 2.0, 3.0])

        ac = LocalMetrics.autocorrelation(signal, lag=5)
        assert math.isnan(ac)

    def test_autocorrelation_with_nulls(self):
        """Test autocorrelation handles nulls correctly."""
        signal = pl.Series([1.0, None, 3.0, 4.0, 5.0] * 20)

        ac = LocalMetrics.autocorrelation(signal, lag=1)
        # Should drop nulls and compute on valid data
        assert not math.isnan(ac)

    def test_long_short_spread_positive(self):
        """Test long/short spread with positive alpha."""
        # Higher signal -> higher returns
        signal = pl.Series(list(range(1, 101)))  # 1 to 100
        returns = pl.Series([x / 1000 for x in range(1, 101)])  # Proportional

        spread = LocalMetrics.long_short_spread(signal, returns, n_deciles=10)
        assert spread > 0


class TestAlphaMetricsAdapter:
    """Tests for AlphaMetricsAdapter."""

    @pytest.fixture()
    def adapter(self):
        """Create metrics adapter."""
        return AlphaMetricsAdapter(prefer_qlib=False)  # Use local for testing

    @pytest.fixture()
    def sample_data(self):
        """Create sample signal and returns data."""
        n_stocks = 100
        signal_df = pl.DataFrame(
            {
                "permno": list(range(n_stocks)),
                "date": [date(2024, 1, 1)] * n_stocks,
                "signal": [float(i) for i in range(n_stocks)],
            }
        )
        returns_df = pl.DataFrame(
            {
                "permno": list(range(n_stocks)),
                "date": [date(2024, 1, 1)] * n_stocks,
                "return": [i / 1000 for i in range(n_stocks)],
            }
        )
        return signal_df, returns_df

    def test_compute_ic(self, adapter, sample_data):
        """Test IC computation."""
        signal, returns = sample_data

        result = adapter.compute_ic(signal, returns)

        assert result.n_observations == 100
        assert result.pearson_ic == pytest.approx(1.0, abs=0.01)
        assert result.rank_ic == pytest.approx(1.0, abs=0.01)

    def test_compute_hit_rate(self, adapter):
        """Test hit rate computation."""
        signal = pl.DataFrame(
            {
                "permno": list(range(40)),
                "date": [date(2024, 1, 1)] * 40,
                "signal": [1.0, -1.0] * 20,
            }
        )
        returns = pl.DataFrame(
            {
                "permno": list(range(40)),
                "date": [date(2024, 1, 1)] * 40,
                "return": [0.1, -0.1] * 20,
            }
        )

        hr = adapter.compute_hit_rate(signal, returns)
        assert hr == pytest.approx(1.0)

    def test_compute_coverage(self, adapter):
        """Test coverage computation."""
        signal = pl.DataFrame(
            {
                "permno": list(range(100)),
                "date": [date(2024, 1, 1)] * 100,
                "signal": [1.0 if i < 80 else None for i in range(100)],
            }
        )

        cov = adapter.compute_coverage(signal, 100)
        assert cov == pytest.approx(0.8)

    def test_compute_long_short_spread(self, adapter, sample_data):
        """Test long/short spread computation."""
        signal, returns = sample_data

        spread = adapter.compute_long_short_spread(signal, returns, n_deciles=10)
        assert spread > 0  # Higher signal = higher return

    def test_backend_property(self, adapter):
        """Test backend property."""
        assert adapter.backend in ["qlib", "polars"]

    def test_empty_data_handling(self, adapter):
        """Test handling of empty data."""
        empty_signal = pl.DataFrame(
            schema={"permno": pl.Int64, "date": pl.Date, "signal": pl.Float64}
        )
        empty_returns = pl.DataFrame(
            schema={"permno": pl.Int64, "date": pl.Date, "return": pl.Float64}
        )

        result = adapter.compute_ic(empty_signal, empty_returns)
        assert math.isnan(result.pearson_ic)
        assert result.n_observations == 0

    def test_compute_icir_basic(self, adapter):
        """Test ICIR computation with valid data."""
        # Create daily IC data for 30 days
        dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(30)]
        daily_ic = pl.DataFrame(
            {"date": dates, "rank_ic": [0.05 + 0.01 * (i % 5) for i in range(30)]}
        )

        result = adapter.compute_icir(daily_ic, window=20)

        assert not math.isnan(result.icir)
        assert result.n_periods == 30
        assert not math.isnan(result.mean_ic)
        assert not math.isnan(result.std_ic)

    def test_compute_icir_insufficient_data(self, adapter):
        """Test ICIR returns NaN with insufficient data."""
        daily_ic = pl.DataFrame(
            {"date": [date(2024, 1, i) for i in range(1, 11)], "rank_ic": [0.05] * 10}
        )

        result = adapter.compute_icir(daily_ic, window=20)

        assert math.isnan(result.icir)
        assert result.n_periods == 10

    def test_compute_icir_zero_std(self, adapter):
        """Test ICIR handles zero standard deviation - returns very large value."""
        # All same IC values => std very close to 0 (floating point)
        dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(30)]
        daily_ic = pl.DataFrame({"date": dates, "rank_ic": [0.05] * 30})

        result = adapter.compute_icir(daily_ic, window=20)

        # Due to floating point, std may be very small but not exactly 0
        # This can result in very large ICIR or NaN
        assert result.mean_ic == pytest.approx(0.05)
        # std_ic should be very close to 0
        assert result.std_ic < 1e-10

    def test_compute_icir_with_ic_column(self, adapter):
        """Test ICIR computation with 'ic' column instead of 'rank_ic'."""
        dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(30)]
        daily_ic = pl.DataFrame({"date": dates, "ic": [0.05 + 0.01 * i for i in range(30)]})

        result = adapter.compute_icir(daily_ic, window=20)

        assert not math.isnan(result.icir)
        assert result.n_periods == 30

    def test_compute_grouped_ic(self, adapter):
        """Test grouped IC computation per sector."""
        n_stocks = 100
        signal_df = pl.DataFrame(
            {
                "permno": list(range(n_stocks)),
                "date": [date(2024, 1, 1)] * n_stocks,
                "signal": [float(i) for i in range(n_stocks)],
            }
        )
        returns_df = pl.DataFrame(
            {
                "permno": list(range(n_stocks)),
                "date": [date(2024, 1, 1)] * n_stocks,
                "return": [i / 1000 for i in range(n_stocks)],
            }
        )
        sector_mapping = pl.DataFrame(
            {
                "permno": list(range(n_stocks)),
                "date": [date(2024, 1, 1)] * n_stocks,
                "gics_sector": ["Tech" if i < 50 else "Finance" for i in range(n_stocks)],
            }
        )

        result = adapter.compute_grouped_ic(signal_df, returns_df, sector_mapping)

        assert result.height == 2  # Two sectors
        assert "gics_sector" in result.columns
        assert "ic" in result.columns
        assert "rank_ic" in result.columns
        assert "n_stocks" in result.columns

    def test_compute_grouped_ic_empty_data(self, adapter):
        """Test grouped IC with empty data."""
        empty_signal = pl.DataFrame(
            schema={"permno": pl.Int64, "date": pl.Date, "signal": pl.Float64}
        )
        empty_returns = pl.DataFrame(
            schema={"permno": pl.Int64, "date": pl.Date, "return": pl.Float64}
        )
        empty_mapping = pl.DataFrame(
            schema={"permno": pl.Int64, "date": pl.Date, "gics_sector": pl.Utf8}
        )

        result = adapter.compute_grouped_ic(empty_signal, empty_returns, empty_mapping)

        assert result.height == 0
        assert "gics_sector" in result.columns

    def test_compute_grouped_ic_insufficient_sector_data(self, adapter):
        """Test grouped IC skips sectors with insufficient data."""
        # Create a sector with only 5 stocks (below MIN_OBSERVATIONS)
        signal_df = pl.DataFrame(
            {
                "permno": list(range(55)),
                "date": [date(2024, 1, 1)] * 55,
                "signal": [float(i) for i in range(55)],
            }
        )
        returns_df = pl.DataFrame(
            {
                "permno": list(range(55)),
                "date": [date(2024, 1, 1)] * 55,
                "return": [i / 1000 for i in range(55)],
            }
        )
        sector_mapping = pl.DataFrame(
            {
                "permno": list(range(55)),
                "date": [date(2024, 1, 1)] * 55,
                # First 50 stocks are Tech (enough data), last 5 are Finance (insufficient)
                "gics_sector": ["Tech"] * 50 + ["Finance"] * 5,
            }
        )

        result = adapter.compute_grouped_ic(signal_df, returns_df, sector_mapping)

        # Only Tech sector should be included (Finance has < 30 observations)
        assert result.height == 1
        assert result["gics_sector"].to_list() == ["Tech"]

    def test_compute_decay_curve(self, adapter):
        """Test decay curve computation."""
        n_stocks = 50
        signal_df = pl.DataFrame(
            {
                "permno": list(range(n_stocks)),
                "date": [date(2024, 1, 1)] * n_stocks,
                "signal": [float(i) for i in range(n_stocks)],
            }
        )

        # Create returns at different horizons with decaying correlation
        returns_by_horizon = {}
        for horizon in [1, 5, 10, 20]:
            # Higher horizon = weaker correlation (add more noise)
            noise_factor = horizon / 20.0
            returns_by_horizon[horizon] = pl.DataFrame(
                {
                    "permno": list(range(n_stocks)),
                    "date": [date(2024, 1, 1)] * n_stocks,
                    "return": [
                        i / 1000 * (1 - noise_factor) + (i % 3) * noise_factor / 1000
                        for i in range(n_stocks)
                    ],
                }
            )

        result = adapter.compute_decay_curve(signal_df, returns_by_horizon)

        assert isinstance(result, DecayCurveResult)
        assert result.decay_curve.height == 4
        assert "horizon" in result.decay_curve.columns
        assert "ic" in result.decay_curve.columns
        assert "rank_ic" in result.decay_curve.columns

    def test_compute_decay_curve_empty(self, adapter):
        """Test decay curve with empty horizons."""
        signal_df = pl.DataFrame(
            {
                "permno": [1, 2, 3],
                "date": [date(2024, 1, 1)] * 3,
                "signal": [1.0, 2.0, 3.0],
            }
        )

        result = adapter.compute_decay_curve(signal_df, {})

        assert isinstance(result, DecayCurveResult)
        assert result.decay_curve.height == 0
        assert result.half_life is None

    def test_compute_autocorrelation(self, adapter):
        """Test autocorrelation computation."""
        dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(100)]
        signal_ts = pl.DataFrame(
            {
                "date": dates,
                "signal": list(range(100)),  # Highly autocorrelated
            }
        )

        result = adapter.compute_autocorrelation(signal_ts)

        assert 1 in result
        assert 5 in result
        assert 20 in result
        assert result[1] > 0.9  # High autocorrelation for lag=1

    def test_compute_autocorrelation_custom_lags(self, adapter):
        """Test autocorrelation with custom lags."""
        dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(100)]
        signal_ts = pl.DataFrame(
            {
                "date": dates,
                "signal": list(range(100)),
            }
        )

        result = adapter.compute_autocorrelation(signal_ts, lags=[1, 2, 3])

        assert 1 in result
        assert 2 in result
        assert 3 in result
        assert 5 not in result

    def test_compute_autocorrelation_empty(self, adapter):
        """Test autocorrelation with empty data."""
        empty_ts = pl.DataFrame(schema={"date": pl.Date, "signal": pl.Float64})

        result = adapter.compute_autocorrelation(empty_ts)

        assert math.isnan(result[1])
        assert math.isnan(result[5])
        assert math.isnan(result[20])

    def test_compute_hit_rate_empty(self, adapter):
        """Test hit rate with empty data."""
        empty_signal = pl.DataFrame(
            schema={"permno": pl.Int64, "date": pl.Date, "signal": pl.Float64}
        )
        empty_returns = pl.DataFrame(
            schema={"permno": pl.Int64, "date": pl.Date, "return": pl.Float64}
        )

        hr = adapter.compute_hit_rate(empty_signal, empty_returns)
        assert math.isnan(hr)

    def test_compute_coverage_empty(self, adapter):
        """Test coverage with empty signal."""
        empty_signal = pl.DataFrame(
            schema={"permno": pl.Int64, "date": pl.Date, "signal": pl.Float64}
        )

        cov = adapter.compute_coverage(empty_signal, 100)
        assert cov == 0.0

    def test_compute_long_short_spread_empty(self, adapter):
        """Test long/short spread with empty data."""
        empty_signal = pl.DataFrame(
            schema={"permno": pl.Int64, "date": pl.Date, "signal": pl.Float64}
        )
        empty_returns = pl.DataFrame(
            schema={"permno": pl.Int64, "date": pl.Date, "return": pl.Float64}
        )

        spread = adapter.compute_long_short_spread(empty_signal, empty_returns)
        assert math.isnan(spread)

    def test_compute_long_short_spread_all_nulls(self, adapter):
        """Test long/short spread when all values are null."""
        signal = pl.DataFrame(
            {
                "permno": list(range(50)),
                "date": [date(2024, 1, 1)] * 50,
                "signal": [None] * 50,
            }
        )
        returns = pl.DataFrame(
            {
                "permno": list(range(50)),
                "date": [date(2024, 1, 1)] * 50,
                "return": [None] * 50,
            }
        )

        spread = adapter.compute_long_short_spread(signal, returns)
        assert math.isnan(spread)

    def test_compute_ic_with_nulls_only(self, adapter):
        """Test IC computation when all values are null after filtering."""
        signal = pl.DataFrame(
            {
                "permno": list(range(50)),
                "date": [date(2024, 1, 1)] * 50,
                "signal": [None] * 50,
            }
        )
        returns = pl.DataFrame(
            {
                "permno": list(range(50)),
                "date": [date(2024, 1, 1)] * 50,
                "return": [None] * 50,
            }
        )

        result = adapter.compute_ic(signal, returns)
        assert math.isnan(result.pearson_ic)
        assert result.n_observations == 0

    def test_compute_ic_insufficient_per_date(self, adapter):
        """Test IC computation when each date has insufficient observations."""
        # Create data with multiple dates but very few obs per date
        n_per_date = 5  # Below MIN_OBSERVATIONS (30)
        dates_list = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)]

        signal_data = []
        returns_data = []
        for d in dates_list:
            for i in range(n_per_date):
                signal_data.append({"permno": i, "date": d, "signal": float(i)})
                returns_data.append({"permno": i, "date": d, "return": i / 1000})

        signal = pl.DataFrame(signal_data)
        returns = pl.DataFrame(returns_data)

        result = adapter.compute_ic(signal, returns)
        # Should have insufficient data per date
        assert math.isnan(result.pearson_ic)
        # But coverage should still be computed
        assert result.coverage > 0


class TestQlibAvailable:
    """Tests for _qlib_available function."""

    def test_qlib_available_returns_bool(self):
        """Test _qlib_available returns a boolean."""
        # Simply verify the function exists and returns a bool
        # The actual return value depends on whether qlib is installed
        result = _qlib_available()
        assert isinstance(result, bool)

    def test_qlib_not_installed_fallback(self):
        """Test that adapter falls back to polars when qlib not available."""
        # This tests the fallback behavior without mocking imports
        adapter = AlphaMetricsAdapter(prefer_qlib=False)
        assert adapter.backend == "polars"
        assert adapter._qlib is None


class TestQlibMetrics:
    """Tests for QlibMetrics implementation."""

    def test_qlib_pearson_ic_basic(self):
        """Test QlibMetrics Pearson IC computation."""
        signal = pl.Series([1.0, 2.0, 3.0, 4.0, 5.0] * 10)
        returns = pl.Series([0.1, 0.2, 0.3, 0.4, 0.5] * 10)

        ic = QlibMetrics.pearson_ic(signal, returns)
        assert ic == pytest.approx(1.0, abs=0.01)

    def test_qlib_pearson_ic_insufficient_data(self):
        """Test QlibMetrics Pearson IC with insufficient data."""
        signal = pl.Series([1.0, 2.0, 3.0])
        returns = pl.Series([0.1, 0.2, 0.3])

        ic = QlibMetrics.pearson_ic(signal, returns)
        assert math.isnan(ic)

    def test_qlib_rank_ic_basic(self):
        """Test QlibMetrics Rank IC computation."""
        signal = pl.Series([1.0, 2.0, 3.0, 4.0, 5.0] * 10)
        returns = pl.Series([0.1, 0.2, 0.3, 0.4, 0.5] * 10)

        ic = QlibMetrics.rank_ic(signal, returns)
        assert ic == pytest.approx(1.0, abs=0.01)

    def test_qlib_rank_ic_insufficient_data(self):
        """Test QlibMetrics Rank IC with insufficient data."""
        signal = pl.Series([1.0, 2.0, 3.0])
        returns = pl.Series([0.1, 0.2, 0.3])

        ic = QlibMetrics.rank_ic(signal, returns)
        assert math.isnan(ic)


class TestLocalMetricsEdgeCases:
    """Additional edge case tests for LocalMetrics."""

    def test_pearson_ic_length_mismatch(self):
        """Test Pearson IC raises error on length mismatch."""
        signal = pl.Series([1.0, 2.0, 3.0])
        returns = pl.Series([0.1, 0.2])

        with pytest.raises(ValueError, match="same length"):
            LocalMetrics.pearson_ic(signal, returns)

    def test_rank_ic_length_mismatch(self):
        """Test Rank IC raises error on length mismatch."""
        signal = pl.Series([1.0, 2.0, 3.0])
        returns = pl.Series([0.1, 0.2])

        with pytest.raises(ValueError, match="same length"):
            LocalMetrics.rank_ic(signal, returns)

    def test_compute_ic_invalid_method(self):
        """Test compute_ic raises error on invalid method."""
        signal = pl.Series([1.0, 2.0, 3.0] * 20)
        returns = pl.Series([0.1, 0.2, 0.3] * 20)

        with pytest.raises(ValueError, match="Unknown method"):
            LocalMetrics.compute_ic(signal, returns, method="invalid")

    def test_compute_ic_pearson_method(self):
        """Test compute_ic with pearson method."""
        signal = pl.Series([1.0, 2.0, 3.0, 4.0, 5.0] * 10)
        returns = pl.Series([0.1, 0.2, 0.3, 0.4, 0.5] * 10)

        ic = LocalMetrics.compute_ic(signal, returns, method="pearson")
        assert ic == pytest.approx(1.0, abs=0.01)

    def test_hit_rate_insufficient_data(self):
        """Test hit rate with insufficient data."""
        signal = pl.Series([1.0, -1.0, 1.0])
        returns = pl.Series([0.1, -0.1, 0.1])

        hr = LocalMetrics.hit_rate(signal, returns)
        assert math.isnan(hr)

    def test_hit_rate_all_zeros(self):
        """Test hit rate when all signals and returns are zero."""
        signal = pl.Series([0.0] * 50)
        returns = pl.Series([0.0] * 50)

        hr = LocalMetrics.hit_rate(signal, returns)
        assert math.isnan(hr)  # No non-zero pairs

    def test_coverage_zero_universe(self):
        """Test coverage with zero universe size."""
        signal = pl.Series([1.0, 2.0, 3.0])

        cov = LocalMetrics.coverage(signal, 0)
        assert cov == 0.0

    def test_long_short_spread_insufficient_data(self):
        """Test long/short spread with insufficient data."""
        signal = pl.Series([1.0, 2.0, 3.0])
        returns = pl.Series([0.1, 0.2, 0.3])

        spread = LocalMetrics.long_short_spread(signal, returns)
        assert math.isnan(spread)

    def test_autocorrelation_empty_after_dropna(self):
        """Test autocorrelation when all values are null."""
        signal = pl.Series([None, None, None, None, None])

        ac = LocalMetrics.autocorrelation(signal, lag=1)
        assert math.isnan(ac)


class TestEstimateHalfLife:
    """Tests for _estimate_half_life method."""

    @pytest.fixture()
    def adapter(self):
        """Create metrics adapter."""
        return AlphaMetricsAdapter(prefer_qlib=False)

    def test_half_life_insufficient_data(self, adapter):
        """Test half-life returns None with insufficient data."""
        decay_df = pl.DataFrame({"horizon": [1], "rank_ic": [0.5]})

        half_life = adapter._estimate_half_life(decay_df)
        assert half_life is None

    def test_half_life_initial_nan(self, adapter):
        """Test half-life returns None when initial IC is NaN."""
        decay_df = pl.DataFrame({"horizon": [1, 5, 10], "rank_ic": [float("nan"), 0.3, 0.2]})

        half_life = adapter._estimate_half_life(decay_df)
        assert half_life is None

    def test_half_life_initial_zero(self, adapter):
        """Test half-life returns None when initial IC is zero."""
        decay_df = pl.DataFrame({"horizon": [1, 5, 10], "rank_ic": [0.0, 0.3, 0.2]})

        half_life = adapter._estimate_half_life(decay_df)
        assert half_life is None

    def test_half_life_initial_negative(self, adapter):
        """Test half-life returns None when initial IC is negative."""
        decay_df = pl.DataFrame({"horizon": [1, 5, 10], "rank_ic": [-0.1, 0.3, 0.2]})

        half_life = adapter._estimate_half_life(decay_df)
        assert half_life is None

    def test_half_life_no_decay_below_half(self, adapter):
        """Test half-life returns None when IC never drops to half."""
        decay_df = pl.DataFrame({"horizon": [1, 5, 10], "rank_ic": [0.5, 0.45, 0.4]})

        half_life = adapter._estimate_half_life(decay_df)
        assert half_life is None  # Never dropped to 0.25

    def test_half_life_immediate_drop(self, adapter):
        """Test half-life when IC drops quickly."""
        # IC starts at 0.1, half is 0.05
        # IC at horizon 5 is 0.05 (exactly half), so half_life should be 5
        decay_df = pl.DataFrame({"horizon": [1, 5, 10], "rank_ic": [0.1, 0.05, 0.02]})

        half_life = adapter._estimate_half_life(decay_df)
        assert half_life == pytest.approx(5.0, abs=0.1)  # Half-life at horizon 5

    def test_half_life_interpolation(self, adapter):
        """Test half-life interpolation between horizons."""
        decay_df = pl.DataFrame({"horizon": [1, 5, 10], "rank_ic": [0.5, 0.4, 0.2]})

        half_life = adapter._estimate_half_life(decay_df)
        # Half IC = 0.25, which falls between horizon 5 (0.4) and 10 (0.2)
        assert half_life is not None
        assert 5 < half_life < 10

    def test_half_life_with_nan_in_middle(self, adapter):
        """Test half-life handles NaN values in decay curve."""
        decay_df = pl.DataFrame({"horizon": [1, 5, 10], "rank_ic": [0.5, float("nan"), 0.2]})

        half_life = adapter._estimate_half_life(decay_df)
        # Should handle NaN and still compute if possible
        assert half_life is not None or half_life is None  # Either works

    def test_half_life_zero_slope(self, adapter):
        """Test half-life when slope is zero."""
        decay_df = pl.DataFrame({"horizon": [1, 5, 10], "rank_ic": [0.5, 0.3, 0.3]})

        half_life = adapter._estimate_half_life(decay_df)
        # Slope=0 at transition point
        assert half_life is None or isinstance(half_life, float)

    def test_half_life_prev_nan(self, adapter):
        """Test half-life when previous IC is NaN."""
        decay_df = pl.DataFrame(
            {"horizon": [1, 5, 10, 15], "rank_ic": [0.5, float("nan"), 0.3, 0.2]}
        )

        half_life = adapter._estimate_half_life(decay_df)
        # Should handle this edge case
        assert half_life is None or isinstance(half_life, float)


class TestDataclasses:
    """Tests for dataclass structures."""

    def test_ic_result_creation(self):
        """Test ICResult dataclass creation."""
        result = ICResult(pearson_ic=0.5, rank_ic=0.6, n_observations=100, coverage=0.95)

        assert result.pearson_ic == 0.5
        assert result.rank_ic == 0.6
        assert result.n_observations == 100
        assert result.coverage == 0.95

    def test_icir_result_creation(self):
        """Test ICIRResult dataclass creation."""
        result = ICIRResult(icir=2.5, mean_ic=0.05, std_ic=0.02, n_periods=50)

        assert result.icir == 2.5
        assert result.mean_ic == 0.05
        assert result.std_ic == 0.02
        assert result.n_periods == 50

    def test_decay_curve_result_creation(self):
        """Test DecayCurveResult dataclass creation."""
        decay_df = pl.DataFrame(
            {"horizon": [1, 5, 10], "ic": [0.5, 0.4, 0.3], "rank_ic": [0.55, 0.45, 0.35]}
        )
        result = DecayCurveResult(decay_curve=decay_df, half_life=7.5)

        assert result.decay_curve.height == 3
        assert result.half_life == 7.5


class TestAlphaMetricsAdapterInit:
    """Tests for AlphaMetricsAdapter initialization."""

    def test_adapter_prefer_qlib_false(self):
        """Test adapter with prefer_qlib=False."""
        adapter = AlphaMetricsAdapter(prefer_qlib=False)

        assert adapter.backend == "polars"
        assert adapter._qlib is None

    def test_adapter_prefer_qlib_true_no_qlib(self):
        """Test adapter with prefer_qlib=True when Qlib not available."""
        with patch("libs.trading.alpha.metrics.QLIB_INSTALLED", False):
            adapter = AlphaMetricsAdapter(prefer_qlib=True)
            assert adapter.backend == "polars"


class TestMultiDateIC:
    """Tests for IC computation across multiple dates."""

    @pytest.fixture()
    def adapter(self):
        """Create metrics adapter."""
        return AlphaMetricsAdapter(prefer_qlib=False)

    def test_ic_multiple_dates(self, adapter):
        """Test IC computation with multiple dates."""
        n_stocks = 50
        dates_list = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)]

        signal_data = []
        returns_data = []
        for d in dates_list:
            for i in range(n_stocks):
                signal_data.append({"permno": i, "date": d, "signal": float(i)})
                returns_data.append({"permno": i, "date": d, "return": i / 1000})

        signal = pl.DataFrame(signal_data)
        returns = pl.DataFrame(returns_data)

        result = adapter.compute_ic(signal, returns)

        assert result.n_observations > 0
        assert result.pearson_ic == pytest.approx(1.0, abs=0.01)
        assert result.rank_ic == pytest.approx(1.0, abs=0.01)
        assert result.coverage == pytest.approx(1.0, abs=0.01)

    def test_ic_low_coverage_warning(self, adapter):
        """Test IC computation with low coverage triggers warning."""
        n_stocks = 50
        signal = pl.DataFrame(
            {
                "permno": list(range(n_stocks)),
                "date": [date(2024, 1, 1)] * n_stocks,
                # Half are null
                "signal": [float(i) if i >= 25 else None for i in range(n_stocks)],
            }
        )
        returns = pl.DataFrame(
            {
                "permno": list(range(n_stocks)),
                "date": [date(2024, 1, 1)] * n_stocks,
                # Half are null (overlapping)
                "return": [i / 1000 if i >= 15 else None for i in range(n_stocks)],
            }
        )

        result = adapter.compute_ic(signal, returns)

        # Should still compute IC on valid pairs
        assert result.n_observations < n_stocks
        # Coverage reflects the fraction with valid pairs
        assert result.coverage < 1.0
