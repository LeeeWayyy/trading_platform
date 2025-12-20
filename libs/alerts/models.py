"""Pydantic models for alert rules and deliveries."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class ChannelType(str, Enum):
    """Supported outbound delivery channels."""

    EMAIL = "email"
    SLACK = "slack"
    SMS = "sms"


class DeliveryStatus(str, Enum):
    """Status lifecycle for alert deliveries."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    POISON = "poison"


class ChannelConfig(BaseModel):
    """Channel configuration for an alert rule."""

    type: ChannelType
    recipient: str
    enabled: bool = True


class AlertRule(BaseModel):
    """Definition of an alert rule with delivery channels."""

    id: UUID
    name: str
    condition_type: str
    threshold_value: Decimal
    comparison: str
    channels: list[ChannelConfig]
    enabled: bool
    created_by: str
    created_at: datetime
    updated_at: datetime


class AlertEvent(BaseModel):
    """Triggered alert event associated with a rule."""

    id: UUID
    rule_id: UUID
    triggered_at: datetime
    trigger_value: Decimal | None = None
    acknowledged_at: datetime | None = None
    acknowledged_by: str | None = None
    acknowledged_note: str | None = None
    routed_channels: list[str] = Field(default_factory=list)
    created_at: datetime


class AlertDelivery(BaseModel):
    """Delivery attempt associated with a triggered alert event."""

    id: UUID
    alert_id: UUID
    channel: ChannelType
    recipient: str
    dedup_key: str
    status: DeliveryStatus
    attempts: int = Field(ge=0, le=3, default=0)
    last_attempt_at: datetime | None = None
    delivered_at: datetime | None = None
    poison_at: datetime | None = None
    error_message: str | None = None
    created_at: datetime


__all__ = [
    "ChannelType",
    "DeliveryStatus",
    "ChannelConfig",
    "AlertRule",
    "AlertEvent",
    "AlertDelivery",
]
