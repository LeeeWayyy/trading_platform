"""UI helper utilities for NiceGUI."""

from __future__ import annotations

from typing import Any


def safe_classes(
    element: Any,
    *,
    add: str | None = None,
    remove: str | None = None,
    toggle: str | None = None,
    replace: str | None = None,
) -> None:
    """Call NiceGUI element.classes with type validation.

    Prevents common misuse like replace=True which causes runtime errors.
    """
    if isinstance(replace, bool):
        raise TypeError("classes(replace=...) expects a string, not bool")
    if isinstance(add, bool) or isinstance(remove, bool) or isinstance(toggle, bool):
        raise TypeError("classes(add/remove/toggle=...) expects strings, not bools")
    element.classes(add=add, remove=remove, toggle=toggle, replace=replace)
