"""
Event schemas for Redis pub/sub.

Defines Pydantic models for events published to Redis channels.
All events include timestamp and event_type for traceability.

Channels:
    signals.generated: Published when signals are generated (T3)
    orders.executed: Published when orders are executed (T4)
    positions.updated: Published when positions change (T4)

Example:
    >>> from libs.redis_client.events import SignalEvent
    >>> from datetime import datetime, timezone
    >>>
    >>> event = SignalEvent(
    ...     timestamp=datetime.now(timezone.utc),
    ...     strategy_id="alpha_baseline",
    ...     symbols=["AAPL", "MSFT"],
    ...     num_signals=2,
    ...     as_of_date="2025-01-17"
    ... )
    >>> print(event.model_dump_json())

See Also:
    - docs/ADRs/0009-redis-integration.md for event architecture
    - libs/redis_client/event_publisher.py for publishing
"""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class SignalEvent(BaseModel):
    """
    Event published when trading signals are generated.

    Published to channel: signals.generated
    Publisher: T3 Signal Service

    This event notifies subscribers that new signals are available,
    enabling event-driven orchestration workflows.

    Attributes:
        event_type: Always "signals.generated"
        timestamp: When signals were generated (UTC)
        strategy_id: Strategy that generated signals
        symbols: List of symbols with signals
        num_signals: Number of signals generated
        as_of_date: Date for which signals were generated

    Example:
        {
            "event_type": "signals.generated",
            "timestamp": "2025-01-18T09:00:00+00:00",
            "strategy_id": "alpha_baseline",
            "symbols": ["AAPL", "MSFT", "GOOGL"],
            "num_signals": 3,
            "as_of_date": "2025-01-17"
        }
    """

    event_type: str = Field(default="signals.generated", description="Event type identifier")

    timestamp: datetime = Field(..., description="Event timestamp (UTC)")

    strategy_id: str = Field(..., description="Strategy that generated signals")

    symbols: list[str] = Field(..., min_length=1, description="Symbols with generated signals")

    num_signals: int = Field(..., ge=0, description="Number of signals generated")

    as_of_date: str = Field(..., description="Date for which signals were generated (ISO format)")

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp_utc(cls, v: datetime) -> datetime:
        """Ensure timestamp is timezone-aware."""
        if v.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware (use timezone.utc)")
        return v


class OrderEvent(BaseModel):
    """
    Event published when orders are executed.

    Published to channel: orders.executed
    Publisher: T4 Execution Gateway or T5 Orchestrator

    This event notifies subscribers about order execution results,
    useful for monitoring and alerting.

    Attributes:
        event_type: Always "orders.executed"
        timestamp: When orders were executed (UTC)
        run_id: Orchestration run ID (UUID)
        strategy_id: Strategy that generated orders
        num_orders: Total number of orders submitted
        num_accepted: Number of orders accepted
        num_rejected: Number of orders rejected

    Example:
        {
            "event_type": "orders.executed",
            "timestamp": "2025-01-18T09:01:00+00:00",
            "run_id": "550e8400-e29b-41d4-a716-446655440000",
            "strategy_id": "alpha_baseline",
            "num_orders": 3,
            "num_accepted": 3,
            "num_rejected": 0
        }
    """

    event_type: str = Field(default="orders.executed", description="Event type identifier")

    timestamp: datetime = Field(..., description="Event timestamp (UTC)")

    run_id: str = Field(..., description="Orchestration run ID (UUID)")

    strategy_id: str = Field(..., description="Strategy that generated orders")

    num_orders: int = Field(..., ge=0, description="Total number of orders submitted")

    num_accepted: int = Field(..., ge=0, description="Number of orders accepted")

    num_rejected: int = Field(..., ge=0, description="Number of orders rejected")

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp_utc(cls, v: datetime) -> datetime:
        """Ensure timestamp is timezone-aware."""
        if v.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware (use timezone.utc)")
        return v


class PositionEvent(BaseModel):
    """
    Event published when positions are updated.

    Published to channel: positions.updated
    Publisher: T4 Execution Gateway

    This event notifies subscribers about position changes,
    useful for real-time P&L monitoring and risk management.

    Attributes:
        event_type: Always "positions.updated"
        timestamp: When position was updated (UTC)
        symbol: Stock symbol
        action: Action that caused update ("buy", "sell", "fill")
        qty_change: Change in position quantity (signed)
        new_qty: New total position quantity (signed)
        price: Execution price
        strategy_id: Strategy that owns the position

    Example:
        {
            "event_type": "positions.updated",
            "timestamp": "2025-01-18T09:01:30+00:00",
            "symbol": "AAPL",
            "action": "buy",
            "qty_change": 100,
            "new_qty": 100,
            "price": "150.25",
            "strategy_id": "alpha_baseline"
        }
    """

    event_type: str = Field(default="positions.updated", description="Event type identifier")

    timestamp: datetime = Field(..., description="Event timestamp (UTC)")

    symbol: str = Field(..., description="Stock symbol")

    action: str = Field(..., description="Action that caused update (buy/sell/fill)")

    qty_change: int = Field(..., description="Change in position quantity (signed)")

    new_qty: int = Field(..., description="New total position quantity (signed)")

    price: str = Field(..., description="Execution price (Decimal as string)")

    strategy_id: str = Field(..., description="Strategy that owns the position")

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp_utc(cls, v: datetime) -> datetime:
        """Ensure timestamp is timezone-aware."""
        if v.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware (use timezone.utc)")
        return v

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        """Validate action is one of allowed values."""
        allowed = {"buy", "sell", "fill", "partial_fill"}
        if v not in allowed:
            raise ValueError(f"action must be one of {allowed}, got '{v}'")
        return v
