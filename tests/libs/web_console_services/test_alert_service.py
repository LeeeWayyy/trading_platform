"""
Unit tests for libs.web_console_services.alert_service.

Tests cover:
- AlertConfigService initialization and channel handler lazy loading
- CRUD operations for alert rules (get, create, update, delete)
- RBAC enforcement for all operations
- Alert acknowledgment with validation
- Test notification sending with channel handlers
- Channel management (add, update, remove)
- Alert event retrieval
- Edge cases (None values, empty lists, invalid inputs)
- Error handling paths

Target: 85%+ branch coverage (baseline from 0%)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import ANY, AsyncMock, Mock, patch
from uuid import UUID, uuid4

import pytest

from libs.core.common.exceptions import ConfigurationError
from libs.platform.alerts.models import (
    AlertEvent,
    AlertRule,
    ChannelConfig,
    ChannelType,
    DeliveryResult,
)
from libs.web_console_services.alert_service import (
    DEFAULT_ALERT_EVENT_LIMIT,
    MIN_ACK_NOTE_LENGTH,
    AlertConfigService,
    AlertRuleCreate,
    AlertRuleUpdate,
    TestResult,
)


# Test fixtures


@pytest.fixture
def mock_db_pool() -> Mock:
    """Create mock database pool."""
    pool = Mock()
    return pool


@pytest.fixture
def mock_audit_logger() -> Mock:
    """Create mock audit logger."""
    logger = Mock()
    logger.log_action = AsyncMock()
    return logger


@pytest.fixture
def alert_service(mock_db_pool: Mock, mock_audit_logger: Mock) -> AlertConfigService:
    """Create AlertConfigService instance."""
    return AlertConfigService(db_pool=mock_db_pool, audit_logger=mock_audit_logger)


@pytest.fixture
def admin_user() -> dict[str, Any]:
    """Create admin user with all permissions."""
    return {"user_id": "admin-123", "role": "admin"}


@pytest.fixture
def operator_user() -> dict[str, Any]:
    """Create operator user with standard permissions."""
    return {"user_id": "operator-456", "role": "operator"}


@pytest.fixture
def viewer_user() -> dict[str, Any]:
    """Create viewer user with read-only permissions."""
    return {"user_id": "viewer-789", "role": "viewer"}


@pytest.fixture
def sample_channel_config() -> ChannelConfig:
    """Create sample channel configuration."""
    return ChannelConfig(
        type=ChannelType.EMAIL,
        recipient="test@example.com",
        enabled=True,
    )


@pytest.fixture
def sample_alert_rule_create(sample_channel_config: ChannelConfig) -> AlertRuleCreate:
    """Create sample AlertRuleCreate."""
    return AlertRuleCreate(
        name="Test Alert",
        condition_type="drawdown",
        threshold_value=Decimal("0.05"),
        comparison="gt",
        channels=[sample_channel_config],
        enabled=True,
    )


# Test classes


class TestAlertServiceInitialization:
    """Tests for AlertConfigService initialization."""

    def test_init_with_defaults(
        self, mock_db_pool: Mock, mock_audit_logger: Mock
    ) -> None:
        """Test AlertConfigService initializes with default config."""
        service = AlertConfigService(db_pool=mock_db_pool, audit_logger=mock_audit_logger)

        assert service.db_pool is mock_db_pool
        assert service.audit_logger is mock_audit_logger
        assert service._channel_handlers is None

    def test_channel_handlers_lazy_initialization(self, alert_service: AlertConfigService) -> None:
        """Test channel handlers are lazily initialized."""
        assert alert_service._channel_handlers is None

        with patch("libs.web_console_services.alert_service.EmailChannel") as mock_email, \
             patch("libs.web_console_services.alert_service.SlackChannel") as mock_slack, \
             patch("libs.web_console_services.alert_service.SMSChannel") as mock_sms:

            handlers = alert_service._get_channel_handlers()

            assert handlers is not None
            assert ChannelType.EMAIL in handlers
            assert ChannelType.SLACK in handlers
            assert ChannelType.SMS in handlers
            mock_email.assert_called_once()
            mock_slack.assert_called_once()
            mock_sms.assert_called_once()

    def test_channel_handlers_cached_after_first_call(
        self, alert_service: AlertConfigService
    ) -> None:
        """Test channel handlers are cached after first initialization."""
        with patch("libs.web_console_services.alert_service.EmailChannel") as mock_email, \
             patch("libs.web_console_services.alert_service.SlackChannel") as mock_slack, \
             patch("libs.web_console_services.alert_service.SMSChannel") as mock_sms:

            handlers1 = alert_service._get_channel_handlers()
            handlers2 = alert_service._get_channel_handlers()

            assert handlers1 is handlers2
            # Should only be called once due to caching
            assert mock_email.call_count == 1
            assert mock_slack.call_count == 1
            assert mock_sms.call_count == 1

    def test_sms_channel_disabled_on_configuration_error(
        self, alert_service: AlertConfigService
    ) -> None:
        """Test SMS channel is skipped if Twilio credentials not configured."""
        with patch("libs.web_console_services.alert_service.EmailChannel"), \
             patch("libs.web_console_services.alert_service.SlackChannel"), \
             patch("libs.web_console_services.alert_service.SMSChannel", side_effect=ConfigurationError("Missing Twilio credentials")), \
             patch("libs.web_console_services.alert_service.logger") as mock_logger:

            handlers = alert_service._get_channel_handlers()

            assert ChannelType.EMAIL in handlers
            assert ChannelType.SLACK in handlers
            assert ChannelType.SMS not in handlers
            mock_logger.warning.assert_called_once()


class TestGetRules:
    """Tests for get_rules method."""

    @pytest.mark.asyncio
    async def test_get_rules_returns_empty_list(
        self, alert_service: AlertConfigService, mock_db_pool: Mock
    ) -> None:
        """Test get_rules returns empty list when no rules exist."""
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=[])
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch("libs.web_console_services.alert_service.acquire_connection") as mock_acquire:
            mock_acquire.return_value.__aenter__.return_value = mock_conn

            rules = await alert_service.get_rules()

            assert rules == []

    @pytest.mark.asyncio
    async def test_get_rules_returns_single_rule(
        self, alert_service: AlertConfigService, mock_db_pool: Mock
    ) -> None:
        """Test get_rules returns single rule correctly."""
        rule_id = uuid4()
        now = datetime.now(UTC)
        channel_data = [{"type": "email", "recipient": "test@example.com", "enabled": True}]

        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(
            return_value=[
                (
                    rule_id,
                    "Test Rule",
                    "drawdown",
                    Decimal("0.05"),
                    "gt",
                    channel_data,
                    True,
                    "admin-123",
                    now,
                    now,
                )
            ]
        )
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch("libs.web_console_services.alert_service.acquire_connection") as mock_acquire:
            mock_acquire.return_value.__aenter__.return_value = mock_conn

            rules = await alert_service.get_rules()

            assert len(rules) == 1
            assert rules[0].id == rule_id
            assert rules[0].name == "Test Rule"
            assert rules[0].condition_type == "drawdown"
            assert len(rules[0].channels) == 1
            assert rules[0].channels[0].type == ChannelType.EMAIL

    @pytest.mark.asyncio
    async def test_get_rules_with_null_channels(
        self, alert_service: AlertConfigService, mock_db_pool: Mock
    ) -> None:
        """Test get_rules handles null channels field."""
        rule_id = uuid4()
        now = datetime.now(UTC)

        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(
            return_value=[
                (
                    rule_id,
                    "Test Rule",
                    "drawdown",
                    Decimal("0.05"),
                    "gt",
                    None,  # NULL channels
                    True,
                    "admin-123",
                    now,
                    now,
                )
            ]
        )
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch("libs.web_console_services.alert_service.acquire_connection") as mock_acquire:
            mock_acquire.return_value.__aenter__.return_value = mock_conn

            rules = await alert_service.get_rules()

            assert len(rules) == 1
            assert rules[0].channels == []


class TestCreateRule:
    """Tests for create_rule method."""

    @pytest.mark.asyncio
    async def test_create_rule_success(
        self,
        alert_service: AlertConfigService,
        admin_user: dict[str, Any],
        sample_alert_rule_create: AlertRuleCreate,
    ) -> None:
        """Test create_rule succeeds with valid input."""
        rule_id = uuid4()
        now = datetime.now(UTC)

        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        channel_data = [{"type": "email", "recipient": "test@example.com", "enabled": True}]
        mock_cursor.fetchone = AsyncMock(
            return_value=(
                rule_id,
                "Test Alert",
                "drawdown",
                Decimal("0.05"),
                "gt",
                channel_data,
                True,
                "admin-123",
                now,
                now,
            )
        )
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch("libs.web_console_services.alert_service.acquire_connection") as mock_acquire, \
             patch("libs.web_console_services.alert_service.has_permission", return_value=True), \
             patch("libs.web_console_services.alert_service.uuid4", return_value=rule_id):
            mock_acquire.return_value.__aenter__.return_value = mock_conn

            rule = await alert_service.create_rule(sample_alert_rule_create, admin_user)

            assert rule.id == rule_id
            assert rule.name == "Test Alert"
            alert_service.audit_logger.log_action.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_rule_permission_denied(
        self,
        alert_service: AlertConfigService,
        viewer_user: dict[str, Any],
        sample_alert_rule_create: AlertRuleCreate,
    ) -> None:
        """Test create_rule raises PermissionError without permission."""
        with patch("libs.web_console_services.alert_service.has_permission", return_value=False):
            with pytest.raises(PermissionError, match="Permission CREATE_ALERT_RULE required"):
                await alert_service.create_rule(sample_alert_rule_create, viewer_user)

    @pytest.mark.asyncio
    async def test_create_rule_not_found_after_insert(
        self,
        alert_service: AlertConfigService,
        admin_user: dict[str, Any],
        sample_alert_rule_create: AlertRuleCreate,
    ) -> None:
        """Test create_rule raises RuntimeError if rule not found after insert."""
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=None)
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch("libs.web_console_services.alert_service.acquire_connection") as mock_acquire, \
             patch("libs.web_console_services.alert_service.has_permission", return_value=True):
            mock_acquire.return_value.__aenter__.return_value = mock_conn

            with pytest.raises(RuntimeError, match="not found after create"):
                await alert_service.create_rule(sample_alert_rule_create, admin_user)

    @pytest.mark.asyncio
    async def test_create_rule_with_unknown_user_id(
        self,
        alert_service: AlertConfigService,
        sample_alert_rule_create: AlertRuleCreate,
    ) -> None:
        """Test create_rule uses 'unknown' when user_id missing."""
        rule_id = uuid4()
        now = datetime.now(UTC)
        user_without_id: dict[str, Any] = {"role": "admin"}

        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        channel_data = [{"type": "email", "recipient": "test@example.com", "enabled": True}]
        mock_cursor.fetchone = AsyncMock(
            return_value=(
                rule_id,
                "Test Alert",
                "drawdown",
                Decimal("0.05"),
                "gt",
                channel_data,
                True,
                "unknown",
                now,
                now,
            )
        )
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch("libs.web_console_services.alert_service.acquire_connection") as mock_acquire, \
             patch("libs.web_console_services.alert_service.has_permission", return_value=True), \
             patch("libs.web_console_services.alert_service.uuid4", return_value=rule_id):
            mock_acquire.return_value.__aenter__.return_value = mock_conn

            rule = await alert_service.create_rule(sample_alert_rule_create, user_without_id)

            assert rule.created_by == "unknown"


class TestUpdateRule:
    """Tests for update_rule method."""

    @pytest.mark.asyncio
    async def test_update_rule_success(
        self,
        alert_service: AlertConfigService,
        operator_user: dict[str, Any],
    ) -> None:
        """Test update_rule succeeds with valid input."""
        rule_id = str(uuid4())
        now = datetime.now(UTC)
        update = AlertRuleUpdate(name="Updated Name", enabled=False)

        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        channel_data = [{"type": "email", "recipient": "test@example.com", "enabled": True}]
        mock_cursor.fetchone = AsyncMock(
            return_value=(
                UUID(rule_id),
                "Updated Name",
                "drawdown",
                Decimal("0.05"),
                "gt",
                channel_data,
                False,
                "operator-456",
                now,
                now,
            )
        )
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch("libs.web_console_services.alert_service.acquire_connection") as mock_acquire, \
             patch("libs.web_console_services.alert_service.has_permission", return_value=True):
            mock_acquire.return_value.__aenter__.return_value = mock_conn

            rule = await alert_service.update_rule(rule_id, update, operator_user)

            assert rule.name == "Updated Name"
            assert rule.enabled is False
            alert_service.audit_logger.log_action.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_rule_permission_denied(
        self,
        alert_service: AlertConfigService,
        viewer_user: dict[str, Any],
    ) -> None:
        """Test update_rule raises PermissionError without permission."""
        rule_id = str(uuid4())
        update = AlertRuleUpdate(name="Updated Name")

        with patch("libs.web_console_services.alert_service.has_permission", return_value=False):
            with pytest.raises(PermissionError, match="Permission UPDATE_ALERT_RULE required"):
                await alert_service.update_rule(rule_id, update, viewer_user)

    @pytest.mark.asyncio
    async def test_update_rule_with_channels(
        self,
        alert_service: AlertConfigService,
        operator_user: dict[str, Any],
        sample_channel_config: ChannelConfig,
    ) -> None:
        """Test update_rule with channels updates and masks them in audit log."""
        rule_id = str(uuid4())
        now = datetime.now(UTC)
        update = AlertRuleUpdate(channels=[sample_channel_config])

        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        channel_data = [{"type": "email", "recipient": "test@example.com", "enabled": True}]
        mock_cursor.fetchone = AsyncMock(
            return_value=(
                UUID(rule_id),
                "Test Rule",
                "drawdown",
                Decimal("0.05"),
                "gt",
                channel_data,
                True,
                "operator-456",
                now,
                now,
            )
        )
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch("libs.web_console_services.alert_service.acquire_connection") as mock_acquire, \
             patch("libs.web_console_services.alert_service.has_permission", return_value=True):
            mock_acquire.return_value.__aenter__.return_value = mock_conn

            await alert_service.update_rule(rule_id, update, operator_user)

            # Verify channels were masked in audit log
            call_args = alert_service.audit_logger.log_action.call_args
            details = call_args[1]["details"]
            assert "channels" in details["changes"]
            # mask_for_logs uses last 4 chars: test@example.com -> ***.com
            assert details["changes"]["channels"][0]["recipient"] == "***.com"

    @pytest.mark.asyncio
    async def test_update_rule_not_found_after_update(
        self,
        alert_service: AlertConfigService,
        operator_user: dict[str, Any],
    ) -> None:
        """Test update_rule raises RuntimeError if rule not found after update."""
        rule_id = str(uuid4())
        update = AlertRuleUpdate(name="Updated Name")

        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=None)
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch("libs.web_console_services.alert_service.acquire_connection") as mock_acquire, \
             patch("libs.web_console_services.alert_service.has_permission", return_value=True):
            mock_acquire.return_value.__aenter__.return_value = mock_conn

            with pytest.raises(RuntimeError, match="not found after update"):
                await alert_service.update_rule(rule_id, update, operator_user)


class TestDeleteRule:
    """Tests for delete_rule method."""

    @pytest.mark.asyncio
    async def test_delete_rule_success(
        self,
        alert_service: AlertConfigService,
        admin_user: dict[str, Any],
    ) -> None:
        """Test delete_rule succeeds with admin permission."""
        rule_id = str(uuid4())
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        with patch("libs.web_console_services.alert_service.acquire_connection") as mock_acquire, \
             patch("libs.web_console_services.alert_service.has_permission", return_value=True):
            mock_acquire.return_value.__aenter__.return_value = mock_conn

            await alert_service.delete_rule(rule_id, admin_user)

            alert_service.audit_logger.log_action.assert_called_once_with(
                user_id=admin_user.get("user_id"),
                action="ALERT_RULE_DELETED",
                resource_type="alert_rule",
                resource_id=rule_id,
                outcome="success",
            )

    @pytest.mark.asyncio
    async def test_delete_rule_permission_denied(
        self,
        alert_service: AlertConfigService,
        operator_user: dict[str, Any],
    ) -> None:
        """Test delete_rule raises PermissionError without DELETE permission."""
        rule_id = str(uuid4())

        with patch("libs.web_console_services.alert_service.has_permission", return_value=False):
            with pytest.raises(PermissionError, match="Permission DELETE_ALERT_RULE required"):
                await alert_service.delete_rule(rule_id, operator_user)


class TestAcknowledgeAlert:
    """Tests for acknowledge_alert method."""

    @pytest.mark.asyncio
    async def test_acknowledge_alert_success(
        self,
        alert_service: AlertConfigService,
        operator_user: dict[str, Any],
    ) -> None:
        """Test acknowledge_alert succeeds with valid note."""
        alert_id = str(uuid4())
        note = "This is a valid acknowledgment note with sufficient length"
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        with patch("libs.web_console_services.alert_service.acquire_connection") as mock_acquire, \
             patch("libs.web_console_services.alert_service.has_permission", return_value=True):
            mock_acquire.return_value.__aenter__.return_value = mock_conn

            await alert_service.acknowledge_alert(alert_id, note, operator_user)

            alert_service.audit_logger.log_action.assert_called_once()

    @pytest.mark.asyncio
    async def test_acknowledge_alert_permission_denied(
        self,
        alert_service: AlertConfigService,
        viewer_user: dict[str, Any],
    ) -> None:
        """Test acknowledge_alert raises PermissionError without permission."""
        alert_id = str(uuid4())
        note = "Valid note with sufficient length"

        with patch("libs.web_console_services.alert_service.has_permission", return_value=False):
            with pytest.raises(PermissionError, match="Permission ACKNOWLEDGE_ALERT required"):
                await alert_service.acknowledge_alert(alert_id, note, viewer_user)

    @pytest.mark.asyncio
    async def test_acknowledge_alert_note_too_short(
        self,
        alert_service: AlertConfigService,
        operator_user: dict[str, Any],
    ) -> None:
        """Test acknowledge_alert raises ValueError for short note."""
        alert_id = str(uuid4())
        note = "short"

        with patch("libs.web_console_services.alert_service.has_permission", return_value=True):
            with pytest.raises(
                ValueError, match=f"at least {MIN_ACK_NOTE_LENGTH} characters"
            ):
                await alert_service.acknowledge_alert(alert_id, note, operator_user)

    @pytest.mark.asyncio
    async def test_acknowledge_alert_strips_whitespace_in_validation(
        self,
        alert_service: AlertConfigService,
        operator_user: dict[str, Any],
    ) -> None:
        """Test acknowledge_alert strips whitespace before length check."""
        alert_id = str(uuid4())
        note = "   short   "  # Short after stripping

        with patch("libs.web_console_services.alert_service.has_permission", return_value=True):
            with pytest.raises(ValueError, match=f"at least {MIN_ACK_NOTE_LENGTH} characters"):
                await alert_service.acknowledge_alert(alert_id, note, operator_user)


class TestTestNotification:
    """Tests for test_notification method."""

    @pytest.mark.asyncio
    async def test_test_notification_success(
        self,
        alert_service: AlertConfigService,
        operator_user: dict[str, Any],
        sample_channel_config: ChannelConfig,
    ) -> None:
        """Test test_notification succeeds with valid channel."""
        mock_handler = AsyncMock()
        mock_handler.send = AsyncMock(
            return_value=DeliveryResult(success=True, message_id="msg-123")
        )

        with patch("libs.web_console_services.alert_service.has_permission", return_value=True), \
             patch.object(alert_service, "_get_channel_handlers", return_value={ChannelType.EMAIL: mock_handler}):

            result = await alert_service.test_notification(sample_channel_config, operator_user)

            assert result.success is True
            assert result.error is None
            mock_handler.send.assert_called_once()
            alert_service.audit_logger.log_action.assert_called_once()

    @pytest.mark.asyncio
    async def test_test_notification_permission_denied(
        self,
        alert_service: AlertConfigService,
        viewer_user: dict[str, Any],
        sample_channel_config: ChannelConfig,
    ) -> None:
        """Test test_notification raises PermissionError without permission."""
        with patch("libs.web_console_services.alert_service.has_permission", return_value=False):
            with pytest.raises(PermissionError, match="Permission TEST_NOTIFICATION required"):
                await alert_service.test_notification(sample_channel_config, viewer_user)

    @pytest.mark.asyncio
    async def test_test_notification_handler_failure(
        self,
        alert_service: AlertConfigService,
        operator_user: dict[str, Any],
        sample_channel_config: ChannelConfig,
    ) -> None:
        """Test test_notification handles handler send failure."""
        mock_handler = AsyncMock()
        mock_handler.send = AsyncMock(
            return_value=DeliveryResult(success=False, error="SMTP connection failed")
        )

        with patch("libs.web_console_services.alert_service.has_permission", return_value=True), \
             patch.object(alert_service, "_get_channel_handlers", return_value={ChannelType.EMAIL: mock_handler}):

            result = await alert_service.test_notification(sample_channel_config, operator_user)

            assert result.success is False
            assert result.error == "SMTP connection failed"
            alert_service.audit_logger.log_action.assert_called_once()

    @pytest.mark.asyncio
    async def test_test_notification_handler_exception(
        self,
        alert_service: AlertConfigService,
        operator_user: dict[str, Any],
        sample_channel_config: ChannelConfig,
    ) -> None:
        """Test test_notification handles handler exception."""
        mock_handler = AsyncMock()
        mock_handler.send = AsyncMock(side_effect=Exception("Network timeout"))

        with patch("libs.web_console_services.alert_service.has_permission", return_value=True), \
             patch.object(alert_service, "_get_channel_handlers", return_value={ChannelType.EMAIL: mock_handler}):

            result = await alert_service.test_notification(sample_channel_config, operator_user)

            assert result.success is False
            assert "Network timeout" in result.error
            # Verify audit log recorded failure
            call_args = alert_service.audit_logger.log_action.call_args
            assert call_args[1]["outcome"] == "failed"

    @pytest.mark.asyncio
    async def test_test_notification_unsupported_channel(
        self,
        alert_service: AlertConfigService,
        operator_user: dict[str, Any],
    ) -> None:
        """Test test_notification raises ValueError for unsupported channel."""
        # Create a channel with a type that's not in handlers
        channel = ChannelConfig(type=ChannelType.SMS, recipient="+1234567890", enabled=True)

        with patch("libs.web_console_services.alert_service.has_permission", return_value=True), \
             patch.object(alert_service, "_get_channel_handlers", return_value={}):

            with pytest.raises(ValueError, match="Unsupported channel type"):
                await alert_service.test_notification(channel, operator_user)


class TestAddChannel:
    """Tests for add_channel method."""

    @pytest.mark.asyncio
    async def test_add_channel_success(
        self,
        alert_service: AlertConfigService,
        operator_user: dict[str, Any],
        sample_channel_config: ChannelConfig,
    ) -> None:
        """Test add_channel succeeds when channel type doesn't exist."""
        rule_id = str(uuid4())
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=(False,))  # Channel doesn't exist
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch("libs.web_console_services.alert_service.acquire_connection") as mock_acquire, \
             patch("libs.web_console_services.alert_service.has_permission", return_value=True):
            mock_acquire.return_value.__aenter__.return_value = mock_conn

            await alert_service.add_channel(rule_id, sample_channel_config, operator_user)

            alert_service.audit_logger.log_action.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_channel_permission_denied(
        self,
        alert_service: AlertConfigService,
        viewer_user: dict[str, Any],
        sample_channel_config: ChannelConfig,
    ) -> None:
        """Test add_channel raises PermissionError without permission."""
        rule_id = str(uuid4())

        with patch("libs.web_console_services.alert_service.has_permission", return_value=False):
            with pytest.raises(PermissionError, match="Permission UPDATE_ALERT_RULE required"):
                await alert_service.add_channel(rule_id, sample_channel_config, viewer_user)

    @pytest.mark.asyncio
    async def test_add_channel_duplicate_type(
        self,
        alert_service: AlertConfigService,
        operator_user: dict[str, Any],
        sample_channel_config: ChannelConfig,
    ) -> None:
        """Test add_channel raises ValueError for duplicate channel type."""
        rule_id = str(uuid4())
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=(True,))  # Channel already exists
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch("libs.web_console_services.alert_service.acquire_connection") as mock_acquire, \
             patch("libs.web_console_services.alert_service.has_permission", return_value=True):
            mock_acquire.return_value.__aenter__.return_value = mock_conn

            with pytest.raises(ValueError, match="already exists for this rule"):
                await alert_service.add_channel(rule_id, sample_channel_config, operator_user)


