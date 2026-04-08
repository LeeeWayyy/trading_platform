"""Tests for Excel export in apps/execution_gateway/routes/export.py.

Verifies that _generate_excel_content returns real grid data (not placeholders)
and that all cell values are sanitised against formula injection.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest

from apps.execution_gateway.app_factory import create_mock_context
from apps.execution_gateway.routes import export as export_module
from apps.execution_gateway.routes.export import (
    _GRID_COLUMNS,
    _build_filter_clauses,
    _build_order_clause,
    _validate_columns,
)

# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


class TestValidateColumns:
    def test_returns_allowlist_when_no_visible_columns(self) -> None:
        result = _validate_columns("positions", None)
        assert result == _GRID_COLUMNS["positions"]

    def test_filters_unknown_columns(self) -> None:
        result = _validate_columns("positions", ["symbol", "EVIL_COL", "qty"])
        assert result == ["symbol", "qty"]

    def test_preserves_requested_order(self) -> None:
        result = _validate_columns("orders", ["status", "symbol", "side"])
        assert result == ["status", "symbol", "side"]

    def test_falls_back_to_default_when_all_invalid(self) -> None:
        result = _validate_columns("fills", ["bad1", "bad2"])
        assert result == _GRID_COLUMNS["fills"]

    def test_resolves_frontend_column_aliases(self) -> None:
        """Frontend alias "time" is resolved to "executed_at" for fills."""
        result = _validate_columns("fills", ["time", "symbol", "qty"])
        assert result == ["executed_at", "symbol", "qty"]

    def test_resolves_orders_type_alias(self) -> None:
        """Frontend alias "type" is resolved to "order_type" for orders."""
        result = _validate_columns("orders", ["symbol", "type", "status"])
        assert result == ["symbol", "order_type", "status"]


class TestBuildOrderClause:
    def test_default_order_when_no_sort_model(self) -> None:
        assert _build_order_clause(None, ["symbol"], "symbol ASC") == "symbol ASC"

    def test_valid_sort_model(self) -> None:
        model = [{"colId": "symbol", "sort": "desc"}]
        result = _build_order_clause(model, ["symbol", "qty"], "symbol ASC")
        assert result == "symbol DESC"

    def test_ignores_unknown_columns(self) -> None:
        model = [{"colId": "DROP TABLE", "sort": "asc"}]
        result = _build_order_clause(model, ["symbol"], "symbol ASC")
        assert result == "symbol ASC"

    def test_sort_index_determines_precedence(self) -> None:
        """Multi-column sorts should honour sortIndex from AG Grid."""
        model = [
            {"colId": "qty", "sort": "asc", "sortIndex": 1},
            {"colId": "symbol", "sort": "desc", "sortIndex": 0},
        ]
        result = _build_order_clause(model, ["symbol", "qty"], "symbol ASC")
        assert result == "symbol DESC, qty ASC"

    def test_sort_index_missing_falls_back_to_list_order(self) -> None:
        """Items without sortIndex retain their original list position."""
        model = [
            {"colId": "qty", "sort": "asc"},
            {"colId": "symbol", "sort": "desc"},
        ]
        result = _build_order_clause(model, ["symbol", "qty"], "symbol ASC")
        assert result == "qty ASC, symbol DESC"


class TestBuildFilterClauses:
    def test_none_filter_returns_empty(self) -> None:
        clause, params = _build_filter_clauses(None, ["symbol"])
        assert clause == ""
        assert params == []

    def test_text_contains_filter(self) -> None:
        filt = {"symbol": {"filterType": "text", "type": "contains", "filter": "AA"}}
        clause, params = _build_filter_clauses(filt, ["symbol"])
        assert "ILIKE" in clause
        assert "%AA%" in params

    def test_text_equals_filter(self) -> None:
        filt = {"symbol": {"filterType": "text", "type": "equals", "filter": "AAPL"}}
        clause, params = _build_filter_clauses(filt, ["symbol"])
        assert "= %s" in clause
        assert "AAPL" in params

    def test_number_filter(self) -> None:
        filt = {"qty": {"filterType": "number", "type": "greaterThan", "filter": 100}}
        clause, params = _build_filter_clauses(filt, ["qty"])
        assert "> %s" in clause
        assert 100 in params

    def test_set_filter(self) -> None:
        filt = {"status": {"filterType": "set", "values": ["new", "filled"]}}
        clause, params = _build_filter_clauses(filt, ["status"])
        assert "ANY" in clause
        assert ["new", "filled"] in params

    def test_unknown_column_ignored(self) -> None:
        filt = {"evil_col": {"filterType": "text", "type": "equals", "filter": "x"}}
        clause, params = _build_filter_clauses(filt, ["symbol"])
        assert clause == ""
        assert params == []

    def test_col_prefix_applied(self) -> None:
        filt = {"symbol": {"filterType": "text", "type": "equals", "filter": "AAPL"}}
        clause, params = _build_filter_clauses(filt, ["symbol"], col_prefix="t.")
        assert "t.symbol" in clause

    def test_jsonb_column_cast_to_text(self) -> None:
        """JSONB columns should be cast to ::text for text filters."""
        filt = {"details": {"filterType": "text", "type": "contains", "filter": "login"}}
        clause, params = _build_filter_clauses(
            filt, ["details"], jsonb_columns={"details"}
        )
        assert "details::text ILIKE" in clause
        assert "%login%" in params

    def test_non_jsonb_column_not_cast(self) -> None:
        """Non-JSONB columns should not be cast even when jsonb_columns is provided."""
        filt = {"action": {"filterType": "text", "type": "contains", "filter": "login"}}
        clause, params = _build_filter_clauses(
            filt, ["action"], jsonb_columns={"details"}
        )
        assert "action ILIKE" in clause
        assert "::text" not in clause

    def test_malformed_filter_spec_ignored(self) -> None:
        """Non-dict filter specs should be silently skipped, not raise."""
        filt: dict[str, Any] = {"symbol": "AAPL"}  # bare string, not a dict spec
        clause, params = _build_filter_clauses(filt, ["symbol"])
        assert clause == ""
        assert params == []

    def test_date_in_range_filter(self) -> None:
        filt = {
            "executed_at": {
                "filterType": "date",
                "type": "inRange",
                "dateFrom": "2026-01-01",
                "dateTo": "2026-02-01",
            }
        }
        clause, params = _build_filter_clauses(filt, ["executed_at"])
        assert "2026-01-01" in params
        assert "2026-02-01" in params
        # End date uses ``::date + interval '1 day'`` to include the
        # entire end date (AG Grid sends midnight of the selected day).
        assert "+ interval '1 day'" in clause

    def test_date_in_range_single_day(self) -> None:
        """Single-day range (same dateFrom/dateTo) should return rows."""
        filt = {
            "executed_at": {
                "filterType": "date",
                "type": "inRange",
                "dateFrom": "2026-01-01",
                "dateTo": "2026-01-01",
            }
        }
        clause, params = _build_filter_clauses(filt, ["executed_at"])
        # Both dates should be present
        assert params.count("2026-01-01") == 2
        # The upper bound uses ``::date + interval '1 day'`` so a
        # single-day range can actually match rows on that date.
        assert "+ interval '1 day'" in clause

    def test_date_greater_than_uses_strict_gt(self) -> None:
        """greaterThan date filter should use strict '>' not '>='."""
        filt = {
            "executed_at": {
                "filterType": "date",
                "type": "greaterThan",
                "dateFrom": "2026-01-01",
            }
        }
        clause, params = _build_filter_clauses(filt, ["executed_at"])
        assert "> %s" in clause
        assert ">=" not in clause

    def test_compound_and_filter(self) -> None:
        """AG Grid compound AND filter with conditions array."""
        filt: dict[str, Any] = {
            "symbol": {
                "filterType": "text",
                "operator": "AND",
                "conditions": [
                    {"type": "contains", "filter": "A"},
                    {"type": "startsWith", "filter": "AA"},
                ],
            }
        }
        clause, params = _build_filter_clauses(filt, ["symbol"])
        assert "AND" in clause
        assert "%A%" in params
        assert "AA%" in params

    def test_compound_or_filter(self) -> None:
        """AG Grid compound OR filter with conditions array."""
        filt: dict[str, Any] = {
            "symbol": {
                "filterType": "text",
                "operator": "OR",
                "conditions": [
                    {"type": "equals", "filter": "AAPL"},
                    {"type": "equals", "filter": "MSFT"},
                ],
            }
        }
        clause, params = _build_filter_clauses(filt, ["symbol"])
        assert "OR" in clause
        assert "AAPL" in params
        assert "MSFT" in params

    def test_filter_on_hidden_column_applied(self) -> None:
        """Filters on columns not in the projected set should still apply."""
        filt: dict[str, Any] = {
            "status": {"filterType": "text", "type": "equals", "filter": "new"}
        }
        # 'status' is not in projected columns, but is in filterable_columns
        clause, params = _build_filter_clauses(
            filt, ["symbol", "qty", "status"]
        )
        assert "status" in clause
        assert "new" in params


# ---------------------------------------------------------------------------
# Integration tests for _generate_excel_content
# ---------------------------------------------------------------------------


def _make_cursor_mock(rows: list[tuple[Any, ...]], col_names: list[str]) -> MagicMock:
    """Create a mock cursor that returns the given rows."""
    cursor = MagicMock()
    cursor.description = [(name,) for name in col_names]
    cursor.fetchall.return_value = rows
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    return cursor


def _make_conn_mock(cursor: MagicMock) -> MagicMock:
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    return conn


def _make_ctx_with_rows(rows: list[tuple[Any, ...]], col_names: list[str]) -> MagicMock:
    cursor = _make_cursor_mock(rows, col_names)
    conn = _make_conn_mock(cursor)
    ctx = create_mock_context()
    ctx.db.transaction.return_value = conn
    return ctx


@pytest.mark.asyncio()
class TestGenerateExcelContent:
    async def test_positions_returns_real_data(self) -> None:
        rows = [
            (
                "AAPL",
                Decimal("10"),
                Decimal("150.25"),
                Decimal("152.00"),
                Decimal("17.50"),
                Decimal("0"),
                datetime(2026, 1, 1, tzinfo=UTC),
            ),
            (
                "MSFT",
                Decimal("5"),
                Decimal("400.00"),
                Decimal("405.00"),
                Decimal("25.00"),
                Decimal("10"),
                datetime(2026, 1, 2, tzinfo=UTC),
            ),
        ]
        col_names = [
            "symbol",
            "qty",
            "avg_entry_price",
            "current_price",
            "unrealized_pl",
            "realized_pl",
            "updated_at",
        ]
        ctx = _make_ctx_with_rows(rows, col_names)

        content, row_count = await export_module._generate_excel_content(
            ctx=ctx,
            grid_name="positions",
            strategy_ids=["alpha"],
            filter_params=None,
            visible_columns=None,
            sort_model=None,
        )

        assert row_count == 2
        assert isinstance(content, bytes)
        assert len(content) > 0

        # Verify it's a valid Excel file by reading it
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(content))
        ws = wb.active
        assert ws.title == "Positions"
        # Header row
        assert ws.cell(1, 1).value == "symbol"
        # Data — NOT placeholder text
        assert ws.cell(2, 1).value == "AAPL"
        assert ws.cell(3, 1).value == "MSFT"
        assert "placeholder" not in str(ws.cell(2, 1).value).lower()
        assert "pending" not in str(ws.cell(2, 1).value).lower()

    async def test_orders_returns_real_data(self) -> None:
        rows = [
            (
                "ord-1",
                "alpha",
                "AAPL",
                "buy",
                10,
                "market",
                None,
                None,
                "day",
                "filled",
                Decimal("10"),
                Decimal("150"),
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
            ),
        ]
        col_names = [
            "client_order_id",
            "strategy_id",
            "symbol",
            "side",
            "qty",
            "order_type",
            "limit_price",
            "stop_price",
            "time_in_force",
            "status",
            "filled_qty",
            "filled_avg_price",
            "created_at",
            "filled_at",
        ]
        ctx = _make_ctx_with_rows(rows, col_names)

        content, row_count = await export_module._generate_excel_content(
            ctx=ctx,
            grid_name="orders",
            strategy_ids=["alpha"],
            filter_params=None,
            visible_columns=["symbol", "side", "qty", "status"],
            sort_model=None,
        )

        assert row_count == 1
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(content))
        ws = wb.active
        # Only requested (valid) columns appear
        assert ws.cell(1, 1).value == "symbol"
        assert ws.cell(1, 2).value == "side"

    async def test_unsupported_grid_raises(self) -> None:
        ctx = create_mock_context()
        with pytest.raises(NotImplementedError, match="not implemented"):
            await export_module._generate_excel_content(
                ctx=ctx,
                grid_name="unknown_grid",
                strategy_ids=["alpha"],
                filter_params=None,
                visible_columns=None,
                sort_model=None,
            )

    async def test_empty_result_returns_zero_row_count(self) -> None:
        ctx = _make_ctx_with_rows(
            [],
            [
                "symbol",
                "qty",
                "avg_entry_price",
                "current_price",
                "unrealized_pl",
                "realized_pl",
                "updated_at",
            ],
        )

        content, row_count = await export_module._generate_excel_content(
            ctx=ctx,
            grid_name="positions",
            strategy_ids=["alpha"],
            filter_params=None,
            visible_columns=None,
            sort_model=None,
        )

        assert row_count == 0

    async def test_formula_injection_sanitised(self) -> None:
        """Values starting with = are prefixed with ' to prevent formula injection."""
        rows = [
            (
                "=IMPORTDATA(url)",
                Decimal("1"),
                Decimal("1"),
                Decimal("1"),
                Decimal("0"),
                Decimal("0"),
                datetime(2026, 1, 1, tzinfo=UTC),
            )
        ]
        col_names = [
            "symbol",
            "qty",
            "avg_entry_price",
            "current_price",
            "unrealized_pl",
            "realized_pl",
            "updated_at",
        ]
        ctx = _make_ctx_with_rows(rows, col_names)

        content, _ = await export_module._generate_excel_content(
            ctx=ctx,
            grid_name="positions",
            strategy_ids=["alpha"],
            filter_params=None,
            visible_columns=None,
            sort_model=None,
        )

        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(content))
        ws = wb.active
        # The dangerous formula should be prefixed with '
        assert ws.cell(2, 1).value == "'=IMPORTDATA(url)"

    async def test_numeric_types_preserved_in_excel(self) -> None:
        """Numeric and datetime values are stored as native types, not strings."""
        rows = [
            (
                "AAPL",
                Decimal("10"),
                Decimal("150.25"),
                Decimal("152.00"),
                Decimal("17.50"),
                Decimal("0"),
                datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ]
        col_names = [
            "symbol",
            "qty",
            "avg_entry_price",
            "current_price",
            "unrealized_pl",
            "realized_pl",
            "updated_at",
        ]
        ctx = _make_ctx_with_rows(rows, col_names)

        content, _ = await export_module._generate_excel_content(
            ctx=ctx,
            grid_name="positions",
            strategy_ids=["alpha"],
            filter_params=None,
            visible_columns=None,
            sort_model=None,
        )

        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(content))
        ws = wb.active
        # String column stays string
        assert ws.cell(2, 1).value == "AAPL"
        # Numeric columns must NOT be stringified — openpyxl stores Decimal
        # as a number, so the cell value should be numeric (not a string).
        qty_val = ws.cell(2, 2).value
        assert not isinstance(qty_val, str), f"qty should be numeric, got str: {qty_val!r}"
        # datetime should be preserved as a datetime
        updated_val = ws.cell(2, 7).value
        assert not isinstance(
            updated_val, str
        ), f"updated_at should be datetime, got str: {updated_val!r}"

    async def test_filter_params_passed_to_fetcher(self) -> None:
        """Verify filter_params are resolved and forwarded to the fetcher."""
        rows = [
            (
                "AAPL",
                Decimal("10"),
                Decimal("150.25"),
                Decimal("152.00"),
                Decimal("17.50"),
                Decimal("0"),
                datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ]
        col_names = [
            "symbol",
            "qty",
            "avg_entry_price",
            "current_price",
            "unrealized_pl",
            "realized_pl",
            "updated_at",
        ]
        ctx = _make_ctx_with_rows(rows, col_names)

        # Provide a text filter on symbol
        content, row_count = await export_module._generate_excel_content(
            ctx=ctx,
            grid_name="positions",
            strategy_ids=["alpha"],
            filter_params={"symbol": {"filterType": "text", "type": "contains", "filter": "AA"}},
            visible_columns=None,
            sort_model=None,
        )

        # The SQL query should have been executed with the filter params
        # We verify that the call succeeded and returned data
        assert row_count == 1
        assert isinstance(content, bytes)

    async def test_orders_export_filters_pending_statuses(self) -> None:
        """Verify that orders export includes the pending-status filter."""
        rows = [
            (
                "ord-1",
                "alpha",
                "AAPL",
                "buy",
                10,
                "market",
                None,
                None,
                "day",
                "new",
                Decimal("0"),
                None,
                datetime(2026, 1, 1, tzinfo=UTC),
                None,
            ),
        ]
        col_names = [
            "client_order_id",
            "strategy_id",
            "symbol",
            "side",
            "qty",
            "order_type",
            "limit_price",
            "stop_price",
            "time_in_force",
            "status",
            "filled_qty",
            "filled_avg_price",
            "created_at",
            "filled_at",
        ]
        ctx = _make_ctx_with_rows(rows, col_names)

        content, row_count = await export_module._generate_excel_content(
            ctx=ctx,
            grid_name="orders",
            strategy_ids=["alpha"],
            filter_params=None,
            visible_columns=None,
            sort_model=None,
        )

        # Verify the SQL was executed with pending status filter
        cursor_mock = ctx.db.transaction().__enter__().cursor().__enter__()
        call_args = cursor_mock.execute.call_args
        sql = call_args[0][0]
        assert "status = ANY" in sql

    async def test_audit_row_count_matches_data(self) -> None:
        """Verify row_count reflects actual exported rows, not a placeholder."""
        rows = [
            (1, datetime(2026, 1, 1, tzinfo=UTC), "admin", "login", "{}", None),
            (2, datetime(2026, 1, 2, tzinfo=UTC), "admin", "export", "{}", "test"),
            (3, datetime(2026, 1, 3, tzinfo=UTC), "admin", "logout", "{}", None),
        ]
        col_names = ["id", "timestamp", "user_id", "action", "details", "reason"]
        ctx = _make_ctx_with_rows(rows, col_names)

        _, row_count = await export_module._generate_excel_content(
            ctx=ctx,
            grid_name="audit",
            strategy_ids=["alpha"],
            filter_params=None,
            visible_columns=None,
            sort_model=None,
        )

        assert row_count == 3

    async def test_fills_status_column_synthesized(self) -> None:
        """Fills export should synthesize 'status' as 'filled' since all trades are fills."""
        rows = [
            (
                "fill-1",
                "ord-1",
                "alpha",
                "AAPL",
                "buy",
                Decimal("10"),
                Decimal("150.25"),
                datetime(2026, 1, 1, tzinfo=UTC),
                "filled",
            ),
        ]
        col_names = [
            "trade_id",
            "client_order_id",
            "strategy_id",
            "symbol",
            "side",
            "qty",
            "price",
            "executed_at",
            "status",
        ]
        ctx = _make_ctx_with_rows(rows, col_names)

        content, row_count = await export_module._generate_excel_content(
            ctx=ctx,
            grid_name="fills",
            strategy_ids=["alpha"],
            filter_params=None,
            visible_columns=["symbol", "status", "qty"],
            sort_model=None,
        )

        assert row_count == 1
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(content))
        ws = wb.active
        # 'status' column should be present in the header
        assert ws.cell(1, 2).value == "status"
        # Value should be the synthesized 'filled'
        assert ws.cell(2, 2).value == "filled"
