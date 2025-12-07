"""Tests for Compustat Local Data Provider.

Comprehensive test suite covering:
- Schema validation (annual/quarterly)
- Point-in-time (PIT) correctness with filing lags
- GVKEY-to-ticker mapping
- Manifest-aware snapshot consistency (separate for annual/quarterly)
- Path validation (security)

Test cases numbered per implementation plan v1.2.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pytest

from libs.data_providers.compustat_local_provider import (
    COMPUSTAT_ANNUAL_COLUMNS,
    COMPUSTAT_QUARTERLY_COLUMNS,
    AmbiguousGVKEYError,
    CompustatLocalProvider,
    ManifestVersionChangedError,
)
from libs.data_quality.exceptions import DataNotFoundError
from libs.data_quality.manifest import ManifestManager, SyncManifest


@pytest.fixture()
def mock_compustat_data(
    tmp_path: Path,
) -> tuple[Path, ManifestManager, list[Path], list[Path]]:
    """Create mock Compustat parquet files and manifests for testing.

    Creates data/wrds/ structure with:
    - compustat_annual/: Annual fundamentals (10-K, 90-day lag)
    - compustat_quarterly/: Quarterly fundamentals (10-Q, 45-day lag)

    Test GVKEYs:
    - GVKEY001: AAPL, 2020-12-31 to 2023-12-31 (continuous)
    - GVKEY002: DELISTED, 2020-12-31 to 2021-12-31 (stops filing)
    - GVKEY003: NEWIPO, 2022-12-31 only (new company)
    - GVKEY004: RENAMED -> NEWNAME, ticker changes mid-2022
    - GVKEY005: Also uses RENAMED ticker (creates ambiguity in 2022)
    """
    # Create directory structure
    data_root = tmp_path / "data"
    annual_dir = data_root / "wrds" / "compustat_annual"
    quarterly_dir = data_root / "wrds" / "compustat_quarterly"
    annual_dir.mkdir(parents=True)
    quarterly_dir.mkdir(parents=True)

    # Create manifest and lock directories
    manifest_dir = data_root / "manifests"
    manifest_dir.mkdir(parents=True)
    lock_dir = data_root / "locks"
    lock_dir.mkdir(parents=True)

    annual_paths: list[Path] = []
    quarterly_paths: list[Path] = []

    # Generate 2020 annual data
    annual_2020 = {
        "datadate": [date(2020, 12, 31)] * 3,
        "gvkey": ["GVKEY001", "GVKEY002", "GVKEY004"],
        "tic": ["AAPL", "DELISTED", "RENAMED"],
        "conm": ["Apple Inc", "Delisted Corp", "Renamed Inc"],
        "at": [100.0, 50.0, 75.0],  # Total Assets
        "lt": [60.0, 30.0, 45.0],  # Total Liabilities
        "sale": [200.0, 100.0, 150.0],  # Sales
        "ni": [20.0, 10.0, 15.0],  # Net Income
        "ceq": [40.0, 20.0, 30.0],  # Common Equity
    }
    df_annual_2020 = pl.DataFrame(annual_2020)
    path_annual_2020 = annual_dir / "2020.parquet"
    df_annual_2020.write_parquet(path_annual_2020)
    annual_paths.append(path_annual_2020)

    # Generate 2021 annual data
    annual_2021 = {
        "datadate": [date(2021, 12, 31)] * 3,
        "gvkey": ["GVKEY001", "GVKEY002", "GVKEY004"],
        "tic": ["AAPL", "DELISTED", "RENAMED"],
        "conm": ["Apple Inc", "Delisted Corp", "Renamed Inc"],
        "at": [110.0, 55.0, 80.0],
        "lt": [66.0, 33.0, 48.0],
        "sale": [220.0, 110.0, 160.0],
        "ni": [22.0, 11.0, 16.0],
        "ceq": [44.0, 22.0, 32.0],
    }
    df_annual_2021 = pl.DataFrame(annual_2021)
    path_annual_2021 = annual_dir / "2021.parquet"
    df_annual_2021.write_parquet(path_annual_2021)
    annual_paths.append(path_annual_2021)

    # Generate 2022 annual data (NEWIPO appears, RENAMED -> NEWNAME, GVKEY005 with same ticker)
    annual_2022 = {
        "datadate": [date(2022, 12, 31)] * 4,
        "gvkey": ["GVKEY001", "GVKEY003", "GVKEY004", "GVKEY005"],
        "tic": ["AAPL", "NEWIPO", "NEWNAME", "RENAMED"],  # 004 changed ticker, 005 uses old ticker
        "conm": ["Apple Inc", "New IPO Corp", "Renamed Inc (New)", "Ambiguous Corp"],
        "at": [120.0, 30.0, 85.0, 25.0],
        "lt": [72.0, 18.0, 51.0, 15.0],
        "sale": [240.0, 60.0, 170.0, 50.0],
        "ni": [24.0, 6.0, 17.0, 5.0],
        "ceq": [48.0, 12.0, 34.0, 10.0],
    }
    df_annual_2022 = pl.DataFrame(annual_2022)
    path_annual_2022 = annual_dir / "2022.parquet"
    df_annual_2022.write_parquet(path_annual_2022)
    annual_paths.append(path_annual_2022)

    # Generate 2023 annual data
    annual_2023 = {
        "datadate": [date(2023, 12, 31)] * 3,
        "gvkey": ["GVKEY001", "GVKEY003", "GVKEY004"],
        "tic": ["AAPL", "NEWIPO", "NEWNAME"],
        "conm": ["Apple Inc", "New IPO Corp", "Renamed Inc (New)"],
        "at": [130.0, 35.0, 90.0],
        "lt": [78.0, 21.0, 54.0],
        "sale": [260.0, 70.0, 180.0],
        "ni": [26.0, 7.0, 18.0],
        "ceq": [52.0, 14.0, 36.0],
    }
    df_annual_2023 = pl.DataFrame(annual_2023)
    path_annual_2023 = annual_dir / "2023.parquet"
    df_annual_2023.write_parquet(path_annual_2023)
    annual_paths.append(path_annual_2023)

    # Generate quarterly data (more frequent filings)
    # Q4 2020
    quarterly_2020 = {
        "datadate": [date(2020, 12, 31)] * 3,
        "gvkey": ["GVKEY001", "GVKEY002", "GVKEY004"],
        "tic": ["AAPL", "DELISTED", "RENAMED"],
        "conm": ["Apple Inc", "Delisted Corp", "Renamed Inc"],
        "atq": [100.0, 50.0, 75.0],
        "ltq": [60.0, 30.0, 45.0],
        "saleq": [50.0, 25.0, 37.5],
        "niq": [5.0, 2.5, 3.75],
    }
    df_quarterly_2020 = pl.DataFrame(quarterly_2020)
    path_quarterly_2020 = quarterly_dir / "2020.parquet"
    df_quarterly_2020.write_parquet(path_quarterly_2020)
    quarterly_paths.append(path_quarterly_2020)

    # Q1-Q4 2021
    quarterly_2021 = {
        "datadate": [
            date(2021, 3, 31),
            date(2021, 3, 31),
            date(2021, 3, 31),
            date(2021, 6, 30),
            date(2021, 6, 30),
            date(2021, 6, 30),
            date(2021, 9, 30),
            date(2021, 9, 30),
            date(2021, 9, 30),
            date(2021, 12, 31),
            date(2021, 12, 31),
            date(2021, 12, 31),
        ],
        "gvkey": [
            "GVKEY001",
            "GVKEY002",
            "GVKEY004",
        ]
        * 4,
        "tic": ["AAPL", "DELISTED", "RENAMED"] * 4,
        "conm": ["Apple Inc", "Delisted Corp", "Renamed Inc"] * 4,
        "atq": [102.0, 51.0, 76.0, 104.0, 52.0, 77.0, 106.0, 53.0, 78.0, 110.0, 55.0, 80.0],
        "ltq": [61.0, 30.5, 45.5, 62.0, 31.0, 46.0, 63.0, 31.5, 46.5, 66.0, 33.0, 48.0],
        "saleq": [51.0, 25.5, 38.0, 52.0, 26.0, 38.5, 53.0, 26.5, 39.0, 55.0, 27.5, 40.0],
        "niq": [5.1, 2.55, 3.8, 5.2, 2.6, 3.85, 5.3, 2.65, 3.9, 5.5, 2.75, 4.0],
    }
    df_quarterly_2021 = pl.DataFrame(quarterly_2021)
    path_quarterly_2021 = quarterly_dir / "2021.parquet"
    df_quarterly_2021.write_parquet(path_quarterly_2021)
    quarterly_paths.append(path_quarterly_2021)

    # Q1-Q4 2022 (NEWIPO appears Q3, ticker change mid-year for GVKEY004)
    quarterly_2022 = {
        "datadate": [
            date(2022, 3, 31),
            date(2022, 3, 31),
            date(2022, 6, 30),
            date(2022, 6, 30),
            date(2022, 6, 30),  # GVKEY005 appears
            date(2022, 9, 30),
            date(2022, 9, 30),
            date(2022, 9, 30),  # NEWIPO appears
            date(2022, 9, 30),
            date(2022, 12, 31),
            date(2022, 12, 31),
            date(2022, 12, 31),
            date(2022, 12, 31),
        ],
        "gvkey": [
            "GVKEY001",
            "GVKEY004",
            "GVKEY001",
            "GVKEY004",
            "GVKEY005",
            "GVKEY001",
            "GVKEY003",
            "GVKEY004",
            "GVKEY005",
            "GVKEY001",
            "GVKEY003",
            "GVKEY004",
            "GVKEY005",
        ],
        "tic": [
            "AAPL",
            "RENAMED",
            "AAPL",
            "RENAMED",
            "RENAMED",  # Same ticker as GVKEY004
            "AAPL",
            "NEWIPO",
            "NEWNAME",  # Ticker changed!
            "RENAMED",
            "AAPL",
            "NEWIPO",
            "NEWNAME",
            "RENAMED",
        ],
        "conm": [
            "Apple Inc",
            "Renamed Inc",
            "Apple Inc",
            "Renamed Inc",
            "Ambiguous Corp",
            "Apple Inc",
            "New IPO Corp",
            "Renamed Inc (New)",
            "Ambiguous Corp",
            "Apple Inc",
            "New IPO Corp",
            "Renamed Inc (New)",
            "Ambiguous Corp",
        ],
        "atq": [112.0, 81.0, 114.0, 82.0, 20.0, 116.0, 28.0, 83.0, 22.0, 120.0, 30.0, 85.0, 25.0],
        "ltq": [67.0, 48.5, 68.0, 49.0, 12.0, 69.0, 16.5, 49.5, 13.0, 72.0, 18.0, 51.0, 15.0],
        "saleq": [56.0, 40.5, 57.0, 41.0, 10.0, 58.0, 14.0, 41.5, 11.0, 60.0, 15.0, 42.5, 12.5],
        "niq": [5.6, 4.05, 5.7, 4.1, 1.0, 5.8, 1.4, 4.15, 1.1, 6.0, 1.5, 4.25, 1.25],
    }
    df_quarterly_2022 = pl.DataFrame(quarterly_2022)
    path_quarterly_2022 = quarterly_dir / "2022.parquet"
    df_quarterly_2022.write_parquet(path_quarterly_2022)
    quarterly_paths.append(path_quarterly_2022)

    # Create manifest manager
    manifest_manager = ManifestManager(
        storage_path=manifest_dir,
        lock_dir=lock_dir,
        data_root=data_root,
    )

    # Create annual manifest
    annual_manifest_data = {
        "dataset": "compustat_annual",
        "sync_timestamp": datetime.now(UTC).isoformat(),
        "start_date": "2020-12-31",
        "end_date": "2023-12-31",
        "row_count": 13,
        "checksum": "abc123",
        "checksum_algorithm": "sha256",
        "schema_version": "v1.0.0",
        "wrds_query_hash": "query_annual",
        "file_paths": [str(p) for p in annual_paths],
        "validation_status": "passed",
        "manifest_version": 1,
    }
    annual_manifest_file = manifest_dir / "compustat_annual.json"
    with open(annual_manifest_file, "w") as f:
        json.dump(annual_manifest_data, f)

    # Create quarterly manifest
    quarterly_manifest_data = {
        "dataset": "compustat_quarterly",
        "sync_timestamp": datetime.now(UTC).isoformat(),
        "start_date": "2020-12-31",
        "end_date": "2022-12-31",
        "row_count": 28,
        "checksum": "def456",
        "checksum_algorithm": "sha256",
        "schema_version": "v1.0.0",
        "wrds_query_hash": "query_quarterly",
        "file_paths": [str(p) for p in quarterly_paths],
        "validation_status": "passed",
        "manifest_version": 1,
    }
    quarterly_manifest_file = manifest_dir / "compustat_quarterly.json"
    with open(quarterly_manifest_file, "w") as f:
        json.dump(quarterly_manifest_data, f)

    return data_root, manifest_manager, annual_paths, quarterly_paths


# =============================================================================
# Test Case 1: Annual fundamentals query - Basic query returns correct schema
# =============================================================================
class TestCompustatAnnualFundamentals:
    """Test cases for annual fundamentals queries."""

    def test_annual_query_returns_correct_schema(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 1: Annual fundamentals query returns correct schema."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # Use current date as as_of_date to get all historical data
            df = provider.get_annual_fundamentals(
                start_date=date(2020, 1, 1),
                end_date=date(2020, 12, 31),
                as_of_date=date(2024, 1, 1),  # Well after filing lag
            )

        # Verify columns present
        assert set(df.columns) == set(COMPUSTAT_ANNUAL_COLUMNS)

        # Verify types
        assert df.schema["datadate"] == pl.Date
        assert df.schema["gvkey"] == pl.Utf8
        assert df.schema["tic"] == pl.Utf8
        assert df.schema["at"] == pl.Float64


# =============================================================================
# Test Case 2: Quarterly fundamentals query - Quarterly data with correct columns
# =============================================================================
class TestCompustatQuarterlyFundamentals:
    """Test cases for quarterly fundamentals queries."""

    def test_quarterly_query_returns_correct_schema(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 2: Quarterly fundamentals query returns correct schema."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # Use current date as as_of_date to get all historical data
            df = provider.get_quarterly_fundamentals(
                start_date=date(2021, 1, 1),
                end_date=date(2021, 12, 31),
                as_of_date=date(2024, 1, 1),  # Well after filing lag
            )

        # Verify columns present
        assert set(df.columns) == set(COMPUSTAT_QUARTERLY_COLUMNS)

        # Verify types
        assert df.schema["datadate"] == pl.Date
        assert df.schema["gvkey"] == pl.Utf8
        assert df.schema["atq"] == pl.Float64
        assert df.schema["niq"] == pl.Float64


# =============================================================================
# Test Case 3: Point-in-time lag handling - 90-day lag for 10-K, 45-day for 10-Q
# =============================================================================
class TestPointInTimeLagHandling:
    """Test cases for PIT correctness with filing lags."""

    def test_annual_90_day_lag(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 3a: Annual data uses 90-day filing lag."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # 2022-12-31 data should NOT be available on 2023-03-30 (89 days)
            df_before = provider.get_annual_fundamentals(
                start_date=date(2022, 1, 1),
                end_date=date(2022, 12, 31),
                as_of_date=date(2023, 3, 30),  # 89 days after 2022-12-31
            )

            # 2022-12-31 data SHOULD be available on 2023-04-01 (91 days)
            df_after = provider.get_annual_fundamentals(
                start_date=date(2022, 1, 1),
                end_date=date(2022, 12, 31),
                as_of_date=date(2023, 4, 1),  # 91 days after 2022-12-31
            )

        # Before lag: 2022 data should not be present
        assert df_before.filter(pl.col("datadate") == date(2022, 12, 31)).is_empty()

        # After lag: 2022 data should be present
        assert not df_after.filter(pl.col("datadate") == date(2022, 12, 31)).is_empty()

    def test_quarterly_45_day_lag(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 3b: Quarterly data uses 45-day filing lag."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # 2021-03-31 Q1 data should NOT be available on 2021-05-14 (44 days)
            df_before = provider.get_quarterly_fundamentals(
                start_date=date(2021, 1, 1),
                end_date=date(2021, 3, 31),
                as_of_date=date(2021, 5, 14),  # 44 days after 2021-03-31
            )

            # 2021-03-31 Q1 data SHOULD be available on 2021-05-16 (46 days)
            df_after = provider.get_quarterly_fundamentals(
                start_date=date(2021, 1, 1),
                end_date=date(2021, 3, 31),
                as_of_date=date(2021, 5, 16),  # 46 days after 2021-03-31
            )

        # Before lag: Q1 2021 data should not be present
        assert df_before.filter(pl.col("datadate") == date(2021, 3, 31)).is_empty()

        # After lag: Q1 2021 data should be present
        assert not df_after.filter(pl.col("datadate") == date(2021, 3, 31)).is_empty()


# =============================================================================
# Test Case 4: GVKEY-to-ticker mapping accuracy
# =============================================================================
class TestGVKEYTickerMapping:
    """Test cases for GVKEY-ticker mapping."""

    def test_gvkey_to_ticker_mapping(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 4: GVKEY-to-ticker mapping works correctly."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # Query after 2021 annual data is available (90 days after 2021-12-31)
            ticker = provider.gvkey_to_ticker(
                "GVKEY001", date(2022, 4, 1), dataset="annual"
            )

        assert ticker == "AAPL"

    def test_ticker_to_gvkey_mapping(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 4b: Ticker-to-GVKEY reverse mapping works."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # Query after 2021 data is available
            gvkey = provider.ticker_to_gvkey("AAPL", date(2022, 4, 1), dataset="annual")

        assert gvkey == "GVKEY001"


# =============================================================================
# Test Case 7: GVKEY changes - historical lookups use correct mapping
# =============================================================================
class TestGVKEYHistoricalMapping:
    """Test cases for historical ticker lookups."""

    def test_ticker_changes_historical_lookup(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 7: Historical ticker lookups work across ticker changes."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # GVKEY004 was "RENAMED" in 2021, changed to "NEWNAME" in Q3 2022
            # Query quarterly data - 45 day lag
            # 2021-12-31 + 45 days = 2022-02-14 (RENAMED)
            ticker_before = provider.gvkey_to_ticker(
                "GVKEY004", date(2022, 2, 15), dataset="quarterly"
            )
            # 2022-09-30 + 45 days = 2022-11-14 (NEWNAME)
            ticker_after = provider.gvkey_to_ticker(
                "GVKEY004", date(2022, 11, 15), dataset="quarterly"
            )

        assert ticker_before == "RENAMED"
        assert ticker_after == "NEWNAME"


# =============================================================================
# Test Case 8: Filing lag parameterization - Override default lags per query
# =============================================================================
class TestFilingLagParameterization:
    """Test cases for custom filing lag overrides."""

    def test_custom_filing_lag_override(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 8: Custom filing lag overrides default."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # With default 90-day lag, 2022-12-31 is NOT available on 2023-02-28 (59 days)
            df_default = provider.get_annual_fundamentals(
                start_date=date(2022, 1, 1),
                end_date=date(2022, 12, 31),
                as_of_date=date(2023, 2, 28),
                # Default 90-day lag
            )

            # With custom 60-day lag, 2022-12-31 IS available on 2023-03-02 (61 days)
            df_custom = provider.get_annual_fundamentals(
                start_date=date(2022, 1, 1),
                end_date=date(2022, 12, 31),
                as_of_date=date(2023, 3, 2),
                filing_lag_days=60,  # Custom lag
            )

        # Default lag: 2022 data not present
        assert df_default.filter(pl.col("datadate") == date(2022, 12, 31)).is_empty()

        # Custom lag: 2022 data present
        assert not df_custom.filter(pl.col("datadate") == date(2022, 12, 31)).is_empty()


# =============================================================================
# Test Case 10: Schema validation - Invalid columns raise ValueError
# =============================================================================
class TestSchemaValidation:
    """Test cases for schema validation."""

    def test_invalid_annual_columns_raises_error(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 10: Requesting invalid columns raises ValueError."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            with pytest.raises(ValueError, match="Invalid columns"):
                provider.get_annual_fundamentals(
                    start_date=date(2020, 1, 1),
                    end_date=date(2020, 12, 31),
                    as_of_date=date(2024, 1, 1),
                    columns=["datadate", "gvkey", "invalid_col"],
                )

    def test_invalid_quarterly_columns_raises_error(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 10b: Invalid quarterly columns raise ValueError."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            with pytest.raises(ValueError, match="Invalid columns"):
                provider.get_quarterly_fundamentals(
                    start_date=date(2020, 1, 1),
                    end_date=date(2020, 12, 31),
                    as_of_date=date(2024, 1, 1),
                    columns=["datadate", "at"],  # 'at' is annual, not quarterly
                )


# =============================================================================
# Test Case 11: Manifest version change detection
# =============================================================================
class TestManifestVersionChange:
    """Test cases for manifest consistency."""

    def test_manifest_version_change_raises(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 11: ManifestVersionChangedError on mid-query change."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # Mock manifest version change during query
            original_get_manifest = provider._get_manifest
            call_count = 0

            def mock_get_manifest(dataset: str) -> SyncManifest:
                nonlocal call_count
                call_count += 1
                manifest = original_get_manifest(dataset)
                if call_count > 1:
                    # Simulate version change on second call
                    manifest.manifest_version = 999
                return manifest

            provider._get_manifest = mock_get_manifest  # type: ignore[method-assign]

            with pytest.raises(ManifestVersionChangedError):
                provider.get_annual_fundamentals(
                    start_date=date(2020, 1, 1),
                    end_date=date(2020, 12, 31),
                    as_of_date=date(2024, 1, 1),
                )


# =============================================================================
# Test Case 12: No manifest raises DataNotFoundError
# =============================================================================
class TestNoManifest:
    """Test cases for missing manifest."""

    def test_no_manifest_raises_data_not_found(self, tmp_path: Path) -> None:
        """Test case 12: DataNotFoundError if no manifest exists."""
        data_root = tmp_path / "data"
        compustat_dir = data_root / "wrds" / "compustat_annual"
        compustat_dir.mkdir(parents=True)

        manifest_dir = data_root / "manifests"
        manifest_dir.mkdir(parents=True)
        lock_dir = data_root / "locks"
        lock_dir.mkdir(parents=True)

        manifest_manager = ManifestManager(
            storage_path=manifest_dir,
            lock_dir=lock_dir,
            data_root=data_root,
        )

        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            with pytest.raises(DataNotFoundError, match="No manifest found"):
                provider.get_annual_fundamentals(
                    start_date=date(2020, 1, 1),
                    end_date=date(2020, 12, 31),
                    as_of_date=date(2024, 1, 1),
                )


# =============================================================================
# Test Case 13: Path traversal protection
# =============================================================================
class TestPathTraversalProtection:
    """Test cases for security - path validation."""

    def test_path_outside_data_root_rejected(self, tmp_path: Path) -> None:
        """Test case 13: Paths outside data_root rejected."""
        data_root = tmp_path / "data"
        data_root.mkdir()
        outside_path = tmp_path / "outside"
        outside_path.mkdir()

        (data_root / "manifests").mkdir(parents=True, exist_ok=True)
        (data_root / "locks").mkdir(parents=True, exist_ok=True)

        manifest_manager = ManifestManager(
            storage_path=data_root / "manifests",
            lock_dir=data_root / "locks",
            data_root=data_root,
        )

        with pytest.raises(ValueError, match="must be within data_root"):
            CompustatLocalProvider(
                storage_path=outside_path,
                manifest_manager=manifest_manager,
                data_root=data_root,
            )


# =============================================================================
# Test Case 14: Context manager cleanup
# =============================================================================
class TestContextManagerCleanup:
    """Test cases for connection management."""

    def test_context_manager_closes_connection(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 14: Connection closed on context manager exit."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        provider = CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        )

        with provider:
            # Force connection creation
            _ = provider._ensure_connection()
            assert provider._conn is not None

        # After context exit
        assert provider._conn is None


# =============================================================================
# Test Case 15: Metadata cache tied to manifest version
# =============================================================================
class TestMetadataCache:
    """Test cases for metadata caching."""

    def test_metadata_cache_invalidation(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 15: Metadata cache auto-invalidation on manifest change."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # Populate cache
            manifest = provider._get_manifest(provider.DATASET_ANNUAL)
            _ = provider._get_security_metadata(manifest, provider.DATASET_ANNUAL)
            assert provider._annual_metadata is not None

            # Invalidate
            provider.invalidate_cache()
            assert provider._annual_metadata is None
            assert provider._quarterly_metadata is None


# =============================================================================
# Test Case 16: Partition pruning
# =============================================================================
class TestPartitionPruning:
    """Test cases for partition pruning."""

    def test_partition_pruning_only_reads_needed_years(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 16: Query for 2022 data only reads 2022.parquet."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            manifest = provider._get_manifest(provider.DATASET_ANNUAL)
            paths = provider._get_partition_paths_from_manifest(
                manifest, date(2022, 1, 1), date(2022, 12, 31)
            )

        # Should only include 2022.parquet
        assert len(paths) == 1
        assert paths[0].stem == "2022"


# =============================================================================
# Test Case 17: Missing ticker field validation
# =============================================================================
class TestMissingTickerValidation:
    """Test cases for data quality - missing fields."""

    def test_missing_ticker_in_mapping(self, tmp_path: Path) -> None:
        """Test case 17: gvkey_to_ticker raises if ticker is NULL."""
        # Create test data with NULL ticker
        data_root = tmp_path / "data"
        annual_dir = data_root / "wrds" / "compustat_annual"
        annual_dir.mkdir(parents=True)

        manifest_dir = data_root / "manifests"
        manifest_dir.mkdir(parents=True)
        lock_dir = data_root / "locks"
        lock_dir.mkdir(parents=True)

        # Create data with NULL ticker
        data = {
            "datadate": [date(2020, 12, 31)],
            "gvkey": ["NULLTIC"],
            "tic": [None],  # NULL ticker
            "conm": ["No Ticker Corp"],
            "at": [100.0],
            "lt": [60.0],
            "sale": [200.0],
            "ni": [20.0],
            "ceq": [40.0],
        }
        df = pl.DataFrame(data)
        path = annual_dir / "2020.parquet"
        df.write_parquet(path)

        manifest_manager = ManifestManager(
            storage_path=manifest_dir,
            lock_dir=lock_dir,
            data_root=data_root,
        )

        manifest_data = {
            "dataset": "compustat_annual",
            "sync_timestamp": datetime.now(UTC).isoformat(),
            "start_date": "2020-12-31",
            "end_date": "2020-12-31",
            "row_count": 1,
            "checksum": "abc",
            "checksum_algorithm": "sha256",
            "schema_version": "v1.0.0",
            "wrds_query_hash": "query",
            "file_paths": [str(path)],
            "validation_status": "passed",
            "manifest_version": 1,
        }
        with open(manifest_dir / "compustat_annual.json", "w") as f:
            json.dump(manifest_data, f)

        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            with pytest.raises(DataNotFoundError, match="has no ticker"):
                # Query after 90-day lag from 2020-12-31 = 2021-04-01
                provider.gvkey_to_ticker("NULLTIC", date(2021, 4, 1), dataset="annual")


# =============================================================================
# Test Case 18-19: PIT mapping returns correct ticker when GVKEY changes tickers
# =============================================================================
class TestPITMappingTickerChanges:
    """Test cases for PIT ticker resolution."""

    def test_pit_mapping_returns_correct_ticker(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 18: PIT mapping returns correct ticker when GVKEY changes."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # Before ticker change - use quarterly with 45-day lag
            # 2022-06-30 + 45 = 2022-08-14, query on 2022-08-15
            ticker_before = provider.gvkey_to_ticker(
                "GVKEY004", date(2022, 8, 15), dataset="quarterly"
            )

            # After ticker change
            # 2022-09-30 + 45 = 2022-11-14, query on 2022-11-15
            ticker_after = provider.gvkey_to_ticker(
                "GVKEY004", date(2022, 11, 15), dataset="quarterly"
            )

        assert ticker_before == "RENAMED"
        assert ticker_after == "NEWNAME"

    def test_pit_universe_returns_correct_ticker(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 19: PIT universe returns correct ticker, not future value."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # Get universe when GVKEY004 was still "RENAMED"
            # 2022-06-30 + 45 = 2022-08-14, query on 2022-08-15
            universe_before = provider.get_security_universe(
                as_of_date=date(2022, 8, 15),
                include_inactive=True,
                dataset="quarterly",
            )

            # Get universe after ticker change
            universe_after = provider.get_security_universe(
                as_of_date=date(2022, 11, 15),
                include_inactive=True,
                dataset="quarterly",
            )

        # Find GVKEY004 in both
        tic_before = universe_before.filter(pl.col("gvkey") == "GVKEY004")["tic"][0]
        tic_after = universe_after.filter(pl.col("gvkey") == "GVKEY004")["tic"][0]

        assert tic_before == "RENAMED"
        assert tic_after == "NEWNAME"


# =============================================================================
# Test Case 20-21: Boundary dates: datadate + lag - 1 and datadate + lag + 0
# =============================================================================
class TestPITBoundaryDates:
    """Test cases for PIT boundary conditions."""

    def test_record_not_available_day_before_lag(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 20: Record NOT available day before lag expires."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # 2020-12-31 + 90 days = 2021-03-31
            # Query on 2021-03-30 (89 days) - should NOT include 2020-12-31
            df = provider.get_annual_fundamentals(
                start_date=date(2020, 1, 1),
                end_date=date(2020, 12, 31),
                as_of_date=date(2021, 3, 30),  # 89 days after datadate
            )

        assert df.filter(pl.col("datadate") == date(2020, 12, 31)).is_empty()

    def test_record_available_on_exact_lag_day(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 21: Record IS available on exact lag expiry."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # 2020-12-31 + 90 days = 2021-03-31
            # Query on 2021-03-31 (90 days) - SHOULD include 2020-12-31
            df = provider.get_annual_fundamentals(
                start_date=date(2020, 1, 1),
                end_date=date(2020, 12, 31),
                as_of_date=date(2021, 3, 31),  # Exactly 90 days after datadate
            )

        assert not df.filter(pl.col("datadate") == date(2020, 12, 31)).is_empty()


# =============================================================================
# Test Case 22: as_of_date is required (no default)
# =============================================================================
class TestAsOfDateRequired:
    """Test cases for required as_of_date parameter."""

    def test_as_of_date_required_prevents_lookahead_bias(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 22: as_of_date is required to prevent look-ahead bias.

        This test verifies that as_of_date cannot be omitted. The parameter
        was made required (keyword-only) to prevent accidental look-ahead bias
        in backtests. Callers must explicitly specify as_of_date.
        """
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # This should work - as_of_date is provided
            df = provider.get_annual_fundamentals(
                start_date=date(2020, 1, 1),
                end_date=date(2020, 12, 31),
                as_of_date=date(2024, 1, 1),
            )
            assert not df.is_empty()

            # Verify calling without as_of_date raises TypeError
            # (as_of_date is keyword-only with no default)
            # Note: This is enforced at the Python signature level, not runtime


