"""Fill backfill logic for reconciliation.

This module handles backfilling fill metadata from Alpaca's account
activities API and from broker order data when webhooks are unavailable.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from apps.execution_gateway.reconciliation.helpers import (
    calculate_synthetic_fill,
    generate_fill_id_from_activity,
)

if TYPE_CHECKING:
    from apps.execution_gateway.alpaca_client import AlpacaExecutor
    from apps.execution_gateway.database import DatabaseClient

logger = logging.getLogger(__name__)


def backfill_alpaca_fills(
    db_client: DatabaseClient,
    alpaca_client: AlpacaExecutor,
    *,
    lookback_hours: int | None = None,
    recalc_all_trades: bool = False,
    fills_backfill_enabled: bool = False,
    fills_backfill_initial_lookback_hours: int = 24,
    fills_backfill_page_size: int = 100,
    fills_backfill_max_pages: int = 5,
    overlap_seconds: int = 60,
) -> dict[str, Any]:
    """Backfill fill metadata from Alpaca account activities API.

    This function fetches FILL activities from Alpaca, matches them to
    orders in the database, and appends fill metadata. It also triggers
    P&L recalculation for affected strategy/symbol pairs.

    Args:
        db_client: Database client for order/fill operations.
        alpaca_client: Alpaca client for activity API.
        lookback_hours: Override lookback window. If None, use high-water mark.
        recalc_all_trades: Whether to recalculate all trades vs just new ones.
        fills_backfill_enabled: Feature flag for fills backfill.
        fills_backfill_initial_lookback_hours: Initial lookback if no high-water mark.
        fills_backfill_page_size: API page size.
        fills_backfill_max_pages: Max pages to fetch per run.
        overlap_seconds: Time overlap to catch edge cases.

    Returns:
        Dict with status and counts:
        - status: "ok", "disabled", or "error"
        - fills_seen: Total fills fetched from API
        - fills_inserted: Fills successfully inserted
        - unmatched: Fills that couldn't be matched to orders
        - pnl_updates: P&L recalculations performed
        - pnl_failures: P&L recalculations that failed

    Example:
        >>> result = backfill_alpaca_fills(db_client, alpaca_client)
        >>> print(f"Inserted {result['fills_inserted']} of {result['fills_seen']} fills")
    """
    if not fills_backfill_enabled and lookback_hours is None:
        return {"status": "disabled"}

    now = datetime.now(UTC)
    last_check = db_client.get_reconciliation_high_water_mark("alpaca_fills")
    if lookback_hours is not None:
        after_time = now - timedelta(hours=lookback_hours)
    elif last_check:
        after_time = last_check - timedelta(seconds=overlap_seconds)
    else:
        after_time = now - timedelta(hours=fills_backfill_initial_lookback_hours)

    # Paginate through FILL activities
    page_token: str | None = None
    last_activity_id: str | None = None
    pages = 0
    fills: list[dict[str, Any]] = []

    while pages < fills_backfill_max_pages:
        page_size = fills_backfill_page_size + (1 if page_token else 0)
        raw_page = alpaca_client.get_account_activities(
            "FILL",
            after=after_time,
            until=now,
            page_size=page_size,
            page_token=page_token,
            direction="desc",
        )
        if not raw_page:
            break
        page = raw_page
        if last_activity_id:
            page = [item for item in raw_page if str(item.get("id")) != last_activity_id]
        fills.extend(page)
        if len(raw_page) < page_size:
            break
        last_id = raw_page[-1].get("id")
        if not last_id:
            break
        last_activity_id = str(last_id)
        page_token = last_activity_id
        pages += 1

    if not fills:
        db_client.set_reconciliation_high_water_mark(now, name="alpaca_fills")
        return {"status": "ok", "fills_seen": 0, "fills_inserted": 0, "unmatched": 0}

    # Match fills to orders
    broker_ids = [
        str(fill.get("order_id"))
        for fill in fills
        if fill.get("order_id")
    ]
    orders_by_broker = db_client.get_orders_by_broker_ids(broker_ids)

    fills_by_client: dict[str, list[dict[str, Any]]] = {}
    affected: set[tuple[str, str]] = set()
    unmatched = 0

    for fill in fills:
        broker_order_id = fill.get("order_id")
        if not broker_order_id:
            unmatched += 1
            continue
        order = orders_by_broker.get(str(broker_order_id))
        if not order:
            unmatched += 1
            continue

        fill_id = str(fill.get("id") or "").strip()
        if not fill_id:
            fill_id = generate_fill_id_from_activity(fill)

        qty = fill.get("qty")
        price = fill.get("price")
        timestamp = fill.get("transaction_time") or fill.get("activity_time")

        fill_data = {
            "fill_id": fill_id,
            "fill_qty": qty,
            "fill_price": price,
            "realized_pl": "0",
            "timestamp": timestamp,
            "synthetic": False,
            "source": "alpaca_activity",
        }
        fills_by_client.setdefault(order.client_order_id, []).append(fill_data)
        affected.add((order.strategy_id, order.symbol))

    # Insert fills and recalculate P&L within transaction
    inserted = 0
    pnl_updates = 0
    pnl_failures = 0
    with db_client.transaction() as conn:
        for client_order_id, fill_items in fills_by_client.items():
            for fill_data in fill_items:
                updated = db_client.append_fill_to_order_metadata(
                    client_order_id=client_order_id,
                    fill_data=fill_data,
                    conn=conn,
                )
                if updated is not None:
                    inserted += 1

        for strategy_id, symbol in affected:
            try:
                result = db_client.recalculate_trade_realized_pnl(
                    strategy_id,
                    symbol,
                    update_all=recalc_all_trades,
                    conn=conn,
                )
                pnl_updates += int(result.get("trades_updated", 0))
            except Exception as exc:
                pnl_failures += 1
                logger.error(
                    "Alpaca fills backfill P&L recalculation failed - rolling back",
                    extra={
                        "strategy_id": strategy_id,
                        "symbol": symbol,
                        "error": str(exc),
                    },
                    exc_info=True,
                )
                raise RuntimeError(
                    f"P&L recalculation failed for {strategy_id}:{symbol}"
                ) from exc

    db_client.set_reconciliation_high_water_mark(now, name="alpaca_fills")
    logger.info(
        "Alpaca fills backfill completed",
        extra={
            "fills_seen": len(fills),
            "fills_inserted": inserted,
            "unmatched": unmatched,
            "pnl_updates": pnl_updates,
            "pnl_failures": pnl_failures,
            "after": after_time.isoformat(),
            "until": now.isoformat(),
        },
    )
    return {
        "status": "ok",
        "fills_seen": len(fills),
        "fills_inserted": inserted,
        "unmatched": unmatched,
        "pnl_updates": pnl_updates,
        "pnl_failures": pnl_failures,
    }


def backfill_fill_metadata(
    client_order_id: str,
    broker_order: dict[str, Any],
    db_client: DatabaseClient,
    updated_at: datetime | None = None,
    cached_order: Any | None = None,
) -> bool:
    """Backfill fill metadata when webhooks are unavailable (local dev).

    This function creates synthetic fills to fill gaps between broker's
    filled_qty and the sum of existing fills in the database.

    Args:
        client_order_id: Order to backfill.
        broker_order: Broker order data with filled_qty and filled_avg_price.
        db_client: Database client for order operations.
        updated_at: Timestamp to use for the fill.
        cached_order: Cached order object to avoid re-fetching.

    Returns:
        True if a synthetic fill was injected, False otherwise.
    """
    try:
        if updated_at is None:
            updated_at = broker_order.get("updated_at") or broker_order.get("created_at")
        if updated_at is None:
            updated_at = datetime.now(UTC)

        filled_qty = broker_order.get("filled_qty") or Decimal("0")
        filled_avg_price = broker_order.get("filled_avg_price")
        if filled_avg_price is None:
            return False

        with db_client.transaction() as conn:
            order = cached_order or db_client.get_order_for_update(client_order_id, conn)
            if not order:
                return False

            existing_fills = order.metadata.get("fills", [])
            fill_data = calculate_synthetic_fill(
                client_order_id=client_order_id,
                filled_qty=Decimal(str(filled_qty)),
                filled_avg_price=Decimal(str(filled_avg_price)),
                timestamp=updated_at,
                existing_fills=existing_fills,
                source="recon",
            )
            if fill_data is None:
                return False

            # Extract and remove internal field before storage
            missing_qty = fill_data.pop("_missing_qty")
            # Override source for storage (fill_id uses short prefix, source uses full name)
            fill_data["source"] = "reconciliation_backfill"

            db_client.append_fill_to_order_metadata(
                client_order_id=client_order_id,
                fill_data=fill_data,
                conn=conn,
            )
            logger.info(
                "reconciliation_backfill_fill",
                extra={
                    "client_order_id": client_order_id,
                    "symbol": getattr(order, "symbol", None),
                    "strategy_id": getattr(order, "strategy_id", None),
                    "fill_qty": str(missing_qty),
                    "fill_price": str(filled_avg_price),
                    "action": "synthetic_fill_injected",
                },
            )
            return True
    except Exception as exc:
        logger.warning(
            "Reconciliation fill backfill failed",
            extra={"client_order_id": client_order_id, "error": str(exc)},
        )
        return False


def backfill_fill_metadata_from_order(
    order: Any,
    db_client: DatabaseClient,
) -> bool:
    """Backfill fill metadata using DB order data only.

    For orders that are filled in DB but missing fill metadata,
    create synthetic fills based on filled_qty and filled_avg_price.

    Args:
        order: DB order object with filled_qty and filled_avg_price.
        db_client: Database client for order operations.

    Returns:
        True if a synthetic fill was injected, False otherwise.
    """
    try:
        filled_avg_price = getattr(order, "filled_avg_price", None)
        filled_qty = getattr(order, "filled_qty", None)
        if filled_avg_price is None or filled_qty is None:
            return False

        timestamp = getattr(order, "filled_at", None) or getattr(order, "updated_at", None)
        if timestamp is None:
            timestamp = datetime.now(UTC)

        with db_client.transaction() as conn:
            locked = db_client.get_order_for_update(order.client_order_id, conn)
            if not locked:
                return False

            existing_fills = locked.metadata.get("fills", [])
            fill_data = calculate_synthetic_fill(
                client_order_id=order.client_order_id,
                filled_qty=Decimal(str(filled_qty)),
                filled_avg_price=Decimal(str(filled_avg_price)),
                timestamp=timestamp,
                existing_fills=existing_fills,
                source="recon_db",
            )
            if fill_data is None:
                return False

            # Extract and remove internal field before storage
            missing_qty = fill_data.pop("_missing_qty")
            # Override source for storage (fill_id uses short prefix, source uses full name)
            fill_data["source"] = "reconciliation_db_backfill"

            db_client.append_fill_to_order_metadata(
                client_order_id=order.client_order_id,
                fill_data=fill_data,
                conn=conn,
            )
            logger.info(
                "reconciliation_db_backfill_fill",
                extra={
                    "client_order_id": order.client_order_id,
                    "symbol": getattr(order, "symbol", None),
                    "strategy_id": getattr(order, "strategy_id", None),
                    "fill_qty": str(missing_qty),
                    "fill_price": str(filled_avg_price),
                    "action": "synthetic_fill_injected",
                },
            )
            return True
    except Exception as exc:
        logger.warning(
            "Reconciliation DB fill backfill failed",
            extra={"client_order_id": getattr(order, "client_order_id", "?"), "error": str(exc)},
        )
        return False


def backfill_missing_fills_scan(
    db_client: DatabaseClient,
    limit: int = 200,
) -> int:
    """Scan for terminal orders missing fills and backfill them.

    Args:
        db_client: Database client for order operations.
        limit: Max orders to scan per run.

    Returns:
        Count of orders backfilled.
    """
    try:
        missing_fill_orders = db_client.get_filled_orders_missing_fills(limit=limit)
        backfilled = 0
        for db_order in missing_fill_orders:
            if backfill_fill_metadata_from_order(db_order, db_client):
                backfilled += 1
        return backfilled
    except Exception as exc:
        logger.warning(
            "Reconciliation backfill scan failed",
            extra={"error": str(exc)},
        )
        return 0
