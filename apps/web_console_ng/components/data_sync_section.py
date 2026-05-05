"""Data Sync section for the data-management page."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Literal

from nicegui import ui

from apps.web_console_ng.components.data_management_common import (
    TREND_DATASETS,
    format_datetime,
    get_user_id_safe,
)
from libs.platform.web_console_auth.permissions import Permission, has_permission
from libs.web_console_services.data_manifest_service import (
    ALPACA_SIP_CORP_ACTIONS_DATASET,
    ALPACA_SIP_DAILY_DATASET,
)
from libs.web_console_services.data_sync_service import (
    DataSyncService,
)
from libs.web_console_services.data_sync_service import (
    PreflightRequired as SyncPreflightRequired,
)
from libs.web_console_services.data_sync_service import (
    RateLimitExceeded as SyncRateLimitExceeded,
)
from libs.web_console_services.schemas.data_management import (
    DataAcquisitionJobDTO,
    DataAcquisitionPreflightDTO,
    DataAcquisitionRequestDTO,
    DataAcquisitionSubmitDTO,
    SyncScheduleUpdateDTO,
)

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

    if has_view:
        ui_ctx.label("Dataset Sync Status").classes("font-bold mb-2")
        status_container = ui_ctx.column().classes("w-full")
        try:
            statuses = await sync_service.get_sync_status(user)
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
        ui_ctx.label("Data Acquisition").classes("font-bold mb-2")
        preflight_state: dict[str, DataAcquisitionPreflightDTO | None] = {"value": None}
        preflight_generation = {"value": 0}

        with ui_ctx.row().classes("gap-4 items-end"):
            dataset_input = ui_ctx.select(
                label="Dataset",
                options=[ALPACA_SIP_DAILY_DATASET, ALPACA_SIP_CORP_ACTIONS_DATASET],
                value=ALPACA_SIP_DAILY_DATASET,
            ).classes("w-56")
            start_input = ui_ctx.input(
                label="Start Date",
                placeholder="YYYY-MM-DD",
            ).classes("w-36")
            end_input = ui_ctx.input(
                label="End Date",
                placeholder="YYYY-MM-DD",
            ).classes("w-36")
            symbol_source_input = ui_ctx.input(
                label="Symbols",
                placeholder="AAPL,MSFT or file:data/symbols/core.txt",
            ).classes("w-40")
            mode_input = ui_ctx.select(
                label="Mode",
                options=["backfill"],
                value="backfill",
            ).classes("w-36")
            dry_run_input = ui_ctx.switch("Dry Run", value=True)

            reason_input = ui_ctx.input(
                label="Reason",
                placeholder="Why grab this data now?",
            ).classes("w-64")

        preflight_container = ui_ctx.column().classes("w-full mt-3")
        job_container = ui_ctx.column().classes("w-full mt-3")

        with ui_ctx.row().classes("gap-2 mt-2"):
            suppress_invalidation = {"value": False}

            def invalidate_preflight(_: Any = None) -> None:
                if suppress_invalidation["value"]:
                    return
                preflight_generation["value"] += 1
                if preflight_state["value"] is None:
                    return
                preflight_state["value"] = None
                preflight_container.clear()
                job_container.clear()

            def reset_acquisition_form() -> None:
                suppress_invalidation["value"] = True
                try:
                    start_input.value = ""
                    end_input.value = ""
                    symbol_source_input.value = ""
                    mode_input.value = "backfill"
                    dry_run_input.value = True
                    reason_input.value = ""
                finally:
                    suppress_invalidation["value"] = False

            async def run_preflight() -> None:
                invalidate_preflight()
                request_generation = preflight_generation["value"]
                dataset_value = str(dataset_input.value or "")
                reason_value = _normalize_sync_reason(reason_input.value)
                if not dataset_value:
                    ui_ctx.notify("Please select a dataset", type="warning")
                    return
                if not reason_value:
                    ui_ctx.notify("Please provide a reason for audit logging", type="warning")
                    return
                try:
                    request = DataAcquisitionRequestDTO(
                        dataset=dataset_value,
                        start_date=_parse_date_input(start_input.value, "start date"),
                        end_date=_parse_date_input(end_input.value, "end date"),
                        symbol_source=str(symbol_source_input.value or "").strip(),
                        mode=_normalize_acquisition_mode(mode_input.value),
                        adjustment_mode=(
                            "raw" if dataset_value == ALPACA_SIP_DAILY_DATASET else None
                        ),
                        reason=reason_value,
                        dry_run=bool(dry_run_input.value),
                    )
                    preflight = await sync_service.preflight_acquisition(user, request)
                    if request_generation != preflight_generation["value"]:
                        ui_ctx.notify("Acquisition inputs changed; rerun preflight", type="warning")
                        return
                    preflight_state["value"] = preflight
                    preflight_container.clear()
                    with preflight_container:
                        render_acquisition_preflight(preflight, ui_module=ui_ctx)
                    job_container.clear()
                    ui_ctx.notify("Acquisition preflight ready", type="positive")
                except ValueError as e:
                    ui_ctx.notify(str(e), type="warning")
                except SyncRateLimitExceeded:
                    ui_ctx.notify("Rate limit reached; wait before retrying", type="warning")
                except PermissionError as e:
                    ui_ctx.notify(str(e), type="negative")
                except Exception:
                    logger_ctx.exception(
                        "service_call_failed",
                        extra={
                            "method": "preflight_acquisition",
                            "service": "DataSyncService",
                            "dataset": dataset_value,
                            "user_id": get_user_id_safe(user),
                        },
                    )
                    ui_ctx.notify("Service temporarily unavailable", type="warning")

            async def submit_preflight() -> None:
                preflight = preflight_state["value"]
                if preflight is None:
                    ui_ctx.notify("Run preflight before submitting", type="warning")
                    return

                def clear_preflight_after_submit_error(
                    message: str,
                    *,
                    msg_type: str = "warning",
                ) -> None:
                    preflight_state["value"] = None
                    preflight_container.clear()
                    ui_ctx.notify(message, type=msg_type)

                try:
                    job = await sync_service.submit_acquisition(
                        user,
                        DataAcquisitionSubmitDTO(
                            idempotency_key=preflight.idempotency_key,
                            submit_token=preflight.submit_token,
                        ),
                    )
                    job_container.clear()
                    with job_container:
                        render_acquisition_job(job, ui_module=ui_ctx)
                    preflight_state["value"] = None
                    preflight_container.clear()
                    reset_acquisition_form()
                    notification = (
                        f"Acquisition scope already covered by job {job.id} ({job.status})"
                        if "duplicate_submission_reused_existing_job" in job.logs
                        else f"Acquisition job {job.id} {job.status}"
                    )
                    ui_ctx.notify(notification, type="positive")
                except SyncPreflightRequired as e:
                    clear_preflight_after_submit_error(str(e))
                except SyncRateLimitExceeded:
                    clear_preflight_after_submit_error("Rate limit reached; wait before retrying")
                except PermissionError as e:
                    clear_preflight_after_submit_error(str(e), msg_type="negative")
                except Exception:
                    logger_ctx.exception(
                        "service_call_failed",
                        extra={
                            "method": "submit_acquisition",
                            "service": "DataSyncService",
                            "dataset": preflight.dataset,
                            "user_id": get_user_id_safe(user),
                        },
                    )
                    clear_preflight_after_submit_error("Service temporarily unavailable")

            ui_ctx.button("Preflight", on_click=run_preflight, color="primary")
            ui_ctx.button("Submit Job", on_click=submit_preflight).props("outline")
            for acquisition_input in (
                dataset_input,
                start_input,
                end_input,
                symbol_source_input,
                mode_input,
                dry_run_input,
                reason_input,
            ):
                acquisition_input.on_value_change(invalidate_preflight)

        ui_ctx.separator().classes("my-4")
        ui_ctx.label("Manual Sync").classes("font-bold mb-2")
        with ui_ctx.row().classes("gap-4 items-end"):
            manual_dataset_input = ui_ctx.select(
                label="Sync Dataset",
                options=list(TREND_DATASETS),
                value=TREND_DATASETS[0],
            ).classes("w-48")
            manual_reason_input = ui_ctx.input(
                label="Sync Reason",
                placeholder="Why run this sync now?",
            ).classes("w-64")

            async def trigger_sync() -> None:
                dataset_value = str(manual_dataset_input.value or "")
                reason_value = _normalize_sync_reason(manual_reason_input.value)
                if not dataset_value:
                    ui_ctx.notify("Please select a dataset", type="warning")
                    return
                if not reason_value:
                    ui_ctx.notify("Please provide a reason for audit logging", type="warning")
                    return
                try:
                    job = await sync_service.trigger_sync(user, dataset_value, reason_value)
                    ui_ctx.notify(f"Sync job {job.id} queued for {job.dataset}", type="positive")
                    manual_reason_input.value = ""
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
                            "dataset": dataset_value,
                            "user_id": get_user_id_safe(user),
                        },
                    )
                    ui_ctx.notify("Service temporarily unavailable", type="warning")

            ui_ctx.button("Trigger Sync", on_click=trigger_sync, color="primary")

    return status_container


def _parse_date_input(value: Any, label: str) -> date:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Please provide a {label}")
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        raise ValueError(f"Invalid {label}; expected YYYY-MM-DD")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"Invalid {label}; expected YYYY-MM-DD") from exc


def _normalize_acquisition_mode(value: Any) -> Literal["backfill"]:
    text = str(value or "backfill").strip()
    if text == "backfill":
        return "backfill"
    raise ValueError("Mode must be backfill")


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


def build_acquisition_preflight_rows(
    preflight: DataAcquisitionPreflightDTO,
) -> list[dict[str, str]]:
    """Build UI-safe preflight rows without exposing the submit token value."""
    requested_start = preflight.requested_start_date or preflight.start_date
    requested_end = preflight.requested_end_date or preflight.end_date
    return [
        {"field": "Dataset", "value": preflight.dataset},
        {"field": "Requested date range", "value": f"{requested_start} to {requested_end}"},
        {
            "field": "Effective acquisition scope",
            "value": f"{preflight.start_date} to {preflight.end_date}",
        },
        {"field": "Symbols", "value": preflight.symbol_source},
        {"field": "Mode", "value": preflight.mode},
        {"field": "Dry run", "value": str(preflight.dry_run).lower()},
        {"field": "Provider", "value": preflight.provider_id},
        {"field": "Source feed", "value": preflight.source_feed},
        {"field": "Canonical storage", "value": preflight.canonical_storage_mode},
        {
            "field": "Read-time adjustment",
            "value": preflight.read_time_adjustment_mode or "-",
        },
        {"field": "Adjustment mode", "value": preflight.adjustment_mode or "-"},
        {"field": "Idempotency key", "value": preflight.idempotency_key},
        {"field": "Submit token status", "value": preflight.submit_token_status},
        {
            "field": "Submit token expires",
            "value": format_datetime(preflight.submit_token_expires_at),
        },
        {
            "field": "Supported semantics",
            "value": "; ".join(preflight.supported_semantics),
        },
        {"field": "Warnings", "value": "; ".join(preflight.warnings) or "-"},
        {"field": "Preflight logs", "value": "; ".join(preflight.logs)},
    ]


def render_acquisition_preflight(
    preflight: DataAcquisitionPreflightDTO,
    *,
    ui_module: Any | None = None,
) -> None:
    """Render acquisition preflight details without exposing the submit token."""
    ui_ctx, _ = _resolve_dependencies(ui_module, None)
    columns = [
        {"name": "field", "label": "Field", "field": "field"},
        {"name": "value", "label": "Value", "field": "value"},
    ]
    ui_ctx.table(
        columns=columns,
        rows=build_acquisition_preflight_rows(preflight),
    ).classes("w-full")


def build_acquisition_job_rows(job: DataAcquisitionJobDTO) -> list[dict[str, str]]:
    """Build UI-safe acquisition job rows."""
    return [
        {"field": "Job ID", "value": job.id},
        {"field": "Dataset", "value": job.dataset},
        {"field": "Status", "value": job.status},
        {"field": "Idempotency key", "value": job.idempotency_key},
        {"field": "Mode", "value": job.mode},
        {"field": "Dry run", "value": str(job.dry_run).lower()},
        {"field": "Provider", "value": job.provider_id},
        {"field": "Source feed", "value": job.source_feed},
        {"field": "Canonical storage", "value": job.canonical_storage_mode},
        {"field": "Adjustment mode", "value": job.adjustment_mode or "-"},
        {"field": "Submit token status", "value": job.submit_token_status},
        {"field": "Adapter", "value": job.adapter},
        {"field": "Started", "value": format_datetime(job.started_at)},
        {"field": "Produced manifests", "value": "; ".join(job.produced_manifest_ids) or "-"},
        {"field": "Validation", "value": "; ".join(job.validation_output)},
        {"field": "Logs", "value": "; ".join(job.logs)},
    ]


def render_acquisition_job(
    job: DataAcquisitionJobDTO,
    *,
    ui_module: Any | None = None,
) -> None:
    """Render acquisition job state and logs."""
    ui_ctx, _ = _resolve_dependencies(ui_module, None)
    columns = [
        {"name": "field", "label": "Field", "field": "field"},
        {"name": "value", "label": "Value", "field": "value"},
    ]
    ui_ctx.table(columns=columns, rows=build_acquisition_job_rows(job)).classes("w-full")


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
