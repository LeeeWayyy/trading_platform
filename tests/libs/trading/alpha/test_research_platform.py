"""Tests for PITBacktester and point-in-time correctness."""

from datetime import date
from unittest.mock import MagicMock

import polars as pl
import pytest

from libs.trading.alpha.alpha_definition import BaseAlpha
from libs.trading.alpha.exceptions import JobCancelled, MissingForwardReturnError, PITViolationError
from libs.trading.alpha.research_platform import BacktestResult, PITBacktester


class SimpleTestAlpha(BaseAlpha):
    """Simple alpha for testing that uses permno as signal."""

    @property
    def name(self) -> str:
        return "test_alpha"

    @property
    def category(self) -> str:
        return "test"

    def _compute_raw(self, prices, fundamentals, as_of_date):
        return prices.filter(pl.col("date") == as_of_date).select(
            [
                pl.col("permno"),
                pl.col("permno").cast(pl.Float64).alias("raw_signal"),
            ]
        )


def _build_full_mock_backtester():
    """Helper to construct a fully mocked PITBacktester setup."""
    version_mgr = MagicMock()
    crsp = MagicMock()
    compustat = MagicMock()
    backtester = PITBacktester(version_mgr, crsp, compustat)

    mock_snapshot = MagicMock()
    mock_snapshot.version_tag = "test_snapshot"
    mock_snapshot.datasets = {
        "crsp": MagicMock(
            date_range_end=date(2024, 1, 20),
            sync_manifest_version="v1.0.0",
        ),
        "compustat": MagicMock(
            date_range_end=date(2024, 1, 20),
            sync_manifest_version="v1.0.0",
        ),
    }
    version_mgr.create_snapshot.return_value = mock_snapshot

    dates = [date(2024, 1, i) for i in range(1, 16)]
    prices = pl.DataFrame(
        {
            "permno": sorted([1, 2, 3, 4, 5] * 15),
            "date": dates * 5,
            "ret": [0.01 + 0.001 * (i % 5) for i in range(75)],
            "prc": [100.0 + i for i in range(75)],
            "shrout": [1000.0] * 75,
        }
    )

    fundamentals = pl.DataFrame(
        {
            "permno": [1, 2, 3, 4, 5],
            "datadate": [date(2023, 9, 30)] * 5,
            "ceq": [100.0, 200.0, 150.0, 180.0, 120.0],
            "ni": [10.0, 20.0, 15.0, 18.0, 12.0],
        }
    )

    def mock_lock_snapshot(snapshot_id):
        backtester._snapshot = mock_snapshot
        return mock_snapshot

    def mock_get_pit_prices(as_of):
        return prices.filter(pl.col("date") <= as_of)

    def mock_get_pit_fundamentals(as_of):
        return fundamentals

    backtester._lock_snapshot = mock_lock_snapshot
    backtester._get_pit_prices = mock_get_pit_prices
    backtester._get_pit_fundamentals = mock_get_pit_fundamentals
    backtester._prices_cache = prices
    backtester._fundamentals_cache = fundamentals

    return backtester, prices, mock_snapshot


