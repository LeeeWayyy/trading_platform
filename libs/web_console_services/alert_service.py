"""Alert configuration service with RBAC enforcement and audit logging."""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any
from uuid import UUID as _UUID
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from libs.core.common.db import acquire_connection
from libs.core.common.exceptions import ConfigurationError
from libs.platform.alerts.channels.base import BaseChannel
from libs.platform.alerts.channels.pagerduty import PagerDutyChannel
from libs.platform.alerts.channels.slack import SlackChannel
from libs.platform.alerts.models import AlertEvent, AlertRule, ChannelConfig, ChannelType
from libs.platform.alerts.pii import mask_for_logs
from libs.platform.alerts.poison_queue import _sanitize_error_for_log
from libs.platform.web_console_auth.audit_log import AuditLogger
from libs.platform.web_console_auth.permissions import Permission, has_permission

logger = logging.getLogger(__name__)

_EMAIL_CHANNEL_IMPORT_ERROR: str | None
try:
    from libs.platform.alerts.channels.email import EmailChannel as _ImportedEmailChannel
except ModuleNotFoundError as exc:
    _EmailChannel: type[BaseChannel] | None = None
    _EMAIL_CHANNEL_IMPORT_ERROR = str(exc)
else:
    _EmailChannel = _ImportedEmailChannel
    _EMAIL_CHANNEL_IMPORT_ERROR = None

_SMS_CHANNEL_IMPORT_ERROR: str | None
try:
    from libs.platform.alerts.channels.sms import SMSChannel as _ImportedSMSChannel
except ModuleNotFoundError as exc:
    _SMSChannel: type[BaseChannel] | None = None
    _SMS_CHANNEL_IMPORT_ERROR = str(exc)
else:
    _SMSChannel = _ImportedSMSChannel
    _SMS_CHANNEL_IMPORT_ERROR = None

EmailChannel: type[BaseChannel] | None = _EmailChannel
SMSChannel: type[BaseChannel] | None = _SMSChannel

# Default limit for alert event queries
DEFAULT_ALERT_EVENT_LIMIT = 20

# Minimum characters required for acknowledgment notes (shared with UI)
MIN_ACK_NOTE_LENGTH = 15


class TestResult(BaseModel):
    """Result of test notification."""

    success: bool
    error: str | None = None


class AlertRuleCreate(BaseModel):
    """Pydantic model for creating alert rules."""

    name: str
    condition_type: str
    threshold_value: Decimal
    comparison: str
    channels: list[ChannelConfig]
    enabled: bool = True
    model_config = ConfigDict(extra="forbid")


class AlertRuleUpdate(BaseModel):
    """Pydantic model for updating alert rules."""

    name: str | None = None
    condition_type: str | None = None
    threshold_value: Decimal | None = None
    comparison: str | None = None
    channels: list[ChannelConfig] | None = None
    enabled: bool | None = None
    model_config = ConfigDict(extra="forbid")


