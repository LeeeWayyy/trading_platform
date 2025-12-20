from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from apps.web_console.services.alert_service import (
    AlertConfigService,
    AlertRuleCreate,
    AlertRuleUpdate,
)
from libs.alerts.models import ChannelConfig, ChannelType


class DummyCursor:
    def __init__(self, rows=None) -> None:
        self.rows = rows or []
        self.params = None

    async def fetchall(self):
        return self.rows

    async def fetchone(self):
        return self.rows[0] if self.rows else None


class DummyConn:
    def __init__(self, cursor: DummyCursor) -> None:
        self.cursor = cursor
        self.executed = []

    async def execute(self, query, params=None):
        self.executed.append((query.strip(), params))
        return self.cursor


@asynccontextmanager
async def _conn_ctx(conn: DummyConn):
    yield conn


@pytest.mark.asyncio()
async def test_create_rule_emits_audit(monkeypatch):
    cursor = DummyCursor()
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
