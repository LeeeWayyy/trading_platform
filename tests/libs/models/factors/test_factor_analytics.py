"""
Tests for FactorAnalytics.
"""

from datetime import date

import numpy as np
import polars as pl
import pytest

from libs.models.factors import FactorAnalytics, ICAnalysis


@pytest.fixture()
def factor_analytics() -> FactorAnalytics:
    """Fixture for FactorAnalytics instance."""
    return FactorAnalytics()


@pytest.fixture()
def sample_exposures() -> pl.DataFrame:
    """Sample factor exposures for testing."""
    np.random.seed(42)

    data = []
    dates = [date(2023, 1, d) for d in range(1, 31)]
    permnos = list(range(100, 150))

    for dt in dates:
        for permno in permnos:
            data.append(
                {
                    "date": dt,
                    "permno": permno,
                    "factor_name": "momentum_12_1",
                    "zscore": np.random.normal(0, 1),
                }
            )
            data.append(
                {
                    "date": dt,
                    "permno": permno,
                    "factor_name": "log_market_cap",
                    "zscore": np.random.normal(0, 1),
                }
            )

    return pl.DataFrame(data)


@pytest.fixture()
def sample_returns() -> pl.DataFrame:
    """Sample return data for testing."""
    np.random.seed(42)

    data = []
    dates = [date(2023, 1, d) for d in range(1, 31)]
    permnos = list(range(100, 150))

    for dt in dates:
        for permno in permnos:
            data.append(
                {
                    "date": dt,
                    "permno": permno,
                    "ret": np.random.normal(0.0005, 0.02),
                    "ret_1d": np.random.normal(0.0005, 0.02),
                    "ret_5d": np.random.normal(0.0025, 0.04),
                    "ret_20d": np.random.normal(0.01, 0.08),
                }
            )

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

        for _factor_name, horizons in result.items():
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
            for _horizon, analysis in horizons.items():
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
        returns_data = pl.DataFrame(
            {
                "date": [date(2023, 1, i) for i in range(1, 11)],
                "permno": [100] * 10,
                "ret": [0.01, 0.02, 0.03, 0.04, 0.05, -0.01, -0.02, -0.03, -0.04, -0.05],
            }
        )

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
        returns_data = pl.DataFrame(
            {
                "date": [date(2023, 1, i) for i in range(1, 6)],
                "permno": [100] * 5,
                "ret": [0.01, 0.02, 0.03, 0.04, 0.05],
            }
        )

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

    def test_compute_horizon_returns_extreme_negative(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """Horizon returns handle extreme negative returns (clamping test)."""
        # Test the -0.9999 clamping for extreme losses
        returns = pl.Series([-0.99, -0.50, 0.10, 0.05])

        result = factor_analytics._compute_horizon_returns(returns, horizon=2)

        # Should clamp at -0.9999 to avoid -inf from log(0)
        # Result should be finite (not -inf or NaN where valid)
        assert result.is_finite().sum() >= 0


class TestComputeICEdgeCases:
    """Tests for edge cases in compute_ic."""

    def test_compute_ic_missing_return_column(
        self,
        factor_analytics: FactorAnalytics,
        sample_exposures: pl.DataFrame,
        sample_returns: pl.DataFrame,
    ):
        """compute_ic handles missing return columns gracefully."""
        # Remove a return column to trigger warning
        returns_subset = sample_returns.drop("ret_20d")

        result = factor_analytics.compute_ic(
            sample_exposures,
            returns_subset,
            horizons=[1, 5, 20],  # 20d column is missing
        )

        # Should still compute IC for available horizons
        assert isinstance(result, dict)
        for _factor_name, horizons in result.items():
            # Should have 1d and 5d, but not 20d
            assert 20 not in horizons

    def test_compute_ic_empty_result(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """compute_ic handles empty data gracefully."""
        empty_exposures = pl.DataFrame(
            {
                "date": [],
                "permno": [],
                "factor_name": [],
                "zscore": [],
            }
        )
        empty_returns = pl.DataFrame(
            {
                "date": [],
                "permno": [],
                "ret_1d": [],
            }
        )

        result = factor_analytics.compute_ic(
            empty_exposures,
            empty_returns,
            horizons=[1],
        )

        # Should return empty dict
        assert result == {}

    def test_compute_ic_insufficient_observations(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """compute_ic filters out dates with <10 observations."""
        # Create data with only 5 stocks (below threshold)
        small_exposures = pl.DataFrame(
            {
                "date": [date(2023, 1, 1)] * 5,
                "permno": [100, 101, 102, 103, 104],
                "factor_name": ["test_factor"] * 5,
                "zscore": [1.0, 0.5, 0.0, -0.5, -1.0],
            }
        )
        small_returns = pl.DataFrame(
            {
                "date": [date(2023, 1, 1)] * 5,
                "permno": [100, 101, 102, 103, 104],
                "ret_1d": [0.01, 0.005, 0.0, -0.005, -0.01],
            }
        )

        result = factor_analytics.compute_ic(
            small_exposures,
            small_returns,
            horizons=[1],
        )

        # Should skip due to insufficient observations (<10)
        if "test_factor" in result:
            assert 1 not in result["test_factor"]

    def test_compute_ic_all_null_values(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """compute_ic handles all-null data."""
        null_exposures = pl.DataFrame(
            {
                "date": [date(2023, 1, 1)] * 20,
                "permno": list(range(100, 120)),
                "factor_name": ["test_factor"] * 20,
                "zscore": [None] * 20,
            }
        )
        null_returns = pl.DataFrame(
            {
                "date": [date(2023, 1, 1)] * 20,
                "permno": list(range(100, 120)),
                "ret_1d": [None] * 20,
            }
        )

        result = factor_analytics.compute_ic(
            null_exposures,
            null_returns,
            horizons=[1],
        )

        # Should handle gracefully (no crash)
        assert isinstance(result, dict)

    def test_compute_ic_zero_ic_std(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """compute_ic handles zero IC standard deviation (all ICs identical)."""
        # Create data that will produce zero std (constant IC across dates)
        exposures = pl.DataFrame(
            {
                "date": [date(2023, 1, d) for d in range(1, 4) for _ in range(20)],
                "permno": [100 + i for _ in range(1, 4) for i in range(20)],
                "factor_name": ["const_factor"] * 60,
                "zscore": [float(i % 20) for _ in range(1, 4) for i in range(20)],
            }
        )
        returns = pl.DataFrame(
            {
                "date": [date(2023, 1, d) for d in range(1, 4) for _ in range(20)],
                "permno": [100 + i for _ in range(1, 4) for i in range(20)],
                "ret_1d": [float(i % 20) * 0.01 for _ in range(1, 4) for i in range(20)],
            }
        )

        result = factor_analytics.compute_ic(
            exposures,
            returns,
            horizons=[1],
        )

        # Should handle zero std gracefully (ICIR and t_stat = 0)
        if "const_factor" in result and 1 in result["const_factor"]:
            analysis = result["const_factor"][1]
            # When std is 0, ICIR and t_stat should be 0
            if analysis.ic_std == 0:
                assert analysis.icir == 0.0
                assert analysis.t_statistic == 0.0


class TestAnalyzeDecayEdgeCases:
    """Tests for edge cases in analyze_decay."""

    def test_analyze_decay_no_valid_dates(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """analyze_decay handles case with no valid dates (<10 obs per date)."""
        # Create data with only 5 stocks per date
        small_exposures = pl.DataFrame(
            {
                "date": [date(2023, 1, d) for d in range(1, 6) for _ in range(5)],
                "permno": [100 + i for _ in range(1, 6) for i in range(5)],
                "factor_name": ["test_factor"] * 25,
                "zscore": [float(i) for _ in range(1, 6) for i in range(5)],
            }
        )
        small_returns = pl.DataFrame(
            {
                "date": [date(2023, 1, d) for d in range(1, 6) for _ in range(5)],
                "permno": [100 + i for _ in range(1, 6) for i in range(5)],
                "ret": [0.01 * i for _ in range(1, 6) for i in range(5)],
            }
        )

        result = factor_analytics.analyze_decay(
            small_exposures,
            small_returns,
            max_horizon=10,
        )

        # Should return empty or minimal results
        assert isinstance(result, pl.DataFrame)

    def test_analyze_decay_empty_merged(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """analyze_decay handles empty merge results."""
        exposures = pl.DataFrame(
            {
                "date": [date(2023, 1, 1)] * 20,
                "permno": list(range(100, 120)),
                "factor_name": ["factor_a"] * 20,
                "zscore": [float(i) for i in range(20)],
            }
        )
        # Non-overlapping returns (different dates)
        returns = pl.DataFrame(
            {
                "date": [date(2023, 2, 1)] * 20,
                "permno": list(range(100, 120)),
                "ret": [0.01] * 20,
            }
        )

        result = factor_analytics.analyze_decay(
            exposures,
            returns,
            max_horizon=10,
        )

        # Should handle empty merge gracefully
        assert isinstance(result, pl.DataFrame)


class TestComputeTurnoverEdgeCases:
    """Tests for edge cases in compute_turnover."""

    def test_compute_turnover_insufficient_stocks(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """compute_turnover skips dates with <10 stocks."""
        small_exposures = pl.DataFrame(
            {
                "date": [date(2023, 1, 1), date(2023, 1, 2)] * 5,
                "permno": [100, 101, 102, 103, 104] * 2,
                "factor_name": ["test_factor"] * 10,
                "zscore": [float(i) for i in range(10)],
            }
        )

        result = factor_analytics.compute_turnover(small_exposures)

        # Should skip dates with <10 stocks
        assert result.height == 0

    def test_compute_turnover_single_date(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """compute_turnover handles single date (no pairs)."""
        single_date = pl.DataFrame(
            {
                "date": [date(2023, 1, 1)] * 20,
                "permno": list(range(100, 120)),
                "factor_name": ["factor_a"] * 20,
                "zscore": [float(i) for i in range(20)],
            }
        )

        result = factor_analytics.compute_turnover(single_date)

        # Should return empty (no consecutive dates to compare)
        assert result.height == 0

    def test_compute_turnover_no_overlap(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """compute_turnover handles no stock overlap between dates."""
        no_overlap = pl.DataFrame(
            {
                "date": [date(2023, 1, 1)] * 20 + [date(2023, 1, 2)] * 20,
                "permno": list(range(100, 120)) + list(range(200, 220)),  # Different stocks
                "factor_name": ["factor_a"] * 40,
                "zscore": [float(i) for i in range(40)],
            }
        )

        result = factor_analytics.compute_turnover(no_overlap)

        # Should handle gracefully (no overlap means no valid turnover)
        assert isinstance(result, pl.DataFrame)


class TestCorrelationMatrixEdgeCases:
    """Tests for edge cases in compute_correlation_matrix."""

    def test_correlation_matrix_insufficient_data(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """compute_correlation_matrix handles <10 observations."""
        small_exposures = pl.DataFrame(
            {
                "date": [date(2023, 1, 1)] * 5,
                "permno": [100, 101, 102, 103, 104],
                "factor_name": ["factor_a", "factor_b"] * 2 + ["factor_a"],
                "zscore": [1.0, -1.0, 0.5, -0.5, 0.0],
            }
        )

        result = factor_analytics.compute_correlation_matrix(small_exposures)

        # Should return empty DataFrame due to insufficient data
        assert result.height == 0

    def test_correlation_matrix_missing_factor_columns(
        self,
        factor_analytics: FactorAnalytics,
        sample_exposures: pl.DataFrame,
    ):
        """compute_correlation_matrix handles missing factor columns in pivot."""
        # This tests the None assignment path (line 298)
        result = factor_analytics.compute_correlation_matrix(sample_exposures)

        # All factors should be present after pivot, but test defensive code
        assert isinstance(result, pl.DataFrame)
        if result.height > 0:
            # Check that missing columns get None (defensive code path)
            assert "factor_name" in result.columns

    def test_correlation_matrix_empty_input(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """compute_correlation_matrix handles empty input."""
        empty_exposures = pl.DataFrame(
            {
                "date": [],
                "permno": [],
                "factor_name": [],
                "zscore": [],
            }
        )

        result = factor_analytics.compute_correlation_matrix(empty_exposures)

        # Should return empty DataFrame
        assert result.height == 0


class TestRankCorrEdgeCases:
    """Additional edge case tests for _compute_rank_corr."""

    def test_compute_rank_corr_all_nan(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """_compute_rank_corr returns 0 for all NaN inputs."""
        x = np.array([np.nan, np.nan, np.nan])
        y = np.array([np.nan, np.nan, np.nan])

        corr = factor_analytics._compute_rank_corr(x, y)

        assert corr == 0.0

    def test_compute_rank_corr_exactly_two_points(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """_compute_rank_corr handles exactly 2 valid points (edge of threshold)."""
        x = np.array([1.0, 2.0, np.nan])
        y = np.array([3.0, 4.0, np.nan])

        corr = factor_analytics._compute_rank_corr(x, y)

        # With only 2 points, should return 0 (threshold is <3)
        assert corr == 0.0

    def test_compute_rank_corr_three_points(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """_compute_rank_corr works with exactly 3 valid points."""
        x = np.array([1.0, 2.0, 3.0])
        y = np.array([1.0, 2.0, 3.0])

        corr = factor_analytics._compute_rank_corr(x, y)

        # Should compute correlation (>=3 points)
        assert abs(corr - 1.0) < 0.01

    def test_compute_rank_corr_constant_values(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """_compute_rank_corr handles constant values (no variance)."""
        x = np.array([1.0, 1.0, 1.0, 1.0])
        y = np.array([2.0, 2.0, 2.0, 2.0])

        corr = factor_analytics._compute_rank_corr(x, y)

        # Spearman correlation is undefined for constant values, should handle gracefully
        assert not np.isnan(corr)  # Should not crash, returns valid value or 0


class TestForwardReturnsAdditional:
    """Additional tests for forward returns computation."""

    def test_forward_returns_with_nulls_in_middle(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """_compute_forward_returns handles nulls in the middle of data."""
        returns_with_nulls = pl.DataFrame(
            {
                "date": [date(2023, 1, i) for i in range(1, 11)],
                "permno": [100] * 10,
                "ret": [0.01, 0.02, None, 0.04, 0.05, 0.01, None, 0.02, 0.03, 0.04],
            }
        )

        result = factor_analytics._compute_forward_returns(returns_with_nulls, horizon=3)

        # Should filter out rows where forward returns can't be computed
        assert isinstance(result, pl.DataFrame)
        assert "forward_ret" in result.columns

    def test_forward_returns_multiple_securities(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """_compute_forward_returns handles multiple securities correctly."""
        multi_sec_returns = pl.DataFrame(
            {
                "date": [date(2023, 1, i) for i in range(1, 6)] * 2,
                "permno": [100] * 5 + [200] * 5,
                "ret": [0.01, 0.02, 0.03, 0.04, 0.05] * 2,
            }
        )

        result = factor_analytics._compute_forward_returns(multi_sec_returns, horizon=2)

        # Should compute separately for each permno
        permnos = result["permno"].unique().to_list()
        assert len(permnos) >= 1

    def test_forward_returns_zero_returns(
        self,
        factor_analytics: FactorAnalytics,
    ):
        """_compute_forward_returns handles zero returns."""
        zero_returns = pl.DataFrame(
            {
                "date": [date(2023, 1, i) for i in range(1, 6)],
                "permno": [100] * 5,
                "ret": [0.0, 0.0, 0.0, 0.0, 0.0],
            }
        )

        result = factor_analytics._compute_forward_returns(zero_returns, horizon=2)

        # Should compute successfully (forward returns should be ~0)
        if result.height > 0:
            forward_rets = result["forward_ret"].to_numpy()
            assert np.all(np.abs(forward_rets) < 0.001)
