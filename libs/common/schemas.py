"""Shared Pydantic schema utilities.

Provides common mixins and base classes for consistent schema behavior
across all services in the trading platform.
"""

from datetime import datetime

from pydantic import field_serializer


class TimestampSerializerMixin:
    """
    Mixin providing consistent UTC timestamp serialization.

    Serializes datetime fields named 'timestamp' with 'Z' suffix instead of
    '+00:00' for cleaner ISO 8601 format consistency across API responses.

    Usage:
        class MyResponse(TimestampSerializerMixin, BaseModel):
            timestamp: datetime
            # ... other fields

    The serializer outputs: "2024-10-17T16:30:00Z" instead of
    "2024-10-17T16:30:00+00:00" for UTC timestamps.

    Note: Mixin must be listed BEFORE BaseModel in inheritance order.
    """

    @field_serializer("timestamp")
    def serialize_timestamp(self, value: datetime) -> str:
        """Serialize timestamp with Z suffix for UTC consistency."""
        return value.isoformat().replace("+00:00", "Z")
