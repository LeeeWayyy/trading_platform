"""Positions AG Grid component for the NiceGUI trading console."""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

import httpx
from nicegui import ui

from apps.web_console_ng.core.client import AsyncTradingClient

logger = logging.getLogger(__name__)


def create_positions_grid() -> ui.aggrid:
    """Create AG Grid for positions with real-time updates."""

    column_defs = [
        {
            "field": "symbol",
            "headerName": "Symbol",
            "sortable": True,
            "filter": True,
            "pinned": "left",
            "width": 100,
        },
        {
            "field": "qty",
            "headerName": "Qty",
            "sortable": True,
            "type": "numericColumn",
            "width": 80,
        },
        {
            "field": "avg_entry_price",
            "headerName": "Avg Entry",
            "sortable": True,
            "valueFormatter": "x => '$' + x.value.toFixed(2)",
            "type": "numericColumn",
        },
        {
            "field": "current_price",
            "headerName": "Current",
            "sortable": True,
            "valueFormatter": "x => '$' + x.value.toFixed(2)",
            "type": "numericColumn",
        },
        {
            "field": "unrealized_pl",
            "headerName": "P&L ($)",
            "sortable": True,
            "valueFormatter": "x => '$' + x.value.toFixed(2)",
            "cellStyle": {
                "function": "params.value >= 0 ? {color: '#16a34a'} : {color: '#dc2626'}"
            },
            "type": "numericColumn",
        },
        {
            "field": "unrealized_plpc",
            "headerName": "P&L (%)",
            "sortable": True,
            "valueFormatter": "x => (x.value * 100).toFixed(2) + '%'",
            "cellStyle": {
                "function": "params.value >= 0 ? {color: '#16a34a'} : {color: '#dc2626'}"
            },
            "type": "numericColumn",
        },
        {
            "field": "actions",
            "headerName": "Actions",
            "cellRenderer": "closePositionRenderer",
            "pinned": "right",
            "width": 100,
            "suppressSorting": True,
        },
    ]

    grid = ui.aggrid(
        {
            "columnDefs": column_defs,
            "rowData": [],
            "domLayout": "autoHeight",
            "defaultColDef": {
                "resizable": True,
                "sortable": True,
            },
            "rowSelection": "multiple",
            "suppressRowClickSelection": True,
            "animateRows": True,
            "getRowId": "data => data.symbol",
            "onGridReady": "params => { window._positionsGridApi = params.api; }",
        }
    ).classes("w-full")

    return grid


async def update_positions_grid(
    grid: ui.aggrid,
    positions: list[dict[str, Any]],
    previous_symbols: set[str] | None = None,
) -> set[str]:
    """
    Update grid with new positions data using AG Grid's applyTransaction.

    Uses getRowId (configured as 'data => data.symbol') for efficient delta updates:
    - No full re-render
    - Preserves scroll position
    - Preserves row selection
    - Only updates changed rows
    - ADDS new positions (symbols not in previous snapshot)
    - REMOVES closed positions (symbols no longer in snapshot)

    Args:
        grid: The AG Grid instance
        positions: Current positions snapshot from backend
        previous_symbols: Set of symbols from previous update (for remove detection)

    Returns:
        Set of current symbols (pass to next update for remove detection)

    Rev 18h: Filter malformed entries (per Codex review - LOW)
    """
    valid_positions = [p for p in positions if p.get("symbol")]
    if len(valid_positions) < len(positions):
        malformed_count = len(positions) - len(valid_positions)
        logger.warning(
            "update_positions_grid_malformed_entries",
            extra={"malformed_count": malformed_count, "total_count": len(positions)},
        )

    current_symbols = {p["symbol"] for p in valid_positions}

    if previous_symbols is None:
        await grid.run_grid_method("api.setRowData", valid_positions)
        return current_symbols

    added_positions = [p for p in valid_positions if p["symbol"] not in previous_symbols]
    updated_positions = [p for p in valid_positions if p["symbol"] in previous_symbols]
    removed_symbols = [{"symbol": s} for s in (previous_symbols - current_symbols)]

    await grid.run_grid_method(
        "api.applyTransaction",
        {"add": added_positions, "update": updated_positions, "remove": removed_symbols},
    )

    return current_symbols


def generate_close_order_id(symbol: str, qty: int, dialog_nonce: str, user_id: str) -> str:
    """Generate deterministic client_order_id for position close.

    Pattern: hash(user_id|symbol|qty|close|nonce)[:24]
    """
    normalized_symbol = symbol.upper().strip()
    raw = f"{user_id}|{normalized_symbol}|{abs(qty)}|close|{dialog_nonce}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


