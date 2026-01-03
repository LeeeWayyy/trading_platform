"""Kill Switch Management page for NiceGUI web console."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from nicegui import Client, ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.core.audit import audit_log
from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from apps.web_console_ng.core.realtime import RealtimeUpdater, kill_switch_channel
from apps.web_console_ng.ui.layout import main_layout

logger = logging.getLogger(__name__)


@ui.page("/kill-switch")
@requires_auth
@main_layout
async def kill_switch_page(client: Client) -> None:
    """Kill switch management page with real-time status and safety confirmations."""
    trading_client = AsyncTradingClient.get()
    user = get_current_user()
    user_id = str(user.get("user_id") or user.get("username") or "").strip()
    user_role = str(user.get("role") or "viewer")

    if not user_id:
        logger.warning("kill_switch_missing_user_id")
        ui.notify("Session expired - please log in again", type="negative")
        ui.navigate.to("/login")
        return

    # Check permission
    if user_role == "viewer":
        ui.notify("Viewers cannot manage kill switch", type="negative")
        ui.navigate.to("/")
        return

    client_id = client.storage.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        ui.notify("Session error - please refresh", type="negative")
        return

    realtime = RealtimeUpdater(client_id, client)
    lifecycle = ClientLifecycleManager.get()
    action_in_progress = False
    current_state = "UNKNOWN"

    with ui.card().classes("w-full max-w-lg mx-auto"):
        ui.label("Kill Switch Management").classes("text-2xl font-bold mb-4")

        # Status display
        status_container = ui.column().classes("w-full items-center p-6 rounded-lg mb-6")
        with status_container:
            status_label = ui.label("Loading...").classes("text-4xl font-bold")
            status_desc = ui.label("Checking kill switch status...").classes("text-gray-600")

        # Action buttons
        with ui.row().classes("w-full gap-4 justify-center"):
            engage_btn = ui.button("ENGAGE Kill Switch", color="red").classes("text-white px-6 py-3")
            disengage_btn = ui.button("Disengage Kill Switch", color="green").classes("text-white px-6 py-3")

    def update_status_display(state: str) -> None:
        nonlocal current_state
        current_state = state.upper()

        if current_state == "ENGAGED":
            status_container.classes(remove="bg-green-100 bg-yellow-100", add="bg-red-100")
            status_label.set_text("ENGAGED")
            status_label.classes(remove="text-green-600 text-yellow-600", add="text-red-600")
            status_desc.set_text("Trading is HALTED - no new orders will be accepted")
            engage_btn.disable()
            disengage_btn.enable()
        elif current_state == "DISENGAGED":
            status_container.classes(remove="bg-red-100 bg-yellow-100", add="bg-green-100")
            status_label.set_text("DISENGAGED")
            status_label.classes(remove="text-red-600 text-yellow-600", add="text-green-600")
            status_desc.set_text("Trading is active - orders are being accepted")
            engage_btn.enable()
            disengage_btn.disable()
        else:
            # Unknown state - show warning and disable both buttons until confirmed
            status_container.classes(remove="bg-red-100 bg-green-100", add="bg-yellow-100")
            status_label.set_text("UNKNOWN")
            status_label.classes(remove="text-red-600 text-green-600", add="text-yellow-600")
            status_desc.set_text("Kill switch status unknown - verify backend connection")
            engage_btn.disable()
            disengage_btn.disable()

    async def load_initial_status() -> None:
        try:
            ks_status = await trading_client.fetch_kill_switch_status(
                user_id, role=user_role
            )
            update_status_display(ks_status.get("state", "UNKNOWN"))
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "kill_switch_status_failed",
                extra={"user_id": user_id, "status": exc.response.status_code},
            )
            ui.notify(f"Failed to load status: HTTP {exc.response.status_code}", type="negative")
            update_status_display("UNKNOWN")
        except httpx.RequestError as exc:
            logger.warning(
                "kill_switch_status_failed",
                extra={"user_id": user_id, "error": type(exc).__name__},
            )
            ui.notify("Failed to load status: network error", type="negative")
            update_status_display("UNKNOWN")

    await load_initial_status()

    async def on_kill_switch_update(data: dict[str, Any]) -> None:
        state = str(data.get("state", "")).upper()
        # Update display for any state (ENGAGED, DISENGAGED, or unknown)
        update_status_display(state if state else "UNKNOWN")

    await realtime.subscribe(kill_switch_channel(), on_kill_switch_update)

    async def show_engage_dialog() -> None:
        nonlocal action_in_progress
        if action_in_progress:
            return

        # Disable button to prevent double-open
        engage_btn.disable()

        with ui.dialog() as dialog, ui.card().classes("p-6 min-w-[400px]"):

            def on_dialog_close() -> None:
                if not action_in_progress:
                    # Re-apply button states based on current state (handles UNKNOWN)
                    update_status_display(current_state)

            dialog.on("close", on_dialog_close)
            ui.label("Engage Kill Switch?").classes("text-xl font-bold text-red-600 mb-4")
            ui.label(
                "This will immediately HALT all trading. No new orders will be accepted."
            ).classes("text-red-600 mb-4")

            reason_input = ui.textarea(
                "Reason (required)",
                placeholder="Why are you engaging the kill switch? (min 10 chars)",
                validation={"Min 10 characters": lambda v: bool(v and len(v.strip()) >= 10)},
            ).classes("w-full mb-4")

            with ui.row().classes("gap-4 justify-end"):
                async def confirm_engage() -> None:
                    nonlocal action_in_progress
                    if action_in_progress:
                        return

                    reason = (reason_input.value or "").strip()
                    if len(reason) < 10:
                        ui.notify("Reason must be at least 10 characters", type="warning")
                        return

                    action_in_progress = True
                    confirm_btn.disable()
                    engage_btn.disable()
                    disengage_btn.disable()

                    try:
                        await trading_client.engage_kill_switch(
                            user_id,
                            reason=reason,
                            role=user_role,
                        )

                        audit_log(
                            action="kill_switch_engaged",
                            user_id=user_id,
                            details={"reason": reason},
                        )

                        ui.notify("Kill Switch ENGAGED", type="positive")
                        update_status_display("ENGAGED")
                        dialog.close()

                    except httpx.HTTPStatusError as exc:
                        logger.warning(
                            "kill_switch_engage_failed",
                            extra={"user_id": user_id, "status": exc.response.status_code},
                        )
                        audit_log(
                            action="kill_switch_engage_failed",
                            user_id=user_id,
                            details={"error": f"HTTP {exc.response.status_code}"},
                        )
                        ui.notify(f"Failed to engage: HTTP {exc.response.status_code}", type="negative")
                    except httpx.RequestError as exc:
                        logger.warning(
                            "kill_switch_engage_failed",
                            extra={"user_id": user_id, "error": type(exc).__name__},
                        )
                        audit_log(
                            action="kill_switch_engage_failed",
                            user_id=user_id,
                            details={"error": type(exc).__name__},
                        )
                        ui.notify("Failed to engage: network error", type="negative")
                    finally:
                        action_in_progress = False
                        confirm_btn.enable()
                        # Re-apply button states based on current state (handles UNKNOWN)
                        update_status_display(current_state)

                confirm_btn = ui.button("ENGAGE", on_click=confirm_engage, color="red").classes("text-white")
                ui.button("Cancel", on_click=dialog.close)

        dialog.open()

    async def show_disengage_dialog() -> None:
        nonlocal action_in_progress
        if action_in_progress:
            return

        # Check admin permission
        if user_role != "admin":
            ui.notify("Admin permission required to disengage kill switch", type="negative")
            return

        # Disable button to prevent double-open
        disengage_btn.disable()

        # First confirmation dialog
        with ui.dialog() as dialog1, ui.card().classes("p-6 min-w-[450px]"):

            def on_dialog1_close() -> None:
                if not action_in_progress:
                    # Re-apply button states based on current state (handles UNKNOWN)
                    update_status_display(current_state)

            dialog1.on("close", on_dialog1_close)
            ui.label("Disengage Kill Switch?").classes("text-xl font-bold text-yellow-600 mb-4")
            ui.label(
                "This will resume trading. Are you sure the issue has been resolved?"
            ).classes("mb-4")

            notes_input = ui.textarea(
                "Resolution Notes (required)",
                placeholder="What was the issue and how was it resolved? (min 10 chars)",
                validation={"Min 10 characters": lambda v: bool(v and len(v.strip()) >= 10)},
            ).classes("w-full mb-4")

            with ui.row().classes("gap-4 justify-end"):
                async def proceed_to_confirm() -> None:
                    notes = (notes_input.value or "").strip()
                    if len(notes) < 10:
                        ui.notify("Notes must be at least 10 characters", type="warning")
                        return
                    dialog1.close()
                    await show_second_confirmation(notes)

                ui.button("Proceed", on_click=proceed_to_confirm, color="yellow").classes("text-black")
                ui.button("Cancel", on_click=dialog1.close)

        dialog1.open()

    async def show_second_confirmation(notes: str) -> None:
        nonlocal action_in_progress

        # Two-factor confirmation
        with ui.dialog() as dialog2, ui.card().classes("p-6 min-w-[400px]"):
            ui.label("FINAL CONFIRMATION").classes("text-xl font-bold text-red-600 mb-4")
            ui.label("Type CONFIRM to disengage the kill switch:").classes("mb-4")

            confirm_input = ui.input("Type CONFIRM").classes("w-full mb-4 font-mono")

            with ui.row().classes("gap-4 justify-end"):
                async def execute_disengage() -> None:
                    nonlocal action_in_progress
                    if action_in_progress:
                        return

                    if confirm_input.value != "CONFIRM":
                        ui.notify("Type CONFIRM exactly to proceed", type="warning")
                        return

                    action_in_progress = True
                    disengage_confirm_btn.disable()
                    engage_btn.disable()
                    disengage_btn.disable()

                    try:
                        await trading_client.disengage_kill_switch(
                            user_id,
                            role=user_role,
                            notes=notes,
                        )

                        audit_log(
                            action="kill_switch_disengaged",
                            user_id=user_id,
                            details={"notes": notes},
                        )

                        ui.notify("Kill Switch DISENGAGED - trading resumed", type="positive")
                        update_status_display("DISENGAGED")
                        dialog2.close()

                    except httpx.HTTPStatusError as exc:
                        logger.warning(
                            "kill_switch_disengage_failed",
                            extra={"user_id": user_id, "status": exc.response.status_code},
                        )
                        audit_log(
                            action="kill_switch_disengage_failed",
                            user_id=user_id,
                            details={"error": f"HTTP {exc.response.status_code}"},
                        )
                        ui.notify(f"Failed to disengage: HTTP {exc.response.status_code}", type="negative")
                    except httpx.RequestError as exc:
                        logger.warning(
                            "kill_switch_disengage_failed",
                            extra={"user_id": user_id, "error": type(exc).__name__},
                        )
                        audit_log(
                            action="kill_switch_disengage_failed",
                            user_id=user_id,
                            details={"error": type(exc).__name__},
                        )
                        ui.notify("Failed to disengage: network error", type="negative")
                    finally:
                        action_in_progress = False
                        disengage_confirm_btn.enable()
                        # Re-apply button states based on current state (handles UNKNOWN)
                        update_status_display(current_state)

                disengage_confirm_btn = ui.button("DISENGAGE", on_click=execute_disengage, color="green").classes("text-white")
                ui.button("Cancel", on_click=dialog2.close)

        dialog2.open()

    engage_btn.on("click", show_engage_dialog)
    disengage_btn.on("click", show_disengage_dialog)

    await lifecycle.register_cleanup_callback(client_id, realtime.cleanup)


__all__ = ["kill_switch_page"]
