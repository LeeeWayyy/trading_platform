"""Helpers for resolving app paths under ASGI root_path."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from apps.web_console_ng.auth.redirects import with_root_path_once


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


__all__ = ["resolve_rooted_path_from_ui"]
