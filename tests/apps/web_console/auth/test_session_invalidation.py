import pytest

from apps.web_console.auth.session_invalidation import (
    SessionInvalidationError,
    invalidate_user_sessions,
    validate_session_version,
)


class FakeCursor:
    """Fake cursor that mimics psycopg3 AsyncCursor."""

    def __init__(self, row=None):
        self._row = row

    async def fetchone(self):
        return self._row


class FakeConn:
    """Fake connection that mimics psycopg3 AsyncConnection."""

    def __init__(self, version: int = 1):
        self.version = version
        self.has_row = False

    async def execute(self, query, params=None):
        if "UPDATE" in query:
            self.version += 1
            self.has_row = True
            return FakeCursor(row=(self.version,))
        if "SELECT" in query:
            if not self.has_row:
                return FakeCursor(row=None)
            return FakeCursor(row=(self.version,))
        return FakeCursor(row=None)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeAsyncContextManager:
    """Async context manager for connection()."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_args):
        return None


class FakePool:
    """Fake pool that mimics psycopg_pool.AsyncConnectionPool."""

    def __init__(self):
        self.conn = FakeConn()

    def connection(self):
        """Return async context manager like psycopg_pool."""
        return FakeAsyncContextManager(self.conn)


class DummyAudit:
    def __init__(self):
        self.logged = False

    async def log_admin_change(self, **kwargs):
        self.logged = True


class FailingConn(FakeConn):
    async def execute(self, query, params=None):
        raise RuntimeError("db down")


class FailingPool:
    """Pool that simulates DB failure."""

    def __init__(self):
        self.conn = FailingConn()

    def connection(self):
        return FakeAsyncContextManager(self.conn)


class NoRowConn(FakeConn):
    async def execute(self, query, params=None):
        return FakeCursor(row=None)


class NoRowPool:
    """Pool that simulates no rows found."""

    def __init__(self):
        self.conn = NoRowConn()

    def connection(self):
        return FakeAsyncContextManager(self.conn)


@pytest.mark.asyncio()
async def test_invalidate_user_sessions_increments_and_logs():
    pool = FakePool()
    audit = DummyAudit()

    new_version = await invalidate_user_sessions(
        "user", pool, audit_logger=audit, admin_user_id="admin"
    )
    assert new_version == 2
    assert audit.logged


@pytest.mark.asyncio()
async def test_validate_session_version_matches():
    pool = FakePool()
    assert await validate_session_version("user", 1, pool) is False
    # After increment version stored 1->2 above, still mismatch
    await invalidate_user_sessions("user", pool)
    assert await validate_session_version("user", pool.conn.version, pool)


@pytest.mark.asyncio()
async def test_invalidate_user_sessions_raises_on_db_failure():
    pool = FailingPool()

    with pytest.raises(RuntimeError):
        await invalidate_user_sessions("user", pool)


@pytest.mark.asyncio()
async def test_invalidate_user_sessions_errors_when_no_rows_updated():
    pool = NoRowPool()

    with pytest.raises(SessionInvalidationError):
        await invalidate_user_sessions("missing-user", pool)
