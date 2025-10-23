"""
Unit tests for EventPublisher.

Tests cover:
- Event publishing
- Channel routing
- JSON serialization
- Subscriber counting
- Error handling
"""

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from redis.exceptions import RedisError

from libs.redis_client.event_publisher import EventPublisher
from libs.redis_client.events import OrderEvent, PositionEvent, SignalEvent


class TestEventPublisherInitialization:
    """Tests for EventPublisher initialization."""

    def test_initialization(self):
        """Test EventPublisher initialization."""
        mock_redis = Mock()
        publisher = EventPublisher(mock_redis)

        assert publisher.redis is mock_redis

    def test_channel_constants(self):
        """Test channel constants are defined correctly."""
        assert EventPublisher.CHANNEL_SIGNALS == "signals.generated"
        assert EventPublisher.CHANNEL_ORDERS == "orders.executed"
        assert EventPublisher.CHANNEL_POSITIONS == "positions.updated"


class TestEventPublisherPublish:
    """Tests for generic publish method."""

    @pytest.fixture()
    def mock_publisher(self):
        """Create mock event publisher."""
        mock_redis = Mock()
        publisher = EventPublisher(mock_redis)
        return publisher, mock_redis

    def test_publish_success(self, mock_publisher):
        """Test successful event publishing."""
        publisher, mock_redis = mock_publisher
        mock_redis.publish.return_value = 2  # 2 subscribers

        event = SignalEvent(
            timestamp=datetime.now(UTC),
            strategy_id="alpha_baseline",
            symbols=["AAPL"],
            num_signals=1,
            as_of_date="2025-01-17",
        )

        num_subscribers = publisher.publish("test_channel", event)

        assert num_subscribers == 2
        mock_redis.publish.assert_called_once()

        # Verify message is JSON string
        call_args = mock_redis.publish.call_args[0]
        assert call_args[0] == "test_channel"
        assert "signals.generated" in call_args[1]

    def test_publish_no_subscribers(self, mock_publisher):
        """Test publishing with no subscribers."""
        publisher, mock_redis = mock_publisher
        mock_redis.publish.return_value = 0

        event = SignalEvent(
            timestamp=datetime.now(UTC),
            strategy_id="alpha_baseline",
            symbols=["AAPL"],
            num_signals=1,
            as_of_date="2025-01-17",
        )

        num_subscribers = publisher.publish("test_channel", event)

        assert num_subscribers == 0

    def test_publish_redis_error(self, mock_publisher):
        """Test publishing with Redis error."""
        publisher, mock_redis = mock_publisher
        mock_redis.publish.side_effect = RedisError("Connection lost")

        event = SignalEvent(
            timestamp=datetime.now(UTC),
            strategy_id="alpha_baseline",
            symbols=["AAPL"],
            num_signals=1,
            as_of_date="2025-01-17",
        )

        num_subscribers = publisher.publish("test_channel", event)

        # Should return None on error (graceful degradation)
        assert num_subscribers is None


class TestEventPublisherSignalEvents:
    """Tests for publishing SignalEvents."""

    @pytest.fixture()
    def mock_publisher(self):
        """Create mock event publisher."""
        mock_redis = Mock()
        publisher = EventPublisher(mock_redis)
        return publisher, mock_redis

    def test_publish_signal_event(self, mock_publisher):
        """Test publishing SignalEvent."""
        publisher, mock_redis = mock_publisher
        mock_redis.publish.return_value = 1

        event = SignalEvent(
            timestamp=datetime.now(UTC),
            strategy_id="alpha_baseline",
            symbols=["AAPL", "MSFT"],
            num_signals=2,
            as_of_date="2025-01-17",
        )

        num_subscribers = publisher.publish_signal_event(event)

        assert num_subscribers == 1
        mock_redis.publish.assert_called_once()

        # Verify correct channel
        call_args = mock_redis.publish.call_args[0]
        assert call_args[0] == "signals.generated"

        # Verify JSON contains event data
        json_str = call_args[1]
        assert "alpha_baseline" in json_str
        assert "AAPL" in json_str
        assert "MSFT" in json_str

    def test_publish_signal_event_multiple_symbols(self, mock_publisher):
        """Test publishing SignalEvent with many symbols."""
        publisher, mock_redis = mock_publisher
        mock_redis.publish.return_value = 3

        symbols = [f"SYM{i}" for i in range(10)]
        event = SignalEvent(
            timestamp=datetime.now(UTC),
            strategy_id="alpha_baseline",
            symbols=symbols,
            num_signals=10,
            as_of_date="2025-01-17",
        )

        num_subscribers = publisher.publish_signal_event(event)

        assert num_subscribers == 3


class TestEventPublisherOrderEvents:
    """Tests for publishing OrderEvents."""

    @pytest.fixture()
    def mock_publisher(self):
        """Create mock event publisher."""
        mock_redis = Mock()
        publisher = EventPublisher(mock_redis)
        return publisher, mock_redis

    def test_publish_order_event(self, mock_publisher):
        """Test publishing OrderEvent."""
        publisher, mock_redis = mock_publisher
        mock_redis.publish.return_value = 2

        event = OrderEvent(
            timestamp=datetime.now(UTC),
            run_id="550e8400-e29b-41d4-a716-446655440000",
            strategy_id="alpha_baseline",
            num_orders=3,
            num_accepted=3,
            num_rejected=0,
        )

        num_subscribers = publisher.publish_order_event(event)

        assert num_subscribers == 2
        mock_redis.publish.assert_called_once()

        # Verify correct channel
        call_args = mock_redis.publish.call_args[0]
        assert call_args[0] == "orders.executed"

        # Verify JSON contains event data
        json_str = call_args[1]
        assert "550e8400-e29b-41d4-a716-446655440000" in json_str
        assert "alpha_baseline" in json_str

    def test_publish_order_event_with_rejections(self, mock_publisher):
        """Test publishing OrderEvent with partial rejections."""
        publisher, mock_redis = mock_publisher
        mock_redis.publish.return_value = 1

        event = OrderEvent(
            timestamp=datetime.now(UTC),
            run_id="test-run-id",
            strategy_id="alpha_baseline",
            num_orders=5,
            num_accepted=3,
            num_rejected=2,
        )

        num_subscribers = publisher.publish_order_event(event)

        assert num_subscribers == 1

        # Verify rejection counts in JSON
        call_args = mock_redis.publish.call_args[0]
        json_str = call_args[1]
        assert '"num_rejected":2' in json_str.replace(" ", "")