async def on_close_position(symbol: str, qty: int, user_id: str, user_role: str) -> None:
    """Handle close position button click."""
    client = AsyncTradingClient.get()

    try:
        qty_float = float(qty)
    except (TypeError, ValueError):
        logger.warning(
            "close_position_invalid_qty",
            extra={"user_id": user_id, "symbol": symbol, "qty": qty, "strategy_id": "manual"},
        )
        ui.notify("Invalid quantity value", type="negative")
        return

    if qty_float != int(qty_float):
        logger.warning(
            "close_position_fractional_qty_rejected",
            extra={"user_id": user_id, "symbol": symbol, "qty": qty, "strategy_id": "manual"},
        )
        ui.notify("Fractional shares not supported", type="warning")
        return

    qty = int(qty_float)

    if qty == 0:
        ui.notify("Cannot close position with zero quantity", type="warning")
        return

    if user_role == "viewer":
        ui.notify("Viewers cannot execute trades", type="warning")
        return

    try:
        ks_status = await client.fetch_kill_switch_status(user_id, role=user_role)
        if ks_status.get("state") == "ENGAGED":
            logger.info(
                "close_blocked_kill_switch",
                extra={
                    "user_id": user_id,
                    "symbol": symbol,
                    "qty": qty,
                    "strategy_id": "manual",
                },
            )
            ui.notify("Cannot close position: Kill Switch is ENGAGED", type="negative")
            return
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "close_position_safety_check_failed",
            extra={
                "user_id": user_id,
                "symbol": symbol,
                "qty": qty,
                "status": exc.response.status_code,
                "strategy_id": "manual",
            },
        )
        ui.notify(f"Safety check failed: HTTP {exc.response.status_code}", type="negative")
        return
    except httpx.RequestError as exc:
        logger.warning(
            "close_position_safety_check_failed",
            extra={
                "user_id": user_id,
                "symbol": symbol,
                "qty": qty,
                "error": type(exc).__name__,
                "strategy_id": "manual",
            },
        )
        ui.notify("Cannot reach safety service - try again", type="negative")
        return

    try:
        cb_status = await client.fetch_circuit_breaker_status(user_id, role=user_role)
        cb_state = str(cb_status.get("state", "")).upper()
        if cb_state in {"TRIPPED", "OPEN", "ENGAGED", "ON"}:
            logger.info(
                "close_position_circuit_breaker_tripped",
                extra={
                    "user_id": user_id,
                    "symbol": symbol,
                    "qty": qty,
                    "state": cb_state,
                    "strategy_id": "manual",
                },
            )
            ui.notify(
                "Circuit breaker is TRIPPED (close allowed for risk reduction)",
                type="warning",
            )
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "close_position_circuit_breaker_check_failed",
            extra={
                "user_id": user_id,
                "symbol": symbol,
                "qty": qty,
                "status": exc.response.status_code,
                "strategy_id": "manual",
            },
        )
    except httpx.RequestError as exc:
        logger.warning(
            "close_position_circuit_breaker_check_failed",
            extra={
                "user_id": user_id,
                "symbol": symbol,
                "qty": qty,
                "error": type(exc).__name__,
                "strategy_id": "manual",
            },
        )

    dialog_nonce = uuid.uuid4().hex

    with ui.dialog() as dialog, ui.card().classes("p-4"):
        ui.label(f"Close {symbol} position?").classes("text-lg font-bold")
        ui.label(f"Quantity: {qty} shares")

        submitting = False
        confirm_button: ui.button | None = None

        with ui.row().classes("gap-4 mt-4"):

            async def confirm() -> None:
                nonlocal submitting
                if submitting:
                    return
                submitting = True
                if confirm_button:
                    confirm_button.disable()

                try:
                    ks = await client.fetch_kill_switch_status(user_id, role=user_role)
                    if ks.get("state") == "ENGAGED":
                        ui.notify("Order blocked: Kill Switch engaged", type="negative")
                        dialog.close()
                        return
                except httpx.HTTPStatusError as exc:
                    logger.warning(
                        "close_confirm_kill_switch_check_failed",
                        extra={
                            "user_id": user_id,
                            "symbol": symbol,
                            "status": exc.response.status_code,
                            "strategy_id": "manual",
                        },
                    )
                    ui.notify("Cannot verify safety status - order blocked", type="negative")
                    dialog.close()
                    return
                except httpx.RequestError as exc:
                    logger.warning(
                        "close_confirm_kill_switch_check_failed",
                        extra={
                            "user_id": user_id,
                            "symbol": symbol,
                            "error": type(exc).__name__,
                            "strategy_id": "manual",
                        },
                    )
                    ui.notify("Cannot reach safety service - order blocked", type="negative")
                    dialog.close()
                    return

                order_id = generate_close_order_id(symbol, qty, dialog_nonce, user_id)

                try:
                    await client.submit_order(
                        {
                            "symbol": symbol,
                            "qty": abs(qty),
                            "side": "sell" if qty > 0 else "buy",
                            "type": "market",
                            "client_order_id": order_id,
                            "reduce_only": True,
                            "reason": "Manual close via dashboard",
                        },
                        user_id=user_id,
                        role=user_role,
                    )
                    logger.info(
                        "close_position_submitted",
                        extra={
                            "user_id": user_id,
                            "symbol": symbol,
                            "qty": qty,
                            "client_order_id": order_id,
                            "strategy_id": "manual",
                        },
                    )
                    ui.notify(f"Closing {symbol} position", type="positive")
                    dialog.close()
                except httpx.HTTPStatusError as exc:
                    logger.warning(
                        "close_position_submit_failed",
                        extra={
                            "user_id": user_id,
                            "symbol": symbol,
                            "client_order_id": order_id,
                            "status": exc.response.status_code,
                            "strategy_id": "manual",
                        },
                    )
                    ui.notify(f"Close failed: HTTP {exc.response.status_code}", type="negative")
                except httpx.RequestError as exc:
                    logger.warning(
                        "close_position_submit_failed",
                        extra={
                            "user_id": user_id,
                            "symbol": symbol,
                            "client_order_id": order_id,
                            "error": type(exc).__name__,
                            "strategy_id": "manual",
                        },
                    )
                    ui.notify("Close failed: network error - please retry", type="negative")
                finally:
                    submitting = False
                    if confirm_button:
                        confirm_button.enable()

            confirm_button = ui.button("Confirm", on_click=confirm).classes(
                "bg-red-600 text-white"
            )
            ui.button("Cancel", on_click=dialog.close)

    dialog.open()


__all__ = [
    "create_positions_grid",
    "update_positions_grid",
    "generate_close_order_id",
    "on_close_position",
]
