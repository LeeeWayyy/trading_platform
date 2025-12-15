"""
Tests for M3: Mid-Price None Handling.

M3 Fix: Ensures mid-price calculation handles None values gracefully.
The previous code only checked hasattr() which doesn't catch None values,
leading to InvalidOperation when calling Decimal(str(None)).

Test scenarios:
- Both bid/ask valid → correct mid-price
- One or both None → returns None (no crash)
- Missing attributes → returns None
- Zero values → valid calculation (edge case)
"""

from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest

from apps.execution_gateway.alpaca_client import AlpacaExecutor


class MockQuote:
    """Mock quote object for testing."""

    def __init__(
        self,
        ap: Any = None,
        bp: Any = None,
        has_ap: bool = True,
        has_bp: bool = True,
        timestamp: str | None = "2024-01-01T10:00:00Z",
    ) -> None:
        self.timestamp = timestamp
        if has_ap:
            self.ap = ap
        if has_bp:
            self.bp = bp


class TestMidpriceNoneHandling:
    """Test suite for M3 mid-price None handling."""

    @pytest.fixture()
    def mock_alpaca_executor(self) -> AlpacaExecutor:
        """Create AlpacaExecutor with mocked dependencies."""
        with patch("apps.execution_gateway.alpaca_client.TradingClient"):
            with patch("apps.execution_gateway.alpaca_client.StockHistoricalDataClient"):
                executor = AlpacaExecutor(
                    api_key="test_key",
                    secret_key="test_secret",
                    paper=True,
                )
                return executor

    def test_midprice_both_valid(self, mock_alpaca_executor: AlpacaExecutor) -> None:
        """Mid-price calculated correctly when both bid and ask are present and non-None."""
        quote = MockQuote(ap=100.0, bp=99.0)
        mock_quotes = {"AAPL": quote}

        with patch.object(
            mock_alpaca_executor.data_client,
            "get_stock_latest_quote",
            return_value=mock_quotes,
        ):
            result = mock_alpaca_executor.get_latest_quotes(["AAPL"])

        assert "AAPL" in result
        assert result["AAPL"]["ask_price"] == Decimal("100.0")
        assert result["AAPL"]["bid_price"] == Decimal("99.0")
        assert result["AAPL"]["last_price"] == Decimal("99.5")  # (100 + 99) / 2

    def test_midprice_ask_none(self, mock_alpaca_executor: AlpacaExecutor) -> None:
        """Returns None when ask price (ap) is None."""
        quote = MockQuote(ap=None, bp=99.0)
        mock_quotes = {"AAPL": quote}

        with patch.object(
            mock_alpaca_executor.data_client,
            "get_stock_latest_quote",
            return_value=mock_quotes,
        ):
            result = mock_alpaca_executor.get_latest_quotes(["AAPL"])

        assert "AAPL" in result
        assert result["AAPL"]["ask_price"] is None
        assert result["AAPL"]["bid_price"] is None
        assert result["AAPL"]["last_price"] is None

    def test_midprice_bid_none(self, mock_alpaca_executor: AlpacaExecutor) -> None:
        """Returns None when bid price (bp) is None."""
        quote = MockQuote(ap=100.0, bp=None)
        mock_quotes = {"AAPL": quote}

        with patch.object(
            mock_alpaca_executor.data_client,
            "get_stock_latest_quote",
            return_value=mock_quotes,
        ):
            result = mock_alpaca_executor.get_latest_quotes(["AAPL"])

        assert "AAPL" in result
        assert result["AAPL"]["ask_price"] is None
        assert result["AAPL"]["bid_price"] is None
        assert result["AAPL"]["last_price"] is None

    def test_midprice_both_none(self, mock_alpaca_executor: AlpacaExecutor) -> None:
        """Returns None when both bid and ask are None."""
        quote = MockQuote(ap=None, bp=None)
        mock_quotes = {"AAPL": quote}

        with patch.object(
            mock_alpaca_executor.data_client,
            "get_stock_latest_quote",
            return_value=mock_quotes,
        ):
            result = mock_alpaca_executor.get_latest_quotes(["AAPL"])

        assert "AAPL" in result
        assert result["AAPL"]["ask_price"] is None
        assert result["AAPL"]["bid_price"] is None
        assert result["AAPL"]["last_price"] is None

    def test_midprice_missing_ask_attribute(self, mock_alpaca_executor: AlpacaExecutor) -> None:
        """Returns None when ask price attribute is missing entirely."""
        quote = MockQuote(bp=99.0, has_ap=False)
        mock_quotes = {"AAPL": quote}

        with patch.object(
            mock_alpaca_executor.data_client,
            "get_stock_latest_quote",
            return_value=mock_quotes,
        ):
            result = mock_alpaca_executor.get_latest_quotes(["AAPL"])

        assert "AAPL" in result
        assert result["AAPL"]["ask_price"] is None
        assert result["AAPL"]["bid_price"] is None
        assert result["AAPL"]["last_price"] is None

    def test_midprice_missing_bid_attribute(self, mock_alpaca_executor: AlpacaExecutor) -> None:
        """Returns None when bid price attribute is missing entirely."""
        quote = MockQuote(ap=100.0, has_bp=False)
        mock_quotes = {"AAPL": quote}

        with patch.object(
            mock_alpaca_executor.data_client,
            "get_stock_latest_quote",
            return_value=mock_quotes,
        ):
            result = mock_alpaca_executor.get_latest_quotes(["AAPL"])

        assert "AAPL" in result
        assert result["AAPL"]["ask_price"] is None
        assert result["AAPL"]["bid_price"] is None
        assert result["AAPL"]["last_price"] is None

    def test_midprice_zero_values(self, mock_alpaca_executor: AlpacaExecutor) -> None:
        """Zero values are valid and should calculate correctly (edge case)."""
        quote = MockQuote(ap=0.0, bp=0.0)
        mock_quotes = {"AAPL": quote}

        with patch.object(
            mock_alpaca_executor.data_client,
            "get_stock_latest_quote",
            return_value=mock_quotes,
        ):
            result = mock_alpaca_executor.get_latest_quotes(["AAPL"])

        assert "AAPL" in result
        # Zero is a valid price (unlikely but possible in edge cases)
        assert result["AAPL"]["ask_price"] == Decimal("0.0")
        assert result["AAPL"]["bid_price"] == Decimal("0.0")
        assert result["AAPL"]["last_price"] == Decimal("0")
