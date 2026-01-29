"""
Pydantic schemas for Execution Gateway API.

Defines request and response models for all endpoints, ensuring type safety
and validation at the API boundary.
"""

import math
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, TypeAlias

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_serializer, field_validator, model_validator

from libs.core.common import TimestampSerializerMixin

# ============================================================================
# Type Aliases
# ============================================================================

# Order status type - used consistently across OrderResponse, OrderDetail, and SliceDetail
# DRY principle: Define once, use everywhere
OrderStatus: TypeAlias = Literal[
    "pending_new",
    "new",
    "accepted",
    "partially_filled",
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
    "scheduled",
    "blocked_kill_switch",
    "blocked_circuit_breaker",
]

# TWAP configuration constants (T6.0.1)
TWAP_MIN_DURATION_MINUTES = 5  # Prevent overly short TWAPs that behave like instant orders.
TWAP_MAX_DURATION_MINUTES = 480  # Cap to one trading day (8 hours) for v1 scheduling.
TWAP_MIN_INTERVAL_SECONDS = 30  # Avoid excessive slice frequency / API rate pressure.
TWAP_MAX_INTERVAL_SECONDS = 300  # Avoid overly sparse slices that reduce TWAP benefit.
TWAP_MIN_SLICES = 2  # TWAP requires at least two slices to be meaningful.
TWAP_MIN_SLICE_QTY = 10  # Avoid tiny odd-lot slices that increase fees/slippage.
TWAP_MIN_SLICE_NOTIONAL = Decimal("500")  # Alpaca minimum notional per order/slice.

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
    execution_style: Literal["instant", "twap"] = Field(
        default="instant", description="Execution style"
    )
    twap_duration_minutes: int | None = Field(
        default=None,
        description="TWAP duration in minutes (required for execution_style=twap)",
    )
    twap_interval_seconds: int | None = Field(
        default=None,
        description="TWAP interval in seconds (required for execution_style=twap)",
    )
    start_time: datetime | None = Field(
        default=None,
        description="Optional scheduled start time (UTC) for TWAP execution",
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
    def validate_order_type_prices(self) -> "OrderRequest":
        """Validate price requirements for order types."""
        if self.order_type in ("limit", "stop_limit") and self.limit_price is None:
            raise ValueError(f"limit_price required for order_type={self.order_type}")

        if self.order_type in ("stop", "stop_limit") and self.stop_price is None:
            raise ValueError(f"stop_price required for order_type={self.order_type}")

        if self.order_type == "stop_limit":
            if self.limit_price is not None and self.stop_price is not None:
                if self.side == "buy" and self.limit_price < self.stop_price:
                    raise ValueError(
                        "Buy stop-limit requires limit_price >= stop_price "
                        f"(got limit={self.limit_price}, stop={self.stop_price})"
                    )
                if self.side == "sell" and self.limit_price > self.stop_price:
                    raise ValueError(
                        "Sell stop-limit requires limit_price <= stop_price "
                        f"(got limit={self.limit_price}, stop={self.stop_price})"
                    )

        return self

    @model_validator(mode="after")
    def validate_twap_constraints(self) -> "OrderRequest":
        """Validate TWAP-specific constraints (backend defense in depth)."""
        if self.execution_style == "twap":
            if self.order_type not in ("market", "limit"):
                raise ValueError(
                    f"TWAP execution not supported for order_type={self.order_type}. "
                    "Use 'market' or 'limit' only."
                )

            if self.time_in_force != "day":
                raise ValueError(
                    f"TWAP execution not supported for time_in_force={self.time_in_force}. "
                    "Use 'day' only. Multi-day TWAP (gtc) and IOC/FOK are not supported."
                )

            if self.twap_duration_minutes is None:
                raise ValueError("twap_duration_minutes required for TWAP orders")
            if self.twap_interval_seconds is None:
                raise ValueError("twap_interval_seconds required for TWAP orders")

            if not (
                TWAP_MIN_DURATION_MINUTES
                <= self.twap_duration_minutes
                <= TWAP_MAX_DURATION_MINUTES
            ):
                raise ValueError(
                    f"twap_duration_minutes must be between {TWAP_MIN_DURATION_MINUTES} and "
                    f"{TWAP_MAX_DURATION_MINUTES} (got {self.twap_duration_minutes})"
                )

            if not (
                TWAP_MIN_INTERVAL_SECONDS
                <= self.twap_interval_seconds
                <= TWAP_MAX_INTERVAL_SECONDS
            ):
                raise ValueError(
                    f"twap_interval_seconds must be between {TWAP_MIN_INTERVAL_SECONDS} and "
                    f"{TWAP_MAX_INTERVAL_SECONDS} (got {self.twap_interval_seconds})"
                )

            duration_seconds = self.twap_duration_minutes * 60
            num_slices = max(1, math.ceil(duration_seconds / self.twap_interval_seconds))
            if num_slices < TWAP_MIN_SLICES:
                raise ValueError(
                    f"TWAP requires at least {TWAP_MIN_SLICES} slices "
                    f"(duration/interval produces {num_slices} slices)"
                )

            base_slice_qty = self.qty // num_slices
            if base_slice_qty < TWAP_MIN_SLICE_QTY:
                raise ValueError(
                    f"TWAP minimum slice size is {TWAP_MIN_SLICE_QTY} shares "
                    f"(got {base_slice_qty} shares per slice)"
                )

            if self.start_time is not None:
                now = datetime.now(UTC)
                start_time = (
                    self.start_time.replace(tzinfo=UTC)
                    if self.start_time.tzinfo is None
                    else self.start_time.astimezone(UTC)
                )
                if start_time < now - timedelta(minutes=1):
                    raise ValueError("start_time cannot be in the past")
                if start_time > now + timedelta(days=5):
                    raise ValueError("start_time cannot be more than 5 days in the future")

        return self

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
        stop_price: Stop price (if applicable)
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
    stop_price: Decimal | None = None
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
                    "stop_price": None,
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
                    "stop_price": None,
                    "created_at": "2024-10-17T16:31:00Z",
                    "message": "Order logged (DRY_RUN mode)",
                },
            ]
        }
    }


