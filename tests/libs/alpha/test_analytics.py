"""Tests for alpha analytics extensions."""

import math
from datetime import date

import polars as pl
import pytest

from libs.alpha.analytics import (
    AlphaAnalytics,
    DecayAnalysisResult,
    GroupedICResult,
)


class TestGroupedICResult:
    """Tests for GroupedICResult dataclass."""

    def test_creation(self):
        """Test creating GroupedICResult."""
        by_group = pl.DataFrame(
            {
                "gics_sector": ["Tech", "Finance", "Healthcare"],
                "rank_ic": [0.08, 0.05, 0.03],
                "n_stocks": [100, 80, 60],
            }
        )

        result = GroupedICResult(
            by_group=by_group,
            overall_ic=0.05,
            high_ic_groups=["Tech"],
            low_ic_groups=["Healthcare"],
        )

        assert result.overall_ic == 0.05
        assert "Tech" in result.high_ic_groups
        assert "Healthcare" in result.low_ic_groups


class TestDecayAnalysisResult:
    """Tests for DecayAnalysisResult dataclass."""

    def test_creation(self):
        """Test creating DecayAnalysisResult."""
        decay_curve = pl.DataFrame(
            {
                "horizon": [1, 5, 10, 20],
                "ic": [0.05, 0.04, 0.03, 0.02],
                "rank_ic": [0.06, 0.05, 0.04, 0.03],
            }
        )

        result = DecayAnalysisResult(
            decay_curve=decay_curve,
            half_life=10.0,
            decay_rate=0.05,
            is_persistent=True,
        )

        assert result.half_life == 10.0
        assert result.decay_rate == 0.05
        assert result.is_persistent is True


