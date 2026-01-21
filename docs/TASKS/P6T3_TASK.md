---
id: P6T3
title: "Professional Trading Terminal - Notifications & Hotkeys"
phase: P6
task: T3
priority: P0
owner: "@development-team"
state: TASK
created: 2026-01-13
dependencies: [P6T1]
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P6_PLANNING.md]
features: [T3.1-T3.4]
---

# P6T3: Notifications & Hotkeys

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** TASK (Planning Complete)
**Priority:** P0 (UX Foundation)
**Owner:** @development-team
**Created:** 2026-01-13
**Track:** Track 3 of 18
**Dependency:** P6T1 (Core Infrastructure) must be complete

---

## Objective

Implement notification management with quiet mode and keyboard hotkeys for professional trading workflows.

**Success looks like:**
- Notification center prevents toast spam during algo execution
- Keyboard hotkeys enable rapid trading (B/S/Enter/Escape)
- State feedback on all trading buttons
- Cell flash updates for price changes

---

## Pre-Implementation Analysis Summary

### Existing Infrastructure (from codebase analysis)

**Notification System (current state):**
- Uses `ui.notify(message, type="positive|negative|warning")` directly throughout codebase
- No queue, no deduplication, no priority routing
- Toast spam during rapid fills/updates is a known issue
- `ActivityFeed` component (`components/activity_feed.py`) provides a model for log rendering with incremental updates

**Event Handling Patterns:**
- Custom events via `window.dispatchEvent('trading_state_change', {detail: {...}})`
- `trading_state_listener.js` handles global state changes
- Redis Pub/Sub for real-time updates with throttling (10/sec, 100-item queue)
- `RealtimeUpdater` class manages subscriptions with backpressure

**State Management:**
- `UserStateManager` with Redis-backed storage (24h TTL) for preferences
- `app.storage.user` for session-level ephemeral state
- `ClientLifecycleManager` for cleanup callbacks on disconnect

**Layout (`ui/layout.py` - 600 lines):**
- Left drawer for navigation
- Header with kill switch, circuit breaker, connection badges
- 5-second polling loop for status updates
- Theme colors defined in `ui/theme.py` and `static/css/custom.css`

**Grid Components:**
- `positions_grid.py`: Uses `applyTransaction()` with delta updates
- `orders_table.py`: Uses `applyTransaction()` with delta updates
- `GridThrottle.js`: Tracks update rates, toggles degradation mode per-grid

---

## Tasks (5 total)

### T3.0: Architectural Decision Record (ADR) - REQUIRED

**Goal:** Document the architectural decisions for the new Notification Router and Hotkey Management subsystems.

- **Deliverable:** `docs/ADRs/ADR-0032-notification-and-hotkey-system.md`
- **Scope:**
  - Notification priority routing logic and quiet mode persistence.
  - Hotkey context management (global vs scoped) and conflict resolution strategy.
  - Client-side event handling pattern (backpressure, throttling integration).

### T3.1: Notification Center / Quiet Mode - HIGH PRIORITY

**Goal:** Prevent toast notification spam during algo execution via priority-based routing.

#### Notification Priority Routing

**Quiet Mode Behavior:**
- **Quiet Mode OFF (default):** HIGH and MEDIUM show toasts; LOW goes to drawer only
- **Quiet Mode ON:** Only HIGH shows toasts; MEDIUM/LOW go to drawer only

| Priority | Quiet Mode OFF | Quiet Mode ON | Examples |
|----------|----------------|---------------|----------|
| HIGH | Toast + Drawer + Badge | Toast + Drawer + Badge | Risk Reject, Circuit Breaker Trip, Errors, Kill Switch |
| MEDIUM | Toast + Drawer + Badge | Drawer + Badge (no toast) | Order Filled, Position Changed, Large P&L Move |
| LOW | Drawer only | Drawer only | Slice Filled, Heartbeat, Data Updates, Price Updates |

#### Current vs Target State

| Component | Current | Target |
|-----------|---------|--------|
| Notification routing | Direct `ui.notify()` | `NotificationRouter.emit()` with priority |
| Toast spam | Unbounded | HIGH priority only in quiet mode |
| History | None | Log drawer with 100-item scrollable history |
| Badge | None | Unread count in header |
| Quiet mode | None | Toggle in header (persisted in Redis) |

#### Implementation Details

**1. Notification Router Module**

Create `apps/web_console_ng/core/notification_router.py`:

```python
"""Notification routing with priority-based delivery and quiet mode support.

Priority Levels:
- HIGH: Always show toast + add to drawer + increment badge (critical alerts)
- MEDIUM: Toast when quiet mode OFF; drawer + badge only when quiet mode ON
- LOW: Add to drawer only (no toast, no badge)

Quiet Mode Behavior:
- OFF (default): HIGH and MEDIUM show toasts; useful during normal trading
- ON: Only HIGH shows toasts; enables focus during algo execution
- Persisted via UserStateManager.save_preferences() for cross-session preference

Integration with UserStateManager:
- Constructor accepts optional UserStateManager instance
- On init, call await load_preferences() to restore quiet mode from persisted state
- On toggle, call await save_quiet_mode() to persist via UserStateManager.save_preferences()
- All persistence is async (UserStateManager is async-only)
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable

from nicegui import ui

if TYPE_CHECKING:
    from apps.web_console_ng.core.state_manager import UserStateManager

logger = logging.getLogger(__name__)


class NotificationPriority(Enum):
    """Notification priority levels."""
    HIGH = "high"      # Toast + drawer + badge (Risk Reject, Circuit Breaker, Errors)
    MEDIUM = "medium"  # Drawer + badge only (Order Filled, Position Changed)
    LOW = "low"        # Drawer only (Slice Filled, Heartbeat, Data Updates)


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
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
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
    - CRITICAL: Captures client context to ensure ui.notify targets correct client

    Usage:
        # In layout initialization (async context)
        state_manager = UserStateManager(user_id=user.id)
        notification_router = NotificationRouter(state_manager)

        # IMPORTANT: Load preferences before rendering UI
        await notification_router.load_preferences()

        # Now quiet mode is restored from Redis

    Thread Safety:
        Notifications may be emitted from background tasks (Redis listeners, etc.).
        The router captures the client context at initialization and uses it to
        ensure ui.notify() targets the correct user session.
    """

    MAX_HISTORY = 100
    PREF_KEY = "notification_quiet_mode"

    def __init__(self, state_manager: "UserStateManager | None" = None) -> None:
        self._history: deque[Notification] = deque(maxlen=self.MAX_HISTORY)
        self._state_manager = state_manager
        self._unread_count: int = 0
        self._notification_counter: int = 0

        # Quiet mode starts as False; call load_preferences() to restore from Redis
        self._quiet_mode: bool = False

        # CRITICAL: Capture client context for thread-safe notification emission
        # This allows background tasks to emit notifications to the correct client
        try:
            from nicegui import context
            self._client = context.client
        except (ImportError, RuntimeError):
            # No client context available (e.g., testing or initialization outside request)
            self._client = None
            logger.warning(
                "notification_router_no_client_context",
                extra={"note": "Router created outside client context; toasts will be skipped"},
            )

        # Callbacks for UI updates
        self._on_notification: Callable[[Notification], None] | None = None
        self._on_badge_update: Callable[[int], None] | None = None
        self._on_quiet_mode_change: Callable[[bool], None] | None = None

    async def load_preferences(self) -> None:
        """Load quiet mode from persisted state (call before rendering UI).

        Must be called in async context during layout initialization.
        """
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
        """Set quiet mode state and persist to UserStateManager (async).

        Must be awaited; uses UserStateManager.save_preferences() for persistence.
        """
        if self._quiet_mode != enabled:
            self._quiet_mode = enabled
            # Persist via UserStateManager.save_preferences() (async)
            if self._state_manager:
                await self._state_manager.save_preferences(self.PREF_KEY, enabled)
            logger.info(
                "notification_quiet_mode_changed",
                extra={"quiet_mode": enabled},
            )
            if self._on_quiet_mode_change:
                self._on_quiet_mode_change(enabled)

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
        """Emit a notification with priority-based routing.

        Args:
            message: Notification text
            priority: Routing priority (HIGH/MEDIUM/LOW)
            notification_type: Display type (positive/negative/warning/info)
            metadata: Optional additional data (symbol, order_id, etc.)

        Returns:
            The created Notification object
        """
        self._notification_counter += 1
        notification = Notification(
            id=f"notif-{self._notification_counter}",
            message=message,
            priority=priority,
            type=notification_type,
            metadata=metadata or {},
        )

        # Add to history
        self._history.appendleft(notification)

        # Route based on priority
        should_toast = self._should_show_toast(priority)
        should_badge = self._should_show_badge(priority)

        if should_toast:
            self._show_toast(notification)

        if should_badge:
            self._unread_count += 1
            if self._on_badge_update:
                self._on_badge_update(self._unread_count)

        # Always notify drawer callback
        if self._on_notification:
            self._on_notification(notification)

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
        """Determine if toast should be shown based on priority and quiet mode."""
        # HIGH priority always shows toast (even in quiet mode)
        if priority == NotificationPriority.HIGH:
            return True
        # MEDIUM shows toast only when quiet mode is OFF
        if priority == NotificationPriority.MEDIUM:
            return not self._quiet_mode
        # LOW never shows toast
        return False

    def _should_show_badge(self, priority: NotificationPriority) -> bool:
        """Determine if badge count should increment."""
        # HIGH and MEDIUM increment badge, LOW does not
        return priority in (NotificationPriority.HIGH, NotificationPriority.MEDIUM)

    def _show_toast(self, notification: Notification) -> None:
        """Display toast notification via NiceGUI.

        CRITICAL: Uses captured client context to ensure toast targets correct user.
        This allows background tasks to safely emit notifications.
        """
        if self._client is None:
            # No client context - skip toast (log to drawer only)
            logger.debug(
                "notification_toast_skipped_no_context",
                extra={"notification_id": notification.id},
            )
            return

        # Use captured client context for thread-safe notification
        with self._client:
            ui.notify(
                notification.message,
                type=notification.type.value,
                position="top-right",
                timeout=5000,
            )

    def mark_all_read(self) -> None:
        """Mark all notifications as read and reset badge count."""
        for notif in self._history:
            notif.read = True
        self._unread_count = 0
        if self._on_badge_update:
            self._on_badge_update(0)

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
            self._on_badge_update(0)


# Convenience functions for common notification types
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
    """Emit medium-priority notification.

    Behavior depends on quiet mode:
    - Quiet mode OFF: Toast + Drawer + Badge
    - Quiet mode ON: Drawer + Badge only (no toast)
    """
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
```

