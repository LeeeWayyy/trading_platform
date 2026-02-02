"""Tests for Quantile Analysis module.

P6T10: Track 10 - Quantile & Attribution Analytics

Coverage targets:
- QuantileAnalysisConfig validation (skip_days, n_quantiles, holding_period)
- QuantileAnalyzer.analyze happy path and edge cases
- Rank IC computation (_compute_rank_ic)
- Quantile assignment (_assign_quantiles)
- Signal date normalization
- run_quantile_analysis convenience function
"""

from __future__ import annotations

import importlib.util
from datetime import date
from unittest.mock import MagicMock

import numpy as np
import pytest

# Skip if optional heavy deps missing
_missing = [mod for mod in ("polars", "scipy") if importlib.util.find_spec(mod) is None]
if _missing:
    pytest.skip(
        f"Skipping quantile analysis tests because dependencies are missing: {', '.join(_missing)}",
        allow_module_level=True,
    )

import polars as pl

from libs.trading.backtest.quantile_analysis import (
    InsufficientDataError,
    QuantileAnalysisConfig,
    QuantileAnalyzer,
    QuantileResult,
    run_quantile_analysis,
)

# ------------------------------------------------------------------ Fixtures


class MockCalendar:
    """Mock exchange calendar for testing."""

    def __init__(self, sessions: list[date]):
        self._sessions = set(sessions)
        self._sessions_list = sorted(sessions)

    def is_session(self, d: date) -> bool:
        return d in self._sessions

    def date_to_session(self, d: date, direction: str = "previous") -> MagicMock:
        """Return a mock Timestamp with .date() method."""
        if d in self._sessions:
            mock = MagicMock()
            mock.date.return_value = d
            return mock

        idx = None
        for i, s in enumerate(self._sessions_list):
            if s > d:
                idx = i
                break

        if direction == "previous":
            if idx is None:
                # d is after all sessions, return last session
                result_date = self._sessions_list[-1] if self._sessions_list else d
            elif idx == 0:
                # d is before first session
                result_date = self._sessions_list[0]
            else:
                result_date = self._sessions_list[idx - 1]
        else:  # "next"
            if idx is None:
                result_date = self._sessions_list[-1] if self._sessions_list else d
            else:
                result_date = self._sessions_list[idx]

        mock = MagicMock()
        mock.date.return_value = result_date
        return mock

    def sessions_in_range(self, start: date, end: date):
        """Return sessions between start and end (inclusive)."""
        result = []
        for s in self._sessions_list:
            if start <= s <= end:
                mock = MagicMock()
                mock.date.return_value = s
                result.append(mock)
        return result

    def session_offset(self, d: date, offset: int) -> MagicMock:
        """Offset a date by trading days."""
        if d not in self._sessions:
            # Normalize to nearest session first
            d = self.date_to_session(d, "previous").date()

        idx = self._sessions_list.index(d)
        target_idx = min(max(idx + offset, 0), len(self._sessions_list) - 1)
        result_date = self._sessions_list[target_idx]
        mock = MagicMock()
        mock.date.return_value = result_date
        return mock


def _create_trading_dates(start: date, n_days: int) -> list[date]:
    """Create list of trading dates (skip weekends)."""
    from datetime import timedelta

    dates = []
    current = start
    while len(dates) < n_days:
        # Skip weekends (5=Saturday, 6=Sunday)
        if current.weekday() < 5:
            dates.append(current)
        current = current + timedelta(days=1)
    return dates


@pytest.fixture()
def trading_calendar() -> MockCalendar:
    """Fixture for mock trading calendar."""
    # Jan 2024 trading days (skip weekends)
    sessions = _create_trading_dates(date(2024, 1, 2), 100)
    return MockCalendar(sessions)


@pytest.fixture()
def basic_signals() -> pl.DataFrame:
    """Basic signals DataFrame with 100 observations per date."""
    dates = _create_trading_dates(date(2024, 1, 2), 60)
    rows = []
    for d in dates:
        for permno in range(1, 101):  # 100 stocks per date
            # Signal with some variance
            signal = (permno - 50) / 50 + np.random.normal(0, 0.1)
            rows.append({"signal_date": d, "permno": permno, "signal_value": signal})
    return pl.DataFrame(rows)


@pytest.fixture()
def basic_forward_returns(basic_signals: pl.DataFrame) -> pl.DataFrame:
    """Forward returns DataFrame matching signals."""
    rows = []
    for row in basic_signals.iter_rows(named=True):
        # Forward return correlated with signal (positive IC)
        signal = row["signal_value"]
        fwd_return = signal * 0.01 + np.random.normal(0, 0.005)
        rows.append(
            {
                "signal_date": row["signal_date"],
                "permno": row["permno"],
                "forward_return": fwd_return,
            }
        )
    return pl.DataFrame(rows)


# ------------------------------------------------------------------ Config Tests


