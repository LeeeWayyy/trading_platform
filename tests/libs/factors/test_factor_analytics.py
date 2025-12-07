"""
Tests for FactorAnalytics.
"""

from datetime import date

import numpy as np
import polars as pl
import pytest

from libs.factors import FactorAnalytics, ICAnalysis


@pytest.fixture
def factor_analytics() -> FactorAnalytics:
    """Fixture for FactorAnalytics instance."""
    return FactorAnalytics()


@pytest.fixture
def sample_exposures() -> pl.DataFrame:
    """Sample factor exposures for testing."""
    np.random.seed(42)

    data = []
    dates = [date(2023, 1, d) for d in range(1, 31)]
    permnos = list(range(100, 150))

    for dt in dates:
        for permno in permnos:
            data.append({
                "date": dt,
                "permno": permno,
                "factor_name": "momentum_12_1",
                "zscore": np.random.normal(0, 1),
            })
            data.append({
                "date": dt,
                "permno": permno,
                "factor_name": "log_market_cap",
                "zscore": np.random.normal(0, 1),
            })

    return pl.DataFrame(data)


@pytest.fixture
def sample_returns() -> pl.DataFrame:
    """Sample return data for testing."""
    np.random.seed(42)

    data = []
    dates = [date(2023, 1, d) for d in range(1, 31)]
    permnos = list(range(100, 150))

    for dt in dates:
        for permno in permnos:
            data.append({
                "date": dt,
                "permno": permno,
                "ret": np.random.normal(0.0005, 0.02),
                "ret_1d": np.random.normal(0.0005, 0.02),
                "ret_5d": np.random.normal(0.0025, 0.04),
                "ret_20d": np.random.normal(0.01, 0.08),
            })

    return pl.DataFrame(data)


class TestICAnalysis:
    """Tests for ICAnalysis dataclass."""

    def test_ic_analysis_fields(self):
        """ICAnalysis has all required fields."""
        analysis = ICAnalysis(
            factor_name="test",
            ic_mean=0.05,
            ic_std=0.03,
            icir=1.67,
            t_statistic=2.5,
            hit_rate=0.6,
            n_periods=100,
        )

        assert analysis.factor_name == "test"
        assert analysis.ic_mean == 0.05
        assert analysis.ic_std == 0.03
        assert analysis.icir == 1.67
        assert analysis.t_statistic == 2.5
        assert analysis.hit_rate == 0.6
        assert analysis.n_periods == 100


class TestComputeIC:
    """Tests for FactorAnalytics.compute_ic()."""

    def test_compute_ic_returns_dict(
        self,
        factor_analytics: FactorAnalytics,
        sample_exposures: pl.DataFrame,
        sample_returns: pl.DataFrame,
    ):
        """compute_ic returns dictionary of results."""
        result = factor_analytics.compute_ic(
            sample_exposures,
            sample_returns,
            horizons=[1, 5],
        )

        assert isinstance(result, dict)
        assert "momentum_12_1" in result
        assert "log_market_cap" in result

    def test_compute_ic_has_horizons(
        self,
        factor_analytics: FactorAnalytics,
        sample_exposures: pl.DataFrame,
        sample_returns: pl.DataFrame,
    ):
        """compute_ic returns results for each horizon."""
        result = factor_analytics.compute_ic(
            sample_exposures,
            sample_returns,
            horizons=[1, 5],
        )

        for factor_name, horizons in result.items():
            assert 1 in horizons or 5 in horizons

    def test_compute_ic_returns_icanalysis(
        self,
        factor_analytics: FactorAnalytics,
        sample_exposures: pl.DataFrame,
        sample_returns: pl.DataFrame,
    ):
        """compute_ic returns ICAnalysis objects."""
        result = factor_analytics.compute_ic(
            sample_exposures,
            sample_returns,
            horizons=[1],
        )

        for factor_name, horizons in result.items():
            for horizon, analysis in horizons.items():
                assert isinstance(analysis, ICAnalysis)
                assert analysis.factor_name == factor_name
                assert analysis.n_periods > 0


class TestAnalyzeDecay:
    """Tests for FactorAnalytics.analyze_decay()."""

    def test_analyze_decay_returns_dataframe(
        self,
        factor_analytics: FactorAnalytics,
        sample_exposures: pl.DataFrame,
        sample_returns: pl.DataFrame,
    ):
        """analyze_decay returns a DataFrame."""
        result = factor_analytics.analyze_decay(
            sample_exposures,
            sample_returns,
            max_horizon=20,
        )

        assert isinstance(result, pl.DataFrame)
        assert "factor_name" in result.columns
        assert "horizon" in result.columns
        assert "ic_mean" in result.columns

    def test_analyze_decay_multiple_horizons(
        self,
        factor_analytics: FactorAnalytics,
        sample_exposures: pl.DataFrame,
        sample_returns: pl.DataFrame,
    ):
        """analyze_decay computes multiple horizons."""
        result = factor_analytics.analyze_decay(
            sample_exposures,
            sample_returns,
            max_horizon=20,
        )

        # Should have multiple horizon values
        horizons = result["horizon"].unique().to_list()
        assert len(horizons) >= 2


class TestComputeTurnover:
    """Tests for FactorAnalytics.compute_turnover()."""

    def test_compute_turnover_returns_dataframe(
        self,
        factor_analytics: FactorAnalytics,
        sample_exposures: pl.DataFrame,
    ):
        """compute_turnover returns a DataFrame."""
        result = factor_analytics.compute_turnover(sample_exposures)

        assert isinstance(result, pl.DataFrame)
        assert "factor_name" in result.columns
        assert "date" in result.columns
        assert "turnover" in result.columns

    def test_compute_turnover_values_bounded(
        self,
        factor_analytics: FactorAnalytics,
        sample_exposures: pl.DataFrame,
    ):
        """Turnover values are between 0 and 2."""
        result = factor_analytics.compute_turnover(sample_exposures)

        if result.height > 0:
            turnovers = result["turnover"].to_numpy()
            assert np.all(turnovers >= 0)
            assert np.all(turnovers <= 2)  # 1 - (-1) = 2 is max


