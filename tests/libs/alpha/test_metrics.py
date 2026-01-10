"""Unit tests for alpha metrics."""

import math
from datetime import date

import polars as pl
import pytest

from libs.alpha.metrics import (
    AlphaMetricsAdapter,
    LocalMetrics,
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
