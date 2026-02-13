"""Data Management page for NiceGUI web console (P6T13).

Combines Data Sync, Data Explorer, and Data Quality into a unified dashboard.
All sections are wired to backend services with per-capability RBAC gating.

Features:
    - Data Sync: Sync status, manual sync, sync logs, schedule config
    - Data Explorer: Dataset browser, schema viewer, query editor
    - Data Quality: Validation results, anomaly alerts, trends, quarantine

Services:
    - DataSyncService: Sync status, logs, schedule, manual trigger
    - DataExplorerService: Dataset browsing, SQL queries, export
    - DataQualityService: Validation, anomaly alerts, trends, quarantine

TODO: This module has grown large. Refactor Sync, Explorer, and Quality tabs
into independent component modules under apps/web_console_ng/components/.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
from nicegui import ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console_ng.utils.session import get_or_create_client_id
from libs.data.data_quality.quality_scorer import (
    compute_quality_scores,
    compute_trend_summary,
    normalize_validation_status,
)
from libs.data.data_quality.validation import validate_quarantine_path
from libs.platform.web_console_auth.permissions import Permission, has_permission
from libs.web_console_services.data_explorer_service import DataExplorerService
from libs.web_console_services.data_explorer_service import (
    RateLimitExceeded as ExplorerRateLimitExceeded,
)
from libs.web_console_services.data_quality_service import DataQualityService
from libs.web_console_services.data_sync_service import DataSyncService
from libs.web_console_services.data_sync_service import (
    RateLimitExceeded as SyncRateLimitExceeded,
)
from libs.web_console_services.schemas.data_management import SyncScheduleUpdateDTO

logger = logging.getLogger(__name__)

# Rate limits (displayed in UI messages)
MAX_QUERIES_PER_MINUTE = 10
MAX_EXPORTS_PER_HOUR = 5

# Dataset name pattern for quarantine drill-down (64-char cap aligns with typical naming)
_DATASET_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Default data directory for quarantine path validation
_DATA_DIR = Path("data")

# Severity normalization: raw service values -> canonical UI levels
_SEVERITY_MAP: dict[str, str] = {
    "critical": "critical",
    "high": "high",
    "warning": "medium",
    "medium": "medium",
    "low": "low",
    "info": "low",
}

# Severity -> CSS classes for alert cards
_SEVERITY_COLORS: dict[str, str] = {
    "critical": "bg-red-200 border-red-600 text-red-800",
    "high": "bg-red-100 border-red-500 text-red-700",
    "medium": "bg-yellow-100 border-yellow-500 text-yellow-700",
    "low": "bg-blue-100 border-blue-500 text-blue-700",
}

# Acknowledged filter mapping: UI label -> service parameter
_ACK_MAP: dict[str, bool | None] = {"all": None, "unacked": False, "acked": True}

# Timer cleanup owner key (page-scoped, replaces only this page's callback)
_CLEANUP_OWNER_KEY = "data_management_timers"


def _format_datetime(dt: Any) -> str:
    """Format a datetime for display, handling None and non-datetime values."""
    if dt is not None and hasattr(dt, "isoformat"):
        return str(dt.isoformat())
    return "-"


def _get_user_id_safe(user: Any) -> str | None:
    """Extract user_id from user dict or object safely."""
    if isinstance(user, dict):
        val = user.get("id")
        return str(val) if val is not None else None
    val = getattr(user, "id", None)
    return str(val) if val is not None else None


@ui.page("/data")
@requires_auth
@main_layout
async def data_management_page() -> None:
    """Data Management page with live service integration."""
    user = get_current_user()

    # Instantiate services at page-load time (not module level)
    sync_service = DataSyncService()
    explorer_service = DataExplorerService()
    quality_service = DataQualityService()

    # Page title
    ui.label("Data Management").classes("text-2xl font-bold mb-4")

    # Per-capability tab visibility
    has_sync = has_permission(user, Permission.VIEW_DATA_SYNC)
    has_trigger = has_permission(user, Permission.TRIGGER_DATA_SYNC)
    has_schedule = has_permission(user, Permission.MANAGE_SYNC_SCHEDULE)
    show_sync_tab = has_sync or has_trigger or has_schedule

    has_view_datasets = has_permission(user, Permission.VIEW_DATA_SYNC)
    has_query = has_permission(user, Permission.QUERY_DATA)
    show_explorer_tab = has_view_datasets or has_query

    has_quality = has_permission(user, Permission.VIEW_DATA_QUALITY)

    # Main tabs for the three data modules
    with ui.tabs().classes("w-full") as tabs:
        if show_sync_tab:
            tab_sync = ui.tab("Data Sync")
        if show_explorer_tab:
            tab_explorer = ui.tab("Data Explorer")
        if has_quality:
            tab_quality = ui.tab("Data Quality")

    # Determine default tab
    default_tab = None
    if show_sync_tab:
        default_tab = tab_sync
    elif show_explorer_tab:
        default_tab = tab_explorer
    elif has_quality:
        default_tab = tab_quality

    if default_tab is None:
        ui.label("No data permissions assigned. Contact your administrator.").classes(
            "text-gray-500"
        )
        return

    # Overlap guard flags (per-client scope — each page load creates a new function scope)
    _sync_refreshing = False
    _alerts_refreshing = False

    # Containers for refreshable content
    sync_status_container: ui.column | None = None
    alerts_container: ui.column | None = None
    scores_container: ui.column | None = None

    with ui.tab_panels(tabs, value=default_tab).classes("w-full"):
        if show_sync_tab:
            with ui.tab_panel(tab_sync):
                sync_status_container = await _render_data_sync_section(
                    user, sync_service
                )

        if show_explorer_tab:
            with ui.tab_panel(tab_explorer):
                await _render_data_explorer_section(user, explorer_service)

        if has_quality:
            with ui.tab_panel(tab_quality):
                alerts_container, scores_container, _load_alerts_fn = (
                    await _render_data_quality_section(user, quality_service)
                )

    # === Auto-refresh Timers ===
    async def refresh_sync_status() -> None:
        nonlocal _sync_refreshing
        if _sync_refreshing or sync_status_container is None:
            return
        _sync_refreshing = True
        try:
            if not has_permission(user, Permission.VIEW_DATA_SYNC):
                return
            statuses = await sync_service.get_sync_status(user)
            sync_status_container.clear()
            with sync_status_container:
                _build_sync_status_table(statuses)
        except Exception:
            logger.exception(
                "refresh_sync_status_failed",
                extra={
                    "service": "DataSyncService",
                    "method": "get_sync_status",
                    "user_id": _get_user_id_safe(user),
                },
            )
        finally:
            _sync_refreshing = False

    async def refresh_alerts() -> None:
        nonlocal _alerts_refreshing
        if _alerts_refreshing or _load_alerts_fn is None:
            return
        _alerts_refreshing = True
        try:
            await _load_alerts_fn()
        except Exception:
            logger.exception(
                "refresh_alerts_failed",
                extra={
                    "service": "DataQualityService",
                    "method": "get_anomaly_alerts",
                    "user_id": _get_user_id_safe(user),
                },
            )
        finally:
            _alerts_refreshing = False

    # Overlap guard for scores refresh
    _scores_refreshing = False

    async def refresh_scores() -> None:
        nonlocal _scores_refreshing
        if _scores_refreshing or scores_container is None:
            return
        _scores_refreshing = True
        try:
            scores_container.clear()
            with scores_container:
                await _build_quality_score_cards(user, quality_service)
        except Exception:
            logger.exception(
                "refresh_scores_failed",
                extra={
                    "service": "DataQualityService",
                    "method": "refresh_scores",
                    "user_id": _get_user_id_safe(user),
                },
            )
        finally:
            _scores_refreshing = False

    # Timers: sync (30s), alerts (60s), scores (60s)
    timer_sync = ui.timer(30.0, refresh_sync_status)
    timer_alerts = ui.timer(60.0, refresh_alerts)
    timer_scores = ui.timer(60.0, refresh_scores)

    # Unified timer cleanup
    async def _cleanup_timers() -> None:
        timer_sync.cancel()
        timer_alerts.cancel()
        timer_scores.cancel()

    # Register keyed cleanup callback
    lifecycle = ClientLifecycleManager.get()
    client_id = get_or_create_client_id()
    if client_id:
        await lifecycle.register_client(client_id)
        await lifecycle.register_cleanup_callback(
            client_id, _cleanup_timers, owner_key=_CLEANUP_OWNER_KEY
        )


# =============================================================================
# Data Sync Section
# =============================================================================


async def _render_data_sync_section(
    user: dict[str, Any],
    sync_service: DataSyncService,
) -> ui.column | None:
    """Render Data Sync dashboard section. Returns the status container for refresh."""
    ui.label("Data Sync Dashboard").classes("text-xl font-bold mb-2")

    has_view = has_permission(user, Permission.VIEW_DATA_SYNC)
    has_trigger = has_permission(user, Permission.TRIGGER_DATA_SYNC)
    has_manage = has_permission(user, Permission.MANAGE_SYNC_SCHEDULE)

    with ui.tabs().classes("w-full") as sync_tabs:
        tab_status = ui.tab("Sync Status")
        tab_logs = ui.tab("Sync Logs")
        tab_schedule = ui.tab("Schedule Config")

    sync_status_container: ui.column | None = None

    with ui.tab_panels(sync_tabs, value=tab_status).classes("w-full"):
        with ui.tab_panel(tab_status):
            sync_status_container = await _render_sync_status(
                user, sync_service, has_view, has_trigger
            )

        with ui.tab_panel(tab_logs):
            await _render_sync_logs(user, sync_service, has_view)

        with ui.tab_panel(tab_schedule):
            await _render_sync_schedule(user, sync_service, has_view, has_manage)

    return sync_status_container


async def _render_sync_status(
    user: dict[str, Any],
    sync_service: DataSyncService,
    has_view: bool,
    has_trigger: bool,
) -> ui.column | None:
    """Render sync status table and manual trigger. Returns container for refresh."""
    # Status table (requires VIEW_DATA_SYNC)
    status_container: ui.column | None = None
    dataset_names: list[str] = []

    if has_view:
        ui.label("Dataset Sync Status").classes("font-bold mb-2")
        status_container = ui.column().classes("w-full")
        try:
            statuses = await sync_service.get_sync_status(user)
            dataset_names = [s.dataset for s in statuses]
            with status_container:
                _build_sync_status_table(statuses)
        except PermissionError as e:
            ui.notify(str(e), type="negative")
        except Exception:
            logger.exception(
                "service_call_failed",
                extra={
                    "method": "get_sync_status",
                    "service": "DataSyncService",
                    "user_id": _get_user_id_safe(user),
                },
            )
            ui.notify("Service temporarily unavailable", type="warning")
    else:
        ui.label("Sync status requires data-sync view permission").classes(
            "text-gray-500"
        )

    # Manual sync section (requires TRIGGER_DATA_SYNC)
    if has_trigger:
        ui.separator().classes("my-4")
        ui.label("Manual Sync").classes("font-bold mb-2")

        with ui.row().classes("gap-4 items-end"):
            dataset_input: Any  # ui.select or ui.input depending on permissions
            if dataset_names:
                dataset_input = ui.select(
                    label="Dataset",
                    options=dataset_names,
                    value=dataset_names[0],
                ).classes("w-48")
            else:
                # Fallback: text input when user lacks VIEW_DATA_SYNC
                dataset_input = ui.input(
                    label="Dataset Name",
                    placeholder="Enter dataset name",
                ).classes("w-48")

            reason_input = ui.input(
                label="Reason",
                placeholder="Why run this sync now?",
            ).classes("w-64")

            async def trigger_sync() -> None:
                dataset_val = dataset_input.value
                if not dataset_val:
                    ui.notify("Please select a dataset", type="warning")
                    return
                if not reason_input.value:
                    ui.notify(
                        "Please provide a reason for audit logging", type="warning"
                    )
                    return
                try:
                    job = await sync_service.trigger_sync(
                        user, str(dataset_val), str(reason_input.value)
                    )
                    ui.notify(
                        f"Sync job {job.id} queued for {job.dataset}", type="positive"
                    )
                    reason_input.value = ""
                except SyncRateLimitExceeded:
                    ui.notify("Rate limit: 1 sync per minute", type="warning")
                except PermissionError as e:
                    ui.notify(str(e), type="negative")
                except Exception:
                    logger.exception(
                        "service_call_failed",
                        extra={
                            "method": "trigger_sync",
                            "service": "DataSyncService",
                            "dataset": str(dataset_val),
                            "user_id": _get_user_id_safe(user),
                        },
                    )
                    ui.notify("Service temporarily unavailable", type="warning")

            ui.button("Trigger Sync", on_click=trigger_sync, color="primary")

    return status_container


def _build_sync_status_table(statuses: list[Any]) -> None:
    """Build the sync status table from SyncStatusDTO list."""
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
            "last_sync": _format_datetime(s.last_sync),
            "row_count": s.row_count or 0,
            "validation_status": s.validation_status or "-",
        }
        for s in statuses
    ]
    ui.table(columns=columns, rows=rows).classes("w-full")


async def _render_sync_logs(
    user: dict[str, Any],
    sync_service: DataSyncService,
    has_view: bool,
) -> None:
    """Render sync logs viewer with filters."""
    if not has_view:
        ui.label("Sync logs require data-sync view permission").classes("text-gray-500")
        return

    ui.label("Recent Sync Logs").classes("font-bold mb-2")

    # Filter controls
    with ui.row().classes("gap-4 mb-4"):
        dataset_filter = ui.select(
            label="Dataset",
            options=["all"],
            value="all",
        ).classes("w-40")
        level_filter = ui.select(
            label="Level",
            options=["all", "info", "warn", "error"],
            value="all",
        ).classes("w-32")

    log_container = ui.column().classes("w-full")

    async def load_logs() -> None:
        ds = None if dataset_filter.value == "all" else str(dataset_filter.value)
        lvl = None if level_filter.value == "all" else str(level_filter.value)
        try:
            logs = await sync_service.get_sync_logs(user, dataset=ds, level=lvl)
            log_container.clear()
            with log_container:
                _build_sync_logs_table(logs)
        except PermissionError as e:
            ui.notify(str(e), type="negative")
        except Exception:
            logger.exception(
                "service_call_failed",
                extra={
                    "method": "get_sync_logs",
                    "service": "DataSyncService",
                    "user_id": _get_user_id_safe(user),
                },
            )
            ui.notify("Service temporarily unavailable", type="warning")

    dataset_filter.on_value_change(lambda _: load_logs())
    level_filter.on_value_change(lambda _: load_logs())

    # Initial load
    await load_logs()


def _build_sync_logs_table(logs: list[Any]) -> None:
    """Build sync logs table from SyncLogEntry list."""
    _level_colors: dict[str, str] = {
        "info": "text-gray-600",
        "warn": "text-amber-600",
        "error": "text-red-600",
    }
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
            "created_at": _format_datetime(log.created_at),
            "dataset": log.dataset,
            "level": log.level,
            "message": log.message,
        }
        for log in logs
    ]
    ui.table(columns=columns, rows=rows).classes("w-full")


async def _render_sync_schedule(
    user: dict[str, Any],
    sync_service: DataSyncService,
    has_view: bool,
    has_manage: bool,
) -> None:
    """Render sync schedule configuration with optional inline editing."""
    ui.label("Sync Schedule").classes("font-bold mb-2")

    if not has_view:
        ui.label("Schedule viewing requires data-sync view permission").classes(
            "text-gray-500"
        )
        return

    try:
        schedules = await sync_service.get_sync_schedule(user)
    except PermissionError as e:
        ui.notify(str(e), type="negative")
        return
    except Exception:
        logger.exception(
            "service_call_failed",
            extra={
                "method": "get_sync_schedule",
                "service": "DataSyncService",
                "user_id": _get_user_id_safe(user),
            },
        )
        ui.notify("Service temporarily unavailable", type="warning")
        return

    if not has_manage:
        ui.label("Schedule editing requires MANAGE_SYNC_SCHEDULE permission").classes(
            "text-gray-500 mb-2"
        )

    for sched in schedules:
        with ui.card().classes("w-full p-4 mb-2"):
            with ui.row().classes("items-center gap-4"):
                ui.label(sched.dataset).classes("font-bold w-32")
                ui.label(f"Cron: {sched.cron_expression}").classes("text-gray-600")
                status_label = "Enabled" if sched.enabled else "Disabled"
                status_color = "text-green-600" if sched.enabled else "text-red-600"
                ui.label(status_label).classes(status_color)

                if sched.last_scheduled_run:
                    ui.label(
                        f"Last: {_format_datetime(sched.last_scheduled_run)}"
                    ).classes("text-sm text-gray-500")
                if sched.next_scheduled_run:
                    ui.label(
                        f"Next: {_format_datetime(sched.next_scheduled_run)}"
                    ).classes("text-sm text-gray-500")

            # Inline edit (requires MANAGE_SYNC_SCHEDULE)
            if has_manage:
                with ui.row().classes("items-center gap-4 mt-2"):
                    cron_input = ui.input(
                        label="Cron Expression",
                        value=sched.cron_expression,
                    ).classes("w-48")
                    enabled_switch = ui.switch(
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
                            result = await sync_service.update_sync_schedule(
                                user, ds, update
                            )
                            ui.notify(
                                f"Schedule updated for {result.dataset}",
                                type="positive",
                            )
                        except PermissionError as e:
                            ui.notify(str(e), type="negative")
                        except Exception:
                            logger.exception(
                                "service_call_failed",
                                extra={
                                    "method": "update_sync_schedule",
                                    "service": "DataSyncService",
                                    "dataset": ds,
                                    "user_id": _get_user_id_safe(user),
                                },
                            )
                            ui.notify(
                                "Service temporarily unavailable", type="warning"
                            )

                    ui.button("Save", on_click=save_schedule).props("flat dense")


# =============================================================================
# Data Explorer Section
# =============================================================================


async def _render_data_explorer_section(
    user: dict[str, Any],
    explorer_service: DataExplorerService,
) -> None:
    """Render Data Explorer section with per-capability gating."""
    has_view_datasets = has_permission(user, Permission.VIEW_DATA_SYNC)
    has_query = has_permission(user, Permission.QUERY_DATA)
    has_export = has_permission(user, Permission.EXPORT_DATA)

    ui.label("Data Explorer").classes("text-xl font-bold mb-2")

    # Track selected dataset
    selected_dataset: dict[str, str | None] = {"value": None}

    with ui.row().classes("w-full gap-4"):
        # Dataset browser sidebar
        with ui.card().classes("w-64 p-4"):
            ui.label("Datasets").classes("font-bold mb-2")

            if has_view_datasets:
                try:
                    datasets = await explorer_service.list_datasets(user)
                except PermissionError as e:
                    ui.notify(str(e), type="negative")
                    datasets = []
                except Exception:
                    logger.exception(
                        "service_call_failed",
                        extra={
                            "method": "list_datasets",
                            "service": "DataExplorerService",
                            "user_id": _get_user_id_safe(user),
                        },
                    )
                    ui.notify("Service temporarily unavailable", type="warning")
                    datasets = []

                dataset_names = [d.name for d in datasets]
                dataset_map = {d.name: d for d in datasets}

                if dataset_names:
                    selected_dataset["value"] = dataset_names[0]

                dataset_select = ui.select(
                    label="Select Dataset",
                    options=dataset_names,
                    value=dataset_names[0] if dataset_names else None,
                ).classes("w-full")

                ui.separator().classes("my-4")

                metadata_container = ui.column().classes("w-full")

                schema_container = ui.column().classes("w-full")

                def _show_dataset_info(ds_name: str | None) -> None:
                    """Show metadata for the selected dataset."""
                    metadata_container.clear()
                    if not ds_name or ds_name not in dataset_map:
                        return
                    info = dataset_map[ds_name]
                    with metadata_container:
                        ui.label("Dataset Info").classes("font-bold mb-1")
                        if info.description:
                            ui.label(info.description).classes(
                                "text-sm text-gray-600"
                            )
                        if info.row_count is not None:
                            ui.label(f"Rows: {info.row_count:,}").classes(
                                "text-sm text-gray-600"
                            )
                        if info.symbol_count is not None:
                            ui.label(f"Symbols: {info.symbol_count:,}").classes(
                                "text-sm text-gray-600"
                            )
                        if info.date_range:
                            start = info.date_range.get("start", "?")
                            end = info.date_range.get("end", "?")
                            ui.label(f"Range: {start} to {end}").classes(
                                "text-sm text-gray-600"
                            )

                async def _load_schema(ds_name: str | None) -> None:
                    """Load schema preview for dataset (requires QUERY_DATA)."""
                    schema_container.clear()
                    if not ds_name or not has_query:
                        with schema_container:
                            if not has_query:
                                ui.label("Schema requires query permission").classes(
                                    "text-sm text-gray-400"
                                )
                        return
                    try:
                        preview = await explorer_service.get_dataset_preview(
                            user, ds_name, limit=5
                        )
                        with schema_container:
                            ui.label("Schema Preview").classes("font-bold mb-1")
                            for col in preview.columns:
                                ui.label(f"  {col}").classes(
                                    "text-sm text-gray-600 font-mono"
                                )
                    except PermissionError:
                        with schema_container:
                            ui.label("Schema requires query permission").classes(
                                "text-sm text-gray-400"
                            )
                    except Exception:
                        logger.exception(
                            "schema_preview_failed",
                            extra={
                                "method": "get_dataset_preview",
                                "service": "DataExplorerService",
                                "dataset": ds_name,
                                "user_id": _get_user_id_safe(user),
                            },
                        )

                async def on_dataset_change(e: Any) -> None:
                    ds = str(e.value) if e.value else None
                    selected_dataset["value"] = ds
                    _show_dataset_info(ds)
                    await _load_schema(ds)

                dataset_select.on_value_change(on_dataset_change)

                # Initial load
                _show_dataset_info(selected_dataset["value"])
                await _load_schema(selected_dataset["value"])
            else:
                ui.label("Dataset listing requires data-sync view permission").classes(
                    "text-gray-400 text-sm"
                )

        # Main content area
        with ui.column().classes("flex-1"):
            # Query editor
            with ui.card().classes("w-full p-4 mb-4"):
                ui.label("Query Editor").classes("font-bold mb-2")

                if has_query:
                    query_textarea = ui.textarea(
                        label="SQL Query",
                        placeholder="SELECT * FROM dataset LIMIT 10",
                        value="",
                    ).classes("w-full font-mono")

                    results_container = ui.column().classes("w-full mt-4")

                    with ui.row().classes("gap-2 mt-2"):

                        async def run_query() -> None:
                            ds = selected_dataset["value"]
                            if not ds:
                                ui.notify("Please select a dataset", type="warning")
                                return
                            query_val = str(query_textarea.value).strip()
                            if not query_val:
                                ui.notify("Please enter a query", type="warning")
                                return
                            try:
                                result = await explorer_service.execute_query(
                                    user, ds, query_val
                                )
                                results_container.clear()
                                with results_container:
                                    _build_query_results(result)
                            except ValueError as e:
                                ui.notify(f"Query error: {e}", type="negative")
                            except ExplorerRateLimitExceeded:
                                ui.notify(
                                    f"Rate limit: {MAX_QUERIES_PER_MINUTE} queries/minute",
                                    type="warning",
                                )
                            except PermissionError as e:
                                ui.notify(str(e), type="negative")
                            except Exception:
                                logger.exception(
                                    "service_call_failed",
                                    extra={
                                        "method": "execute_query",
                                        "service": "DataExplorerService",
                                        "dataset": ds,
                                        "user_id": _get_user_id_safe(user),
                                    },
                                )
                                ui.notify(
                                    "Service temporarily unavailable", type="warning"
                                )

                        ui.button(
                            "Run Query", on_click=run_query, color="primary"
                        )

                        if has_export:
                            export_format: dict[str, str] = {"value": "csv"}
                            ui.radio(
                                ["csv", "parquet"],
                                value="csv",
                                on_change=lambda e: export_format.update(
                                    {"value": str(e.value)}
                                ),
                            ).props("inline")

                            async def export_data() -> None:
                                ds = selected_dataset["value"]
                                if not ds:
                                    ui.notify(
                                        "Please select a dataset", type="warning"
                                    )
                                    return
                                query_val = str(query_textarea.value).strip()
                                if not query_val:
                                    ui.notify(
                                        "Please enter a query to export",
                                        type="warning",
                                    )
                                    return
                                fmt = export_format["value"]
                                try:
                                    job = await explorer_service.export_data(
                                        user, ds, query_val, fmt  # type: ignore[arg-type]
                                    )
                                    ui.notify(
                                        f"Export job {job.id} queued ({fmt})",
                                        type="positive",
                                    )
                                except ExplorerRateLimitExceeded:
                                    ui.notify(
                                        f"Rate limit: {MAX_EXPORTS_PER_HOUR} exports/hour",
                                        type="warning",
                                    )
                                except PermissionError as e:
                                    ui.notify(str(e), type="negative")
                                except Exception:
                                    logger.exception(
                                        "service_call_failed",
                                        extra={
                                            "method": "export_data",
                                            "service": "DataExplorerService",
                                            "dataset": ds,
                                            "user_id": _get_user_id_safe(user),
                                        },
                                    )
                                    ui.notify(
                                        "Service temporarily unavailable",
                                        type="warning",
                                    )

                            ui.button(
                                "Export Results", on_click=export_data
                            ).props("flat")
                        elif has_permission(user, Permission.EXPORT_DATA) is False:
                            pass  # Export button hidden when no permission
                else:
                    ui.label("Query execution requires QUERY_DATA permission").classes(
                        "text-gray-400"
                    )


def _build_query_results(result: Any) -> None:
    """Build query results table from QueryResultDTO."""
    if not result.columns:
        ui.label("No results").classes("text-gray-500")
        return

    columns: list[dict[str, Any]] = [
        {"name": col, "label": col, "field": col, "sortable": True}
        for col in result.columns
    ]
    ui.table(columns=columns, rows=result.rows).classes("w-full")

    with ui.row().classes("gap-4 mt-2"):
        ui.label(f"Total: {result.total_count} rows").classes("text-sm text-gray-600")
        if result.has_more:
            ui.label("(more results available)").classes(
                "text-sm text-amber-600"
            )


# =============================================================================
# Data Quality Section
# =============================================================================


async def _render_data_quality_section(
    user: dict[str, Any],
    quality_service: DataQualityService,
) -> tuple[ui.column | None, ui.column | None, Callable[[], Any] | None]:
    """Render Data Quality reports section.

    Returns:
        Tuple of (alerts_container, scores_container, load_alerts_fn) for
        auto-refresh timers. load_alerts_fn respects current filter state.
    """
    ui.label("Data Quality Reports").classes("text-xl font-bold mb-2")

    # Quality score cards at top of section
    scores_container = ui.column().classes("w-full mb-4")
    with scores_container:
        await _build_quality_score_cards(user, quality_service)

    with ui.tabs().classes("w-full") as quality_tabs:
        tab_validation = ui.tab("Validation Results")
        tab_anomalies = ui.tab("Anomaly Alerts")
        tab_trends = ui.tab("Quality Trends")
        tab_quarantine = ui.tab("Quarantine Inspector")

    alerts_container: ui.column | None = None
    load_alerts_fn: Callable[[], Any] | None = None

    with ui.tab_panels(quality_tabs, value=tab_validation).classes("w-full"):
        with ui.tab_panel(tab_validation):
            await _render_validation_results(user, quality_service)

        with ui.tab_panel(tab_anomalies):
            alerts_container, load_alerts_fn = await _render_anomaly_alerts(
                user, quality_service
            )

        with ui.tab_panel(tab_trends):
            await _render_quality_trends(user, quality_service)

        with ui.tab_panel(tab_quarantine):
            await _render_quarantine_inspector(user, quality_service)

    return alerts_container, scores_container, load_alerts_fn


async def _build_quality_score_cards(
    user: dict[str, Any],
    quality_service: DataQualityService,
) -> None:
    """Build quality score cards per dataset using compute_quality_scores()."""
    try:
        validations = await quality_service.get_validation_results(
            user, dataset=None
        )
        alerts = await quality_service.get_anomaly_alerts(
            user, severity=None, acknowledged=None
        )
        quarantine = await quality_service.get_quarantine_status(user)
    except PermissionError as exc:
        ui.notify(str(exc), type="negative")
        return
    except Exception:
        logger.exception(
            "quality_score_load_failed",
            extra={
                "service": "DataQualityService",
                "user_id": _get_user_id_safe(user),
            },
        )
        ui.notify("Could not load quality scores", type="warning")
        return

    scores = compute_quality_scores(validations, alerts, quarantine)

    if not scores:
        ui.label("No quality data available").classes("text-gray-500")
        return

    ui.label("Quality Scores").classes("font-bold mb-2")
    with ui.row().classes("gap-4 flex-wrap"):
        for score in scores:
            # Color based on overall score
            if score.overall_score is None:
                color_cls = "text-gray-500"
                score_text = "N/A"
            elif score.overall_score >= 90.0:
                color_cls = "text-green-600"
                score_text = f"{score.overall_score:.1f}%"
            elif score.overall_score >= 70.0:
                color_cls = "text-amber-600"
                score_text = f"{score.overall_score:.1f}%"
            else:
                color_cls = "text-red-600"
                score_text = f"{score.overall_score:.1f}%"

            with ui.card().classes("p-4 min-w-[200px]"):
                ui.label(score.dataset).classes("font-bold text-lg")
                ui.label(score_text).classes(f"text-3xl font-bold {color_cls}")
                with ui.row().classes("gap-4 mt-2"):
                    rate_text = (
                        f"{score.validation_pass_rate:.0f}%"
                        if score.validation_pass_rate is not None
                        else "N/A"
                    )
                    ui.label(f"Pass Rate: {rate_text}").classes(
                        "text-sm text-gray-600"
                    )
                    ui.label(f"Anomalies: {score.anomaly_count}").classes(
                        "text-sm text-gray-600"
                    )
                    ui.label(f"Quarantine: {score.quarantine_count}").classes(
                        "text-sm text-gray-600"
                    )


async def _render_validation_results(
    user: dict[str, Any],
    quality_service: DataQualityService,
) -> None:
    """Render validation results table with dataset filter."""
    ui.label("Recent Validation Results").classes("font-bold mb-2")

    dataset_filter = ui.select(
        label="Dataset Filter",
        options=["all"],
        value="all",
    ).classes("w-40 mb-4")

    results_container = ui.column().classes("w-full")

    async def load_results() -> None:
        ds = None if dataset_filter.value == "all" else str(dataset_filter.value)
        try:
            results = await quality_service.get_validation_results(user, dataset=ds)
            results_container.clear()
            with results_container:
                _build_validation_table(results)
        except PermissionError as e:
            ui.notify(str(e), type="negative")
        except Exception:
            logger.exception(
                "service_call_failed",
                extra={
                    "method": "get_validation_results",
                    "service": "DataQualityService",
                    "user_id": _get_user_id_safe(user),
                },
            )
            ui.notify("Service temporarily unavailable", type="warning")

    dataset_filter.on_value_change(lambda _: load_results())
    await load_results()


def _build_validation_table(results: list[Any]) -> None:
    """Build validation results table from ValidationResultDTO list."""
    _status_colors: dict[str, str] = {
        "passed": "text-green-600",
        "failed": "text-red-600",
        "warning": "text-amber-600",
    }

    columns: list[dict[str, Any]] = [
        {"name": "dataset", "label": "Dataset", "field": "dataset"},
        {"name": "validation_type", "label": "Check", "field": "validation_type"},
        {"name": "status", "label": "Status", "field": "status"},
        {"name": "expected", "label": "Expected", "field": "expected"},
        {"name": "actual", "label": "Actual", "field": "actual"},
        {
            "name": "created_at",
            "label": "Timestamp",
            "field": "created_at",
            "sortable": True,
        },
    ]
    rows: list[dict[str, Any]] = []
    for r in results:
        normalized = normalize_validation_status(r.status)
        rows.append(
            {
                "dataset": r.dataset,
                "validation_type": r.validation_type,
                "status": normalized,
                "expected": str(r.expected_value) if r.expected_value is not None else "-",
                "actual": str(r.actual_value) if r.actual_value is not None else "-",
                "created_at": _format_datetime(r.created_at),
            }
        )
    ui.table(columns=columns, rows=rows).classes("w-full")


async def _render_anomaly_alerts(
    user: dict[str, Any],
    quality_service: DataQualityService,
) -> tuple[ui.column, Callable[[], Any]]:
    """Render anomaly alerts with filters.

    Returns:
        Tuple of (alerts_container, load_alerts_callable) so that the timer
        refresh can reuse the same filter-aware path.
    """
    ui.label("Anomaly Alerts").classes("font-bold mb-2")

    with ui.row().classes("gap-4 mb-4"):
        severity_filter = ui.select(
            label="Severity",
            options=["all", "critical", "high", "medium", "low"],
            value="all",
        ).classes("w-32")
        ack_filter = ui.select(
            label="Status",
            options=["all", "unacked", "acked"],
            value="unacked",
        ).classes("w-32")

    alerts_container = ui.column().classes("w-full")

    async def load_alerts() -> None:
        ack_mapped = _ACK_MAP[str(ack_filter.value)]
        try:
            raw_alerts = await quality_service.get_anomaly_alerts(
                user, severity=None, acknowledged=ack_mapped
            )
            # Client-side severity normalization and filtering
            sev_value = str(severity_filter.value)
            filtered = _normalize_and_filter_alerts(raw_alerts, sev_value)

            alerts_container.clear()
            with alerts_container:
                _build_anomaly_alert_cards(filtered, user, quality_service)
        except PermissionError as e:
            ui.notify(str(e), type="negative")
        except Exception:
            logger.exception(
                "service_call_failed",
                extra={
                    "method": "get_anomaly_alerts",
                    "service": "DataQualityService",
                    "user_id": _get_user_id_safe(user),
                },
            )
            ui.notify("Service temporarily unavailable", type="warning")

    severity_filter.on_value_change(lambda _: load_alerts())
    ack_filter.on_value_change(lambda _: load_alerts())
    await load_alerts()

    return alerts_container, load_alerts


def _normalize_and_filter_alerts(
    raw_alerts: list[Any], severity_filter: str
) -> list[Any]:
    """Normalize severity values and apply client-side filter.

    Uses a local lookup to avoid mutating incoming DTO objects. The normalized
    severity is stored as ``_normalized_severity`` on each alert for filtering.
    """
    result: list[Any] = []
    for alert in raw_alerts:
        normalized = _SEVERITY_MAP.get(
            alert.severity.lower(), alert.severity.lower()
        )
        # Attach normalized value without mutating the original .severity field
        alert._normalized_severity = normalized  # noqa: SLF001
        if severity_filter == "all" or normalized == severity_filter:
            result.append(alert)
    return result


def _build_anomaly_alert_cards(
    alerts: list[Any],
    user: dict[str, Any],
    quality_service: DataQualityService,
) -> None:
    """Build alert cards from normalized AnomalyAlertDTO list."""
    can_ack = has_permission(user, Permission.ACKNOWLEDGE_ALERTS)

    if not alerts:
        ui.label("No alerts matching filters").classes("text-gray-500")
        return

    for alert in alerts:
        sev = getattr(alert, "_normalized_severity", alert.severity)
        color_class = _SEVERITY_COLORS.get(
            sev, "bg-gray-100 border-gray-300 text-gray-700"
        )
        with ui.card().classes(f"w-full p-4 mb-2 border-l-4 {color_class}"):
            with ui.row().classes("items-center gap-2"):
                ui.label(sev.upper()).classes("font-bold")
                ui.label(alert.metric).classes("text-sm")
                ui.label(_format_datetime(alert.created_at)).classes(
                    "text-sm text-gray-500"
                )
                if alert.acknowledged:
                    ui.label("ACK").classes(
                        "text-xs bg-green-200 px-2 py-0.5 rounded"
                    )
            ui.label(alert.message).classes("mt-1")

            if alert.deviation_pct is not None:
                ui.label(
                    f"Deviation: {alert.deviation_pct:.1f}% "
                    f"(current: {alert.current_value}, expected: {alert.expected_value})"
                ).classes("text-sm text-gray-600 mt-1")

            if can_ack and not alert.acknowledged:
                _alert_id = alert.id

                async def ack_alert(aid: str = _alert_id) -> None:
                    try:
                        ack = await quality_service.acknowledge_alert(
                            user, aid, "Acknowledged via dashboard"
                        )
                        ui.notify(
                            f"Alert acknowledged by {ack.acknowledged_by}",
                            type="positive",
                        )
                    except PermissionError as e:
                        ui.notify(str(e), type="negative")
                    except Exception:
                        logger.exception(
                            "service_call_failed",
                            extra={
                                "method": "acknowledge_alert",
                                "service": "DataQualityService",
                                "alert_id": aid,
                                "user_id": _get_user_id_safe(user),
                            },
                        )
                        ui.notify(
                            "Service temporarily unavailable", type="warning"
                        )

                ui.button(
                    "Acknowledge", on_click=ack_alert
                ).props("flat dense").classes("mt-1")


async def _render_quality_trends(
    user: dict[str, Any],
    quality_service: DataQualityService,
) -> None:
    """Render quality trend charts with dataset selector."""
    ui.label("Quality Trends").classes("font-bold mb-2")

    dataset_select = ui.select(
        label="Dataset",
        options=["crsp", "compustat", "taq", "fama_french"],
        value="crsp",
    ).classes("w-40 mb-4")

    trend_container = ui.column().classes("w-full")

    async def load_trends() -> None:
        ds = str(dataset_select.value)
        try:
            trend = await quality_service.get_quality_trends(user, dataset=ds, days=30)
            trend_container.clear()
            with trend_container:
                _build_quality_trend_chart(trend)
        except PermissionError as e:
            ui.notify(str(e), type="negative")
        except Exception:
            logger.exception(
                "service_call_failed",
                extra={
                    "method": "get_quality_trends",
                    "service": "DataQualityService",
                    "dataset": ds,
                    "user_id": _get_user_id_safe(user),
                },
            )
            ui.notify("Service temporarily unavailable", type="warning")

    dataset_select.on_value_change(lambda _: load_trends())
    await load_trends()


def _build_quality_trend_chart(trend: Any) -> None:
    """Build Plotly trend chart with threshold lines and trend summary cards."""
    if not trend.data_points:
        with ui.card().classes("w-full p-4"):
            ui.label(
                f"Quality Trends - {trend.dataset} ({trend.period_days}d)"
            ).classes("text-lg mb-4")
            ui.label("No trend data available yet").classes("text-gray-500")
        return

    # Build Plotly chart with threshold lines
    fig = go.Figure()
    unique_metrics = list({p.metric for p in trend.data_points})
    for metric_name in sorted(unique_metrics):
        metric_points = [p for p in trend.data_points if p.metric == metric_name]
        metric_points.sort(key=lambda p: p.date)
        fig.add_trace(
            go.Scatter(
                x=[p.date for p in metric_points],
                y=[p.value for p in metric_points],
                mode="lines+markers",
                name=metric_name,
            )
        )

    # Threshold lines at 90 (good) and 70 (critical)
    fig.add_hline(
        y=90, line_dash="dash", line_color="green",
        annotation_text="Good (90)", annotation_position="top right",
    )
    fig.add_hline(
        y=70, line_dash="dash", line_color="red",
        annotation_text="Critical (70)", annotation_position="bottom right",
    )
    fig.update_layout(
        title=f"Quality Trends - {trend.dataset}",
        xaxis_title="Date",
        yaxis_title="Score",
    )
    ui.plotly(fig).classes("w-full")

    # Compute trend summary per metric using quality_scorer
    for metric_name in sorted(unique_metrics):
        summary = compute_trend_summary(trend, metric_name)

        # Trend arrow and color
        _direction_display: dict[str, tuple[str, str]] = {
            "improving": ("\u2191", "text-green-600"),
            "stable": ("\u2192", "text-gray-600"),
            "degrading": ("\u2193", "text-red-600"),
            "insufficient_data": ("\u2014", "text-gray-400"),
        }
        arrow, direction_color = _direction_display.get(
            summary.trend_direction, ("\u2014", "text-gray-400")
        )

        if len(unique_metrics) > 1:
            ui.label(f"Metric: {metric_name}").classes("font-bold mt-4 mb-2")

        with ui.row().classes("gap-4 mt-2"):
            with ui.card().classes("flex-1 p-4 text-center"):
                ui.label("Current Score").classes("text-sm text-gray-500")
                current_text = (
                    f"{summary.current_score:.1f}%"
                    if summary.current_score is not None
                    else "N/A"
                )
                ui.label(current_text).classes("text-3xl font-bold text-green-600")

            with ui.card().classes("flex-1 p-4 text-center"):
                ui.label("7-Day Average").classes("text-sm text-gray-500")
                avg7_text = (
                    f"{summary.avg_7d:.1f}%"
                    if summary.avg_7d is not None
                    else "N/A"
                )
                ui.label(avg7_text).classes("text-3xl font-bold text-green-600")

            with ui.card().classes("flex-1 p-4 text-center"):
                ui.label("30-Day Average").classes("text-sm text-gray-500")
                avg30_text = (
                    f"{summary.avg_30d:.1f}%"
                    if summary.avg_30d is not None
                    else "N/A"
                )
                ui.label(avg30_text).classes("text-3xl font-bold text-green-600")

            with ui.card().classes("flex-1 p-4 text-center"):
                ui.label("Trend").classes("text-sm text-gray-500")
                ui.label(f"{arrow} {summary.trend_direction}").classes(
                    f"text-xl font-bold {direction_color}"
                )

        # Degradation alert
        if summary.degradation_alert:
            with ui.card().classes(
                "w-full p-4 mt-2 bg-amber-100 border-l-4 border-amber-500"
            ):
                ui.label(
                    f"Quality degradation detected: 7-day average is significantly "
                    f"below 30-day average for {trend.dataset}"
                ).classes("text-amber-800 font-bold")


async def _render_quarantine_inspector(
    user: dict[str, Any],
    quality_service: DataQualityService,
) -> None:
    """Render quarantine inspector with drill-down preview via DuckDB."""
    ui.label("Quarantine Inspector").classes("font-bold mb-2")

    try:
        entries = await quality_service.get_quarantine_status(user)
    except PermissionError as exc:
        ui.notify(str(exc), type="negative")
        return
    except Exception:
        logger.exception(
            "service_call_failed",
            extra={
                "method": "get_quarantine_status",
                "service": "DataQualityService",
                "user_id": _get_user_id_safe(user),
            },
        )
        ui.notify("Service temporarily unavailable", type="warning")
        return

    if not entries:
        ui.label("No quarantine entries").classes("text-gray-500")
        return

    # Group entries by dataset
    by_dataset: dict[str, list[Any]] = {}
    for entry in entries:
        by_dataset.setdefault(entry.dataset, []).append(entry)

    preview_container = ui.column().classes("w-full mt-4")

    for ds_name in sorted(by_dataset):
        ds_entries = by_dataset[ds_name]
        with ui.expansion(f"{ds_name} ({len(ds_entries)} entries)").classes(
            "w-full mb-2"
        ):
            for entry in ds_entries:
                with ui.card().classes("w-full p-3 mb-2 border-l-4 border-amber-400"):
                    with ui.row().classes("items-center gap-4"):
                        ui.label(entry.reason).classes("font-bold")
                        ui.label(entry.quarantine_path).classes(
                            "text-sm text-gray-500 font-mono"
                        )
                        ui.label(_format_datetime(entry.created_at)).classes(
                            "text-sm text-gray-400"
                        )

                    # Drill-down button
                    _entry = entry

                    async def inspect_entry(
                        qe: Any = _entry,
                    ) -> None:
                        preview_container.clear()
                        with preview_container:
                            await _load_quarantine_preview(qe)

                    ui.button(
                        "Inspect", on_click=inspect_entry
                    ).props("flat dense").classes("mt-1")


async def _load_quarantine_preview(entry: Any) -> None:
    """Load quarantine data preview via DuckDB with path validation."""
    # Step 1: Validate path (CPU-only, no filesystem I/O)
    try:
        safe_path = validate_quarantine_path(entry.quarantine_path, _DATA_DIR)
    except ValueError as exc:
        ui.label(f"Path validation failed: {exc}").classes("text-red-600")
        return

    # Step 2: Sanitize dataset name (CPU-only regex check)
    if not _DATASET_PATTERN.match(entry.dataset):
        ui.label(f"Invalid dataset name: {entry.dataset!r}").classes("text-red-600")
        return

    # Steps 3-4: Filesystem checks + DuckDB query in worker thread
    def _validate_and_query() -> tuple[str, Any]:
        """Run filesystem validation and DuckDB query (sync, worker thread).

        Returns (status, result) where status is "ok", "path_escape",
        "no_dir", "no_file", or "error".
        """
        # TOCTOU re-validation at point of use
        quarantine_root = (_DATA_DIR / "quarantine").resolve()
        if not safe_path.resolve().is_relative_to(quarantine_root):
            return ("path_escape", None)

        if not safe_path.exists():
            return ("no_dir", None)

        entry_file = safe_path / f"{entry.dataset}.parquet"
        if not entry_file.exists():
            return ("no_file", None)

        import duckdb

        conn = duckdb.connect()
        try:
            conn.execute(
                "CREATE TABLE quarantine AS SELECT * FROM read_parquet(?)",
                [str(entry_file)],
            )
            return ("ok", conn.execute(
                "SELECT * FROM quarantine LIMIT 100"
            ).fetchdf())
        finally:
            conn.close()

    try:
        status, result = await asyncio.to_thread(_validate_and_query)

        if status == "path_escape":
            ui.label("Path validation failed at access time").classes(
                "text-red-600"
            )
            return
        if status == "no_dir":
            ui.label(
                "Preview unavailable — quarantine directory does not exist "
                "yet. Full drill-down available when the quality service is "
                "DB-backed."
            ).classes("text-gray-500 italic")
            return
        if status == "no_file":
            ui.label(
                f"Preview unavailable — {entry.dataset}.parquet not found "
                f"in quarantine directory. Full drill-down available when "
                f"the quality service is DB-backed."
            ).classes("text-gray-500 italic")
            return

        # Display preview table
        if result.empty:
            ui.label("No matching data for this entry").classes("text-gray-500")
            return

        ui.label(
            f"Quarantine Preview: {entry.dataset} — {len(result)} rows"
        ).classes("font-bold mb-2")
        columns: list[dict[str, Any]] = [
            {"name": col, "label": col, "field": col, "sortable": True}
            for col in result.columns
        ]
        rows = result.to_dict(orient="records")
        ui.table(columns=columns, rows=rows).classes("w-full")

    except Exception:
        logger.exception(
            "quarantine_preview_failed",
            extra={
                "dataset": entry.dataset,
                "quarantine_path": entry.quarantine_path,
            },
        )
        ui.label(
            "Preview unavailable — error loading quarantine data."
        ).classes("text-red-600")


__all__ = ["data_management_page"]
