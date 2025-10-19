"""
Market Data Library

Provides real-time market data streaming from Alpaca via WebSocket.

Components:
- AlpacaMarketDataStream: WebSocket client for live quotes
- PriceData: Type-safe price data model
- MarketDataError: Exception hierarchy

Usage:
    from libs.market_data import AlpacaMarketDataStream

    stream = AlpacaMarketDataStream(
        api_key="your_key",
        secret_key="your_secret",
        redis_client=redis_client,
        event_publisher=publisher
    )

    await stream.subscribe_symbols(["AAPL", "MSFT"])
    await stream.start()
"""

from libs.market_data.types import PriceData, QuoteData
from libs.market_data.alpaca_stream import AlpacaMarketDataStream
from libs.market_data.exceptions import (
    MarketDataError,
    ConnectionError,
    SubscriptionError,
)

__all__ = [
    "AlpacaMarketDataStream",
    "PriceData",
    "QuoteData",
    "MarketDataError",
    "ConnectionError",
    "SubscriptionError",
]