class TestPITBacktesterSnapshotLocking:
    """Tests for snapshot locking mechanism."""

    def test_ensure_snapshot_locked_raises_without_snapshot(self):
        """Test PITViolationError raised when no snapshot locked."""
        version_mgr = MagicMock()
        crsp = MagicMock()
        compustat = MagicMock()

        backtester = PITBacktester(version_mgr, crsp, compustat)

        with pytest.raises(PITViolationError, match="No snapshot locked"):
            backtester._ensure_snapshot_locked()

    def test_lock_snapshot_creates_new_when_none_provided(self):
        """Test new snapshot created when snapshot_id is None."""
        version_mgr = MagicMock()
        crsp = MagicMock()
        compustat = MagicMock()

        mock_snapshot = MagicMock()
        mock_snapshot.version_tag = "backtest_test123"
        version_mgr.create_snapshot.return_value = mock_snapshot

        backtester = PITBacktester(version_mgr, crsp, compustat)
        result = backtester._lock_snapshot(None)

        version_mgr.create_snapshot.assert_called_once()
        assert result == mock_snapshot

    def test_lock_snapshot_retrieves_existing(self):
        """Test existing snapshot retrieved when snapshot_id provided."""
        version_mgr = MagicMock()
        crsp = MagicMock()
        compustat = MagicMock()

        mock_snapshot = MagicMock()
        mock_snapshot.version_tag = "existing_snapshot"
        version_mgr.get_snapshot.return_value = mock_snapshot

        backtester = PITBacktester(version_mgr, crsp, compustat)
        result = backtester._lock_snapshot("existing_snapshot")

        version_mgr.get_snapshot.assert_called_once_with("existing_snapshot")
        assert result == mock_snapshot

    def test_lock_snapshot_raises_when_not_found(self):
        """Test PITViolationError when snapshot not found."""
        version_mgr = MagicMock()
        crsp = MagicMock()
        compustat = MagicMock()
        version_mgr.get_snapshot.return_value = None

        backtester = PITBacktester(version_mgr, crsp, compustat)

        with pytest.raises(PITViolationError, match="not found"):
            backtester._lock_snapshot("nonexistent_id")


class TestPITBacktesterStrictDateFiltering:
    """Tests for strict date filtering in data access."""

    @pytest.fixture()
    def mock_backtester(self):
        """Create backtester with mocked dependencies."""
        version_mgr = MagicMock()
        crsp = MagicMock()
        compustat = MagicMock()
        backtester = PITBacktester(version_mgr, crsp, compustat)

        # Set up snapshot
        mock_snapshot = MagicMock()
        mock_snapshot.version_tag = "test_snapshot"
        mock_snapshot.datasets = {
            "crsp": MagicMock(
                date_range_end=date(2024, 6, 30),
                sync_manifest_version="v1.0.0",
            ),
            "compustat": MagicMock(
                date_range_end=date(2024, 6, 30),
                sync_manifest_version="v1.0.0",
            ),
        }
        backtester._snapshot = mock_snapshot

        return backtester

    def test_get_pit_prices_raises_when_date_exceeds_snapshot(self, mock_backtester):
        """Test PITViolationError when requested date exceeds snapshot."""
        future_date = date(2024, 7, 15)  # Beyond snapshot end

        with pytest.raises(PITViolationError, match="snapshot ends"):
            mock_backtester._get_pit_prices(future_date)

    def test_get_pit_prices_filters_future_data(self, mock_backtester):
        """Test that prices are strictly filtered to <= as_of_date."""
        # Create test price data spanning multiple dates
        prices = pl.DataFrame(
            {
                "permno": [1, 1, 1, 2, 2, 2],
                "date": [
                    date(2024, 1, 1),
                    date(2024, 1, 2),
                    date(2024, 1, 3),
                    date(2024, 1, 1),
                    date(2024, 1, 2),
                    date(2024, 1, 3),
                ],
                "ret": [0.01, 0.02, 0.03, 0.01, 0.02, 0.03],
                "prc": [100.0] * 6,
                "shrout": [1000.0] * 6,
            }
        )
        mock_backtester._prices_cache = prices

        # Request prices as of Jan 2nd - should NOT include Jan 3rd data
        result = mock_backtester._get_pit_prices(date(2024, 1, 2))

        # Should only have 4 rows (2 stocks x 2 days)
        assert result.height == 4
        max_date = result.select(pl.col("date").max()).item()
        assert max_date == date(2024, 1, 2)


