"""Unit tests for ExposureQueries adapter (P6T15/T15.3)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from libs.web_console_data.exposure_queries import (
    ExposureQueryResult,
    get_strategy_positions,
)


def _make_cursor(
    rows: list[dict[str, Any]],
    excluded_count: int = 0,
    unmapped_count: int = 0,
) -> AsyncMock:
    """Build a mock cursor that returns *rows* for the combined CTE query.

    Each row is enriched with ``_excluded_count`` and ``_unmapped_count``
    to match the combined CTE query output.  When rows are empty the
    single fallback query uses ``fetchone`` returning both counts.
    """
    enriched_rows = [
        {**row, "_excluded_count": excluded_count, "_unmapped_count": unmapped_count}
        for row in rows
    ]
    cursor = AsyncMock()
    cursor.fetchall = AsyncMock(return_value=enriched_rows)
    # Fallback: single combined query returning both counts
    cursor.fetchone = AsyncMock(
        return_value={"excluded_count": excluded_count, "unmapped_count": unmapped_count},
    )
    cursor.execute = AsyncMock()
    cursor.__aenter__ = AsyncMock(return_value=cursor)
    cursor.__aexit__ = AsyncMock(return_value=False)
    return cursor


def _make_pool(cursor: AsyncMock) -> AsyncMock:
    """Build a mock async DB pool wrapping *cursor*."""
    conn = AsyncMock()
    conn.cursor = MagicMock(return_value=cursor)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)

    # conn.transaction(...) returns an async context manager
    tx_ctx = AsyncMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=None)
    tx_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_ctx)

    pool = AsyncMock()
    pool.connection = MagicMock(return_value=conn)
    return pool


class TestGetStrategyPositions:
    @pytest.mark.asyncio()
    async def test_empty_strategies_returns_empty(self) -> None:
        result = await get_strategy_positions([], AsyncMock())
        assert result == ExposureQueryResult(
            positions=[], excluded_symbol_count=0, unmapped_position_count=0
        )

    @pytest.mark.asyncio()
    async def test_returns_positions_and_excluded_count(self) -> None:
        rows = [
            {
                "symbol": "AAPL",
                "qty": 100,
                "avg_entry_price": 150.0,
                "current_price": 155.0,
                "unrealized_pl": 500.0,
                "realized_pl": 0,
                "updated_at": "2026-01-10T12:00:00Z",
                "last_trade_at": None,
                "strategy": "alpha1",
            },
        ]
        cursor = _make_cursor(rows, excluded_count=2)
        pool = _make_pool(cursor)

        result = await get_strategy_positions(["alpha1"], pool)

        assert len(result.positions) == 1
        assert result.positions[0]["symbol"] == "AAPL"
        assert "_excluded_count" not in result.positions[0]
        assert "_unmapped_count" not in result.positions[0]
        assert result.excluded_symbol_count == 2
        # Scoped query omits unmapped count (irrelevant for non-VIEW_ALL)
        assert result.unmapped_position_count == 0

    @pytest.mark.asyncio()
    async def test_sql_uses_parameterized_query(self) -> None:
        rows = [
            {
                "symbol": "AAPL",
                "qty": 100,
                "avg_entry_price": 150.0,
                "current_price": 155.0,
                "unrealized_pl": None,
                "realized_pl": 0,
                "updated_at": "2026-01-10T12:00:00Z",
                "last_trade_at": None,
                "strategy": "alpha1",
            },
        ]
        cursor = _make_cursor(rows, excluded_count=0)
        pool = _make_pool(cursor)

        await get_strategy_positions(["alpha1", "beta1"], pool)

        # Single combined CTE query (no fallback needed because rows
        # are non-empty).  Runs inside conn.transaction(REPEATABLE_READ).
        assert cursor.execute.call_count == 1
        call = cursor.execute.call_args_list[0]
        sql = call[0][0]
        assert "ANY(%s)" in sql
        assert "BOOL_OR" in sql
        # Scoped query omits unmapped count CTE (irrelevant for non-VIEW_ALL)
        assert "unmapped" not in sql.lower()
        assert call[0][1] == (["alpha1", "beta1"], ["alpha1", "beta1"])

    @pytest.mark.asyncio()
    async def test_db_error_propagates(self) -> None:
        """Errors must NOT be swallowed — they propagate to the page layer."""
        pool = AsyncMock()
        conn = AsyncMock()
        conn.__aenter__ = AsyncMock(side_effect=ConnectionError("DB down"))
        conn.__aexit__ = AsyncMock(return_value=False)
        pool.connection = MagicMock(return_value=conn)

        with pytest.raises(ConnectionError, match="DB down"):
            await get_strategy_positions(["alpha1"], pool)

    @pytest.mark.asyncio()
    async def test_empty_rows_excluded_count_defaults_to_zero(self) -> None:
        """When no position rows match and no ambiguity, counts are 0."""
        cursor = _make_cursor([], excluded_count=0, unmapped_count=0)
        pool = _make_pool(cursor)

        result = await get_strategy_positions(["alpha1"], pool)

        assert result.positions == []
        assert result.excluded_symbol_count == 0
        assert result.unmapped_position_count == 0

    @pytest.mark.asyncio()
    async def test_empty_rows_with_excluded_uses_scoped_fallback(self) -> None:
        """When all positions are ambiguous, scoped fallback returns excluded count only."""
        cursor = _make_cursor([], excluded_count=5, unmapped_count=2)
        pool = _make_pool(cursor)

        result = await get_strategy_positions(["alpha1"], pool)

        assert result.positions == []
        assert result.excluded_symbol_count == 5
        # Scoped fallback omits unmapped count (irrelevant for non-VIEW_ALL)
        assert result.unmapped_position_count == 0
        # Main CTE + scoped fallback query = 2 calls
        # (runs inside conn.transaction(REPEATABLE_READ))
        assert cursor.execute.call_count == 2

    @pytest.mark.asyncio()
    async def test_transaction_uses_repeatable_read(self) -> None:
        """Query executes inside a REPEATABLE READ transaction."""
        from psycopg import IsolationLevel

        rows = [
            {
                "symbol": "AAPL",
                "qty": 100,
                "avg_entry_price": 150.0,
                "current_price": 155.0,
                "unrealized_pl": None,
                "realized_pl": 0,
                "updated_at": "2026-01-10T12:00:00Z",
                "last_trade_at": None,
                "strategy": "alpha1",
            },
        ]
        cursor = _make_cursor(rows)
        pool = _make_pool(cursor)
        # Extract the conn mock to inspect transaction call
        conn = pool.connection().__aenter__.return_value  # type: ignore[union-attr]

        await get_strategy_positions(["alpha1"], pool)

        # Isolation level set before transaction, then restored after
        assert conn.set_isolation_level.await_count == 2
        calls = conn.set_isolation_level.await_args_list
        assert calls[0].args == (IsolationLevel.REPEATABLE_READ,)
        assert calls[1].args == (None,)  # Restore to server default
        conn.transaction.assert_called_once_with()

    @pytest.mark.asyncio()
    async def test_none_strategies_uses_all_positions_query(self) -> None:
        """strategies=None triggers the all-strategies query (no strategy filter)."""
        rows = [
            {
                "symbol": "AAPL",
                "qty": 100,
                "avg_entry_price": 150.0,
                "current_price": 155.0,
                "unrealized_pl": 500.0,
                "realized_pl": 0,
                "updated_at": "2026-01-10T12:00:00Z",
                "last_trade_at": None,
                "strategy": "alpha1",
            },
        ]
        cursor = _make_cursor(rows, excluded_count=3, unmapped_count=1)
        pool = _make_pool(cursor)

        result = await get_strategy_positions(None, pool)

        assert len(result.positions) == 1
        assert result.excluded_symbol_count == 3
        assert result.unmapped_position_count == 1
        # All-strategies query is parameterless — single execute call
        assert cursor.execute.call_count == 1
        call = cursor.execute.call_args_list[0]
        sql = call[0][0]
        # Should NOT have strategy filter in WHERE clause
        assert "ANY(%s)" not in sql
        # Should still have the symbol_strategy CTE
        assert "symbol_strategy" in sql

    @pytest.mark.asyncio()
    async def test_none_strategies_empty_rows_uses_fallback_counts(self) -> None:
        """strategies=None with no mapped positions still returns fallback counts."""
        cursor = AsyncMock()
        cursor.fetchall = AsyncMock(return_value=[])
        cursor.fetchone = AsyncMock(
            return_value={"excluded_count": 4, "unmapped_count": 7},
        )
        cursor.execute = AsyncMock()
        cursor.__aenter__ = AsyncMock(return_value=cursor)
        cursor.__aexit__ = AsyncMock(return_value=False)
        pool = _make_pool(cursor)

        result = await get_strategy_positions(None, pool)

        assert result.positions == []
        assert result.excluded_symbol_count == 4
        assert result.unmapped_position_count == 7
        # Main query + fallback query = 2 calls
        assert cursor.execute.call_count == 2

    @pytest.mark.asyncio()
    async def test_multiple_positions_returned(self) -> None:
        rows = [
            {
                "symbol": "AAPL",
                "qty": 100,
                "avg_entry_price": 150.0,
                "current_price": 155.0,
                "unrealized_pl": None,
                "realized_pl": 0,
                "updated_at": "2026-01-10T12:00:00Z",
                "last_trade_at": None,
                "strategy": "alpha1",
            },
            {
                "symbol": "MSFT",
                "qty": -50,
                "avg_entry_price": 200.0,
                "current_price": 195.0,
                "unrealized_pl": None,
                "realized_pl": 0,
                "updated_at": "2026-01-10T12:00:00Z",
                "last_trade_at": None,
                "strategy": "alpha1",
            },
        ]
        cursor = _make_cursor(rows, excluded_count=0)
        pool = _make_pool(cursor)

        result = await get_strategy_positions(["alpha1"], pool)

        assert len(result.positions) == 2
        assert result.excluded_symbol_count == 0
        assert result.unmapped_position_count == 0
        # Verify internal columns stripped from all rows
        for pos in result.positions:
            assert "_excluded_count" not in pos
            assert "_unmapped_count" not in pos
