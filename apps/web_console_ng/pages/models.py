"""Model Registry Browser page for NiceGUI web console (P6T17.2).

Provides model listing per strategy with activate/deactivate controls.
Reads from the Postgres ``model_registry`` table (active/inactive/testing/failed
statuses), NOT the file-based registry.
"""

from __future__ import annotations

import logging
import os
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

    from libs.web_console_services.model_registry_browser_service import (
        ModelRegistryBrowserService,
    )

logger = logging.getLogger(__name__)


def _get_model_registry_service(
    db_pool: AsyncConnectionPool,
) -> ModelRegistryBrowserService:
    """Get ModelRegistryBrowserService with async pool (global cache)."""
    if not hasattr(app.storage, "_model_registry_browser_service"):
        from libs.platform.web_console_auth.audit_log import AuditLogger
        from libs.web_console_services.model_registry_browser_service import (
            ModelRegistryBrowserService,
        )

        audit_logger = AuditLogger(db_pool)
        model_registry_url = config.SERVICE_URLS.get("model_registry")
        validate_token = os.getenv("MODEL_REGISTRY_ADMIN_TOKEN", "")
        app.storage._model_registry_browser_service = ModelRegistryBrowserService(  # type: ignore[attr-defined]  # noqa: B010
            db_pool,
            audit_logger,
            model_registry_url=model_registry_url,
            validate_token=validate_token or None,
        )

    service: ModelRegistryBrowserService = app.storage._model_registry_browser_service  # type: ignore[attr-defined]  # noqa: B009
    return service


@ui.page("/models")
@requires_auth
@main_layout
async def models_page() -> None:
    """Model Registry Browser page."""
    user = get_current_user()

    # Feature flag check
    if not config.FEATURE_MODEL_REGISTRY:
        ui.label("Model Registry Browser feature is disabled.").classes("text-lg")
        ui.label("Set FEATURE_MODEL_REGISTRY=true to enable.").classes("text-gray-500")
        return

    # Permission check
    if not has_permission(user, Permission.VIEW_MODELS):
        ui.label("Permission denied: VIEW_MODELS required").classes("text-red-500 text-lg")
        return

    # Get async db pool
    async_pool = get_db_pool()
    if async_pool is None:
        ui.label("Database not configured. Contact administrator.").classes("text-red-500")
        return

    service = _get_model_registry_service(async_pool)
    can_manage = is_admin(user)

    # Page title
    ui.label("Model Registry Browser").classes("text-2xl font-bold mb-4")

    # Fetch strategies with models
    try:
        strategy_list = await service.list_strategies_with_models(user)
    except psycopg.OperationalError as e:
        logger.warning(
            "model_registry_fetch_error",
            extra={"error": str(e), "operation": "list_strategies"},
        )
        ui.label("Database error loading model registry.").classes("text-red-500")
        return
    except PermissionError as e:
        ui.label(f"Permission denied: {e}").classes("text-red-500 text-lg")
        return

    if not strategy_list:
        ui.label("No models found in registry.").classes("text-gray-500")
        return

    # Tabs for each strategy
    with ui.tabs().classes("w-full") as tabs:
        strategy_tabs = {}
        for s in strategy_list:
            strategy_tabs[s["strategy_name"]] = ui.tab(s["strategy_name"])

    with ui.tab_panels(tabs).classes("w-full"):
        for s in strategy_list:
            with ui.tab_panel(strategy_tabs[s["strategy_name"]]):
                await _render_strategy_models(service, s["strategy_name"], user, can_manage)