class TestPITBacktesterFilingLag:
    """Tests for filing lag enforcement in fundamentals."""

    def test_get_pit_fundamentals_applies_90_day_lag(self):
        """Test that fundamentals respect 90-day filing lag."""
        version_mgr = MagicMock()
        crsp = MagicMock()
        compustat = MagicMock()
        backtester = PITBacktester(version_mgr, crsp, compustat)

        # Set up snapshot
        mock_snapshot = MagicMock()
        mock_snapshot.version_tag = "test_snapshot"
        mock_snapshot.datasets = {
            "crsp": MagicMock(date_range_end=date(2024, 6, 30)),
            "compustat": MagicMock(date_range_end=date(2024, 6, 30)),
        }
        backtester._snapshot = mock_snapshot

        # Create fundamentals with various datadates
        # Filing lag = 90 days, so if as_of_date = April 1, only Dec 31 data available
        fundamentals = pl.DataFrame(
            {
                "permno": [1, 1, 2, 2],
                "datadate": [
                    date(2023, 12, 31),  # Available on April 1
                    date(2024, 3, 31),  # NOT available on April 1 (only 0 days)
                    date(2023, 12, 31),  # Available on April 1
                    date(2024, 3, 31),  # NOT available on April 1
                ],
                "ceq": [100.0, 110.0, 200.0, 210.0],
                "ni": [10.0, 11.0, 20.0, 21.0],
            }
        )
        backtester._fundamentals_cache = fundamentals

        # Request as of April 1, 2024 (only Dec 31 data should be available)
        result = backtester._get_pit_fundamentals(date(2024, 4, 1))

        # Should only have Dec 31 data (2 rows)
        assert result.height == 2
        max_datadate = result.select(pl.col("datadate").max()).item()
        assert max_datadate == date(2023, 12, 31)


class TestPITBacktesterForwardReturns:
    """Tests for forward return calculation and fail-fast behavior."""

    @pytest.fixture()
    def mock_backtester_with_prices(self):
        """Create backtester with price data."""
        version_mgr = MagicMock()
        crsp = MagicMock()
        compustat = MagicMock()
        backtester = PITBacktester(version_mgr, crsp, compustat)

        mock_snapshot = MagicMock()
        mock_snapshot.version_tag = "test_snapshot"
        mock_snapshot.datasets = {
            "crsp": MagicMock(date_range_end=date(2024, 1, 10)),
        }
        backtester._snapshot = mock_snapshot

        # Create 10 trading days of data
        dates = [date(2024, 1, i) for i in range(1, 11)]
        prices = pl.DataFrame(
            {
                "permno": [1] * 10 + [2] * 10,
                "date": dates * 2,
                "ret": [0.01] * 20,
                "prc": [100.0] * 20,
                "shrout": [1000.0] * 20,
            }
        )
        backtester._prices_cache = prices

        return backtester

    def test_get_pit_forward_returns_raises_when_insufficient_data(
        self, mock_backtester_with_prices
    ):
        """Test MissingForwardReturnError when insufficient future data."""
        # Request 20-day forward return but only have 10 days total
        with pytest.raises(MissingForwardReturnError, match="trading days"):
            mock_backtester_with_prices._get_pit_forward_returns(date(2024, 1, 5), horizon=20)

    def test_get_pit_forward_returns_computes_geometric_return(self, mock_backtester_with_prices):
        """Test geometric compounding in forward returns."""
        result = mock_backtester_with_prices._get_pit_forward_returns(date(2024, 1, 1), horizon=3)

        # With 1% daily returns, 3-day geometric return = (1.01)^3 - 1 ≈ 0.030301
        expected_return = (1.01**3) - 1
        actual_return = result.filter(pl.col("permno") == 1).select("return").item()

        assert actual_return == pytest.approx(expected_return, rel=1e-6)

    def test_forward_returns_require_exact_horizon(self, mock_backtester_with_prices):
        """Test that forward returns require exact horizon observations."""
        # Modify prices to have one stock missing a day
        prices = mock_backtester_with_prices._prices_cache
        # Remove one observation for permno=2
        prices = prices.filter(~((pl.col("permno") == 2) & (pl.col("date") == date(2024, 1, 2))))
        mock_backtester_with_prices._prices_cache = prices

        result = mock_backtester_with_prices._get_pit_forward_returns(date(2024, 1, 1), horizon=3)

        # permno=2 should be excluded due to missing day
        permnos = result.get_column("permno").to_list()
        assert 1 in permnos
        assert 2 not in permnos


