"""
Pydantic schemas for Execution Gateway API.

Defines request and response models for all endpoints, ensuring type safety
and validation at the API boundary.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional, Literal, Dict, Any, List
from pydantic import BaseModel, Field, field_validator


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
        default="market",
        description="Order type"
    )
    limit_price: Optional[Decimal] = Field(
        default=None,
        description="Limit price (required for limit orders)"
    )
    stop_price: Optional[Decimal] = Field(
        default=None,
        description="Stop price (required for stop orders)"
    )
    time_in_force: Literal["day", "gtc", "ioc", "fok"] = Field(
        default="day",
        description="Time in force"
    )

    @field_validator("symbol")
    @classmethod
    def symbol_uppercase(cls, v: str) -> str:
        """Ensure symbol is uppercase."""
        return v.upper()

    @field_validator("limit_price", "stop_price")
    @classmethod
    def price_positive(cls, v: Optional[Decimal]) -> Optional[Decimal]:
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
                    "time_in_force": "day"
                },
                {
                    "symbol": "MSFT",
                    "side": "sell",
                    "qty": 5,
                    "order_type": "limit",
                    "limit_price": "300.50",
                    "time_in_force": "day"
                }
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
    status: str
    broker_order_id: Optional[str] = None
    symbol: str
    side: str
    qty: int
    order_type: str
    limit_price: Optional[Decimal] = None
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
                    "message": "Order submitted to broker"
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
                    "message": "Order logged (DRY_RUN mode)"
                }
            ]
        }
    }


class OrderDetail(BaseModel):
    """
    Detailed order information including fill details and timestamps.

    Used for GET /api/v1/orders/{client_order_id} endpoint.
    """
    client_order_id: str
    strategy_id: str
    symbol: str
    side: str
    qty: int
    order_type: str
    limit_price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    time_in_force: str
    status: str
    broker_order_id: Optional[str] = None
    error_message: Optional[str] = None
    retry_count: int
    created_at: datetime
    updated_at: datetime
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    filled_qty: Decimal
    filled_avg_price: Optional[Decimal] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


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
    current_price: Optional[Decimal] = None
    unrealized_pl: Optional[Decimal] = None
    realized_pl: Decimal
    updated_at: datetime
    last_trade_at: Optional[datetime] = None

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
                    "last_trade_at": "2024-10-17T16:30:00Z"
                }
            ]
        }
    }


class PositionsResponse(BaseModel):
    """Response containing list of current positions."""
    positions: List[Position]
    total_positions: int
    total_unrealized_pl: Optional[Decimal] = None
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
                            "updated_at": "2024-10-17T16:30:00Z"
                        }
                    ],
                    "total_positions": 1,
                    "total_unrealized_pl": "25.00",
                    "total_realized_pl": "0.00"
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
        "calculated"
    ]
    order: Dict[str, Any]  # Full order object from Alpaca
    timestamp: datetime
    execution_id: Optional[str] = None
    position_qty: Optional[str] = None


class WebhookEvent(BaseModel):
    """
    Webhook event envelope from Alpaca.

    Contains the event type and data payload.
    """
    event_type: str = Field(..., alias="event")
    data: OrderEventData

    model_config = {
        "populate_by_name": True
    }


# ============================================================================
# Health Check Schema
# ============================================================================

class HealthResponse(BaseModel):
    """Health check response."""
    status: Literal["healthy", "degraded", "unhealthy"]
    service: str = "execution_gateway"
    version: str
    dry_run: bool
    database_connected: bool
    alpaca_connected: bool
    timestamp: datetime
    details: Optional[Dict[str, Any]] = None

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
                    "details": {}
                }
            ]
        }
    }


# ============================================================================
# Error Schema
# ============================================================================

class ErrorResponse(BaseModel):
    """Standard error response."""
    error: str
    detail: Optional[str] = None
    timestamp: datetime

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "error": "Order submission failed",
                    "detail": "Insufficient buying power",
                    "timestamp": "2024-10-17T16:30:00Z"
                }
            ]
        }
    }
