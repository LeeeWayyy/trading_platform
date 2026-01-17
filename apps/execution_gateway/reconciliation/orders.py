"""Order reconciliation logic.

This module handles synchronization of order state between the broker
and the local database, including CAS updates and missing order handling.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from prometheus_client import Counter

from apps.execution_gateway.database import status_rank_for

if TYPE_CHECKING:
    from apps.execution_gateway.alpaca_client import AlpacaExecutor
    from apps.execution_gateway.database import DatabaseClient

logger = logging.getLogger(__name__)

# Pod label for Prometheus metrics
POD_LABEL = os.getenv("POD_NAME") or os.getenv("HOSTNAME") or "unknown"

# Source priority ordering (lower number = higher priority)
# Used as a tie-breaker in CAS when timestamp, status_rank, and filled_qty are equal.
SOURCE_PRIORITY_MANUAL = 1
SOURCE_PRIORITY_RECONCILIATION = 2
SOURCE_PRIORITY_WEBHOOK = 3

# Prometheus metrics (re-exported from main metrics module for compatibility)
reconciliation_mismatches_total = Counter(
    "execution_gateway_reconciliation_mismatches_total",
    "Total order mismatches corrected by reconciliation",
    ["pod"],
)

reconciliation_conflicts_skipped_total = Counter(
    "execution_gateway_reconciliation_conflicts_skipped_total",
    "Total reconciliation updates skipped due to CAS conflict",
    ["pod"],
)


def apply_broker_update(
    client_order_id: str,
    broker_order: dict[str, Any],
    db_client: DatabaseClient,
    backfill_fills_callback: Any | None = None,
) -> bool:
    """Apply broker order state to local database using CAS.

    Uses Compare-And-Swap (CAS) semantics to safely update order state.
    Only updates if the broker state is "newer" based on:
    - Updated timestamp
    - Status rank (terminal > non-terminal)
    - Source priority (Manual > Reconciliation > Webhook)

    Args:
        client_order_id: Client order ID to update.
        broker_order: Broker order data dict.
        db_client: Database client for order updates.
        backfill_fills_callback: Optional callback to backfill fill metadata.

    Returns:
        True if order was updated, False if CAS conflict skipped.

    Example:
        >>> updated = apply_broker_update(
        ...     "order_123",
        ...     {"status": "filled", "filled_qty": "100"},
        ...     db_client,
        ... )
        >>> print(f"Order updated: {updated}")
    """
    status = broker_order.get("status") or ""
    status_rank = status_rank_for(status)
    updated_at = broker_order.get("updated_at") or broker_order.get("created_at")
    if updated_at is None:
        updated_at = datetime.now(UTC)
    filled_qty = broker_order.get("filled_qty") or Decimal("0")
    filled_avg_price = broker_order.get("filled_avg_price")
    filled_at = broker_order.get("filled_at") if status == "filled" else None

    updated = db_client.update_order_status_cas(
        client_order_id=client_order_id,
        status=status,
        broker_updated_at=updated_at,
        status_rank=status_rank,
        source_priority=SOURCE_PRIORITY_RECONCILIATION,
        filled_qty=Decimal(str(filled_qty)),
        filled_avg_price=filled_avg_price,
        filled_at=filled_at,
        broker_order_id=broker_order.get("id"),
    )

    if updated is None:
        reconciliation_conflicts_skipped_total.labels(pod=POD_LABEL).inc()
        logger.warning(
            "Reconciliation CAS skipped",
            extra={"client_order_id": client_order_id, "status": status},
        )
        return False

    # Backfill fill metadata when webhooks aren't available (e.g., local dev).
    # This powers Recent Activity and P&L history based on fills.
    if status in ("filled", "partially_filled") and backfill_fills_callback:
        backfill_fills_callback(client_order_id, broker_order, updated_at, updated)

    reconciliation_mismatches_total.labels(pod=POD_LABEL).inc()
    return True


def reconcile_known_orders(
    db_orders: list[Any],
    orders_by_client: dict[str, dict[str, Any]],
    db_client: DatabaseClient,
    backfill_fills_callback: Any | None = None,
) -> int:
    """Reconcile known orders against broker state.

    For each DB order that has a corresponding broker order, apply
    the broker's state using CAS.

    Args:
        db_orders: List of DB order objects (non-terminal).
        orders_by_client: Dict mapping client_order_id to broker order.
        db_client: Database client for updates.
        backfill_fills_callback: Optional callback for fill backfill.

    Returns:
        Count of orders updated.
    """
    updated_count = 0
    for db_order in db_orders:
        broker_order = orders_by_client.get(db_order.client_order_id)
        if broker_order:
            if apply_broker_update(
                db_order.client_order_id,
                broker_order,
                db_client,
                backfill_fills_callback,
            ):
                updated_count += 1
    return updated_count


def reconcile_missing_orders(
    db_orders: list[Any],
    after_time: datetime | None,
    db_client: DatabaseClient,
    alpaca_client: AlpacaExecutor,
    max_individual_lookups: int = 100,
    submitted_unconfirmed_grace_seconds: int = 300,
    backfill_fills_callback: Any | None = None,
) -> dict[str, int]:
    """Handle orders not found in broker bulk query.

    For orders that weren't in the bulk query results:
    - Do individual lookups if within limits
    - Mark submitted_unconfirmed orders as failed after grace period

    Args:
        db_orders: List of DB order objects to check.
        after_time: Window start time for filtering.
        db_client: Database client for order updates.
        alpaca_client: Alpaca client for individual lookups.
        max_individual_lookups: Cap on individual API calls.
        submitted_unconfirmed_grace_seconds: Grace period before marking failed.
        backfill_fills_callback: Optional callback for fill backfill.

    Returns:
        Dict with counts: {"lookups": N, "updated": M, "marked_failed": K}
    """
    lookups = 0
    updated = 0
    marked_failed = 0
    now = datetime.now(UTC)

    for db_order in db_orders:
        # Skip orders that are within the query window (already checked)
        if db_order.status != "submitted_unconfirmed" and after_time is not None:
            if db_order.created_at >= after_time:
                continue

        if lookups >= max_individual_lookups:
            logger.warning(
                "Reconciliation lookup cap reached",
                extra={"max_lookups": max_individual_lookups},
            )
            break

        lookups += 1
        broker_order = alpaca_client.get_order_by_client_id(db_order.client_order_id)
        if broker_order:
            if apply_broker_update(
                db_order.client_order_id,
                broker_order,
                db_client,
                backfill_fills_callback,
            ):
                updated += 1
            continue

        # Handle submitted_unconfirmed that aren't at broker
        if db_order.status == "submitted_unconfirmed":
            age_seconds = (now - db_order.created_at).total_seconds()
            if age_seconds < submitted_unconfirmed_grace_seconds:
                logger.info(
                    "Submitted_unconfirmed within grace period; deferring failure",
                    extra={
                        "client_order_id": db_order.client_order_id,
                        "age_seconds": age_seconds,
                        "grace_seconds": submitted_unconfirmed_grace_seconds,
                    },
                )
                continue

            # Use the original order timestamp to avoid blocking later broker updates
            # that may arrive with earlier broker_updated_at values.
            broker_updated_at = db_order.created_at
            cas_updated = db_client.update_order_status_cas(
                client_order_id=db_order.client_order_id,
                status="failed",
                broker_updated_at=broker_updated_at,
                status_rank=status_rank_for("failed"),
                source_priority=SOURCE_PRIORITY_RECONCILIATION,
                filled_qty=Decimal("0"),
                filled_avg_price=None,
                filled_at=None,
                broker_order_id=db_order.broker_order_id,
            )
            if cas_updated is None:
                reconciliation_conflicts_skipped_total.labels(pod=POD_LABEL).inc()
            else:
                reconciliation_mismatches_total.labels(pod=POD_LABEL).inc()
                marked_failed += 1
                logger.warning(
                    "Submitted_unconfirmed marked as failed after grace period",
                    extra={
                        "client_order_id": db_order.client_order_id,
                        "age_seconds": age_seconds,
                    },
                )

    return {"lookups": lookups, "updated": updated, "marked_failed": marked_failed}


def backfill_terminal_fills(
    orders_by_client: dict[str, dict[str, Any]],
    db_known_ids: set[str],
    backfill_fills_callback: Any,
) -> int:
    """Backfill fill metadata for terminal orders.

    For filled/partially_filled orders that are in DB but might be missing
    fill metadata, call the backfill callback.

    Args:
        orders_by_client: Dict mapping client_order_id to broker order.
        db_known_ids: Set of client_order_ids known to DB.
        backfill_fills_callback: Callback function for fill backfill.

    Returns:
        Count of orders processed.
    """
    processed = 0
    for client_id, broker_order in orders_by_client.items():
        if client_id not in db_known_ids:
            continue
        status = str(broker_order.get("status") or "").lower()
        if status in ("filled", "partially_filled"):
            backfill_fills_callback(client_id, broker_order)
            processed += 1
    return processed
