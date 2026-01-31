"""Shared page layout for the NiceGUI trading console."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

import httpx
from nicegui import app, ui

from apps.web_console_ng.auth.middleware import get_current_user
from apps.web_console_ng.components.command_palette import CommandPalette
from apps.web_console_ng.components.header_metrics import HeaderMetrics
from apps.web_console_ng.components.log_drawer import LogDrawer
from apps.web_console_ng.components.market_clock import MarketClock
from apps.web_console_ng.components.status_bar import StatusBar
from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from apps.web_console_ng.core.connection_monitor import ConnectionMonitor
from apps.web_console_ng.core.grid_performance import get_all_monitors
from apps.web_console_ng.core.hotkey_manager import HotkeyManager
from apps.web_console_ng.core.latency_monitor import LatencyMonitor
from apps.web_console_ng.core.notification_router import NotificationRouter
from apps.web_console_ng.core.state_manager import UserStateManager
from apps.web_console_ng.ui.dark_theme import enable_dark_mode
from apps.web_console_ng.ui.theme import (
    CONNECTION_BADGE_REMOVE_CLASSES,
    LATENCY_BADGE_REMOVE_CLASSES,
    LATENCY_DISCONNECTED,
)
from apps.web_console_ng.utils.session import get_or_create_client_id
from libs.platform.web_console_auth.permissions import Permission, has_permission

logger = logging.getLogger(__name__)

AsyncPage = Callable[..., Awaitable[Any]]


def main_layout(page_func: AsyncPage) -> AsyncPage:
    """Decorator for consistent page layout with header, sidebar, and content."""

    @wraps(page_func)
    async def wrapper(*args: Any, **kwargs: Any) -> None:
        enable_dark_mode()
        # Trading state listener JS extracted to separate file for maintainability
        ui.add_head_html('<script src="/static/js/trading_state_listener.js"></script>')
        ui.add_head_html('<script src="/static/js/grid_throttle.js"></script>')
        ui.add_head_html('<script src="/static/js/cell_flash.js"></script>')
        ui.add_head_html('<script src="/static/js/grid_state_manager.js"></script>')
        ui.add_head_html('<script src="/static/js/hierarchical_grid.js"></script>')
        ui.add_head_html('<script src="/static/js/sparkline.js"></script>')
        ui.add_head_html('<script src="/static/js/dom_ladder.js"></script>')
        ui.add_head_html('<script src="/static/js/hotkey_handler.js"></script>')
        ui.add_head_html('<script src="/static/js/grid_export.js"></script>')
        ui.add_head_html('<link rel="stylesheet" href="/static/css/density.css">')
        ui.add_head_html('<link rel="stylesheet" href="/static/css/custom.css">')

        degrade_threshold = os.environ.get("GRID_DEGRADE_THRESHOLD", "120")
        debug_mode = os.environ.get("GRID_DEBUG", "false").lower() == "true"
        ui.add_body_html(
            "<script>"
            f'document.body.dataset.gridDegradeThreshold = "{degrade_threshold}";'
            f'document.body.dataset.gridDebug = "{str(debug_mode).lower()}";'
            "</script>"
        )

        user = get_current_user()
        user_role = str(user.get("role", "viewer"))
        user_name = str(user.get("username", "Unknown"))
        user_id = str(user.get("user_id") or user_name)
        # Extract strategies for API calls (needed for INTERNAL_TOKEN_SECRET in production)
        raw_strategies = user.get("strategies")
        user_strategies: list[str] = (
            list(raw_strategies) if isinstance(raw_strategies, list | tuple) else []
        )

        request = getattr(app.storage, "request", None)
        current_path = "/"
        if request is not None:
            current_path = getattr(getattr(request, "url", None), "path", "//") or "/"
        app.storage.user["current_path"] = current_path

        state_manager = UserStateManager(
            user_id=user_id,
            role=user_role,
            strategies=user_strategies,
        )
        notification_router = NotificationRouter(state_manager=state_manager)
        await notification_router.load_preferences()
        # Store in client storage (per-tab) to avoid multi-tab conflicts
        app.storage.client["notification_router"] = notification_router
        existing_hotkey_manager = app.storage.client.get("hotkey_manager")
        if isinstance(existing_hotkey_manager, HotkeyManager):
            hotkey_manager = existing_hotkey_manager
        else:
            hotkey_manager = HotkeyManager()
            # Store in client storage (per-tab) to avoid multi-tab conflicts
            app.storage.client["hotkey_manager"] = hotkey_manager

        command_palette = CommandPalette(hotkey_manager)
        command_palette.create()

        def show_hotkey_help() -> None:
            with ui.dialog() as help_dialog:
                with ui.card().classes("p-6 bg-surface-2"):
                    ui.label("Keyboard Shortcuts").classes("text-xl font-bold text-white mb-4")
                    for binding in hotkey_manager.get_bindings():
                        if not binding.enabled:
                            continue
                        with ui.row().classes("items-center justify-between py-1"):
                            ui.label(binding.description).classes("text-white")
                            parts = [mod.upper() for mod in binding.modifiers]
                            parts.append(binding.key.upper())
                            ui.label("+".join(parts)).classes(
                                "text-xs bg-surface-1 px-2 py-1 rounded font-mono"
                            )
                    ui.button("Close", on_click=help_dialog.close).classes("mt-4")
            help_dialog.open()

        command_palette.register_command("open_palette", "Open Command Palette", lambda: None)
        command_palette.register_command("show_help", "Show Hotkey Reference", show_hotkey_help)

        hotkey_manager.register_handler("open_palette", command_palette.open)
        hotkey_manager.register_handler("show_help", show_hotkey_help)

        # Register hotkey handlers on every page load (NiceGUI ui.on handlers are per-page)
        # The JS side has its own guard against duplicate keydown listeners
        bindings_list = hotkey_manager.get_bindings_json()
        bindings_json_str = json.dumps(bindings_list)

        async def init_hotkeys() -> None:
            await ui.run_javascript(f"window.HotkeyHandler.init({bindings_json_str})")

        ui.on("connect", init_hotkeys)

        async def on_hotkey_action(detail: dict[str, Any]) -> None:
            action = detail.get("action")
            if action:
                hotkey_manager.handle_action(action)

        # NiceGUI's type hints don't fully cover the args parameter pattern
        ui.on("hotkey_action", on_hotkey_action, args=["detail"])  # type: ignore[arg-type]

        client = AsyncTradingClient.get()

        # Left drawer (sidebar)
        drawer = ui.left_drawer(value=True).classes("bg-surface-1 w-64")
        with drawer:
            with ui.column().classes("w-full gap-1 p-3"):
                ui.label("Navigation").classes("text-gray-500 text-xs uppercase tracking-wide mb-2")

                nav_items = [
                    ("Dashboard", "/", "dashboard", None),
                    ("Manual Controls", "/manual-order", "edit", None),
                    ("Circuit Breaker", "/circuit-breaker", "electric_bolt", None),
                    ("System Health", "/health", "monitor_heart", None),
                    ("Risk Analytics", "/risk", "trending_up", None),
                    ("Alpha Explorer", "/alpha-explorer", "insights", None),  # P5T8
                    ("Compare", "/compare", "compare_arrows", None),  # P5T8
                    ("Journal", "/journal", "book", None),  # P5T8
                    ("Notebooks", "/notebooks", "article", None),  # P5T8
                    ("Performance", "/performance", "show_chart", None),  # P5T8
                    ("Reports", "/reports", "summarize", None),  # P5T8
                    ("Backtest", "/backtest", "science", None),
                    (
                        "Admin",
                        "/admin",
                        "settings",
                        None,
                    ),  # Visibility controlled by permission check
                ]

                for label, path, icon, _required_role in nav_items:
                    # Admin link requires MANAGE_API_KEYS or MANAGE_SYSTEM_CONFIG or VIEW_AUDIT
                    if path == "/admin" and not any(
                        has_permission(user, p)
                        for p in (
                            Permission.MANAGE_API_KEYS,
                            Permission.MANAGE_SYSTEM_CONFIG,
                            Permission.VIEW_AUDIT,
                        )
                    ):
                        continue

                    is_active = current_path == path
                    active_classes = (
                        "bg-blue-100 text-blue-700" if is_active else "hover:bg-slate-200"
                    )

                    with ui.link(target=path).classes(f"nav-link w-full rounded {active_classes}"):
                        with ui.row().classes("items-center gap-3 p-2"):
                            ui.icon(icon).classes("text-blue-600" if is_active else "text-gray-600")
                            ui.label(label).classes("text-sm")

        # Header
        status_bar = StatusBar()

        with ui.header().classes(
            "bg-slate-900 items-center text-white px-4 h-14 flex-nowrap overflow-x-auto"
        ):
            ui.button(icon="menu", on_click=lambda: drawer.toggle()).props("flat color=white")
            ui.label("Trading Console").classes("text-xl font-bold ml-2 leading-none shrink-0")

            # Header metrics (NLV, Leverage, Day Change) - P6T2
            header_metrics = HeaderMetrics()

            latency_monitor = LatencyMonitor()
            latency_badge = (
                ui.label("--")
                .classes(
                    "h-6 px-2 py-0.5 rounded text-xs font-medium bg-gray-600 text-white shrink-0"
                )
                .props("id=latency-badge")
            )
            latency_badge.tooltip("API Latency: --")

            market_clock = MarketClock(exchanges=["NYSE"])

            quiet_mode_btn: ui.button | None = None

            async def toggle_quiet_mode() -> None:
                enabled = not notification_router.quiet_mode
                await notification_router.set_quiet_mode(enabled)
                if quiet_mode_btn:
                    quiet_mode_btn.props(
                        f"icon={'notifications_off' if enabled else 'notifications_active'}"
                    )
                    quiet_mode_btn.tooltip("Quiet Mode: ON" if enabled else "Quiet Mode: OFF")

            initial_icon = (
                "notifications_off" if notification_router.quiet_mode else "notifications_active"
            )
            initial_tooltip = (
                "Quiet Mode: ON" if notification_router.quiet_mode else "Quiet Mode: OFF"
            )
            quiet_mode_btn = ui.button(icon=initial_icon, on_click=toggle_quiet_mode).props(
                "flat color=white"
            )
            quiet_mode_btn.tooltip(initial_tooltip)

            ui.space()

            with ui.row().classes("gap-2 items-center flex-nowrap h-10 shrink-0 overflow-x-auto"):
                kill_switch_button = (
                    ui.button(
                        "KILL SWITCH: UNKNOWN",
                    )
                    .classes(
                        "h-8 px-3 py-1 rounded text-sm font-medium bg-yellow-500 text-black shrink-0"
                    )
                    .props("id=kill-switch-badge unelevated")
                )
                engage_button = (
                    ui.button(
                        "Engage",
                        icon="power_settings_new",
                        on_click=lambda: open_kill_switch_dialog("ENGAGE"),
                    )
                    .classes("h-8 px-2 py-1 rounded text-xs bg-red-600 text-white shrink-0")
                    .props("id=kill-switch-engage")
                )
                disengage_button = (
                    ui.button(
                        "Disengage",
                        icon="power_off",
                        on_click=lambda: open_kill_switch_dialog("DISENGAGE"),
                    )
                    .classes("h-8 px-2 py-1 rounded text-xs bg-green-600 text-white shrink-0")
                    .props("id=kill-switch-disengage")
                )
                circuit_breaker_badge = (
                    ui.label("Circuit: Unknown")
                    .classes(
                        "h-8 px-3 py-1 rounded text-sm font-medium bg-yellow-500 text-black flex items-center shrink-0"
                    )
                    .props("id=circuit-breaker-badge")
                )
                # Connection status is derived from API polling + latency monitoring.
                connection_badge = (
                    ui.label("Connected")
                    .classes(
                        "h-8 px-2 py-1 rounded text-xs bg-green-500 text-white flex items-center shrink-0"
                    )
                    .props("id=connection-badge")
                )

                log_drawer = LogDrawer(notification_router)
                log_drawer.create()

                with ui.row().classes("items-center gap-2"):
                    ui.label(user_name).classes("text-sm")
                    ui.badge(user_role).classes("bg-blue-500 text-white")

                async def logout() -> None:
                    # Fire-and-forget logout. Handle redirect fully in the browser
                    # to avoid server-side JS timeouts.
                    try:
                        ui.run_javascript(
                            """
                            (async () => {
                              const getCookie = (name) => {
                                const match = document.cookie
                                  .split('; ')
                                  .find((row) => row.startsWith(`${name}=`));
                                return match ? match.split('=')[1] : '';
                              };
                              const csrf = getCookie('ng_csrf');
                              try {
                                const resp = await fetch('/auth/logout', {
                                  method: 'POST',
                                  headers: { 'X-CSRF-Token': csrf || '' },
                                });
                                let logoutUrl = null;
                                if (resp.ok) {
                                  const data = await resp.json().catch(() => null);
                                  logoutUrl = data && data.logout_url ? data.logout_url : null;
                                }
                                window.location.href = logoutUrl || '/login';
                              } catch (e) {
                                window.location.href = '/login';
                              }
                            })();
                            """
                        )
                    except (RuntimeError, TimeoutError) as e:
                        logger.warning(
                            "Logout JavaScript execution failed",
                            extra={
                                "user_id": user_id,
                                "error": str(e),
                                "error_type": type(e).__name__,
                            },
                            exc_info=True,
                        )
                        ui.notify("Logout failed. Please try again.", type="negative")

                ui.button(icon="logout", on_click=logout).props("flat color=white").tooltip(
                    "Logout"
                )

        # Main content area
        with ui.column().classes("w-full p-2 bg-surface-0 min-h-screen text-text-primary"):
            await page_func(*args, **kwargs)

        last_kill_switch_state: str | None = None
        kill_switch_state: str | None = None
        kill_switch_action_in_progress = False
        connection_monitor = ConnectionMonitor()
        last_connection_state: str | None = None
        last_read_only: bool | None = None

        kill_switch_action_buttons: list[Any] = [engage_button, disengage_button]

        def set_kill_switch_controls(state: str | None) -> None:
            if kill_switch_action_in_progress:
                for button in kill_switch_action_buttons:
                    button.disable()
                return
            if state == "ENGAGED":
                engage_button.disable()
                disengage_button.enable()
            elif state == "DISENGAGED":
                engage_button.enable()
                disengage_button.disable()
            else:
                engage_button.enable()
                disengage_button.enable()

        async def perform_kill_switch_action(action: str, reason: str) -> None:
            nonlocal kill_switch_action_in_progress
            if kill_switch_action_in_progress:
                return
            kill_switch_action_in_progress = True
            for button in kill_switch_action_buttons:
                button.disable()
            try:
                if action == "ENGAGE":
                    await client.engage_kill_switch(
                        user_id,
                        reason=reason,
                        role=user_role,
                        strategies=user_strategies,
                    )
                    ui.notify("Kill switch engaged", type="negative")
                else:
                    await client.disengage_kill_switch(
                        user_id,
                        role=user_role,
                        strategies=user_strategies,
                        notes=reason,
                    )
                    ui.notify("Kill switch disengaged", type="positive")
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 400:
                    ui.notify("Kill switch already in requested state", type="warning")
                else:
                    ui.notify(
                        f"Kill switch action failed: HTTP {exc.response.status_code}",
                        type="negative",
                    )
            except httpx.RequestError:
                ui.notify("Kill switch action failed: network error", type="negative")
            finally:
                await update_global_status()
                kill_switch_action_in_progress = False
                set_kill_switch_controls(kill_switch_state)

        def open_kill_switch_dialog(action: str) -> None:
            title = "Engage Kill Switch" if action == "ENGAGE" else "Disengage Kill Switch"
            with ui.dialog() as dialog, ui.card().classes("p-6 min-w-[420px]"):
                ui.label(title).classes("text-lg font-semibold")
                ui.label("Provide a reason/note for audit logging.").classes(
                    "text-sm text-gray-600"
                )
                reason_input = ui.input("Reason / Notes").props("autofocus").classes("w-full")
                confirm_input = None
                if action == "DISENGAGE":
                    ui.label("Type RESUME to confirm trading will resume.").classes(
                        "text-sm text-gray-600 mt-2"
                    )
                    confirm_input = ui.input("Type RESUME to confirm").classes("w-full")
                error_label = ui.label("").classes("text-xs text-red-600")

                async def confirm() -> None:
                    reason = str(reason_input.value or "").strip()
                    if not reason:
                        error_label.set_text("Reason is required.")
                        return
                    if action == "DISENGAGE" and confirm_input is not None:
                        confirmation = str(confirm_input.value or "").strip().upper()
                        if confirmation != "RESUME":
                            error_label.set_text("Type RESUME to confirm.")
                            return
                    dialog.close()
                    await perform_kill_switch_action(action, reason)

                with ui.row().classes("justify-end gap-2"):
                    ui.button("Cancel", on_click=dialog.close).props("flat")
                    ui.button(
                        "Engage" if action == "ENGAGE" else "Disengage",
                        on_click=confirm,
                    ).props("color=negative" if action == "ENGAGE" else "color=positive")
            dialog.open()

        def dispatch_trading_state(update: dict[str, Any]) -> None:
            try:
                payload = json.dumps(update)
                ui.run_javascript(
                    f"window.dispatchEvent(new CustomEvent('trading_state_change', {{ detail: {payload} }}));"
                )
            except Exception as exc:
                logger.debug(
                    "trading_state_dispatch_failed",
                    extra={"user_id": user_id, "error": type(exc).__name__},
                )

        status_poll_lock = asyncio.Lock()

        def reset_latency_badge() -> None:
            """Reset latency badge to disconnected state."""
            latency_badge.set_text("--")
            latency_badge.classes(
                add=LATENCY_DISCONNECTED,
                remove=LATENCY_BADGE_REMOVE_CLASSES,
            )
            latency_badge.tooltip("API Latency: --")

        def sync_connection_state() -> None:
            nonlocal last_connection_state, last_read_only
            state_value = connection_monitor.get_connection_state().value
            read_only = connection_monitor.is_read_only()
            connection_badge.set_text(connection_monitor.get_badge_text())
            connection_badge.classes(
                connection_monitor.get_badge_class(),
                remove=CONNECTION_BADGE_REMOVE_CLASSES,
            )
            app.storage.user["connection_state"] = state_value
            app.storage.user["read_only"] = read_only
            if state_value != last_connection_state or read_only != last_read_only:
                dispatch_trading_state(
                    {
                        "connectionState": state_value,
                        "readOnly": read_only,
                    }
                )
                last_connection_state = state_value
                last_read_only = read_only

        async def update_global_status() -> None:
            nonlocal last_kill_switch_state, kill_switch_state
            if status_poll_lock.locked():
                return
            async with status_poll_lock:
                if not connection_monitor.should_attempt():
                    connection_monitor.start_reconnect()
                    reset_latency_badge()
                    try:
                        market_clock.update()
                    except Exception as e:
                        logger.debug(
                            "Market clock update failed",
                            extra={"user_id": user_id, "error": type(e).__name__},
                        )
                    if header_metrics.is_stale():
                        header_metrics.mark_stale()
                    sync_connection_state()
                    return

                status_success = False
                try:
                    # Pass full auth context for production with INTERNAL_TOKEN_SECRET
                    status = await client.fetch_kill_switch_status(
                        user_id, role=user_role, strategies=user_strategies
                    )
                    state = str(status.get("state", "UNKNOWN")).upper()
                    kill_switch_state = state if state else "UNKNOWN"
                    try:
                        cb_status = await client.fetch_circuit_breaker_status(
                            user_id, role=user_role, strategies=user_strategies
                        )
                        cb_state = str(cb_status.get("state", "UNKNOWN")).upper()
                    except (
                        httpx.HTTPStatusError,
                        httpx.RequestError,
                        ValueError,
                        KeyError,
                        TypeError,
                    ) as e:
                        # Circuit breaker status fetch failed - fallback to UNKNOWN and continue
                        logger.warning(
                            "Circuit breaker status fetch failed",
                            extra={
                                "user_id": user_id,
                                "error": str(e),
                                "error_type": type(e).__name__,
                            },
                        )
                        cb_state = "UNKNOWN"

                    if state == "ENGAGED":
                        kill_switch_button.set_text("KILL SWITCH: ENGAGED")
                        kill_switch_button.classes(
                            "bg-red-500 text-white",
                            remove="bg-green-500 bg-yellow-500 text-black",
                        )
                        if last_kill_switch_state != "ENGAGED":
                            ui.notify("Kill switch engaged", type="negative")
                    elif state == "DISENGAGED":
                        # Only show "TRADING ACTIVE" for explicit DISENGAGED state
                        kill_switch_button.set_text("KILL SWITCH: DISENGAGED")
                        kill_switch_button.classes(
                            "bg-green-500 text-white",
                            remove="bg-red-500 bg-yellow-500 text-black",
                        )
                    else:
                        # Unknown/invalid state - show warning
                        kill_switch_button.set_text(f"KILL SWITCH: {state}")
                        kill_switch_button.classes(
                            "bg-yellow-500 text-black",
                            remove="bg-red-500 bg-green-500 text-white",
                        )
                    status_bar.update_state(state)

                    if cb_state == "TRIPPED":
                        circuit_breaker_badge.set_text("CIRCUIT TRIPPED")
                        circuit_breaker_badge.classes(
                            "bg-red-500 text-white",
                            remove="bg-green-500 bg-yellow-500 text-black",
                        )
                    elif cb_state == "OPEN":
                        circuit_breaker_badge.set_text("CIRCUIT OK")
                        circuit_breaker_badge.classes(
                            "bg-green-500 text-white",
                            remove="bg-red-500 bg-yellow-500 text-black",
                        )
                    elif cb_state == "QUIET_PERIOD":
                        circuit_breaker_badge.set_text("CIRCUIT QUIET PERIOD")
                        circuit_breaker_badge.classes(
                            "bg-yellow-500 text-black",
                            remove="bg-red-500 bg-green-500 text-white",
                        )
                    else:
                        circuit_breaker_badge.set_text(f"CIRCUIT: {cb_state}")
                        circuit_breaker_badge.classes(
                            "bg-yellow-500 text-black",
                            remove="bg-red-500 bg-green-500 text-white",
                        )

                    last_kill_switch_state = state
                    set_kill_switch_controls(state)
                    status_success = True
                except (ValueError, KeyError, TypeError, ConnectionError, httpx.HTTPError) as e:
                    logger.warning(
                        "Kill switch status update failed",
                        extra={
                            "user_id": user_id,
                            "error": str(e),
                            "error_type": type(e).__name__,
                        },
                        exc_info=True,
                    )
                    kill_switch_state = "UNKNOWN"
                    kill_switch_button.set_text("STATUS UNKNOWN")
                    kill_switch_button.classes(
                        "bg-yellow-500 text-black",
                        remove="bg-red-500 bg-green-500",
                    )
                    status_bar.update_state("UNKNOWN")
                    set_kill_switch_controls("UNKNOWN")
                    circuit_breaker_badge.set_text("CIRCUIT: UNKNOWN")
                    circuit_breaker_badge.classes(
                        "bg-yellow-500 text-black",
                        remove="bg-red-500 bg-green-500 text-white",
                    )

                if status_success:
                    connection_monitor.record_success()
                else:
                    connection_monitor.record_failure()
                    connection_monitor.start_reconnect()

                # ISOLATED: Latency update - errors here should not affect kill switch/connection
                try:
                    latency_ms = await latency_monitor.measure()
                    if status_success and latency_ms is not None:
                        connection_monitor.record_latency(latency_ms)
                    latency_badge.set_text(latency_monitor.format_display())
                    latency_badge.classes(
                        latency_monitor.get_status_color_class(),
                        remove=LATENCY_BADGE_REMOVE_CLASSES,
                    )
                    latency_badge.tooltip(latency_monitor.format_tooltip())
                except Exception as e:
                    logger.warning(
                        "Latency monitor update failed",
                        extra={"user_id": user_id, "error": type(e).__name__},
                    )
                    reset_latency_badge()

                try:
                    market_clock.update()
                except Exception as e:
                    logger.debug(
                        "Market clock update failed",
                        extra={"user_id": user_id, "error": type(e).__name__},
                    )

                sync_connection_state()

                # ISOLATED: Header metrics update - errors here NEVER affect kill switch/connection
                # This try/except is separate from the kill switch block above
                try:
                    await header_metrics.update(
                        client, user_id, role=user_role, strategies=user_strategies
                    )
                except Exception as e:
                    # Metrics errors only mark metrics stale, handled internally
                    # Log at debug level since HeaderMetrics already logs warnings
                    logger.debug(
                        "Header metrics update exception (handled internally)",
                        extra={"user_id": user_id, "error": str(e)},
                    )

        # Create timer for global status polling
        status_timer = ui.timer(5.0, update_global_status)
        await update_global_status()

        # Register cleanup on client disconnect to prevent timer leaks
        cleanup_id = get_or_create_client_id()
        if cleanup_id:
            lifecycle_mgr = ClientLifecycleManager.get()
            await lifecycle_mgr.register_cleanup_callback(cleanup_id, lambda: status_timer.cancel())

        async def log_grid_metrics() -> None:
            """Periodic metrics logging for all grids."""
            for (_grid_id, _session_id), monitor in get_all_monitors().items():
                monitor.log_metrics()

        metrics_timer = ui.timer(60.0, log_grid_metrics)
        if cleanup_id:
            lifecycle_mgr = ClientLifecycleManager.get()
            await lifecycle_mgr.register_cleanup_callback(
                cleanup_id, lambda: metrics_timer.cancel()
            )
            # Close latency monitor's persistent HTTP client on session cleanup
            await lifecycle_mgr.register_cleanup_callback(cleanup_id, latency_monitor.close)

    return wrapper


__all__ = ["main_layout"]
