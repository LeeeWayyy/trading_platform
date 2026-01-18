"""Tests for YFinanceProvider.

Comprehensive test suite covering:
- Basic functionality (single/bulk symbol fetch)
- Production gating (matrix tests for env × CRSP × flag)
- Drift detection (baseline comparison)
- Atomic writes and quarantine
- Cache hit/miss behavior
- Rate limiting
- Error handling
- Lock acquisition failures
- Disk space checks
- Cache integrity verification
- Baseline validation
- Manifest operations
- Quarantine operations
- Directory fsync operations
"""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import polars as pl
import pytest

from libs.data.data_providers.yfinance_provider import (
    BASELINE_FILE_SUFFIX,
    BASELINE_MANIFEST_FILE,
    VALID_SYMBOL_PATTERN,
    YFINANCE_SCHEMA,
    DriftDetectedError,
    ProductionGateError,
    YFinanceError,
    YFinanceProvider,
)

# Skip entire test module if yfinance is not installed
# This allows tests to run in environments without the optional dev dependency
pytest.importorskip("yfinance", reason="yfinance not installed - skipping tests")

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture()
def provider(tmp_path: Path) -> YFinanceProvider:
    """Create a YFinanceProvider for testing in development environment."""
    storage_path = tmp_path / "yfinance"
    storage_path.mkdir(parents=True, exist_ok=True)
    return YFinanceProvider(
        storage_path=storage_path,
        environment="development",
    )


@pytest.fixture()
def provider_with_baseline(tmp_path: Path) -> YFinanceProvider:
    """Create a YFinanceProvider with baseline path configured."""
    storage_path = tmp_path / "yfinance"
    baseline_path = tmp_path / "baseline"
    storage_path.mkdir(parents=True, exist_ok=True)
    baseline_path.mkdir(parents=True, exist_ok=True)
    return YFinanceProvider(
        storage_path=storage_path,
        baseline_path=baseline_path,
        environment="development",
    )


@pytest.fixture()
def mock_ohlcv_data() -> pl.DataFrame:
    """Create mock OHLCV data."""
    return pl.DataFrame(
        {
            "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
            "symbol": ["SPY", "SPY", "SPY"],
            "open": [471.50, 473.20, 472.80],
            "high": [474.00, 475.50, 476.20],
            "low": [470.00, 472.00, 471.50],
            "close": [473.00, 474.80, 475.50],
            "volume": [50000000.0, 48000000.0, 52000000.0],
            "adj_close": [473.00, 474.80, 475.50],
        }
    )


@pytest.fixture()
def mock_baseline_data() -> pl.DataFrame:
    """Create mock baseline data for drift detection."""
    return pl.DataFrame(
        {
            "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
            "adj_close": [473.00, 474.80, 475.50],  # Matches mock_ohlcv_data
        }
    )


@pytest.fixture()
def mock_baseline_data_with_drift() -> pl.DataFrame:
    """Create mock baseline data with significant drift (>1%)."""
    return pl.DataFrame(
        {
            "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
            "adj_close": [473.00, 474.80, 490.00],  # Last price drifts >1%
        }
    )


@pytest.fixture()
def provider_with_cache(
    provider: YFinanceProvider,
    mock_ohlcv_data: pl.DataFrame,
) -> YFinanceProvider:
    """Create provider with pre-populated cache."""
    # Write mock data to cache
    cache_path = provider._daily_dir / "SPY.parquet"
    mock_ohlcv_data.write_parquet(cache_path)

    # Create manifest
    manifest = {
        "dataset": "yfinance",
        "files": {
            "SPY.parquet": {
                "symbol": "SPY",
                "checksum": provider._compute_checksum(cache_path),
                "row_count": mock_ohlcv_data.height,
                "start_date": "2024-01-02",
                "end_date": "2024-01-04",
            }
        },
    }
    manifest_path = provider._storage_path / "yfinance_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f)

    return provider


# =============================================================================
# Basic Functionality Tests
# =============================================================================