class OrderModifyRequest(BaseModel):
    """Request to modify a working order via atomic replace."""

    idempotency_key: str
    qty: int | None = None
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: Literal["day", "gtc"] | None = None
    reason: str | None = None

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, v: str) -> str:
        try:
            uuid.UUID(v)
        except ValueError:
            raise ValueError("idempotency_key must be a valid UUID") from None
        return v

    @field_validator("qty")
    @classmethod
    def validate_qty(cls, v: int | None) -> int | None:
        if v is None:
            return v
        if v <= 0:
            raise ValueError("qty must be positive")
        return v

    @field_validator("limit_price", "stop_price")
    @classmethod
    def validate_prices(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v <= 0:
            raise ValueError("price must be positive")
        return v

    @field_validator("time_in_force")
    @classmethod
    def validate_tif(cls, v: str | None) -> str | None:
        if v in ("ioc", "fok"):
            raise ValueError(
                "time_in_force ioc/fok is not allowed for order modifications. "
                "Use day or gtc instead."
            )
        return v

    @model_validator(mode="after")
    def validate_at_least_one_field(self) -> "OrderModifyRequest":
        if (
            self.qty is None
            and self.limit_price is None
            and self.stop_price is None
            and self.time_in_force is None
        ):
            raise ValueError("At least one field must be provided to modify an order")
        return self


class OrderModifyResponse(BaseModel):
    """Response after successful order modification."""

    original_client_order_id: str
    new_client_order_id: str
    modification_id: str
    modified_at: datetime
    status: Literal["pending", "completed", "failed", "submitted_unconfirmed"]
    changes: dict[str, tuple[Any, Any]]


class OrderModificationRecord(BaseModel):
    """Record of a single order modification."""

    modification_id: str
    original_client_order_id: str
    new_client_order_id: str
    modified_at: datetime
    modified_by: str
    changes: dict[str, tuple[Any, Any]]
    reason: str | None = None


class OrderSubmitResponse(BaseModel):
    """Response after submitting an order (supports TWAP warnings)."""

    client_order_id: str
    status: str
    warnings: list[str] = Field(default_factory=list)


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
    execution_style: Literal["instant", "twap"] | None = None
    status: OrderStatus
    broker_order_id: str | None = None
    replaced_order_id: str | None = None
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


class AccountInfoResponse(BaseModel):
    """Account summary information from broker."""

    account_number: str | None = None
    status: str | None = None
    currency: str | None = None
    buying_power: Decimal | None = None
    cash: Decimal | None = None
    portfolio_value: Decimal | None = None
    pattern_day_trader: bool | None = None
    trading_blocked: bool | None = None
    transfers_blocked: bool | None = None


class MarketPricePoint(BaseModel):
    """Market price snapshot for a symbol."""

    symbol: str
    mid: Decimal | None = None
    timestamp: datetime | None = None


class CircuitBreakerStatusResponse(BaseModel):
    """Circuit breaker status payload."""

    state: str
    tripped_at: datetime | None = None
    trip_reason: str | None = None
    trip_details: dict[str, Any] | None = None


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
    liquidity_check_enabled: bool = Field(
        ..., description="Liquidity-aware slicing enabled (ADV-based limits)"
    )
    max_slice_pct_of_adv: float = Field(
        ..., description="Max slice size as pct of ADV when liquidity checks enabled"
    )
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
                    "liquidity_check_enabled": True,
                    "max_slice_pct_of_adv": 0.01,
                    "timestamp": "2025-10-22T10:30:00Z",
                }
            ]
        }
    }


