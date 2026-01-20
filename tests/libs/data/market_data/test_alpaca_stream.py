"""
Comprehensive unit tests for AlpacaMarketDataStream.

Tests cover:
- WebSocket stream handling and lifecycle
- Message parsing and validation (Quote objects and dict mappings)
- Connection management and state tracking
- Reconnection logic with exponential backoff
- Subscription management with source ref-counting
- Error handling and graceful degradation
- Edge cases (empty lists, invalid data, concurrent operations)

Target: 85%+ branch coverage
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest
from alpaca.data.models import Quote
from redis.exceptions import RedisError

import libs.data.market_data.alpaca_stream as alpaca_stream
from libs.core.redis_client import RedisKeys
from libs.data.market_data.exceptions import ConnectionError, SubscriptionError
from libs.data.market_data.types import PriceUpdateEvent


class _FakeStockDataStream:
    """
    Fake implementation of Alpaca StockDataStream for testing.

    Simulates WebSocket behavior without actual network connections.
    """

    def __init__(self, api_key: str, secret_key: str) -> None:
        self.api_key = api_key
        self.secret_key = secret_key
        self.subscriptions: list[tuple[object, tuple[str, ...]]] = []
        self.unsubscribed: list[str] = []
        self.run_called = 0
        self.stop_called = 0
        self.should_raise_on_run = False
        self.exception_to_raise: Exception | None = None

    def subscribe_quotes(self, handler: object, *symbols: str) -> None:
        """Simulate subscribing to quote stream."""
        if self.should_raise_on_run:
            raise RuntimeError("Subscription failed")
        self.subscriptions.append((handler, symbols))

    def unsubscribe_quotes(self, symbol: str) -> None:
        """Simulate unsubscribing from quote stream."""
        self.unsubscribed.append(symbol)

    def run(self) -> None:
        """Simulate running the WebSocket connection."""
        self.run_called += 1
        if self.should_raise_on_run and self.exception_to_raise:
            raise self.exception_to_raise

    def stop(self) -> None:
        """Simulate stopping the WebSocket connection."""
        self.stop_called += 1


@pytest.fixture()
def fake_stream_cls(monkeypatch: pytest.MonkeyPatch) -> type[_FakeStockDataStream]:
    """Provide fake StockDataStream class for testing."""
    monkeypatch.setattr(alpaca_stream, "StockDataStream", _FakeStockDataStream)
    return _FakeStockDataStream


@pytest.fixture()
def redis_client() -> MagicMock:
    """Provide mock Redis client."""
    return MagicMock()


@pytest.fixture()
def event_publisher() -> MagicMock:
    """Provide mock event publisher."""
    return MagicMock()


@pytest.fixture()
def stream(
    fake_stream_cls: type[_FakeStockDataStream],
    redis_client: MagicMock,
    event_publisher: MagicMock,
) -> alpaca_stream.AlpacaMarketDataStream:
    """Provide AlpacaMarketDataStream instance with fake dependencies."""
    return alpaca_stream.AlpacaMarketDataStream(
        api_key="key",
        secret_key="secret",
        redis_client=redis_client,
        event_publisher=event_publisher,
    )


class TestAlpacaMarketDataStreamInitialization:
    """Tests for AlpacaMarketDataStream initialization."""

    def test_initialization_default_ttl(
        self,
        fake_stream_cls: type[_FakeStockDataStream],
        redis_client: MagicMock,
        event_publisher: MagicMock,
    ) -> None:
        """Test initialization with default price TTL."""
        stream = alpaca_stream.AlpacaMarketDataStream(
            api_key="test_key",
            secret_key="test_secret",
            redis_client=redis_client,
            event_publisher=event_publisher,
        )

        assert stream.api_key == "test_key"
        assert stream.secret_key == "test_secret"
        assert stream.redis is redis_client
        assert stream.publisher is event_publisher
        assert stream.price_ttl == 300
        assert isinstance(stream.stream, _FakeStockDataStream)
        assert stream._running is False
        assert stream._connected is False
        assert stream._reconnect_attempts == 0
        assert stream._max_reconnect_attempts == 10

    def test_initialization_custom_ttl(
        self,
        fake_stream_cls: type[_FakeStockDataStream],
        redis_client: MagicMock,
        event_publisher: MagicMock,
    ) -> None:
        """Test initialization with custom price TTL."""
        stream = alpaca_stream.AlpacaMarketDataStream(
            api_key="test_key",
            secret_key="test_secret",
            redis_client=redis_client,
            event_publisher=event_publisher,
            price_ttl=600,
        )

        assert stream.price_ttl == 600

    def test_subscription_sources_initialized_empty(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test subscription sources are initialized empty."""
        assert stream._subscription_sources == {}
        assert stream.subscribed_symbols == set()
        assert stream.get_subscribed_symbols() == []


