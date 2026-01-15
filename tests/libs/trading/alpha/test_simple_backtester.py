"""Tests for SimpleBacktester (non-PIT data sources).

Tests cover:
- Data preparation with pseudo-permnos
- Forward returns computation
- Data leakage prevention
- n_days accuracy after early termination
- Error handling for empty/invalid data
"""

from __future__ import annotations

import importlib.util
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

# Check for required dependencies
_missing = [
    mod
    for mod in ("polars", "structlog")
    if importlib.util.find_spec(mod) is None
]
if _missing:
    pytest.skip(
        f"Skipping simple_backtester tests: missing {', '.join(_missing)}",
        allow_module_level=True,
    )

import polars as pl

from libs.trading.alpha.exceptions import MissingForwardReturnError
from libs.trading.alpha.metrics import AlphaMetricsAdapter
from libs.trading.alpha.simple_backtester import SimpleBacktester


@pytest.fixture()
def mock_fetcher():
    """Create a mock UnifiedDataFetcher."""
    fetcher = MagicMock()
    fetcher.get_active_provider.return_value = "yfinance"
    return fetcher


@pytest.fixture()
def mock_metrics():
    """Create a mock AlphaMetricsAdapter."""
    metrics = MagicMock(spec=AlphaMetricsAdapter)

    # Mock IC result
    ic_result = MagicMock()
    ic_result.pearson_ic = 0.05
    ic_result.rank_ic = 0.08
    metrics.compute_ic.return_value = ic_result

    # Mock ICIR result
    icir_result = MagicMock()
    icir_result.icir = 1.5
    metrics.compute_icir.return_value = icir_result

    # Mock other metrics
    metrics.compute_hit_rate.return_value = 0.55
    metrics.compute_long_short_spread.return_value = 0.02
    metrics.compute_autocorrelation.return_value = 0.3

    # Mock decay result
    decay_result = MagicMock()
    decay_result.decay_curve = {1: 0.08, 5: 0.05, 20: 0.02}
    decay_result.half_life = 10.0
    metrics.compute_decay_curve.return_value = decay_result

    return metrics


@pytest.fixture()
def sample_price_data():
    """Create sample price data for testing."""
    dates = [date(2024, 1, i) for i in range(1, 32)]  # 31 days
    symbols = ["AAPL", "MSFT", "GOOGL"]

    rows = []
    for d in dates:
        for sym in symbols:
            rows.append({
                "date": d,
                "symbol": sym,
                "open": 100.0,
                "high": 105.0,
                "low": 95.0,
                "close": 102.0,
                "adj_close": 102.0,
                "volume": 1000000,
            })

    return pl.DataFrame(rows)


class TestSimpleBacktesterInit:
    """Tests for SimpleBacktester initialization."""

    def test_init_with_default_metrics(self, mock_fetcher):
        """Test initialization creates default metrics adapter."""
        backtester = SimpleBacktester(mock_fetcher)
        assert backtester._fetcher is mock_fetcher
        assert isinstance(backtester._metrics, AlphaMetricsAdapter)

    def test_init_with_custom_metrics(self, mock_fetcher, mock_metrics):
        """Test initialization with custom metrics adapter."""
        backtester = SimpleBacktester(mock_fetcher, mock_metrics)
        assert backtester._metrics is mock_metrics


class TestPermnoMapping:
    """Tests for symbol-to-permno mapping."""

    def test_get_permno_creates_mapping(self, mock_fetcher):
        """Test that _get_permno creates unique permnos."""
        backtester = SimpleBacktester(mock_fetcher)

        permno1 = backtester._get_permno("AAPL")
        permno2 = backtester._get_permno("MSFT")
        permno3 = backtester._get_permno("AAPL")  # Same symbol

        assert permno1 == 1
        assert permno2 == 2
        assert permno3 == 1  # Same as first AAPL call

    def test_permno_bidirectional_mapping(self, mock_fetcher):
        """Test that permno mapping is bidirectional."""
        backtester = SimpleBacktester(mock_fetcher)

        backtester._get_permno("AAPL")
        backtester._get_permno("MSFT")

        assert backtester._symbol_map["AAPL"] == 1
        assert backtester._symbol_map["MSFT"] == 2
        assert backtester._permno_map[1] == "AAPL"
        assert backtester._permno_map[2] == "MSFT"