async def _render_strategy_models(
    service: ModelRegistryBrowserService,
    strategy_name: str,
    user: dict[str, Any],
    can_manage: bool,
) -> None:
    """Render model list for a single strategy."""
    try:
        models = await service.get_models_for_strategy(strategy_name, user)
    except PermissionError as e:
        ui.label(f"Access denied: {e}").classes("text-red-500")
        return
    except psycopg.OperationalError as e:
        logger.warning(
            "model_fetch_error",
            extra={"strategy_name": strategy_name, "error": str(e)},
        )
        ui.label("Error loading models.").classes("text-red-500")
        return

    if not models:
        ui.label(f"No models for strategy '{strategy_name}'.").classes("text-gray-500")
        return

    for model in models:
        with ui.card().classes("w-full p-4 mb-3"):
            with ui.row().classes("items-center justify-between w-full"):
                with ui.column().classes("gap-1"):
                    with ui.row().classes("items-center gap-2"):
                        version_str = model["version"]
                        version_label = (
                            version_str if version_str.startswith("v") else f"v{version_str}"
                        )
                        ui.label(version_label).classes("text-lg font-semibold")
                        status = model["status"]
                        status_colors = {
                            "active": "green",
                            "inactive": "gray",
                            "testing": "orange",
                            "failed": "red",
                        }
                        ui.badge(
                            status.title(),
                            color=status_colors.get(status, "gray"),
                        ).classes("text-xs")

                    ui.label(f"Path: {model['model_path']}").classes("text-sm text-gray-500")
                    if model.get("created_by"):
                        ui.label(f"Created by: {model['created_by']}").classes(
                            "text-xs text-gray-400"
                        )

                # Admin actions
                if can_manage:
                    version = model["version"]

                    if status in ("inactive", "testing"):

                        async def on_activate(
                            v: str = version,
                            sn: str = strategy_name,
                        ) -> None:
                            await _show_model_action_dialog(service, sn, v, "ACTIVATE", user)

                        ui.button("Activate", on_click=on_activate, color="green").props(
                            "outline size=sm"
                        )

                    elif status == "active":

                        async def on_deactivate(
                            v: str = version,
                            sn: str = strategy_name,
                        ) -> None:
                            await _show_model_action_dialog(service, sn, v, "DEACTIVATE", user)

                        ui.button("Deactivate", on_click=on_deactivate, color="red").props(
                            "outline size=sm"
                        )

            # Expandable details
            with ui.expansion("Details").classes("w-full mt-2"):
                if model.get("performance_metrics"):
                    ui.label("Performance Metrics:").classes("font-semibold text-sm")
                    ui.json_editor(
                        {"content": {"json": model["performance_metrics"]}},
                    ).classes("max-h-40")
                if model.get("config"):
                    ui.label("Config:").classes("font-semibold text-sm mt-2")
                    ui.json_editor(
                        {"content": {"json": model["config"]}},
                    ).classes("max-h-40")
                if model.get("notes"):
                    ui.label(f"Notes: {model['notes']}").classes("text-sm text-gray-600 mt-2")
                if model.get("activated_at"):
                    ui.label(f"Activated: {model['activated_at']}").classes("text-xs text-gray-400")
                if model.get("deactivated_at"):
                    ui.label(f"Deactivated: {model['deactivated_at']}").classes(
                        "text-xs text-gray-400"
                    )


async def _show_model_action_dialog(
    service: ModelRegistryBrowserService,
    strategy_name: str,
    version: str,
    action: str,
    user: dict[str, Any],
) -> None:
    """Show confirmation dialog for model activate/deactivate."""
    with ui.dialog() as dialog, ui.card().classes("p-6"):
        ui.label("Confirm Action").classes("text-xl font-bold")
        ui.label(
            f"Type {action} to confirm {action.lower()}ing model " f"'{strategy_name}/{version}'"
        )
        confirm_input = ui.input(label="Confirmation").classes("w-full")

        async def on_confirm() -> None:
            if confirm_input.value != action:
                ui.notify(f"Type {action} to confirm", type="negative")
                return
            try:
                if action == "ACTIVATE":
                    await service.activate_model(strategy_name, version, user)
                else:
                    await service.deactivate_model(strategy_name, version, user)
                ui.notify(
                    f"Model {strategy_name}/{version} {action.lower()}d",
                    type="positive",
                )
                dialog.close()
                # Force page reload to reflect changes
                ui.navigate.reload()
            except PermissionError as e:
                logger.exception(
                    "model_action_permission_denied",
                    extra={
                        "strategy_name": strategy_name,
                        "version": version,
                        "error": str(e),
                    },
                )
                ui.notify(f"Permission denied: {e}", type="negative")
            except ValueError as e:
                ui.notify(str(e), type="negative")
            except psycopg.OperationalError as e:
                logger.exception(
                    "model_action_db_error",
                    extra={
                        "strategy_name": strategy_name,
                        "version": version,
                        "error": str(e),
                    },
                )
                ui.notify("Database error. Please try again.", type="negative")

        with ui.row().classes("gap-2 mt-4"):
            ui.button("Confirm", on_click=on_confirm, color="red")
            ui.button("Cancel", on_click=dialog.close)

    dialog.open()