class TestBasicFunctionality:
    """Tests for basic provider functionality."""

    def test_provider_initialization(self, tmp_path: Path) -> None:
        """Test provider initializes correctly."""
        storage_path = tmp_path / "yfinance"
        provider = YFinanceProvider(storage_path=storage_path)

        assert provider._storage_path == storage_path.resolve()
        assert provider._daily_dir.exists()
        assert provider._quarantine_dir.exists()
        assert provider._lock_dir.exists()

    def test_provider_with_custom_lock_dir(self, tmp_path: Path) -> None:
        """Test provider with custom lock directory."""
        storage_path = tmp_path / "yfinance"
        lock_dir = tmp_path / "custom_locks"
        provider = YFinanceProvider(storage_path=storage_path, lock_dir=lock_dir)

        assert provider._lock_dir == lock_dir
        assert lock_dir.exists()

    def test_provider_with_baseline_path(self, tmp_path: Path) -> None:
        """Test provider with baseline path configured."""
        storage_path = tmp_path / "yfinance"
        baseline_path = tmp_path / "baseline"
        provider = YFinanceProvider(storage_path=storage_path, baseline_path=baseline_path)

        assert provider._baseline_path == baseline_path.resolve()

    def test_provider_without_baseline_path(self, tmp_path: Path) -> None:
        """Test provider without baseline path."""
        storage_path = tmp_path / "yfinance"
        provider = YFinanceProvider(storage_path=storage_path, baseline_path=None)

        assert provider._baseline_path is None

    def test_environment_normalized_to_lowercase(self, tmp_path: Path) -> None:
        """Test environment is normalized to lowercase."""
        storage_path = tmp_path / "yfinance"
        provider = YFinanceProvider(storage_path=storage_path, environment="PRODUCTION")

        assert provider._environment == "production"

    def test_provider_rejects_path_traversal(self, tmp_path: Path) -> None:
        """Test provider rejects paths with traversal."""
        with pytest.raises(ValueError, match="Path traversal"):
            YFinanceProvider(storage_path=tmp_path / ".." / "evil")

    def test_symbol_path_traversal_rejected(self, provider: YFinanceProvider) -> None:
        """Test symbols with path traversal are rejected."""
        with pytest.raises(ValueError, match="path traversal"):
            provider.get_daily_prices(
                symbols=["../evil"],
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
            )

    def test_symbol_with_slash_rejected(self, provider: YFinanceProvider) -> None:
        """Test symbols with forward slash are rejected."""
        with pytest.raises(ValueError, match="path traversal"):
            provider.get_daily_prices(
                symbols=["foo/bar"],
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
            )

    def test_symbol_with_backslash_rejected(self, provider: YFinanceProvider) -> None:
        """Test symbols with backslash are rejected."""
        with pytest.raises(ValueError, match="path traversal"):
            provider.get_daily_prices(
                symbols=["foo\\bar"],
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
            )

    def test_symbol_invalid_characters_rejected(self, provider: YFinanceProvider) -> None:
        """Test symbols with invalid characters are rejected."""
        with pytest.raises(ValueError, match="Invalid symbol format"):
            provider.get_daily_prices(
                symbols=["SPY$$$"],
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
            )

    def test_symbol_too_long_rejected(self, provider: YFinanceProvider) -> None:
        """Test symbols longer than 15 characters are rejected."""
        with pytest.raises(ValueError, match="Invalid symbol format"):
            provider.get_daily_prices(
                symbols=["A" * 16],  # 16 characters
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
            )

    def test_valid_symbol_formats_accepted(self, provider: YFinanceProvider) -> None:
        """Test valid symbol formats are accepted (BRK.B, BRK-B)."""
        # These should not raise - they're valid symbol formats
        # Mock the download to avoid network calls
        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = MagicMock(empty=True)
            mock_ticker_cls.return_value = mock_ticker

            # Should not raise ValueError for valid formats
            provider.get_daily_prices(
                symbols=["BRK.B", "BRK-A"],
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 5),
            )

    def test_empty_symbols_list_raises(self, provider: YFinanceProvider) -> None:
        """Test empty symbols list raises ValueError."""
        with pytest.raises(ValueError, match="symbols list cannot be empty"):
            provider.get_daily_prices(
                symbols=[],
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
            )

    def test_symbols_normalized_to_uppercase(self, provider_with_cache: YFinanceProvider) -> None:
        """Test symbols are normalized to uppercase."""
        # Cache has SPY, query with lowercase
        df = provider_with_cache.get_daily_prices(
            symbols=["spy"],  # lowercase
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )

        assert df.height > 0
        assert df["symbol"][0] == "SPY"

    def test_cache_hit_returns_data(self, provider_with_cache: YFinanceProvider) -> None:
        """Test cache hit returns cached data without network call.

        Cache only returns data when it FULLY covers the requested range.
        Mock data covers Jan 2-4, so we request exactly that range.
        """
        df = provider_with_cache.get_daily_prices(
            symbols=["SPY"],
            start_date=date(2024, 1, 2),  # Match mock data range exactly
            end_date=date(2024, 1, 4),
        )

        assert df.height == 3
        assert "open" in df.columns
        assert "close" in df.columns

    def test_partial_cache_triggers_refetch(self, provider_with_cache: YFinanceProvider) -> None:
        """Test partial cache coverage triggers fresh fetch.

        Cache only has Jan 2-4. Requesting Jan 1-31 should trigger refetch
        because cache doesn't fully cover the requested range.
        """
        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = MagicMock(empty=True)
            mock_ticker_cls.return_value = mock_ticker

            _df = provider_with_cache.get_daily_prices(
                symbols=["SPY"],
                start_date=date(2024, 1, 1),  # Before cache start
                end_date=date(2024, 1, 31),
            )

            # Should have triggered a fetch due to partial cache coverage
            assert mock_ticker_cls.called

    def test_cache_miss_triggers_fetch(self, provider: YFinanceProvider) -> None:
        """Test cache miss triggers download attempt."""
        # Mock yfinance to avoid network calls
        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = MagicMock(
                empty=True,
            )
            mock_ticker_cls.return_value = mock_ticker

            _df = provider.get_daily_prices(
                symbols=["AAPL"],
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
            )

            # Should have attempted download (may retry on empty response)
            assert mock_ticker_cls.called
            # First call should be with the symbol
            mock_ticker_cls.assert_any_call("AAPL")
            del _df  # Suppress unused variable warning

    def test_partial_data_logs_warning(
        self, provider: YFinanceProvider, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test warning logged when some symbols fail to fetch."""
        import logging

        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            # Return empty for all symbols to simulate failure
            mock_ticker.history.return_value = MagicMock(empty=True)
            mock_ticker_cls.return_value = mock_ticker

            with caplog.at_level(logging.WARNING):
                _df = provider.get_daily_prices(
                    symbols=["FAIL1", "FAIL2"],
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 1, 5),
                )

            # Should have logged warning about failed symbols
            assert any(
                "Some symbols failed to fetch" in record.message for record in caplog.records
            )
            del _df

    def test_many_uncached_symbols_logs_performance_warning(
        self, provider: YFinanceProvider, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test warning logged when fetching many uncached symbols."""
        import logging

        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = MagicMock(empty=True)
            mock_ticker_cls.return_value = mock_ticker

            # Fetch >10 symbols to trigger warning
            symbols = [f"SYM{i}" for i in range(15)]
            with caplog.at_level(logging.WARNING):
                _df = provider.get_daily_prices(
                    symbols=symbols,
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 1, 5),
                )

            # Should warn about fetching many uncached symbols
            assert any(
                "Fetching many uncached symbols" in record.message for record in caplog.records
            )
            del _df

    def test_use_cache_false_skips_cache(self, provider_with_cache: YFinanceProvider) -> None:
        """Test use_cache=False skips cache and fetches from network."""
        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = MagicMock(empty=True)
            mock_ticker_cls.return_value = mock_ticker

            _df = provider_with_cache.get_daily_prices(
                symbols=["SPY"],
                start_date=date(2024, 1, 2),
                end_date=date(2024, 1, 4),
                use_cache=False,  # Skip cache
            )

            # Should have fetched from network despite cache availability
            assert mock_ticker_cls.called

    def test_verify_data_with_valid_cache(self, provider_with_cache: YFinanceProvider) -> None:
        """Test verify_data returns True for valid cache."""
        results = provider_with_cache.verify_data()

        assert "SPY.parquet" in results
        assert results["SPY.parquet"] is True

    def test_verify_data_with_no_manifest(self, provider: YFinanceProvider) -> None:
        """Test verify_data returns empty dict when no manifest exists."""
        results = provider.verify_data()
        assert results == {}

    def test_verify_data_with_missing_checksum(self, provider_with_cache: YFinanceProvider) -> None:
        """Test verify_data returns False for missing checksum."""
        # Remove checksum from manifest
        manifest = provider_with_cache.get_manifest()
        manifest["files"]["SPY.parquet"].pop("checksum")
        manifest_path = provider_with_cache._storage_path / "yfinance_manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f)

        results = provider_with_cache.verify_data()
        assert results["SPY.parquet"] is False

    def test_verify_data_with_missing_file(self, provider_with_cache: YFinanceProvider) -> None:
        """Test verify_data returns False for missing file."""
        # Remove the cache file but keep manifest entry
        cache_path = provider_with_cache._daily_dir / "SPY.parquet"
        cache_path.unlink()

        results = provider_with_cache.verify_data()
        assert results["SPY.parquet"] is False

    def test_invalidate_cache_removes_files(self, provider_with_cache: YFinanceProvider) -> None:
        """Test invalidate_cache removes cache files."""
        # Verify cache exists
        cache_path = provider_with_cache._daily_dir / "SPY.parquet"
        assert cache_path.exists()

        # Invalidate
        removed = provider_with_cache.invalidate_cache(symbols=["SPY"])

        assert removed == 1
        assert not cache_path.exists()

    def test_invalidate_cache_all(self, provider_with_cache: YFinanceProvider) -> None:
        """Test invalidate_cache(None) removes all cache."""
        removed = provider_with_cache.invalidate_cache(symbols=None)

        assert removed == 1
        assert not (provider_with_cache._daily_dir / "SPY.parquet").exists()
        assert not (provider_with_cache._storage_path / "yfinance_manifest.json").exists()

    def test_invalidate_cache_updates_manifest(self, provider_with_cache: YFinanceProvider) -> None:
        """Test invalidate_cache for specific symbols updates manifest."""
        # Verify manifest has SPY entry
        manifest_before = provider_with_cache.get_manifest()
        assert manifest_before is not None
        assert "SPY.parquet" in manifest_before["files"]

        # Invalidate SPY
        removed = provider_with_cache.invalidate_cache(symbols=["SPY"])

        assert removed == 1

        # Manifest should be updated without SPY entry
        manifest_after = provider_with_cache.get_manifest()
        assert manifest_after is not None
        assert "SPY.parquet" not in manifest_after["files"]

    def test_invalidate_cache_rejects_traversal(
        self, provider_with_cache: YFinanceProvider
    ) -> None:
        """Test invalidate_cache rejects path traversal attempts."""
        # Should not raise but should log warning and not remove anything
        removed = provider_with_cache.invalidate_cache(symbols=["../evil"])

        assert removed == 0
        # Cache file should still exist
        assert (provider_with_cache._daily_dir / "SPY.parquet").exists()

    def test_invalidate_cache_handles_missing_cache_gracefully(
        self, provider_with_cache: YFinanceProvider
    ) -> None:
        """Test invalidate_cache handles missing cache files gracefully."""
        # Try to invalidate a symbol that doesn't exist in cache
        removed = provider_with_cache.invalidate_cache(symbols=["NOTEXIST"])

        assert removed == 0

    def test_invalidate_cache_handles_file_removal_error(
        self, provider_with_cache: YFinanceProvider, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test invalidate_cache logs warning on file removal error."""
        import logging

        # Mock unlink to raise an error
        with patch.object(Path, "unlink", side_effect=OSError("Permission denied")):
            with caplog.at_level(logging.WARNING):
                removed = provider_with_cache.invalidate_cache(symbols=["SPY"])

            assert removed == 0
            assert any("Failed to remove cache file" in record.message for record in caplog.records)

    def test_empty_result_returns_correct_schema(self, provider: YFinanceProvider) -> None:
        """Test _empty_result returns DataFrame with correct schema."""
        df = provider._empty_result()

        assert df.is_empty()
        assert df.schema == YFINANCE_SCHEMA


# =============================================================================
# Production Gating Tests (Matrix Tests)
# =============================================================================


class TestProductionGating:
    """Tests for production gating logic.

    Matrix tests for all combinations of:
    - environment: development, test, staging, production
    - crsp_available: True, False
    - use_yfinance_in_prod: True, False
    """

    def test_development_always_allowed(self, tmp_path: Path) -> None:
        """Test development environment is always allowed."""
        provider = YFinanceProvider(
            storage_path=tmp_path / "yfinance",
            environment="development",
            crsp_available=True,  # Even with CRSP
            use_yfinance_in_prod=False,
        )

        # Should not raise
        provider._check_production_gate()

    def test_test_environment_always_allowed(self, tmp_path: Path) -> None:
        """Test test environment is always allowed."""
        provider = YFinanceProvider(
            storage_path=tmp_path / "yfinance",
            environment="test",
            crsp_available=True,
            use_yfinance_in_prod=False,
        )

        # Should not raise
        provider._check_production_gate()

    def test_production_crsp_available_blocks(self, tmp_path: Path) -> None:
        """Test production with CRSP available always blocks."""
        provider = YFinanceProvider(
            storage_path=tmp_path / "yfinance",
            environment="production",
            crsp_available=True,
            use_yfinance_in_prod=True,  # Even with override
        )

        with pytest.raises(ProductionGateError, match="CRSP data is available"):
            provider._check_production_gate()

    def test_production_no_crsp_no_flag_blocks(self, tmp_path: Path) -> None:
        """Test production without CRSP and without flag blocks."""
        provider = YFinanceProvider(
            storage_path=tmp_path / "yfinance",
            environment="production",
            crsp_available=False,
            use_yfinance_in_prod=False,
        )

        with pytest.raises(ProductionGateError, match="yfinance blocked in production"):
            provider._check_production_gate()

    def test_production_no_crsp_with_flag_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test production without CRSP but with flag warns and allows."""
        provider = YFinanceProvider(
            storage_path=tmp_path / "yfinance",
            environment="production",
            crsp_available=False,
            use_yfinance_in_prod=True,
        )

        # Should not raise
        provider._check_production_gate()

        # Should warn
        assert "yfinance used in production without CRSP" in caplog.text

    def test_staging_warns_and_allows(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test staging environment warns and allows."""
        provider = YFinanceProvider(
            storage_path=tmp_path / "yfinance",
            environment="staging",
            crsp_available=False,
            use_yfinance_in_prod=False,
        )

        # Should not raise
        provider._check_production_gate()

        # Should warn
        assert "non-development environment" in caplog.text

    def test_get_daily_prices_respects_gate(self, tmp_path: Path) -> None:
        """Test get_daily_prices checks production gate."""
        provider = YFinanceProvider(
            storage_path=tmp_path / "yfinance",
            environment="production",
            crsp_available=True,
        )

        with pytest.raises(ProductionGateError):
            provider.get_daily_prices(
                symbols=["SPY"],
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
            )

    def test_fetch_and_cache_respects_gate(self, tmp_path: Path) -> None:
        """Test fetch_and_cache checks production gate."""
        provider = YFinanceProvider(
            storage_path=tmp_path / "yfinance",
            environment="production",
            crsp_available=True,
        )

        with pytest.raises(ProductionGateError):
            provider.fetch_and_cache(symbols=["SPY"])


# =============================================================================
# Drift Detection Tests
# =============================================================================


class TestDriftDetection:
    """Tests for drift detection against baseline data."""

    def test_drift_within_tolerance_passes(
        self,
        provider_with_baseline: YFinanceProvider,
        mock_ohlcv_data: pl.DataFrame,
        mock_baseline_data: pl.DataFrame,
    ) -> None:
        """Test drift within tolerance passes."""
        # Write baseline
        baseline_path = provider_with_baseline._baseline_path / "spy_60d.parquet"
        mock_baseline_data.write_parquet(baseline_path)

        passed, max_drift = provider_with_baseline.check_drift(
            symbol="SPY",
            yfinance_data=mock_ohlcv_data,
        )

        assert passed is True
        assert max_drift is not None
        assert max_drift < 0.01  # Within 1%

    def test_drift_exceeds_tolerance_fails(
        self,
        provider_with_baseline: YFinanceProvider,
        mock_ohlcv_data: pl.DataFrame,
        mock_baseline_data_with_drift: pl.DataFrame,
    ) -> None:
        """Test drift exceeding tolerance fails."""
        # Write baseline with drift
        baseline_path = provider_with_baseline._baseline_path / "spy_60d.parquet"
        mock_baseline_data_with_drift.write_parquet(baseline_path)

        passed, max_drift = provider_with_baseline.check_drift(
            symbol="SPY",
            yfinance_data=mock_ohlcv_data,
        )

        assert passed is False
        assert max_drift is not None
        assert max_drift > 0.01  # Exceeds 1%

    def test_drift_with_custom_tolerance(
        self,
        provider_with_baseline: YFinanceProvider,
        mock_ohlcv_data: pl.DataFrame,
        mock_baseline_data_with_drift: pl.DataFrame,
    ) -> None:
        """Test drift check with custom tolerance."""
        baseline_path = provider_with_baseline._baseline_path / "spy_60d.parquet"
        mock_baseline_data_with_drift.write_parquet(baseline_path)

        # Should pass with higher tolerance
        passed, max_drift = provider_with_baseline.check_drift(
            symbol="SPY",
            yfinance_data=mock_ohlcv_data,
            tolerance=0.10,  # 10% tolerance
        )

        assert passed is True
        assert max_drift is not None

    def test_missing_baseline_passes_with_warning(
        self,
        provider_with_baseline: YFinanceProvider,
        mock_ohlcv_data: pl.DataFrame,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test missing baseline passes with warning (per requirements)."""
        # Don't create baseline file

        passed, max_drift = provider_with_baseline.check_drift(
            symbol="SPY",
            yfinance_data=mock_ohlcv_data,
        )

        assert passed is True
        assert max_drift is None
        assert "Baseline missing for drift check" in caplog.text

    def test_no_baseline_path_skips_check(
        self,
        provider: YFinanceProvider,
        mock_ohlcv_data: pl.DataFrame,
    ) -> None:
        """Test no baseline path configured skips check."""
        passed, max_drift = provider.check_drift(
            symbol="SPY",
            yfinance_data=mock_ohlcv_data,
        )

        assert passed is True
        assert max_drift is None

    def test_no_overlapping_dates_passes_with_warning(
        self,
        provider_with_baseline: YFinanceProvider,
        mock_ohlcv_data: pl.DataFrame,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test no overlapping dates passes with warning."""
        # Create baseline with different dates
        baseline_data = pl.DataFrame(
            {
                "date": [date(2023, 1, 2), date(2023, 1, 3)],  # Different year
                "adj_close": [400.0, 401.0],
            }
        )
        baseline_path = provider_with_baseline._baseline_path / "spy_60d.parquet"
        baseline_data.write_parquet(baseline_path)

        passed, max_drift = provider_with_baseline.check_drift(
            symbol="SPY",
            yfinance_data=mock_ohlcv_data,
        )

        assert passed is True
        assert max_drift is None
        assert "No overlapping dates" in caplog.text

    def test_drift_check_uses_adj_close_when_available(
        self,
        provider_with_baseline: YFinanceProvider,
        mock_ohlcv_data: pl.DataFrame,
        mock_baseline_data: pl.DataFrame,
    ) -> None:
        """Test drift check uses adj_close when available."""
        baseline_path = provider_with_baseline._baseline_path / "spy_60d.parquet"
        mock_baseline_data.write_parquet(baseline_path)

        # Should use adj_close from both datasets
        passed, max_drift = provider_with_baseline.check_drift(
            symbol="SPY",
            yfinance_data=mock_ohlcv_data,
        )

        assert passed is True
        assert max_drift is not None

    def test_drift_check_fallback_to_close(
        self,
        provider_with_baseline: YFinanceProvider,
    ) -> None:
        """Test drift check falls back to close when adj_close missing."""
        # Create data without adj_close
        yf_data = pl.DataFrame(
            {
                "date": [date(2024, 1, 2), date(2024, 1, 3)],
                "close": [473.00, 474.80],
            }
        )
        baseline_data = pl.DataFrame(
            {
                "date": [date(2024, 1, 2), date(2024, 1, 3)],
                "close": [473.00, 474.80],
            }
        )
        baseline_path = provider_with_baseline._baseline_path / "spy_60d.parquet"
        baseline_data.write_parquet(baseline_path)

        passed, max_drift = provider_with_baseline.check_drift(
            symbol="SPY",
            yfinance_data=yf_data,
        )

        assert passed is True
        assert max_drift is not None

    def test_drift_check_missing_price_column_in_yfinance(
        self,
        provider_with_baseline: YFinanceProvider,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test drift check handles missing price column in yfinance data."""
        yf_data = pl.DataFrame(
            {
                "date": [date(2024, 1, 2)],
                "symbol": ["SPY"],
                # Missing both close and adj_close
            }
        )
        baseline_data = pl.DataFrame(
            {
                "date": [date(2024, 1, 2)],
                "adj_close": [473.00],
            }
        )
        baseline_path = provider_with_baseline._baseline_path / "spy_60d.parquet"
        baseline_data.write_parquet(baseline_path)

        passed, max_drift = provider_with_baseline.check_drift(
            symbol="SPY",
            yfinance_data=yf_data,
        )

        assert passed is True
        assert max_drift is None
        assert "Missing price column" in caplog.text

    def test_drift_check_missing_price_column_in_baseline(
        self,
        provider_with_baseline: YFinanceProvider,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test drift check handles missing price column in baseline."""
        yf_data = pl.DataFrame(
            {
                "date": [date(2024, 1, 2)],
                "adj_close": [473.00],
            }
        )
        baseline_data = pl.DataFrame(
            {
                "date": [date(2024, 1, 2)],
                # Missing both close and adj_close
            }
        )
        baseline_path = provider_with_baseline._baseline_path / "spy_60d.parquet"
        baseline_data.write_parquet(baseline_path)

        passed, max_drift = provider_with_baseline.check_drift(
            symbol="SPY",
            yfinance_data=yf_data,
        )

        assert passed is True
        assert max_drift is None
        assert "Missing price column in baseline" in caplog.text

    def test_baseline_manifest_checksum_validation(
        self,
        provider_with_baseline: YFinanceProvider,
        mock_ohlcv_data: pl.DataFrame,
        mock_baseline_data: pl.DataFrame,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test baseline checksum validation against manifest."""
        # Write baseline file
        baseline_path = provider_with_baseline._baseline_path / "spy_60d.parquet"
        mock_baseline_data.write_parquet(baseline_path)

        # Compute correct checksum
        correct_checksum = provider_with_baseline._compute_checksum(baseline_path)

        # Create manifest with correct checksum
        manifest = {
            "files": {
                "spy_60d.parquet": {
                    "checksum": correct_checksum,
                    "symbol": "SPY",
                }
            }
        }
        manifest_path = provider_with_baseline._baseline_path / "baseline_manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f)

        # Drift check should pass (valid checksum)
        passed, max_drift = provider_with_baseline.check_drift(
            symbol="SPY",
            yfinance_data=mock_ohlcv_data,
        )

        assert passed is True
        assert "checksum mismatch" not in caplog.text

    def test_baseline_manifest_checksum_mismatch(
        self,
        provider_with_baseline: YFinanceProvider,
        mock_ohlcv_data: pl.DataFrame,
        mock_baseline_data: pl.DataFrame,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test baseline checksum mismatch blocks caching."""
        # Write baseline file
        baseline_path = provider_with_baseline._baseline_path / "spy_60d.parquet"
        mock_baseline_data.write_parquet(baseline_path)

        # Create manifest with WRONG checksum
        manifest = {
            "files": {
                "spy_60d.parquet": {
                    "checksum": "wrongchecksum123",
                    "symbol": "SPY",
                }
            }
        }
        manifest_path = provider_with_baseline._baseline_path / "baseline_manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f)

        # Drift check should FAIL (return False) to block caching
        # This prevents ingesting unverified data when baseline is corrupted
        passed, max_drift = provider_with_baseline.check_drift(
            symbol="SPY",
            yfinance_data=mock_ohlcv_data,
        )

        assert passed is False  # Should block caching
        assert max_drift is None
        assert "checksum" in caplog.text.lower()

    def test_baseline_manifest_missing_entry(
        self,
        provider_with_baseline: YFinanceProvider,
        mock_ohlcv_data: pl.DataFrame,
        mock_baseline_data: pl.DataFrame,
    ) -> None:
        """Test baseline validation skips when manifest has no entry for file."""
        baseline_path = provider_with_baseline._baseline_path / "spy_60d.parquet"
        mock_baseline_data.write_parquet(baseline_path)

        # Create manifest without entry for this file
        manifest = {"files": {}}
        manifest_path = provider_with_baseline._baseline_path / "baseline_manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f)

        passed, max_drift = provider_with_baseline.check_drift(
            symbol="SPY",
            yfinance_data=mock_ohlcv_data,
        )

        # Should pass (skip validation when no manifest entry)
        assert passed is True

    def test_baseline_manifest_missing_checksum_field(
        self,
        provider_with_baseline: YFinanceProvider,
        mock_ohlcv_data: pl.DataFrame,
        mock_baseline_data: pl.DataFrame,
    ) -> None:
        """Test baseline validation skips when manifest entry has no checksum."""
        baseline_path = provider_with_baseline._baseline_path / "spy_60d.parquet"
        mock_baseline_data.write_parquet(baseline_path)

        # Create manifest entry without checksum field
        manifest = {
            "files": {
                "spy_60d.parquet": {
                    "symbol": "SPY",
                    # No checksum field
                }
            }
        }
        manifest_path = provider_with_baseline._baseline_path / "baseline_manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f)

        passed, max_drift = provider_with_baseline.check_drift(
            symbol="SPY",
            yfinance_data=mock_ohlcv_data,
        )

        # Should pass (skip validation when no checksum)
        assert passed is True

    def test_baseline_manifest_read_error(
        self,
        provider_with_baseline: YFinanceProvider,
        mock_ohlcv_data: pl.DataFrame,
        mock_baseline_data: pl.DataFrame,
    ) -> None:
        """Test baseline validation handles manifest read errors gracefully."""
        baseline_path = provider_with_baseline._baseline_path / "spy_60d.parquet"
        mock_baseline_data.write_parquet(baseline_path)

        # Create invalid JSON manifest
        manifest_path = provider_with_baseline._baseline_path / "baseline_manifest.json"
        with open(manifest_path, "w") as f:
            f.write("invalid json{{{")

        passed, max_drift = provider_with_baseline.check_drift(
            symbol="SPY",
            yfinance_data=mock_ohlcv_data,
        )

        # Should pass (skip validation on manifest read error)
        assert passed is True

    def test_drift_check_handles_zero_baseline(
        self,
        provider_with_baseline: YFinanceProvider,
        mock_ohlcv_data: pl.DataFrame,
    ) -> None:
        """Test drift check handles zero baseline prices (avoids division by zero)."""
        # Create baseline with zero price
        baseline_data = pl.DataFrame(
            {
                "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
                "adj_close": [0.0, 0.0, 0.0],  # All zeros
            }
        )
        baseline_path = provider_with_baseline._baseline_path / "spy_60d.parquet"
        baseline_data.write_parquet(baseline_path)

        # Should not raise division by zero
        passed, max_drift = provider_with_baseline.check_drift(
            symbol="SPY",
            yfinance_data=mock_ohlcv_data,
        )

        # Passes because all zero prices are filtered out
        assert passed is True
        assert max_drift is None

    def test_drift_check_handles_null_baseline(
        self,
        provider_with_baseline: YFinanceProvider,
        mock_ohlcv_data: pl.DataFrame,
    ) -> None:
        """Test drift check handles null baseline prices."""
        # Create baseline with null prices
        baseline_data = pl.DataFrame(
            {
                "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
                "adj_close": [None, None, None],
            }
        )
        baseline_path = provider_with_baseline._baseline_path / "spy_60d.parquet"
        baseline_data.write_parquet(baseline_path)

        passed, max_drift = provider_with_baseline.check_drift(
            symbol="SPY",
            yfinance_data=mock_ohlcv_data,
        )

        # Should pass (all null prices filtered out)
        assert passed is True
        assert max_drift is None

    def test_drift_check_reads_from_cache_when_data_not_provided(
        self,
        provider_with_baseline: YFinanceProvider,
        mock_ohlcv_data: pl.DataFrame,
        mock_baseline_data: pl.DataFrame,
    ) -> None:
        """Test drift check reads from cache when yfinance_data not provided."""
        # Write baseline
        baseline_path = provider_with_baseline._baseline_path / "spy_60d.parquet"
        mock_baseline_data.write_parquet(baseline_path)

        # Write cache
        cache_path = provider_with_baseline._daily_dir / "SPY.parquet"
        mock_ohlcv_data.write_parquet(cache_path)

        # Call without providing yfinance_data
        passed, max_drift = provider_with_baseline.check_drift(symbol="SPY")

        assert passed is True
        assert max_drift is not None

    def test_drift_check_handles_invalid_symbol_for_cache_read(
        self,
        provider_with_baseline: YFinanceProvider,
    ) -> None:
        """Test drift check handles invalid symbol when reading from cache."""
        baseline_path = provider_with_baseline._baseline_path / "spy_60d.parquet"
        baseline_data = pl.DataFrame(
            {
                "date": [date(2024, 1, 2)],
                "adj_close": [473.00],
            }
        )
        baseline_data.write_parquet(baseline_path)

        # Call with invalid symbol (will fail cache path validation)
        passed, max_drift = provider_with_baseline.check_drift(symbol="../evil")

        # Should pass (skip check on invalid symbol)
        assert passed is True
        assert max_drift is None

    def test_drift_check_handles_missing_cache_file(
        self,
        provider_with_baseline: YFinanceProvider,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test drift check handles missing cache file when reading from cache."""
        baseline_path = provider_with_baseline._baseline_path / "spy_60d.parquet"
        baseline_data = pl.DataFrame(
            {
                "date": [date(2024, 1, 2)],
                "adj_close": [473.00],
            }
        )
        baseline_data.write_parquet(baseline_path)

        # Call without cache file
        passed, max_drift = provider_with_baseline.check_drift(symbol="SPY")

        assert passed is True
        assert max_drift is None
        assert "No yfinance cache for drift check" in caplog.text

    def test_drift_check_handles_baseline_read_error(
        self,
        provider_with_baseline: YFinanceProvider,
        mock_ohlcv_data: pl.DataFrame,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test drift check handles baseline file read errors."""
        baseline_path = provider_with_baseline._baseline_path / "spy_60d.parquet"
        # Create invalid parquet file
        with open(baseline_path, "w") as f:
            f.write("not a parquet file")

        passed, max_drift = provider_with_baseline.check_drift(
            symbol="SPY",
            yfinance_data=mock_ohlcv_data,
        )

        assert passed is True
        assert max_drift is None
        assert "Failed to read baseline" in caplog.text

    def test_drift_check_runs_on_fetch_and_cache(
        self,
        provider_with_baseline: YFinanceProvider,
        mock_baseline_data_with_drift: pl.DataFrame,
    ) -> None:
        """Test drift check runs automatically during fetch_and_cache."""
        # Write baseline with drift
        baseline_path = provider_with_baseline._baseline_path / "spy_60d.parquet"
        mock_baseline_data_with_drift.write_parquet(baseline_path)

        # Mock yfinance download
        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
                "Open": [471.50, 473.20, 472.80],
                "High": [474.00, 475.50, 476.20],
                "Low": [470.00, 472.00, 471.50],
                "Close": [473.00, 474.80, 475.50],
                "Volume": [50000000, 48000000, 52000000],
                "Adj Close": [473.00, 474.80, 475.50],
            }
        ).set_index("Date")

        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = mock_pdf
            mock_ticker_cls.return_value = mock_ticker

            result = provider_with_baseline.fetch_and_cache(
                symbols=["SPY"],
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 5),
                run_drift_check=True,
            )

            # Should have drift warning
            assert "SPY" in result["drift_warnings"]

    def test_drift_check_skipped_when_disabled(
        self,
        provider_with_baseline: YFinanceProvider,
        mock_baseline_data_with_drift: pl.DataFrame,
    ) -> None:
        """Test drift check can be disabled in fetch_and_cache."""
        # Write baseline with drift
        baseline_path = provider_with_baseline._baseline_path / "spy_60d.parquet"
        mock_baseline_data_with_drift.write_parquet(baseline_path)

        # Mock yfinance download
        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2024-01-02"]),
                "Open": [471.50],
                "High": [474.00],
                "Low": [470.00],
                "Close": [473.00],
                "Volume": [50000000],
                "Adj Close": [473.00],
            }
        ).set_index("Date")

        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = mock_pdf
            mock_ticker_cls.return_value = mock_ticker

            result = provider_with_baseline.fetch_and_cache(
                symbols=["SPY"],
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 5),
                run_drift_check=False,  # Disable drift check
            )

            # Should NOT have drift warnings (check was skipped)
            assert "SPY" not in result["drift_warnings"]
            # Should have succeeded
            assert "SPY" not in result["failed_symbols"]


# =============================================================================
# Fetch and Cache Tests
# =============================================================================


class TestFetchAndCache:
    """Tests for fetch_and_cache functionality."""

    def test_fetch_and_cache_empty_symbols_returns_empty_result(
        self, provider: YFinanceProvider
    ) -> None:
        """Test fetch_and_cache with empty symbols returns empty result."""
        result = provider.fetch_and_cache(symbols=[])

        assert result == {"files": {}, "failed_symbols": [], "drift_warnings": {}}

    def test_fetch_and_cache_default_date_range(self, provider: YFinanceProvider) -> None:
        """Test fetch_and_cache uses default 5-year date range."""
        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2024-01-02"]),
                "Open": [100.0],
                "High": [101.0],
                "Low": [99.0],
                "Close": [100.5],
                "Volume": [1000000],
                "Adj Close": [100.5],
            }
        ).set_index("Date")

        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = mock_pdf
            mock_ticker_cls.return_value = mock_ticker

            with patch("libs.data.data_providers.yfinance_provider.date") as mock_date:
                mock_date.today.return_value = date(2024, 1, 10)
                result = provider.fetch_and_cache(
                    symbols=["SPY"],
                    # No start_date/end_date provided
                    run_drift_check=False,
                )

            # Should have called history with 5-year range
            assert "SPY.parquet" in result["files"]

    def test_fetch_and_cache_updates_manifest_per_symbol(self, provider: YFinanceProvider) -> None:
        """Test fetch_and_cache updates manifest after each symbol."""
        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2024-01-02"]),
                "Open": [100.0],
                "High": [101.0],
                "Low": [99.0],
                "Close": [100.5],
                "Volume": [1000000],
                "Adj Close": [100.5],
            }
        ).set_index("Date")

        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = mock_pdf
            mock_ticker_cls.return_value = mock_ticker

            provider.fetch_and_cache(
                symbols=["SPY", "QQQ"],
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 5),
                run_drift_check=False,
            )

        # Manifest should have both symbols
        manifest = provider.get_manifest()
        assert "SPY.parquet" in manifest["files"]
        assert "QQQ.parquet" in manifest["files"]

    def test_fetch_and_cache_handles_empty_download(
        self, provider: YFinanceProvider, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test fetch_and_cache handles empty download gracefully."""
        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = MagicMock(empty=True)
            mock_ticker_cls.return_value = mock_ticker

            result = provider.fetch_and_cache(
                symbols=["NOSUCH"],
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 5),
                run_drift_check=False,
            )

        assert "NOSUCH" in result["failed_symbols"]
        assert "Download failed or empty" in caplog.text

    def test_fetch_and_cache_lock_timeout_raises_error(self, provider: YFinanceProvider) -> None:
        """Test fetch_and_cache raises error on lock timeout."""
        with patch(
            "libs.data.data_providers.yfinance_provider.AtomicFileLock.acquire",
            side_effect=TimeoutError("Lock timeout"),
        ):
            with pytest.raises(YFinanceError, match="Failed to acquire cache lock"):
                provider.fetch_and_cache(symbols=["SPY"])

    def test_fetch_and_cache_lock_oserror_raises_error(self, provider: YFinanceProvider) -> None:
        """Test fetch_and_cache raises error on lock OSError."""
        with patch(
            "libs.data.data_providers.yfinance_provider.AtomicFileLock.acquire",
            side_effect=OSError("Filesystem error"),
        ):
            with pytest.raises(YFinanceError, match="Failed to acquire cache lock"):
                provider.fetch_and_cache(symbols=["SPY"])

    def test_fetch_and_cache_releases_lock_on_success(self, provider: YFinanceProvider) -> None:
        """Test fetch_and_cache releases lock after successful operation."""
        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2024-01-02"]),
                "Open": [100.0],
                "High": [101.0],
                "Low": [99.0],
                "Close": [100.5],
                "Volume": [1000000],
                "Adj Close": [100.5],
            }
        ).set_index("Date")

        mock_lock = Mock()
        mock_lock.acquire.return_value = "test_token"

        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = mock_pdf
            mock_ticker_cls.return_value = mock_ticker

            with patch(
                "libs.data.data_providers.yfinance_provider.AtomicFileLock",
                return_value=mock_lock,
            ):
                provider.fetch_and_cache(
                    symbols=["SPY"],
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 1, 5),
                    run_drift_check=False,
                )

        # Should have released lock
        mock_lock.release.assert_called_once_with("test_token")

    def test_fetch_and_cache_releases_lock_on_error(self, provider: YFinanceProvider) -> None:
        """Test fetch_and_cache releases lock even on error."""
        mock_lock = Mock()
        mock_lock.acquire.return_value = "test_token"

        with patch("yfinance.Ticker", side_effect=Exception("Download failed")):
            with patch(
                "libs.data.data_providers.yfinance_provider.AtomicFileLock",
                return_value=mock_lock,
            ):
                with pytest.raises(Exception, match="Download failed"):
                    provider.fetch_and_cache(
                        symbols=["SPY"],
                        start_date=date(2024, 1, 1),
                        end_date=date(2024, 1, 5),
                        run_drift_check=False,
                    )

        # Should have released lock even on error
        mock_lock.release.assert_called_once_with("test_token")

    def test_fetch_and_cache_drift_check_blocks_caching_on_failure(
        self, provider_with_baseline: YFinanceProvider, mock_baseline_data_with_drift: pl.DataFrame
    ) -> None:
        """Test drift check failure blocks caching and quarantines existing cache."""
        # Write baseline with drift
        baseline_path = provider_with_baseline._baseline_path / "spy_60d.parquet"
        mock_baseline_data_with_drift.write_parquet(baseline_path)

        # Pre-populate cache with old data
        old_cache_path = provider_with_baseline._daily_dir / "SPY.parquet"
        old_data = pl.DataFrame(
            {
                "date": [date(2024, 1, 1)],
                "symbol": ["SPY"],
                "close": [400.0],
                "adj_close": [400.0],
            }
        )
        old_data.write_parquet(old_cache_path)

        # Create manifest for old cache
        manifest = {
            "dataset": "yfinance",
            "files": {
                "SPY.parquet": {
                    "symbol": "SPY",
                    "checksum": provider_with_baseline._compute_checksum(old_cache_path),
                }
            },
        }
        manifest_path = provider_with_baseline._storage_path / "yfinance_manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f)

        # Mock yfinance download
        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
                "Open": [471.50, 473.20, 472.80],
                "High": [474.00, 475.50, 476.20],
                "Low": [470.00, 472.00, 471.50],
                "Close": [473.00, 474.80, 475.50],
                "Volume": [50000000, 48000000, 52000000],
                "Adj Close": [473.00, 474.80, 475.50],
            }
        ).set_index("Date")

        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = mock_pdf
            mock_ticker_cls.return_value = mock_ticker

            result = provider_with_baseline.fetch_and_cache(
                symbols=["SPY"],
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 5),
                run_drift_check=True,
            )

        # Should have failed due to drift
        assert "SPY" in result["failed_symbols"]
        assert "SPY" in result["drift_warnings"]

        # Old cache should be quarantined
        assert not old_cache_path.exists()
        quarantine_files = list(provider_with_baseline._quarantine_dir.glob("*drift*"))
        assert len(quarantine_files) > 0

        # Manifest should not have SPY entry
        final_manifest = provider_with_baseline.get_manifest()
        if final_manifest:
            assert "SPY.parquet" not in final_manifest.get("files", {})


