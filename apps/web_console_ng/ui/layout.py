"""Shared page layout for the NiceGUI trading console."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

import httpx
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
        ui.add_head_html(
            """
            <script>
            (function () {
              if (window.__tpTradingStateListenerAdded) return;
              window.__tpTradingStateListenerAdded = true;
              window.addEventListener('trading_state_change', (event) => {
                const detail = (event && event.detail) || {};
                const killSwitch = detail.killSwitch;
                const killSwitchState = detail.killSwitchState;
                const circuitBreaker = detail.circuitBreaker;
                const circuitBreakerState = detail.circuitBreakerState;
                const ksEl = document.getElementById('kill-switch-badge');
                if (ksEl) {
                  if (typeof killSwitchState === 'string') {
                    const state = killSwitchState.toUpperCase();
                    if (state === 'ENGAGED') {
                      ksEl.textContent = 'KILL SWITCH ENGAGED';
                      ksEl.classList.add('bg-red-500', 'text-white');
                      ksEl.classList.remove('bg-green-500', 'bg-yellow-500', 'text-black');
                    } else if (state === 'DISENGAGED') {
                      ksEl.textContent = 'TRADING ACTIVE';
                      ksEl.classList.add('bg-green-500', 'text-white');
                      ksEl.classList.remove('bg-red-500', 'bg-yellow-500', 'text-black');
                    } else {
                      ksEl.textContent = `STATE: ${state || 'UNKNOWN'}`;
                      ksEl.classList.add('bg-yellow-500', 'text-black');
                      ksEl.classList.remove('bg-red-500', 'bg-green-500', 'text-white');
                    }
                  } else if (typeof killSwitch === 'boolean') {
                    if (killSwitch) {
                      ksEl.textContent = 'KILL SWITCH ENGAGED';
                      ksEl.classList.add('bg-red-500', 'text-white');
                      ksEl.classList.remove('bg-green-500', 'bg-yellow-500', 'text-black');
                    } else {
                      ksEl.textContent = 'TRADING ACTIVE';
                      ksEl.classList.add('bg-green-500', 'text-white');
                      ksEl.classList.remove('bg-red-500', 'bg-yellow-500', 'text-black');
                    }
                  }
                }
                const cbEl = document.getElementById('circuit-breaker-badge');
                if (cbEl) {
                  if (typeof circuitBreakerState === 'string') {
                    const state = circuitBreakerState.toUpperCase();
                    if (state === 'TRIPPED') {
                      cbEl.textContent = 'CIRCUIT TRIPPED';
                      cbEl.classList.add('bg-red-500', 'text-white');
                      cbEl.classList.remove('bg-green-500', 'bg-yellow-500', 'text-black');
                    } else if (state === 'OPEN') {
                      cbEl.textContent = 'CIRCUIT OK';
                      cbEl.classList.add('bg-green-500', 'text-white');
                      cbEl.classList.remove('bg-red-500', 'bg-yellow-500', 'text-black');
                    } else if (state === 'QUIET_PERIOD') {
                      cbEl.textContent = 'CIRCUIT QUIET PERIOD';
                      cbEl.classList.add('bg-yellow-500', 'text-black');
                      cbEl.classList.remove('bg-red-500', 'bg-green-500', 'text-white');
                    } else {
                      cbEl.textContent = `CIRCUIT: ${state || 'UNKNOWN'}`;
                      cbEl.classList.add('bg-yellow-500', 'text-black');
                      cbEl.classList.remove('bg-red-500', 'bg-green-500', 'text-white');
                    }
                  } else if (typeof circuitBreaker === 'boolean') {
                    if (circuitBreaker) {
                      cbEl.textContent = 'CIRCUIT TRIPPED';
                      cbEl.classList.add('bg-red-500', 'text-white');
                      cbEl.classList.remove('bg-green-500', 'bg-yellow-500', 'text-black');
                    } else {
                      cbEl.textContent = 'CIRCUIT OK';
                      cbEl.classList.add('bg-green-500', 'text-white');
                      cbEl.classList.remove('bg-red-500', 'bg-yellow-500', 'text-black');
                    }
                  }
                }
              });
            })();
            </script>
            """
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

        client = AsyncTradingClient.get()

        # Left drawer (sidebar)
        drawer = ui.left_drawer(value=True).classes("bg-slate-100 w-64")
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
                kill_switch_button = ui.button(
                    "KILL SWITCH: UNKNOWN",
                ).classes("px-3 py-1 rounded text-sm font-medium bg-yellow-500 text-black").props(
                    "id=kill-switch-badge unelevated"
                )
                circuit_breaker_badge = ui.label("Circuit: Unknown").classes(
                    "px-3 py-1 rounded text-sm font-medium bg-yellow-500 text-black"
                ).props("id=circuit-breaker-badge")
                # Connection status is derived from kill-switch polling, not a websocket heartbeat.
                connection_badge = ui.label("Connected").classes(
                    "px-2 py-1 rounded text-xs bg-green-500 text-white"
                ).props("id=connection-badge")

                with ui.row().classes("items-center gap-2"):
                    ui.label(user_name).classes("text-sm")
                    ui.badge(user_role).classes("bg-blue-500 text-white")

                async def logout() -> None:
                    # Fire-and-forget logout. Handle redirect fully in the browser
                    # to avoid server-side JS timeouts.
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

                ui.button(icon="logout", on_click=logout).props("flat color=white").tooltip(
                    "Logout"
                )

        # Main content area
        with ui.column().classes("w-full p-6 bg-gray-50 min-h-screen"):
            await page_func(*args, **kwargs)

        last_kill_switch_state: str | None = None
        kill_switch_state: str | None = None
        kill_switch_action_in_progress = False

        async def toggle_kill_switch() -> None:
            """Toggle kill switch state immediately without confirmation dialog.

            DESIGN DECISION (Dev Team): The kill switch is intentionally designed for
            FAST emergency response. In production incidents (runaway orders, market
            flash crash, broker connectivity issues), every second counts. A confirmation
            dialog would add critical delay when operators need to halt trading immediately.

            The trade-off is reduced auditability for the toggle reason, but this is
            acceptable because:
            1. All kill switch state changes are logged server-side with timestamps
            2. The Circuit Breaker page provides detailed controls for non-emergency use
            3. Incident post-mortems can correlate timing with system logs
            4. The header toggle is a "panic button" - detailed reasons can be added later

            For detailed audit trails and reason capture, use the Circuit Breaker page.
            """
            nonlocal kill_switch_action_in_progress, kill_switch_state
            if kill_switch_action_in_progress:
                return
            kill_switch_action_in_progress = True
            kill_switch_button.disable()
            try:
                try:
                    status = await client.fetch_kill_switch_status(
                        user_id, role=user_role, strategies=user_strategies
                    )
                    state = str(status.get("state", "UNKNOWN")).upper()
                    kill_switch_state = state
                except httpx.HTTPStatusError as exc:
                    ui.notify(
                        f"Kill switch status failed: HTTP {exc.response.status_code}",
                        type="negative",
                    )
                    state = "UNKNOWN"
                except httpx.RequestError:
                    ui.notify("Kill switch status failed: network error", type="negative")
                    state = "UNKNOWN"
                if state == "ENGAGED":
                    await client.disengage_kill_switch(
                        user_id, role=user_role, strategies=user_strategies
                    )
                    ui.notify("Kill switch disengaged", type="positive")
                elif state == "DISENGAGED":
                    await client.engage_kill_switch(
                        user_id,
                        reason="Emergency header toggle",
                        role=user_role,
                        strategies=user_strategies,
                    )
                    ui.notify("Kill switch engaged", type="negative")
                else:
                    ui.notify("Kill switch state unknown; refresh status", type="warning")
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
                kill_switch_button.enable()

        kill_switch_button.on_click(toggle_kill_switch)

        async def update_global_status() -> None:
            nonlocal last_kill_switch_state, kill_switch_state
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
                except Exception:
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

                connection_badge.set_text("Connected")
                connection_badge.classes(
                    "bg-green-500 text-white",
                    remove="bg-red-500",
                )
                last_kill_switch_state = state
            except Exception:
                kill_switch_button.set_text("STATUS UNKNOWN")
                kill_switch_button.classes(
                    "bg-yellow-500 text-black",
                    remove="bg-red-500 bg-green-500",
                )
                circuit_breaker_badge.set_text("CIRCUIT: UNKNOWN")
                circuit_breaker_badge.classes(
                    "bg-yellow-500 text-black",
                    remove="bg-red-500 bg-green-500 text-white",
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
