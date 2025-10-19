"""
Alpaca Market Data Streaming Client

WebSocket client for real-time market data from Alpaca.
"""

import asyncio
import concurrent.futures
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Set

from alpaca.data.live import StockDataStream
from alpaca.data.models import Quote
from pydantic import ValidationError

from libs.market_data.exceptions import ConnectionError, QuoteHandlingError, SubscriptionError
from libs.market_data.types import PriceData, PriceUpdateEvent, QuoteData
from libs.redis_client import EventPublisher, RedisClient, RedisConnectionError

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

        # Track subscribed symbols
        self.subscribed_symbols: Set[str] = set()
        self._subscription_lock = asyncio.Lock()  # Prevent concurrent subscription/unsubscription

        # Connection state
        self._running = False
        self._connected = False  # Track actual connection state
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10

        logger.info("AlpacaMarketDataStream initialized")

    async def subscribe_symbols(self, symbols: list[str]) -> None:
        """
        Subscribe to real-time quotes for symbols.

        Args:
            symbols: List of symbols (e.g., ["AAPL", "MSFT"])

        Raises:
            SubscriptionError: If subscription fails
        """
        if not symbols:
            logger.warning("subscribe_symbols called with empty list")
            return

        async with self._subscription_lock:
            # Filter out already subscribed symbols
            new_symbols = [s for s in symbols if s not in self.subscribed_symbols]

            if not new_symbols:
                logger.debug(f"All symbols already subscribed: {symbols}")
                return

            try:
                # Subscribe to quotes via Alpaca SDK
                self.stream.subscribe_quotes(self._handle_quote, *new_symbols)

                # Update tracking
                self.subscribed_symbols.update(new_symbols)

                logger.info(f"Subscribed to {len(new_symbols)} symbols: {new_symbols}")

            except Exception as e:
                logger.error(f"Failed to subscribe to symbols {new_symbols}: {e}")
                raise SubscriptionError(f"Failed to subscribe to symbols {new_symbols}: {e}") from e

    async def unsubscribe_symbols(self, symbols: list[str]) -> None:
        """
        Unsubscribe from symbols.

        Args:
            symbols: List of symbols to unsubscribe from

        Raises:
            SubscriptionError: If unsubscription fails
        """
        if not symbols:
            return

        async with self._subscription_lock:
            try:
                for symbol in symbols:
                    if symbol in self.subscribed_symbols:
                        self.stream.unsubscribe_quotes(symbol)
                        self.subscribed_symbols.remove(symbol)
                        logger.info(f"Unsubscribed from {symbol}")

            except Exception as e:
                logger.error(f"Failed to unsubscribe from symbols {symbols}: {e}")
                raise SubscriptionError(f"Failed to unsubscribe from symbols {symbols}: {e}") from e

    async def _handle_quote(self, quote: Quote) -> None:
        """
        Handle incoming quote from Alpaca.

        Stores price in Redis cache and publishes event to subscribers.

        Args:
            quote: Quote object from Alpaca SDK

        Raises:
            QuoteHandlingError: If quote processing fails
        """
        try:
            # Convert Alpaca Quote to our QuoteData model
            quote_data = QuoteData(
                symbol=quote.symbol,
                bid_price=Decimal(str(quote.bid_price)),
                ask_price=Decimal(str(quote.ask_price)),
                bid_size=quote.bid_size,
                ask_size=quote.ask_size,
                timestamp=quote.timestamp,
                exchange=quote.exchange,
            )

            # Create price data for caching
            price_data = PriceData.from_quote(quote_data)

            # Store in Redis with TTL
            cache_key = f"price:{quote_data.symbol}"
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

        except (ValidationError, ValueError, AttributeError, RedisConnectionError) as e:
            # Catch specific errors: Pydantic validation, invalid decimal conversion,
            # missing quote attributes, or Redis connection issues
            # Do not re-raise to prevent stream crash on single bad quote
            logger.error(f"Error handling quote for {quote.symbol}: {e}", exc_info=True)

    async def start(self) -> None:
        """
        Start WebSocket connection with automatic reconnection.

        Implements exponential backoff for reconnection attempts.
        Runs the synchronous StockDataStream.run() in a thread pool to avoid blocking.

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

                # Run WebSocket in thread pool (StockDataStream.run() is synchronous, not async)
                # This blocks until disconnect
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self.stream.run)

                # If we reach here, connection was established and then closed gracefully
                # This is a "successful" cycle, so reset the counter
                self._connected = False
                if self._reconnect_attempts > 0:
                    logger.info(f"Resetting reconnect counter to 0 (was {self._reconnect_attempts})")
                    self._reconnect_attempts = 0

                if self._running:
                    logger.warning("WebSocket connection closed unexpectedly, will reconnect immediately.")

            except Exception as e:
                self._connected = False
                self._reconnect_attempts += 1

                if self._reconnect_attempts >= self._max_reconnect_attempts:
                    logger.error("Max reconnection attempts reached. Giving up.")
                    raise ConnectionError(
                        f"Failed to establish WebSocket connection after "
                        f"{self._max_reconnect_attempts} attempts"
                    )

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
        return sorted(list(self.subscribed_symbols))

    def get_connection_stats(self) -> dict:
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
