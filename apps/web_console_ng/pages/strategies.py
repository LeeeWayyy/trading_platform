"""Strategy Management page for NiceGUI web console (P6T17.1).

Provides strategy listing with enable/disable toggle. Admin-only toggle
requires confirmation when open positions exist.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

import psycopg
from nicegui import app, ui

from apps.web_console_ng import config
from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.core.database import get_db_pool
from apps.web_console_ng.ui.layout import main_layout
from libs.platform.web_console_auth.permissions import (
    Permission,
    has_permission,
    is_admin,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from libs.web_console_services.strategy_service import StrategyService

logger = logging.getLogger(__name__)


def _get_strategy_service(db_pool: AsyncConnectionPool) -> StrategyService:
    """Get StrategyService with async pool (global cache)."""
    if not hasattr(app.storage, "_strategy_service"):
        from libs.platform.web_console_auth.audit_log import AuditLogger
        from libs.web_console_services.strategy_service import StrategyService

        audit_logger = AuditLogger(db_pool)
        app.storage._strategy_service = StrategyService(db_pool, audit_logger)  # type: ignore[attr-defined]  # noqa: B010

    service: StrategyService = getattr(app.storage, "_strategy_service")  # noqa: B009
    return service


def _format_updated_label(updated_at: Any, updated_by: Any) -> str:
    """Format updated-at/by metadata for compact row rendering."""
    if isinstance(updated_at, datetime):
        timestamp = updated_at.strftime("%Y-%m-%d %H:%M:%S")
    elif updated_at is None:
        timestamp = "—"
    else:
        timestamp = str(updated_at)
    updater = str(updated_by or "system")
    return f"{timestamp} · {updater}"


def _activity_badge(activity: str) -> tuple[str, str]:
    """Return activity label and style class for strategy activity state."""
    normalized = activity.strip().lower()
    if normalized == "active":
        return "ACTIVE", "workspace-v2-pill workspace-v2-pill-positive"
    if normalized == "idle":
        return "IDLE", "workspace-v2-pill workspace-v2-pill-warning"
    return "UNKNOWN", "workspace-v2-pill"


@ui.page("/strategies")
@requires_auth
@main_layout
async def strategies_page() -> None:
    """Strategy Management page."""
    user = get_current_user()

    # Feature flag check
    if not config.FEATURE_STRATEGY_MANAGEMENT:
        ui.label("Strategy Management feature is disabled.").classes("text-lg")
        ui.label("Set FEATURE_STRATEGY_MANAGEMENT=true to enable.").classes("text-gray-500")
        return

    # Permission check
    if not has_permission(user, Permission.MANAGE_STRATEGIES):
        ui.label("Permission denied: MANAGE_STRATEGIES required").classes("text-red-500 text-lg")
        return

    # Get async db pool
    async_pool = get_db_pool()
    if async_pool is None:
        ui.label("Database not configured. Contact administrator.").classes("text-red-500")
        return

    service = _get_strategy_service(async_pool)
    can_toggle = is_admin(user)

    # State
    strategies_data: list[dict[str, Any]] = []

    async def fetch_strategies() -> None:
        nonlocal strategies_data
        try:
            strategies_data = await service.get_strategies(user)
        except psycopg.OperationalError as e:
            logger.warning(
                "strategies_fetch_db_error",
                extra={"error": str(e), "operation": "fetch_strategies"},
            )
            strategies_data = []
        except PermissionError as e:
            logger.warning(
                "strategies_fetch_permission_denied",
                extra={"error": str(e), "operation": "fetch_strategies"},
            )
            strategies_data = []

    await fetch_strategies()

    ui.label("Strategy Management").classes("text-2xl font-bold mb-2")
    ui.label(
        "Dense strategy control surface with explicit status, activity, and guarded actions."
    ).classes("text-xs text-slate-400 mb-3")

    @ui.refreshable
    def strategy_list() -> None:
        if not strategies_data:
            ui.label("No strategies found.").classes("text-gray-500")
            return

        active_count = sum(1 for strat in strategies_data if bool(strat.get("active")))
        idle_count = sum(
            1
            for strat in strategies_data
            if str(strat.get("activity_status", "unknown")).lower() == "idle"
        )
        unknown_count = sum(
            1
            for strat in strategies_data
            if str(strat.get("activity_status", "unknown")).lower() == "unknown"
        )

        with ui.row().classes("w-full gap-2 mb-2"):
            ui.label(f"{len(strategies_data)} strategies").classes(
                "workspace-v2-pill workspace-v2-data-mono"
            )
            ui.label(f"{active_count} active").classes(
                "workspace-v2-pill workspace-v2-pill-positive workspace-v2-data-mono"
            )
            ui.label(f"{idle_count} idle").classes(
                "workspace-v2-pill workspace-v2-pill-warning workspace-v2-data-mono"
            )
            ui.label(f"{unknown_count} unknown").classes("workspace-v2-pill workspace-v2-data-mono")

        with ui.row().classes(
            "w-full items-center gap-3 px-3 py-2 text-[11px] uppercase tracking-wide text-slate-400"
        ):
            ui.label("Status").classes("w-20")
            ui.label("Name").classes("w-44")
            ui.label("Strategy ID").classes("w-40")
            ui.label("Activity").classes("w-28")
            ui.label("Exposure").classes("w-28")
            ui.label("Updated At / By").classes("flex-1")
            ui.label("Actions").classes("w-28 text-right")

        with ui.column().classes("w-full gap-2"):
            for strat in strategies_data:
                strategy_id = str(strat["strategy_id"])
                strategy_name = str(strat["name"] or strategy_id)
                is_active = bool(strat["active"])
                activity_label, activity_style = _activity_badge(
                    str(strat.get("activity_status", "unknown"))
                )
                status_label = "ACTIVE" if is_active else "INACTIVE"
                status_style = (
                    "workspace-v2-pill workspace-v2-pill-positive"
                    if is_active
                    else "workspace-v2-pill workspace-v2-pill-negative"
                )

                with ui.card().classes("w-full p-0 bg-slate-900/35 border border-slate-800"):
                    with ui.row().classes("w-full items-center gap-3 px-3 py-2 text-sm"):
                        ui.label(status_label).classes(f"w-20 {status_style}")
                        ui.label(strategy_name).classes("w-44 text-sm font-semibold text-slate-100")
                        ui.label(strategy_id).classes(
                            "w-40 text-xs text-slate-400 workspace-v2-data-mono"
                        )
                        ui.label(activity_label).classes(f"w-28 {activity_style}")
                        ui.label("Evaluated on toggle").classes(
                            "w-28 text-[11px] text-slate-500 workspace-v2-data-mono"
                        )
                        ui.label(_format_updated_label(strat.get("updated_at"), strat.get("updated_by"))).classes(
                            "flex-1 text-xs text-slate-400 workspace-v2-data-mono"
                        )

                        if can_toggle:
                            async def on_toggle(
                                _event: Any,
                                sid: str = strategy_id,
                                currently_active: bool = is_active,
                                sname: str = strategy_name,
                            ) -> None:
                                await _show_toggle_dialog(
                                    service,
                                    sid,
                                    currently_active,
                                    sname,
                                    user,
                                    fetch_strategies,
                                    strategy_list,
                                )

                            if is_active:
                                ui.button("Deactivate", on_click=on_toggle, color="red").props(
                                    "outline size=sm"
                                ).classes("w-24")
                            else:
                                ui.button("Activate", on_click=on_toggle, color="green").props(
                                    "outline size=sm"
                                ).classes("w-24")
                        else:
                            ui.label("Read only").classes("w-24 text-xs text-slate-500 text-right")

                    with ui.expansion("Details").classes("w-full px-3 pb-3 pt-1"):
                        ui.label(strat.get("description") or "No strategy description.").classes(
                            "text-xs text-slate-300"
                        )
                        ui.label(
                            "Deactivation requires explicit typed confirmation and exposure check."
                        ).classes("text-[11px] text-slate-500 mt-1")

    strategy_list()


async def _show_toggle_dialog(
    service: StrategyService,
    strategy_id: str,
    currently_active: bool,
    name: str,
    user: dict[str, Any],
    fetch_fn: Any,
    refresh_fn: Any,
) -> None:
    """Show confirmation dialog for strategy toggle."""
    action = "DEACTIVATE" if currently_active else "ACTIVATE"
    new_active = not currently_active

    # Check open exposure for deactivation (fail-closed: abort on error)
    exposure_info = ""
    if currently_active:
        try:
            exposure = await service.get_open_exposure(strategy_id, user)
            if exposure["positions_count"] > 0 or exposure["open_orders_count"] > 0:
                exposure_info = (
                    f"\n\nWarning: {exposure['positions_count']} open positions, "
                    f"{exposure['open_orders_count']} open orders"
                )
        except (psycopg.OperationalError, ConnectionError, TimeoutError, OSError) as e:
            logger.warning(
                "exposure_check_failed",
                extra={"strategy_id": strategy_id, "error": str(e)},
            )
            ui.notify(
                "Cannot verify open exposure. Toggle aborted for safety.",
                type="negative",
            )
            return

    with ui.dialog() as confirm_dialog, ui.card().classes("p-6"):
        ui.label("Confirm Action").classes("text-xl font-bold")
        ui.label(
            f"Type {action} to confirm {action.lower()}ing strategy '{name}'" f"{exposure_info}"
        )
        confirm_input = ui.input(label="Confirmation").classes("w-full")

        async def on_confirm() -> None:
            if confirm_input.value != action:
                ui.notify(f"Type {action} to confirm", type="negative")
                return
            try:
                await service.toggle_strategy(
                    strategy_id,
                    active=new_active,
                    user=user,
                )
                ui.notify(
                    f"Strategy '{name}' {action.lower()}d",
                    type="positive",
                )
                confirm_dialog.close()
                await fetch_fn()
                refresh_fn.refresh()
            except PermissionError as e:
                logger.exception(
                    "strategy_toggle_permission_denied",
                    extra={
                        "strategy_id": strategy_id,
                        "error": str(e),
                        "operation": "toggle_strategy",
                    },
                )
                ui.notify(f"Permission denied: {e}", type="negative")
            except ValueError as e:
                ui.notify(str(e), type="negative")
            except psycopg.OperationalError as e:
                logger.exception(
                    "strategy_toggle_db_error",
                    extra={
                        "strategy_id": strategy_id,
                        "error": str(e),
                        "operation": "toggle_strategy",
                    },
                )
                ui.notify("Database error. Please try again.", type="negative")

        with ui.row().classes("gap-2 mt-4"):
            ui.button("Confirm", on_click=on_confirm, color="red")
            ui.button("Cancel", on_click=confirm_dialog.close)

    confirm_dialog.open()
