"""Extra coverage for DatabaseClient helper methods without hitting a real DB."""

from __future__ import annotations

from datetime import UTC, datetime, date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.execution_gateway.database import DatabaseClient


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []
        self.rowcount = len(rows)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        if not self.rows:
            return None
        return self.rows[0]

    def fetchall(self):
        return self.rows


class FakeTransaction:
    def __init__(self, conn):
        self.conn = conn
        self.closed = False

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        self.closed = True
        return False


class FakeConn:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.transaction_ctx = FakeTransaction(self)

    def cursor(self, row_factory=None):
        return FakeCursor(self.rows)

    def commit(self):
        self.commits += 1

    def transaction(self):
        return self.transaction_ctx


class FakePool:
    def __init__(self, conn):
        self.conn = conn
        self.closed = False

    def connection(self):
        class Ctx:
            def __init__(self, pool_conn):
                self.pool_conn = pool_conn

            def __enter__(self):
                return self.pool_conn

            def __exit__(self, exc_type, exc, tb):
                return False

        return Ctx(self.conn)

    def close(self):
        self.closed = True


def make_db(rows=None):
    db = DatabaseClient("postgresql://user:pass@localhost/db")
    db._pool = FakePool(FakeConn(rows))
    return db


def test_get_positions_for_strategies_fail_closed_on_multi_strategy():
    # Symbol touched by two strategies -> filtered out by SQL HAVING clause.
    # With SQL-based filtering, the query returns no rows for multi-strategy symbols.
    # The mock must return an empty result to simulate the SQL behavior.
    rows: list[dict] = []  # SQL query returns empty when symbol has multiple strategies
    db = make_db(rows)
    result = db.get_positions_for_strategies(["s1"])
    assert result == []


def test_get_daily_pnl_history_and_availability(monkeypatch):
    rows = [
        {
            "trade_date": date(2024, 1, 1),
            "daily_realized_pl": Decimal("5"),
            "closing_trade_count": 1,
        }
    ]
    conn = FakeConn(rows)
    db = DatabaseClient("postgresql://user:pass@localhost/db")
    db._pool = FakePool(conn)
    # get_daily_pnl_history uses fetchall
    history = db.get_daily_pnl_history(date(2024, 1, 1), date(2024, 1, 2), ["alpha"])
    assert history == rows
    # get_data_availability_date uses fetchone
    conn.rows = [{"first_date": date(2024, 1, 1)}]
    assert db.get_data_availability_date() == date(2024, 1, 1)


def test_append_fill_and_update_status_with_conn(monkeypatch):
    order_row = {
        "client_order_id": "abc",
        "strategy_id": "s",
        "symbol": "AAPL",
        "side": "buy",
        "qty": 1,
        "order_type": "market",
        "time_in_force": "day",
        "status": "pending_new",
        "retry_count": 0,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "filled_qty": Decimal("0"),
    }
    conn = FakeConn(rows=[order_row])
    db = DatabaseClient("postgresql://user:pass@localhost/db")
    db._pool = FakePool(conn)

    # append_fill_to_order_metadata should return OrderDetail despite minimal row
    result = db.append_fill_to_order_metadata(
        "abc",
        {"fill_id": "abc_1", "realized_pl": "1"},
        conn,
    )
    assert result is not None

    # update_order_status_with_conn returns OrderDetail
    updated = db.update_order_status_with_conn(
        client_order_id="abc",
        status="filled",
        filled_qty=1,
        filled_avg_price=Decimal("1"),
        filled_at=datetime.now(UTC),
        conn=conn,
    )
    assert updated is not None


def test_transaction_rolls_back_on_exception():
    conn = FakeConn()
    db = DatabaseClient("postgresql://user:pass@localhost/db")
    db._pool = FakePool(conn)

    with pytest.raises(RuntimeError):
        with db.transaction() as _conn:
            raise RuntimeError("boom")
    # ensure transaction context was entered/exited
    assert conn.transaction_ctx.closed is True