class TestEventPublisherPositionEvents:
    """Tests for publishing PositionEvents."""

    @pytest.fixture()
    def mock_publisher(self):
        """Create mock event publisher."""
        mock_redis = Mock()
        publisher = EventPublisher(mock_redis)
        return publisher, mock_redis

    def test_publish_position_event_buy(self, mock_publisher):
        """Test publishing PositionEvent for buy action."""
        publisher, mock_redis = mock_publisher
        mock_redis.publish.return_value = 1

        event = PositionEvent(
            timestamp=datetime.now(UTC),
            symbol="AAPL",
            action="buy",
            qty_change=100,
            new_qty=100,
            price="150.25",
            strategy_id="alpha_baseline",
        )

        num_subscribers = publisher.publish_position_event(event)

        assert num_subscribers == 1
        mock_redis.publish.assert_called_once()

        # Verify correct channel
        call_args = mock_redis.publish.call_args[0]
        assert call_args[0] == "positions.updated"

        # Verify JSON contains event data
        json_str = call_args[1]
        assert "AAPL" in json_str
        assert "buy" in json_str
        assert "150.25" in json_str

    def test_publish_position_event_sell(self, mock_publisher):
        """Test publishing PositionEvent for sell action."""
        publisher, mock_redis = mock_publisher
        mock_redis.publish.return_value = 2

        event = PositionEvent(
            timestamp=datetime.now(UTC),
            symbol="MSFT",
            action="sell",
            qty_change=-50,
            new_qty=50,
            price="300.00",
            strategy_id="alpha_baseline",
        )

        num_subscribers = publisher.publish_position_event(event)

        assert num_subscribers == 2

        # Verify sell action in JSON
        call_args = mock_redis.publish.call_args[0]
        json_str = call_args[1]
        assert "sell" in json_str
        assert '"qty_change":-50' in json_str.replace(" ", "")


class TestEventPublisherEndToEnd:
    """End-to-end tests for typical publishing patterns."""

    @pytest.fixture()
    def mock_publisher(self):
        """Create mock event publisher."""
        mock_redis = Mock()
        publisher = EventPublisher(mock_redis)
        return publisher, mock_redis

    def test_publish_multiple_event_types(self, mock_publisher):
        """Test publishing different event types sequentially."""
        publisher, mock_redis = mock_publisher
        mock_redis.publish.return_value = 1

        # Publish SignalEvent
        signal_event = SignalEvent(
            timestamp=datetime.now(UTC),
            strategy_id="alpha_baseline",
            symbols=["AAPL"],
            num_signals=1,
            as_of_date="2025-01-17",
        )
        publisher.publish_signal_event(signal_event)

        # Publish OrderEvent
        order_event = OrderEvent(
            timestamp=datetime.now(UTC),
            run_id="test-id",
            strategy_id="alpha_baseline",
            num_orders=1,
            num_accepted=1,
            num_rejected=0,
        )
        publisher.publish_order_event(order_event)

        # Publish PositionEvent
        position_event = PositionEvent(
            timestamp=datetime.now(UTC),
            symbol="AAPL",
            action="buy",
            qty_change=100,
            new_qty=100,
            price="150.00",
            strategy_id="alpha_baseline",
        )
        publisher.publish_position_event(position_event)

        # Verify all three were published
        assert mock_redis.publish.call_count == 3

        # Verify correct channels were used
        channels = [call[0][0] for call in mock_redis.publish.call_args_list]
        assert "signals.generated" in channels
        assert "orders.executed" in channels
        assert "positions.updated" in channels

    def test_publish_workflow_simulation(self, mock_publisher):
        """Test publishing events in typical trading workflow order."""
        publisher, mock_redis = mock_publisher
        mock_redis.publish.return_value = 1

        # Step 1: Signals generated
        signal_event = SignalEvent(
            timestamp=datetime(2025, 1, 17, 9, 0, 0, tzinfo=UTC),
            strategy_id="alpha_baseline",
            symbols=["AAPL", "MSFT"],
            num_signals=2,
            as_of_date="2025-01-17",
        )
        publisher.publish_signal_event(signal_event)

        # Step 2: Orders executed
        order_event = OrderEvent(
            timestamp=datetime(2025, 1, 17, 9, 1, 0, tzinfo=UTC),
            run_id="run-123",
            strategy_id="alpha_baseline",
            num_orders=2,
            num_accepted=2,
            num_rejected=0,
        )
        publisher.publish_order_event(order_event)

        # Step 3: Positions updated
        for symbol in ["AAPL", "MSFT"]:
            position_event = PositionEvent(
                timestamp=datetime(2025, 1, 17, 9, 1, 30, tzinfo=UTC),
                symbol=symbol,
                action="fill",
                qty_change=100,
                new_qty=100,
                price="150.00",
                strategy_id="alpha_baseline",
            )
            publisher.publish_position_event(position_event)

        # Verify correct number of events published
        assert mock_redis.publish.call_count == 4  # 1 signal + 1 order + 2 positions
