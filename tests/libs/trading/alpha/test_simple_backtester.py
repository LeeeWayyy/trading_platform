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
_missing = [mod for mod in ("polars", "structlog") if importlib.util.find_spec(mod) is None]
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
            rows.append(
                {
                    "date": d,
                    "symbol": sym,
                    "open": 100.0,
                    "high": 105.0,
                    "low": 95.0,
                    "close": 102.0,
                    "adj_close": 102.0,
                    "volume": 1000000,
                }
            )

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

    def test_prepare_data_falls_back_to_close_when_adj_close_is_null(self, mock_fetcher):
        """Test non-SIP data can compute returns when adjusted closes are unavailable."""
        mock_fetcher.get_daily_prices.return_value = pl.DataFrame(
            [
                {
                    "date": date(2024, 1, 1),
                    "symbol": "AAPL",
                    "open": 100.0,
                    "high": 100.0,
                    "low": 100.0,
                    "close": 100.0,
                    "adj_close": None,
                    "volume": 1000000,
                },
                {
                    "date": date(2024, 1, 2),
                    "symbol": "AAPL",
                    "open": 110.0,
                    "high": 110.0,
                    "low": 110.0,
                    "close": 110.0,
                    "adj_close": None,
                    "volume": 1000000,
                },
                {
                    "date": date(2024, 1, 3),
                    "symbol": "AAPL",
                    "open": 121.0,
                    "high": 121.0,
                    "low": 121.0,
                    "close": 121.0,
                    "adj_close": None,
                    "volume": 1000000,
                },
            ]
        )
        backtester = SimpleBacktester(mock_fetcher)

        result = backtester._prepare_data(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 3),
            symbols=["AAPL"],
        ).sort("date")

        returns = result["ret"].to_list()
        assert returns[0] is None
        assert returns[1] == pytest.approx(0.1)
        assert returns[2] == pytest.approx(0.1)
        assert result["prc"].to_list() == [100.0, 110.0, 121.0]

    def test_prepare_data_zero_previous_price_does_not_emit_infinite_return(self, mock_fetcher):
        mock_fetcher.get_daily_prices.return_value = pl.DataFrame(
            [
                {
                    "date": date(2024, 1, 1),
                    "symbol": "AAPL",
                    "open": 0.0,
                    "high": 0.0,
                    "low": 0.0,
                    "close": 0.0,
                    "adj_close": None,
                    "volume": 1000000,
                },
                {
                    "date": date(2024, 1, 2),
                    "symbol": "AAPL",
                    "open": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "close": 10.0,
                    "adj_close": None,
                    "volume": 1000000,
                },
            ]
        )
        backtester = SimpleBacktester(mock_fetcher)

        result = backtester._prepare_data(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 2),
            symbols=["AAPL"],
        ).sort("date")

        assert result["ret"].to_list() == [None, None]

    def test_prepare_data_does_not_mix_partial_adjusted_close_with_raw_close(self, mock_fetcher):
        """Test partially adjusted non-SIP data does not compute mixed-series returns."""
        mock_fetcher.get_daily_prices.return_value = pl.DataFrame(
            [
                {
                    "date": date(2024, 1, 1),
                    "symbol": "AAPL",
                    "open": 100.0,
                    "high": 100.0,
                    "low": 100.0,
                    "close": 100.0,
                    "adj_close": 100.0,
                    "volume": 1000000,
                },
                {
                    "date": date(2024, 1, 2),
                    "symbol": "AAPL",
                    "open": 110.0,
                    "high": 110.0,
                    "low": 110.0,
                    "close": 110.0,
                    "adj_close": None,
                    "volume": 1000000,
                },
                {
                    "date": date(2024, 1, 3),
                    "symbol": "AAPL",
                    "open": 121.0,
                    "high": 121.0,
                    "low": 121.0,
                    "close": 121.0,
                    "adj_close": 121.0,
                    "volume": 1000000,
                },
            ]
        )
        backtester = SimpleBacktester(mock_fetcher)

        result = backtester._prepare_data(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 3),
            symbols=["AAPL"],
        ).sort("date")

        assert result["ret"].to_list() == [None, None, None]
        assert result["prc"].to_list() == [100.0, 110.0, 121.0]

    def test_prepare_data_does_not_fill_crsp_missing_ret_from_raw_close(self, mock_fetcher):
        """Test CRSP-like raw close is not used when adjusted returns are missing."""
        mock_fetcher.get_active_provider.return_value = "crsp"
        mock_fetcher.get_daily_prices.return_value = pl.DataFrame(
            [
                {
                    "date": date(2024, 1, 1),
                    "symbol": "AAPL",
                    "open": None,
                    "high": None,
                    "low": None,
                    "close": 100.0,
                    "adj_close": None,
                    "volume": 1000000,
                },
                {
                    "date": date(2024, 1, 2),
                    "symbol": "AAPL",
                    "open": None,
                    "high": None,
                    "low": None,
                    "close": 110.0,
                    "adj_close": None,
                    "volume": 1000000,
                },
            ]
        )
        backtester = SimpleBacktester(mock_fetcher)

        result = backtester._prepare_data(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 2),
            symbols=["AAPL"],
        ).sort("date")

        assert result["ret"].to_list() == [None, None]
        assert result["prc"].to_list() == [100.0, 110.0]

    def test_prepare_data_rejects_sip_without_adjusted_close(self, mock_fetcher):
        """Test raw SIP snapshots cannot silently drive split-unsafe backtests."""
        mock_fetcher.get_active_provider.return_value = "alpaca_sip"
        mock_fetcher.get_daily_prices.return_value = pl.DataFrame(
            [
                {
                    "date": date(2024, 1, 1),
                    "symbol": "AAPL",
                    "open": 100.0,
                    "high": 100.0,
                    "low": 100.0,
                    "close": 100.0,
                    "adj_close": None,
                    "volume": 1000000,
                },
                {
                    "date": date(2024, 1, 2),
                    "symbol": "AAPL",
                    "open": 25.0,
                    "high": 25.0,
                    "low": 25.0,
                    "close": 25.0,
                    "adj_close": None,
                    "volume": 4000000,
                },
            ]
        )
        backtester = SimpleBacktester(mock_fetcher)

        with pytest.raises(ValueError, match="Raw SIP-priced backtests via alpaca_sip"):
            backtester._prepare_data(
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 2),
                symbols=["AAPL"],
            )

    def test_prepare_data_allows_hybrid_without_adjusted_close_when_ret_present(
        self,
        mock_fetcher,
    ):
        """Hybrid CRSP/SIP routes may rely on provider returns without adjusted close."""
        mock_fetcher.get_active_provider.return_value = "hybrid_crsp_universe_sip_prices"
        mock_fetcher.get_daily_prices.return_value = pl.DataFrame(
            [
                {
                    "date": date(2024, 1, 1),
                    "symbol": "AAPL",
                    "open": 100.0,
                    "high": 100.0,
                    "low": 100.0,
                    "close": 100.0,
                    "adj_close": None,
                    "ret": None,
                    "volume": 1000000,
                },
                {
                    "date": date(2024, 1, 2),
                    "symbol": "AAPL",
                    "open": 25.0,
                    "high": 25.0,
                    "low": 25.0,
                    "close": 25.0,
                    "adj_close": None,
                    "ret": 0.01,
                    "volume": 4000000,
                },
            ]
        )
        backtester = SimpleBacktester(mock_fetcher)

        result = backtester._prepare_data(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 2),
            symbols=["AAPL"],
        )

        assert result["ret"].to_list() == [None, 0.01]

    def test_prepare_data_hybrid_falls_back_to_close_when_returns_missing(
        self,
        mock_fetcher,
    ):
        """Hybrid CRSP/SIP uses close returns when current SIP snapshots are raw."""
        mock_fetcher.get_active_provider.return_value = "hybrid_crsp_universe_sip_prices"
        mock_fetcher.get_daily_prices.return_value = pl.DataFrame(
            [
                {
                    "date": date(2024, 1, 1),
                    "symbol": "AAPL",
                    "open": 100.0,
                    "high": 100.0,
                    "low": 100.0,
                    "close": 100.0,
                    "adj_close": None,
                    "ret": None,
                    "volume": 1000000,
                },
                {
                    "date": date(2024, 1, 2),
                    "symbol": "AAPL",
                    "open": 110.0,
                    "high": 110.0,
                    "low": 110.0,
                    "close": 110.0,
                    "adj_close": None,
                    "ret": None,
                    "volume": 1000000,
                },
                {
                    "date": date(2024, 1, 3),
                    "symbol": "AAPL",
                    "open": 121.0,
                    "high": 121.0,
                    "low": 121.0,
                    "close": 121.0,
                    "adj_close": None,
                    "ret": None,
                    "volume": 1000000,
                },
            ]
        )
        backtester = SimpleBacktester(mock_fetcher)

        result = backtester._prepare_data(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 3),
            symbols=["AAPL"],
        ).sort("date")

        returns = result["ret"].to_list()
        assert returns[0] is None
        assert returns[1] == pytest.approx(0.1)
        assert returns[2] == pytest.approx(0.1)

    def test_prepare_data_rejects_sip_partially_adjusted_close(self, mock_fetcher):
        """Test SIP cannot fall back to raw close for partially adjusted rows."""
        mock_fetcher.get_active_provider.return_value = "alpaca_sip"
        mock_fetcher.get_daily_prices.return_value = pl.DataFrame(
            [
                {
                    "date": date(2024, 1, 1),
                    "symbol": "AAPL",
                    "open": 100.0,
                    "high": 100.0,
                    "low": 100.0,
                    "close": 100.0,
                    "adj_close": 100.0,
                    "volume": 1000000,
                },
                {
                    "date": date(2024, 1, 2),
                    "symbol": "AAPL",
                    "open": 25.0,
                    "high": 25.0,
                    "low": 25.0,
                    "close": 25.0,
                    "adj_close": None,
                    "volume": 4000000,
                },
            ]
        )
        backtester = SimpleBacktester(mock_fetcher)

        with pytest.raises(ValueError, match="Every returned row must have adj_close"):
            backtester._prepare_data(
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 2),
                symbols=["AAPL"],
            )

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
            rows.append(
                {
                    "date": d,
                    "symbol": "AAPL",
                    "permno": 1,
                    "ret": 0.01,  # 1% daily return
                }
            )

        prices = pl.DataFrame(rows)
        backtester = SimpleBacktester(mock_fetcher)

        result = backtester._compute_forward_returns(prices, as_of_date=date(2024, 1, 1), horizon=1)

        assert "return" in result.columns
        assert "permno" in result.columns
        assert result.height == 1

    def test_compute_forward_returns_insufficient_data(self, mock_fetcher):
        """Test MissingForwardReturnError when insufficient data."""
        # Only 2 days of data
        prices = pl.DataFrame(
            {
                "date": [date(2024, 1, 1), date(2024, 1, 2)],
                "symbol": ["AAPL", "AAPL"],
                "permno": [1, 1],
                "ret": [0.01, 0.01],
            }
        )
        backtester = SimpleBacktester(mock_fetcher)

        with pytest.raises(MissingForwardReturnError):
            backtester._compute_forward_returns(prices, as_of_date=date(2024, 1, 2), horizon=5)


