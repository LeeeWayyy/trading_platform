"""Tests for tax lot service."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from libs.platform.web_console_auth.permissions import Role
from libs.web_console_services.tax_lot_service import TaxLotService


def make_user(user_id: str, role: Role | str) -> dict:
    """Create a user dict for permission checks."""
    return {"user_id": user_id, "role": role}


class MockAsyncCursor:
    """Mock async cursor for psycopg-style usage."""

    def __init__(self, *, rows=None, row=None, rowcount=0):
        self._rows = rows or []
        self._row = row
        self.rowcount = rowcount

    async def execute(self, *_args, **_kwargs):
        return None

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._row


class MockAsyncCursorCM:
    """Async context manager for cursor."""

    def __init__(self, cursor: MockAsyncCursor):
        self._cursor = cursor

    async def __aenter__(self):
        return self._cursor

    async def __aexit__(self, *_args):
        return None


class MockAsyncTransactionCM:
    """Mock async transaction context manager."""

    async def __aenter__(self):
        return None

    async def __aexit__(self, *args):
        return None


class MockAsyncConnection:
    """Mock async connection."""

    def __init__(self, cursor: MockAsyncCursor):
        self._cursor = cursor
        self.commit = AsyncMock()

    def cursor(self, *_args, **_kwargs):
        return MockAsyncCursorCM(self._cursor)

    def transaction(self):
        return MockAsyncTransactionCM()


class MockAsyncPool:
    """Mock pool with async connection context manager."""

    def __init__(self, conn: MockAsyncConnection):
        self._conn = conn

    def connection(self):
        conn = self._conn

        class _ConnCM:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *_args):
                return None

        return _ConnCM()


@pytest.mark.asyncio()
async def test_list_lots_returns_entries() -> None:
    """list_lots should return TaxLot entries."""
    now = datetime.now(UTC)
    rows = [
        {
            "id": "lot-1",
            "user_id": "user-1",
            "symbol": "AAPL",
            "quantity": Decimal("10"),
            "total_cost": Decimal("1500"),
            "acquired_at": now,
            "remaining_quantity": Decimal("10"),
            "closed_at": None,
        }
    ]

    cursor = MockAsyncCursor(rows=rows)
    conn = MockAsyncConnection(cursor)
    pool = MockAsyncPool(conn)

    service = TaxLotService(db_pool=pool, user=make_user("user-1", Role.VIEWER))

    lots = await service.list_lots()

    assert len(lots) == 1
    assert lots[0].symbol == "AAPL"
    assert lots[0].cost_basis == Decimal("1500")
    assert lots[0].status == "open"


@pytest.mark.asyncio()
async def test_create_lot_success() -> None:
    """create_lot should insert and return a tax lot."""
    now = datetime.now(UTC)
    row = {
        "id": "lot-2",
        "user_id": "user-2",
        "symbol": "MSFT",
        "quantity": Decimal("5"),
        "total_cost": Decimal("750"),
        "acquired_at": now,
        "remaining_quantity": Decimal("5"),
        "closed_at": None,
    }

    cursor = MockAsyncCursor(row=row)
    conn = MockAsyncConnection(cursor)
    pool = MockAsyncPool(conn)

    service = TaxLotService(db_pool=pool, user=make_user("user-2", Role.ADMIN))

    lot = await service.create_lot(
        symbol="MSFT",
        quantity=Decimal("5"),
        cost_basis=Decimal("750"),
        acquisition_date=now,
        strategy_id="alpha",
        status="open",
        user_id="user-2",
    )

    assert lot.lot_id == "lot-2"
    assert lot.symbol == "MSFT"


@pytest.mark.asyncio()
async def test_any_role_can_list_lots_single_admin() -> None:
    """P6T19: Any role can list lots — single-admin model."""
    cursor = MockAsyncCursor(rows=[])
    conn = MockAsyncConnection(cursor)
    pool = MockAsyncPool(conn)

    service = TaxLotService(db_pool=pool, user=make_user("user-3", "unknown"))

    result = await service.list_lots()
    assert isinstance(result, list)


@pytest.mark.asyncio()
async def test_viewer_can_list_other_user_lots_single_admin() -> None:
    """P6T19: Viewer can access other user's lots — single-admin model."""
    cursor = MockAsyncCursor(rows=[])
    conn = MockAsyncConnection(cursor)
    pool = MockAsyncPool(conn)

    service = TaxLotService(db_pool=pool, user=make_user("user-4", Role.VIEWER))

    result = await service.list_lots(user_id="user-5")
    assert isinstance(result, list)


