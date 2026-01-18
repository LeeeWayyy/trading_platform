import json
import logging
import uuid
from unittest.mock import AsyncMock

import pytest

from apps.web_console_ng import config
from apps.web_console_ng.auth import audit as audit_module
from apps.web_console_ng.auth.audit import AuthAuditLogger, _AUDIT_REQUEST_ID_NAMESPACE


class _AcquireContext:
    def __init__(self, conn, enter_exc: Exception | None = None) -> None:
        self._conn = conn
        self._enter_exc = enter_exc

    async def __aenter__(self):
        if self._enter_exc is not None:
            raise self._enter_exc
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.fixture(autouse=True)
def _reset_singleton():
    AuthAuditLogger._instance = None


@pytest.fixture()
def _audit_sink_db(monkeypatch):
    monkeypatch.setattr(config, "AUDIT_LOG_SINK", "db")


def _build_logger(db_pool: object | None = object()) -> AuthAuditLogger:
    logger = AuthAuditLogger(db_enabled=True, db_pool=db_pool)
    return logger


def test_log_event_invalid_request_id_includes_raw(_audit_sink_db):
    logger = _build_logger()

    request_id = "not-a-uuid"
    logger.log_event(
        event_type="login",
        user_id=None,
        session_id="abcdefgh1234",
        client_ip="10.0.0.1",
        user_agent="agent",
        auth_type="password",
        outcome="success",
        request_id=request_id,
    )

    assert len(logger._queue) == 1
    payload, attempts = logger._queue[0]
    assert attempts == 0
    assert payload[1] == "login"
    assert payload[2] == "anonymous"
    assert payload[3] == "abcdefgh"

    expected_uuid = uuid.uuid5(_AUDIT_REQUEST_ID_NAMESPACE, request_id)
    assert payload[9] == expected_uuid

    extra_data = json.loads(payload[10])
    assert extra_data["request_id_raw"] == request_id


@pytest.mark.asyncio()
async def test_flush_to_db_writes_payloads(monkeypatch, _audit_sink_db):
    conn = AsyncMock()
    conn.executemany = AsyncMock()

    def fake_acquire(_pool):
        return _AcquireContext(conn)

    monkeypatch.setattr(audit_module, "acquire_connection", fake_acquire)

    logger = _build_logger(db_pool=object())
    logger.log_event(
        event_type="logout",
        user_id="user-1",
        session_id="session-123",
        client_ip="127.0.0.1",
        user_agent="agent",
        auth_type="sso",
        outcome="success",
    )

    await logger._flush_to_db()

    conn.executemany.assert_awaited_once()
    assert len(logger._queue) == 0


@pytest.mark.asyncio()
async def test_flush_to_db_failure_dead_letters(monkeypatch, _audit_sink_db):
    def fake_acquire(_pool):
        return _AcquireContext(None, enter_exc=RuntimeError("boom"))

    monkeypatch.setattr(audit_module, "acquire_connection", fake_acquire)

    logger = _build_logger(db_pool=object())
    logger._max_retries = 1

    logger.log_event(
        event_type="login",
        user_id="user-1",
        session_id="session-123",
        client_ip="127.0.0.1",
        user_agent="agent",
        auth_type="password",
        outcome="failure",
    )

    await logger._flush_to_db()

    assert len(logger._queue) == 0
    assert logger._dead_letter_count == 1
    assert len(logger._dead_letter) == 1


@pytest.mark.asyncio()
async def test_start_disables_db_without_pool(caplog, _audit_sink_db):
    logger = _build_logger(db_pool=None)

    with caplog.at_level(logging.WARNING, logger="audit.auth"):
        await logger.start()

    assert logger._db_enabled is False
    assert logger._flush_task is None
    assert any("audit_db_pool_unavailable_startup" in record.message for record in caplog.records)