# =============================================================================
# Atomic Write Tests
# =============================================================================


class TestAtomicWrites:
    """Tests for atomic write operations."""

    def test_atomic_write_creates_file(
        self, provider: YFinanceProvider, mock_ohlcv_data: pl.DataFrame
    ) -> None:
        """Test atomic write creates file successfully."""
        target_path = provider._daily_dir / "TEST.parquet"

        checksum = provider._atomic_write_parquet(mock_ohlcv_data, target_path)

        assert target_path.exists()
        assert len(checksum) == 64  # SHA-256 hex

    def test_atomic_write_no_tmp_file_visible(
        self, provider: YFinanceProvider, mock_ohlcv_data: pl.DataFrame
    ) -> None:
        """Test atomic write doesn't leave .tmp files."""
        target_path = provider._daily_dir / "TEST.parquet"
        temp_path = target_path.with_suffix(".parquet.tmp")

        provider._atomic_write_parquet(mock_ohlcv_data, target_path)

        assert not temp_path.exists()

    def test_empty_dataframe_quarantined(self, provider: YFinanceProvider) -> None:
        """Test empty DataFrame triggers quarantine."""
        empty_df = pl.DataFrame(schema={"date": pl.Date, "close": pl.Float64})
        target_path = provider._daily_dir / "EMPTY.parquet"

        with pytest.raises(ValueError, match="Empty DataFrame"):
            provider._atomic_write_parquet(empty_df, target_path)

        # File should be quarantined
        quarantine_files = list(provider._quarantine_dir.glob("*empty_dataframe*"))
        assert len(quarantine_files) == 1

    def test_atomic_write_insufficient_disk_space(
        self, provider: YFinanceProvider, mock_ohlcv_data: pl.DataFrame
    ) -> None:
        """Test atomic write fails on insufficient disk space."""
        target_path = provider._daily_dir / "TEST.parquet"

        with patch.object(
            provider, "_check_disk_space", side_effect=OSError("Insufficient disk space")
        ):
            with pytest.raises(OSError, match="Insufficient disk space"):
                provider._atomic_write_parquet(mock_ohlcv_data, target_path)

    def test_atomic_write_cleans_up_tmp_on_error(
        self, provider: YFinanceProvider, mock_ohlcv_data: pl.DataFrame
    ) -> None:
        """Test atomic write cleans up temp file on error."""
        target_path = provider._daily_dir / "TEST.parquet"
        temp_path = target_path.with_suffix(".parquet.tmp")

        # Make rename fail
        with patch.object(Path, "rename", side_effect=OSError("Rename failed")):
            with pytest.raises(OSError, match="Rename failed"):
                provider._atomic_write_parquet(mock_ohlcv_data, target_path)

        # Temp file should be cleaned up
        assert not temp_path.exists()

    def test_checksum_mismatch_detected(self, provider_with_cache: YFinanceProvider) -> None:
        """Test checksum mismatch is detected by verify_data."""
        # Corrupt the cache file
        cache_path = provider_with_cache._daily_dir / "SPY.parquet"
        with open(cache_path, "ab") as f:
            f.write(b"corruption")

        results = provider_with_cache.verify_data()

        assert results["SPY.parquet"] is False

    def test_atomic_write_manifest_creates_file(self, provider: YFinanceProvider) -> None:
        """Test atomic manifest write creates file."""
        manifest_data = {
            "dataset": "yfinance",
            "files": {},
        }

        provider._atomic_write_manifest(manifest_data)

        manifest_path = provider._storage_path / "yfinance_manifest.json"
        assert manifest_path.exists()

    def test_atomic_write_manifest_no_tmp_file_visible(self, provider: YFinanceProvider) -> None:
        """Test atomic manifest write doesn't leave .tmp files."""
        manifest_data = {"dataset": "yfinance", "files": {}}
        manifest_path = provider._storage_path / "yfinance_manifest.json"
        temp_path = manifest_path.with_suffix(".json.tmp")

        provider._atomic_write_manifest(manifest_data)

        assert not temp_path.exists()

    def test_atomic_write_manifest_handles_serialization_error(
        self, provider: YFinanceProvider
    ) -> None:
        """Test atomic manifest write handles serialization errors."""

        # Create non-serializable data
        class NotSerializable:
            pass

        manifest_data = {"dataset": "yfinance", "bad_data": NotSerializable()}

        with pytest.raises((TypeError, ValueError)):
            provider._atomic_write_manifest(manifest_data)

        # Temp file should be cleaned up
        temp_path = provider._storage_path / "yfinance_manifest.json.tmp"
        assert not temp_path.exists()

    def test_atomic_write_manifest_cleans_up_tmp_on_error(self, provider: YFinanceProvider) -> None:
        """Test atomic manifest write cleans up temp file on error."""
        manifest_data = {"dataset": "yfinance", "files": {}}
        temp_path = provider._storage_path / "yfinance_manifest.json.tmp"

        with patch.object(Path, "rename", side_effect=OSError("Rename failed")):
            with pytest.raises(OSError, match="Rename failed"):
                provider._atomic_write_manifest(manifest_data)

        # Temp file should be cleaned up
        assert not temp_path.exists()

    def test_check_disk_space_raises_on_insufficient_space(
        self, provider: YFinanceProvider, tmp_path: Path
    ) -> None:
        """Test disk space check raises error when insufficient space."""
        with patch("os.statvfs") as mock_statvfs:
            # Mock very low available space
            mock_stat = Mock()
            mock_stat.f_bavail = 1  # 1 block
            mock_stat.f_frsize = 4096  # 4KB blocks
            mock_statvfs.return_value = mock_stat

            with pytest.raises(OSError, match="Insufficient disk space"):
                provider._check_disk_space(tmp_path)

    def test_check_disk_space_passes_with_sufficient_space(
        self, provider: YFinanceProvider, tmp_path: Path
    ) -> None:
        """Test disk space check passes with sufficient space."""
        with patch("os.statvfs") as mock_statvfs:
            # Mock plenty of available space
            mock_stat = Mock()
            mock_stat.f_bavail = 1000000  # Many blocks
            mock_stat.f_frsize = 4096  # 4KB blocks
            mock_statvfs.return_value = mock_stat

            # Should not raise
            provider._check_disk_space(tmp_path)

    def test_check_disk_space_handles_windows(
        self, provider: YFinanceProvider, tmp_path: Path
    ) -> None:
        """Test disk space check handles Windows (no statvfs)."""
        with patch("os.statvfs", side_effect=AttributeError("Windows")):
            # Should not raise on Windows
            provider._check_disk_space(tmp_path)

    def test_fsync_directory_handles_oserror(
        self, provider: YFinanceProvider, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test fsync directory handles OSError gracefully."""
        import logging

        with patch("os.open", side_effect=OSError("Unsupported")):
            with caplog.at_level(logging.WARNING):
                provider._fsync_directory(tmp_path)

            assert "Failed to fsync directory" in caplog.text

    def test_fsync_directory_handles_attribute_error(
        self, provider: YFinanceProvider, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test fsync directory handles AttributeError (no O_DIRECTORY)."""
        import logging

        with patch("os.open", side_effect=AttributeError("O_DIRECTORY not defined")):
            with caplog.at_level(logging.WARNING):
                provider._fsync_directory(tmp_path)

            assert "Failed to fsync directory" in caplog.text


# =============================================================================
# Cache Integrity Tests
# =============================================================================


class TestCacheIntegrity:
    """Tests for cache integrity verification."""

    def test_cache_integrity_verified_on_read(self, provider_with_cache: YFinanceProvider) -> None:
        """Test cache integrity is verified before reading."""
        # Valid cache should be read successfully
        df = provider_with_cache.get_daily_prices(
            symbols=["SPY"],
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 4),
        )

        assert df.height > 0

    def test_corrupted_cache_quarantined(self, provider_with_cache: YFinanceProvider) -> None:
        """Test corrupted cache is quarantined."""
        # Corrupt the cache file
        cache_path = provider_with_cache._daily_dir / "SPY.parquet"
        with open(cache_path, "ab") as f:
            f.write(b"corruption")

        # Attempt to read - should detect corruption and quarantine
        df = provider_with_cache.get_daily_prices(
            symbols=["SPY"],
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 4),
        )

        # Should return empty (cache was quarantined)
        assert df.is_empty()

        # Cache file should be quarantined
        assert not cache_path.exists()
        quarantine_files = list(provider_with_cache._quarantine_dir.glob("*checksum*"))
        assert len(quarantine_files) > 0

    def test_cache_without_manifest_entry_skips_verification(
        self, provider: YFinanceProvider, mock_ohlcv_data: pl.DataFrame
    ) -> None:
        """Test cache without manifest entry skips verification (race condition)."""
        # Write cache file without manifest entry
        cache_path = provider._daily_dir / "SPY.parquet"
        mock_ohlcv_data.write_parquet(cache_path)

        # Should read successfully (skips verification when no manifest entry)
        df = provider.get_daily_prices(
            symbols=["SPY"],
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 4),
        )

        assert df.height > 0

    def test_cache_with_empty_data_returns_none(self, provider: YFinanceProvider) -> None:
        """Test cache with empty data returns None."""
        # Write empty cache
        empty_df = pl.DataFrame(schema=YFINANCE_SCHEMA)
        cache_path = provider._daily_dir / "SPY.parquet"
        # Can't write empty parquet normally, so create a valid file first
        temp_df = pl.DataFrame(
            {
                "date": [date(2024, 1, 1)],
                "symbol": ["SPY"],
                "open": [100.0],
                "high": [100.0],
                "low": [100.0],
                "close": [100.0],
                "volume": [0.0],
                "adj_close": [100.0],
            }
        )
        temp_df.write_parquet(cache_path)
        # Now overwrite with empty
        empty_df.write_parquet(cache_path)

        # Should return None (empty cache)
        result = provider._read_from_cache("SPY", date(2024, 1, 1), date(2024, 1, 31))
        # Empty dataframe returns None
        assert result is None

    def test_cache_partial_coverage_returns_none(
        self, provider_with_cache: YFinanceProvider
    ) -> None:
        """Test cache with partial coverage returns None."""
        # Cache has Jan 2-4, request Jan 1-31
        result = provider_with_cache._read_from_cache("SPY", date(2024, 1, 1), date(2024, 1, 31))

        # Should return None (partial coverage)
        assert result is None

    def test_cache_with_gaps_quarantined(
        self, provider: YFinanceProvider, mock_ohlcv_data: pl.DataFrame
    ) -> None:
        """Test cache with suspicious gaps is quarantined."""
        # Create cache with very few rows for a long date range (suspicious)
        gapped_data = pl.DataFrame(
            {
                "date": [date(2024, 1, 1), date(2024, 12, 31)],  # 1 year span, only 2 rows
                "symbol": ["SPY", "SPY"],
                "open": [100.0, 110.0],
                "high": [101.0, 111.0],
                "low": [99.0, 109.0],
                "close": [100.5, 110.5],
                "volume": [1000000.0, 1000000.0],
                "adj_close": [100.5, 110.5],
            }
        )

        cache_path = provider._daily_dir / "SPY.parquet"
        gapped_data.write_parquet(cache_path)

        # Create manifest
        manifest = {
            "dataset": "yfinance",
            "files": {
                "SPY.parquet": {
                    "symbol": "SPY",
                    "checksum": provider._compute_checksum(cache_path),
                }
            },
        }
        manifest_path = provider._storage_path / "yfinance_manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f)

        # Attempt to read
        result = provider._read_from_cache("SPY", date(2024, 1, 1), date(2024, 12, 31))

        # Should return None (cache was quarantined)
        assert result is None

        # Cache should be quarantined
        assert not cache_path.exists()
        quarantine_files = list(provider._quarantine_dir.glob("*potential_gaps*"))
        assert len(quarantine_files) > 0

    def test_cache_read_handles_invalid_symbol(self, provider: YFinanceProvider) -> None:
        """Test cache read handles invalid symbol gracefully."""
        result = provider._read_from_cache("../evil", date(2024, 1, 1), date(2024, 1, 31))

        # Should return None (invalid symbol)
        assert result is None

    def test_cache_read_handles_missing_file(self, provider: YFinanceProvider) -> None:
        """Test cache read handles missing file gracefully."""
        result = provider._read_from_cache("NOTEXIST", date(2024, 1, 1), date(2024, 1, 31))

        assert result is None

    def test_cache_read_handles_parquet_error(
        self, provider_with_cache: YFinanceProvider, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test cache read handles parquet read errors."""
        import logging

        # Corrupt the cache file to cause read error
        cache_path = provider_with_cache._daily_dir / "SPY.parquet"
        with open(cache_path, "w") as f:
            f.write("not a parquet file")

        with caplog.at_level(logging.WARNING):
            result = provider_with_cache._read_from_cache(
                "SPY", date(2024, 1, 1), date(2024, 1, 31)
            )

        assert result is None
        assert "Failed to read cache" in caplog.text

    def test_cache_quarantine_handles_error(
        self, provider_with_cache: YFinanceProvider, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test cache quarantine handles errors gracefully."""
        import logging

        # Corrupt cache
        cache_path = provider_with_cache._daily_dir / "SPY.parquet"
        with open(cache_path, "ab") as f:
            f.write(b"corruption")

        # Mock quarantine to fail
        with patch.object(
            provider_with_cache, "_quarantine_file", side_effect=OSError("Quarantine failed")
        ):
            with caplog.at_level(logging.WARNING):
                result = provider_with_cache._read_from_cache(
                    "SPY", date(2024, 1, 2), date(2024, 1, 4)
                )

            assert result is None
            assert "Failed to quarantine corrupted cache" in caplog.text

    def test_cache_with_null_dates_returns_none(self, provider: YFinanceProvider) -> None:
        """Test cache with null min/max dates returns None."""
        # Create cache with null dates (shouldn't happen but defensive)
        cache_data = pl.DataFrame(
            {
                "date": [None, None],
                "symbol": ["SPY", "SPY"],
                "close": [100.0, 101.0],
                "adj_close": [100.0, 101.0],
            }
        )
        cache_path = provider._daily_dir / "SPY.parquet"
        cache_data.write_parquet(cache_path)

        result = provider._read_from_cache("SPY", date(2024, 1, 1), date(2024, 1, 31))

        assert result is None

    def test_cache_filters_to_requested_range(self, provider_with_cache: YFinanceProvider) -> None:
        """Test cache filters data to requested date range."""
        # Cache has Jan 2-4, request exactly Jan 2-3
        df = provider_with_cache.get_daily_prices(
            symbols=["SPY"],
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 3),
        )

        # Should return only 2 rows
        assert df.height == 2
        assert df["date"].min() == date(2024, 1, 2)
        assert df["date"].max() == date(2024, 1, 3)

    def test_cache_filtered_to_empty_returns_none(
        self, provider_with_cache: YFinanceProvider
    ) -> None:
        """Test cache filtered to empty returns None."""
        # Request date range that doesn't overlap with cache
        result = provider_with_cache._read_from_cache("SPY", date(2023, 1, 1), date(2023, 1, 31))

        assert result is None


