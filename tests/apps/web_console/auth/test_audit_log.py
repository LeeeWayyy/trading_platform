import pytest

from apps.web_console.auth.audit_log import AuditLogger


class FakeConn:
    def __init__(self):
        self.executed = []

    async def execute(self, query, *args):
        self.executed.append((query.strip(), args))
        return "DELETE 2"

    async def fetchrow(self, query, *args):
        self.executed.append((query.strip(), args))
        return {"session_version": 2}

    async def fetch(self, query, *args):
        self.executed.append((query.strip(), args))
        return []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def transaction(self):
        return self

    async def cursor(self, *args, **kwargs):
        for _ in []:
            yield _


class FakePool:
    def __init__(self):
        self.conn = FakeConn()

    async def acquire(self):
        return self.conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


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
    query, args = pool.conn.executed[-1]
    assert "INSERT INTO audit_log" in query
    # user_id, action, details, event_type, resource_type, resource_id, outcome, amr_method
    assert args[0] == "admin"
    assert args[1] == "invalidate_sessions"
    assert args[3] == "admin"