class TestDataPreparation:
    """Tests for _prepare_data method."""

    def test_prepare_data_adds_returns(self, mock_fetcher, sample_price_data):
        """Test that _prepare_data calculates returns from adj_close."""
        mock_fetcher.get_daily_prices.return_value = sample_price_data
        backtester = SimpleBacktester(mock_fetcher)

        result = backtester._prepare_data(
            start_date=date(2024, 1, 5),
            end_date=date(2024, 1, 25),
            symbols=["AAPL", "MSFT"],
        )

        assert "ret" in result.columns
        assert "permno" in result.columns
        assert "prc" in result.columns

    def test_prepare_data_empty_input(self, mock_fetcher):
        """Test handling of empty data from fetcher."""
        mock_fetcher.get_daily_prices.return_value = pl.DataFrame()
        backtester = SimpleBacktester(mock_fetcher)

        result = backtester._prepare_data(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            symbols=["AAPL"],
        )

        assert result.is_empty()


class TestForwardReturns:
    """Tests for _compute_forward_returns method."""

    def test_compute_forward_returns_success(self, mock_fetcher):
        """Test successful forward returns computation."""
        # Create price data with known returns
        dates = [date(2024, 1, i) for i in range(1, 11)]  # 10 days
        rows = []
        for d in dates:
            rows.append({
                "date": d,
                "symbol": "AAPL",
                "permno": 1,
                "ret": 0.01,  # 1% daily return
            })

        prices = pl.DataFrame(rows)
        backtester = SimpleBacktester(mock_fetcher)

        result = backtester._compute_forward_returns(
            prices, as_of_date=date(2024, 1, 1), horizon=1
        )

        assert "return" in result.columns
        assert "permno" in result.columns
        assert result.height == 1

    def test_compute_forward_returns_insufficient_data(self, mock_fetcher):
        """Test MissingForwardReturnError when insufficient data."""
        # Only 2 days of data
        prices = pl.DataFrame({
            "date": [date(2024, 1, 1), date(2024, 1, 2)],
            "symbol": ["AAPL", "AAPL"],
            "permno": [1, 1],
            "ret": [0.01, 0.01],
        })
        backtester = SimpleBacktester(mock_fetcher)

        with pytest.raises(MissingForwardReturnError):
            backtester._compute_forward_returns(
                prices, as_of_date=date(2024, 1, 2), horizon=5
            )


class TestDataLeakagePrevention:
    """Tests for data leakage prevention."""

    def test_date_index_building(self, mock_fetcher):
        """Test that _build_date_index creates correct mapping."""
        prices = pl.DataFrame({
            "date": [
                date(2024, 1, 1), date(2024, 1, 1),  # 2 rows for day 1
                date(2024, 1, 2), date(2024, 1, 2),  # 2 rows for day 2
                date(2024, 1, 3),  # 1 row for day 3
            ],
            "symbol": ["AAPL", "MSFT", "AAPL", "MSFT", "AAPL"],
        })
        backtester = SimpleBacktester(mock_fetcher)

        index = backtester._build_date_index(prices)

        assert index[date(2024, 1, 1)] == 0
        assert index[date(2024, 1, 2)] == 2
        assert index[date(2024, 1, 3)] == 4


class TestRunBacktest:
    """Tests for the main run_backtest method."""

    def test_run_backtest_empty_universe(self, mock_fetcher, mock_metrics):
        """Test error when universe produces no data."""
        mock_fetcher.get_daily_prices.return_value = pl.DataFrame()
        backtester = SimpleBacktester(mock_fetcher, mock_metrics)

        mock_alpha = MagicMock()
        mock_alpha.name = "test_alpha"

        with pytest.raises(ValueError, match="No data returned"):
            backtester.run_backtest(
                alpha=mock_alpha,
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                universe=["INVALID"],
            )

    def test_run_backtest_no_trading_days(self, mock_fetcher, mock_metrics):
        """Test error when no trading days in range."""
        # Data exists but not in requested range
        mock_fetcher.get_daily_prices.return_value = pl.DataFrame({
            "date": [date(2023, 1, 1)],  # Wrong year
            "symbol": ["AAPL"],
            "open": [100.0],
            "high": [105.0],
            "low": [95.0],
            "close": [102.0],
            "adj_close": [102.0],
            "volume": [1000000],
        })
        backtester = SimpleBacktester(mock_fetcher, mock_metrics)

        mock_alpha = MagicMock()
        mock_alpha.name = "test_alpha"

        with pytest.raises(ValueError, match="No trading days found"):
            backtester.run_backtest(
                alpha=mock_alpha,
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                universe=["AAPL"],
            )

    def test_n_days_reflects_processed_count(self, mock_fetcher, mock_metrics):
        """Test that n_days uses processed_days, not total trading days."""
        # Create data where forward returns will fail after 3 days
        dates = [date(2024, 1, i) for i in range(1, 8)]  # 7 days
        rows = []
        for d in dates:
            rows.append({
                "date": d,
                "symbol": "AAPL",
                "open": 100.0,
                "high": 105.0,
                "low": 95.0,
                "close": 102.0,
                "adj_close": 102.0,
                "volume": 1000000,
            })

        mock_fetcher.get_daily_prices.return_value = pl.DataFrame(rows)
        backtester = SimpleBacktester(mock_fetcher, mock_metrics)

        mock_alpha = MagicMock()
        mock_alpha.name = "test_alpha"
        mock_alpha.compute.return_value = pl.DataFrame({
            "date": [date(2024, 1, 1)],
            "permno": [1],
            "signal": [0.5],
        })

        # The backtest should stop early due to forward return limitations
        # We mock to verify n_days matches processed count
        with patch.object(
            backtester,
            "_compute_forward_returns",
            side_effect=[
                pl.DataFrame({"permno": [1], "return": [0.01], "date": [date(2024, 1, 1)]}),
                pl.DataFrame({"permno": [1], "return": [0.01], "date": [date(2024, 1, 2)]}),
                MissingForwardReturnError("No more data"),
            ],
        ):
            result = backtester.run_backtest(
                alpha=mock_alpha,
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 7),
                universe=["AAPL"],
            )

            # Should only count the 2 successfully processed days
            assert result.n_days == 2