@pytest.mark.asyncio()
async def test_get_lot_returns_lot_for_owner() -> None:
    """get_lot should return lot for owner."""
    now = datetime.now(UTC)
    row = {
        "id": "lot-5",
        "user_id": "user-5",
        "symbol": "GOOG",
        "quantity": Decimal("3"),
        "total_cost": Decimal("450"),
        "acquired_at": now,
        "remaining_quantity": Decimal("3"),
        "closed_at": None,
    }

    cursor = MockAsyncCursor(row=row)
    conn = MockAsyncConnection(cursor)
    pool = MockAsyncPool(conn)

    service = TaxLotService(db_pool=pool, user=make_user("user-5", Role.VIEWER))

    lot = await service.get_lot("lot-5")

    assert lot is not None
    assert lot.lot_id == "lot-5"
    assert lot.symbol == "GOOG"


@pytest.mark.asyncio()
async def test_get_lot_returns_none_for_nonexistent() -> None:
    """get_lot should return None for nonexistent or other user's lot."""
    cursor = MockAsyncCursor(row=None)
    conn = MockAsyncConnection(cursor)
    pool = MockAsyncPool(conn)

    service = TaxLotService(db_pool=pool, user=make_user("user-6", Role.VIEWER))

    lot = await service.get_lot("nonexistent")

    assert lot is None


@pytest.mark.asyncio()
async def test_update_lot_success() -> None:
    """update_lot should update and return lot."""
    now = datetime.now(UTC)
    existing_row = {
        "id": "lot-7",
        "user_id": "user-7",
        "symbol": "TSLA",
        "quantity": Decimal("10"),
        "total_cost": Decimal("2000"),
        "acquired_at": now,
        "remaining_quantity": Decimal("10"),
        "closed_at": None,
    }
    updated_row = {
        **existing_row,
        "symbol": "NVDA",
    }

    class MockCursorUpdate:
        """Mock cursor for update: first SELECT returns existing, then UPDATE returns updated."""

        def __init__(self):
            self._call_count = 0

        async def execute(self, *_args, **_kwargs):
            return None

        async def fetchone(self):
            self._call_count += 1
            if self._call_count == 1:
                return existing_row
            return updated_row

        async def fetchall(self):
            return []

    cursor = MockCursorUpdate()
    conn = MockAsyncConnection(MockAsyncCursor())
    conn.commit = AsyncMock()

    def cursor_cm(*_args, **_kwargs):
        class _CM:
            async def __aenter__(self):
                return cursor

            async def __aexit__(self, *_args):
                return None

        return _CM()

    conn.cursor = cursor_cm
    pool = MockAsyncPool(conn)

    service = TaxLotService(db_pool=pool, user=make_user("user-7", Role.ADMIN))

    lot = await service.update_lot("lot-7", {"symbol": "NVDA"})

    assert lot.symbol == "NVDA"



@pytest.mark.asyncio()
async def test_close_lot_success() -> None:
    """close_lot should close and return lot."""
    now = datetime.now(UTC)
    row = {
        "id": "lot-8",
        "user_id": "user-8",
        "symbol": "AMD",
        "quantity": Decimal("5"),
        "total_cost": Decimal("500"),
        "acquired_at": now,
        "remaining_quantity": Decimal("0"),
        "closed_at": now,
    }

    cursor = MockAsyncCursor(row=row)
    conn = MockAsyncConnection(cursor)
    pool = MockAsyncPool(conn)

    service = TaxLotService(db_pool=pool, user=make_user("user-8", Role.ADMIN))

    lot = await service.close_lot("lot-8")

    assert lot is not None
    assert lot.status == "closed"



@pytest.mark.asyncio()
async def test_close_lot_returns_none_for_other_user() -> None:
    """close_lot should return None when lot not found for user."""
    cursor = MockAsyncCursor(row=None)
    conn = MockAsyncConnection(cursor)
    pool = MockAsyncPool(conn)

    service = TaxLotService(db_pool=pool, user=make_user("user-9", Role.ADMIN))

    lot = await service.close_lot("other-users-lot")

    assert lot is None


