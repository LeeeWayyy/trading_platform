"""Tests for FamaFrenchLocalProvider.

Comprehensive test suite covering:
- Factor model queries (3-factor, 5-factor, 6-factor)
- Industry portfolio queries (10, 30, 49 industries)
- Daily and monthly frequencies
- Return normalization (percent → decimal)
- Atomic writes with quarantine
- Manifest consistency
- Error handling
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from libs.data_providers.fama_french_local_provider import (
    ChecksumError,
    FamaFrenchLocalProvider,
    FamaFrenchSyncError,
)
from libs.data_quality.exceptions import DataNotFoundError

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture()
def provider(tmp_path: Path) -> FamaFrenchLocalProvider:
    """Create a FamaFrenchLocalProvider for testing."""
    storage_path = tmp_path / "fama_french"
    storage_path.mkdir(parents=True, exist_ok=True)
    return FamaFrenchLocalProvider(storage_path=storage_path)


@pytest.fixture()
def mock_ff3_data() -> pl.DataFrame:
    """Create mock 3-factor data (already normalized to decimal)."""
    return pl.DataFrame(
        {
            "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
            "mkt_rf": [0.0105, -0.0052, 0.0078],  # Already in decimal
            "smb": [0.0023, -0.0011, 0.0045],
            "hml": [-0.0034, 0.0067, -0.0012],
            "rf": [0.0002, 0.0002, 0.0002],
        }
    )


@pytest.fixture()
def mock_ff5_data() -> pl.DataFrame:
    """Create mock 5-factor data (already normalized to decimal)."""
    return pl.DataFrame(
        {
            "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
            "mkt_rf": [0.0105, -0.0052, 0.0078],
            "smb": [0.0023, -0.0011, 0.0045],
            "hml": [-0.0034, 0.0067, -0.0012],
            "rmw": [0.0015, -0.0008, 0.0022],
            "cma": [-0.0011, 0.0033, -0.0005],
            "rf": [0.0002, 0.0002, 0.0002],
        }
    )


@pytest.fixture()
def mock_momentum_data() -> pl.DataFrame:
    """Create mock momentum data (already normalized to decimal)."""
    return pl.DataFrame(
        {
            "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
            "umd": [0.0089, -0.0123, 0.0056],
        }
    )


@pytest.fixture()
def mock_industry_data() -> pl.DataFrame:
    """Create mock industry portfolio data (already normalized to decimal)."""
    return pl.DataFrame(
        {
            "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
            "nodur": [0.0112, -0.0045, 0.0089],
            "durbl": [0.0078, -0.0023, 0.0134],
            "manuf": [0.0056, -0.0067, 0.0045],
            "enrgy": [0.0145, 0.0078, -0.0034],
            "hitec": [0.0189, -0.0112, 0.0156],
            "telcm": [0.0034, -0.0011, 0.0023],
            "shops": [0.0067, -0.0034, 0.0078],
            "hlth": [0.0023, 0.0045, 0.0012],
            "utils": [0.0011, 0.0023, -0.0005],
            "other": [0.0045, -0.0023, 0.0034],
        }
    )


@pytest.fixture()
def provider_with_data(
    provider: FamaFrenchLocalProvider,
    mock_ff3_data: pl.DataFrame,
    mock_ff5_data: pl.DataFrame,
    mock_momentum_data: pl.DataFrame,
    mock_industry_data: pl.DataFrame,
) -> FamaFrenchLocalProvider:
    """Create provider with pre-populated data files."""
    # Write factor files
    factors_dir = provider._factors_dir
    factors_dir.mkdir(parents=True, exist_ok=True)

    mock_ff3_data.write_parquet(factors_dir / "factors_3_daily.parquet")
    mock_ff5_data.write_parquet(factors_dir / "factors_5_daily.parquet")
    mock_momentum_data.write_parquet(factors_dir / "momentum_daily.parquet")

    # Create 6-factor by joining
    ff6_data = mock_ff5_data.join(mock_momentum_data, on="date", how="inner")
    # Reorder to put RF at end
    cols = ["date", "mkt_rf", "smb", "hml", "rmw", "cma", "umd", "rf"]
    ff6_data = ff6_data.select(cols)
    ff6_data.write_parquet(factors_dir / "factors_6_daily.parquet")

    # Write monthly versions (same data for testing)
    mock_ff3_data.write_parquet(factors_dir / "factors_3_monthly.parquet")
    mock_ff5_data.write_parquet(factors_dir / "factors_5_monthly.parquet")
    mock_momentum_data.write_parquet(factors_dir / "momentum_monthly.parquet")
    ff6_data.write_parquet(factors_dir / "factors_6_monthly.parquet")

    # Write industry files
    industries_dir = provider._industries_dir
    industries_dir.mkdir(parents=True, exist_ok=True)

    mock_industry_data.write_parquet(industries_dir / "ind10_daily.parquet")
    mock_industry_data.write_parquet(industries_dir / "ind10_monthly.parquet")
    mock_industry_data.write_parquet(industries_dir / "ind30_daily.parquet")
    mock_industry_data.write_parquet(industries_dir / "ind30_monthly.parquet")
    mock_industry_data.write_parquet(industries_dir / "ind49_daily.parquet")
    mock_industry_data.write_parquet(industries_dir / "ind49_monthly.parquet")

    return provider


# =============================================================================
# Factor Model Tests
# =============================================================================


class TestGetFactors:
    """Tests for get_factors() method."""

    def test_ff3_daily_returns_correct_columns(
        self, provider_with_data: FamaFrenchLocalProvider
    ) -> None:
        """Test 3-factor daily returns correct columns."""
        df = provider_with_data.get_factors(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            model="ff3",
            frequency="daily",
        )

        assert set(df.columns) == {"date", "mkt_rf", "smb", "hml", "rf"}
        assert df.height == 3

    def test_ff3_monthly_returns_correct_columns(
        self, provider_with_data: FamaFrenchLocalProvider
    ) -> None:
        """Test 3-factor monthly returns correct columns."""
        df = provider_with_data.get_factors(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            model="ff3",
            frequency="monthly",
        )

        assert set(df.columns) == {"date", "mkt_rf", "smb", "hml", "rf"}

    def test_ff5_daily_adds_rmw_cma_columns(
        self, provider_with_data: FamaFrenchLocalProvider
    ) -> None:
        """Test 5-factor daily adds RMW and CMA columns."""
        df = provider_with_data.get_factors(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            model="ff5",
            frequency="daily",
        )

        assert "rmw" in df.columns
        assert "cma" in df.columns
        assert set(df.columns) == {"date", "mkt_rf", "smb", "hml", "rmw", "cma", "rf"}

    def test_ff5_monthly_adds_rmw_cma_columns(
        self, provider_with_data: FamaFrenchLocalProvider
    ) -> None:
        """Test 5-factor monthly adds RMW and CMA columns."""
        df = provider_with_data.get_factors(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            model="ff5",
            frequency="monthly",
        )

        assert "rmw" in df.columns
        assert "cma" in df.columns

    def test_ff6_daily_includes_umd(self, provider_with_data: FamaFrenchLocalProvider) -> None:
        """Test 6-factor daily includes UMD (momentum)."""
        df = provider_with_data.get_factors(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            model="ff6",
            frequency="daily",
        )

        assert "umd" in df.columns
        expected_cols = {"date", "mkt_rf", "smb", "hml", "rmw", "cma", "umd", "rf"}
        assert set(df.columns) == expected_cols

    def test_ff6_monthly_includes_umd(self, provider_with_data: FamaFrenchLocalProvider) -> None:
        """Test 6-factor monthly includes UMD (momentum)."""
        df = provider_with_data.get_factors(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            model="ff6",
            frequency="monthly",
        )

        assert "umd" in df.columns

    def test_date_filtering_works(self, provider_with_data: FamaFrenchLocalProvider) -> None:
        """Test date filtering returns correct rows."""
        df = provider_with_data.get_factors(
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 3),
            model="ff3",
            frequency="daily",
        )

        assert df.height == 2
        dates = df.get_column("date").to_list()
        assert date(2024, 1, 2) in dates
        assert date(2024, 1, 3) in dates
        assert date(2024, 1, 4) not in dates

    def test_empty_date_range_returns_empty_dataframe(
        self, provider_with_data: FamaFrenchLocalProvider
    ) -> None:
        """Test empty date range returns empty DataFrame."""
        df = provider_with_data.get_factors(
            start_date=date(2020, 1, 1),
            end_date=date(2020, 1, 31),
            model="ff3",
            frequency="daily",
        )

        assert df.height == 0

    def test_invalid_model_raises_error(self, provider_with_data: FamaFrenchLocalProvider) -> None:
        """Test invalid model raises ValueError."""
        with pytest.raises(ValueError, match="Invalid model"):
            provider_with_data.get_factors(
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                model="ff7",  # type: ignore[arg-type]
                frequency="daily",
            )

    def test_invalid_frequency_raises_error(
        self, provider_with_data: FamaFrenchLocalProvider
    ) -> None:
        """Test invalid frequency raises ValueError."""
        with pytest.raises(ValueError, match="Invalid frequency"):
            provider_with_data.get_factors(
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                model="ff3",
                frequency="weekly",  # type: ignore[arg-type]
            )

    def test_missing_data_raises_error(self, provider: FamaFrenchLocalProvider) -> None:
        """Test missing data raises DataNotFoundError."""
        with pytest.raises(DataNotFoundError, match="Factor data not found"):
            provider.get_factors(
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                model="ff3",
                frequency="daily",
            )


# =============================================================================
# Industry Portfolio Tests
# =============================================================================


class TestGetIndustryReturns:
    """Tests for get_industry_returns() method."""

    def test_10_industry_daily(self, provider_with_data: FamaFrenchLocalProvider) -> None:
        """Test 10-industry daily download and storage."""
        df = provider_with_data.get_industry_returns(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            num_industries=10,
            frequency="daily",
        )

        assert df.height == 3
        assert "date" in df.columns

    def test_10_industry_monthly(self, provider_with_data: FamaFrenchLocalProvider) -> None:
        """Test 10-industry monthly download and storage."""
        df = provider_with_data.get_industry_returns(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            num_industries=10,
            frequency="monthly",
        )

        assert df.height == 3

    def test_30_industry_daily(self, provider_with_data: FamaFrenchLocalProvider) -> None:
        """Test 30-industry daily download and storage."""
        df = provider_with_data.get_industry_returns(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            num_industries=30,
            frequency="daily",
        )

        assert df.height == 3

    def test_30_industry_monthly(self, provider_with_data: FamaFrenchLocalProvider) -> None:
        """Test 30-industry monthly download and storage."""
        df = provider_with_data.get_industry_returns(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            num_industries=30,
            frequency="monthly",
        )

        assert df.height == 3

    def test_49_industry_daily(self, provider_with_data: FamaFrenchLocalProvider) -> None:
        """Test 49-industry daily download and storage."""
        df = provider_with_data.get_industry_returns(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            num_industries=49,
            frequency="daily",
        )

        assert df.height == 3

    def test_49_industry_monthly(self, provider_with_data: FamaFrenchLocalProvider) -> None:
        """Test 49-industry monthly download and storage."""
        df = provider_with_data.get_industry_returns(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            num_industries=49,
            frequency="monthly",
        )

        assert df.height == 3

    def test_invalid_num_industries_raises_error(
        self, provider_with_data: FamaFrenchLocalProvider
    ) -> None:
        """Test invalid num_industries raises ValueError."""
        with pytest.raises(ValueError, match="Invalid num_industries"):
            provider_with_data.get_industry_returns(
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                num_industries=25,  # type: ignore[arg-type]
                frequency="daily",
            )

    def test_missing_industry_data_raises_error(self, provider: FamaFrenchLocalProvider) -> None:
        """Test missing industry data raises DataNotFoundError."""
        with pytest.raises(DataNotFoundError, match="Industry data not found"):
            provider.get_industry_returns(
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                num_industries=10,
                frequency="daily",
            )


# =============================================================================
# Return Normalization Tests
# =============================================================================


class TestReturnNormalization:
    """Tests for return normalization (percent → decimal)."""

    def test_factor_returns_normalized(self, provider: FamaFrenchLocalProvider) -> None:
        """Test factor returns converted from percent to decimal."""
        # Create percent data (Ken French format)
        percent_data = pl.DataFrame(
            {
                "date": [date(2024, 1, 2)],
                "mkt_rf": [1.05],  # 1.05%
                "smb": [0.23],
                "hml": [-0.34],
                "rf": [0.02],
            }
        )

        # Normalize
        normalized = provider._normalize_returns(percent_data)

        # Check values are in decimal form
        assert abs(normalized.get_column("mkt_rf")[0] - 0.0105) < 1e-10
        assert abs(normalized.get_column("rf")[0] - 0.0002) < 1e-10

    def test_industry_returns_normalized(self, provider: FamaFrenchLocalProvider) -> None:
        """Test industry returns converted from percent to decimal."""
        # Create percent data
        percent_data = pl.DataFrame(
            {
                "date": [date(2024, 1, 2)],
                "nodur": [1.12],  # 1.12%
                "durbl": [-0.45],
            }
        )

        normalized = provider._normalize_returns(percent_data)

        assert abs(normalized.get_column("nodur")[0] - 0.0112) < 1e-10
        assert abs(normalized.get_column("durbl")[0] - (-0.0045)) < 1e-10

    def test_daily_factor_values_in_expected_range(
        self, provider_with_data: FamaFrenchLocalProvider
    ) -> None:
        """Test daily factor values in expected range [-0.10, 0.10]."""
        df = provider_with_data.get_factors(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            model="ff3",
            frequency="daily",
        )

        for col in ["mkt_rf", "smb", "hml"]:
            values = df.get_column(col).to_list()
            for v in values:
                assert -0.10 <= v <= 0.10, f"{col} value {v} out of expected range"


# =============================================================================
# Atomic Write & Quarantine Tests
# =============================================================================


class TestAtomicWrite:
    """Tests for atomic write pattern."""

    def test_temp_files_never_visible(self, provider: FamaFrenchLocalProvider) -> None:
        """Test temp files (.tmp) never visible after write."""
        df = pl.DataFrame(
            {
                "date": [date(2024, 1, 2)],
                "mkt_rf": [0.0105],
            }
        )

        target_path = provider._factors_dir / "test.parquet"
        provider._atomic_write_parquet(df, target_path)

        # Check no .tmp files exist
        tmp_files = list(provider._factors_dir.glob("*.tmp"))
        assert len(tmp_files) == 0
        assert target_path.exists()

    def test_interrupted_write_cleans_up(self, provider: FamaFrenchLocalProvider) -> None:
        """Test interrupted write cleans up temp file."""
        # Mock write to fail after creating temp file
        with patch.object(pl.DataFrame, "write_parquet", side_effect=OSError("Disk full")):
            df = pl.DataFrame(
                {
                    "date": [date(2024, 1, 2)],
                    "mkt_rf": [0.0105],
                }
            )

            target_path = provider._factors_dir / "test.parquet"

            with pytest.raises(OSError, match="Disk full"):
                provider._atomic_write_parquet(df, target_path)

        # Verify no temp files remain
        tmp_files = list(provider._factors_dir.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_checksum_mismatch_quarantines(self, provider: FamaFrenchLocalProvider) -> None:
        """Test checksum mismatch moves file to quarantine."""
        df = pl.DataFrame(
            {
                "date": [date(2024, 1, 2)],
                "mkt_rf": [0.0105],
            }
        )

        target_path = provider._factors_dir / "test.parquet"

        with pytest.raises(ChecksumError, match="Checksum mismatch"):
            provider._atomic_write_parquet(df, target_path, expected_checksum="wrong_checksum")

        # Check file was quarantined
        quarantine_files = list(provider._quarantine_dir.glob("*"))
        assert len(quarantine_files) == 1
        assert "checksum_mismatch" in quarantine_files[0].name

    def test_empty_dataframe_quarantines(self, provider: FamaFrenchLocalProvider) -> None:
        """Test empty DataFrame moves file to quarantine."""
        df = pl.DataFrame({"date": [], "mkt_rf": []})

        target_path = provider._factors_dir / "test.parquet"

        with pytest.raises(ValueError, match="Empty DataFrame"):
            provider._atomic_write_parquet(df, target_path)

        # Check file was quarantined
        quarantine_files = list(provider._quarantine_dir.glob("*"))
        assert len(quarantine_files) == 1
        assert "empty_dataframe" in quarantine_files[0].name

    def test_quarantine_directory_created_if_not_exists(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test quarantine directory created if not exists."""
        # Ensure quarantine dir doesn't exist
        if provider._quarantine_dir.exists():
            import shutil

            shutil.rmtree(provider._quarantine_dir)

        df = pl.DataFrame({"date": [], "mkt_rf": []})
        target_path = provider._factors_dir / "test.parquet"

        with pytest.raises(ValueError, match="Empty DataFrame, file quarantined"):
            provider._atomic_write_parquet(df, target_path)

        # Quarantine dir should now exist
        assert provider._quarantine_dir.exists()