# ============================================================================
# Fat-Finger Threshold Schemas
# ============================================================================


class FatFingerThresholds(BaseModel):
    """Fat-finger thresholds for order validation."""

    max_notional: Decimal | None = Field(
        default=None, gt=0, description="Max order notional (in USD)"
    )
    max_qty: int | None = Field(default=None, gt=0, description="Max order quantity (shares)")
    max_adv_pct: Decimal | None = Field(
        default=None,
        gt=0,
        le=1,
        description="Max order size as fraction of ADV (0-1)",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{"max_notional": "100000", "max_qty": 10000, "max_adv_pct": "0.05"}]
        }
    }


class FatFingerThresholdsUpdateRequest(BaseModel):
    """Update fat-finger thresholds (defaults + per-symbol overrides)."""

    default_thresholds: FatFingerThresholds | None = Field(
        default=None, description="Default thresholds applied when no override exists"
    )
    symbol_overrides: dict[str, FatFingerThresholds | None] | None = Field(
        default=None,
        description="Per-symbol overrides; set value to null to remove override",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "default_thresholds": {
                        "max_notional": "150000",
                        "max_qty": 12000,
                        "max_adv_pct": "0.06",
                    },
                    "symbol_overrides": {
                        "AAPL": {"max_qty": 5000},
                        "TSLA": {"max_notional": "200000", "max_adv_pct": "0.03"},
                    },
                }
            ]
        }
    }


class FatFingerThresholdsResponse(BaseModel):
    """Current fat-finger threshold configuration."""

    default_thresholds: FatFingerThresholds
    symbol_overrides: dict[str, FatFingerThresholds]
    updated_at: datetime

    @field_serializer("updated_at")
    def serialize_updated_at(self, value: datetime) -> str:
        """Serialize updated_at with Z suffix for UTC consistency."""
        if value.tzinfo is None or value.utcoffset() == timedelta(0):
            return value.strftime("%Y-%m-%dT%H:%M:%SZ")
        return value.isoformat()


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
# Reconciliation Schemas
# ============================================================================


class ReconciliationForceCompleteRequest(BaseModel):
    """Request to force-complete startup reconciliation."""

    reason: str | None = Field(
        None, description="Operator-provided reason for forcing reconciliation completion"
    )


