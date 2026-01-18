"""Tests for BaseChannel abstract interface."""

import pytest

from libs.platform.alerts.channels.base import BaseChannel
from libs.platform.alerts.models import DeliveryResult


class _TestChannel(BaseChannel):
    channel_type = "test"

    async def send(
        self,
        recipient: str,
        subject: str,
        body: str,
        metadata=None,
        attachments=None,
    ) -> DeliveryResult:
        return DeliveryResult(success=True, metadata=metadata or {})


def test_base_channel_is_abstract():
    """BaseChannel should not be instantiable directly."""
    with pytest.raises(TypeError):
        BaseChannel()  # type: ignore[abstract]


@pytest.mark.asyncio()
async def test_base_channel_contract():
    """Concrete subclasses can implement send and return DeliveryResult."""
    channel = _TestChannel()
    result = await channel.send(
        recipient="user",
        subject="Subject",
        body="Body",
        metadata={"key": "value"},
        attachments=["file.txt"],
    )

    assert result.success is True
    assert result.metadata == {"key": "value"}
