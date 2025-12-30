"""Tests for tax-loss harvesting recommendations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from libs.tax.tax_loss_harvesting import (
    DEFAULT_MIN_LOSS_THRESHOLD,
    LONG_TERM_TAX_RATE,
    SHORT_TERM_TAX_RATE,
    TaxLossHarvester,
)


class MockAsyncCursor:
    """Mock async cursor for psycopg-style usage."""

    def __init__(self, *, rows: list[dict[str, Any]] | None = None):
        self._rows = rows or []
        self._fetchone_index = 0

    async def execute(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    async def fetchone(self) -> dict[str, Any] | None:
        if self._fetchone_index < len(self._rows):
            result = self._rows[self._fetchone_index]
            self._fetchone_index += 1
            return result
        # For COUNT queries, return 0
        return {"count": 0}


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

    def _make(rows: list[dict[str, Any]] | None = None) -> MockAsyncPool:
        cursor = MockAsyncCursor(rows=rows)
        conn = MockAsyncConnection(cursor)
        return MockAsyncPool(conn)

    return _make


class TestTaxLossHarvesting:
    """Test tax-loss harvesting opportunity detection."""

    @pytest.mark.asyncio()
    async def test_find_opportunities_empty_when_no_lots(self, make_pool: Any) -> None:
        """Returns empty when user has no open lots."""
        pool = make_pool(rows=[])
        harvester = TaxLossHarvester(pool)

        recommendation = await harvester.find_opportunities(
            user_id="user-123",
            current_prices={"AAPL": Decimal("150")},
        )

        assert len(recommendation.opportunities) == 0
        assert recommendation.total_harvestable_loss == Decimal(0)

    @pytest.mark.asyncio()
    async def test_find_opportunities_skips_gains(self, make_pool: Any) -> None:
        """Lots with unrealized gains are not harvesting opportunities."""
        lot_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=100)

        rows = [
            {
                "id": lot_id,
                "symbol": "AAPL",
                "remaining_quantity": Decimal("100"),
                "cost_per_share": Decimal("100"),  # Cost $100/share
                "acquired_at": acquired,
            }
        ]

        pool = make_pool(rows=rows)
        harvester = TaxLossHarvester(pool)

        # Current price $150 = unrealized gain
        recommendation = await harvester.find_opportunities(
            user_id="user-123",
            current_prices={"AAPL": Decimal("150")},
        )

        assert len(recommendation.opportunities) == 0

    @pytest.mark.asyncio()
    async def test_find_opportunities_identifies_losses(self, make_pool: Any) -> None:
        """Lots with unrealized losses are identified as opportunities."""
        lot_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=100)

        rows = [
            {
                "id": lot_id,
                "symbol": "AAPL",
                "remaining_quantity": Decimal("100"),
                "cost_per_share": Decimal("200"),  # Cost $200/share
                "acquired_at": acquired,
            }
        ]

        pool = make_pool(rows=rows)
        harvester = TaxLossHarvester(pool)

        # Current price $150 = $50/share loss = $5000 total loss
        recommendation = await harvester.find_opportunities(
            user_id="user-123",
            current_prices={"AAPL": Decimal("150")},
        )

        assert len(recommendation.opportunities) == 1
        assert recommendation.opportunities[0].symbol == "AAPL"
        assert recommendation.opportunities[0].unrealized_loss == Decimal("-5000")
        assert recommendation.total_harvestable_loss == Decimal("5000")

    @pytest.mark.asyncio()
    async def test_find_opportunities_respects_threshold(self, make_pool: Any) -> None:
        """Losses below threshold are not included."""
        lot_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=100)

        rows = [
            {
                "id": lot_id,
                "symbol": "AAPL",
                "remaining_quantity": Decimal("1"),
                "cost_per_share": Decimal("150"),  # Cost $150
                "acquired_at": acquired,
            }
        ]

        pool = make_pool(rows=rows)
        harvester = TaxLossHarvester(pool)

        # Current price $100 = $50 loss (below default $100 threshold)
        recommendation = await harvester.find_opportunities(
            user_id="user-123",
            current_prices={"AAPL": Decimal("100")},
            min_loss_threshold=Decimal("100"),
        )

        assert len(recommendation.opportunities) == 0

    @pytest.mark.asyncio()
    async def test_holding_period_short_term(self, make_pool: Any) -> None:
        """Lots held <= 365 days are short-term."""
        lot_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=100)  # Short-term

        rows = [
            {
                "id": lot_id,
                "symbol": "AAPL",
                "remaining_quantity": Decimal("100"),
                "cost_per_share": Decimal("200"),
                "acquired_at": acquired,
            }
        ]

        pool = make_pool(rows=rows)
        harvester = TaxLossHarvester(pool)

        recommendation = await harvester.find_opportunities(
            user_id="user-123",
            current_prices={"AAPL": Decimal("150")},
        )

        assert len(recommendation.opportunities) == 1
        assert recommendation.opportunities[0].holding_period == "short_term"

    @pytest.mark.asyncio()
    async def test_holding_period_long_term(self, make_pool: Any) -> None:
        """Lots held > 365 days are long-term."""
        lot_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=400)  # Long-term

        rows = [
            {
                "id": lot_id,
                "symbol": "AAPL",
                "remaining_quantity": Decimal("100"),
                "cost_per_share": Decimal("200"),
                "acquired_at": acquired,
            }
        ]

        pool = make_pool(rows=rows)
        harvester = TaxLossHarvester(pool)

        recommendation = await harvester.find_opportunities(
            user_id="user-123",
            current_prices={"AAPL": Decimal("150")},
        )

        assert len(recommendation.opportunities) == 1
        assert recommendation.opportunities[0].holding_period == "long_term"

    @pytest.mark.asyncio()
    async def test_missing_price_warning(self, make_pool: Any) -> None:
        """Lots without prices generate warnings."""
        lot_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=100)

        rows = [
            {
                "id": lot_id,
                "symbol": "UNKNOWN",
                "remaining_quantity": Decimal("100"),
                "cost_per_share": Decimal("200"),
                "acquired_at": acquired,
            }
        ]

        pool = make_pool(rows=rows)
        harvester = TaxLossHarvester(pool)

        recommendation = await harvester.find_opportunities(
            user_id="user-123",
            current_prices={},  # No prices
        )

        assert len(recommendation.opportunities) == 0
        assert len(recommendation.warnings) == 1
        assert "UNKNOWN" in recommendation.warnings[0]

    @pytest.mark.asyncio()
    async def test_estimated_tax_savings_short_term(self, make_pool: Any) -> None:
        """Tax savings estimated at short-term rate for short-term losses."""
        lot_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=100)  # Short-term

        rows = [
            {
                "id": lot_id,
                "symbol": "AAPL",
                "remaining_quantity": Decimal("100"),
                "cost_per_share": Decimal("200"),  # $20000 cost
                "acquired_at": acquired,
            }
        ]

        pool = make_pool(rows=rows)
        harvester = TaxLossHarvester(pool)

        # Price $150 = $5000 loss, short-term rate 35% = $1750 savings
        recommendation = await harvester.find_opportunities(
            user_id="user-123",
            current_prices={"AAPL": Decimal("150")},
        )

        expected_savings = Decimal("5000") * SHORT_TERM_TAX_RATE
        assert recommendation.estimated_tax_savings == expected_savings

    @pytest.mark.asyncio()
    async def test_estimated_tax_savings_long_term(self, make_pool: Any) -> None:
        """Tax savings estimated at long-term rate for long-term losses."""
        lot_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=400)  # Long-term

        rows = [
            {
                "id": lot_id,
                "symbol": "AAPL",
                "remaining_quantity": Decimal("100"),
                "cost_per_share": Decimal("200"),  # $20000 cost
                "acquired_at": acquired,
            }
        ]

        pool = make_pool(rows=rows)
        harvester = TaxLossHarvester(pool)

        # Price $150 = $5000 loss, long-term rate 15% = $750 savings
        recommendation = await harvester.find_opportunities(
            user_id="user-123",
            current_prices={"AAPL": Decimal("150")},
        )

        expected_savings = Decimal("5000") * LONG_TERM_TAX_RATE
        assert recommendation.estimated_tax_savings == expected_savings


class TestTaxRateConstants:
    """Test tax rate constants."""

    def test_short_term_rate(self) -> None:
        """Short-term rate is 35%."""
        assert SHORT_TERM_TAX_RATE == Decimal("0.35")

    def test_long_term_rate(self) -> None:
        """Long-term rate is 15%."""
        assert LONG_TERM_TAX_RATE == Decimal("0.15")

    def test_default_threshold(self) -> None:
        """Default minimum loss threshold is $100."""
        assert DEFAULT_MIN_LOSS_THRESHOLD == Decimal("100")
