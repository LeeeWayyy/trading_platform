"""Comprehensive coverage tests for scheduled_reports.py page (target: 85%+ coverage).

This file extends test_scheduled_reports.py to achieve 85%+ branch coverage by testing:
1. Schedule creation/edit form with all input validation
2. Cron expression generation from presets (daily, weekly, monthly)
3. Run history display and downloads
4. Permission checks (VIEW_REPORTS, MANAGE_REPORTS)
5. Error handling for database and file operations
6. Demo mode rendering
7. Schedule management operations (run now, delete, update)
8. Form field visibility toggling based on preset selection
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.web_console_ng.pages import scheduled_reports as reports_module


class DummyElement:
    """Mock UI element supporting common NiceGUI operations."""

    def __init__(self, *, text: str | None = None, value: Any = None, **kwargs: Any) -> None:
        self.text = text or ""
        self.value = value
        self.visible = True
        self.on_click_cb = None
        self.on_value_change_cb = None
        self.kwargs = kwargs

    def __enter__(self) -> DummyElement:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def classes(self, *args: Any, **kwargs: Any) -> DummyElement:
        return self

    def props(self, *args: Any, **kwargs: Any) -> DummyElement:
        return self

    def set_visibility(self, value: bool) -> None:
        self.visible = value

    def set_text(self, value: str) -> None:
        self.text = value

    def on_click(self, cb: Callable[..., Any]) -> None:
        self.on_click_cb = cb

    def on_value_change(self, cb: Callable[..., Any]) -> None:
        self.on_value_change_cb = cb

    def clear(self) -> None:
        pass


class DummyUI:
    """Mock NiceGUI ui module."""

    def __init__(self) -> None:
        self.labels: list[str] = []
        self.buttons: list[DummyElement] = []
        self.inputs: dict[str, DummyElement] = {}
        self.textareas: dict[str, DummyElement] = {}
        self.selects: dict[str, DummyElement] = {}
        self.switches: list[DummyElement] = []
        self.notifications: list[tuple[str, str | None]] = []
        self.downloads: list[tuple[bytes, str]] = []
        self.tables: list[dict[str, Any]] = []
        self.navigate = SimpleNamespace(to=MagicMock())

    def refreshable(self, func: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        def refresh(*args: Any, **kwargs: Any) -> None:
            pass

        wrapper.refresh = refresh  # type: ignore[attr-defined]
        return wrapper

    def card(self, *args: Any, **kwargs: Any) -> DummyElement:
        return DummyElement()

    def row(self, *args: Any, **kwargs: Any) -> DummyElement:
        return DummyElement()

    def column(self, *args: Any, **kwargs: Any) -> DummyElement:
        return DummyElement()

    def grid(self, *args: Any, **kwargs: Any) -> DummyElement:
        return DummyElement()

    def expansion(self, *args: Any, **kwargs: Any) -> DummyElement:
        return DummyElement()

    def label(self, text: str = "", *args: Any, **kwargs: Any) -> DummyElement:
        self.labels.append(text)
        return DummyElement(text=text)

    def select(self, *, label: str = "", options=None, value=None, **kwargs: Any) -> DummyElement:
        element = DummyElement(text=label, value=value, options=options, **kwargs)
        if label:
            self.selects[label] = element
        return element

    def input(self, *, label: str = "", value: Any = "", **kwargs: Any) -> DummyElement:
        element = DummyElement(text=label, value=value, **kwargs)
        if label:
            self.inputs[label] = element
        return element

    def textarea(self, *, label: str = "", value: Any = "", **kwargs: Any) -> DummyElement:
        element = DummyElement(text=label, value=value, **kwargs)
        if label:
            self.textareas[label] = element
        return element

    def switch(self, text: str = "", value: bool = True, **kwargs: Any) -> DummyElement:
        element = DummyElement(text=text, value=value, **kwargs)
        self.switches.append(element)
        return element

    def button(
        self, text: str = "", icon: str | None = None, on_click=None, **kwargs: Any
    ) -> DummyElement:
        element = DummyElement(text=text, icon=icon, **kwargs)
        if on_click is not None:
            element.on_click(on_click)
        self.buttons.append(element)
        return element

    def separator(self, *args: Any, **kwargs: Any) -> DummyElement:
        return DummyElement()

    def download(self, content: bytes, filename: str) -> None:
        self.downloads.append((content, filename))

    def notify(self, message: str, type: str | None = None) -> None:
        self.notifications.append((message, type))

    def table(self, *, columns: list[dict[str, Any]], rows: list[dict[str, Any]]) -> DummyElement:
        self.tables.append({"columns": columns, "rows": rows})
        return DummyElement()

    def icon(self, name: str, **kwargs: Any) -> DummyElement:
        return DummyElement(text=name, **kwargs)


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    """Fixture providing mocked NiceGUI ui module."""
    ui = DummyUI()
    monkeypatch.setattr(reports_module, "ui", ui)
    return ui


async def _call(cb: Callable[..., Any] | None, *args: Any) -> None:
    """Helper to call sync or async callback."""
    if cb is None:
        return
    if asyncio.iscoroutinefunction(cb):
        await cb(*args)
    else:
        cb(*args)


# ==========================
# CRON PRESET TESTS
# ==========================


@pytest.mark.asyncio()
async def test_schedule_form_daily_preset(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test daily preset generates correct cron expression."""
    service = SimpleNamespace(create_schedule=AsyncMock())

    await reports_module._render_schedule_form(service, user={"user_id": "u1"}, schedule=None)

    # Set daily preset
    dummy_ui.selects["Run Frequency"].value = "Daily"
    dummy_ui.inputs["Run Time (local)"].value = "09:30"
    dummy_ui.inputs["Schedule Name"].value = "Daily Report"
    dummy_ui.textareas["Report Parameters (JSON)"].value = "{}"

    submit_btn = next(btn for btn in dummy_ui.buttons if btn.text == "Create Schedule")
    await submit_btn.on_click_cb()

    # Should create schedule with cron "30 9 * * *"
    service.create_schedule.assert_awaited_once()
    call_args = service.create_schedule.await_args[0]
    assert call_args[2] == "30 9 * * *"


