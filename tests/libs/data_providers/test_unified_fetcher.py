"""Tests for UnifiedDataFetcher.

Comprehensive test suite covering:
- FetcherConfig from environment
- Provider selection logic (AUTO vs explicit)
- Production safety (fallback disabled)
- Universe operations require CRSP
- Usage metrics logging
- Configuration validation
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from libs.data_providers.protocols import (
    UNIFIED_COLUMNS,
    ConfigurationError,
    ProductionProviderRequiredError,
    ProviderNotSupportedError,
    ProviderUnavailableError,
)
from libs.data_providers.unified_fetcher import (
    FetcherConfig,
    ProviderType,
    UnifiedDataFetcher,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture()
def mock_yfinance_provider() -> MagicMock:
    """Create mock YFinanceProvider."""
    provider = MagicMock()
    provider.get_daily_prices.return_value = pl.DataFrame(
        {
            "date": [date(2024, 1, 2), date(2024, 1, 3)],
            "symbol": ["AAPL", "AAPL"],
            "open": [180.0, 182.0],
            "high": [185.0, 186.0],
            "low": [178.0, 180.0],
            "close": [183.0, 184.5],
            "volume": [50000000.0, 48000000.0],
            "adj_close": [183.0, 184.5],
        }
    )
    return provider


@pytest.fixture()
def mock_crsp_provider() -> MagicMock:
    """Create mock CRSPLocalProvider."""
    provider = MagicMock()
    provider.get_daily_prices.return_value = pl.DataFrame(
        {
            "date": [date(2024, 1, 2), date(2024, 1, 3)],
            "ticker": ["AAPL", "AAPL"],
            "prc": [183.0, 184.5],
            "vol": [50000000.0, 48000000.0],
            "ret": [0.015, 0.008],
        }
    )
    provider.get_universe.return_value = pl.DataFrame(
        {
            "ticker": ["AAPL", "MSFT", "GOOGL"],
        }
    )
    return provider


@pytest.fixture()
def dev_config() -> FetcherConfig:
    """Create development config."""
    return FetcherConfig(
        provider=ProviderType.AUTO,
        environment="development",
        fallback_enabled=True,
    )


@pytest.fixture()
def prod_config() -> FetcherConfig:
    """Create production config."""
    return FetcherConfig(
        provider=ProviderType.AUTO,
        environment="production",
        fallback_enabled=True,  # Will be forced False
    )


# =============================================================================
# FetcherConfig Tests
# =============================================================================


class TestFetcherConfigDefaults:
    """Test FetcherConfig default values."""

    def test_default_provider_is_auto(self) -> None:
        """Default provider is AUTO."""
        config = FetcherConfig()
        assert config.provider == ProviderType.AUTO

    def test_default_environment_is_development(self) -> None:
        """Default environment is development."""
        config = FetcherConfig()
        assert config.environment == "development"

    def test_default_fallback_enabled(self) -> None:
        """Default fallback is enabled."""
        config = FetcherConfig()
        assert config.fallback_enabled is True

    def test_default_paths_are_none(self) -> None:
        """Default paths are None."""
        config = FetcherConfig()
        assert config.yfinance_storage_path is None
        assert config.crsp_storage_path is None
        assert config.manifest_path is None


class TestFetcherConfigProductionSafety:
    """Test production safety rules."""

    def test_fallback_forced_false_in_production(self) -> None:
        """Fallback is forced False when environment=production."""
        config = FetcherConfig(
            environment="production",
            fallback_enabled=True,
        )
        assert config.fallback_enabled is False

    def test_fallback_forced_false_production_uppercase(self) -> None:
        """Fallback forced False works with uppercase PRODUCTION."""
        config = FetcherConfig(
            environment="PRODUCTION",
            fallback_enabled=True,
        )
        assert config.fallback_enabled is False
        assert config.environment == "production"

    def test_fallback_allowed_in_development(self) -> None:
        """Fallback allowed in development."""
        config = FetcherConfig(
            environment="development",
            fallback_enabled=True,
        )
        assert config.fallback_enabled is True

    def test_fallback_allowed_in_test(self) -> None:
        """Fallback allowed in test."""
        config = FetcherConfig(
            environment="test",
            fallback_enabled=True,
        )
        assert config.fallback_enabled is True

    def test_unknown_environment_defaults_to_production(self) -> None:
        """Unknown environment values default to production for safety.

        CRITICAL: This prevents typos like 'prod' from bypassing safety checks.
        """
        config = FetcherConfig(
            environment="prod",  # Typo - should be "production"
            fallback_enabled=True,
        )
        # Unknown env defaults to production
        assert config.environment == "production"
        # And fallback is forced False
        assert config.fallback_enabled is False

    def test_unknown_environment_various_typos(self) -> None:
        """Various typos all default to production."""
        for typo in ["prod", "Prod", "PROD", "prd", "live", "main", "invalid"]:
            config = FetcherConfig(environment=typo, fallback_enabled=True)
            assert config.environment == "production", f"'{typo}' should default to production"
            assert config.fallback_enabled is False, f"'{typo}' should have fallback=False"


class TestFetcherConfigFromEnv:
    """Test FetcherConfig.from_env()."""

    def test_from_env_defaults_no_vars(self) -> None:
        """Default config values when no env vars set."""
        with patch.dict(os.environ, {}, clear=True):
            config = FetcherConfig.from_env()

        assert config.provider == ProviderType.AUTO
        assert config.environment == "development"
        assert config.fallback_enabled is True
        assert config.yfinance_storage_path is None

    def test_from_env_all_vars(self, tmp_path: Path) -> None:
        """Config loads all environment variables."""
        yfinance_path = tmp_path / "yfinance"
        crsp_path = tmp_path / "crsp"
        manifest_path = tmp_path / "manifests"

        env = {
            "DATA_PROVIDER": "crsp",
            "ENVIRONMENT": "staging",
            "YFINANCE_STORAGE_PATH": str(yfinance_path),
            "CRSP_STORAGE_PATH": str(crsp_path),
            "MANIFEST_PATH": str(manifest_path),
            "FALLBACK_ENABLED": "false",
        }

        with patch.dict(os.environ, env, clear=True):
            config = FetcherConfig.from_env()

        assert config.provider == ProviderType.CRSP
        assert config.environment == "staging"
        assert config.fallback_enabled is False
        assert config.yfinance_storage_path == yfinance_path
        assert config.crsp_storage_path == crsp_path
        assert config.manifest_path == manifest_path

    def test_from_env_invalid_provider_defaults_to_auto(self) -> None:
        """Invalid DATA_PROVIDER defaults to AUTO with warning."""
        with patch.dict(os.environ, {"DATA_PROVIDER": "invalid"}, clear=True):
            config = FetcherConfig.from_env()

        assert config.provider == ProviderType.AUTO

    def test_from_env_fallback_true_variants(self) -> None:
        """FALLBACK_ENABLED accepts true, 1, yes."""
        for value in ["true", "1", "yes", "TRUE", "True"]:
            with patch.dict(
                os.environ, {"FALLBACK_ENABLED": value, "ENVIRONMENT": "development"}, clear=True
            ):
                config = FetcherConfig.from_env()
            assert config.fallback_enabled is True


class TestFetcherConfigValidatePaths:
    """Test FetcherConfig.validate_paths()."""

    def test_validate_paths_success(self, tmp_path: Path) -> None:
        """validate_paths succeeds when all configured paths exist."""
        yf_path = tmp_path / "yfinance"
        yf_path.mkdir()

        config = FetcherConfig(yfinance_storage_path=yf_path)
        config.validate_paths()  # Should not raise

    def test_validate_paths_missing_dir(self, tmp_path: Path) -> None:
        """validate_paths raises ConfigurationError for missing directory."""
        missing_path = tmp_path / "nonexistent"

        config = FetcherConfig(yfinance_storage_path=missing_path)

        with pytest.raises(ConfigurationError, match="does not exist"):
            config.validate_paths()

    def test_validate_paths_not_directory(self, tmp_path: Path) -> None:
        """validate_paths raises ConfigurationError when path is not a directory."""
        file_path = tmp_path / "file.txt"
        file_path.write_text("test")

        config = FetcherConfig(yfinance_storage_path=file_path)

        with pytest.raises(ConfigurationError, match="not a directory"):
            config.validate_paths()

    def test_validate_paths_skips_none(self) -> None:
        """validate_paths skips validation for None paths."""
        config = FetcherConfig(
            yfinance_storage_path=None,
            crsp_storage_path=None,
            manifest_path=None,
        )
        config.validate_paths()  # Should not raise

    def test_validate_paths_not_readable(self, tmp_path: Path) -> None:
        """validate_paths raises ConfigurationError for unreadable directory."""
        import os

        unreadable_path = tmp_path / "unreadable"
        unreadable_path.mkdir()

        # Remove read and execute permissions
        original_mode = unreadable_path.stat().st_mode
        os.chmod(unreadable_path, 0o000)

        try:
            config = FetcherConfig(yfinance_storage_path=unreadable_path)

            with pytest.raises(ConfigurationError, match="not readable/accessible"):
                config.validate_paths()
        finally:
            # Restore permissions for cleanup
            os.chmod(unreadable_path, original_mode)


# =============================================================================
# Provider Selection Tests
# =============================================================================


class TestProviderSelectionAuto:
    """Test AUTO mode provider selection."""

    def test_auto_returns_crsp_when_available(
        self,
        dev_config: FetcherConfig,
        mock_yfinance_provider: MagicMock,
        mock_crsp_provider: MagicMock,
    ) -> None:
        """In AUTO mode, prefer CRSP when available."""
        fetcher = UnifiedDataFetcher(
            config=dev_config,
            yfinance_provider=mock_yfinance_provider,
            crsp_provider=mock_crsp_provider,
        )

        provider = fetcher.get_active_provider()
        assert provider == "crsp"

    def test_auto_fallback_to_yfinance_in_development(
        self,
        dev_config: FetcherConfig,
        mock_yfinance_provider: MagicMock,
    ) -> None:
        """In development with AUTO, fallback to yfinance if CRSP unavailable."""
        fetcher = UnifiedDataFetcher(
            config=dev_config,
            yfinance_provider=mock_yfinance_provider,
            crsp_provider=None,
        )

        provider = fetcher.get_active_provider()
        assert provider == "yfinance"

    def test_auto_error_when_crsp_missing_in_production(
        self,
        prod_config: FetcherConfig,
        mock_yfinance_provider: MagicMock,
    ) -> None:
        """In production with AUTO and no CRSP, raises ProductionProviderRequiredError."""
        fetcher = UnifiedDataFetcher(
            config=prod_config,
            yfinance_provider=mock_yfinance_provider,
            crsp_provider=None,
        )

        with pytest.raises(ProductionProviderRequiredError):
            fetcher.get_active_provider()

    def test_auto_returns_crsp_in_production(
        self,
        prod_config: FetcherConfig,
        mock_crsp_provider: MagicMock,
    ) -> None:
        """In production with AUTO, CRSP is used."""
        fetcher = UnifiedDataFetcher(
            config=prod_config,
            yfinance_provider=None,
            crsp_provider=mock_crsp_provider,
        )

        provider = fetcher.get_active_provider()
        assert provider == "crsp"


class TestProviderSelectionExplicit:
    """Test explicit provider selection."""

    def test_explicit_yfinance(
        self,
        mock_yfinance_provider: MagicMock,
        mock_crsp_provider: MagicMock,
    ) -> None:
        """Explicit YFINANCE selection uses yfinance."""
        config = FetcherConfig(
            provider=ProviderType.YFINANCE,
            environment="development",
        )
        fetcher = UnifiedDataFetcher(
            config=config,
            yfinance_provider=mock_yfinance_provider,
            crsp_provider=mock_crsp_provider,
        )

        provider = fetcher.get_active_provider()
        assert provider == "yfinance"

    def test_explicit_crsp(
        self,
        mock_yfinance_provider: MagicMock,
        mock_crsp_provider: MagicMock,
    ) -> None:
        """Explicit CRSP selection uses CRSP."""
        config = FetcherConfig(
            provider=ProviderType.CRSP,
            environment="development",
        )
        fetcher = UnifiedDataFetcher(
            config=config,
            yfinance_provider=mock_yfinance_provider,
            crsp_provider=mock_crsp_provider,
        )

        provider = fetcher.get_active_provider()
        assert provider == "crsp"

    def test_explicit_provider_unavailable_error(
        self,
        mock_yfinance_provider: MagicMock,
    ) -> None:
        """Explicit provider not available raises ProviderUnavailableError."""
        config = FetcherConfig(
            provider=ProviderType.CRSP,
            environment="development",
        )
        fetcher = UnifiedDataFetcher(
            config=config,
            yfinance_provider=mock_yfinance_provider,
            crsp_provider=None,
        )

        with pytest.raises(ProviderUnavailableError) as exc_info:
            fetcher.get_active_provider()

        assert exc_info.value.provider_name == "crsp"
        assert "yfinance" in exc_info.value.available_providers

    def test_explicit_provider_no_fallback(
        self,
        mock_yfinance_provider: MagicMock,
    ) -> None:
        """Explicit provider selection doesn't fallback."""
        config = FetcherConfig(
            provider=ProviderType.CRSP,
            environment="development",
            fallback_enabled=True,
        )
        fetcher = UnifiedDataFetcher(
            config=config,
            yfinance_provider=mock_yfinance_provider,
            crsp_provider=None,
        )

        # Even with fallback enabled, explicit selection should not fallback
        with pytest.raises(ProviderUnavailableError):
            fetcher.get_active_provider()

    def test_explicit_yfinance_in_production_raises(
        self,
        mock_yfinance_provider: MagicMock,
        mock_crsp_provider: MagicMock,
    ) -> None:
        """Explicit yfinance in production raises ProductionProviderRequiredError.

        CRITICAL: This test ensures that even with explicit provider selection,
        non-production-ready providers are blocked in production environment.
        """
        config = FetcherConfig(
            provider=ProviderType.YFINANCE,
            environment="production",
        )
        fetcher = UnifiedDataFetcher(
            config=config,
            yfinance_provider=mock_yfinance_provider,
            crsp_provider=mock_crsp_provider,
        )

        with pytest.raises(ProductionProviderRequiredError) as exc_info:
            fetcher.get_active_provider()

        assert "yfinance" in str(exc_info.value)
        assert "not suitable for production" in str(exc_info.value)