**2. Log Drawer Component**

Create `apps/web_console_ng/components/log_drawer.py`:

```python
"""Log drawer component for notification history display.

Features:
- Right-side drawer toggle from header
- Scrollable notification history (100 items max)
- Priority-colored entries (HIGH=red, MEDIUM=yellow, LOW=gray)
- Clear all button
- Mark as read on open
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nicegui import ui

from apps.web_console_ng.core.notification_router import (
    Notification,
    NotificationPriority,
    NotificationRouter,
    NotificationType,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# Priority to color mapping
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

        # Register router callbacks
        self._router.set_callbacks(
            on_notification=self._on_notification,
            on_badge_update=self._on_badge_update,
        )

    def create(self) -> tuple[ui.button, ui.right_drawer]:
        """Create and return the drawer toggle button and drawer.

        Returns:
            Tuple of (toggle_button, drawer) for placement in layout.
        """
        # Create toggle button with badge
        with ui.button(icon="notifications", on_click=self._toggle_drawer).props(
            "flat color=white"
        ) as btn:
            self._toggle_button = btn
            self._badge = (
                ui.badge("0")
                .props("floating color=red")
                .classes("text-xs")
            )
            self._badge.set_visibility(False)
        self._toggle_button.tooltip("Notification Log")

        # Create right drawer
        self._drawer = ui.right_drawer(value=False).classes(
            "bg-surface-1 w-80"
        )
        with self._drawer:
            with ui.column().classes("w-full h-full"):
                # Header
                with ui.row().classes("w-full items-center justify-between p-3 border-b border-gray-700"):
                    ui.label("Notifications").classes("text-lg font-semibold text-white")
                    with ui.row().classes("gap-1"):
                        ui.button(icon="done_all", on_click=self._mark_all_read).props(
                            "flat dense size=sm"
                        ).tooltip("Mark all read")
                        ui.button(icon="delete_sweep", on_click=self._clear_all).props(
                            "flat dense size=sm"
                        ).tooltip("Clear all")

                # Scrollable items container
                with ui.scroll_area().classes("flex-1 w-full"):
                    self._items_container = ui.column().classes("w-full gap-1 p-2")
                    # Render existing history
                    for notif in self._router.get_history():
                        self._render_notification(notif)

        return self._toggle_button, self._drawer

    def _toggle_drawer(self) -> None:
        """Toggle drawer visibility and mark read on open."""
        if self._drawer:
            if not self._drawer.value:
                # Opening drawer - mark all as read
                self._router.mark_all_read()
            self._drawer.toggle()

    def _on_notification(self, notification: Notification) -> None:
        """Callback when new notification is emitted."""
        if self._items_container:
            with self._items_container:
                row = self._render_notification(notification, highlight=True)
                # Move to top
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
```

**3. Layout Integration**

Modify `apps/web_console_ng/ui/layout.py` to add:

```python
# Add imports at top:
from apps.web_console_ng.components.log_drawer import LogDrawer
from apps.web_console_ng.core.notification_router import NotificationRouter
from apps.web_console_ng.core.state_manager import UserStateManager

# In main_layout wrapper (which is an async function), after user extraction:
# Create state manager with correct constructor signature
state_manager = UserStateManager(user_id=user.id, role=user.role, strategies=user.strategies)

# Create notification router
notification_router = NotificationRouter(state_manager=state_manager)

# CRITICAL: Load preferences BEFORE rendering UI to ensure correct initial state
# This restores quiet mode from Redis before header buttons are rendered
await notification_router.load_preferences()

app.storage.user["notification_router"] = notification_router  # Store for access by pages

# After market_clock in header, before ui.space():
# Define toggle function BEFORE button creation to avoid NameError
async def toggle_quiet_mode():
    """Toggle quiet mode (async - persists to Redis)."""
    enabled = not notification_router.quiet_mode
    await notification_router.set_quiet_mode(enabled)  # Async persistence
    quiet_mode_btn.props(f"icon={'notifications_off' if enabled else 'notifications_active'}")
    quiet_mode_btn.tooltip("Quiet Mode: ON" if enabled else "Quiet Mode: OFF")

# Quiet mode toggle - initial icon based on restored quiet_mode state
initial_icon = "notifications_off" if notification_router.quiet_mode else "notifications_active"
initial_tooltip = "Quiet Mode: ON" if notification_router.quiet_mode else "Quiet Mode: OFF"

quiet_mode_btn = ui.button(icon=initial_icon, on_click=toggle_quiet_mode).props(
    "flat color=white"
).tooltip(initial_tooltip)

# Create log drawer (right side)
log_drawer = LogDrawer(notification_router)
log_toggle_btn, drawer = log_drawer.create()

# Add log_toggle_btn after connection_badge in header
```

**4. Theme Colors**

Add to `apps/web_console_ng/ui/theme.py`:

