"""Shared page layout for the NiceGUI trading console."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from nicegui import app, ui

from apps.web_console_ng.auth.middleware import get_current_user
from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from libs.web_console_auth.permissions import Permission, has_permission

AsyncPage = Callable[..., Awaitable[Any]]


def main_layout(page_func: AsyncPage) -> AsyncPage:
    """Decorator for consistent page layout with header, sidebar, and content."""

    @wraps(page_func)
    async def wrapper(*args: Any, **kwargs: Any) -> None:
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

        client = AsyncTradingClient.get()

        # Left drawer (sidebar)
        drawer = ui.left_drawer(value=True).classes("bg-slate-100 w-64")
        with drawer:
            with ui.column().classes("w-full gap-1 p-3"):
                ui.label("Navigation").classes("text-gray-500 text-xs uppercase tracking-wide mb-2")

                nav_items = [
                    ("Dashboard", "/", "dashboard", None),
                    ("Manual Controls", "/manual-order", "edit", None),
                    ("Kill Switch", "/kill-switch", "warning", None),
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
                    ("Admin", "/admin", "settings", None),  # Visibility controlled by permission check
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
        with ui.header().classes("bg-slate-900 items-center text-white px-4 h-14"):
            ui.button(icon="menu", on_click=lambda: drawer.toggle()).props("flat color=white")
            ui.label("Trading Console").classes("text-xl font-bold ml-2")
            ui.space()

            with ui.row().classes("gap-4 items-center"):
                kill_switch_badge = ui.label("Checking...").classes(
                    "px-3 py-1 rounded text-sm font-medium bg-yellow-500 text-black"
                )
                # Connection status is derived from kill-switch polling, not a websocket heartbeat.
                connection_badge = ui.label("Connected").classes(
                    "px-2 py-1 rounded text-xs bg-green-500 text-white"
                )

                with ui.row().classes("items-center gap-2"):
                    ui.label(user_name).classes("text-sm")
                    ui.badge(user_role).classes("bg-blue-500 text-white")

                async def logout() -> None:
                    try:
                        result = await ui.run_javascript(
                            """
                            (async () => {
                              const getCookie = (name) => {
                                const match = document.cookie
                                  .split('; ')
                                  .find((row) => row.startsWith(`${name}=`));
                                return match ? match.split('=')[1] : '';
                              };
                              const csrf = getCookie('ng_csrf');
                              const resp = await fetch('/auth/logout', {
                                method: 'POST',
                                headers: { 'X-CSRF-Token': csrf || '' },
                              });
                              if (!resp.ok) {
                                return { error: true, status: resp.status };
                              }
                              return await resp.json();
                            })();
                            """
                        )
                    except Exception:
                        ui.notify("Logout failed. Please try again.", type="negative")
                        return

                    if isinstance(result, dict) and result.get("error"):
                        ui.notify("Logout failed. Please refresh and try again.", type="negative")
                        return

                    logout_url = None
                    if isinstance(result, dict):
                        logout_url = result.get("logout_url")
                    if logout_url:
                        ui.navigate.to(logout_url)
                        return
                    ui.navigate.to("/login")

                ui.button(icon="logout", on_click=logout).props("flat color=white").tooltip(
                    "Logout"
                )

        # Main content area
        with ui.column().classes("w-full p-6 bg-gray-50 min-h-screen"):
            await page_func(*args, **kwargs)

        last_kill_switch_state: str | None = None

        async def update_global_status() -> None:
            nonlocal last_kill_switch_state
            try:
                # Pass full auth context for production with INTERNAL_TOKEN_SECRET
                status = await client.fetch_kill_switch_status(
                    user_id, role=user_role, strategies=user_strategies
                )
                state = str(status.get("state", "UNKNOWN")).upper()

                if state == "ENGAGED":
                    kill_switch_badge.set_text("KILL SWITCH ENGAGED")
                    kill_switch_badge.classes(
                        "bg-red-500 text-white",
                        remove="bg-green-500 bg-yellow-500 text-black",
                    )
                    if last_kill_switch_state != "ENGAGED":
                        ui.notify("Kill switch engaged", type="negative")
                elif state == "DISENGAGED":
                    # Only show "TRADING ACTIVE" for explicit DISENGAGED state
                    kill_switch_badge.set_text("TRADING ACTIVE")
                    kill_switch_badge.classes(
                        "bg-green-500 text-white",
                        remove="bg-red-500 bg-yellow-500 text-black",
                    )
                else:
                    # Unknown/invalid state - show warning
                    kill_switch_badge.set_text(f"STATE: {state}")
                    kill_switch_badge.classes(
                        "bg-yellow-500 text-black",
                        remove="bg-red-500 bg-green-500 text-white",
                    )

                connection_badge.set_text("Connected")
                connection_badge.classes(
                    "bg-green-500 text-white",
                    remove="bg-red-500",
                )
                last_kill_switch_state = state
            except Exception:
                kill_switch_badge.set_text("STATUS UNKNOWN")
                kill_switch_badge.classes(
                    "bg-yellow-500 text-black",
                    remove="bg-red-500 bg-green-500",
                )
                connection_badge.set_text("Disconnected")
                connection_badge.classes(
                    "bg-red-500 text-white",
                    remove="bg-green-500",
                )

        # Create timer for global status polling
        status_timer = ui.timer(5.0, update_global_status)
        await update_global_status()

        # Register cleanup on client disconnect to prevent timer leaks
        client_id = ui.context.client.storage.get("client_id")
        if client_id:
            lifecycle_mgr = ClientLifecycleManager.get()
            await lifecycle_mgr.register_cleanup_callback(client_id, lambda: status_timer.cancel())

    return wrapper


__all__ = ["main_layout"]
