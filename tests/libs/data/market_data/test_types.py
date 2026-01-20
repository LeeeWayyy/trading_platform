from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from libs.data.market_data.types import PriceData, PriceUpdateEvent, QuoteData


@pytest.fixture()
def quote() -> QuoteData:
    return QuoteData(
        symbol="AAPL",
        bid_price=Decimal("100.00"),
        ask_price=Decimal("100.10"),
        bid_size=10,
        ask_size=12,
        timestamp=datetime(2025, 1, 1, 12, 0, tzinfo=UTC),
        exchange="NASDAQ",
    )


def test_quote_mid_and_spread(quote: QuoteData) -> None:
    assert quote.mid_price == Decimal("100.05")
    assert quote.spread == Decimal("0.10")


def test_quote_spread_bps(quote: QuoteData) -> None:
    assert quote.spread_bps.quantize(Decimal("0.0001")) == Decimal("9.9950")


def test_quote_spread_bps_zero_mid() -> None:
    quote = QuoteData(
        symbol="ZERO",
        bid_price=Decimal("0"),
        ask_price=Decimal("0"),
        bid_size=0,
        ask_size=0,
        timestamp=datetime(2025, 1, 1, 12, 0, tzinfo=UTC),
        exchange=None,
    )
    assert quote.spread_bps == Decimal("0")


def test_quote_crossed_market_validation() -> None:
    with pytest.raises(ValidationError) as excinfo:
        QuoteData(
            symbol="AAPL",
            bid_price=Decimal("101"),
            ask_price=Decimal("100"),
            bid_size=1,
            ask_size=1,
            timestamp=datetime(2025, 1, 1, 12, 0, tzinfo=UTC),
            exchange=None,
        )
    assert "crossed market" in str(excinfo.value)


def test_price_data_from_quote(quote: QuoteData) -> None:
    price = PriceData.from_quote(quote)
    assert price.symbol == quote.symbol
    assert price.bid == quote.bid_price
    assert price.ask == quote.ask_price
    assert price.mid == quote.mid_price
    assert price.bid_size == quote.bid_size
    assert price.ask_size == quote.ask_size
    assert price.timestamp == quote.timestamp.isoformat()
    assert price.exchange == quote.exchange


def test_price_update_event_from_quote(quote: QuoteData) -> None:
    event = PriceUpdateEvent.from_quote(quote)
    assert event.event_type == "price.updated"
    assert event.symbol == quote.symbol
    assert event.price == quote.mid_price
    assert event.timestamp == quote.timestamp.isoformat()