```python
# =============================================================================
# Notification Priority Colors
# =============================================================================

NOTIFICATION_HIGH = "bg-red-500 text-white"
NOTIFICATION_MEDIUM = "bg-yellow-500 text-black"
NOTIFICATION_LOW = "bg-gray-600 text-white"

NOTIFICATION_BADGE_REMOVE_CLASSES = "bg-red-500 bg-yellow-500 bg-gray-600 text-white text-black"
```

**Files to Create/Modify:**

| File | Action | Description |
|------|--------|-------------|
| `apps/web_console_ng/core/notification_router.py` | Create | Priority-based notification routing |
| `apps/web_console_ng/components/log_drawer.py` | Create | Right-side notification drawer |
| `apps/web_console_ng/ui/layout.py` | Modify | Add drawer toggle, quiet mode, wire router |
| `apps/web_console_ng/ui/theme.py` | Modify | Add notification priority colors |

**Acceptance Criteria:**
- [ ] Log drawer accessible from header (right side, togglable)
- [ ] LOW-priority events never show toasts (drawer only)
- [ ] HIGH-priority events always show toasts (even in quiet mode)
- [ ] MEDIUM-priority events show toasts when quiet mode OFF; no toast when quiet mode ON
- [ ] Badge shows unread count for HIGH/MEDIUM notifications (capped at 99+)
- [ ] Quiet mode toggle in header (persisted across sessions via UserStateManager/Redis)
- [ ] Notification history persists during session (100 items max, FIFO)
- [ ] Mark all read button works
- [ ] Clear all button works

---

### T3.2: Keyboard Hotkeys - HIGH PRIORITY

**Goal:** Enable keyboard-driven trading for rapid execution.

#### Hotkey Bindings

| Key | Action | Scope |
|-----|--------|-------|
| `b` | Focus buy quantity input | Order entry form |
| `s` | Focus sell quantity input | Order entry form |
| `Enter` | Submit order form | Order entry form |
| `Escape` | Cancel/clear form | Order entry form |
| `/` or `Ctrl+K` | Open command palette | Global |
| `F1` | Show hotkey reference | Global |
| `?` | Show hotkey reference (alt) | Global |

#### Implementation Details

**1. Hotkey Manager Module**

Create `apps/web_console_ng/core/hotkey_manager.py`:

```python
"""Keyboard hotkey management for trading workflows.

Features:
- Configurable key bindings
- Context-aware activation (form scope vs global)
- Conflict detection with browser/OS shortcuts
- Session-only configuration (bindings reset on page reload)

Note: Hotkey bindings are NOT persisted across sessions. This is intentional:
- Default bindings are sufficient for most users
- Prevents confusion from stale custom bindings
- Simplifies implementation and testing
- Future: If user demand arises, add persistence via UserStateManager
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class HotkeyScope(Enum):
    """Hotkey activation scope."""
    GLOBAL = "global"           # Active everywhere
    ORDER_FORM = "order_form"   # Active only when order form is focused
    GRID = "grid"               # Active only when grid is focused


@dataclass
class HotkeyBinding:
    """Single hotkey binding configuration."""
    key: str                      # Key code (e.g., "b", "Enter", "Escape")
    action: str                   # Action identifier
    description: str              # Human-readable description
    scope: HotkeyScope            # Where hotkey is active
    modifiers: list[str] = field(default_factory=list)  # ["ctrl", "shift", "alt"]
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JS."""
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
        # Case-insensitive key comparison
        if self.key.lower() != key.lower():
            return False
        # Check modifiers match exactly
        return set(self.modifiers) == set(modifiers)


# Default hotkey configuration
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
        key="?",  # Browser event.key is "?" when Shift+/ is pressed
        action="show_help",
        description="Show hotkey reference",
        scope=HotkeyScope.GLOBAL,
        # Note: No shift modifier needed - event.key already reports "?"
    ),
]


class HotkeyManager:
    """Manages keyboard hotkey bindings and dispatching.

    Note: Actual key capture happens in browser via hotkey_handler.js.
    This class manages configuration and provides bindings to JS.
    """

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
        return [b for b in self._bindings if b.scope == scope]

    def get_bindings_json(self) -> list[dict[str, Any]]:
        """Get bindings as JSON-serializable list for JS."""
        return [b.to_dict() for b in self._bindings if b.enabled]

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
        """Handle an action triggered by hotkey (called from JS via callback)."""
        handler = self._action_handlers.get(action)
        if handler:
            try:
                result = handler()
                # Handle async handlers
                import asyncio
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
                return True
            except Exception as exc:
                logger.error(
                    "hotkey_action_failed",
                    extra={"action": action, "error": str(exc)},
                )
        return False


__all__ = [
    "DEFAULT_HOTKEYS",
    "HotkeyBinding",
    "HotkeyManager",
    "HotkeyScope",
]
```

**2. Browser-Side Hotkey Handler**

Create `apps/web_console_ng/static/js/hotkey_handler.js`:

```javascript
/**
 * Keyboard hotkey handler for trading workflows.
 *
 * Features:
 * - Configurable bindings loaded from server
 * - Context-aware (respects input focus)
 * - Prevents conflicts with browser shortcuts
 * - Dispatches actions back to Python via NiceGUI
 */
window.HotkeyHandler = {
    bindings: [],
    enabled: true,
    currentScope: 'global',

    // Elements that should suppress hotkeys when focused
    INPUT_SELECTORS: 'input, textarea, select, [contenteditable="true"]',

    /**
     * Initialize hotkey handler with bindings from server.
     * @param {Array} bindings - Hotkey binding configurations
     */
    init(bindings) {
        this.bindings = bindings;
        this._registerListener();
        console.log('HotkeyHandler initialized with', bindings.length, 'bindings');
    },

    /**
     * Set current scope (e.g., when order form receives focus).
     * @param {string} scope - 'global', 'order_form', or 'grid'
     */
    setScope(scope) {
        this.currentScope = scope;
    },

    /**
     * Enable/disable all hotkeys.
     * @param {boolean} enabled
     */
    setEnabled(enabled) {
        this.enabled = enabled;
    },

    /**
     * Register keydown event listener.
     */
    _registerListener() {
        document.addEventListener('keydown', (event) => {
            if (!this.enabled) return;

            // Don't capture when typing in inputs (unless specific form hotkeys)
            const isInput = event.target.matches(this.INPUT_SELECTORS);

            // Get active modifiers
            // Note: For shifted punctuation (e.g., Shift+/ produces "?"), we omit 'shift'
            // from modifiers since the shifted character is already reflected in event.key.
            // This allows binding key="?" without modifiers to match Shift+/ correctly.
            const modifiers = [];
            if (event.ctrlKey || event.metaKey) modifiers.push('ctrl');
            if (event.shiftKey && !this._isShiftedPunctuation(event.key)) {
                modifiers.push('shift');
            }
            if (event.altKey) modifiers.push('alt');

            // Find matching binding
            const binding = this._findBinding(event.key, modifiers, isInput);

            if (binding) {
                // Prevent default browser behavior
                event.preventDefault();
                event.stopPropagation();

                // Dispatch action
                this._dispatchAction(binding.action);
            }
        });
    },

    /**
     * Check if key is a shifted punctuation character.
     * These characters are produced by Shift+number or Shift+punctuation keys.
     * We omit 'shift' from modifiers for these since event.key already reflects the shift.
     *
     * ASSUMPTION: US keyboard layout. Non-US keyboards may produce different event.key
     * values for shifted keys. If issues arise on international layouts, consider:
     * 1. Using event.code (physical key) instead of event.key (logical character)
     * 2. Providing a keyboard layout selector in settings
     * 3. Expanding shiftedPunctuation set based on user reports
     */
    _isShiftedPunctuation(key) {
        // Shifted characters that don't need explicit shift modifier (US layout)
        // Includes: ! @ # $ % ^ & * ( ) _ + { } | : " < > ?
        const shiftedPunctuation = '!@#$%^&*()_+{}|:"<>?~';
        return key.length === 1 && shiftedPunctuation.includes(key);
    },

    /**
     * Find matching hotkey binding.
     */
    _findBinding(key, modifiers, isInput) {
        for (const binding of this.bindings) {
            if (!binding.enabled) continue;

            // Check key match (case-insensitive)
            if (binding.key.toLowerCase() !== key.toLowerCase()) continue;

            // Check modifiers match exactly
            const bindingMods = new Set(binding.modifiers || []);
            const eventMods = new Set(modifiers);
            if (bindingMods.size !== eventMods.size) continue;
            let modsMatch = true;
            for (const mod of bindingMods) {
                if (!eventMods.has(mod)) {
                    modsMatch = false;
                    break;
                }
            }
            if (!modsMatch) continue;

            // Check scope
            // Global hotkeys with modifiers work even in inputs
            if (binding.scope === 'global' && modifiers.length > 0) {
                return binding;
            }

            // Global hotkeys without modifiers only work outside inputs
            if (binding.scope === 'global' && !isInput) {
                return binding;
            }

            // Order form hotkeys only work when in order form scope OR form has focus
            if (binding.scope === 'order_form') {
                if (this.currentScope === 'order_form' || this._isInOrderForm()) {
                    return binding;
                }
            }

            // Grid hotkeys only work in grid scope
            if (binding.scope === 'grid' && this.currentScope === 'grid') {
                return binding;
            }
        }
        return null;
    },

    /**
     * Check if currently focused element is within order form.
     */
    _isInOrderForm() {
        const activeEl = document.activeElement;
        if (!activeEl) return false;
        return activeEl.closest('[data-order-form]') !== null;
    },

    /**
     * Dispatch action to Python backend via custom event.
     */
    _dispatchAction(action) {
        console.log('Hotkey action:', action);
        window.dispatchEvent(new CustomEvent('hotkey_action', {
            detail: { action }
        }));
    },

    /**
     * Get formatted hotkey string for display.
     */
    formatHotkey(binding) {
        const parts = [];
        if (binding.modifiers) {
            for (const mod of binding.modifiers) {
                if (mod === 'ctrl') parts.push('Ctrl');
                else if (mod === 'shift') parts.push('Shift');
                else if (mod === 'alt') parts.push('Alt');
            }
        }
        // Format special keys
        let keyDisplay = binding.key;
        if (binding.key === 'Enter') keyDisplay = 'Enter';
        else if (binding.key === 'Escape') keyDisplay = 'Esc';
        else if (binding.key === ' ') keyDisplay = 'Space';
        else keyDisplay = binding.key.toUpperCase();

        parts.push(keyDisplay);
        return parts.join('+');
    }
};
```

