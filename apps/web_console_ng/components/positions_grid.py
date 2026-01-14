"""Positions AG Grid component for the NiceGUI trading console."""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import UTC, datetime
from typing import Any

import httpx
from nicegui import ui

from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng.core.grid_performance import GridPerformanceMonitor, get_monitor
from apps.web_console_ng.ui.trading_layout import (
    apply_compact_grid_classes,
    apply_compact_grid_options,
)

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
            # Handle null/undefined and Decimal strings from API
            ":valueFormatter": "x => (x.value == null) ? '$--.--' : '$' + Number(x.value).toFixed(2)",
            "type": "numericColumn",
        },
        {
            "field": "current_price",
            "headerName": "Current",
            "sortable": True,
            # Handle null/undefined and Decimal strings from API
            ":valueFormatter": "x => (x.value == null) ? '$--.--' : '$' + Number(x.value).toFixed(2)",
            "type": "numericColumn",
        },
        {
            "field": "unrealized_pl",
            "headerName": "P&L ($)",
            "sortable": True,
            # Handle null/undefined and Decimal strings from API
            ":valueFormatter": "x => (x.value == null) ? '$--.--' : '$' + Number(x.value).toFixed(2)",
            "cellStyle": {
                "function": "params.value >= 0 ? {color: 'var(--profit)'} : {color: 'var(--loss)'}"
            },
            "type": "numericColumn",
        },
        {
            "field": "unrealized_plpc",
            "headerName": "P&L (%)",
            "sortable": True,
            # Handle null/undefined and Decimal strings from API
            ":valueFormatter": "x => (x.value == null) ? '--.--' + '%' : (Number(x.value) * 100).toFixed(2) + '%'",
            "cellStyle": {
                "function": "params.value >= 0 ? {color: 'var(--profit)'} : {color: 'var(--loss)'}"
            },
            "type": "numericColumn",
        },
        {
            "field": "actions",
            "headerName": "Actions",
            ":cellRenderer": "window.closePositionRenderer",
            "pinned": "right",
            "width": 100,
            "sortable": False,
        },
    ]

    options = apply_compact_grid_options(
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
            "asyncTransactionWaitMillis": 50,
            "suppressAnimationFrame": False,
            "animateRows": True,
            ":getRowId": "params => params.data.symbol",
            ":onGridReady": "params => { window._positionsGridApi = params.api; if (window.GridThrottle) window.GridThrottle.registerAsyncGrid('positions_grid'); }",
            ":onAsyncTransactionsFlushed": "params => { if (window.GridThrottle) window.GridThrottle.recordTransactionResult(params.api, 'positions_grid', params.results); }",
            ":onRowDataUpdated": "params => { if (window.GridThrottle) window.GridThrottle.recordUpdate(params.api, 'positions_grid'); }",
        }
    )
    grid = ui.aggrid(options).classes("w-full ag-theme-alpine-dark")
    apply_compact_grid_classes(grid)

    monitor = GridPerformanceMonitor("positions_grid")
    monitor.attach_to_grid(grid)

    grid._ready_event = asyncio.Event()  # type: ignore[attr-defined]
    grid.on("gridReady", lambda _: grid._ready_event.set())  # type: ignore[attr-defined]
    grid.on(
        "gridReady",
        lambda _: ui.run_javascript(
            "window.GridStateManager.restoreState(window._positionsGridApi, 'positions_grid')"
        ),
    )
    grid.on(
        "gridReady",
        lambda _: ui.run_javascript(
            "window.GridStateManager.registerAutoSave(window._positionsGridApi, 'positions_grid')"
        ),
    )

    return grid


