"""
Alpaca Market Data Streaming Client

WebSocket client for real-time market data from Alpaca.
"""

import asyncio
import logging
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from alpaca.data.live import StockDataStream
from alpaca.data.models import Quote
from pydantic import ValidationError
from redis.exceptions import RedisError

from libs.market_data.exceptions import ConnectionError, SubscriptionError
from libs.market_data.types import PriceData, PriceUpdateEvent, QuoteData
from libs.redis_client import EventPublisher, RedisClient, RedisKeys

logger = logging.getLogger(__name__)


class AlpacaMarketDataStream:
    """
    WebSocket client for Alpaca real-time market data.

    Manages WebSocket connection, symbol subscriptions, and quote distribution
    via Redis cache and pub/sub.

    Example:
        stream = AlpacaMarketDataStream(
            api_key="your_key",
            secret_key="your_secret",
            redis_client=redis_client,
            event_publisher=publisher
        )

        await stream.subscribe_symbols(["AAPL", "MSFT"])
        await stream.start()
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        redis_client: RedisClient,
        event_publisher: EventPublisher,
        price_ttl: int = 300,  # 5 minutes
    ):
        """
        Initialize Alpaca market data stream.

        Args:
            api_key: Alpaca API key
            secret_key: Alpaca secret key
            redis_client: Redis client for price caching
            event_publisher: Event publisher for price updates
            price_ttl: TTL for price cache in seconds (default: 5 minutes)
        """
        self.api_key = api_key
        self.secret_key = secret_key
        self.redis = redis_client
        self.publisher = event_publisher
        self.price_ttl = price_ttl

        # Initialize Alpaca WebSocket client
        self.stream = StockDataStream(api_key, secret_key)

        # H5 Fix: Track subscribed symbols with source ref-counting
        # Maps symbol -> set of sources (e.g., "manual", "position")
        # Only unsubscribe when all sources have unsubscribed
        self._subscription_sources: dict[str, set[str]] = {}
        self._subscription_lock = asyncio.Lock()  # Prevent concurrent subscription/unsubscription

        # Connection state
        self._running = False
        self._connected = False  # Track actual connection state
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10

        logger.info("AlpacaMarketDataStream initialized")

    @property
    def subscribed_symbols(self) -> set[str]:
        """
        Get set of currently subscribed symbols (backwards compatibility).

        Returns:
            Set of symbol strings that have at least one active subscription source.

        Note:
            This property is maintained for backwards compatibility.
            The underlying implementation now uses ref-counting with source tracking.
        """
        return set(self._subscription_sources.keys())

    async def subscribe_symbols(self, symbols: list[str], source: str = "manual") -> None:
        """
        Subscribe to real-time quotes for symbols with source tracking.

        H5 Fix: Uses ref-counting to track subscription sources. A symbol is only
        actually subscribed to Alpaca when the first source subscribes, and only
        unsubscribed when all sources have unsubscribed.

        Args:
            symbols: List of symbols (e.g., ["AAPL", "MSFT"])
            source: Subscription source identifier (e.g., "manual", "position").
                    Default is "manual" for backwards compatibility.

        Raises:
            SubscriptionError: If subscription fails

        Example:
            # Manual subscription
            await stream.subscribe_symbols(["AAPL"], source="manual")

            # Position-based auto-subscription
            await stream.subscribe_symbols(["AAPL"], source="position")

            # Now AAPL has two sources: {"manual", "position"}
            # Unsubscribing one source won't unsubscribe from Alpaca
        """
        if not symbols:
            logger.warning("subscribe_symbols called with empty list")
            return

        async with self._subscription_lock:
            # Find symbols that need actual Alpaca subscription (not already subscribed)
            new_alpaca_symbols = [s for s in symbols if s not in self._subscription_sources]

            # Find symbols that just need source tracking update
            existing_symbols = [s for s in symbols if s in self._subscription_sources]

            try:
                # Subscribe to new symbols via Alpaca SDK
                if new_alpaca_symbols:
                    self.stream.subscribe_quotes(self._handle_quote, *new_alpaca_symbols)
                    logger.info(
                        f"Subscribed to {len(new_alpaca_symbols)} new symbols via Alpaca: "
                        f"{new_alpaca_symbols}"
                    )

                # Update ref-counting for all symbols
                for symbol in symbols:
                    if symbol not in self._subscription_sources:
                        self._subscription_sources[symbol] = set()
                    self._subscription_sources[symbol].add(source)

                # Log source tracking updates
                if existing_symbols:
                    logger.debug(
                        f"Added source '{source}' to {len(existing_symbols)} existing subscriptions: "
                        f"{existing_symbols}"
                    )

                logger.info(
                    f"Subscription update: {len(symbols)} symbols, source='{source}', "
                    f"new_alpaca={len(new_alpaca_symbols)}, existing={len(existing_symbols)}"
                )

            except Exception as e:
                logger.error(f"Failed to subscribe to symbols {symbols}: {e}")
                raise SubscriptionError(f"Failed to subscribe to symbols {symbols}: {e}") from e

    async def unsubscribe_symbols(self, symbols: list[str], source: str = "manual") -> None:
        """
        Unsubscribe from symbols with source tracking.

        H5 Fix: Uses ref-counting to track subscription sources. Only actually
        unsubscribes from Alpaca when ALL sources have unsubscribed from a symbol.

        Args:
            symbols: List of symbols to unsubscribe from
            source: Subscription source identifier (e.g., "manual", "position").
                    Default is "manual" for backwards compatibility.

        Raises:
            SubscriptionError: If unsubscription fails

        Example:
            # AAPL has sources: {"manual", "position"}
            await stream.unsubscribe_symbols(["AAPL"], source="position")
            # Now AAPL has sources: {"manual"} - still subscribed to Alpaca!

            await stream.unsubscribe_symbols(["AAPL"], source="manual")
            # Now AAPL has no sources - actually unsubscribed from Alpaca
        """
        if not symbols:
            return

        async with self._subscription_lock:
            actually_unsubscribed: list[str] = []
            source_removed: list[str] = []
            not_found: list[str] = []

            try:
                for symbol in symbols:
                    if symbol not in self._subscription_sources:
                        not_found.append(symbol)
                        continue

                    # Remove this source from the symbol
                    self._subscription_sources[symbol].discard(source)
                    source_removed.append(symbol)

                    # If no more sources, actually unsubscribe from Alpaca
                    if not self._subscription_sources[symbol]:
                        del self._subscription_sources[symbol]
                        self.stream.unsubscribe_quotes(symbol)
                        actually_unsubscribed.append(symbol)
                        logger.info(f"Unsubscribed from {symbol} (no remaining sources)")

                # Log summary
                if actually_unsubscribed:
                    logger.info(
                        f"Actually unsubscribed from Alpaca: {len(actually_unsubscribed)} symbols: "
                        f"{actually_unsubscribed}"
                    )

                if source_removed and not actually_unsubscribed:
                    logger.debug(
                        f"Removed source '{source}' from {len(source_removed)} symbols "
                        f"(still subscribed via other sources)"
                    )

                if not_found:
                    logger.debug(f"Symbols not found in subscriptions: {not_found}")

            except Exception as e:
                logger.error(f"Failed to unsubscribe from symbols {symbols}: {e}")
                raise SubscriptionError(f"Failed to unsubscribe from symbols {symbols}: {e}") from e

    async def _handle_quote(self, quote: Quote | Mapping[str, Any]) -> None:
        """
        Handle incoming quote from Alpaca.

        Stores price in Redis cache and publishes event to subscribers.

        Args:
            quote: Quote object from Alpaca SDK

        Notes:
            - Errors are handled gracefully within this function and not re-raised
            - This prevents individual quote errors from crashing the WebSocket stream
            - Failed quotes are logged with full traceback for debugging
        """
        try:
            # Convert Alpaca Quote to our QuoteData model
            symbol = quote["symbol"] if isinstance(quote, Mapping) else quote.symbol
            bid_price_value = quote["bid_price"] if isinstance(quote, Mapping) else quote.bid_price
            ask_price_value = quote["ask_price"] if isinstance(quote, Mapping) else quote.ask_price
            bid_size_value = (
                quote.get("bid_size", 0) if isinstance(quote, Mapping) else quote.bid_size
            )
            ask_size_value = (
                quote.get("ask_size", 0) if isinstance(quote, Mapping) else quote.ask_size
            )
            raw_timestamp = (
                quote.get("timestamp") if isinstance(quote, Mapping) else quote.timestamp
            )
            if raw_timestamp is None:
                logger.warning("Received quote without timestamp for symbol %s", symbol)
                return
            if isinstance(raw_timestamp, str):
                # Handle ISO8601 'Z' suffix (Zulu/UTC time) which fromisoformat may not parse
                ts_str = (
                    raw_timestamp.replace("Z", "+00:00")
                    if raw_timestamp.endswith("Z")
                    else raw_timestamp
                )
                timestamp_value = datetime.fromisoformat(ts_str)
            else:
                timestamp_value = raw_timestamp
            if not isinstance(timestamp_value, datetime):
                logger.warning(
                    "Received quote with unsupported timestamp type %s for symbol %s",
                    type(timestamp_value),
                    symbol,
                )
                return
            ask_exchange = (
                quote.get("ask_exchange", "UNKNOWN")
                if isinstance(quote, Mapping)
                else getattr(quote, "ask_exchange", "UNKNOWN")
            )

            quote_data = QuoteData(
                symbol=symbol,
                bid_price=Decimal(str(bid_price_value)),
                ask_price=Decimal(str(ask_price_value)),
                bid_size=int(bid_size_value),
                ask_size=int(ask_size_value),
                timestamp=timestamp_value,
                exchange=ask_exchange,
            )

            # Create price data for caching
            price_data = PriceData.from_quote(quote_data)

            # Store in Redis with TTL
            cache_key = RedisKeys.price(quote_data.symbol)
            self.redis.set(
                cache_key,
                price_data.model_dump_json(),
                ttl=self.price_ttl,
            )

            # Publish price update event
            event = PriceUpdateEvent.from_quote(quote_data)
            channel = f"price.updated.{quote_data.symbol}"

            self.publisher.publish(channel, event)

            logger.debug(
                f"Price update: {quote_data.symbol} = ${quote_data.mid_price:.2f} "
                f"(spread: {quote_data.spread_bps:.1f} bps)"
            )

        except (ValidationError, ValueError, AttributeError, InvalidOperation, RedisError) as e:
            # Catch specific errors: Pydantic validation, invalid decimal conversion,
            # missing quote attributes, decimal parsing errors (NaN, None, etc.),
            # or ALL Redis failures (connection, timeout, memory, etc.)
            # Do not re-raise to prevent stream crash on single bad quote
            # Use getattr to safely access symbol in case quote object is malformed
            symbol = getattr(quote, "symbol", "<unknown>")
            logger.error(f"Error handling quote for {symbol}: {e}", exc_info=True)

    async def start(self) -> None:
        """
        Start WebSocket connection with automatic reconnection.

        Implements exponential backoff for reconnection attempts.
        StockDataStream.run() is an async coroutine that must be awaited.

        Raises:
            ConnectionError: If max reconnection attempts exceeded
        """
        self._running = True
        retry_delay = 5  # Base delay in seconds

        while self._running and self._reconnect_attempts < self._max_reconnect_attempts:
            try:
                logger.info(
                    f"Starting WebSocket connection "
                    f"(attempt {self._reconnect_attempts + 1}/{self._max_reconnect_attempts})..."
                )

                # Mark as connected before running
                self._connected = True

                # Await the async WebSocket coroutine (NOT synchronous - do not use executor)
                # This will block until disconnect or error
                await self.stream.run()  # type: ignore[func-returns-value]

                # If we reach here, connection was established and then closed gracefully
                # This is a "successful" cycle, so reset the counter
                self._connected = False
                if self._reconnect_attempts > 0:
                    logger.info(
                        f"Resetting reconnect counter to 0 (was {self._reconnect_attempts})"
                    )
                    self._reconnect_attempts = 0

                if self._running:
                    logger.warning(
                        "WebSocket connection closed unexpectedly, will reconnect immediately."
                    )

            except Exception as e:
                self._connected = False
                self._reconnect_attempts += 1

                if self._reconnect_attempts >= self._max_reconnect_attempts:
                    logger.error("Max reconnection attempts reached. Giving up.")
                    raise ConnectionError(
                        f"Failed to establish WebSocket connection after "
                        f"{self._max_reconnect_attempts} attempts"
                    ) from e

                # Exponential backoff (5s, 10s, 20s, 40s, ..., max 300s)
                delay = min(retry_delay * (2 ** (self._reconnect_attempts - 1)), 300)

                logger.warning(
                    f"WebSocket error: {e}. "
                    f"Retrying in {delay}s (attempt {self._reconnect_attempts}/{self._max_reconnect_attempts})"
                )

                await asyncio.sleep(delay)

        if not self._running:
            self._connected = False
            logger.info("WebSocket stopped gracefully")

    async def stop(self) -> None:
        """Stop WebSocket connection gracefully."""
        logger.info("Stopping WebSocket connection...")
        self._running = False
        self._connected = False

        try:
            # stream.stop() is synchronous and may block, run in executor
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.stream.stop)
            logger.info("WebSocket stopped successfully")
        except Exception as e:
            logger.error(f"Error stopping WebSocket: {e}")

    def is_connected(self) -> bool:
        """
        Check if WebSocket is currently connected.

        Returns:
            True if connected, False otherwise
        """
        return self._running and self._connected

    def get_subscribed_symbols(self) -> list[str]:
        """
        Get list of currently subscribed symbols.

        Returns:
            List of symbol strings
        """
        return sorted(self.subscribed_symbols)

    def get_connection_stats(self) -> dict[str, int | bool]:
        """
        Get connection statistics.

        Returns:
            Dictionary with connection stats
        """
        return {
            "is_connected": self.is_connected(),
            "subscribed_symbols": len(self.subscribed_symbols),
            "reconnect_attempts": self._reconnect_attempts,
            "max_reconnect_attempts": self._max_reconnect_attempts,
        }

    def get_subscription_sources(self) -> dict[str, list[str]]:
        """
        Get subscription sources for each symbol (H5 fix debugging).

        Returns:
            Dictionary mapping symbol -> list of sources

        Example:
            >>> stream.get_subscription_sources()
            {"AAPL": ["manual", "position"], "MSFT": ["position"]}
        """
        return {symbol: sorted(sources) for symbol, sources in self._subscription_sources.items()}