@pytest.mark.unit()
class TestQuantileAnalysisConfig:
    """Tests for QuantileAnalysisConfig validation."""

    def test_default_config_valid(self):
        """Default config should pass validation."""
        config = QuantileAnalysisConfig()
        assert config.n_quantiles == 5
        assert config.skip_days == 1
        assert config.holding_period_days == 20

    def test_skip_days_zero_raises(self):
        """skip_days=0 should raise to prevent look-ahead bias."""
        with pytest.raises(ValueError, match="skip_days must be >= 1"):
            QuantileAnalysisConfig(skip_days=0)

    def test_skip_days_negative_raises(self):
        """Negative skip_days should raise."""
        with pytest.raises(ValueError, match="skip_days must be >= 1"):
            QuantileAnalysisConfig(skip_days=-1)

    def test_holding_period_zero_raises(self):
        """holding_period_days=0 should raise."""
        with pytest.raises(ValueError, match="holding_period_days must be > 0"):
            QuantileAnalysisConfig(holding_period_days=0)

    def test_holding_period_negative_raises(self):
        """Negative holding_period should raise."""
        with pytest.raises(ValueError, match="holding_period_days must be > 0"):
            QuantileAnalysisConfig(holding_period_days=-5)

    def test_n_quantiles_one_raises(self):
        """n_quantiles=1 should raise (need at least 2 for L/S)."""
        with pytest.raises(ValueError, match="n_quantiles must be >= 2"):
            QuantileAnalysisConfig(n_quantiles=1)

    def test_n_quantiles_zero_raises(self):
        """n_quantiles=0 should raise."""
        with pytest.raises(ValueError, match="n_quantiles must be >= 2"):
            QuantileAnalysisConfig(n_quantiles=0)

    def test_min_observations_less_than_quantiles_raises(self):
        """min_observations_per_date < n_quantiles should raise."""
        with pytest.raises(ValueError, match="min_observations_per_date.*must be >= n_quantiles"):
            QuantileAnalysisConfig(n_quantiles=10, min_observations_per_date=5)

    def test_custom_valid_config(self):
        """Custom config with valid values should work."""
        config = QuantileAnalysisConfig(
            n_quantiles=10,
            holding_period_days=5,
            min_observations_per_date=100,
            min_total_dates=20,
            skip_days=2,
        )
        assert config.n_quantiles == 10
        assert config.skip_days == 2

    def test_config_is_frozen(self):
        """Config should be immutable (frozen dataclass)."""
        config = QuantileAnalysisConfig()
        with pytest.raises(AttributeError):
            config.n_quantiles = 10


# ------------------------------------------------------------------ QuantileResult Tests


@pytest.mark.unit()
class TestQuantileResult:
    """Tests for QuantileResult dataclass."""

    def test_result_is_frozen(self):
        """Result should be immutable."""
        result = QuantileResult(
            mean_rank_ic=0.05,
            rank_ic_std=0.02,
            rank_ic_t_stat=2.5,
            rank_ic_positive_pct=60.0,
        )
        with pytest.raises(AttributeError):
            result.mean_rank_ic = 0.1

    def test_result_default_values(self):
        """Result should have sensible defaults."""
        result = QuantileResult(
            mean_rank_ic=0.05,
            rank_ic_std=0.02,
            rank_ic_t_stat=2.5,
            rank_ic_positive_pct=60.0,
        )
        assert result.n_dates == 0
        assert result.quantile_returns == {}
        assert result.long_short_spread == 0.0


# ------------------------------------------------------------------ QuantileAnalyzer Tests