class TestAlphaAnalytics:
    """Tests for AlphaAnalytics class."""

    @pytest.fixture()
    def analytics(self):
        """Create analytics instance."""
        return AlphaAnalytics()

    @pytest.fixture()
    def sample_data(self):
        """Create sample signal and returns data."""
        n_stocks = 100

        signal = pl.DataFrame(
            {
                "permno": list(range(n_stocks)),
                "date": [date(2024, 1, 1)] * n_stocks,
                "signal": [float(i) for i in range(n_stocks)],  # Higher permno = higher signal
            }
        )

        returns = pl.DataFrame(
            {
                "permno": list(range(n_stocks)),
                "date": [date(2024, 1, 1)] * n_stocks,
                "return": [i / 1000 for i in range(n_stocks)],  # Proportional returns
            }
        )

        return signal, returns

    def test_compute_quintile_returns(self, analytics, sample_data):
        """Test quintile returns computation."""
        signal, returns = sample_data

        quintile_returns = analytics.compute_quintile_returns(signal, returns, n_quintiles=5)

        assert quintile_returns.height == 5
        assert "quintile" in quintile_returns.columns
        assert "mean_return" in quintile_returns.columns
        assert "n_stocks" in quintile_returns.columns

        # Returns should be monotonically increasing with quintile
        sorted_qr = quintile_returns.sort("quintile")
        means = sorted_qr.get_column("mean_return").to_list()

        for i in range(len(means) - 1):
            assert means[i + 1] > means[i], "Returns should increase with quintile"

    def test_compute_quintile_returns_empty(self, analytics):
        """Test quintile returns with empty data."""
        empty_signal = pl.DataFrame(
            schema={"permno": pl.Int64, "date": pl.Date, "signal": pl.Float64}
        )
        empty_returns = pl.DataFrame(
            schema={"permno": pl.Int64, "date": pl.Date, "return": pl.Float64}
        )

        result = analytics.compute_quintile_returns(empty_signal, empty_returns)
        assert result.height == 0

    def test_check_monotonicity_true(self, analytics, sample_data):
        """Test monotonicity check with monotonic data."""
        signal, returns = sample_data
        quintile_returns = analytics.compute_quintile_returns(signal, returns)

        is_mono, corr = analytics.check_monotonicity(quintile_returns)

        assert is_mono is True
        assert corr > 0.9  # Should be close to 1

    def test_check_monotonicity_false(self, analytics):
        """Test monotonicity check with non-monotonic data."""
        # Create non-monotonic quintile returns
        quintile_returns = pl.DataFrame(
            {
                "quintile": [1, 2, 3, 4, 5],
                "mean_return": [0.01, 0.03, 0.02, 0.04, 0.05],  # Q2 > Q3, not monotonic
                "n_stocks": [20, 20, 20, 20, 20],
            }
        )

        is_mono, corr = analytics.check_monotonicity(quintile_returns)

        assert is_mono is False

    def test_check_monotonicity_short_data(self, analytics):
        """Test monotonicity check with insufficient data."""
        quintile_returns = pl.DataFrame(
            {
                "quintile": [1],
                "mean_return": [0.01],
                "n_stocks": [100],
            }
        )

        is_mono, corr = analytics.check_monotonicity(quintile_returns)

        assert is_mono is False
        assert math.isnan(corr)

    def test_analyze_by_market_cap(self, analytics):
        """Test analysis by market cap quintile."""
        n_stocks = 100

        signal = pl.DataFrame(
            {
                "permno": list(range(n_stocks)),
                "date": [date(2024, 1, 1)] * n_stocks,
                "signal": [float(i) for i in range(n_stocks)],
            }
        )

        returns = pl.DataFrame(
            {
                "permno": list(range(n_stocks)),
                "date": [date(2024, 1, 1)] * n_stocks,
                "return": [i / 1000 for i in range(n_stocks)],
            }
        )

        market_caps = pl.DataFrame(
            {
                "permno": list(range(n_stocks)),
                "date": [date(2024, 1, 1)] * n_stocks,
                "market_cap": [float(i * 1000) for i in range(n_stocks)],
            }
        )

        result = analytics.analyze_by_market_cap(signal, returns, market_caps, n_quintiles=5)

        assert isinstance(result, GroupedICResult)
        assert result.by_group.height <= 5

    def test_analyze_by_market_cap_empty(self, analytics):
        """Test market cap analysis with empty data."""
        empty = pl.DataFrame(schema={"permno": pl.Int64, "date": pl.Date, "signal": pl.Float64})
        empty_ret = pl.DataFrame(schema={"permno": pl.Int64, "date": pl.Date, "return": pl.Float64})
        empty_mc = pl.DataFrame(
            schema={"permno": pl.Int64, "date": pl.Date, "market_cap": pl.Float64}
        )

        result = analytics.analyze_by_market_cap(empty, empty_ret, empty_mc)

        assert result.by_group.height == 0
        assert math.isnan(result.overall_ic)

    def test_analyze_decay(self, analytics):
        """Test decay curve analysis."""
        n_stocks = 100

        signal = pl.DataFrame(
            {
                "permno": list(range(n_stocks)),
                "date": [date(2024, 1, 1)] * n_stocks,
                "signal": [float(i) for i in range(n_stocks)],
            }
        )

        # Create returns at different horizons with decaying correlation
        returns_by_horizon = {}
        for horizon in [1, 5, 10, 20]:
            # Decay factor: longer horizon = weaker correlation
            decay = 1.0 - (horizon / 100)
            returns_by_horizon[horizon] = pl.DataFrame(
                {
                    "permno": list(range(n_stocks)),
                    "date": [date(2024, 1, 1)] * n_stocks,
                    "return": [i / 1000 * decay for i in range(n_stocks)],
                }
            )

        result = analytics.analyze_decay(signal, returns_by_horizon)

        assert isinstance(result, DecayAnalysisResult)
        assert result.decay_curve.height > 0

    def test_analyze_decay_insufficient_data(self, analytics):
        """Test decay analysis with insufficient data."""
        signal = pl.DataFrame(
            {
                "permno": [1],
                "date": [date(2024, 1, 1)],
                "signal": [1.0],
            }
        )

        returns_by_horizon = {
            1: pl.DataFrame(
                {
                    "permno": [1],
                    "date": [date(2024, 1, 1)],
                    "return": [0.01],
                }
            )
        }

        result = analytics.analyze_decay(signal, returns_by_horizon)

        # Should handle gracefully
        assert result.half_life is None
        assert result.decay_rate is None

    def test_estimate_decay_rate(self, analytics):
        """Test decay rate estimation."""
        # Create decay curve with exponential decay
        decay_curve = pl.DataFrame(
            {
                "horizon": [1, 5, 10, 20, 40],
                "rank_ic": [0.08, 0.06, 0.04, 0.02, 0.01],  # Decaying IC
            }
        )

        rate = analytics._estimate_decay_rate(decay_curve)

        assert rate is not None
        assert rate > 0  # Should detect positive decay

    def test_estimate_decay_rate_insufficient(self, analytics):
        """Test decay rate with insufficient valid points."""
        decay_curve = pl.DataFrame(
            {
                "horizon": [1],
                "rank_ic": [0.05],
            }
        )

        rate = analytics._estimate_decay_rate(decay_curve)
        assert rate is None

    def test_estimate_decay_rate_with_negatives(self, analytics):
        """Test decay rate handles negative ICs."""
        decay_curve = pl.DataFrame(
            {
                "horizon": [1, 5, 10],
                "rank_ic": [0.05, -0.01, -0.02],  # IC turns negative
            }
        )

        rate = analytics._estimate_decay_rate(decay_curve)

        # Should only use positive ICs (1 point), so returns None
        assert rate is None
