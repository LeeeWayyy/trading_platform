"""High-density trading layout utilities."""

from __future__ import annotations

from typing import Any

from nicegui import ui


def compact_card(title: str | None = None) -> ui.card:
    """Create a compact card with reduced padding."""
    card = ui.card().classes("card-compact bg-surface-1")
    if title:
        with card:
            ui.label(title).classes("text-xs font-semibold text-text-secondary uppercase")
    return card


def trading_grid() -> ui.element:
    """Create a dense grid container for trading widgets."""
    return ui.element("div").classes("grid-dense trading-layout")


def stats_row() -> ui.row:
    """Create a compact row for statistics."""
    return ui.row().classes("gap-2 items-center h-6")


def numeric_label(value: str | float, prefix: str = "", suffix: str = "") -> ui.label:
    """Create a monospace label for numeric values."""
    text = f"{prefix}{value}{suffix}"
    return ui.label(text).classes("font-mono-numbers text-sm")


def apply_compact_grid_options(options: dict[str, Any]) -> dict[str, Any]:
    """Add compact styling options to AG Grid config."""
    options.update(
        {
            "rowHeight": 22,
            "headerHeight": 28,
            "suppressCellFocus": True,
        }
    )
    return options


def apply_compact_grid_classes(grid: ui.aggrid) -> ui.aggrid:
    """Apply compact CSS classes to AG Grid wrapper.

    IMPORTANT: Must be called after grid creation to add the .ag-grid-compact class
    which applies the compact row/header heights via CSS.
    """
    grid.classes("ag-grid-compact")
    return grid


__all__ = [
    "compact_card",
    "trading_grid",
    "stats_row",
    "numeric_label",
    "apply_compact_grid_options",
    "apply_compact_grid_classes",
]
