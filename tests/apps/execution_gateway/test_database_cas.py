from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from apps.execution_gateway.database import DatabaseClient


class _Cursor:
    def __init__(self, row: dict[str, Any]):
        self.row = row
        self.last_sql: str | None = None
        self.last_params: tuple[Any, ...] | None = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql: str, params: tuple[Any, ...]):
        self.last_sql = sql
        self.last_params = params

    def fetchone(self):
        return self.row


class _Conn:
    def __init__(self, row: dict[str, Any]):
        self.cursor_obj = _Cursor(row)

    def cursor(self, **_kwargs):
        return self.cursor_obj


def _make_row(**overrides: Any) -> dict[str, Any]:
    base = {
        "client_order_id": "abc",
        "strategy_id": "s1",
        "symbol": "AAPL",
        "side": "buy",
        "qty": 1,
        "order_type": "market",
        "time_in_force": "day",
        "status": "filled",
        "retry_count": 0,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "filled_qty": Decimal("1"),
    }
    base.update(overrides)
    return base


def test_update_order_status_cas_allows_filled_order_updates():
    """Verify that filled orders can be updated (for price corrections and late fills).

    The terminal lock should allow updates to filled orders while blocking
    updates to other terminal statuses like canceled, rejected, etc.
    """
    row = _make_row()
    db = DatabaseClient("postgresql://user:pass@localhost/db")
    conn = _Conn(row)

    updated = db.update_order_status_cas(
        client_order_id="abc",
        status="filled",
        broker_updated_at=datetime.now(UTC),
        status_rank=5,
        source_priority=3,
        filled_qty=Decimal("2"),
        filled_avg_price=Decimal("10"),
        filled_at=datetime.now(UTC),
        broker_order_id="broker-1",
        conn=conn,
    )

    assert updated is not None
    assert conn.cursor_obj.last_sql is not None
    # Terminal lock: (is_terminal = FALSE OR status = 'filled')
    # This allows updates to non-terminal orders OR filled orders specifically
    assert "is_terminal = FALSE OR status = 'filled'" in conn.cursor_obj.last_sql


def test_update_order_status_cas_includes_tiebreakers():
    row = _make_row()
    db = DatabaseClient("postgresql://user:pass@localhost/db")
    conn = _Conn(row)

    db.update_order_status_cas(
        client_order_id="abc",
        status="filled",
        broker_updated_at=datetime.now(UTC),
        status_rank=5,
        source_priority=3,
        filled_qty=Decimal("2"),
        filled_avg_price=Decimal("10"),
        filled_at=datetime.now(UTC),
        broker_order_id="broker-1",
        conn=conn,
    )

    sql = conn.cursor_obj.last_sql or ""
    assert "status_rank <" in sql
    assert "filled_qty <" in sql
    assert "source_priority <" in sql