class TestDataLeakagePrevention:
    """Tests for data leakage prevention."""

    def test_date_index_building(self, mock_fetcher):
        """Test that _build_date_index creates correct mapping."""
        prices = pl.DataFrame(
            {
                "date": [
                    date(2024, 1, 1),
                    date(2024, 1, 1),  # 2 rows for day 1
                    date(2024, 1, 2),
                    date(2024, 1, 2),  # 2 rows for day 2
                    date(2024, 1, 3),  # 1 row for day 3
                ],
                "symbol": ["AAPL", "MSFT", "AAPL", "MSFT", "AAPL"],
            }
        )
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
        mock_fetcher.get_daily_prices.return_value = pl.DataFrame(
            {
                "date": [date(2023, 1, 1)],  # Wrong year
                "symbol": ["AAPL"],
                "open": [100.0],
                "high": [105.0],
                "low": [95.0],
                "close": [102.0],
                "adj_close": [102.0],
                "volume": [1000000],
            }
        )
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
            rows.append(
                {
                    "date": d,
                    "symbol": "AAPL",
                    "open": 100.0,
                    "high": 105.0,
                    "low": 95.0,
                    "close": 102.0,
                    "adj_close": 102.0,
                    "volume": 1000000,
                }
            )

        mock_fetcher.get_daily_prices.return_value = pl.DataFrame(rows)
        backtester = SimpleBacktester(mock_fetcher, mock_metrics)

        mock_alpha = MagicMock()
        mock_alpha.name = "test_alpha"
        mock_alpha.compute.return_value = pl.DataFrame(
            {
                "date": [date(2024, 1, 1)],
                "permno": [1],
                "signal": [0.5],
            }
        )

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

    def test_daily_prices_fall_back_to_close_when_adj_close_is_null(
        self, mock_fetcher, mock_metrics
    ):
        """Test non-SIP data still produces usable price artifacts."""
        dates = [date(2024, 1, i) for i in range(1, 4)]
        rows = []
        for offset, d in enumerate(dates):
            rows.extend(
                [
                    {
                        "date": d,
                        "symbol": "AAPL",
                        "open": 100.0 + offset,
                        "high": 101.0 + offset,
                        "low": 99.0 + offset,
                        "close": 100.0 + offset,
                        "adj_close": None,
                        "volume": 1000000,
                    },
                    {
                        "date": d,
                        "symbol": "MSFT",
                        "open": 200.0 + offset,
                        "high": 201.0 + offset,
                        "low": 199.0 + offset,
                        "close": 200.0 + offset,
                        "adj_close": None,
                        "volume": 1000000,
                    },
                ]
            )
        mock_fetcher.get_daily_prices.return_value = pl.DataFrame(rows)
        backtester = SimpleBacktester(mock_fetcher, mock_metrics)

        mock_alpha = MagicMock()
        mock_alpha.name = "test_alpha"

        def compute_signal(prices, fundamentals, as_of_date):
            return pl.DataFrame(
                {
                    "date": [as_of_date, as_of_date],
                    "permno": [1, 2],
                    "signal": [0.5, -0.5],
                }
            )

        mock_alpha.compute.side_effect = compute_signal

        with patch.object(
            backtester,
            "_compute_forward_returns",
            side_effect=lambda prices, as_of_date, horizon: pl.DataFrame(
                {
                    "date": [as_of_date, as_of_date],
                    "permno": [1, 2],
                    "return": [0.01, -0.01],
                }
            ),
        ):
            result = backtester.run_backtest(
                alpha=mock_alpha,
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 3),
                universe=["AAPL", "MSFT"],
            )

        prices = result.daily_prices.sort(["date", "symbol"])
        assert prices["price"].null_count() == 0
        assert prices.filter(pl.col("symbol") == "AAPL")["price"].to_list() == [
            100.0,
            101.0,
            102.0,
        ]
        assert prices.filter(pl.col("symbol") == "MSFT")["price"].to_list() == [
            200.0,
            201.0,
            202.0,
        ]

    def test_daily_prices_do_not_mix_partial_adjusted_close_with_raw_close(
        self, mock_fetcher, mock_metrics
    ):
        """Test price artifacts do not bridge adjusted-close gaps with raw close."""
        dates = [date(2024, 1, i) for i in range(1, 4)]
        rows = []
        aapl_adjusted = [100.0, None, 121.0]
        for offset, d in enumerate(dates):
            rows.extend(
                [
                    {
                        "date": d,
                        "symbol": "AAPL",
                        "open": 100.0 + offset * 10.0,
                        "high": 101.0 + offset * 10.0,
                        "low": 99.0 + offset * 10.0,
                        "close": 100.0 + offset * 10.0,
                        "adj_close": aapl_adjusted[offset],
                        "volume": 1000000,
                    },
                    {
                        "date": d,
                        "symbol": "MSFT",
                        "open": 200.0 + offset,
                        "high": 201.0 + offset,
                        "low": 199.0 + offset,
                        "close": 200.0 + offset,
                        "adj_close": None,
                        "volume": 1000000,
                    },
                ]
            )
        mock_fetcher.get_daily_prices.return_value = pl.DataFrame(rows)
        backtester = SimpleBacktester(mock_fetcher, mock_metrics)

        mock_alpha = MagicMock()
        mock_alpha.name = "test_alpha"

        def compute_signal(prices, fundamentals, as_of_date):
            return pl.DataFrame(
                {
                    "date": [as_of_date, as_of_date],
                    "permno": [1, 2],
                    "signal": [0.5, -0.5],
                }
            )

        mock_alpha.compute.side_effect = compute_signal

        with patch.object(
            backtester,
            "_compute_forward_returns",
            side_effect=lambda prices, as_of_date, horizon: pl.DataFrame(
                {
                    "date": [as_of_date, as_of_date],
                    "permno": [1, 2],
                    "return": [0.01, -0.01],
                }
            ),
        ):
            result = backtester.run_backtest(
                alpha=mock_alpha,
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 3),
                universe=["AAPL", "MSFT"],
            )

        prices = result.daily_prices.sort(["date", "symbol"])
        assert prices.filter(pl.col("symbol") == "AAPL")["price"].to_list() == [
            100.0,
            None,
            121.0,
        ]
        assert prices.filter(pl.col("symbol") == "MSFT")["price"].to_list() == [
            200.0,
            201.0,
            202.0,
        ]


