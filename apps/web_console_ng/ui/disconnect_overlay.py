"""Helpers for injecting disconnect overlay JS into NiceGUI pages."""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

_JS_PATH = Path(__file__).with_name("disconnect_overlay.js")
_injected = False


def inject_disconnect_overlay() -> None:
    """Inject the disconnect overlay JavaScript once per process."""
    global _injected
    if _injected:
        return
    script = _JS_PATH.read_text(encoding="utf-8")
    ui.add_head_html(f"<script>{script}</script>")
    _injected = True


__all__ = ["inject_disconnect_overlay"]