# =============================================================================
# Manifest Tests
# =============================================================================


class TestManifest:
    """Tests for manifest functionality."""

    def test_get_manifest_returns_none_when_missing(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test get_manifest returns None when no manifest exists."""
        assert provider.get_manifest() is None

    def test_manifest_written_after_atomic_write(self, provider: FamaFrenchLocalProvider) -> None:
        """Test manifest is written after sync."""
        # Write manifest
        manifest_data = {
            "dataset": "fama_french",
            "sync_timestamp": "2024-01-02T10:00:00Z",
            "files": {
                "factors_3_daily.parquet": {
                    "checksum": "abc123",
                    "row_count": 100,
                }
            },
        }
        provider._atomic_write_manifest(manifest_data)

        # Read it back
        manifest = provider.get_manifest()
        assert manifest is not None
        assert manifest["dataset"] == "fama_french"
        assert "factors_3_daily.parquet" in manifest["files"]

    def test_verify_data_detects_valid_checksums(self, provider: FamaFrenchLocalProvider) -> None:
        """Test verify_data validates checksums correctly."""
        # Write a test file
        df = pl.DataFrame(
            {
                "date": [date(2024, 1, 2)],
                "mkt_rf": [0.0105],
            }
        )
        target_path = provider._factors_dir / "test.parquet"
        checksum = provider._atomic_write_parquet(df, target_path)

        # Write manifest with correct checksum
        manifest_data = {"files": {"test.parquet": {"checksum": checksum}}}
        provider._atomic_write_manifest(manifest_data)

        # Verify
        results = provider.verify_data()
        assert results["test.parquet"] is True

    def test_verify_data_detects_invalid_checksums(self, provider: FamaFrenchLocalProvider) -> None:
        """Test verify_data detects invalid checksums."""
        # Write a test file
        df = pl.DataFrame(
            {
                "date": [date(2024, 1, 2)],
                "mkt_rf": [0.0105],
            }
        )
        target_path = provider._factors_dir / "test.parquet"
        provider._atomic_write_parquet(df, target_path)

        # Write manifest with wrong checksum
        manifest_data = {"files": {"test.parquet": {"checksum": "wrong_checksum"}}}
        provider._atomic_write_manifest(manifest_data)

        # Verify
        results = provider.verify_data()
        assert results["test.parquet"] is False

    def test_manifest_regenerated_for_existing_files_without_entry(
        self, provider: FamaFrenchLocalProvider, mock_ff3_data: pl.DataFrame
    ) -> None:
        """Test manifest entries are regenerated for existing files missing from manifest.

        This covers the case where:
        1. Files exist on disk (e.g., manual copy or manifest loss)
        2. Manifest is missing or doesn't have entry for these files
        3. On sync (not forced), entries should be regenerated from existing files
        """
        # Write a file directly (simulating existing file without manifest)
        target_path = provider._factors_dir / "factors_3_daily.parquet"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        mock_ff3_data.write_parquet(target_path)

        # No manifest exists at this point
        assert provider.get_manifest() is None

        # Mock pandas-datareader to avoid actual network calls
        with patch("pandas_datareader.data") as mock_pdr:
            # Setup mock - sync should skip existing file
            mock_pdr.DataReader = MagicMock()

            # Sync with specific datasets (not forcing)
            result = provider.sync_data(
                datasets=["factors_3_daily"],
                force=False,
            )

        # Manifest should now have entry for the existing file
        manifest = provider.get_manifest()
        assert manifest is not None
        assert "factors_3_daily.parquet" in manifest["files"]

        # Verify entry has all required fields
        entry = manifest["files"]["factors_3_daily.parquet"]
        assert "checksum" in entry
        assert "row_count" in entry
        assert entry["row_count"] == mock_ff3_data.height
        assert "start_date" in entry
        assert "end_date" in entry

        # Total row count should include regenerated entry
        assert result["total_row_count"] == mock_ff3_data.height


# =============================================================================
# Path Traversal Prevention Tests
# =============================================================================


class TestPathTraversalPrevention:
    """Tests for path traversal attack prevention."""

    def test_path_traversal_rejected(self, tmp_path: Path) -> None:
        """Test path traversal attempts are rejected."""
        with pytest.raises(ValueError, match="Path traversal detected"):
            FamaFrenchLocalProvider(
                storage_path=tmp_path / ".." / "etc" / "passwd",
            )

    def test_valid_path_accepted(self, tmp_path: Path) -> None:
        """Test valid paths are accepted."""
        storage_path = tmp_path / "fama_french"
        provider = FamaFrenchLocalProvider(storage_path=storage_path)
        assert provider._storage_path == storage_path.resolve()


# =============================================================================
# Sync Tests (Mocked)
# =============================================================================


class TestSyncData:
    """Tests for sync_data() method (mocked network calls)."""

    def test_sync_requires_pandas_datareader(self, provider: FamaFrenchLocalProvider) -> None:
        """Test sync raises error if pandas-datareader not available."""
        import builtins

        original_import = builtins.__import__

        def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "pandas_datareader.data" or name == "pandas_datareader":
                raise ImportError("No module named 'pandas_datareader'")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=mock_import):
            with pytest.raises(FamaFrenchSyncError, match="pandas-datareader"):
                provider.sync_data()

    def test_sync_invalid_datasets_raises_error(self, provider: FamaFrenchLocalProvider) -> None:
        """Test sync with invalid dataset names raises error."""
        with patch("pandas_datareader.data.DataReader"):
            with pytest.raises(ValueError, match="No valid datasets"):
                provider.sync_data(datasets=["invalid_dataset"])

    def test_sync_skip_existing_without_force(
        self, provider_with_data: FamaFrenchLocalProvider
    ) -> None:
        """Test sync skips existing files without force flag."""
        # Mock DataReader to track calls
        with patch("pandas_datareader.data.DataReader") as mock_reader:
            # Sync without force - should skip existing files
            provider_with_data.sync_data(
                datasets=["factors_3_daily"],
                force=False,
            )

            # DataReader should not be called since file exists
            mock_reader.assert_not_called()

    def test_sync_preserves_existing_manifest_entries(
        self, provider: FamaFrenchLocalProvider, mock_ff3_data: pl.DataFrame
    ) -> None:
        """Test sync preserves manifest entries for skipped datasets (critical fix)."""
        # Setup: Create file and manifest with existing entry
        factors_dir = provider._factors_dir
        factors_dir.mkdir(parents=True, exist_ok=True)
        mock_ff3_data.write_parquet(factors_dir / "factors_3_daily.parquet")

        # Write initial manifest with entry
        initial_manifest = {
            "dataset": "fama_french",
            "files": {
                "factors_3_daily.parquet": {
                    "checksum": "initial_checksum_abc123",
                    "row_count": 100,
                    "start_date": "2024-01-01",
                    "end_date": "2024-12-31",
                }
            },
            "total_row_count": 100,
        }
        provider._atomic_write_manifest(initial_manifest)

        # Sync without force - should skip existing and PRESERVE manifest entry
        with patch("pandas_datareader.data.DataReader"):
            result = provider.sync_data(datasets=["factors_3_daily"], force=False)

        # Verify existing entry preserved (not overwritten/removed)
        assert "factors_3_daily.parquet" in result["files"]
        assert result["files"]["factors_3_daily.parquet"]["checksum"] == "initial_checksum_abc123"
        assert result["files"]["factors_3_daily.parquet"]["row_count"] == 100

    def test_sync_reports_failed_datasets(self, provider: FamaFrenchLocalProvider) -> None:
        """Test sync reports failed datasets in result."""
        # Mock DataReader to return None (simulating failure)
        with patch("pandas_datareader.data.DataReader", return_value=None):
            with patch.object(provider, "_download_with_retry", return_value=None):
                result = provider.sync_data(datasets=["factors_3_daily"])

        # Verify failed datasets reported
        assert "failed_datasets" in result
        assert "factors_3_daily" in result["failed_datasets"]


# =============================================================================
# 6-Factor Materialization Tests
# =============================================================================


class TestFF6Materialization:
    """Tests for 6-factor file materialization."""

    def test_ff6_daily_materialized(self, provider_with_data: FamaFrenchLocalProvider) -> None:
        """Test 6-factor daily stored as materialized Parquet."""
        ff6_path = provider_with_data._factors_dir / "factors_6_daily.parquet"
        assert ff6_path.exists()

        df = pl.read_parquet(ff6_path)
        assert "umd" in df.columns
        assert "rmw" in df.columns
        assert "cma" in df.columns

    def test_ff6_monthly_materialized(self, provider_with_data: FamaFrenchLocalProvider) -> None:
        """Test 6-factor monthly stored as materialized Parquet."""
        ff6_path = provider_with_data._factors_dir / "factors_6_monthly.parquet"
        assert ff6_path.exists()

        df = pl.read_parquet(ff6_path)
        assert "umd" in df.columns

    def test_ff6_has_correct_columns(self, provider_with_data: FamaFrenchLocalProvider) -> None:
        """Test 6-factor files have correct column order."""
        df = provider_with_data.get_factors(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            model="ff6",
            frequency="daily",
        )

        # RF should be last (after UMD)
        cols = list(df.columns)
        assert cols[-1] == "rf"
        assert "umd" in cols
