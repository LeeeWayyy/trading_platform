import pytest

from apps.web_console.auth.audit_log import AuditLogger


class FakeConn:
    """Fake connection mimicking psycopg3 AsyncConnection."""

    def __init__(self):
        self.executed = []

    async def execute(self, query, params=None):
        # psycopg3 pattern: execute(query, params_tuple) returning cursor with rowcount
        self.executed.append((query.strip(), params or ()))
        return FakeCursor(rowcount=2 if query.strip().lower().startswith("delete") else 0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def transaction(self):
        return self


class FakeCursor:
    """Fake cursor exposing rowcount like psycopg3 AsyncCursor."""

    def __init__(self, rowcount: int = 0):
        self.rowcount = rowcount


class FakeAsyncContextManager:
    """Async context manager for connection()."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_args):
        return None


class FakePool:
    """Fake pool mimicking psycopg_pool.AsyncConnectionPool."""

    def __init__(self):
        self.conn = FakeConn()

    def connection(self):
        """Return async context manager like psycopg_pool."""
        return FakeAsyncContextManager(self.conn)


@pytest.mark.asyncio
async def test_log_action_writes_event():
    pool = FakePool()
    logger = AuditLogger(db_pool=pool)

    await logger.log_action(
        user_id="user", action="flatten_all", resource_type="order", resource_id="123", outcome="success"
    )

    assert logger  # ensure no exception
    assert pool.conn.executed


@pytest.mark.asyncio
async def test_cleanup_old_events_returns_count():
    pool = FakePool()
    logger = AuditLogger(db_pool=pool, retention_days=1)

    deleted = await logger.cleanup_old_events()
    assert isinstance(deleted, int)


@pytest.mark.asyncio
async def test_log_admin_change_persists_event():
    pool = FakePool()
    logger = AuditLogger(db_pool=pool)

    await logger.log_admin_change(
        admin_user_id="admin",
        action="invalidate_sessions",
        target_user_id="user123",
        details={"new_session_version": 4},
    )

    assert pool.conn.executed, "Expected audit log write to persist"
    query, params = pool.conn.executed[-1]
    assert "INSERT INTO audit_log" in query
    # params is tuple: (user_id, action, details, event_type, resource_type, resource_id, outcome, amr_method)
    assert params[0] == "admin"  # user_id
    assert params[1] == "invalidate_sessions"  # action
    assert params[3] == "admin"  # event_type
