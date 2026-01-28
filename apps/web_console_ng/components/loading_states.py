"""Shared loading state utilities for UI feedback."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from nicegui import ui


@asynccontextmanager
async def loading_overlay(
    element: ui.element, message: str = "Loading..."
) -> AsyncGenerator[None, None]:
    """Context manager that shows loading overlay on an element."""
    with element:
        with ui.element("div").classes(
            "absolute inset-0 bg-black/50 flex items-center justify-center z-50"
        ) as overlay:
            with ui.column().classes("items-center gap-2"):
                ui.spinner("dots", size="lg")
                ui.label(message).classes("text-white text-sm")

    try:
        yield
    finally:
        overlay.delete()


def inline_spinner(size: str = "sm") -> ui.spinner:
    """Create an inline spinner for button/text loading states."""
    return ui.spinner("dots", size=size).classes("mx-1")


def skeleton_loader(width: str = "w-full", height: str = "h-4") -> ui.element:
    """Create a skeleton loader placeholder."""
    return ui.element("div").classes(f"{width} {height} bg-surface-3 rounded animate-pulse")


class LoadingState:
    """Reusable loading state manager for components."""

    def __init__(self) -> None:
        self._element: ui.element | None = None
        self._overlay: ui.element | None = None
        self._message_label: ui.label | None = None

    def attach(self, element: ui.element) -> None:
        """Attach to a parent element for overlay display."""
        self._element = element

    def start(self, message: str = "Loading...") -> None:
        """Show loading overlay."""
        if self._element is None:
            return

        with self._element:
            self._overlay = ui.element("div").classes(
                "absolute inset-0 bg-black/50 flex items-center justify-center z-50"
            )
            with self._overlay:
                with ui.column().classes("items-center gap-2"):
                    ui.spinner("dots", size="lg")
                    self._message_label = ui.label(message).classes("text-white text-sm")

    def update_message(self, message: str) -> None:
        """Update loading message."""
        if self._message_label:
            self._message_label.set_text(message)

    def stop(self) -> None:
        """Hide loading overlay."""
        if self._overlay:
            self._overlay.delete()
            self._overlay = None
            self._message_label = None


__all__ = [
    "LoadingState",
    "inline_spinner",
    "loading_overlay",
    "skeleton_loader",
]
