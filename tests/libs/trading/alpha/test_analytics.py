"""Tests for alpha analytics extensions."""

import math
from datetime import date
from unittest.mock import Mock

import polars as pl
import pytest

from libs.trading.alpha.analytics import (
    AlphaAnalytics,
    DecayAnalysisResult,
    GroupedICResult,
)
from libs.trading.alpha.metrics import AlphaMetricsAdapter


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

    def test_creation_with_empty_groups(self):
        """Test creating GroupedICResult with empty high/low groups."""
        by_group = pl.DataFrame(
            {
                "gics_sector": ["Tech"],
                "rank_ic": [0.05],
                "n_stocks": [100],
            }
        )

        result = GroupedICResult(
            by_group=by_group, overall_ic=0.05, high_ic_groups=[], low_ic_groups=[]
        )

        assert len(result.high_ic_groups) == 0
        assert len(result.low_ic_groups) == 0

    def test_nan_overall_ic(self):
        """Test GroupedICResult with NaN overall IC."""
        by_group = pl.DataFrame(
            schema={
                "gics_sector": pl.Utf8,
                "rank_ic": pl.Float64,
                "n_stocks": pl.Int64,
            }
        )

        result = GroupedICResult(
            by_group=by_group,
            overall_ic=float("nan"),
            high_ic_groups=[],
            low_ic_groups=[],
        )

        assert math.isnan(result.overall_ic)


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

    def test_creation_with_none_values(self):
        """Test creating DecayAnalysisResult with None values."""
        decay_curve = pl.DataFrame(
            {
                "horizon": [1],
                "ic": [0.05],
                "rank_ic": [0.06],
            }
        )

        result = DecayAnalysisResult(
            decay_curve=decay_curve,
            half_life=None,
            decay_rate=None,
            is_persistent=False,
        )

        assert result.half_life is None
        assert result.decay_rate is None
        assert result.is_persistent is False


