"""Tests for scheduled reports service."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from apps.web_console.services.scheduled_reports_service import ScheduledReportsService
from libs.web_console_auth.permissions import Role


def make_user(user_id: str, role: Role | str) -> dict:
    """Create a user dict for permission checks."""
    return {"user_id": user_id, "role": role}


class MockAsyncCursor:
    """Mock async cursor for psycopg-style usage."""

    def __init__(self, *, rows=None, row=None, rowcount=0):
        self._rows = rows or []
        self._row = row
        self.rowcount = rowcount

    async def execute(self, *_args, **_kwargs):
        return None

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._row


class MockAsyncCursorCM:
    """Async context manager for cursor."""

    def __init__(self, cursor: MockAsyncCursor):
        self._cursor = cursor

    async def __aenter__(self):
        return self._cursor

    async def __aexit__(self, *_args):
        return None


class MockAsyncConnection:
    """Mock async connection."""

    def __init__(self, cursor: MockAsyncCursor):
        self._cursor = cursor
        self.commit = AsyncMock()

    def cursor(self, *, row_factory=None):
        # row_factory is accepted but ignored - mock always returns dicts
        return MockAsyncCursorCM(self._cursor)


class MockAsyncPool:
    """Mock pool with async connection context manager."""

    def __init__(self, conn: MockAsyncConnection):
        self._conn = conn

    def connection(self):
        conn = self._conn

        class _ConnCM:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *_args):
                return None

        return _ConnCM()


@pytest.mark.asyncio()
async def test_list_schedules_returns_entries() -> None:
    """list_schedules should return ReportSchedule entries."""
    now = datetime.now(UTC)
    rows = [
        {
            "id": "schedule-1",
            "user_id": "user-1",
            "name": "Daily Report",
            "template_type": "daily_summary",
            "schedule_config": {"cron": "0 6 * * *", "params": {"foo": "bar"}},
            "recipients": [],
            "strategies": [],
            "enabled": True,
            "last_run_at": None,
            "next_run_at": None,
            "created_at": now,
            "updated_at": now,
        }
    ]

    cursor = MockAsyncCursor(rows=rows)
    conn = MockAsyncConnection(cursor)
    pool = MockAsyncPool(conn)

    service = ScheduledReportsService(db_pool=pool, user=make_user("user-1", Role.VIEWER))

    schedules = await service.list_schedules()

    assert len(schedules) == 1
    assert schedules[0].name == "Daily Report"
    assert schedules[0].cron == "0 6 * * *"


@pytest.mark.asyncio()
async def test_create_schedule_with_mock_db() -> None:
    """create_schedule should insert and return schedule."""
    now = datetime.now(UTC)
    row = {
        "id": "schedule-2",
        "user_id": "user-2",
        "name": "Weekly",
        "template_type": "weekly_performance",
        "schedule_config": {"cron": "0 7 * * 1", "params": {}},
        "recipients": [],
        "strategies": [],
        "enabled": True,
        "last_run_at": None,
        "next_run_at": None,
        "created_at": now,
        "updated_at": now,
    }

    cursor = MockAsyncCursor(row=row)
    conn = MockAsyncConnection(cursor)
    pool = MockAsyncPool(conn)

    service = ScheduledReportsService(db_pool=pool, user=make_user("user-2", Role.ADMIN))

    schedule = await service.create_schedule(
        name="Weekly",
        report_type="weekly_performance",
        cron="0 7 * * 1",
        params={},
        user_id="user-2",
    )

    assert schedule.id == "schedule-2"
    assert schedule.report_type == "weekly_performance"
    conn.commit.assert_awaited()


@pytest.mark.asyncio()
async def test_permission_denied_without_view_reports() -> None:
    """list_schedules should raise without VIEW_REPORTS permission."""
    cursor = MockAsyncCursor(rows=[])
    conn = MockAsyncConnection(cursor)
    pool = MockAsyncPool(conn)

    service = ScheduledReportsService(db_pool=pool, user=make_user("user-3", "unknown"))

    with pytest.raises(PermissionError):
        await service.list_schedules()


@pytest.mark.asyncio()
async def test_permission_denied_without_manage_reports() -> None:
    """create_schedule should raise without MANAGE_REPORTS permission."""
    cursor = MockAsyncCursor(row=None)
    conn = MockAsyncConnection(cursor)
    pool = MockAsyncPool(conn)

    service = ScheduledReportsService(db_pool=pool, user=make_user("user-4", "unknown"))

    with pytest.raises(PermissionError):
        await service.create_schedule(
            name="Daily",
            report_type="daily_summary",
            cron="0 6 * * *",
            params={},
            user_id="user-4",
        )


@pytest.mark.asyncio()
async def test_update_schedule_success() -> None:
    """update_schedule should update and commit."""
    now = datetime.now(UTC)
    row = {
        "id": "schedule-5",
        "user_id": "user-5",
        "name": "Updated Name",
        "template_type": "daily_summary",
        "schedule_config": {"cron": "0 8 * * *", "params": {}},
        "recipients": [],
        "strategies": [],
        "enabled": True,
        "last_run_at": None,
        "next_run_at": None,
        "created_at": now,
        "updated_at": now,
    }

    cursor = MockAsyncCursor(row=row)
    conn = MockAsyncConnection(cursor)
    pool = MockAsyncPool(conn)

    service = ScheduledReportsService(db_pool=pool, user=make_user("user-5", Role.ADMIN))

    schedule = await service.update_schedule("schedule-5", {"name": "Updated Name"})

    assert schedule.name == "Updated Name"
    conn.commit.assert_awaited()


@pytest.mark.asyncio()
async def test_delete_schedule_success() -> None:
    """delete_schedule should delete and commit."""
    cursor = MockAsyncCursor(rowcount=1)
    conn = MockAsyncConnection(cursor)
    pool = MockAsyncPool(conn)

    service = ScheduledReportsService(db_pool=pool, user=make_user("user-6", Role.ADMIN))

    result = await service.delete_schedule("schedule-6")

    assert result is True
    conn.commit.assert_awaited()


@pytest.mark.asyncio()
async def test_permission_denied_for_update_schedule() -> None:
    """update_schedule should raise without MANAGE_REPORTS permission."""
    cursor = MockAsyncCursor(row=None)
    conn = MockAsyncConnection(cursor)
    pool = MockAsyncPool(conn)

    service = ScheduledReportsService(db_pool=pool, user=make_user("user-7", "unknown"))

    with pytest.raises(PermissionError):
        await service.update_schedule("schedule-7", {"name": "New Name"})


@pytest.mark.asyncio()
async def test_permission_denied_for_delete_schedule() -> None:
    """delete_schedule should raise without MANAGE_REPORTS permission."""
    cursor = MockAsyncCursor(rowcount=0)
    conn = MockAsyncConnection(cursor)
    pool = MockAsyncPool(conn)

    service = ScheduledReportsService(db_pool=pool, user=make_user("user-8", "unknown"))

    with pytest.raises(PermissionError):
        await service.delete_schedule("schedule-8")


@pytest.mark.asyncio()
async def test_list_schedules_all_users_requires_manage_reports() -> None:
    """list_schedules with all_users=True requires MANAGE_REPORTS permission."""
    cursor = MockAsyncCursor(rows=[])
    conn = MockAsyncConnection(cursor)
    pool = MockAsyncPool(conn)

    # VIEWER has VIEW_REPORTS but not MANAGE_REPORTS
    service = ScheduledReportsService(db_pool=pool, user=make_user("user-9", Role.VIEWER))

    with pytest.raises(PermissionError):
        await service.list_schedules(all_users=True)


@pytest.mark.asyncio()
async def test_list_schedules_other_user_requires_manage_reports() -> None:
    """list_schedules with different user_id requires MANAGE_REPORTS permission."""
    cursor = MockAsyncCursor(rows=[])
    conn = MockAsyncConnection(cursor)
    pool = MockAsyncPool(conn)

    # VIEWER has VIEW_REPORTS but not MANAGE_REPORTS
    service = ScheduledReportsService(db_pool=pool, user=make_user("user-10", Role.VIEWER))

    with pytest.raises(PermissionError):
        await service.list_schedules(user_id="other-user")


@pytest.mark.asyncio()
async def test_list_schedules_all_users_succeeds_for_admin() -> None:
    """list_schedules with all_users=True succeeds for ADMIN."""
    now = datetime.now(UTC)
    rows = [
        {
            "id": "schedule-11",
            "user_id": "user-a",
            "name": "Admin Report",
            "template_type": "daily_summary",
            "schedule_config": {"cron": "0 6 * * *", "params": {}},
            "recipients": [],
            "strategies": [],
            "enabled": True,
            "last_run_at": None,
            "next_run_at": None,
            "created_at": now,
            "updated_at": now,
        }
    ]

    cursor = MockAsyncCursor(rows=rows)
    conn = MockAsyncConnection(cursor)
    pool = MockAsyncPool(conn)

    service = ScheduledReportsService(db_pool=pool, user=make_user("admin-1", Role.ADMIN))

    schedules = await service.list_schedules(all_users=True)

    assert len(schedules) == 1
    assert schedules[0].name == "Admin Report"


@pytest.mark.asyncio()
async def test_download_archive_returns_none_without_user_id() -> None:
    """download_archive returns None when user context lacks user_id."""
    cursor = MockAsyncCursor(row=None)
    conn = MockAsyncConnection(cursor)
    pool = MockAsyncPool(conn)

    # User dict without user_id
    service = ScheduledReportsService(db_pool=pool, user={"role": Role.ADMIN})

    result = await service.download_archive("run-123")

    assert result is None


@pytest.mark.asyncio()
async def test_list_schedules_requires_user_id_in_context() -> None:
    """list_schedules raises PermissionError when user_id missing from context."""
    cursor = MockAsyncCursor(rows=[])
    conn = MockAsyncConnection(cursor)
    pool = MockAsyncPool(conn)

    # User dict without user_id (but with VIEW_REPORTS role)
    service = ScheduledReportsService(db_pool=pool, user={"role": Role.VIEWER})

    with pytest.raises(PermissionError):
        await service.list_schedules()


@pytest.mark.asyncio()
async def test_get_run_history_returns_entries_for_owner() -> None:
    """get_run_history returns run entries for schedule owner."""
    now = datetime.now(UTC)

    # First query returns schedule ownership, second returns run history
    schedule_row = {"user_id": "user-owner"}
    run_rows = [
        {
            "id": "run-1",
            "schedule_id": "schedule-owner",
            "run_key": "key-1",
            "status": "completed",
            "started_at": now,
            "completed_at": now,
            "error_message": None,
        }
    ]

    class MockCursorMultiQuery:
        """Mock cursor that returns different results for each query."""

        def __init__(self):
            self._call_count = 0

        async def execute(self, *_args, **_kwargs):
            return None

        async def fetchone(self):
            self._call_count += 1
            if self._call_count == 1:
                return schedule_row
            return None

        async def fetchall(self):
            return run_rows

    cursor = MockCursorMultiQuery()
    conn = MockAsyncConnection(MockAsyncCursor())
    conn._cursor = cursor

    # Override cursor method to return our multi-query cursor
    def cursor_cm(*, row_factory=None):
        class _CM:
            async def __aenter__(self):
                return cursor

            async def __aexit__(self, *_args):
                return None

        return _CM()

    conn.cursor = cursor_cm
    pool = MockAsyncPool(conn)

    service = ScheduledReportsService(
        db_pool=pool, user=make_user("user-owner", Role.VIEWER)
    )

    runs = await service.get_run_history("schedule-owner")

    assert len(runs) == 1
    assert runs[0].run_key == "key-1"
    assert runs[0].status == "completed"


@pytest.mark.asyncio()
async def test_get_run_history_returns_empty_for_other_users_schedule() -> None:
    """get_run_history returns empty list for other user's schedule (no oracle leak)."""
    # Non-admin user won't find the schedule because query is scoped to their user_id
    # This prevents existence oracle attack - same response for non-existent and
    # other user's schedules

    class MockCursorNoMatch:
        """Mock cursor that returns no match (schedule not found for user)."""

        async def execute(self, *_args, **_kwargs):
            return None

        async def fetchone(self):
            return None  # Schedule not found for this user

        async def fetchall(self):
            return []

    cursor = MockCursorNoMatch()
    conn = MockAsyncConnection(MockAsyncCursor())

    def cursor_cm(*, row_factory=None):
        class _CM:
            async def __aenter__(self):
                return cursor

            async def __aexit__(self, *_args):
                return None

        return _CM()

    conn.cursor = cursor_cm
    pool = MockAsyncPool(conn)

    # VIEWER has VIEW_REPORTS but not MANAGE_REPORTS
    service = ScheduledReportsService(
        db_pool=pool, user=make_user("requesting-user", Role.VIEWER)
    )

    # Returns empty list instead of PermissionError to prevent oracle attack
    result = await service.get_run_history("other-users-schedule")
    assert result == []
