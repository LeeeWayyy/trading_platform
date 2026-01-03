"""Orders AG Grid component for the NiceGUI trading console."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from nicegui import ui

from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng.core.synthetic_id import (
    SyntheticIdContext,
    compute_order_fingerprint,
    resolve_synthetic_id,
)

logger = logging.getLogger(__name__)


def _ensure_order_id(
    order: dict[str, Any],
    log_context: dict[str, Any],
    id_ctx: SyntheticIdContext,
    notified_missing_ids: set[str] | None,
    client_id: str | None,
) -> None:
    """Ensure order has a client_order_id, generating synthetic ID if needed.

    This function modifies the order dict in place, setting:
    - client_order_id: The ID to use for AG Grid row identity
    - _broker_order_id: Preserved broker ID if available
    - _missing_client_order_id: Flag if original ID was missing
    - _missing_all_ids: Flag if both client and broker IDs were missing

    Args:
        order: Order dict to process (modified in place)
        log_context: Context dict for logging
        id_ctx: Shared context for synthetic ID generation
        notified_missing_ids: Set to track which IDs have been notified (for deduping)
        client_id: Client ID for notification context
    """
    broker_id = order.get("id") or order.get("order_id")
    if broker_id:
        order["_broker_order_id"] = broker_id

    if order.get("client_order_id"):
        return  # Already has ID

    if broker_id:
        # Use broker ID as fallback
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
        return

    # No IDs at all - generate synthetic ID
    fingerprint, base_hash = compute_order_fingerprint(order)
    synthetic_id = resolve_synthetic_id(fingerprint, base_hash, id_ctx)

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

    # Notify user about missing ID (deduped if notified_missing_ids provided)
    context_suffix = f" (ref {client_id[-6:]})" if client_id else ""
    should_notify = True
    if notified_missing_ids is not None:
        if synthetic_id in notified_missing_ids:
            should_notify = False
        else:
            notified_missing_ids.add(synthetic_id)

    if should_notify:
        ui.notify(
            f"WARNING: Order for {order.get('symbol', 'unknown')} has no ID - contact support{context_suffix}",
            type="negative",
            timeout=0,
        )


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
            # Handle null/undefined and Decimal strings from API
            "valueFormatter": "x => (x.value == null) ? 'MKT' : '$' + Number(x.value).toFixed(2)",
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
    synthetic_id_miss_counts: dict[str, int] | None = None,
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
    - Cleans up stale entries from synthetic_id_map (prevents memory leak)

    Note: Uses dict[str, Any] for orders because AG Grid's JavaScript API requires
    raw dict objects. Type safety is enforced at the API boundary (backend schemas).

    Args:
        grid: The AG Grid instance
        orders: Current open orders snapshot from backend (raw dicts for AG Grid API)
        previous_order_ids: Set of order IDs from previous update (for add/remove detection)
        notified_missing_ids: Set of synthetic IDs already notified (mutated in place to dedupe)
        synthetic_id_map: Dict mapping order fingerprints to stable synthetic IDs
            (mutated in place; stale entries cleaned up automatically after 3 consecutive misses)
        synthetic_id_miss_counts: Dict tracking consecutive misses per fingerprint
            (mutated in place; prevents churn from transient snapshot gaps)
        user_id: Optional user ID for audit context
        client_id: Optional client ID for audit context

    Returns:
        Set of current order IDs (pass to next update for remove detection)
    """
    orders = [order.copy() for order in orders]

    log_context = {
        "user_id": user_id,
        "client_id": client_id,
        "strategy_id": "manual",  # Dashboard operations are manual
    }

    # Context for synthetic ID generation across this batch
    id_ctx = SyntheticIdContext(
        synthetic_id_map=synthetic_id_map,
        previous_order_ids=previous_order_ids,
        batch_generated_ids=set(),
    )

    for order in orders:
        _ensure_order_id(order, log_context, id_ctx, notified_missing_ids, client_id)

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

    # Cleanup synthetic_id_map to prevent unbounded growth in long-running sessions
    # Use miss counts to avoid churn from transient snapshot gaps (delete after 3 misses)
    # Promote suffix IDs when base ID disappears to preserve row stability
    if synthetic_id_map is not None:
        miss_threshold = 3

        # First pass: promote suffix IDs if base ID is gone but suffix ID is present
        # This prevents row ID churn when one of multiple duplicate orders fills
        for fp, sid in list(synthetic_id_map.items()):
            if "|_suffix_" in fp:
                continue  # Skip suffix entries - they're handled below
            if sid not in current_ids:
                # Base ID is gone - check if any suffix ID is still present
                suffix = 1
                suffix_key = f"{fp}|_suffix_{suffix}"
                while suffix_key in synthetic_id_map:
                    suffix_sid = synthetic_id_map[suffix_key]
                    if suffix_sid in current_ids:
                        # Promote this suffix ID to be the base ID
                        synthetic_id_map[fp] = suffix_sid
                        del synthetic_id_map[suffix_key]
                        if synthetic_id_miss_counts is not None:
                            synthetic_id_miss_counts.pop(fp, None)
                            synthetic_id_miss_counts.pop(suffix_key, None)
                        break
                    suffix += 1
                    suffix_key = f"{fp}|_suffix_{suffix}"

        # Second pass: apply miss count logic for cleanup
        for fp, sid in list(synthetic_id_map.items()):
            if sid not in current_ids:
                if synthetic_id_miss_counts is not None:
                    synthetic_id_miss_counts[fp] = synthetic_id_miss_counts.get(fp, 0) + 1
                    if synthetic_id_miss_counts[fp] >= miss_threshold:
                        del synthetic_id_map[fp]
                        del synthetic_id_miss_counts[fp]
                else:
                    # No miss tracking - delete immediately (legacy behavior)
                    del synthetic_id_map[fp]
            elif synthetic_id_miss_counts is not None and fp in synthetic_id_miss_counts:
                # Order reappeared - reset miss count
                del synthetic_id_miss_counts[fp]

    return current_ids


