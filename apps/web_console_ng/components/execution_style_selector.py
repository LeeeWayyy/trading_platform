"""Execution style selector for order entry."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui


class ExecutionStyleSelector:
    """Selector for choosing execution style (instant vs TWAP)."""

    def __init__(self, on_change: Callable[[str], None]) -> None:
        self._on_change = on_change
        self._toggle: ui.toggle | None = None
        self._disabled_hint: ui.label | None = None
        self._value = "instant"

    def create(self) -> ui.row:
        """Create the selector UI."""
        with ui.row().classes("w-full gap-2 items-center") as row:
            ui.label("Execution").classes("w-24")
            self._toggle = ui.toggle(["instant", "twap"], value=self._value).classes("flex-1")
            self._toggle.on_value_change(lambda e: self._handle_change(e.value))
            self._disabled_hint = ui.label("").classes("text-xs text-amber-600 hidden")
        return row

    def _handle_change(self, value: str) -> None:
        self._value = str(value)
        self._on_change(self._value)

    def value(self) -> str:
        """Return the current execution style value."""
        return self._value

    def set_value(self, value: str) -> None:
        """Update selector value."""
        self._value = value
        if self._toggle:
            self._toggle.value = value

    def set_disabled(self, disabled: bool, reason: str | None = None) -> None:
        """Enable/disable selector with optional reason hint."""
        if self._toggle is None:
            if disabled:
                self._value = "instant"
            return

        if disabled:
            self.set_value("instant")
            self._toggle.disable()
        else:
            self._toggle.enable()

        if self._disabled_hint:
            if disabled and reason:
                self._disabled_hint.set_text(reason)
                self._disabled_hint.classes(remove="hidden")
            else:
                self._disabled_hint.set_text("")
                self._disabled_hint.classes(add="hidden")
