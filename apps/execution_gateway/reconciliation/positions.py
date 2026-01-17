"""Position reconciliation logic.

This module handles synchronization of positions between the broker
and the local database.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.execution_gateway.alpaca_client import AlpacaExecutor
    from apps.execution_gateway.database import DatabaseClient

logger = logging.getLogger(__name__)


def reconcile_positions(
    db_client: DatabaseClient,
    alpaca_client: AlpacaExecutor,
) -> dict[str, int]:
    """Reconcile positions between broker and database.

    This function:
    1. Fetches all positions from the broker
    2. Updates local DB with broker position snapshots
    3. Sets positions to flat (qty=0) for symbols in DB but not at broker

    Args:
        db_client: Database client for position operations.
        alpaca_client: Alpaca client for fetching broker positions.

    Returns:
        Dict with counts: {"updated": N, "flattened": M}

    Example:
        >>> result = reconcile_positions(db_client, alpaca_client)
        >>> print(f"Updated {result['updated']}, flattened {result['flattened']}")
    """
    broker_positions = alpaca_client.get_all_positions()
    broker_by_symbol = {pos["symbol"]: pos for pos in broker_positions}

    db_positions = db_client.get_all_positions()
    db_symbols = {pos.symbol for pos in db_positions}

    updated_count = 0
    flattened_count = 0

    # Upsert broker positions to DB
    for symbol, broker_pos in broker_by_symbol.items():
        qty = Decimal(str(broker_pos.get("qty") or 0))
        avg_entry_price = Decimal(str(broker_pos.get("avg_entry_price") or 0))
        current_price = broker_pos.get("current_price")
        updated_at = datetime.now(UTC)

        db_client.upsert_position_snapshot(
            symbol=symbol,
            qty=qty,
            avg_entry_price=avg_entry_price,
            current_price=current_price,
            updated_at=updated_at,
        )
        updated_count += 1

    # Positions in DB but not in broker -> set to flat
    for db_symbol in db_symbols - set(broker_by_symbol.keys()):
        db_client.upsert_position_snapshot(
            symbol=db_symbol,
            qty=Decimal("0"),
            avg_entry_price=Decimal("0"),
            current_price=None,
            updated_at=datetime.now(UTC),
        )
        flattened_count += 1
        logger.info(
            "Position flattened - not found at broker",
            extra={"symbol": db_symbol},
        )

    return {"updated": updated_count, "flattened": flattened_count}
