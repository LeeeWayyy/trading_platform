"""Pydantic schemas for manual control API endpoints."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from apps.execution_gateway.schemas import (
    OrderDetail,
    OrderResponse,
    Position,
    TWAP_MAX_DURATION_MINUTES,
    TWAP_MAX_INTERVAL_SECONDS,
    TWAP_MIN_DURATION_MINUTES,
    TWAP_MIN_INTERVAL_SECONDS,
    TWAP_MIN_SLICE_QTY,
    TWAP_MIN_SLICES,
)


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
    execution_style: Literal["instant", "twap"] = "instant"
    twap_duration_minutes: int | None = None
    twap_interval_seconds: int | None = None
    start_time: datetime | None = None
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

    @model_validator(mode="after")
    def validate_twap_fields(self) -> "ManualOrderRequest":
        if self.execution_style != "twap":
            return self

        if self.order_type not in ("market", "limit"):
            raise ValueError(
                "TWAP execution only supports market or limit order types"
            )

        if self.time_in_force != "day":
            raise ValueError("TWAP execution only supports time_in_force=day")

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
                f"twap_duration_minutes must be between {TWAP_MIN_DURATION_MINUTES} "
                f"and {TWAP_MAX_DURATION_MINUTES}"
            )

        if not (
            TWAP_MIN_INTERVAL_SECONDS
            <= self.twap_interval_seconds
            <= TWAP_MAX_INTERVAL_SECONDS
        ):
            raise ValueError(
                f"twap_interval_seconds must be between {TWAP_MIN_INTERVAL_SECONDS} "
                f"and {TWAP_MAX_INTERVAL_SECONDS}"
            )

        duration_seconds = self.twap_duration_minutes * 60
        num_slices = max(1, math.ceil(duration_seconds / self.twap_interval_seconds))
        if num_slices < TWAP_MIN_SLICES:
            raise ValueError(
                f"TWAP requires at least {TWAP_MIN_SLICES} slices "
                f"(duration/interval produces {num_slices} slices)"
            )

        if self.qty == self.qty.to_integral_value():
            base_slice_qty = int(self.qty) // num_slices
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


class ManualOrderResponse(OrderResponse):
    slice_count: int | None = None
    first_slice_at: datetime | None = None
    last_slice_at: datetime | None = None


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
