"""Orders AG Grid component for the NiceGUI trading console."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import httpx
from nicegui import ui

from apps.web_console_ng.core.client import AsyncTradingClient

logger = logging.getLogger(__name__)


def create_orders_table() -> ui.aggrid:
    """Create AG Grid for open orders."""

    column_defs = [
        {"field": "symbol", "headerName": "Symbol", "width": 100},
        {
            "field": "side",
            "headerName": "Side",
            "width": 80,
            "cellStyle": {
                "function": "params.value === 'buy' ? {color: '#16a34a'} : {color: '#dc2626'}"
            },
        },
        {"field": "qty", "headerName": "Qty", "width": 80},
        {"field": "type", "headerName": "Type", "width": 80},
        {
            "field": "limit_price",
            "headerName": "Price",
            "valueFormatter": "x => (x.value !== null && x.value !== undefined) ? '$' + x.value.toFixed(2) : 'MKT'",
        },
        {
            "field": "status",
            "headerName": "Status",
            "cellRenderer": "statusBadgeRenderer",
            "width": 100,
        },
        {
            "field": "created_at",
            "headerName": "Time (UTC)",
            "valueFormatter": "x => new Date(x.value).toLocaleTimeString('en-US', {timeZone: 'UTC', hour12: false})",
        },
        {
            "field": "actions",
            "headerName": "",
            "width": 80,
            "cellRenderer": "cancelButtonRenderer",
        },
    ]

    return ui.aggrid(
        {
            "columnDefs": column_defs,
            "rowData": [],
            "domLayout": "autoHeight",
            "getRowId": "data => data.client_order_id",
            "onGridReady": "params => { window._ordersGridApi = params.api; }",
        }
    ).classes("w-full")


async def update_orders_table(
    grid: ui.aggrid,
    orders: list[dict[str, Any]],
    previous_order_ids: set[str] | None = None,
    notified_missing_ids: set[str] | None = None,
    synthetic_id_map: dict[str, str] | None = None,
    user_id: str | None = None,
    client_id: str | None = None,
) -> set[str]:
    """
    Update orders grid using AG Grid's applyTransaction.

    Uses getRowId (configured as 'data => data.client_order_id') for efficient updates:
    - Adds newly created orders
    - Updates existing orders (status changes, partial fills)
    - Removes filled/cancelled orders no longer in snapshot
    - Preserves scroll position and row selection

    Args:
        grid: The AG Grid instance
        orders: Current open orders snapshot from backend
        previous_order_ids: Set of order IDs from previous update (for add/remove detection)
        notified_missing_ids: Set of synthetic IDs already notified (mutated in place to dedupe)
        synthetic_id_map: Dict mapping order fingerprints to stable synthetic IDs (mutated in place)
        user_id: Optional user ID for audit context
        client_id: Optional client ID for audit context

    Returns:
        Set of current order IDs (pass to next update for remove detection)
    """
    orders = [order.copy() for order in orders]

    log_context = {
        "user_id": user_id,
        "client_id": client_id,
    }
    for order in orders:
        broker_id = order.get("id") or order.get("order_id")
        if broker_id:
            order["_broker_order_id"] = broker_id

        if not order.get("client_order_id"):
            if broker_id:
                order["client_order_id"] = f"__ng_fallback_{broker_id}"
                order["_missing_client_order_id"] = True
                logger.warning(
                    "order_missing_client_order_id_using_fallback",
                    extra={
                        "broker_order_id": broker_id,
                        "symbol": order.get("symbol"),
                        **log_context,
                    },
                )
            else:
                fingerprint_fields = [
                    order.get("symbol", ""),
                    order.get("side", ""),
                    order.get("created_at", ""),
                    order.get("account_id", ""),
                ]
                fingerprint = "|".join(fingerprint_fields)
                if synthetic_id_map is not None and fingerprint in synthetic_id_map:
                    synthetic_id = synthetic_id_map[fingerprint]
                else:
                    base_hash = hashlib.sha256(fingerprint.encode()).hexdigest()[:12]
                    synthetic_id = f"unknown_{base_hash}"

                    if synthetic_id_map is not None:
                        existing_ids = set(synthetic_id_map.values())
                        suffix = 0
                        while synthetic_id in existing_ids:
                            suffix += 1
                            synthetic_id = f"unknown_{base_hash}_{suffix}"
                        synthetic_id_map[fingerprint] = synthetic_id

                order["client_order_id"] = synthetic_id
                order["_missing_all_ids"] = True
                order["_missing_client_order_id"] = True
                logger.error(
                    "order_missing_all_ids_using_synthetic",
                    extra={
                        "symbol": order.get("symbol"),
                        "side": order.get("side"),
                        "synthetic_id": synthetic_id,
                        **log_context,
                    },
                )

                if notified_missing_ids is not None:
                    if synthetic_id not in notified_missing_ids:
                        notified_missing_ids.add(synthetic_id)
                        context_suffix = (
                            f" (ref {client_id[-6:]})" if client_id else ""
                        )
                        ui.notify(
                            f"WARNING: Order for {order.get('symbol', 'unknown')} has no ID - contact support{context_suffix}",
                            type="negative",
                            timeout=0,
                        )
                else:
                    context_suffix = f" (ref {client_id[-6:]})" if client_id else ""
                    ui.notify(
                        f"WARNING: Order for {order.get('symbol', 'unknown')} has no ID - contact support{context_suffix}",
                        type="negative",
                        timeout=0,
                    )

    valid_orders = [order for order in orders if order.get("client_order_id")]
    current_ids = {order["client_order_id"] for order in valid_orders}

    if previous_order_ids is None:
        await grid.run_grid_method("api.setRowData", valid_orders)
        return current_ids

    added_orders = [o for o in valid_orders if o["client_order_id"] not in previous_order_ids]
    updated_orders = [o for o in valid_orders if o["client_order_id"] in previous_order_ids]
    removed_orders = [
        {"client_order_id": oid} for oid in (previous_order_ids - current_ids)
    ]

    await grid.run_grid_method(
        "api.applyTransaction",
        {"add": added_orders, "update": updated_orders, "remove": removed_orders},
    )

    return current_ids


async def on_cancel_order(
    order_id: str,
    symbol: str,
    user_id: str,
    user_role: str,
    broker_order_id: str | None = None,
) -> None:
    """Handle cancel order button click (always allowed for risk reduction)."""
    if not order_id and not broker_order_id:
        logger.warning(
            "cancel_order_missing_id",
            extra={"user_id": user_id, "symbol": symbol, "strategy_id": "manual"},
        )
        ui.notify("Cannot cancel order: missing order ID", type="negative")
        return

    if user_role == "viewer":
        ui.notify("Viewers cannot cancel orders", type="warning")
        return

    effective_order_id = order_id
    if order_id.startswith("__ng_fallback_") and broker_order_id:
        effective_order_id = broker_order_id
    elif order_id.startswith("__ng_fallback_") and not broker_order_id:
        logger.warning(
            "cancel_order_missing_client_id",
            extra={
                "user_id": user_id,
                "symbol": symbol,
                "client_order_id": order_id,
                "strategy_id": "manual",
            },
        )
        ui.notify("Cannot cancel order: missing client_order_id", type="negative")
        return

    client = AsyncTradingClient.get()
    try:
        await client.cancel_order(effective_order_id, user_id, role=user_role)
        logger.info(
            "cancel_order_submitted",
            extra={
                "user_id": user_id,
                "symbol": symbol,
                "client_order_id": order_id,
                "broker_order_id": broker_order_id,
                "strategy_id": "manual",
            },
        )
        ui.notify(f"Cancel requested for {symbol}", type="positive")
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "cancel_order_failed",
            extra={
                "user_id": user_id,
                "symbol": symbol,
                "client_order_id": order_id,
                "status": exc.response.status_code,
                "strategy_id": "manual",
            },
        )
        ui.notify(f"Cancel failed: HTTP {exc.response.status_code}", type="negative")
    except httpx.RequestError as exc:
        logger.warning(
            "cancel_order_failed",
            extra={
                "user_id": user_id,
                "symbol": symbol,
                "client_order_id": order_id,
                "error": type(exc).__name__,
                "strategy_id": "manual",
            },
        )
        ui.notify("Cancel failed: network error - please retry", type="negative")


__all__ = ["create_orders_table", "update_orders_table", "on_cancel_order"]
