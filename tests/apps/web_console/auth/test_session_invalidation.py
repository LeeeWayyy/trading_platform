import pytest

from apps.web_console.auth.session_invalidation import (
    SessionInvalidationError,
    invalidate_user_sessions,
    validate_session_version,
)


class FakeConn:
    def __init__(self, version: int = 1):
        self.version = version
        self.has_row = False

    async def fetchrow(self, query, *args):
        if "UPDATE" in query:
            self.version += 1
            self.has_row = True
            return {"session_version": self.version}
        if "SELECT" in query:
            if not self.has_row:
                return None
            return {"session_version": self.version}
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self):
        self.conn = FakeConn()

    async def acquire(self):
        return self.conn


class DummyAudit:
    def __init__(self):
        self.logged = False

    async def log_admin_change(self, **kwargs):
        self.logged = True


class FailingConn(FakeConn):
    async def fetchrow(self, query, *args):
        raise RuntimeError("db down")


class FailingPool(FakePool):
    def __init__(self):
        self.conn = FailingConn()


class NoRowConn(FakeConn):
    async def fetchrow(self, query, *args):
        return None


class NoRowPool(FakePool):
    def __init__(self):
        self.conn = NoRowConn()


@pytest.mark.asyncio
async def test_invalidate_user_sessions_increments_and_logs():
    pool = FakePool()
    audit = DummyAudit()

    new_version = await invalidate_user_sessions("user", pool, audit_logger=audit, admin_user_id="admin")
    assert new_version == 2
    assert audit.logged


@pytest.mark.asyncio
async def test_validate_session_version_matches():
    pool = FakePool()
    assert await validate_session_version("user", 1, pool) is False
    # After increment version stored 1->2 above, still mismatch
    await invalidate_user_sessions("user", pool)
    assert await validate_session_version("user", pool.conn.version, pool)


@pytest.mark.asyncio
async def test_invalidate_user_sessions_raises_on_db_failure():
    pool = FailingPool()

    with pytest.raises(RuntimeError):
        await invalidate_user_sessions("user", pool)


@pytest.mark.asyncio
async def test_invalidate_user_sessions_errors_when_no_rows_updated():
    pool = NoRowPool()

    with pytest.raises(SessionInvalidationError):
        await invalidate_user_sessions("missing-user", pool)
