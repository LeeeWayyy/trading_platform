"""High-coverage tests for DatabaseClient using lightweight fakes."""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from types import ModuleType

# Stub pydantic core classes only if library missing (CI often preinstalls)
try:
    import pydantic as _pd  # type: ignore  # noqa
except ImportError:  # pragma: no cover - fallback for hermetic envs
    pydantic_stub = ModuleType("pydantic")

    class _ValidationError(Exception): ...

    class _BaseModel:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def _Field(*args, **kwargs):
        return None

    def _field_validator(*args, **kwargs):
        def wrapper(fn):
            return fn

        return wrapper

    def _model_validator(*args, **kwargs):
        def wrapper(fn):
            return fn

        return wrapper

    pydantic_stub.BaseModel = _BaseModel
    pydantic_stub.Field = _Field
    pydantic_stub.field_validator = _field_validator
    pydantic_stub.model_validator = _model_validator
    pydantic_stub.ValidationError = _ValidationError
    sys.modules.setdefault("pydantic", pydantic_stub)

# Stub libs.common TimestampSerializerMixin dependency
common_schemas_stub = ModuleType("libs.core.common.schemas")


class TimestampSerializerMixin: ...


common_schemas_stub.TimestampSerializerMixin = TimestampSerializerMixin
sys.modules.setdefault("libs.core.common.schemas", common_schemas_stub)
common_stub = ModuleType("libs.common")
common_stub.TimestampSerializerMixin = TimestampSerializerMixin
sys.modules.setdefault("libs.common", common_stub)


from apps.execution_gateway.database import DatabaseClient
from apps.execution_gateway.schemas import OrderRequest


class FakeCursor:
    def __init__(self, rows=None, rowcount: int = 0):
        self.rows = list(rows or [])
        self._index = 0
        self.last_sql = ""
        self.last_params = ()
        self.rowcount = rowcount if rowcount else len(self.rows)

    def execute(self, sql, params=None):
        self.last_sql = sql
        self.last_params = params or ()
        # mimic rowcount for update/delete statements
        if sql.strip().lower().startswith("update"):
            # if status update target row exists, pretend one row affected
            self.rowcount = 1

    def fetchone(self):
        if self._index < len(self.rows):
            row = self.rows[self._index]
            self._index += 1
            return row
        return None

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeTxn:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeConnection:
    def __init__(self, cursor: FakeCursor):
        self._cursor = cursor
        self.commits = 0

    def cursor(self, row_factory=None):
        return self._cursor

    def commit(self):
        self.commits += 1

    def transaction(self):
        return FakeTxn(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self, cursor: FakeCursor):
        self.cursor_template = cursor

    def connection(self):
        # Provide a fresh cursor per connection to avoid exhausted iterators
        fresh_cursor = FakeCursor(
            rows=list(self.cursor_template.rows), rowcount=self.cursor_template.rowcount
        )
        return FakeConnection(fresh_cursor)

    def close(self):
        return None


def make_db_with_rows(rows):
    import psycopg_pool

    from apps.execution_gateway import database as dbmod

    original_pool = psycopg_pool.ConnectionPool
    original_local_pool = dbmod.ConnectionPool
    try:
        psycopg_pool.ConnectionPool = lambda *a, **k: FakePool(FakeCursor(rows=rows))
        dbmod.ConnectionPool = psycopg_pool.ConnectionPool
        db = DatabaseClient("postgresql://user:pass@localhost/db")
        db._pool = FakePool(FakeCursor(rows=rows))
    finally:
        psycopg_pool.ConnectionPool = original_pool
        dbmod.ConnectionPool = original_local_pool
    return db