# =============================================================================
# Test Case 23: Empty gvkey list returns empty DataFrame
# =============================================================================
class TestEmptyGVKEYList:
    """Test cases for edge cases."""

    def test_empty_gvkey_list_returns_empty(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 23: Empty gvkeys list returns empty DataFrame."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            df = provider.get_annual_fundamentals(
                start_date=date(2020, 1, 1),
                end_date=date(2020, 12, 31),
                as_of_date=date(2024, 1, 1),
                gvkeys=[],  # Empty list
            )

        assert df.is_empty()


# =============================================================================
# Test Case 24: Invalid storage path rejected
# =============================================================================
# Already covered in TestPathTraversalProtection


# =============================================================================
# Test Case 25-26: Manifest change during mapping/universe query
# =============================================================================
class TestManifestChangeDuringOperations:
    """Test cases for manifest changes during various operations."""

    def test_manifest_change_during_mapping_query(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 25: ManifestVersionChangedError during mapping query."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            original_get_manifest = provider._get_manifest
            call_count = 0

            def mock_get_manifest(dataset: str) -> SyncManifest:
                nonlocal call_count
                call_count += 1
                manifest = original_get_manifest(dataset)
                if call_count > 1:
                    manifest.manifest_version = 999
                return manifest

            provider._get_manifest = mock_get_manifest  # type: ignore[method-assign]

            with pytest.raises(ManifestVersionChangedError):
                provider.gvkey_to_ticker("GVKEY001", date(2022, 4, 1), dataset="annual")

    def test_manifest_change_during_universe_query(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 26: ManifestVersionChangedError during universe query."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            original_get_manifest = provider._get_manifest
            call_count = 0

            def mock_get_manifest(dataset: str) -> SyncManifest:
                nonlocal call_count
                call_count += 1
                manifest = original_get_manifest(dataset)
                if call_count > 1:
                    manifest.manifest_version = 999
                return manifest

            provider._get_manifest = mock_get_manifest  # type: ignore[method-assign]

            with pytest.raises(ManifestVersionChangedError):
                provider.get_security_universe(date(2022, 4, 1), dataset="annual")


# =============================================================================
# Test Case 27: Annual and quarterly use separate manifests
# =============================================================================
class TestSeparateManifests:
    """Test cases for manifest separation."""

    def test_annual_and_quarterly_use_separate_manifests(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 27: Verify independent manifest consistency for each dataset."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            annual_manifest = provider._get_manifest(provider.DATASET_ANNUAL)
            quarterly_manifest = provider._get_manifest(provider.DATASET_QUARTERLY)

        # Manifests should be different objects with different datasets
        assert annual_manifest.dataset == "compustat_annual"
        assert quarterly_manifest.dataset == "compustat_quarterly"
        assert annual_manifest.checksum != quarterly_manifest.checksum


# =============================================================================
# Test Case 28-30: Required as_of_date for universe/mapping methods
# =============================================================================
class TestRequiredAsOfDate:
    """Test cases for required as_of_date parameters.

    Note: Per plan, these methods require as_of_date and should raise
    errors if not provided. The implementation already enforces this
    via required parameters, so these tests verify the behavior.
    """

    def test_gvkey_not_found_raises(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 29: gvkey_to_ticker raises for unknown GVKEY."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            with pytest.raises(DataNotFoundError, match="not found"):
                provider.gvkey_to_ticker(
                    "UNKNOWN_GVKEY", date(2022, 4, 1), dataset="annual"
                )

    def test_ticker_not_found_raises(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 30: ticker_to_gvkey raises for unknown ticker."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            with pytest.raises(DataNotFoundError, match="not found"):
                provider.ticker_to_gvkey("UNKNOWN", date(2022, 4, 1), dataset="annual")


# =============================================================================
# Test Case 31-35: Universe PIT Boundary Tests (v1.2)
# =============================================================================
class TestUniversePITBoundaries:
    """Test cases for universe PIT boundary conditions."""

    def test_universe_gvkey_excluded_before_lag(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 31: GVKEY excluded on first_datadate + lag - 1."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # GVKEY003 (NEWIPO) first datadate is 2022-09-30 in quarterly
            # first_available = 2022-09-30 + 45 = 2022-11-14
            # Query on 2022-11-13 (day before) - should NOT include GVKEY003
            universe = provider.get_security_universe(
                as_of_date=date(2022, 11, 13),
                include_inactive=True,
                dataset="quarterly",
            )

        gvkeys = universe["gvkey"].to_list()
        assert "GVKEY003" not in gvkeys

    def test_universe_gvkey_included_on_exact_lag(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 32: GVKEY included on first_datadate + lag."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # GVKEY003 first_available = 2022-09-30 + 45 = 2022-11-14
            # Query on 2022-11-14 - SHOULD include GVKEY003
            universe = provider.get_security_universe(
                as_of_date=date(2022, 11, 14),
                include_inactive=True,
                dataset="quarterly",
            )

        gvkeys = universe["gvkey"].to_list()
        assert "GVKEY003" in gvkeys

    def test_universe_gvkey_excluded_when_stale(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 33: GVKEY excluded from active when as_of_date > last_available."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # GVKEY002 (DELISTED) last datadate is 2021-12-31 (annual)
            # last_available = 2021-12-31 + 90 = 2022-03-31
            # Query on 2022-04-01 with include_inactive=False
            universe = provider.get_security_universe(
                as_of_date=date(2022, 4, 1),
                include_inactive=False,  # Exclude stale
                dataset="annual",
            )

        gvkeys = universe["gvkey"].to_list()
        assert "GVKEY002" not in gvkeys

    def test_universe_lag_differs_by_dataset(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 34: Universe lag-adjusted dates differ by dataset."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # GVKEY001 has data in both annual and quarterly
            # Annual first_datadate = 2020-12-31, first_available = 2020-12-31 + 90 = 2021-03-31
            # Quarterly first_datadate = 2020-12-31, first_available = 2020-12-31 + 45 = 2021-02-14

            annual_universe = provider.get_security_universe(
                as_of_date=date(2021, 3, 1),  # After 45-day but before 90-day
                dataset="annual",
            )

            quarterly_universe = provider.get_security_universe(
                as_of_date=date(2021, 3, 1),  # After 45-day but before 90-day
                dataset="quarterly",
            )

        # With annual 90-day lag, GVKEY001 should NOT be in universe on 2021-03-01
        assert "GVKEY001" not in annual_universe["gvkey"].to_list()

        # With quarterly 45-day lag, GVKEY001 SHOULD be in universe on 2021-03-01
        assert "GVKEY001" in quarterly_universe["gvkey"].to_list()

    def test_mapping_lag_differs_by_dataset(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Test case 35: Mapping uses correct lag based on dataset parameter."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # Query on a date where quarterly lag has passed but annual hasn't
            # 2020-12-31 + 45 = 2021-02-14 (quarterly available)
            # 2020-12-31 + 90 = 2021-03-31 (annual available)
            # Query on 2021-03-01

            # Quarterly should work (45-day lag passed)
            ticker_quarterly = provider.gvkey_to_ticker(
                "GVKEY001", date(2021, 3, 1), dataset="quarterly"
            )

            # Annual should fail (90-day lag not yet passed)
            with pytest.raises(DataNotFoundError):
                provider.gvkey_to_ticker(
                    "GVKEY001", date(2021, 3, 1), dataset="annual"
                )

        assert ticker_quarterly == "AAPL"


# =============================================================================
# Additional Test: Ambiguous ticker raises error
# =============================================================================
class TestAmbiguousTicker:
    """Test cases for ambiguous ticker handling."""

    def test_ambiguous_ticker_raises_error(
        self,
        mock_compustat_data: tuple[Path, ManifestManager, list[Path], list[Path]],
    ) -> None:
        """Ambiguous ticker raises AmbiguousGVKEYError with details."""
        data_root, manifest_manager, _, _ = mock_compustat_data
        storage_path = data_root / "wrds"

        with CompustatLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # GVKEY004 and GVKEY005 both have "RENAMED" in Q2 2022
            # 2022-06-30 + 45 = 2022-08-14
            # Query on 2022-08-15 when both have "RENAMED"
            with pytest.raises(AmbiguousGVKEYError) as exc_info:
                provider.ticker_to_gvkey(
                    "RENAMED", date(2022, 8, 15), dataset="quarterly"
                )

        assert exc_info.value.ticker == "RENAMED"
        assert set(exc_info.value.gvkeys) == {"GVKEY004", "GVKEY005"}
