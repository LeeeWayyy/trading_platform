"""Data Provider Protocol and Adapters.

This module defines the common DataProvider protocol interface and adapter
implementations for yfinance and CRSP providers.

The protocol enables seamless switching between data sources (yfinance for
development, CRSP for production) via configuration.

Classes:
    DataProvider: Protocol defining the common interface for all data providers.
    YFinanceDataProviderAdapter: Adapter for YFinanceProvider.
    CRSPDataProviderAdapter: Adapter for CRSPLocalProvider.

Exceptions:
    DataProviderError: Base exception for data provider errors.
    ProviderUnavailableError: Raised when requested provider is not available.
    ProviderNotSupportedError: Raised when operation not supported by provider.
    ProductionProviderRequiredError: Raised when production requires CRSP.
    ConfigurationError: Raised when fetcher configuration is invalid.

See Also:
    docs/CONCEPTS/unified-data-fetcher.md for usage examples.
    docs/ADRs/ADR-016-data-provider-protocol.md for design decisions.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import polars as pl

if TYPE_CHECKING:
    from libs.data_providers.crsp_local_provider import CRSPLocalProvider
    from libs.data_providers.yfinance_provider import YFinanceProvider

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class DataProviderError(Exception):
    """Base exception for data provider errors."""

    pass


class ProviderUnavailableError(DataProviderError):
    """Raised when a requested provider is not available.

    Attributes:
        provider_name: Name of the unavailable provider.
        available_providers: List of available provider names.
    """

    def __init__(
        self,
        message: str,
        provider_name: str | None = None,
        available_providers: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.provider_name = provider_name
        self.available_providers = available_providers or []


class ProviderNotSupportedError(DataProviderError):
    """Raised when operation not supported by provider.

    Example: get_universe() on yfinance.

    Attributes:
        provider_name: Provider that doesn't support the operation.
        operation: The unsupported operation name.
    """

    def __init__(
        self,
        message: str,
        provider_name: str | None = None,
        operation: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider_name = provider_name
        self.operation = operation


class ProductionProviderRequiredError(DataProviderError):
    """Raised when production env requires production-ready provider.

    This occurs when:
    - environment=production AND
    - AUTO mode selected AND
    - CRSP provider not available
    """

    pass


class ConfigurationError(DataProviderError):
    """Raised when fetcher configuration is invalid.

    Examples:
    - Invalid storage paths
    - Missing required config
    """

    pass


# =============================================================================
# Unified Schema
# =============================================================================

# Canonical column order for unified schema
UNIFIED_COLUMNS = [
    "date",
    "symbol",
    "close",
    "volume",
    "ret",
    "open",
    "high",
    "low",
    "adj_close",
]

# Schema types for validation
UNIFIED_SCHEMA: dict[str, type[pl.DataType]] = {
    "date": pl.Date,
    "symbol": pl.Utf8,
    "close": pl.Float64,
    "volume": pl.Float64,
    "ret": pl.Float64,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "adj_close": pl.Float64,
}


# =============================================================================
# Protocol Definition
# =============================================================================


@runtime_checkable
class DataProvider(Protocol):
    """Common interface for all data providers.

    Enables switching between yfinance (dev) and CRSP (prod)
    transparently via configuration.

    Thread Safety: Implementations must be thread-safe for concurrent reads.

    Unified Schema:
        Required columns (always present):
        - date: Date (trading date)
        - symbol: str (ticker, uppercase)
        - close: Float64 (closing price, absolute)
        - volume: Float64 (volume in raw shares)
        - ret: Float64 (holding period return, may be null)

        Optional columns (may be null depending on provider):
        - open: Float64 (yfinance only, null for CRSP)
        - high: Float64 (yfinance only, null for CRSP)
        - low: Float64 (yfinance only, null for CRSP)
        - adj_close: Float64 (yfinance only, null for CRSP)

    Note:
        CRSP prc is NOT split-adjusted, so adj_close is null for CRSP.
        Use the `ret` column for performance calculations with CRSP data
        (ret IS split-adjusted in CRSP).
    """

    @property
    def name(self) -> str:
        """Provider identifier (e.g., 'yfinance', 'crsp')."""
        ...

    @property
    def is_production_ready(self) -> bool:
        """Whether provider is suitable for production backtests.

        Returns:
            True for CRSP (survivorship-bias-free)
            False for yfinance (lacks survivorship handling)
        """
        ...

    @property
    def supports_universe(self) -> bool:
        """Whether provider supports get_universe operation.

        Returns:
            True for CRSP (has point-in-time universe)
            False for yfinance (no universe concept)
        """
        ...

    def get_daily_prices(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date,
    ) -> pl.DataFrame:
        """Fetch daily price data.

        Args:
            symbols: List of ticker symbols to fetch.
            start_date: Start of date range (inclusive).
            end_date: End of date range (inclusive).

        Returns:
            DataFrame with unified schema columns in canonical order.
            Empty DataFrame (0 rows) if no data found for the date range.

        Raises:
            ValueError: If symbols list is empty.

        Edge Cases:
            - start_date > end_date: Returns empty DataFrame (no error).
            - Symbol not found: Silently omitted from results (check row count).
            - Weekend/holiday dates: Returns data for nearest trading days.
            - Future dates: Provider-dependent behavior (may return empty or error).
        """
        ...

    def get_universe(self, as_of_date: date) -> list[str]:
        """Get tradeable universe as of date.

        Args:
            as_of_date: Reference date for universe construction.

        Returns:
            List of ticker symbols available on the given date.
            Empty list if no symbols match criteria.

        Raises:
            ProviderNotSupportedError: If provider doesn't support universe
                                       (e.g., yfinance has no universe concept).

        Edge Cases:
            - Future date: Returns current universe (no forecasting).
            - Weekend/holiday: Returns universe from nearest trading day.
            - Very old date: May have limited or no data.
        """
        ...


# =============================================================================
# Adapter Implementations
# =============================================================================


class YFinanceDataProviderAdapter:
    """Adapter to make YFinanceProvider conform to DataProvider protocol.

    This adapter wraps YFinanceProvider to provide a consistent interface
    matching the DataProvider protocol.

    Schema Normalization:
        - Symbols normalized to uppercase
        - ret column added as null (yfinance doesn't provide returns)
        - All OHLC columns preserved (yfinance has these)
        - Columns reordered to canonical order

    Limitations:
        - is_production_ready = False (lacks survivorship handling)
        - supports_universe = False (no universe concept)
        - get_universe() raises ProviderNotSupportedError
    """

    def __init__(self, provider: YFinanceProvider) -> None:
        """Initialize adapter with YFinanceProvider.

        Args:
            provider: Configured YFinanceProvider instance.
        """
        self._provider = provider

    @property
    def name(self) -> str:
        """Provider identifier."""
        return "yfinance"

    @property
    def is_production_ready(self) -> bool:
        """yfinance lacks survivorship handling."""
        return False

    @property
    def supports_universe(self) -> bool:
        """yfinance has no universe concept."""
        return False

    def get_daily_prices(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date,
    ) -> pl.DataFrame:
        """Fetch daily prices via yfinance.

        Args:
            symbols: List of ticker symbols.
            start_date: Start of date range.
            end_date: End of date range.

        Returns:
            DataFrame with unified schema.

        Raises:
            ValueError: If symbols list is empty.
        """
        if not symbols:
            raise ValueError("symbols list cannot be empty")

        df = self._provider.get_daily_prices(
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
        )

        return self._normalize_schema(df)

    def _normalize_schema(self, df: pl.DataFrame) -> pl.DataFrame:
        """Normalize yfinance output to unified schema.

        Transformations:
        1. Validate required columns (date, symbol, close, volume) exist
        2. Cast date to pl.Date for schema consistency
        3. Symbols normalized to uppercase
        4. ret column added as null (yfinance doesn't provide returns)
        5. Missing optional columns added as null
        6. All numeric columns cast to Float64 for schema consistency
        7. Columns reordered to canonical order
        """
        if df.is_empty():
            return self._empty_result()

        # Validate required columns exist (fail fast with clear errors)
        required_cols = ["date", "symbol", "close", "volume"]
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            raise ValueError(
                f"DataFrame missing required columns: {missing}. "
                f"Got columns: {df.columns}"
            )

        # Cast date to pl.Date for schema consistency
        df = df.with_columns(pl.col("date").cast(pl.Date))

        # Normalize symbols to uppercase
        df = df.with_columns(pl.col("symbol").str.to_uppercase())

        # Add null ret column (yfinance doesn't provide returns)
        if "ret" not in df.columns:
            df = df.with_columns(pl.lit(None).cast(pl.Float64).alias("ret"))

        # Ensure all optional columns exist (may already be present)
        for col in ["open", "high", "low", "adj_close"]:
            if col not in df.columns:
                df = df.with_columns(pl.lit(None).cast(pl.Float64).alias(col))

        # Cast all numeric columns to Float64 for schema consistency
        float_cols = ["close", "volume", "ret", "open", "high", "low", "adj_close"]
        for col in float_cols:
            if col in df.columns:
                df = df.with_columns(pl.col(col).cast(pl.Float64))

        # Select columns in canonical order
        return df.select(UNIFIED_COLUMNS)

    def _empty_result(self) -> pl.DataFrame:
        """Return empty DataFrame with unified schema."""
        return pl.DataFrame(schema=UNIFIED_SCHEMA)

    def get_universe(self, as_of_date: date) -> list[str]:
        """Not supported by yfinance.

        Raises:
            ProviderNotSupportedError: Always raised.
        """
        raise ProviderNotSupportedError(
            "yfinance does not support universe queries. "
            "Use CRSP provider for production universe operations.",
            provider_name="yfinance",
            operation="get_universe",
        )


class CRSPDataProviderAdapter:
    """Adapter to make CRSPLocalProvider conform to DataProvider protocol.

    This adapter wraps CRSPLocalProvider to provide a consistent interface
    matching the DataProvider protocol.

    Schema Normalization:
        - ticker → symbol (renamed)
        - prc → close (renamed, uses adjust_prices=True for abs(prc))
        - vol → volume (renamed)
        - Symbols normalized to uppercase
        - open, high, low, adj_close set to null (CRSP doesn't have these)

    CRITICAL: CRSP prc is NOT split-adjusted. The adj_close column is null.
    Use the `ret` column for performance calculations (ret IS split-adjusted).

    Volume: CRSP daily vol is already in raw shares (not hundreds like TAQ).
    """

    def __init__(self, provider: CRSPLocalProvider) -> None:
        """Initialize adapter with CRSPLocalProvider.

        Args:
            provider: Configured CRSPLocalProvider instance.
        """
        self._provider = provider

    @property
    def name(self) -> str:
        """Provider identifier."""
        return "crsp"

    @property
    def is_production_ready(self) -> bool:
        """CRSP is survivorship-bias-free."""
        return True

    @property
    def supports_universe(self) -> bool:
        """CRSP has point-in-time universe."""
        return True

    def get_daily_prices(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date,
    ) -> pl.DataFrame:
        """Fetch daily prices via CRSP.

        Args:
            symbols: List of ticker symbols.
            start_date: Start of date range.
            end_date: End of date range.

        Returns:
            DataFrame with unified schema.

        Raises:
            ValueError: If symbols list is empty.
        """
        if not symbols:
            raise ValueError("symbols list cannot be empty")

        df = self._provider.get_daily_prices(
            start_date=start_date,
            end_date=end_date,
            symbols=symbols,
            adjust_prices=True,  # Use abs(prc)
        )

        return self._normalize_schema(df)

    def _normalize_schema(self, df: pl.DataFrame) -> pl.DataFrame:
        """Normalize CRSP output to unified schema.

        Transformations:
        1. Validate required date column exists
        2. Cast date to pl.Date for schema consistency
        3. Rename: ticker → symbol, prc → close, vol → volume
        4. Symbols normalized to uppercase
        5. open, high, low, adj_close set to null (CRSP doesn't have these)
        6. All numeric columns cast to Float64 for schema consistency
        7. Columns reordered to canonical order

        CRITICAL: adj_close is NULL because CRSP prc is NOT split-adjusted.
        Use `ret` column for performance calculations (ret IS split-adjusted).
        """
        if df.is_empty():
            return self._empty_result()

        # Validate date column exists before any transformations
        if "date" not in df.columns:
            raise ValueError("DataFrame must contain 'date' column")

        # Cast date to pl.Date for schema consistency
        df = df.with_columns(pl.col("date").cast(pl.Date))

        # Rename columns (CRSP uses different names)
        rename_map = {}
        if "ticker" in df.columns:
            rename_map["ticker"] = "symbol"
        if "prc" in df.columns:
            rename_map["prc"] = "close"
        if "vol" in df.columns:
            rename_map["vol"] = "volume"

        if rename_map:
            df = df.rename(rename_map)

        # Validate required columns exist after renaming (fail fast with clear errors)
        required_cols = ["symbol", "close", "volume", "ret"]
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            raise ValueError(
                f"DataFrame missing required columns: {missing}. "
                f"Got columns: {df.columns}"
            )

        # Normalize symbols to uppercase
        df = df.with_columns(pl.col("symbol").str.to_uppercase())

        # Add null columns for OHLC and adj_close only if not already present
        # This preserves any higher-quality data from future provider upgrades
        # CRITICAL: adj_close is NULL because CRSP prc is NOT split-adjusted
        # Use `ret` column for performance calculations (ret IS split-adjusted)
        for col in ["open", "high", "low", "adj_close"]:
            if col not in df.columns:
                df = df.with_columns(pl.lit(None).cast(pl.Float64).alias(col))

        # Cast all numeric columns to Float64 for schema consistency
        float_cols = ["close", "volume", "ret", "open", "high", "low", "adj_close"]
        for col in float_cols:
            if col in df.columns:
                df = df.with_columns(pl.col(col).cast(pl.Float64))

        # Select columns in canonical order
        return df.select(UNIFIED_COLUMNS)

    def _empty_result(self) -> pl.DataFrame:
        """Return empty DataFrame with unified schema."""
        return pl.DataFrame(schema=UNIFIED_SCHEMA)

    def get_universe(self, as_of_date: date) -> list[str]:
        """Get tradeable universe via CRSP.

        Args:
            as_of_date: Reference date for universe construction.

        Returns:
            List of ticker symbols (uppercase) available on the given date.
        """
        universe_df = self._provider.get_universe(as_of_date=as_of_date)
        # Return uppercase symbols for consistency
        return [s.upper() for s in universe_df["ticker"].to_list()]