class TestUpdateChannel:
    """Tests for update_channel method."""

    @pytest.mark.asyncio
    async def test_update_channel_success(
        self,
        alert_service: AlertConfigService,
        operator_user: dict[str, Any],
        sample_channel_config: ChannelConfig,
    ) -> None:
        """Test update_channel succeeds with valid input."""
        rule_id = str(uuid4())
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        with patch("libs.web_console_services.alert_service.acquire_connection") as mock_acquire, \
             patch("libs.web_console_services.alert_service.has_permission", return_value=True):
            mock_acquire.return_value.__aenter__.return_value = mock_conn

            await alert_service.update_channel(rule_id, sample_channel_config, operator_user)

            alert_service.audit_logger.log_action.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_channel_permission_denied(
        self,
        alert_service: AlertConfigService,
        viewer_user: dict[str, Any],
        sample_channel_config: ChannelConfig,
    ) -> None:
        """Test update_channel raises PermissionError without permission."""
        rule_id = str(uuid4())

        with patch("libs.web_console_services.alert_service.has_permission", return_value=False):
            with pytest.raises(PermissionError, match="Permission UPDATE_ALERT_RULE required"):
                await alert_service.update_channel(rule_id, sample_channel_config, viewer_user)


class TestRemoveChannel:
    """Tests for remove_channel method."""

    @pytest.mark.asyncio
    async def test_remove_channel_success(
        self,
        alert_service: AlertConfigService,
        operator_user: dict[str, Any],
    ) -> None:
        """Test remove_channel succeeds with valid input."""
        rule_id = str(uuid4())
        channel_type = "email"
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        with patch("libs.web_console_services.alert_service.acquire_connection") as mock_acquire, \
             patch("libs.web_console_services.alert_service.has_permission", return_value=True):
            mock_acquire.return_value.__aenter__.return_value = mock_conn

            await alert_service.remove_channel(rule_id, channel_type, operator_user)

            alert_service.audit_logger.log_action.assert_called_once()

    @pytest.mark.asyncio
    async def test_remove_channel_permission_denied(
        self,
        alert_service: AlertConfigService,
        viewer_user: dict[str, Any],
    ) -> None:
        """Test remove_channel raises PermissionError without permission."""
        rule_id = str(uuid4())
        channel_type = "email"

        with patch("libs.web_console_services.alert_service.has_permission", return_value=False):
            with pytest.raises(PermissionError, match="Permission UPDATE_ALERT_RULE required"):
                await alert_service.remove_channel(rule_id, channel_type, viewer_user)


