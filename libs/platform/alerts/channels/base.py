"""Abstract base class for alert delivery channels."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from libs.platform.alerts.models import DeliveryResult


class BaseChannel(ABC):
    """Abstract base for delivery channels.

    Implementations are responsible for network I/O only; rate limiting and
    retries are handled by the delivery service layer.
    """

    channel_type: str  # e.g., "email", "slack", "sms"

    @abstractmethod
    async def send(
        self,
        recipient: str,
        subject: str,
        body: str,
        metadata: dict[str, Any] | None = None,
        attachments: list[str] | None = None,
    ) -> DeliveryResult:
        """Send a notification via the channel."""
        raise NotImplementedError


__all__ = ["BaseChannel"]
