"""Tests for AuthAuditLogger."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from apps.web_console_ng import config
from apps.web_console_ng.auth.audit import AuthAuditLogger


class _FakeConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[tuple[object, ...]]]] = []

    async def executemany(self, query: str, batch: list[tuple[object, ...]]) -> None:
        self.calls.append((query, batch))


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    @asynccontextmanager
    async def connection(self):
        yield self._conn


@pytest.mark.asyncio()
async def test_audit_logger_db_flush(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "AUDIT_LOG_SINK", "db")

    conn = _FakeConn()
    pool = _FakePool(conn)
    logger = AuthAuditLogger(db_enabled=True, db_pool=pool)

    logger.log_event(
        event_type="login_success",
        user_id="user-1",
        session_id="session-12345678",
        client_ip="127.0.0.1",
        user_agent="ua",
        auth_type="dev",
        outcome="success",
    )

    await logger._flush_to_db()

    assert len(conn.calls) == 1
    _, batch = conn.calls[0]
    assert batch[0][1] == "login_success"
    assert batch[0][2] == "user-1"
    assert batch[0][3] == "session-"[:8]