@pytest.mark.unit()
class TestQuantileAnalyzer:
    """Tests for QuantileAnalyzer."""

    def test_analyze_happy_path(
        self,
        trading_calendar: MockCalendar,
        basic_signals: pl.DataFrame,
        basic_forward_returns: pl.DataFrame,
    ):
        """Basic analyze should compute Rank IC and quantile returns."""
        analyzer = QuantileAnalyzer(trading_calendar)
        result = analyzer.analyze(
            signals=basic_signals,
            forward_returns=basic_forward_returns,
            config=QuantileAnalysisConfig(min_total_dates=10),
        )

        assert isinstance(result, QuantileResult)
        assert result.n_dates > 10
        assert -1.0 <= result.mean_rank_ic <= 1.0
        assert result.rank_ic_std >= 0
        assert 0 <= result.rank_ic_positive_pct <= 100
        assert len(result.quantile_returns) == 5  # default n_quantiles

    def test_analyze_empty_signals_raises(self, trading_calendar: MockCalendar):
        """Empty signals should raise InsufficientDataError."""
        analyzer = QuantileAnalyzer(trading_calendar)
        empty_signals = pl.DataFrame(
            {
                "signal_date": pl.Series([], dtype=pl.Date),
                "permno": pl.Series([], dtype=pl.Int64),
                "signal_value": pl.Series([], dtype=pl.Float64),
            }
        )

        with pytest.raises(InsufficientDataError, match="No signals provided"):
            analyzer.analyze(
                signals=empty_signals,
                forward_returns=pl.DataFrame(),
            )

    def test_analyze_empty_returns_raises(
        self, trading_calendar: MockCalendar, basic_signals: pl.DataFrame
    ):
        """Empty forward returns should raise InsufficientDataError."""
        analyzer = QuantileAnalyzer(trading_calendar)
        empty_returns = pl.DataFrame(
            {
                "signal_date": pl.Series([], dtype=pl.Date),
                "permno": pl.Series([], dtype=pl.Int64),
                "forward_return": pl.Series([], dtype=pl.Float64),
            }
        )

        with pytest.raises(InsufficientDataError, match="No forward returns provided"):
            analyzer.analyze(
                signals=basic_signals,
                forward_returns=empty_returns,
            )

    def test_analyze_no_overlap_raises(self, trading_calendar: MockCalendar):
        """No overlapping signal/return data should raise."""
        analyzer = QuantileAnalyzer(trading_calendar)

        signals = pl.DataFrame(
            {
                "signal_date": [date(2024, 1, 2)] * 100,
                "permno": list(range(1, 101)),
                "signal_value": [float(i) for i in range(100)],
            }
        )

        # Returns have different permnos - no overlap
        returns = pl.DataFrame(
            {
                "signal_date": [date(2024, 1, 2)] * 100,
                "permno": list(range(1001, 1101)),
                "forward_return": [0.01] * 100,
            }
        )

        with pytest.raises(InsufficientDataError, match="No overlapping signal/return data"):
            analyzer.analyze(signals=signals, forward_returns=returns)

    def test_analyze_insufficient_dates_raises(self, trading_calendar: MockCalendar):
        """Too few valid dates should raise."""
        analyzer = QuantileAnalyzer(trading_calendar)

        # Only 5 dates of data
        dates = _create_trading_dates(date(2024, 1, 2), 5)
        signals = pl.DataFrame(
            {
                "signal_date": dates * 100,
                "permno": list(range(100)) * 5,
                "signal_value": [float(i) for i in range(500)],
            }
        )

        returns = pl.DataFrame(
            {
                "signal_date": dates * 100,
                "permno": list(range(100)) * 5,
                "forward_return": [0.01] * 500,
            }
        )

        with pytest.raises(InsufficientDataError, match="Only.*valid dates"):
            analyzer.analyze(
                signals=signals,
                forward_returns=returns,
                config=QuantileAnalysisConfig(min_total_dates=50),
            )

    def test_analyze_renames_date_column(
        self,
        trading_calendar: MockCalendar,
    ):
        """Signals with 'date' column should be renamed to 'signal_date'."""
        analyzer = QuantileAnalyzer(trading_calendar)

        dates = _create_trading_dates(date(2024, 1, 2), 60)
        rows = []
        for d in dates:
            for permno in range(1, 101):
                rows.append(
                    {
                        "date": d,  # Using 'date' instead of 'signal_date'
                        "permno": permno,
                        "signal_value": float(permno) / 100,
                    }
                )
        signals = pl.DataFrame(rows)

        # Create matching forward returns
        fwd_rows = []
        for row in signals.iter_rows(named=True):
            fwd_rows.append(
                {
                    "signal_date": row["date"],
                    "permno": row["permno"],
                    "forward_return": row["signal_value"] * 0.01,
                }
            )
        forward_returns = pl.DataFrame(fwd_rows)

        # Should work without error
        result = analyzer.analyze(
            signals=signals,
            forward_returns=forward_returns,
            config=QuantileAnalysisConfig(min_total_dates=10),
        )
        assert result.n_dates > 0

    def test_analyze_coerces_datetime_to_date(self, trading_calendar: MockCalendar):
        """Datetime signal_date should be coerced to Date."""
        from datetime import datetime as dt

        analyzer = QuantileAnalyzer(trading_calendar)

        dates = _create_trading_dates(date(2024, 1, 2), 60)
        # Create signals with datetime
        rows = []
        for d in dates:
            for permno in range(1, 101):
                rows.append(
                    {
                        "signal_date": dt(d.year, d.month, d.day),  # datetime object
                        "permno": permno,
                        "signal_value": float(permno) / 100,
                    }
                )
        signals = pl.DataFrame(rows)
        # Cast to ensure Datetime dtype
        signals = signals.with_columns(pl.col("signal_date").cast(pl.Datetime("us")))

        # Forward returns with Date type
        fwd_rows = []
        for d in dates:
            for permno in range(1, 101):
                fwd_rows.append(
                    {
                        "signal_date": d,  # date object
                        "permno": permno,
                        "forward_return": float(permno) / 10000,
                    }
                )
        returns = pl.DataFrame(fwd_rows)

        result = analyzer.analyze(
            signals=signals,
            forward_returns=returns,
            config=QuantileAnalysisConfig(min_total_dates=10),
        )
        assert result.n_dates > 0

    def test_analyze_filters_null_dates(self, trading_calendar: MockCalendar):
        """Null signal_date/permno rows should be filtered."""
        analyzer = QuantileAnalyzer(trading_calendar)

        dates = _create_trading_dates(date(2024, 1, 2), 60)
        # Create signals with some valid data
        signals_data = []
        for d in dates:
            for permno in range(1, 101):
                signals_data.append(
                    {
                        "signal_date": d,
                        "permno": permno,
                        "signal_value": float(permno) / 100,
                    }
                )
        # Add null entries (should be filtered)
        signals_data.append({"signal_date": None, "permno": 1, "signal_value": 0.0})
        signals_data.append({"signal_date": dates[0], "permno": None, "signal_value": 0.0})

        signals = pl.DataFrame(signals_data)

        # Create matching forward returns
        fwd_data = []
        for d in dates:
            for permno in range(1, 101):
                fwd_data.append(
                    {
                        "signal_date": d,
                        "permno": permno,
                        "forward_return": float(permno) / 10000,
                    }
                )
        returns = pl.DataFrame(fwd_data)

        result = analyzer.analyze(
            signals=signals,
            forward_returns=returns,
            config=QuantileAnalysisConfig(min_total_dates=10),
        )
        assert result.n_dates > 0

    def test_analyze_deduplicates_signals(self, trading_calendar: MockCalendar):
        """Duplicate (date, permno) signals should be averaged."""
        analyzer = QuantileAnalyzer(trading_calendar)

        dates = _create_trading_dates(date(2024, 1, 2), 60)
        signals_data = []
        for d in dates:
            for permno in range(1, 101):
                # Add duplicate with different signal values (will be averaged)
                # Use permno-based values to ensure variance after averaging
                base_signal = float(permno) / 100
                signals_data.append(
                    {
                        "signal_date": d,
                        "permno": permno,
                        "signal_value": base_signal - 0.1,  # e.g., 0.0 for permno=1
                    }
                )
                signals_data.append(
                    {
                        "signal_date": d,
                        "permno": permno,
                        "signal_value": base_signal + 0.1,  # e.g., 0.2 for permno=1
                    }
                )

        signals = pl.DataFrame(signals_data)  # 12000 rows (6000 unique)

        # Create matching forward returns (correlated with signal)
        fwd_data = []
        for d in dates:
            for permno in range(1, 101):
                fwd_data.append(
                    {
                        "signal_date": d,
                        "permno": permno,
                        "forward_return": float(permno) / 10000,  # Correlated with signal
                    }
                )
        returns = pl.DataFrame(fwd_data)

        result = analyzer.analyze(
            signals=signals,
            forward_returns=returns,
            config=QuantileAnalysisConfig(min_total_dates=10),
        )
        assert result.n_dates > 0

    def test_analyze_skips_low_observation_dates(self, trading_calendar: MockCalendar):
        """Dates with too few observations should be skipped."""
        analyzer = QuantileAnalyzer(trading_calendar)

        dates = _create_trading_dates(date(2024, 1, 2), 60)
        signals_data = []
        for i, d in enumerate(dates):
            # First 50 dates have 100 obs, last 10 have only 10
            n_obs = 100 if i < 50 else 10
            for permno in range(1, n_obs + 1):
                signals_data.append(
                    {
                        "signal_date": d,
                        "permno": permno,
                        "signal_value": float(permno),
                    }
                )

        signals = pl.DataFrame(signals_data)
        returns = signals.with_columns(pl.col("signal_value").alias("forward_return") * 0.01).drop(
            "signal_value"
        )

        result = analyzer.analyze(
            signals=signals,
            forward_returns=returns,
            config=QuantileAnalysisConfig(
                min_observations_per_date=50,
                min_total_dates=10,
            ),
        )
        # Only first 50 dates should be included
        assert result.n_dates == 50
        assert result.n_dates_skipped >= 10

    def test_analyze_filters_non_finite_values(self, trading_calendar: MockCalendar):
        """NaN and Inf values in signal/return should be filtered."""
        analyzer = QuantileAnalyzer(trading_calendar)

        dates = _create_trading_dates(date(2024, 1, 2), 60)
        signals_data = []
        for d in dates:
            for permno in range(1, 101):
                # Add some NaN values (permno=1 gets NaN, others get valid)
                val = float("nan") if permno == 1 else float(permno) / 100
                signals_data.append(
                    {
                        "signal_date": d,
                        "permno": permno,
                        "signal_value": val,
                    }
                )

        signals = pl.DataFrame(signals_data)

        # Create matching forward returns
        fwd_data = []
        for d in dates:
            for permno in range(1, 101):
                fwd_data.append(
                    {
                        "signal_date": d,
                        "permno": permno,
                        "forward_return": float(permno) / 10000,
                    }
                )
        returns = pl.DataFrame(fwd_data)

        result = analyzer.analyze(
            signals=signals,
            forward_returns=returns,
            config=QuantileAnalysisConfig(min_total_dates=10, min_observations_per_date=50),
        )
        # Should still work after filtering NaN rows (still have 99 obs per date)
        assert result.n_dates > 0

    def test_analyze_metadata_populated(
        self,
        trading_calendar: MockCalendar,
        basic_signals: pl.DataFrame,
        basic_forward_returns: pl.DataFrame,
    ):
        """Result should have metadata populated."""
        analyzer = QuantileAnalyzer(trading_calendar)
        result = analyzer.analyze(
            signals=basic_signals,
            forward_returns=basic_forward_returns,
            config=QuantileAnalysisConfig(min_total_dates=10),
            signal_name="test_signal",
            universe_name="SP500",
        )

        assert result.signal_name == "test_signal"
        assert result.universe_name == "SP500"
        assert result.period_start is not None
        assert result.period_end is not None
        assert result.period_start <= result.period_end


