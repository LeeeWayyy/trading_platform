"""Action button with state feedback for trading operations."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from enum import Enum
from typing import Any

from nicegui import ui

logger = logging.getLogger(__name__)


class ButtonState(Enum):
    """Button feedback states."""

    DEFAULT = "default"
    SENDING = "sending"
    CONFIRMING = "confirming"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"


STATE_CONFIG: dict[ButtonState, dict[str, Any]] = {
    ButtonState.DEFAULT: {
        "icon": None,
        "text_suffix": "",
        "classes": "",
        "spinning": False,
    },
    ButtonState.SENDING: {
        "icon": "sync",
        "text_suffix": "Sending...",
        "classes": "animate-button-pulse",
        "spinning": True,
    },
    ButtonState.CONFIRMING: {
        "icon": "hourglass_top",
        "text_suffix": "Confirming...",
        "classes": "animate-button-pulse",
        "spinning": True,
    },
    ButtonState.SUCCESS: {
        "icon": "check_circle",
        "text_suffix": "",
        "classes": "bg-green-600 animate-success-flash",
        "spinning": False,
    },
    ButtonState.FAILED: {
        "icon": "error",
        "text_suffix": "",
        "classes": "bg-red-600 animate-failed-shake",
        "spinning": False,
    },
    ButtonState.TIMEOUT: {
        "icon": "schedule",
        "text_suffix": "Taking longer than expected...",
        "classes": "bg-yellow-600 animate-button-pulse",
        "spinning": True,
    },
}


class ActionButton:
    """Button with state feedback for async operations."""

    TIMEOUT_THRESHOLD_MS = 5000
    SUCCESS_DISPLAY_MS = 2000
    # All CSS classes used for state styling (for removal when switching states)
    _ALL_STATE_CLASSES = (
        "animate-button-pulse animate-success-flash animate-failed-shake "
        "bg-green-600 bg-red-600 bg-yellow-600"
    )

    def __init__(
        self,
        label: str,
        on_click: Callable[[], Coroutine[Any, Any, bool | None]],
        icon: str | None = None,
        color: str = "primary",
        manual_lifecycle: bool = False,
    ) -> None:
        """Initialize ActionButton.

        Args:
            label: Button text.
            on_click: Async callback; return True=SUCCESS, False=FAILED, None=external control.
            icon: Optional icon name.
            color: Button color.
            manual_lifecycle: If True, ignore return value and defer to set_external_state()
                for all state transitions after SENDING.
        """
        self._label = label
        self._on_click = on_click
        self._icon = icon
        self._color = color
        self._manual_lifecycle = manual_lifecycle
        self._state = ButtonState.DEFAULT

        self._button: ui.button | None = None
        self._icon_element: ui.icon | None = None
        self._label_element: ui.label | None = None
        self._timeout_task: asyncio.Task[None] | None = None

    def create(self) -> ui.button:
        """Create and return the button element."""
        self._button = ui.button(on_click=self._handle_click).props(f"color={self._color}")
        with self._button:
            with ui.row().classes("items-center gap-2"):
                placeholder_icon = self._icon or "sync"
                self._icon_element = ui.icon(placeholder_icon)
                if not self._icon:
                    self._icon_element.classes("hidden")
                self._label_element = ui.label(self._label)

        return self._button

    @property
    def state(self) -> ButtonState:
        """Get current button state."""
        return self._state

    def _set_state(self, state: ButtonState) -> None:
        """Update button visual state."""
        self._state = state
        config = STATE_CONFIG[state]

        if self._button is None:
            return

        target_icon = config["icon"] if config["icon"] else self._icon
        if self._icon_element:
            if target_icon:
                self._icon_element.props(f"name={target_icon}")
                self._icon_element.classes(remove="hidden")
                if config["spinning"]:
                    self._icon_element.classes("animate-spin")
                else:
                    self._icon_element.classes(remove="animate-spin")
            else:
                self._icon_element.classes("hidden", remove="animate-spin")

        if self._label_element:
            if config["text_suffix"]:
                self._label_element.set_text(config["text_suffix"])
            else:
                self._label_element.set_text(self._label)

        self._button.classes(remove=self._ALL_STATE_CLASSES)
        if config["classes"]:
            self._button.classes(add=config["classes"])

        if state in (ButtonState.SENDING, ButtonState.CONFIRMING, ButtonState.TIMEOUT):
            self._button.disable()
        else:
            self._button.enable()

    async def _handle_click(self) -> None:
        """Handle button click with state management."""
        # Allow clicks from DEFAULT or FAILED states only (enables retry after failure).
        # TIMEOUT is excluded because the original operation is still running - allowing
        # a new click could cause concurrent operations which is dangerous for trading.
        if self._state not in (ButtonState.DEFAULT, ButtonState.FAILED):
            return

        self._set_state(ButtonState.SENDING)
        self._timeout_task = asyncio.create_task(self._watch_timeout())

        result: bool | None = None
        try:
            result = await self._on_click()
        except asyncio.CancelledError:
            self._set_state(ButtonState.DEFAULT)
            return
        except Exception as exc:
            logger.error(
                "action_button_error",
                extra={"label": self._label, "error": str(exc)},
            )
            self._set_state(ButtonState.FAILED)
            return
        finally:
            if self._timeout_task:
                self._timeout_task.cancel()
                self._timeout_task = None

        if self._manual_lifecycle:
            return

        if result is True:
            self._set_state(ButtonState.SUCCESS)
            # Use non-blocking task for consistent behavior with set_external_state
            asyncio.create_task(self._auto_reset_after_success())
        elif result is False:
            self._set_state(ButtonState.FAILED)
        else:
            logger.warning(
                "action_button_unexpected_return",
                extra={"label": self._label, "result": result},
            )
            self._set_state(ButtonState.DEFAULT)

    async def _watch_timeout(self) -> None:
        """Watch for timeout and update state."""
        try:
            await asyncio.sleep(self.TIMEOUT_THRESHOLD_MS / 1000)
        except asyncio.CancelledError:
            return
        if self._state == ButtonState.SENDING:
            self._set_state(ButtonState.TIMEOUT)

    def reset(self) -> None:
        """Reset button to default state (for dismiss after failure)."""
        if self._timeout_task:
            self._timeout_task.cancel()
            self._timeout_task = None
        self._set_state(ButtonState.DEFAULT)

    async def trigger(self) -> None:
        """Programmatically trigger the button click."""
        await self._handle_click()

    def set_external_state(self, state: ButtonState) -> None:
        """Set state from external events (e.g., order lifecycle updates)."""
        if self._timeout_task:
            self._timeout_task.cancel()
            self._timeout_task = None
        self._set_state(state)

        if state == ButtonState.SUCCESS:
            asyncio.create_task(self._auto_reset_after_success())

    async def _auto_reset_after_success(self) -> None:
        """Auto-reset to default after success display period."""
        await asyncio.sleep(self.SUCCESS_DISPLAY_MS / 1000)
        if self._state == ButtonState.SUCCESS:
            self._set_state(ButtonState.DEFAULT)


__all__ = ["ActionButton", "ButtonState", "STATE_CONFIG"]
