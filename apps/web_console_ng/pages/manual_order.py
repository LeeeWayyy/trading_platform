"""Manual Order Entry page for NiceGUI web console."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from nicegui import Client, app, ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.components.action_button import ActionButton, ButtonState
from apps.web_console_ng.core.audit import audit_log
from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from apps.web_console_ng.core.hotkey_manager import HotkeyManager
from apps.web_console_ng.core.realtime import RealtimeUpdater, kill_switch_channel
from apps.web_console_ng.ui.layout import main_layout

logger = logging.getLogger(__name__)


@ui.page("/manual-order")
@requires_auth
@main_layout
async def manual_order_page(client: Client) -> None:
    """Manual order entry page with kill switch safety checks."""
    trading_client = AsyncTradingClient.get()
    user = get_current_user()
    user_id = str(user.get("user_id") or user.get("username") or "").strip()
    user_role = str(user.get("role") or "viewer")

    if not user_id:
        logger.warning("manual_order_missing_user_id")
        ui.notify("Session expired - please log in again", type="negative")
        ui.navigate.to("/login")
        return

    # Check permission
    if user_role == "viewer":
        ui.notify("Viewers cannot submit orders", type="negative")
        ui.navigate.to("/")
        return

    lifecycle = ClientLifecycleManager.get()

    # Get or generate client_id (may not be set yet if WebSocket hasn't connected)
    client_id = client.storage.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        client_id = lifecycle.generate_client_id()
        client.storage["client_id"] = client_id

    realtime = RealtimeUpdater(client_id, client)
    submitting = False
    kill_switch_engaged: bool | None = None  # Real-time cached state for instant UI response
    submit_action_button: ActionButton | None = None
    submit_button_element: ui.button | None = None

    with ui.card().classes("w-full max-w-lg mx-auto").props("data-order-form"):
        ui.label("Manual Order Entry").classes("text-2xl font-bold mb-4")

        # Form fields
        symbol_input = ui.input(
            "Symbol",
            placeholder="e.g. AAPL",
            validation={"Required": lambda v: bool(v and v.strip())},
        ).classes("w-full mb-2")

        with ui.row().classes("w-full gap-4 mb-2"):
            side_select = ui.select(
                options=["buy", "sell"],
                label="Side",
                value="buy",
            ).classes("flex-1")

            qty_input = ui.number(
                "Quantity",
                value=0,
                min=1,
                step=1,
                format="%d",
                validation={"Min 1": lambda v: v is not None and v >= 1},
            ).classes("flex-1")

        with ui.row().classes("w-full gap-4 mb-2"):
            order_type_select = ui.select(
                options=["market", "limit"],
                label="Order Type",
                value="market",
            ).classes("flex-1")

            tif_select = ui.select(
                options=["day", "gtc", "ioc", "fok"],
                label="Time in Force",
                value="day",
            ).classes("flex-1")

        # Limit price - only visible for limit orders
        limit_price_container = ui.column().classes("w-full mb-2")
        with limit_price_container:
            limit_price_input = ui.number(
                "Limit Price",
                value=None,
                min=0.01,
                step=0.01,
                format="%.2f",
            ).classes("w-full")
        limit_price_container.set_visibility(False)

        def on_order_type_change(e: Any) -> None:
            limit_price_container.set_visibility(e.value == "limit")

        order_type_select.on_value_change(on_order_type_change)

        reason_input = ui.textarea(
            "Reason (required)",
            placeholder="Why are you placing this order? (min 10 chars)",
            validation={"Min 10 characters": lambda v: bool(v and len(v.strip()) >= 10)},
        ).classes("w-full mb-4")

        submit_container = ui.row().classes("w-full")

    def is_read_only_mode() -> bool:
        return bool(app.storage.user.get("read_only"))

    async def check_kill_switch(*, use_cache: bool = False) -> bool:
        """Check if kill switch is engaged. Returns True if safe to proceed.

        Args:
            use_cache: If True, use cached real-time state for instant response.
                       If False (default), perform blocking API check.

        Fails closed: unknown/missing state blocks action (safety first).
        """
        # Use cached state for instant pre-check (e.g., before showing dialog)
        if use_cache:
            if kill_switch_engaged is True:
                ui.notify("Cannot submit: Kill Switch is ENGAGED", type="negative")
                return False
            elif kill_switch_engaged is False:
                return True
            # Fall through to API check if cache is None (not yet initialized)

        # Blocking API check for confirmation or when cache unavailable
        try:
            ks_status = await trading_client.fetch_kill_switch_status(user_id, role=user_role)
            state = str(ks_status.get("state", "")).upper()
            if state == "ENGAGED":
                ui.notify("Cannot submit: Kill Switch is ENGAGED", type="negative")
                return False
            if state == "DISENGAGED":
                return True
            # Unknown state - fail closed for safety
            logger.warning(
                "kill_switch_unknown_state",
                extra={"user_id": user_id, "state": state},
            )
            ui.notify("Cannot verify kill switch: unknown state", type="negative")
            return False
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "kill_switch_check_failed",
                extra={"user_id": user_id, "status": exc.response.status_code},
            )
            ui.notify(
                f"Cannot verify kill switch: HTTP {exc.response.status_code}", type="negative"
            )
            return False
        except httpx.RequestError as exc:
            logger.warning(
                "kill_switch_check_failed",
                extra={"user_id": user_id, "error": type(exc).__name__},
            )
            ui.notify("Cannot verify kill switch: network error", type="negative")
            return False

    def validate_form() -> dict[str, Any] | None:
        """Validate form and return order data if valid."""
        symbol = (symbol_input.value or "").strip().upper()
        if not symbol:
            ui.notify("Symbol is required", type="warning")
            return None

        qty = qty_input.value
        if qty is None or qty < 1:
            ui.notify("Quantity must be at least 1", type="warning")
            return None
        if qty != int(qty):
            ui.notify("Quantity must be a whole number", type="warning")
            return None

        reason = (reason_input.value or "").strip()
        if len(reason) < 10:
            ui.notify("Reason must be at least 10 characters", type="warning")
            return None

        order_data: dict[str, Any] = {
            "symbol": symbol,
            "side": side_select.value,
            "qty": int(qty),
            "order_type": order_type_select.value,
            "time_in_force": tif_select.value,
        }

        if order_type_select.value == "limit":
            limit_price = limit_price_input.value
            if limit_price is None or limit_price <= 0:
                ui.notify("Limit price is required for limit orders", type="warning")
                return None
            order_data["limit_price"] = float(limit_price)

        return order_data

    def reset_form() -> None:
        symbol_input.value = ""
        qty_input.value = 0
        reason_input.value = ""
        order_type_select.value = "market"
        side_select.value = "buy"
        tif_select.value = "day"
        limit_price_input.value = None
        limit_price_container.set_visibility(False)

    async def show_preview() -> None:
        nonlocal submitting
        if submitting:
            if submit_action_button:
                submit_action_button.reset()
            return

        if is_read_only_mode():
            ui.notify("Read-only mode: connection lost", type="warning")
            if submit_action_button:
                submit_action_button.reset()
            return

        order_data = validate_form()
        if order_data is None:
            if submit_action_button:
                submit_action_button.reset()
            return

        # Check kill switch BEFORE showing preview using cached real-time state
        # This provides instant UI response; fresh check happens at confirmation
        if not await check_kill_switch(use_cache=True):
            if submit_action_button:
                submit_action_button.reset()
            return

        reason = (reason_input.value or "").strip()

        with ui.dialog() as dialog, ui.card().classes("p-6 min-w-[400px]"):

            def on_dialog_close() -> None:
                if not submitting and submit_button_element:
                    submit_button_element.enable()

            dialog.on("close", on_dialog_close)
            ui.label("Order Preview").classes("text-xl font-bold mb-4")

            with ui.column().classes("gap-2 mb-4"):
                ui.label(f"Symbol: {order_data['symbol']}").classes("font-mono")
                side_color = "text-green-600" if order_data["side"] == "buy" else "text-red-600"
                ui.label(f"Side: {order_data['side'].upper()}").classes(f"font-bold {side_color}")
                ui.label(f"Quantity: {order_data['qty']:,}").classes("font-mono")
                ui.label(f"Type: {order_data['order_type'].upper()}").classes("font-mono")
                if "limit_price" in order_data:
                    ui.label(f"Limit Price: ${order_data['limit_price']:.2f}").classes("font-mono")
                ui.label(f"Time in Force: {order_data['time_in_force'].upper()}").classes(
                    "font-mono"
                )
                ui.label(f"Reason: {reason}").classes("text-gray-600 text-sm")

            with ui.row().classes("gap-4 justify-end"):

                async def confirm_order() -> None:
                    nonlocal submitting
                    if submitting:
                        return

                    submitting = True
                    confirm_btn.disable()
                    if submit_button_element:
                        submit_button_element.disable()

                    try:
                        if is_read_only_mode():
                            ui.notify("Read-only mode: connection lost", type="warning")
                            submitting = False
                            confirm_btn.enable()
                            if submit_button_element:
                                submit_button_element.enable()
                            return
                        # FRESH kill switch check at confirmation time
                        if not await check_kill_switch():
                            return

                        # Submit order - backend generates deterministic client_order_id
                        # for idempotency based on order params + date
                        payload = {
                            **order_data,
                            "reason": reason,
                            "requested_by": user_id,
                            "requested_at": datetime.now(UTC).isoformat(),
                        }
                        result = await trading_client.submit_manual_order(
                            payload,
                            user_id,
                            role=user_role,
                        )

                        order_id = result.get("client_order_id", "")
                        display_id = order_id[:12] + "..." if len(order_id) > 12 else order_id

                        audit_log(
                            action="manual_order_submitted",
                            user_id=user_id,
                            details={
                                "symbol": order_data["symbol"],
                                "side": order_data["side"],
                                "qty": order_data["qty"],
                                "order_type": order_data["order_type"],
                                "client_order_id": order_id,
                                "reason": reason,
                            },
                        )

                        ui.notify(f"Order submitted: {display_id}", type="positive")
                        dialog.close()

                        # Reset form
                        reset_form()

                    except httpx.HTTPStatusError as exc:
                        error_detail = ""
                        try:
                            payload = exc.response.json()
                            detail = (
                                payload.get("detail", payload)
                                if isinstance(payload, dict)
                                else payload
                            )
                            if isinstance(detail, dict):
                                error_detail = detail.get("message") or detail.get("error") or ""
                        except (ValueError, TypeError):
                            error_detail = ""
                        logger.warning(
                            "manual_order_failed",
                            extra={
                                "user_id": user_id,
                                "symbol": order_data["symbol"],
                                "status": exc.response.status_code,
                                "detail": error_detail or None,
                            },
                        )
                        audit_log(
                            action="manual_order_failed",
                            user_id=user_id,
                            details={
                                "symbol": order_data["symbol"],
                                "error": f"HTTP {exc.response.status_code}",
                                "detail": error_detail or None,
                            },
                        )
                        if error_detail:
                            ui.notify(
                                f"Order failed: {error_detail} (HTTP {exc.response.status_code})",
                                type="negative",
                            )
                        else:
                            ui.notify(
                                f"Order failed: HTTP {exc.response.status_code}",
                                type="negative",
                            )
                    except httpx.RequestError as exc:
                        logger.warning(
                            "manual_order_failed",
                            extra={
                                "user_id": user_id,
                                "symbol": order_data["symbol"],
                                "error": type(exc).__name__,
                            },
                        )
                        audit_log(
                            action="manual_order_failed",
                            user_id=user_id,
                            details={
                                "symbol": order_data["symbol"],
                                "error": type(exc).__name__,
                            },
                        )
                        ui.notify("Order failed: network error", type="negative")
                    finally:
                        submitting = False
                        confirm_btn.enable()
                        if submit_button_element:
                            submit_button_element.enable()

                confirm_btn = ui.button("Confirm", on_click=confirm_order, color="primary")
                ui.button("Cancel", on_click=dialog.close)

        if submit_action_button:
            submit_action_button.reset()
        if submit_button_element:
            submit_button_element.disable()

        dialog.open()

    submit_action_button = ActionButton(
        "Preview Order",
        show_preview,
        color="primary",
        manual_lifecycle=True,
    )
    with submit_container:
        submit_button_element = (
            submit_action_button.create()
            .classes("w-full")
            .props(
                "data-readonly-disable=true data-readonly-tooltip='Connection lost - read-only mode'"
            )
        )

    def _register_order_form_hotkeys(
        hotkey_manager: HotkeyManager,
        quantity_input: ui.number,
        side_selector: ui.select,
        submit_button: ActionButton,
        clear_form_callback: Callable[[], None],
    ) -> None:
        """Register order form hotkey handlers."""

        def _focus_buy() -> None:
            side_selector.value = "buy"
            quantity_input.run_method("focus")

        def _focus_sell() -> None:
            side_selector.value = "sell"
            quantity_input.run_method("focus")

        hotkey_manager.register_handler("focus_buy", _focus_buy)
        hotkey_manager.register_handler("focus_sell", _focus_sell)

        async def submit_via_hotkey() -> None:
            if submit_button.state == ButtonState.DEFAULT:
                await submit_button.trigger()

        hotkey_manager.register_handler("submit_order", submit_via_hotkey)
        hotkey_manager.register_handler("cancel_form", clear_form_callback)

    hotkey_manager = app.storage.client.get("hotkey_manager")
    if isinstance(hotkey_manager, HotkeyManager):
        _register_order_form_hotkeys(
            hotkey_manager,
            qty_input,
            side_select,
            submit_action_button,
            reset_form,
        )
    else:
        logger.warning("hotkey_manager_not_available", extra={"user_id": user_id})

    # Subscribe to real-time kill switch updates for instant UI responses
    async def on_kill_switch_update(data: dict[str, Any]) -> None:
        nonlocal kill_switch_engaged
        state = str(data.get("state", "")).upper()
        # Fail-closed: only mark as safe if explicitly DISENGAGED
        kill_switch_engaged = state != "DISENGAGED"

    await realtime.subscribe(kill_switch_channel(), on_kill_switch_update)

    # Fetch initial kill switch status
    async def check_initial_kill_switch() -> None:
        nonlocal kill_switch_engaged
        try:
            ks_status = await trading_client.fetch_kill_switch_status(user_id, role=user_role)
            state = str(ks_status.get("state", "")).upper()
            kill_switch_engaged = state != "DISENGAGED"
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning(
                "kill_switch_initial_check_failed",
                extra={"user_id": user_id, "error": type(exc).__name__},
            )
            # Fail-closed: treat API failure as engaged
            kill_switch_engaged = True

    await check_initial_kill_switch()

    # Register cleanup for realtime subscriptions
    await lifecycle.register_cleanup_callback(client_id, realtime.cleanup)


__all__ = ["manual_order_page"]
