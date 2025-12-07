"""Unified Data Fetcher.

This module provides a unified interface for fetching market data from
different providers (yfinance for development, CRSP for production).

Classes:
    ProviderType: Enum of available provider types.
    FetcherConfig: Configuration for UnifiedDataFetcher.
    UnifiedDataFetcher: Main entry point for data access.

Example:
    # Using environment configuration
    config = FetcherConfig.from_env()
    fetcher = UnifiedDataFetcher(config, yfinance_provider=yf_provider)

    # Fetch prices
    df = fetcher.get_daily_prices(
        symbols=["AAPL", "MSFT"],
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
    )

See Also:
    docs/CONCEPTS/unified-data-fetcher.md for usage examples.
    docs/ADRs/ADR-016-data-provider-protocol.md for design decisions.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

from libs.data_providers.protocols import (
    ConfigurationError,
    CRSPDataProviderAdapter,
    DataProvider,
    ProductionProviderRequiredError,
    ProviderNotSupportedError,
    ProviderUnavailableError,
    YFinanceDataProviderAdapter,
)

if TYPE_CHECKING:
    from libs.data_providers.crsp_local_provider import CRSPLocalProvider
    from libs.data_providers.yfinance_provider import YFinanceProvider

logger = logging.getLogger(__name__)


class ProviderType(str, Enum):
    """Available data provider types.

    Values:
        YFINANCE: Free data for development (NOT for production).
        CRSP: Production-ready academic data from WRDS.
        AUTO: Automatically select based on environment and availability.
    """

    YFINANCE = "yfinance"
    CRSP = "crsp"
    AUTO = "auto"


# Valid environment values for FetcherConfig
VALID_ENVIRONMENTS = frozenset({"development", "test", "staging", "production"})


@dataclass
class FetcherConfig:
    """Configuration for UnifiedDataFetcher.

    Attributes:
        provider: Which provider to use (AUTO, YFINANCE, or CRSP).
        environment: Current environment (development, test, staging, production).
        yfinance_storage_path: Path to yfinance cache directory.
        crsp_storage_path: Path to CRSP data directory.
        manifest_path: Path to data manifests directory.
        fallback_enabled: Whether to fallback to yfinance if CRSP unavailable.
            CRITICAL: Forced to False in production environment.

    Environment Variables:
        DATA_PROVIDER: auto|yfinance|crsp (default: auto)
        ENVIRONMENT: development|test|staging|production (default: development)
        YFINANCE_STORAGE_PATH: Path to yfinance cache
        CRSP_STORAGE_PATH: Path to CRSP data
        MANIFEST_PATH: Path to data manifests
        FALLBACK_ENABLED: true|false (ignored in production)
    """

    provider: ProviderType = ProviderType.AUTO
    environment: str = "development"
    yfinance_storage_path: Path | None = None
    crsp_storage_path: Path | None = None
    manifest_path: Path | None = None
    fallback_enabled: bool = True

    def __post_init__(self) -> None:
        """Enforce production safety rules.

        CRITICAL: Unknown environment values default to 'production' (safest).
        This prevents typos like 'prod' or 'PROD' from bypassing safety checks.
        """
        # Normalize environment to lowercase
        self.environment = self.environment.lower()

        # CRITICAL: Unknown environments default to production (safest behavior)
        if self.environment not in VALID_ENVIRONMENTS:
            logger.warning(
                "Unknown environment '%s' defaulting to 'production' for safety. "
                "Valid values: %s",
                self.environment,
                sorted(VALID_ENVIRONMENTS),
            )
            self.environment = "production"

        # CRITICAL: Fallback ALWAYS disabled in production
        if self.environment == "production":
            self.fallback_enabled = False

    def validate_paths(self) -> None:
        """Validate configured storage paths exist and are readable.

        Raises:
            ConfigurationError: If any configured path is invalid.

        Validation Rules:
            1. If yfinance_storage_path is set, it must exist and be a directory.
            2. If crsp_storage_path is set, it must exist and be a directory.
            3. If manifest_path is set, it must exist and be a directory.
            4. Paths are only validated if they are configured (None = skip).
        """
        paths_to_check = [
            ("yfinance_storage_path", self.yfinance_storage_path),
            ("crsp_storage_path", self.crsp_storage_path),
            ("manifest_path", self.manifest_path),
        ]

        for name, path in paths_to_check:
            if path is not None:
                if not path.exists():
                    raise ConfigurationError(f"{name} does not exist: {path}")
                if not path.is_dir():
                    raise ConfigurationError(f"{name} is not a directory: {path}")
                # Check read and execute permissions (needed to list/read directory contents)
                if not os.access(path, os.R_OK | os.X_OK):
                    raise ConfigurationError(
                        f"{name} is not readable/accessible: {path}"
                    )

    @classmethod
    def from_env(cls) -> FetcherConfig:
        """Load config from environment variables.

        Environment Variables:
            DATA_PROVIDER: auto|yfinance|crsp (default: auto)
            ENVIRONMENT: development|test|staging|production (default: development)
            YFINANCE_STORAGE_PATH: Path to yfinance cache
            CRSP_STORAGE_PATH: Path to CRSP data
            MANIFEST_PATH: Path to data manifests
            FALLBACK_ENABLED: true|false (ignored in production)

        Note:
            Paths are validated lazily when validate_paths() is called,
            not during config construction.

        Returns:
            FetcherConfig instance with values from environment.
        """
        env = os.getenv("ENVIRONMENT", "development").lower()

        # Parse provider type
        provider_str = os.getenv("DATA_PROVIDER", "auto").lower()
        try:
            provider = ProviderType(provider_str)
        except ValueError:
            logger.warning(
                "Invalid DATA_PROVIDER value, using AUTO",
                extra={"value": provider_str},
            )
            provider = ProviderType.AUTO

        # Parse fallback, but will be overridden in __post_init__ for prod
        fallback_str = os.getenv("FALLBACK_ENABLED", "true").lower()
        fallback = fallback_str in ("true", "1", "yes")

        # Parse paths
        yfinance_path = None
        if p := os.getenv("YFINANCE_STORAGE_PATH"):
            yfinance_path = Path(p)

        crsp_path = None
        if p := os.getenv("CRSP_STORAGE_PATH"):
            crsp_path = Path(p)

        manifest_path = None
        if p := os.getenv("MANIFEST_PATH"):
            manifest_path = Path(p)

        return cls(
            provider=provider,
            environment=env,
            yfinance_storage_path=yfinance_path,
            crsp_storage_path=crsp_path,
            manifest_path=manifest_path,
            fallback_enabled=fallback,
        )


class UnifiedDataFetcher:
    """Unified interface for fetching market data.

    Provides a single entry point for data access, abstracting the
    underlying provider (yfinance, CRSP, etc.).

    Provider Selection Rules (EXPLICIT):

    1. AUTO mode:
       - Production: CRSP required, NO fallback, error if unavailable
       - Development/Test: CRSP preferred, fallback to yfinance if enabled

    2. Explicit mode (YFINANCE or CRSP):
       - Use specified provider
       - Error if unavailable (no fallback)

    3. Universe operations:
       - ALWAYS require supports_universe=True provider
       - yfinance cannot serve universe (raises ProviderNotSupportedError)

    Example:
        # Create fetcher with yfinance for development
        config = FetcherConfig(environment="development")
        fetcher = UnifiedDataFetcher(
            config,
            yfinance_provider=YFinanceProvider(storage_path=Path("data/yfinance")),
        )

        # Fetch prices - uses best available provider
        df = fetcher.get_daily_prices(
            symbols=["AAPL", "MSFT"],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )
    """

    def __init__(
        self,
        config: FetcherConfig,
        yfinance_provider: YFinanceProvider | None = None,
        crsp_provider: CRSPLocalProvider | None = None,
    ) -> None:
        """Initialize UnifiedDataFetcher.

        Args:
            config: Fetcher configuration.
            yfinance_provider: Optional YFinanceProvider instance.
            crsp_provider: Optional CRSPLocalProvider instance.

        Note:
            At least one provider should be supplied for the fetcher to work.
            The fetcher will error on data access if no providers are available.
        """
        self._config = config
        self._adapters: dict[ProviderType, DataProvider] = {}

        # Initialize available adapters
        if yfinance_provider is not None:
            self._adapters[ProviderType.YFINANCE] = YFinanceDataProviderAdapter(
                yfinance_provider
            )
        if crsp_provider is not None:
            self._adapters[ProviderType.CRSP] = CRSPDataProviderAdapter(crsp_provider)

        logger.info(
            "UnifiedDataFetcher initialized",
            extra={
                "available_providers": [p.value for p in self._adapters.keys()],
                "config_provider": config.provider.value,
                "environment": config.environment,
                "fallback_enabled": config.fallback_enabled,
            },
        )

    def _select_provider(self, require_universe: bool = False) -> DataProvider:
        """Select provider based on config and availability.

        Args:
            require_universe: If True, only return providers supporting universe.

        Returns:
            Selected DataProvider adapter.

        Raises:
            ProviderUnavailableError: If requested provider not available.
            ProductionProviderRequiredError: If production requires CRSP but unavailable.
            ProviderNotSupportedError: If require_universe but no provider supports it.
        """
        # Handle explicit provider selection
        if self._config.provider != ProviderType.AUTO:
            provider = self._adapters.get(self._config.provider)
            if provider is None:
                raise ProviderUnavailableError(
                    f"Requested provider '{self._config.provider.value}' is not available. "
                    f"Available: {[p.value for p in self._adapters.keys()]}",
                    provider_name=self._config.provider.value,
                    available_providers=[p.value for p in self._adapters.keys()],
                )
            # CRITICAL: Block non-production-ready providers in production
            if (
                self._config.environment == "production"
                and not provider.is_production_ready
            ):
                raise ProductionProviderRequiredError(
                    f"Production environment requires a production-ready provider. "
                    f"'{provider.name}' is not suitable for production. "
                    f"Use CRSP or change environment."
                )
            if require_universe and not provider.supports_universe:
                raise ProviderNotSupportedError(
                    f"Provider '{provider.name}' does not support universe queries.",
                    provider_name=provider.name,
                    operation="get_universe",
                )
            return provider

        # AUTO mode selection
        is_production = self._config.environment == "production"

        # Production: CRSP required, NO fallback
        if is_production:
            crsp = self._adapters.get(ProviderType.CRSP)
            if crsp is None:
                raise ProductionProviderRequiredError(
                    "Production environment requires CRSP provider but it is not available. "
                    "Configure CRSP data source or change environment."
                )
            return crsp

        # Development/Test: prefer CRSP, fallback to yfinance if enabled
        if ProviderType.CRSP in self._adapters:
            return self._adapters[ProviderType.CRSP]

        if self._config.fallback_enabled and ProviderType.YFINANCE in self._adapters:
            yf = self._adapters[ProviderType.YFINANCE]
            if require_universe:
                raise ProviderNotSupportedError(
                    "Universe queries require CRSP provider. "
                    "yfinance does not support universe operations.",
                    provider_name="yfinance",
                    operation="get_universe",
                )
            logger.warning(
                "Using yfinance fallback - NOT suitable for production",
                extra={"environment": self._config.environment},
            )
            return yf

        raise ProviderUnavailableError(
            "No data provider available. Configure CRSP or enable yfinance fallback.",
            available_providers=[p.value for p in self._adapters.keys()],
        )

    def get_daily_prices(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date,
    ) -> pl.DataFrame:
        """Fetch daily prices using selected provider.

        Args:
            symbols: List of ticker symbols to fetch.
            start_date: Start of date range (inclusive).
            end_date: End of date range (inclusive).

        Returns:
            DataFrame with unified schema columns.

        Raises:
            ValueError: If symbols list is empty.
            ProviderUnavailableError: If no provider available.
            ProductionProviderRequiredError: If production and CRSP unavailable.
        """
        provider = self._select_provider(require_universe=False)

        self._log_usage(provider.name, "get_daily_prices", len(symbols))

        return provider.get_daily_prices(symbols, start_date, end_date)

    def get_universe(self, as_of_date: date) -> list[str]:
        """Get tradeable universe using selected provider.

        This operation requires a production-ready provider (CRSP).
        yfinance cannot serve universe queries.

        Args:
            as_of_date: Reference date for universe construction.

        Returns:
            List of ticker symbols available on the given date.

        Raises:
            ProviderNotSupportedError: If selected provider doesn't support universe.
            ProviderUnavailableError: If no provider available.
            ProductionProviderRequiredError: If production and CRSP unavailable.
        """
        provider = self._select_provider(require_universe=True)

        self._log_usage(provider.name, "get_universe", 0)

        return provider.get_universe(as_of_date)

    def get_active_provider(self) -> str:
        """Return name of currently active provider.

        Returns:
            Provider name (e.g., 'crsp', 'yfinance').

        Raises:
            ProviderUnavailableError: If no provider available.
        """
        return self._select_provider().name

    def is_available(self, provider_type: ProviderType) -> bool:
        """Check if a specific provider is available.

        Args:
            provider_type: Provider type to check.

        Returns:
            True if provider is configured and available.
        """
        return provider_type in self._adapters

    def _log_usage(
        self,
        provider: str,
        operation: str,
        count: int,
    ) -> None:
        """Log usage metrics for monitoring.

        Args:
            provider: Provider name used.
            operation: Operation performed.
            count: Number of symbols (for price fetches).
        """
        logger.info(
            "Data fetch operation",
            extra={
                "provider": provider,
                "operation": operation,
                "symbol_count": count,
                "environment": self._config.environment,
            },
        )