async def update_positions_grid(
    grid: ui.aggrid,
    positions: list[dict[str, Any]],
    previous_symbols: set[str] | None = None,
    notified_malformed: set[int] | None = None,
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
        notified_malformed: Set of malformed counts already notified (mutated in place
            to dedupe notifications across updates; pass same set each update)

    Returns:
        Set of current symbols (pass to next update for remove detection)

    Rev 18h: Filter malformed entries (per Codex review - LOW)
    Rev 19: Dedupe malformed notifications (per Codex review - LOW)
    """
    valid_positions = [p.copy() for p in positions if p.get("symbol")]
    if len(valid_positions) < len(positions):
        malformed_count = len(positions) - len(valid_positions)
        logger.warning(
            "update_positions_grid_malformed_entries",
            extra={"malformed_count": malformed_count, "total_count": len(positions)},
        )
        # Alert user that position data may be incomplete (don't silently hide positions)
        # Dedupe notifications to avoid spamming on every update tick
        # Reset dedupe when issue resolves so we re-notify if it recurs
        should_notify = True
        if notified_malformed is not None:
            if malformed_count in notified_malformed:
                should_notify = False
            else:
                notified_malformed.add(malformed_count)
        if should_notify:
            ui.notify(
                f"⚠️ Data Integrity: {malformed_count} position(s) hidden due to missing symbol - contact support",
                type="warning",
                timeout=0,  # Persistent until dismissed
            )
    else:
        # Issue resolved - reset dedupe so we can re-notify if it recurs
        if notified_malformed is not None:
            notified_malformed.clear()

    for pos in valid_positions:
        if pos.get("unrealized_plpc") is not None:
            continue
        try:
            unrealized_pl = float(pos.get("unrealized_pl"))  # type: ignore[arg-type]
            avg_entry = float(pos.get("avg_entry_price"))  # type: ignore[arg-type]
            qty = float(pos.get("qty"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if avg_entry == 0 or qty == 0:
            continue
        pos["unrealized_plpc"] = unrealized_pl / (avg_entry * abs(qty))

    current_symbols = {p["symbol"] for p in valid_positions}

    if getattr(grid, "_ready_event", None) is not None and not grid._ready_event.is_set():  # type: ignore[attr-defined]
        grid.options["rowData"] = valid_positions
        grid.update()
        return current_symbols

    if previous_symbols is None:
        # Fire-and-forget to avoid UI timeouts when the browser is busy.
        grid.run_grid_method("setRowData", valid_positions, timeout=5)
        return current_symbols

    added_positions = [p for p in valid_positions if p["symbol"] not in previous_symbols]
    updated_positions = [p for p in valid_positions if p["symbol"] in previous_symbols]
    removed_symbols = [{"symbol": s} for s in (previous_symbols - current_symbols)]

    monitor = get_monitor(grid)
    if monitor:
        delta_size = len(added_positions) + len(updated_positions) + len(removed_symbols)
        monitor.metrics.record_update(delta_size)

    # Fire-and-forget to avoid UI timeouts when the browser is busy.
    grid.run_grid_method(
        "applyTransactionAsync",
        {"add": added_positions, "update": updated_positions, "remove": removed_symbols},
        timeout=5,
    )

    return current_symbols


async def on_close_position(
    symbol: str,
    qty: int | float | str,
    user_id: str,
    user_role: str,
    *,
    kill_switch_engaged: bool | None = None,
    strategies: list[str] | None = None,
) -> None:
    """Handle close position button click.

    Safety checks:
    - Kill Switch ENGAGED: Blocks close (explicit policy)
    - Kill Switch unreachable: Warns but proceeds (fail-open for risk reduction)
    - Circuit Breaker tripped: Warns but allows close (reducing risk is permitted)

    Note: Closing positions is risk-reducing, so we fail-open on network errors.
    The Execution Gateway performs final validation.

    Args:
        symbol: The symbol to close
        qty: Position quantity
        user_id: User ID for the request
        user_role: User role for authorization
        kill_switch_engaged: Optional real-time cached kill switch state.
            If True, skips blocking API call (instant UI response).
            If False, skips pre-check (safe to show dialog).
            If None (default), performs blocking API check (fail-open).
    """
    client = AsyncTradingClient.get()
    requested_at = datetime.now(UTC).isoformat()

    try:
        qty_float = float(qty)
    except (TypeError, ValueError):
        logger.warning(
            "close_position_invalid_qty",
            extra={
                "user_id": user_id,
                "symbol": symbol,
                "qty": qty,
                "requested_at": requested_at,
                "strategy_id": "manual",
            },
        )
        ui.notify("Invalid quantity value", type="negative")
        return

    # Guard against NaN/inf from upstream
    if not math.isfinite(qty_float):
        logger.warning(
            "close_position_invalid_qty",
            extra={
                "user_id": user_id,
                "symbol": symbol,
                "qty": qty,
                "requested_at": requested_at,
                "strategy_id": "manual",
            },
        )
        ui.notify("Invalid quantity value", type="negative")
        return

    if qty_float != int(qty_float):
        logger.warning(
            "close_position_fractional_qty_rejected",
            extra={
                "user_id": user_id,
                "symbol": symbol,
                "qty": qty,
                "requested_at": requested_at,
                "strategy_id": "manual",
            },
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

    # Pre-check kill switch state using real-time cached value if available
    # This provides instant UI response while preserving safety at confirmation time
    if kill_switch_engaged is True:
        # Use cached state - instant response, no API delay
        logger.info(
            "close_blocked_kill_switch_cached",
            extra={
                "user_id": user_id,
                "symbol": symbol,
                "qty": qty,
                "requested_at": requested_at,
                "strategy_id": "manual",
            },
        )
        ui.notify("Cannot close position: Kill Switch is ENGAGED", type="negative")
        return
    elif kill_switch_engaged is False:
        # Cached state says safe - skip pre-check, proceed to dialog
        # Fresh check will still happen at confirmation time
        pass
    else:
        # No cached state (None) - fall back to blocking API check
        try:
            ks_status = await client.fetch_kill_switch_status(
                user_id,
                role=user_role,
                strategies=strategies,
            )
            if ks_status.get("state") == "ENGAGED":
                logger.info(
                    "close_blocked_kill_switch",
                    extra={
                        "user_id": user_id,
                        "symbol": symbol,
                        "qty": qty,
                        "requested_at": requested_at,
                        "strategy_id": "manual",
                    },
                )
                ui.notify("Cannot close position: Kill Switch is ENGAGED", type="negative")
                return
        except httpx.HTTPStatusError as exc:
            # Fail-open for 5xx (server errors), fail-closed for 4xx (client errors)
            # Risk-reducing actions should proceed when safety service has transient issues
            if exc.response.status_code >= 500:
                logger.warning(
                    "close_position_safety_check_5xx_proceeding",
                    extra={
                        "user_id": user_id,
                        "symbol": symbol,
                        "qty": qty,
                        "status": exc.response.status_code,
                        "requested_at": requested_at,
                        "strategy_id": "manual",
                    },
                )
                ui.notify(f"Safety service error (HTTP {exc.response.status_code}) - proceeding with close", type="warning")
                # Do NOT return - proceed to show confirmation dialog
            else:
                logger.warning(
                    "close_position_safety_check_failed",
                    extra={
                        "user_id": user_id,
                        "symbol": symbol,
                        "qty": qty,
                        "status": exc.response.status_code,
                        "requested_at": requested_at,
                        "strategy_id": "manual",
                    },
                )
                ui.notify(f"Safety check failed: HTTP {exc.response.status_code}", type="negative")
                return
        except httpx.RequestError as exc:
            # Fail-open: closing positions reduces risk, so proceed with warning
            # Execution Gateway will perform final validation
            logger.warning(
                "close_position_safety_check_unreachable_proceeding",
                extra={
                    "user_id": user_id,
                    "symbol": symbol,
                    "qty": qty,
                    "error": type(exc).__name__,
                    "requested_at": requested_at,
                    "strategy_id": "manual",
                },
            )
            ui.notify("Safety service unreachable - proceeding with close", type="warning")
            # Do NOT return - proceed to show confirmation dialog

    try:
        cb_status = await client.fetch_circuit_breaker_status(
            user_id,
            role=user_role,
            strategies=strategies,
        )
        cb_state = str(cb_status.get("state", "")).upper()
        if cb_state in {"TRIPPED", "ENGAGED", "ON", "QUIET_PERIOD"}:
            logger.info(
                "close_position_circuit_breaker_tripped",
                extra={
                    "user_id": user_id,
                    "symbol": symbol,
                    "qty": qty,
                    "state": cb_state,
                    "requested_at": requested_at,
                    "strategy_id": "manual",
                },
            )
            ui.notify(
                f"Circuit breaker is {cb_state} (close allowed for risk reduction)",
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
                "requested_at": requested_at,
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
                "requested_at": requested_at,
                "strategy_id": "manual",
            },
        )

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
                    ks = await client.fetch_kill_switch_status(
                        user_id,
                        role=user_role,
                        strategies=strategies,
                    )
                    if ks.get("state") == "ENGAGED":
                        logger.info(
                            "close_confirm_kill_switch_engaged_blocked",
                            extra={
                                "user_id": user_id,
                                "symbol": symbol,
                                "requested_at": requested_at,
                                "strategy_id": "manual",
                            },
                        )
                        ui.notify("Order blocked: Kill Switch engaged", type="negative")
                        dialog.close()
                        return
                except httpx.HTTPStatusError as exc:
                    # Fail-open for 5xx (server errors), fail-closed for 4xx (client errors)
                    if exc.response.status_code >= 500:
                        logger.warning(
                            "close_confirm_safety_5xx_proceeding",
                            extra={
                                "user_id": user_id,
                                "symbol": symbol,
                                "status": exc.response.status_code,
                                "requested_at": requested_at,
                                "strategy_id": "manual",
                            },
                        )
                        ui.notify(f"Safety service error (HTTP {exc.response.status_code}) - proceeding", type="warning")
                        # Do NOT return - proceed to submit close order
                    else:
                        logger.warning(
                            "close_confirm_kill_switch_check_failed",
                            extra={
                                "user_id": user_id,
                                "symbol": symbol,
                                "status": exc.response.status_code,
                                "requested_at": requested_at,
                                "strategy_id": "manual",
                            },
                        )
                        ui.notify("Cannot verify safety status - order blocked", type="negative")
                        dialog.close()
                        return
                except httpx.RequestError as exc:
                    # Fail-open: closing positions reduces risk, so proceed with warning
                    logger.warning(
                        "close_confirm_safety_unreachable_proceeding",
                        extra={
                            "user_id": user_id,
                            "symbol": symbol,
                            "error": type(exc).__name__,
                            "requested_at": requested_at,
                            "strategy_id": "manual",
                        },
                    )
                    ui.notify("Safety service unreachable - proceeding with close", type="warning")
                    # Do NOT return - proceed to submit close order

                try:
                    # Backend generates deterministic order ID from action/symbol/qty/user/date
                    # Pass qty to ensure backend closes exact amount shown to user
                    result = await client.close_position(
                        symbol=symbol,
                        qty=abs(qty),
                        reason="Manual close via dashboard",
                        requested_by=user_id,
                        requested_at=requested_at,
                        user_id=user_id,
                        role=user_role,
                    )
                    order_id = result.get("order_id")
                    if not order_id:
                        logger.warning(
                            "close_position_missing_order_id",
                            extra={
                                "user_id": user_id,
                                "symbol": symbol,
                                "client_order_id": None,
                                "requested_at": requested_at,
                                "strategy_id": "manual",
                            },
                        )
                        order_id = "unknown"
                    logger.info(
                        "close_position_submitted",
                        extra={
                            "user_id": user_id,
                            "symbol": symbol,
                            "qty": abs(qty),  # Log submitted qty (abs for shorts)
                            "order_id": order_id,
                            "client_order_id": order_id,  # Backend-generated ID
                            "requested_at": requested_at,
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
                            "client_order_id": None,  # Failed before order created
                            "status": exc.response.status_code,
                            "requested_at": requested_at,
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
                            "client_order_id": None,  # Failed before order created
                            "error": type(exc).__name__,
                            "requested_at": requested_at,
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
    "on_close_position",
]
