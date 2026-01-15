"""
Market Data Exceptions

Exception hierarchy for market data operations.
"""


class MarketDataError(Exception):
    """Base exception for all market data errors."""

    pass


class ConnectionError(MarketDataError):
    """Raised when WebSocket connection fails."""

    pass


class SubscriptionError(MarketDataError):
    """Raised when symbol subscription/unsubscription fails."""

    pass


class QuoteHandlingError(MarketDataError):
    """Raised when processing a quote fails."""

    pass