class ReconciliationFillsBackfillRequest(BaseModel):
    """Request to trigger Alpaca fills backfill."""

    lookback_hours: int | None = Field(
        None, description="Override lookback window in hours (optional)"
    )
    recalc_all_trades: bool = Field(
        False, description="Recalculate realized P&L for all trades in scope"
    )


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


class TWAPPreviewRequest(BaseModel):
    """Request for TWAP slicing preview."""

    symbol: str
    side: Literal["buy", "sell"]
    qty: int
    order_type: Literal["market", "limit"]
    limit_price: Decimal | None = None
    time_in_force: Literal["day"] = "day"
    duration_minutes: int = Field(ge=TWAP_MIN_DURATION_MINUTES, le=TWAP_MAX_DURATION_MINUTES)
    interval_seconds: int = Field(ge=TWAP_MIN_INTERVAL_SECONDS, le=TWAP_MAX_INTERVAL_SECONDS)
    start_time: datetime | None = None
    strategy_id: str
    timezone: str = "UTC"

    @field_validator("symbol")
    @classmethod
    def preview_symbol_uppercase(cls, v: str) -> str:
        return v.upper()

    @field_validator("limit_price")
    @classmethod
    def preview_price_positive(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v <= 0:
            raise ValueError("Price must be positive")
        return v

    @model_validator(mode="after")
    def validate_preview(self) -> "TWAPPreviewRequest":
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("limit_price required for limit orders")

        if self.start_time is not None:
            now = datetime.now(UTC)
            start_time = (
                self.start_time.replace(tzinfo=UTC)
                if self.start_time.tzinfo is None
                else self.start_time.astimezone(UTC)
            )
            if start_time < now - timedelta(minutes=1):
                raise ValueError("start_time cannot be in the past")
            if start_time > now + timedelta(days=5):
                raise ValueError("start_time cannot be more than 5 days in the future")

        return self


class TWAPPreviewResponse(BaseModel):
    """Response from TWAP preview endpoint."""

    slice_count: int
    base_slice_qty: int
    remainder_distribution: list[int]
    scheduled_times: list[datetime]
    display_times: list[str]
    first_slice_at: datetime
    last_slice_at: datetime
    estimated_duration_minutes: int
    market_hours_warning: str | None
    notional_warning: str | None
    slice_notional: Decimal | None
    validation_errors: list[str]


class TWAPPreviewError(BaseModel):
    error: Literal["validation_error"] = "validation_error"
    errors: list[str]


class TWAPValidationException(HTTPException):
    def __init__(self, errors: list[str]):
        super().__init__(
            status_code=422,
            detail=TWAPPreviewError(errors=errors).model_dump(),
        )


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


class StrategyStatusResponse(TimestampSerializerMixin, BaseModel):
    """Strategy status information for monitoring.

    Provides consolidated view of strategy state including:
    - Basic strategy info (id, name, status)
    - Model version and status
    - Activity indicators (last signal, errors)
    - Position and order counts
    - Today's P&L
    """

    strategy_id: str
    name: str
    status: Literal["active", "paused", "error", "inactive"]
    model_version: str | None = None
    model_status: Literal["active", "inactive", "testing", "failed"] | None = None
    last_signal_at: datetime | None = None
    last_error: str | None = None
    positions_count: int = 0
    open_orders_count: int = 0
    today_pnl: Decimal | None = None
    timestamp: datetime

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "strategy_id": "alpha_baseline",
                    "name": "Alpha Baseline Strategy",
                    "status": "active",
                    "model_version": "v1.2.0",
                    "model_status": "active",
                    "last_signal_at": "2024-10-17T16:30:00Z",
                    "last_error": None,
                    "positions_count": 15,
                    "open_orders_count": 3,
                    "today_pnl": "1234.56",
                    "timestamp": "2024-10-17T16:35:00Z",
                }
            ]
        }
    }


class StrategiesListResponse(TimestampSerializerMixin, BaseModel):
    """Response for listing all strategies with their status."""

    strategies: list[StrategyStatusResponse]
    total_count: int
    timestamp: datetime


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
