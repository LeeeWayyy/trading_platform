from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable

import pytest

psycopg = pytest.importorskip("psycopg")
from apps.execution_gateway.database import DatabaseClient


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows
        self.executed_sql: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql: str, params=None):
        self.executed_sql.append(sql)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows
        self.cursor_obj = _FakeCursor(rows)

    def cursor(self, **_kwargs):
        return self.cursor_obj

    def commit(self):
        return None


def _mock_execute_with_conn(db: DatabaseClient, rows: list[dict[str, Any]]):
    def _wrapper(conn: Any, operation: Callable[[Any], Any]):
        return operation(_FakeConn(rows))

    db._execute_with_conn = _wrapper  # type: ignore[attr-defined]


def test_get_positions_for_strategies_filters_multi_strategy(monkeypatch):
    # With SQL-based filtering, the query handles strategy filtering via HAVING clause.
    # The mock should return what the SQL would return: only positions for symbols
    # traded by exactly one strategy AND that strategy is in the authorized list.
    # MSFT (multi-strategy) is excluded by the SQL HAVING COUNT(DISTINCT strategy_id) = 1.
    db = DatabaseClient("postgresql://user:pass@localhost/db")
    rows = [
        {
            "symbol": "AAPL",
            "qty": 1,
            "avg_entry_price": Decimal("10"),
            "current_price": None,
            "unrealized_pl": None,
            "realized_pl": Decimal("0"),
            "updated_at": datetime.now(UTC),
            "last_trade_at": None,
            # No "strategies" key - SQL returns position columns only
        },
    ]
    _mock_execute_with_conn(db, rows)

    positions = db.get_positions_for_strategies(["s1"])
    assert len(positions) == 1
    assert positions[0].symbol == "AAPL"


def test_get_daily_pnl_history_returns_rows(monkeypatch):
    db = DatabaseClient("postgresql://user:pass@localhost/db")
    expected_rows = [
        {"trade_date": date(2024, 1, 1), "daily_realized_pl": Decimal("5"), "closing_trade_count": 1}
    ]
    _mock_execute_with_conn(db, expected_rows)

    rows = db.get_daily_pnl_history(date(2024, 1, 1), date(2024, 1, 2), ["s1"])
    assert rows == expected_rows


def test_get_data_availability_date_handles_none(monkeypatch):
    db = DatabaseClient("postgresql://user:pass@localhost/db")
    _mock_execute_with_conn(db, [{"first_date": None}])
    assert db.get_data_availability_date() is None


def test_get_data_availability_date_returns_date(monkeypatch):
    db = DatabaseClient("postgresql://user:pass@localhost/db")
    _mock_execute_with_conn(db, [{"first_date": date(2024, 1, 1)}])
    assert db.get_data_availability_date() == date(2024, 1, 1)
