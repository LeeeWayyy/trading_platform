"""Keyboard hotkey management for trading workflows."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class HotkeyScope(Enum):
    """Hotkey activation scope."""

    GLOBAL = "global"
    ORDER_FORM = "order_form"
    GRID = "grid"


@dataclass
class HotkeyBinding:
    """Single hotkey binding configuration."""

    key: str
    action: str
    description: str
    scope: HotkeyScope
    modifiers: list[str] = field(default_factory=list)
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JS serialization."""
        return {
            "key": self.key,
            "action": self.action,
            "description": self.description,
            "scope": self.scope.value,
            "modifiers": self.modifiers,
            "enabled": self.enabled,
        }

    def matches(self, key: str, modifiers: list[str]) -> bool:
        """Check if key event matches this binding."""
        if not self.enabled:
            return False
        if self.key.lower() != key.lower():
            return False
        return set(self.modifiers) == set(modifiers)


DEFAULT_HOTKEYS: list[HotkeyBinding] = [
    HotkeyBinding(
        key="b",
        action="focus_buy",
        description="Focus buy quantity",
        scope=HotkeyScope.ORDER_FORM,
    ),
    HotkeyBinding(
        key="s",
        action="focus_sell",
        description="Focus sell quantity",
        scope=HotkeyScope.ORDER_FORM,
    ),
    HotkeyBinding(
        key="Enter",
        action="submit_order",
        description="Submit order",
        scope=HotkeyScope.ORDER_FORM,
    ),
    HotkeyBinding(
        key="Escape",
        action="cancel_form",
        description="Cancel/clear form",
        scope=HotkeyScope.ORDER_FORM,
    ),
    HotkeyBinding(
        key="/",
        action="open_palette",
        description="Open command palette",
        scope=HotkeyScope.GLOBAL,
    ),
    HotkeyBinding(
        key="k",
        action="open_palette",
        description="Open command palette",
        scope=HotkeyScope.GLOBAL,
        modifiers=["ctrl"],
    ),
    HotkeyBinding(
        key="F1",
        action="show_help",
        description="Show hotkey reference",
        scope=HotkeyScope.GLOBAL,
    ),
    HotkeyBinding(
        key="?",
        action="show_help",
        description="Show hotkey reference",
        scope=HotkeyScope.GLOBAL,
    ),
]


class HotkeyManager:
    """Manages keyboard hotkey bindings and dispatching."""

    def __init__(self) -> None:
        self._bindings: list[HotkeyBinding] = list(DEFAULT_HOTKEYS)
        self._action_handlers: dict[str, Callable[[], Any]] = {}

    def register_handler(self, action: str, handler: Callable[[], Any]) -> None:
        """Register a Python handler for an action."""
        self._action_handlers[action] = handler

    def get_bindings(self, scope: HotkeyScope | None = None) -> list[HotkeyBinding]:
        """Get all bindings, optionally filtered by scope."""
        if scope is None:
            return list(self._bindings)
        return [binding for binding in self._bindings if binding.scope == scope]

    def get_bindings_json(self) -> list[dict[str, Any]]:
        """Get bindings as JSON-serializable list for JS."""
        return [binding.to_dict() for binding in self._bindings if binding.enabled]

    def update_binding(self, action: str, key: str, modifiers: list[str] | None = None) -> bool:
        """Update key binding for an action."""
        for binding in self._bindings:
            if binding.action == action:
                binding.key = key
                binding.modifiers = modifiers or []
                logger.info(
                    "hotkey_binding_updated",
                    extra={"action": action, "key": key, "modifiers": modifiers},
                )
                return True
        return False

    def set_binding_enabled(self, action: str, enabled: bool) -> None:
        """Enable or disable a hotkey binding."""
        for binding in self._bindings:
            if binding.action == action:
                binding.enabled = enabled

    def handle_action(self, action: str) -> bool:
        """Handle an action triggered by hotkey."""
        handler = self._action_handlers.get(action)
        if handler:
            try:
                result = handler()
                import asyncio

                if asyncio.iscoroutine(result):
                    task = asyncio.create_task(result)

                    def _log_task_exception(task: asyncio.Task[Any]) -> None:
                        try:
                            exc = task.exception()
                        except asyncio.CancelledError:
                            return
                        if exc is not None:
                            logger.error(
                                "hotkey_action_task_failed",
                                extra={"action": action, "error": str(exc)},
                            )

                    task.add_done_callback(_log_task_exception)
                return True
            except Exception as exc:
                logger.error(
                    "hotkey_action_failed",
                    extra={"action": action, "error": str(exc)},
                )
        return False


__all__ = ["DEFAULT_HOTKEYS", "HotkeyBinding", "HotkeyManager", "HotkeyScope"]