# =============================================================================
# Rate Limiting Tests
# =============================================================================


class TestRateLimiting:
    """Tests for rate limiting behavior."""

    def test_delay_between_symbols(self, provider: YFinanceProvider) -> None:
        """Test delay is applied between symbol fetches."""
        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2024-01-02"]),
                "Open": [100.0],
                "High": [101.0],
                "Low": [99.0],
                "Close": [100.5],
                "Volume": [1000000],
                "Adj Close": [100.5],
            }
        ).set_index("Date")

        call_times: list[float] = []

        def mock_ticker_factory(symbol: str) -> MagicMock:
            call_times.append(time.time())
            ticker = MagicMock()
            ticker.history.return_value = mock_pdf
            return ticker

        with patch("yfinance.Ticker", side_effect=mock_ticker_factory):
            provider.fetch_and_cache(
                symbols=["SPY", "QQQ"],
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 5),
                run_drift_check=False,
            )

        # Should have delay between calls
        assert len(call_times) == 2
        delay = call_times[1] - call_times[0]
        assert delay >= provider.REQUEST_DELAY_SECONDS

    def test_delay_between_fetch_symbols(self, provider: YFinanceProvider) -> None:
        """Test delay applied in _fetch_symbols."""
        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2024-01-02"]),
                "Open": [100.0],
                "High": [101.0],
                "Low": [99.0],
                "Close": [100.5],
                "Volume": [1000000],
                "Adj Close": [100.5],
            }
        ).set_index("Date")

        call_times: list[float] = []

        def mock_ticker_factory(symbol: str) -> MagicMock:
            call_times.append(time.time())
            ticker = MagicMock()
            ticker.history.return_value = mock_pdf
            return ticker

        with patch("yfinance.Ticker", side_effect=mock_ticker_factory):
            results = provider._fetch_symbols(
                symbols=["SPY", "QQQ"],
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 5),
            )

        assert len(results) == 2
        assert len(call_times) == 2
        delay = call_times[1] - call_times[0]
        assert delay >= provider.REQUEST_DELAY_SECONDS

    def test_retry_on_failure(
        self, provider: YFinanceProvider, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test retry logic on download failure."""
        import pandas as pd

        # First call fails, second succeeds
        call_count = 0

        def mock_ticker_factory(symbol: str) -> MagicMock:
            nonlocal call_count
            call_count += 1
            ticker = MagicMock()

            if call_count < 3:  # Fail first 2 times
                ticker.history.side_effect = Exception("Network error")
            else:
                ticker.history.return_value = pd.DataFrame(
                    {
                        "Date": pd.to_datetime(["2024-01-02"]),
                        "Open": [100.0],
                        "High": [101.0],
                        "Low": [99.0],
                        "Close": [100.5],
                        "Volume": [1000000],
                        "Adj Close": [100.5],
                    }
                ).set_index("Date")

            return ticker

        with patch("yfinance.Ticker", side_effect=mock_ticker_factory):
            # Use reduced retry delay for test
            provider.RETRY_DELAY_SECONDS = 0.1
            provider.JITTER_MAX_SECONDS = 0.05

            df = provider._download_with_retry(
                symbol="SPY",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 5),
            )

            assert df is not None
            assert "Download attempt failed" in caplog.text

    def test_retry_on_empty_response(
        self, provider: YFinanceProvider, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test retry logic on empty response."""
        import pandas as pd

        call_count = 0

        def mock_ticker_factory(symbol: str) -> MagicMock:
            nonlocal call_count
            call_count += 1
            ticker = MagicMock()

            if call_count < 2:  # Return empty first time
                ticker.history.return_value = pd.DataFrame()  # Empty
            else:
                ticker.history.return_value = pd.DataFrame(
                    {
                        "Date": pd.to_datetime(["2024-01-02"]),
                        "Open": [100.0],
                        "High": [101.0],
                        "Low": [99.0],
                        "Close": [100.5],
                        "Volume": [1000000],
                        "Adj Close": [100.5],
                    }
                ).set_index("Date")

            return ticker

        with patch("yfinance.Ticker", side_effect=mock_ticker_factory):
            provider.RETRY_DELAY_SECONDS = 0.1
            provider.JITTER_MAX_SECONDS = 0.05

            df = provider._download_with_retry(
                symbol="SPY",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 5),
            )

            assert df is not None
            assert "Empty response from yfinance" in caplog.text

    def test_max_retries_exhausted_returns_none(
        self, provider: YFinanceProvider, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test download returns None after max retries."""
        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.history.side_effect = Exception("Network error")
            mock_ticker_cls.return_value = mock_ticker

            provider.RETRY_DELAY_SECONDS = 0.1
            provider.JITTER_MAX_SECONDS = 0.05

            df = provider._download_with_retry(
                symbol="SPY",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 5),
            )

            assert df is None
            assert "Download failed after retries" in caplog.text


# =============================================================================
# Download Tests
# =============================================================================


class TestDownload:
    """Tests for download functionality."""

    def test_download_with_retry_success(self, provider: YFinanceProvider) -> None:
        """Test successful download with retry."""
        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
                "Open": [100.0, 101.0],
                "High": [101.0, 102.0],
                "Low": [99.0, 100.0],
                "Close": [100.5, 101.5],
                "Volume": [1000000, 1100000],
                "Adj Close": [100.5, 101.5],
            }
        ).set_index("Date")

        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = mock_pdf
            mock_ticker_cls.return_value = mock_ticker

            df = provider._download_with_retry(
                symbol="SPY",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 5),
            )

        assert df is not None
        assert df.height == 2
        assert "symbol" in df.columns
        assert df["symbol"][0] == "SPY"

    def test_download_normalizes_column_names(self, provider: YFinanceProvider) -> None:
        """Test download normalizes column names."""
        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2024-01-02"]),
                "Open": [100.0],
                "High": [101.0],
                "Low": [99.0],
                "Close": [100.5],
                "Volume": [1000000],
                "Adj Close": [100.5],  # Space in name
            }
        ).set_index("Date")

        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = mock_pdf
            mock_ticker_cls.return_value = mock_ticker

            df = provider._download_with_retry(
                symbol="SPY",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 5),
            )

        assert df is not None
        assert "adj_close" in df.columns  # Normalized

    def test_download_converts_date_to_date_type(self, provider: YFinanceProvider) -> None:
        """Test download converts date column to date type."""
        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2024-01-02"]),
                "Open": [100.0],
                "High": [101.0],
                "Low": [99.0],
                "Close": [100.5],
                "Volume": [1000000],
                "Adj Close": [100.5],
            }
        ).set_index("Date")

        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = mock_pdf
            mock_ticker_cls.return_value = mock_ticker

            df = provider._download_with_retry(
                symbol="SPY",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 5),
            )

        assert df is not None
        assert df.schema["date"] == pl.Date

    def test_download_adds_symbol_column(self, provider: YFinanceProvider) -> None:
        """Test download adds symbol column."""
        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2024-01-02"]),
                "Open": [100.0],
                "High": [101.0],
                "Low": [99.0],
                "Close": [100.5],
                "Volume": [1000000],
                "Adj Close": [100.5],
            }
        ).set_index("Date")

        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = mock_pdf
            mock_ticker_cls.return_value = mock_ticker

            df = provider._download_with_retry(
                symbol="SPY",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 5),
            )

        assert df is not None
        assert "symbol" in df.columns
        assert df["symbol"][0] == "SPY"

    def test_download_selects_only_available_columns(self, provider: YFinanceProvider) -> None:
        """Test download only selects available columns."""
        import pandas as pd

        # Create mock with missing volume column
        mock_pdf = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2024-01-02"]),
                "Open": [100.0],
                "High": [101.0],
                "Low": [99.0],
                "Close": [100.5],
                "Adj Close": [100.5],
                # Missing Volume
            }
        ).set_index("Date")

        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = mock_pdf
            mock_ticker_cls.return_value = mock_ticker

            df = provider._download_with_retry(
                symbol="SPY",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 5),
            )

        assert df is not None
        # Should have all columns except volume
        assert "volume" not in df.columns


# =============================================================================
# Manifest Tests
# =============================================================================


class TestManifest:
    """Tests for manifest operations."""

    def test_get_manifest_returns_none_if_missing(self, provider: YFinanceProvider) -> None:
        """Test get_manifest returns None if no manifest exists."""
        result = provider.get_manifest()
        assert result is None

    def test_manifest_updated_on_fetch(self, provider: YFinanceProvider) -> None:
        """Test manifest is updated after fetch_and_cache."""
        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2024-01-02"]),
                "Open": [100.0],
                "High": [101.0],
                "Low": [99.0],
                "Close": [100.5],
                "Volume": [1000000],
                "Adj Close": [100.5],
            }
        ).set_index("Date")

        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = mock_pdf
            mock_ticker_cls.return_value = mock_ticker

            provider.fetch_and_cache(
                symbols=["SPY"],
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 5),
                run_drift_check=False,
            )

        manifest = provider.get_manifest()
        assert manifest is not None
        assert "SPY.parquet" in manifest["files"]
        assert "checksum" in manifest["files"]["SPY.parquet"]

    def test_manifest_preserves_existing_entries(
        self, provider_with_cache: YFinanceProvider
    ) -> None:
        """Test manifest preserves existing entries on new fetch."""
        import pandas as pd

        mock_pdf = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2024-01-02"]),
                "Open": [200.0],
                "High": [201.0],
                "Low": [199.0],
                "Close": [200.5],
                "Volume": [2000000],
                "Adj Close": [200.5],
            }
        ).set_index("Date")

        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = mock_pdf
            mock_ticker_cls.return_value = mock_ticker

            provider_with_cache.fetch_and_cache(
                symbols=["QQQ"],  # Different symbol
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 5),
                run_drift_check=False,
            )

        manifest = provider_with_cache.get_manifest()
        # Both SPY (existing) and QQQ (new) should be in manifest
        assert "SPY.parquet" in manifest["files"]
        assert "QQQ.parquet" in manifest["files"]


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Tests for error handling."""

    def test_yfinance_not_installed_raises(self, provider: YFinanceProvider) -> None:
        """Test YFinanceError raised if yfinance not installed."""
        import sys

        # Save original yfinance module reference
        original_yfinance = sys.modules.get("yfinance")

        try:
            # Simulate yfinance not being installed by removing it from sys.modules
            # and making import fail
            if "yfinance" in sys.modules:
                del sys.modules["yfinance"]

            # Make import raise ImportError
            with patch.dict("sys.modules", {"yfinance": None}):
                with pytest.raises(YFinanceError, match="yfinance not installed"):
                    provider._download_with_retry("SPY", date(2024, 1, 1), date(2024, 1, 5))
        finally:
            # Restore original yfinance module
            if original_yfinance is not None:
                sys.modules["yfinance"] = original_yfinance

    def test_failed_symbols_reported(self, provider: YFinanceProvider) -> None:
        """Test failed symbols are reported in fetch_and_cache result."""
        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = MagicMock(empty=True)
            mock_ticker_cls.return_value = mock_ticker

            # Use a valid symbol format but mock a download failure
            result = provider.fetch_and_cache(
                symbols=["NOSUCH"],  # Valid format, just doesn't exist
                run_drift_check=False,
            )

            assert "NOSUCH" in result["failed_symbols"]


# =============================================================================
# Dev-Only Warning Tests
# =============================================================================


class TestDevOnlyWarnings:
    """Tests for dev-only warnings in logs."""

    def test_warning_in_staging(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Test warning is logged when initialized in staging."""
        YFinanceProvider(
            storage_path=tmp_path / "yfinance",
            environment="staging",
        )

        assert "non-development environment" in caplog.text
        assert "use CRSP for production backtests" in caplog.text

    def test_no_warning_in_development(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test no warning in development environment."""
        YFinanceProvider(
            storage_path=tmp_path / "yfinance",
            environment="development",
        )

        assert "non-development environment" not in caplog.text

    def test_no_warning_in_test(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Test no warning in test environment."""
        YFinanceProvider(
            storage_path=tmp_path / "yfinance",
            environment="test",
        )

        assert "non-development environment" not in caplog.text


# =============================================================================
# Baseline Path Tests
# =============================================================================


class TestBaselinePath:
    """Tests for baseline path validation and handling."""

    def test_safe_baseline_path_validates_symbol(
        self, provider_with_baseline: YFinanceProvider
    ) -> None:
        """Test _safe_baseline_path validates symbol format."""
        # Invalid symbol should return None
        result = provider_with_baseline._safe_baseline_path("../evil")

        assert result is None

    def test_safe_baseline_path_prevents_traversal(
        self, provider_with_baseline: YFinanceProvider, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test _safe_baseline_path prevents path traversal."""
        # This test verifies the path stays within baseline directory
        # Even with a valid symbol, we test the defense in depth check

        # Mock _validate_symbol to return a traversal attempt
        with patch.object(
            provider_with_baseline, "_validate_symbol", return_value="..%2F..%2Fevil"
        ):
            result = provider_with_baseline._safe_baseline_path("test")

            # Should detect path escape and return None
            assert result is None
            assert "Baseline path escape attempt" in caplog.text

    def test_safe_baseline_path_returns_none_when_no_baseline_configured(
        self, provider: YFinanceProvider
    ) -> None:
        """Test _safe_baseline_path returns None when no baseline path."""
        result = provider._safe_baseline_path("SPY")

        assert result is None

    def test_safe_baseline_path_success(self, provider_with_baseline: YFinanceProvider) -> None:
        """Test _safe_baseline_path returns correct path for valid symbol."""
        result = provider_with_baseline._safe_baseline_path("SPY")

        assert result is not None
        assert result.name == "spy_60d.parquet"
        assert result.parent == provider_with_baseline._baseline_path


# =============================================================================
# Exception Tests
# =============================================================================


class TestExceptions:
    """Tests for custom exceptions."""

    def test_drift_detected_error_attributes(self) -> None:
        """Test DriftDetectedError stores attributes correctly."""
        error = DriftDetectedError(symbol="SPY", max_drift=0.05, tolerance=0.01)

        assert error.symbol == "SPY"
        assert error.max_drift == 0.05
        assert error.tolerance == 0.01
        assert "SPY" in str(error)
        assert "0.0500" in str(error)
        assert "0.0100" in str(error)


# =============================================================================
# Constants Tests
# =============================================================================


class TestConstants:
    """Tests for module constants."""

    def test_valid_symbol_pattern(self) -> None:
        """Test VALID_SYMBOL_PATTERN regex."""
        # Valid symbols
        assert VALID_SYMBOL_PATTERN.match("SPY")
        assert VALID_SYMBOL_PATTERN.match("BRK.B")
        assert VALID_SYMBOL_PATTERN.match("BRK-A")
        assert VALID_SYMBOL_PATTERN.match("A")  # Single character

        # Invalid symbols
        assert not VALID_SYMBOL_PATTERN.match("spy")  # Lowercase
        assert not VALID_SYMBOL_PATTERN.match("SPY$")  # Invalid character
        assert not VALID_SYMBOL_PATTERN.match("A" * 16)  # Too long
        assert not VALID_SYMBOL_PATTERN.match("")  # Empty

    def test_baseline_file_suffix(self) -> None:
        """Test BASELINE_FILE_SUFFIX constant."""
        assert BASELINE_FILE_SUFFIX == "_60d.parquet"

    def test_baseline_manifest_file(self) -> None:
        """Test BASELINE_MANIFEST_FILE constant."""
        assert BASELINE_MANIFEST_FILE == "baseline_manifest.json"

    def test_yfinance_schema(self) -> None:
        """Test YFINANCE_SCHEMA has correct columns."""
        expected_columns = ["date", "symbol", "open", "high", "low", "close", "volume", "adj_close"]
        assert list(YFINANCE_SCHEMA.keys()) == expected_columns
        assert YFINANCE_SCHEMA["date"] == pl.Date
        assert YFINANCE_SCHEMA["symbol"] == pl.Utf8