class TestResultMetadata:
    """Tests for BacktestResult metadata fields."""

    def test_dataset_version_ids_structure(self, mock_fetcher, mock_metrics):
        """Test that dataset_version_ids has correct structure."""
        dates = [date(2024, 1, i) for i in range(1, 15)]
        rows = []
        for d in dates:
            rows.append(
                {
                    "date": d,
                    "symbol": "AAPL",
                    "open": 100.0,
                    "high": 105.0,
                    "low": 95.0,
                    "close": 102.0,
                    "adj_close": 102.0,
                    "volume": 1000000,
                }
            )

        mock_fetcher.get_daily_prices.return_value = pl.DataFrame(rows)
        backtester = SimpleBacktester(mock_fetcher, mock_metrics)

        mock_alpha = MagicMock()
        mock_alpha.name = "test_alpha"
        mock_alpha.compute.return_value = pl.DataFrame(
            {
                "date": [date(2024, 1, 1)],
                "permno": [1],
                "signal": [0.5],
            }
        )

        with patch.object(
            backtester,
            "_compute_forward_returns",
            return_value=pl.DataFrame(
                {
                    "permno": [1],
                    "return": [0.01],
                    "date": [date(2024, 1, 1)],
                }
            ),
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

    def test_alpaca_sip_manifest_metadata_changes_snapshot_id(self, mock_fetcher, mock_metrics):
        """Test that Alpaca SIP manifest metadata is stamped into reproducibility data."""
        mock_fetcher.get_active_provider.return_value = "alpaca_sip"
        dates = [date(2024, 1, i) for i in range(1, 15)]
        mock_fetcher.get_daily_prices.return_value = pl.DataFrame(
            [
                {
                    "date": d,
                    "symbol": "AAPL",
                    "open": 100.0,
                    "high": 105.0,
                    "low": 95.0,
                    "close": 102.0,
                    "adj_close": 102.0,
                    "volume": 1000000,
                }
                for d in dates
            ]
        )
        mock_alpha = MagicMock()
        mock_alpha.name = "test_alpha"
        mock_alpha.compute.return_value = pl.DataFrame(
            {
                "date": [date(2024, 1, 1)],
                "permno": [1],
                "signal": [0.5],
            }
        )

        def run_with_checksum(checksum: str):
            backtester = SimpleBacktester(
                mock_fetcher,
                mock_metrics,
                dataset_version_ids={
                    "version": "manifest-v7",
                    "alpaca_sip_daily_manifest_version": "7",
                    "alpaca_sip_daily_checksum": checksum,
                    "alpaca_sip_daily_schema_version": "v1.0.0",
                },
            )
            with patch.object(
                backtester,
                "_compute_forward_returns",
                return_value=pl.DataFrame(
                    {
                        "permno": [1],
                        "return": [0.01],
                        "date": [date(2024, 1, 1)],
                    }
                ),
            ):
                return backtester.run_backtest(
                    alpha=mock_alpha,
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 1, 5),
                    universe=["AAPL"],
                )

        result = run_with_checksum("checksum-a")
        changed = run_with_checksum("checksum-b")

        assert result.dataset_version_ids["provider_type"] == "alpaca_sip"
        assert result.dataset_version_ids["version"] == "manifest-v7"
        assert result.dataset_version_ids["alpaca_sip_daily_manifest_version"] == "7"
        assert result.dataset_version_ids["alpaca_sip_daily_checksum"] == "checksum-a"
        assert result.snapshot_id.startswith("alpaca_sip-simple-")
        assert changed.snapshot_id != result.snapshot_id


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

        t1 = backtester._invoke_callbacks(progress_callback, None, 0.0, 50, date(2024, 1, 1))
        # Force should invoke even though within rate limit
        backtester._invoke_callbacks(progress_callback, None, t1, 60, date(2024, 1, 2), force=True)

        assert progress_callback.call_count == 2