class TestAlphaAnalytics:
    """Tests for AlphaAnalytics class."""

    @pytest.fixture()
    def analytics(self):
        """Create analytics instance."""
        return AlphaAnalytics()

    @pytest.fixture()
    def mock_metrics(self):
        """Create mock metrics adapter."""
        return Mock(spec=AlphaMetricsAdapter)

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

    # ===== Initialization Tests =====

    def test_init_with_default_adapter(self):
        """Test initialization with default metrics adapter."""
        analytics = AlphaAnalytics()
        assert analytics._metrics is not None
        assert isinstance(analytics._metrics, AlphaMetricsAdapter)

    def test_init_with_custom_adapter(self):
        """Test initialization with custom metrics adapter."""
        custom_adapter = Mock(spec=AlphaMetricsAdapter)
        analytics = AlphaAnalytics(metrics_adapter=custom_adapter)
        assert analytics._metrics is custom_adapter

    # ===== analyze_by_sector Tests =====

    def test_analyze_by_sector_basic(self, analytics):
        """Test sector analysis with valid data."""
        n_stocks = 150

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

        sector_mapping = pl.DataFrame(
            {
                "permno": list(range(n_stocks)),
                "date": [date(2024, 1, 1)] * n_stocks,
                "gics_sector": (
                    ["Tech"] * 50 + ["Finance"] * 50 + ["Healthcare"] * 50
                ),
            }
        )

        result = analytics.analyze_by_sector(signal, returns, sector_mapping)

        assert isinstance(result, GroupedICResult)
        assert result.by_group.height > 0
        assert "gics_sector" in result.by_group.columns
        assert not math.isnan(result.overall_ic)

    def test_analyze_by_sector_empty_grouped_ic(self, analytics):
        """Test sector analysis when grouped IC is empty."""
        signal = pl.DataFrame(
            {"permno": [1, 2], "date": [date(2024, 1, 1)] * 2, "signal": [1.0, 2.0]}
        )
        returns = pl.DataFrame(
            {"permno": [1, 2], "date": [date(2024, 1, 1)] * 2, "return": [0.01, 0.02]}
        )
        sector_mapping = pl.DataFrame(
            {"permno": [1, 2], "date": [date(2024, 1, 1)] * 2, "gics_sector": ["Tech", "Finance"]}
        )

        result = analytics.analyze_by_sector(signal, returns, sector_mapping)

        # Should return result with empty groups and NaN overall_ic
        assert result.by_group.height == 0
        assert math.isnan(result.overall_ic)
        assert result.high_ic_groups == []
        assert result.low_ic_groups == []

    def test_analyze_by_sector_high_and_low_groups(self, analytics):
        """Test identification of high and low IC sectors."""
        # Create data with varying IC per sector:
        # - Tech: positive correlation (high IC)
        # - Finance: negative correlation (low IC)
        # - Healthcare: moderate correlation (close to overall)
        n_per_sector = 50
        n_stocks = n_per_sector * 3

        # Tech: signal = i, return = i (positive correlation)
        tech_signals = [float(i) for i in range(n_per_sector)]
        tech_returns = [i / 1000 for i in range(n_per_sector)]

        # Finance: signal = i, return = -i (negative correlation)
        finance_signals = [float(i) for i in range(n_per_sector)]
        finance_returns = [-(i / 1000) for i in range(n_per_sector)]

        # Healthcare: signal = i, return = i*0.5 (moderate positive correlation)
        health_signals = [float(i) for i in range(n_per_sector)]
        health_returns = [i * 0.5 / 1000 for i in range(n_per_sector)]

        signal = pl.DataFrame(
            {
                "permno": list(range(n_stocks)),
                "date": [date(2024, 1, 1)] * n_stocks,
                "signal": tech_signals + finance_signals + health_signals,
            }
        )

        returns = pl.DataFrame(
            {
                "permno": list(range(n_stocks)),
                "date": [date(2024, 1, 1)] * n_stocks,
                "return": tech_returns + finance_returns + health_returns,
            }
        )

        sector_mapping = pl.DataFrame(
            {
                "permno": list(range(n_stocks)),
                "date": [date(2024, 1, 1)] * n_stocks,
                "gics_sector": (
                    ["Tech"] * n_per_sector
                    + ["Finance"] * n_per_sector
                    + ["Healthcare"] * n_per_sector
                ),
            }
        )

        result = analytics.analyze_by_sector(signal, returns, sector_mapping)

        # Should have high IC (Tech) and/or low IC (Finance) groups
        assert len(result.high_ic_groups) > 0 or len(result.low_ic_groups) > 0

    # ===== analyze_by_market_cap Tests =====

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

    def test_analyze_by_market_cap_custom_quintiles(self, analytics):
        """Test market cap analysis with custom number of quintiles."""
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

        result = analytics.analyze_by_market_cap(signal, returns, market_caps, n_quintiles=3)

        assert result.by_group.height <= 3

    def test_analyze_by_market_cap_quintile_sorting(self, analytics):
        """Test that market cap quintile results are sorted."""
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

        # Check that gics_sector (which contains quintile numbers as strings) is sorted
        sectors = result.by_group.get_column("gics_sector").to_list()
        assert sectors == sorted(sectors)

    # ===== analyze_decay Tests =====

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

    def test_analyze_decay_custom_horizons(self, analytics):
        """Test decay analysis with custom horizons."""
        n_stocks = 100

        signal = pl.DataFrame(
            {
                "permno": list(range(n_stocks)),
                "date": [date(2024, 1, 1)] * n_stocks,
                "signal": [float(i) for i in range(n_stocks)],
            }
        )

        custom_horizons = [3, 7, 14, 30]
        returns_by_horizon = {}
        for horizon in custom_horizons:
            decay = 1.0 - (horizon / 100)
            returns_by_horizon[horizon] = pl.DataFrame(
                {
                    "permno": list(range(n_stocks)),
                    "date": [date(2024, 1, 1)] * n_stocks,
                    "return": [i / 1000 * decay for i in range(n_stocks)],
                }
            )

        result = analytics.analyze_decay(signal, returns_by_horizon, horizons=custom_horizons)

        assert result.decay_curve.height == len(custom_horizons)

    def test_analyze_decay_persistence_positive(self, analytics):
        """Test decay analysis detects persistence when IC stays positive."""
        n_stocks = 100

        signal = pl.DataFrame(
            {
                "permno": list(range(n_stocks)),
                "date": [date(2024, 1, 1)] * n_stocks,
                "signal": [float(i) for i in range(n_stocks)],
            }
        )

        # All horizons have positive correlation
        returns_by_horizon = {}
        for horizon in [1, 5, 10, 20, 60]:
            decay = 0.8 - (horizon / 200)  # Still positive at horizon 60
            returns_by_horizon[horizon] = pl.DataFrame(
                {
                    "permno": list(range(n_stocks)),
                    "date": [date(2024, 1, 1)] * n_stocks,
                    "return": [i / 1000 * decay for i in range(n_stocks)],
                }
            )

        result = analytics.analyze_decay(signal, returns_by_horizon)

        assert result.is_persistent is True

    def test_analyze_decay_persistence_negative(self, analytics):
        """Test decay analysis detects non-persistence when IC turns negative."""
        n_stocks = 100

        signal = pl.DataFrame(
            {
                "permno": list(range(n_stocks)),
                "date": [date(2024, 1, 1)] * n_stocks,
                "signal": [float(i) for i in range(n_stocks)],
            }
        )

        # Long horizons have negative correlation
        returns_by_horizon = {}
        for horizon in [1, 5, 10, 20, 60]:
            decay = 1.0 - (horizon / 30)  # Turns negative at longer horizons
            returns_by_horizon[horizon] = pl.DataFrame(
                {
                    "permno": list(range(n_stocks)),
                    "date": [date(2024, 1, 1)] * n_stocks,
                    "return": [i / 1000 * decay for i in range(n_stocks)],
                }
            )

        result = analytics.analyze_decay(signal, returns_by_horizon)

        assert result.is_persistent is False

    def test_analyze_decay_none_last_ic(self, analytics):
        """Test decay analysis handles None last IC gracefully."""
        n_stocks = 100

        signal = pl.DataFrame(
            {
                "permno": list(range(n_stocks)),
                "date": [date(2024, 1, 1)] * n_stocks,
                "signal": [float(i) for i in range(n_stocks)],
            }
        )

        returns_by_horizon = {
            1: pl.DataFrame(
                {
                    "permno": list(range(n_stocks)),
                    "date": [date(2024, 1, 1)] * n_stocks,
                    "return": [i / 1000 for i in range(n_stocks)],
                }
            ),
            5: pl.DataFrame(
                {
                    "permno": list(range(n_stocks)),
                    "date": [date(2024, 1, 1)] * n_stocks,
                    "return": [i / 1000 * 0.8 for i in range(n_stocks)],
                }
            ),
        }

        result = analytics.analyze_decay(signal, returns_by_horizon)

        # Should have valid is_persistent value (True or False, not None)
        assert isinstance(result.is_persistent, bool)

    # ===== _estimate_decay_rate Tests =====

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

    def test_estimate_decay_rate_with_nans(self, analytics):
        """Test decay rate filters out NaN values."""
        decay_curve = pl.DataFrame(
            {
                "horizon": [1, 5, 10, 20, 40],
                "rank_ic": [0.08, float("nan"), 0.04, 0.02, 0.01],
            }
        )

        rate = analytics._estimate_decay_rate(decay_curve)

        # Should filter NaN and compute on valid positive values
        assert rate is not None
        assert rate > 0

    def test_estimate_decay_rate_zero_denominator(self, analytics):
        """Test decay rate handles zero denominator in regression."""
        # All horizons are the same (constant x)
        decay_curve = pl.DataFrame(
            {
                "horizon": [5, 5, 5, 5],
                "rank_ic": [0.08, 0.06, 0.04, 0.02],
            }
        )

        rate = analytics._estimate_decay_rate(decay_curve)

        # Should return None due to zero denominator
        assert rate is None

    def test_estimate_decay_rate_positive_slope(self, analytics):
        """Test decay rate returns 0 when slope is positive (IC increasing)."""
        decay_curve = pl.DataFrame(
            {
                "horizon": [1, 5, 10, 20],
                "rank_ic": [0.02, 0.04, 0.06, 0.08],  # Increasing IC
            }
        )

        rate = analytics._estimate_decay_rate(decay_curve)

        # Should return 0.0 for positive slope
        assert rate == 0.0

    # ===== compute_quintile_returns Tests =====

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

    def test_compute_quintile_returns_custom_quintiles(self, analytics, sample_data):
        """Test quintile returns with custom number of quintiles."""
        signal, returns = sample_data

        quintile_returns = analytics.compute_quintile_returns(signal, returns, n_quintiles=10)

        assert quintile_returns.height == 10

    def test_compute_quintile_returns_with_nulls(self, analytics):
        """Test quintile returns handles null values."""
        n_stocks = 100

        signal = pl.DataFrame(
            {
                "permno": list(range(n_stocks)),
                "date": [date(2024, 1, 1)] * n_stocks,
                "signal": [float(i) if i % 10 != 0 else None for i in range(n_stocks)],
            }
        )

        returns = pl.DataFrame(
            {
                "permno": list(range(n_stocks)),
                "date": [date(2024, 1, 1)] * n_stocks,
                "return": [i / 1000 for i in range(n_stocks)],
            }
        )

        result = analytics.compute_quintile_returns(signal, returns)

        # Should handle nulls and still produce results
        assert result.height > 0

    # ===== check_monotonicity Tests =====

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

    def test_check_monotonicity_decreasing(self, analytics):
        """Test monotonicity check detects decreasing pattern."""
        quintile_returns = pl.DataFrame(
            {
                "quintile": [1, 2, 3, 4, 5],
                "mean_return": [0.05, 0.04, 0.03, 0.02, 0.01],  # Strictly decreasing
                "n_stocks": [20, 20, 20, 20, 20],
            }
        )

        is_mono, corr = analytics.check_monotonicity(quintile_returns)

        assert is_mono is True  # Monotonic (decreasing)
        assert corr < 0  # Negative correlation

    def test_check_monotonicity_with_equal_values(self, analytics):
        """Test monotonicity check with equal consecutive values."""
        quintile_returns = pl.DataFrame(
            {
                "quintile": [1, 2, 3, 4, 5],
                "mean_return": [0.01, 0.02, 0.02, 0.03, 0.04],  # Q2 == Q3
                "n_stocks": [20, 20, 20, 20, 20],
            }
        )

        is_mono, corr = analytics.check_monotonicity(quintile_returns)

        # Not strictly monotonic (has equal values)
        assert is_mono is False

    # ===== Edge Cases and Error Handling =====

    def test_analyze_by_sector_no_overlap(self, analytics):
        """Test sector analysis when signal and returns have no overlap."""
        signal = pl.DataFrame(
            {"permno": [1, 2, 3], "date": [date(2024, 1, 1)] * 3, "signal": [1.0, 2.0, 3.0]}
        )
        returns = pl.DataFrame(
            {"permno": [4, 5, 6], "date": [date(2024, 1, 1)] * 3, "return": [0.01, 0.02, 0.03]}
        )
        sector_mapping = pl.DataFrame(
            {"permno": [1, 2, 3], "date": [date(2024, 1, 1)] * 3, "gics_sector": ["Tech"] * 3}
        )

        result = analytics.analyze_by_sector(signal, returns, sector_mapping)

        assert result.by_group.height == 0
        assert math.isnan(result.overall_ic)

    def test_analyze_by_market_cap_no_overlap(self, analytics):
        """Test market cap analysis when signal and returns have no overlap."""
        signal = pl.DataFrame(
            {"permno": [1, 2, 3], "date": [date(2024, 1, 1)] * 3, "signal": [1.0, 2.0, 3.0]}
        )
        returns = pl.DataFrame(
            {"permno": [4, 5, 6], "date": [date(2024, 1, 1)] * 3, "return": [0.01, 0.02, 0.03]}
        )
        market_caps = pl.DataFrame(
            {"permno": [1, 2, 3], "date": [date(2024, 1, 1)] * 3, "market_cap": [1e6, 2e6, 3e6]}
        )

        result = analytics.analyze_by_market_cap(signal, returns, market_caps)

        assert result.by_group.height == 0
        assert math.isnan(result.overall_ic)

    def test_compute_quintile_returns_no_overlap(self, analytics):
        """Test quintile returns when signal and returns have no overlap."""
        signal = pl.DataFrame(
            {"permno": [1, 2, 3], "date": [date(2024, 1, 1)] * 3, "signal": [1.0, 2.0, 3.0]}
        )
        returns = pl.DataFrame(
            {"permno": [4, 5, 6], "date": [date(2024, 1, 1)] * 3, "return": [0.01, 0.02, 0.03]}
        )

        result = analytics.compute_quintile_returns(signal, returns)

        assert result.height == 0
