"""Tests for wash sale detector.

Tests cover IRS Publication 550 scenarios for wash sale detection.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from libs.platform.tax.wash_sale_detector import (
    WASH_SALE_WINDOW_DAYS,
    WashSaleDetector,
    WashSaleMatch,
)


class MockAsyncCursor:
    """Mock async cursor for psycopg-style usage.

    Supports multiple query responses for complex test scenarios where
    a method makes several database queries.
    """

    def __init__(
        self,
        *,
        rows: list[dict[str, Any]] | None = None,
        row: dict[str, Any] | None = None,
        query_responses: list[list[dict[str, Any]] | dict[str, Any] | None] | None = None,
    ):
        self._rows = rows or []
        self._row = row
        self._fetchone_calls = 0
        self._query_responses = query_responses or []
        self._query_index = 0
        self._execute_calls: list[tuple[Any, ...]] = []

    async def execute(self, query: str, *args: Any, **kwargs: Any) -> None:
        """Track execute calls for verification."""
        self._execute_calls.append((query, args, kwargs))
        return None

    async def fetchall(self) -> list[dict[str, Any]]:
        """Return rows for fetchall calls."""
        if self._query_responses and self._query_index < len(self._query_responses):
            response = self._query_responses[self._query_index]
            self._query_index += 1
            if isinstance(response, list):
                return response
        return self._rows

    async def fetchone(self) -> dict[str, Any] | None:
        """Return single row for fetchone calls."""
        if self._query_responses and self._query_index < len(self._query_responses):
            response = self._query_responses[self._query_index]
            self._query_index += 1
            if isinstance(response, dict) or response is None:
                return response

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

    def _make(
        rows: list[dict[str, Any]] | None = None,
        row: dict[str, Any] | None = None,
        query_responses: list[list[dict[str, Any]] | dict[str, Any] | None] | None = None,
    ) -> MockAsyncPool:
        cursor = MockAsyncCursor(rows=rows, row=row, query_responses=query_responses)
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

    @pytest.mark.asyncio()
    async def test_wash_sale_with_already_used_shares(self, make_pool: Any) -> None:
        """Wash sale respects shares already used in prior wash sales."""
        sale_date = datetime(2024, 6, 15, tzinfo=UTC)
        replacement_lot_id = uuid4()

        # Lot has 100 shares but 50 already used in prior wash sales
        rows = [
            {
                "id": replacement_lot_id,
                "symbol": "AAPL",
                "quantity": Decimal("100"),
                "remaining_quantity": Decimal("100"),
                "cost_per_share": Decimal("155"),
                "acquired_at": sale_date,
                "shares_already_used": Decimal("50"),  # 50 already allocated
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

        # Should only match 50 available shares (100 - 50 used)
        assert len(matches) == 1
        assert matches[0].matching_shares == Decimal(50)
        assert matches[0].disallowed_loss == Decimal("500")

    @pytest.mark.asyncio()
    async def test_wash_sale_respects_remaining_quantity(self, make_pool: Any) -> None:
        """Wash sale only allocates up to remaining_quantity (partially sold lots)."""
        sale_date = datetime(2024, 6, 15, tzinfo=UTC)
        replacement_lot_id = uuid4()

        # Original 100 shares, but only 30 remaining (70 already sold)
        rows = [
            {
                "id": replacement_lot_id,
                "symbol": "AAPL",
                "quantity": Decimal("100"),
                "remaining_quantity": Decimal("30"),  # Only 30 left
                "cost_per_share": Decimal("155"),
                "acquired_at": sale_date,
                "shares_already_used": Decimal("0"),
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

        # Should only match 30 remaining shares (not original 100)
        assert len(matches) == 1
        assert matches[0].matching_shares == Decimal(30)
        assert matches[0].disallowed_loss == Decimal("300")

    @pytest.mark.asyncio()
    async def test_wash_sale_outside_window_before(self, make_pool: Any) -> None:
        """No wash sale when DB returns no replacements (simulating outside window).

        Note: The mock pool doesn't execute real SQL, so we simulate the database
        returning empty results (what would happen if a purchase is >30 days before).
        The actual SQL filtering is tested in integration tests.
        """
        # Simulate DB returning no replacement lots (purchase would be outside window)
        pool = make_pool(rows=[])
        detector = WashSaleDetector(pool)

        matches = await detector.detect_wash_sales(
            user_id="user-123",
            symbol="AAPL",
            sale_date=datetime(2024, 6, 15, tzinfo=UTC),
            loss_amount=Decimal("-1000"),
            shares_sold=Decimal(100),
        )

        # No replacements in window, no wash sale
        assert len(matches) == 0

    @pytest.mark.asyncio()
    async def test_wash_sale_outside_window_after(self, make_pool: Any) -> None:
        """No wash sale when DB returns no replacements (simulating outside window).

        Note: The mock pool doesn't execute real SQL, so we simulate the database
        returning empty results (what would happen if a purchase is >30 days after).
        The actual SQL filtering is tested in integration tests.
        """
        # Simulate DB returning no replacement lots (purchase would be outside window)
        pool = make_pool(rows=[])
        detector = WashSaleDetector(pool)

        matches = await detector.detect_wash_sales(
            user_id="user-123",
            symbol="AAPL",
            sale_date=datetime(2024, 6, 15, tzinfo=UTC),
            loss_amount=Decimal("-1000"),
            shares_sold=Decimal(100),
        )

        # No replacements in window, no wash sale
        assert len(matches) == 0

    @pytest.mark.asyncio()
    async def test_wash_sale_break_even_no_match(self, make_pool: Any) -> None:
        """No wash sale on break-even (zero gain/loss)."""
        pool = make_pool(rows=[])
        detector = WashSaleDetector(pool)

        matches = await detector.detect_wash_sales(
            user_id="user-123",
            symbol="AAPL",
            sale_date=datetime(2024, 6, 15, tzinfo=UTC),
            loss_amount=Decimal("0"),  # Break-even
            shares_sold=Decimal(100),
        )

        assert len(matches) == 0

    @pytest.mark.asyncio()
    async def test_wash_sale_fully_allocated_lot_skipped(self, make_pool: Any) -> None:
        """Fully allocated lot (shares_already_used >= quantity) is skipped."""
        sale_date = datetime(2024, 6, 15, tzinfo=UTC)
        lot1_id = uuid4()
        lot2_id = uuid4()

        # Lot1: 100 shares, all already used
        # Lot2: 50 shares, available
        rows = [
            {
                "id": lot1_id,
                "symbol": "AAPL",
                "quantity": Decimal("100"),
                "remaining_quantity": Decimal("100"),
                "cost_per_share": Decimal("155"),
                "acquired_at": sale_date - timedelta(days=5),
                "shares_already_used": Decimal("100"),  # Fully used
            },
            {
                "id": lot2_id,
                "symbol": "AAPL",
                "quantity": Decimal("50"),
                "remaining_quantity": Decimal("50"),
                "cost_per_share": Decimal("155"),
                "acquired_at": sale_date,
                "shares_already_used": Decimal("0"),
            },
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

        # Only lot2 should match
        assert len(matches) == 1
        assert matches[0].replacement_lot_id == lot2_id
        assert matches[0].matching_shares == Decimal(50)

    @pytest.mark.asyncio()
    async def test_wash_sale_negative_shares_sold_raises(self, make_pool: Any) -> None:
        """Should raise ValueError for negative shares_sold."""
        pool = make_pool(rows=[])
        detector = WashSaleDetector(pool)

        with pytest.raises(ValueError, match="shares_sold must be positive"):
            await detector.detect_wash_sales(
                user_id="user-123",
                symbol="AAPL",
                sale_date=datetime(2024, 6, 15, tzinfo=UTC),
                loss_amount=Decimal("-1000"),
                shares_sold=Decimal(-10),
            )

    @pytest.mark.asyncio()
    async def test_wash_sale_zero_remaining_quantity_skipped(self, make_pool: Any) -> None:
        """Lot with zero remaining_quantity should not match."""
        sale_date = datetime(2024, 6, 15, tzinfo=UTC)
        replacement_lot_id = uuid4()

        # Lot fully sold (remaining_quantity = 0)
        rows = [
            {
                "id": replacement_lot_id,
                "symbol": "AAPL",
                "quantity": Decimal("100"),
                "remaining_quantity": Decimal("0"),  # Fully sold
                "cost_per_share": Decimal("155"),
                "acquired_at": sale_date,
                "shares_already_used": Decimal("0"),
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

        # Query filters remaining_quantity > 0, so no matches
        assert len(matches) == 0

    @pytest.mark.asyncio()
    async def test_wash_sale_complex_multi_lot_allocation(self, make_pool: Any) -> None:
        """Complex scenario: multiple lots with partial availability."""
        sale_date = datetime(2024, 6, 15, tzinfo=UTC)
        lot1_id = uuid4()
        lot2_id = uuid4()
        lot3_id = uuid4()

        # Lot1: 100 shares, 60 available (40 used)
        # Lot2: 80 shares, 30 available (50 remaining, 50 used)
        # Lot3: 50 shares, 0 available (50 used, fully allocated)
        rows = [
            {
                "id": lot1_id,
                "symbol": "AAPL",
                "quantity": Decimal("100"),
                "remaining_quantity": Decimal("100"),
                "cost_per_share": Decimal("150"),
                "acquired_at": sale_date - timedelta(days=10),
                "shares_already_used": Decimal("40"),
            },
            {
                "id": lot2_id,
                "symbol": "AAPL",
                "quantity": Decimal("80"),
                "remaining_quantity": Decimal("50"),  # 30 sold
                "cost_per_share": Decimal("155"),
                "acquired_at": sale_date - timedelta(days=5),
                "shares_already_used": Decimal("50"),
            },
            {
                "id": lot3_id,
                "symbol": "AAPL",
                "quantity": Decimal("50"),
                "remaining_quantity": Decimal("50"),
                "cost_per_share": Decimal("160"),
                "acquired_at": sale_date,
                "shares_already_used": Decimal("50"),  # Fully allocated
            },
        ]

        pool = make_pool(rows=rows)
        detector = WashSaleDetector(pool)

        # Sell 100 shares at $10/share loss
        matches = await detector.detect_wash_sales(
            user_id="user-123",
            symbol="AAPL",
            sale_date=sale_date,
            loss_amount=Decimal("-1000"),
            shares_sold=Decimal(100),
        )

        # Lot1: 60 available (100 - 40 used) -> match 60
        # Lot2: 30 available (min(50 remaining, 80 - 50 used)) -> match 30
        # Lot3: 0 available (50 - 50 used) -> skip
        # Remaining: 100 - 60 - 30 = 10 shares unmatched
        assert len(matches) == 2
        assert matches[0].replacement_lot_id == lot1_id
        assert matches[0].matching_shares == Decimal(60)
        assert matches[0].disallowed_loss == Decimal("600")
        assert matches[1].replacement_lot_id == lot2_id
        assert matches[1].matching_shares == Decimal(30)
        assert matches[1].disallowed_loss == Decimal("300")


class TestWashSaleAdjustments:
    """Test wash sale adjustment application."""

    @pytest.mark.asyncio()
    async def test_apply_empty_matches_returns_empty(self, make_pool: Any) -> None:
        """Applying empty matches list returns empty adjustments."""
        pool = make_pool(rows=[])
        detector = WashSaleDetector(pool)

        adjustments = await detector.apply_wash_sale_adjustments(
            matches=[], loss_disposition_id=uuid4()
        )

        assert adjustments == []

    @pytest.mark.asyncio()
    async def test_apply_adjustments_single_match(self, make_pool: Any) -> None:
        """Apply wash sale adjustment for single match."""
        loss_disposition_id = uuid4()
        replacement_lot_id = uuid4()
        sale_date = datetime(2024, 6, 15, tzinfo=UTC)
        acquired_date = datetime(2024, 6, 10, tzinfo=UTC)

        match = WashSaleMatch(
            loss_disposition_id=UUID("00000000-0000-0000-0000-000000000000"),
            replacement_lot_id=replacement_lot_id,
            symbol="AAPL",
            disallowed_loss=Decimal("500"),
            matching_shares=Decimal(50),
            sale_date=sale_date,
            replacement_date=acquired_date,
        )

        # Query 1: Get original lot acquired_at (returns holding period)
        # Query 2: Check existing adjustment (none)
        # Query 3: Insert/update adjustment
        # Query 4: Update disposition wash_sale_disallowed
        # Query 5: Update replacement lot cost basis
        query_responses = [
            {"acquired_at": acquired_date},  # Original lot
            None,  # No existing adjustment
        ]

        pool = make_pool(query_responses=query_responses)
        detector = WashSaleDetector(pool)

        adjustments = await detector.apply_wash_sale_adjustments(
            matches=[match], loss_disposition_id=loss_disposition_id
        )

        assert len(adjustments) == 1
        assert adjustments[0].lot_id == replacement_lot_id
        assert adjustments[0].disallowed_loss == Decimal("500")
        assert adjustments[0].basis_adjustment == Decimal("500")
        assert adjustments[0].holding_period_adjustment_days == 5  # 6/10 to 6/15

    @pytest.mark.asyncio()
    async def test_apply_adjustments_idempotent(self, make_pool: Any) -> None:
        """Applying same adjustment twice only adds delta (idempotency)."""
        loss_disposition_id = uuid4()
        replacement_lot_id = uuid4()
        sale_date = datetime(2024, 6, 15, tzinfo=UTC)
        acquired_date = datetime(2024, 6, 10, tzinfo=UTC)

        match = WashSaleMatch(
            loss_disposition_id=UUID("00000000-0000-0000-0000-000000000000"),
            replacement_lot_id=replacement_lot_id,
            symbol="AAPL",
            disallowed_loss=Decimal("500"),
            matching_shares=Decimal(50),
            sale_date=sale_date,
            replacement_date=acquired_date,
        )

        # Simulate existing adjustment of $300 (retry scenario)
        query_responses = [
            {"acquired_at": acquired_date},  # Original lot
            {"disallowed_loss": Decimal("300")},  # Existing adjustment
        ]

        pool = make_pool(query_responses=query_responses)
        detector = WashSaleDetector(pool)

        adjustments = await detector.apply_wash_sale_adjustments(
            matches=[match], loss_disposition_id=loss_disposition_id
        )

        # Should compute delta: 500 - 300 = 200
        assert len(adjustments) == 1
        assert adjustments[0].disallowed_loss == Decimal("500")  # Total
        # Note: The actual DB updates would use delta ($200), but returned
        # adjustment shows the total disallowed_loss from the match

    @pytest.mark.asyncio()
    async def test_apply_adjustments_no_original_lot(self, make_pool: Any) -> None:
        """Handles missing original lot gracefully (holding period = 0)."""
        loss_disposition_id = uuid4()
        replacement_lot_id = uuid4()
        sale_date = datetime(2024, 6, 15, tzinfo=UTC)
        acquired_date = datetime(2024, 6, 10, tzinfo=UTC)

        match = WashSaleMatch(
            loss_disposition_id=UUID("00000000-0000-0000-0000-000000000000"),
            replacement_lot_id=replacement_lot_id,
            symbol="AAPL",
            disallowed_loss=Decimal("500"),
            matching_shares=Decimal(50),
            sale_date=sale_date,
            replacement_date=acquired_date,
        )

        # Query 1: Get original lot acquired_at (returns None)
        # Query 2: Check existing adjustment (none)
        query_responses = [
            None,  # No original lot found
            None,  # No existing adjustment
        ]

        pool = make_pool(query_responses=query_responses)
        detector = WashSaleDetector(pool)

        adjustments = await detector.apply_wash_sale_adjustments(
            matches=[match], loss_disposition_id=loss_disposition_id
        )

        assert len(adjustments) == 1
        assert adjustments[0].holding_period_adjustment_days == 0  # No holding period

    @pytest.mark.asyncio()
    async def test_apply_adjustments_multiple_matches(self, make_pool: Any) -> None:
        """Apply wash sale adjustments for multiple matches."""
        loss_disposition_id = uuid4()
        lot1_id = uuid4()
        lot2_id = uuid4()
        sale_date = datetime(2024, 6, 15, tzinfo=UTC)
        acquired_date = datetime(2024, 6, 5, tzinfo=UTC)

        matches = [
            WashSaleMatch(
                loss_disposition_id=UUID("00000000-0000-0000-0000-000000000000"),
                replacement_lot_id=lot1_id,
                symbol="AAPL",
                disallowed_loss=Decimal("600"),
                matching_shares=Decimal(60),
                sale_date=sale_date,
                replacement_date=sale_date - timedelta(days=5),
            ),
            WashSaleMatch(
                loss_disposition_id=UUID("00000000-0000-0000-0000-000000000000"),
                replacement_lot_id=lot2_id,
                symbol="AAPL",
                disallowed_loss=Decimal("400"),
                matching_shares=Decimal(40),
                sale_date=sale_date,
                replacement_date=sale_date + timedelta(days=3),
            ),
        ]

        # Query sequence: 1) original lot (once), 2) existing adjustment per match
        query_responses = [
            {"acquired_at": acquired_date},  # Original lot (queried once before loop)
            None,  # No existing adjustment (match 1)
            None,  # No existing adjustment (match 2)
        ]

        pool = make_pool(query_responses=query_responses)
        detector = WashSaleDetector(pool)

        adjustments = await detector.apply_wash_sale_adjustments(
            matches=matches, loss_disposition_id=loss_disposition_id
        )

        assert len(adjustments) == 2
        assert adjustments[0].lot_id == lot1_id
        assert adjustments[0].disallowed_loss == Decimal("600")
        assert adjustments[1].lot_id == lot2_id
        assert adjustments[1].disallowed_loss == Decimal("400")

    @pytest.mark.asyncio()
    async def test_apply_adjustments_with_zero_delta(self, make_pool: Any) -> None:
        """Idempotent retry with same values (delta = 0) doesn't update DB."""
        loss_disposition_id = uuid4()
        replacement_lot_id = uuid4()
        sale_date = datetime(2024, 6, 15, tzinfo=UTC)
        acquired_date = datetime(2024, 6, 10, tzinfo=UTC)

        match = WashSaleMatch(
            loss_disposition_id=UUID("00000000-0000-0000-0000-000000000000"),
            replacement_lot_id=replacement_lot_id,
            symbol="AAPL",
            disallowed_loss=Decimal("500"),
            matching_shares=Decimal(50),
            sale_date=sale_date,
            replacement_date=acquired_date,
        )

        # Simulate existing adjustment with SAME value (delta = 0)
        query_responses = [
            {"acquired_at": acquired_date},  # Original lot
            {"disallowed_loss": Decimal("500")},  # Existing = new (delta = 0)
        ]

        pool = make_pool(query_responses=query_responses)
        detector = WashSaleDetector(pool)

        adjustments = await detector.apply_wash_sale_adjustments(
            matches=[match], loss_disposition_id=loss_disposition_id
        )

        # Should still return adjustment, but internal delta = 0
        assert len(adjustments) == 1
        assert adjustments[0].disallowed_loss == Decimal("500")


