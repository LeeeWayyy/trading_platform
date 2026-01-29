"""Reconcile pending order modifications after replace failures."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from psycopg import IntegrityError

from apps.execution_gateway.schemas import OrderRequest

logger = logging.getLogger(__name__)


def _apply_change(value: Any, change: Any) -> Any:
    if isinstance(change, (list, tuple)) and len(change) == 2:
        return change[1]
    return value


def reconcile_pending_modifications(
    db_client,
    alpaca_client,
    *,
    stale_after: timedelta = timedelta(minutes=5),
) -> int:
    """Recover pending modifications that may have succeeded at broker.

    Returns number of recovered modifications.
    """
    cutoff = datetime.now(UTC) - stale_after
    pending = db_client.get_pending_modifications_older_than(cutoff)
    recovered = 0

    for mod in pending:
        original_id = mod["original_client_order_id"]
        new_client_id = mod["new_client_order_id"]
        modification_id = str(mod["modification_id"])

        try:
            alpaca_order = alpaca_client.get_order_by_client_id(new_client_id)
        except Exception as exc:
            logger.warning(
                "pending_modification_alpaca_lookup_failed",
                extra={"modification_id": modification_id, "error": str(exc)},
            )
            continue

        if alpaca_order is None:
            db_client.update_modification_status(
                modification_id,
                status="failed",
                error_message="Replacement order not found at broker after timeout",
            )
            logger.warning(
                "pending_modification_not_found_at_broker",
                extra={"modification_id": modification_id, "new_client_order_id": new_client_id},
            )
            continue

        original = db_client.get_order_by_client_id(original_id)
        if not original:
            db_client.update_modification_status(
                modification_id,
                status="failed",
                error_message="Original order missing during modification recovery",
            )
            logger.warning(
                "pending_modification_original_missing",
                extra={"modification_id": modification_id, "original_client_order_id": original_id},
            )
            continue

        changes = mod.get("changes") or {}
        effective_qty = _apply_change(original.qty, changes.get("qty"))
        effective_limit = _apply_change(original.limit_price, changes.get("limit_price"))
        effective_stop = _apply_change(original.stop_price, changes.get("stop_price"))
        effective_tif = _apply_change(original.time_in_force, changes.get("time_in_force"))

        replacement_request = OrderRequest(
            symbol=original.symbol,
            side=original.side,
            qty=int(effective_qty),
            order_type=original.order_type,
            limit_price=Decimal(str(effective_limit)) if effective_limit is not None else None,
            stop_price=Decimal(str(effective_stop)) if effective_stop is not None else None,
            time_in_force=str(effective_tif),
            execution_style=original.execution_style or "instant",
        )

        with db_client.transaction() as conn:
            db_client.finalize_modification(
                modification_id,
                new_broker_order_id=alpaca_order.get("id"),
                status="completed",
                new_client_order_id=alpaca_order.get("client_order_id") or new_client_id,
                conn=conn,
            )
            db_client.update_order_status_simple_with_conn(original_id, "replaced", conn=conn)
            try:
                db_client.insert_replacement_order(
                    client_order_id=alpaca_order.get("client_order_id") or new_client_id,
                    replaced_order_id=original_id,
                    strategy_id=original.strategy_id,
                    order_request=replacement_request,
                    status=alpaca_order.get("status") or "pending_new",
                    broker_order_id=alpaca_order.get("id"),
                    conn=conn,
                )
            except IntegrityError:
                # Replacement order already inserted; ignore.
                pass

        recovered += 1

    return recovered