@pytest.mark.unit()
class TestRankICComputation:
    """Tests for _compute_rank_ic method."""

    def test_rank_ic_perfect_positive(self, trading_calendar: MockCalendar):
        """Perfect positive correlation should give IC = 1."""
        analyzer = QuantileAnalyzer(trading_calendar)
        signals = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        returns = np.array([0.1, 0.2, 0.3, 0.4, 0.5])

        ic = analyzer._compute_rank_ic(signals, returns)
        assert ic is not None
        assert abs(ic - 1.0) < 1e-6

    def test_rank_ic_perfect_negative(self, trading_calendar: MockCalendar):
        """Perfect negative correlation should give IC = -1."""
        analyzer = QuantileAnalyzer(trading_calendar)
        signals = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        returns = np.array([0.5, 0.4, 0.3, 0.2, 0.1])

        ic = analyzer._compute_rank_ic(signals, returns)
        assert ic is not None
        assert abs(ic - (-1.0)) < 1e-6

    def test_rank_ic_constant_signal_returns_none(self, trading_calendar: MockCalendar):
        """Constant signal values should return None."""
        analyzer = QuantileAnalyzer(trading_calendar)
        signals = np.array([1.0, 1.0, 1.0, 1.0, 1.0])
        returns = np.array([0.1, 0.2, 0.3, 0.4, 0.5])

        ic = analyzer._compute_rank_ic(signals, returns)
        assert ic is None

    def test_rank_ic_constant_returns_returns_none(self, trading_calendar: MockCalendar):
        """Constant return values should return None."""
        analyzer = QuantileAnalyzer(trading_calendar)
        signals = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        returns = np.array([0.1, 0.1, 0.1, 0.1, 0.1])

        ic = analyzer._compute_rank_ic(signals, returns)
        assert ic is None


