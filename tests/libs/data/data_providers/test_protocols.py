"""Tests for DataProvider protocol and adapters.

Comprehensive test suite covering:
- Protocol compliance for adapters
- Schema normalization for yfinance and CRSP
- Exception attributes and error handling
- Edge cases and empty data handling
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import polars as pl
import pytest

from libs.data.data_providers.protocols import (
    UNIFIED_COLUMNS,
    UNIFIED_SCHEMA,
    ConfigurationError,
    CRSPDataProviderAdapter,
    DataProvider,
    DataProviderError,
    ProductionProviderRequiredError,
    ProviderNotSupportedError,
    ProviderUnavailableError,
    YFinanceDataProviderAdapter,
)

if TYPE_CHECKING:
    pass


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
            "symbol": ["aapl", "aapl"],  # Lowercase to test normalization
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
            "ticker": ["aapl", "aapl"],  # CRSP uses ticker, lowercase to test
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
def yfinance_adapter(mock_yfinance_provider: MagicMock) -> YFinanceDataProviderAdapter:
    """Create YFinanceDataProviderAdapter for testing."""
    return YFinanceDataProviderAdapter(mock_yfinance_provider)


@pytest.fixture()
def crsp_adapter(mock_crsp_provider: MagicMock) -> CRSPDataProviderAdapter:
    """Create CRSPDataProviderAdapter for testing."""
    return CRSPDataProviderAdapter(mock_crsp_provider)


# =============================================================================
# Protocol Compliance Tests
# =============================================================================


class TestProtocolCompliance:
    """Test that adapters implement DataProvider protocol."""

    def test_yfinance_adapter_is_data_provider(
        self, yfinance_adapter: YFinanceDataProviderAdapter
    ) -> None:
        """YFinanceDataProviderAdapter implements DataProvider protocol."""
        assert isinstance(yfinance_adapter, DataProvider)

    def test_crsp_adapter_is_data_provider(self, crsp_adapter: CRSPDataProviderAdapter) -> None:
        """CRSPDataProviderAdapter implements DataProvider protocol."""
        assert isinstance(crsp_adapter, DataProvider)

    def test_yfinance_name_property(self, yfinance_adapter: YFinanceDataProviderAdapter) -> None:
        """YFinance adapter returns correct provider name."""
        assert yfinance_adapter.name == "yfinance"

    def test_crsp_name_property(self, crsp_adapter: CRSPDataProviderAdapter) -> None:
        """CRSP adapter returns correct provider name."""
        assert crsp_adapter.name == "crsp"

    def test_yfinance_is_not_production_ready(
        self, yfinance_adapter: YFinanceDataProviderAdapter
    ) -> None:
        """yfinance returns False for is_production_ready."""
        assert yfinance_adapter.is_production_ready is False

    def test_crsp_is_production_ready(self, crsp_adapter: CRSPDataProviderAdapter) -> None:
        """CRSP returns True for is_production_ready."""
        assert crsp_adapter.is_production_ready is True

    def test_yfinance_does_not_support_universe(
        self, yfinance_adapter: YFinanceDataProviderAdapter
    ) -> None:
        """yfinance returns False for supports_universe."""
        assert yfinance_adapter.supports_universe is False

    def test_crsp_supports_universe(self, crsp_adapter: CRSPDataProviderAdapter) -> None:
        """CRSP returns True for supports_universe."""
        assert crsp_adapter.supports_universe is True


# =============================================================================
# Schema Normalization Tests
# =============================================================================


class TestYFinanceSchemaormalization:
    """Test yfinance adapter schema normalization."""

    def test_yfinance_schema_has_unified_columns(
        self,
        yfinance_adapter: YFinanceDataProviderAdapter,
    ) -> None:
        """yfinance output has all unified schema columns."""
        df = yfinance_adapter.get_daily_prices(["AAPL"], date(2024, 1, 1), date(2024, 1, 31))
        assert list(df.columns) == UNIFIED_COLUMNS

    def test_yfinance_symbols_normalized_to_uppercase(
        self,
        yfinance_adapter: YFinanceDataProviderAdapter,
    ) -> None:
        """yfinance adapter normalizes symbols to uppercase."""
        df = yfinance_adapter.get_daily_prices(["aapl"], date(2024, 1, 1), date(2024, 1, 31))
        assert df["symbol"].to_list() == ["AAPL", "AAPL"]

    def test_yfinance_ret_column_is_null(
        self,
        yfinance_adapter: YFinanceDataProviderAdapter,
    ) -> None:
        """yfinance adapter adds null ret column (yfinance doesn't provide returns)."""
        df = yfinance_adapter.get_daily_prices(["AAPL"], date(2024, 1, 1), date(2024, 1, 31))
        assert df["ret"].is_null().all()

    def test_yfinance_ohlc_columns_preserved(
        self,
        yfinance_adapter: YFinanceDataProviderAdapter,
    ) -> None:
        """yfinance adapter preserves OHLC columns."""
        df = yfinance_adapter.get_daily_prices(["AAPL"], date(2024, 1, 1), date(2024, 1, 31))
        assert df["open"].to_list() == [180.0, 182.0]
        assert df["high"].to_list() == [185.0, 186.0]
        assert df["low"].to_list() == [178.0, 180.0]
        assert df["adj_close"].to_list() == [183.0, 184.5]


class TestCRSPSchemaNormalization:
    """Test CRSP adapter schema normalization."""

    def test_crsp_schema_has_unified_columns(
        self,
        crsp_adapter: CRSPDataProviderAdapter,
    ) -> None:
        """CRSP output has all unified schema columns."""
        df = crsp_adapter.get_daily_prices(["AAPL"], date(2024, 1, 1), date(2024, 1, 31))
        assert list(df.columns) == UNIFIED_COLUMNS

    def test_crsp_ticker_renamed_to_symbol(
        self,
        crsp_adapter: CRSPDataProviderAdapter,
    ) -> None:
        """CRSP adapter renames ticker to symbol."""
        df = crsp_adapter.get_daily_prices(["AAPL"], date(2024, 1, 1), date(2024, 1, 31))
        assert "symbol" in df.columns
        assert "ticker" not in df.columns

    def test_crsp_symbols_normalized_to_uppercase(
        self,
        crsp_adapter: CRSPDataProviderAdapter,
    ) -> None:
        """CRSP adapter normalizes symbols to uppercase."""
        df = crsp_adapter.get_daily_prices(["aapl"], date(2024, 1, 1), date(2024, 1, 31))
        assert df["symbol"].to_list() == ["AAPL", "AAPL"]

    def test_crsp_prc_renamed_to_close(
        self,
        crsp_adapter: CRSPDataProviderAdapter,
    ) -> None:
        """CRSP adapter renames prc to close."""
        df = crsp_adapter.get_daily_prices(["AAPL"], date(2024, 1, 1), date(2024, 1, 31))
        assert df["close"].to_list() == [183.0, 184.5]

    def test_crsp_vol_renamed_to_volume(
        self,
        crsp_adapter: CRSPDataProviderAdapter,
    ) -> None:
        """CRSP adapter renames vol to volume."""
        df = crsp_adapter.get_daily_prices(["AAPL"], date(2024, 1, 1), date(2024, 1, 31))
        assert df["volume"].to_list() == [50000000.0, 48000000.0]

    def test_crsp_ret_column_preserved(
        self,
        crsp_adapter: CRSPDataProviderAdapter,
    ) -> None:
        """CRSP adapter preserves ret column for performance calculations."""
        df = crsp_adapter.get_daily_prices(["AAPL"], date(2024, 1, 1), date(2024, 1, 31))
        assert df["ret"].to_list() == [0.015, 0.008]

    def test_crsp_ohlc_columns_are_null(
        self,
        crsp_adapter: CRSPDataProviderAdapter,
    ) -> None:
        """CRSP adapter sets open/high/low to null (CRSP doesn't have these)."""
        df = crsp_adapter.get_daily_prices(["AAPL"], date(2024, 1, 1), date(2024, 1, 31))
        assert df["open"].is_null().all()
        assert df["high"].is_null().all()
        assert df["low"].is_null().all()

    def test_crsp_adj_close_is_null(
        self,
        crsp_adapter: CRSPDataProviderAdapter,
    ) -> None:
        """CRSP adapter sets adj_close to null (prc is NOT split-adjusted)."""
        df = crsp_adapter.get_daily_prices(["AAPL"], date(2024, 1, 1), date(2024, 1, 31))
        assert df["adj_close"].is_null().all()


class TestSchemaConsistency:
    """Test schema consistency between adapters."""

    def test_both_adapters_same_column_order(
        self,
        yfinance_adapter: YFinanceDataProviderAdapter,
        crsp_adapter: CRSPDataProviderAdapter,
    ) -> None:
        """Both adapters return columns in same canonical order."""
        yf_df = yfinance_adapter.get_daily_prices(["AAPL"], date(2024, 1, 1), date(2024, 1, 31))
        crsp_df = crsp_adapter.get_daily_prices(["AAPL"], date(2024, 1, 1), date(2024, 1, 31))
        assert list(yf_df.columns) == list(crsp_df.columns)
        assert list(yf_df.columns) == UNIFIED_COLUMNS

    def test_yfinance_dtypes_match_unified_schema(
        self,
        yfinance_adapter: YFinanceDataProviderAdapter,
    ) -> None:
        """yfinance adapter returns correct dtypes per unified schema."""
        df = yfinance_adapter.get_daily_prices(["AAPL"], date(2024, 1, 1), date(2024, 1, 31))
        # Verify date column is pl.Date (not Datetime)
        assert df.schema["date"] == pl.Date
        # Verify symbol is Utf8
        assert df.schema["symbol"] == pl.Utf8
        # Verify all numeric columns are Float64
        float_cols = ["close", "volume", "ret", "open", "high", "low", "adj_close"]
        for col in float_cols:
            assert df.schema[col] == pl.Float64, f"{col} should be Float64"

    def test_crsp_dtypes_match_unified_schema(
        self,
        crsp_adapter: CRSPDataProviderAdapter,
    ) -> None:
        """CRSP adapter returns correct dtypes per unified schema."""
        df = crsp_adapter.get_daily_prices(["AAPL"], date(2024, 1, 1), date(2024, 1, 31))
        # Verify date column is pl.Date (not Datetime)
        assert df.schema["date"] == pl.Date
        # Verify symbol is Utf8
        assert df.schema["symbol"] == pl.Utf8
        # Verify all numeric columns are Float64
        float_cols = ["close", "volume", "ret", "open", "high", "low", "adj_close"]
        for col in float_cols:
            assert df.schema[col] == pl.Float64, f"{col} should be Float64"


# =============================================================================
# Universe Operation Tests
# =============================================================================


class TestUniverseOperations:
    """Test get_universe operations."""

    def test_crsp_get_universe_returns_symbols(
        self,
        crsp_adapter: CRSPDataProviderAdapter,
    ) -> None:
        """CRSP get_universe returns list of symbols."""
        symbols = crsp_adapter.get_universe(date(2024, 1, 15))
        assert symbols == ["AAPL", "MSFT", "GOOGL"]

    def test_crsp_get_universe_uppercase(
        self,
        mock_crsp_provider: MagicMock,
    ) -> None:
        """CRSP get_universe returns uppercase symbols."""
        mock_crsp_provider.get_universe.return_value = pl.DataFrame(
            {
                "ticker": ["aapl", "msft"],
            }
        )
        adapter = CRSPDataProviderAdapter(mock_crsp_provider)
        symbols = adapter.get_universe(date(2024, 1, 15))
        assert symbols == ["AAPL", "MSFT"]

    def test_yfinance_get_universe_raises(
        self,
        yfinance_adapter: YFinanceDataProviderAdapter,
    ) -> None:
        """yfinance get_universe raises ProviderNotSupportedError."""
        with pytest.raises(ProviderNotSupportedError) as exc_info:
            yfinance_adapter.get_universe(date(2024, 1, 15))

        assert exc_info.value.provider_name == "yfinance"
        assert exc_info.value.operation == "get_universe"


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestExceptionAttributes:
    """Test exception attributes."""

    def test_provider_unavailable_error_attributes(self) -> None:
        """ProviderUnavailableError includes provider name and available list."""
        exc = ProviderUnavailableError(
            "Test error",
            provider_name="crsp",
            available_providers=["yfinance"],
        )
        assert exc.provider_name == "crsp"
        assert exc.available_providers == ["yfinance"]

    def test_provider_unavailable_error_default_available(self) -> None:
        """ProviderUnavailableError defaults to empty available list."""
        exc = ProviderUnavailableError("Test error")
        assert exc.provider_name is None
        assert exc.available_providers == []

    def test_provider_not_supported_error_attributes(self) -> None:
        """ProviderNotSupportedError includes provider name and operation."""
        exc = ProviderNotSupportedError(
            "Test error",
            provider_name="yfinance",
            operation="get_universe",
        )
        assert exc.provider_name == "yfinance"
        assert exc.operation == "get_universe"

    def test_all_exceptions_inherit_from_base(self) -> None:
        """All custom exceptions inherit from DataProviderError."""
        assert issubclass(ProviderUnavailableError, DataProviderError)
        assert issubclass(ProviderNotSupportedError, DataProviderError)
        assert issubclass(ProductionProviderRequiredError, DataProviderError)
        assert issubclass(ConfigurationError, DataProviderError)


class TestInputValidation:
    """Test input validation."""

    def test_yfinance_empty_symbols_raises_value_error(
        self,
        yfinance_adapter: YFinanceDataProviderAdapter,
    ) -> None:
        """Empty symbols list raises ValueError for yfinance."""
        with pytest.raises(ValueError, match="symbols list cannot be empty"):
            yfinance_adapter.get_daily_prices([], date(2024, 1, 1), date(2024, 1, 31))

    def test_crsp_empty_symbols_raises_value_error(
        self,
        crsp_adapter: CRSPDataProviderAdapter,
    ) -> None:
        """Empty symbols list raises ValueError for CRSP."""
        with pytest.raises(ValueError, match="symbols list cannot be empty"):
            crsp_adapter.get_daily_prices([], date(2024, 1, 1), date(2024, 1, 31))

    def test_yfinance_missing_required_columns_raises(
        self,
        mock_yfinance_provider: MagicMock,
    ) -> None:
        """yfinance adapter raises ValueError when required columns are missing."""
        # Missing date and close
        mock_yfinance_provider.get_daily_prices.return_value = pl.DataFrame(
            {
                "symbol": ["AAPL"],
                "volume": [50000000.0],
            }
        )
        adapter = YFinanceDataProviderAdapter(mock_yfinance_provider)

        with pytest.raises(ValueError, match="missing required columns"):
            adapter.get_daily_prices(["AAPL"], date(2024, 1, 1), date(2024, 1, 31))

    def test_yfinance_missing_symbol_column_raises(
        self,
        mock_yfinance_provider: MagicMock,
    ) -> None:
        """yfinance adapter raises ValueError when symbol column is missing."""
        mock_yfinance_provider.get_daily_prices.return_value = pl.DataFrame(
            {
                "date": [date(2024, 1, 2)],
                "close": [183.0],
                "volume": [50000000.0],
            }
        )
        adapter = YFinanceDataProviderAdapter(mock_yfinance_provider)

        with pytest.raises(ValueError, match="missing required columns.*symbol"):
            adapter.get_daily_prices(["AAPL"], date(2024, 1, 1), date(2024, 1, 31))

    def test_crsp_missing_date_column_raises(
        self,
        mock_crsp_provider: MagicMock,
    ) -> None:
        """CRSP adapter raises ValueError when date column is missing."""
        mock_crsp_provider.get_daily_prices.return_value = pl.DataFrame(
            {
                "ticker": ["AAPL"],
                "prc": [183.0],
                "vol": [50000000.0],
                "ret": [0.015],
            }
        )
        adapter = CRSPDataProviderAdapter(mock_crsp_provider)

        with pytest.raises(ValueError, match="must contain 'date' column"):
            adapter.get_daily_prices(["AAPL"], date(2024, 1, 1), date(2024, 1, 31))

    def test_crsp_missing_required_columns_raises(
        self,
        mock_crsp_provider: MagicMock,
    ) -> None:
        """CRSP adapter raises ValueError when required columns are missing after rename."""
        # Missing ret column (required for CRSP)
        mock_crsp_provider.get_daily_prices.return_value = pl.DataFrame(
            {
                "date": [date(2024, 1, 2)],
                "ticker": ["AAPL"],
                "prc": [183.0],
                "vol": [50000000.0],
                # Missing ret
            }
        )
        adapter = CRSPDataProviderAdapter(mock_crsp_provider)

        with pytest.raises(ValueError, match="missing required columns.*ret"):
            adapter.get_daily_prices(["AAPL"], date(2024, 1, 1), date(2024, 1, 31))


# =============================================================================
# Empty Data Tests
# =============================================================================


class TestEmptyDataHandling:
    """Test handling of empty data."""

    def test_yfinance_empty_result_returns_empty_unified_schema(
        self,
        mock_yfinance_provider: MagicMock,
    ) -> None:
        """yfinance adapter returns empty DataFrame with unified schema."""
        mock_yfinance_provider.get_daily_prices.return_value = pl.DataFrame()
        adapter = YFinanceDataProviderAdapter(mock_yfinance_provider)

        df = adapter.get_daily_prices(["AAPL"], date(2024, 1, 1), date(2024, 1, 31))

        assert df.is_empty()
        assert list(df.columns) == list(UNIFIED_SCHEMA.keys())

    def test_crsp_empty_result_returns_empty_unified_schema(
        self,
        mock_crsp_provider: MagicMock,
    ) -> None:
        """CRSP adapter returns empty DataFrame with unified schema."""
        mock_crsp_provider.get_daily_prices.return_value = pl.DataFrame()
        adapter = CRSPDataProviderAdapter(mock_crsp_provider)

        df = adapter.get_daily_prices(["AAPL"], date(2024, 1, 1), date(2024, 1, 31))

        assert df.is_empty()
        assert list(df.columns) == list(UNIFIED_SCHEMA.keys())


# =============================================================================
# Unified Schema Constants Tests
# =============================================================================


class TestUnifiedSchemaConstants:
    """Test unified schema constants."""

    def test_unified_columns_order(self) -> None:
        """UNIFIED_COLUMNS has expected order."""
        assert UNIFIED_COLUMNS == [
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

    def test_unified_schema_types(self) -> None:
        """UNIFIED_SCHEMA has expected types."""
        assert UNIFIED_SCHEMA["date"] == pl.Date
        assert UNIFIED_SCHEMA["symbol"] == pl.Utf8
        assert UNIFIED_SCHEMA["close"] == pl.Float64
        assert UNIFIED_SCHEMA["volume"] == pl.Float64
        assert UNIFIED_SCHEMA["ret"] == pl.Float64
        assert UNIFIED_SCHEMA["open"] == pl.Float64
        assert UNIFIED_SCHEMA["high"] == pl.Float64
        assert UNIFIED_SCHEMA["low"] == pl.Float64
        assert UNIFIED_SCHEMA["adj_close"] == pl.Float64