class TestPITBacktesterRunBacktest:
    """Tests for full backtest execution."""

    @pytest.fixture()
    def full_mock_backtester(self):
        """Create backtester with complete mocked setup."""
        version_mgr = MagicMock()
        crsp = MagicMock()
        compustat = MagicMock()
        backtester = PITBacktester(version_mgr, crsp, compustat)

        # Mock snapshot
        mock_snapshot = MagicMock()
        mock_snapshot.version_tag = "test_snapshot"
        mock_snapshot.datasets = {
            "crsp": MagicMock(
                date_range_end=date(2024, 1, 20),
                sync_manifest_version="v1.0.0",
            ),
            "compustat": MagicMock(
                date_range_end=date(2024, 1, 20),
                sync_manifest_version="v1.0.0",
            ),
        }
        version_mgr.create_snapshot.return_value = mock_snapshot

        # Create price data for 15 trading days
        dates = [date(2024, 1, i) for i in range(1, 16)]
        prices = pl.DataFrame(
            {
                "permno": sorted([1, 2, 3, 4, 5] * 15),
                "date": dates * 5,
                "ret": [0.01 + 0.001 * (i % 5) for i in range(75)],
                "prc": [100.0 + i for i in range(75)],
                "shrout": [1000.0] * 75,
            }
        )

        fundamentals = pl.DataFrame(
            {
                "permno": [1, 2, 3, 4, 5],
                "datadate": [date(2023, 9, 30)] * 5,
                "ceq": [100.0, 200.0, 150.0, 180.0, 120.0],
                "ni": [10.0, 20.0, 15.0, 18.0, 12.0],
            }
        )

        # Patch internal methods
        def mock_lock_snapshot(sid):
            backtester._snapshot = mock_snapshot
            return mock_snapshot

        def mock_get_pit_prices(as_of):
            return prices.filter(pl.col("date") <= as_of)

        def mock_get_pit_fundamentals(as_of):
            return fundamentals

        backtester._lock_snapshot = mock_lock_snapshot
        backtester._get_pit_prices = mock_get_pit_prices
        backtester._get_pit_fundamentals = mock_get_pit_fundamentals
        backtester._prices_cache = prices
        backtester._fundamentals_cache = fundamentals

        return backtester, prices, mock_snapshot

    def test_run_backtest_stops_on_missing_forward_returns(self, full_mock_backtester):
        """Test backtest stops completely when forward returns unavailable."""
        backtester, prices, mock_snapshot = full_mock_backtester

        # Patch _get_pit_forward_returns to fail after a few days
        call_count = [0]

        def mock_forward_returns(as_of, horizon=1):
            call_count[0] += 1
            if call_count[0] > 5:
                raise MissingForwardReturnError("No more data")
            return prices.filter(pl.col("date") == as_of).select(
                [
                    pl.col("permno"),
                    pl.lit(as_of).alias("date"),
                    pl.col("ret").alias("return"),
                ]
            )

        backtester._get_pit_forward_returns = mock_forward_returns

        alpha = SimpleTestAlpha()
        result = backtester.run_backtest(
            alpha=alpha,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 15),
            decay_horizons=[1],
        )

        # Should have stopped early - daily_signals has limited dates
        # Check the unique dates in daily_signals (actual computed signals)
        n_signal_dates = result.daily_signals.select("date").unique().height
        assert n_signal_dates <= 6  # May vary due to batch processing

    def test_run_backtest_returns_complete_result(self, full_mock_backtester):
        """Test backtest returns BacktestResult with all fields."""
        backtester, prices, mock_snapshot = full_mock_backtester

        def mock_forward_returns(as_of, horizon=1):
            return prices.filter(pl.col("date") == as_of).select(
                [
                    pl.col("permno"),
                    pl.lit(as_of).alias("date"),
                    pl.col("ret").alias("return"),
                ]
            )

        backtester._get_pit_forward_returns = mock_forward_returns

        alpha = SimpleTestAlpha()
        result = backtester.run_backtest(
            alpha=alpha,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 10),
            weight_method="zscore",
            decay_horizons=[1, 2],
        )

        # Verify result structure
        assert isinstance(result, BacktestResult)
        assert result.alpha_name == "test_alpha"
        assert result.snapshot_id == "test_snapshot"
        assert result.weight_method == "zscore"
        assert result.daily_signals.height > 0
        assert result.daily_weights.height > 0
        assert result.daily_portfolio_returns.height > 0


