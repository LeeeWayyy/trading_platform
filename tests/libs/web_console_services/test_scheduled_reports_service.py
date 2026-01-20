"""Unit tests for libs.web_console_services.scheduled_reports_service."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from libs.platform.web_console_auth.permissions import Permission
from libs.web_console_services.scheduled_reports_service import (
    ReportRun,
    ScheduledReportsService,
)


@pytest.fixture()
def mock_db_pool() -> Mock:
    return Mock()


@pytest.fixture()
def viewer_user() -> dict[str, Any]:
    return {"user_id": "viewer-1", "role": "viewer"}


@pytest.fixture()
def admin_user() -> dict[str, Any]:
    return {"user_id": "admin-1", "role": "admin"}


class AsyncContextManager:
    """Helper to create proper async context managers for mocking."""

    def __init__(self, return_value: Any) -> None:
        self._return_value = return_value

    async def __aenter__(self) -> Any:
        return self._return_value

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


def _mock_acquire_connection(mock_conn: AsyncMock) -> AsyncContextManager:
    """Create an async context manager that returns mock_conn."""
    return AsyncContextManager(mock_conn)


def _mock_cursor_conn(mock_cursor: AsyncMock) -> Mock:
    """Create a mock connection with proper cursor async context manager."""
    mock_conn = Mock()
    mock_conn.cursor.return_value = AsyncContextManager(mock_cursor)
    mock_conn.commit = AsyncMock()  # conn.commit() is awaited
    return mock_conn


class TestNormalizeHelpers:
    def test_normalize_config(self, mock_db_pool: Mock, viewer_user: dict[str, Any]) -> None:
        service = ScheduledReportsService(mock_db_pool, viewer_user)
        assert service._normalize_config(None) == {}
        assert service._normalize_config("not-json") == {}
        assert service._normalize_config({"cron": "* * * * *"}) == {"cron": "* * * * *"}
        assert service._normalize_config('{"cron": "* * * * *"}') == {"cron": "* * * * *"}

    def test_parse_json_list(self, mock_db_pool: Mock, viewer_user: dict[str, Any]) -> None:
        service = ScheduledReportsService(mock_db_pool, viewer_user)
        assert service._parse_json_list(None) == []
        assert service._parse_json_list(["a"]) == ["a"]
        assert service._parse_json_list('["x", "y"]') == ["x", "y"]
        assert service._parse_json_list("not-json") == []


class TestListSchedules:
    @pytest.mark.asyncio()
    async def test_list_schedules_requires_user_context(self, mock_db_pool: Mock) -> None:
        service = ScheduledReportsService(mock_db_pool, {"role": "viewer"})

        with patch(
            "libs.web_console_services.scheduled_reports_service.has_permission",
            return_value=True,
        ):
            with pytest.raises(PermissionError, match="User context required"):
                await service.list_schedules()

    @pytest.mark.asyncio()
    async def test_list_schedules_requires_manage_for_all_users(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        service = ScheduledReportsService(mock_db_pool, viewer_user)

        def _perm(user: dict[str, Any], permission: Permission) -> bool:
            return permission == Permission.VIEW_REPORTS

        with patch(
            "libs.web_console_services.scheduled_reports_service.has_permission",
            side_effect=_perm,
        ):
            with pytest.raises(PermissionError, match="manage_reports"):
                await service.list_schedules(all_users=True)

    @pytest.mark.asyncio()
    async def test_list_schedules_returns_rows(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        service = ScheduledReportsService(mock_db_pool, viewer_user)

        row = {
            "id": "sched-1",
            "user_id": "viewer-1",
            "name": "Daily",
            "template_type": "positions",
            "schedule_config": json.dumps({"cron": "0 0 * * *", "params": {}}),
            "recipients": json.dumps(["a@example.com"]),
            "strategies": json.dumps(["s1"]),
            "enabled": True,
            "last_run_at": None,
            "next_run_at": None,
            "created_at": datetime(2024, 1, 1, tzinfo=UTC),
            "updated_at": datetime(2024, 1, 1, tzinfo=UTC),
        }
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=[row])
        mock_conn = _mock_cursor_conn(mock_cursor)

        with (
            patch(
                "libs.web_console_services.scheduled_reports_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.scheduled_reports_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
        ):
            schedules = await service.list_schedules()

        assert len(schedules) == 1
        assert schedules[0].id == "sched-1"
        assert schedules[0].cron == "0 0 * * *"


class TestCreateUpdateDelete:
    @pytest.mark.asyncio()
    async def test_create_schedule(self, mock_db_pool: Mock, admin_user: dict[str, Any]) -> None:
        service = ScheduledReportsService(mock_db_pool, admin_user)

        row = {
            "id": "sched-1",
            "user_id": "admin-1",
            "name": "Daily",
            "template_type": "positions",
            "schedule_config": json.dumps({"cron": "0 0 * * *", "params": {}}),
            "recipients": json.dumps([]),
            "strategies": json.dumps([]),
            "enabled": True,
            "last_run_at": None,
            "next_run_at": None,
            "created_at": datetime(2024, 1, 1, tzinfo=UTC),
            "updated_at": datetime(2024, 1, 1, tzinfo=UTC),
        }
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=row)
        mock_conn = _mock_cursor_conn(mock_cursor)

        with (
            patch(
                "libs.web_console_services.scheduled_reports_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.scheduled_reports_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
        ):
            schedule = await service.create_schedule(
                name="Daily",
                report_type="positions",
                cron="0 0 * * *",
                params={},
                user_id="admin-1",
            )

        assert schedule.name == "Daily"
        assert schedule.report_type == "positions"

    @pytest.mark.asyncio()
    async def test_update_schedule_merges_config(
        self, mock_db_pool: Mock, admin_user: dict[str, Any]
    ) -> None:
        service = ScheduledReportsService(mock_db_pool, admin_user)

        existing = {
            "id": "sched-1",
            "user_id": "admin-1",
            "name": "Daily",
            "template_type": "positions",
            "schedule_config": json.dumps({"cron": "0 0 * * *", "params": {"a": 1}}),
            "recipients": json.dumps([]),
            "strategies": json.dumps([]),
            "enabled": True,
            "last_run_at": None,
            "next_run_at": None,
            "created_at": datetime(2024, 1, 1, tzinfo=UTC),
            "updated_at": datetime(2024, 1, 1, tzinfo=UTC),
        }
        updated = dict(existing)
        updated["schedule_config"] = json.dumps({"cron": "5 0 * * *", "params": {"b": 2}})

        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(side_effect=[existing, updated])
        mock_conn = _mock_cursor_conn(mock_cursor)

        with (
            patch(
                "libs.web_console_services.scheduled_reports_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.scheduled_reports_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
        ):
            schedule = await service.update_schedule(
                "sched-1",
                updates={"cron": "5 0 * * *", "params": {"b": 2}},
            )

        assert schedule.cron == "5 0 * * *"
        assert schedule.params == {"b": 2}

    @pytest.mark.asyncio()
    async def test_delete_schedule(self, mock_db_pool: Mock, admin_user: dict[str, Any]) -> None:
        service = ScheduledReportsService(mock_db_pool, admin_user)

        mock_cursor = AsyncMock()
        mock_cursor.rowcount = 1
        mock_conn = _mock_cursor_conn(mock_cursor)

        with (
            patch(
                "libs.web_console_services.scheduled_reports_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.scheduled_reports_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
        ):
            deleted = await service.delete_schedule("sched-1")

        assert deleted is True


class TestRunHistory:
    @pytest.mark.asyncio()
    async def test_get_run_history_not_owned(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        service = ScheduledReportsService(mock_db_pool, viewer_user)

        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=None)
        mock_conn = _mock_cursor_conn(mock_cursor)

        with (
            patch(
                "libs.web_console_services.scheduled_reports_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.scheduled_reports_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
        ):
            runs = await service.get_run_history("sched-1")

        assert runs == []

    @pytest.mark.asyncio()
    async def test_get_run_history_returns_runs(
        self, mock_db_pool: Mock, admin_user: dict[str, Any]
    ) -> None:
        service = ScheduledReportsService(mock_db_pool, admin_user)

        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value={"user_id": "admin-1"})
        mock_cursor.fetchall = AsyncMock(
            return_value=[
                {
                    "id": "run-1",
                    "schedule_id": "sched-1",
                    "run_key": "rk",
                    "status": "completed",
                    "started_at": None,
                    "completed_at": None,
                    "error_message": None,
                    "file_format": "html",
                }
            ]
        )
        mock_conn = _mock_cursor_conn(mock_cursor)

        with (
            patch(
                "libs.web_console_services.scheduled_reports_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.scheduled_reports_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
        ):
            runs = await service.get_run_history("sched-1")

        assert len(runs) == 1
        assert runs[0].format == "html"


class TestTradingDataFetch:
    @pytest.mark.asyncio()
    async def test_fetch_trading_data_no_client(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        service = ScheduledReportsService(mock_db_pool, viewer_user)
        positions, fills, errors = await service._fetch_trading_data("u", None, None)
        assert positions == {}
        assert fills == {}
        assert "Trading client not configured" in errors[0]

    @pytest.mark.asyncio()
    async def test_fetch_trading_data_error(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        async def _raise() -> None:
            raise ConnectionError("down")

        client = AsyncMock()
        client.startup = AsyncMock(side_effect=_raise)

        service = ScheduledReportsService(
            mock_db_pool, viewer_user, trading_client_factory=lambda: client
        )
        positions, fills, errors = await service._fetch_trading_data("u", None, None)
        assert positions == {}
        assert fills == {}
        assert errors
        assert "Failed to fetch trading data" in errors[0]


class TestRunNow:
    @pytest.mark.asyncio()
    async def test_run_now_generates_archive(
        self, mock_db_pool: Mock, admin_user: dict[str, Any], tmp_path: Path
    ) -> None:
        service = ScheduledReportsService(mock_db_pool, admin_user)

        schedule_row = {
            "id": "sched-1",
            "user_id": "admin-1",
            "name": "Daily",
            "template_type": "positions",
            "schedule_config": json.dumps({"cron": "0 0 * * *", "params": {}}),
        }

        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=schedule_row)
        mock_conn = _mock_cursor_conn(mock_cursor)

        with (
            patch(
                "libs.web_console_services.scheduled_reports_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.scheduled_reports_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
            patch(
                "libs.web_console_services.scheduled_reports_service.uuid4",
                return_value=Mock(hex="abc123"),
            ),
            patch.dict(os.environ, {"REPORT_OUTPUT_DIR": str(tmp_path)}),
        ):
            report_run = await service.run_now("sched-1")

        assert isinstance(report_run, ReportRun)
        report_path = tmp_path / "report_manual-abc123.html"
        assert report_path.exists()


class TestDownloadArchive:
    @pytest.mark.asyncio()
    async def test_download_archive_valid_path(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any], tmp_path: Path
    ) -> None:
        service = ScheduledReportsService(mock_db_pool, viewer_user)
        file_path = tmp_path / "report.html"
        file_path.write_text("ok")

        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(
            side_effect=[{"run_key": "rk"}, {"file_path": str(file_path)}]
        )
        mock_conn = _mock_cursor_conn(mock_cursor)

        with (
            patch(
                "libs.web_console_services.scheduled_reports_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.scheduled_reports_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
            patch.dict(os.environ, {"REPORT_OUTPUT_DIR": str(tmp_path)}),
        ):
            result = await service.download_archive("run-1")

        assert result == file_path

    @pytest.mark.asyncio()
    async def test_download_archive_missing_user(self, mock_db_pool: Mock, tmp_path: Path) -> None:
        service = ScheduledReportsService(mock_db_pool, {"role": "viewer"})

        with (
            patch(
                "libs.web_console_services.scheduled_reports_service.has_permission",
                return_value=True,
            ),
            patch.dict(os.environ, {"REPORT_OUTPUT_DIR": str(tmp_path)}),
        ):
            result = await service.download_archive("run-1")

        assert result is None