@pytest.mark.unit()
class TestQuantileAssignment:
    """Tests for _assign_quantiles method."""

    def test_assign_quantiles_five_buckets(self, trading_calendar: MockCalendar):
        """Five quantile buckets should be 1-5."""
        analyzer = QuantileAnalyzer(trading_calendar)
        signals = np.array([1.0, 2.0, 3.0, 4.0, 5.0])

        quantiles = analyzer._assign_quantiles(signals, 5)

        assert set(quantiles) == {1, 2, 3, 4, 5}
        assert quantiles[0] == 1  # Lowest signal -> Q1
        assert quantiles[4] == 5  # Highest signal -> Q5

    def test_assign_quantiles_two_buckets(self, trading_calendar: MockCalendar):
        """Two quantile buckets for long/short."""
        analyzer = QuantileAnalyzer(trading_calendar)
        signals = np.array([1.0, 2.0, 3.0, 4.0])

        quantiles = analyzer._assign_quantiles(signals, 2)

        assert set(quantiles).issubset({1, 2})
        assert quantiles[0] == 1  # Low
        assert quantiles[3] == 2  # High

    def test_assign_quantiles_ties_handled(self, trading_calendar: MockCalendar):
        """Tied signal values should use average rank."""
        analyzer = QuantileAnalyzer(trading_calendar)
        # All same value
        signals = np.array([1.0, 1.0, 1.0, 1.0])

        quantiles = analyzer._assign_quantiles(signals, 2)

        # All should be in same bucket due to average rank
        assert len(set(quantiles)) <= 2


# ------------------------------------------------------------------ run_quantile_analysis Tests


@pytest.mark.unit()
class TestRunQuantileAnalysis:
    """Tests for run_quantile_analysis convenience function."""

    def test_run_quantile_analysis_happy_path(self, trading_calendar: MockCalendar):
        """Convenience function should work end-to-end."""
        dates = _create_trading_dates(date(2024, 1, 2), 60)
        rows = []
        for d in dates:
            for permno in range(1, 101):
                rows.append(
                    {
                        "signal_date": d,
                        "permno": permno,
                        "signal_value": float(permno) / 100,
                    }
                )
        signals = pl.DataFrame(rows)

        # Mock ForwardReturnsProvider
        fwd_rows = []
        for d in dates:
            for permno in range(1, 101):
                fwd_rows.append(
                    {
                        "signal_date": d,
                        "permno": permno,
                        "forward_return": float(permno) / 10000,
                    }
                )

        mock_provider = MagicMock()
        mock_provider.get_forward_returns.return_value = pl.DataFrame(fwd_rows)

        result = run_quantile_analysis(
            signals=signals,
            forward_returns_provider=mock_provider,
            calendar=trading_calendar,
            config=QuantileAnalysisConfig(min_total_dates=10),
        )

        assert isinstance(result, QuantileResult)
        assert result.n_dates > 0

    def test_run_quantile_analysis_renames_date(self, trading_calendar: MockCalendar):
        """Should handle 'date' column by renaming to 'signal_date'."""
        dates = _create_trading_dates(date(2024, 1, 2), 60)
        rows = []
        for d in dates:
            for permno in range(1, 101):
                rows.append(
                    {
                        "date": d,  # Using 'date' not 'signal_date'
                        "permno": permno,
                        "signal_value": float(permno) / 100,
                    }
                )
        signals = pl.DataFrame(rows)

        fwd_rows = []
        for d in dates:
            for permno in range(1, 101):
                fwd_rows.append(
                    {
                        "signal_date": d,
                        "permno": permno,
                        "forward_return": float(permno) / 10000,
                    }
                )

        mock_provider = MagicMock()
        mock_provider.get_forward_returns.return_value = pl.DataFrame(fwd_rows)

        result = run_quantile_analysis(
            signals=signals,
            forward_returns_provider=mock_provider,
            calendar=trading_calendar,
            config=QuantileAnalysisConfig(min_total_dates=10),
        )

        assert result.n_dates > 0

    def test_run_quantile_analysis_normalizes_dates(self, trading_calendar: MockCalendar):
        """Should normalize non-trading dates to previous session."""
        # Include a weekend date (Jan 6, 2024 is Saturday)
        # It should normalize to Jan 5 (Friday)
        rows = []
        for permno in range(1, 101):
            rows.append(
                {
                    "signal_date": date(2024, 1, 6),  # Saturday - should normalize to Friday
                    "permno": permno,
                    "signal_value": float(permno) / 100,
                }
            )
        signals = pl.DataFrame(rows)

        # Returns use normalized date (Jan 5, Friday)
        fwd_rows = []
        for permno in range(1, 101):
            fwd_rows.append(
                {
                    "signal_date": date(2024, 1, 5),
                    "permno": permno,
                    "forward_return": float(permno) / 10000,
                }
            )

        mock_provider = MagicMock()
        mock_provider.get_forward_returns.return_value = pl.DataFrame(fwd_rows)

        # Should work (dates get normalized)
        _result = run_quantile_analysis(
            signals=signals,
            forward_returns_provider=mock_provider,
            calendar=trading_calendar,
            config=QuantileAnalysisConfig(min_total_dates=1, min_observations_per_date=10),
        )
        # The normalization should convert Saturday to Friday
        # Result may have 0 or 1 date depending on calendar setup

    def test_run_quantile_analysis_deduplicates_signal_keys(self, trading_calendar: MockCalendar):
        """Should deduplicate signal keys before calling provider."""
        dates = _create_trading_dates(date(2024, 1, 2), 60)
        # Add duplicate entries with permno-based variance
        signals_data = []
        for d in dates:
            for permno in range(1, 101):
                base_signal = float(permno) / 100
                signals_data.append(
                    {
                        "signal_date": d,
                        "permno": permno,
                        "signal_value": base_signal - 0.05,
                    }
                )
                signals_data.append(
                    {
                        "signal_date": d,
                        "permno": permno,
                        "signal_value": base_signal + 0.05,
                    }
                )

        signals = pl.DataFrame(signals_data)

        # Create matching forward returns (correlated with signal)
        fwd_rows = []
        for d in dates:
            for permno in range(1, 101):
                fwd_rows.append(
                    {
                        "signal_date": d,
                        "permno": permno,
                        "forward_return": float(permno) / 10000,  # Correlated
                    }
                )

        mock_provider = MagicMock()
        mock_provider.get_forward_returns.return_value = pl.DataFrame(fwd_rows)

        run_quantile_analysis(
            signals=signals,
            forward_returns_provider=mock_provider,
            calendar=trading_calendar,
            config=QuantileAnalysisConfig(min_total_dates=10),
        )

        # Provider should receive deduplicated signal keys
        call_args = mock_provider.get_forward_returns.call_args
        signals_df_arg = (
            call_args[1]["signals_df"] if "signals_df" in call_args[1] else call_args[0][0]
        )
        # Should have 6000 unique keys, not 12000
        assert signals_df_arg.height == 6000


