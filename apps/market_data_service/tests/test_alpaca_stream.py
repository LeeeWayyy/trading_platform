"""
Tests for AlpacaMarketDataStream.

Note: These are unit tests with mocked Alpaca SDK.
Integration tests with real Alpaca API are separate.
"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from redis.exceptions import RedisError

from libs.market_data.alpaca_stream import AlpacaMarketDataStream


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    redis = MagicMock()  # Changed from AsyncMock since RedisClient is synchronous
    redis.set = MagicMock()
    return redis


@pytest.fixture
def mock_publisher():
    """Mock event publisher."""
    publisher = MagicMock()  # Changed from AsyncMock since EventPublisher is synchronous
    publisher.publish = MagicMock()
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
    quote.timestamp = datetime.now(UTC)
    quote.ask_exchange = "NASDAQ"  # Fixed: Alpaca SDK uses ask_exchange, not exchange
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

        # Verify Redis cache was updated (changed from setex to set with ttl parameter)
        assert mock_redis.set.called
        call_args = mock_redis.set.call_args
        assert call_args[0][0] == "price:AAPL"  # Cache key
        # Second arg is JSON string, third arg (ttl keyword) is TTL
        assert call_args[1]["ttl"] == 300  # TTL as keyword argument

        # Verify event was published (changed to expect Pydantic model, not dict)
        assert mock_publisher.publish.called
        pub_call_args = mock_publisher.publish.call_args
        assert pub_call_args[0][0] == "price.updated.AAPL"  # Channel
        # Second argument should be a Pydantic model, not a dict
        from libs.market_data.types import PriceUpdateEvent

        assert isinstance(pub_call_args[0][1], PriceUpdateEvent)

    @pytest.mark.asyncio
    async def test_handle_quote_with_invalid_data(self, stream, mock_redis, mock_publisher):
        """Test handling quote with invalid data logs error but doesn't crash stream."""
        # Create quote with crossed market (ask < bid)
        bad_quote = Mock()
        bad_quote.symbol = "AAPL"
        bad_quote.bid_price = 150.10
        bad_quote.ask_price = 150.00  # Invalid: ask < bid
        bad_quote.bid_size = 100
        bad_quote.ask_size = 200
        bad_quote.timestamp = datetime.now(UTC)
        bad_quote.ask_exchange = "NASDAQ"  # Fixed: Alpaca SDK uses ask_exchange

        # Should NOT raise exception - stream should continue processing
        await stream._handle_quote(bad_quote)

        # Verify Redis was not updated (bad quote rejected)
        assert not mock_redis.set.called

        # Verify event was not published (bad quote rejected)
        assert not mock_publisher.publish.called

    @pytest.mark.asyncio
    async def test_stream_continues_after_bad_quote(
        self, stream, mock_alpaca_quote, mock_redis, mock_publisher
    ):
        """
        Test stream continues processing valid quotes after encountering a bad quote.

        This is a HIGH priority fix from automated review: if _handle_quote raises
        exceptions for bad data, it could crash the entire WebSocket stream since
        Alpaca SDK's asyncio.gather() doesn't use return_exceptions=True.

        The fix ensures _handle_quote logs errors but doesn't re-raise, allowing
        the stream to continue processing subsequent valid quotes.
        """
        # Create a bad quote (crossed market)
        bad_quote = Mock()
        bad_quote.symbol = "AAPL"
        bad_quote.bid_price = 150.10
        bad_quote.ask_price = 150.00  # Invalid: ask < bid
        bad_quote.bid_size = 100
        bad_quote.ask_size = 200
        bad_quote.timestamp = datetime.now(UTC)
        bad_quote.ask_exchange = "NASDAQ"  # Fixed: Alpaca SDK uses ask_exchange

        # Process bad quote - should NOT crash
        await stream._handle_quote(bad_quote)

        # Verify bad quote was rejected
        assert not mock_redis.set.called
        assert not mock_publisher.publish.called

        # Reset mocks
        mock_redis.set.reset_mock()
        mock_publisher.publish.reset_mock()

        # Now process a valid quote - stream should still work
        await stream._handle_quote(mock_alpaca_quote)

        # Verify valid quote was processed successfully
        assert mock_redis.set.called
        assert mock_publisher.publish.called

        # Verify the valid quote data is correct
        call_args = mock_redis.set.call_args
        assert call_args[0][0] == "price:AAPL"

        pub_call_args = mock_publisher.publish.call_args
        assert pub_call_args[0][0] == "price.updated.AAPL"

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
        stream._connected = True  # Changed from stream._running to self._connected

        assert stream.is_connected() is True

    def test_is_connected_when_not_running(self, stream):
        """Test is_connected returns False when stream is not running."""
        stream._running = False

        assert stream.is_connected() is False

    @pytest.mark.asyncio
    async def test_reconnect_counter_resets_after_successful_connection(self, stream):
        """
        Test that reconnect counter resets after successful connection.

        This is a P1 fix from automated review: without resetting the counter,
        transient network failures over the lifetime of a process accumulate
        toward max_reconnect_attempts (10), eventually causing permanent failure.

        The fix ensures reconnect counter resets to 0 after each successful connection.
        """
        # Simulate a scenario where connection fails twice, then succeeds
        stream._reconnect_attempts = 2  # Simulate 2 previous failed attempts

        # Mock stream.run() to succeed immediately (no exception)
        async def mock_run():
            # Simulate successful connection that runs and then gracefully closes
            await asyncio.sleep(0.01)  # Simulate brief connection
            # When run() returns normally, it means connection closed gracefully

        stream.stream.run = AsyncMock(side_effect=mock_run)

        # Start the stream in background
        task = asyncio.create_task(stream.start())

        # Wait briefly for connection to establish and counter to reset
        await asyncio.sleep(0.05)

        # Stop the stream to exit cleanly
        await stream.stop()

        # Wait for task to complete
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except TimeoutError:
            task.cancel()

        # Verify reconnect counter was reset to 0 after successful connection
        # (before it was 2, after successful run() it should be 0)
        assert stream._reconnect_attempts == 0

    @pytest.mark.asyncio
    async def test_concurrent_subscribe_with_lock(self, stream):
        """
        Test that concurrent subscribe calls are handled atomically with lock.

        MEDIUM priority fix: Without a lock, concurrent subscribe_symbols calls
        could lead to race conditions where:
        1. Both calls check subscribed_symbols at the same time
        2. Both think they need to subscribe to the same symbol
        3. Both call the broker API, hitting rate limits or causing duplicate subscriptions

        The fix uses asyncio.Lock to ensure subscription operations are atomic.
        """
        # Create multiple concurrent subscription tasks for the same symbol
        tasks = [
            stream.subscribe_symbols(["AAPL"]),
            stream.subscribe_symbols(["AAPL"]),
            stream.subscribe_symbols(["AAPL"]),
        ]

        # Run all tasks concurrently
        await asyncio.gather(*tasks)

        # Verify that subscribe_quotes was only called once (not three times)
        # This proves the lock prevented duplicate subscriptions
        assert stream.stream.subscribe_quotes.call_count == 1

        # Verify AAPL is in subscribed_symbols
        assert "AAPL" in stream.subscribed_symbols

    @pytest.mark.asyncio
    async def test_handle_quote_redis_timeout_does_not_crash_stream(
        self, stream, mock_alpaca_quote, mock_redis, mock_publisher
    ):
        """
        Test that Redis timeout errors don't crash the WebSocket stream.

        P1 fix: RedisClient.set() can raise RedisError subtypes (timeout, memory, etc.),
        not just RedisConnectionError. The _handle_quote callback must catch ALL
        RedisError types to prevent stream termination on transient Redis failures.

        This test verifies that when Redis.set() raises a RedisError (e.g., timeout),
        the stream logs the error but continues processing.
        """
        # Mock Redis.set() to raise RedisError (simulating timeout)
        mock_redis.set.side_effect = RedisError("Connection timeout")

        # Process quote - should NOT raise exception
        await stream._handle_quote(mock_alpaca_quote)

        # Verify Redis.set() was called (and failed)
        assert mock_redis.set.called

        # Verify event was not published (quote processing failed)
        assert not mock_publisher.publish.called

        # Reset mocks
        mock_redis.set.reset_mock()
        mock_redis.set.side_effect = None  # Remove error
        mock_publisher.publish.reset_mock()

        # Now process a valid quote - stream should still work
        await stream._handle_quote(mock_alpaca_quote)

        # Verify valid quote was processed successfully
        assert mock_redis.set.called
        assert mock_publisher.publish.called

    @pytest.mark.asyncio
    async def test_handle_quote_with_invalid_decimal_does_not_crash_stream(
        self, stream, mock_redis, mock_publisher
    ):
        """
        Test that invalid decimal values (NaN, None, etc.) don't crash the stream.

        P1 fix: Decimal(str(...)) can raise InvalidOperation for NaN, None, or
        non-numeric values. The _handle_quote callback must catch InvalidOperation
        to prevent stream termination on malformed quote data.

        This test verifies that when quote contains invalid price data (NaN),
        the stream logs the error but continues processing.
        """
        # Create quote with NaN price (triggers InvalidOperation)
        bad_quote = Mock()
        bad_quote.symbol = "AAPL"
        bad_quote.bid_price = float(
            "nan"
        )  # Invalid: will cause Decimal(str(...)) to raise InvalidOperation
        bad_quote.ask_price = 150.10
        bad_quote.bid_size = 100
        bad_quote.ask_size = 200
        bad_quote.timestamp = datetime.now(UTC)
        bad_quote.ask_exchange = "NASDAQ"  # Fixed: Alpaca SDK uses ask_exchange

        # Process quote - should NOT raise exception
        await stream._handle_quote(bad_quote)

        # Verify Redis was not updated (bad quote rejected)
        assert not mock_redis.set.called

        # Verify event was not published (bad quote rejected)
        assert not mock_publisher.publish.called

    @pytest.mark.asyncio
    async def test_handle_quote_missing_symbol_attribute_does_not_crash_stream(
        self, stream, mock_redis, mock_publisher
    ):
        """
        Test that quotes missing symbol attribute don't crash the stream.

        P1 fix: Logging quote.symbol in exception handler can raise AttributeError
        if the quote object is missing the symbol attribute. Must use getattr()
        with default value to safely access symbol in error logging.

        This test verifies that when quote is missing symbol attribute,
        the stream logs the error with <unknown> and continues processing.
        """
        # Create quote object missing symbol attribute
        bad_quote = Mock(spec=[])  # Empty spec = no attributes
        bad_quote.bid_price = 150.00
        bad_quote.ask_price = 150.10
        bad_quote.bid_size = 100
        bad_quote.ask_size = 200
        bad_quote.timestamp = datetime.now(UTC)
        bad_quote.ask_exchange = "NASDAQ"  # Fixed: Alpaca SDK uses ask_exchange

        # Process quote - should NOT raise exception even when accessing symbol fails
        await stream._handle_quote(bad_quote)

        # Verify Redis was not updated (bad quote rejected)
        assert not mock_redis.set.called

        # Verify event was not published (bad quote rejected)
        assert not mock_publisher.publish.called
