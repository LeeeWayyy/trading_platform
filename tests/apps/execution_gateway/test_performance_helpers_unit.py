"""Unit tests for performance helpers and RBAC plumbing in execution gateway."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from apps.execution_gateway import main
from apps.execution_gateway.database import DatabaseClient
from apps.execution_gateway.schemas import Position


class _DummyCursor:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows
        self.executed = []

    def execute(self, *args, **kwargs):
        self.executed.append((args, kwargs))

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _DummyConn:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows

    def cursor(self, row_factory=None):
        return _DummyCursor(self.rows)


class _DummyRedis:
    def __init__(self):
        self.index: dict[str, set[str]] = {}
        self.deleted: list[str] = []

    # pipeline methods
    def pipeline(self):
        return self

    def sadd(self, key, value):
        self.index.setdefault(key, set()).add(value)

    def expire(self, key, ttl):
        # no-op for tests
        return None

    def execute(self):
        return None

    # direct methods used by invalidation
    def smembers(self, key):
        return self.index.get(key, set())

    def sscan_iter(self, key):
        """Iterate over set members like Redis sscan_iter."""
        return iter(self.index.get(key, set()))

    def delete(self, *keys):
        for key in keys:
            self.deleted.append(key)
            self.index.pop(key, None)


def _make_db(rows: list[dict[str, Any]]) -> DatabaseClient:
    db = DatabaseClient.__new__(DatabaseClient)
    db._execute_with_conn = lambda conn, op: op(_DummyConn(rows))  # type: ignore[attr-defined]
    return db


def test_performance_cache_key_scopes_user_and_strategies():
    key = main._performance_cache_key(date(2024, 1, 1), date(2024, 1, 2), ("b", "a"), "user-1")
    assert "user-1" in key
    # ordering of strategies should not affect hash
    key2 = main._performance_cache_key(date(2024, 1, 1), date(2024, 1, 2), ("a", "b"), "user-1")
    assert key == key2


def test_build_user_context_with_dict_user():
    scope = {"type": "http", "query_string": b"strategies=s1&strategies=s2"}
    request = Request(scope)
    request.state.user = {"role": "viewer", "strategies": ["s1"], "user_id": "u1"}
    ctx = main._build_user_context(request)
    assert ctx["role"] == "viewer"
    assert ctx["strategies"] == ["s1"]
    assert ctx["requested_strategies"] == ["s1", "s2"]
    assert ctx["user_id"] == "u1"


def test_build_user_context_from_object_id_attr():
    class UserObj:
        def __init__(self):
            self.role = "operator"
            self.id = "obj-id"
            self.strategies = ["s3"]

    request = Request({"type": "http", "query_string": b""})
    request.state.user = UserObj()
    ctx = main._build_user_context(request)
    assert ctx["user_id"] == "obj-id"
    assert ctx["strategies"] == ["s3"]


@pytest.mark.parametrize(
    "user_state",
    [None, {}, {"strategies": ["s1"]}],
)
def test_build_user_context_rejects_missing_role(user_state):
    request = Request({"type": "http", "query_string": b""})
    if user_state is not None:
        request.state.user = user_state
    with pytest.raises(HTTPException):
        main._build_user_context(request)


def test_performance_cache_register_and_invalidate(monkeypatch):
    fake_redis = _DummyRedis()
    original = main.redis_client
    monkeypatch.setattr(main, "redis_client", fake_redis)

    cache_key = "performance:daily:u1:2024-01-01:2024-01-03:abc"
    main._register_performance_cache(cache_key, date(2024, 1, 1), date(2024, 1, 3))
    # index should include all dates
    assert len(fake_redis.index) == 3

    main._invalidate_performance_cache(date(2024, 1, 2))
    # cache key and index entry removed
    assert cache_key in fake_redis.deleted
    assert any("2024-01-02" in key for key in fake_redis.deleted)

    # restore to avoid side effects
    monkeypatch.setattr(main, "redis_client", original)


def test_get_data_availability_date_handles_none():
    rows = [{"first_date": None}]
    db = _make_db(rows)
    assert db.get_data_availability_date() is None


def test_get_data_availability_date_returns_date():
    rows = [{"first_date": date(2024, 1, 5)}]
    db = _make_db(rows)
    assert db.get_data_availability_date() == date(2024, 1, 5)


def test_get_daily_pnl_history_filters_empty_strategies():
    db = _make_db([])
    assert db.get_daily_pnl_history(date(2024, 1, 1), date(2024, 1, 2), []) == []


def test_get_daily_pnl_history_returns_rows():
    rows = [
        {"trade_date": date(2024, 1, 2), "daily_realized_pl": Decimal("10"), "closing_trade_count": 2},
        {"trade_date": date(2024, 1, 3), "daily_realized_pl": Decimal("5"), "closing_trade_count": 1},
    ]
    db = _make_db(rows)
    result = db.get_daily_pnl_history(date(2024, 1, 1), date(2024, 1, 4), ["s1"])
    assert result == rows


def test_get_positions_for_strategies_fail_closed_multiple_strats():
    now = datetime.now(timezone.utc)
    rows = [
        {
            "symbol": "AAPL",
            "qty": Decimal("10"),
            "avg_entry_price": Decimal("100"),
            "current_price": Decimal("101"),
            "unrealized_pl": Decimal("10"),
            "realized_pl": Decimal("0"),
            "updated_at": now,
            "strategies": ["s1", "s2"],
        },
        {
            "symbol": "MSFT",
            "qty": Decimal("5"),
            "avg_entry_price": Decimal("200"),
            "current_price": Decimal("199"),
            "unrealized_pl": Decimal("-5"),
            "realized_pl": Decimal("0"),
            "updated_at": now,
            "strategies": ["s1"],
        },
    ]
    db = _make_db(rows)
    positions = db.get_positions_for_strategies(["s1"])
    assert len(positions) == 1
    assert isinstance(positions[0], Position)
    assert positions[0].symbol == "MSFT"
