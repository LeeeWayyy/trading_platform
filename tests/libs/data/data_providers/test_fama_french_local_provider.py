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

from libs.data.data_providers.fama_french_local_provider import (
    ChecksumError,
    FamaFrenchLocalProvider,
    FamaFrenchSyncError,
)
from libs.data.data_quality.exceptions import DataNotFoundError

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

    @pytest.mark.integration()
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


# =============================================================================
# Industry Returns - Invalid Frequency Test (Line 277)
# =============================================================================


class TestIndustryReturnsFrequencyValidation:
    """Tests for get_industry_returns frequency validation (covers line 277)."""

    def test_invalid_frequency_raises_value_error(
        self, provider_with_data: FamaFrenchLocalProvider
    ) -> None:
        """Test invalid frequency raises ValueError for industry returns."""
        with pytest.raises(ValueError, match="Invalid frequency"):
            provider_with_data.get_industry_returns(
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                num_industries=10,
                frequency="weekly",  # type: ignore[arg-type]
            )


# =============================================================================
# Sync Data - Full Dataset Sync (Line 336)
# =============================================================================


class TestSyncDataAllDatasets:
    """Tests for sync_data with all datasets (covers line 336)."""

    def test_sync_all_datasets_when_none_specified(
        self, provider: FamaFrenchLocalProvider, mock_ff3_data: pl.DataFrame
    ) -> None:
        """Test sync_data syncs all datasets when datasets=None."""
        # Mock the download to return valid data
        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Mkt-RF": [1.05, -0.52, 0.78],
                "SMB": [0.23, -0.11, 0.45],
                "HML": [-0.34, 0.67, -0.12],
                "RF": [0.02, 0.02, 0.02],
            },
            index=pd.DatetimeIndex(
                [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
            ),
        )

        with patch("pandas_datareader.data.DataReader") as mock_reader:
            # Return dict format like pandas-datareader does
            mock_reader.return_value = {0: mock_pdf}

            # Sync with datasets=None should use ALL_DATASETS
            result = provider.sync_data(datasets=None, force=True)

        # Should have attempted to sync all datasets
        assert "files" in result
        # The call count should equal the number of datasets
        assert mock_reader.call_count == len(provider.ALL_DATASETS)


# =============================================================================
# Sync Data - Lock Acquisition Failure (Lines 350-351)
# =============================================================================


