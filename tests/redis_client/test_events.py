"""
Unit tests for event schemas.

Tests cover:
- Event model validation
- Required fields
- Timestamp validation (UTC requirement)
- Field constraints
- JSON serialization
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from libs.redis_client.events import OrderEvent, PositionEvent, SignalEvent


class TestSignalEvent:
    """Tests for SignalEvent schema."""

    def test_signal_event_valid(self):
        """Test creating valid SignalEvent."""
        event = SignalEvent(
            timestamp=datetime.now(UTC),
            strategy_id="alpha_baseline",
            symbols=["AAPL", "MSFT"],
            num_signals=2,
            as_of_date="2025-01-17",
        )

        assert event.event_type == "signals.generated"
        assert event.strategy_id == "alpha_baseline"
        assert event.symbols == ["AAPL", "MSFT"]
        assert event.num_signals == 2
        assert event.as_of_date == "2025-01-17"

    def test_signal_event_json_serialization(self):
        """Test SignalEvent JSON serialization."""
        event = SignalEvent(
            timestamp=datetime(2025, 1, 17, 9, 0, 0, tzinfo=UTC),
            strategy_id="alpha_baseline",
            symbols=["AAPL"],
            num_signals=1,
            as_of_date="2025-01-17",
        )

        json_str = event.model_dump_json()

        assert "signals.generated" in json_str
        assert "alpha_baseline" in json_str
        assert "AAPL" in json_str

    def test_signal_event_missing_timestamp(self):
        """Test SignalEvent validation error on missing timestamp."""
        with pytest.raises(ValidationError):
            SignalEvent(
                strategy_id="alpha_baseline",
                symbols=["AAPL"],
                num_signals=1,
                as_of_date="2025-01-17",
            )

    def test_signal_event_naive_timestamp(self):
        """Test SignalEvent validation error on naive (non-UTC) timestamp."""
        with pytest.raises(ValidationError, match="timezone-aware"):
            SignalEvent(
                timestamp=datetime.now(),  # Naive timestamp (no tzinfo)
                strategy_id="alpha_baseline",
                symbols=["AAPL"],
                num_signals=1,
                as_of_date="2025-01-17",
            )

    def test_signal_event_empty_symbols(self):
        """Test SignalEvent validation error on empty symbols list."""
        with pytest.raises(ValidationError):
            SignalEvent(
                timestamp=datetime.now(UTC),
                strategy_id="alpha_baseline",
                symbols=[],  # Empty list not allowed
                num_signals=0,
                as_of_date="2025-01-17",
            )

    def test_signal_event_negative_num_signals(self):
        """Test SignalEvent validation error on negative num_signals."""
        with pytest.raises(ValidationError):
            SignalEvent(
                timestamp=datetime.now(UTC),
                strategy_id="alpha_baseline",
                symbols=["AAPL"],
                num_signals=-1,  # Negative not allowed
                as_of_date="2025-01-17",
            )


class TestOrderEvent:
    """Tests for OrderEvent schema."""

    def test_order_event_valid(self):
        """Test creating valid OrderEvent."""
        event = OrderEvent(
            timestamp=datetime.now(UTC),
            run_id="550e8400-e29b-41d4-a716-446655440000",
            strategy_id="alpha_baseline",
            num_orders=3,
            num_accepted=3,
            num_rejected=0,
        )

        assert event.event_type == "orders.executed"
        assert event.run_id == "550e8400-e29b-41d4-a716-446655440000"
        assert event.num_orders == 3
        assert event.num_accepted == 3
        assert event.num_rejected == 0

    def test_order_event_json_serialization(self):
        """Test OrderEvent JSON serialization."""
        event = OrderEvent(
            timestamp=datetime(2025, 1, 17, 9, 1, 0, tzinfo=UTC),
            run_id="550e8400-e29b-41d4-a716-446655440000",
            strategy_id="alpha_baseline",
            num_orders=2,
            num_accepted=2,
            num_rejected=0,
        )

        json_str = event.model_dump_json()

        assert "orders.executed" in json_str
        assert "550e8400-e29b-41d4-a716-446655440000" in json_str

    def test_order_event_naive_timestamp(self):
        """Test OrderEvent validation error on naive timestamp."""
        with pytest.raises(ValidationError, match="timezone-aware"):
            OrderEvent(
                timestamp=datetime.now(),  # Naive
                run_id="550e8400-e29b-41d4-a716-446655440000",
                strategy_id="alpha_baseline",
                num_orders=1,
                num_accepted=1,
                num_rejected=0,
            )

    def test_order_event_negative_counts(self):
        """Test OrderEvent validation error on negative counts."""
        with pytest.raises(ValidationError):
            OrderEvent(
                timestamp=datetime.now(UTC),
                run_id="550e8400-e29b-41d4-a716-446655440000",
                strategy_id="alpha_baseline",
                num_orders=-1,  # Negative not allowed
                num_accepted=0,
                num_rejected=0,
            )

    def test_order_event_partial_rejection(self):
        """Test OrderEvent with partial rejections."""
        event = OrderEvent(
            timestamp=datetime.now(UTC),
            run_id="550e8400-e29b-41d4-a716-446655440000",
            strategy_id="alpha_baseline",
            num_orders=5,
            num_accepted=3,
            num_rejected=2,
        )

        assert event.num_orders == 5
        assert event.num_accepted == 3
        assert event.num_rejected == 2


class TestPositionEvent:
    """Tests for PositionEvent schema."""

    def test_position_event_buy(self):
        """Test creating PositionEvent for buy action."""
        event = PositionEvent(
            timestamp=datetime.now(UTC),
            symbol="AAPL",
            action="buy",
            qty_change=100,
            new_qty=100,
            price="150.25",
            strategy_id="alpha_baseline",
        )

        assert event.event_type == "positions.updated"
        assert event.symbol == "AAPL"
        assert event.action == "buy"
        assert event.qty_change == 100
        assert event.new_qty == 100
        assert event.price == "150.25"

    def test_position_event_sell(self):
        """Test creating PositionEvent for sell action."""
        event = PositionEvent(
            timestamp=datetime.now(UTC),
            symbol="MSFT",
            action="sell",
            qty_change=-50,
            new_qty=50,
            price="300.00",
            strategy_id="alpha_baseline",
        )

        assert event.action == "sell"
        assert event.qty_change == -50
        assert event.new_qty == 50

    def test_position_event_fill(self):
        """Test creating PositionEvent for fill action."""
        event = PositionEvent(
            timestamp=datetime.now(UTC),
            symbol="GOOGL",
            action="fill",
            qty_change=25,
            new_qty=25,
            price="140.50",
            strategy_id="alpha_baseline",
        )

        assert event.action == "fill"

    def test_position_event_invalid_action(self):
        """Test PositionEvent validation error on invalid action."""
        with pytest.raises(ValidationError, match="action must be one of"):
            PositionEvent(
                timestamp=datetime.now(UTC),
                symbol="AAPL",
                action="invalid_action",  # Not in allowed set
                qty_change=100,
                new_qty=100,
                price="150.25",
                strategy_id="alpha_baseline",
            )

    def test_position_event_naive_timestamp(self):
        """Test PositionEvent validation error on naive timestamp."""
        with pytest.raises(ValidationError, match="timezone-aware"):
            PositionEvent(
                timestamp=datetime.now(),  # Naive
                symbol="AAPL",
                action="buy",
                qty_change=100,
                new_qty=100,
                price="150.25",
                strategy_id="alpha_baseline",
            )

    def test_position_event_json_serialization(self):
        """Test PositionEvent JSON serialization."""
        event = PositionEvent(
            timestamp=datetime(2025, 1, 17, 9, 1, 30, tzinfo=UTC),
            symbol="AAPL",
            action="buy",
            qty_change=100,
            new_qty=100,
            price="150.25",
            strategy_id="alpha_baseline",
        )

        json_str = event.model_dump_json()

        assert "positions.updated" in json_str
        assert "AAPL" in json_str
        assert "buy" in json_str
        assert "150.25" in json_str


class TestEventDefaults:
    """Tests for event default values."""

    def test_signal_event_default_event_type(self):
        """Test SignalEvent has correct default event_type."""
        event = SignalEvent(
            timestamp=datetime.now(UTC),
            strategy_id="test",
            symbols=["AAPL"],
            num_signals=1,
            as_of_date="2025-01-17",
        )

        assert event.event_type == "signals.generated"

    def test_order_event_default_event_type(self):
        """Test OrderEvent has correct default event_type."""
        event = OrderEvent(
            timestamp=datetime.now(UTC),
            run_id="test-id",
            strategy_id="test",
            num_orders=1,
            num_accepted=1,
            num_rejected=0,
        )

        assert event.event_type == "orders.executed"

    def test_position_event_default_event_type(self):
        """Test PositionEvent has correct default event_type."""
        event = PositionEvent(
            timestamp=datetime.now(UTC),
            symbol="AAPL",
            action="buy",
            qty_change=100,
            new_qty=100,
            price="150.00",
            strategy_id="test",
        )

        assert event.event_type == "positions.updated"
