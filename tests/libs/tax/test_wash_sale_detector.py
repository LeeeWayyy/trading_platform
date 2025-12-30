"""Tests for wash sale detector.

Tests cover IRS Publication 550 scenarios for wash sale detection.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from libs.tax.wash_sale_detector import (
    WASH_SALE_WINDOW_DAYS,
    WashSaleDetector,
)


class MockAsyncCursor:
    """Mock async cursor for psycopg-style usage."""

    def __init__(self, *, rows: list[dict[str, Any]] | None = None, row: dict[str, Any] | None = None):
        self._rows = rows or []
        self._row = row
        self._fetchone_calls = 0

    async def execute(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    async def fetchone(self) -> dict[str, Any] | None:
        self._fetchone_calls += 1
        if self._rows and self._fetchone_calls <= len(self._rows):
            return self._rows[self._fetchone_calls - 1]
        return self._row


class MockAsyncCursorCM:
    """Async context manager for cursor."""

    def __init__(self, cursor: MockAsyncCursor):
        self._cursor = cursor

    async def __aenter__(self) -> MockAsyncCursor:
        return self._cursor

    async def __aexit__(self, *_args: Any) -> None:
        return None


class MockAsyncConnection:
    """Mock async connection."""

    def __init__(self, cursor: MockAsyncCursor):
        self._cursor = cursor

    def cursor(self, *_args: Any, **_kwargs: Any) -> MockAsyncCursorCM:
        return MockAsyncCursorCM(self._cursor)

    async def commit(self) -> None:
        pass


class MockAsyncPool:
    """Mock pool with async connection context manager."""

    def __init__(self, conn: MockAsyncConnection):
        self._conn = conn

    def connection(self) -> _MockConnCM:
        return _MockConnCM(self._conn)


class _MockConnCM:
    def __init__(self, conn: MockAsyncConnection):
        self._conn = conn

    async def __aenter__(self) -> MockAsyncConnection:
        return self._conn

    async def __aexit__(self, *_args: Any) -> None:
        return None


@pytest.fixture()
def make_pool():
    """Factory for creating mock pool with specified rows."""

    def _make(rows: list[dict[str, Any]] | None = None, row: dict[str, Any] | None = None) -> MockAsyncPool:
        cursor = MockAsyncCursor(rows=rows, row=row)
        conn = MockAsyncConnection(cursor)
        return MockAsyncPool(conn)

    return _make


class TestWashSaleDetection:
    """Test wash sale detection per IRS rules."""

    @pytest.mark.asyncio()
    async def test_no_wash_sale_without_replacement(self, make_pool: Any) -> None:
        """No wash sale if no replacement purchase exists."""
        pool = make_pool(rows=[])
        detector = WashSaleDetector(pool)

        matches = await detector.detect_wash_sales(
            user_id="user-123",
            symbol="AAPL",
            sale_date=datetime(2024, 6, 15, tzinfo=UTC),
            loss_amount=Decimal("-1000"),
            shares_sold=Decimal(100),
        )

        assert len(matches) == 0

    @pytest.mark.asyncio()
    async def test_no_wash_sale_when_no_loss(self, make_pool: Any) -> None:
        """No wash sale if there is no loss (gain or break-even)."""
        pool = make_pool(rows=[])
        detector = WashSaleDetector(pool)

        matches = await detector.detect_wash_sales(
            user_id="user-123",
            symbol="AAPL",
            sale_date=datetime(2024, 6, 15, tzinfo=UTC),
            loss_amount=Decimal("500"),  # Gain, not loss
            shares_sold=Decimal(100),
        )

        assert len(matches) == 0

    @pytest.mark.asyncio()
    async def test_wash_sale_same_day_repurchase(self, make_pool: Any) -> None:
        """Wash sale triggered by same-day repurchase."""
        sale_date = datetime(2024, 6, 15, tzinfo=UTC)
        replacement_lot_id = uuid4()

        # Replacement purchase on same day
        rows = [
            {
                "id": replacement_lot_id,
                "symbol": "AAPL",
                "quantity": Decimal("100"),
                "remaining_quantity": Decimal("100"),
                "cost_per_share": Decimal("155"),
                "acquired_at": sale_date,
            }
        ]

        pool = make_pool(rows=rows)
        detector = WashSaleDetector(pool)

        matches = await detector.detect_wash_sales(
            user_id="user-123",
            symbol="AAPL",
            sale_date=sale_date,
            loss_amount=Decimal("-1000"),
            shares_sold=Decimal(100),
        )

        assert len(matches) == 1
        assert matches[0].disallowed_loss == Decimal("1000")
        assert matches[0].matching_shares == Decimal(100)

    @pytest.mark.asyncio()
    async def test_wash_sale_30_days_before(self, make_pool: Any) -> None:
        """Wash sale triggered by purchase 30 days before sale."""
        sale_date = datetime(2024, 6, 15, tzinfo=UTC)
        purchase_date = sale_date - timedelta(days=30)  # Within window
        replacement_lot_id = uuid4()

        rows = [
            {
                "id": replacement_lot_id,
                "symbol": "AAPL",
                "quantity": Decimal("100"),
                "remaining_quantity": Decimal("100"),
                "cost_per_share": Decimal("155"),
                "acquired_at": purchase_date,
            }
        ]

        pool = make_pool(rows=rows)
        detector = WashSaleDetector(pool)

        matches = await detector.detect_wash_sales(
            user_id="user-123",
            symbol="AAPL",
            sale_date=sale_date,
            loss_amount=Decimal("-1000"),
            shares_sold=Decimal(100),
        )

        assert len(matches) == 1
        assert matches[0].disallowed_loss == Decimal("1000")

    @pytest.mark.asyncio()
    async def test_wash_sale_30_days_after(self, make_pool: Any) -> None:
        """Wash sale triggered by purchase 30 days after sale."""
        sale_date = datetime(2024, 6, 15, tzinfo=UTC)
        purchase_date = sale_date + timedelta(days=30)  # Within window
        replacement_lot_id = uuid4()

        rows = [
            {
                "id": replacement_lot_id,
                "symbol": "AAPL",
                "quantity": Decimal("100"),
                "remaining_quantity": Decimal("100"),
                "cost_per_share": Decimal("155"),
                "acquired_at": purchase_date,
            }
        ]

        pool = make_pool(rows=rows)
        detector = WashSaleDetector(pool)

        matches = await detector.detect_wash_sales(
            user_id="user-123",
            symbol="AAPL",
            sale_date=sale_date,
            loss_amount=Decimal("-1000"),
            shares_sold=Decimal(100),
        )

        assert len(matches) == 1
        assert matches[0].disallowed_loss == Decimal("1000")

    @pytest.mark.asyncio()
    async def test_partial_wash_sale(self, make_pool: Any) -> None:
        """Partial wash sale when replacement < shares sold."""
        sale_date = datetime(2024, 6, 15, tzinfo=UTC)
        replacement_lot_id = uuid4()

        # Sold 100 shares at loss, bought only 50 back
        rows = [
            {
                "id": replacement_lot_id,
                "symbol": "AAPL",
                "quantity": Decimal("50"),  # Only 50 shares
                "remaining_quantity": Decimal("50"),
                "cost_per_share": Decimal("155"),
                "acquired_at": sale_date,
            }
        ]

        pool = make_pool(rows=rows)
        detector = WashSaleDetector(pool)

        matches = await detector.detect_wash_sales(
            user_id="user-123",
            symbol="AAPL",
            sale_date=sale_date,
            loss_amount=Decimal("-1000"),  # Total loss on 100 shares
            shares_sold=Decimal(100),
        )

        assert len(matches) == 1
        # Only 50 shares matched, so only $500 disallowed
        assert matches[0].disallowed_loss == Decimal("500")
        assert matches[0].matching_shares == Decimal(50)

    @pytest.mark.asyncio()
    async def test_invalid_shares_sold_raises(self, make_pool: Any) -> None:
        """Should raise ValueError for invalid shares_sold."""
        pool = make_pool(rows=[])
        detector = WashSaleDetector(pool)

        with pytest.raises(ValueError, match="shares_sold must be positive"):
            await detector.detect_wash_sales(
                user_id="user-123",
                symbol="AAPL",
                sale_date=datetime(2024, 6, 15, tzinfo=UTC),
                loss_amount=Decimal("-1000"),
                shares_sold=Decimal(0),
            )

    @pytest.mark.asyncio()
    async def test_multiple_replacement_lots(self, make_pool: Any) -> None:
        """Multiple replacement lots match against sold shares."""
        sale_date = datetime(2024, 6, 15, tzinfo=UTC)
        lot1_id = uuid4()
        lot2_id = uuid4()

        # Two replacement purchases totaling 150 shares
        rows = [
            {
                "id": lot1_id,
                "symbol": "AAPL",
                "quantity": Decimal("80"),
                "remaining_quantity": Decimal("80"),
                "cost_per_share": Decimal("155"),
                "acquired_at": sale_date - timedelta(days=10),
            },
            {
                "id": lot2_id,
                "symbol": "AAPL",
                "quantity": Decimal("70"),
                "remaining_quantity": Decimal("70"),
                "cost_per_share": Decimal("157"),
                "acquired_at": sale_date + timedelta(days=5),
            },
        ]

        pool = make_pool(rows=rows)
        detector = WashSaleDetector(pool)

        matches = await detector.detect_wash_sales(
            user_id="user-123",
            symbol="AAPL",
            sale_date=sale_date,
            loss_amount=Decimal("-1000"),  # $10/share loss on 100 shares
            shares_sold=Decimal(100),
        )

        assert len(matches) == 2
        # First lot matches 80 shares ($800 disallowed)
        assert matches[0].matching_shares == Decimal(80)
        assert matches[0].disallowed_loss == Decimal("800")
        # Second lot matches remaining 20 shares ($200 disallowed)
        assert matches[1].matching_shares == Decimal(20)
        assert matches[1].disallowed_loss == Decimal("200")


class TestWashSaleWindowConstant:
    """Test wash sale window constant."""

    def test_window_is_30_days(self) -> None:
        """IRS wash sale window is 30 days."""
        assert WASH_SALE_WINDOW_DAYS == 30