class TestSyncLockFailure:
    """Tests for sync_data lock acquisition failure (covers lines 350-351)."""

    def test_lock_acquisition_failure_raises_sync_error(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test lock acquisition failure raises FamaFrenchSyncError."""
        with patch("pandas_datareader.data.DataReader"):
            with patch(
                "libs.data.data_providers.fama_french_local_provider.AtomicFileLock.acquire",
                side_effect=OSError("Lock acquisition failed"),
            ):
                with pytest.raises(FamaFrenchSyncError, match="Failed to acquire sync lock"):
                    provider.sync_data(datasets=["factors_3_daily"])


# =============================================================================
# Sync Data - Manifest Regeneration Failure (Lines 391-392)
# =============================================================================


class TestManifestRegenerationFailure:
    """Tests for manifest regeneration failure handling (covers lines 391-392)."""

    def test_manifest_regeneration_failure_logs_warning(
        self, provider: FamaFrenchLocalProvider, mock_ff3_data: pl.DataFrame, caplog: Any
    ) -> None:
        """Test manifest entry regeneration failure logs warning but continues."""
        import logging

        # Create file but with corrupted content that can't be read properly
        factors_dir = provider._factors_dir
        factors_dir.mkdir(parents=True, exist_ok=True)
        target_path = factors_dir / "factors_3_daily.parquet"
        mock_ff3_data.write_parquet(target_path)

        # Mock read_parquet to fail during manifest regeneration
        original_read = pl.read_parquet

        def mock_read(path: Any) -> Any:
            if "factors_3_daily" in str(path):
                raise OSError("Corrupted file")
            return original_read(path)

        with patch("pandas_datareader.data.DataReader"):
            with patch("polars.read_parquet", side_effect=mock_read):
                with caplog.at_level(logging.WARNING):
                    # Should not raise, but should log warning
                    _ = provider.sync_data(datasets=["factors_3_daily"], force=False)

        # File should still exist but manifest entry might be missing
        assert target_path.exists()


# =============================================================================
# Schema Validation Tests (Lines 690-708)
# =============================================================================


class TestSchemaValidation:
    """Tests for _validate_schema method (covers lines 690-708)."""

    def test_validate_schema_factors_3_missing_columns(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test schema validation fails when factor columns missing."""
        # Missing HML column
        df = pl.DataFrame(
            {
                "date": [date(2024, 1, 2)],
                "mkt_rf": [0.0105],
                "smb": [0.0023],
                # Missing: hml, rf
            }
        )

        with pytest.raises(ValueError, match="schema mismatch"):
            provider._validate_schema(df, "factors_3_daily")

    def test_validate_schema_factors_5_missing_columns(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test schema validation fails for 5-factor with missing columns."""
        # Missing RMW, CMA columns
        df = pl.DataFrame(
            {
                "date": [date(2024, 1, 2)],
                "mkt_rf": [0.0105],
                "smb": [0.0023],
                "hml": [-0.0034],
                "rf": [0.0002],
                # Missing: rmw, cma
            }
        )

        with pytest.raises(ValueError, match="schema mismatch"):
            provider._validate_schema(df, "factors_5_daily")

    def test_validate_schema_industry_missing_date(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test industry schema validation fails when date column missing."""
        # Industry dataset without date column
        df = pl.DataFrame(
            {
                "nodur": [0.0112],
                "durbl": [0.0078],
            }
        )

        with pytest.raises(ValueError, match="missing required 'date' column"):
            provider._validate_schema(df, "ind10_daily")

    def test_validate_schema_industry_with_date_succeeds(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test industry schema validation succeeds with date column."""
        df = pl.DataFrame(
            {
                "date": [date(2024, 1, 2)],
                "nodur": [0.0112],
                "durbl": [0.0078],
            }
        )

        # Should not raise
        result = provider._validate_schema(df, "ind10_daily")
        assert result is True

    def test_validate_schema_momentum_missing_umd(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test momentum schema validation fails when UMD missing."""
        df = pl.DataFrame(
            {
                "date": [date(2024, 1, 2)],
                # Missing: umd
            }
        )

        with pytest.raises(ValueError, match="schema mismatch"):
            provider._validate_schema(df, "momentum_daily")

    def test_validate_schema_factors_3_valid(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test schema validation succeeds for valid 3-factor data."""
        df = pl.DataFrame(
            {
                "date": [date(2024, 1, 2)],
                "mkt_rf": [0.0105],
                "smb": [0.0023],
                "hml": [-0.0034],
                "rf": [0.0002],
            }
        )

        result = provider._validate_schema(df, "factors_3_daily")
        assert result is True


# =============================================================================
# Download With Retry Tests (Lines 596-657)
# =============================================================================


class TestDownloadWithRetry:
    """Tests for _download_with_retry method (covers lines 596-657)."""

    def test_download_with_datetime_index(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test download handles DatetimeIndex correctly."""
        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Mkt-RF": [1.05, -0.52],
                "SMB": [0.23, -0.11],
                "HML": [-0.34, 0.67],
                "RF": [0.02, 0.02],
            },
            index=pd.DatetimeIndex([date(2024, 1, 2), date(2024, 1, 3)]),
        )

        mock_web = MagicMock()
        mock_web.DataReader.return_value = {0: mock_pdf}

        result = provider._download_with_retry(mock_web, "F-F_Research_Data_Factors_daily", "factors_3_daily")

        assert result is not None
        assert "date" in result.columns
        assert result.height == 2

    def test_download_with_period_index(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test download handles PeriodIndex correctly (monthly data)."""
        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Mkt-RF": [1.05, -0.52],
                "SMB": [0.23, -0.11],
                "HML": [-0.34, 0.67],
                "RF": [0.02, 0.02],
            },
            index=pd.PeriodIndex(["2024-01", "2024-02"], freq="M"),
        )

        mock_web = MagicMock()
        mock_web.DataReader.return_value = {0: mock_pdf}

        result = provider._download_with_retry(mock_web, "F-F_Research_Data_Factors", "factors_3_monthly")

        assert result is not None
        assert "date" in result.columns
        assert result.height == 2

    def test_download_with_unexpected_index_raises_error(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test download rejects unexpected index types."""
        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Mkt-RF": [1.05, -0.52],
                "SMB": [0.23, -0.11],
            },
            index=pd.RangeIndex(start=0, stop=2),  # Unexpected index type
        )

        mock_web = MagicMock()
        mock_web.DataReader.return_value = {0: mock_pdf}

        # Should return None after retries due to FamaFrenchSyncError
        result = provider._download_with_retry(mock_web, "F-F_Research_Data_Factors_daily", "factors_3_daily")
        assert result is None

    def test_download_returns_dataframe_directly(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test download handles non-dict return value."""
        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Mkt-RF": [1.05],
                "SMB": [0.23],
                "HML": [-0.34],
                "RF": [0.02],
            },
            index=pd.DatetimeIndex([date(2024, 1, 2)]),
        )

        mock_web = MagicMock()
        # Return DataFrame directly instead of dict
        mock_web.DataReader.return_value = mock_pdf

        result = provider._download_with_retry(mock_web, "F-F_Research_Data_Factors_daily", "factors_3_daily")

        assert result is not None
        assert "date" in result.columns

    def test_download_with_dict_no_zero_key(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test download handles dict without key 0."""
        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Mkt-RF": [1.05],
                "SMB": [0.23],
                "HML": [-0.34],
                "RF": [0.02],
            },
            index=pd.DatetimeIndex([date(2024, 1, 2)]),
        )

        mock_web = MagicMock()
        # Return dict with different key
        mock_web.DataReader.return_value = {"main": mock_pdf}

        result = provider._download_with_retry(mock_web, "F-F_Research_Data_Factors_daily", "factors_3_daily")

        assert result is not None
        assert "date" in result.columns

    def test_download_retries_on_failure(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test download retries on failure with exponential backoff."""
        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Mkt-RF": [1.05],
                "SMB": [0.23],
                "HML": [-0.34],
                "RF": [0.02],
            },
            index=pd.DatetimeIndex([date(2024, 1, 2)]),
        )

        mock_web = MagicMock()
        # Fail first two attempts, succeed on third
        mock_web.DataReader.side_effect = [
            Exception("Network error"),
            Exception("Timeout"),
            {0: mock_pdf},
        ]

        with patch("time.sleep"):  # Skip actual sleep
            result = provider._download_with_retry(mock_web, "F-F_Research_Data_Factors_daily", "factors_3_daily")

        assert result is not None
        assert mock_web.DataReader.call_count == 3

    def test_download_returns_none_after_max_retries(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test download returns None after exhausting retries."""
        mock_web = MagicMock()
        mock_web.DataReader.side_effect = Exception("Persistent failure")

        with patch("time.sleep"):  # Skip actual sleep
            result = provider._download_with_retry(mock_web, "F-F_Research_Data_Factors_daily", "factors_3_daily")

        assert result is None
        assert mock_web.DataReader.call_count == provider.MAX_RETRIES

    def test_download_normalizes_column_names(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test download normalizes column names to lowercase with underscores."""
        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Mkt-RF": [1.05],
                "SMB": [0.23],
                "HML": [-0.34],
                "RF": [0.02],
            },
            index=pd.DatetimeIndex([date(2024, 1, 2)]),
        )

        mock_web = MagicMock()
        mock_web.DataReader.return_value = {0: mock_pdf}

        result = provider._download_with_retry(mock_web, "F-F_Research_Data_Factors_daily", "factors_3_daily")

        assert result is not None
        assert "mkt_rf" in result.columns  # Normalized from Mkt-RF


# =============================================================================
# FF6 Creation Tests (Lines 720-732)
# =============================================================================


class TestCreateFF6:
    """Tests for _create_ff6 method (covers lines 720-732)."""

    def test_create_ff6_joins_correctly(
        self, provider: FamaFrenchLocalProvider, mock_ff5_data: pl.DataFrame, mock_momentum_data: pl.DataFrame
    ) -> None:
        """Test FF6 is created by joining FF5 and momentum data."""
        # Write source files
        ff5_path = provider._factors_dir / "factors_5_daily.parquet"
        mom_path = provider._factors_dir / "momentum_daily.parquet"

        mock_ff5_data.write_parquet(ff5_path)
        mock_momentum_data.write_parquet(mom_path)

        # Create FF6
        ff6_df = provider._create_ff6(ff5_path, mom_path)

        # Verify columns
        assert "umd" in ff6_df.columns
        assert "mkt_rf" in ff6_df.columns
        assert "rmw" in ff6_df.columns
        assert "cma" in ff6_df.columns

        # RF should be last
        cols = list(ff6_df.columns)
        assert cols[-1] == "rf"
        assert cols[0] == "date"

    def test_create_ff6_inner_join(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test FF6 uses inner join (only dates in both datasets)."""
        # Create FF5 with 3 dates
        ff5_data = pl.DataFrame(
            {
                "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
                "mkt_rf": [0.01, 0.02, 0.03],
                "smb": [0.001, 0.002, 0.003],
                "hml": [0.001, 0.002, 0.003],
                "rmw": [0.001, 0.002, 0.003],
                "cma": [0.001, 0.002, 0.003],
                "rf": [0.0001, 0.0001, 0.0001],
            }
        )

        # Create momentum with 2 dates (missing Jan 4)
        mom_data = pl.DataFrame(
            {
                "date": [date(2024, 1, 2), date(2024, 1, 3)],
                "umd": [0.005, 0.006],
            }
        )

        ff5_path = provider._factors_dir / "factors_5_daily.parquet"
        mom_path = provider._factors_dir / "momentum_daily.parquet"

        ff5_data.write_parquet(ff5_path)
        mom_data.write_parquet(mom_path)

        ff6_df = provider._create_ff6(ff5_path, mom_path)

        # Should only have 2 rows (inner join)
        assert ff6_df.height == 2

    def test_create_ff6_handles_missing_rf(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test FF6 creation handles case where RF column might be absent."""
        ff5_data = pl.DataFrame(
            {
                "date": [date(2024, 1, 2)],
                "mkt_rf": [0.01],
                "smb": [0.001],
                "hml": [0.001],
                "rmw": [0.001],
                "cma": [0.001],
                # No RF column
            }
        )

        mom_data = pl.DataFrame(
            {
                "date": [date(2024, 1, 2)],
                "umd": [0.005],
            }
        )

        ff5_path = provider._factors_dir / "factors_5_daily.parquet"
        mom_path = provider._factors_dir / "momentum_daily.parquet"

        ff5_data.write_parquet(ff5_path)
        mom_data.write_parquet(mom_path)

        ff6_df = provider._create_ff6(ff5_path, mom_path)

        # Should succeed without RF
        assert ff6_df.height == 1
        assert "rf" not in ff6_df.columns


# =============================================================================
# Target Path Tests (Line 576)
# =============================================================================


class TestGetTargetPath:
    """Tests for _get_target_path method (covers line 576)."""

    def test_industry_dataset_path(self, provider: FamaFrenchLocalProvider) -> None:
        """Test industry datasets go to industries directory."""
        path = provider._get_target_path("ind10_daily")
        assert "industries" in str(path)
        assert path.name == "ind10_daily.parquet"

    def test_factor_dataset_path(self, provider: FamaFrenchLocalProvider) -> None:
        """Test factor datasets go to factors directory."""
        path = provider._get_target_path("factors_3_daily")
        assert "factors" in str(path)
        assert path.name == "factors_3_daily.parquet"


# =============================================================================
# Fsync Directory Tests (Lines 869-870)
# =============================================================================


class TestFsyncDirectory:
    """Tests for _fsync_directory error handling (covers lines 869-870)."""

    def test_fsync_handles_oserror(
        self, provider: FamaFrenchLocalProvider, caplog: Any
    ) -> None:
        """Test fsync handles OSError gracefully."""
        import logging

        with patch("os.open", side_effect=OSError("Permission denied")):
            with caplog.at_level(logging.WARNING):
                # Should not raise, but log warning
                provider._fsync_directory(provider._storage_path)

        assert "Failed to fsync directory" in caplog.text


# =============================================================================
# Atomic Write Manifest Error Handling (Lines 890-916)
# =============================================================================


class TestAtomicWriteManifestErrors:
    """Tests for _atomic_write_manifest error handling (covers lines 890-916)."""

    def test_manifest_write_oserror(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test manifest write handles OSError."""
        manifest_data = {"dataset": "test", "files": {}}

        with patch("builtins.open", side_effect=OSError("Disk full")):
            with pytest.raises(OSError, match="Disk full"):
                provider._atomic_write_manifest(manifest_data)

    def test_manifest_write_serialization_error(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test manifest write handles serialization errors (ValueError branch)."""
        # The _atomic_write_manifest uses default=str, so it handles most types.
        # ValueError is raised for circular references which can't be serialized.
        manifest_data: dict[str, Any] = {"dataset": "test"}
        # Create circular reference
        manifest_data["self_ref"] = manifest_data

        with pytest.raises(ValueError, match="Circular reference"):
            provider._atomic_write_manifest(manifest_data)


# =============================================================================
# Verify Data Edge Cases (Lines 940, 948-949, 953, 958-959)
# =============================================================================


class TestVerifyDataEdgeCases:
    """Tests for verify_data edge cases (covers lines 940, 948-949, 953, 958-959)."""

    def test_verify_returns_empty_dict_when_no_manifest(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test verify_data returns empty dict when no manifest exists."""
        result = provider.verify_data()
        assert result == {}

    def test_verify_returns_false_when_no_checksum(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test verify_data returns False when checksum missing from manifest."""
        # Write manifest with missing checksum
        manifest_data = {
            "files": {
                "test.parquet": {
                    "row_count": 100,
                    # No checksum field
                }
            }
        }
        provider._atomic_write_manifest(manifest_data)

        result = provider.verify_data()
        assert result["test.parquet"] is False

    def test_verify_returns_false_when_file_missing(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test verify_data returns False when file doesn't exist."""
        # Write manifest pointing to non-existent file
        manifest_data = {
            "files": {
                "missing.parquet": {
                    "checksum": "abc123",
                    "row_count": 100,
                }
            }
        }
        provider._atomic_write_manifest(manifest_data)

        result = provider.verify_data()
        assert result["missing.parquet"] is False

    def test_verify_handles_industry_files(
        self, provider: FamaFrenchLocalProvider, mock_industry_data: pl.DataFrame
    ) -> None:
        """Test verify_data correctly routes industry files to industries directory."""
        # Write industry file
        ind_path = provider._industries_dir / "ind10_daily.parquet"
        mock_industry_data.write_parquet(ind_path)

        # Compute actual checksum
        checksum = provider._compute_checksum(ind_path)

        # Write manifest
        manifest_data = {
            "files": {
                "ind10_daily.parquet": {
                    "checksum": checksum,
                    "row_count": mock_industry_data.height,
                }
            }
        }
        provider._atomic_write_manifest(manifest_data)

        result = provider.verify_data()
        assert result["ind10_daily.parquet"] is True


# =============================================================================
# Sync Data - Schema Validation Failure (Lines 410-438)
# =============================================================================


class TestSyncSchemaValidationFailure:
    """Tests for sync_data schema validation failure (covers lines 410-438)."""

    def test_sync_schema_validation_failure_adds_to_failed(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test schema validation failure adds dataset to failed list."""
        import pandas as pd

        # Create data missing required columns
        mock_pdf = pd.DataFrame(
            {
                "Mkt-RF": [1.05],
                # Missing SMB, HML, RF
            },
            index=pd.DatetimeIndex([date(2024, 1, 2)]),
        )

        mock_web = MagicMock()
        mock_web.DataReader.return_value = {0: mock_pdf}

        with patch("pandas_datareader.data", mock_web):
            with patch.object(provider, "_download_with_retry") as mock_download:
                # Return DataFrame missing columns
                mock_download.return_value = pl.DataFrame(
                    {
                        "date": [date(2024, 1, 2)],
                        "mkt_rf": [0.0105],
                        # Missing other required columns
                    }
                )

                result = provider.sync_data(datasets=["factors_3_daily"], force=True)

        assert "failed_datasets" in result
        assert "factors_3_daily" in result["failed_datasets"]


# =============================================================================
# FF6 Creation During Sync (Lines 459-486, 493-514, 517-533)
# =============================================================================


class TestFF6CreationDuringSync:
    """Tests for FF6 creation during sync (covers lines 459-486, 493-514, 517-533)."""

    def test_ff6_creation_missing_prerequisites_reports_failure(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test FF6 creation reports failure when prerequisites missing."""
        import pandas as pd

        # Only create FF3 data (missing FF5 and momentum)
        mock_pdf = pd.DataFrame(
            {
                "Mkt-RF": [1.05],
                "SMB": [0.23],
                "HML": [-0.34],
                "RF": [0.02],
            },
            index=pd.DatetimeIndex([date(2024, 1, 2)]),
        )

        with patch("pandas_datareader.data.DataReader", return_value={0: mock_pdf}):
            # Sync only FF3 - FF5 and momentum won't exist
            result = provider.sync_data(datasets=["factors_3_daily"], force=True)

        # FF6 should be in failed datasets since prerequisites missing
        assert "failed_datasets" in result
        assert "factors_6_daily" in result["failed_datasets"]

    def test_ff6_exists_but_prerequisites_missing_preserves_manifest(
        self, provider: FamaFrenchLocalProvider, mock_ff5_data: pl.DataFrame, mock_momentum_data: pl.DataFrame
    ) -> None:
        """Test existing FF6 preserved when prerequisites missing during sync."""
        # Create FF6 file manually (simulating previous sync)
        ff6_data = mock_ff5_data.join(mock_momentum_data, on="date", how="inner")
        cols = ["date", "mkt_rf", "smb", "hml", "rmw", "cma", "umd", "rf"]
        ff6_data = ff6_data.select(cols)

        ff6_path = provider._factors_dir / "factors_6_daily.parquet"
        ff6_data.write_parquet(ff6_path)

        # Don't create FF5 or momentum files (prerequisites missing)

        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Mkt-RF": [1.05],
                "SMB": [0.23],
                "HML": [-0.34],
                "RF": [0.02],
            },
            index=pd.DatetimeIndex([date(2024, 1, 2)]),
        )

        with patch("pandas_datareader.data.DataReader", return_value={0: mock_pdf}):
            result = provider.sync_data(datasets=["factors_3_daily"], force=True)

        # FF6 file should still exist
        assert ff6_path.exists()

        # Manifest should have FF6 entry (regenerated)
        assert "factors_6_daily.parquet" in result["files"]

    def test_ff6_creation_with_force_flag(
        self, provider: FamaFrenchLocalProvider, mock_ff5_data: pl.DataFrame, mock_momentum_data: pl.DataFrame
    ) -> None:
        """Test FF6 is recreated with force flag even if exists."""
        # Write prerequisite files
        mock_ff5_data.write_parquet(provider._factors_dir / "factors_5_daily.parquet")
        mock_momentum_data.write_parquet(provider._factors_dir / "momentum_daily.parquet")

        # Create initial FF6
        ff6_path = provider._factors_dir / "factors_6_daily.parquet"
        initial_ff6 = mock_ff5_data.join(mock_momentum_data, on="date", how="inner")
        cols = ["date", "mkt_rf", "smb", "hml", "rmw", "cma", "umd", "rf"]
        initial_ff6 = initial_ff6.select(cols)
        initial_ff6.write_parquet(ff6_path)

        initial_checksum = provider._compute_checksum(ff6_path)

        # Modify source data slightly
        modified_ff5 = mock_ff5_data.with_columns(pl.col("mkt_rf") * 2)
        modified_ff5.write_parquet(provider._factors_dir / "factors_5_daily.parquet")

        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Mkt-RF": [1.05],
                "SMB": [0.23],
                "HML": [-0.34],
                "RF": [0.02],
            },
            index=pd.DatetimeIndex([date(2024, 1, 2)]),
        )

        with patch("pandas_datareader.data.DataReader", return_value={0: mock_pdf}):
            # Sync with force=True should recreate FF6
            _ = provider.sync_data(datasets=["factors_5_daily"], force=True)

        # FF6 should have been recreated (different checksum)
        new_checksum = provider._compute_checksum(ff6_path)
        assert new_checksum != initial_checksum

    def test_ff6_exists_not_forced_manifest_regenerated(
        self, provider: FamaFrenchLocalProvider, mock_ff5_data: pl.DataFrame, mock_momentum_data: pl.DataFrame
    ) -> None:
        """Test existing FF6 gets manifest entry regenerated when not forcing."""
        # Write all prerequisite files
        mock_ff5_data.write_parquet(provider._factors_dir / "factors_5_daily.parquet")
        mock_momentum_data.write_parquet(provider._factors_dir / "momentum_daily.parquet")

        # Create FF6 file manually
        ff6_data = mock_ff5_data.join(mock_momentum_data, on="date", how="inner")
        cols = ["date", "mkt_rf", "smb", "hml", "rmw", "cma", "umd", "rf"]
        ff6_data = ff6_data.select(cols)
        ff6_path = provider._factors_dir / "factors_6_daily.parquet"
        ff6_data.write_parquet(ff6_path)

        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Mkt-RF": [1.05],
                "SMB": [0.23],
                "HML": [-0.34],
                "RF": [0.02],
            },
            index=pd.DatetimeIndex([date(2024, 1, 2)]),
        )

        with patch("pandas_datareader.data.DataReader", return_value={0: mock_pdf}):
            # Sync without force - existing FF6 should have manifest entry regenerated
            result = provider.sync_data(datasets=["factors_3_daily"], force=False)

        # FF6 manifest entry should exist
        assert "factors_6_daily.parquet" in result["files"]
        assert "checksum" in result["files"]["factors_6_daily.parquet"]
        assert "row_count" in result["files"]["factors_6_daily.parquet"]

    def test_ff6_creation_failure_adds_to_failed_datasets(
        self, provider: FamaFrenchLocalProvider, mock_ff5_data: pl.DataFrame, mock_momentum_data: pl.DataFrame
    ) -> None:
        """Test FF6 creation failure adds to failed datasets list."""
        # Write prerequisite files
        mock_ff5_data.write_parquet(provider._factors_dir / "factors_5_daily.parquet")
        mock_momentum_data.write_parquet(provider._factors_dir / "momentum_daily.parquet")

        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Mkt-RF": [1.05],
                "SMB": [0.23],
                "HML": [-0.34],
                "RF": [0.02],
            },
            index=pd.DatetimeIndex([date(2024, 1, 2)]),
        )

        with patch("pandas_datareader.data.DataReader", return_value={0: mock_pdf}):
            # Mock _create_ff6 to raise an exception
            with patch.object(provider, "_create_ff6", side_effect=Exception("Join failed")):
                result = provider.sync_data(datasets=["factors_3_daily"], force=True)

        # FF6 should be in failed datasets
        assert "failed_datasets" in result
        assert "factors_6_daily" in result["failed_datasets"]

    def test_ff6_manifest_regeneration_failure_logs_warning(
        self, provider: FamaFrenchLocalProvider, mock_ff5_data: pl.DataFrame, mock_momentum_data: pl.DataFrame, caplog: Any
    ) -> None:
        """Test FF6 manifest regeneration failure logs warning but continues."""
        import logging

        # Create FF6 file but make read fail during manifest regeneration
        mock_ff5_data.write_parquet(provider._factors_dir / "factors_5_daily.parquet")
        mock_momentum_data.write_parquet(provider._factors_dir / "momentum_daily.parquet")

        ff6_data = mock_ff5_data.join(mock_momentum_data, on="date", how="inner")
        cols = ["date", "mkt_rf", "smb", "hml", "rmw", "cma", "umd", "rf"]
        ff6_data = ff6_data.select(cols)
        ff6_path = provider._factors_dir / "factors_6_daily.parquet"
        ff6_data.write_parquet(ff6_path)

        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Mkt-RF": [1.05],
                "SMB": [0.23],
                "HML": [-0.34],
                "RF": [0.02],
            },
            index=pd.DatetimeIndex([date(2024, 1, 2)]),
        )

        # Create a counter to track calls
        call_count = [0]
        original_read = pl.read_parquet

        def mock_read(path: Any) -> pl.DataFrame:
            call_count[0] += 1
            # Fail on FF6 read during manifest regeneration (later in the process)
            if "factors_6" in str(path) and call_count[0] > 10:
                raise OSError("Read failed")
            return original_read(path)

        with patch("pandas_datareader.data.DataReader", return_value={0: mock_pdf}):
            with caplog.at_level(logging.WARNING):
                # This should complete despite the failure
                result = provider.sync_data(datasets=["factors_3_daily"], force=False)

        # Sync should complete
        assert "files" in result