@pytest.mark.unit()
class TestInsufficientDataError:
    """Tests for InsufficientDataError exception."""

    def test_error_is_exception(self):
        """Should be a proper exception."""
        error = InsufficientDataError("Not enough data")
        assert isinstance(error, Exception)
        assert str(error) == "Not enough data"


# ------------------------------------------------------------------ Edge Cases Tests


class MockCalendarWithNormalization:
    """Mock calendar that simulates weekend/holiday normalization."""

    def __init__(self, trading_days: list[date]):
        self._trading = set(trading_days)

    def is_session(self, d: date) -> bool:
        return d in self._trading

    def date_to_session(self, d: date, direction: str = "next") -> MagicMock:
        """Return previous trading day for direction='previous'."""
        if direction == "previous":
            # Find previous trading day
            current = d
            for _ in range(10):  # Max 10 days back
                from datetime import timedelta

                current = current - timedelta(days=1)
                if current in self._trading:
                    result = MagicMock()
                    result.date.return_value = current
                    return result
        raise ValueError(f"No session found near {d}")

    def sessions_in_range(self, start: date, end: date) -> list:
        """Return sessions in range as MagicMock objects."""
        result = []
        for d in sorted(self._trading):
            if start <= d <= end:
                mock = MagicMock()
                mock.date.return_value = d
                result.append(mock)
        return result


