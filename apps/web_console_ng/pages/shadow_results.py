"""Shadow mode results page for NiceGUI web console (P6T14/T14.4)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import plotly.graph_objects as go
from nicegui import ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console_ng.ui.trading_layout import apply_compact_grid_options
from apps.web_console_ng.utils.session import get_or_create_client_id
from apps.web_console_ng.utils.time import format_relative_time
from libs.platform.web_console_auth.helpers import get_user_id
from libs.platform.web_console_auth.permissions import Permission, has_permission
from libs.web_console_services.schemas.data_management import ShadowResultDTO, ShadowTrendDTO
from libs.web_console_services.shadow_results_service import ShadowResultsService

logger = logging.getLogger(__name__)

_CLEANUP_OWNER_KEY = "shadow_results_timers"


def _pass_rate_class(pass_rate: float) -> str:
    if pass_rate >= 90.0:
        return "text-green-600"
    if pass_rate >= 70.0:
        return "text-amber-600"
    return "text-red-600"


def _render_summary_cards(results: list[ShadowResultDTO], container: ui.column) -> None:
    total = len(results)
    pass_rate = (sum(item.passed for item in results) / total * 100.0) if total else 0.0
    avg_corr = (sum(item.correlation for item in results) / total) if total else None
    avg_div = (sum(item.mean_abs_diff_ratio for item in results) / total) if total else None

    container.clear()
    with container:
        with ui.row().classes("w-full gap-4 mb-3"):
            with ui.card().classes("p-3 min-w-44"):
                ui.label("Total Validations").classes("text-sm text-gray-500")
                ui.label(str(total)).classes("text-2xl font-bold")
            with ui.card().classes("p-3 min-w-44"):
                ui.label("Pass Rate").classes("text-sm text-gray-500")
                ui.label(f"{pass_rate:.2f}%").classes(
                    f"text-2xl font-bold {_pass_rate_class(pass_rate)}"
                )
            with ui.card().classes("p-3 min-w-44"):
                ui.label("Average Correlation").classes("text-sm text-gray-500")
                ui.label("-" if avg_corr is None else f"{avg_corr:.2f}").classes("text-2xl font-bold")
            with ui.card().classes("p-3 min-w-44"):
                ui.label("Average Divergence").classes("text-sm text-gray-500")
                ui.label("-" if avg_div is None else f"{avg_div:.2f}").classes("text-2xl font-bold")


def _plot_trend(trend: ShadowTrendDTO, container: ui.column) -> None:
    container.clear()

    if not trend.data_points:
        with container:
            with ui.card().classes("w-full p-4"):
                ui.label(
                    "No shadow validations recorded. "
                    "Shadow validation runs automatically during model hot-swap "
                    "when SHADOW_VALIDATION_ENABLED=true."
                ).classes("text-gray-500")
        return

    sorted_points = sorted(trend.data_points, key=lambda point: point.date)
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=[point.date for point in sorted_points],
            y=[point.correlation for point in sorted_points],
            mode="lines+markers",
            name="Correlation",
            line={"color": "#2563eb"},
            yaxis="y",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=[point.date for point in sorted_points],
            y=[point.mean_abs_diff_ratio for point in sorted_points],
            mode="lines+markers",
            name="Divergence",
            line={"color": "#f97316"},
            yaxis="y2",
        )
    )
    fig.add_hline(y=0.5, line_dash="dash", line_color="red", annotation_text="Threshold (0.5)")

    dates = [point.date for point in sorted_points]
    for idx in range(1, len(dates)):
        gap_days = (dates[idx] - dates[idx - 1]).days
        if gap_days > 1:
            fig.add_vrect(
                x0=dates[idx - 1],
                x1=dates[idx],
                fillcolor="gray",
                opacity=0.15,
                line_width=0,
                annotation_text="No data" if gap_days > 3 else None,
            )

    max_divergence = max(point.mean_abs_diff_ratio for point in sorted_points)
    fig.update_layout(
        title="Shadow Validation Trends",
        xaxis_title="Date",
        yaxis={"title": "Correlation", "range": [0.0, 1.05]},
        yaxis2={
            "title": "Divergence",
            "overlaying": "y",
            "side": "right",
            "range": [0.0, max(1.05, max_divergence * 1.1)],
        },
        height=360,
        margin={"l": 10, "r": 10, "t": 40, "b": 10},
    )

    with container:
        ui.plotly(fig).classes("w-full")


def _table_rows(results: list[ShadowResultDTO]) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    return [
        {
            "id": result.id,
            "time": format_relative_time(result.validation_time, now=now),
            "model_version": result.model_version,
            "status": "passed" if result.passed else "failed",
            "correlation": result.correlation,
            "divergence": result.mean_abs_diff_ratio,
            "sign_change_rate": result.sign_change_rate,
            "sample_count": result.sample_count,
            "message": result.message,
            "correlation_threshold": result.correlation_threshold,
            "divergence_threshold": result.divergence_threshold,
            "old_range": result.old_range,
            "new_range": result.new_range,
            "strategy": result.strategy,
        }
        for result in results
    ]


def _format_metric(value: Any, precision: int = 4) -> str:
    if isinstance(value, int | float):
        return f"{float(value):.{precision}f}"
    return "-"


@ui.page("/data/shadow")
@requires_auth
@main_layout
async def shadow_results_page() -> None:
    """Render shadow validation trend and recent results."""
    user = get_current_user()

    if not has_permission(user, Permission.VIEW_SHADOW_RESULTS):
        ui.notify("Permission denied: VIEW_SHADOW_RESULTS required", type="negative")
        with ui.card().classes("w-full p-6"):
            ui.label("Permission denied: VIEW_SHADOW_RESULTS required.").classes(
                "text-red-500 text-center"
            )
        return

    service = ShadowResultsService()

    ui.label("Shadow Mode Results").classes("text-2xl font-bold mb-2")
    ui.label("Preview Data").classes(
        "inline-block px-3 py-1 rounded bg-amber-100 text-amber-700 text-xs font-semibold mb-3"
    )

    summary_container = ui.column().classes("w-full")
    trend_container = ui.column().classes("w-full")
    detail_container = ui.column().classes("w-full")

    grid_options = apply_compact_grid_options(
        {
            "columnDefs": [
                {"field": "time", "headerName": "Time", "sortable": True, "minWidth": 110},
                {
                    "field": "model_version",
                    "headerName": "Model Version",
                    "sortable": True,
                    "minWidth": 130,
                },
                {
                    "field": "status",
                    "headerName": "Status",
                    "minWidth": 100,
                    "cellClass": "params => params.value === 'passed' ? 'text-green-600 font-bold' : 'text-red-600 font-bold'",
                },
                {"field": "correlation", "headerName": "Correlation", "minWidth": 120},
                {"field": "divergence", "headerName": "Divergence", "minWidth": 120},
                {
                    "field": "sign_change_rate",
                    "headerName": "Sign Change Rate",
                    "minWidth": 150,
                },
                {"field": "sample_count", "headerName": "Sample Count", "minWidth": 120},
                {"field": "message", "headerName": "Message", "flex": 1, "minWidth": 320},
            ],
            "rowData": [],
            "rowSelection": "single",
            "animateRows": True,
            "domLayout": "normal",
        }
    )
    grid = ui.aggrid(grid_options).classes("w-full ag-theme-alpine-dark")

    _refreshing = False

    async def render_selected_detail() -> None:
        selected = await grid.get_selected_rows()
        detail_container.clear()
        if not selected:
            with detail_container:
                with ui.card().classes("w-full p-4 mt-3"):
                    ui.label("Select a result row to inspect details.").classes("text-gray-500")
            return
        row = selected[0]
        with detail_container:
            with ui.card().classes("w-full p-4 mt-3"):
                ui.label(f"Validation Detail: {row.get('id', '-')}").classes("text-lg font-bold")
                ui.label(f"Strategy: {row.get('strategy', '-')}").classes("text-sm text-gray-400")
                ui.label(f"Reasoning: {row.get('message', '-')}").classes("text-sm text-gray-300")
                ui.separator().classes("my-2")
                with ui.row().classes("w-full gap-4 flex-wrap"):
                    ui.label(
                        "Correlation: "
                        f"{_format_metric(row.get('correlation'))} "
                        f"(threshold {_format_metric(row.get('correlation_threshold'), precision=2)})"
                    ).classes("text-sm")
                    ui.label(
                        "Divergence: "
                        f"{_format_metric(row.get('divergence'))} "
                        f"(threshold {_format_metric(row.get('divergence_threshold'), precision=2)})"
                    ).classes("text-sm")
                    ui.label(f"Sign change rate: {_format_metric(row.get('sign_change_rate'))}").classes(
                        "text-sm"
                    )
                    ui.label(f"Sample count: {row.get('sample_count', '-')}").classes("text-sm")
                    ui.label(
                        "Range comparison: "
                        f"old={_format_metric(row.get('old_range'))}, "
                        f"new={_format_metric(row.get('new_range'))}"
                    ).classes("text-sm")

    async def refresh_shadow_results() -> None:
        nonlocal _refreshing
        if _refreshing:
            return
        _refreshing = True
        try:
            results = await service.get_recent_results(user)
            trend = await service.get_trend(user)

            if not results:
                ui.notify(
                    "No shadow validations recorded. "
                    "Shadow validation runs automatically during model hot-swap when "
                    "SHADOW_VALIDATION_ENABLED=true.",
                    type="info",
                )

            _render_summary_cards(results, summary_container)
            _plot_trend(trend, trend_container)
            grid.options["rowData"] = _table_rows(results)
            grid.update()
            await render_selected_detail()
        except PermissionError as exc:
            ui.notify(str(exc), type="negative")
        except Exception:
            logger.exception(
                "shadow_results_failed",
                extra={
                    "service": "ShadowResultsService",
                    "user_id": get_user_id(user),
                },
            )
            ui.notify("Shadow results unavailable", type="warning")
        finally:
            _refreshing = False

    def _on_row_selected(_event: Any) -> None:
        asyncio.create_task(render_selected_detail())

    grid.on("rowSelected", _on_row_selected)

    await refresh_shadow_results()

    timer_shadow = ui.timer(60.0, refresh_shadow_results)
    lifecycle = ClientLifecycleManager.get()
    client_id = get_or_create_client_id()
    if client_id:
        await lifecycle.register_client(client_id)
        await lifecycle.register_cleanup_callback(
            client_id,
            timer_shadow.cancel,
            owner_key=_CLEANUP_OWNER_KEY,
        )


__all__ = [
    "_CLEANUP_OWNER_KEY",
    "shadow_results_page",
]
