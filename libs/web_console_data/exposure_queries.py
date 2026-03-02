"""Exposure data queries for strategy-scoped position access (P6T15/T15.3).

Uses shared SQL from ``strategy_mapping_sql`` to maintain parity with the
execution gateway (``DatabaseClient.get_positions_for_strategies``).

Do NOT use ``StrategyScopedDataAccess.get_positions()`` — it references a
non-existent ``strategy_id`` column on the positions table.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from psycopg import IsolationLevel
from psycopg.rows import dict_row

from libs.web_console_data.strategy_mapping_sql import (
    ALL_POSITIONS_QUERY,
    AMBIGUOUS_COUNT_CTE,
    SCOPED_FALLBACK_QUERY,
    SYMBOL_STRATEGY_CTE,
    VIEW_ALL_FALLBACK_QUERY,
)

logger = logging.getLogger(__name__)


class ExposureQueryResult(NamedTuple):
    """Result of a strategy-scoped position query.

    Attributes:
        positions: Position dicts with keys matching the positions table columns.
            Each dict includes an additional ``strategy`` key from the mapping.
        excluded_symbol_count: Number of symbols excluded because they are
            traded by more than one strategy (fail-closed).
        unmapped_position_count: Number of non-zero positions with no
            order-to-strategy mapping at all.
    """

    positions: list[dict[str, Any]]
    excluded_symbol_count: int
    unmapped_position_count: int = 0


# Strategy-scoped query: positions + excluded count via CTEs.
# Omits unmapped count (service discards it for non-VIEW_ALL users).
# Parameters: (strategies, strategies) — first for BOOL_OR, second for WHERE.
_SCOPED_EXPOSURE_QUERY = f"""\
    WITH {SYMBOL_STRATEGY_CTE},
    {AMBIGUOUS_COUNT_CTE}
    SELECT p.symbol, p.qty, p.avg_entry_price, p.current_price,
           p.unrealized_pl, p.realized_pl, p.updated_at,
           p.last_trade_at, ss.strategy,
           (SELECT cnt FROM ambiguous) AS _excluded_count
    FROM positions p
    JOIN symbol_strategy ss ON p.symbol = ss.symbol
    WHERE p.qty != 0
      AND ss.strategy = ANY(%s)
    ORDER BY p.symbol"""


async def get_strategy_positions(
    strategies: list[str] | None,
    db_pool: Any,
) -> ExposureQueryResult:
    """Fetch positions scoped to *strategies* with fail-closed mapping.

    Returns positions where exactly one strategy has traded the symbol.
    Symbols traded by multiple strategies are excluded (fail-closed).
    Positions with ``qty = 0`` are filtered out.

    When *strategies* is ``None``, all mapped positions are returned
    (used for VIEW_ALL_STRATEGIES users).  When *strategies* is an
    empty list, an empty result is returned immediately.

    The positions and excluded-symbol count are computed in a single SQL
    statement (via CTEs) to guarantee a consistent snapshot.  When the main
    CTE returns zero rows, a single fallback query fires for both counts.
    The entire operation runs under ``REPEATABLE READ`` so all statements
    share the same database snapshot.

    Args:
        strategies: Strategy IDs the user is authorised for, or ``None``
            for all strategies (VIEW_ALL_STRATEGIES).
        db_pool: psycopg (v3) async connection pool (must support
            ``async with pool.connection()``).

    Returns:
        ``ExposureQueryResult`` with positions and excluded symbol count.
    """
    if strategies is not None and not strategies:
        return ExposureQueryResult(positions=[], excluded_symbol_count=0)

    view_all = strategies is None

    async with db_pool.connection() as conn:
        # REPEATABLE READ transaction: all statements see the same snapshot,
        # even when the fallback count query fires on zero rows.
        # psycopg 3.x requires isolation level to be set on the connection
        # before starting the transaction (transaction() does not accept it).
        # Restore to server default (READ COMMITTED) after to prevent leaking
        # the stricter isolation level to subsequent pool borrowers.
        await conn.set_isolation_level(IsolationLevel.REPEATABLE_READ)
        try:
            async with conn.transaction():
                async with conn.cursor(row_factory=dict_row) as cur:
                    if view_all:
                        await cur.execute(ALL_POSITIONS_QUERY)
                        rows = await cur.fetchall()
                    else:
                        await cur.execute(
                            _SCOPED_EXPOSURE_QUERY, (strategies, strategies)
                        )
                        rows = await cur.fetchall()

                    excluded_count = 0
                    unmapped_count = 0
                    if rows:
                        excluded_count = int(rows[0].get("_excluded_count", 0))
                        unmapped_count = int(rows[0].get("_unmapped_count", 0))
                        for row in rows:
                            row.pop("_excluded_count", None)
                            row.pop("_unmapped_count", None)
                    else:
                        # No attributable positions — single fallback for counts.
                        # Same REPEATABLE READ snapshot as the main query above.
                        if view_all:
                            await cur.execute(VIEW_ALL_FALLBACK_QUERY)
                            counts_row = await cur.fetchone()
                            if counts_row:
                                excluded_count = int(counts_row["excluded_count"])
                                unmapped_count = int(counts_row["unmapped_count"])
                        else:
                            # Scoped fallback returns excluded_count only
                            # (unmapped is irrelevant for non-VIEW_ALL users).
                            await cur.execute(SCOPED_FALLBACK_QUERY, (strategies,))
                            counts_row = await cur.fetchone()
                            if counts_row:
                                excluded_count = int(counts_row["excluded_count"])
        finally:
            await conn.set_isolation_level(None)

    return ExposureQueryResult(
        positions=rows,
        excluded_symbol_count=excluded_count,
        unmapped_position_count=unmapped_count,
    )


__all__ = [
    "ExposureQueryResult",
    "get_strategy_positions",
]
