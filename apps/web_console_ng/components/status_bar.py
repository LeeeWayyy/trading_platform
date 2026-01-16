"""Persistent status bar for trading state."""

from __future__ import annotations

from nicegui import ui


class StatusBar:
    """Thin persistent status bar for trading state."""

    def __init__(self) -> None:
        self._container: ui.element | None = None
        self._label: ui.label | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        self._container = ui.element("div").classes(
            "w-full h-6 flex items-center justify-center text-xs font-semibold tracking-wide"
        )
        with self._container:
            self._label = ui.label("TRADING STATUS UNKNOWN").classes("uppercase")
        self._set_state_classes("UNKNOWN")

    def _set_state_classes(self, state: str) -> None:
        if not self._container:
            return
        if state == "ENGAGED":
            self._container.classes(
                "bg-red-600 text-white",
                remove="bg-green-600 bg-yellow-500 text-black",
            )
        elif state == "DISENGAGED":
            self._container.classes(
                "bg-green-600 text-white",
                remove="bg-red-600 bg-yellow-500 text-black",
            )
        else:
            self._container.classes(
                "bg-yellow-500 text-black",
                remove="bg-red-600 bg-green-600 text-white",
            )

    def update_state(self, state: str | None) -> None:
        """Update status bar text and color based on kill switch state."""
        normalized = (state or "UNKNOWN").upper()
        if self._label:
            if normalized == "ENGAGED":
                self._label.set_text("TRADING HALTED")
            elif normalized == "DISENGAGED":
                self._label.set_text("TRADING ACTIVE")
            else:
                self._label.set_text("TRADING STATUS UNKNOWN")
        self._set_state_classes(normalized)


__all__ = ["StatusBar"]
