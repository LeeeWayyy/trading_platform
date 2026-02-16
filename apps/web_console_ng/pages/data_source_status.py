"""Data source status page for NiceGUI web console (P6T14/T14.2)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import plotly.graph_objects as go
from nicegui import ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from apps.web_console_ng.core.redis_ha import get_redis_store
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console_ng.utils.session import get_or_create_client_id
from apps.web_console_ng.utils.time import format_relative_time
from libs.platform.web_console_auth.helpers import get_user_id
from libs.platform.web_console_auth.permissions import Permission, has_permission
from libs.web_console_services.data_source_status_service import DataSourceStatusService
from libs.web_console_services.schemas.data_management import DataSourceStatusDTO

logger = logging.getLogger(__name__)

_STATUS_COLORS = {
    "ok": "text-green-600",
    "stale": "text-amber-600",
    "error": "text-red-600",
    "unknown": "text-gray-400",
}

_CLEANUP_OWNER_KEY = "data_source_status_timers"


def _status_counts(sources: list[DataSourceStatusDTO]) -> dict[str, int]:
    counts = {"ok": 0, "stale": 0, "error": 0, "unknown": 0}
    for source in sources:
        counts[source.status] = counts.get(source.status, 0) + 1
    return counts


def _status_for_row(source: DataSourceStatusDTO, *, now: datetime) -> dict[str, Any]:
    return {
        "name": source.name,
        "display_name": source.display_name,
        "provider_type": source.provider_type,
        "status": source.status,
        "last_update": format_relative_time(source.last_update, now=now),
        "age_seconds": source.age_seconds,
        "age_display": f"{source.age_seconds:.0f}s" if source.age_seconds is not None else "-",
        "row_count": source.row_count,
        "error_rate": (
            f"{source.error_rate_pct:.2f}%" if source.error_rate_pct is not None else "-"
        ),
        "error_rate_pct": source.error_rate_pct,
        "production_ready": "check" if source.is_production_ready else "-",
        "datasets": ", ".join(source.tables),
        "error_message": source.error_message or "",
    }


def _render_summary_cards(sources: list[DataSourceStatusDTO], container: ui.column) -> None:
    container.clear()
    counts = _status_counts(sources)
    with container:
        with ui.row().classes("w-full gap-4 mb-3"):
            with ui.card().classes("p-3 min-w-40"):
                ui.label("Total Sources").classes("text-sm text-gray-500")
                ui.label(str(len(sources))).classes("text-2xl font-bold")
            with ui.card().classes("p-3 min-w-40"):
                ui.label("Healthy").classes("text-sm text-gray-500")
                ui.label(str(counts.get("ok", 0))).classes("text-2xl font-bold text-green-600")
            with ui.card().classes("p-3 min-w-40"):
                ui.label("Stale").classes("text-sm text-gray-500")
                ui.label(str(counts.get("stale", 0))).classes("text-2xl font-bold text-amber-600")
            with ui.card().classes("p-3 min-w-40"):
                ui.label("Errored").classes("text-sm text-gray-500")
                ui.label(str(counts.get("error", 0))).classes("text-2xl font-bold text-red-600")


def _render_status_plot(sources: list[DataSourceStatusDTO], container: ui.column) -> None:
    container.clear()
    counts = _status_counts(sources)
    fig = go.Figure(
        data=[
            go.Pie(
                labels=["ok", "stale", "error", "unknown"],
                values=[
                    counts.get("ok", 0),
                    counts.get("stale", 0),
                    counts.get("error", 0),
                    counts.get("unknown", 0),
                ],
                marker={
                    "colors": ["#16a34a", "#d97706", "#dc2626", "#9ca3af"],
                },
                hole=0.4,
                sort=False,
            )
        ]
    )
    fig.update_layout(height=260, margin={"l": 0, "r": 0, "t": 10, "b": 10}, showlegend=True)
    with container:
        ui.plotly(fig).classes("w-full")


@ui.page("/data/sources")
@requires_auth
@main_layout
async def data_source_status_page() -> None:
    """Render data source health/status dashboard."""
    user = get_current_user()

    if not has_permission(user, Permission.VIEW_DATA_SYNC):
        ui.label("Permission denied: VIEW_DATA_SYNC required").classes("text-gray-500")
        return

    source_service = DataSourceStatusService(
        redis_client_factory=lambda: get_redis_store().get_master()
    )
    can_refresh = has_permission(user, Permission.TRIGGER_DATA_SYNC)

    ui.label("Data Source Status").classes("text-2xl font-bold mb-2")
    ui.label("Preview Data").classes(
        "inline-block px-3 py-1 rounded bg-amber-100 text-amber-700 text-xs font-semibold mb-3"
    )

    summary_container = ui.column().classes("w-full")
    plot_container = ui.column().classes("w-full")

    grid_options = {
        "columnDefs": [
            {
                "field": "display_name",
                "headerName": "Source Name",
                "sortable": True,
                "pinned": "left",
                "minWidth": 180,
            },
            {
                "field": "provider_type",
                "headerName": "Provider",
                "filter": True,
                "minWidth": 120,
            },
            {
                "field": "status",
                "headerName": "Status",
                "cellClass": "params => params.value === 'ok' ? 'text-green-600 font-bold' : params.value === 'stale' ? 'text-amber-600 font-bold' : params.value === 'error' ? 'text-red-600 font-bold' : 'text-gray-400 font-bold'",
                "minWidth": 110,
            },
            {"field": "last_update", "headerName": "Last Update", "minWidth": 120},
            {
                "field": "age_display",
                "headerName": "Age",
                "minWidth": 95,
                "cellClass": "params => params.data && params.data.status === 'stale' ? 'text-amber-600' : params.data && params.data.status === 'error' ? 'text-red-600' : ''",
            },
            {
                "field": "row_count",
                "headerName": "Row Count",
                "minWidth": 130,
                "valueFormatter": "params => params.value != null ? Number(params.value).toLocaleString() : '-'",
            },
            {"field": "error_rate", "headerName": "Error Rate", "minWidth": 110},
            {
                "field": "production_ready",
                "headerName": "Production Ready",
                "minWidth": 150,
            },
            {"field": "datasets", "headerName": "Datasets", "flex": 1, "minWidth": 220},
        ],
        "rowData": [],
        "rowSelection": "single",
        "animateRows": True,
        "domLayout": "normal",
    }
    grid = ui.aggrid(grid_options).classes("w-full ag-theme-alpine-dark")

    _refreshing = False

    async def load_sources() -> list[DataSourceStatusDTO] | None:
        try:
            return await source_service.get_all_sources(user)
        except PermissionError as exc:
            ui.notify(str(exc), type="negative")
            return None
        except Exception:
            logger.exception(
                "data_source_status_failed",
                extra={
                    "service": "DataSourceStatusService",
                    "user_id": get_user_id(user),
                },
            )
            ui.notify("Data source status unavailable", type="warning")
            return None

    async def refresh_sources() -> None:
        nonlocal _refreshing
        if _refreshing:
            return
        _refreshing = True
        try:
            sources = await load_sources()
            if sources is None:
                return
            now = datetime.now(UTC)
            rows = [_status_for_row(source, now=now) for source in sources]
            _render_summary_cards(sources, summary_container)
            _render_status_plot(sources, plot_container)
            grid.options["rowData"] = rows
            grid.update()
        except Exception:
            logger.exception("refresh_sources_failed")
        finally:
            _refreshing = False

    async def refresh_selected_source() -> None:
        if not can_refresh:
            ui.notify("Permission trigger_data_sync required", type="negative")
            return
        selected_rows = await grid.get_selected_rows()
        if not selected_rows:
            ui.notify("Select a source to refresh", type="warning")
            return
        source_name = str(selected_rows[0].get("name", "")).strip().lower()
        if not source_name:
            ui.notify("Invalid source selection", type="warning")
            return
        try:
            await source_service.refresh_source(user, source_name)
            await refresh_sources()
            ui.notify(f"Refresh requested for {source_name}", type="positive")
        except PermissionError as exc:
            ui.notify(str(exc), type="negative")
        except TimeoutError:
            ui.notify("Refresh timed out", type="warning")
        except Exception:
            logger.exception(
                "data_source_refresh_failed",
                extra={"source_name": source_name, "user_id": get_user_id(user)},
            )
            ui.notify("Source refresh unavailable", type="warning")

    if can_refresh:
        with ui.row().classes("mt-3 mb-3"):
            ui.button("Refresh Selected Source", on_click=refresh_selected_source, color="primary")

    await refresh_sources()

    timer_sources = ui.timer(30.0, refresh_sources)

    lifecycle = ClientLifecycleManager.get()
    client_id = get_or_create_client_id()
    if client_id:
        await lifecycle.register_client(client_id)
        await lifecycle.register_cleanup_callback(
            client_id,
            timer_sources.cancel,
            owner_key=_CLEANUP_OWNER_KEY,
        )


__all__ = [
    "data_source_status_page",
    "_CLEANUP_OWNER_KEY",
    "_STATUS_COLORS",
]
