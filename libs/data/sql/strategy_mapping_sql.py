"""Shared SQL for fail-closed symbol-to-strategy position mapping.

Both the execution gateway (sync psycopg v3) and the exposure adapter
(async psycopg v3) use the same fail-closed mapping logic to attribute
positions to strategies via historical order data.  This module provides
the canonical SQL so changes are made in one place.

Design:
    The ``positions`` table is symbol-scoped and does not store
    ``strategy_id``.  We infer ownership by inspecting ``orders``:
    a symbol is attributed to a strategy only when exactly ONE strategy
    has ever traded it (fail-closed).  Symbols traded by multiple
    strategies are excluded to prevent cross-strategy data leakage.

This module is shared across services; see the module-level note for rationale.

Note:
    This module lives in ``libs.data.sql`` (not ``libs.web_console_data``)
    so that consumers such as the execution gateway can import shared SQL
    without pulling in web-console dependencies (crypto, caching, etc.).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Shared CTE: symbols with non-zero positions.
# Reused across all queries to avoid repeated scans.
# ---------------------------------------------------------------------------
ACTIVE_SYMBOLS_CTE = """\
    active_symbols AS (
        SELECT symbol FROM positions WHERE qty != 0
    )"""

# ---------------------------------------------------------------------------
# Core CTE: maps each symbol to its single owning strategy.
# Symbols traded by multiple strategies are excluded (fail-closed).
# ---------------------------------------------------------------------------
SYMBOL_STRATEGY_CTE = f"""\
    {ACTIVE_SYMBOLS_CTE},
    symbol_strategy AS (
        SELECT
            symbol,
            (ARRAY_AGG(DISTINCT strategy_id))[1] AS strategy
        FROM orders
        WHERE strategy_id IS NOT NULL
          AND symbol IN (SELECT symbol FROM active_symbols)
        GROUP BY symbol
        HAVING COUNT(DISTINCT strategy_id) = 1
    )"""

# ---------------------------------------------------------------------------
# Mapped positions query: joins positions to their single-strategy owner.
# Parameters: (strategies: list[str],)
# ---------------------------------------------------------------------------
MAPPED_POSITIONS_QUERY = f"""\
    WITH {SYMBOL_STRATEGY_CTE}
    SELECT p.*
    FROM positions p
    JOIN symbol_strategy ss ON p.symbol = ss.symbol
    WHERE p.qty != 0
      AND ss.strategy = ANY(%s)
    ORDER BY p.symbol"""

# ---------------------------------------------------------------------------
# Full exposure query: positions + excluded + unmapped counts via CTEs.
# Parameters: (strategies: list[str], strategies: list[str])
# — first %s is for BOOL_OR in ambiguous CTE, second for the WHERE filter.
# ---------------------------------------------------------------------------
AMBIGUOUS_COUNT_CTE = """\
    ambiguous AS (
        SELECT COUNT(*) AS cnt
        FROM (
            SELECT symbol
            FROM orders
            WHERE strategy_id IS NOT NULL
              AND symbol IN (SELECT symbol FROM active_symbols)
            GROUP BY symbol
            HAVING COUNT(DISTINCT strategy_id) > 1
              AND BOOL_OR(strategy_id = ANY(%s))
        ) AS a
    )"""

UNMAPPED_COUNT_CTE = """\
    unmapped AS (
        SELECT COUNT(*) AS cnt
        FROM positions p
        WHERE p.qty != 0
          AND NOT EXISTS (
              SELECT 1 FROM orders o
              WHERE o.symbol = p.symbol
                AND o.strategy_id IS NOT NULL
          )
    )"""

# ---------------------------------------------------------------------------
# All-strategies variant: no strategy filter on the final WHERE clause.
# Used when VIEW_ALL_STRATEGIES is granted.
# Parameters: none
# ---------------------------------------------------------------------------
ALL_POSITIONS_QUERY = f"""\
    WITH {SYMBOL_STRATEGY_CTE},
    ambiguous_all AS (
        SELECT COUNT(*) AS cnt
        FROM (
            SELECT symbol
            FROM orders
            WHERE strategy_id IS NOT NULL
              AND symbol IN (SELECT symbol FROM active_symbols)
            GROUP BY symbol
            HAVING COUNT(DISTINCT strategy_id) > 1
        ) AS a
    ),
    {UNMAPPED_COUNT_CTE}
    SELECT p.symbol, p.qty, p.avg_entry_price, p.current_price,
           p.unrealized_pl, p.realized_pl, p.updated_at,
           p.last_trade_at, ss.strategy,
           (SELECT cnt FROM ambiguous_all) AS _excluded_count,
           (SELECT cnt FROM unmapped) AS _unmapped_count
    FROM positions p
    JOIN symbol_strategy ss ON p.symbol = ss.symbol
    WHERE p.qty != 0
    ORDER BY p.symbol"""


# ---------------------------------------------------------------------------
# All-strategies fallback: counts only (when ALL_POSITIONS_QUERY returns no
# rows).  Parameters: none.
# ---------------------------------------------------------------------------
VIEW_ALL_FALLBACK_QUERY = f"""\
    WITH {ACTIVE_SYMBOLS_CTE},
    {UNMAPPED_COUNT_CTE}
    SELECT
        (SELECT COUNT(*) FROM (
            SELECT symbol
            FROM orders
            WHERE strategy_id IS NOT NULL
              AND symbol IN (SELECT symbol FROM active_symbols)
            GROUP BY symbol
            HAVING COUNT(DISTINCT strategy_id) > 1
        ) AS a) AS excluded_count,
        (SELECT cnt FROM unmapped) AS unmapped_count"""


# ---------------------------------------------------------------------------
# Strategy-scoped fallback: excluded count only (when scoped exposure query
# returns no rows).  Omits unmapped count (service discards it for
# non-VIEW_ALL users).  Parameters: (strategies: list[str],) — for BOOL_OR.
# ---------------------------------------------------------------------------
SCOPED_FALLBACK_QUERY = f"""\
    WITH {ACTIVE_SYMBOLS_CTE}
    SELECT
        (SELECT COUNT(*) FROM (
            SELECT symbol
            FROM orders
            WHERE strategy_id IS NOT NULL
              AND symbol IN (SELECT symbol FROM active_symbols)
            GROUP BY symbol
            HAVING COUNT(DISTINCT strategy_id) > 1
              AND BOOL_OR(strategy_id = ANY(%s))
        ) AS a) AS excluded_count"""


__all__ = [
    "ACTIVE_SYMBOLS_CTE",
    "SYMBOL_STRATEGY_CTE",
    "MAPPED_POSITIONS_QUERY",
    "AMBIGUOUS_COUNT_CTE",
    "UNMAPPED_COUNT_CTE",
    "ALL_POSITIONS_QUERY",
    "VIEW_ALL_FALLBACK_QUERY",
    "SCOPED_FALLBACK_QUERY",
]