class TestFallbackBehavior:
    """Test fallback behavior."""

    def test_fallback_disabled_raises_when_primary_unavailable(
        self,
        mock_yfinance_provider: MagicMock,
    ) -> None:
        """With fallback=False, error if primary provider unavailable."""
        config = FetcherConfig(
            provider=ProviderType.AUTO,
            environment="development",
            fallback_enabled=False,
        )
        fetcher = UnifiedDataFetcher(
            config=config,
            yfinance_provider=mock_yfinance_provider,
            crsp_provider=None,
        )

        with pytest.raises(ProviderUnavailableError):
            fetcher.get_active_provider()

    def test_no_providers_available(self) -> None:
        """Error when no providers available."""
        config = FetcherConfig(
            provider=ProviderType.AUTO,
            environment="development",
            fallback_enabled=False,
        )
        fetcher = UnifiedDataFetcher(
            config=config,
            yfinance_provider=None,
            crsp_provider=None,
        )

        with pytest.raises(ProviderUnavailableError):
            fetcher.get_active_provider()


# =============================================================================
# Universe Operation Tests
# =============================================================================


class TestUniverseOperations:
    """Test get_universe operations."""

    def test_get_universe_with_crsp(
        self,
        dev_config: FetcherConfig,
        mock_crsp_provider: MagicMock,
    ) -> None:
        """get_universe works with CRSP provider."""
        fetcher = UnifiedDataFetcher(
            config=dev_config,
            yfinance_provider=None,
            crsp_provider=mock_crsp_provider,
        )

        symbols = fetcher.get_universe(date(2024, 1, 15))
        assert symbols == ["AAPL", "MSFT", "GOOGL"]

    def test_get_universe_with_yfinance_raises(
        self,
        dev_config: FetcherConfig,
        mock_yfinance_provider: MagicMock,
    ) -> None:
        """get_universe with only yfinance raises ProviderNotSupportedError."""
        fetcher = UnifiedDataFetcher(
            config=dev_config,
            yfinance_provider=mock_yfinance_provider,
            crsp_provider=None,
        )

        with pytest.raises(ProviderNotSupportedError):
            fetcher.get_universe(date(2024, 1, 15))

    def test_auto_mode_universe_requires_crsp(
        self,
        dev_config: FetcherConfig,
        mock_yfinance_provider: MagicMock,
    ) -> None:
        """In AUTO mode, universe operation requires CRSP even if yfinance available."""
        fetcher = UnifiedDataFetcher(
            config=dev_config,
            yfinance_provider=mock_yfinance_provider,
            crsp_provider=None,
        )

        with pytest.raises(ProviderNotSupportedError):
            fetcher.get_universe(date(2024, 1, 15))

    def test_explicit_yfinance_universe_raises(
        self,
        mock_yfinance_provider: MagicMock,
        mock_crsp_provider: MagicMock,
    ) -> None:
        """Explicit yfinance selection for universe raises ProviderNotSupportedError."""
        config = FetcherConfig(
            provider=ProviderType.YFINANCE,
            environment="development",
        )
        fetcher = UnifiedDataFetcher(
            config=config,
            yfinance_provider=mock_yfinance_provider,
            crsp_provider=mock_crsp_provider,
        )

        with pytest.raises(ProviderNotSupportedError):
            fetcher.get_universe(date(2024, 1, 15))