@pytest.mark.asyncio()
async def test_viewer_can_create_lot_single_admin() -> None:
    """P6T19: Viewer can create lots — single-admin model."""
    now = datetime.now(UTC)
    row = {
        "id": "lot-new",
        "user_id": "user-10",
        "symbol": "TEST",
        "quantity": Decimal("1"),
        "cost_basis": Decimal("100"),
        "acquisition_date": now,
        "strategy_id": None,
        "status": "open",
        "closed_at": None,
        "created_at": now,
        "updated_at": now,
    }
    cursor = MockAsyncCursor(row=row)
    conn = MockAsyncConnection(cursor)
    pool = MockAsyncPool(conn)

    service = TaxLotService(db_pool=pool, user=make_user("user-10", Role.VIEWER))

    result = await service.create_lot(
        symbol="TEST",
        quantity=Decimal("1"),
        cost_basis=Decimal("100"),
        acquisition_date=now,
        strategy_id=None,
        status="open",
    )
    assert result is not None


@pytest.mark.asyncio()
async def test_viewer_can_update_lot_single_admin() -> None:
    """P6T19: Viewer can update lots — single-admin model."""
    now = datetime.now(UTC)
    row = {
        "id": "lot-11",
        "user_id": "user-11",
        "symbol": "UPDATED",
        "quantity": Decimal("1"),
        "cost_basis": Decimal("100"),
        "acquisition_date": now,
        "strategy_id": None,
        "status": "open",
        "closed_at": None,
        "created_at": now,
        "updated_at": now,
    }
    cursor = MockAsyncCursor(row=row)
    conn = MockAsyncConnection(cursor)
    pool = MockAsyncPool(conn)

    service = TaxLotService(db_pool=pool, user=make_user("user-11", Role.VIEWER))

    result = await service.update_lot("lot-11", {"symbol": "UPDATED"})
    assert result is not None


@pytest.mark.asyncio()
async def test_viewer_can_close_lot_single_admin() -> None:
    """P6T19: Viewer can close lots — single-admin model."""
    now = datetime.now(UTC)
    row = {
        "id": "lot-12",
        "user_id": "user-12",
        "symbol": "TEST",
        "quantity": Decimal("1"),
        "cost_basis": Decimal("100"),
        "acquisition_date": now,
        "strategy_id": None,
        "status": "closed",
        "closed_at": now,
        "created_at": now,
        "updated_at": now,
    }
    cursor = MockAsyncCursor(row=row)
    conn = MockAsyncConnection(cursor)
    pool = MockAsyncPool(conn)

    service = TaxLotService(db_pool=pool, user=make_user("user-12", Role.VIEWER))

    result = await service.close_lot("lot-12")
    assert result is not None


@pytest.mark.asyncio()
async def test_update_lot_ignores_null_status() -> None:
    """update_lot should not change closed_at when status is None."""
    now = datetime.now(UTC)
    existing_row = {
        "id": "lot-13",
        "user_id": "user-13",
        "symbol": "META",
        "quantity": Decimal("10"),
        "total_cost": Decimal("3000"),
        "acquired_at": now,
        "remaining_quantity": Decimal("10"),
        "closed_at": None,
    }
    # Updated row should have same closed_at (None) since status: None is ignored
    updated_row = {
        **existing_row,
        "symbol": "META_UPDATED",
    }

    class MockCursorNullStatus:
        """Mock cursor for update with null status."""

        def __init__(self):
            self._call_count = 0

        async def execute(self, *_args, **_kwargs):
            return None

        async def fetchone(self):
            self._call_count += 1
            if self._call_count == 1:
                return existing_row
            return updated_row

        async def fetchall(self):
            return []

    cursor = MockCursorNullStatus()
    conn = MockAsyncConnection(MockAsyncCursor())
    conn.commit = AsyncMock()

    def cursor_cm(*_args, **_kwargs):
        class _CM:
            async def __aenter__(self):
                return cursor

            async def __aexit__(self, *_args):
                return None

        return _CM()

    conn.cursor = cursor_cm
    pool = MockAsyncPool(conn)

    service = TaxLotService(db_pool=pool, user=make_user("user-13", Role.ADMIN))

    # Pass status: None which should be ignored
    lot = await service.update_lot("lot-13", {"symbol": "META_UPDATED", "status": None})

    assert lot.symbol == "META_UPDATED"
    # Status should remain "open" since closed_at is still None
    assert lot.status == "open"

