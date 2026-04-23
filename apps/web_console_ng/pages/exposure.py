"""Strategy Exposure Dashboard page for NiceGUI web console (P6T15/T15.3)."""

from __future__ import annotations

import asyncio
import logging

from nicegui import ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.components.strategy_exposure import (
    build_exposure_chart_figure,
    build_exposure_rows,
    render_bias_warning,
    render_data_quality_warning,
    render_exposure_grid,
    render_exposure_summary_cards,
    render_exposure_unavailable,
)
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from apps.web_console_ng.core.database import get_db_pool
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console_ng.ui.root_path import resolve_rooted_path_from_ui
from apps.web_console_ng.utils.session import get_or_create_client_id
from libs.platform.web_console_auth.permissions import Permission, has_permission
from libs.web_console_services.exposure_service import ExposureService
from libs.web_console_services.schemas.exposure import TotalExposureDTO

logger = logging.getLogger(__name__)

_CLEANUP_OWNER_KEY = "exposure_timers"


@ui.page("/risk/exposure")
@requires_auth
@main_layout
async def exposure_page() -> None:
    """Render strategy exposure dashboard with auto-refresh."""
    user = get_current_user()

    if not has_permission(user, Permission.VIEW_STRATEGY_EXPOSURE):
        ui.notify("Permission denied: VIEW_STRATEGY_EXPOSURE required", type="negative")
        with ui.card().classes("w-full p-6"):
            ui.label("Permission denied: VIEW_STRATEGY_EXPOSURE required.").classes(
                "text-red-500 text-center"
            )
        return

    ui.label("Strategy Exposure Dashboard").classes("text-2xl font-bold mb-2")

    service = ExposureService()
    db_pool = get_db_pool()

    summary_container = ui.column().classes("w-full")
    warning_container = ui.column().classes("w-full")
    grid_container = ui.column().classes("w-full")
    chart_container = ui.column().classes("w-full")
    badge_container = ui.column().classes("w-full")
    status_container = ui.column().classes("w-full")

    empty_total = TotalExposureDTO(
        long_total=0.0,
        short_total=0.0,
        gross_total=0.0,
        net_total=0.0,
        net_pct=0.0,
        strategy_count=0,
    )
    with grid_container:
        exposure_grid = render_exposure_grid([], empty_total, include_total=False)
    with chart_container:
        exposure_chart = ui.plotly(build_exposure_chart_figure([])).classes("w-full")

    _refreshing = False
    _error_count = 0
    _had_success = False
    _timer_ref: list[ui.timer | None] = [None]

    async def refresh_exposure() -> None:
        nonlocal _refreshing, _error_count, _had_success, exposure_grid, exposure_chart
        if _refreshing:
            return
        _refreshing = True
        # Initialise before try so exception handlers never hit UnboundLocalError.
        current_user = user
        try:
            # Rehydrate user context each poll cycle so mid-session
            # permission revocations are detected within one cycle.
            current_user = get_current_user()
            if not has_permission(current_user, Permission.VIEW_STRATEGY_EXPOSURE):
                raise PermissionError("Permission 'view_strategy_exposure' revoked")

            exposures, total = await asyncio.wait_for(
                service.get_strategy_exposure(current_user, db_pool),
                timeout=25.0,  # Must complete before next 30s poll
            )
            _error_count = 0  # Reset on success
            _had_success = True

            badge_container.clear()
            status_container.clear()
            if total.is_placeholder:
                with badge_container:
                    if total.strategy_count == 0:
                        badge_text = "No strategies configured — showing example data"
                    else:
                        badge_text = "No live positions — showing example data"
                    ui.label(badge_text).classes(
                        "inline-block px-3 py-1 rounded bg-amber-100 "
                        "text-amber-700 text-xs font-semibold mb-3"
                    )

            # When positions exist but none can be attributed to strategies,
            # show an explicit "unavailable" state instead of misleading $0
            # totals that look like "flat risk" when the reality is "unknown".
            if not exposures and total.is_partial:
                summary_container.clear()
                warning_container.clear()
                with warning_container:
                    render_exposure_unavailable(total)
                exposure_grid.options["rowData"] = []
                exposure_grid.update()
                exposure_chart.figure = build_exposure_chart_figure([])
                exposure_chart.update()
            else:
                summary_container.clear()
                with summary_container:
                    render_exposure_summary_cards(total)

                warning_container.clear()
                with warning_container:
                    render_bias_warning(total)
                    render_data_quality_warning(total)

                exposure_grid.options["rowData"] = build_exposure_rows(exposures, total)
                exposure_grid.update()
                exposure_chart.figure = build_exposure_chart_figure(exposures)
                exposure_chart.update()

        except PermissionError as exc:
            for container in (
                status_container,
                badge_container,
                summary_container,
                warning_container,
                grid_container,
                chart_container,
            ):
                container.clear()
            with grid_container:
                exposure_grid = render_exposure_grid([], empty_total, include_total=False)
            with chart_container:
                exposure_chart = ui.plotly(build_exposure_chart_figure([])).classes("w-full")
            with warning_container:
                ui.label("Access revoked — exposure data cleared.").classes(
                    "text-red-500 text-center"
                )
            ui.notify(str(exc), type="negative")
            # Cancel timer to stop polling after access revocation
            if _timer_ref[0] is not None:
                _timer_ref[0].cancel()
        except Exception:
            _error_count += 1
            # Log first 5 failures, then every 10th to reduce noise
            if _error_count <= 5 or _error_count % 10 == 0:
                logger.exception(
                    "exposure_refresh_failed",
                    extra={
                        "user_id": current_user.get("user_id")
                        if isinstance(current_user, dict)
                        else None,
                        "role": current_user.get("role")
                        if isinstance(current_user, dict)
                        else None,
                        "strategy_count": len(
                            current_user.get("strategies", [])
                        )
                        if isinstance(current_user, dict)
                        else None,
                        "page": "/risk/exposure",
                        "consecutive_failures": _error_count,
                    },
                )
            if _error_count <= 2:
                ui.notify("Exposure data unavailable", type="warning")
            # Show stale data banner after 3+ consecutive failures so users
            # know the displayed data may not reflect current positions.
            if _error_count >= 3:
                badge_container.clear()
                with badge_container:
                    ui.label(
                        f"Data may be stale — {_error_count} consecutive"
                        " refresh failures"
                    ).classes(
                        "inline-block px-3 py-1 rounded bg-red-100 "
                        "text-red-700 text-xs font-semibold mb-3"
                    )
            status_container.clear()
            with status_container:
                with ui.card().classes("w-full p-2 bg-amber-50 border border-amber-300"):
                    ui.label(
                        "Exposure refresh failed; showing last successful data."
                        if _had_success
                        else "Exposure data is currently unavailable."
                    ).classes("text-xs text-amber-700")
        finally:
            _refreshing = False

    await refresh_exposure()

    timer_exposure = ui.timer(30.0, refresh_exposure)
    _timer_ref[0] = timer_exposure

    lifecycle = ClientLifecycleManager.get()
    client_id = get_or_create_client_id()
    if client_id:
        await lifecycle.register_client(client_id)
        await lifecycle.register_cleanup_callback(
            client_id,
            timer_exposure.cancel,
            owner_key=_CLEANUP_OWNER_KEY,
        )


__all__ = ["exposure_page"]


@ui.page("/exposure")
@requires_auth
async def exposure_alias_page() -> None:
    """Legacy alias route; keep deep links working with canonical path."""
    ui.navigate.to(resolve_rooted_path_from_ui("/risk/exposure", ui_module=ui))