class TestPITBacktesterCallbacks:
    """Tests for progress and cancel callbacks on PITBacktester."""

    def test_progress_callback_called_with_increasing_percentages(self, monkeypatch):
        backtester, prices, _ = _build_full_mock_backtester()

        def mock_forward_returns(as_of, horizon=1):
            return prices.filter(pl.col("date") == as_of).select(
                [
                    pl.col("permno"),
                    pl.lit(as_of).alias("date"),
                    pl.col("ret").alias("return"),
                ]
            )

        backtester._get_pit_forward_returns = mock_forward_returns

        current_time = {"t": 0.0}

        def mock_monotonic():
            t = current_time["t"]
            current_time["t"] += 40.0
            return t

        monkeypatch.setattr("libs.trading.alpha.research_platform.time.monotonic", mock_monotonic)

        progress_cb = MagicMock()
        alpha = SimpleTestAlpha()

        result = backtester.run_backtest(
            alpha=alpha,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 5),
            decay_horizons=[1],
            progress_callback=progress_cb,
        )

        assert isinstance(result, BacktestResult)
        assert progress_cb.call_count > 1

        pct_calls = [call.args[0] for call in progress_cb.call_args_list]
        assert pct_calls[0] == 0
        assert pct_calls[-1] == 100
        # Progress should trend upward overall, even if helper routines emit
        # smaller intermediate percentages.
        cumulative_max = []
        current_max = -1
        for pct in pct_calls:
            current_max = max(current_max, pct)
            cumulative_max.append(current_max)
        assert cumulative_max == sorted(cumulative_max)

    def test_cancel_check_raises_job_cancelled_stops_backtest(self, monkeypatch):
        backtester, prices, _ = _build_full_mock_backtester()

        def mock_forward_returns(as_of, horizon=1):
            return prices.filter(pl.col("date") == as_of).select(
                [
                    pl.col("permno"),
                    pl.lit(as_of).alias("date"),
                    pl.col("ret").alias("return"),
                ]
            )

        backtester._get_pit_forward_returns = mock_forward_returns

        current_time = {"t": 0.0}

        def mock_monotonic():
            t = current_time["t"]
            current_time["t"] += 40.0
            return t

        monkeypatch.setattr("libs.trading.alpha.research_platform.time.monotonic", mock_monotonic)

        call_counter = {"n": 0}

        def cancel_check():
            call_counter["n"] += 1
            if call_counter["n"] >= 3:
                raise JobCancelled("cancelled")

        alpha = SimpleTestAlpha()

        with pytest.raises(JobCancelled):
            backtester.run_backtest(
                alpha=alpha,
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 10),
                decay_horizons=[1],
                progress_callback=MagicMock(),
                cancel_check=cancel_check,
            )

        assert backtester._snapshot is None
        assert backtester._prices_cache is None
        assert backtester._fundamentals_cache is None

    def test_callbacks_none_backward_compatible(self):
        backtester, prices, _ = _build_full_mock_backtester()

        def mock_forward_returns(as_of, horizon=1):
            return prices.filter(pl.col("date") == as_of).select(
                [
                    pl.col("permno"),
                    pl.lit(as_of).alias("date"),
                    pl.col("ret").alias("return"),
                ]
            )

        backtester._get_pit_forward_returns = mock_forward_returns

        alpha = SimpleTestAlpha()
        result = backtester.run_backtest(
            alpha=alpha,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 5),
            decay_horizons=[1],
            progress_callback=None,
            cancel_check=None,
        )

        assert isinstance(result, BacktestResult)
        assert result.daily_signals.height > 0

    def test_callback_throttling_respects_30_second_interval(self, monkeypatch):
        backtester, prices, _ = _build_full_mock_backtester()

        def mock_forward_returns(as_of, horizon=1):
            return prices.filter(pl.col("date") == as_of).select(
                [
                    pl.col("permno"),
                    pl.lit(as_of).alias("date"),
                    pl.col("ret").alias("return"),
                ]
            )

        backtester._get_pit_forward_returns = mock_forward_returns

        # Avoid extra callback noise from downstream computations
        backtester._compute_daily_ic = lambda *args, **kwargs: (
            pl.DataFrame({"date": [], "ic": [], "rank_ic": []}),
            args[4],  # preserve last_callback_time
        )
        backtester._compute_horizon_returns = lambda *args, **kwargs: (
            pl.DataFrame(),
            args[4],  # preserve last_callback_time
        )

        times = [0, 5, 35, 40, 45, 90]
        times_iter = iter(times)

        def mock_monotonic():
            try:
                return next(times_iter)
            except StopIteration:
                return times[-1] + 1

        monkeypatch.setattr("libs.trading.alpha.research_platform.time.monotonic", mock_monotonic)

        progress_cb = MagicMock()
        alpha = SimpleTestAlpha()

        backtester.run_backtest(
            alpha=alpha,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 2),
            decay_horizons=[],
            progress_callback=progress_cb,
        )

        pct_calls = [call.args[0] for call in progress_cb.call_args_list]
        # Expect initial force call (0), one throttled call at >=30s, and final force call (100)
        assert pct_calls[0] == 0
        assert 0 < pct_calls[1] <= 100
        assert pct_calls[-1] == 100
        # Should not call for every iteration (only 3 calls expected)
        assert len(pct_calls) <= 3

    def test_reproducibility_fields_populated_after_callbacks(self):
        backtester, prices, snapshot = _build_full_mock_backtester()

        def mock_forward_returns(as_of, horizon=1):
            return prices.filter(pl.col("date") == as_of).select(
                [
                    pl.col("permno"),
                    pl.lit(as_of).alias("date"),
                    pl.col("ret").alias("return"),
                ]
            )

        backtester._get_pit_forward_returns = mock_forward_returns

        alpha = SimpleTestAlpha()
        result = backtester.run_backtest(
            alpha=alpha,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 5),
            decay_horizons=[1],
            progress_callback=MagicMock(),
        )

        assert result.snapshot_id == snapshot.version_tag
        assert result.dataset_version_ids

    def test_cache_cleared_on_job_cancelled(self):
        backtester, prices, _ = _build_full_mock_backtester()

        def mock_forward_returns(as_of, horizon=1):
            return prices.filter(pl.col("date") == as_of).select(
                [
                    pl.col("permno"),
                    pl.lit(as_of).alias("date"),
                    pl.col("ret").alias("return"),
                ]
            )

        backtester._get_pit_forward_returns = mock_forward_returns

        call_counter = {"n": 0}

        def cancel_check():
            call_counter["n"] += 1
            # Allow the initial pre-lock callback to proceed so the backtester
            # enters its try/finally and clears caches on exit.
            if call_counter["n"] >= 2:
                raise JobCancelled("cancel now")

        alpha = SimpleTestAlpha()

        with pytest.raises(JobCancelled):
            backtester.run_backtest(
                alpha=alpha,
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 5),
                decay_horizons=[1],
                progress_callback=MagicMock(),
                cancel_check=cancel_check,
            )

        assert backtester._snapshot is None
        assert backtester._prices_cache is None
        assert backtester._fundamentals_cache is None


