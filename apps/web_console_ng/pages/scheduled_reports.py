"""Scheduled Reports management page for NiceGUI web console (P5T8).

Provides interface for managing automated report schedules.

Features:
    - Schedule list/selector
    - Create/Edit schedule form
    - Report run history with download
    - Enable/disable schedules

PARITY: Mirrors UI layout from apps/web_console/pages/scheduled_reports.py

NOTE: This page uses demo mode with placeholder data when services are unavailable.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from croniter import CroniterBadCronError, croniter  # type: ignore[import-untyped]
from nicegui import run, ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.core.database import get_db_pool
from apps.web_console_ng.ui.layout import main_layout
from libs.web_console_auth.permissions import Permission, has_permission

logger = logging.getLogger(__name__)

DEFAULT_REPORT_TYPES = [
    "daily_summary",
    "weekly_performance",
    "risk_snapshot",
    "custom",
]


def _get_service(db_pool: Any, user: dict[str, Any]) -> Any:
    """Get or create ScheduledReportsService.

    Args:
        db_pool: NiceGUI async connection pool (psycopg_pool.AsyncConnectionPool).
        user: Current user dict.

    Returns:
        ScheduledReportsService instance or None if db_pool is None.
    """
    from apps.web_console.services.scheduled_reports_service import ScheduledReportsService

    if db_pool is None:
        return None
    return ScheduledReportsService(db_pool=db_pool, user=dict(user))


@ui.page("/reports")
@requires_auth
@main_layout
async def scheduled_reports_page() -> None:
    """Scheduled Reports management page."""
    user = get_current_user()

    # Page title
    ui.label("Scheduled Reports").classes("text-2xl font-bold mb-4")
    ui.label(
        "This page lets you set up automatic reports. Think of it as a timer that sends you a "
        "summary of your trading (like daily or weekly results) without you doing anything. "
        "You can pick what report to send, how often it should run, who should receive it, and "
        "which strategies it should cover. You can also see past runs to confirm they worked."
    ).classes("text-sm text-gray-600 mb-6")

    # Permission check
    if not has_permission(user, Permission.VIEW_REPORTS):
        ui.notify("Permission denied: VIEW_REPORTS required", type="negative")
        with ui.card().classes("w-full p-6"):
            ui.label("Permission denied: VIEW_REPORTS required.").classes(
                "text-red-500 text-center"
            )
        return

    # Get async DB pool (NiceGUI's pool, compatible with ScheduledReportsService)
    db_pool = get_db_pool()
    if db_pool is None:
        _render_demo_mode(user)
        return

    # Try to get service
    try:
        service = await run.io_bound(_get_service, db_pool, user)
    except (ConnectionError, OSError) as exc:
        logger.error(
            "scheduled_reports_service_init_db_connection_failed",
            extra={"user_id": user.get("user_id"), "error": str(exc)},
            exc_info=True,
        )
        _render_demo_mode(user)
        return
    except (ValueError, TypeError) as exc:
        logger.error(
            "scheduled_reports_service_init_data_error",
            extra={"user_id": user.get("user_id"), "error": str(exc)},
            exc_info=True,
        )
        _render_demo_mode(user)
        return

    if service is None:
        _render_demo_mode(user)
        return

    # Load schedules
    try:
        user_id = user.get("user_id")
        schedules = await service.list_schedules(user_id)
    except (ConnectionError, OSError) as exc:
        logger.error(
            "schedules_load_db_connection_failed",
            extra={"user_id": user_id, "error": str(exc)},
            exc_info=True,
        )
        with ui.card().classes("w-full p-6"):
            ui.label("Failed to load schedules: Database connection error").classes(
                "text-red-500 text-center"
            )
        ui.notify("Database connection error", type="negative")
        return
    except (ValueError, KeyError, TypeError) as exc:
        logger.error(
            "schedules_load_data_error",
            extra={"user_id": user_id, "error": str(exc)},
            exc_info=True,
        )
        with ui.card().classes("w-full p-6"):
            ui.label("Failed to load schedules: Data processing error").classes(
                "text-red-500 text-center"
            )
        ui.notify("Data processing error", type="negative")
        return

    await _render_reports_page(service, user, schedules)


async def _render_reports_page(
    service: Any,
    user: dict[str, Any],
    schedules: list[Any],
) -> None:
    """Render the full reports management page."""
    can_manage = has_permission(user, Permission.MANAGE_REPORTS)

    # State for selected schedule
    state: dict[str, Any] = {"selected_id": None}

    # Schedules section
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("Schedules").classes("text-lg font-bold mb-2")

        if not schedules:
            ui.label("No schedules configured yet.").classes("text-gray-500 p-4")
        else:
            # Schedule selector
            schedule_options = {
                s.id: f"{s.name} ({s.report_type})" for s in schedules
            }
            schedule_select = ui.select(
                label="Select Schedule",
                options=schedule_options,
                value=schedules[0].id if schedules else None,
            ).classes("w-full max-w-md mb-4")

            if schedules:
                state["selected_id"] = schedules[0].id

            def update_selected() -> None:
                state["selected_id"] = schedule_select.value
                render_schedule_details.refresh()

            schedule_select.on_value_change(lambda _: update_selected())

    # Create new schedule section (for users with MANAGE_REPORTS)
    if can_manage:
        with ui.expansion("Create New Schedule", value=not schedules).classes("w-full mb-4"):
            await _render_schedule_form(service, user, None)

    # Schedule details section
    details_container = ui.column().classes("w-full mb-4")

    @ui.refreshable  # type: ignore[arg-type]
    async def render_schedule_details() -> None:
        details_container.clear()

        selected_id = state.get("selected_id")
        if not selected_id:
            return

        selected = next((s for s in schedules if s.id == selected_id), None)
        if not selected:
            return

        with details_container:
            with ui.card().classes("w-full p-4"):
                ui.label("Schedule Details").classes("text-lg font-bold mb-2")

                # Details grid
                with ui.grid(columns=2).classes("gap-4 mb-4"):
                    ui.label("Name:").classes("font-medium")
                    ui.label(selected.name)

                    ui.label("Report Type:").classes("font-medium")
                    ui.label(selected.report_type)

                    ui.label("Cron:").classes("font-medium")
                    ui.label(selected.cron or "Not set")

                    ui.label("Enabled:").classes("font-medium")
                    ui.label("Yes" if selected.enabled else "No").classes(
                        "text-green-600" if selected.enabled else "text-red-600"
                    )

                    ui.label("Last Run:").classes("font-medium")
                    ui.label(
                        _format_dt(selected.last_run_at) if selected.last_run_at else "Never"
                    )

                    ui.label("Next Run:").classes("font-medium")
                    ui.label(
                        _format_dt(selected.next_run_at) if selected.next_run_at else "Not scheduled"
                    )

                if can_manage:
                    ui.separator().classes("my-4")

                    with ui.expansion("Edit Schedule").classes("w-full"):
                        await _render_schedule_form(service, user, selected)

                    async def run_now() -> None:
                        try:
                            await service.run_now(selected.id)
                            ui.notify("Report generated", type="positive")
                            ui.navigate.to("/reports")
                        except (ConnectionError, OSError) as exc:
                            logger.error(
                                "schedule_run_now_db_connection_failed",
                                extra={"schedule_id": selected.id, "error": str(exc)},
                                exc_info=True,
                            )
                            ui.notify("Run failed: Database connection error", type="negative")
                        except (ValueError, KeyError, TypeError) as exc:
                            logger.error(
                                "schedule_run_now_data_error",
                                extra={"schedule_id": selected.id, "error": str(exc)},
                                exc_info=True,
                            )
                            ui.notify("Run failed: Data processing error", type="negative")

                    async def delete_schedule() -> None:
                        try:
                            deleted = await service.delete_schedule(selected.id)
                            if deleted:
                                ui.notify("Schedule deleted", type="positive")
                                ui.navigate.to("/reports")
                            else:
                                ui.notify("Schedule not found", type="warning")
                        except (ConnectionError, OSError) as exc:
                            logger.error(
                                "schedule_delete_db_connection_failed",
                                extra={"schedule_id": selected.id, "error": str(exc)},
                                exc_info=True,
                            )
                            ui.notify("Failed to delete: Database connection error", type="negative")
                        except (ValueError, KeyError, TypeError) as exc:
                            logger.error(
                                "schedule_delete_data_error",
                                extra={"schedule_id": selected.id, "error": str(exc)},
                                exc_info=True,
                            )
                            ui.notify("Failed to delete: Data processing error", type="negative")

                    ui.button(
                        "Run Now",
                        icon="play_arrow",
                        on_click=run_now,
                    ).classes("mt-2")

                    ui.button(
                        "Delete Schedule",
                        icon="delete",
                        on_click=delete_schedule,
                    ).props("color=negative").classes("mt-4")

            ui.separator().classes("my-4")

            # Run history
            await _render_run_history(service, selected.id)

    await render_schedule_details()


async def _render_schedule_form(
    service: Any,
    user: dict[str, Any],
    schedule: Any | None,
) -> None:
    """Render schedule create/edit form."""
    is_edit = schedule is not None

    with ui.card().classes("w-full p-4"):
        name_input = ui.input(
            label="Schedule Name",
            value=schedule.name if schedule else "",
        ).classes("w-full max-w-md")
        ui.label(
            "A friendly label so you can recognize this schedule later (e.g., "
            "'Daily Alpha Summary' or 'Weekly Risk Review')."
        ).classes("text-gray-500 text-xs")

        report_types = list(DEFAULT_REPORT_TYPES)
        if schedule and schedule.report_type not in report_types:
            report_types.insert(0, schedule.report_type)

        type_select = ui.select(
            label="Report Type",
            options=report_types,
            value=schedule.report_type if schedule else report_types[0],
        ).classes("w-full max-w-md")
        ui.label(
            "What kind of report to generate. Pick one of the preset types or a custom "
            "template if your system supports it."
        ).classes("text-gray-500 text-xs")

        preset_options = [
            "Daily",
            "Weekdays (Mon-Fri)",
            "Weekly (choose day)",
            "Monthly (choose day)",
            "Custom (advanced)",
        ]
        preset_select = ui.select(
            label="Run Frequency",
            options=preset_options,
            value="Custom (advanced)" if schedule else "Daily",
        ).classes("w-full max-w-md")
        ui.label(
            "Pick a human-friendly schedule. Use Custom only if you already know cron."
        ).classes("text-gray-500 text-xs")

        time_input = ui.input(
            label="Run Time (local)",
            value="06:00",
        ).props("type=time").classes("w-full max-w-md")

        weekday_select = ui.select(
            label="Day of Week",
            options=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            value="Mon",
        ).classes("w-full max-w-md")

        monthday_input = ui.input(
            label="Day of Month",
            value="1",
        ).classes("w-full max-w-md").props("type=number min=1 max=31")

        cron_input = ui.input(
            label="Cron Expression (advanced)",
            value=schedule.cron if schedule else "0 6 * * *",
        ).classes("w-full max-w-md")
        cron_help = ui.label(
            "Cron format: minute hour day month weekday (e.g., '0 6 * * *' = 6 AM daily)."
        ).classes("text-gray-500 text-xs")

        cron_preview = ui.label("").classes("text-xs text-gray-500")
        next_run_preview = ui.label("").classes("text-xs text-gray-500")

        def _compute_cron_from_preset() -> str:
            time_value = time_input.value
            if not isinstance(time_value, str) or not time_value.strip():
                time_value = "06:00"
            try:
                hour_str, minute_str = time_value.split(":")
                hour = int(hour_str)
                minute = int(minute_str)
            except (ValueError, AttributeError):
                hour = 6
                minute = 0

            preset = preset_select.value
            if preset == "Daily":
                return f"{minute} {hour} * * *"
            if preset == "Weekdays (Mon-Fri)":
                return f"{minute} {hour} * * 1-5"
            if preset == "Weekly (choose day)":
                day_map = {
                    "Mon": 1,
                    "Tue": 2,
                    "Wed": 3,
                    "Thu": 4,
                    "Fri": 5,
                    "Sat": 6,
                    "Sun": 0,
                }
                return f"{minute} {hour} * * {day_map.get(weekday_select.value, 1)}"
            if preset == "Monthly (choose day)":
                try:
                    day = int(monthday_input.value)
                except (ValueError, TypeError):
                    day = 1
                day = max(1, min(day, 31))
                return f"{minute} {hour} {day} * *"
            return cron_input.value or "0 6 * * *"

        def _estimate_next_run() -> str:
            cron_value = (
                cron_input.value.strip()
                if preset_select.value == "Custom (advanced)"
                else _compute_cron_from_preset()
            )
            try:
                iterator = croniter(cron_value, datetime.now())
                next_dt: datetime = iterator.get_next(datetime)
                return str(next_dt.strftime("%Y-%m-%d %H:%M"))
            except (CroniterBadCronError, ValueError, TypeError):
                return "Invalid schedule"

        def _sync_visibility() -> None:
            preset = preset_select.value
            is_custom = preset == "Custom (advanced)"
            weekday_select.set_visibility(preset == "Weekly (choose day)")
            monthday_input.set_visibility(preset == "Monthly (choose day)")
            cron_input.set_visibility(is_custom)
            cron_help.set_visibility(is_custom)
            if not is_custom:
                cron_preview.set_text(f"Generated cron: {_compute_cron_from_preset()}")
            else:
                cron_preview.set_text("")
            next_run_preview.set_text(f"Next run (estimated): {_estimate_next_run()}")

        preset_select.on_value_change(lambda _: _sync_visibility())
        time_input.on_value_change(lambda _: _sync_visibility())
        weekday_select.on_value_change(lambda _: _sync_visibility())
        monthday_input.on_value_change(lambda _: _sync_visibility())
        _sync_visibility()

        enabled_switch = ui.switch(
            "Enabled",
            value=schedule.enabled if schedule else True,
        )
        ui.label(
            "Turn this on to allow the scheduler to run. Turn it off to pause without deleting."
        ).classes("text-gray-500 text-xs")

        params_text = json.dumps(schedule.params, indent=2) if schedule and schedule.params else "{}"
        params_input = ui.textarea(
            label="Report Parameters (JSON)",
            value=params_text,
        ).classes("w-full max-w-md").props("rows=4")
        ui.label(
            "Optional JSON settings for the report. Must be a JSON object (key/value pairs). "
            "Example: {\"timezone\": \"America/Los_Angeles\", \"include_charts\": true, "
            "\"lookback_days\": 7}. Leave {} if you are not sure."
        ).classes("text-gray-500 text-xs")

        async def submit_form() -> None:
            # Validate JSON
            try:
                params = json.loads(params_input.value) if params_input.value.strip() else {}
            except json.JSONDecodeError as exc:
                ui.notify(f"Invalid JSON: {exc}", type="negative")
                return

            cron_value = (
                cron_input.value.strip()
                if preset_select.value == "Custom (advanced)"
                else _compute_cron_from_preset()
            )

            payload = {
                "name": name_input.value.strip(),
                "report_type": type_select.value,
                "cron": cron_value,
                "params": params,
                "enabled": enabled_switch.value,
            }

            if not payload["name"]:
                ui.notify("Schedule name is required", type="warning")
                return

            try:
                if is_edit and schedule is not None:
                    await service.update_schedule(schedule.id, payload)
                    ui.notify("Schedule updated", type="positive")
                else:
                    await service.create_schedule(
                        payload["name"],
                        payload["report_type"],
                        payload["cron"],
                        payload["params"],
                        user.get("user_id", "unknown"),
                    )
                    ui.notify("Schedule created", type="positive")
                ui.navigate.to("/reports")
            except (ConnectionError, OSError) as exc:
                logger.error(
                    "schedule_save_db_connection_failed",
                    extra={"schedule_name": payload["name"], "error": str(exc)},
                    exc_info=True,
                )
                ui.notify("Failed to save: Database connection error", type="negative")
            except (ValueError, KeyError, TypeError) as exc:
                logger.error(
                    "schedule_save_data_error",
                    extra={"schedule_name": payload["name"], "error": str(exc)},
                    exc_info=True,
                )
                ui.notify("Failed to save: Data processing error", type="negative")

        ui.button(
            "Update Schedule" if is_edit else "Create Schedule",
            icon="save",
            on_click=submit_form,
        ).props("color=primary").classes("mt-4")


async def _render_run_history(service: Any, schedule_id: str) -> None:
    """Render run history table for a schedule."""
    with ui.card().classes("w-full p-4"):
        ui.label("Run History").classes("text-lg font-bold mb-2")

        try:
            runs = await service.get_run_history(schedule_id)
        except (ConnectionError, OSError) as exc:
            logger.error(
                "run_history_load_db_connection_failed",
                extra={"schedule_id": schedule_id, "error": str(exc)},
                exc_info=True,
            )
            ui.label("Failed to load history: Database connection error").classes("text-red-500 p-2")
            return
        except (ValueError, KeyError, TypeError) as exc:
            logger.error(
                "run_history_load_data_error",
                extra={"schedule_id": schedule_id, "error": str(exc)},
                exc_info=True,
            )
            ui.label("Failed to load history: Data processing error").classes("text-red-500 p-2")
            return

        if not runs:
            ui.label("No runs recorded yet.").classes("text-gray-500 p-4")
            return

        columns = [
            {"name": "run_key", "label": "Run Key", "field": "run_key"},
            {"name": "status", "label": "Status", "field": "status"},
            {"name": "started", "label": "Started", "field": "started"},
            {"name": "completed", "label": "Completed", "field": "completed"},
            {"name": "error", "label": "Error", "field": "error"},
        ]

        rows = [
            {
                "run_key": r.run_key,
                "status": r.status,
                "started": _format_dt(r.started_at),
                "completed": _format_dt(r.completed_at),
                "error": r.error_message or "-",
            }
            for r in runs
        ]

        ui.table(columns=columns, rows=rows).classes("w-full mb-4")

        # Download section
        ui.label("Download completed reports:").classes("text-sm mt-4 mb-2")

        def _read_file_bytes(file_path: str) -> bytes:
            """Read file bytes (I/O-bound, run in thread pool)."""
            with open(file_path, "rb") as f:
                return f.read()

        for r in runs:
            if r.status.lower() != "completed":
                continue

            async def download_report(
                run_id: str = r.id,
                run_key: str = r.run_key,
                file_format: str = (getattr(r, "format", None) or "pdf").lower(),
            ) -> None:
                try:
                    path = await service.download_archive(run_id)
                    if not path:
                        ui.notify("Report file not available", type="warning")
                        return

                    # Read file in thread pool and trigger download
                    content = await run.io_bound(_read_file_bytes, path)

                    if file_format == "html":
                        file_name = f"report_{run_key}.html"
                    elif file_format == "pdf":
                        file_name = f"report_{run_key}.pdf"
                    else:
                        file_name = f"report_{run_key}.{file_format}"

                    ui.download(content, file_name)
                    ui.notify(f"Downloading {file_name}", type="positive")
                except FileNotFoundError as exc:
                    logger.error(
                        "report_download_file_not_found",
                        extra={"run_id": run_id, "run_key": run_key, "error": str(exc)},
                        exc_info=True,
                    )
                    ui.notify("Download failed: Report file not found", type="negative")
                except OSError as exc:
                    logger.error(
                        "report_download_io_error",
                        extra={"run_id": run_id, "run_key": run_key, "error": str(exc)},
                        exc_info=True,
                    )
                    ui.notify("Download failed: File access error", type="negative")

            ui.button(
                f"Download {r.run_key}",
                icon="download",
                on_click=download_report,
            ).props("size=sm").classes("mr-2 mb-2")


def _render_demo_mode(user: dict[str, Any]) -> None:
    """Render demo mode with placeholder data."""
    can_manage = has_permission(user, Permission.MANAGE_REPORTS)

    with ui.card().classes("w-full p-3 mb-4 bg-amber-50 border border-amber-300"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("info", color="amber-700")
            ui.label(
                "Demo Mode: Database connection unavailable."
            ).classes("text-amber-700")

    # Demo schedules
    demo_schedules = [
        {
            "id": "1",
            "name": "Daily Performance",
            "report_type": "daily_summary",
            "cron": "0 6 * * *",
            "enabled": True,
            "last_run": "2026-01-03 06:00:00",
            "next_run": "2026-01-04 06:00:00",
        },
        {
            "id": "2",
            "name": "Weekly Risk",
            "report_type": "weekly_performance",
            "cron": "0 8 * * 1",
            "enabled": True,
            "last_run": "2025-12-30 08:00:00",
            "next_run": "2026-01-06 08:00:00",
        },
    ]

    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("Schedules").classes("text-lg font-bold mb-2")

        schedule_options = {s["id"]: f"{s['name']} ({s['report_type']})" for s in demo_schedules}
        ui.select(
            label="Select Schedule",
            options=schedule_options,
            value="1",
        ).classes("w-full max-w-md mb-4")

    if can_manage:
        with ui.expansion("Create New Schedule").classes("w-full mb-4"):
            with ui.card().classes("w-full p-4"):
                ui.input(label="Schedule Name").classes("w-full max-w-md")
                ui.select(
                    label="Report Type",
                    options=DEFAULT_REPORT_TYPES,
                    value=DEFAULT_REPORT_TYPES[0],
                ).classes("w-full max-w-md")
                ui.input(label="Cron Expression", value="0 6 * * *").classes("w-full max-w-md")
                ui.switch("Enabled", value=True)
                ui.textarea(label="Report Parameters (JSON)", value="{}").classes(
                    "w-full max-w-md"
                ).props("rows=3")
                ui.button("Create Schedule", icon="save").props("color=primary disable")

    with ui.card().classes("w-full p-4"):
        ui.label("Schedule Details").classes("text-lg font-bold mb-2")

        with ui.grid(columns=2).classes("gap-4 mb-4"):
            ui.label("Name:").classes("font-medium")
            ui.label("Daily Performance")

            ui.label("Report Type:").classes("font-medium")
            ui.label("daily_summary")

            ui.label("Cron:").classes("font-medium")
            ui.label("0 6 * * *")

            ui.label("Enabled:").classes("font-medium")
            ui.label("Yes").classes("text-green-600")

            ui.label("Last Run:").classes("font-medium")
            ui.label("2026-01-03 06:00:00")

            ui.label("Next Run:").classes("font-medium")
            ui.label("2026-01-04 06:00:00")

    ui.separator().classes("my-4")

    with ui.card().classes("w-full p-4"):
        ui.label("Run History").classes("text-lg font-bold mb-2")

        demo_runs = [
            {
                "run_key": "20260103-060000",
                "status": "completed",
                "started": "2026-01-03 06:00:00",
                "completed": "2026-01-03 06:00:45",
                "error": "-",
            },
            {
                "run_key": "20260102-060000",
                "status": "completed",
                "started": "2026-01-02 06:00:00",
                "completed": "2026-01-02 06:00:38",
                "error": "-",
            },
        ]

        columns = [
            {"name": "run_key", "label": "Run Key", "field": "run_key"},
            {"name": "status", "label": "Status", "field": "status"},
            {"name": "started", "label": "Started", "field": "started"},
            {"name": "completed", "label": "Completed", "field": "completed"},
            {"name": "error", "label": "Error", "field": "error"},
        ]

        ui.table(columns=columns, rows=demo_runs).classes("w-full")


def _format_dt(value: datetime | None) -> str:
    """Format datetime for display."""
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M:%S")


__all__ = ["scheduled_reports_page"]