**3. Command Palette Component**

Create `apps/web_console_ng/components/command_palette.py`:

```python
"""Command palette component for quick action access.

Features:
- Opens with / or Ctrl+K
- Searchable command list
- Hotkey hints displayed
- Recent commands at top
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from nicegui import ui

if TYPE_CHECKING:
    from apps.web_console_ng.core.hotkey_manager import HotkeyManager

logger = logging.getLogger(__name__)


class CommandPalette:
    """Command palette dialog for quick access to actions."""

    def __init__(self, hotkey_manager: "HotkeyManager") -> None:
        self._hotkey_manager = hotkey_manager
        self._dialog: ui.dialog | None = None
        self._search_input: ui.input | None = None
        self._commands_container: ui.column | None = None

        # Available commands (action -> (label, callback))
        self._commands: dict[str, tuple[str, Callable[[], None]]] = {}

    def register_command(
        self, action: str, label: str, callback: Callable[[], None]
    ) -> None:
        """Register a command for the palette."""
        self._commands[action] = (label, callback)

    def create(self) -> ui.dialog:
        """Create the command palette dialog."""
        self._dialog = ui.dialog().props("maximized position=top")

        with self._dialog:
            with ui.card().classes("w-full max-w-xl mx-auto mt-20 bg-surface-2"):
                # Search input
                self._search_input = (
                    ui.input(placeholder="Type a command...")
                    .props("autofocus outlined dense")
                    .classes("w-full")
                    .on("keydown.escape", self.close)
                    .on("input", self._on_search)
                )

                # Commands list
                with ui.scroll_area().classes("max-h-96"):
                    self._commands_container = ui.column().classes("w-full gap-0")
                    self._render_commands()

        return self._dialog

    def open(self) -> None:
        """Open the command palette."""
        if self._dialog:
            self._dialog.open()
            if self._search_input:
                self._search_input.value = ""
                self._render_commands()

    def close(self) -> None:
        """Close the command palette."""
        if self._dialog:
            self._dialog.close()

    def _on_search(self, event: Any) -> None:
        """Filter commands based on search input."""
        self._render_commands(filter_text=str(event.value or ""))

    def _render_commands(self, filter_text: str = "") -> None:
        """Render command list with optional filtering."""
        if not self._commands_container:
            return

        self._commands_container.clear()
        filter_lower = filter_text.lower()

        # Get hotkey bindings for display
        bindings_by_action = {
            b.action: b for b in self._hotkey_manager.get_bindings()
        }

        with self._commands_container:
            for action, (label, callback) in self._commands.items():
                # Filter
                if filter_lower and filter_lower not in label.lower():
                    continue

                # Get hotkey hint
                binding = bindings_by_action.get(action)
                hotkey_hint = ""
                if binding:
                    # Format hotkey for display
                    parts = []
                    for mod in binding.modifiers:
                        parts.append(mod.capitalize())
                    parts.append(binding.key.upper())
                    hotkey_hint = "+".join(parts)

                # Render command row
                with ui.row().classes(
                    "w-full items-center justify-between p-3 hover:bg-surface-3 cursor-pointer rounded"
                ).on("click", lambda cb=callback: self._execute(cb)):
                    ui.label(label).classes("text-white")
                    if hotkey_hint:
                        ui.label(hotkey_hint).classes(
                            "text-xs text-gray-500 bg-surface-1 px-2 py-1 rounded font-mono"
                        )

    def _execute(self, callback: Callable[[], None]) -> None:
        """Execute command and close palette."""
        self.close()
        try:
            callback()
        except Exception as exc:
            logger.error(
                "command_palette_execution_failed",
                extra={"error": str(exc)},
            )


__all__ = ["CommandPalette"]
```

**4. Layout Integration**

Add to `apps/web_console_ng/ui/layout.py`:

