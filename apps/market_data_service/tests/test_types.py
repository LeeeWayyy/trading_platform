"""
Tests for market data type definitions.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from libs.market_data.types import PriceData, PriceUpdateEvent, QuoteData


class TestQuoteData:
    """Tests for QuoteData model."""

    def test_valid_quote(self):
        """Test creating valid quote data."""
        quote = QuoteData(
            symbol="AAPL",
            bid_price=Decimal("150.00"),
            ask_price=Decimal("150.10"),
            bid_size=100,
            ask_size=200,
            timestamp=datetime.now(timezone.utc),
            exchange="NASDAQ",
        )

        assert quote.symbol == "AAPL"
        assert quote.bid_price == Decimal("150.00")
        assert quote.ask_price == Decimal("150.10")
        assert quote.exchange == "NASDAQ"

    def test_mid_price_calculation(self):
        """Test mid price calculation."""
        quote = QuoteData(
            symbol="AAPL",
            bid_price=Decimal("150.00"),
            ask_price=Decimal("150.10"),
            bid_size=100,
            ask_size=200,
            timestamp=datetime.now(timezone.utc),
        )

        assert quote.mid_price == Decimal("150.05")

    def test_spread_calculation(self):
        """Test spread calculation."""
        quote = QuoteData(
            symbol="AAPL",
            bid_price=Decimal("150.00"),
            ask_price=Decimal("150.10"),
            bid_size=100,
            ask_size=200,
            timestamp=datetime.now(timezone.utc),
        )

        assert quote.spread == Decimal("0.10")

    def test_spread_bps_calculation(self):
        """Test spread in basis points."""
        quote = QuoteData(
            symbol="AAPL",
            bid_price=Decimal("150.00"),
            ask_price=Decimal("150.10"),
            bid_size=100,
            ask_size=200,
            timestamp=datetime.now(timezone.utc),
        )

        # 0.10 / 150.05 * 10000 = 6.665... bps
        assert abs(quote.spread_bps - Decimal("6.665")) < Decimal("0.001")

    def test_crossed_market_validation(self):
        """Test that crossed markets are rejected (ask < bid)."""
        with pytest.raises(ValidationError) as exc_info:
            QuoteData(
                symbol="AAPL",
                bid_price=Decimal("150.10"),
                ask_price=Decimal("150.00"),  # Ask < Bid (invalid)
                bid_size=100,
                ask_size=200,
                timestamp=datetime.now(timezone.utc),
            )

        errors = exc_info.value.errors()
        assert any("crossed market" in str(e["msg"]).lower() for e in errors)

    def test_negative_price_validation(self):
        """Test that negative prices are rejected."""
        with pytest.raises(ValidationError):
            QuoteData(
                symbol="AAPL",
                bid_price=Decimal("-150.00"),  # Negative (invalid)
                ask_price=Decimal("150.10"),
                bid_size=100,
                ask_size=200,
                timestamp=datetime.now(timezone.utc),
            )

    def test_negative_size_validation(self):
        """Test that negative sizes are rejected."""
        with pytest.raises(ValidationError):
            QuoteData(
                symbol="AAPL",
                bid_price=Decimal("150.00"),
                ask_price=Decimal("150.10"),
                bid_size=-100,  # Negative (invalid)
                ask_size=200,
                timestamp=datetime.now(timezone.utc),
            )


class TestPriceData:
    """Tests for PriceData model."""

    def test_from_quote(self):
        """Test creating PriceData from QuoteData."""
        timestamp = datetime.now(timezone.utc)
        quote = QuoteData(
            symbol="AAPL",
            bid_price=Decimal("150.00"),
            ask_price=Decimal("150.10"),
            bid_size=100,
            ask_size=200,
            timestamp=timestamp,
            exchange="NASDAQ",
        )

        price_data = PriceData.from_quote(quote)

        assert price_data.symbol == "AAPL"
        assert price_data.bid == Decimal("150.00")
        assert price_data.ask == Decimal("150.10")
        assert price_data.mid == Decimal("150.05")
        assert price_data.bid_size == 100
        assert price_data.ask_size == 200
        assert price_data.timestamp == timestamp.isoformat()
        assert price_data.exchange == "NASDAQ"

    def test_price_data_serialization(self):
        """Test PriceData JSON serialization."""
        price_data = PriceData(
            symbol="AAPL",
            bid=Decimal("150.00"),
            ask=Decimal("150.10"),
            mid=Decimal("150.05"),
            bid_size=100,
            ask_size=200,
            timestamp="2024-10-19T12:00:00+00:00",
            exchange="NASDAQ",
        )

        json_str = price_data.model_dump_json()
        assert "AAPL" in json_str
        assert "150.00" in json_str
        assert "NASDAQ" in json_str


class TestPriceUpdateEvent:
    """Tests for PriceUpdateEvent model."""

    def test_from_quote(self):
        """Test creating PriceUpdateEvent from QuoteData."""
        timestamp = datetime.now(timezone.utc)
        quote = QuoteData(
            symbol="AAPL",
            bid_price=Decimal("150.00"),
            ask_price=Decimal("150.10"),
            bid_size=100,
            ask_size=200,
            timestamp=timestamp,
        )

        event = PriceUpdateEvent.from_quote(quote)

        assert event.event_type == "price.updated"
        assert event.symbol == "AAPL"
        assert event.price == Decimal("150.05")  # Mid price
        assert event.timestamp == timestamp.isoformat()

    def test_event_serialization(self):
        """Test event JSON serialization."""
        event = PriceUpdateEvent(
            symbol="AAPL",
            price=Decimal("150.05"),
            timestamp="2024-10-19T12:00:00+00:00",
        )

        event_dict = event.model_dump()
        assert event_dict["event_type"] == "price.updated"
        assert event_dict["symbol"] == "AAPL"
        assert event_dict["price"] == Decimal("150.05")
