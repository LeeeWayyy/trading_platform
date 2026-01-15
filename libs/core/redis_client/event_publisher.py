"""
Event publisher for Redis pub/sub.

Provides high-level interface for publishing events to Redis channels
with automatic serialization, error handling, and logging.

Example:
    >>> from libs.core.redis_client import RedisClient, EventPublisher
    >>> from libs.core.redis_client.events import SignalEvent
    >>> from datetime import datetime, timezone
    >>>
    >>> redis_client = RedisClient()
    >>> publisher = EventPublisher(redis_client)
    >>>
    >>> event = SignalEvent(
    ...     timestamp=datetime.now(timezone.utc),
    ...     strategy_id="alpha_baseline",
    ...     symbols=["AAPL"],
    ...     num_signals=1,
    ...     as_of_date="2025-01-17"
    ... )
    >>>
    >>> publisher.publish_signal_event(event)

See Also:
    - docs/ADRs/0009-redis-integration.md for pub/sub architecture
    - libs/redis_client/events.py for event schemas
"""

import logging

from pydantic import BaseModel
from redis.exceptions import RedisError

from .client import RedisClient
from .events import OrderEvent, PositionEvent, SignalEvent

logger = logging.getLogger(__name__)


class EventPublisher:
    """
    High-level event publisher for Redis pub/sub.

    Handles serialization, channel routing, and error handling.

    Attributes:
        redis: Redis client instance

    Example:
        >>> publisher = EventPublisher(redis_client)
        >>> event = SignalEvent(...)
        >>> num_subscribers = publisher.publish_signal_event(event)
        >>> print(f"Event sent to {num_subscribers} subscribers")

    Notes:
        - Events are serialized to JSON automatically
        - Returns number of subscribers that received message
        - Logs all publish operations for traceability
        - Gracefully handles Redis errors
    """

    # Channel constants
    CHANNEL_SIGNALS = "signals.generated"
    CHANNEL_ORDERS = "orders.executed"
    CHANNEL_POSITIONS = "positions.updated"

    def __init__(self, redis_client: RedisClient):
        """
        Initialize event publisher.

        Args:
            redis_client: Initialized Redis client
        """
        self.redis = redis_client
        logger.info("Event publisher initialized")

    def publish(self, channel: str, event: BaseModel) -> int | None:
        """
        Publish Pydantic event to Redis channel.

        Args:
            channel: Redis channel name
            event: Pydantic event model to publish

        Returns:
            Number of subscribers that received the message, or None on error

        Raises:
            ValueError: If event serialization fails

        Example:
            >>> event = SignalEvent(...)
            >>> num_subscribers = publisher.publish("signals.generated", event)
        """
        try:
            # Serialize event to JSON
            message = event.model_dump_json()

            # Publish to channel
            num_subscribers = self.redis.publish(channel, message)

            logger.info(
                f"Published {event.event_type} to '{channel}' "  # type: ignore[attr-defined]
                f"({num_subscribers} subscribers)"
            )

            return num_subscribers

        except (TypeError, ValueError) as e:
            logger.error(f"Failed to serialize event {event.event_type}: {e}")  # type: ignore[attr-defined]
            raise ValueError(f"Cannot serialize event: {e}") from e

        except RedisError as e:
            logger.error(f"Failed to publish {event.event_type} to '{channel}': {e}")  # type: ignore[attr-defined]
            # Return None on error (graceful degradation)
            return None

    def publish_signal_event(self, event: SignalEvent) -> int | None:
        """
        Publish signal generation event.

        Args:
            event: SignalEvent to publish

        Returns:
            Number of subscribers, or None on error

        Example:
            >>> from datetime import datetime, timezone
            >>> event = SignalEvent(
            ...     timestamp=datetime.now(timezone.utc),
            ...     strategy_id="alpha_baseline",
            ...     symbols=["AAPL", "MSFT"],
            ...     num_signals=2,
            ...     as_of_date="2025-01-17"
            ... )
            >>> publisher.publish_signal_event(event)
        """
        return self.publish(self.CHANNEL_SIGNALS, event)

    def publish_order_event(self, event: OrderEvent) -> int | None:
        """
        Publish order execution event.

        Args:
            event: OrderEvent to publish

        Returns:
            Number of subscribers, or None on error

        Example:
            >>> from datetime import datetime, timezone
            >>> event = OrderEvent(
            ...     timestamp=datetime.now(timezone.utc),
            ...     run_id="550e8400-e29b-41d4-a716-446655440000",
            ...     strategy_id="alpha_baseline",
            ...     num_orders=3,
            ...     num_accepted=3,
            ...     num_rejected=0
            ... )
            >>> publisher.publish_order_event(event)
        """
        return self.publish(self.CHANNEL_ORDERS, event)

    def publish_position_event(self, event: PositionEvent) -> int | None:
        """
        Publish position update event.

        Args:
            event: PositionEvent to publish

        Returns:
            Number of subscribers, or None on error

        Example:
            >>> from datetime import datetime, timezone
            >>> event = PositionEvent(
            ...     timestamp=datetime.now(timezone.utc),
            ...     symbol="AAPL",
            ...     action="buy",
            ...     qty_change=100,
            ...     new_qty=100,
            ...     price="150.25",
            ...     strategy_id="alpha_baseline"
            ... )
            >>> publisher.publish_position_event(event)
        """
        return self.publish(self.CHANNEL_POSITIONS, event)

    def __repr__(self) -> str:
        """String representation."""
        return (
            "EventPublisher(channels=['signals.generated', 'orders.executed', 'positions.updated'])"
        )
