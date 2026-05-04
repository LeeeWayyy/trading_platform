"""Data Sync section for the data-management page."""

from __future__ import annotations

import logging
from typing import Any

from nicegui import ui

from apps.web_console_ng.components.data_management_common import (
    TREND_DATASETS,
    format_datetime,
    get_user_id_safe,
)
from libs.platform.web_console_auth.permissions import Permission, has_permission
from libs.web_console_services.data_sync_service import DataSyncService
from libs.web_console_services.data_sync_service import (
    RateLimitExceeded as SyncRateLimitExceeded,
)
from libs.web_console_services.schemas.data_management import SyncScheduleUpdateDTO

logger = logging.getLogger(__name__)


def _resolve_dependencies(
    ui_module: Any | None,
    logger_obj: logging.Logger | None,
) -> tuple[Any, logging.Logger]:
    return ui if ui_module is None else ui_module, logger if logger_obj is None else logger_obj


def _normalize_sync_reason(value: Any) -> str:
    return str(value or "").strip()


async def render_data_sync_section(
    user: dict[str, Any],
    sync_service: DataSyncService,
    *,
    ui_module: Any | None = None,
    logger_obj: logging.Logger | None = None,
) -> ui.column | None:
    """Render Data Sync dashboard section. Returns the status container for refresh."""
    ui_ctx, logger_ctx = _resolve_dependencies(ui_module, logger_obj)
    ui_ctx.label("Data Sync Dashboard").classes("text-xl font-bold mb-2")

    has_view = has_permission(user, Permission.VIEW_DATA_SYNC)
    has_trigger = has_permission(user, Permission.TRIGGER_DATA_SYNC)
    has_manage = has_permission(user, Permission.MANAGE_SYNC_SCHEDULE)

    with ui_ctx.tabs().classes("w-full") as sync_tabs:
        tab_status = ui_ctx.tab("Sync Status")
        tab_logs = ui_ctx.tab("Sync Logs")
        tab_schedule = ui_ctx.tab("Schedule Config")

    sync_status_container: ui.column | None = None

    with ui_ctx.tab_panels(sync_tabs, value=tab_status).classes("w-full"):
        with ui_ctx.tab_panel(tab_status):
            sync_status_container = await render_sync_status(
                user,
                sync_service,
                has_view,
                has_trigger,
                ui_module=ui_ctx,
                logger_obj=logger_ctx,
            )

        with ui_ctx.tab_panel(tab_logs):
            await render_sync_logs(
                user, sync_service, has_view, ui_module=ui_ctx, logger_obj=logger_ctx
            )

        with ui_ctx.tab_panel(tab_schedule):
            await render_sync_schedule(
                user,
                sync_service,
                has_view,
                has_manage,
                ui_module=ui_ctx,
                logger_obj=logger_ctx,
            )

    return sync_status_container


async def render_sync_status(
    user: dict[str, Any],
    sync_service: DataSyncService,
    has_view: bool,
    has_trigger: bool,
    *,
    ui_module: Any | None = None,
    logger_obj: logging.Logger | None = None,
) -> ui.column | None:
    """Render sync status table and manual trigger. Returns container for refresh."""
    ui_ctx, logger_ctx = _resolve_dependencies(ui_module, logger_obj)
    status_container: ui.column | None = None
    dataset_names: list[str] = []

    if has_view:
        ui_ctx.label("Dataset Sync Status").classes("font-bold mb-2")
        status_container = ui_ctx.column().classes("w-full")
        try:
            statuses = await sync_service.get_sync_status(user)
            dataset_names = [s.dataset for s in statuses]
            with status_container:
                build_sync_status_table(statuses, ui_module=ui_ctx)
        except PermissionError as e:
            ui_ctx.notify(str(e), type="negative")
        except Exception:
            logger_ctx.exception(
                "service_call_failed",
                extra={
                    "method": "get_sync_status",
                    "service": "DataSyncService",
                    "user_id": get_user_id_safe(user),
                },
            )
            ui_ctx.notify("Service temporarily unavailable", type="warning")
    else:
        ui_ctx.label("Sync status requires data-sync view permission").classes("text-gray-500")

    if has_trigger:
        ui_ctx.separator().classes("my-4")
        ui_ctx.label("Manual Sync").classes("font-bold mb-2")

        with ui_ctx.row().classes("gap-4 items-end"):
            dataset_input: Any
            if dataset_names:
                dataset_input = ui_ctx.select(
                    label="Dataset",
                    options=dataset_names,
                    value=dataset_names[0],
                ).classes("w-48")
            else:
                dataset_input = ui_ctx.input(
                    label="Dataset Name",
                    placeholder="Enter dataset name",
                ).classes("w-48")

            reason_input = ui_ctx.input(
                label="Reason",
                placeholder="Why run this sync now?",
            ).classes("w-64")

            async def trigger_sync() -> None:
                dataset_val = dataset_input.value
                reason_value = _normalize_sync_reason(reason_input.value)
                if not dataset_val:
                    ui_ctx.notify("Please select a dataset", type="warning")
                    return
                if not reason_value:
                    ui_ctx.notify("Please provide a reason for audit logging", type="warning")
                    return
                try:
                    job = await sync_service.trigger_sync(user, str(dataset_val), reason_value)
                    ui_ctx.notify(f"Sync job {job.id} queued for {job.dataset}", type="positive")
                    reason_input.value = ""
                except SyncRateLimitExceeded:
                    ui_ctx.notify("Rate limit: 1 sync per minute", type="warning")
                except PermissionError as e:
                    ui_ctx.notify(str(e), type="negative")
                except Exception:
                    logger_ctx.exception(
                        "service_call_failed",
                        extra={
                            "method": "trigger_sync",
                            "service": "DataSyncService",
                            "dataset": str(dataset_val),
                            "user_id": get_user_id_safe(user),
                        },
                    )
                    ui_ctx.notify("Service temporarily unavailable", type="warning")

            ui_ctx.button("Trigger Sync", on_click=trigger_sync, color="primary")

    return status_container


