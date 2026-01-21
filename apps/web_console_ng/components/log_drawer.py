"""Log drawer component for notification history display."""

from __future__ import annotations

from nicegui import ui

from apps.web_console_ng.core.notification_router import (
    Notification,
    NotificationPriority,
    NotificationRouter,
    NotificationType,
)

PRIORITY_COLORS = {
    NotificationPriority.HIGH: "border-l-4 border-red-500 bg-red-900/20",
    NotificationPriority.MEDIUM: "border-l-4 border-yellow-500 bg-yellow-900/20",
    NotificationPriority.LOW: "border-l-4 border-gray-500 bg-gray-800/20",
}

TYPE_ICONS = {
    NotificationType.POSITIVE: "check_circle",
    NotificationType.NEGATIVE: "error",
    NotificationType.WARNING: "warning",
    NotificationType.INFO: "info",
}

TYPE_ICON_COLORS = {
    NotificationType.POSITIVE: "text-green-400",
    NotificationType.NEGATIVE: "text-red-400",
    NotificationType.WARNING: "text-yellow-400",
    NotificationType.INFO: "text-blue-400",
}


class LogDrawer:
    """Right-side log drawer for notification history."""

    def __init__(self, router: NotificationRouter) -> None:
        self._router = router
        self._drawer: ui.right_drawer | None = None
        self._items_container: ui.column | None = None
        self._badge: ui.badge | None = None
        self._toggle_button: ui.button | None = None

        self._router.set_callbacks(
            on_notification=self._on_notification,
            on_badge_update=self._on_badge_update,
        )

    def create(self) -> tuple[ui.button, ui.right_drawer]:
        """Create and return the drawer toggle button and drawer."""
        with ui.button(icon="notifications", on_click=self._toggle_drawer).props(
            "flat color=white"
        ) as btn:
            self._toggle_button = btn
            self._badge = ui.badge("0").props("floating color=red").classes("text-xs")
            self._badge.set_visibility(False)
        self._toggle_button.tooltip("Notification Log")

        self._drawer = ui.right_drawer(value=False).classes("bg-surface-1 w-80")
        with self._drawer:
            with ui.column().classes("w-full h-full"):
                with ui.row().classes(
                    "w-full items-center justify-between p-3 border-b border-gray-700"
                ):
                    ui.label("Notifications").classes("text-lg font-semibold text-white")
                    with ui.row().classes("gap-1"):
                        ui.button(icon="done_all", on_click=self._mark_all_read).props(
                            "flat dense size=sm"
                        ).tooltip("Mark all read")
                        ui.button(icon="delete_sweep", on_click=self._clear_all).props(
                            "flat dense size=sm"
                        ).tooltip("Clear all")

                with ui.scroll_area().classes("flex-1 w-full"):
                    self._items_container = ui.column().classes("w-full gap-1 p-2")
                    for notif in self._router.get_history():
                        self._render_notification(notif)

        return self._toggle_button, self._drawer

    def _toggle_drawer(self) -> None:
        """Toggle drawer visibility and mark read on open."""
        if self._drawer:
            if not self._drawer.value:
                self._router.mark_all_read()
            self._drawer.toggle()

    def _on_notification(self, notification: Notification) -> None:
        """Callback when new notification is emitted."""
        if self._items_container:
            with self._items_container:
                row = self._render_notification(notification, highlight=True)
                if row:
                    row.move(target_index=0)

    def _on_badge_update(self, count: int) -> None:
        """Callback when badge count changes."""
        if self._badge:
            self._badge.set_text(str(count) if count <= 99 else "99+")
            self._badge.set_visibility(count > 0)

    def _render_notification(
        self, notification: Notification, highlight: bool = False
    ) -> ui.row | None:
        """Render a single notification entry."""
        priority_class = PRIORITY_COLORS.get(
            notification.priority, PRIORITY_COLORS[NotificationPriority.LOW]
        )
        icon = TYPE_ICONS.get(notification.type, "info")
        icon_color = TYPE_ICON_COLORS.get(notification.type, "text-gray-400")

        row_classes = f"w-full p-2 rounded {priority_class}"
        if highlight:
            row_classes += " animate-[fadeHighlight_2s_ease-out_forwards]"

        with ui.row().classes(row_classes) as row:
            ui.icon(icon).classes(f"{icon_color} text-lg")
            with ui.column().classes("flex-1 gap-0"):
                ui.label(notification.message).classes("text-sm text-white")
                time_str = notification.timestamp.strftime("%H:%M:%S")
                ui.label(time_str).classes("text-xs text-gray-500")

        return row

    def _mark_all_read(self) -> None:
        """Mark all notifications as read."""
        self._router.mark_all_read()

    def _clear_all(self) -> None:
        """Clear all notifications."""
        self._router.clear_history()
        if self._items_container:
            self._items_container.clear()


__all__ = ["LogDrawer"]