```python
# Add imports:
import json
from apps.web_console_ng.components.command_palette import CommandPalette
from apps.web_console_ng.core.hotkey_manager import HotkeyManager

# In main_layout wrapper:
# Initialize hotkey manager
hotkey_manager = HotkeyManager()
app.storage.user["hotkey_manager"] = hotkey_manager

# Create command palette
command_palette = CommandPalette(hotkey_manager)
palette_dialog = command_palette.create()

# Define handlers BEFORE creating UI elements that reference them
def show_hotkey_help():
    with ui.dialog() as help_dialog:
        with ui.card().classes("p-6 bg-surface-2"):
            ui.label("Keyboard Shortcuts").classes("text-xl font-bold text-white mb-4")
            for binding in hotkey_manager.get_bindings():
                if not binding.enabled:
                    continue
                with ui.row().classes("items-center justify-between py-1"):
                    ui.label(binding.description).classes("text-white")
                    # Format hotkey
                    parts = [m.upper() for m in binding.modifiers]
                    parts.append(binding.key.upper())
                    ui.label("+".join(parts)).classes(
                        "text-xs bg-surface-1 px-2 py-1 rounded font-mono"
                    )
            ui.button("Close", on_click=help_dialog.close).classes("mt-4")
    help_dialog.open()

# Register standard commands
command_palette.register_command("open_palette", "Open Command Palette", lambda: None)
command_palette.register_command("show_help", "Show Hotkey Reference", show_hotkey_help)

# Register hotkey handlers
hotkey_manager.register_handler("open_palette", command_palette.open)
hotkey_manager.register_handler("show_help", show_hotkey_help)

# Load hotkey handler JS with deferred initialization
# Use ui.on('connect') to ensure script is loaded before calling init
ui.add_head_html('<script src="/static/js/hotkey_handler.js"></script>')
bindings_list = hotkey_manager.get_bindings_json()
bindings_json_str = json.dumps(bindings_list)  # JS-safe serialization

async def init_hotkeys():
    """Initialize hotkey handler after page is connected and scripts loaded."""
    await ui.run_javascript(f"window.HotkeyHandler.init({bindings_json_str})")

ui.on("connect", init_hotkeys)  # Deferred init ensures script is loaded

# Listen for hotkey actions from JS (using established args=['detail'] pattern)
async def on_hotkey_action(detail: dict):
    action = detail.get("action")
    if action:
        hotkey_manager.handle_action(action)

ui.on("hotkey_action", on_hotkey_action, args=['detail'])
```

**5. Order Form Integration (manual_order.py)**

Add hotkey integration to `apps/web_console_ng/pages/manual_order.py`:

```python
# Add to imports:
from apps.web_console_ng.core.hotkey_manager import HotkeyScope

# In the order form page, wrap form in data-order-form container for scope detection:
with ui.card().classes("order-form-container").props("data-order-form"):
    # ... existing form fields (buy_qty_input, sell_qty_input, etc.)
    pass

# CRITICAL: Register handlers that reuse EXISTING submission logic
# Hotkeys MUST NOT create a separate submission path - they only trigger UI actions
def _register_order_form_hotkeys(
    hotkey_manager: HotkeyManager,
    buy_qty_input: ui.input,
    sell_qty_input: ui.input,
    submit_button: ActionButton,  # The existing submit button
    clear_form_callback: Callable[[], None],
):
    """Register order form hotkey handlers.

    TRADING SAFETY: These handlers only trigger UI actions (focus, click).
    They do NOT implement order submission logic directly - they call the
    existing submit button which has circuit breaker, risk validation, and
    idempotency checks built in.
    """
    hotkey_manager.register_handler("focus_buy", lambda: buy_qty_input.run_method("focus"))
    hotkey_manager.register_handler("focus_sell", lambda: sell_qty_input.run_method("focus"))

    # CRITICAL: submit_order uses the public trigger() method
    # This ensures the same code path with all trading safeguards is used
    async def submit_via_hotkey():
        if submit_button.state == ButtonState.DEFAULT:
            await submit_button.trigger()  # Public API - uses existing validated submission

    hotkey_manager.register_handler("submit_order", submit_via_hotkey)
    hotkey_manager.register_handler("cancel_form", clear_form_callback)

# NOTE: Do NOT use focusout to reset scope - it causes flicker during intra-form focus.
# Instead, rely on HotkeyHandler._isInOrderForm() which checks activeElement.closest().
def setup_form_scope_tracking(form_container: ui.element):
    """Set hotkey scope tracking.

    CRITICAL: We do NOT use focusin/focusout listeners to toggle scope state here.
    Doing so causes 'latching' where scope gets stuck in ORDER_FORM if the reset logic
    fails or isn't triggered (e.g. focus lost to window).

    Instead, we rely entirely on the browser-side `HotkeyHandler._isInOrderForm()`
    which checks `document.activeElement.closest('[data-order-form]')` dynamically
    at the time of the keypress.
    """
    # No explicit listeners needed - JS handler does dynamic checking
    pass
```

**Files to Create/Modify:**

| File | Action | Description |
|------|--------|-------------|
| `apps/web_console_ng/core/hotkey_manager.py` | Create | Hotkey binding management |
| `apps/web_console_ng/static/js/hotkey_handler.js` | Create | Browser-side key capture |
| `apps/web_console_ng/components/command_palette.py` | Create | Quick command access dialog |
| `apps/web_console_ng/ui/layout.py` | Modify | Wire hotkey manager, command palette, deferred JS init |
| `apps/web_console_ng/pages/manual_order.py` | Modify | Add order form hotkey handlers and scope tracking |