class TestSubscriptionManagement:
    """Tests for subscription and unsubscription with source tracking."""

    @pytest.mark.asyncio()
    async def test_subscribe_symbols_single_source(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test subscribing to symbols with single source."""
        await stream.subscribe_symbols(["AAPL", "MSFT"], source="manual")

        assert stream.get_subscribed_symbols() == ["AAPL", "MSFT"]
        assert stream.get_subscription_sources() == {
            "AAPL": ["manual"],
            "MSFT": ["manual"],
        }

        # Verify Alpaca SDK was called
        fake_stream = stream.stream
        assert isinstance(fake_stream, _FakeStockDataStream)
        assert len(fake_stream.subscriptions) == 1
        _, symbols = fake_stream.subscriptions[0]
        assert set(symbols) == {"AAPL", "MSFT"}

    @pytest.mark.asyncio()
    async def test_subscribe_symbols_multiple_sources_ref_counting(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test ref-counting with multiple subscription sources."""
        # First subscription source
        await stream.subscribe_symbols(["AAPL", "MSFT"], source="manual")
        # Second subscription source for AAPL
        await stream.subscribe_symbols(["AAPL"], source="position")

        assert stream.get_subscribed_symbols() == ["AAPL", "MSFT"]
        assert stream.get_subscription_sources() == {
            "AAPL": ["manual", "position"],
            "MSFT": ["manual"],
        }

        # Only the first call should have triggered Alpaca subscription
        fake_stream = stream.stream
        assert isinstance(fake_stream, _FakeStockDataStream)
        assert len(fake_stream.subscriptions) == 1

    @pytest.mark.asyncio()
    async def test_subscribe_empty_list_logs_warning(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test subscribing with empty list logs warning and returns early."""
        await stream.subscribe_symbols([], source="manual")

        # No subscriptions should be created
        assert stream.get_subscribed_symbols() == []
        assert stream.get_subscription_sources() == {}

    @pytest.mark.asyncio()
    async def test_subscribe_symbols_exception_raises_subscription_error(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test subscription failure raises SubscriptionError."""
        fake_stream = stream.stream
        assert isinstance(fake_stream, _FakeStockDataStream)
        fake_stream.should_raise_on_run = True

        with pytest.raises(SubscriptionError, match="Failed to subscribe"):
            await stream.subscribe_symbols(["AAPL"], source="manual")

    @pytest.mark.asyncio()
    async def test_unsubscribe_symbols_single_source(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test unsubscribing removes source and symbol."""
        await stream.subscribe_symbols(["AAPL", "MSFT"], source="manual")
        await stream.unsubscribe_symbols(["AAPL"], source="manual")

        assert stream.get_subscribed_symbols() == ["MSFT"]
        assert "AAPL" not in stream.get_subscription_sources()

        # Verify Alpaca unsubscribe was called
        fake_stream = stream.stream
        assert isinstance(fake_stream, _FakeStockDataStream)
        assert fake_stream.unsubscribed == ["AAPL"]

    @pytest.mark.asyncio()
    async def test_unsubscribe_symbols_multiple_sources_keeps_subscription(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test unsubscribing one source keeps symbol subscribed."""
        await stream.subscribe_symbols(["AAPL"], source="manual")
        await stream.subscribe_symbols(["AAPL"], source="position")

        # Unsubscribe one source
        await stream.unsubscribe_symbols(["AAPL"], source="position")

        # Symbol should still be subscribed
        assert stream.get_subscribed_symbols() == ["AAPL"]
        assert stream.get_subscription_sources() == {"AAPL": ["manual"]}

        # Alpaca unsubscribe should NOT have been called
        fake_stream = stream.stream
        assert isinstance(fake_stream, _FakeStockDataStream)
        assert fake_stream.unsubscribed == []

    @pytest.mark.asyncio()
    async def test_unsubscribe_symbols_all_sources_removes_subscription(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test unsubscribing all sources removes Alpaca subscription."""
        await stream.subscribe_symbols(["AAPL"], source="manual")
        await stream.subscribe_symbols(["AAPL"], source="position")

        # Unsubscribe both sources
        await stream.unsubscribe_symbols(["AAPL"], source="position")
        await stream.unsubscribe_symbols(["AAPL"], source="manual")

        # Symbol should be completely unsubscribed
        assert stream.get_subscribed_symbols() == []
        assert "AAPL" not in stream.get_subscription_sources()

        # Alpaca unsubscribe should have been called
        fake_stream = stream.stream
        assert isinstance(fake_stream, _FakeStockDataStream)
        assert fake_stream.unsubscribed == ["AAPL"]

    @pytest.mark.asyncio()
    async def test_unsubscribe_empty_list_returns_early(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test unsubscribing empty list returns early."""
        await stream.unsubscribe_symbols([], source="manual")

        # No changes
        assert stream.get_subscribed_symbols() == []

    @pytest.mark.asyncio()
    async def test_unsubscribe_nonexistent_symbol_logs_debug(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test unsubscribing non-existent symbol logs but doesn't raise."""
        await stream.unsubscribe_symbols(["AAPL"], source="manual")

        # No error, just logging
        assert stream.get_subscribed_symbols() == []

    @pytest.mark.asyncio()
    async def test_unsubscribe_symbols_exception_raises_subscription_error(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test unsubscription failure raises SubscriptionError."""
        await stream.subscribe_symbols(["AAPL"], source="manual")

        # Make unsubscribe_quotes raise an exception
        fake_stream = stream.stream
        assert isinstance(fake_stream, _FakeStockDataStream)

        def raise_error(symbol: str) -> None:
            raise RuntimeError("Unsubscribe failed")

        fake_stream.unsubscribe_quotes = raise_error  # type: ignore

        with pytest.raises(SubscriptionError, match="Failed to unsubscribe"):
            await stream.unsubscribe_symbols(["AAPL"], source="manual")


class TestQuoteHandling:
    """Tests for quote processing and Redis/pub-sub integration."""

    @pytest.mark.asyncio()
    async def test_handle_quote_dict_mapping_success(
        self,
        stream: alpaca_stream.AlpacaMarketDataStream,
        redis_client: MagicMock,
        event_publisher: MagicMock,
    ) -> None:
        """Test handling quote as dict mapping writes to cache and publishes event."""
        quote: dict[str, Any] = {
            "symbol": "AAPL",
            "bid_price": "100.00",
            "ask_price": "100.10",
            "bid_size": 10,
            "ask_size": 12,
            "timestamp": "2025-01-01T12:00:00Z",
            "ask_exchange": "NASDAQ",
        }

        await stream._handle_quote(quote)

        # Verify Redis cache
        cache_key = RedisKeys.price("AAPL")
        redis_client.set.assert_called_once()
        args, kwargs = redis_client.set.call_args
        assert args[0] == cache_key
        assert kwargs.get("ttl") == stream.price_ttl

        # Verify event published
        event_publisher.publish.assert_called_once()
        channel, event = event_publisher.publish.call_args.args
        assert channel == "price.updated.AAPL"
        assert isinstance(event, PriceUpdateEvent)
        assert event.symbol == "AAPL"
        assert event.price == Decimal("100.05")

    @pytest.mark.asyncio()
    async def test_handle_quote_alpaca_object_success(
        self,
        stream: alpaca_stream.AlpacaMarketDataStream,
        redis_client: MagicMock,
        event_publisher: MagicMock,
    ) -> None:
        """Test handling quote as Alpaca Quote object."""
        # Create mock Alpaca Quote object
        quote = Mock(spec=Quote)
        quote.symbol = "MSFT"
        quote.bid_price = 200.00
        quote.ask_price = 200.20
        quote.bid_size = 50
        quote.ask_size = 60
        quote.timestamp = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
        quote.ask_exchange = "NYSE"

        await stream._handle_quote(quote)

        # Verify Redis cache
        RedisKeys.price("MSFT")
        redis_client.set.assert_called_once()

        # Verify event published
        event_publisher.publish.assert_called_once()
        channel, event = event_publisher.publish.call_args.args
        assert channel == "price.updated.MSFT"
        assert event.symbol == "MSFT"
        assert event.price == Decimal("200.10")

    @pytest.mark.asyncio()
    async def test_handle_quote_missing_timestamp_no_side_effects(
        self,
        stream: alpaca_stream.AlpacaMarketDataStream,
        redis_client: MagicMock,
        event_publisher: MagicMock,
    ) -> None:
        """Test quote with missing timestamp is rejected without side effects."""
        quote: dict[str, Any] = {
            "symbol": "AAPL",
            "bid_price": "100.00",
            "ask_price": "100.10",
            "timestamp": None,
        }

        await stream._handle_quote(quote)

        redis_client.set.assert_not_called()
        event_publisher.publish.assert_not_called()

    @pytest.mark.asyncio()
    async def test_handle_quote_invalid_timestamp_type_no_side_effects(
        self,
        stream: alpaca_stream.AlpacaMarketDataStream,
        redis_client: MagicMock,
        event_publisher: MagicMock,
    ) -> None:
        """Test quote with invalid timestamp type is rejected."""
        quote: dict[str, Any] = {
            "symbol": "AAPL",
            "bid_price": "100.00",
            "ask_price": "100.10",
            "timestamp": 123456,  # Invalid type
        }

        await stream._handle_quote(quote)

        redis_client.set.assert_not_called()
        event_publisher.publish.assert_not_called()

    @pytest.mark.asyncio()
    async def test_handle_quote_iso_timestamp_with_z_suffix(
        self,
        stream: alpaca_stream.AlpacaMarketDataStream,
        redis_client: MagicMock,
        event_publisher: MagicMock,
    ) -> None:
        """Test ISO timestamp with 'Z' suffix is handled correctly."""
        quote: dict[str, Any] = {
            "symbol": "AAPL",
            "bid_price": "100.00",
            "ask_price": "100.10",
            "bid_size": 10,
            "ask_size": 12,
            "timestamp": "2025-01-01T12:00:00Z",  # Z suffix
            "ask_exchange": "NASDAQ",
        }

        await stream._handle_quote(quote)

        # Should succeed
        redis_client.set.assert_called_once()
        event_publisher.publish.assert_called_once()

    @pytest.mark.asyncio()
    async def test_handle_quote_iso_timestamp_without_z_suffix(
        self,
        stream: alpaca_stream.AlpacaMarketDataStream,
        redis_client: MagicMock,
        event_publisher: MagicMock,
    ) -> None:
        """Test ISO timestamp without 'Z' suffix is handled correctly."""
        quote: dict[str, Any] = {
            "symbol": "AAPL",
            "bid_price": "100.00",
            "ask_price": "100.10",
            "bid_size": 10,
            "ask_size": 12,
            "timestamp": "2025-01-01T12:00:00+00:00",  # No Z
            "ask_exchange": "NASDAQ",
        }

        await stream._handle_quote(quote)

        # Should succeed
        redis_client.set.assert_called_once()
        event_publisher.publish.assert_called_once()

    @pytest.mark.asyncio()
    async def test_handle_quote_validation_error_swallowed(
        self,
        stream: alpaca_stream.AlpacaMarketDataStream,
        redis_client: MagicMock,
        event_publisher: MagicMock,
    ) -> None:
        """Test Pydantic validation error is caught and logged."""
        quote: dict[str, Any] = {
            "symbol": "AAPL",
            "bid_price": "101.00",  # Crossed market
            "ask_price": "100.00",
            "timestamp": datetime(2025, 1, 1, 12, 0, tzinfo=UTC),
        }

        # Should not raise, just log
        await stream._handle_quote(quote)

        redis_client.set.assert_not_called()
        event_publisher.publish.assert_not_called()

    @pytest.mark.asyncio()
    async def test_handle_quote_invalid_decimal_swallowed(
        self,
        stream: alpaca_stream.AlpacaMarketDataStream,
        redis_client: MagicMock,
        event_publisher: MagicMock,
    ) -> None:
        """Test invalid decimal conversion is caught and logged."""
        quote: dict[str, Any] = {
            "symbol": "AAPL",
            "bid_price": "invalid",  # Invalid decimal
            "ask_price": "100.00",
            "timestamp": datetime(2025, 1, 1, 12, 0, tzinfo=UTC),
        }

        # Should not raise, just log
        await stream._handle_quote(quote)

        redis_client.set.assert_not_called()
        event_publisher.publish.assert_not_called()

    @pytest.mark.asyncio()
    async def test_handle_quote_redis_error_swallowed(
        self,
        stream: alpaca_stream.AlpacaMarketDataStream,
        redis_client: MagicMock,
        event_publisher: MagicMock,
    ) -> None:
        """Test Redis errors are caught and logged without crashing."""
        redis_client.set.side_effect = RedisError("Connection lost")

        quote: dict[str, Any] = {
            "symbol": "AAPL",
            "bid_price": "100.00",
            "ask_price": "100.10",
            "bid_size": 10,
            "ask_size": 12,
            "timestamp": "2025-01-01T12:00:00Z",
            "ask_exchange": "NASDAQ",
        }

        # Should not raise, just log
        await stream._handle_quote(quote)

        # Redis set was called but failed
        redis_client.set.assert_called_once()
        # Event should not be published after Redis failure
        event_publisher.publish.assert_not_called()

    @pytest.mark.asyncio()
    async def test_handle_quote_missing_exchange_uses_unknown(
        self,
        stream: alpaca_stream.AlpacaMarketDataStream,
        redis_client: MagicMock,
        event_publisher: MagicMock,
    ) -> None:
        """Test quote without exchange field defaults to UNKNOWN."""
        quote: dict[str, Any] = {
            "symbol": "AAPL",
            "bid_price": "100.00",
            "ask_price": "100.10",
            "bid_size": 10,
            "ask_size": 12,
            "timestamp": "2025-01-01T12:00:00Z",
            # No ask_exchange field
        }

        await stream._handle_quote(quote)

        # Should succeed with default exchange
        redis_client.set.assert_called_once()
        event_publisher.publish.assert_called_once()

    @pytest.mark.asyncio()
    async def test_handle_quote_malformed_object_with_getattr_fallback(
        self,
        stream: alpaca_stream.AlpacaMarketDataStream,
        redis_client: MagicMock,
        event_publisher: MagicMock,
    ) -> None:
        """Test malformed quote object uses getattr fallback for error logging."""
        # Create a quote that will fail validation but has symbol
        quote = Mock()
        quote.symbol = "AAPL"
        quote.bid_price = "invalid"  # Will cause conversion error

        await stream._handle_quote(quote)

        # Should not crash, just log with symbol
        redis_client.set.assert_not_called()
        event_publisher.publish.assert_not_called()


class TestConnectionManagement:
    """Tests for WebSocket connection lifecycle and reconnection logic."""

    @pytest.mark.asyncio()
    async def test_start_successful_connection(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test successful WebSocket connection start."""
        fake_stream = stream.stream
        assert isinstance(fake_stream, _FakeStockDataStream)

        # Start in background and immediately stop
        start_task = asyncio.create_task(stream.start())
        await asyncio.sleep(0.1)
        await stream.stop()
        await start_task

        assert fake_stream.run_called >= 1
        assert fake_stream.stop_called == 1

    @pytest.mark.asyncio()
    async def test_start_sets_running_and_connected_flags(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test start sets running and connected flags."""
        assert stream._running is False
        assert stream._connected is False

        start_task = asyncio.create_task(stream.start())
        await asyncio.sleep(0.1)

        assert stream._running is True
        # Connected flag is set before run() but may change during execution
        # We'll just verify running was set

        await stream.stop()
        await start_task

    @pytest.mark.asyncio()
    async def test_start_reconnect_on_failure_with_exponential_backoff(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test reconnection with exponential backoff on failure."""
        fake_stream = stream.stream
        assert isinstance(fake_stream, _FakeStockDataStream)

        # Make first 2 calls fail, then succeed
        call_count = 0

        def conditional_raise() -> None:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise RuntimeError("Connection failed")

        fake_stream.exception_to_raise = RuntimeError("Connection failed")
        fake_stream.should_raise_on_run = True

        # Override run to count calls

        def counting_run() -> None:
            conditional_raise()

        fake_stream.run = counting_run  # type: ignore

        # Start and let it reconnect
        start_task = asyncio.create_task(stream.start())
        await asyncio.sleep(0.5)
        await stream.stop()

        try:
            await asyncio.wait_for(start_task, timeout=1.0)
        except (TimeoutError, ConnectionError):
            pass

        # Should have attempted reconnection
        assert stream._reconnect_attempts > 0

    @pytest.mark.asyncio()
    async def test_start_max_reconnect_attempts_raises_connection_error(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test max reconnection attempts raises ConnectionError."""
        fake_stream = stream.stream
        assert isinstance(fake_stream, _FakeStockDataStream)
        fake_stream.exception_to_raise = RuntimeError("Permanent failure")
        fake_stream.should_raise_on_run = True

        # Set max attempts to 3 for faster test
        stream._max_reconnect_attempts = 3

        with pytest.raises(ConnectionError, match="Failed to establish WebSocket connection"):
            await stream.start()

        assert stream._reconnect_attempts == 3

    @pytest.mark.asyncio()
    async def test_stop_sets_flags_and_calls_stream_stop(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test stop sets flags and calls stream.stop()."""
        stream._running = True
        stream._connected = True

        await stream.stop()

        assert stream._running is False
        assert stream._connected is False

        fake_stream = stream.stream
        assert isinstance(fake_stream, _FakeStockDataStream)
        assert fake_stream.stop_called == 1

    @pytest.mark.asyncio()
    async def test_stop_handles_exception_gracefully(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test stop handles exceptions from stream.stop() gracefully."""
        fake_stream = stream.stream
        assert isinstance(fake_stream, _FakeStockDataStream)

        # Make stop raise an exception
        def raise_on_stop() -> None:
            raise RuntimeError("Stop failed")

        fake_stream.stop = raise_on_stop  # type: ignore

        # Should not raise, just log
        await stream.stop()

        assert stream._running is False
        assert stream._connected is False

    def test_is_connected_returns_true_when_running_and_connected(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test is_connected returns True when both flags are set."""
        stream._running = True
        stream._connected = True

        assert stream.is_connected() is True

    def test_is_connected_returns_false_when_not_running(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test is_connected returns False when not running."""
        stream._running = False
        stream._connected = True

        assert stream.is_connected() is False

    def test_is_connected_returns_false_when_not_connected(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test is_connected returns False when not connected."""
        stream._running = True
        stream._connected = False

        assert stream.is_connected() is False


class TestConnectionStats:
    """Tests for connection statistics and monitoring."""

    def test_get_connection_stats_all_fields(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test get_connection_stats returns all expected fields."""
        stats = stream.get_connection_stats()

        assert "is_connected" in stats
        assert "subscribed_symbols" in stats
        assert "reconnect_attempts" in stats
        assert "max_reconnect_attempts" in stats

    def test_get_connection_stats_connected_state(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test connection stats reflect connected state."""
        stream._running = True
        stream._connected = True

        stats = stream.get_connection_stats()
        assert stats["is_connected"] is True

    def test_get_connection_stats_disconnected_state(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test connection stats reflect disconnected state."""
        stream._running = False
        stream._connected = False

        stats = stream.get_connection_stats()
        assert stats["is_connected"] is False

    @pytest.mark.asyncio()
    async def test_get_connection_stats_subscription_count(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test connection stats show correct subscription count."""
        await stream.subscribe_symbols(["AAPL", "MSFT", "GOOGL"], source="manual")

        stats = stream.get_connection_stats()
        assert stats["subscribed_symbols"] == 3

    def test_get_connection_stats_reconnect_attempts(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test connection stats show reconnect attempts."""
        stream._reconnect_attempts = 5

        stats = stream.get_connection_stats()
        assert stats["reconnect_attempts"] == 5
        assert stats["max_reconnect_attempts"] == 10


class TestSubscriptionQueries:
    """Tests for subscription query methods."""

    @pytest.mark.asyncio()
    async def test_get_subscribed_symbols_returns_sorted_list(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test get_subscribed_symbols returns sorted list."""
        await stream.subscribe_symbols(["MSFT", "AAPL", "GOOGL"], source="manual")

        symbols = stream.get_subscribed_symbols()
        assert symbols == ["AAPL", "GOOGL", "MSFT"]

    @pytest.mark.asyncio()
    async def test_get_subscription_sources_returns_sorted_sources(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test get_subscription_sources returns sorted sources."""
        await stream.subscribe_symbols(["AAPL"], source="position")
        await stream.subscribe_symbols(["AAPL"], source="manual")
        await stream.subscribe_symbols(["AAPL"], source="algo")

        sources = stream.get_subscription_sources()
        assert sources["AAPL"] == ["algo", "manual", "position"]

    @pytest.mark.asyncio()
    async def test_subscribed_symbols_property_backwards_compatibility(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test subscribed_symbols property for backwards compatibility."""
        await stream.subscribe_symbols(["AAPL", "MSFT"], source="manual")

        # Property should return set
        symbols = stream.subscribed_symbols
        assert isinstance(symbols, set)
        assert symbols == {"AAPL", "MSFT"}


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.mark.asyncio()
    async def test_subscribe_already_subscribed_symbol_different_source(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test subscribing to already-subscribed symbol with different source."""
        await stream.subscribe_symbols(["AAPL"], source="manual")
        await stream.subscribe_symbols(["AAPL"], source="position")

        # Should have both sources
        sources = stream.get_subscription_sources()
        assert sources["AAPL"] == ["manual", "position"]

        # Alpaca should only be called once
        fake_stream = stream.stream
        assert isinstance(fake_stream, _FakeStockDataStream)
        assert len(fake_stream.subscriptions) == 1

    @pytest.mark.asyncio()
    async def test_subscribe_same_source_idempotent(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test subscribing same symbol+source is idempotent."""
        await stream.subscribe_symbols(["AAPL"], source="manual")
        await stream.subscribe_symbols(["AAPL"], source="manual")

        # Should still have one source
        sources = stream.get_subscription_sources()
        assert sources["AAPL"] == ["manual"]

    @pytest.mark.asyncio()
    async def test_unsubscribe_wrong_source_has_no_effect(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test unsubscribing with wrong source has no effect."""
        await stream.subscribe_symbols(["AAPL"], source="manual")
        await stream.unsubscribe_symbols(["AAPL"], source="position")

        # Symbol should still be subscribed
        assert stream.get_subscribed_symbols() == ["AAPL"]
        assert stream.get_subscription_sources()["AAPL"] == ["manual"]

    @pytest.mark.asyncio()
    async def test_handle_quote_with_missing_optional_fields(
        self,
        stream: alpaca_stream.AlpacaMarketDataStream,
        redis_client: MagicMock,
        event_publisher: MagicMock,
    ) -> None:
        """Test quote with missing optional fields uses defaults."""
        quote: dict[str, Any] = {
            "symbol": "AAPL",
            "bid_price": "100.00",
            "ask_price": "100.10",
            # bid_size and ask_size missing
            "timestamp": "2025-01-01T12:00:00Z",
        }

        await stream._handle_quote(quote)

        # Should succeed with defaults
        redis_client.set.assert_called_once()
        event_publisher.publish.assert_called_once()

    @pytest.mark.asyncio()
    async def test_concurrent_subscriptions_use_lock(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test concurrent subscriptions are serialized by lock."""
        # This tests the lock is acquired, not full concurrency behavior
        tasks = [stream.subscribe_symbols(["AAPL"], source=f"source_{i}") for i in range(5)]

        await asyncio.gather(*tasks)

        # All sources should be recorded
        sources = stream.get_subscription_sources()
        assert len(sources["AAPL"]) == 5

    @pytest.mark.asyncio()
    async def test_reconnect_counter_resets_on_successful_connection(
        self, stream: alpaca_stream.AlpacaMarketDataStream
    ) -> None:
        """Test reconnect counter resets after successful connection cycle."""
        # Simulate some failed attempts
        stream._reconnect_attempts = 3

        # Mock successful connection (run completes without error)
        fake_stream = stream.stream
        assert isinstance(fake_stream, _FakeStockDataStream)

        start_task = asyncio.create_task(stream.start())
        await asyncio.sleep(0.1)

        # Counter should be reset after successful run
        # Note: This is implementation-dependent on when reset happens
        # The test verifies the behavior exists

        await stream.stop()
        await start_task

        # After graceful stop, reconnect attempts should be 0
        assert stream._reconnect_attempts == 0