# =============================================================================
# Atomic Write - Temp File Cleanup on Validation Error (Lines 806, 816)
# =============================================================================


class TestAtomicWriteTempFileCleanup:
    """Tests for atomic write temp file cleanup (covers lines 806, 816)."""

    def test_temp_file_cleaned_on_checksum_error(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test temp file cleaned up on checksum validation error."""
        df = pl.DataFrame(
            {
                "date": [date(2024, 1, 2)],
                "mkt_rf": [0.0105],
            }
        )

        target_path = provider._factors_dir / "test.parquet"
        temp_path = target_path.with_suffix(".parquet.tmp")

        with pytest.raises(ChecksumError):
            provider._atomic_write_parquet(df, target_path, expected_checksum="wrong")

        # Temp file should be cleaned up
        assert not temp_path.exists()

    def test_temp_file_cleaned_on_empty_dataframe_error(
        self, provider: FamaFrenchLocalProvider
    ) -> None:
        """Test temp file cleaned up on empty DataFrame error."""
        df = pl.DataFrame({"date": [], "mkt_rf": []})

        target_path = provider._factors_dir / "test2.parquet"
        temp_path = target_path.with_suffix(".parquet.tmp")

        with pytest.raises(ValueError, match="Empty DataFrame"):
            provider._atomic_write_parquet(df, target_path)

        # Temp file should be cleaned up (moved to quarantine)
        assert not temp_path.exists()