async def on_cancel_order(
    order_id: str | None,
    symbol: str,
    user_id: str,
    user_role: str,
    broker_order_id: str | None = None,
) -> None:
    """Handle cancel order button click.

    Policy: Cancelling orders is ALWAYS allowed (no kill switch/circuit breaker checks)
    because it reduces risk by removing pending orders. This is intentional - even when
    trading is halted, users should be able to cancel outstanding orders.

    Note: Backend cancel endpoint requires client_order_id. Broker_order_id cannot be used
    as a fallback because the endpoint is keyed by client_order_id.
    """
    if not order_id:
        logger.warning(
            "cancel_order_missing_id",
            extra={
                "user_id": user_id,
                "symbol": symbol,
                "broker_order_id": broker_order_id,
                "strategy_id": "manual",
            },
        )
        ui.notify("Cannot cancel order: missing client order ID", type="negative")
        return

    if user_role == "viewer":
        ui.notify("Viewers cannot cancel orders", type="warning")
        return

    # Backend cancel endpoint requires client_order_id - reject invalid IDs
    if order_id.startswith("unknown_"):
        # Synthetic ID - no valid client_order_id
        logger.warning(
            "cancel_order_synthetic_id_blocked",
            extra={
                "user_id": user_id,
                "symbol": symbol,
                "synthetic_id": order_id,
                "broker_order_id": broker_order_id,
                "strategy_id": "manual",
            },
        )
        ui.notify(
            "Cannot cancel: order has no client ID - contact support",
            type="negative",
        )
        return

    if order_id.startswith("__ng_fallback_"):
        # Fallback ID - no valid client_order_id (only broker_order_id exists)
        logger.warning(
            "cancel_order_fallback_id_blocked",
            extra={
                "user_id": user_id,
                "symbol": symbol,
                "fallback_id": order_id,
                "broker_order_id": broker_order_id,
                "strategy_id": "manual",
            },
        )
        ui.notify(
            "Cannot cancel: order has no client ID - use broker interface",
            type="negative",
        )
        return

    client = AsyncTradingClient.get()
    try:
        await client.cancel_order(order_id, user_id, role=user_role)
        logger.info(
            "cancel_order_submitted",
            extra={
                "user_id": user_id,
                "symbol": symbol,
                "client_order_id": order_id,
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
