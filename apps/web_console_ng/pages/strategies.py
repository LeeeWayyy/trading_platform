"""Strategy Management page for NiceGUI web console (P6T17.1).

Provides strategy listing with enable/disable toggle. Admin-only toggle
requires confirmation when open positions exist.
"""

from __future__ import annotations

import logging
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

    # Page title
    ui.label("Strategy Management").classes("text-2xl font-bold mb-4")

    @ui.refreshable
    def strategy_list() -> None:
        if not strategies_data:
            ui.label("No strategies found.").classes("text-gray-500")
            return

        with ui.column().classes("w-full gap-3"):
            for strat in strategies_data:
                with ui.card().classes("w-full p-4"):
                    with ui.row().classes("items-center justify-between w-full"):
                        with ui.column().classes("gap-1"):
                            with ui.row().classes("items-center gap-2"):
                                ui.label(strat["name"] or strat["strategy_id"]).classes(
                                    "text-lg font-semibold"
                                )
                                # Active badge
                                if strat["active"]:
                                    ui.badge("Active", color="green").classes("text-xs")
                                else:
                                    ui.badge("Inactive", color="red").classes("text-xs")
                                # Activity status badge
                                activity = strat.get("activity_status", "unknown")
                                if activity == "active":
                                    ui.badge("Recent Activity", color="blue").classes("text-xs")
                                elif activity == "idle":
                                    ui.badge("Idle", color="gray").classes("text-xs")

                            ui.label(strat["strategy_id"]).classes("text-sm text-gray-500")
                            if strat.get("description"):
                                ui.label(strat["description"]).classes("text-sm text-gray-600")

                        # Toggle button (admin-only)
                        if can_toggle:
                            strategy_id = strat["strategy_id"]
                            is_active = strat["active"]
                            name = strat["name"] or strategy_id

                            async def on_toggle(
                                sid: str = strategy_id,
                                currently_active: bool = is_active,
                                sname: str = name,
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
                                ui.button(
                                    "Deactivate",
                                    on_click=on_toggle,
                                    color="red",
                                ).props("outline size=sm")
                            else:
                                ui.button(
                                    "Activate",
                                    on_click=on_toggle,
                                    color="green",
                                ).props("outline size=sm")

                    # Updated info
                    if strat.get("updated_at"):
                        ui.label(
                            f"Last updated: {strat['updated_at']} by {strat.get('updated_by') or 'system'}"
                        ).classes("text-xs text-gray-400 mt-2")

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