class TestComputeCorrelationMatrix:
    """Tests for FactorAnalytics.compute_correlation_matrix()."""

    def test_correlation_matrix_returns_dataframe(
        self,
        factor_analytics: FactorAnalytics,
        sample_exposures: pl.DataFrame,
    ):
        """compute_correlation_matrix returns a DataFrame."""
        result = factor_analytics.compute_correlation_matrix(sample_exposures)

        assert isinstance(result, pl.DataFrame)
        if result.height > 0:
            assert "factor_name" in result.columns

    def test_correlation_matrix_is_symmetric(
        self,
        factor_analytics: FactorAnalytics,
        sample_exposures: pl.DataFrame,
    ):
        """Correlation matrix should be symmetric."""
        result = factor_analytics.compute_correlation_matrix(sample_exposures)

        if result.height > 0:
            # Check diagonal is 1
            factor_names = result["factor_name"].to_list()
            for name in factor_names:
                if name in result.columns:
                    row = result.filter(pl.col("factor_name") == name)
                    diag = row[name][0]
                    if diag is not None:
                        assert abs(diag - 1.0) < 0.01

    def test_correlation_matrix_for_specific_date(
        self,
        factor_analytics: FactorAnalytics,
        sample_exposures: pl.DataFrame,
    ):
        """compute_correlation_matrix works for specific date."""
        result = factor_analytics.compute_correlation_matrix(
            sample_exposures,
            as_of_date=date(2023, 1, 15),
        )

        assert isinstance(result, pl.DataFrame)


class TestSpearmanCorrelation:
    """Tests for internal Spearman correlation methods."""

    def test_compute_rank_corr_perfect_positive(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """Perfect positive correlation returns 1."""
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y = np.array([2.0, 4.0, 6.0, 8.0, 10.0])

        corr = factor_analytics._compute_rank_corr(x, y)

        assert abs(corr - 1.0) < 0.01

    def test_compute_rank_corr_perfect_negative(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """Perfect negative correlation returns -1."""
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y = np.array([10.0, 8.0, 6.0, 4.0, 2.0])

        corr = factor_analytics._compute_rank_corr(x, y)

        assert abs(corr - (-1.0)) < 0.01

    def test_compute_rank_corr_handles_nan(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """Handles NaN values gracefully."""
        x = np.array([1.0, 2.0, np.nan, 4.0, 5.0])
        y = np.array([2.0, 4.0, 6.0, np.nan, 10.0])

        corr = factor_analytics._compute_rank_corr(x, y)

        # Should return a valid number (computed on non-NaN pairs)
        assert not np.isnan(corr)

    def test_compute_rank_corr_insufficient_data(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """Returns 0 for insufficient data."""
        x = np.array([1.0, np.nan])
        y = np.array([np.nan, 2.0])

        corr = factor_analytics._compute_rank_corr(x, y)

        assert corr == 0.0


class TestForwardReturns:
    """Tests for forward return computation."""

    def test_compute_forward_returns(
        self,
        factor_analytics: FactorAnalytics,
        sample_returns: pl.DataFrame,
    ):
        """_compute_forward_returns creates forward_ret column."""
        result = factor_analytics._compute_forward_returns(sample_returns, horizon=5)

        assert "forward_ret" in result.columns
        # Forward returns should exist for most rows (except last 5 days)
        assert result.height > 0

    def test_forward_returns_pit_correctness(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """Forward returns only use FUTURE data (no look-ahead)."""
        # Create simple test data with known returns
        returns_data = pl.DataFrame({
            "date": [date(2023, 1, i) for i in range(1, 11)],
            "permno": [100] * 10,
            "ret": [0.01, 0.02, 0.03, 0.04, 0.05, -0.01, -0.02, -0.03, -0.04, -0.05],
        })

        result = factor_analytics._compute_forward_returns(returns_data, horizon=3)

        # For day 1 (index 0), forward return should be product of days 2,3,4
        # (1 + 0.02) * (1 + 0.03) * (1 + 0.04) - 1 = 0.0926...
        if result.height > 0:
            first_forward = result.filter(pl.col("date") == date(2023, 1, 1))
            if first_forward.height > 0:
                expected = (1.02 * 1.03 * 1.04) - 1
                actual = first_forward["forward_ret"][0]
                assert abs(actual - expected) < 0.001

    def test_forward_returns_excludes_incomplete_windows(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """Last rows without full forward window are excluded."""
        returns_data = pl.DataFrame({
            "date": [date(2023, 1, i) for i in range(1, 6)],
            "permno": [100] * 5,
            "ret": [0.01, 0.02, 0.03, 0.04, 0.05],
        })

        result = factor_analytics._compute_forward_returns(returns_data, horizon=3)

        # With 5 days and horizon 3, only first 2 rows should have valid forward returns
        # (day 1 uses days 2,3,4; day 2 uses days 3,4,5)
        assert result.height <= 2

    def test_compute_horizon_returns_handles_nan(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """Horizon returns handle NaN in return series."""
        returns = pl.Series([0.01, np.nan, 0.03, 0.04, 0.05])

        result = factor_analytics._compute_horizon_returns(returns, horizon=2)

        # First element should be NaN because day 2 is NaN
        assert np.isnan(result[0])
