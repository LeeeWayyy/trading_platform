"""Tests for BacktestAnalyticsService.

P6T10: Track 10 - Quantile & Attribution Analytics

Coverage targets:
- Ownership verification (verify_job_ownership)
- get_universe_signals with filters and limits
- Limit validation (None, invalid, bounds)
- Column renaming (signal -> signal_value)
- Error handling (JobNotFound, ResultPathMissing)
- run_quantile_analysis with ownership check
- get_backtest_result
- Async/sync bridge (run_in_threadpool)
"""

from __future__ import annotations

import importlib.util
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Skip if optional heavy deps missing
_missing = [mod for mod in ("polars",) if importlib.util.find_spec(mod) is None]
if _missing:
    pytest.skip(
        f"Skipping backtest analytics service tests because dependencies are missing: {', '.join(_missing)}",
        allow_module_level=True,
    )

import polars as pl

from libs.trading.backtest.models import JobNotFound, ResultPathMissing
from libs.web_console_services.backtest_analytics_service import BacktestAnalyticsService

# ------------------------------------------------------------------ Helpers


def make_sync(coro):
    """Helper to make a sync function return value usable by run_in_threadpool mock."""
    def sync_fn(*args, **kwargs):
        return coro
    return sync_fn


# ------------------------------------------------------------------ Fixtures


@pytest.fixture()
def mock_data_access() -> MagicMock:
    """Mock StrategyScopedDataAccess."""
    mock = MagicMock()
    mock.verify_job_ownership = AsyncMock()
    return mock


@pytest.fixture()
def mock_storage() -> MagicMock:
    """Mock BacktestResultStorage."""
    mock = MagicMock()
    return mock


@pytest.fixture()
def service(mock_data_access: MagicMock, mock_storage: MagicMock) -> BacktestAnalyticsService:
    """Create BacktestAnalyticsService with mocks."""
    return BacktestAnalyticsService(mock_data_access, mock_storage)


# ------------------------------------------------------------------ Ownership Tests


@pytest.mark.unit()
@pytest.mark.asyncio()
class TestVerifyJobOwnership:
    """Tests for verify_job_ownership."""

    async def test_verify_ownership_calls_data_access(
        self, service: BacktestAnalyticsService, mock_data_access: MagicMock
    ):
        """Should delegate to data_access.verify_job_ownership."""
        await service.verify_job_ownership("job-123")

        mock_data_access.verify_job_ownership.assert_called_once_with("job-123")

    async def test_verify_ownership_propagates_error(
        self, service: BacktestAnalyticsService, mock_data_access: MagicMock
    ):
        """PermissionError from data_access should propagate."""
        mock_data_access.verify_job_ownership.side_effect = PermissionError("Not owner")

        with pytest.raises(PermissionError, match="Not owner"):
            await service.verify_job_ownership("job-123")


# ------------------------------------------------------------------ get_universe_signals Tests


