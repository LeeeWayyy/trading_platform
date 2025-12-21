from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from apps.web_console.services.alert_service import (
    AlertConfigService,
    AlertRuleCreate,
    AlertRuleUpdate,
)
from libs.alerts.channels.base import DeliveryResult
from libs.alerts.models import ChannelConfig, ChannelType


class DummyCursor:
    def __init__(self, rows: list | None = None) -> None:
        self.rows = rows or []
        self.params = None

    async def fetchall(self) -> list:
        return self.rows

    async def fetchone(self) -> tuple | None:
        return self.rows[0] if self.rows else None


class DummyConn:
    def __init__(self, cursor: DummyCursor) -> None:
        self.cursor = cursor
        self.executed: list[tuple] = []

    async def execute(self, query: str, params: tuple | None = None) -> DummyCursor:
        self.executed.append((query.strip(), params))
        return self.cursor


@asynccontextmanager
async def _conn_ctx(conn: DummyConn):
    yield conn


@pytest.mark.asyncio()
async def test_create_rule_emits_audit(monkeypatch):
    rule_id = uuid4()
    now = datetime.now(UTC)
    # Mock row returned by SELECT after INSERT
    mock_row = (
        rule_id,  # id
        "dd alert",  # name
        "drawdown",  # condition_type
        Decimal("-0.05"),  # threshold_value
        "lt",  # comparison
        [{"type": "email", "recipient": "user@example.com", "enabled": True}],  # channels
        True,  # enabled
        "u1",  # created_by
        now,  # created_at
        now,  # updated_at
    )
    cursor = DummyCursor(rows=[mock_row])
    conn = DummyConn(cursor)

    monkeypatch.setattr(
        "apps.web_console.services.alert_service.acquire_connection",
        lambda db_pool: _conn_ctx(conn),
    )

    audit_logger = AsyncMock()
    service = AlertConfigService(db_pool=object(), audit_logger=audit_logger)
    rule = AlertRuleCreate(
        name="dd alert",
        condition_type="drawdown",
        threshold_value=Decimal("-0.05"),
        comparison="lt",
        channels=[ChannelConfig(type=ChannelType.EMAIL, recipient="user@example.com")],
    )
    user = {"user_id": "u1", "role": "admin"}

    result = await service.create_rule(rule, user)

    assert result.name == "dd alert"
    audit_logger.log_action.assert_awaited()
    actions = [call.kwargs["action"] for call in audit_logger.log_action.await_args_list]  # type: ignore[attr-defined]
    assert "ALERT_RULE_CREATED" in actions


@pytest.mark.asyncio()
async def test_test_notification_masks_recipient(monkeypatch):
    audit_logger = AsyncMock()
    service = AlertConfigService(db_pool=None, audit_logger=audit_logger)
    channel = ChannelConfig(type=ChannelType.SMS, recipient="+15551234567")
    user = {"user_id": "u1", "role": "admin"}

    # Mock channel handler to avoid needing real secrets
    mock_handler = MagicMock()
    mock_handler.send = AsyncMock(return_value=DeliveryResult(success=True, message_id="test-123"))
    monkeypatch.setattr(
        service,
        "_get_channel_handlers",
        lambda: {ChannelType.SMS: mock_handler},
    )

    await service.test_notification(channel, user)

    audit_logger.log_action.assert_awaited()
    details = audit_logger.log_action.await_args.kwargs["details"]  # type: ignore[attr-defined]
    assert details["recipient_masked"].endswith("4567")


@pytest.mark.asyncio()
async def test_update_rule_enforces_permission(monkeypatch):
    audit_logger = AsyncMock()
    service = AlertConfigService(db_pool=None, audit_logger=audit_logger)
    user = {"role": "viewer"}
    update = AlertRuleUpdate(name="x")
    with pytest.raises(PermissionError):
        await service.update_rule("rule1", update, user)
