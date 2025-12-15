"""
Pydantic schemas for Execution Gateway API.

Defines request and response models for all endpoints, ensuring type safety
and validation at the API boundary.
"""

import math
import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, Field, field_validator, model_validator

from libs.common import TimestampSerializerMixin

# ============================================================================
# Type Aliases
# ============================================================================

# Order status type - used consistently across OrderResponse, OrderDetail, and SliceDetail
# DRY principle: Define once, use everywhere
OrderStatus: TypeAlias = Literal[
    "pending_new",
    "accepted",
    "filled",
    "canceled",
    "rejected",
    "expired",
    "replaced",
    "done_for_day",
    "stopped",
    "suspended",
    "pending_cancel",
    "pending_replace",
    "calculated",
    "submitted",
    "submitted_unconfirmed",  # Broker submitted but DB update failed (reconciliation needed)
    "dry_run",
    "failed",
    "blocked_kill_switch",
    "blocked_circuit_breaker",
]

# ============================================================================
# Order Schemas
# ============================================================================


class OrderRequest(BaseModel):
    """
    Request to submit a new order.

    The order will be assigned a deterministic client_order_id based on
    the order parameters and current date. This ensures idempotency - the
    same order submitted multiple times will have the same ID.

    Examples:
        Market buy order:
        >>> order = OrderRequest(
        ...     symbol="AAPL",
        ...     side="buy",
        ...     qty=10,
        ...     order_type="market"
        ... )

        Limit sell order:
        >>> order = OrderRequest(
        ...     symbol="MSFT",
        ...     side="sell",
        ...     qty=5,
        ...     order_type="limit",
        ...     limit_price=300.50
        ... )
    """

    symbol: str = Field(..., description="Stock symbol (e.g., 'AAPL')")
    side: Literal["buy", "sell"] = Field(..., description="Order side")
    qty: int = Field(..., gt=0, description="Order quantity (must be positive)")
    order_type: Literal["market", "limit", "stop", "stop_limit"] = Field(
        default="market", description="Order type"
    )
    limit_price: Decimal | None = Field(
        default=None, description="Limit price (required for limit orders)"
    )
    stop_price: Decimal | None = Field(
        default=None, description="Stop price (required for stop orders)"
    )
    time_in_force: Literal["day", "gtc", "ioc", "fok"] = Field(
        default="day", description="Time in force"
    )

    @field_validator("symbol")
    @classmethod
    def symbol_uppercase(cls, v: str) -> str:
        """Ensure symbol is uppercase."""
        return v.upper()

    @field_validator("limit_price", "stop_price")
    @classmethod
    def price_positive(cls, v: Decimal | None) -> Decimal | None:
        """Ensure prices are positive if provided."""
        if v is not None and v <= 0:
            raise ValueError("Price must be positive")
        return v

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "symbol": "AAPL",
                    "side": "buy",
                    "qty": 10,
                    "order_type": "market",
                    "time_in_force": "day",
                },
                {
                    "symbol": "MSFT",
                    "side": "sell",
                    "qty": 5,
                    "order_type": "limit",
                    "limit_price": "300.50",
                    "time_in_force": "day",
                },
            ]
        }
    }