class AlertConfigService:
    """Service for alert configuration CRUD with audit logging."""

    def __init__(self, db_pool: Any, audit_logger: AuditLogger) -> None:
        self.db_pool = db_pool
        self.audit_logger = audit_logger
        self._channel_handlers: dict[ChannelType, BaseChannel] | None = None

    def _get_channel_handlers(self) -> dict[ChannelType, BaseChannel]:
        """Build channel handlers, lazily skipping unconfigured channels.

        SMS channel requires Twilio credentials. If not configured, SMS is
        skipped and a warning is logged. Email, Slack, and PagerDuty are always enabled.
        """
        if self._channel_handlers is None:
            self._channel_handlers = {
                ChannelType.SLACK: SlackChannel(),
            }
            if EmailChannel is None:
                logger.warning(
                    "email_channel_disabled",
                    extra={
                        "reason": _EMAIL_CHANNEL_IMPORT_ERROR or "email dependencies unavailable",
                        "hint": "Install aiosmtplib to enable SMTP email notifications",
                    },
                )
            else:
                self._channel_handlers[ChannelType.EMAIL] = EmailChannel()

            # SMS requires Twilio credentials - skip if not configured
            self._add_sms_channel_handler(self._channel_handlers)
            # PagerDuty uses routing key per-recipient, no global credentials needed
            self._channel_handlers[ChannelType.PAGERDUTY] = PagerDutyChannel()
        return self._channel_handlers

    def _add_sms_channel_handler(self, handlers: dict[ChannelType, BaseChannel]) -> None:
        """Attach SMS handler when dependencies and credentials are available."""
        if SMSChannel is None:
            logger.warning(
                "sms_channel_disabled",
                extra={
                    "reason": _SMS_CHANNEL_IMPORT_ERROR or "sms dependencies unavailable",
                    "hint": (
                        "Install twilio and set TWILIO_ACCOUNT_SID, "
                        "TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER"
                    ),
                },
            )
            return

        try:
            handlers[ChannelType.SMS] = SMSChannel()
        except ConfigurationError as exc:
            logger.warning(
                "sms_channel_disabled",
                extra={
                    "reason": str(exc),
                    "hint": "Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER",
                },
            )

    async def get_rules(self) -> list[AlertRule]:
        """Fetch all alert rules."""

        async with acquire_connection(self.db_pool) as conn:
            cursor = await conn.execute(
                """
                SELECT id, name, condition_type, threshold_value, comparison,
                       channels, enabled, created_by, created_at, updated_at
                FROM alert_rules
                ORDER BY created_at DESC
                """
            )
            rows = await cursor.fetchall()
        rules: list[AlertRule] = []
        for row in rows:
            channels_raw = row[5] or []
            channels = [ChannelConfig(**c) for c in channels_raw]
            rules.append(
                AlertRule(
                    id=row[0],
                    name=row[1],
                    condition_type=row[2],
                    threshold_value=row[3],
                    comparison=row[4],
                    channels=channels,
                    enabled=row[6],
                    created_by=row[7],
                    created_at=row[8],
                    updated_at=row[9],
                )
            )
        return rules

    async def create_rule(self, rule: AlertRuleCreate, user: dict[str, Any]) -> AlertRule:
        """Create new alert rule with audit logging.

        Security Note: The channels JSONB column stores raw recipient addresses
        (email, phone, webhook URL) because the system needs these to deliver
        notifications. Access to this data is protected by RBAC (CREATE_ALERT_RULE
        permission required). For enhanced security, consider implementing
        application-level encryption at rest for the channels field.

        Emits: ALERT_RULE_CREATED audit event.
        """
        if not has_permission(user, Permission.CREATE_ALERT_RULE):
            raise PermissionError("Permission CREATE_ALERT_RULE required")

        rule_id = uuid4()
        user_id = user.get("user_id", "unknown")

        async with acquire_connection(self.db_pool) as conn:
            await conn.execute(
                """
                INSERT INTO alert_rules (
                    id, name, condition_type, threshold_value, comparison,
                    channels, enabled, created_by, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                """,
                (
                    str(rule_id),
                    rule.name,
                    rule.condition_type,
                    rule.threshold_value,
                    rule.comparison,
                    json.dumps([c.model_dump() for c in rule.channels]),
                    rule.enabled,
                    user_id,
                ),
            )
            cursor = await conn.execute(
                """
                SELECT id, name, condition_type, threshold_value, comparison,
                       channels, enabled, created_by, created_at, updated_at
                FROM alert_rules
                WHERE id = %s
                """,
                (str(rule_id),),
            )
            row = await cursor.fetchone()

        if not row:
            raise RuntimeError(f"Alert rule {rule_id} not found after create")

        channels_raw = row[5] or []
        channels = [ChannelConfig(**c) for c in channels_raw]

        await self.audit_logger.log_action(
            user_id=user.get("user_id"),
            action="ALERT_RULE_CREATED",
            resource_type="alert_rule",
            resource_id=str(rule_id),
            outcome="success",
            details={"rule_name": rule.name, "condition_type": rule.condition_type},
        )
        return AlertRule(
            id=row[0],
            name=row[1],
            condition_type=row[2],
            threshold_value=row[3],
            comparison=row[4],
            channels=channels,
            enabled=row[6],
            created_by=row[7],
            created_at=row[8],
            updated_at=row[9],
        )

    async def update_rule(
        self, rule_id: str, update: AlertRuleUpdate, user: dict[str, Any]
    ) -> AlertRule:
        """Update alert rule with audit logging.

        Emits: ALERT_RULE_UPDATED audit event.
        """
        if not has_permission(user, Permission.UPDATE_ALERT_RULE):
            raise PermissionError("Permission UPDATE_ALERT_RULE required")

        update_dict = update.model_dump(exclude_unset=True)
        masked_changes = update_dict.copy()
        if update.channels is not None:
            masked_changes["channels"] = [
                {
                    "type": channel.type.value,
                    "recipient": mask_for_logs(channel.recipient, channel.type.value),
                    "enabled": channel.enabled,
                }
                for channel in update.channels
            ]
        # Use update.channels (ChannelConfig objects) not update_dict["channels"] (dicts)
        channels_json = (
            json.dumps([c.model_dump() for c in update.channels])
            if update.channels is not None
            else None
        )

        async with acquire_connection(self.db_pool) as conn:
            await conn.execute(
                """
                UPDATE alert_rules
                SET
                    name = COALESCE(%s, name),
                    condition_type = COALESCE(%s, condition_type),
                    threshold_value = COALESCE(%s, threshold_value),
                    comparison = COALESCE(%s, comparison),
                    channels = COALESCE(%s, channels),
                    enabled = COALESCE(%s, enabled),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    update_dict.get("name"),
                    update_dict.get("condition_type"),
                    update_dict.get("threshold_value"),
                    update_dict.get("comparison"),
                    channels_json,
                    update_dict.get("enabled"),
                    rule_id,
                ),
            )

        await self.audit_logger.log_action(
            user_id=user.get("user_id"),
            action="ALERT_RULE_UPDATED",
            resource_type="alert_rule",
            resource_id=rule_id,
            outcome="success",
            details={"changes": masked_changes},
        )

        # Return latest view
        async with acquire_connection(self.db_pool) as conn:
            cursor = await conn.execute(
                """
                SELECT id, name, condition_type, threshold_value, comparison,
                       channels, enabled, created_by, created_at, updated_at
                FROM alert_rules
                WHERE id = %s
                """,
                (rule_id,),
            )
            row = await cursor.fetchone()
        if not row:
            raise RuntimeError(f"Alert rule {rule_id} not found after update")
        channels_raw = row[5] or []
        channels = [ChannelConfig(**c) for c in channels_raw]
        return AlertRule(
            id=row[0],
            name=row[1],
            condition_type=row[2],
            threshold_value=row[3],
            comparison=row[4],
            channels=channels,
            enabled=row[6],
            created_by=row[7],
            created_at=row[8],
            updated_at=row[9],
        )

    async def delete_rule(self, rule_id: str, user: dict[str, Any]) -> None:
        """Delete alert rule (admin only) with audit logging.

        Emits: ALERT_RULE_DELETED audit event.
        """
        if not has_permission(user, Permission.DELETE_ALERT_RULE):
            raise PermissionError("Permission DELETE_ALERT_RULE required")

        async with acquire_connection(self.db_pool) as conn:
            await conn.execute(
                """
                DELETE FROM alert_rules
                WHERE id = %s
                """,
                (rule_id,),
            )
        await self.audit_logger.log_action(
            user_id=user.get("user_id"),
            action="ALERT_RULE_DELETED",
            resource_type="alert_rule",
            resource_id=rule_id,
            outcome="success",
        )

    async def acknowledge_alert(self, alert_id: str, note: str, user: dict[str, Any]) -> None:
        """Acknowledge alert event.

        Emits: ALERT_ACKNOWLEDGED audit event.
        """
        if not has_permission(user, Permission.ACKNOWLEDGE_ALERT):
            raise PermissionError("Permission ACKNOWLEDGE_ALERT required")

        if len(note.strip()) < MIN_ACK_NOTE_LENGTH:
            raise ValueError(
                f"Acknowledgment note must be at least {MIN_ACK_NOTE_LENGTH} characters"
            )

        async with acquire_connection(self.db_pool) as conn:
            await conn.execute(
                """
                UPDATE alert_events
                SET acknowledged_at = NOW(),
                    acknowledged_by = %s,
                    acknowledged_note = %s
                WHERE id = %s
                """,
                (user.get("user_id"), note, alert_id),
            )

        sanitized_note = _sanitize_error_for_log(note)
        await self.audit_logger.log_action(
            user_id=user.get("user_id"),
            action="ALERT_ACKNOWLEDGED",
            resource_type="alert_event",
            resource_id=alert_id,
            outcome="success",
            details={"note": sanitized_note},
        )

    async def test_notification(self, channel: ChannelConfig, user: dict[str, Any]) -> TestResult:
        """Send test notification.

        Emits: TEST_NOTIFICATION_SENT audit event
        """
        if not has_permission(user, Permission.TEST_NOTIFICATION):
            raise PermissionError("Permission TEST_NOTIFICATION required")

        handlers = self._get_channel_handlers()
        handler = handlers.get(channel.type)
        if handler is None:  # pragma: no cover - defensive guard
            raise ValueError(f"Unsupported channel type: {channel.type}")

        subject = "Trading Platform Test Notification"
        body = (
            "This is a test notification from the trading platform web console. "
            "If you received this message, your channel configuration is working."
        )

        try:
            result = await handler.send(
                recipient=channel.recipient,
                subject=subject,
                body=body,
                metadata={"test_notification": "true"},
            )
        except (
            Exception
        ) as exc:  # Generic catch justified - safety net for unexpected channel failures
            error_msg = _sanitize_error_for_log(str(exc))
            await self.audit_logger.log_action(
                user_id=user.get("user_id"),
                action="TEST_NOTIFICATION_SENT",
                resource_type="notification_channel",
                resource_id=channel.type.value,
                outcome="failed",
                details={
                    "recipient_masked": mask_for_logs(channel.recipient, channel.type.value),
                    "error": error_msg,
                    "error_type": type(exc).__name__,
                },
            )
            return TestResult(success=False, error=error_msg)

        outcome = "success" if result.success else "failed"
        masked_recipient = mask_for_logs(channel.recipient, channel.type.value)
        sanitized_error = _sanitize_error_for_log(result.error) if result.error else None
        await self.audit_logger.log_action(
            user_id=user.get("user_id"),
            action="TEST_NOTIFICATION_SENT",
            resource_type="notification_channel",
            resource_id=channel.type.value,
            outcome=outcome,
            details={
                "recipient_masked": masked_recipient,
                "error": sanitized_error,
                "channel_type": channel.type.value,
            },
        )
        return TestResult(success=result.success, error=sanitized_error)

    async def add_channel(self, rule_id: str, channel: ChannelConfig, user: dict[str, Any]) -> None:
        """Add notification channel to rule.

        Only one channel per type is allowed per rule. Adding a duplicate type raises ValueError.
        Emits: CHANNEL_ADDED audit event
        """
        if not has_permission(user, Permission.UPDATE_ALERT_RULE):
            raise PermissionError("Permission UPDATE_ALERT_RULE required")

        async with acquire_connection(self.db_pool) as conn:
            # Check if channel type already exists (enforce uniqueness)
            cursor = await conn.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM alert_rules, jsonb_array_elements(channels) AS elem
                    WHERE id = %s AND elem ->> 'type' = %s
                )
                """,
                (rule_id, channel.type.value),
            )
            row = await cursor.fetchone()
            if row and row[0]:
                raise ValueError(
                    f"Channel type '{channel.type.value}' already exists for this rule"
                )

            await conn.execute(
                """
                UPDATE alert_rules
                SET channels = channels || %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (json.dumps([channel.model_dump()]), rule_id),
            )

        await self.audit_logger.log_action(
            user_id=user.get("user_id"),
            action="CHANNEL_ADDED",
            resource_type="notification_channel",
            resource_id=f"{rule_id}:{channel.type.value}",
            outcome="success",
            details={"channel_type": channel.type.value},
        )

    async def update_channel(
        self, rule_id: str, channel: ChannelConfig, user: dict[str, Any]
    ) -> None:
        """Update notification channel configuration.

        Assumes channel types are unique per rule (enforced by add_channel).
        Emits: CHANNEL_UPDATED audit event
        """
        if not has_permission(user, Permission.UPDATE_ALERT_RULE):
            raise PermissionError("Permission UPDATE_ALERT_RULE required")

        async with acquire_connection(self.db_pool) as conn:
            # This query finds the array index of the channel with the matching type,
            # then uses jsonb_set to replace that element with the updated config.
            # Channel types are unique per rule (enforced by add_channel), so
            # generate_series finds at most one matching index.
            await conn.execute(
                """
                UPDATE alert_rules
                SET channels = jsonb_set(
                    channels,
                    ('{' || idx || '}')::text[],
                    %s::jsonb
                )
                FROM (
                    SELECT i - 1 AS idx
                    FROM generate_series(1, jsonb_array_length(channels)) AS s(i)
                    WHERE channels -> (i - 1) ->> 'type' = %s
                ) AS sub
                WHERE id = %s
                """,
                (json.dumps(channel.model_dump()), channel.type.value, rule_id),
            )

        await self.audit_logger.log_action(
            user_id=user.get("user_id"),
            action="CHANNEL_UPDATED",
            resource_type="notification_channel",
            resource_id=f"{rule_id}:{channel.type.value}",
            outcome="success",
            details={"channel_type": channel.type.value, "enabled": channel.enabled},
        )

    async def remove_channel(self, rule_id: str, channel_type: str, user: dict[str, Any]) -> None:
        """Remove notification channel from rule.

        Emits: CHANNEL_REMOVED audit event
        """
        if not has_permission(user, Permission.UPDATE_ALERT_RULE):
            raise PermissionError("Permission UPDATE_ALERT_RULE required")

        async with acquire_connection(self.db_pool) as conn:
            await conn.execute(
                """
                UPDATE alert_rules
                SET channels = COALESCE((
                    SELECT jsonb_agg(elem)
                    FROM jsonb_array_elements(channels) elem
                    WHERE elem ->> 'type' <> %s
                ), '[]'::jsonb),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (channel_type, rule_id),
            )

        await self.audit_logger.log_action(
            user_id=user.get("user_id"),
            action="CHANNEL_REMOVED",
            resource_type="notification_channel",
            resource_id=f"{rule_id}:{channel_type}",
            outcome="success",
        )

    async def get_alert_events(
        self,
        limit: int = DEFAULT_ALERT_EVENT_LIMIT,
        offset: int = 0,
        status_filter: str | None = None,
    ) -> list[AlertEvent]:
        """Get recent alert events ordered by triggered_at.

        Args:
            limit: Maximum number of events to return
            offset: Number of events to skip (for pagination)
            status_filter: Optional filter — "pending" or "acknowledged"

        Returns:
            List of AlertEvent objects
        """
        where_clause = ""
        params: list[Any] = []

        if status_filter == "pending":
            where_clause = "WHERE ae.acknowledged_at IS NULL"
        elif status_filter == "acknowledged":
            where_clause = "WHERE ae.acknowledged_at IS NOT NULL"

        params.extend([limit, offset])

        async with acquire_connection(self.db_pool) as conn:
            cursor = await conn.execute(
                f"""
                SELECT ae.id,
                       ae.rule_id,
                       ar.name AS rule_name,
                       ae.triggered_at,
                       ae.trigger_value,
                       ae.acknowledged_at,
                       ae.acknowledged_by,
                       ae.acknowledged_note,
                       ae.routed_channels,
                       ae.created_at
                FROM alert_events AS ae
                LEFT JOIN alert_rules AS ar ON ar.id = ae.rule_id
                {where_clause}
                ORDER BY ae.triggered_at DESC, ae.id DESC
                LIMIT %s OFFSET %s
                """,
                params,
            )
            rows = await cursor.fetchall()
        return [AlertEvent(**row) for row in rows]

    async def get_alert_events_count(self, status_filter: str | None = None) -> int:
        """Get total count of alert events for pagination.

        Args:
            status_filter: Optional filter — "pending" or "acknowledged"

        Returns:
            Total count of matching events
        """
        where_clause = ""
        if status_filter == "pending":
            where_clause = "WHERE acknowledged_at IS NULL"
        elif status_filter == "acknowledged":
            where_clause = "WHERE acknowledged_at IS NOT NULL"

        async with acquire_connection(self.db_pool) as conn:
            cursor = await conn.execute(
                f"""
                SELECT COUNT(*)
                FROM alert_events
                {where_clause}
                """
            )
            row = await cursor.fetchone()
        return row[0] if row else 0

    async def bulk_acknowledge_alerts(
        self, alert_ids: list[str], note: str, user: dict[str, Any]
    ) -> int:
        """Bulk acknowledge multiple alert events in a single UPDATE.

        Emits: ALERTS_BULK_ACKNOWLEDGED audit event.

        Returns:
            Number of events acknowledged
        """
        if not has_permission(user, Permission.ACKNOWLEDGE_ALERT):
            raise PermissionError("Permission ACKNOWLEDGE_ALERT required")

        if len(note.strip()) < MIN_ACK_NOTE_LENGTH:
            raise ValueError(
                f"Acknowledgment note must be at least {MIN_ACK_NOTE_LENGTH} characters"
            )

        if not alert_ids:
            return 0

        # Pre-validate UUIDs to give a clear ValueError instead of a DB DataError
        for aid in alert_ids:
            try:
                _UUID(aid)
            except (ValueError, AttributeError) as exc:
                raise ValueError(f"Invalid alert ID format: {aid}") from exc

        user_id = user.get("user_id", "unknown")

        async with acquire_connection(self.db_pool) as conn:
            cursor = await conn.execute(
                """
                UPDATE alert_events
                SET acknowledged_at = NOW(),
                    acknowledged_by = %s,
                    acknowledged_note = %s
                WHERE id = ANY(%s::uuid[])
                  AND acknowledged_at IS NULL
                """,
                (user_id, note.strip(), alert_ids),
            )
            count: int = cursor.rowcount or 0

        sanitized_note = _sanitize_error_for_log(note)
        await self.audit_logger.log_action(
            user_id=user_id,
            action="ALERTS_BULK_ACKNOWLEDGED",
            resource_type="alert_event",
            resource_id=",".join(alert_ids[:5]),
            outcome="success",
            details={
                "count": count,
                "note": sanitized_note,
            },
        )
        return count


__all__ = [
    "AlertConfigService",
    "AlertRuleCreate",
    "AlertRuleUpdate",
    "TestResult",
]
