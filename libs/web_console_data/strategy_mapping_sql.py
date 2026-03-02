"""Re-export from canonical location ``libs.data.sql.strategy_mapping_sql``.

The shared SQL lives in ``libs/data/sql/`` so that consumers outside the
web-console layer (e.g. the execution gateway) can import it without
pulling in web-console dependencies (crypto, caching).
"""

from libs.data.sql.strategy_mapping_sql import (  # noqa: F401
    ACTIVE_SYMBOLS_CTE,
    ALL_POSITIONS_QUERY,
    AMBIGUOUS_COUNT_CTE,
    MAPPED_POSITIONS_QUERY,
    SCOPED_FALLBACK_QUERY,
    SYMBOL_STRATEGY_CTE,
    UNMAPPED_COUNT_CTE,
    VIEW_ALL_FALLBACK_QUERY,
)

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