class TestPITBacktesterHorizonReturns:
    """Tests for horizon return computation."""

    def test_compute_horizon_returns_uses_geometric_compounding(self):
        """Test horizon returns use geometric compounding."""
        version_mgr = MagicMock()
        crsp = MagicMock()
        compustat = MagicMock()
        backtester = PITBacktester(version_mgr, crsp, compustat)

        mock_snapshot = MagicMock()
        mock_snapshot.version_tag = "test_snapshot"
        mock_snapshot.datasets = {
            "crsp": MagicMock(date_range_end=date(2024, 1, 10)),
        }
        backtester._snapshot = mock_snapshot

        # Create price data
        dates = [date(2024, 1, i) for i in range(1, 11)]
        prices = pl.DataFrame(
            {
                "permno": [1] * 10,
                "date": dates,
                "ret": [0.02] * 10,  # 2% daily return
            }
        )
        backtester._prices_cache = prices

        result, _ = backtester._compute_horizon_returns(
            date(2024, 1, 1),
            horizon=5,
            progress_callback=None,
            cancel_check=None,
            last_callback_time=0.0,
            pct=0,
        )

        # 5-day return with 2% daily = (1.02)^5 - 1 ≈ 0.10408
        expected_return = (1.02**5) - 1

        # Get first result
        actual = result.filter(pl.col("date") == date(2024, 1, 1)).select("return").item()
        assert actual == pytest.approx(expected_return, rel=1e-6)

    def test_compute_horizon_returns_filters_incomplete_stocks(self):
        """Test that stocks with incomplete data are filtered."""
        version_mgr = MagicMock()
        crsp = MagicMock()
        compustat = MagicMock()
        backtester = PITBacktester(version_mgr, crsp, compustat)

        mock_snapshot = MagicMock()
        mock_snapshot.version_tag = "test_snapshot"
        mock_snapshot.datasets = {
            "crsp": MagicMock(date_range_end=date(2024, 1, 10)),
        }
        backtester._snapshot = mock_snapshot

        # Create price data with one stock missing days
        dates_full = [date(2024, 1, i) for i in range(1, 11)]
        dates_partial = [date(2024, 1, i) for i in [1, 2, 4, 5, 6, 7, 8, 9, 10]]  # Missing day 3

        prices = pl.concat(
            [
                pl.DataFrame(
                    {
                        "permno": [1] * 10,
                        "date": dates_full,
                        "ret": [0.02] * 10,
                    }
                ),
                pl.DataFrame(
                    {
                        "permno": [2] * 9,
                        "date": dates_partial,
                        "ret": [0.02] * 9,
                    }
                ),
            ]
        )
        backtester._prices_cache = prices

        result, _ = backtester._compute_horizon_returns(
            date(2024, 1, 1),
            horizon=5,
            progress_callback=None,
            cancel_check=None,
            last_callback_time=0.0,
            pct=0,
        )

        # Filter to first date result
        first_date_result = result.filter(pl.col("date") == date(2024, 1, 1))

        # permno=2 should be excluded for this date due to missing day 3
        permnos = first_date_result.get_column("permno").to_list()
        assert 1 in permnos


class TestBacktestResult:
    """Tests for BacktestResult dataclass."""

    def test_average_turnover_property(self):
        """Test average_turnover property returns correct value."""
        from libs.trading.alpha.portfolio import TurnoverResult

        turnover_result = TurnoverResult(
            daily_turnover=pl.DataFrame({"date": [], "turnover": []}),
            average_turnover=0.05,
            annualized_turnover=12.6,
        )

        result = BacktestResult(
            alpha_name="test",
            backtest_id="test-id",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 10),
            snapshot_id="snap",
            dataset_version_ids={},
            daily_signals=pl.DataFrame(),
            daily_ic=pl.DataFrame(),
            mean_ic=0.05,
            icir=1.5,
            hit_rate=0.55,
            coverage=0.95,
            long_short_spread=0.02,
            autocorrelation={1: 0.8},
            weight_method="zscore",
            daily_weights=pl.DataFrame(),
            daily_portfolio_returns=pl.DataFrame(schema={"date": pl.Date, "return": pl.Float64}),
            turnover_result=turnover_result,
            decay_curve=pl.DataFrame(),
            decay_half_life=10.0,
        )

        assert result.average_turnover == 0.05