# =============================================================================
# Data Fetching Tests
# =============================================================================


class TestGetDailyPrices:
    """Test get_daily_prices operations."""

    def test_get_daily_prices_returns_data(
        self,
        dev_config: FetcherConfig,
        mock_yfinance_provider: MagicMock,
    ) -> None:
        """get_daily_prices returns DataFrame with data."""
        fetcher = UnifiedDataFetcher(
            config=dev_config,
            yfinance_provider=mock_yfinance_provider,
            crsp_provider=None,
        )

        df = fetcher.get_daily_prices(
            ["AAPL"],
            date(2024, 1, 1),
            date(2024, 1, 31),
        )

        assert not df.is_empty()
        assert list(df.columns) == UNIFIED_COLUMNS

    def test_get_daily_prices_empty_symbols_raises(
        self,
        dev_config: FetcherConfig,
        mock_yfinance_provider: MagicMock,
    ) -> None:
        """get_daily_prices with empty symbols raises ValueError."""
        fetcher = UnifiedDataFetcher(
            config=dev_config,
            yfinance_provider=mock_yfinance_provider,
            crsp_provider=None,
        )

        with pytest.raises(ValueError, match="symbols list cannot be empty"):
            fetcher.get_daily_prices(
                [],
                date(2024, 1, 1),
                date(2024, 1, 31),
            )