class TestResultMetadata:
    """Tests for BacktestResult metadata fields."""

    def test_dataset_version_ids_structure(self, mock_fetcher, mock_metrics):
        """Test that dataset_version_ids has correct structure."""
        dates = [date(2024, 1, i) for i in range(1, 15)]
        rows = []
        for d in dates:
            rows.append({
                "date": d,
                "symbol": "AAPL",
                "open": 100.0,
                "high": 105.0,
                "low": 95.0,
                "close": 102.0,
                "adj_close": 102.0,
                "volume": 1000000,
            })

        mock_fetcher.get_daily_prices.return_value = pl.DataFrame(rows)
        backtester = SimpleBacktester(mock_fetcher, mock_metrics)

        mock_alpha = MagicMock()
        mock_alpha.name = "test_alpha"
        mock_alpha.compute.return_value = pl.DataFrame({
            "date": [date(2024, 1, 1)],
            "permno": [1],
            "signal": [0.5],
        })

        with patch.object(
            backtester,
            "_compute_forward_returns",
            return_value=pl.DataFrame({
                "permno": [1],
                "return": [0.01],
                "date": [date(2024, 1, 1)],
            }),
        ):
            result = backtester.run_backtest(
                alpha=mock_alpha,
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 5),
                universe=["AAPL"],
            )

        # Check dataset_version_ids structure
        assert "provider_type" in result.dataset_version_ids
        assert result.dataset_version_ids["provider_type"] == "yfinance"
        assert result.dataset_version_ids["pit_compliant"] == "false"
        assert result.dataset_version_ids["version"] == "N/A"

        # Check snapshot_id format
        assert result.snapshot_id.startswith("yfinance-simple-")


class TestCallbacks:
    """Tests for progress and cancellation callbacks."""

    def test_invoke_callbacks_rate_limiting(self, mock_fetcher):
        """Test that callbacks are rate-limited."""
        backtester = SimpleBacktester(mock_fetcher)
        progress_callback = MagicMock()
        cancel_check = MagicMock()

        # First call should invoke
        t1 = backtester._invoke_callbacks(
            progress_callback, cancel_check, 0.0, 50, date(2024, 1, 1)
        )

        # Immediate second call should be rate-limited (return early)
        _t2 = backtester._invoke_callbacks(
            progress_callback, cancel_check, t1, 60, date(2024, 1, 2)
        )

        # Progress should only be called once
        assert progress_callback.call_count == 1
        # But cancel_check should always be called
        assert cancel_check.call_count == 2

    def test_invoke_callbacks_force_bypass_rate_limit(self, mock_fetcher):
        """Test that force=True bypasses rate limiting."""
        backtester = SimpleBacktester(mock_fetcher)
        progress_callback = MagicMock()

        t1 = backtester._invoke_callbacks(
            progress_callback, None, 0.0, 50, date(2024, 1, 1)
        )
        # Force should invoke even though within rate limit
        backtester._invoke_callbacks(
            progress_callback, None, t1, 60, date(2024, 1, 2), force=True
        )

        assert progress_callback.call_count == 2