class OrderResponse(BaseModel):
    """
    Response after submitting an order.

    Contains the deterministic client_order_id, current status, and
    broker_order_id (if submitted to broker).

    Attributes:
        client_order_id: Deterministic ID for idempotency
        status: Current order status
        broker_order_id: Alpaca's order ID (null for dry_run)
        symbol: Stock symbol
        side: Order side
        qty: Order quantity
        order_type: Order type
        limit_price: Limit price (if applicable)
        created_at: Order creation timestamp
        message: Human-readable status message
    """

    client_order_id: str
    status: OrderStatus
    broker_order_id: str | None = None
    symbol: str
    side: Literal["buy", "sell"]
    qty: int
    order_type: Literal["market", "limit", "stop", "stop_limit"]
    limit_price: Decimal | None = None
    created_at: datetime
    message: str

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "client_order_id": "a1b2c3d4e5f6g7h8i9j0k1l2",
                    "status": "pending_new",
                    "broker_order_id": "f7e6d5c4-b3a2-1098-7654-3210fedcba98",
                    "symbol": "AAPL",
                    "side": "buy",
                    "qty": 10,
                    "order_type": "market",
                    "limit_price": None,
                    "created_at": "2024-10-17T16:30:00Z",
                    "message": "Order submitted to broker",
                },
                {
                    "client_order_id": "z9y8x7w6v5u4t3s2r1q0p9o8",
                    "status": "dry_run",
                    "broker_order_id": None,
                    "symbol": "MSFT",
                    "side": "sell",
                    "qty": 5,
                    "order_type": "limit",
                    "limit_price": "300.50",
                    "created_at": "2024-10-17T16:31:00Z",
                    "message": "Order logged (DRY_RUN mode)",
                },
            ]
        }
    }


class OrderDetail(BaseModel):
    """
    Detailed order information including fill details and timestamps.

    Used for GET /api/v1/orders/{client_order_id} endpoint.

    Slicing Fields (P2T0 - TWAP Order Slicing):
        parent_order_id: Links child slice to parent (NULL for parent orders)
        slice_num: Sequential slice number 0..N-1 (NULL for parent orders)
        total_slices: Total number of slices planned (set on parent, NULL on children)
        scheduled_time: UTC timestamp for slice execution (NULL for parent, set on children)
    """

    client_order_id: str
    strategy_id: str
    symbol: str
    side: Literal["buy", "sell"]
    qty: int
    order_type: Literal["market", "limit", "stop", "stop_limit"]
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: Literal["day", "gtc", "ioc", "fok"]
    status: OrderStatus
    broker_order_id: str | None = None
    error_message: str | None = None
    retry_count: int
    created_at: datetime
    updated_at: datetime
    submitted_at: datetime | None = None
    filled_at: datetime | None = None
    filled_qty: Decimal
    filled_avg_price: Decimal | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    # TWAP slicing fields (P2T0)
    parent_order_id: str | None = None
    slice_num: int | None = None
    total_slices: int | None = None
    scheduled_time: datetime | None = None


# ============================================================================
# Position Schemas
# ============================================================================


class Position(BaseModel):
    """
    Current position for a symbol.

    Attributes:
        symbol: Stock symbol
        qty: Position quantity (positive=long, negative=short, zero=flat)
        avg_entry_price: Average entry price
        current_price: Current market price (if available)
        unrealized_pl: Unrealized P&L
        realized_pl: Realized P&L from closed positions
        updated_at: Last update timestamp
        last_trade_at: Last trade timestamp
    """

    symbol: str
    qty: Decimal
    avg_entry_price: Decimal
    current_price: Decimal | None = None
    unrealized_pl: Decimal | None = None
    realized_pl: Decimal
    updated_at: datetime
    last_trade_at: datetime | None = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "symbol": "AAPL",
                    "qty": "10",
                    "avg_entry_price": "150.25",
                    "current_price": "152.75",
                    "unrealized_pl": "25.00",
                    "realized_pl": "0.00",
                    "updated_at": "2024-10-17T16:30:00Z",
                    "last_trade_at": "2024-10-17T16:30:00Z",
                }
            ]
        }
    }


class PositionsResponse(BaseModel):
    """Response containing list of current positions."""

    positions: list[Position]
    total_positions: int
    total_unrealized_pl: Decimal | None = None
    total_realized_pl: Decimal

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "positions": [
                        {
                            "symbol": "AAPL",
                            "qty": "10",
                            "avg_entry_price": "150.25",
                            "current_price": "152.75",
                            "unrealized_pl": "25.00",
                            "realized_pl": "0.00",
                            "updated_at": "2024-10-17T16:30:00Z",
                        }
                    ],
                    "total_positions": 1,
                    "total_unrealized_pl": "25.00",
                    "total_realized_pl": "0.00",
                }
            ]
        }
    }


