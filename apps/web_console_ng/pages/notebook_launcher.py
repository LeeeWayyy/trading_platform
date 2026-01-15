"""Research Notebook Launcher page for NiceGUI web console (P5T8).

Provides interface for launching and managing research notebooks.

Features:
    - Notebook template selector
    - Dynamic parameters form
    - Launch with confirmation
    - Active sessions table with terminate option

PARITY: Mirrors UI layout from apps/web_console/pages/notebook_launcher.py

NOTE: This page uses demo mode with placeholder data when services are unavailable.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from nicegui import run, ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.core.dependencies import get_sync_redis_client
from apps.web_console_ng.ui.layout import main_layout
from libs.platform.web_console_auth.permissions import Permission, has_permission

logger = logging.getLogger(__name__)

# Redis key prefix for notebook sessions
_NOTEBOOK_SESSION_PREFIX = "notebook_session:"
_NOTEBOOK_SESSION_TTL = 86400  # 24 hours


def _get_redis_session_store(user_id: str) -> dict[str, Any]:
    """Get notebook session store from Redis for a user.

    Uses Redis for multi-worker consistency instead of module-level dict.
    """
    redis_client = get_sync_redis_client()
    key = f"{_NOTEBOOK_SESSION_PREFIX}{user_id}"
    data = redis_client.get(key)
    if data:
        try:
            parsed = json.loads(data)  # type: ignore[arg-type]
            if isinstance(parsed, dict):
                return parsed
            return {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _save_redis_session_store(user_id: str, session_store: dict[str, Any]) -> None:
    """Save notebook session store to Redis for a user."""
    redis_client = get_sync_redis_client()
    key = f"{_NOTEBOOK_SESSION_PREFIX}{user_id}"
    redis_client.setex(key, _NOTEBOOK_SESSION_TTL, json.dumps(session_store))


def _get_service(user: dict[str, Any], session_store: dict[str, Any]) -> Any:
    """Get or create NotebookLauncherService with session storage."""
    from libs.web_console_services.notebook_launcher_service import NotebookLauncherService

    return NotebookLauncherService(
        user=dict(user),
        session_store=session_store,
    )


@ui.page("/notebooks")
@requires_auth
@main_layout
async def notebook_launcher_page() -> None:
    """Research Notebook Launcher page."""
    user = get_current_user()
    user_id = str(user.get("user_id") or user.get("username") or "unknown")

    # Page title
    ui.label("Research Notebook Launcher").classes("text-2xl font-bold mb-4")

    # Permission check
    if not has_permission(user, Permission.LAUNCH_NOTEBOOKS):
        ui.notify("Permission denied: LAUNCH_NOTEBOOKS required", type="negative")
        with ui.card().classes("w-full p-6"):
            ui.label("Permission denied: LAUNCH_NOTEBOOKS required.").classes(
                "text-red-500 text-center"
            )
        return

    # Use Redis-backed session store for multi-worker consistency.
    session_store = _get_redis_session_store(user_id)

    # Try to get service
    try:
        service = await run.io_bound(_get_service, user, session_store)
    except ImportError as e:
        logger.error(
            "Failed to initialize NotebookLauncherService - missing dependencies",
            extra={"error": str(e), "page": "notebook_launcher"},
            exc_info=True,
        )
        _render_demo_mode()
        return
    except RuntimeError as e:
        message = str(e)
        logger.error(
            "Failed to initialize NotebookLauncherService - runtime configuration error",
            extra={"error": message, "page": "notebook_launcher"},
            exc_info=True,
        )
        _render_notebook_config_help(message)
        return
    except Exception as e:
        logger.error(
            "Failed to initialize NotebookLauncherService",
            extra={"error": str(e), "page": "notebook_launcher"},
            exc_info=True,
        )
        _render_demo_mode()
        return

    # Load templates
    try:
        templates = await run.io_bound(service.list_templates)
    except FileNotFoundError as e:
        logger.error(
            "Failed to load notebook templates - templates directory not found",
            extra={"error": str(e), "page": "notebook_launcher"},
            exc_info=True,
        )
        with ui.card().classes("w-full p-6"):
            ui.label("Templates directory not found. Please check configuration.").classes(
                "text-red-500 text-center"
            )
        return
    except Exception as e:
        logger.error(
            "Failed to load notebook templates",
            extra={"error": str(e), "page": "notebook_launcher"},
            exc_info=True,
        )
        with ui.card().classes("w-full p-6"):
            ui.label(f"Failed to load notebook templates: {e}").classes(
                "text-red-500 text-center"
            )
        return

    if not templates:
        with ui.card().classes("w-full p-6"):
            ui.label("No notebook templates available.").classes(
                "text-gray-500 text-center"
            )
        return

    await _render_notebook_launcher(service, templates, user_id, session_store)


def _render_notebook_config_help(error_message: str) -> None:
    """Render actionable setup instructions when notebook launch config is missing."""
    with ui.card().classes("w-full p-6 border border-yellow-300 bg-yellow-50"):
        ui.label("Notebook launcher is not configured.").classes(
            "text-lg font-semibold text-yellow-800 mb-2"
        )
        ui.label(error_message).classes("text-yellow-700 mb-4")
        ui.label("To enable notebooks in local dev:").classes("text-sm font-semibold mb-2")
        ui.markdown(
            """
