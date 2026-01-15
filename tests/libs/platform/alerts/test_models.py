"""Tests for alert models and enums."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from libs.platform.alerts.models import (
    AlertDelivery,
    AlertEvent,
    AlertRule,
    ChannelConfig,
    ChannelType,
    DeliveryResult,
    DeliveryStatus,
)


class TestChannelType:
    """Test ChannelType enum."""

    def test_enum_values(self):
        """Test all channel types have correct values."""
        assert ChannelType.EMAIL.value == "email"
        assert ChannelType.SLACK.value == "slack"
        assert ChannelType.SMS.value == "sms"

    def test_enum_from_value(self):
        """Test creating enum from string value."""
        assert ChannelType("email") == ChannelType.EMAIL
        assert ChannelType("slack") == ChannelType.SLACK
        assert ChannelType("sms") == ChannelType.SMS

    def test_invalid_value_raises(self):
        """Test invalid channel type raises ValueError."""
        with pytest.raises(ValueError, match="invalid"):
            ChannelType("invalid")


class TestDeliveryStatus:
    """Test DeliveryStatus enum."""

    def test_enum_values(self):
        """Test all statuses have correct values."""
        assert DeliveryStatus.PENDING.value == "pending"
        assert DeliveryStatus.IN_PROGRESS.value == "in_progress"
        assert DeliveryStatus.DELIVERED.value == "delivered"
        assert DeliveryStatus.FAILED.value == "failed"
        assert DeliveryStatus.POISON.value == "poison"


class TestChannelConfig:
    """Test ChannelConfig model."""

    def test_valid_config(self):
        """Test creating valid channel config."""
        config = ChannelConfig(
            type=ChannelType.EMAIL,
            recipient="test@example.com",
            enabled=True,
        )
        assert config.type == ChannelType.EMAIL
        assert config.recipient == "test@example.com"
        assert config.enabled is True

    def test_default_enabled(self):
        """Test enabled defaults to True."""
        config = ChannelConfig(
            type=ChannelType.SLACK,
            recipient="#alerts",
        )
        assert config.enabled is True

    def test_disabled_config(self):
        """Test disabled channel config."""
        config = ChannelConfig(
            type=ChannelType.SMS,
            recipient="+15551234567",
            enabled=False,
        )
        assert config.enabled is False


class TestDeliveryResult:
    """Test DeliveryResult model."""

    def test_success_result(self):
        """Test successful delivery result."""
        result = DeliveryResult(
            success=True,
            message_id="msg-123",
        )
        assert result.success is True
        assert result.message_id == "msg-123"
        assert result.error is None
        assert result.retryable is True
        assert result.metadata == {}

    def test_failure_result(self):
        """Test failed delivery result."""
        result = DeliveryResult(
            success=False,
            error="Connection timeout",
            retryable=True,
            metadata={"retry_after": "60"},
        )
        assert result.success is False
        assert result.error == "Connection timeout"
        assert result.retryable is True
        assert result.metadata == {"retry_after": "60"}

    def test_permanent_failure(self):
        """Test non-retryable failure."""
        result = DeliveryResult(
            success=False,
            error="Invalid recipient",
            retryable=False,
        )
        assert result.retryable is False


class TestAlertDelivery:
    """Test AlertDelivery model."""

    def test_valid_delivery(self):
        """Test creating valid alert delivery."""
        now = datetime.now(UTC)
        delivery_id = uuid4()
        alert_id = uuid4()

        delivery = AlertDelivery(
            id=delivery_id,
            alert_id=alert_id,
            channel=ChannelType.EMAIL,
            recipient="test@example.com",
            dedup_key="abc123",
            status=DeliveryStatus.PENDING,
            attempts=0,
            created_at=now,
        )

        assert delivery.id == delivery_id
        assert delivery.alert_id == alert_id
        assert delivery.channel == ChannelType.EMAIL
        assert delivery.status == DeliveryStatus.PENDING
        assert delivery.attempts == 0

    def test_attempts_constraint(self):
        """Test attempts field constraints."""
        now = datetime.now(UTC)

        # Valid: 0 attempts
        delivery = AlertDelivery(
            id=uuid4(),
            alert_id=uuid4(),
            channel=ChannelType.EMAIL,
            recipient="test@example.com",
            dedup_key="abc123",
            status=DeliveryStatus.PENDING,
            attempts=0,
            created_at=now,
        )
        assert delivery.attempts == 0

        # Valid: 3 attempts
        delivery = AlertDelivery(
            id=uuid4(),
            alert_id=uuid4(),
            channel=ChannelType.EMAIL,
            recipient="test@example.com",
            dedup_key="abc123",
            status=DeliveryStatus.PENDING,
            attempts=3,
            created_at=now,
        )
        assert delivery.attempts == 3

        # Invalid: negative attempts
        with pytest.raises(ValidationError):
            AlertDelivery(
                id=uuid4(),
                alert_id=uuid4(),
                channel=ChannelType.EMAIL,
                recipient="test@example.com",
                dedup_key="abc123",
                status=DeliveryStatus.PENDING,
                attempts=-1,
                created_at=now,
            )

        # Invalid: too many attempts
        with pytest.raises(ValidationError):
            AlertDelivery(
                id=uuid4(),
                alert_id=uuid4(),
                channel=ChannelType.EMAIL,
                recipient="test@example.com",
                dedup_key="abc123",
                status=DeliveryStatus.PENDING,
                attempts=4,
                created_at=now,
            )


class TestAlertEvent:
    """Test AlertEvent model."""

    def test_valid_event(self):
        """Test creating valid alert event."""
        now = datetime.now(UTC)
        event_id = uuid4()
        rule_id = uuid4()

        event = AlertEvent(
            id=event_id,
            rule_id=rule_id,
            triggered_at=now,
            trigger_value=Decimal("100.50"),
            routed_channels=["email", "slack"],
            created_at=now,
        )

        assert event.id == event_id
        assert event.rule_id == rule_id
        assert event.trigger_value == Decimal("100.50")
        assert event.routed_channels == ["email", "slack"]

    def test_acknowledged_event(self):
        """Test acknowledged alert event."""
        now = datetime.now(UTC)

        event = AlertEvent(
            id=uuid4(),
            rule_id=uuid4(),
            triggered_at=now,
            acknowledged_at=now,
            acknowledged_by="user@example.com",
            acknowledged_note="False positive",
            created_at=now,
        )

        assert event.acknowledged_at == now
        assert event.acknowledged_by == "user@example.com"
        assert event.acknowledged_note == "False positive"


class TestAlertRule:
    """Test AlertRule model."""

    def test_valid_rule(self):
        """Test creating valid alert rule."""
        now = datetime.now(UTC)
        rule_id = uuid4()

        rule = AlertRule(
            id=rule_id,
            name="Test Alert",
            condition_type="threshold",
            threshold_value=Decimal("50.00"),
            comparison="gt",
            channels=[
                ChannelConfig(type=ChannelType.EMAIL, recipient="test@example.com"),
                ChannelConfig(type=ChannelType.SLACK, recipient="#alerts"),
            ],
            enabled=True,
            created_by="admin@example.com",
            created_at=now,
            updated_at=now,
        )

        assert rule.id == rule_id
        assert rule.name == "Test Alert"
        assert rule.enabled is True
        assert len(rule.channels) == 2