class RealtimePositionPnL(BaseModel):
    """
    Real-time P&L for a single position.

    Uses latest prices from Redis cache (market data service).
    Falls back to database price if real-time data unavailable.
    """

    symbol: str
    qty: Decimal
    avg_entry_price: Decimal
    current_price: Decimal
    price_source: Literal["real-time", "database", "fallback"] = Field(
        description="Source of current price (real-time=Redis, database=last known, fallback=entry price)"
    )
    unrealized_pl: Decimal
    unrealized_pl_pct: Decimal = Field(description="Unrealized P&L as percentage")
    last_price_update: datetime | None = Field(
        None, description="Timestamp of last price update from market data"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "symbol": "AAPL",
                    "qty": "10",
                    "avg_entry_price": "150.00",
                    "current_price": "152.50",
                    "price_source": "real-time",
                    "unrealized_pl": "25.00",
                    "unrealized_pl_pct": "1.67",
                    "last_price_update": "2024-10-19T14:30:15Z",
                }
            ]
        }
    }


class RealtimePnLResponse(BaseModel):
    """
    Response with real-time P&L for all positions.

    Fetches latest prices from Redis (populated by Market Data Service).
    Falls back to database prices if real-time data unavailable.
    """

    positions: list[RealtimePositionPnL]
    total_positions: int
    total_unrealized_pl: Decimal
    total_unrealized_pl_pct: Decimal | None = Field(
        None, description="Total unrealized P&L as percentage of total investment"
    )
    realtime_prices_available: int = Field(description="Number of positions with real-time prices")
    timestamp: datetime = Field(description="Response generation timestamp")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "positions": [
                        {
                            "symbol": "AAPL",
                            "qty": "10",
                            "avg_entry_price": "150.00",
                            "current_price": "152.50",
                            "price_source": "real-time",
                            "unrealized_pl": "25.00",
                            "unrealized_pl_pct": "1.67",
                            "last_price_update": "2024-10-19T14:30:15Z",
                        }
                    ],
                    "total_positions": 1,
                    "total_unrealized_pl": "25.00",
                    "total_unrealized_pl_pct": "1.67",
                    "realtime_prices_available": 1,
                    "timestamp": "2024-10-19T14:30:20Z",
                }
            ]
        }
    }


# ============================================================================
# Webhook Schemas
# ============================================================================


class OrderEventData(BaseModel):
    """
    Order event data from Alpaca webhook.

    See: https://docs.alpaca.markets/docs/webhooks#order-updates
    """

    event: Literal[
        "new",
        "fill",
        "partial_fill",
        "canceled",
        "expired",
        "done_for_day",
        "replaced",
        "rejected",
        "pending_new",
        "pending_cancel",
        "pending_replace",
        "stopped",
        "suspended",
        "calculated",
    ]
    order: dict[str, Any]  # Full order object from Alpaca
    timestamp: datetime
    execution_id: str | None = None
    position_qty: str | None = None


class WebhookEvent(BaseModel):
    """
    Webhook event envelope from Alpaca.

    Contains the event type and data payload.
    """

    event_type: str = Field(..., alias="event")
    data: OrderEventData

    model_config = {"populate_by_name": True}


# ============================================================================
# Health Check Schema
# ============================================================================


class HealthResponse(TimestampSerializerMixin, BaseModel):
    """Health check response."""

    status: Literal["healthy", "degraded", "unhealthy"]
    service: str = "execution_gateway"
    version: str
    dry_run: bool
    database_connected: bool
    alpaca_connected: bool
    timestamp: datetime
    details: dict[str, Any] | None = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "status": "healthy",
                    "service": "execution_gateway",
                    "version": "0.1.0",
                    "dry_run": True,
                    "database_connected": True,
                    "alpaca_connected": True,
                    "timestamp": "2024-10-17T16:30:00Z",
                    "details": {},
                }
            ]
        }
    }


# ============================================================================
# Configuration Schema
# ============================================================================


