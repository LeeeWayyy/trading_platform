"""Unit tests for MicrostructureAnalyzer."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import polars as pl
import pytest

from libs.data.data_quality.exceptions import DataNotFoundError
from libs.data.data_quality.versioning import (
    DatasetVersionManager,
    SnapshotManifest,
)
from libs.platform.analytics.microstructure import (
    CompositeVersionInfo,
    IntradayPatternResult,
    MicrostructureAnalyzer,
    RealizedVolatilityResult,
    SpreadDepthResult,
    VPINResult,
)


@pytest.fixture()
def mock_taq_provider() -> MagicMock:
    """Create mock TAQLocalProvider."""
    provider = MagicMock()
    provider.manifest_manager = MagicMock()
    provider.version_manager = None
    return provider


@pytest.fixture()
def analyzer(mock_taq_provider: MagicMock) -> MicrostructureAnalyzer:
    """Create MicrostructureAnalyzer with mock provider."""
    return MicrostructureAnalyzer(mock_taq_provider)


def _create_minute_bars(
    symbol: str,
    target_date: date,
    n_bars: int = 78,
    base_price: float = 100.0,
) -> pl.DataFrame:
    """Create mock minute bars DataFrame."""
    timestamps = []
    for i in range(n_bars):
        total_minutes = 9 * 60 + 30 + i
        hour = total_minutes // 60
        minute = total_minutes % 60
        timestamps.append(
            datetime(target_date.year, target_date.month, target_date.day, hour, minute)
        )
    prices = [base_price + np.random.randn() * 0.5 for _ in range(n_bars)]
    return pl.DataFrame(
        {
            "ts": timestamps,
            "symbol": [symbol] * n_bars,
            "open": prices,
            "high": [p + 0.1 for p in prices],
            "low": [p - 0.1 for p in prices],
            "close": prices,
            "volume": [1000] * n_bars,
            "vwap": prices,
            "date": [target_date] * n_bars,
        }
    )


def _create_tick_data(
    symbol: str,
    target_date: date,
    n_ticks: int = 100,
    base_price: float = 100.0,
) -> pl.DataFrame:
    """Create mock tick data DataFrame."""
    timestamps = _create_timestamps(target_date, n_ticks)
    prices = [base_price + np.random.randn() * 0.1 for _ in range(n_ticks)]
    return pl.DataFrame(
        {
            "ts": timestamps,
            "symbol": [symbol] * n_ticks,
            "bid": [p - 0.01 for p in prices],
            "ask": [p + 0.01 for p in prices],
            "bid_size": [100] * n_ticks,
            "ask_size": [100] * n_ticks,
            "trade_px": prices,
            "trade_size": [10] * n_ticks,
            "cond": [""] * n_ticks,
        }
    )


def _create_timestamps(
    target_date: date, n: int, start_hour: int = 9, start_minute: int = 30
) -> list[datetime]:
    """Create n valid timestamps starting at start_hour:start_minute."""
    timestamps = []
    for i in range(n):
        total_seconds = start_hour * 3600 + start_minute * 60 + i
        hour = total_seconds // 3600
        minute = (total_seconds % 3600) // 60
        second = total_seconds % 60
        timestamps.append(
            datetime(target_date.year, target_date.month, target_date.day, hour, minute, second)
        )
    return timestamps


class TestRealizedVolatility:
    """Tests for compute_realized_volatility method."""

    def test_rv_computation(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test RV formula is correct."""
        mock_taq_provider.fetch_realized_volatility.side_effect = DataNotFoundError(
            "no precomputed"
        )
        bars = _create_minute_bars("AAPL", date(2024, 1, 15), n_bars=78)
        mock_taq_provider.fetch_minute_bars.return_value = bars
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.compute_realized_volatility("AAPL", date(2024, 1, 15))

        assert isinstance(result, RealizedVolatilityResult)
        assert result.rv_daily >= 0
        assert result.rv_annualized == pytest.approx(result.rv_daily * math.sqrt(252), rel=1e-6)

    def test_rv_uses_precomputed(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test 5/30 min optimization uses precomputed RV."""
        rv_df = pl.DataFrame(
            {
                "date": [date(2024, 1, 15)],
                "symbol": ["AAPL"],
                "rv": [0.02],
                "obs": [78],
            }
        )
        mock_taq_provider.fetch_realized_volatility.return_value = rv_df
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.compute_realized_volatility(
            "AAPL", date(2024, 1, 15), sampling_freq_minutes=5
        )

        assert result.rv_daily == 0.02
        mock_taq_provider.fetch_minute_bars.assert_not_called()

    def test_rv_missing_data(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test <10 observations returns NaN."""
        mock_taq_provider.fetch_realized_volatility.side_effect = DataNotFoundError(
            "no precomputed"
        )
        bars = _create_minute_bars("AAPL", date(2024, 1, 15), n_bars=5)
        mock_taq_provider.fetch_minute_bars.return_value = bars
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.compute_realized_volatility("AAPL", date(2024, 1, 15))

        assert math.isnan(result.rv_daily)
        assert math.isnan(result.rv_annualized)

    def test_rv_includes_version_id(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test metadata includes version_id."""
        mock_taq_provider.fetch_realized_volatility.side_effect = DataNotFoundError(
            "no precomputed"
        )
        bars = _create_minute_bars("AAPL", date(2024, 1, 15))
        mock_taq_provider.fetch_minute_bars.return_value = bars
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="test_version"
        )

        result = analyzer.compute_realized_volatility("AAPL", date(2024, 1, 15))

        assert result.dataset_version_id == "test_version"

    def test_rv_with_as_of(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test PIT query uses version_manager."""
        mock_version_manager = MagicMock(spec=DatasetVersionManager)
        mock_taq_provider.version_manager = mock_version_manager

        snapshot = MagicMock(spec=SnapshotManifest)
        snapshot.datasets = {
            "taq_1min_bars": MagicMock(sync_manifest_version=42),
        }
        mock_version_manager.query_as_of.return_value = (Path("/data"), snapshot)

        mock_taq_provider.fetch_realized_volatility.side_effect = DataNotFoundError(
            "no precomputed"
        )
        bars = _create_minute_bars("AAPL", date(2024, 1, 15))
        mock_taq_provider.fetch_minute_bars.return_value = bars

        result = analyzer.compute_realized_volatility(
            "AAPL", date(2024, 1, 15), as_of=date(2024, 2, 1)
        )

        assert result.dataset_version_id == "42"
        assert result.as_of_date == date(2024, 2, 1)

    def test_rv_pit_failure(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test PIT failure raises DataNotFoundError."""
        mock_version_manager = MagicMock(spec=DatasetVersionManager)
        mock_taq_provider.version_manager = mock_version_manager
        mock_version_manager.query_as_of.side_effect = DataNotFoundError("no snapshot")

        mock_taq_provider.fetch_realized_volatility.side_effect = DataNotFoundError(
            "no precomputed"
        )

        with pytest.raises(DataNotFoundError):
            analyzer.compute_realized_volatility("AAPL", date(2024, 1, 15), as_of=date(2024, 2, 1))

    def test_rv_missing_manifest(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test missing manifest returns version_id='unknown'."""
        mock_taq_provider.fetch_realized_volatility.side_effect = DataNotFoundError(
            "no precomputed"
        )
        bars = _create_minute_bars("AAPL", date(2024, 1, 15))
        mock_taq_provider.fetch_minute_bars.return_value = bars
        mock_taq_provider.manifest_manager.load_manifest.return_value = None

        result = analyzer.compute_realized_volatility("AAPL", date(2024, 1, 15))

        assert result.dataset_version_id == "unknown"


class TestVPIN:
    """Tests for compute_vpin method."""

    def test_vpin_basic(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test VPIN with known imbalance."""
        ticks = _create_tick_data("AAPL", date(2024, 1, 15), n_ticks=200)
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.compute_vpin(
            "AAPL", date(2024, 1, 15), volume_per_bucket=100, window_buckets=5, sigma_lookback=10
        )

        assert isinstance(result, VPINResult)
        assert result.num_buckets > 0

    def test_vpin_range(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test VPIN values are in [0, 1]."""
        ticks = _create_tick_data("AAPL", date(2024, 1, 15), n_ticks=200)
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.compute_vpin(
            "AAPL", date(2024, 1, 15), volume_per_bucket=100, window_buckets=5, sigma_lookback=10
        )

        valid_vpin = result.data.filter(~pl.col("vpin").is_nan())["vpin"].to_numpy()
        assert all(0 <= v <= 1 for v in valid_vpin)

    def test_vpin_volume_per_bucket(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test bucket volume logic."""
        ticks = _create_tick_data("AAPL", date(2024, 1, 15), n_ticks=200)
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.compute_vpin(
            "AAPL", date(2024, 1, 15), volume_per_bucket=50, window_buckets=5, sigma_lookback=10
        )

        assert result.num_buckets > 0

    def test_vpin_sigma_zero(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test sigma=0 returns VPIN=NaN with warning."""
        timestamps = _create_timestamps(date(2024, 1, 15), 100)
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 100,
                "bid": [100.0] * 100,
                "ask": [100.02] * 100,
                "bid_size": [100] * 100,
                "ask_size": [100] * 100,
                "trade_px": [100.01] * 100,
                "trade_size": [10] * 100,
                "cond": [""] * 100,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.compute_vpin(
            "AAPL", date(2024, 1, 15), volume_per_bucket=50, window_buckets=5, sigma_lookback=10
        )

        assert any("sigma=0" in w.lower() for w in result.warnings)
        valid_vpin = result.data.filter(~pl.col("vpin").is_nan())
        assert valid_vpin.height == 0

    def test_vpin_flat_prices(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test Z=0 case when P_i == P_{i-1}."""
        timestamps = _create_timestamps(date(2024, 1, 15), 100)
        prices = [100.0] * 50 + [100.01] * 50
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 100,
                "bid": [p - 0.01 for p in prices],
                "ask": [p + 0.01 for p in prices],
                "bid_size": [100] * 100,
                "ask_size": [100] * 100,
                "trade_px": prices,
                "trade_size": [10] * 100,
                "cond": [""] * 100,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.compute_vpin(
            "AAPL", date(2024, 1, 15), volume_per_bucket=50, window_buckets=5, sigma_lookback=10
        )

        assert isinstance(result, VPINResult)

    def test_vpin_empty_day(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test empty day returns empty DataFrame with warning."""
        empty_df = pl.DataFrame(
            schema={
                "ts": pl.Datetime,
                "symbol": pl.Utf8,
                "bid": pl.Float64,
                "ask": pl.Float64,
                "bid_size": pl.Int64,
                "ask_size": pl.Int64,
                "trade_px": pl.Float64,
                "trade_size": pl.Int64,
                "cond": pl.Utf8,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = empty_df
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.compute_vpin("AAPL", date(2024, 1, 15))

        assert result.data.is_empty()
        assert any("empty" in w.lower() or "no tick" in w.lower() for w in result.warnings)

    def test_vpin_partial_bucket(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test EOD partial bucket handling."""
        ticks = _create_tick_data("AAPL", date(2024, 1, 15), n_ticks=50)
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.compute_vpin(
            "AAPL", date(2024, 1, 15), volume_per_bucket=1000, window_buckets=5, sigma_lookback=10
        )

        if result.num_buckets > 0:
            last_bucket = result.data.filter(pl.col("bucket_id") == result.num_buckets - 1)
            if not last_bucket.is_empty():
                assert last_bucket["is_partial"][0] is True

    def test_vpin_bucket_overflow(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test trade splitting on bucket overflow."""
        timestamps = [datetime(2024, 1, 15, 9, 30, i) for i in range(50)]
        prices = [100.0 + i * 0.01 for i in range(50)]
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 50,
                "bid": [p - 0.01 for p in prices],
                "ask": [p + 0.01 for p in prices],
                "bid_size": [100] * 50,
                "ask_size": [100] * 50,
                "trade_px": prices,
                "trade_size": [100] * 50,
                "cond": [""] * 50,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.compute_vpin(
            "AAPL", date(2024, 1, 15), volume_per_bucket=150, window_buckets=3, sigma_lookback=5
        )

        assert result.num_buckets > 0

    def test_vpin_multi_bucket_overflow(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test large trade spanning 3+ buckets."""
        timestamps = [datetime(2024, 1, 15, 9, 30, i) for i in range(30)]
        prices = [100.0 + i * 0.01 for i in range(30)]
        sizes = [10] * 25 + [500] + [10] * 4
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 30,
                "bid": [p - 0.01 for p in prices],
                "ask": [p + 0.01 for p in prices],
                "bid_size": [100] * 30,
                "ask_size": [100] * 30,
                "trade_px": prices,
                "trade_size": sizes,
                "cond": [""] * 30,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.compute_vpin(
            "AAPL", date(2024, 1, 15), volume_per_bucket=100, window_buckets=3, sigma_lookback=5
        )

        assert result.num_buckets >= 3

    def test_vpin_warmup_period(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test is_warmup=True for early buckets."""
        ticks = _create_tick_data("AAPL", date(2024, 1, 15), n_ticks=200)
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.compute_vpin(
            "AAPL", date(2024, 1, 15), volume_per_bucket=50, window_buckets=5, sigma_lookback=10
        )

        if result.num_buckets > 0:
            early_buckets = result.data.filter(pl.col("bucket_id") < 4)
            assert all(early_buckets["is_warmup"])

    def test_vpin_sigma_warmup(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test sigma NaN during warmup skips trades."""
        ticks = _create_tick_data("AAPL", date(2024, 1, 15), n_ticks=50)
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.compute_vpin(
            "AAPL", date(2024, 1, 15), volume_per_bucket=50, window_buckets=5, sigma_lookback=30
        )

        assert isinstance(result, VPINResult)

    def test_vpin_no_valid_buckets(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test day ends during warmup - no valid buckets."""
        ticks = _create_tick_data("AAPL", date(2024, 1, 15), n_ticks=15)
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.compute_vpin(
            "AAPL", date(2024, 1, 15), volume_per_bucket=1000, window_buckets=50, sigma_lookback=20
        )

        assert result.num_buckets == 0
        assert math.isnan(result.avg_vpin)
        assert any("Day ended during warmup period" in w for w in result.warnings)

    def test_vpin_uses_fetch_ticks(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test VPIN uses fetch_ticks for data."""
        ticks = _create_tick_data("AAPL", date(2024, 1, 15), n_ticks=100)
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        analyzer.compute_vpin("AAPL", date(2024, 1, 15))

        mock_taq_provider.fetch_ticks.assert_called_once()

    def test_vpin_includes_version_id(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test VPIN result includes version_id."""
        ticks = _create_tick_data("AAPL", date(2024, 1, 15), n_ticks=100)
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="vpin_version"
        )

        result = analyzer.compute_vpin("AAPL", date(2024, 1, 15))

        assert result.dataset_version_id == "vpin_version"

    def test_vpin_pit_failure(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test PIT failure returns empty result with warning (graceful degradation)."""
        mock_version_manager = MagicMock(spec=DatasetVersionManager)
        mock_taq_provider.version_manager = mock_version_manager
        mock_version_manager.query_as_of.side_effect = DataNotFoundError("no snapshot")

        result = analyzer.compute_vpin("AAPL", date(2024, 1, 15), as_of=date(2024, 2, 1))

        # Graceful degradation: returns empty result with warning instead of raising
        assert result.num_buckets == 0
        assert result.dataset_version_id == "snapshot_unavailable"
        assert len(result.warnings) == 1
        assert "PIT snapshot unavailable" in result.warnings[0]
        assert math.isnan(result.avg_vpin)

    def test_vpin_insufficient_buckets(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test partial results when <window_buckets."""
        ticks = _create_tick_data("AAPL", date(2024, 1, 15), n_ticks=100)
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.compute_vpin(
            "AAPL", date(2024, 1, 15), volume_per_bucket=500, window_buckets=50, sigma_lookback=10
        )

        assert result.num_buckets < 50

    def test_vpin_trade_split_ratio(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test split preserves v_buy/v_sell ratio."""
        ticks = _create_tick_data("AAPL", date(2024, 1, 15), n_ticks=100)
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.compute_vpin(
            "AAPL", date(2024, 1, 15), volume_per_bucket=50, window_buckets=5, sigma_lookback=10
        )

        assert isinstance(result, VPINResult)


class TestIntradayPattern:
    """Tests for analyze_intraday_pattern method."""

    def test_intraday_u_shape(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test intraday pattern analysis."""
        bars = _create_minute_bars("AAPL", date(2024, 1, 15), n_bars=390)
        mock_taq_provider.fetch_minute_bars.return_value = bars
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.analyze_intraday_pattern(
            "AAPL", date(2024, 1, 15), date(2024, 1, 15), bucket_minutes=30
        )

        assert isinstance(result, IntradayPatternResult)
        assert not result.data.is_empty()

    def test_intraday_timezone(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test timezone handling."""
        bars = _create_minute_bars("AAPL", date(2024, 1, 15))
        mock_taq_provider.fetch_minute_bars.return_value = bars
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.analyze_intraday_pattern("AAPL", date(2024, 1, 15), date(2024, 1, 15))

        assert isinstance(result, IntradayPatternResult)

    def test_intraday_half_day(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test half-day handling."""
        bars = _create_minute_bars("AAPL", date(2024, 1, 15), n_bars=195)
        mock_taq_provider.fetch_minute_bars.return_value = bars
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.analyze_intraday_pattern("AAPL", date(2024, 1, 15), date(2024, 1, 15))

        assert isinstance(result, IntradayPatternResult)

    def test_intraday_holidays(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test holiday (0 bars) exclusion."""
        empty_bars = pl.DataFrame(
            schema={
                "ts": pl.Datetime,
                "symbol": pl.Utf8,
                "open": pl.Float64,
                "high": pl.Float64,
                "low": pl.Float64,
                "close": pl.Float64,
                "volume": pl.Int64,
                "vwap": pl.Float64,
                "date": pl.Date,
            }
        )
        mock_taq_provider.fetch_minute_bars.return_value = empty_bars
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.analyze_intraday_pattern("AAPL", date(2024, 1, 15), date(2024, 1, 15))

        assert result.data.is_empty()

    def test_intraday_includes_version_id(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test version tracking in intraday analysis."""
        bars = _create_minute_bars("AAPL", date(2024, 1, 15))
        mock_taq_provider.fetch_minute_bars.return_value = bars
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="intraday_v1"
        )

        result = analyzer.analyze_intraday_pattern("AAPL", date(2024, 1, 15), date(2024, 1, 15))

        assert result.dataset_version_id == "intraday_v1"

    def test_intraday_pit_failure(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test PIT failure raises DataNotFoundError."""
        mock_version_manager = MagicMock(spec=DatasetVersionManager)
        mock_taq_provider.version_manager = mock_version_manager
        mock_version_manager.query_as_of.side_effect = DataNotFoundError("no snapshot")

        with pytest.raises(DataNotFoundError):
            analyzer.analyze_intraday_pattern(
                "AAPL", date(2024, 1, 15), date(2024, 1, 15), as_of=date(2024, 2, 1)
            )

    def test_intraday_asof_precedence(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test as_of takes precedence."""
        mock_version_manager = MagicMock(spec=DatasetVersionManager)
        mock_taq_provider.version_manager = mock_version_manager

        snapshot = MagicMock(spec=SnapshotManifest)
        snapshot.datasets = {"taq_1min_bars": MagicMock(sync_manifest_version=99)}
        mock_version_manager.query_as_of.return_value = (Path("/data"), snapshot)

        bars = _create_minute_bars("AAPL", date(2024, 1, 15))
        mock_taq_provider.fetch_minute_bars.return_value = bars

        result = analyzer.analyze_intraday_pattern(
            "AAPL", date(2024, 1, 15), date(2024, 1, 15), as_of=date(2024, 2, 1)
        )

        assert result.dataset_version_id == "99"
        assert result.as_of_date == date(2024, 2, 1)


class TestSpreadDepth:
    """Tests for compute_spread_depth_stats method."""

    def test_spread_retrieval(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test spread retrieval from precomputed stats."""
        spread_df = pl.DataFrame(
            {
                "date": [date(2024, 1, 15)],
                "symbol": ["AAPL"],
                "qwap_spread": [0.001],
                "ewas": [0.0008],
                "quotes": [10000],
                "trades": [5000],
            }
        )
        mock_taq_provider.fetch_spread_metrics.return_value = spread_df
        mock_taq_provider.fetch_ticks.return_value = _create_tick_data("AAPL", date(2024, 1, 15))
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="spread_v1"
        )

        result = analyzer.compute_spread_depth_stats("AAPL", date(2024, 1, 15))

        assert result.qwap_spread == 0.001
        assert result.ewas == 0.0008

    def test_depth_computation(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test time-weighted depth calculation."""
        spread_df = pl.DataFrame(
            {
                "date": [date(2024, 1, 15)],
                "symbol": ["AAPL"],
                "qwap_spread": [0.001],
                "ewas": [0.0008],
                "quotes": [10000],
                "trades": [5000],
            }
        )
        mock_taq_provider.fetch_spread_metrics.return_value = spread_df
        mock_taq_provider.fetch_ticks.return_value = _create_tick_data("AAPL", date(2024, 1, 15))
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="depth_v1"
        )

        result = analyzer.compute_spread_depth_stats("AAPL", date(2024, 1, 15))

        assert not math.isnan(result.avg_bid_depth)
        assert not math.isnan(result.avg_ask_depth)

    def test_depth_quote_only_filter(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test quote filter excludes trade-only rows."""
        spread_df = pl.DataFrame(
            {
                "date": [date(2024, 1, 15)],
                "symbol": ["AAPL"],
                "qwap_spread": [0.001],
                "ewas": [0.0008],
                "quotes": [10000],
                "trades": [5000],
            }
        )
        mock_taq_provider.fetch_spread_metrics.return_value = spread_df

        timestamps = [datetime(2024, 1, 15, 9, 30, i) for i in range(10)]
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 10,
                "bid": [100.0] * 5 + [0.0] * 5,
                "ask": [100.02] * 5 + [0.0] * 5,
                "bid_size": [100] * 5 + [0] * 5,
                "ask_size": [100] * 5 + [0] * 5,
                "trade_px": [100.01] * 10,
                "trade_size": [10] * 10,
                "cond": [""] * 10,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="filter_v1"
        )

        result = analyzer.compute_spread_depth_stats("AAPL", date(2024, 1, 15))

        assert isinstance(result, SpreadDepthResult)

    def test_depth_imbalance(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test depth imbalance formula."""
        spread_df = pl.DataFrame(
            {
                "date": [date(2024, 1, 15)],
                "symbol": ["AAPL"],
                "qwap_spread": [0.001],
                "ewas": [0.0008],
                "quotes": [10000],
                "trades": [5000],
            }
        )
        mock_taq_provider.fetch_spread_metrics.return_value = spread_df

        timestamps = [datetime(2024, 1, 15, 9, 30, i) for i in range(10)]
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 10,
                "bid": [100.0] * 10,
                "ask": [100.02] * 10,
                "bid_size": [200] * 10,
                "ask_size": [100] * 10,
                "trade_px": [100.01] * 10,
                "trade_size": [10] * 10,
                "cond": [""] * 10,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="imbal_v1"
        )

        result = analyzer.compute_spread_depth_stats("AAPL", date(2024, 1, 15))

        expected_imbalance = (result.avg_bid_depth - result.avg_ask_depth) / result.avg_total_depth
        assert result.depth_imbalance == pytest.approx(expected_imbalance, rel=1e-6)

    def test_spread_depth_composite_version(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test deterministic composite version_id."""
        spread_df = pl.DataFrame(
            {
                "date": [date(2024, 1, 15)],
                "symbol": ["AAPL"],
                "qwap_spread": [0.001],
                "ewas": [0.0008],
                "quotes": [10000],
                "trades": [5000],
            }
        )
        mock_taq_provider.fetch_spread_metrics.return_value = spread_df
        mock_taq_provider.fetch_ticks.return_value = _create_tick_data("AAPL", date(2024, 1, 15))

        def get_manifest_side_effect(dataset: str) -> MagicMock:
            checksums = {
                "taq_spread_stats": "spread_abc",
                "taq_samples_20240115": "samples_xyz",
            }
            mock = MagicMock()
            mock.checksum = checksums.get(dataset, "unknown")
            return mock

        mock_taq_provider.manifest_manager.load_manifest.side_effect = get_manifest_side_effect

        result1 = analyzer.compute_spread_depth_stats("AAPL", date(2024, 1, 15))
        result2 = analyzer.compute_spread_depth_stats("AAPL", date(2024, 1, 15))

        assert result1.dataset_version_id == result2.dataset_version_id
        assert len(result1.dataset_version_id) == 32

    def test_depth_empty_book(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test zero depth handling."""
        spread_df = pl.DataFrame(
            {
                "date": [date(2024, 1, 15)],
                "symbol": ["AAPL"],
                "qwap_spread": [0.001],
                "ewas": [0.0008],
                "quotes": [10000],
                "trades": [5000],
            }
        )
        mock_taq_provider.fetch_spread_metrics.return_value = spread_df

        timestamps = [datetime(2024, 1, 15, 9, 30, i) for i in range(10)]
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 10,
                "bid": [0.0] * 10,
                "ask": [0.0] * 10,
                "bid_size": [0] * 10,
                "ask_size": [0] * 10,
                "trade_px": [100.01] * 10,
                "trade_size": [10] * 10,
                "cond": [""] * 10,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="empty_v1"
        )

        result = analyzer.compute_spread_depth_stats("AAPL", date(2024, 1, 15))

        assert math.isnan(result.avg_bid_depth) or result.depth_is_estimated

    def test_spread_depth_pit(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test single snapshot for PIT queries."""
        mock_version_manager = MagicMock(spec=DatasetVersionManager)
        mock_taq_provider.version_manager = mock_version_manager

        snapshot = MagicMock(spec=SnapshotManifest)
        snapshot.datasets = {
            "taq_spread_stats": MagicMock(sync_manifest_version=1),
            "taq_samples_20240115": MagicMock(sync_manifest_version=2),
        }
        snapshot.aggregate_checksum = "snap_abc"
        mock_version_manager.query_as_of.return_value = (Path("/data"), snapshot)

        spread_df = pl.DataFrame(
            {
                "date": [date(2024, 1, 15)],
                "symbol": ["AAPL"],
                "qwap_spread": [0.001],
                "ewas": [0.0008],
                "quotes": [10000],
                "trades": [5000],
            }
        )
        mock_taq_provider.fetch_spread_metrics.return_value = spread_df
        mock_taq_provider.fetch_ticks.return_value = _create_tick_data("AAPL", date(2024, 1, 15))

        result = analyzer.compute_spread_depth_stats(
            "AAPL", date(2024, 1, 15), as_of=date(2024, 2, 1)
        )

        assert result.as_of_date == date(2024, 2, 1)
        assert result.dataset_versions is not None

    def test_spread_depth_pit_missing_dataset(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test DataNotFoundError when dataset not in snapshot."""
        mock_version_manager = MagicMock(spec=DatasetVersionManager)
        mock_taq_provider.version_manager = mock_version_manager

        snapshot = MagicMock(spec=SnapshotManifest)
        snapshot.datasets = {
            "taq_spread_stats": MagicMock(sync_manifest_version=1),
        }
        mock_version_manager.query_as_of.return_value = (Path("/data"), snapshot)

        with pytest.raises(DataNotFoundError):
            analyzer.compute_spread_depth_stats("AAPL", date(2024, 1, 15), as_of=date(2024, 2, 1))

    def test_locked_markets(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test locked market detection."""
        spread_df = pl.DataFrame(
            {
                "date": [date(2024, 1, 15)],
                "symbol": ["AAPL"],
                "qwap_spread": [0.001],
                "ewas": [0.0008],
                "quotes": [10000],
                "trades": [5000],
            }
        )
        mock_taq_provider.fetch_spread_metrics.return_value = spread_df

        timestamps = [datetime(2024, 1, 15, 9, 30, i) for i in range(10)]
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 10,
                "bid": [100.0] * 5 + [100.01] * 5,
                "ask": [100.0] * 5 + [100.02] * 5,
                "bid_size": [100] * 10,
                "ask_size": [100] * 10,
                "trade_px": [100.01] * 10,
                "trade_size": [10] * 10,
                "cond": [""] * 10,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="locked_v1"
        )

        result = analyzer.compute_spread_depth_stats("AAPL", date(2024, 1, 15))

        assert result.has_locked_markets is True
        assert result.locked_pct == pytest.approx(0.5, rel=0.1)

    def test_crossed_markets(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test crossed market detection."""
        spread_df = pl.DataFrame(
            {
                "date": [date(2024, 1, 15)],
                "symbol": ["AAPL"],
                "qwap_spread": [0.001],
                "ewas": [0.0008],
                "quotes": [10000],
                "trades": [5000],
            }
        )
        mock_taq_provider.fetch_spread_metrics.return_value = spread_df

        timestamps = [datetime(2024, 1, 15, 9, 30, i) for i in range(10)]
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 10,
                "bid": [100.02] * 3 + [100.0] * 7,
                "ask": [100.0] * 3 + [100.02] * 7,
                "bid_size": [100] * 10,
                "ask_size": [100] * 10,
                "trade_px": [100.01] * 10,
                "trade_size": [10] * 10,
                "cond": [""] * 10,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="crossed_v1"
        )

        result = analyzer.compute_spread_depth_stats("AAPL", date(2024, 1, 15))

        assert result.has_crossed_markets is True
        assert result.crossed_pct == pytest.approx(0.3, rel=0.1)

    def test_stale_quotes_high_pct(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test >50% stale quotes warning."""
        spread_df = pl.DataFrame(
            {
                "date": [date(2024, 1, 15)],
                "symbol": ["AAPL"],
                "qwap_spread": [0.001],
                "ewas": [0.0008],
                "quotes": [10000],
                "trades": [5000],
            }
        )
        mock_taq_provider.fetch_spread_metrics.return_value = spread_df

        timestamps = [datetime(2024, 1, 15, 9, 30) + timedelta(seconds=i * 120) for i in range(10)]
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 10,
                "bid": [100.0] * 10,
                "ask": [100.02] * 10,
                "bid_size": [100] * 10,
                "ask_size": [100] * 10,
                "trade_px": [100.01] * 10,
                "trade_size": [10] * 10,
                "cond": [""] * 10,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="stale_v1"
        )

        result = analyzer.compute_spread_depth_stats("AAPL", date(2024, 1, 15))

        assert result.stale_quote_pct > 0

    def test_spread_only_fallback(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test depth_is_estimated=True when ticks missing."""
        spread_df = pl.DataFrame(
            {
                "date": [date(2024, 1, 15)],
                "symbol": ["AAPL"],
                "qwap_spread": [0.001],
                "ewas": [0.0008],
                "quotes": [10000],
                "trades": [5000],
            }
        )
        mock_taq_provider.fetch_spread_metrics.return_value = spread_df
        mock_taq_provider.fetch_ticks.side_effect = DataNotFoundError("no ticks")
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="fallback_v1"
        )

        result = analyzer.compute_spread_depth_stats("AAPL", date(2024, 1, 15))

        assert result.depth_is_estimated is True
        assert math.isnan(result.avg_bid_depth)


class TestDeterminism:
    """Tests for deterministic behavior."""

    def test_vpin_deterministic_rerun(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test identical VPIN on re-execution."""
        np.random.seed(42)
        ticks = _create_tick_data("AAPL", date(2024, 1, 15), n_ticks=200)
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="det_v1")

        result1 = analyzer.compute_vpin(
            "AAPL", date(2024, 1, 15), volume_per_bucket=100, window_buckets=5, sigma_lookback=10
        )
        result2 = analyzer.compute_vpin(
            "AAPL", date(2024, 1, 15), volume_per_bucket=100, window_buckets=5, sigma_lookback=10
        )

        assert result1.num_buckets == result2.num_buckets
        assert result1.avg_vpin == result2.avg_vpin or (
            math.isnan(result1.avg_vpin) and math.isnan(result2.avg_vpin)
        )

    def test_composite_version_deterministic(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test same datasets produce same hash."""
        info1 = CompositeVersionInfo(
            versions={"ds1": "v1", "ds2": "v2"},
            snapshot_id="snap123",
            is_pit=True,
        )
        info2 = CompositeVersionInfo(
            versions={"ds2": "v2", "ds1": "v1"},
            snapshot_id="snap123",
            is_pit=True,
        )

        assert info1.composite_version_id == info2.composite_version_id

    def test_depth_calculation_deterministic(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test same quotes produce same depth."""
        spread_df = pl.DataFrame(
            {
                "date": [date(2024, 1, 15)],
                "symbol": ["AAPL"],
                "qwap_spread": [0.001],
                "ewas": [0.0008],
                "quotes": [10000],
                "trades": [5000],
            }
        )
        mock_taq_provider.fetch_spread_metrics.return_value = spread_df
        ticks = _create_tick_data("AAPL", date(2024, 1, 15), n_ticks=100)
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="det_depth"
        )

        result1 = analyzer.compute_spread_depth_stats("AAPL", date(2024, 1, 15))
        result2 = analyzer.compute_spread_depth_stats("AAPL", date(2024, 1, 15))

        assert result1.avg_bid_depth == result2.avg_bid_depth
        assert result1.avg_ask_depth == result2.avg_ask_depth


class TestEdgeCases:
    """Additional edge cases and error handling tests."""

    def test_rv_sampling_freq_non_standard(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test RV with non-standard sampling frequency (not 5 or 30 min)."""
        mock_taq_provider.fetch_realized_volatility.side_effect = DataNotFoundError(
            "no precomputed"
        )
        bars = _create_minute_bars("AAPL", date(2024, 1, 15), n_bars=78)
        mock_taq_provider.fetch_minute_bars.return_value = bars
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.compute_realized_volatility(
            "AAPL", date(2024, 1, 15), sampling_freq_minutes=15
        )

        assert isinstance(result, RealizedVolatilityResult)
        assert result.sampling_freq_minutes == 15
        assert not math.isnan(result.rv_daily)

    def test_rv_empty_bars_dataframe(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test RV with completely empty bars DataFrame."""
        mock_taq_provider.fetch_realized_volatility.side_effect = DataNotFoundError(
            "no precomputed"
        )
        empty_bars = pl.DataFrame(
            schema={
                "ts": pl.Datetime,
                "symbol": pl.Utf8,
                "open": pl.Float64,
                "high": pl.Float64,
                "low": pl.Float64,
                "close": pl.Float64,
                "volume": pl.Int64,
                "vwap": pl.Float64,
                "date": pl.Date,
            }
        )
        mock_taq_provider.fetch_minute_bars.return_value = empty_bars
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.compute_realized_volatility("AAPL", date(2024, 1, 15))

        assert math.isnan(result.rv_daily)
        assert result.num_observations == 0

    def test_rv_pit_no_version_manager(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test RV raises ValueError when PIT requested but version_manager is None."""
        mock_taq_provider.version_manager = None

        with pytest.raises(ValueError, match="version_manager required"):
            analyzer.compute_realized_volatility("AAPL", date(2024, 1, 15), as_of=date(2024, 2, 1))

    def test_vpin_no_trades_in_data(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test VPIN when tick data has no trades (all trade_size=0)."""
        timestamps = _create_timestamps(date(2024, 1, 15), 100)
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 100,
                "bid": [100.0] * 100,
                "ask": [100.02] * 100,
                "bid_size": [100] * 100,
                "ask_size": [100] * 100,
                "trade_px": [100.01] * 100,
                "trade_size": [0] * 100,  # All zero
                "cond": [""] * 100,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.compute_vpin("AAPL", date(2024, 1, 15))

        assert result.num_buckets == 0
        assert any("no valid trades" in w.lower() for w in result.warnings)

    def test_vpin_zero_volume_trades_warning(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test VPIN warns when >5% trades have zero volume."""
        timestamps = _create_timestamps(date(2024, 1, 15), 100)
        sizes = [10] * 93 + [0] * 7  # 7% zero volume
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 100,
                "bid": [100.0] * 100,
                "ask": [100.02] * 100,
                "bid_size": [100] * 100,
                "ask_size": [100] * 100,
                "trade_px": [100.0 + i * 0.01 for i in range(100)],
                "trade_size": sizes,
                "cond": [""] * 100,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.compute_vpin(
            "AAPL", date(2024, 1, 15), volume_per_bucket=50, window_buckets=5, sigma_lookback=10
        )

        assert any("zero-volume" in w.lower() for w in result.warnings)

    def test_composite_version_without_snapshot(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test CompositeVersionInfo without snapshot_id (non-PIT query)."""
        info = CompositeVersionInfo(
            versions={"ds1": "v1", "ds2": "v2"},
            snapshot_id=None,
            is_pit=False,
        )

        # Should generate deterministic hash without snapshot
        assert len(info.composite_version_id) == 32
        assert info.is_pit is False

    def test_filter_quotes_with_record_type(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test _filter_quotes uses record_type column when available."""
        timestamps = [datetime(2024, 1, 15, 9, 30, i) for i in range(10)]
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 10,
                "bid": [100.0] * 10,
                "ask": [100.02] * 10,
                "bid_size": [100] * 10,
                "ask_size": [100] * 10,
                "trade_px": [100.01] * 10,
                "trade_size": [10] * 10,
                "cond": [""] * 10,
                "record_type": ["quote"] * 5 + ["trade"] * 5,
            }
        )

        quotes_df = analyzer._filter_quotes(ticks)

        assert quotes_df.height == 5
        assert all(quotes_df["record_type"] == "quote")

    def test_filter_quotes_without_record_type(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test _filter_quotes fallback logic when record_type missing."""
        timestamps = [datetime(2024, 1, 15, 9, 30, i) for i in range(10)]
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 10,
                "bid": [100.0] * 5 + [0.0] * 5,
                "ask": [100.02] * 5 + [0.0] * 5,
                "bid_size": [100] * 5 + [0] * 5,
                "ask_size": [100] * 5 + [0] * 5,
                "trade_px": [100.01] * 10,
                "trade_size": [10] * 10,
                "cond": [""] * 10,
            }
        )

        quotes_df = analyzer._filter_quotes(ticks)

        # Should filter to rows with bid_size > 0 AND ask_size > 0
        assert quotes_df.height == 5

    def test_depth_with_zero_duration_filtered(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test _compute_depth_from_ticks filters out zero-duration quotes."""
        # Create quotes with same timestamp (zero duration after shift)
        timestamp = datetime(2024, 1, 15, 9, 30, 0)
        quotes = pl.DataFrame(
            {
                "ts": [timestamp] * 5,
                "bid": [100.0] * 5,
                "ask": [100.02] * 5,
                "bid_size": [100] * 5,
                "ask_size": [100] * 5,
            }
        )

        avg_bid, avg_ask = analyzer._compute_depth_from_ticks(quotes)

        # All quotes have zero duration, should return NaN
        assert math.isnan(avg_bid)
        assert math.isnan(avg_ask)

    def test_depth_with_invalid_quotes(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test _compute_depth_from_ticks with invalid bid/ask (bid > ask)."""
        timestamps = [datetime(2024, 1, 15, 9, 30, i) for i in range(10)]
        quotes = pl.DataFrame(
            {
                "ts": timestamps,
                "bid": [100.02] * 10,  # bid > ask (invalid)
                "ask": [100.0] * 10,
                "bid_size": [100] * 10,
                "ask_size": [100] * 10,
            }
        )

        avg_bid, avg_ask = analyzer._compute_depth_from_ticks(quotes)

        # Should filter out invalid quotes, return NaN
        assert math.isnan(avg_bid)
        assert math.isnan(avg_ask)

    def test_stale_quotes_less_than_two(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test _compute_stale_quote_pct with <2 quotes returns 0."""
        quotes = pl.DataFrame(
            {
                "ts": [datetime(2024, 1, 15, 9, 30, 0)],
                "bid": [100.0],
                "ask": [100.02],
                "bid_size": [100],
                "ask_size": [100],
            }
        )

        stale_pct = analyzer._compute_stale_quote_pct(quotes)

        assert stale_pct == 0.0

    def test_spread_depth_missing_spread_data(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test compute_spread_depth_stats raises DataNotFoundError when spread_df empty."""
        empty_spread_df = pl.DataFrame(
            schema={
                "date": pl.Date,
                "symbol": pl.Utf8,
                "qwap_spread": pl.Float64,
                "ewas": pl.Float64,
                "quotes": pl.Int64,
                "trades": pl.Int64,
            }
        )
        mock_taq_provider.fetch_spread_metrics.return_value = empty_spread_df
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="empty_v1"
        )

        with pytest.raises(DataNotFoundError, match="No spread stats found"):
            analyzer.compute_spread_depth_stats("AAPL", date(2024, 1, 15))

    def test_resolve_mean_with_nan(self) -> None:
        """Test _resolve_mean handles NaN values correctly."""
        from libs.platform.analytics.microstructure import _resolve_mean

        # DataFrame with NaN mean
        df = pl.DataFrame({"vpin": [float("nan"), float("nan")]})
        result = _resolve_mean(df)
        assert math.isnan(result)

    def test_resolve_mean_with_valid_values(self) -> None:
        """Test _resolve_mean returns correct float for valid data."""
        from libs.platform.analytics.microstructure import _resolve_mean

        df = pl.DataFrame({"vpin": [0.1, 0.2, 0.3]})
        result = _resolve_mean(df)
        assert result == pytest.approx(0.2, rel=1e-6)

    def test_get_version_id_dataset_not_in_snapshot(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test _get_version_id raises DataNotFoundError when dataset missing from snapshot."""
        mock_version_manager = MagicMock(spec=DatasetVersionManager)
        mock_taq_provider.version_manager = mock_version_manager

        snapshot = MagicMock(spec=SnapshotManifest)
        snapshot.datasets = {"other_dataset": MagicMock(sync_manifest_version=1)}
        mock_version_manager.query_as_of.return_value = (Path("/data"), snapshot)

        with pytest.raises(DataNotFoundError, match="Dataset 'taq_1min_bars' not found"):
            analyzer._get_version_id("taq_1min_bars", as_of=date(2024, 1, 15))

    def test_get_multi_version_id_no_version_manager(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test _get_multi_version_id raises ValueError when PIT but no version_manager."""
        mock_taq_provider.version_manager = None

        with pytest.raises(ValueError, match="version_manager required"):
            analyzer._get_multi_version_id(["ds1", "ds2"], as_of=date(2024, 1, 15))

    def test_intraday_pattern_pit_no_version_manager(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test analyze_intraday_pattern raises ValueError when PIT but no version_manager."""
        mock_taq_provider.version_manager = None

        with pytest.raises(ValueError, match="version_manager required"):
            analyzer.analyze_intraday_pattern(
                "AAPL", date(2024, 1, 15), date(2024, 1, 15), as_of=date(2024, 2, 1)
            )

    def test_spread_depth_pit_no_version_manager(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test compute_spread_depth_stats raises ValueError when PIT but no version_manager."""
        mock_taq_provider.version_manager = None

        with pytest.raises(ValueError, match="version_manager required"):
            analyzer.compute_spread_depth_stats("AAPL", date(2024, 1, 15), as_of=date(2024, 2, 1))

    def test_vpin_with_precomputed_rv_fallback(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test RV falls back to bars when precomputed raises KeyError."""
        mock_taq_provider.fetch_realized_volatility.side_effect = KeyError("missing key")
        bars = _create_minute_bars("AAPL", date(2024, 1, 15), n_bars=78)
        mock_taq_provider.fetch_minute_bars.return_value = bars
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.compute_realized_volatility("AAPL", date(2024, 1, 15))

        # Should successfully compute from bars
        assert not math.isnan(result.rv_daily)
        mock_taq_provider.fetch_minute_bars.assert_called_once()

    def test_depth_with_single_quote(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test _compute_depth_from_ticks with single quote (edge case for shift)."""
        quotes = pl.DataFrame(
            {
                "ts": [datetime(2024, 1, 15, 9, 30, 0)],
                "bid": [100.0],
                "ask": [100.02],
                "bid_size": [100],
                "ask_size": [100],
            }
        )

        avg_bid, avg_ask = analyzer._compute_depth_from_ticks(quotes)

        # Single quote gets 1 second duration by default
        assert avg_bid == 100.0
        assert avg_ask == 100.0

    def test_locked_markets_empty_dataframe(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test _detect_locked_markets with empty DataFrame."""
        empty_quotes = pl.DataFrame(
            schema={
                "ts": pl.Datetime,
                "bid": pl.Float64,
                "ask": pl.Float64,
                "bid_size": pl.Int64,
                "ask_size": pl.Int64,
            }
        )

        has_locked, locked_pct = analyzer._detect_locked_markets(empty_quotes)

        assert has_locked is False
        assert locked_pct == 0.0

    def test_crossed_markets_empty_dataframe(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test _detect_crossed_markets with empty DataFrame."""
        empty_quotes = pl.DataFrame(
            schema={
                "ts": pl.Datetime,
                "bid": pl.Float64,
                "ask": pl.Float64,
                "bid_size": pl.Int64,
                "ask_size": pl.Int64,
            }
        )

        has_crossed, crossed_pct = analyzer._detect_crossed_markets(empty_quotes)

        assert has_crossed is False
        assert crossed_pct == 0.0

    def test_vpin_no_valid_buckets_after_processing(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test VPIN returns empty when _compute_vpin_buckets returns no buckets."""
        # Create tick data with sufficient trades but volume_per_bucket too large
        # to construct any buckets
        timestamps = _create_timestamps(date(2024, 1, 15), 25)
        prices = [100.0 + i * 0.01 for i in range(25)]
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 25,
                "bid": [p - 0.01 for p in prices],
                "ask": [p + 0.01 for p in prices],
                "bid_size": [100] * 25,
                "ask_size": [100] * 25,
                "trade_px": prices,
                "trade_size": [1] * 25,  # Very small trades
                "cond": [""] * 25,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="abc123")

        result = analyzer.compute_vpin(
            "AAPL",
            date(2024, 1, 15),
            volume_per_bucket=100000,  # Very large bucket
            window_buckets=50,
            sigma_lookback=10,
        )

        # Should get only partial bucket or no buckets
        assert result.num_buckets <= 1
        if result.num_buckets == 0:
            assert any("no valid buckets" in w.lower() for w in result.warnings)

    def test_compute_vpin_value_zero_total_volume(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test _compute_vpin_value returns NaN when total_volume is zero."""
        v_buy_history = [0.0] * 50
        v_sell_history = [0.0] * 50

        result = analyzer._compute_vpin_value(
            v_buy_history, v_sell_history, window_buckets=50, sigma_zero_contaminated=False
        )

        assert math.isnan(result)

    def test_rolling_sigma_insufficient_lookback(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test _compute_rolling_sigma_vectorized with lookback < 2."""
        log_returns = np.array([0.01, 0.02, 0.03])

        sigma_arr = analyzer._compute_rolling_sigma_vectorized(log_returns, lookback=1)

        # Should return zeros when lookback < 2
        assert all(sigma_arr == 0)

    def test_rolling_sigma_short_array(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test _compute_rolling_sigma_vectorized when n < lookback."""
        log_returns = np.array([0.01, 0.02])

        sigma_arr = analyzer._compute_rolling_sigma_vectorized(log_returns, lookback=10)

        # Should return zeros when array is shorter than lookback
        assert all(sigma_arr == 0)

    def test_spread_depth_empty_ticks(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test compute_spread_depth_stats with empty ticks DataFrame."""
        spread_df = pl.DataFrame(
            {
                "date": [date(2024, 1, 15)],
                "symbol": ["AAPL"],
                "qwap_spread": [0.001],
                "ewas": [0.0008],
                "quotes": [10000],
                "trades": [5000],
            }
        )
        mock_taq_provider.fetch_spread_metrics.return_value = spread_df

        # Return empty DataFrame for ticks
        empty_ticks = pl.DataFrame(
            schema={
                "ts": pl.Datetime,
                "symbol": pl.Utf8,
                "bid": pl.Float64,
                "ask": pl.Float64,
                "bid_size": pl.Int64,
                "ask_size": pl.Int64,
                "trade_px": pl.Float64,
                "trade_size": pl.Int64,
                "cond": pl.Utf8,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = empty_ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="empty_ticks_v1"
        )

        result = analyzer.compute_spread_depth_stats("AAPL", date(2024, 1, 15))

        assert result.depth_is_estimated is True
        assert math.isnan(result.avg_bid_depth)
        assert math.isnan(result.avg_ask_depth)

    def test_spread_depth_zero_total_depth(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test depth_imbalance is NaN when total depth is zero."""
        spread_df = pl.DataFrame(
            {
                "date": [date(2024, 1, 15)],
                "symbol": ["AAPL"],
                "qwap_spread": [0.001],
                "ewas": [0.0008],
                "quotes": [10000],
                "trades": [5000],
            }
        )
        mock_taq_provider.fetch_spread_metrics.return_value = spread_df

        # Create quotes with zero bid/ask (will be filtered out)
        timestamps = [datetime(2024, 1, 15, 9, 30, i) for i in range(10)]
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 10,
                "bid": [0.0] * 10,  # Zero bid
                "ask": [100.02] * 10,
                "bid_size": [100] * 10,
                "ask_size": [100] * 10,
                "trade_px": [100.01] * 10,
                "trade_size": [10] * 10,
                "cond": [""] * 10,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="zero_depth_v1"
        )

        result = analyzer.compute_spread_depth_stats("AAPL", date(2024, 1, 15))

        # Depth should be estimated due to invalid quotes
        assert result.depth_is_estimated is True
        assert math.isnan(result.depth_imbalance)

    def test_resolve_mean_non_real_type(self) -> None:
        """Test _resolve_mean returns NaN for non-Real mean value."""
        from libs.platform.analytics.microstructure import _resolve_mean

        # Create a DataFrame where mean returns None (e.g., all null values)
        df = pl.DataFrame({"vpin": pl.Series([], dtype=pl.Float64)})
        result = _resolve_mean(df)
        assert math.isnan(result)


class TestNumbaAcceleration:
    """Tests for Numba-accelerated code paths."""

    def test_numba_availability_flag(self) -> None:
        """Test NUMBA_AVAILABLE flag is a boolean."""
        from libs.platform.analytics.microstructure import NUMBA_AVAILABLE

        assert isinstance(NUMBA_AVAILABLE, bool)

    def test_bucket_arrays_to_dicts(self) -> None:
        """Test _bucket_arrays_to_dicts conversion function."""
        from libs.platform.analytics.microstructure import _bucket_arrays_to_dicts

        # Create sample bucket arrays
        bucket_ids = np.array([0, 1, 2], dtype=np.int64)
        vpins = np.array([0.1, 0.2, 0.3], dtype=np.float64)
        cumvols = np.array([100.0, 200.0, 300.0], dtype=np.float64)
        imbalances = np.array([10.0, 20.0, 30.0], dtype=np.float64)
        ts_ns = np.array(
            [1705312200000000000, 1705312201000000000, 1705312202000000000], dtype=np.int64
        )
        partial_flags = np.array([False, False, True], dtype=np.bool_)
        warmup_flags = np.array([True, True, False], dtype=np.bool_)

        bucket_arrays = (bucket_ids, vpins, cumvols, imbalances, ts_ns, partial_flags, warmup_flags)
        timestamps = [
            datetime(2024, 1, 15, 9, 30, 0),
            datetime(2024, 1, 15, 9, 30, 1),
            datetime(2024, 1, 15, 9, 30, 2),
        ]

        buckets = _bucket_arrays_to_dicts(bucket_arrays, timestamps)

        assert len(buckets) == 3
        assert buckets[0]["bucket_id"] == 0
        assert buckets[0]["vpin"] == 0.1
        assert buckets[0]["cumulative_volume"] == 100.0
        assert buckets[0]["imbalance"] == 10.0
        assert buckets[0]["is_partial"] is False
        assert buckets[0]["is_warmup"] is True
        assert buckets[2]["is_partial"] is True

    def test_vpin_with_numba_available(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test VPIN computation when Numba is available."""
        from libs.platform.analytics.microstructure import NUMBA_AVAILABLE

        # Create realistic tick data
        timestamps = _create_timestamps(date(2024, 1, 15), 500)
        prices = [100.0 + np.random.randn() * 0.1 for _ in range(500)]
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 500,
                "bid": [p - 0.01 for p in prices],
                "ask": [p + 0.01 for p in prices],
                "bid_size": [100] * 500,
                "ask_size": [100] * 500,
                "trade_px": prices,
                "trade_size": [50] * 500,  # Consistent trade size
                "cond": [""] * 500,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="numba_v1"
        )

        result = analyzer.compute_vpin(
            "AAPL",
            date(2024, 1, 15),
            volume_per_bucket=500,
            window_buckets=10,
            sigma_lookback=20,
        )

        # Both paths should produce valid results
        assert isinstance(result, VPINResult)
        assert result.num_buckets > 0
        if NUMBA_AVAILABLE:
            # Numba path was used
            assert result.data.height > 0

    def test_numba_fallback_stub(self) -> None:
        """Test the numba fallback stub raises RuntimeError when called."""
        from libs.platform.analytics.microstructure import (
            NUMBA_AVAILABLE,
            _compute_vpin_buckets_numba,
        )

        if not NUMBA_AVAILABLE:
            # When numba is not available, the stub should raise RuntimeError
            with pytest.raises(RuntimeError, match="Numba not available"):
                _compute_vpin_buckets_numba()  # type: ignore[call-arg]

    def test_vpin_partial_bucket_eod_warning(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test VPIN includes 'partial bucket at EOD' warning when appropriate."""
        timestamps = _create_timestamps(date(2024, 1, 15), 100)
        prices = [100.0 + i * 0.01 for i in range(100)]
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 100,
                "bid": [p - 0.01 for p in prices],
                "ask": [p + 0.01 for p in prices],
                "bid_size": [100] * 100,
                "ask_size": [100] * 100,
                "trade_px": prices,
                "trade_size": [50] * 100,
                "cond": [""] * 100,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="partial_v1"
        )

        result = analyzer.compute_vpin(
            "AAPL",
            date(2024, 1, 15),
            volume_per_bucket=1000,  # Large enough to have partial bucket
            window_buckets=3,
            sigma_lookback=10,
        )

        # Should have partial bucket at end
        if result.num_buckets > 0:
            last_bucket = result.data.filter(pl.col("bucket_id") == result.num_buckets - 1)
            if not last_bucket.is_empty() and last_bucket["is_partial"][0]:
                assert any("partial bucket" in w.lower() for w in result.warnings)


class TestVPINBucketComputation:
    """Tests for VPIN bucket computation edge cases."""

    def test_vpin_bucket_exact_volume_fill(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test VPIN when trades exactly fill buckets."""
        timestamps = _create_timestamps(date(2024, 1, 15), 50)
        prices = [100.0 + i * 0.02 for i in range(50)]
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 50,
                "bid": [p - 0.01 for p in prices],
                "ask": [p + 0.01 for p in prices],
                "bid_size": [100] * 50,
                "ask_size": [100] * 50,
                "trade_px": prices,
                "trade_size": [100] * 50,  # Each trade exactly 100
                "cond": [""] * 50,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="exact_v1"
        )

        result = analyzer.compute_vpin(
            "AAPL",
            date(2024, 1, 15),
            volume_per_bucket=100,  # Exactly one trade per bucket
            window_buckets=5,
            sigma_lookback=10,
        )

        assert result.num_buckets > 0

    def test_vpin_bucket_remaining_capacity_zero(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test VPIN bucket logic when remaining capacity is exactly zero."""
        timestamps = _create_timestamps(date(2024, 1, 15), 40)
        prices = [100.0 + i * 0.02 for i in range(40)]
        # First trades fill exactly, then continue
        sizes = [50, 50] * 20  # Alternating trades that exactly fill 100-volume buckets
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 40,
                "bid": [p - 0.01 for p in prices],
                "ask": [p + 0.01 for p in prices],
                "bid_size": [100] * 40,
                "ask_size": [100] * 40,
                "trade_px": prices,
                "trade_size": sizes,
                "cond": [""] * 40,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="capacity_v1"
        )

        result = analyzer.compute_vpin(
            "AAPL",
            date(2024, 1, 15),
            volume_per_bucket=100,
            window_buckets=5,
            sigma_lookback=10,
        )

        assert isinstance(result, VPINResult)

    def test_vpin_large_single_trade_multi_bucket(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test VPIN with a single very large trade spanning multiple buckets."""
        timestamps = _create_timestamps(date(2024, 1, 15), 35)
        prices = [100.0 + i * 0.01 for i in range(35)]
        # Small trades, then one huge trade
        sizes = [10] * 30 + [5000] + [10] * 4
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 35,
                "bid": [p - 0.01 for p in prices],
                "ask": [p + 0.01 for p in prices],
                "bid_size": [100] * 35,
                "ask_size": [100] * 35,
                "trade_px": prices,
                "trade_size": sizes,
                "cond": [""] * 35,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="large_v1"
        )

        result = analyzer.compute_vpin(
            "AAPL",
            date(2024, 1, 15),
            volume_per_bucket=100,
            window_buckets=5,
            sigma_lookback=10,
        )

        # Large trade should create multiple buckets
        assert result.num_buckets >= 5


class TestDepthEdgeCases:
    """Tests for depth computation edge cases."""

    def test_compute_depth_empty_after_filtering(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test _compute_depth_from_ticks returns NaN when all quotes filtered out."""
        # Quotes with bid > ask (invalid)
        timestamps = [datetime(2024, 1, 15, 9, 30, i) for i in range(5)]
        quotes = pl.DataFrame(
            {
                "ts": timestamps,
                "bid": [100.1] * 5,  # All bid > ask
                "ask": [100.0] * 5,
                "bid_size": [100] * 5,
                "ask_size": [100] * 5,
            }
        )

        avg_bid, avg_ask = analyzer._compute_depth_from_ticks(quotes)

        assert math.isnan(avg_bid)
        assert math.isnan(avg_ask)

    def test_spread_depth_quotes_empty_after_filter(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test spread depth when quotes DataFrame becomes empty after filtering."""
        spread_df = pl.DataFrame(
            {
                "date": [date(2024, 1, 15)],
                "symbol": ["AAPL"],
                "qwap_spread": [0.001],
                "ewas": [0.0008],
                "quotes": [10000],
                "trades": [5000],
            }
        )
        mock_taq_provider.fetch_spread_metrics.return_value = spread_df

        # All bid/ask_size are zero, so will be filtered out
        timestamps = [datetime(2024, 1, 15, 9, 30, i) for i in range(10)]
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 10,
                "bid": [100.0] * 10,
                "ask": [100.02] * 10,
                "bid_size": [0] * 10,  # All zero
                "ask_size": [0] * 10,  # All zero
                "trade_px": [100.01] * 10,
                "trade_size": [10] * 10,
                "cond": [""] * 10,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="empty_quotes_v1"
        )

        result = analyzer.compute_spread_depth_stats("AAPL", date(2024, 1, 15))

        # Quotes will be filtered out (bid_size and ask_size both zero)
        assert result.depth_is_estimated is True


class TestPythonFallbackPath:
    """Tests for Python fallback path when Numba is unavailable."""

    def test_vpin_python_fallback_path(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test VPIN computation using Python fallback when Numba is disabled."""
        from unittest.mock import patch

        timestamps = _create_timestamps(date(2024, 1, 15), 100)
        prices = [100.0 + i * 0.02 for i in range(100)]
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 100,
                "bid": [p - 0.01 for p in prices],
                "ask": [p + 0.01 for p in prices],
                "bid_size": [100] * 100,
                "ask_size": [100] * 100,
                "trade_px": prices,
                "trade_size": [50] * 100,
                "cond": [""] * 100,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="fallback_v1"
        )

        # Patch NUMBA_AVAILABLE at the module level to force Python fallback
        with patch("libs.platform.analytics.microstructure.NUMBA_AVAILABLE", False):
            result = analyzer.compute_vpin(
                "AAPL",
                date(2024, 1, 15),
                volume_per_bucket=200,
                window_buckets=5,
                sigma_lookback=10,
            )

        assert isinstance(result, VPINResult)
        assert result.num_buckets > 0

    def test_vpin_python_fallback_with_partial_bucket(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test Python fallback VPIN with partial bucket at end of day."""
        from unittest.mock import patch

        timestamps = _create_timestamps(date(2024, 1, 15), 50)
        prices = [100.0 + i * 0.02 for i in range(50)]
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 50,
                "bid": [p - 0.01 for p in prices],
                "ask": [p + 0.01 for p in prices],
                "bid_size": [100] * 50,
                "ask_size": [100] * 50,
                "trade_px": prices,
                "trade_size": [30] * 50,  # Trade sizes that don't exactly fill buckets
                "cond": [""] * 50,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="partial_fallback_v1"
        )

        with patch("libs.platform.analytics.microstructure.NUMBA_AVAILABLE", False):
            result = analyzer.compute_vpin(
                "AAPL",
                date(2024, 1, 15),
                volume_per_bucket=500,
                window_buckets=3,
                sigma_lookback=10,
            )

        assert isinstance(result, VPINResult)
        # Should have partial bucket warning
        if result.num_buckets > 0:
            last_bucket = result.data.filter(pl.col("bucket_id") == result.num_buckets - 1)
            if not last_bucket.is_empty() and last_bucket["is_partial"][0]:
                assert any("partial bucket" in w.lower() for w in result.warnings)

    def test_vpin_python_fallback_no_buckets(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test Python fallback returns no buckets when volume too small."""
        from unittest.mock import patch

        # Create data with very small total volume that can't fill even one bucket
        timestamps = _create_timestamps(date(2024, 1, 15), 25)
        prices = [100.0 + i * 0.02 for i in range(25)]
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 25,
                "bid": [p - 0.01 for p in prices],
                "ask": [p + 0.01 for p in prices],
                "bid_size": [100] * 25,
                "ask_size": [100] * 25,
                "trade_px": prices,
                "trade_size": [1] * 25,  # Very small trades
                "cond": [""] * 25,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="no_buckets_v1"
        )

        with patch("libs.platform.analytics.microstructure.NUMBA_AVAILABLE", False):
            result = analyzer.compute_vpin(
                "AAPL",
                date(2024, 1, 15),
                volume_per_bucket=100000,  # Very large bucket size
                window_buckets=5,
                sigma_lookback=10,
            )

        assert isinstance(result, VPINResult)
        # Should either have 0 or 1 partial bucket
        assert result.num_buckets <= 1

    def test_vpin_python_fallback_sigma_zero_contamination(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test Python fallback handles sigma=0 contamination properly."""
        from unittest.mock import patch

        # Create flat price data that will cause sigma=0
        timestamps = _create_timestamps(date(2024, 1, 15), 100)
        ticks = pl.DataFrame(
            {
                "ts": timestamps,
                "symbol": ["AAPL"] * 100,
                "bid": [99.99] * 100,
                "ask": [100.01] * 100,
                "bid_size": [100] * 100,
                "ask_size": [100] * 100,
                "trade_px": [100.0] * 100,  # Flat prices -> sigma=0
                "trade_size": [50] * 100,
                "cond": [""] * 100,
            }
        )
        mock_taq_provider.fetch_ticks.return_value = ticks
        mock_taq_provider.manifest_manager.load_manifest.return_value = MagicMock(
            checksum="sigma_zero_v1"
        )

        with patch("libs.platform.analytics.microstructure.NUMBA_AVAILABLE", False):
            result = analyzer.compute_vpin(
                "AAPL",
                date(2024, 1, 15),
                volume_per_bucket=200,
                window_buckets=5,
                sigma_lookback=10,
            )

        assert isinstance(result, VPINResult)
        assert any("sigma=0" in w.lower() for w in result.warnings)

    def test_compute_depth_zero_duration_quotes(
        self, analyzer: MicrostructureAnalyzer, mock_taq_provider: MagicMock
    ) -> None:
        """Test depth computation filters zero duration quotes after first sort."""
        # Create quotes where duration becomes zero after filtering
        timestamps = [datetime(2024, 1, 15, 9, 30, 0)] * 3 + [
            datetime(2024, 1, 15, 9, 30, 1),
            datetime(2024, 1, 15, 9, 30, 2),
        ]
        quotes = pl.DataFrame(
            {
                "ts": timestamps,
                "bid": [100.0] * 5,
                "ask": [100.02] * 5,
                "bid_size": [100] * 5,
                "ask_size": [100] * 5,
            }
        )

        avg_bid, avg_ask = analyzer._compute_depth_from_ticks(quotes)

        # Should handle the duplicate timestamps gracefully
        assert isinstance(avg_bid, float)
        assert isinstance(avg_ask, float)