class TestWashSaleSummary:
    """Test wash sale summary reporting."""

    @pytest.mark.asyncio()
    async def test_get_summary_no_wash_sales(self, make_pool: Any) -> None:
        """Get summary with no wash sales returns zero counts."""
        query_responses = [
            {"wash_sale_count": 0, "total_disallowed": Decimal("0")},
        ]

        pool = make_pool(query_responses=query_responses)
        detector = WashSaleDetector(pool)

        summary = await detector.get_wash_sale_summary(user_id="user-123", tax_year=2024)

        assert summary["wash_sale_count"] == 0
        assert summary["total_disallowed"] == Decimal("0")

    @pytest.mark.asyncio()
    async def test_get_summary_with_wash_sales(self, make_pool: Any) -> None:
        """Get summary with multiple wash sales."""
        query_responses = [
            {"wash_sale_count": 5, "total_disallowed": Decimal("2500.50")},
        ]

        pool = make_pool(query_responses=query_responses)
        detector = WashSaleDetector(pool)

        summary = await detector.get_wash_sale_summary(user_id="user-123", tax_year=2024)

        assert summary["wash_sale_count"] == 5
        assert summary["total_disallowed"] == Decimal("2500.50")

    @pytest.mark.asyncio()
    async def test_get_summary_handles_null_result(self, make_pool: Any) -> None:
        """Get summary handles None result from query."""
        query_responses = [None]  # No result from DB

        pool = make_pool(query_responses=query_responses)
        detector = WashSaleDetector(pool)

        summary = await detector.get_wash_sale_summary(user_id="user-123", tax_year=2024)

        assert summary["wash_sale_count"] == 0
        assert summary["total_disallowed"] == Decimal("0")

    @pytest.mark.asyncio()
    async def test_get_summary_handles_null_fields(self, make_pool: Any) -> None:
        """Get summary handles null fields in result."""
        query_responses = [
            {"wash_sale_count": None, "total_disallowed": None},
        ]

        pool = make_pool(query_responses=query_responses)
        detector = WashSaleDetector(pool)

        summary = await detector.get_wash_sale_summary(user_id="user-123", tax_year=2024)

        assert summary["wash_sale_count"] == 0
        assert summary["total_disallowed"] == Decimal("0")

    @pytest.mark.asyncio()
    async def test_get_summary_different_tax_year(self, make_pool: Any) -> None:
        """Get summary for specific tax year."""
        query_responses = [
            {"wash_sale_count": 3, "total_disallowed": Decimal("1500")},
        ]

        pool = make_pool(query_responses=query_responses)
        detector = WashSaleDetector(pool)

        summary = await detector.get_wash_sale_summary(user_id="user-456", tax_year=2023)

        assert summary["wash_sale_count"] == 3
        assert summary["total_disallowed"] == Decimal("1500")


class TestWashSaleWindowConstant:
    """Test wash sale window constant."""

    def test_window_is_30_days(self) -> None:
        """IRS wash sale window is 30 days."""
        assert WASH_SALE_WINDOW_DAYS == 30