def build_sync_status_table(
    statuses: list[Any],
    *,
    ui_module: Any | None = None,
) -> None:
    """Build the sync status table from SyncStatusDTO list."""
    ui_ctx, _ = _resolve_dependencies(ui_module, None)
    columns: list[dict[str, Any]] = [
        {"name": "dataset", "label": "Dataset", "field": "dataset", "sortable": True},
        {
            "name": "last_sync",
            "label": "Last Sync",
            "field": "last_sync",
            "sortable": True,
        },
        {"name": "row_count", "label": "Row Count", "field": "row_count", "sortable": True},
        {
            "name": "validation_status",
            "label": "Validation",
            "field": "validation_status",
        },
    ]
    rows: list[dict[str, Any]] = [
        {
            "dataset": s.dataset,
            "last_sync": format_datetime(s.last_sync),
            "row_count": s.row_count or 0,
            "validation_status": s.validation_status or "-",
        }
        for s in statuses
    ]
    ui_ctx.table(columns=columns, rows=rows).classes("w-full")


async def render_sync_logs(
    user: dict[str, Any],
    sync_service: DataSyncService,
    has_view: bool,
    *,
    ui_module: Any | None = None,
    logger_obj: logging.Logger | None = None,
) -> None:
    """Render sync logs viewer with filters."""
    ui_ctx, logger_ctx = _resolve_dependencies(ui_module, logger_obj)
    if not has_view:
        ui_ctx.label("Sync logs require data-sync view permission").classes("text-gray-500")
        return

    ui_ctx.label("Recent Sync Logs").classes("font-bold mb-2")

    with ui_ctx.row().classes("gap-4 mb-4"):
        dataset_filter = ui_ctx.select(
            label="Dataset",
            options=["all", *TREND_DATASETS],
            value="all",
        ).classes("w-40")
        level_filter = ui_ctx.select(
            label="Level",
            options=["all", "info", "warn", "error"],
            value="all",
        ).classes("w-32")

    log_container = ui_ctx.column().classes("w-full")

    async def load_logs() -> None:
        ds = None if dataset_filter.value == "all" else str(dataset_filter.value)
        lvl = None if level_filter.value == "all" else str(level_filter.value)
        try:
            logs = await sync_service.get_sync_logs(user, dataset=ds, level=lvl)
            log_container.clear()
            with log_container:
                build_sync_logs_table(logs, ui_module=ui_ctx)
        except PermissionError as e:
            ui_ctx.notify(str(e), type="negative")
        except Exception:
            logger_ctx.exception(
                "service_call_failed",
                extra={
                    "method": "get_sync_logs",
                    "service": "DataSyncService",
                    "user_id": get_user_id_safe(user),
                },
            )
            ui_ctx.notify("Service temporarily unavailable", type="warning")

    dataset_filter.on_value_change(lambda _: load_logs())
    level_filter.on_value_change(lambda _: load_logs())

    await load_logs()


