"""Position Management page for NiceGUI web console."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from nicegui import Client, app, ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.core.audit import audit_log
from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from apps.web_console_ng.core.realtime import (
    RealtimeUpdater,
    kill_switch_channel,
    position_channel,
)
from apps.web_console_ng.ui.layout import main_layout

logger = logging.getLogger(__name__)


@dataclass
class KillSwitchCheckResult:
    """Result of a kill switch safety check."""

    safe_to_proceed: bool
    kill_switch_engaged: bool
    error_message: str | None = None


async def check_kill_switch_safety(
    trading_client: AsyncTradingClient,
    user_id: str,
    user_role: str,
) -> KillSwitchCheckResult:
    """Check kill switch status before a critical action.

    Implements fail-closed behavior: blocks action on ENGAGED, unknown state, or API error.

    Args:
        trading_client: Client for API calls
        user_id: User ID for the request
        user_role: User role for authorization

    Returns:
        KillSwitchCheckResult with safe_to_proceed flag and state info
    """
    try:
        ks_status = await trading_client.fetch_kill_switch_status(user_id, role=user_role)
        state = str(ks_status.get("state", "")).upper()

        if state == "ENGAGED":
            return KillSwitchCheckResult(
                safe_to_proceed=False,
                kill_switch_engaged=True,
                error_message="BLOCKED: Kill Switch is ENGAGED",
            )

        if state != "DISENGAGED":
            # Unknown state - fail closed for safety
            logger.warning(
                "kill_switch_unknown_state",
                extra={"user_id": user_id, "state": state},
            )
            return KillSwitchCheckResult(
                safe_to_proceed=False,
                kill_switch_engaged=True,
                error_message="Cannot verify kill switch: unknown state",
            )

        # State is DISENGAGED - safe to proceed
        return KillSwitchCheckResult(
            safe_to_proceed=True,
            kill_switch_engaged=False,
        )

    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        logger.warning(
            "kill_switch_check_failed",
            extra={"user_id": user_id, "error": type(exc).__name__},
        )
        # Fail-closed: treat API failure as unsafe
        return KillSwitchCheckResult(
            safe_to_proceed=False,
            kill_switch_engaged=True,
            error_message="Cannot verify kill switch status - action blocked",
        )


@ui.page("/position-management")
@requires_auth
@main_layout
async def position_management_page(client: Client) -> None:
    """Position management page with close, flatten, and cancel actions."""
    trading_client = AsyncTradingClient.get()
    user = get_current_user()
    user_id = str(user.get("user_id") or user.get("username") or "").strip()
    user_role = str(user.get("role") or "viewer")

    if not user_id:
        logger.warning("position_management_missing_user_id")
        ui.notify("Session expired - please log in again", type="negative")
        ui.navigate.to("/login")
        return

    if user_role == "viewer":
        ui.notify("Viewers cannot manage positions", type="negative")
        ui.navigate.to("/")
        return

    lifecycle = ClientLifecycleManager.get()

    # Get or generate client_id (may not be set yet if WebSocket hasn't connected)
    client_id = client.storage.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        client_id = lifecycle.generate_client_id()
        client.storage["client_id"] = client_id

    realtime = RealtimeUpdater(client_id, client)
    action_in_progress = False
    positions_data: list[dict[str, Any]] = []
    kill_switch_engaged = True  # Secure by default - assume engaged until proven otherwise

    with ui.card().classes("w-full max-w-4xl mx-auto"):
        ui.label("Position Management").classes("text-2xl font-bold mb-4")

        # Kill switch warning banner
        ks_banner = ui.row().classes("w-full bg-red-100 p-4 rounded-lg mb-4 hidden")
        with ks_banner:
            ui.icon("warning", color="red").classes("text-2xl mr-2")
            ui.label("Kill Switch is ENGAGED - position close/flatten blocked").classes("text-red-600 font-bold")

        # Summary row
        with ui.row().classes("w-full gap-4 mb-4"):
            position_count_label = ui.label("Positions: 0").classes("font-bold")
            total_value_label = ui.label("Total Value: $0.00")
            unrealized_pnl_label = ui.label("Unrealized P&L: $0.00")

        # Positions table
        positions_grid = ui.aggrid({
            "columnDefs": [
                {"field": "symbol", "headerName": "Symbol", "width": 100},
                {"field": "qty", "headerName": "Qty", "width": 80},
                {
                    "field": "market_value",
                    "headerName": "Market Value",
                    "width": 120,
                    "valueFormatter": "x => x.value != null ? '$' + x.value.toLocaleString('en-US', {minimumFractionDigits: 2}) : '-'",
                },
                {
                    "field": "unrealized_pl",
                    "headerName": "Unrealized P&L",
                    "width": 130,
                    "valueFormatter": "x => x.value != null ? '$' + x.value.toLocaleString('en-US', {minimumFractionDigits: 2}) : '-'",
                    "cellStyle": "params => ({ color: params.value >= 0 ? 'green' : 'red' })",
                },
                {"field": "avg_entry_price", "headerName": "Avg Entry", "width": 100},
                {"field": "current_price", "headerName": "Current", "width": 100},
            ],
            "rowData": [],
            "domLayout": "autoHeight",
            "getRowId": "data => data.symbol",
            "rowSelection": "single",
        }).classes("w-full mb-4")

        # Action buttons
        with ui.row().classes("w-full gap-4"):
            close_btn = ui.button("Close Selected Position", color="orange").classes("text-white")
            cancel_all_btn = ui.button("Cancel All Orders (Selected Symbol)", color="yellow").classes("text-black")
            flatten_btn = ui.button("FLATTEN ALL POSITIONS", color="red").classes("text-white")

    def update_summary() -> None:
        position_count_label.set_text(f"Positions: {len(positions_data)}")
        total_value = sum(p.get("market_value", 0) or 0 for p in positions_data)
        total_value_label.set_text(f"Total Value: ${total_value:,.2f}")
        unrealized = sum(p.get("unrealized_pl", 0) or 0 for p in positions_data)
        color = "text-green-600" if unrealized >= 0 else "text-red-600"
        unrealized_pnl_label.classes(remove="text-green-600 text-red-600", add=color)
        unrealized_pnl_label.set_text(f"Unrealized P&L: ${unrealized:,.2f}")

    async def load_positions() -> None:
        nonlocal positions_data
        try:
            result = await trading_client.fetch_positions(user_id, role=user_role)
            positions_data = result.get("positions", [])
            positions_grid.options["rowData"] = positions_data
            positions_grid.update()
            update_summary()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "load_positions_failed",
                extra={"user_id": user_id, "status": exc.response.status_code},
            )
            ui.notify(f"Failed to load positions: HTTP {exc.response.status_code}", type="negative")
        except httpx.RequestError as exc:
            logger.warning(
                "load_positions_failed",
                extra={"user_id": user_id, "error": type(exc).__name__},
            )
            ui.notify("Failed to load positions: network error", type="negative")

    async def check_kill_switch_status() -> None:
        nonlocal kill_switch_engaged
        try:
            ks_status = await trading_client.fetch_kill_switch_status(user_id, role=user_role)
            state = str(ks_status.get("state", "")).upper()
            # Fail-closed: only allow actions if explicitly DISENGAGED
            kill_switch_engaged = state != "DISENGAGED"
            if kill_switch_engaged:
                ks_banner.classes(remove="hidden")
            else:
                ks_banner.classes(add="hidden")
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning(
                "kill_switch_status_check_failed",
                extra={"user_id": user_id, "error": type(exc).__name__},
            )
            # Fail-closed: treat API failure as unsafe
            kill_switch_engaged = True
            ks_banner.classes(remove="hidden")

    await load_positions()
    await check_kill_switch_status()

    async def on_position_update(data: dict[str, Any]) -> None:
        nonlocal positions_data
        if "positions" in data:
            positions_data = data["positions"]
            positions_grid.options["rowData"] = positions_data
            positions_grid.update()
            update_summary()

    async def on_kill_switch_update(data: dict[str, Any]) -> None:
        nonlocal kill_switch_engaged
        state = str(data.get("state", "")).upper()
        # Fail-closed: only allow actions if explicitly DISENGAGED
        kill_switch_engaged = state != "DISENGAGED"
        if kill_switch_engaged:
            ks_banner.classes(remove="hidden")
        else:
            ks_banner.classes(add="hidden")

    await realtime.subscribe(position_channel(user_id), on_position_update)
    await realtime.subscribe(kill_switch_channel(), on_kill_switch_update)

    async def get_selected_position() -> dict[str, Any] | None:
        rows = await positions_grid.get_selected_rows()
        if not rows:
            ui.notify("Please select a position first", type="warning")
            return None
        return rows[0]

    async def show_close_dialog() -> None:
        nonlocal action_in_progress
        if action_in_progress:
            return

        if kill_switch_engaged:
            ui.notify("Cannot close position: Kill Switch is ENGAGED", type="negative")
            return

        position = await get_selected_position()
        if not position:
            return

        symbol = position.get("symbol", "")
        qty = position.get("qty", 0)

        # Disable button to prevent double-open
        close_btn.disable()

        with ui.dialog() as dialog, ui.card().classes("p-6 min-w-[400px]"):

            def on_dialog_close() -> None:
                if not action_in_progress:
                    close_btn.enable()

            dialog.on("close", on_dialog_close)
            ui.label(f"Close {symbol} Position?").classes("text-xl font-bold mb-4")
            ui.label(f"Quantity: {qty:,} shares")

            reason_input = ui.textarea(
                "Reason (required)",
                placeholder="Why are you closing this position? (min 10 chars)",
                validation={"Min 10 characters": lambda v: bool(v and len(v.strip()) >= 10)},
            ).classes("w-full my-4")

            with ui.row().classes("gap-4 justify-end"):
                async def confirm_close() -> None:
                    nonlocal action_in_progress, kill_switch_engaged
                    if action_in_progress:
                        return

                    reason = (reason_input.value or "").strip()
                    if len(reason) < 10:
                        ui.notify("Reason must be at least 10 characters", type="warning")
                        return

                    # Fresh kill switch check via API (fail closed on unknown state)
                    ks_result = await check_kill_switch_safety(
                        trading_client, user_id, user_role
                    )
                    kill_switch_engaged = ks_result.kill_switch_engaged
                    if ks_result.kill_switch_engaged:
                        ks_banner.classes(remove="hidden")
                    else:
                        ks_banner.classes(add="hidden")

                    if not ks_result.safe_to_proceed:
                        if ks_result.error_message:
                            ui.notify(ks_result.error_message, type="negative")
                        dialog.close()
                        return

                    action_in_progress = True
                    close_confirm_btn.disable()

                    requested_at = datetime.now(UTC).isoformat()
                    try:
                        result = await trading_client.close_position(
                            symbol=symbol,
                            reason=reason,
                            requested_by=user_id,
                            requested_at=requested_at,
                            user_id=user_id,
                            role=user_role,
                        )

                        order_id = result.get("order_id", "")
                        audit_log(
                            action="position_closed",
                            user_id=user_id,
                            details={
                                "symbol": symbol,
                                "qty": qty,
                                "order_id": order_id,
                                "reason": reason,
                                "requested_at": requested_at,
                            },
                        )

                        ui.notify(f"Closing {symbol}: order submitted", type="positive")
                        dialog.close()
                        await load_positions()

                    except httpx.HTTPStatusError as exc:
                        logger.warning(
                            "close_position_failed",
                            extra={
                                "user_id": user_id,
                                "symbol": symbol,
                                "status": exc.response.status_code,
                                "requested_at": requested_at,
                            },
                        )
                        audit_log(
                            action="close_position_failed",
                            user_id=user_id,
                            details={
                                "symbol": symbol,
                                "error": f"HTTP {exc.response.status_code}",
                                "requested_at": requested_at,
                            },
                        )
                        ui.notify(f"Failed to close: HTTP {exc.response.status_code}", type="negative")
                    except httpx.RequestError as exc:
                        logger.warning(
                            "close_position_failed",
                            extra={
                                "user_id": user_id,
                                "symbol": symbol,
                                "error": type(exc).__name__,
                                "requested_at": requested_at,
                            },
                        )
                        audit_log(
                            action="close_position_failed",
                            user_id=user_id,
                            details={
                                "symbol": symbol,
                                "error": type(exc).__name__,
                                "requested_at": requested_at,
                            },
                        )
                        ui.notify("Failed to close: network error", type="negative")
                    finally:
                        action_in_progress = False
                        close_confirm_btn.enable()
                        close_btn.enable()

                close_confirm_btn = ui.button("Close Position", on_click=confirm_close, color="orange").classes("text-white")
                ui.button("Cancel", on_click=dialog.close)

        dialog.open()

    async def show_cancel_all_dialog() -> None:
        nonlocal action_in_progress
        if action_in_progress:
            return

        # Cancel-all BYPASSES kill switch (risk-reducing)

        position = await get_selected_position()
        if not position:
            return

        symbol = position.get("symbol", "")

        # Disable button to prevent double-open
        cancel_all_btn.disable()

        with ui.dialog() as dialog, ui.card().classes("p-6 min-w-[400px]"):

            def on_dialog_close() -> None:
                if not action_in_progress:
                    cancel_all_btn.enable()

            dialog.on("close", on_dialog_close)
            ui.label(f"Cancel All Orders for {symbol}?").classes("text-xl font-bold text-orange-600 mb-4")
            ui.label(
                "This will cancel all open orders for this symbol."
            ).classes("mb-4")

            reason_input = ui.textarea(
                "Reason (required)",
                placeholder="Why are you cancelling all orders? (min 10 chars)",
                validation={"Min 10 characters": lambda v: bool(v and len(v.strip()) >= 10)},
            ).classes("w-full mb-4")

            with ui.row().classes("gap-4 justify-end"):
                async def confirm_cancel() -> None:
                    nonlocal action_in_progress
                    if action_in_progress:
                        return

                    reason = (reason_input.value or "").strip()
                    if len(reason) < 10:
                        ui.notify("Reason must be at least 10 characters", type="warning")
                        return

                    action_in_progress = True
                    cancel_confirm_btn.disable()
                    cancel_all_btn.disable()

                    requested_at = datetime.now(UTC).isoformat()
                    try:
                        result = await trading_client.cancel_all_orders(
                            symbol=symbol,
                            reason=reason,
                            requested_by=user_id,
                            requested_at=requested_at,
                            user_id=user_id,
                            role=user_role,
                        )

                        cancelled_count = result.get("cancelled_count", 0)
                        audit_log(
                            action="cancel_all_orders",
                            user_id=user_id,
                            details={
                                "symbol": symbol,
                                "cancelled_count": cancelled_count,
                                "reason": reason,
                                "requested_at": requested_at,
                            },
                        )

                        ui.notify(f"Cancelled {cancelled_count} orders for {symbol}", type="positive")
                        dialog.close()

                    except httpx.HTTPStatusError as exc:
                        logger.warning(
                            "cancel_all_orders_failed",
                            extra={
                                "user_id": user_id,
                                "symbol": symbol,
                                "status": exc.response.status_code,
                                "requested_at": requested_at,
                            },
                        )
                        audit_log(
                            action="cancel_all_orders_failed",
                            user_id=user_id,
                            details={
                                "symbol": symbol,
                                "error": f"HTTP {exc.response.status_code}",
                                "requested_at": requested_at,
                            },
                        )
                        ui.notify(f"Failed to cancel: HTTP {exc.response.status_code}", type="negative")
                    except httpx.RequestError as exc:
                        logger.warning(
                            "cancel_all_orders_failed",
                            extra={
                                "user_id": user_id,
                                "symbol": symbol,
                                "error": type(exc).__name__,
                                "requested_at": requested_at,
                            },
                        )
                        audit_log(
                            action="cancel_all_orders_failed",
                            user_id=user_id,
                            details={
                                "symbol": symbol,
                                "error": type(exc).__name__,
                                "requested_at": requested_at,
                            },
                        )
                        ui.notify("Failed to cancel: network error", type="negative")
                    finally:
                        action_in_progress = False
                        cancel_confirm_btn.enable()
                        cancel_all_btn.enable()

                cancel_confirm_btn = ui.button("Cancel All Orders", on_click=confirm_cancel, color="yellow").classes("text-black")
                ui.button("Keep Orders", on_click=dialog.close)

        dialog.open()

    async def show_flatten_dialog() -> None:
        nonlocal action_in_progress
        if action_in_progress:
            return

        if kill_switch_engaged:
            ui.notify("Cannot flatten: Kill Switch is ENGAGED", type="negative")
            return

        # Check admin permission
        if user_role != "admin":
            ui.notify("Admin permission required to flatten all positions", type="negative")
            return

        # Disable button to prevent double-open
        flatten_btn.disable()

        # First confirmation
        with ui.dialog() as dialog1, ui.card().classes("p-6 min-w-[450px]"):

            def on_dialog1_close() -> None:
                if not action_in_progress:
                    flatten_btn.enable()

            dialog1.on("close", on_dialog1_close)
            ui.label("Flatten ALL Positions?").classes("text-xl font-bold text-red-600 mb-4")
            ui.label(
                "This will submit MARKET orders to close ALL positions. "
                "This action CANNOT be undone."
            ).classes("text-red-600 mb-4")
            ui.label(f"Positions to close: {len(positions_data)}").classes("font-bold mb-4")

            reason_input = ui.textarea(
                "Reason (required)",
                placeholder="Why are you flattening all positions? (min 20 chars)",
                validation={"Min 20 characters": lambda v: bool(v and len(v.strip()) >= 20)},
            ).classes("w-full mb-4")

            with ui.row().classes("gap-4 justify-end"):
                async def proceed_to_confirm() -> None:
                    reason = (reason_input.value or "").strip()
                    if len(reason) < 20:
                        ui.notify("Reason must be at least 20 characters", type="warning")
                        return
                    dialog1.close()
                    await show_flatten_confirmation(reason)

                ui.button("Proceed", on_click=proceed_to_confirm, color="yellow").classes("text-black")
                ui.button("Cancel", on_click=dialog1.close)

        dialog1.open()

    async def show_flatten_confirmation(reason: str) -> None:
        nonlocal action_in_progress

        # Two-factor confirmation
        with ui.dialog() as dialog2, ui.card().classes("p-6 min-w-[400px]"):

            def on_dialog2_close() -> None:
                if not action_in_progress:
                    flatten_btn.enable()

            dialog2.on("close", on_dialog2_close)
            ui.label("FINAL CONFIRMATION").classes("text-xl font-bold text-red-600 mb-4")
            ui.label("Type FLATTEN to confirm:").classes("mb-4")

            confirm_input = ui.input("Type FLATTEN").classes("w-full mb-4 font-mono")

            with ui.row().classes("gap-4 justify-end"):
                async def execute_flatten() -> None:
                    nonlocal action_in_progress, kill_switch_engaged
                    if action_in_progress:
                        return

                    if confirm_input.value != "FLATTEN":
                        ui.notify("Type FLATTEN exactly to proceed", type="warning")
                        return

                    # Fresh kill switch check via API (fail closed on unknown state)
                    ks_result = await check_kill_switch_safety(
                        trading_client, user_id, user_role
                    )
                    kill_switch_engaged = ks_result.kill_switch_engaged
                    if ks_result.kill_switch_engaged:
                        ks_banner.classes(remove="hidden")
                    else:
                        ks_banner.classes(add="hidden")

                    if not ks_result.safe_to_proceed:
                        if ks_result.error_message:
                            ui.notify(ks_result.error_message, type="negative")
                        dialog2.close()
                        return

                    # Get MFA token from session
                    id_token = app.storage.user.get("id_token")
                    if not id_token:
                        ui.notify("MFA required - please re-authenticate", type="negative")
                        dialog2.close()
                        return

                    action_in_progress = True
                    flatten_confirm_btn.disable()

                    requested_at = datetime.now(UTC).isoformat()
                    try:
                        result = await trading_client.flatten_all_positions(
                            reason=reason,
                            requested_by=user_id,
                            requested_at=requested_at,
                            id_token=id_token,
                            user_id=user_id,
                            role=user_role,
                        )

                        positions_closed = result.get("positions_closed", 0)
                        audit_log(
                            action="flatten_all_positions",
                            user_id=user_id,
                            details={
                                "positions_closed": positions_closed,
                                "reason": reason,
                                "requested_at": requested_at,
                            },
                        )

                        ui.notify(f"Flattened {positions_closed} positions", type="positive")
                        dialog2.close()
                        await load_positions()

                    except httpx.HTTPStatusError as exc:
                        logger.warning(
                            "flatten_all_failed",
                            extra={
                                "user_id": user_id,
                                "status": exc.response.status_code,
                                "requested_at": requested_at,
                            },
                        )
                        audit_log(
                            action="flatten_all_failed",
                            user_id=user_id,
                            details={
                                "error": f"HTTP {exc.response.status_code}",
                                "reason": reason,
                                "requested_at": requested_at,
                            },
                        )
                        ui.notify(f"Failed to flatten: HTTP {exc.response.status_code}", type="negative")
                    except httpx.RequestError as exc:
                        logger.warning(
                            "flatten_all_failed",
                            extra={
                                "user_id": user_id,
                                "error": type(exc).__name__,
                                "requested_at": requested_at,
                            },
                        )
                        audit_log(
                            action="flatten_all_failed",
                            user_id=user_id,
                            details={
                                "error": type(exc).__name__,
                                "reason": reason,
                                "requested_at": requested_at,
                            },
                        )
                        ui.notify("Failed to flatten: network error", type="negative")
                    finally:
                        action_in_progress = False
                        flatten_confirm_btn.enable()
                        flatten_btn.enable()

                flatten_confirm_btn = ui.button("FLATTEN ALL", on_click=execute_flatten, color="red").classes("text-white")
                ui.button("Cancel", on_click=dialog2.close)

        dialog2.open()

    close_btn.on("click", show_close_dialog)
    cancel_all_btn.on("click", show_cancel_all_dialog)
    flatten_btn.on("click", show_flatten_dialog)

    # Fallback polling timer for reliability
    # Primary updates come via real-time subscription (on_position_update).
    # This timer is a safety net for missed updates due to:
    # - Transient Redis/network issues
    # - Reconnection gaps in pub/sub
    # - Edge cases where real-time event is lost
    # Interval is longer (30s) since real-time handles the normal case.
    timer = ui.timer(30.0, load_positions)

    async def cleanup() -> None:
        timer.cancel()
        await realtime.cleanup()

    await lifecycle.register_cleanup_callback(client_id, cleanup)


__all__ = ["position_management_page"]