class ConfigResponse(TimestampSerializerMixin, BaseModel):
    """
    Configuration verification response.

    Exposes critical safety flags and environment settings for automated
    verification in smoke tests and monitoring. Used to ensure paper trading
    mode is active in staging/CI environments.

    Examples:
        >>> config = ConfigResponse(
        ...     service="execution_gateway",
        ...     version="0.1.0",
        ...     environment="staging",
        ...     dry_run=True,
        ...     alpaca_paper=True,
        ...     circuit_breaker_enabled=True,
        ...     timestamp=datetime.now(UTC)
        ... )
        >>> assert config.dry_run is True  # Staging safety check
        >>> assert config.alpaca_paper is True
    """

    service: str = Field(..., description="Service name")
    version: str = Field(..., description="Service version")
    environment: str = Field(..., description="Environment (dev, staging, production)")
    dry_run: bool = Field(..., description="Dry-run mode enabled (no real orders)")
    alpaca_paper: bool = Field(..., description="Alpaca paper trading mode")
    circuit_breaker_enabled: bool = Field(..., description="Circuit breaker feature enabled")
    timestamp: datetime = Field(..., description="Response timestamp (UTC)")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "service": "execution_gateway",
                    "version": "0.1.0",
                    "environment": "staging",
                    "dry_run": True,
                    "alpaca_paper": True,
                    "circuit_breaker_enabled": True,
                    "timestamp": "2025-10-22T10:30:00Z",
                }
            ]
        }
    }


# ============================================================================
# Kill-Switch Schemas
# ============================================================================


class KillSwitchEngageRequest(BaseModel):
    """Request to engage kill-switch (emergency halt)."""

    reason: str = Field(..., description="Human-readable reason for engagement")
    operator: str = Field(..., description="Operator ID/name (for audit trail)")
    details: dict[str, Any] | None = Field(None, description="Optional additional context")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "reason": "Market anomaly detected",
                    "operator": "ops_team",
                    "details": {"anomaly_type": "flash_crash", "severity": "high"},
                }
            ]
        }
    }


class KillSwitchDisengageRequest(BaseModel):
    """Request to disengage kill-switch (resume trading)."""

    operator: str = Field(..., description="Operator ID/name (for audit trail)")
    notes: str | None = Field(None, description="Optional notes about resolution")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "operator": "ops_team",
                    "notes": "Market conditions normalized, all systems operational",
                }
            ]
        }
    }


# ============================================================================
# TWAP Order Slicing Schemas (P2T0)
# ============================================================================


