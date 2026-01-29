"""Symbol filter component for trading panels."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from nicegui import ui

logger = logging.getLogger(__name__)

ALL_SYMBOL_LABEL = "All"


def normalize_symbol(value: str | None) -> str | None:
    """Normalize symbol input to uppercase or None for all/empty."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.lower() == ALL_SYMBOL_LABEL.lower():
        return None
    return text.upper()


def build_symbol_options(symbols: list[str] | None) -> list[str]:
    """Build symbol options including the All entry."""
    options: list[str] = [ALL_SYMBOL_LABEL]
    if symbols:
        # Filter out None values before sorting to satisfy type checker
        normalized_set = {normalize_symbol(symbol) for symbol in symbols if symbol}
        valid_symbols = [s for s in normalized_set if s is not None]
        options.extend(sorted(valid_symbols))
    return options


@dataclass
class SymbolFilterState:
    """Holds state for a symbol filter control."""

    value: str | None
    select: ui.select | None = None

    def set_value(self, raw_value: str | None) -> None:
        """Set filter value and update UI selection if available."""
        normalized = normalize_symbol(raw_value)
        self.value = normalized
        if self.select is not None:
            if normalized:
                options = list(self.select.options or [])
                if normalized not in options:
                    options.append(normalized)
                    self.select.options = options
                    self.select.update()
            self.select.value = normalized or ALL_SYMBOL_LABEL

    def update_options(self, symbols: list[str] | None) -> None:
        """Update select options from a new symbol list."""
        if self.select is None:
            return
        self.select.options = build_symbol_options(symbols)
        self.select.update()


def create_symbol_filter(
    symbols: list[str] | None = None,
    *,
    value: str | None = None,
    label: str = "Symbol",
    on_change: Callable[[str | None], None] | None = None,
) -> SymbolFilterState:
    """Create a reusable symbol filter control.

    Args:
        symbols: Optional list of symbols to populate dropdown.
        value: Initial symbol value (None = All).
        label: Field label.
        on_change: Callback invoked with normalized symbol or None.

    Returns:
        SymbolFilterState instance with UI element and value.
    """
    options = build_symbol_options(symbols)
    normalized = normalize_symbol(value)
    initial_raw = normalized or ALL_SYMBOL_LABEL
    select = ui.select(
        options=options,
        label=label,
        value=initial_raw,
        with_input=True,
        new_value_mode="add-unique",
        clearable=True,
    ).classes("w-36 input-compact")

    state = SymbolFilterState(value=normalized, select=select)

    def _handle_change(event: Any) -> None:
        raw = getattr(event, "value", event)
        state.set_value(raw)
        if on_change is not None:
            on_change(state.value)

    select.on_value_change(_handle_change)
    return state


__all__ = [
    "SymbolFilterState",
    "create_symbol_filter",
    "normalize_symbol",
    "build_symbol_options",
    "ALL_SYMBOL_LABEL",
]