class TestGetAlertEvents:
    """Tests for get_alert_events method."""

    @pytest.mark.asyncio
    async def test_get_alert_events_with_default_limit(
        self, alert_service: AlertConfigService, mock_db_pool: Mock
    ) -> None:
        """Test get_alert_events uses default limit."""
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=[])
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch("libs.web_console_services.alert_service.acquire_connection") as mock_acquire:
            mock_acquire.return_value.__aenter__.return_value = mock_conn

            events = await alert_service.get_alert_events()

            assert events == []
            # Verify default limit was used
            call_args = mock_conn.execute.call_args
            assert call_args[0][1] == (DEFAULT_ALERT_EVENT_LIMIT,)

    @pytest.mark.asyncio
    async def test_get_alert_events_with_custom_limit(
        self, alert_service: AlertConfigService, mock_db_pool: Mock
    ) -> None:
        """Test get_alert_events with custom limit."""
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=[])
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch("libs.web_console_services.alert_service.acquire_connection") as mock_acquire:
            mock_acquire.return_value.__aenter__.return_value = mock_conn

            events = await alert_service.get_alert_events(limit=50)

            assert events == []
            # Verify custom limit was used
            call_args = mock_conn.execute.call_args
            assert call_args[0][1] == (50,)

    @pytest.mark.asyncio
    async def test_get_alert_events_returns_events(
        self, alert_service: AlertConfigService, mock_db_pool: Mock
    ) -> None:
        """Test get_alert_events returns AlertEvent objects."""
        event_id = uuid4()
        rule_id = uuid4()
        now = datetime.now(UTC)

        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(
            return_value=[
                {
                    "id": event_id,
                    "rule_id": rule_id,
                    "rule_name": "Test Rule",
                    "triggered_at": now,
                    "trigger_value": Decimal("0.06"),
                    "acknowledged_at": None,
                    "acknowledged_by": None,
                    "acknowledged_note": None,
                    "routed_channels": ["email", "slack"],
                    "created_at": now,
                }
            ]
        )
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch("libs.web_console_services.alert_service.acquire_connection") as mock_acquire:
            mock_acquire.return_value.__aenter__.return_value = mock_conn

            events = await alert_service.get_alert_events()

            assert len(events) == 1
            assert isinstance(events[0], AlertEvent)
            assert events[0].id == event_id
            assert events[0].rule_id == rule_id


