"""
Market Data Type Definitions

Pydantic models for type-safe market data handling.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


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
    exchange: str | None = Field(None, description="Exchange code (e.g., 'NASDAQ')")

    @field_validator("ask_price")
    @classmethod
    def ask_must_be_gte_bid(cls, v: Decimal, info: Any) -> Decimal:
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
    exchange: str | None = Field(None, description="Exchange code")

    @classmethod
    def from_quote(cls, quote: QuoteData) -> PriceData:
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


class ADVData(BaseModel):
    """
    Average Daily Volume (ADV) data for a symbol.

    Represents 20-day average daily volume in shares.
    """

    symbol: str = Field(..., description="Stock symbol")
    adv: int = Field(..., description="20-day average daily volume in shares", ge=0)
    data_date: date = Field(..., description="Date of the ADV calculation")
    source: str = Field(..., description="Data provider (e.g., 'alpaca')")


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
    def from_quote(cls, quote: QuoteData) -> PriceUpdateEvent:
        """Create event from QuoteData."""
        return cls(
            symbol=quote.symbol,
            price=quote.mid_price,
            timestamp=quote.timestamp.isoformat(),
        )


class ParsedPrice:
    """Result of parsing a Redis price payload."""

    __slots__ = ("mid", "timestamp")

    def __init__(self, mid: Decimal, timestamp: datetime) -> None:
        self.mid = mid
        self.timestamp = timestamp


def parse_redis_price_json(
    raw: str,
    expected_symbol: str | None = None,
    max_price_age_seconds: int | None = None,
    log_extra: dict[str, Any] | None = None,
) -> ParsedPrice | None:
    """Parse and validate a Redis price JSON payload.

    Shared validation logic for Redis market data cache entries.
    Used by orchestrator (``_parse_and_cache_price``) and
    execution-gateway (``batch_fetch_realtime_prices_from_redis``)
    to keep parsing, symbol-mismatch, and sanity checks consistent.

    Args:
        raw: Raw JSON string from Redis
        expected_symbol: If provided, rejects payloads whose ``symbol``
            field does not match (guards against misaligned cache entries)
        max_price_age_seconds: If provided, rejects prices older than
            this many seconds
        log_extra: Optional dict of additional context fields (e.g.
            ``strategy_id``, ``client_order_id``) merged into warning
            log entries for traceability

    Returns:
        ParsedPrice with validated mid price and timestamp, or None
        if the payload is invalid, stale, or mismatched.

    Example:
        >>> result = parse_redis_price_json(
        ...     '{"symbol":"AAPL","mid":"150.00","timestamp":"2026-04-06T10:00:00+00:00"}',
        ...     expected_symbol="AAPL",
        ...     max_price_age_seconds=30,
        ... )
        >>> if result:
        ...     print(result.mid)
        Decimal('150.00')
    """
    _extra = log_extra or {}
    try:
        price_data = json.loads(raw)

        # Symbol mismatch guard: reject if symbol is missing or mismatched
        if expected_symbol is not None:
            payload_symbol = price_data.get("symbol")
            if payload_symbol is None:
                logger.warning(
                    "Redis price missing symbol field, expected %s",
                    expected_symbol,
                    extra={**_extra, "expected_symbol": expected_symbol},
                )
                return None
            if payload_symbol != expected_symbol:
                logger.warning(
                    "Redis price symbol mismatch: expected %s, got %s",
                    expected_symbol,
                    payload_symbol,
                    extra={
                        **_extra,
                        "expected_symbol": expected_symbol,
                        "payload_symbol": payload_symbol,
                    },
                )
                return None

        # Timestamp validation
        ts_raw = price_data.get("timestamp")
        if ts_raw is None:
            logger.warning(
                "Redis price missing timestamp for %s",
                expected_symbol or "unknown",
                extra={**_extra, "symbol": expected_symbol or "unknown"},
            )
            return None

        price_ts = datetime.fromisoformat(str(ts_raw))
        if price_ts.tzinfo is None:
            price_ts = price_ts.replace(tzinfo=UTC)

        # Timestamp age calculation (used by both staleness and future checks)
        age_seconds = (datetime.now(UTC) - price_ts).total_seconds()

        # Future timestamps are always suspicious — reject regardless of staleness config.
        # Allow 5s tolerance for multi-host clock skew (NTP can drift 1-2s).
        if age_seconds < -5:
            logger.warning(
                "Redis price has future timestamp for %s",
                expected_symbol or "unknown",
                extra={
                    **_extra,
                    "symbol": expected_symbol or "unknown",
                    "price_age_seconds": age_seconds,
                },
            )
            return None

        # Staleness check (only when max_price_age_seconds is configured)
        if max_price_age_seconds is not None and age_seconds > max_price_age_seconds:
            logger.warning(
                "Redis price stale for %s",
                expected_symbol or "unknown",
                extra={
                    **_extra,
                    "symbol": expected_symbol or "unknown",
                    "price_age_seconds": max(age_seconds, 0),
                    "max_price_age_seconds": max_price_age_seconds,
                },
            )
            return None

        # Mid price validation
        mid_raw = price_data.get("mid")
        if mid_raw is None:
            logger.warning(
                "Redis price has null mid for %s",
                expected_symbol or "unknown",
                extra={
                    **_extra,
                    "symbol": expected_symbol or "unknown",
                },
            )
            return None
        mid = Decimal(str(mid_raw))
        if mid <= 0 or not mid.is_finite():
            logger.warning(
                "Redis price has invalid mid for %s",
                expected_symbol or "unknown",
                extra={
                    **_extra,
                    "symbol": expected_symbol or "unknown",
                    "mid": str(mid),
                },
            )
            return None

        return ParsedPrice(mid=mid, timestamp=price_ts)

    except (json.JSONDecodeError, KeyError, ValueError, TypeError, InvalidOperation, AttributeError) as e:
        logger.warning(
            "Failed to parse Redis price payload",
            extra={
                **_extra,
                "symbol": expected_symbol or "unknown",
                "error": str(e),
                "error_type": type(e).__name__,
            },
        )
        return None