1. Add to `.env`:
```
NOTEBOOK_BASE_URL=http://localhost
NOTEBOOK_LAUNCH_COMMAND=jupyter lab --ip=0.0.0.0 --port={port} --no-browser --NotebookApp.token={token} --NotebookApp.allow_remote_access=True /app/{template_path}
```
2. Ensure `notebooks/templates/` exists (mounted into the container).
3. Restart the web console: `docker compose --profile dev up -d web_console_dev`
""",
        ).classes("text-sm")


async def _render_notebook_launcher(
    service: Any, templates: list[Any], user_id: str, session_store: dict[str, Any]
) -> None:
    """Render the full notebook launcher interface."""
    # Template selector
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("Select Template").classes("text-lg font-bold mb-2")

        template_options = {t.template_id: t.name for t in templates}
        template_select = ui.select(
            label="Notebook Template",
            options=template_options,
            value=templates[0].template_id if templates else None,
        ).classes("w-full max-w-md")

        # Template description
        desc_label = ui.label(
            templates[0].description if templates else ""
        ).classes("text-gray-600 text-sm mt-2")

        def update_description() -> None:
            selected_id = template_select.value
            selected = next((t for t in templates if t.template_id == selected_id), None)
            if selected:
                desc_label.set_text(selected.description or "")

        template_select.on_value_change(lambda _: update_description())

    # Parameters form
    params_container = ui.column().classes("w-full mb-4")

    # State for parameters (values accessed via .value attribute)
    param_inputs: dict[str, Any] = {}

    @ui.refreshable
    def render_parameters_form() -> None:
        params_container.clear()
        param_inputs.clear()

        selected_id = template_select.value
        selected = next((t for t in templates if t.template_id == selected_id), None)

        if not selected or not selected.parameters:
            return

        with params_container:
            with ui.card().classes("w-full p-4"):
                ui.label("Parameters").classes("text-lg font-bold mb-2")

                with ui.column().classes("gap-4"):
                    for param in selected.parameters:
                        param_name = param.key
                        param_type = param.kind
                        param_label = param.label or param_name
                        default = param.default
                        options = list(param.options or [])

                        inp: Any  # Can be Select, Number, or Input
                        if options:
                            inp = ui.select(
                                label=param_label,
                                options=options,
                                value=default,
                            ).classes("w-full max-w-md")
                        elif param_type == "number":
                            inp = ui.number(
                                label=param_label,
                                value=float(default) if default is not None else 0,
                            ).classes("w-full max-w-md")
                        else:
                            inp = ui.input(
                                label=param_label,
                                value=str(default) if default is not None else "",
                            ).classes("w-full max-w-md")

                        param_inputs[param_name] = inp

    render_parameters_form()
    template_select.on_value_change(lambda _: render_parameters_form.refresh())

    # Launch button
    with ui.row().classes("w-full gap-4 mb-4"):
        launch_btn = ui.button("Launch Notebook", icon="play_arrow").props("color=primary")

    # Result container
    result_container = ui.column().classes("w-full mb-4")

    async def launch_notebook() -> None:
        result_container.clear()

        selected_id = template_select.value
        if not selected_id:
            ui.notify("Please select a template", type="warning")
            return

        # Gather parameters
        parameters = {name: inp.value for name, inp in param_inputs.items()}

        with result_container:
            ui.spinner("dots")
            ui.label("Launching notebook...")

        try:
            from libs.web_console_services.notebook_launcher_service import SessionStatus

            session = await run.io_bound(
                service.create_notebook, selected_id, parameters
            )
            # Persist session changes to Redis for multi-worker consistency
            await run.io_bound(_save_redis_session_store, user_id, session_store)

            result_container.clear()
            with result_container:
                if session.status == SessionStatus.ERROR:
                    ui.label(
                        session.error_message or "Notebook launch failed."
                    ).classes("text-red-500 p-2")
                else:
                    ui.label("Notebook session started successfully!").classes(
                        "text-green-600 p-2"
                    )
                    if session.access_url:
                        with ui.row().classes("items-center gap-2"):
                            ui.label("Access URL:").classes("font-medium")
                            ui.link(session.access_url, session.access_url, new_tab=True)

        except FileNotFoundError as e:
            logger.error(
                "Failed to launch notebook - template not found",
                extra={"error": str(e), "template_id": selected_id, "page": "notebook_launcher"},
                exc_info=True,
            )
            result_container.clear()
            with result_container:
                ui.label("Template not found. Please check configuration.").classes(
                    "text-red-500 p-2"
                )
        except ValueError as e:
            logger.error(
                "Failed to launch notebook - invalid parameters",
                extra={"error": str(e), "template_id": selected_id, "parameters": parameters, "page": "notebook_launcher"},
                exc_info=True,
            )
            result_container.clear()
            with result_container:
                ui.label("Invalid parameters. Please check your inputs.").classes(
                    "text-red-500 p-2"
                )
        except Exception as e:
            logger.error(
                "Failed to launch notebook",
                extra={"error": str(e), "template_id": selected_id, "page": "notebook_launcher"},
                exc_info=True,
            )
            result_container.clear()
            with result_container:
                ui.label(f"Failed to launch notebook: {e}").classes(
                    "text-red-500 p-2"
                )

    launch_btn.on_click(launch_notebook)

    ui.separator().classes("my-4")

    # Active sessions
    await _render_active_sessions(service, user_id, session_store)


async def _render_active_sessions(
    service: Any, user_id: str, session_store: dict[str, Any]
) -> None:
    """Render active sessions table."""
    with ui.card().classes("w-full p-4"):
        ui.label("Active Sessions").classes("text-lg font-bold mb-2")

        sessions_container = ui.column().classes("w-full")

        @ui.refreshable  # type: ignore[arg-type]
        async def render_sessions() -> None:
            sessions_container.clear()

            try:
                sessions = await run.io_bound(
                    service.list_sessions, include_stopped=False
                )
            except FileNotFoundError as e:
                logger.error(
                    "Failed to load sessions - session store not found",
                    extra={"error": str(e), "page": "notebook_launcher"},
                    exc_info=True,
                )
                with sessions_container:
                    ui.label("Session store not found. Please check configuration.").classes(
                        "text-red-500 p-2"
                    )
                return
            except Exception as e:
                logger.error(
                    "Failed to load sessions",
                    extra={"error": str(e), "page": "notebook_launcher"},
                    exc_info=True,
                )
                with sessions_container:
                    ui.label(f"Failed to load sessions: {e}").classes(
                        "text-red-500 p-2"
                    )
                return

            with sessions_container:
                if not sessions:
                    ui.label("No active sessions.").classes("text-gray-500 p-4")
                    return

                columns = [
                    {"name": "session_id", "label": "Session ID", "field": "session_id"},
                    {"name": "template", "label": "Template", "field": "template"},
                    {"name": "status", "label": "Status", "field": "status"},
                    {"name": "started", "label": "Started", "field": "started"},
                    {"name": "url", "label": "URL", "field": "url"},
                ]

                rows = []
                for s in sessions:
                    rows.append({
                        "session_id": s.session_id[:8] + "...",
                        "template": s.template_id,
                        "status": s.status.value if hasattr(s.status, "value") else str(s.status),
                        "started": str(s.created_at)[:19] if s.created_at else "-",
                        "url": s.access_url or "-",
                    })

                ui.table(columns=columns, rows=rows).classes("w-full")

                # Terminate buttons
                ui.label("Terminate a session:").classes("text-sm mt-4 mb-2")
                for s in sessions:
                    async def terminate(session_id: str = s.session_id) -> None:
                        try:
                            await run.io_bound(service.terminate_session, session_id)
                            # Persist session changes to Redis for multi-worker consistency
                            await run.io_bound(_save_redis_session_store, user_id, session_store)
                            ui.notify(f"Session {session_id[:8]}... terminated", type="positive")
                            render_sessions.refresh()
                        except FileNotFoundError as e:
                            logger.error(
                                "Failed to terminate session - session not found",
                                extra={"error": str(e), "session_id": session_id, "page": "notebook_launcher"},
                                exc_info=True,
                            )
                            ui.notify("Session not found.", type="negative")
                        except Exception as e:
                            logger.error(
                                "Failed to terminate session",
                                extra={"error": str(e), "session_id": session_id, "page": "notebook_launcher"},
                                exc_info=True,
                            )
                            ui.notify(f"Failed to terminate: {e}", type="negative")

                    ui.button(
                        f"Terminate {s.session_id[:8]}...",
                        icon="stop",
                        on_click=terminate,
                    ).props("color=negative size=sm")

        await render_sessions()

        # Refresh button
        ui.button(
            "Refresh Sessions",
            icon="refresh",
            on_click=lambda: render_sessions.refresh(),
        ).classes("mt-4")


def _render_demo_mode() -> None:
    """Render demo mode with placeholder data."""
    with ui.card().classes("w-full p-3 mb-4 bg-amber-50 border border-amber-300"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("info", color="amber-700")
            ui.label(
                "Demo Mode: Notebook service unavailable."
            ).classes("text-amber-700")

    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("Select Template").classes("text-lg font-bold mb-2")

        demo_templates = ["Alpha Research", "Backtest Analysis", "Signal Exploration"]
        ui.select(
            label="Notebook Template",
            options=demo_templates,
            value=demo_templates[0],
        ).classes("w-full max-w-md")

        ui.label("Standard research notebook for alpha development.").classes(
            "text-gray-600 text-sm mt-2"
        )

    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("Parameters").classes("text-lg font-bold mb-2")

        with ui.column().classes("gap-4"):
            ui.input(label="Strategy ID", value="momentum_v1").classes("w-full max-w-md")
            ui.number(label="Lookback Days", value=30).classes("w-full max-w-md")

    ui.button("Launch Notebook", icon="play_arrow").props("color=primary disable")

    ui.separator().classes("my-4")

    with ui.card().classes("w-full p-4"):
        ui.label("Active Sessions").classes("text-lg font-bold mb-2")
        ui.label("No active sessions.").classes("text-gray-500 p-4")


__all__ = ["notebook_launcher_page"]