class SlicingRequest(BaseModel):
    """
    Request to create a TWAP (Time-Weighted Average Price) order slicing plan.

    Large parent orders are split into smaller child slices distributed evenly
    over a time period to minimize market impact.

    Examples:
        Market order slicing:
        >>> request = SlicingRequest(
        ...     symbol="AAPL",
        ...     side="buy",
        ...     qty=100,
        ...     duration_minutes=5,
        ...     interval_seconds=60,
        ...     order_type="market"
        ... )

        Custom interval slicing:
        >>> request = SlicingRequest(
        ...     symbol="MSFT",
        ...     side="sell",
        ...     qty=600,
        ...     duration_minutes=60,
        ...     interval_seconds=300,
        ...     order_type="limit",
        ...     limit_price=300.50
        ... )
    """

    symbol: str = Field(..., description="Stock symbol (e.g., 'AAPL')")
    side: Literal["buy", "sell"] = Field(..., description="Order side")
    qty: int = Field(..., gt=0, description="Total order quantity (must be positive)")
    duration_minutes: int = Field(..., gt=0, description="Total slicing duration in minutes")
    interval_seconds: int = Field(
        default=60,
        gt=0,
        description="Interval between slices in seconds (default: 60 = 1 minute)",
    )
    order_type: Literal["market", "limit", "stop", "stop_limit"] = Field(
        default="market", description="Order type for each slice"
    )
    limit_price: Decimal | None = Field(
        default=None, description="Limit price (required for limit orders)"
    )
    stop_price: Decimal | None = Field(
        default=None, description="Stop price (required for stop orders)"
    )
    time_in_force: Literal["day", "gtc", "ioc", "fok"] = Field(
        default="day", description="Time in force for each slice"
    )
    trade_date: date | None = Field(
        default=None,
        description="Trading date for order ID generation (defaults to today UTC). "
        "CRITICAL for idempotency: retries after midnight must pass same trade_date "
        "to avoid creating duplicate orders.",
    )

    @field_validator("symbol")
    @classmethod
    def symbol_uppercase(cls, v: str) -> str:
        """Ensure symbol is uppercase."""
        return v.upper()

    @field_validator("limit_price", "stop_price")
    @classmethod
    def price_positive(cls, v: Decimal | None) -> Decimal | None:
        """Ensure prices are positive if provided."""
        if v is not None and v <= 0:
            raise ValueError("Price must be positive")
        return v

    @model_validator(mode="after")
    def validate_qty_duration_relationship(self) -> "SlicingRequest":
        """
        Ensure qty >= required slices.

        Compute required number of slices from duration + interval and ensure
        qty is sufficient to allocate at least one share to each slice.

        Raises:
            ValueError: If qty < required_slices
        """
        total_duration_seconds = self.duration_minutes * 60
        required_slices = max(1, math.ceil(total_duration_seconds / self.interval_seconds))

        if self.qty < required_slices:
            raise ValueError(
                f"qty ({self.qty}) must be >= number of slices ({required_slices}) derived from "
                f"duration and interval to avoid zero-quantity slices"
            )
        return self

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "symbol": "AAPL",
                    "side": "buy",
                    "qty": 100,
                    "duration_minutes": 5,
                    "interval_seconds": 60,
                    "order_type": "market",
                    "time_in_force": "day",
                },
                {
                    "symbol": "MSFT",
                    "side": "sell",
                    "qty": 50,
                    "duration_minutes": 10,
                    "interval_seconds": 300,
                    "order_type": "limit",
                    "limit_price": "300.50",
                    "time_in_force": "day",
                },
            ]
        }
    }


class SliceDetail(BaseModel):
    """
    Details for a single TWAP child slice.

    Attributes:
        slice_num: Sequential slice number (0-indexed)
        qty: Slice quantity
        scheduled_time: UTC timestamp for scheduled execution
        client_order_id: Deterministic ID for this slice
        strategy_id: Strategy identifier used for this slice's order ID generation
        status: Slice status (uses order status vocabulary from orders table)
    """

    slice_num: int = Field(..., ge=0, description="Slice number (0-indexed)")
    qty: int = Field(..., gt=0, description="Slice quantity")
    scheduled_time: datetime = Field(..., description="Scheduled execution time (UTC)")
    client_order_id: str = Field(..., description="Deterministic slice order ID")
    strategy_id: str = Field(
        ..., description="Strategy ID for this slice (e.g., 'twap_slice_parent123_0')"
    )
    status: OrderStatus = Field(default="pending_new", description="Current slice status")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "slice_num": 0,
                    "qty": 20,
                    "scheduled_time": "2025-10-26T14:00:00Z",
                    "client_order_id": "abc123def456...",
                    "strategy_id": "twap_slice_abc123_0",
                    "status": "pending_new",
                }
            ]
        }
    }


