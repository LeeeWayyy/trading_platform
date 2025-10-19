"""
Tests for AlpacaMarketDataStream.

Note: These are unit tests with mocked Alpaca SDK.
Integration tests with real Alpaca API are separate.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from libs.market_data.alpaca_stream import AlpacaMarketDataStream
from libs.market_data.exceptions import QuoteHandlingError


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    redis = AsyncMock()
    redis.setex = AsyncMock()
    return redis


@pytest.fixture
def mock_publisher():
    """Mock event publisher."""
    publisher = AsyncMock()
    publisher.publish = AsyncMock()
    return publisher


@pytest.fixture
def mock_alpaca_quote():
    """Mock Alpaca Quote object."""
    quote = Mock()
    quote.symbol = "AAPL"
    quote.bid_price = 150.00
    quote.ask_price = 150.10
    quote.bid_size = 100
    quote.ask_size = 200
    quote.timestamp = datetime.now(timezone.utc)
    quote.exchange = "NASDAQ"
    return quote


@pytest.fixture
def stream(mock_redis, mock_publisher):
    """Create AlpacaMarketDataStream with mocked dependencies."""
    with patch("libs.market_data.alpaca_stream.StockDataStream") as mock_stream_class:
        mock_stream_instance = MagicMock()
        mock_stream_class.return_value = mock_stream_instance

        stream = AlpacaMarketDataStream(
            api_key="test_key",
            secret_key="test_secret",
            redis_client=mock_redis,
            event_publisher=mock_publisher,
            price_ttl=300,
        )

        stream.stream = mock_stream_instance
        yield stream


class TestAlpacaMarketDataStream:
    """Tests for AlpacaMarketDataStream."""

    def test_initialization(self, stream):
        """Test stream initialization."""
        assert stream.api_key == "test_key"
        assert stream.secret_key == "test_secret"
        assert stream.price_ttl == 300
        assert stream.subscribed_symbols == set()
        assert stream._running is False
        assert stream._reconnect_attempts == 0

    @pytest.mark.asyncio
    async def test_subscribe_symbols(self, stream):
        """Test subscribing to symbols."""
        symbols = ["AAPL", "MSFT", "GOOGL"]

        await stream.subscribe_symbols(symbols)

        assert stream.subscribed_symbols == set(symbols)
        assert stream.stream.subscribe_quotes.called

    @pytest.mark.asyncio
    async def test_subscribe_empty_list(self, stream):
        """Test subscribing with empty list does nothing."""
        await stream.subscribe_symbols([])

        assert stream.subscribed_symbols == set()
        assert not stream.stream.subscribe_quotes.called

    @pytest.mark.asyncio
    async def test_subscribe_duplicate_symbols(self, stream):
        """Test subscribing to already subscribed symbols."""
        # First subscription
        await stream.subscribe_symbols(["AAPL", "MSFT"])

        # Reset mock
        stream.stream.subscribe_quotes.reset_mock()

        # Try to subscribe again (should filter duplicates)
        await stream.subscribe_symbols(["AAPL", "GOOGL"])

        # Should only subscribe to GOOGL
        assert stream.subscribed_symbols == {"AAPL", "MSFT", "GOOGL"}

    @pytest.mark.asyncio
    async def test_unsubscribe_symbols(self, stream):
        """Test unsubscribing from symbols."""
        # First subscribe
        await stream.subscribe_symbols(["AAPL", "MSFT", "GOOGL"])

        # Then unsubscribe
        await stream.unsubscribe_symbols(["AAPL", "MSFT"])

        assert stream.subscribed_symbols == {"GOOGL"}

    @pytest.mark.asyncio
    async def test_handle_quote(self, stream, mock_alpaca_quote, mock_redis, mock_publisher):
        """Test handling incoming quote."""
        await stream._handle_quote(mock_alpaca_quote)

        # Verify Redis cache was updated
        assert mock_redis.setex.called
        call_args = mock_redis.setex.call_args
        assert call_args[0][0] == "price:AAPL"  # Cache key
        assert call_args[0][1] == 300  # TTL

        # Verify event was published
        assert mock_publisher.publish.called
        pub_call_args = mock_publisher.publish.call_args
        assert pub_call_args[0][0] == "price.updated.AAPL"  # Channel

    @pytest.mark.asyncio
    async def test_handle_quote_with_invalid_data(self, stream):
        """Test handling quote with invalid data raises error."""
        # Create quote with crossed market (ask < bid)
        bad_quote = Mock()
        bad_quote.symbol = "AAPL"
        bad_quote.bid_price = 150.10
        bad_quote.ask_price = 150.00  # Invalid: ask < bid
        bad_quote.bid_size = 100
        bad_quote.ask_size = 200
        bad_quote.timestamp = datetime.now(timezone.utc)
        bad_quote.exchange = "NASDAQ"

        with pytest.raises(QuoteHandlingError):
            await stream._handle_quote(bad_quote)

    def test_get_subscribed_symbols(self, stream):
        """Test getting list of subscribed symbols."""
        stream.subscribed_symbols = {"AAPL", "MSFT", "GOOGL"}

        symbols = stream.get_subscribed_symbols()

        assert sorted(symbols) == ["AAPL", "GOOGL", "MSFT"]

    def test_get_connection_stats(self, stream):
        """Test getting connection statistics."""
        stream._running = True
        stream._reconnect_attempts = 2
        stream.subscribed_symbols = {"AAPL", "MSFT"}

        stats = stream.get_connection_stats()

        assert stats["subscribed_symbols"] == 2
        assert stats["reconnect_attempts"] == 2
        assert stats["max_reconnect_attempts"] == 10

    def test_is_connected_when_running(self, stream):
        """Test is_connected returns True when stream is running."""
        stream._running = True
        stream.stream._running = True

        assert stream.is_connected() is True

    def test_is_connected_when_not_running(self, stream):
        """Test is_connected returns False when stream is not running."""
        stream._running = False

        assert stream.is_connected() is False
