"""
Unit tests for the main ETL pipeline.

Tests cover:
- Complete pipeline execution
- Freshness check integration
- Corporate action adjustment integration
- Quality gate integration
- File persistence
- Statistics tracking
- Error handling
"""

import shutil
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from libs.common.exceptions import DataQualityError, StalenessError
from libs.data_pipeline.etl import load_adjusted_data, run_etl_pipeline


class TestRunETLPipeline:
    """Tests for run_etl_pipeline function."""

    def test_complete_pipeline_success(self):
        """Full pipeline with good data should succeed."""
        # Create fresh, normal data
        raw_data = pl.DataFrame(
            {
                "symbol": ["AAPL"] * 3,
                "date": ["2024-01-10", "2024-01-11", "2024-01-12"],
                "open": [149.0, 150.5, 151.0],
                "high": [151.0, 152.0, 153.0],
                "low": [148.0, 149.0, 150.0],
                "close": [150.0, 151.0, 152.0],
                "volume": [1_000_000, 1_100_000, 1_200_000],
                "timestamp": [datetime.now(UTC)] * 3,
            }
        )

        result = run_etl_pipeline(raw_data, output_dir=None)

        # Check structure
        assert "adjusted" in result
        assert "quarantined" in result
        assert "stats" in result

        # All data should be good
        assert len(result["adjusted"]) == 3
        assert len(result["quarantined"]) == 0

        # Check stats
        stats = result["stats"]
        assert stats["input_rows"] == 3
        assert stats["adjusted_rows"] == 3
        assert stats["quarantined_rows"] == 0
        assert "AAPL" in stats["symbols_processed"]

    def test_stale_data_raises_error(self):
        """Stale data should raise StalenessError."""
        old_time = datetime.now(UTC) - timedelta(hours=2)
        raw_data = pl.DataFrame(
            {
                "symbol": ["AAPL"] * 3,
                "date": ["2024-01-10", "2024-01-11", "2024-01-12"],
                "open": [149.0, 150.5, 151.0],
                "high": [151.0, 152.0, 153.0],
                "low": [148.0, 149.0, 150.0],
                "close": [150.0, 151.0, 152.0],
                "volume": [1_000_000, 1_100_000, 1_200_000],
                "timestamp": [old_time] * 3,
            }
        )

        with pytest.raises(StalenessError):
            run_etl_pipeline(raw_data, freshness_minutes=30)

    def test_split_adjustment_applied(self):
        """Pipeline should apply split adjustments."""
        raw_data = pl.DataFrame(
            {
                "symbol": ["AAPL"] * 3,
                "date": ["2024-01-10", "2024-01-15", "2024-01-20"],
                "open": [400.0, 100.0, 105.0],
                "high": [420.0, 110.0, 115.0],
                "low": [390.0, 95.0, 100.0],
                "close": [500.0, 125.0, 130.0],
                "volume": [1_000_000, 4_000_000, 3_800_000],
                "timestamp": [datetime.now(UTC)] * 3,
            }
        )

        splits = pl.DataFrame({"symbol": ["AAPL"], "date": ["2024-01-15"], "split_ratio": [4.0]})

        result = run_etl_pipeline(raw_data, splits_df=splits, output_dir=None)

        adjusted = result["adjusted"]

        # Pre-split close should be adjusted from 500 to 125
        pre_split = adjusted.filter(pl.col("date") == date(2024, 1, 10))["close"][0]
        assert pre_split == pytest.approx(125.0, abs=0.01)

    def test_outlier_gets_quarantined(self):
        """Outliers should be separated into quarantine."""
        raw_data = pl.DataFrame(
            {
                "symbol": ["AAPL"] * 4,
                "date": ["2024-01-10", "2024-01-11", "2024-01-12", "2024-01-15"],
                "open": [149.0, 150.5, 225.0, 226.0],
                "high": [151.0, 152.0, 230.0, 228.0],
                "low": [148.0, 149.0, 220.0, 224.0],
                "close": [150.0, 151.0, 225.0, 226.0],  # 50% spike on Jan 12
                "volume": [1_000_000, 1_100_000, 5_000_000, 5_200_000],
                "timestamp": [datetime.now(UTC)] * 4,
            }
        )

        result = run_etl_pipeline(raw_data, outlier_threshold=0.30, output_dir=None)

        # Jan 10, 11, and 15 should be good. Jan 12 (the spike) quarantined
        # Note: Jan 11 -> Jan 12 has +49% return (exceeds 30% threshold)
        assert len(result["adjusted"]) == 3
        assert len(result["quarantined"]) == 1

        # Check stats
        assert result["stats"]["quarantined_rows"] == 1

        # Quarantine should have reason
        assert "reason" in result["quarantined"].columns

    def test_outlier_with_ca_not_quarantined(self):
        """Large move with corporate action should pass."""
        raw_data = pl.DataFrame(
            {
                "symbol": ["AAPL"] * 4,
                "date": ["2024-01-10", "2024-01-11", "2024-01-15", "2024-01-16"],
                "open": [400.0, 410.0, 100.0, 105.0],
                "high": [420.0, 430.0, 110.0, 115.0],
                "low": [390.0, 400.0, 95.0, 100.0],
                "close": [500.0, 504.0, 125.0, 130.0],  # Split on Jan 15
                "volume": [1_000_000, 1_100_000, 4_000_000, 3_800_000],
                "timestamp": [datetime.now(UTC)] * 4,
            }
        )

        splits = pl.DataFrame({"symbol": ["AAPL"], "date": ["2024-01-15"], "split_ratio": [4.0]})

        result = run_etl_pipeline(raw_data, splits_df=splits, output_dir=None)

        # All should pass (split explains the large move on Jan 15)
        # After adjustment: [125.0, 126.0, 125.0, 130.0] - all continuous
        assert len(result["adjusted"]) == 4
        assert len(result["quarantined"]) == 0

    def test_empty_dataframe_raises_error(self):
        """Empty DataFrame should raise ValueError."""
        raw_data = pl.DataFrame(
            {
                "symbol": pl.Series([], dtype=pl.Utf8),
                "date": pl.Series([], dtype=pl.Date),
                "open": pl.Series([], dtype=pl.Float64),
                "high": pl.Series([], dtype=pl.Float64),
                "low": pl.Series([], dtype=pl.Float64),
                "close": pl.Series([], dtype=pl.Float64),
                "volume": pl.Series([], dtype=pl.Int64),
                "timestamp": pl.Series([], dtype=pl.Datetime(time_zone="UTC")),
            }
        )

        with pytest.raises(ValueError) as exc_info:
            run_etl_pipeline(raw_data)

        assert "empty" in str(exc_info.value).lower()

    def test_missing_columns_raises_error(self):
        """Missing required columns should raise DataQualityError."""
        raw_data = pl.DataFrame(
            {
                "symbol": ["AAPL"],
                "close": [150.0],
                # Missing many required columns
            }
        )

        with pytest.raises(DataQualityError) as exc_info:
            run_etl_pipeline(raw_data)

        assert "missing required columns" in str(exc_info.value).lower()

    def test_file_persistence(self):
        """Pipeline should save files to disk when output_dir provided."""
        # Create temporary directory
        temp_dir = Path(tempfile.mkdtemp())

        try:
            raw_data = pl.DataFrame(
                {
                    "symbol": ["AAPL", "MSFT"] * 2,
                    "date": ["2024-01-10", "2024-01-10", "2024-01-11", "2024-01-11"],
                    "open": [149.0, 99.0, 150.5, 100.5],
                    "high": [151.0, 101.0, 152.0, 102.0],
                    "low": [148.0, 98.0, 149.0, 99.0],
                    "close": [150.0, 100.0, 151.0, 101.0],
                    "volume": [1_000_000, 500_000, 1_100_000, 550_000],
                    "timestamp": [datetime.now(UTC)] * 4,
                }
            )

            result = run_etl_pipeline(raw_data, output_dir=temp_dir, run_date=date(2024, 1, 11))

            # Check files were created
            adjusted_dir = temp_dir / "adjusted" / "2024-01-11"
            assert adjusted_dir.exists()
            assert (adjusted_dir / "AAPL.parquet").exists()
            assert (adjusted_dir / "MSFT.parquet").exists()

            # Load files and verify
            aapl_data = pl.read_parquet(adjusted_dir / "AAPL.parquet")
            assert len(aapl_data) == 2
            assert all(aapl_data["symbol"] == "AAPL")

        finally:
            # Clean up
            shutil.rmtree(temp_dir)

    def test_quarantine_file_persistence(self):
        """Quarantined data should be saved to quarantine directory."""
        temp_dir = Path(tempfile.mkdtemp())

        try:
            raw_data = pl.DataFrame(
                {
                    "symbol": ["AAPL"] * 3,
                    "date": ["2024-01-10", "2024-01-11", "2024-01-12"],
                    "open": [150.0, 225.0, 151.0],
                    "high": [151.0, 230.0, 152.0],
                    "low": [149.0, 220.0, 150.0],
                    "close": [150.0, 225.0, 151.0],
                    "volume": [1_000_000, 5_000_000, 1_100_000],
                    "timestamp": [datetime.now(UTC)] * 3,
                }
            )

            result = run_etl_pipeline(raw_data, output_dir=temp_dir, run_date=date(2024, 1, 12))

            # Check quarantine file created
            quarantine_dir = temp_dir / "quarantine" / "2024-01-12"
            assert quarantine_dir.exists()
            assert (quarantine_dir / "AAPL.parquet").exists()

            # Verify it has reason column
            quarantined = pl.read_parquet(quarantine_dir / "AAPL.parquet")
            assert "reason" in quarantined.columns

        finally:
            shutil.rmtree(temp_dir)


