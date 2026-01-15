"""Tests for YFinanceProvider.

Comprehensive test suite covering:
- Basic functionality (single/bulk symbol fetch)
- Production gating (matrix tests for env × CRSP × flag)
- Drift detection (baseline comparison)
- Atomic writes and quarantine
- Cache hit/miss behavior
- Rate limiting
- Error handling
"""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from libs.data.data_providers.yfinance_provider import (
    ProductionGateError,
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
            "close": [473.00, 474.80, 475.50],  # Matches mock_ohlcv_data
        }
    )


@pytest.fixture()
def mock_baseline_data_with_drift() -> pl.DataFrame:
    """Create mock baseline data with significant drift (>1%)."""
    return pl.DataFrame(
        {
            "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
            "close": [473.00, 474.80, 490.00],  # Last price drifts >1%
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

    def test_verify_data_with_valid_cache(self, provider_with_cache: YFinanceProvider) -> None:
        """Test verify_data returns True for valid cache."""
        results = provider_with_cache.verify_data()

        assert "SPY.parquet" in results
        assert results["SPY.parquet"] is True

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
                "close": [400.0, 401.0],
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
                "close": [0.0, 0.0, 0.0],  # All zeros
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

    def test_checksum_mismatch_detected(self, provider_with_cache: YFinanceProvider) -> None:
        """Test checksum mismatch is detected by verify_data."""
        # Corrupt the cache file
        cache_path = provider_with_cache._daily_dir / "SPY.parquet"
        with open(cache_path, "ab") as f:
            f.write(b"corruption")

        results = provider_with_cache.verify_data()

        assert results["SPY.parquet"] is False


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
        from datetime import date

        from libs.data.data_providers.yfinance_provider import YFinanceError

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
