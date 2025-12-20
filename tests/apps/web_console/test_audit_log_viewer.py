"""Tests for audit log viewer component."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from apps.web_console.components import audit_log_viewer as viewer
from libs.web_console_auth.gateway_auth import AuthenticatedUser
from libs.web_console_auth.permissions import Role


class _FakeCursor:
    def __init__(self, rows: list[Any]):
        self.rows = rows

    async def fetchall(self) -> list[Any]:
        return self.rows

    async def fetchone(self) -> Any:
        return self.rows[0] if self.rows else None


class _FakeConn:
    def __init__(self, rows: list[Any], count: int = 0) -> None:
        self.rows = rows
        self.count = count
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, params: tuple[Any, ...]) -> _FakeCursor:
        self.executed.append((query, params))
        if "COUNT" in query:
            return _FakeCursor([{"count": self.count}])
        return _FakeCursor(self.rows)


class _FakeAsyncCM:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self.conn

    async def __aexit__(self, *_args: Any) -> None:
        return None


class _StubStreamlit:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, msg: str, **_kwargs: Any) -> None:
        self.errors.append(msg)


@pytest.fixture()
def admin_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id="admin",
        role=Role.ADMIN,
        strategies=[],
        session_version=1,
        request_id="req-1",
    )


@pytest.mark.asyncio()
async def test_fetch_logs_without_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {
            "id": 1,
            "timestamp": datetime(2025, 12, 19, 12, 0, tzinfo=UTC),
            "user_id": "admin",
            "action": "login",
            "event_type": "auth",
            "resource_type": "session",
            "resource_id": "sess-1",
            "outcome": "success",
            "details": {"email": "admin@example.com"},
        }
    ]
    conn = _FakeConn(rows, count=1)
    monkeypatch.setattr(viewer, "acquire_connection", lambda _db: _FakeAsyncCM(conn))

    filters = viewer.AuditFilters(None, None, None, None, None, None)
    logs, total = await viewer._fetch_audit_logs(
        db_pool=object(), filters=filters, limit=50, offset=0
    )

    assert total == 1
    assert logs[0]["user_id"] == "admin"
    assert conn.executed[0][1][-2:] == (50, 0)


@pytest.mark.asyncio()
async def test_fetch_logs_with_user_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeConn([], count=0)
    monkeypatch.setattr(viewer, "acquire_connection", lambda _db: _FakeAsyncCM(conn))

    filters = viewer.AuditFilters("user-123", None, None, None, None, None)
    await viewer._fetch_audit_logs(db_pool=object(), filters=filters, limit=50, offset=0)

    first_params = conn.executed[0][1]
    assert first_params[0] == "user-123"
    assert first_params[1] == "user-123"


@pytest.mark.asyncio()
async def test_fetch_logs_with_date_range(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeConn([], count=0)
    monkeypatch.setattr(viewer, "acquire_connection", lambda _db: _FakeAsyncCM(conn))

    start = datetime(2025, 12, 1, tzinfo=UTC)
    end = datetime(2025, 12, 2, tzinfo=UTC)
    filters = viewer.AuditFilters(None, None, None, None, start, end)
    await viewer._fetch_audit_logs(db_pool=object(), filters=filters, limit=50, offset=0)

    params = conn.executed[0][1]
    assert params[8] == start
    assert params[10] == end


def test_offset_calculation() -> None:
    assert viewer._offset_for_page(0) == 0
    assert viewer._offset_for_page(2) == 100
    assert viewer._offset_for_page(-1) == 0


@pytest.mark.asyncio()
async def test_pii_masking_in_details(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        (
            1,
            datetime(2025, 12, 19, 12, 0, tzinfo=UTC),
            "u1",
            "login",
            "auth",
            "session",
            "sess-1",
            "success",
            {"email": "user@example.com", "phone": "+1 5551234567"},
        )
    ]
    conn = _FakeConn(rows, count=1)
    monkeypatch.setattr(viewer, "acquire_connection", lambda _db: _FakeAsyncCM(conn))

    filters = viewer.AuditFilters(None, None, None, None, None, None)
    logs, _ = await viewer._fetch_audit_logs(db_pool=object(), filters=filters, limit=50, offset=0)

    details = logs[0]["details"]
    assert details["email"] == "***@example.com"
    assert details["phone"] == "***4567"


def test_rbac_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    viewer_st_stub = _StubStreamlit()
    monkeypatch.setattr(viewer, "st", viewer_st_stub)

    viewer.render_audit_log_viewer(
        user=AuthenticatedUser(
            user_id="viewer",
            role=Role.VIEWER,
            strategies=[],
            session_version=1,
            request_id="req-1",
        ),
        db_pool=object(),
    )

    assert "Permission denied" in viewer_st_stub.errors[0]
