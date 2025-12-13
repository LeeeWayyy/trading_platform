from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")
from apps.execution_gateway.database import DatabaseClient


class _Cursor:
    def __init__(self, row: dict[str, Any] | None):
        self.row = row
        self.last_sql: str | None = None
        self.params: tuple[Any, ...] | None = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql: str, params: tuple[Any, ...]):
        self.last_sql = sql
        self.params = params

    def fetchone(self):
        return self.row


class _Conn:
    def __init__(self, row: dict[str, Any] | None):
        self.row = row
        self.cursor_obj = _Cursor(row)

    def cursor(self, **_kwargs):
        return self.cursor_obj


class _Pool:
    def __init__(self, row: dict[str, Any] | None):
        self.row = row

    def connection(self):
        return SimpleNamespace(__enter__=lambda self=self: _Conn(self.row), __exit__=lambda *args, **kwargs: False)

    def close(self):
        return None


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


def test_append_fill_to_order_metadata_returns_order_detail(monkeypatch):
    row = _make_row(metadata={"fills": []})
    db = DatabaseClient("postgresql://user:pass@localhost/db")
    db._pool = _Pool(row)  # type: ignore[attr-defined]

    result = db.append_fill_to_order_metadata(
        client_order_id="abc",
        fill_data={"fill_id": "abc_1", "realized_pl": "5"},
        conn=db._pool.connection().__enter__(),
    )

    assert result is not None
    assert result.client_order_id == "abc"


def test_append_fill_to_order_metadata_returns_none_when_missing(monkeypatch):
    db = DatabaseClient("postgresql://user:pass@localhost/db")
    db._pool = _Pool(None)  # type: ignore[attr-defined]

    result = db.append_fill_to_order_metadata(
        client_order_id="missing",
        fill_data={"fill_id": "x", "realized_pl": "0"},
        conn=db._pool.connection().__enter__(),
    )

    assert result is None


def test_update_order_status_with_conn_updates_row(monkeypatch):
    row = _make_row(status="pending_new", filled_qty=Decimal("0"), filled_avg_price=None, filled_at=None)
    db = DatabaseClient("postgresql://user:pass@localhost/db")
    db._pool = _Pool(row)  # type: ignore[attr-defined]

    conn = db._pool.connection().__enter__()
    updated = db.update_order_status_with_conn(
        client_order_id="abc",
        status="filled",
        filled_qty=1,
        filled_avg_price=Decimal("10"),
        filled_at=datetime(2024, 1, 1, tzinfo=UTC),
        conn=conn,
    )

    assert updated is not None
    assert updated.status == "filled"