# =============================================================================
# Provider Availability Tests
# =============================================================================


class TestIsAvailable:
    """Test is_available method."""

    def test_is_available_returns_true_for_configured(
        self,
        dev_config: FetcherConfig,
        mock_yfinance_provider: MagicMock,
        mock_crsp_provider: MagicMock,
    ) -> None:
        """is_available returns True for configured providers."""
        fetcher = UnifiedDataFetcher(
            config=dev_config,
            yfinance_provider=mock_yfinance_provider,
            crsp_provider=mock_crsp_provider,
        )

        assert fetcher.is_available(ProviderType.YFINANCE) is True
        assert fetcher.is_available(ProviderType.CRSP) is True

    def test_is_available_returns_false_for_unconfigured(
        self,
        dev_config: FetcherConfig,
        mock_yfinance_provider: MagicMock,
    ) -> None:
        """is_available returns False for unconfigured providers."""
        fetcher = UnifiedDataFetcher(
            config=dev_config,
            yfinance_provider=mock_yfinance_provider,
            crsp_provider=None,
        )

        assert fetcher.is_available(ProviderType.YFINANCE) is True
        assert fetcher.is_available(ProviderType.CRSP) is False


# =============================================================================
# Logging Tests
# =============================================================================


class TestUsageLogging:
    """Test usage metrics logging."""

    def test_get_daily_prices_logs_usage(
        self,
        dev_config: FetcherConfig,
        mock_yfinance_provider: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """get_daily_prices logs usage metrics."""
        import logging

        caplog.set_level(logging.INFO)

        fetcher = UnifiedDataFetcher(
            config=dev_config,
            yfinance_provider=mock_yfinance_provider,
            crsp_provider=None,
        )

        fetcher.get_daily_prices(["AAPL", "MSFT"], date(2024, 1, 1), date(2024, 1, 31))

        assert "Data fetch operation" in caplog.text

    def test_get_universe_logs_usage(
        self,
        dev_config: FetcherConfig,
        mock_crsp_provider: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """get_universe logs usage metrics."""
        import logging

        caplog.set_level(logging.INFO)

        fetcher = UnifiedDataFetcher(
            config=dev_config,
            yfinance_provider=None,
            crsp_provider=mock_crsp_provider,
        )

        fetcher.get_universe(date(2024, 1, 15))

        assert "Data fetch operation" in caplog.text


# =============================================================================
# ProviderType Enum Tests
# =============================================================================


class TestProviderTypeEnum:
    """Test ProviderType enum."""

    def test_provider_type_values(self) -> None:
        """ProviderType has expected values."""
        assert ProviderType.YFINANCE.value == "yfinance"
        assert ProviderType.CRSP.value == "crsp"
        assert ProviderType.AUTO.value == "auto"

    def test_provider_type_from_string(self) -> None:
        """ProviderType can be created from string."""
        assert ProviderType("yfinance") == ProviderType.YFINANCE
        assert ProviderType("crsp") == ProviderType.CRSP
        assert ProviderType("auto") == ProviderType.AUTO