class TestEdgeCases:
    """Tests for edge cases and error conditions."""

    @pytest.mark.asyncio
    async def test_create_rule_with_multiple_channels(
        self,
        alert_service: AlertConfigService,
        admin_user: dict[str, Any],
    ) -> None:
        """Test create_rule with multiple channel configurations."""
        channels = [
            ChannelConfig(type=ChannelType.EMAIL, recipient="test@example.com", enabled=True),
            ChannelConfig(type=ChannelType.SLACK, recipient="https://hooks.slack.com/test", enabled=True),
        ]
        rule_create = AlertRuleCreate(
            name="Multi-channel Alert",
            condition_type="volatility",
            threshold_value=Decimal("0.10"),
            comparison="lt",
            channels=channels,
            enabled=True,
        )

        rule_id = uuid4()
        now = datetime.now(UTC)
        channel_data = [c.model_dump() for c in channels]

        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(
            return_value=(
                rule_id,
                "Multi-channel Alert",
                "volatility",
                Decimal("0.10"),
                "lt",
                channel_data,
                True,
                "admin-123",
                now,
                now,
            )
        )
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch("libs.web_console_services.alert_service.acquire_connection") as mock_acquire, \
             patch("libs.web_console_services.alert_service.has_permission", return_value=True), \
             patch("libs.web_console_services.alert_service.uuid4", return_value=rule_id):
            mock_acquire.return_value.__aenter__.return_value = mock_conn

            rule = await alert_service.create_rule(rule_create, admin_user)

            assert len(rule.channels) == 2

    @pytest.mark.asyncio
    async def test_update_rule_with_none_values(
        self,
        alert_service: AlertConfigService,
        operator_user: dict[str, Any],
    ) -> None:
        """Test update_rule with all None values (no-op update)."""
        rule_id = str(uuid4())
        now = datetime.now(UTC)
        update = AlertRuleUpdate()  # All fields are None

        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        channel_data = [{"type": "email", "recipient": "test@example.com", "enabled": True}]
        mock_cursor.fetchone = AsyncMock(
            return_value=(
                UUID(rule_id),
                "Original Name",
                "drawdown",
                Decimal("0.05"),
                "gt",
                channel_data,
                True,
                "operator-456",
                now,
                now,
            )
        )
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch("libs.web_console_services.alert_service.acquire_connection") as mock_acquire, \
             patch("libs.web_console_services.alert_service.has_permission", return_value=True):
            mock_acquire.return_value.__aenter__.return_value = mock_conn

            rule = await alert_service.update_rule(rule_id, update, operator_user)

            # Rule should remain unchanged
            assert rule.name == "Original Name"

    @pytest.mark.asyncio
    async def test_acknowledge_alert_with_exact_minimum_length(
        self,
        alert_service: AlertConfigService,
        operator_user: dict[str, Any],
    ) -> None:
        """Test acknowledge_alert with note exactly at minimum length."""
        alert_id = str(uuid4())
        note = "A" * MIN_ACK_NOTE_LENGTH  # Exact minimum
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        with patch("libs.web_console_services.alert_service.acquire_connection") as mock_acquire, \
             patch("libs.web_console_services.alert_service.has_permission", return_value=True):
            mock_acquire.return_value.__aenter__.return_value = mock_conn

            # Should not raise
            await alert_service.acknowledge_alert(alert_id, note, operator_user)

            alert_service.audit_logger.log_action.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_alert_events_with_zero_limit(
        self, alert_service: AlertConfigService, mock_db_pool: Mock
    ) -> None:
        """Test get_alert_events with zero limit."""
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=[])
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch("libs.web_console_services.alert_service.acquire_connection") as mock_acquire:
            mock_acquire.return_value.__aenter__.return_value = mock_conn

            events = await alert_service.get_alert_events(limit=0)

            assert events == []
