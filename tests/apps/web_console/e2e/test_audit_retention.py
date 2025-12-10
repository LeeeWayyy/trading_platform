import pytest

from apps.web_console.auth.audit_log import AuditLogger
from apps.web_console.tasks.audit_cleanup import run_audit_cleanup


class FakeConn:
    async def execute(self, *args, **kwargs):
        return "DELETE 5"

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self):
        self.conn = FakeConn()

    async def acquire(self):
        return self.conn


@pytest.mark.asyncio
async def test_audit_cleanup_updates_metrics():
    pool = FakePool()
    logger = AuditLogger(pool)
    deleted = await run_audit_cleanup(logger)
    assert deleted == 5
