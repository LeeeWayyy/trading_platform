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
    # Track IDs generated in this batch to prevent same-batch collisions
    batch_generated_ids: set[str] = set()
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
                # Include more fields to reduce collision risk for distinct orders
                # Normalize numeric values to avoid fingerprint fragility (10 vs 10.0)
                def _normalize_num(val: object) -> str:
                    if isinstance(val, (int, float)):  # noqa: UP038
                        # Use repr for full precision to avoid fingerprint collisions
                        # (e.g., crypto with >6 decimal precision)
                        return repr(float(val))
                    return str(val) if val is not None else ""

                fingerprint_fields = [
                    order.get("symbol", ""),
                    order.get("side", ""),
                    order.get("created_at", ""),
                    order.get("account_id", ""),
                    _normalize_num(order.get("qty")),
                    order.get("type", ""),
                    _normalize_num(order.get("limit_price")),
                    order.get("time_in_force", ""),
                ]
                fingerprint = "|".join(fingerprint_fields)
                base_hash = hashlib.sha256(fingerprint.encode()).hexdigest()[:12]

                if synthetic_id_map is not None and fingerprint in synthetic_id_map:
                    # Fingerprint already mapped - but check if base ID is still valid
                    # If base ID was removed but suffix exists, prefer suffix (row stability)
                    base_id = synthetic_id_map[fingerprint]
                    synthetic_id = base_id

                    # If base ID is NOT in previous snapshot but a suffix IS, use the suffix
                    # This prevents row churn when base order fills and suffix remains
                    if previous_order_ids is not None and base_id not in previous_order_ids:
                        suffix = 1
                        suffix_key = f"{fingerprint}|_suffix_{suffix}"
                        while suffix_key in synthetic_id_map:
                            suffix_id = synthetic_id_map[suffix_key]
                            if suffix_id in previous_order_ids:
                                # This suffix was in previous snapshot - use it
                                synthetic_id = suffix_id
                                break
                            suffix += 1
                            suffix_key = f"{fingerprint}|_suffix_{suffix}"

                    if synthetic_id in batch_generated_ids:
                        # Same-batch collision - check for existing suffix or generate new one
                        suffix = 1
                        suffix_key = f"{fingerprint}|_suffix_{suffix}"
                        # First try to find an existing suffix key not yet used in this batch
                        while suffix_key in synthetic_id_map:
                            existing_suffix_id = synthetic_id_map[suffix_key]
                            if existing_suffix_id not in batch_generated_ids:
                                synthetic_id = existing_suffix_id
                                break
                            suffix += 1
                            suffix_key = f"{fingerprint}|_suffix_{suffix}"
                        else:
                            # No existing suffix found - generate new one
                            synthetic_id = f"{synthetic_id}_{suffix}"
                            # Persist suffix ID to prevent churn if only this order remains
                            synthetic_id_map[suffix_key] = synthetic_id
                else:
                    # Base fingerprint not in map - check for orphaned suffix entries first
                    # (can happen when base order fills but suffix order remains)
                    found_orphan_suffix = False
                    if synthetic_id_map is not None:
                        suffix = 1
                        suffix_key = f"{fingerprint}|_suffix_{suffix}"
                        while suffix_key in synthetic_id_map:
                            orphan_id = synthetic_id_map[suffix_key]
                            if orphan_id not in batch_generated_ids:
                                synthetic_id = orphan_id
                                found_orphan_suffix = True
                                break
                            suffix += 1
                            suffix_key = f"{fingerprint}|_suffix_{suffix}"

                    if not found_orphan_suffix:
                        synthetic_id = f"unknown_{base_hash}"

                        # Check against both persistent map and current batch to avoid collisions
                        existing_ids = batch_generated_ids.copy()
                        if synthetic_id_map is not None:
                            existing_ids.update(synthetic_id_map.values())
                        suffix = 0
                        while synthetic_id in existing_ids:
                            suffix += 1
                            synthetic_id = f"unknown_{base_hash}_{suffix}"
                        if synthetic_id_map is not None:
                            synthetic_id_map[fingerprint] = synthetic_id

                batch_generated_ids.add(synthetic_id)

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