def build_sync_logs_table(
    logs: list[Any],
    *,
    ui_module: Any | None = None,
) -> None:
    """Build sync logs table from SyncLogEntry list."""
    ui_ctx, _ = _resolve_dependencies(ui_module, None)
    columns: list[dict[str, Any]] = [
        {
            "name": "created_at",
            "label": "Timestamp",
            "field": "created_at",
            "sortable": True,
        },
        {"name": "dataset", "label": "Dataset", "field": "dataset"},
        {"name": "level", "label": "Level", "field": "level"},
        {"name": "message", "label": "Message", "field": "message"},
    ]
    rows: list[dict[str, Any]] = [
        {
            "created_at": format_datetime(log.created_at),
            "dataset": log.dataset,
            "level": log.level,
            "message": log.message,
        }
        for log in logs
    ]
    ui_ctx.table(columns=columns, rows=rows).classes("w-full")


async def render_sync_schedule(
    user: dict[str, Any],
    sync_service: DataSyncService,
    has_view: bool,
    has_manage: bool,
    *,
    ui_module: Any | None = None,
    logger_obj: logging.Logger | None = None,
) -> None:
    """Render sync schedule configuration with optional inline editing."""
    ui_ctx, logger_ctx = _resolve_dependencies(ui_module, logger_obj)
    ui_ctx.label("Sync Schedule").classes("font-bold mb-2")

    if not has_view:
        ui_ctx.label("Schedule viewing requires data-sync view permission").classes("text-gray-500")
        return

    try:
        schedules = await sync_service.get_sync_schedule(user)
    except PermissionError as e:
        ui_ctx.notify(str(e), type="negative")
        return
    except Exception:
        logger_ctx.exception(
            "service_call_failed",
            extra={
                "method": "get_sync_schedule",
                "service": "DataSyncService",
                "user_id": get_user_id_safe(user),
            },
        )
        ui_ctx.notify("Service temporarily unavailable", type="warning")
        return

    if not has_manage:
        ui_ctx.label("Schedule editing requires MANAGE_SYNC_SCHEDULE permission").classes(
            "text-gray-500 mb-2"
        )

    for sched in schedules:
        with ui_ctx.card().classes("w-full p-4 mb-2"):
            with ui_ctx.row().classes("items-center gap-4"):
                ui_ctx.label(sched.dataset).classes("font-bold w-32")
                ui_ctx.label(f"Cron: {sched.cron_expression}").classes("text-gray-600")
                status_label = "Enabled" if sched.enabled else "Disabled"
                status_color = "text-green-600" if sched.enabled else "text-red-600"
                ui_ctx.label(status_label).classes(status_color)

                if sched.last_scheduled_run:
                    ui_ctx.label(f"Last: {format_datetime(sched.last_scheduled_run)}").classes(
                        "text-sm text-gray-500"
                    )
                if sched.next_scheduled_run:
                    ui_ctx.label(f"Next: {format_datetime(sched.next_scheduled_run)}").classes(
                        "text-sm text-gray-500"
                    )

            if has_manage:
                with ui_ctx.row().classes("items-center gap-4 mt-2"):
                    cron_input = ui_ctx.input(
                        label="Cron Expression",
                        value=sched.cron_expression,
                    ).classes("w-48")
                    enabled_switch = ui_ctx.switch(
                        "Enabled",
                        value=sched.enabled,
                    )

                    _ds = sched.dataset

                    async def save_schedule(
                        ds: str = _ds,
                        cron_el: ui.input = cron_input,
                        enabled_el: ui.switch = enabled_switch,
                    ) -> None:
                        try:
                            update = SyncScheduleUpdateDTO(
                                cron_expression=str(cron_el.value),
                                enabled=bool(enabled_el.value),
                            )
                            result = await sync_service.update_sync_schedule(user, ds, update)
                            ui_ctx.notify(
                                f"Schedule updated for {result.dataset}",
                                type="positive",
                            )
                        except PermissionError as e:
                            ui_ctx.notify(str(e), type="negative")
                        except Exception:
                            logger_ctx.exception(
                                "service_call_failed",
                                extra={
                                    "method": "update_sync_schedule",
                                    "service": "DataSyncService",
                                    "dataset": ds,
                                    "user_id": get_user_id_safe(user),
                                },
                            )
                            ui_ctx.notify("Service temporarily unavailable", type="warning")

                    ui_ctx.button("Save", on_click=save_schedule).props("flat dense")


__all__ = [
    "build_sync_logs_table",
    "build_sync_status_table",
    "render_data_sync_section",
    "render_sync_logs",
    "render_sync_schedule",
    "render_sync_status",
]
