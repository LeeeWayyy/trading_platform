"""
Unit tests for fetch_current_prices() function (P1T0).

Tests coverage for price fetching from Alpaca API with mocked responses.

The fetch_current_prices function:
- Fetches latest quotes from Alpaca API for batch of symbols
- Uses mid-quote pricing with multiple fallbacks
- Handles API errors gracefully
- Returns empty dict on failures (graceful degradation)

Test Strategy:
- Mock AlpacaExecutor to avoid real API calls
- Test all price fallback scenarios (last_price, mid-quote, ask, bid)
- Test error handling (API down, symbol not found)
- Test batch fetching efficiency

See Also:
    - scripts/paper_run.py:fetch_current_prices() - Function under test
    - apps/execution_gateway/alpaca_client.py - AlpacaExecutor implementation
    - ADR-0008: Enhanced P&L calculation architecture
"""

import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from apps.execution_gateway.alpaca_client import AlpacaConnectionError  # noqa: E402
from scripts.ops.paper_run import fetch_current_prices  # noqa: E402


class TestFetchCurrentPrices:
    """Test suite for fetch_current_prices() function."""

    @pytest.mark.asyncio()
    async def test_fetch_single_symbol_with_last_price(self) -> None:
        """
        Test fetching single symbol with last_price available.

        Scenario:
            - Request AAPL quote
            - Alpaca returns last_price: $152.75
            - Expected: Use last_price directly
        """
        mock_executor = MagicMock()
        mock_executor.get_latest_quotes.return_value = {
            "AAPL": {
                "last_price": Decimal("152.75"),
                "ask_price": Decimal("152.80"),
                "bid_price": Decimal("152.70"),
            }
        }

        with patch("scripts.ops.paper_run.AlpacaExecutor", return_value=mock_executor):
            config: dict[str, Any] = {}
            prices = await fetch_current_prices(["AAPL"], config)

        assert prices == {"AAPL": Decimal("152.75")}
        mock_executor.get_latest_quotes.assert_called_once_with(["AAPL"])

    @pytest.mark.asyncio()
    async def test_fetch_multiple_symbols_batch(self) -> None:
        """
        Test fetching multiple symbols in single batch request.

        Scenario:
            - Request AAPL, MSFT, GOOGL
            - Alpaca returns all quotes in single response
            - Expected: Single API call with all prices
        """
        mock_executor = MagicMock()
        mock_executor.get_latest_quotes.return_value = {
            "AAPL": {"last_price": Decimal("152.75"), "ask_price": None, "bid_price": None},
            "MSFT": {"last_price": Decimal("380.50"), "ask_price": None, "bid_price": None},
            "GOOGL": {"last_price": Decimal("140.25"), "ask_price": None, "bid_price": None},
        }

        with patch("scripts.ops.paper_run.AlpacaExecutor", return_value=mock_executor):
            config: dict[str, Any] = {}
            prices = await fetch_current_prices(["AAPL", "MSFT", "GOOGL"], config)

        assert prices == {
            "AAPL": Decimal("152.75"),
            "MSFT": Decimal("380.50"),
            "GOOGL": Decimal("140.25"),
        }
        # Verify single batch call
        mock_executor.get_latest_quotes.assert_called_once_with(["AAPL", "MSFT", "GOOGL"])

    @pytest.mark.asyncio()
    async def test_uses_mid_quote_from_latest_quotes(self) -> None:
        """
        Test that mid-quote calculated by get_latest_quotes() is used.

        Note: get_latest_quotes() already calculates mid-quote when bid/ask available
        and returns it as last_price. This test verifies we use that value correctly.

        Scenario:
            - AAPL: last_price=$152.75 (mid-quote from get_latest_quotes)
            - Expected: Use last_price directly
        """
        mock_executor = MagicMock()
        mock_executor.get_latest_quotes.return_value = {
            "AAPL": {
                "last_price": Decimal("152.75"),  # Mid-quote calculated by get_latest_quotes
                "ask_price": Decimal("152.80"),
                "bid_price": Decimal("152.70"),
            }
        }

        with patch("scripts.ops.paper_run.AlpacaExecutor", return_value=mock_executor):
            config: dict[str, Any] = {}
            prices = await fetch_current_prices(["AAPL"], config)

        assert prices == {"AAPL": Decimal("152.75")}

    @pytest.mark.asyncio()
    async def test_fallback_to_ask_price_only(self) -> None:
        """
        Test fallback to ask_price when last_price and bid_price are None.

        Scenario:
            - AAPL: last_price=None, bid_price=None, ask=$152.80
            - Expected: Use ask_price
        """
        mock_executor = MagicMock()
        mock_executor.get_latest_quotes.return_value = {
            "AAPL": {
                "last_price": None,
                "ask_price": Decimal("152.80"),
                "bid_price": None,
            }
        }

        with patch("scripts.ops.paper_run.AlpacaExecutor", return_value=mock_executor):
            config: dict[str, Any] = {}
            prices = await fetch_current_prices(["AAPL"], config)

        assert prices == {"AAPL": Decimal("152.80")}

    @pytest.mark.asyncio()
    async def test_fallback_to_bid_price_only(self) -> None:
        """
        Test fallback to bid_price when last_price and ask_price are None.

        Scenario:
            - AAPL: last_price=None, ask_price=None, bid=$152.70
            - Expected: Use bid_price
        """
        mock_executor = MagicMock()
        mock_executor.get_latest_quotes.return_value = {
            "AAPL": {
                "last_price": None,
                "ask_price": None,
                "bid_price": Decimal("152.70"),
            }
        }

        with patch("scripts.ops.paper_run.AlpacaExecutor", return_value=mock_executor):
            config: dict[str, Any] = {}
            prices = await fetch_current_prices(["AAPL"], config)

        assert prices == {"AAPL": Decimal("152.70")}

    @pytest.mark.asyncio()
    async def test_no_price_data_available(self, capsys: pytest.CaptureFixture[str]) -> None:
        """
        Test handling when no price data available for symbol.

        Scenario:
            - AAPL: last_price=None, ask_price=None, bid_price=None
            - Expected: Symbol not in result, warning printed
        """
        mock_executor = MagicMock()
        mock_executor.get_latest_quotes.return_value = {
            "AAPL": {
                "last_price": None,
                "ask_price": None,
                "bid_price": None,
            }
        }

        with patch("scripts.ops.paper_run.AlpacaExecutor", return_value=mock_executor):
            config: dict[str, Any] = {}
            prices = await fetch_current_prices(["AAPL"], config)

        # Symbol should not be in result
        assert prices == {}

        # Check warning was printed
        captured = capsys.readouterr()
        assert "Warning: No price data for AAPL" in captured.err

    @pytest.mark.asyncio()
    async def test_partial_price_data_mixed_symbols(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        Test handling mixed symbols (some with prices, some without).

        Scenario:
            - AAPL: has last_price
            - MSFT: no price data
            - GOOGL: has mid-quote
            - Expected: Only AAPL and GOOGL in result, warning for MSFT
        """
        mock_executor = MagicMock()
        mock_executor.get_latest_quotes.return_value = {
            "AAPL": {"last_price": Decimal("152.75"), "ask_price": None, "bid_price": None},
            "MSFT": {"last_price": None, "ask_price": None, "bid_price": None},
            "GOOGL": {
                "last_price": Decimal("140.25"),  # Mid-quote from get_latest_quotes
                "ask_price": Decimal("140.30"),
                "bid_price": Decimal("140.20"),
            },
        }

        with patch("scripts.ops.paper_run.AlpacaExecutor", return_value=mock_executor):
            config: dict[str, Any] = {}
            prices = await fetch_current_prices(["AAPL", "MSFT", "GOOGL"], config)

        assert prices == {
            "AAPL": Decimal("152.75"),
            "GOOGL": Decimal("140.25"),
        }

        # Check warning for MSFT
        captured = capsys.readouterr()
        assert "Warning: No price data for MSFT" in captured.err

    @pytest.mark.asyncio()
    async def test_alpaca_connection_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """
        Test graceful handling of Alpaca connection error.

        Scenario:
            - Alpaca API is down
            - Expected: Returns empty dict, prints warning
        """
        mock_executor = MagicMock()
        mock_executor.get_latest_quotes.side_effect = AlpacaConnectionError("API unavailable")

        with patch("scripts.ops.paper_run.AlpacaExecutor", return_value=mock_executor):
            config: dict[str, Any] = {}
            prices = await fetch_current_prices(["AAPL"], config)

        # Should return empty dict (graceful degradation)
        assert prices == {}

        # Check warning was printed
        captured = capsys.readouterr()
        assert "Warning: Failed to fetch prices from Alpaca" in captured.err
        assert "Falling back to avg_entry_price" in captured.err

    @pytest.mark.asyncio()
    async def test_unexpected_exception(self, capsys: pytest.CaptureFixture[str]) -> None:
        """
        Test handling of unexpected exception during price fetching.

        Scenario:
            - Unexpected error (e.g., network timeout, JSON parse error)
            - Expected: Returns empty dict, prints error
        """
        mock_executor = MagicMock()
        mock_executor.get_latest_quotes.side_effect = Exception("Unexpected error")

        with patch("scripts.ops.paper_run.AlpacaExecutor", return_value=mock_executor):
            config: dict[str, Any] = {}
            prices = await fetch_current_prices(["AAPL"], config)

        # Should return empty dict (graceful degradation)
        assert prices == {}

        # Check error was logged
        captured = capsys.readouterr()
        assert "Unexpected error fetching prices" in captured.err

    @pytest.mark.asyncio()
    async def test_empty_symbols_list(self) -> None:
        """
        Test handling of empty symbols list.

        Scenario:
            - No symbols requested
            - Expected: Returns empty dict, no API call
        """
        config: dict[str, Any] = {}
        prices = await fetch_current_prices([], config)

        # Should return empty dict without calling API
        assert prices == {}

    @pytest.mark.asyncio()
    async def test_alpaca_client_initialization(self) -> None:
        """
        Test AlpacaExecutor is initialized with correct credentials from env.

        Scenario:
            - Verify AlpacaExecutor initialized with env vars
            - ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL
        """
        mock_executor = MagicMock()
        mock_executor.get_latest_quotes.return_value = {}

        # Set env vars
        os.environ["ALPACA_API_KEY"] = "test_api_key"
        os.environ["ALPACA_SECRET_KEY"] = "test_secret_key"
        os.environ["ALPACA_BASE_URL"] = "https://test.alpaca.markets"

        with patch("scripts.ops.paper_run.AlpacaExecutor") as mock_alpaca_class:
            mock_alpaca_class.return_value = mock_executor
            config: dict[str, Any] = {}
            await fetch_current_prices(["AAPL"], config)

            # Verify initialization with correct credentials
            mock_alpaca_class.assert_called_once_with(
                api_key="test_api_key",
                secret_key="test_secret_key",
                base_url="https://test.alpaca.markets",
            )

    @pytest.mark.asyncio()
    async def test_config_overrides_environment_variables(self) -> None:
        """
        Test that config-provided credentials override environment variables.

        Scenario:
            - Environment has one set of credentials
            - Config dict provides different credentials
            - Expected: AlpacaExecutor receives config credentials, not env
        """
        mock_executor = MagicMock()
        mock_executor.get_latest_quotes.return_value = {}

        # Set env vars with "env_" prefix
        os.environ["ALPACA_API_KEY"] = "env_api_key"
        os.environ["ALPACA_SECRET_KEY"] = "env_secret_key"
        os.environ["ALPACA_BASE_URL"] = "https://env.alpaca.markets"

        # Provide different credentials via config
        config: dict[str, Any] = {
            "alpaca_api_key": "config_api_key",
            "alpaca_secret_key": "config_secret_key",
            "alpaca_base_url": "https://config.alpaca.markets",
        }

        with patch("scripts.ops.paper_run.AlpacaExecutor") as mock_alpaca_class:
            mock_alpaca_class.return_value = mock_executor
            await fetch_current_prices(["AAPL"], config)

            # Verify config credentials are used, not env vars
            mock_alpaca_class.assert_called_once_with(
                api_key="config_api_key",
                secret_key="config_secret_key",
                base_url="https://config.alpaca.markets",
            )

    @pytest.mark.asyncio()
    async def test_decimal_precision(self) -> None:
        """
        Test Decimal precision is maintained for prices.

        Scenario:
            - Prices with multiple decimal places
            - Expected: Decimal precision preserved (no float rounding)
        """
        mock_executor = MagicMock()
        mock_executor.get_latest_quotes.return_value = {
            "AAPL": {
                "last_price": Decimal("152.123456"),
                "ask_price": None,
                "bid_price": None,
            }
        }

        with patch("scripts.ops.paper_run.AlpacaExecutor", return_value=mock_executor):
            config: dict[str, Any] = {}
            prices = await fetch_current_prices(["AAPL"], config)

        # Verify precision preserved
        assert prices["AAPL"] == Decimal("152.123456")

    @pytest.mark.asyncio()
    async def test_mid_quote_from_latest_quotes_precision(self) -> None:
        """
        Test that mid-quote from get_latest_quotes() maintains Decimal precision.

        Note: get_latest_quotes() calculates mid-quote with Decimal precision
        when bid/ask are available. This test verifies precision is maintained.

        Scenario:
            - Bid: $152.123, Ask: $152.789
            - get_latest_quotes returns: last_price = $152.456 (mid-quote)
            - Expected: Decimal precision preserved
        """
        mock_executor = MagicMock()
        expected_mid = (Decimal("152.789") + Decimal("152.123")) / Decimal("2")
        mock_executor.get_latest_quotes.return_value = {
            "AAPL": {
                "last_price": expected_mid,  # Mid-quote from get_latest_quotes
                "ask_price": Decimal("152.789"),
                "bid_price": Decimal("152.123"),
            }
        }

        with patch("scripts.ops.paper_run.AlpacaExecutor", return_value=mock_executor):
            config: dict[str, Any] = {}
            prices = await fetch_current_prices(["AAPL"], config)

        # Verify mid-quote precision preserved
        assert prices["AAPL"] == expected_mid
