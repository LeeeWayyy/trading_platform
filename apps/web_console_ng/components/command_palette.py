"""Command palette component for quick action access."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from nicegui import ui

if TYPE_CHECKING:
    from apps.web_console_ng.core.hotkey_manager import HotkeyManager

logger = logging.getLogger(__name__)


class CommandPalette:
    """Command palette dialog for quick access to actions."""

    def __init__(self, hotkey_manager: HotkeyManager) -> None:
        self._hotkey_manager = hotkey_manager
        self._dialog: ui.dialog | None = None
        self._search_input: ui.input | None = None
        self._commands_container: ui.column | None = None
        self._commands: dict[str, tuple[str, Callable[[], None]]] = {}

    def register_command(self, action: str, label: str, callback: Callable[[], None]) -> None:
        """Register a command for the palette."""
        self._commands[action] = (label, callback)

    def create(self) -> ui.dialog:
        """Create the command palette dialog."""
        self._dialog = ui.dialog().props("maximized position=top")

        with self._dialog:
            with ui.card().classes("w-full max-w-xl mx-auto mt-20 bg-surface-2"):
                self._search_input = (
                    ui.input(placeholder="Type a command...")
                    .props("autofocus outlined dense")
                    .classes("w-full")
                    .on("keydown.escape", self.close)
                    .on("input", self._on_search)
                )

                with ui.scroll_area().classes("max-h-96"):
                    self._commands_container = ui.column().classes("w-full gap-0")
                    self._render_commands()

        return self._dialog

    def open(self) -> None:
        """Open the command palette."""
        if self._dialog:
            self._dialog.open()
            if self._search_input:
                self._search_input.value = ""
                self._render_commands()

    def close(self) -> None:
        """Close the command palette."""
        if self._dialog:
            self._dialog.close()

    def _on_search(self, event: Any) -> None:
        """Filter commands based on search input."""
        self._render_commands(filter_text=str(event.value or ""))

    def _render_commands(self, filter_text: str = "") -> None:
        """Render command list with optional filtering."""
        if not self._commands_container:
            return

        self._commands_container.clear()
        filter_lower = filter_text.lower()

        bindings_by_action = {binding.action: binding for binding in self._hotkey_manager.get_bindings()}

        with self._commands_container:
            for action, (label, callback) in self._commands.items():
                if filter_lower and filter_lower not in label.lower():
                    continue

                binding = bindings_by_action.get(action)
                hotkey_hint = ""
                if binding:
                    parts: list[str] = []
                    for mod in binding.modifiers:
                        parts.append(mod.capitalize())
                    parts.append(binding.key.upper())
                    hotkey_hint = "+".join(parts)

                with ui.row().classes(
                    "w-full items-center justify-between p-3 hover:bg-surface-3 cursor-pointer rounded"
                ).on("click", lambda cb=callback: self._execute(cb)):
                    ui.label(label).classes("text-white")
                    if hotkey_hint:
                        ui.label(hotkey_hint).classes(
                            "text-xs text-gray-500 bg-surface-1 px-2 py-1 rounded font-mono"
                        )

    def _execute(self, callback: Callable[[], None]) -> None:
        """Execute command and close palette."""
        self.close()
        try:
            callback()
        except Exception as exc:
            logger.error(
                "command_palette_execution_failed",
                extra={"error": str(exc)},
            )


__all__ = ["CommandPalette"]
