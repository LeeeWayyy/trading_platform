"""Reusable metric card component for the NiceGUI dashboard."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from nicegui import ui


class MetricCard:
    """Reusable metric card component with real-time updates.

    Features:
    - format_fn: custom formatting for displayed value
    - color_fn: optional class selector based on value
    - stale detection (mark_stale / is_stale)
    - exact color class cleanup (track current class)
    """

    def __init__(
        self,
        title: str,
        initial_value: str = "--",
        format_fn: Callable[[Any], str] | None = None,
        color_fn: Callable[[Any], str] | None = None,
    ) -> None:
        self.title = title
        self.format_fn = format_fn or str
        self.color_fn = color_fn
        self._value_label = None
        self._last_update: float | None = None
        self._current_color_class: str | None = None

        with ui.card().classes("flex-1 min-w-[200px]"):
            ui.label(title).classes("text-gray-500 text-sm")
            self._value_label = ui.label(initial_value).classes("text-2xl font-bold")

    def update(self, value: Any) -> None:
        """Update card value with optional color change."""
        if self._value_label is None:
            return

        formatted = self.format_fn(value)
        self._value_label.set_text(formatted)

        if self.color_fn:
            new_color_class = self.color_fn(value)
            if self._current_color_class and self._current_color_class != new_color_class:
                self._value_label.classes(remove=self._current_color_class)
            self._value_label.classes(new_color_class)
            self._current_color_class = new_color_class

        # Clear stale indicator on fresh data
        self._value_label.classes(remove="opacity-50")
        self._last_update = time.time()

    def mark_stale(self) -> None:
        """Mark data as stale (> 30s old)."""
        if self._value_label is None:
            return
        self._value_label.classes("opacity-50")

    def is_stale(self, threshold: float = 30.0) -> bool:
        """Check if data is stale (no update within threshold seconds)."""
        if self._last_update is None:
            return False
        return (time.time() - self._last_update) > threshold


__all__ = ["MetricCard"]