class TestLoadAdjustedData:
    """Tests for load_adjusted_data function."""

    def test_load_all_symbols(self):
        """Should load all available symbols."""
        temp_dir = Path(tempfile.mkdtemp())

        try:
            # Create test data
            adjusted_dir = temp_dir / "adjusted" / "2024-01-10"
            adjusted_dir.mkdir(parents=True)

            # Write AAPL data
            aapl = pl.DataFrame(
                {
                    "symbol": ["AAPL"] * 2,
                    "date": [date(2024, 1, 10), date(2024, 1, 11)],
                    "close": [150.0, 151.0],
                }
            )
            aapl.write_parquet(adjusted_dir / "AAPL.parquet")

            # Write MSFT data
            msft = pl.DataFrame(
                {
                    "symbol": ["MSFT"] * 2,
                    "date": [date(2024, 1, 10), date(2024, 1, 11)],
                    "close": [100.0, 101.0],
                }
            )
            msft.write_parquet(adjusted_dir / "MSFT.parquet")

            # Load all
            df = load_adjusted_data(data_dir=temp_dir / "adjusted")

            assert len(df) == 4
            assert set(df["symbol"].unique().to_list()) == {"AAPL", "MSFT"}

        finally:
            shutil.rmtree(temp_dir)

    def test_load_specific_symbols(self):
        """Should filter to specific symbols."""
        temp_dir = Path(tempfile.mkdtemp())

        try:
            adjusted_dir = temp_dir / "adjusted" / "2024-01-10"
            adjusted_dir.mkdir(parents=True)

            # Write multiple symbols
            for symbol in ["AAPL", "MSFT", "GOOGL"]:
                df = pl.DataFrame(
                    {
                        "symbol": [symbol] * 2,
                        "date": [date(2024, 1, 10), date(2024, 1, 11)],
                        "close": [100.0, 101.0],
                    }
                )
                df.write_parquet(adjusted_dir / f"{symbol}.parquet")

            # Load only AAPL
            df = load_adjusted_data(symbols=["AAPL"], data_dir=temp_dir / "adjusted")

            assert len(df) == 2
            assert all(df["symbol"] == "AAPL")

        finally:
            shutil.rmtree(temp_dir)

    def test_load_date_range(self):
        """Should filter by date range."""
        temp_dir = Path(tempfile.mkdtemp())

        try:
            adjusted_dir = temp_dir / "adjusted" / "2024-01"
            adjusted_dir.mkdir(parents=True)

            df = pl.DataFrame(
                {
                    "symbol": ["AAPL"] * 5,
                    "date": [
                        date(2024, 1, 1),
                        date(2024, 1, 10),
                        date(2024, 1, 15),
                        date(2024, 1, 20),
                        date(2024, 1, 31),
                    ],
                    "close": [100.0, 101.0, 102.0, 103.0, 104.0],
                }
            )
            df.write_parquet(adjusted_dir / "AAPL.parquet")

            # Load Jan 10-20 only
            result = load_adjusted_data(
                start_date=date(2024, 1, 10),
                end_date=date(2024, 1, 20),
                data_dir=temp_dir / "adjusted",
            )

            assert len(result) == 3
            assert result["date"].min() == date(2024, 1, 10)
            assert result["date"].max() == date(2024, 1, 20)

        finally:
            shutil.rmtree(temp_dir)

    def test_nonexistent_directory_returns_empty(self):
        """Should return empty DataFrame if directory doesn't exist."""
        df = load_adjusted_data(data_dir=Path("/nonexistent/path"))

        assert len(df) == 0
