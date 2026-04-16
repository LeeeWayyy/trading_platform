"""Model Registry Browser page for NiceGUI web console (P6T17.2).

Provides model listing per strategy with activate/deactivate controls.
Reads from the Postgres ``model_registry`` table (active/inactive/testing/failed
statuses), NOT the file-based registry.
"""

from __future__ import annotations

import logging
import os
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


def _format_model_timestamp(value: Any) -> str:
    """Format model datetime values for compact dense-grid rows."""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if value is None:
        return "—"
    return str(value)


def _model_status_badge(status: str) -> tuple[str, str]:
    """Return normalized status text and corresponding style class."""
    normalized = status.strip().lower()
    if normalized == "active":
        return "ACTIVE", "workspace-v2-pill workspace-v2-pill-positive"
    if normalized == "testing":
        return "TESTING", "workspace-v2-pill workspace-v2-pill-warning"
    if normalized == "failed":
        return "FAILED", "workspace-v2-pill workspace-v2-pill-negative"
    return "INACTIVE", "workspace-v2-pill"


def _summarize_metrics(metrics: Any) -> str:
    """Create a short one-line performance summary from metrics payload."""
    if not isinstance(metrics, dict):
        return "—"
    sharpe = metrics.get("sharpe") or metrics.get("sharpe_ratio")
    cagr = metrics.get("cagr")
    win_rate = metrics.get("win_rate")
    snippets: list[str] = []
    if isinstance(sharpe, int | float):
        snippets.append(f"SR {sharpe:.2f}")
    if isinstance(cagr, int | float):
        snippets.append(f"CAGR {cagr:.2%}")
    if isinstance(win_rate, int | float):
        snippets.append(f"WR {win_rate:.1%}")
    return " · ".join(snippets) if snippets else "Metrics"


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

    ui.label("Model Registry Browser").classes("text-2xl font-bold mb-2")
    with ui.card().classes("w-full p-2 mb-2 border border-slate-800 bg-slate-900/35"):
        with ui.row().classes("items-center justify-between gap-2"):
            ui.label("Legacy page: use Research Workspace → Promote for consolidated flow.").classes(
                "text-xs text-slate-300"
            )
            ui.link("Open /research", "/research?tab=promote").classes("text-xs")
    ui.label(
        "Dense model registry surface grouped by strategy with scoped promote/demote actions."
    ).classes("text-xs text-slate-400 mb-3")

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

    with ui.column().classes("w-full gap-3"):
        for strategy in strategy_list:
            strategy_name = str(strategy["strategy_name"])
            with ui.card().classes("w-full p-0 border border-slate-800 bg-slate-900/30"):
                with ui.row().classes("w-full items-center justify-between px-3 py-2 border-b border-slate-800"):
                    ui.label(strategy_name).classes("text-sm font-semibold text-slate-100")
                    model_count = strategy.get("model_count")
                    if isinstance(model_count, int):
                        ui.label(f"{model_count} versions").classes(
                            "workspace-v2-pill workspace-v2-data-mono"
                        )
                with ui.column().classes("w-full p-3 gap-2"):
                    await _render_strategy_models(service, strategy_name, user, can_manage)


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

    active_count = sum(1 for model in models if str(model.get("status", "")).lower() == "active")
    testing_count = sum(
        1 for model in models if str(model.get("status", "")).lower() == "testing"
    )
    failed_count = sum(1 for model in models if str(model.get("status", "")).lower() == "failed")

    with ui.row().classes("w-full gap-2 mb-1"):
        ui.label(f"{len(models)} models").classes("workspace-v2-pill workspace-v2-data-mono")
        ui.label(f"{active_count} active").classes(
            "workspace-v2-pill workspace-v2-pill-positive workspace-v2-data-mono"
        )
        ui.label(f"{testing_count} testing").classes(
            "workspace-v2-pill workspace-v2-pill-warning workspace-v2-data-mono"
        )
        ui.label(f"{failed_count} failed").classes(
            "workspace-v2-pill workspace-v2-pill-negative workspace-v2-data-mono"
        )

    with ui.row().classes(
        "w-full items-center gap-3 px-3 py-2 text-[11px] uppercase tracking-wide text-slate-400"
    ):
        ui.label("Status").classes("w-20")
        ui.label("Version").classes("w-24")
        ui.label("Strategy").classes("w-36")
        ui.label("Deployed").classes("w-40")
        ui.label("Metrics").classes("w-40")
        ui.label("Path").classes("flex-1")
        ui.label("Actions").classes("w-28 text-right")

    for model in models:
        version = str(model["version"])
        version_label = version if version.startswith("v") else f"v{version}"
        status = str(model.get("status", "inactive"))
        status_label, status_style = _model_status_badge(status)
        deployed_at = _format_model_timestamp(model.get("activated_at") or model.get("created_at"))
        metrics_summary = _summarize_metrics(model.get("performance_metrics"))
        model_path = str(model.get("model_path") or "—")

        with ui.card().classes("w-full p-0 border border-slate-800 bg-slate-900/35"):
            with ui.row().classes("w-full items-center gap-3 px-3 py-2 text-sm"):
                ui.label(status_label).classes(f"w-20 {status_style}")
                ui.label(version_label).classes("w-24 text-sm font-semibold text-slate-100")
                ui.label(strategy_name).classes("w-36 text-xs text-slate-300 workspace-v2-data-mono")
                ui.label(deployed_at).classes("w-40 text-xs text-slate-400 workspace-v2-data-mono")
                ui.label(metrics_summary).classes(
                    "w-40 text-xs text-slate-300 workspace-v2-data-mono"
                )
                ui.label(model_path).classes("flex-1 text-xs text-slate-400 workspace-v2-data-mono")

                if can_manage:
                    if status.lower() in ("inactive", "testing"):

                        async def on_activate(
                            v: str = version,
                            sn: str = strategy_name,
                        ) -> None:
                            await _show_model_action_dialog(service, sn, v, "ACTIVATE", user)

                        ui.button("Activate", on_click=on_activate, color="green").props(
                            "outline size=sm"
                        ).classes("w-24")

                    elif status.lower() == "active":

                        async def on_deactivate(
                            v: str = version,
                            sn: str = strategy_name,
                        ) -> None:
                            await _show_model_action_dialog(service, sn, v, "DEACTIVATE", user)

                        ui.button("Deactivate", on_click=on_deactivate, color="red").props(
                            "outline size=sm"
                        ).classes("w-24")
                    else:
                        ui.label("No action").classes("w-24 text-xs text-slate-500 text-right")
                else:
                    ui.label("Read only").classes("w-24 text-xs text-slate-500 text-right")

            with ui.expansion("Details").classes("w-full px-3 pb-3 pt-1"):
                if model.get("performance_metrics"):
                    ui.label("Performance Metrics").classes("font-semibold text-xs text-slate-300")
                    ui.json_editor({"content": {"json": model["performance_metrics"]}}).classes(
                        "max-h-40 mt-1"
                    )
                if model.get("config"):
                    ui.label("Config").classes("font-semibold text-xs text-slate-300 mt-2")
                    ui.json_editor({"content": {"json": model["config"]}}).classes("max-h-40 mt-1")
                if model.get("notes"):
                    ui.label(f"Notes: {model['notes']}").classes("text-xs text-slate-300 mt-2")
                ui.label(
                    f"Created by: {model.get('created_by') or 'unknown'} · "
                    f"Activated: {_format_model_timestamp(model.get('activated_at'))} · "
                    f"Deactivated: {_format_model_timestamp(model.get('deactivated_at'))}"
                ).classes("text-[11px] text-slate-500 mt-1 workspace-v2-data-mono")


async def _show_model_action_dialog(
    service: ModelRegistryBrowserService,
    strategy_name: str,
    version: str,
    action: str,
    user: dict[str, Any],
) -> None:
    """Show confirmation dialog for model activate/deactivate."""
    confirmation_token = f"{strategy_name}:{version}"
    impact_summary = (
        "Impact: this version becomes active and other active versions for this strategy are deactivated."
        if action == "ACTIVATE"
        else "Impact: this version is marked inactive and removed from active rotation."
    )

    with ui.dialog() as dialog, ui.card().classes("p-6"):
        ui.label("Confirm Action").classes("text-xl font-bold")
        ui.label(
            f"Type '{confirmation_token}' to confirm {action.lower()} "
            f"for model '{strategy_name}/{version}'."
        )
        ui.label(impact_summary).classes("text-xs text-slate-500")
        confirm_input = ui.input(label="Confirmation").classes("w-full")

        async def on_confirm() -> None:
            if confirm_input.value != confirmation_token:
                ui.notify(f"Type {confirmation_token} to confirm", type="negative")
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