@pytest.mark.unit()
@pytest.mark.asyncio()
class TestGetUniverseSignals:
    """Tests for get_universe_signals."""

    async def test_verifies_ownership_first(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """Should verify ownership before accessing storage."""
        mock_data_access.verify_job_ownership.side_effect = PermissionError("Forbidden")

        with pytest.raises(PermissionError):
            await service.get_universe_signals("job-123")

        # Storage should NOT be called
        mock_storage.load_universe_signals_lazy.assert_not_called()

    async def test_returns_none_on_job_not_found(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """JobNotFound should return None (not raise)."""
        mock_storage.load_universe_signals_lazy.side_effect = JobNotFound("Not found")

        result = await service.get_universe_signals("job-123")

        assert result is None

    async def test_returns_none_on_result_path_missing(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """ResultPathMissing should return None (not raise)."""
        mock_storage.load_universe_signals_lazy.side_effect = ResultPathMissing("Path missing")

        result = await service.get_universe_signals("job-123")

        assert result is None

    async def test_returns_none_when_lazy_is_none(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """Should return None when storage returns None (no signals file)."""
        mock_storage.load_universe_signals_lazy.return_value = None

        result = await service.get_universe_signals("job-123")

        assert result is None

    async def test_happy_path(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """Should return DataFrame when signals exist."""
        signals_df = pl.DataFrame({
            "date": [date(2024, 1, 2)],
            "permno": [10001],
            "signal": [0.5],
        })
        mock_lazy = MagicMock()
        mock_lazy.collect.return_value = signals_df
        mock_storage.load_universe_signals_lazy.return_value = mock_lazy

        result = await service.get_universe_signals("job-123")

        mock_data_access.verify_job_ownership.assert_called_once_with("job-123")
        assert result is not None
        assert result.height == 1

    async def test_limit_enforced_max(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """Limit above MAX should be capped to MAX."""
        signals_df = pl.DataFrame({
            "date": [date(2024, 1, 2)],
            "permno": [10001],
            "signal": [0.5],
        })
        mock_lazy = MagicMock()
        mock_lazy.collect.return_value = signals_df
        mock_storage.load_universe_signals_lazy.return_value = mock_lazy

        await service.get_universe_signals("job-123", limit=99999)

        # First call should use MAX limit (10000), not 99999
        call_args = mock_storage.load_universe_signals_lazy.call_args
        # Args: (job_id, signal_name, date_range, limit)
        assert call_args[0][3] == 10000  # MAX_UNIVERSE_SIGNALS_LIMIT

    async def test_limit_enforced_min(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """Limit below MIN should be raised to MIN."""
        signals_df = pl.DataFrame({
            "date": [date(2024, 1, 2)],
            "permno": [10001],
            "signal": [0.5],
        })
        mock_lazy = MagicMock()
        mock_lazy.collect.return_value = signals_df
        mock_storage.load_universe_signals_lazy.return_value = mock_lazy

        await service.get_universe_signals("job-123", limit=0)

        # First call should use MIN limit (1), not 0
        call_args = mock_storage.load_universe_signals_lazy.call_args
        assert call_args[0][3] == 1  # MIN_UNIVERSE_SIGNALS_LIMIT

    async def test_limit_none_uses_max(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """Limit=None should use MAX limit."""
        signals_df = pl.DataFrame({
            "date": [date(2024, 1, 2)],
            "permno": [10001],
            "signal": [0.5],
        })
        mock_lazy = MagicMock()
        mock_lazy.collect.return_value = signals_df
        mock_storage.load_universe_signals_lazy.return_value = mock_lazy

        await service.get_universe_signals("job-123", limit=None)  # type: ignore

        call_args = mock_storage.load_universe_signals_lazy.call_args
        assert call_args[0][3] == 10000

    async def test_passes_filters_to_storage(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """Should pass signal_name and date_range to storage."""
        signals_df = pl.DataFrame({
            "date": [date(2024, 1, 2)],
            "permno": [10001],
            "signal": [0.5],
        })
        mock_lazy = MagicMock()
        mock_lazy.collect.return_value = signals_df
        mock_storage.load_universe_signals_lazy.return_value = mock_lazy

        await service.get_universe_signals(
            "job-123",
            signal_name="alpha_signal",
            date_range=(date(2024, 1, 1), date(2024, 1, 31)),
            limit=500,
        )

        call_args = mock_storage.load_universe_signals_lazy.call_args
        # Args: (job_id, signal_name, date_range, limit)
        assert call_args[0][0] == "job-123"
        assert call_args[0][1] == "alpha_signal"
        assert call_args[0][2] == (date(2024, 1, 1), date(2024, 1, 31))
        assert call_args[0][3] == 500


# ------------------------------------------------------------------ get_backtest_result Tests


@pytest.mark.unit()
@pytest.mark.asyncio()
class TestGetBacktestResult:
    """Tests for get_backtest_result."""

    async def test_happy_path(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """Should return BacktestResult."""
        mock_result = MagicMock()
        mock_storage.get_result.return_value = mock_result

        result = await service.get_backtest_result("job-123")

        mock_data_access.verify_job_ownership.assert_called_once_with("job-123")
        mock_storage.get_result.assert_called_once_with("job-123")
        assert result == mock_result

    async def test_verifies_ownership_first(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """Should verify ownership before accessing storage."""
        mock_data_access.verify_job_ownership.side_effect = PermissionError("Forbidden")

        with pytest.raises(PermissionError):
            await service.get_backtest_result("job-123")

        mock_storage.get_result.assert_not_called()

    async def test_propagates_job_not_found(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """JobNotFound should propagate (not caught like in get_universe_signals)."""
        mock_storage.get_result.side_effect = JobNotFound("Not found")

        with pytest.raises(JobNotFound):
            await service.get_backtest_result("job-123")


# ------------------------------------------------------------------ run_quantile_analysis Tests


@pytest.mark.unit()
@pytest.mark.asyncio()
class TestRunQuantileAnalysis:
    """Tests for run_quantile_analysis."""

    async def test_verifies_ownership_first(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """Should verify ownership before running analysis."""
        mock_data_access.verify_job_ownership.side_effect = PermissionError("Forbidden")

        mock_fwd_provider = MagicMock()
        mock_calendar = MagicMock()

        with pytest.raises(PermissionError):
            await service.run_quantile_analysis(
                "job-123",
                mock_fwd_provider,
                mock_calendar,
            )

        mock_storage.load_universe_signals_lazy.assert_not_called()

    async def test_raises_on_no_signals(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """Should raise InsufficientDataError when no signals."""
        from libs.trading.backtest.quantile_analysis import InsufficientDataError

        mock_storage.load_universe_signals_lazy.return_value = None  # No signals

        mock_fwd_provider = MagicMock()
        mock_calendar = MagicMock()

        with pytest.raises(InsufficientDataError, match="No universe signals"):
            await service.run_quantile_analysis(
                "job-123",
                mock_fwd_provider,
                mock_calendar,
            )

    async def test_raises_on_empty_signals(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """Should raise InsufficientDataError when signals empty."""
        from libs.trading.backtest.quantile_analysis import InsufficientDataError

        empty_df = pl.DataFrame({
            "date": pl.Series([], dtype=pl.Date),
            "permno": pl.Series([], dtype=pl.Int64),
            "signal": pl.Series([], dtype=pl.Float64),
        })
        mock_lazy = MagicMock()
        mock_lazy.collect.return_value = empty_df
        mock_storage.load_universe_signals_lazy.return_value = mock_lazy

        mock_fwd_provider = MagicMock()
        mock_calendar = MagicMock()

        with pytest.raises(InsufficientDataError, match="No universe signals"):
            await service.run_quantile_analysis(
                "job-123",
                mock_fwd_provider,
                mock_calendar,
            )

    async def test_raises_on_job_not_found(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """Should raise InsufficientDataError on JobNotFound."""
        from libs.trading.backtest.quantile_analysis import InsufficientDataError

        mock_storage.load_universe_signals_lazy.side_effect = JobNotFound("Not found")

        mock_fwd_provider = MagicMock()
        mock_calendar = MagicMock()

        with pytest.raises(InsufficientDataError, match="Backtest artifacts unavailable"):
            await service.run_quantile_analysis(
                "job-123",
                mock_fwd_provider,
                mock_calendar,
            )

    async def test_raises_on_result_path_missing(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """Should raise InsufficientDataError on ResultPathMissing."""
        from libs.trading.backtest.quantile_analysis import InsufficientDataError

        mock_storage.load_universe_signals_lazy.side_effect = ResultPathMissing("Path missing")

        mock_fwd_provider = MagicMock()
        mock_calendar = MagicMock()

        with pytest.raises(InsufficientDataError, match="Backtest artifacts unavailable"):
            await service.run_quantile_analysis(
                "job-123",
                mock_fwd_provider,
                mock_calendar,
            )


# ------------------------------------------------------------------ run_quantile_analysis Full Path Tests


@pytest.mark.unit()
@pytest.mark.asyncio()
class TestRunQuantileAnalysisFullPath:
    """Tests for run_quantile_analysis full execution path."""

    async def test_happy_path(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """Should run full analysis and return result."""
        from libs.trading.backtest.quantile_analysis import QuantileResult

        # Signal data with proper columns
        signals_df = pl.DataFrame({
            "date": [date(2024, 1, 2), date(2024, 1, 3)],
            "permno": [10001, 10002],
            "signal": [0.5, 0.7],
        })
        mock_lazy = MagicMock()
        mock_lazy.collect.return_value = signals_df
        mock_storage.load_universe_signals_lazy.return_value = mock_lazy

        # Mock forward returns provider
        mock_fwd_provider = MagicMock()
        mock_fwd_provider.get_forward_returns.return_value = pl.DataFrame({
            "signal_date": [date(2024, 1, 2), date(2024, 1, 3)],
            "permno": [10001, 10002],
            "forward_return": [0.02, 0.03],
        })

        # Mock calendar
        mock_calendar = MagicMock()
        mock_calendar.is_session.return_value = True

        with patch(
            "libs.trading.backtest.quantile_analysis.QuantileAnalyzer"
        ) as mock_analyzer_class:
            mock_analyzer = MagicMock()
            mock_result = MagicMock(spec=QuantileResult)
            mock_analyzer.analyze.return_value = mock_result
            mock_analyzer_class.return_value = mock_analyzer

            result = await service.run_quantile_analysis(
                "job-123",
                mock_fwd_provider,
                mock_calendar,
            )

        assert result == mock_result
        mock_analyzer.analyze.assert_called_once()

    async def test_renames_signal_to_signal_value(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """Should rename 'signal' column to 'signal_value'."""
        from libs.trading.backtest.quantile_analysis import QuantileResult

        signals_df = pl.DataFrame({
            "date": [date(2024, 1, 2)],
            "permno": [10001],
            "signal": [0.5],  # Old column name
        })
        mock_lazy = MagicMock()
        mock_lazy.collect.return_value = signals_df
        mock_storage.load_universe_signals_lazy.return_value = mock_lazy

        mock_fwd_provider = MagicMock()
        mock_fwd_provider.get_forward_returns.return_value = pl.DataFrame({
            "signal_date": [date(2024, 1, 2)],
            "permno": [10001],
            "forward_return": [0.02],
        })

        mock_calendar = MagicMock()
        mock_calendar.is_session.return_value = True

        with patch(
            "libs.trading.backtest.quantile_analysis.QuantileAnalyzer"
        ) as mock_analyzer_class:
            mock_analyzer = MagicMock()
            mock_result = MagicMock(spec=QuantileResult)
            mock_analyzer.analyze.return_value = mock_result
            mock_analyzer_class.return_value = mock_analyzer

            await service.run_quantile_analysis(
                "job-123",
                mock_fwd_provider,
                mock_calendar,
            )

        # Check the DataFrame passed to analyze has signal_value column
        analyze_call = mock_analyzer.analyze.call_args
        passed_signals = analyze_call[0][0]
        assert "signal_value" in passed_signals.columns

    async def test_renames_date_to_signal_date(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """Should rename 'date' column to 'signal_date'."""
        from libs.trading.backtest.quantile_analysis import QuantileResult

        signals_df = pl.DataFrame({
            "date": [date(2024, 1, 2)],  # Old column name
            "permno": [10001],
            "signal": [0.5],
        })
        mock_lazy = MagicMock()
        mock_lazy.collect.return_value = signals_df
        mock_storage.load_universe_signals_lazy.return_value = mock_lazy

        mock_fwd_provider = MagicMock()
        mock_fwd_provider.get_forward_returns.return_value = pl.DataFrame({
            "signal_date": [date(2024, 1, 2)],
            "permno": [10001],
            "forward_return": [0.02],
        })

        mock_calendar = MagicMock()
        mock_calendar.is_session.return_value = True

        with patch(
            "libs.trading.backtest.quantile_analysis.QuantileAnalyzer"
        ) as mock_analyzer_class:
            mock_analyzer = MagicMock()
            mock_result = MagicMock(spec=QuantileResult)
            mock_analyzer.analyze.return_value = mock_result
            mock_analyzer_class.return_value = mock_analyzer

            await service.run_quantile_analysis(
                "job-123",
                mock_fwd_provider,
                mock_calendar,
            )

        # Check the DataFrame passed to analyze has signal_date column
        analyze_call = mock_analyzer.analyze.call_args
        passed_signals = analyze_call[0][0]
        assert "signal_date" in passed_signals.columns

    async def test_missing_date_column_raises(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """Should raise InsufficientDataError when no date column."""
        from libs.trading.backtest.quantile_analysis import InsufficientDataError

        # Missing both 'date' and 'signal_date'
        signals_df = pl.DataFrame({
            "permno": [10001],
            "signal": [0.5],
        })
        mock_lazy = MagicMock()
        mock_lazy.collect.return_value = signals_df
        mock_storage.load_universe_signals_lazy.return_value = mock_lazy

        mock_fwd_provider = MagicMock()
        mock_calendar = MagicMock()

        with pytest.raises(InsufficientDataError, match="missing date column"):
            await service.run_quantile_analysis(
                "job-123",
                mock_fwd_provider,
                mock_calendar,
            )

    async def test_missing_required_columns_raises(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """Should raise InsufficientDataError when missing required columns."""
        from libs.trading.backtest.quantile_analysis import InsufficientDataError

        # Has date but missing permno
        signals_df = pl.DataFrame({
            "date": [date(2024, 1, 2)],
            "signal": [0.5],
        })
        mock_lazy = MagicMock()
        mock_lazy.collect.return_value = signals_df
        mock_storage.load_universe_signals_lazy.return_value = mock_lazy

        mock_fwd_provider = MagicMock()
        mock_calendar = MagicMock()

        with pytest.raises(InsufficientDataError, match="missing required columns"):
            await service.run_quantile_analysis(
                "job-123",
                mock_fwd_provider,
                mock_calendar,
            )

    async def test_no_forward_returns_raises(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """Should raise InsufficientDataError when no forward returns."""
        from libs.trading.backtest.quantile_analysis import InsufficientDataError

        signals_df = pl.DataFrame({
            "date": [date(2024, 1, 2)],
            "permno": [10001],
            "signal": [0.5],
        })
        mock_lazy = MagicMock()
        mock_lazy.collect.return_value = signals_df
        mock_storage.load_universe_signals_lazy.return_value = mock_lazy

        mock_fwd_provider = MagicMock()
        # Return empty DataFrame
        mock_fwd_provider.get_forward_returns.return_value = pl.DataFrame({
            "signal_date": pl.Series([], dtype=pl.Date),
            "permno": pl.Series([], dtype=pl.Int64),
            "forward_return": pl.Series([], dtype=pl.Float64),
        })

        mock_calendar = MagicMock()
        mock_calendar.is_session.return_value = True

        with pytest.raises(InsufficientDataError, match="No forward returns"):
            await service.run_quantile_analysis(
                "job-123",
                mock_fwd_provider,
                mock_calendar,
            )

    async def test_normalizes_non_trading_dates(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """Should normalize weekend dates to previous trading day."""
        from libs.trading.backtest.quantile_analysis import QuantileResult

        # Saturday date that needs normalization
        signals_df = pl.DataFrame({
            "date": [date(2024, 1, 6)],  # Saturday
            "permno": [10001],
            "signal": [0.5],
        })
        mock_lazy = MagicMock()
        mock_lazy.collect.return_value = signals_df
        mock_storage.load_universe_signals_lazy.return_value = mock_lazy

        mock_fwd_provider = MagicMock()
        mock_fwd_provider.get_forward_returns.return_value = pl.DataFrame({
            "signal_date": [date(2024, 1, 5)],  # Friday (normalized)
            "permno": [10001],
            "forward_return": [0.02],
        })

        mock_calendar = MagicMock()
        mock_calendar.is_session.side_effect = lambda d: d.weekday() < 5  # Mon-Fri
        mock_calendar.date_to_session.return_value = MagicMock(
            date=MagicMock(return_value=date(2024, 1, 5))
        )

        with patch(
            "libs.trading.backtest.quantile_analysis.QuantileAnalyzer"
        ) as mock_analyzer_class:
            mock_analyzer = MagicMock()
            mock_result = MagicMock(spec=QuantileResult)
            mock_analyzer.analyze.return_value = mock_result
            mock_analyzer_class.return_value = mock_analyzer

            result = await service.run_quantile_analysis(
                "job-123",
                mock_fwd_provider,
                mock_calendar,
            )

        # Should complete without error (date normalization worked)
        assert result == mock_result

    async def test_coerces_datetime_to_date(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """Should coerce datetime signal_date to date type."""
        from datetime import datetime

        from libs.trading.backtest.quantile_analysis import QuantileResult

        # Use datetime instead of date (needs coercion after rename)
        signals_df = pl.DataFrame({
            "signal_date": [datetime(2024, 1, 2, 10, 30)],
            "permno": [10001],
            "signal": [0.5],
        })
        mock_lazy = MagicMock()
        mock_lazy.collect.return_value = signals_df
        mock_storage.load_universe_signals_lazy.return_value = mock_lazy

        mock_fwd_provider = MagicMock()
        mock_fwd_provider.get_forward_returns.return_value = pl.DataFrame({
            "signal_date": [date(2024, 1, 2)],
            "permno": [10001],
            "forward_return": [0.02],
        })

        mock_calendar = MagicMock()
        mock_calendar.is_session.return_value = True

        with patch(
            "libs.trading.backtest.quantile_analysis.QuantileAnalyzer"
        ) as mock_analyzer_class:
            mock_analyzer = MagicMock()
            mock_result = MagicMock(spec=QuantileResult)
            mock_analyzer.analyze.return_value = mock_result
            mock_analyzer_class.return_value = mock_analyzer

            result = await service.run_quantile_analysis(
                "job-123",
                mock_fwd_provider,
                mock_calendar,
            )

        # Should complete successfully (datetime was coerced to date)
        assert result == mock_result

    async def test_passes_config_to_forward_returns(
        self,
        service: BacktestAnalyticsService,
        mock_data_access: MagicMock,
        mock_storage: MagicMock,
    ):
        """Should pass config parameters to forward returns provider."""
        from libs.trading.backtest.quantile_analysis import (
            QuantileAnalysisConfig,
            QuantileResult,
        )

        signals_df = pl.DataFrame({
            "date": [date(2024, 1, 2)],
            "permno": [10001],
            "signal": [0.5],
        })
        mock_lazy = MagicMock()
        mock_lazy.collect.return_value = signals_df
        mock_storage.load_universe_signals_lazy.return_value = mock_lazy

        mock_fwd_provider = MagicMock()
        mock_fwd_provider.get_forward_returns.return_value = pl.DataFrame({
            "signal_date": [date(2024, 1, 2)],
            "permno": [10001],
            "forward_return": [0.02],
        })

        mock_calendar = MagicMock()
        mock_calendar.is_session.return_value = True

        custom_config = QuantileAnalysisConfig(
            skip_days=2,
            holding_period_days=30,
        )

        with patch(
            "libs.trading.backtest.quantile_analysis.QuantileAnalyzer"
        ) as mock_analyzer_class:
            mock_analyzer = MagicMock()
            mock_result = MagicMock(spec=QuantileResult)
            mock_analyzer.analyze.return_value = mock_result
            mock_analyzer_class.return_value = mock_analyzer

            await service.run_quantile_analysis(
                "job-123",
                mock_fwd_provider,
                mock_calendar,
                config=custom_config,
            )

        # Check forward_returns was called with config params
        fwd_call = mock_fwd_provider.get_forward_returns.call_args
        assert fwd_call[0][1] == 2  # skip_days
        assert fwd_call[0][2] == 30  # holding_period_days


# ------------------------------------------------------------------ Service Limits Tests


@pytest.mark.unit()
class TestServiceLimits:
    """Tests for service limit constants."""

    def test_max_limit_constant(self, service: BacktestAnalyticsService):
        """MAX_UNIVERSE_SIGNALS_LIMIT should be 10000."""
        assert service.MAX_UNIVERSE_SIGNALS_LIMIT == 10000

    def test_min_limit_constant(self, service: BacktestAnalyticsService):
        """MIN_UNIVERSE_SIGNALS_LIMIT should be 1."""
        assert service.MIN_UNIVERSE_SIGNALS_LIMIT == 1
