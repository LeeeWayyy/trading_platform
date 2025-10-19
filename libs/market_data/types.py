"""
Market Data Type Definitions

Pydantic models for type-safe market data handling.
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class QuoteData(BaseModel):
    """
    Real-time quote data from market data provider.

    Represents bid/ask prices at a specific point in time.
    """

    symbol: str = Field(..., description="Stock symbol (e.g., 'AAPL')")
    bid_price: Decimal = Field(..., description="Best bid price", ge=0)
    ask_price: Decimal = Field(..., description="Best ask price", ge=0)
    bid_size: int = Field(..., description="Bid size in shares", ge=0)
    ask_size: int = Field(..., description="Ask size in shares", ge=0)
    timestamp: datetime = Field(..., description="Quote timestamp (UTC)")
    exchange: Optional[str] = Field(None, description="Exchange code (e.g., 'NASDAQ')")

    @field_validator("ask_price")
    @classmethod
    def ask_must_be_gte_bid(cls, v, info):
        """Validate that ask >= bid (no crossed market)."""
        if "bid_price" in info.data and v < info.data["bid_price"]:
            raise ValueError(f"Ask price {v} < bid price {info.data['bid_price']} (crossed market)")
        return v

    @property
    def mid_price(self) -> Decimal:
        """Calculate mid-market price."""
        return (self.bid_price + self.ask_price) / 2

    @property
    def spread(self) -> Decimal:
        """Calculate bid-ask spread."""
        return self.ask_price - self.bid_price

    @property
    def spread_bps(self) -> Decimal:
        """Calculate spread in basis points."""
        if self.mid_price == 0:
            return Decimal("0")
        return (self.spread / self.mid_price) * Decimal("10000")


class PriceData(BaseModel):
    """
    Cached price data stored in Redis.

    Simplified version of QuoteData optimized for storage and retrieval.
    """

    symbol: str = Field(..., description="Stock symbol")
    bid: Decimal = Field(..., description="Bid price", ge=0)
    ask: Decimal = Field(..., description="Ask price", ge=0)
    mid: Decimal = Field(..., description="Mid price", ge=0)
    bid_size: int = Field(default=0, description="Bid size", ge=0)
    ask_size: int = Field(default=0, description="Ask size", ge=0)
    timestamp: str = Field(..., description="ISO format timestamp")
    exchange: Optional[str] = Field(None, description="Exchange code")

    @classmethod
    def from_quote(cls, quote: QuoteData) -> "PriceData":
        """Create PriceData from QuoteData."""
        return cls(
            symbol=quote.symbol,
            bid=quote.bid_price,
            ask=quote.ask_price,
            mid=quote.mid_price,
            bid_size=quote.bid_size,
            ask_size=quote.ask_size,
            timestamp=quote.timestamp.isoformat(),
            exchange=quote.exchange,
        )


class PriceUpdateEvent(BaseModel):
    """
    Event published when price is updated.

    Published to Redis pub/sub channel: price.updated.{symbol}
    """

    event_type: Literal["price.updated"] = "price.updated"
    symbol: str = Field(..., description="Stock symbol")
    price: Decimal = Field(..., description="Mid price", ge=0)
    timestamp: str = Field(..., description="ISO format timestamp")

    @classmethod
    def from_quote(cls, quote: QuoteData) -> "PriceUpdateEvent":
        """Create event from QuoteData."""
        return cls(
            symbol=quote.symbol,
            price=quote.mid_price,
            timestamp=quote.timestamp.isoformat(),
        )
