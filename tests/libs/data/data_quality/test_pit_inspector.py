"""Tests for pit_inspector.py (P6T13/T13.1).

Tests cover:
- PITInspector.lookup() with fixture data
- Deduplication (latest run_date wins per market date)
- Look-ahead detection (future partitions and contaminated historical)
- Input validation (ticker, date, lookback)
- _safe_table_name() identifier sanitization
- get_available_tickers() and get_date_range() directory scanning
- Staleness computation
- Edge cases: empty data, boundary dates
"""

from __future__ import annotations

import datetime
from pathlib import Path

import polars as pl
import pytest

from libs.data.data_quality.pit_inspector import (
    MAX_LOOKBACK_DAYS,
    PITInspector,
    _safe_table_name,
)

# ============================================================================
# Fixtures
# ============================================================================


def _create_parquet(
    path: Path,
    dates: list[str],
    close_values: list[float],
) -> None:
    """Create a minimal Parquet file with OHLCV data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame(
        {
            "date": [datetime.date.fromisoformat(d) for d in dates],
            "open": close_values,
            "high": [v + 1 for v in close_values],
            "low": [v - 1 for v in close_values],
            "close": close_values,
            "volume": [1000] * len(dates),
        }
    )
    df.write_parquet(str(path))


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """Create a test data directory with adjusted Parquet files."""
    adjusted = tmp_path / "adjusted"

    # Run date 2024-01-15: AAPL data for Jan 10-15
    _create_parquet(
        adjusted / "2024-01-15" / "AAPL.parquet",
        ["2024-01-10", "2024-01-11", "2024-01-12"],
        [150.0, 151.0, 152.0],
    )
    # Run date 2024-01-20: AAPL data for Jan 15-20 (overlaps Jan 15 from first partition)
    _create_parquet(
        adjusted / "2024-01-20" / "AAPL.parquet",
        ["2024-01-15", "2024-01-16", "2024-01-17", "2024-01-18"],
        [153.0, 154.0, 155.0, 156.0],
    )
    # Run date 2024-02-01: AAPL future data
    _create_parquet(
        adjusted / "2024-02-01" / "AAPL.parquet",
        ["2024-01-25", "2024-01-26"],
        [160.0, 161.0],
    )
    # MSFT in one partition only
    _create_parquet(
        adjusted / "2024-01-15" / "MSFT.parquet",
        ["2024-01-10", "2024-01-11"],
        [350.0, 351.0],
    )

    return tmp_path


@pytest.fixture()
def inspector(data_dir: Path) -> PITInspector:
    return PITInspector(data_dir=data_dir)


# ============================================================================
# _safe_table_name
# ============================================================================


class TestSafeTableName:
    def test_valid_identifier(self) -> None:
        assert _safe_table_name("avail", "2024-01-15") == "avail_2024_01_15"

    def test_multiple_parts(self) -> None:
        assert _safe_table_name("t", "2024-01-15", "AAPL") == "t_2024_01_15_AAPL"

    def test_dots_replaced(self) -> None:
        assert _safe_table_name("t", "v1.2") == "t_v1_2"

    def test_unsafe_chars_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unsafe identifier"):
            _safe_table_name("t", "../../etc")

    def test_spaces_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unsafe identifier"):
            _safe_table_name("t", "bad name")

    def test_semicolons_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unsafe identifier"):
            _safe_table_name("t", "DROP;TABLE")


# ============================================================================
# get_available_tickers
# ============================================================================


class TestGetAvailableTickers:
    def test_scans_all_partitions(self, inspector: PITInspector) -> None:
        tickers = inspector.get_available_tickers()
        assert "AAPL" in tickers
        assert "MSFT" in tickers

    def test_sorted(self, inspector: PITInspector) -> None:
        tickers = inspector.get_available_tickers()
        assert tickers == sorted(tickers)

    def test_empty_directory(self, tmp_path: Path) -> None:
        insp = PITInspector(data_dir=tmp_path)
        assert insp.get_available_tickers() == []

    def test_no_adjusted_dir(self, tmp_path: Path) -> None:
        insp = PITInspector(data_dir=tmp_path / "nonexistent")
        assert insp.get_available_tickers() == []


# ============================================================================
# get_date_range
# ============================================================================


class TestGetDateRange:
    def test_returns_min_max(self, inspector: PITInspector) -> None:
        min_d, max_d = inspector.get_date_range()
        assert min_d == datetime.date(2024, 1, 15)
        assert max_d == datetime.date(2024, 2, 1)

    def test_empty_directory(self, tmp_path: Path) -> None:
        (tmp_path / "adjusted").mkdir()
        insp = PITInspector(data_dir=tmp_path)
        assert insp.get_date_range() == (None, None)

    def test_no_adjusted_dir(self, tmp_path: Path) -> None:
        insp = PITInspector(data_dir=tmp_path / "nonexistent")
        assert insp.get_date_range() == (None, None)


# ============================================================================
# Input Validation
# ============================================================================


class TestInputValidation:
    def test_invalid_ticker(self, inspector: PITInspector) -> None:
        with pytest.raises(ValueError, match="Invalid ticker"):
            inspector.lookup("INVALID_TICKER!", datetime.date(2024, 1, 20))

    def test_empty_ticker(self, inspector: PITInspector) -> None:
        with pytest.raises(ValueError, match="Invalid ticker"):
            inspector.lookup("", datetime.date(2024, 1, 20))

    def test_future_knowledge_date(self, inspector: PITInspector) -> None:
        future = datetime.date.today() + datetime.timedelta(days=1)
        with pytest.raises(ValueError, match="in the future"):
            inspector.lookup("AAPL", future)

    def test_lookback_zero(self, inspector: PITInspector) -> None:
        with pytest.raises(ValueError, match="lookback_days"):
            inspector.lookup("AAPL", datetime.date(2024, 1, 20), lookback_days=0)

    def test_lookback_too_large(self, inspector: PITInspector) -> None:
        with pytest.raises(ValueError, match="lookback_days"):
            inspector.lookup(
                "AAPL",
                datetime.date(2024, 1, 20),
                lookback_days=MAX_LOOKBACK_DAYS + 1,
            )


# ============================================================================
# Lookup: Basic
# ============================================================================


class TestLookupBasic:
    def test_data_available(self, inspector: PITInspector) -> None:
        """Lookup with knowledge_date=2024-01-20 should find data from both partitions."""
        result = inspector.lookup(
            "AAPL", datetime.date(2024, 1, 20), lookback_days=30
        )
        assert result.ticker == "AAPL"
        assert result.knowledge_date == datetime.date(2024, 1, 20)
        assert result.total_rows_available > 0
        assert result.latest_available_date is not None

    def test_no_data_for_ticker(self, inspector: PITInspector) -> None:
        """Ticker with no parquet files returns empty result."""
        result = inspector.lookup(
            "GOOG", datetime.date(2024, 1, 20), lookback_days=30
        )
        assert result.total_rows_available == 0
        assert result.data_available == []
        assert result.has_look_ahead_risk is False
        assert result.has_contaminated_historical is False

    def test_no_data_directory(self, tmp_path: Path) -> None:
        """No adjusted directory returns empty result."""
        insp = PITInspector(data_dir=tmp_path)
        result = insp.lookup("AAPL", datetime.date(2024, 1, 20))
        assert result.total_rows_available == 0

    def test_knowledge_date_boundary(self, inspector: PITInspector) -> None:
        """Data from partition with run_date == knowledge_date is included."""
        result = inspector.lookup(
            "AAPL", datetime.date(2024, 1, 15), lookback_days=30
        )
        # Should include data from the 2024-01-15 partition
        assert result.total_rows_available > 0
        run_dates = {p.run_date for p in result.data_available}
        assert datetime.date(2024, 1, 15) in run_dates


# ============================================================================
# Lookup: Deduplication
# ============================================================================


class TestLookupDedup:
    def test_latest_run_date_wins(self, inspector: PITInspector) -> None:
        """When multiple partitions have the same market date, latest run_date wins."""
        result = inspector.lookup(
            "AAPL", datetime.date(2024, 1, 20), lookback_days=30
        )
        # All market dates should be unique
        market_dates = [p.market_date for p in result.data_available]
        assert len(market_dates) == len(set(market_dates))

    def test_dedup_picks_newest_run_date_values(self, data_dir: Path) -> None:
        """With overlapping market dates, the row from the LATEST run_date wins."""
        # Create two partitions with the SAME market date but different close values
        _create_parquet(
            data_dir / "adjusted" / "2024-01-10" / "GOOG.parquet",
            ["2024-01-08"],  # market_date = Jan 8
            [100.0],  # older close
        )
        _create_parquet(
            data_dir / "adjusted" / "2024-01-12" / "GOOG.parquet",
            ["2024-01-08"],  # same market_date
            [105.0],  # newer close (reprocessed)
        )
        insp = PITInspector(data_dir=data_dir)
        result = insp.lookup(
            "GOOG", datetime.date(2024, 1, 15), lookback_days=30
        )
        # Should pick the row from 2024-01-12 partition (newer run_date)
        jan8_points = [
            p for p in result.data_available
            if p.market_date == datetime.date(2024, 1, 8)
        ]
        assert len(jan8_points) == 1
        assert jan8_points[0].run_date == datetime.date(2024, 1, 12)
        assert jan8_points[0].close == 105.0


# ============================================================================
# Lookup: Look-ahead Detection
# ============================================================================


class TestLookAhead:
    def test_future_partitions_detected(self, inspector: PITInspector) -> None:
        """Partitions with run_date > knowledge_date flag look-ahead risk."""
        result = inspector.lookup(
            "AAPL", datetime.date(2024, 1, 20), lookback_days=30
        )
        # 2024-02-01 partition is in the future
        assert result.has_look_ahead_risk is True
        assert result.future_partition_count == 1

    def test_no_future_partitions(self, inspector: PITInspector) -> None:
        """Knowledge date after all data has no look-ahead risk."""
        result = inspector.lookup(
            "AAPL", datetime.date(2024, 3, 1), lookback_days=365
        )
        assert result.future_partition_count == 0

    def test_future_data_sampled(self, inspector: PITInspector) -> None:
        """Future partitions are sampled for preview."""
        result = inspector.lookup(
            "AAPL", datetime.date(2024, 1, 20), lookback_days=30
        )
        assert len(result.data_future) > 0

    def test_contaminated_historical(self, data_dir: Path) -> None:
        """Future-dated market data in historical partition flags contamination."""
        # Add future-dated data to historical partition
        _create_parquet(
            data_dir / "adjusted" / "2024-01-15" / "GOOG.parquet",
            ["2024-01-10", "2024-03-01"],  # Jan 10 OK, Mar 1 is future
            [100.0, 200.0],
        )
        insp = PITInspector(data_dir=data_dir)
        result = insp.lookup(
            "GOOG", datetime.date(2024, 1, 20), lookback_days=30
        )
        assert result.has_look_ahead_risk is True
        assert result.has_contaminated_historical is True

    def test_future_partitions_not_contaminated(self, inspector: PITInspector) -> None:
        """Future partitions without contaminated historical are flagged separately."""
        result = inspector.lookup(
            "AAPL", datetime.date(2024, 1, 20), lookback_days=30
        )
        # Future partition exists but no contamination in historical partitions
        assert result.has_look_ahead_risk is True
        assert result.future_partition_count == 1
        assert result.has_contaminated_historical is False


# ============================================================================
# Lookup: Staleness
# ============================================================================


class TestStaleness:
    def test_stale_when_latest_before_knowledge(self, inspector: PITInspector) -> None:
        """days_stale > 0 when latest data predates knowledge date."""
        result = inspector.lookup(
            "AAPL", datetime.date(2024, 1, 20), lookback_days=30
        )
        # Latest market date from 2024-01-20 partition is 2024-01-18
        assert result.latest_available_date == datetime.date(2024, 1, 18)
        assert result.days_stale is not None
        assert result.days_stale > 0  # At least 1 trading day

    def test_no_staleness_when_no_data(self, inspector: PITInspector) -> None:
        result = inspector.lookup(
            "GOOG", datetime.date(2024, 1, 20), lookback_days=30
        )
        assert result.days_stale is None


# ============================================================================
# Lookup: run_date Tagging
# ============================================================================


class TestRunDateTagging:
    def test_run_date_from_partition(self, inspector: PITInspector) -> None:
        """Each PITDataPoint.run_date comes from the partition directory name."""
        result = inspector.lookup(
            "AAPL", datetime.date(2024, 1, 20), lookback_days=30
        )
        for point in result.data_available:
            # run_date must be one of the known partition dates
            assert point.run_date in {
                datetime.date(2024, 1, 15),
                datetime.date(2024, 1, 20),
            }
