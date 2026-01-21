"""Notification routing with priority-based delivery and quiet mode support."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from nicegui import ui
from nicegui.client import Client

if TYPE_CHECKING:
    from apps.web_console_ng.core.state_manager import UserStateManager

logger = logging.getLogger(__name__)


class NotificationPriority(Enum):
    """Notification priority levels."""

    HIGH = "high"  # Toast + drawer + badge (Risk Reject, Circuit Breaker, Errors)
    MEDIUM = "medium"  # Drawer + badge only (Order Filled, Position Changed)
    LOW = "low"  # Drawer only (Slice Filled, Heartbeat, Data Updates)


class NotificationType(Enum):
    """Notification display types (maps to ui.notify types)."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    WARNING = "warning"
    INFO = "info"


@dataclass
class Notification:
    """Single notification entry."""

    id: str
    message: str
    priority: NotificationPriority
    type: NotificationType
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    read: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "message": self.message,
            "priority": self.priority.value,
            "type": self.type.value,
            "timestamp": self.timestamp.isoformat(),
            "read": self.read,
            "metadata": self.metadata,
        }


class NotificationRouter:
    """Routes notifications based on priority and quiet mode state.

    Architecture:
    - Single instance per client session (not global)
    - Maintains notification history in bounded deque
    - Callbacks for drawer/badge updates
    - Quiet mode persisted via UserStateManager.save_preferences() (async)
    - Captures client context to ensure ui.notify targets correct client
    """

    MAX_HISTORY = 100
    PREF_KEY = "notification_quiet_mode"

    def __init__(self, state_manager: UserStateManager | None = None) -> None:
        self._history: deque[Notification] = deque(maxlen=self.MAX_HISTORY)
        self._state_manager = state_manager
        self._unread_count: int = 0
        self._notification_counter: int = 0
        self._quiet_mode: bool = False
        self._client: Client | None = None

        try:
            from nicegui import context

            self._client = context.client
        except (ImportError, RuntimeError):
            # _client remains None
            logger.warning(
                "notification_router_no_client_context",
                extra={
                    "note": "Router created outside client context; toasts will be skipped"
                },
            )

        self._on_notification: Callable[[Notification], None] | None = None
        self._on_badge_update: Callable[[int], None] | None = None
        self._on_quiet_mode_change: Callable[[bool], None] | None = None

    async def load_preferences(self) -> None:
        """Load quiet mode from persisted state (call before rendering UI)."""
        if self._state_manager:
            state = await self._state_manager.restore_state()
            preferences = state.get("preferences", {})
            saved_quiet_mode = preferences.get(self.PREF_KEY)
            if saved_quiet_mode is not None:
                self._quiet_mode = bool(saved_quiet_mode)
                logger.debug(
                    "notification_quiet_mode_loaded",
                    extra={"quiet_mode": self._quiet_mode},
                )

    def set_callbacks(
        self,
        on_notification: Callable[[Notification], None] | None = None,
        on_badge_update: Callable[[int], None] | None = None,
        on_quiet_mode_change: Callable[[bool], None] | None = None,
    ) -> None:
        """Register callbacks for notification events."""
        self._on_notification = on_notification
        self._on_badge_update = on_badge_update
        self._on_quiet_mode_change = on_quiet_mode_change

    @property
    def quiet_mode(self) -> bool:
        """Get current quiet mode state."""
        return self._quiet_mode

    async def set_quiet_mode(self, enabled: bool) -> None:
        """Set quiet mode state and persist to UserStateManager (async)."""
        if self._quiet_mode != enabled:
            self._quiet_mode = enabled
            if self._state_manager:
                await self._state_manager.save_preferences(self.PREF_KEY, enabled)
            logger.info(
                "notification_quiet_mode_changed",
                extra={"quiet_mode": enabled},
            )
            if self._on_quiet_mode_change:
                self._run_in_client_context(
                    self._on_quiet_mode_change,
                    enabled,
                    callback_name="on_quiet_mode_change",
                )

    @property
    def unread_count(self) -> int:
        """Get current unread notification count."""
        return self._unread_count

    def emit(
        self,
        message: str,
        priority: NotificationPriority = NotificationPriority.MEDIUM,
        notification_type: NotificationType = NotificationType.INFO,
        metadata: dict[str, Any] | None = None,
    ) -> Notification:
        """Emit a notification with priority-based routing."""
        self._notification_counter += 1
        notification = Notification(
            id=f"notif-{self._notification_counter}",
            message=message,
            priority=priority,
            type=notification_type,
            metadata=metadata or {},
        )

        self._history.appendleft(notification)

        should_toast = self._should_show_toast(priority)
        should_badge = self._should_show_badge(priority)

        if should_toast:
            self._show_toast(notification)

        if should_badge:
            self._unread_count += 1
            if self._on_badge_update:
                self._run_in_client_context(
                    self._on_badge_update,
                    self._unread_count,
                    callback_name="on_badge_update",
                )

        if self._on_notification:
            self._run_in_client_context(
                self._on_notification,
                notification,
                callback_name="on_notification",
            )

        logger.debug(
            "notification_emitted",
            extra={
                "notification_id": notification.id,
                "priority": priority.value,
                "type": notification_type.value,
                "toast_shown": should_toast,
                "badge_incremented": should_badge,
                "quiet_mode": self._quiet_mode,
            },
        )

        return notification

    def _should_show_toast(self, priority: NotificationPriority) -> bool:
        if priority == NotificationPriority.HIGH:
            return True
        if priority == NotificationPriority.MEDIUM:
            return not self._quiet_mode
        return False

    def _should_show_badge(self, priority: NotificationPriority) -> bool:
        return priority in (NotificationPriority.HIGH, NotificationPriority.MEDIUM)

    def _show_toast(self, notification: Notification) -> None:
        """Display toast notification via NiceGUI (client-context safe)."""
        if self._client is None:
            logger.debug(
                "notification_toast_skipped_no_context",
                extra={"notification_id": notification.id},
            )
            return

        try:
            with self._client:
                ui.notify(
                    notification.message,
                    type=notification.type.value,
                    position="top-right",
                    timeout=5000,
                )
        except Exception:
            logger.exception(
                "notification_toast_failed",
                extra={"notification_id": notification.id},
            )

    def _run_in_client_context(
        self,
        callback: Callable[..., Any],
        *args: Any,
        callback_name: str,
    ) -> None:
        """Run a callback within the captured NiceGUI client context.

        Handles both sync and async callbacks. For async callbacks, the coroutine
        is scheduled in the event loop to ensure it runs to completion.
        """
        try:
            if self._client is None:
                result = callback(*args)
                # Handle async callbacks when no client context
                if inspect.iscoroutine(result):
                    asyncio.create_task(result)
                return
            with self._client:
                result = callback(*args)
                # Handle async callbacks within client context
                if inspect.iscoroutine(result):
                    asyncio.create_task(result)
        except Exception:
            logger.exception(
                "notification_callback_failed",
                extra={"callback": callback_name},
            )

    def mark_all_read(self) -> None:
        """Mark all notifications as read and reset badge count."""
        for notif in self._history:
            notif.read = True
        self._unread_count = 0
        if self._on_badge_update:
            self._run_in_client_context(
                self._on_badge_update,
                0,
                callback_name="on_badge_update",
            )

    def get_history(self, limit: int | None = None) -> list[Notification]:
        """Get notification history (most recent first)."""
        if limit is None:
            return list(self._history)
        return list(self._history)[:limit]

    def clear_history(self) -> None:
        """Clear all notification history."""
        self._history.clear()
        self._unread_count = 0
        if self._on_badge_update:
            self._run_in_client_context(
                self._on_badge_update,
                0,
                callback_name="on_badge_update",
            )


def notify_high(
    router: NotificationRouter,
    message: str,
    notification_type: NotificationType = NotificationType.NEGATIVE,
    **metadata: Any,
) -> Notification:
    """Emit high-priority notification (always toasts)."""
    return router.emit(message, NotificationPriority.HIGH, notification_type, metadata)


def notify_medium(
    router: NotificationRouter,
    message: str,
    notification_type: NotificationType = NotificationType.INFO,
    **metadata: Any,
) -> Notification:
    """Emit medium-priority notification."""
    return router.emit(message, NotificationPriority.MEDIUM, notification_type, metadata)


def notify_low(
    router: NotificationRouter,
    message: str,
    notification_type: NotificationType = NotificationType.INFO,
    **metadata: Any,
) -> Notification:
    """Emit low-priority notification (drawer only)."""
    return router.emit(message, NotificationPriority.LOW, notification_type, metadata)


__all__ = [
    "Notification",
    "NotificationPriority",
    "NotificationRouter",
    "NotificationType",
    "notify_high",
    "notify_low",
    "notify_medium",
]