class SlicingPlan(BaseModel):
    """
    Complete TWAP slicing plan with parent order and all child slices.

    The plan includes the parent order metadata and a list of child slices
    scheduled for execution at regular intervals determined by interval_seconds.

    Attributes:
        parent_order_id: Deterministic ID for the parent order
        parent_strategy_id: Strategy identifier used for parent order ID generation
        symbol: Stock symbol
        side: Order side
        total_qty: Total quantity across all slices
        total_slices: Number of child slices
        duration_minutes: Slicing duration
        interval_seconds: Interval between slices in seconds
        slices: List of child slice details (ordered by slice_num)
    """

    parent_order_id: str = Field(..., description="Parent order deterministic ID")
    parent_strategy_id: str = Field(
        ..., description="Strategy ID for parent order (e.g., 'twap_parent_5m_60s')"
    )
    symbol: str = Field(..., description="Stock symbol")
    side: Literal["buy", "sell"] = Field(..., description="Order side")
    total_qty: int = Field(..., gt=0, description="Total quantity")
    total_slices: int = Field(..., gt=0, description="Number of slices")
    duration_minutes: int = Field(..., gt=0, description="Slicing duration in minutes")
    interval_seconds: int = Field(..., gt=0, description="Interval between slices in seconds")
    slices: list[SliceDetail] = Field(..., description="Child slice details (ordered by slice_num)")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "parent_order_id": "parent_xyz789...",
                    "parent_strategy_id": "twap_parent_5m_60s",
                    "symbol": "AAPL",
                    "side": "buy",
                    "total_qty": 100,
                    "total_slices": 5,
                    "duration_minutes": 5,
                    "interval_seconds": 60,
                    "slices": [
                        {
                            "slice_num": 0,
                            "qty": 20,
                            "scheduled_time": "2025-10-26T14:00:00Z",
                            "client_order_id": "slice_0_abc...",
                            "strategy_id": "twap_slice_parent_xyz789_0",
                            "status": "pending_new",
                        },
                        {
                            "slice_num": 1,
                            "qty": 20,
                            "scheduled_time": "2025-10-26T14:01:00Z",
                            "client_order_id": "slice_1_def...",
                            "strategy_id": "twap_slice_parent_xyz789_1",
                            "status": "pending_new",
                        },
                    ],
                }
            ]
        }
    }


# ============================================================================
# Error Schema
# ============================================================================


# ============================================================================
# Performance Dashboard Schemas (P4T6.2)
# ============================================================================


class PerformanceRequest(BaseModel):
    """Request model for daily performance history.

    Validates bounded date range (default last 30 days, max 90) and prevents
    future end dates. Defaults use UTC today.
    """

    start_date: date = Field(
        default_factory=lambda: date.today() - timedelta(days=30),
        description="Start date (UTC, inclusive). Defaults to 30 days ago.",
    )
    end_date: date = Field(
        default_factory=date.today,
        description="End date (UTC, inclusive). Defaults to today.",
    )

    @model_validator(mode="after")
    def validate_range(self) -> "PerformanceRequest":
        """Ensure start<=end, end not future, and range <= MAX_PERFORMANCE_DAYS."""

        max_days = int(os.getenv("MAX_PERFORMANCE_DAYS", "90"))

        if self.start_date > self.end_date:
            raise ValueError("start_date must be <= end_date")

        if self.end_date > date.today():
            raise ValueError("end_date cannot be in the future")

        if (self.end_date - self.start_date).days > max_days:
            raise ValueError(f"Date range cannot exceed {max_days} days")

        return self


class DailyPnL(BaseModel):
    """Daily realized P&L data point for equity/drawdown charts."""

    date: date
    realized_pl: Decimal
    cumulative_realized_pl: Decimal
    peak_equity: Decimal
    drawdown_pct: Decimal
    closing_trade_count: int


class DailyPerformanceResponse(BaseModel):
    """Response payload for /api/v1/performance/daily."""

    daily_pnl: list[DailyPnL]
    total_realized_pl: Decimal
    max_drawdown_pct: Decimal
    start_date: date
    end_date: date
    data_source: str = "realized_only"
    note: str = "Shows realized P&L from closed positions. Unrealized P&L is not included."
    data_available_from: date | None
    last_updated: datetime


class ErrorResponse(TimestampSerializerMixin, BaseModel):
    """Standard error response."""

    error: str
    detail: str | None = None
    timestamp: datetime

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "error": "Order submission failed",
                    "detail": "Insufficient buying power",
                    "timestamp": "2024-10-17T16:30:00Z",
                }
            ]
        }
    }