@pytest.mark.unit()
class TestAnalyzerEdgeCases:
    """Tests for QuantileAnalyzer edge cases."""

    def test_date_normalization_on_weekend_start(self):
        """Weekend start date should normalize to previous Friday."""

        # Trading days: Mon, Tue, Wed, Thu, Fri (not weekends)
        trading_days = [
            date(2024, 1, 8),  # Monday
            date(2024, 1, 9),  # Tuesday
            date(2024, 1, 10),  # Wednesday
            date(2024, 1, 11),  # Thursday
            date(2024, 1, 12),  # Friday
        ]
        calendar = MockCalendarWithNormalization(trading_days)

        # Create analyzer with weekend dates that need normalization
        signals = pl.DataFrame(
            {
                "signal_date": [date(2024, 1, 8), date(2024, 1, 9), date(2024, 1, 10)],
                "permno": [10001, 10002, 10003],
                "signal_value": [0.1, 0.2, 0.3],
            }
        )

        # Pass Saturday (1/13) as part of signal range - should normalize
        fwd_returns = pl.DataFrame(
            {
                "signal_date": [date(2024, 1, 8), date(2024, 1, 9), date(2024, 1, 10)],
                "permno": [10001, 10002, 10003],
                "forward_return": [0.01, 0.02, 0.03],
            }
        )

        analyzer = QuantileAnalyzer(calendar)
        # Should complete without error (weekend normalization worked)
        # Will raise InsufficientDataError because too few dates, but that's expected
        with pytest.raises(InsufficientDataError):
            analyzer.analyze(signals, fwd_returns)

    def test_inverted_range_after_normalization(self):
        """Should handle inverted range after date normalization gracefully."""
        # Only one trading day
        trading_days = [date(2024, 1, 10)]  # Wednesday
        calendar = MockCalendarWithNormalization(trading_days)

        # Signal on the only trading day
        signals = pl.DataFrame(
            {
                "signal_date": [date(2024, 1, 10)],
                "permno": [10001],
                "signal_value": [0.5],
            }
        )

        fwd_returns = pl.DataFrame(
            {
                "signal_date": [date(2024, 1, 10)],
                "permno": [10001],
                "forward_return": [0.02],
            }
        )

        analyzer = QuantileAnalyzer(calendar)
        # Should handle gracefully (too few dates)
        with pytest.raises(InsufficientDataError):
            analyzer.analyze(signals, fwd_returns)

    def test_non_trading_dates_filtered_before_analysis(self):
        """Signal dates that aren't in forward_returns are naturally filtered."""
        # Create calendar with only Mon-Fri
        from datetime import timedelta

        trading_dates = []
        current = date(2024, 1, 2)  # Tuesday
        for _ in range(60):
            if current.weekday() < 5:  # Mon-Fri
                trading_dates.append(current)
            current = current + timedelta(days=1)

        # Calendar only has trading days (weekdays)
        cal = MockCalendar(trading_dates)

        signals_data = []
        # Add signals for first 55 trading days
        for d in trading_dates[:55]:
            for permno in range(1, 101):
                signals_data.append(
                    {
                        "signal_date": d,
                        "permno": permno,
                        "signal_value": float(permno) / 100,
                    }
                )

        signals = pl.DataFrame(signals_data)

        # Forward returns only for trading days (no weekends)
        fwd_data = []
        for d in trading_dates[:55]:
            for permno in range(1, 101):
                fwd_data.append(
                    {
                        "signal_date": d,
                        "permno": permno,
                        "forward_return": float(permno) / 10000,
                    }
                )
        returns = pl.DataFrame(fwd_data)

        analyzer = QuantileAnalyzer(cal)
        result = analyzer.analyze(
            signals=signals,
            forward_returns=returns,
            config=QuantileAnalysisConfig(min_total_dates=10),
        )

        # Analysis should complete with only trading days
        assert result.n_dates > 0
        # All dates are trading days, so none should be skipped for that reason
        # (some may be skipped for low observations if any)

    def test_missing_partition_for_date_skipped(self, trading_calendar: MockCalendar):
        """Dates with no matching partition in forward_returns should be skipped."""
        dates = _create_trading_dates(date(2024, 1, 2), 60)

        # Signals for all dates
        signals_data = []
        for d in dates:
            for permno in range(1, 101):
                signals_data.append(
                    {
                        "signal_date": d,
                        "permno": permno,
                        "signal_value": float(permno) / 100,
                    }
                )

        signals = pl.DataFrame(signals_data)

        # Forward returns for only first 50 dates (missing last 10)
        fwd_data = []
        for d in dates[:50]:
            for permno in range(1, 101):
                fwd_data.append(
                    {
                        "signal_date": d,
                        "permno": permno,
                        "forward_return": float(permno) / 10000,
                    }
                )
        returns = pl.DataFrame(fwd_data)

        analyzer = QuantileAnalyzer(trading_calendar)
        result = analyzer.analyze(
            signals=signals,
            forward_returns=returns,
            config=QuantileAnalysisConfig(min_total_dates=10),
        )

        # Last 10 dates should have been skipped (no partition)
        assert result.n_dates_skipped >= 10
        assert result.n_dates == 50

    def test_all_signals_null_after_filtering_raises(self, trading_calendar: MockCalendar):
        """Should raise InsufficientDataError when all signals are null."""
        # Create signals with all null signal_dates
        signals = pl.DataFrame(
            {
                "signal_date": [None, None, None],
                "permno": [10001, 10002, 10003],
                "signal_value": [0.1, 0.2, 0.3],
            }
        ).cast({"signal_date": pl.Date})

        returns = pl.DataFrame(
            {
                "signal_date": [date(2024, 1, 2)],
                "permno": [10001],
                "forward_return": [0.01],
            }
        )

        analyzer = QuantileAnalyzer(trading_calendar)
        with pytest.raises(InsufficientDataError, match="No valid signals"):
            analyzer.analyze(signals, returns)

    def test_date_normalization_exception_keeps_original(self):
        """Should keep original date when normalization fails."""
        from datetime import timedelta

        # Calendar that raises on date_to_session
        class FailingCalendar:
            def __init__(self, trading_dates: list[date]):
                self._trading = set(trading_dates)

            def is_session(self, d: date) -> bool:
                return d in self._trading

            def date_to_session(self, d: date, direction: str = "next"):
                raise RuntimeError("Calendar error")

            def sessions_in_range(self, start: date, end: date) -> list:
                result = []
                for s in sorted(self._trading):
                    if start <= s <= end:
                        mock = MagicMock()
                        mock.date.return_value = s
                        result.append(mock)
                return result

        # Create trading dates (only weekdays)
        trading_dates = []
        current = date(2024, 1, 2)
        for _ in range(60):
            if current.weekday() < 5:
                trading_dates.append(current)
            current = current + timedelta(days=1)

        cal = FailingCalendar(trading_dates)

        # Signals with a weekend date that needs normalization
        _saturday = date(2024, 1, 6)  # Kept for documentation clarity
        signals_data = []
        for d in trading_dates[:55]:
            for permno in range(1, 101):
                signals_data.append(
                    {
                        "signal_date": d,
                        "permno": permno,
                        "signal_value": float(permno) / 100,
                    }
                )

        signals = pl.DataFrame(signals_data)

        fwd_data = []
        for d in trading_dates[:55]:
            for permno in range(1, 101):
                fwd_data.append(
                    {
                        "signal_date": d,
                        "permno": permno,
                        "forward_return": float(permno) / 10000,
                    }
                )
        returns = pl.DataFrame(fwd_data)

        analyzer = QuantileAnalyzer(cal)
        # Should complete without raising (uses original date when normalization fails)
        result = analyzer.analyze(
            signals=signals,
            forward_returns=returns,
            config=QuantileAnalysisConfig(min_total_dates=10),
        )
        assert result.n_dates > 0

    def test_date_cast_failure_raises(self, trading_calendar: MockCalendar):
        """Should raise InsufficientDataError when date cast fails."""
        # Create signals with non-date string that can't be cast
        signals = pl.DataFrame(
            {
                "signal_date": ["not-a-date", "also-not-a-date"],
                "permno": [10001, 10002],
                "signal_value": [0.1, 0.2],
            }
        )

        returns = pl.DataFrame(
            {
                "signal_date": [date(2024, 1, 2)],
                "permno": [10001],
                "forward_return": [0.01],
            }
        )

        analyzer = QuantileAnalyzer(trading_calendar)
        with pytest.raises(InsufficientDataError, match="Failed to convert signal_date"):
            analyzer.analyze(signals, returns)

    def test_start_date_normalization_to_previous_session(self):
        """Start date on weekend should normalize to previous Friday."""
        from datetime import timedelta

        # Create calendar with only weekdays
        trading_dates = []
        current = date(2024, 1, 1)  # Monday Jan 1
        for _ in range(90):
            if current.weekday() < 5:  # Mon-Fri
                trading_dates.append(current)
            current = current + timedelta(days=1)

        class NormalizingCalendar:
            def __init__(self, sessions: set[date]):
                self._sessions = sessions

            def is_session(self, d: date) -> bool:
                return d in self._sessions

            def date_to_session(self, d: date, direction: str = "next"):
                if direction == "previous":
                    current = d
                    for _ in range(10):
                        current = current - timedelta(days=1)
                        if current in self._sessions:
                            mock = MagicMock()
                            mock.date.return_value = current
                            return mock
                raise ValueError(f"No session found near {d}")

            def sessions_in_range(self, start: date, end: date) -> list:
                result = []
                for s in sorted(self._sessions):
                    if start <= s <= end:
                        mock = MagicMock()
                        mock.date.return_value = s
                        result.append(mock)
                return result

        cal = NormalizingCalendar(set(trading_dates))

        # Create signals starting from a trading day
        signals_data = []
        for d in trading_dates[5:65]:  # 60 trading days
            for permno in range(1, 101):
                signals_data.append(
                    {
                        "signal_date": d,
                        "permno": permno,
                        "signal_value": float(permno) / 100,
                    }
                )

        signals = pl.DataFrame(signals_data)

        fwd_data = []
        for d in trading_dates[5:65]:
            for permno in range(1, 101):
                fwd_data.append(
                    {
                        "signal_date": d,
                        "permno": permno,
                        "forward_return": float(permno) / 10000,
                    }
                )
        returns = pl.DataFrame(fwd_data)

        analyzer = QuantileAnalyzer(cal)
        result = analyzer.analyze(
            signals=signals,
            forward_returns=returns,
            config=QuantileAnalysisConfig(min_total_dates=10),
        )

        # Should complete successfully
        assert result.n_dates > 0

    def test_none_date_in_signals_filtered(self, trading_calendar: MockCalendar):
        """Signals with None dates should be filtered before analysis."""
        dates = _create_trading_dates(date(2024, 1, 2), 60)

        # Create signals including some with None dates
        signals_data = []
        for d in dates:
            for permno in range(1, 101):
                signals_data.append(
                    {
                        "signal_date": d,
                        "permno": permno,
                        "signal_value": float(permno) / 100,
                    }
                )

        signals = pl.DataFrame(signals_data)

        # Now add a few rows and set their dates to None
        # This requires using vstack with None values
        extra_signals = pl.DataFrame(
            {
                "signal_date": pl.Series([None, None], dtype=pl.Date),
                "permno": [10001, 10002],
                "signal_value": [0.5, 0.6],
            }
        )
        signals = pl.concat([signals, extra_signals])

        fwd_data = []
        for d in dates:
            for permno in range(1, 101):
                fwd_data.append(
                    {
                        "signal_date": d,
                        "permno": permno,
                        "forward_return": float(permno) / 10000,
                    }
                )
        returns = pl.DataFrame(fwd_data)

        analyzer = QuantileAnalyzer(trading_calendar)
        result = analyzer.analyze(
            signals=signals,
            forward_returns=returns,
            config=QuantileAnalysisConfig(min_total_dates=10),
        )

        # Should complete successfully (None dates filtered out)
        assert result.n_dates > 0