**Acceptance Criteria:**
- [ ] B/S keys focus buy/sell inputs (when in order form context)
- [ ] Enter submits order form (MUST use existing submit button handler with all safeguards)
- [ ] Escape cancels/clears forms
- [ ] Command palette opens with / or Ctrl+K
- [ ] F1 or ? shows hotkey reference (note: ? won't work inside text inputs by design)
- [ ] Hotkey hints visible in command palette
- [ ] Hotkeys don't fire when typing in text inputs (except global hotkeys with modifiers like Ctrl+K)
- [ ] No conflicts with browser/OS shortcuts (verified manually)
- [ ] Hotkey bindings are session-only (reset on page reload; persistence not implemented)
- [ ] ORDER_FORM scope transitions correctly on form focus/blur
- [ ] Hotkey-triggered submit uses same code path as button click (circuit breaker, risk, idempotency)

---

### T3.3: State Feedback Loops - HIGH PRIORITY

**Goal:** Immediate visual feedback on all user actions.

#### Button States

| State | Visual | Duration |
|-------|--------|----------|
| Default | Normal button appearance | -- |
| Sending | Spinner + "Sending..." | Until response |
| Confirming | Pulsing + "Confirming..." | Until confirmed |
| Success | Green flash + checkmark | 2 seconds, then reset |
| Failed | Red + X mark | Until dismissed |
| Timeout | Warning + "Taking longer..." | After 5 seconds |

#### Implementation Details

**1. Action Button Component**

Create `apps/web_console_ng/components/action_button.py`:

```python
"""Action button with state feedback for trading operations.

Features:
- Immediate feedback on click (<50ms)
- State transitions: Default -> Sending -> Confirming -> Success/Failed
- External state API for order lifecycle integration
- Timeout handling (>5s warning)
- Automatic reset after success

Order Lifecycle Integration:
- Button click triggers SENDING state automatically
- Call set_external_state(CONFIRMING) when order is submitted to broker
- Call set_external_state(SUCCESS) when fill is confirmed
- Call set_external_state(FAILED) on rejection
- Button auto-resets to DEFAULT after SUCCESS display period
"""

from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import Any, Callable, Coroutine

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


# State to visual mapping
STATE_CONFIG = {
    ButtonState.DEFAULT: {
        "icon": None,
        "text_suffix": "",
        "classes": "",
        "spinning": False,
    },
    ButtonState.SENDING: {
        "icon": "sync",
        "text_suffix": "Sending...",
        "classes": "animate-pulse",
        "spinning": True,
    },
    ButtonState.CONFIRMING: {
        "icon": "hourglass_top",
        "text_suffix": "Confirming...",
        "classes": "animate-pulse",
        "spinning": True,
    },
    ButtonState.SUCCESS: {
        "icon": "check_circle",
        "text_suffix": "",
        "classes": "bg-green-600 animate-[successFlash_0.5s_ease-out]",
        "spinning": False,
    },
    ButtonState.FAILED: {
        "icon": "error",
        "text_suffix": "",
        "classes": "bg-red-600",
        "spinning": False,
    },
    ButtonState.TIMEOUT: {
        "icon": "schedule",
        "text_suffix": "Taking longer than expected...",
        "classes": "bg-yellow-600",
        "spinning": True,
    },
}


class ActionButton:
    """Button with state feedback for async operations.

    Two modes of operation:
    1. Auto-lifecycle (default): _on_click return value controls SUCCESS/FAILED
    2. Manual-lifecycle: External code controls state via set_external_state()

    Basic Usage (auto-lifecycle, manual_lifecycle=False):
        async def submit_order():
            response = await api.submit_order(...)
            return response.success  # True -> SUCCESS, False -> FAILED

        btn = ActionButton("Submit Order", submit_order)
        btn.create()

    Advanced Usage (manual-lifecycle for multi-phase order flow):
        btn = ActionButton("Submit Order", submit_order_with_lifecycle, manual_lifecycle=True)
        btn.create()

        async def submit_order_with_lifecycle():
            # Phase 1: Submit to API (SENDING is automatic on click)
            order_id = await api.submit_order(...)

            # Phase 2: Order accepted, waiting for fill
            btn.set_external_state(ButtonState.CONFIRMING)

            # Phase 3: Wait for fill (via websocket/polling)
            fill = await wait_for_fill(order_id, timeout=30)

            if fill.success:
                btn.set_external_state(ButtonState.SUCCESS)  # Auto-resets
            else:
                btn.set_external_state(ButtonState.FAILED)

            # Return value is IGNORED when manual_lifecycle=True
            # State is controlled entirely by set_external_state() calls
    """

    TIMEOUT_THRESHOLD_MS = 5000
    SUCCESS_DISPLAY_MS = 2000

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
            label: Button text
            on_click: Async callback; return True=SUCCESS, False=FAILED, None=external control
            icon: Optional icon name
            color: Button color
            manual_lifecycle: If True, ignore _on_click return value and defer to
                            set_external_state() for all state transitions after SENDING.
                            Use this for multi-phase order lifecycles (Submit→Confirm→Fill).
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
        self._button = ui.button(on_click=self._handle_click).props(
            f"color={self._color}"
        )
        with self._button:
            with ui.row().classes("items-center gap-2"):
                if self._icon:
                    self._icon_element = ui.icon(self._icon)
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

        # Update icon
        # Use config icon if present, otherwise fall back to initial icon (for DEFAULT state)
        target_icon = config["icon"] if config["icon"] else self._icon
        if self._icon_element and target_icon:
            self._icon_element.props(f"name={target_icon}")
            if config["spinning"]:
                self._icon_element.classes("animate-spin")
            else:
                self._icon_element.classes(remove="animate-spin")

        # Update label
        if self._label_element:
            if config["text_suffix"]:
                self._label_element.set_text(config["text_suffix"])
            else:
                self._label_element.set_text(self._label)

        # Update button classes
        self._button.classes(remove="animate-pulse bg-green-600 bg-red-600 bg-yellow-600")
        if config["classes"]:
            self._button.classes(add=config["classes"])

        # Disable button during processing
        if state in (ButtonState.SENDING, ButtonState.CONFIRMING, ButtonState.TIMEOUT):
            self._button.disable()
        else:
            self._button.enable()

    async def _handle_click(self) -> None:
        """Handle button click with state management."""
        if self._state != ButtonState.DEFAULT:
            return

        # Immediate feedback
        self._set_state(ButtonState.SENDING)

        # Start timeout watcher
        self._timeout_task = asyncio.create_task(self._watch_timeout())

        try:
            try:
                # Execute action
                result = await self._on_click()
            finally:
                # Cancel timeout watcher (always)
                if self._timeout_task:
                    self._timeout_task.cancel()

            # CRITICAL: In manual_lifecycle mode, ignore return value
            # State is controlled entirely by set_external_state() calls
            if self._manual_lifecycle:
                # Callback is responsible for calling set_external_state()
                # Do not override any state that may have been set
                return

            # Auto-lifecycle mode: use return value to set state
            if result is True:
                self._set_state(ButtonState.SUCCESS)
                # Auto-reset after success display
                await asyncio.sleep(self.SUCCESS_DISPLAY_MS / 1000)
                self._set_state(ButtonState.DEFAULT)
            elif result is False:
                self._set_state(ButtonState.FAILED)
                # Failed state persists until dismissed or retry
            # If result is None, assume external control (hybrid mode)

        except asyncio.CancelledError:
            self._set_state(ButtonState.DEFAULT)
        except Exception as exc:
            logger.error(
                "action_button_error",
                extra={"label": self._label, "error": str(exc)},
            )
            self._set_state(ButtonState.FAILED)

    async def _watch_timeout(self) -> None:
        """Watch for timeout and update state."""
        await asyncio.sleep(self.TIMEOUT_THRESHOLD_MS / 1000)
        if self._state == ButtonState.SENDING:
            self._set_state(ButtonState.TIMEOUT)

    def reset(self) -> None:
        """Reset button to default state (for dismiss after failure)."""
        if self._timeout_task:
            self._timeout_task.cancel()
        self._set_state(ButtonState.DEFAULT)

    async def trigger(self) -> None:
        """Programmatically trigger the button click.

        Public API for external callers (e.g., hotkey handlers) to trigger
        the button action without accessing private methods.

        This is the preferred way to trigger the button from hotkeys:
            hotkey_manager.register_handler("submit_order", submit_button.trigger)
        """
        await self._handle_click()

    def set_external_state(self, state: ButtonState) -> None:
        """Set state from external events (e.g., order lifecycle updates).

        This allows order submission flow to update button state based on
        external events like order acknowledgment, fill confirmation, or rejection.

        Typical order lifecycle flow:
        1. Button click -> SENDING (automatic)
        2. Order submitted to API -> CONFIRMING (via set_external_state)
        3. Fill received or timeout -> SUCCESS/FAILED (via set_external_state)

        Usage:
            # In order submission callback
            btn.set_external_state(ButtonState.CONFIRMING)

            # When fill is received
            btn.set_external_state(ButtonState.SUCCESS)
            await asyncio.sleep(2.0)  # Display success briefly
            btn.reset()
        """
        if self._timeout_task:
            self._timeout_task.cancel()
        self._set_state(state)

        # Auto-reset after success display if needed
        if state == ButtonState.SUCCESS:
            asyncio.create_task(self._auto_reset_after_success())

    async def _auto_reset_after_success(self) -> None:
        """Auto-reset to default after success display period."""
        await asyncio.sleep(self.SUCCESS_DISPLAY_MS / 1000)
        if self._state == ButtonState.SUCCESS:
            self._set_state(ButtonState.DEFAULT)


__all__ = ["ActionButton", "ButtonState"]
```

**2. CSS Animations**

Add to `apps/web_console_ng/static/css/custom.css`:

```css
/* Button state feedback animations */

/* Success flash animation */
@keyframes successFlash {
    0% {
        background-color: #16a34a;
        box-shadow: 0 0 10px #16a34a;
    }
    50% {
        background-color: #22c55e;
        box-shadow: 0 0 20px #22c55e;
    }
    100% {
        background-color: #16a34a;
        box-shadow: none;
    }
}

/* Failed shake animation */
@keyframes failedShake {
    0%, 100% { transform: translateX(0); }
    10%, 30%, 50%, 70%, 90% { transform: translateX(-2px); }
    20%, 40%, 60%, 80% { transform: translateX(2px); }
}

/* Pulse animation for processing states */
@keyframes buttonPulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.7; }
}

.animate-success-flash {
    animation: successFlash 0.5s ease-out;
}

.animate-failed-shake {
    animation: failedShake 0.5s ease-out;
}

.animate-button-pulse {
    animation: buttonPulse 1s ease-in-out infinite;
}
```

**3. Loading States Helper**

Create `apps/web_console_ng/components/loading_states.py`:

```python
"""Shared loading state utilities for UI feedback.

Provides consistent loading indicators and state management
across components.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from nicegui import ui


@asynccontextmanager
async def loading_overlay(
    element: ui.element, message: str = "Loading..."
) -> AsyncGenerator[None, None]:
    """Context manager that shows loading overlay on an element.

    Usage:
        async with loading_overlay(card, "Fetching data..."):
            data = await fetch_data()
    """
    # Add overlay
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
        # Remove overlay
        overlay.delete()


def inline_spinner(size: str = "sm") -> ui.spinner:
    """Create an inline spinner for button/text loading states."""
    return ui.spinner("dots", size=size).classes("mx-1")


def skeleton_loader(width: str = "w-full", height: str = "h-4") -> ui.element:
    """Create a skeleton loader placeholder."""
    return ui.element("div").classes(
        f"{width} {height} bg-surface-3 rounded animate-pulse"
    )


class LoadingState:
    """Reusable loading state manager for components.

    Usage:
        loading = LoadingState()
        loading.attach(my_card)

        loading.start("Submitting order...")
        result = await submit_order()
        loading.stop()
    """

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
```

**Files to Create/Modify:**

| File | Action | Description |
|------|--------|-------------|
| `apps/web_console_ng/components/action_button.py` | Create | Button with state feedback |
| `apps/web_console_ng/components/loading_states.py` | Create | Shared loading utilities |
| `apps/web_console_ng/static/css/custom.css` | Modify | Add button animations |
| `apps/web_console_ng/pages/manual_order.py` | Modify | Use ActionButton for submit |

**Acceptance Criteria:**
- [ ] All trading buttons show immediate feedback on click (<50ms perceived)
- [ ] Loading state visible (spinner + "Sending...")
- [ ] Success state: green flash + checkmark (2s then reset)
- [ ] Failure state: red + X mark (persists until dismissed)
- [ ] Timeout handling (>5s = "Taking longer than expected...")
- [ ] Button disabled during processing (no double-submit)
- [ ] ActionButton integrates with existing order submission flow

---

### T3.4: Cell Flash Updates - MEDIUM PRIORITY

**Goal:** Visual attention on price/P&L changes via cell flashing.

**Dependency:** Requires P6T1.1 (Throttling) to be complete for backpressure integration.

#### Flash Behavior

| Change | Flash Color | Duration |
|--------|-------------|----------|
| Value increased | Green (#00E676) | 500ms |
| Value decreased | Red (#FF5252) | 500ms |
| No change | No flash | -- |

#### Implementation Details

**1. CSS Flash Animations**

Add to `apps/web_console_ng/static/css/custom.css`:

```css
/* Cell flash animations for price/P&L updates */

@keyframes flashGreen {
    0% {
        background-color: rgba(0, 230, 118, 0.5);
    }
    100% {
        background-color: transparent;
    }
}

@keyframes flashRed {
    0% {
        background-color: rgba(255, 82, 82, 0.5);
    }
    100% {
        background-color: transparent;
    }
}

.cell-flash-up {
    animation: flashGreen 500ms ease-out;
}

.cell-flash-down {
    animation: flashRed 500ms ease-out;
}

/* Disable flash when grid is in degraded mode (high update rate) */
.grid-degraded .cell-flash-up,
.grid-degraded .cell-flash-down {
    animation: none;
}
```

**2. Cell Flash Manager (JavaScript)**

Create `apps/web_console_ng/static/js/cell_flash.js`:

```javascript
/**
 * Cell flash manager for AG Grid price/P&L updates.
 *
 * Features:
 * - Tracks previous values to detect direction
 * - Applies flash animation classes
 * - Respects GridThrottle degradation mode (no flash when degraded)
 * - Configurable flash duration
 */
window.CellFlashManager = {
    // Previous values by grid_id -> row_id -> column_id -> value
    previousValues: {},

    // Flash duration in ms (configurable)
    flashDuration: 500,

    // Columns that should flash on change (aligned with positions_grid.py field names)
    flashColumns: new Set(['unrealized_pl', 'unrealized_plpc', 'current_price']),

    /**
     * Initialize for a grid.
     * @param {string} gridId - Grid identifier
     * @param {Array} columnsToFlash - Column IDs that should flash
     */
    init(gridId, columnsToFlash = null) {
        this.previousValues[gridId] = {};
        if (columnsToFlash) {
            this.flashColumns = new Set(columnsToFlash);
        }
    },

    /**
     * Check if flash should be suppressed (grid in degraded mode).
     * @param {string} gridId
     */
    shouldSuppressFlash(gridId) {
        // Check GridThrottle degradation state
        if (window.GridThrottle) {
            const metrics = window.GridThrottle.getMetrics(gridId);
            if (metrics && metrics.degradedMode) {
                return true;
            }
        }
        return false;
    },

    /**
     * Process cell value change and apply flash if needed.
     * Called from AG Grid cellValueChanged or via transaction processing.
     *
     * @param {string} gridId - Grid identifier
     * @param {string} rowId - Row identifier
     * @param {string} colId - Column identifier
     * @param {number} newValue - New cell value
     * @param {HTMLElement} cellElement - The cell DOM element
     */
    processChange(gridId, rowId, colId, newValue, cellElement) {
        // Skip non-flash columns
        if (!this.flashColumns.has(colId)) {
            return;
        }

        // Skip if degraded
        if (this.shouldSuppressFlash(gridId)) {
            return;
        }

        // Get previous value
        if (!this.previousValues[gridId]) {
            this.previousValues[gridId] = {};
        }
        if (!this.previousValues[gridId][rowId]) {
            this.previousValues[gridId][rowId] = {};
        }

        const prevValue = this.previousValues[gridId][rowId][colId];

        // Store new value
        this.previousValues[gridId][rowId][colId] = newValue;

        // Skip if no previous value (initial render)
        if (prevValue === undefined) {
            return;
        }

        // Skip if no change
        if (prevValue === newValue) {
            return;
        }

        // Determine direction and apply flash
        const flashClass = newValue > prevValue ? 'cell-flash-up' : 'cell-flash-down';

        // Remove any existing flash class
        cellElement.classList.remove('cell-flash-up', 'cell-flash-down');

        // Trigger reflow to restart animation
        void cellElement.offsetWidth;

        // Add flash class
        cellElement.classList.add(flashClass);

        // Remove class after animation completes
        setTimeout(() => {
            cellElement.classList.remove(flashClass);
        }, this.flashDuration);
    },

    /**
     * AG Grid cell renderer with flash support.
     * Use as cellRenderer in column definition.
     */
    createFlashRenderer(gridId, colId) {
        return (params) => {
            const value = params.value;
            const rowId = params.data?.symbol || params.data?.client_order_id || params.node?.id;

            // Create cell content
            const span = document.createElement('span');
            span.textContent = this.formatValue(value, colId);
            span.dataset.gridId = gridId;
            span.dataset.rowId = rowId;
            span.dataset.colId = colId;

            // Process flash on subsequent renders
            if (rowId && params.node) {
                // Use setTimeout to allow DOM to settle
                setTimeout(() => {
                    this.processChange(gridId, rowId, colId, value, span);
                }, 0);
            }

            return span;
        };
    },

    /**
     * Format value for display (basic number formatting).
     */
    formatValue(value, colId) {
        if (value === null || value === undefined) return '-';
        if (typeof value === 'number') {
            // P&L dollar columns
            if (colId === 'unrealized_pl' || colId === 'current_price') {
                return '$' + value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
            }
            // P&L percentage columns
            if (colId === 'unrealized_plpc') {
                return (value * 100).toFixed(2) + '%';
            }
            return value.toLocaleString();
        }
        return String(value);
    },

    /**
     * Clean up stored values for a grid (call on grid destroy).
     */
    cleanup(gridId) {
        delete this.previousValues[gridId];
    }
};
```

**3. Grid Integration**

Modify `apps/web_console_ng/components/positions_grid.py`:

**Note:** Only positions_grid.py has P&L fields that should flash. orders_table.py does not have P&L columns, so it does not need cell flash integration.

```python
# Add to grid options for flash-enabled columns:
# NOTE: Use `:cellRenderer` (with colon prefix) for JS function strings in NiceGUI
{
    "columnDefs": [
        # ... existing columns ...
        {
            "field": "unrealized_pl",  # Actual field name from positions_grid.py
            "headerName": "P&L ($)",
            # Use custom cell renderer with flash support
            ":cellRenderer": "params => window.CellFlashManager.createFlashRenderer('positions_grid', 'unrealized_pl')(params)",
            "cellClass": "text-right font-mono-numbers",
        },
        {
            "field": "unrealized_plpc",  # Actual field name from positions_grid.py
            "headerName": "P&L (%)",
            ":cellRenderer": "params => window.CellFlashManager.createFlashRenderer('positions_grid', 'unrealized_plpc')(params)",
            "cellClass": "text-right font-mono-numbers",
        },
        {
            "field": "current_price",
            "headerName": "Current",
            ":cellRenderer": "params => window.CellFlashManager.createFlashRenderer('positions_grid', 'current_price')(params)",
            "cellClass": "text-right font-mono-numbers",
        },
        # ... other non-flashable columns ...
    ],
    # Initialize flash manager on grid ready (colon prefix required)
    ":onGridReady": """params => {
        window.CellFlashManager.init('positions_grid', ['unrealized_pl', 'unrealized_plpc', 'current_price']);
    }""",
}
```

**Note:** orders_table.py does not have P&L columns - it only has symbol, side, qty, type, limit_price, status, created_at. No flash integration needed for orders table.

**4. Layout Integration**

Add to `apps/web_console_ng/ui/layout.py`:

```python
# Load cell flash manager
ui.add_head_html('<script src="/static/js/cell_flash.js"></script>')

# Note: GridThrottle already dispatches 'grid_degradation_change' events.
# CellFlashManager.shouldSuppressFlash() checks GridThrottle state directly,
# so no additional DOM manipulation is needed for degradation handling.
```

**Files to Create/Modify:**

| File | Action | Description |
|------|--------|-------------|
| `apps/web_console_ng/static/js/cell_flash.js` | Create | Cell flash management |
| `apps/web_console_ng/static/css/custom.css` | Modify | Add flash animations |
| `apps/web_console_ng/components/positions_grid.py` | Modify | Add flash renderers for P&L columns |
| `apps/web_console_ng/ui/layout.py` | Modify | Load cell_flash.js |

**Note:** orders_table.py does NOT need modification - it has no P&L columns.

**Acceptance Criteria:**
- [ ] Price changes flash green (up) or red (down)
- [ ] P&L columns flash on update
- [ ] Flash duration configurable (default 500ms)
- [ ] No performance degradation with frequent updates
- [ ] Flash automatically disabled when update rate exceeds threshold (from GridThrottle)
- [ ] Flash works correctly with delta updates (applyTransaction)

---

## Dependencies

```
P6T1.1 Throttling ──> T3.4 Cell Flash (backpressure integration)

T3.1 Notifications ──> Used by all order actions, circuit breaker, errors
T3.2 Hotkeys ──> Order entry (P6T4), Order actions (P6T7)
T3.3 Feedback ──> All buttons in P6T4-P6T7
T3.4 Cell Flash ──> Positions grid, Orders table
```

**Implementation Order:** T3.1 → T3.3 → T3.2 → T3.4

**Note:** T3.3 (ActionButton) must be implemented before T3.2's manual_order.py integration,
as the hotkey handler's `submit_via_hotkey()` depends on the ActionButton interface.

---

## Testing Strategy

**Note on JS Testing:** JavaScript components (hotkey_handler.js, cell_flash.js) are tested via E2E tests using Playwright, not via Python unit tests. Unit tests cover Python-only logic; browser behavior requires E2E.

### Unit Tests (Python-only)

**T3.1 Notification Center:**
- `tests/apps/web_console_ng/test_notification_router.py`
  - Test priority routing with quiet mode ON/OFF
    - HIGH → always toast
    - MEDIUM → toast when quiet mode OFF, no toast when ON
    - LOW → never toast
  - Test badge increment logic (HIGH/MEDIUM increment, LOW does not)
  - Test history management (100 item limit, FIFO)
  - Test mark all read functionality
  - Test callback invocations
  - Test UserStateManager persistence integration

**T3.2 Keyboard Hotkeys:**
- `tests/apps/web_console_ng/test_hotkey_manager.py`
  - Test binding matching with modifiers (Ctrl+K)
  - Test binding matching without modifiers (including "?" key)
  - Test scope filtering (global vs order_form)
  - Test handler registration and invocation
  - Test binding updates
  - Test get_bindings_json() serialization

**T3.3 State Feedback:**
- `tests/apps/web_console_ng/test_action_button.py`
  - Test state transitions (DEFAULT → SENDING → SUCCESS/FAILED)
  - Test external state API (set_external_state for CONFIRMING)
  - Test timeout state after threshold (>5s)
  - Test failed state persistence
  - Test auto-reset after success display period

### Integration Tests

**T3.1:**
- `tests/integration/test_notification_integration.py`
  - Test notification flow from order fill → drawer display
  - Test quiet mode toggle with UserStateManager persistence

**T3.2:**
- `tests/integration/test_hotkey_integration.py`
  - Test hotkey handler registration and callback invocation
  - Test command palette command registration and execution

### E2E Tests (Playwright - tests JS behavior)

**Note:** JS components require browser environment. Use existing Playwright E2E framework.

- `tests/e2e/test_notifications_e2e.py`
  - Notification drawer open/close behavior
  - Badge count updates in DOM
  - Quiet mode toggle visual state

- `tests/e2e/test_hotkeys_e2e.py`
  - Hotkey workflow (B → enter qty → Enter)
  - Command palette open/close (/ and Ctrl+K)
  - Help dialog open with F1 and ? (Shift+/ produces "?" which should match binding)
  - Hotkey suppression when typing in inputs
  - Key event dispatch to Python handler

- `tests/e2e/test_button_states_e2e.py`
  - Button state visual transitions during order submission
  - Spinner display during SENDING state
  - Success/failure visual feedback

- `tests/e2e/test_cell_flash_e2e.py`
  - Cell flash animation on value change (green up, red down)
  - Flash suppression in degraded mode
  - Flash timing (500ms duration)

---

## Definition of Done

- [ ] All 4 tasks implemented
- [ ] Notification center with quiet mode working
- [ ] Hotkeys functional (B/S/Enter/Escape/Command Palette)
- [ ] State feedback on all trading buttons
- [ ] Cell flash updates working with backpressure awareness
- [ ] Unit tests > 85% coverage
- [ ] Integration tests pass
- [ ] E2E tests pass
- [ ] Code reviewed and approved
- [ ] No performance regression (verified via GridThrottle metrics)

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Hotkey conflicts with browser | Test on Chrome, Firefox, Safari; use Ctrl/Cmd for global shortcuts |
| Flash performance impact | Integrate with GridThrottle degradation mode |
| Toast spam during high activity | Strict priority routing; only HIGH toasts |
| Memory leak in notification history | Bounded deque with MAX_HISTORY limit |
| Hotkey bypasses trading safeguards | **CRITICAL:** All hotkey-triggered order actions MUST route through existing order submission path with circuit breaker checks, risk validation, and idempotency (client_order_id). Hotkeys are UI shortcuts only - they do NOT bypass any trading safety checks. |
| Double-submit via rapid hotkeys | ActionButton disables during SENDING/CONFIRMING state; idempotent client_order_id prevents duplicate orders even if button re-enabled prematurely |
| International keyboard layouts | Document US layout assumption; provide fallback guidance for non-US users |

---

**Last Updated:** 2026-01-20
**Status:** TASK (Planning Complete, Ready for Review)
