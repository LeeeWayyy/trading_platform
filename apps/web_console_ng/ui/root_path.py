"""Helpers for resolving app paths under ASGI root_path."""

from __future__ import annotations

import json
import logging
from typing import Any

from nicegui import ui

from apps.web_console_ng.auth.redirects import with_root_path_once

logger = logging.getLogger(__name__)


def resolve_rooted_path_from_ui(path: str, *, ui_module: Any | None = None) -> str:
    """Prefix ``path`` with current request root_path when available."""
    ui_ref = ui if ui_module is None else ui_module
    try:
        context = getattr(ui_ref, "context", None)
    except (AttributeError, RuntimeError, LookupError):
        context = None
    try:
        client = getattr(context, "client", None) if context is not None else None
    except (AttributeError, RuntimeError, LookupError):
        client = None
    request = getattr(client, "request", None)
    scope = getattr(request, "scope", {}) if request is not None else {}
    root_path = scope.get("root_path") if isinstance(scope, dict) else None
    return with_root_path_once(path, root_path=root_path)


def render_client_redirect(
    target: str,
    *,
    ui_module: Any | None = None,
    message: str = "Redirecting to canonical page...",
) -> None:
    """Render a deterministic client-side redirect with a visible fallback link."""
    ui_ref = ui if ui_module is None else ui_module

    # JS replace keeps browser history clean on modern clients.
    # Link fallback below covers environments where JS is unavailable.
    target_json = (
        json.dumps(target)
        .replace("</", "<\\/")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    try:
        ui_ref.run_javascript(f"window.location.replace({target_json});")
    except Exception:
        # Fallback link below handles environments where JS execution is delayed.
        logger.debug(
            "render_client_redirect_js_unavailable",
            extra={"target": target},
            exc_info=True,
        )

    with ui_ref.column().classes("w-full items-center justify-center gap-2 min-h-[30vh]"):
        ui_ref.label(message).classes("text-sm text-gray-400")
        ui_ref.link("Continue", target=target).classes("text-sm text-blue-400")


__all__ = ["render_client_redirect", "resolve_rooted_path_from_ui"]
