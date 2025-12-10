from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from apps.web_console.auth.session_manager import validate_session


class FakeConn:
    def __init__(self, stored_version: int):
        self.stored_version = stored_version

    async def fetchrow(self, query, *args):
        return {"session_version": self.stored_version}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self, stored_version: int):
        self.conn = FakeConn(stored_version)

    async def acquire(self):
        return self.conn


class FakeSessionStore:
    def __init__(self, session):
        self.session = session
        self.deleted = False

    async def get_session(self, *args, **kwargs):
        return self.session

    async def delete_session(self, session_id):
        self.deleted = True


def _make_session(session_version: int = 1):
    now = datetime.now(UTC)
    return SimpleNamespace(
        user_id="user123",
        email="user@example.com",
        created_at=now,
        last_activity=now,
        access_token_expires_at=now + timedelta(hours=1),
        session_version=session_version,
        role="viewer",
        strategies=["alpha"],
    )


@pytest.mark.asyncio
async def test_validate_session_returns_role_and_strategies():
    session_data = _make_session(session_version=3)
    store = FakeSessionStore(session_data)
    pool = FakePool(stored_version=3)

    result = await validate_session(
        session_id="sess",
        session_store=store,
        client_ip="1.1.1.1",
        user_agent="ua",
        db_pool=pool,
    )

    assert result is not None
    assert result["role"] == "viewer"
    assert result["strategies"] == ["alpha"]
    assert result["session_version"] == 3


@pytest.mark.asyncio
async def test_validate_session_rejects_on_session_version_mismatch():
    session_data = _make_session(session_version=1)
    store = FakeSessionStore(session_data)
    pool = FakePool(stored_version=2)  # Simulate session_version bump in DB

    result = await validate_session(
        session_id="sess",
        session_store=store,
        client_ip="1.1.1.1",
        user_agent="ua",
        db_pool=pool,
    )

    assert result is None
    assert store.deleted is True


@pytest.mark.asyncio
async def test_validate_session_fails_closed_when_db_unavailable(monkeypatch):
    session_data = _make_session(session_version=1)
    store = FakeSessionStore(session_data)

    # Simulate DB pool retrieval failure
    monkeypatch.setattr(
        "apps.web_console.auth.session_manager._maybe_get_db_pool", lambda: None
    )

    result = await validate_session(
        session_id="sess",
        session_store=store,
        client_ip="1.1.1.1",
        user_agent="ua",
        db_pool=None,
    )

    assert result is None
    assert store.deleted is True