def test_create_order_and_fetch_back(monkeypatch):
    # Insert returns a single row; ensure order detail fields mapped
    row = {
        "client_order_id": "cid",
        "strategy_id": "alpha",
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
    db = make_db_with_rows([row])
    req = OrderRequest(symbol="AAPL", side="buy", qty=1, order_type="market")
    created = db.create_order("cid", "alpha", req, status="pending_new")
    assert created.client_order_id == "cid"
    fetched = db.get_order_by_client_id("cid")
    assert fetched.client_order_id == "cid"


def test_parent_and_child_slice_creation():
    row = {
        "client_order_id": "parent1",
        "strategy_id": "twap_parent",
        "symbol": "AAPL",
        "side": "buy",
        "qty": 10,
        "order_type": "market",
        "time_in_force": "day",
        "status": "pending_new",
        "retry_count": 0,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "filled_qty": Decimal("0"),
        "parent_order_id": None,
        "total_slices": 2,
    }
    db = make_db_with_rows(
        [row, {**row, "client_order_id": "child1", "parent_order_id": "parent1", "slice_num": 0}]
    )
    req = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
    parent = db.create_parent_order("parent1", "twap_parent", req, total_slices=2)
    assert parent.total_slices == 2
    # Switch pool so the next connection returns the child row first, ensuring
    # create_child_slice fetches the expected record rather than reusing the
    # parent row from an earlier cursor.
    child_row = {**row, "client_order_id": "child1", "parent_order_id": "parent1", "slice_num": 0}
    db._pool = FakePool(FakeCursor(rows=[child_row]))
    child = db.create_child_slice(
        client_order_id="child1",
        parent_order_id="parent1",
        slice_num=0,
        strategy_id="twap_slice",
        order_request=req,
        scheduled_time=datetime.now(UTC),
    )
    assert child.parent_order_id == "parent1"
    # Prepare pool for slice retrieval (return both parent + child for ordering)
    db._pool = FakePool(FakeCursor(rows=[child_row, row]))
    slices = db.get_slices_by_parent_id("parent1")
    assert any(s.parent_order_id == "parent1" for s in slices)


def test_cancel_pending_and_update_status():
    db = make_db_with_rows(
        [
            {
                "client_order_id": "cid",
                "strategy_id": "alpha",
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
                "parent_order_id": "parent1",
                "slice_num": 0,
            }
        ]
    )
    assert db.cancel_pending_slices("parent1") == 1
    # Return an updated row for the status update call
    db._pool = FakePool(
        FakeCursor(
            rows=[
                {
                    "client_order_id": "cid",
                    "strategy_id": "alpha",
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
                    "filled_avg_price": Decimal("10"),
                }
            ]
        )
    )
    updated = db.update_order_status(
        "cid", status="filled", filled_qty=Decimal("1"), filled_avg_price=Decimal("10")
    )
    assert updated.status == "filled"


def test_position_workflow_and_updates():
    # First call returns no existing position, second returns updated row
    pos_row = {
        "symbol": "AAPL",
        "qty": Decimal("1"),
        "avg_entry_price": Decimal("10"),
        "realized_pl": Decimal("0"),
        "updated_at": datetime.now(UTC),
    }
    db = make_db_with_rows([None, pos_row, pos_row])
    # Use connection mode
    conn = db._pool.connection()
    cur = conn.cursor()
    cur.rows = [None, pos_row]  # ensure fetchone sequence for get_position_for_update then upsert
    position = db.update_position_on_fill_with_conn(
        symbol="AAPL",
        fill_qty=1,
        fill_price=Decimal("10"),
        side="buy",
        conn=conn,
    )
    assert position.qty == Decimal("1")
    # non-transactional path
    db._pool = FakePool(FakeCursor(rows=[pos_row, pos_row]))
    position2 = db.update_position_on_fill("AAPL", qty=1, price=Decimal("10"), side="buy")
    assert position2.symbol == "AAPL"


def test_order_metadata_append_and_status_with_conn():
    fill_row = {
        "client_order_id": "cid",
        "strategy_id": "alpha",
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
        "metadata": {"fills": []},
    }
    # Provide multiple rows: one for FOR UPDATE check, one for RETURNING *
    db = make_db_with_rows([fill_row, fill_row])
    conn = db._pool.connection()
    cur = conn.cursor()
    cur.rows = [fill_row, fill_row]
    appended = db.append_fill_to_order_metadata(
        "cid",
        {"fill_id": "cid_1", "realized_pl": "1", "timestamp": datetime.now(UTC).isoformat()},
        conn=conn,
    )
    assert appended is not None
    # Return an updated row for the transactional status update using a fresh connection
    updated_row = {
        **fill_row,
        "status": "filled",
        "filled_qty": Decimal("1"),
        "filled_avg_price": Decimal("10"),
    }
    db._pool = FakePool(FakeCursor(rows=[updated_row]))
    conn = db._pool.connection()
    updated = db.update_order_status_with_conn(
        client_order_id="cid",
        status="filled",
        filled_qty=1,
        filled_avg_price=Decimal("10"),
        filled_at=datetime.now(UTC),
        conn=conn,
    )
    assert updated is not None


def test_performance_queries_and_positions_filters():
    daily_row = {
        "trade_date": date(2024, 1, 1),
        "daily_realized_pl": Decimal("5"),
        "closing_trade_count": 1,
    }
    pos_row = {
        "symbol": "MSFT",
        "qty": Decimal("2"),
        "avg_entry_price": Decimal("50"),
        "realized_pl": Decimal("0"),
        "updated_at": datetime.now(UTC),
    }
    strategy_row = {**pos_row, "strategies": ["s1"]}
    db = make_db_with_rows([{"first_date": date(2024, 1, 1)}])

    first_date = db.get_data_availability_date()
    assert first_date == date(2024, 1, 1)
    db._pool = FakePool(FakeCursor(rows=[daily_row]))
    history = db.get_daily_pnl_history(date(2024, 1, 1), date(2024, 1, 2), ["alpha"])
    assert history == [daily_row]
    db._pool = FakePool(FakeCursor(rows=[pos_row]))
    positions = db.get_all_positions()
    assert positions
    assert positions[0].symbol == "MSFT"
    db._pool = FakePool(FakeCursor(rows=[strategy_row]))
    strat_positions = db.get_positions_for_strategies(["s1"])
    assert strat_positions == positions  # one strategy mapped


def test_get_position_by_symbol_and_check_connection():
    db = make_db_with_rows([{"qty": 3}])
    assert db.get_position_by_symbol("AAPL") == 3
    assert db.check_connection() is True