@pytest.mark.asyncio()
async def test_schedule_form_weekdays_preset(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test weekdays preset generates correct cron expression."""
    service = SimpleNamespace(create_schedule=AsyncMock())

    await reports_module._render_schedule_form(service, user={"user_id": "u1"}, schedule=None)

    # Set weekdays preset
    dummy_ui.selects["Run Frequency"].value = "Weekdays (Mon-Fri)"
    dummy_ui.inputs["Run Time (local)"].value = "06:00"
    dummy_ui.inputs["Schedule Name"].value = "Weekday Report"
    dummy_ui.textareas["Report Parameters (JSON)"].value = "{}"

    submit_btn = next(btn for btn in dummy_ui.buttons if btn.text == "Create Schedule")
    await submit_btn.on_click_cb()

    # Should create schedule with cron "0 6 * * 1-5"
    service.create_schedule.assert_awaited_once()
    call_args = service.create_schedule.await_args[0]
    assert call_args[2] == "0 6 * * 1-5"


@pytest.mark.asyncio()
async def test_schedule_form_weekly_preset(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test weekly preset generates correct cron expression."""
    service = SimpleNamespace(create_schedule=AsyncMock())

    await reports_module._render_schedule_form(service, user={"user_id": "u1"}, schedule=None)

    # Set weekly preset (Wednesday)
    dummy_ui.selects["Run Frequency"].value = "Weekly (choose day)"
    dummy_ui.selects["Day of Week"].value = "Wed"
    dummy_ui.inputs["Run Time (local)"].value = "08:00"
    dummy_ui.inputs["Schedule Name"].value = "Weekly Report"
    dummy_ui.textareas["Report Parameters (JSON)"].value = "{}"

    submit_btn = next(btn for btn in dummy_ui.buttons if btn.text == "Create Schedule")
    await submit_btn.on_click_cb()

    # Should create schedule with cron "0 8 * * 3" (Wed = 3)
    service.create_schedule.assert_awaited_once()
    call_args = service.create_schedule.await_args[0]
    assert call_args[2] == "0 8 * * 3"


@pytest.mark.asyncio()
async def test_schedule_form_monthly_preset(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test monthly preset generates correct cron expression."""
    service = SimpleNamespace(create_schedule=AsyncMock())

    await reports_module._render_schedule_form(service, user={"user_id": "u1"}, schedule=None)

    # Set monthly preset (15th of month)
    dummy_ui.selects["Run Frequency"].value = "Monthly (choose day)"
    dummy_ui.inputs["Day of Month"].value = "15"
    dummy_ui.inputs["Run Time (local)"].value = "07:00"
    dummy_ui.inputs["Schedule Name"].value = "Monthly Report"
    dummy_ui.textareas["Report Parameters (JSON)"].value = "{}"

    submit_btn = next(btn for btn in dummy_ui.buttons if btn.text == "Create Schedule")
    await submit_btn.on_click_cb()

    # Should create schedule with cron "0 7 15 * *"
    service.create_schedule.assert_awaited_once()
    call_args = service.create_schedule.await_args[0]
    assert call_args[2] == "0 7 15 * *"


@pytest.mark.asyncio()
async def test_schedule_form_custom_preset(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test custom preset uses provided cron expression."""
    service = SimpleNamespace(create_schedule=AsyncMock())

    await reports_module._render_schedule_form(service, user={"user_id": "u1"}, schedule=None)

    # Set custom preset with manual cron
    dummy_ui.selects["Run Frequency"].value = "Custom (advanced)"
    dummy_ui.inputs["Cron Expression (advanced)"].value = "*/15 * * * *"  # Every 15 minutes
    dummy_ui.inputs["Schedule Name"].value = "Custom Report"
    dummy_ui.textareas["Report Parameters (JSON)"].value = "{}"

    submit_btn = next(btn for btn in dummy_ui.buttons if btn.text == "Create Schedule")
    await submit_btn.on_click_cb()

    # Should create schedule with custom cron
    service.create_schedule.assert_awaited_once()
    call_args = service.create_schedule.await_args[0]
    assert call_args[2] == "*/15 * * * *"


@pytest.mark.asyncio()
async def test_schedule_form_invalid_time_format(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test schedule form handles invalid time format gracefully."""
    service = SimpleNamespace(create_schedule=AsyncMock())

    await reports_module._render_schedule_form(service, user={"user_id": "u1"}, schedule=None)

    # Set invalid time format (should default to 06:00)
    dummy_ui.selects["Run Frequency"].value = "Daily"
    dummy_ui.inputs["Run Time (local)"].value = "invalid"
    dummy_ui.inputs["Schedule Name"].value = "Daily Report"
    dummy_ui.textareas["Report Parameters (JSON)"].value = "{}"

    submit_btn = next(btn for btn in dummy_ui.buttons if btn.text == "Create Schedule")
    await submit_btn.on_click_cb()

    # Should create schedule with default time 06:00
    service.create_schedule.assert_awaited_once()
    call_args = service.create_schedule.await_args[0]
    assert call_args[2] == "0 6 * * *"


@pytest.mark.asyncio()
async def test_schedule_form_invalid_day_of_month(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test schedule form clamps invalid day of month."""
    service = SimpleNamespace(create_schedule=AsyncMock())

    await reports_module._render_schedule_form(service, user={"user_id": "u1"}, schedule=None)

    # Set invalid day of month (should clamp to 1)
    dummy_ui.selects["Run Frequency"].value = "Monthly (choose day)"
    dummy_ui.inputs["Day of Month"].value = "invalid"
    dummy_ui.inputs["Run Time (local)"].value = "06:00"
    dummy_ui.inputs["Schedule Name"].value = "Monthly Report"
    dummy_ui.textareas["Report Parameters (JSON)"].value = "{}"

    submit_btn = next(btn for btn in dummy_ui.buttons if btn.text == "Create Schedule")
    await submit_btn.on_click_cb()

    # Should create schedule with day clamped to 1
    service.create_schedule.assert_awaited_once()
    call_args = service.create_schedule.await_args[0]
    assert call_args[2] == "0 6 1 * *"


# ==========================
# SCHEDULE UPDATE TESTS
# ==========================


@pytest.mark.asyncio()
async def test_schedule_form_updates_existing_schedule(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test schedule form updates existing schedule."""
    service = SimpleNamespace(update_schedule=AsyncMock())
    schedule = SimpleNamespace(
        id="sched-1",
        name="Existing Schedule",
        report_type="daily_summary",
        cron="0 6 * * *",
        enabled=True,
        params={},
    )

    await reports_module._render_schedule_form(service, user={"user_id": "u1"}, schedule=schedule)

    # Update schedule name
    dummy_ui.inputs["Schedule Name"].value = "Updated Schedule"
    dummy_ui.textareas["Report Parameters (JSON)"].value = "{}"

    submit_btn = next(btn for btn in dummy_ui.buttons if btn.text == "Update Schedule")
    await submit_btn.on_click_cb()

    # Should call update_schedule
    service.update_schedule.assert_awaited_once()
    call_args = service.update_schedule.await_args[0]
    assert call_args[0] == "sched-1"
    assert call_args[1]["name"] == "Updated Schedule"


@pytest.mark.asyncio()
async def test_schedule_form_db_connection_error_on_create(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test schedule form handles database connection errors on create."""
    service = SimpleNamespace(create_schedule=AsyncMock(side_effect=ConnectionError("DB error")))

    await reports_module._render_schedule_form(service, user={"user_id": "u1"}, schedule=None)

    dummy_ui.inputs["Schedule Name"].value = "Daily Report"
    dummy_ui.textareas["Report Parameters (JSON)"].value = "{}"

    submit_btn = next(btn for btn in dummy_ui.buttons if btn.text == "Create Schedule")
    await submit_btn.on_click_cb()

    # Should show error notification
    assert any("Database connection error" in msg for msg, _ in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_schedule_form_data_error_on_update(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test schedule form handles data errors on update."""
    service = SimpleNamespace(update_schedule=AsyncMock(side_effect=ValueError("Invalid data")))
    schedule = SimpleNamespace(
        id="sched-1",
        name="Existing Schedule",
        report_type="daily_summary",
        cron="0 6 * * *",
        enabled=True,
        params={},
    )

    await reports_module._render_schedule_form(service, user={"user_id": "u1"}, schedule=schedule)

    dummy_ui.inputs["Schedule Name"].value = "Updated Schedule"
    dummy_ui.textareas["Report Parameters (JSON)"].value = "{}"

    submit_btn = next(btn for btn in dummy_ui.buttons if btn.text == "Update Schedule")
    await submit_btn.on_click_cb()

    # Should show error notification
    assert any("Data processing error" in msg for msg, _ in dummy_ui.notifications)


# ==========================
# SCHEDULE OPERATIONS TESTS
# ==========================


@pytest.mark.asyncio()
async def test_run_now_triggers_immediate_execution(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 'Run Now' button triggers immediate schedule execution."""
    schedule = SimpleNamespace(
        id="sched-1",
        name="Daily Performance",
        report_type="daily_summary",
        cron="0 6 * * *",
        enabled=True,
        last_run_at=None,
        next_run_at=None,
        params={},
    )
    service = SimpleNamespace(
        run_now=AsyncMock(),
        get_run_history=AsyncMock(return_value=[]),
    )
    schedules = [schedule]

    monkeypatch.setattr(
        reports_module, "has_permission", lambda user, perm: perm.name == "MANAGE_REPORTS"
    )

    await reports_module._render_reports_page(
        service, user={"user_id": "u1", "permissions": ["MANAGE_REPORTS"]}, schedules=schedules
    )

    # Find and trigger Run Now button
    run_now_btn = next(btn for btn in dummy_ui.buttons if btn.text == "Run Now")
    await run_now_btn.on_click_cb()

    # Should call service.run_now
    service.run_now.assert_awaited_once_with("sched-1")
    assert any("Report generated" in msg for msg, _ in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_run_now_db_connection_error(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 'Run Now' handles database connection errors."""
    schedule = SimpleNamespace(
        id="sched-1",
        name="Daily Performance",
        report_type="daily_summary",
        cron="0 6 * * *",
        enabled=True,
        last_run_at=None,
        next_run_at=None,
        params={},
    )
    service = SimpleNamespace(
        run_now=AsyncMock(side_effect=ConnectionError("DB error")),
        get_run_history=AsyncMock(return_value=[]),
    )
    schedules = [schedule]

    monkeypatch.setattr(
        reports_module, "has_permission", lambda user, perm: perm.name == "MANAGE_REPORTS"
    )

    await reports_module._render_reports_page(
        service, user={"user_id": "u1", "permissions": ["MANAGE_REPORTS"]}, schedules=schedules
    )

    run_now_btn = next(btn for btn in dummy_ui.buttons if btn.text == "Run Now")
    await run_now_btn.on_click_cb()

    # Should show error notification
    assert any("Database connection error" in msg for msg, _ in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_delete_schedule_success(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test schedule deletion succeeds."""
    schedule = SimpleNamespace(
        id="sched-1",
        name="Daily Performance",
        report_type="daily_summary",
        cron="0 6 * * *",
        enabled=True,
        last_run_at=None,
        next_run_at=None,
        params={},
    )
    service = SimpleNamespace(
        delete_schedule=AsyncMock(return_value=True),
        get_run_history=AsyncMock(return_value=[]),
    )
    schedules = [schedule]

    monkeypatch.setattr(
        reports_module, "has_permission", lambda user, perm: perm.name == "MANAGE_REPORTS"
    )

    await reports_module._render_reports_page(
        service, user={"user_id": "u1", "permissions": ["MANAGE_REPORTS"]}, schedules=schedules
    )

    delete_btn = next(btn for btn in dummy_ui.buttons if btn.text == "Delete Schedule")
    await delete_btn.on_click_cb()

    # Should call delete_schedule
    service.delete_schedule.assert_awaited_once_with("sched-1")
    assert any("Schedule deleted" in msg for msg, _ in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_delete_schedule_not_found(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test schedule deletion when schedule not found."""
    schedule = SimpleNamespace(
        id="sched-1",
        name="Daily Performance",
        report_type="daily_summary",
        cron="0 6 * * *",
        enabled=True,
        last_run_at=None,
        next_run_at=None,
        params={},
    )
    service = SimpleNamespace(
        delete_schedule=AsyncMock(return_value=False),
        get_run_history=AsyncMock(return_value=[]),
    )
    schedules = [schedule]

    monkeypatch.setattr(
        reports_module, "has_permission", lambda user, perm: perm.name == "MANAGE_REPORTS"
    )

    await reports_module._render_reports_page(
        service, user={"user_id": "u1", "permissions": ["MANAGE_REPORTS"]}, schedules=schedules
    )

    delete_btn = next(btn for btn in dummy_ui.buttons if btn.text == "Delete Schedule")
    await delete_btn.on_click_cb()

    # Should show warning notification
    assert any("Schedule not found" in msg for msg, _ in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_delete_schedule_db_connection_error(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test schedule deletion handles database connection errors."""
    schedule = SimpleNamespace(
        id="sched-1",
        name="Daily Performance",
        report_type="daily_summary",
        cron="0 6 * * *",
        enabled=True,
        last_run_at=None,
        next_run_at=None,
        params={},
    )
    service = SimpleNamespace(
        delete_schedule=AsyncMock(side_effect=OSError("IO error")),
        get_run_history=AsyncMock(return_value=[]),
    )
    schedules = [schedule]

    monkeypatch.setattr(
        reports_module, "has_permission", lambda user, perm: perm.name == "MANAGE_REPORTS"
    )

    await reports_module._render_reports_page(
        service, user={"user_id": "u1", "permissions": ["MANAGE_REPORTS"]}, schedules=schedules
    )

    delete_btn = next(btn for btn in dummy_ui.buttons if btn.text == "Delete Schedule")
    await delete_btn.on_click_cb()

    # Should show error notification
    assert any("Database connection error" in msg for msg, _ in dummy_ui.notifications)


# ==========================
# RUN HISTORY TESTS
# ==========================


@pytest.mark.asyncio()
async def test_run_history_displays_runs(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test run history displays completed runs."""
    run1 = SimpleNamespace(
        id="run-1",
        run_key="20260103-060000",
        status="completed",
        started_at=datetime(2026, 1, 3, 6, 0, 0),
        completed_at=datetime(2026, 1, 3, 6, 0, 45),
        error_message=None,
    )
    run2 = SimpleNamespace(
        id="run-2",
        run_key="20260102-060000",
        status="failed",
        started_at=datetime(2026, 1, 2, 6, 0, 0),
        completed_at=datetime(2026, 1, 2, 6, 0, 30),
        error_message="Timeout",
    )

    service = SimpleNamespace(get_run_history=AsyncMock(return_value=[run1, run2]))

    await reports_module._render_run_history(service, schedule_id="sched-1")

    # Should render table with runs
    assert len(dummy_ui.tables) > 0
    table = dummy_ui.tables[0]
    assert len(table["rows"]) == 2
    assert table["rows"][0]["run_key"] == "20260103-060000"
    assert table["rows"][1]["error"] == "Timeout"


@pytest.mark.asyncio()
async def test_run_history_no_runs(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test run history displays message when no runs recorded."""
    service = SimpleNamespace(get_run_history=AsyncMock(return_value=[]))

    await reports_module._render_run_history(service, schedule_id="sched-1")

    # Should show "no runs" message
    assert any("No runs recorded yet" in label for label in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_run_history_db_connection_error(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test run history handles database connection errors."""
    service = SimpleNamespace(
        get_run_history=AsyncMock(side_effect=ConnectionError("DB connection failed"))
    )

    await reports_module._render_run_history(service, schedule_id="sched-1")

    # Should show error message
    assert any("Failed to load history" in label for label in dummy_ui.labels)
    assert any("Database connection error" in label for label in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_run_history_data_error(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test run history handles data processing errors."""
    service = SimpleNamespace(get_run_history=AsyncMock(side_effect=KeyError("Missing field")))

    await reports_module._render_run_history(service, schedule_id="sched-1")

    # Should show error message
    assert any("Data processing error" in label for label in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_download_report_pdf_format(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test report download with PDF format."""
    run = SimpleNamespace(
        id="run-1",
        run_key="20260103-060000",
        status="completed",
        started_at=datetime(2026, 1, 3, 6, 0, 0),
        completed_at=datetime(2026, 1, 3, 6, 0, 45),
        error_message=None,
        format="pdf",
    )

    service = SimpleNamespace(
        get_run_history=AsyncMock(return_value=[run]),
        download_archive=AsyncMock(return_value="/tmp/report.pdf"),
    )

    async def io_bound(func: Callable[..., Any], *args: Any) -> Any:
        return b"pdf-bytes"

    monkeypatch.setattr(reports_module.run, "io_bound", io_bound)

    await reports_module._render_run_history(service, schedule_id="sched-1")

    download_btn = next(btn for btn in dummy_ui.buttons if btn.text.startswith("Download"))
    await download_btn.on_click_cb()

    # Should trigger download
    assert len(dummy_ui.downloads) == 1
    content, filename = dummy_ui.downloads[0]
    assert content == b"pdf-bytes"
    assert filename == "report_20260103-060000.pdf"


@pytest.mark.asyncio()
async def test_download_report_html_format(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test report download with HTML format."""
    run = SimpleNamespace(
        id="run-1",
        run_key="20260103-060000",
        status="completed",
        started_at=datetime(2026, 1, 3, 6, 0, 0),
        completed_at=datetime(2026, 1, 3, 6, 0, 45),
        error_message=None,
        format="html",
    )

    service = SimpleNamespace(
        get_run_history=AsyncMock(return_value=[run]),
        download_archive=AsyncMock(return_value="/tmp/report.html"),
    )

    async def io_bound(func: Callable[..., Any], *args: Any) -> Any:
        return b"<html>report</html>"

    monkeypatch.setattr(reports_module.run, "io_bound", io_bound)

    await reports_module._render_run_history(service, schedule_id="sched-1")

    download_btn = next(btn for btn in dummy_ui.buttons if btn.text.startswith("Download"))
    await download_btn.on_click_cb()

    # Should trigger download with HTML filename
    assert len(dummy_ui.downloads) == 1
    content, filename = dummy_ui.downloads[0]
    assert filename == "report_20260103-060000.html"


@pytest.mark.asyncio()
async def test_download_report_file_not_found(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test report download handles file not found errors."""
    run = SimpleNamespace(
        id="run-1",
        run_key="20260103-060000",
        status="completed",
        started_at=datetime(2026, 1, 3, 6, 0, 0),
        completed_at=datetime(2026, 1, 3, 6, 0, 45),
        error_message=None,
        format="pdf",
    )

    service = SimpleNamespace(
        get_run_history=AsyncMock(return_value=[run]),
        download_archive=AsyncMock(side_effect=FileNotFoundError("File not found")),
    )

    await reports_module._render_run_history(service, schedule_id="sched-1")

    download_btn = next(btn for btn in dummy_ui.buttons if btn.text.startswith("Download"))
    await download_btn.on_click_cb()

    # Should show error notification
    assert any("Report file not found" in msg for msg, _ in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_download_report_file_access_error(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test report download handles file access errors."""
    run = SimpleNamespace(
        id="run-1",
        run_key="20260103-060000",
        status="completed",
        started_at=datetime(2026, 1, 3, 6, 0, 0),
        completed_at=datetime(2026, 1, 3, 6, 0, 45),
        error_message=None,
        format="pdf",
    )

    service = SimpleNamespace(
        get_run_history=AsyncMock(return_value=[run]),
        download_archive=AsyncMock(return_value="/tmp/report.pdf"),
    )

    async def io_bound(func: Callable[..., Any], *args: Any) -> Any:
        raise OSError("Permission denied")

    monkeypatch.setattr(reports_module.run, "io_bound", io_bound)

    await reports_module._render_run_history(service, schedule_id="sched-1")

    download_btn = next(btn for btn in dummy_ui.buttons if btn.text.startswith("Download"))
    await download_btn.on_click_cb()

    # Should show error notification
    assert any("File access error" in msg for msg, _ in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_download_report_not_available(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test report download when file path not available."""
    run = SimpleNamespace(
        id="run-1",
        run_key="20260103-060000",
        status="completed",
        started_at=datetime(2026, 1, 3, 6, 0, 0),
        completed_at=datetime(2026, 1, 3, 6, 0, 45),
        error_message=None,
        format="pdf",
    )

    service = SimpleNamespace(
        get_run_history=AsyncMock(return_value=[run]),
        download_archive=AsyncMock(return_value=None),  # No file path
    )

    await reports_module._render_run_history(service, schedule_id="sched-1")

    download_btn = next(btn for btn in dummy_ui.buttons if btn.text.startswith("Download"))
    await download_btn.on_click_cb()

    # Should show warning notification
    assert any("Report file not available" in msg for msg, _ in dummy_ui.notifications)


# ==========================
# DEMO MODE TESTS
# ==========================


@pytest.mark.asyncio()
async def test_demo_mode_renders_placeholder_data(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test demo mode renders with placeholder data."""
    user = {"user_id": "u1", "permissions": ["VIEW_REPORTS"]}

    monkeypatch.setattr(
        reports_module, "has_permission", lambda user, perm: perm.name in user.get("permissions", [])
    )

    reports_module._render_demo_mode(user)

    # Should show demo mode banner (checking by label text not requiring exact match)
    assert any("Demo Mode" in label or "demo" in label.lower() for label in dummy_ui.labels), \
        f"Expected demo mode text in labels: {dummy_ui.labels}"
    assert any("unavailable" in label.lower() or "database" in label.lower() for label in dummy_ui.labels), \
        f"Expected database unavailable text in labels: {dummy_ui.labels}"

    # Should show demo schedules - these are hardcoded in the function
    assert any("Daily Performance" in label for label in dummy_ui.labels), \
        f"Expected 'Daily Performance' in labels: {dummy_ui.labels}"


@pytest.mark.asyncio()
async def test_demo_mode_with_manage_permission(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test demo mode shows create form for users with MANAGE_REPORTS."""
    user = {"user_id": "u1", "permissions": ["VIEW_REPORTS", "MANAGE_REPORTS"]}

    monkeypatch.setattr(
        reports_module, "has_permission", lambda user, perm: perm.name in user.get("permissions", [])
    )

    reports_module._render_demo_mode(user)

    # Should show create schedule section
    assert "Schedule Name" in dummy_ui.inputs
    # Should have disabled create button
    assert any(btn.text == "Create Schedule" for btn in dummy_ui.buttons)


# ==========================
# HELPER FUNCTION TESTS
# ==========================


def test_format_dt_with_datetime() -> None:
    """Test _format_dt formats datetime correctly."""
    dt = datetime(2026, 1, 3, 6, 0, 45)
    result = reports_module._format_dt(dt)
    assert result == "2026-01-03 06:00:45"


def test_format_dt_with_none() -> None:
    """Test _format_dt returns dash for None."""
    result = reports_module._format_dt(None)
    assert result == "-"
