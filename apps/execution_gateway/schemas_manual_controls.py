"""Pydantic schemas for manual control API endpoints."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from apps.execution_gateway.schemas import OrderDetail, Position


class ErrorPayload(BaseModel):
    """Standardized error response payload."""

    error: str
    message: str
    retry_after: int | None = None
    timestamp: datetime


def _validate_symbol(value: str) -> str:
    cleaned = value.strip().upper()
    if not cleaned or not cleaned.isalnum() or not (1 <= len(cleaned) <= 5):
        raise ValueError("symbol must be 1-5 alphanumeric characters")
    return cleaned


class CancelOrderRequest(BaseModel):
    reason: str = Field(..., min_length=10)
    requested_by: str
    requested_at: datetime


class CancelOrderResponse(BaseModel):
    status: Literal["cancelled"]
    order_id: str
    cancelled_at: datetime


class CancelAllOrdersRequest(BaseModel):
    symbol: str = Field(..., description="Symbol to cancel orders for")
    reason: str = Field(..., min_length=10)
    requested_by: str
    requested_at: datetime

    @field_validator("symbol")
    @classmethod
    def uppercase_symbol(cls, value: str) -> str:
        return _validate_symbol(value)


class CancelAllOrdersResponse(BaseModel):
    status: Literal["cancelled"]
    symbol: str
    cancelled_count: int
    order_ids: list[str]
    strategies_affected: list[str]


class ClosePositionRequest(BaseModel):
    reason: str = Field(..., min_length=10)
    requested_by: str
    requested_at: datetime
    qty: Decimal | None = Field(
        default=None, description="Optional partial close quantity (positive number)"
    )

    @field_validator("qty")
    @classmethod
    def qty_positive(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value <= 0:
            raise ValueError("qty must be positive when provided")
        return value

    @field_validator("qty")
    @classmethod
    def qty_integral(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value != value.to_integral_value():
            raise ValueError("qty must be a whole number")
        return value


class ClosePositionResponse(BaseModel):
    status: Literal["closing", "already_flat"]
    symbol: str
    order_id: str | None = None
    qty_to_close: Decimal
    message: str | None = None


class ManualOrderRequest(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    qty: Decimal
    order_type: Literal["market", "limit", "stop", "stop_limit"] = "market"
    time_in_force: Literal["day", "gtc", "ioc", "fok"] = "day"
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    reason: str = Field(..., min_length=10)
    requested_by: str
    requested_at: datetime

    @field_validator("symbol")
    @classmethod
    def uppercase_symbol(cls, value: str) -> str:
        return _validate_symbol(value)

    @field_validator("qty")
    @classmethod
    def qty_positive(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("qty must be positive")
        return value

    @field_validator("limit_price")
    @classmethod
    def limit_price_required_for_limit(
        cls, value: Decimal | None, info: ValidationInfo
    ) -> Decimal | None:
        order_type = info.data.get("order_type")
        if order_type in {"limit", "stop_limit"} and value is None:
            raise ValueError("limit_price required for limit/stop_limit orders")
        if value is not None and value <= 0:
            raise ValueError("limit_price must be positive")
        return value

    @field_validator("stop_price")
    @classmethod
    def stop_price_required_for_stop(
        cls, value: Decimal | None, info: ValidationInfo
    ) -> Decimal | None:
        order_type = info.data.get("order_type")
        if order_type in {"stop", "stop_limit"} and value is None:
            raise ValueError("stop_price required for stop/stop_limit orders")
        if value is not None and value <= 0:
            raise ValueError("stop_price must be positive")
        return value


class AdjustPositionRequest(BaseModel):
    target_qty: Decimal
    reason: str = Field(..., min_length=10)
    requested_by: str
    requested_at: datetime
    order_type: Literal["market", "limit"] = "market"
    limit_price: Decimal | None = None

    @field_validator("target_qty")
    @classmethod
    def target_qty_any(cls, value: Decimal) -> Decimal:
        if value != value.to_integral_value():
            raise ValueError("target_qty must be a whole number")
        return value

    @field_validator("limit_price")
    @classmethod
    def limit_price_required_for_limit(
        cls, value: Decimal | None, info: ValidationInfo
    ) -> Decimal | None:
        order_type = info.data.get("order_type")
        if order_type == "limit" and value is None:
            raise ValueError("limit_price required when order_type is limit")
        if value is not None and value <= 0:
            raise ValueError("limit_price must be positive")
        return value


class AdjustPositionResponse(BaseModel):
    status: Literal["adjusting"]
    symbol: str
    current_qty: Decimal
    target_qty: Decimal
    order_id: str | None = None
    message: str | None = None


class FlattenAllRequest(BaseModel):
    reason: str = Field(..., min_length=20)
    requested_by: str
    requested_at: datetime
    id_token: str = Field(..., description="ID token proving MFA")


class FlattenAllResponse(BaseModel):
    status: Literal["flattening"]
    positions_closed: int
    orders_created: list[str]
    strategies_affected: list[str]


class PendingOrdersResponse(BaseModel):
    orders: list[OrderDetail]
    total: int
    limit: int
    offset: int
    filtered_by_strategy: bool
    user_strategies: list[str]


class PendingOrdersParams(BaseModel):
    strategy_id: str | None = None
    symbol: str | None = None
    limit: int = 100
    offset: int = 0
    sort_by: str = "created_at"
    sort_order: Literal["asc", "desc"] = "desc"

    @field_validator("limit")
    @classmethod
    def enforce_limit(cls, value: int) -> int:
        return min(max(value, 1), 1000)

    @field_validator("offset")
    @classmethod
    def offset_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("offset must be >= 0")
        return value

    @field_validator("symbol")
    @classmethod
    def uppercase_symbol(cls, value: str | None) -> str | None:
        return value.upper() if value else value

    @field_validator("sort_by")
    @classmethod
    def sort_by_whitelist(cls, value: str) -> str:
        allowed = {"created_at", "updated_at", "symbol", "strategy_id", "status"}
        return value if value in allowed else "created_at"


class StrategyScopedPosition(BaseModel):
    position: Position
    strategy_id: str | None = None


class RecentFillEvent(BaseModel):
    """Recent execution/fill event for activity feeds."""

    client_order_id: str
    symbol: str
    side: Literal["buy", "sell"]
    qty: Decimal  # Decimal to support fractional shares from broker/reconciliation
    price: Decimal
    realized_pl: Decimal
    status: Literal["filled", "partially_filled"]
    timestamp: datetime


class RecentFillsResponse(BaseModel):
    events: list[RecentFillEvent]
    total: int
    limit: int
    filtered_by_strategy: bool
    user_strategies: list[str]


__all__ = [
    "CancelOrderRequest",
    "CancelOrderResponse",
    "CancelAllOrdersRequest",
    "CancelAllOrdersResponse",
    "ClosePositionRequest",
    "ClosePositionResponse",
    "AdjustPositionRequest",
    "AdjustPositionResponse",
    "FlattenAllRequest",
    "FlattenAllResponse",
    "PendingOrdersResponse",
    "PendingOrdersParams",
    "ErrorPayload",
    "StrategyScopedPosition",
    "RecentFillEvent",
    "RecentFillsResponse",
]
