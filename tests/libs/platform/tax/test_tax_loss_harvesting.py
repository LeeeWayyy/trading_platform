"""Tests for tax-loss harvesting recommendations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from libs.platform.tax.tax_loss_harvesting import (
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
        self._execute_history: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        """Record execute calls for verification."""
        self._execute_history.append((query, params))
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
    async def test_holding_period_boundary_exactly_365_days(self, make_pool: Any) -> None:
        """Lots held exactly 365 days are short-term (boundary test)."""
        lot_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=365)  # Exactly 365

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
    async def test_holding_period_boundary_366_days(self, make_pool: Any) -> None:
        """Lots held 366 days are long-term (boundary test)."""
        lot_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=366)  # Just over 365

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

    @pytest.mark.asyncio()
    async def test_repurchase_restriction_date_set(self, make_pool: Any) -> None:
        """Repurchase restriction date is set for all opportunities."""
        lot_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=100)

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
        opp = recommendation.opportunities[0]
        assert opp.repurchase_restricted_until is not None
        # Should be 31 days from today
        expected_date = (datetime.now(UTC) + timedelta(days=31)).date()
        assert opp.repurchase_restricted_until == expected_date

    @pytest.mark.asyncio()
    async def test_multiple_lots_sorted_by_loss(self, make_pool: Any) -> None:
        """Multiple opportunities sorted by unrealized loss (most negative first)."""
        lot1_id = uuid4()
        lot2_id = uuid4()
        lot3_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=100)

        rows = [
            {
                "id": lot1_id,
                "symbol": "AAPL",
                "remaining_quantity": Decimal("100"),
                "cost_per_share": Decimal("200"),  # $5000 loss
                "acquired_at": acquired,
            },
            {
                "id": lot2_id,
                "symbol": "GOOG",
                "remaining_quantity": Decimal("50"),
                "cost_per_share": Decimal("3000"),  # $10000 loss
                "acquired_at": acquired,
            },
            {
                "id": lot3_id,
                "symbol": "MSFT",
                "remaining_quantity": Decimal("200"),
                "cost_per_share": Decimal("350"),  # $20000 loss
                "acquired_at": acquired,
            },
        ]

        pool = make_pool(rows=rows)
        harvester = TaxLossHarvester(pool)

        recommendation = await harvester.find_opportunities(
            user_id="user-123",
            current_prices={
                "AAPL": Decimal("150"),  # $5000 loss
                "GOOG": Decimal("2800"),  # $10000 loss
                "MSFT": Decimal("250"),  # $20000 loss
            },
        )

        assert len(recommendation.opportunities) == 3
        # Sorted by most negative first
        assert recommendation.opportunities[0].symbol == "MSFT"  # -$20000
        assert recommendation.opportunities[1].symbol == "GOOG"  # -$10000
        assert recommendation.opportunities[2].symbol == "AAPL"  # -$5000

    @pytest.mark.asyncio()
    async def test_uuid_conversion_from_string(self, make_pool: Any) -> None:
        """Handles lot_id as string and converts to UUID."""
        lot_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=100)

        rows = [
            {
                "id": str(lot_id),  # String UUID
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
        assert recommendation.opportunities[0].lot_id == lot_id

    @pytest.mark.asyncio()
    async def test_loss_exactly_at_threshold(self, make_pool: Any) -> None:
        """Loss exactly at threshold is included (boundary test)."""
        lot_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=100)

        rows = [
            {
                "id": lot_id,
                "symbol": "AAPL",
                "remaining_quantity": Decimal("1"),
                "cost_per_share": Decimal("200"),
                "acquired_at": acquired,
            }
        ]

        pool = make_pool(rows=rows)
        harvester = TaxLossHarvester(pool)

        # Price $100 = $100 loss (exactly at threshold)
        recommendation = await harvester.find_opportunities(
            user_id="user-123",
            current_prices={"AAPL": Decimal("100")},
            min_loss_threshold=Decimal("100"),
        )

        assert len(recommendation.opportunities) == 1

    @pytest.mark.asyncio()
    async def test_loss_just_below_threshold_excluded(self, make_pool: Any) -> None:
        """Loss just below threshold is excluded (boundary test)."""
        lot_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=100)

        rows = [
            {
                "id": lot_id,
                "symbol": "AAPL",
                "remaining_quantity": Decimal("1"),
                "cost_per_share": Decimal("199.99"),
                "acquired_at": acquired,
            }
        ]

        pool = make_pool(rows=rows)
        harvester = TaxLossHarvester(pool)

        # Price $100 = $99.99 loss (just below $100 threshold)
        recommendation = await harvester.find_opportunities(
            user_id="user-123",
            current_prices={"AAPL": Decimal("100")},
            min_loss_threshold=Decimal("100"),
        )

        assert len(recommendation.opportunities) == 0

    @pytest.mark.asyncio()
    async def test_mixed_gains_and_losses(self, make_pool: Any) -> None:
        """Only losses are included, gains are filtered out."""
        lot1_id = uuid4()
        lot2_id = uuid4()
        lot3_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=100)

        rows = [
            {
                "id": lot1_id,
                "symbol": "AAPL",
                "remaining_quantity": Decimal("100"),
                "cost_per_share": Decimal("200"),  # Loss
                "acquired_at": acquired,
            },
            {
                "id": lot2_id,
                "symbol": "GOOG",
                "remaining_quantity": Decimal("50"),
                "cost_per_share": Decimal("2000"),  # Gain
                "acquired_at": acquired,
            },
            {
                "id": lot3_id,
                "symbol": "MSFT",
                "remaining_quantity": Decimal("200"),
                "cost_per_share": Decimal("350"),  # Loss
                "acquired_at": acquired,
            },
        ]

        pool = make_pool(rows=rows)
        harvester = TaxLossHarvester(pool)

        recommendation = await harvester.find_opportunities(
            user_id="user-123",
            current_prices={
                "AAPL": Decimal("150"),  # Loss
                "GOOG": Decimal("2800"),  # Gain
                "MSFT": Decimal("250"),  # Loss
            },
        )

        assert len(recommendation.opportunities) == 2
        symbols = {opp.symbol for opp in recommendation.opportunities}
        assert symbols == {"AAPL", "MSFT"}


class TestWashSaleDetection:
    """Test wash sale detection logic."""

    @pytest.mark.asyncio()
    async def test_no_wash_sale_risk_when_no_recent_purchases(self, make_pool: Any) -> None:
        """No wash sale risk when no recent purchases exist."""
        lot_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=100)

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
        opp = recommendation.opportunities[0]
        assert opp.wash_sale_risk is False
        assert opp.wash_sale_clear_date is None

    @pytest.mark.asyncio()
    async def test_wash_sale_excludes_from_total_loss(self, make_pool: Any) -> None:
        """Opportunities with wash sale risk excluded from total harvestable loss."""
        # This test simulates wash sale detection by using a custom mock
        # that returns wash sale data when queried
        lot1_id = uuid4()
        acquired_old = datetime.now(UTC) - timedelta(days=100)
        acquired_recent = datetime.now(UTC) - timedelta(days=15)  # Within 30-day window

        # First query returns both lots
        # Subsequent queries for wash sale check return count and max_acquired
        class WashSaleCursor(MockAsyncCursor):
            def __init__(self, *args: Any, **kwargs: Any):
                super().__init__(*args, **kwargs)
                self._query_count = 0

            async def fetchall(self) -> list[dict[str, Any]]:
                if self._query_count == 0:
                    self._query_count += 1
                    return self._rows
                return []

            async def fetchone(self) -> dict[str, Any] | None:
                # For wash sale COUNT query
                if "COUNT(*)" in self._execute_history[-1][0]:
                    return {"count": 1}  # Has recent purchase
                # For wash sale MAX(acquired_at) query
                if "MAX(acquired_at)" in self._execute_history[-1][0]:
                    return {"max_acquired": acquired_recent}
                return {"count": 0}

        rows = [
            {
                "id": lot1_id,
                "symbol": "AAPL",
                "remaining_quantity": Decimal("100"),
                "cost_per_share": Decimal("200"),
                "acquired_at": acquired_old,
            }
        ]

        cursor = WashSaleCursor(rows=rows)
        conn = MockAsyncConnection(cursor)
        pool = MockAsyncPool(conn)
        harvester = TaxLossHarvester(pool)

        recommendation = await harvester.find_opportunities(
            user_id="user-123",
            current_prices={"AAPL": Decimal("150")},
        )

        assert len(recommendation.opportunities) == 1
        opp = recommendation.opportunities[0]
        assert opp.wash_sale_risk is True
        assert opp.wash_sale_clear_date is not None
        # Total loss should be 0 because wash sale risk
        assert recommendation.total_harvestable_loss == Decimal("0")

    @pytest.mark.asyncio()
    async def test_wash_sale_clear_date_calculated(self, make_pool: Any) -> None:
        """Wash sale clear date is 31 days after most recent purchase."""
        lot_id = uuid4()
        acquired_old = datetime.now(UTC) - timedelta(days=100)
        acquired_recent = datetime.now(UTC) - timedelta(days=15)

        class WashSaleCursor(MockAsyncCursor):
            def __init__(self, *args: Any, **kwargs: Any):
                super().__init__(*args, **kwargs)
                self._query_count = 0

            async def fetchall(self) -> list[dict[str, Any]]:
                if self._query_count == 0:
                    self._query_count += 1
                    return self._rows
                return []

            async def fetchone(self) -> dict[str, Any] | None:
                if "COUNT(*)" in self._execute_history[-1][0]:
                    return {"count": 1}
                if "MAX(acquired_at)" in self._execute_history[-1][0]:
                    return {"max_acquired": acquired_recent}
                return {"count": 0}

        rows = [
            {
                "id": lot_id,
                "symbol": "AAPL",
                "remaining_quantity": Decimal("100"),
                "cost_per_share": Decimal("200"),
                "acquired_at": acquired_old,
            }
        ]

        cursor = WashSaleCursor(rows=rows)
        conn = MockAsyncConnection(cursor)
        pool = MockAsyncPool(conn)
        harvester = TaxLossHarvester(pool)

        recommendation = await harvester.find_opportunities(
            user_id="user-123",
            current_prices={"AAPL": Decimal("150")},
        )

        assert len(recommendation.opportunities) == 1
        opp = recommendation.opportunities[0]
        assert opp.wash_sale_risk is True
        expected_clear_date = (acquired_recent + timedelta(days=31)).date()
        assert opp.wash_sale_clear_date == expected_clear_date

    @pytest.mark.asyncio()
    async def test_estimated_savings_excludes_wash_sale_risk(self, make_pool: Any) -> None:
        """Estimated tax savings excludes opportunities with wash sale risk."""
        lot1_id = uuid4()
        lot2_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=100)
        acquired_recent = datetime.now(UTC) - timedelta(days=15)

        class WashSaleCursor(MockAsyncCursor):
            def __init__(self, *args: Any, **kwargs: Any):
                super().__init__(*args, **kwargs)
                self._query_count = 0

            async def fetchall(self) -> list[dict[str, Any]]:
                if self._query_count == 0:
                    self._query_count += 1
                    return self._rows
                return []

            async def fetchone(self) -> dict[str, Any] | None:
                # First lot (AAPL) has wash sale risk
                # Second lot (GOOG) does not
                if "symbol = %s" in self._execute_history[-1][0]:
                    symbol = self._execute_history[-1][1][1]
                    if symbol == "AAPL":
                        if "COUNT(*)" in self._execute_history[-1][0]:
                            return {"count": 1}
                        if "MAX(acquired_at)" in self._execute_history[-1][0]:
                            return {"max_acquired": acquired_recent}
                return {"count": 0}

        rows = [
            {
                "id": lot1_id,
                "symbol": "AAPL",
                "remaining_quantity": Decimal("100"),
                "cost_per_share": Decimal("200"),  # $5000 loss
                "acquired_at": acquired,
            },
            {
                "id": lot2_id,
                "symbol": "GOOG",
                "remaining_quantity": Decimal("50"),
                "cost_per_share": Decimal("3000"),  # $10000 loss
                "acquired_at": acquired,
            },
        ]

        cursor = WashSaleCursor(rows=rows)
        conn = MockAsyncConnection(cursor)
        pool = MockAsyncPool(conn)
        harvester = TaxLossHarvester(pool)

        recommendation = await harvester.find_opportunities(
            user_id="user-123",
            current_prices={
                "AAPL": Decimal("150"),  # $5000 loss (wash sale risk)
                "GOOG": Decimal("2800"),  # $10000 loss (no wash sale)
            },
        )

        assert len(recommendation.opportunities) == 2
        # Only GOOG's loss should be in total
        assert recommendation.total_harvestable_loss == Decimal("10000")
        # Only GOOG's savings should be in estimate
        expected_savings = Decimal("10000") * SHORT_TERM_TAX_RATE
        assert recommendation.estimated_tax_savings == expected_savings


class TestHarvestSummaryBySymbol:
    """Test harvest summary grouping by symbol."""

    @pytest.mark.asyncio()
    async def test_get_harvest_summary_single_symbol(self, make_pool: Any) -> None:
        """Summary for single symbol with single lot."""
        lot_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=100)

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

        summary = await harvester.get_harvest_summary_by_symbol(
            user_id="user-123",
            current_prices={"AAPL": Decimal("150")},
        )

        assert "AAPL" in summary
        assert summary["AAPL"]["total_loss"] == Decimal("5000")
        assert summary["AAPL"]["shares"] == Decimal("100")
        assert summary["AAPL"]["lots_count"] == Decimal("1")

    @pytest.mark.asyncio()
    async def test_get_harvest_summary_multiple_lots_same_symbol(self, make_pool: Any) -> None:
        """Summary aggregates multiple lots for same symbol."""
        lot1_id = uuid4()
        lot2_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=100)

        rows = [
            {
                "id": lot1_id,
                "symbol": "AAPL",
                "remaining_quantity": Decimal("100"),
                "cost_per_share": Decimal("200"),  # $5000 loss
                "acquired_at": acquired,
            },
            {
                "id": lot2_id,
                "symbol": "AAPL",
                "remaining_quantity": Decimal("50"),
                "cost_per_share": Decimal("180"),  # $1500 loss
                "acquired_at": acquired,
            },
        ]

        pool = make_pool(rows=rows)
        harvester = TaxLossHarvester(pool)

        summary = await harvester.get_harvest_summary_by_symbol(
            user_id="user-123",
            current_prices={"AAPL": Decimal("150")},
        )

        assert "AAPL" in summary
        assert summary["AAPL"]["total_loss"] == Decimal("6500")  # 5000 + 1500
        assert summary["AAPL"]["shares"] == Decimal("150")  # 100 + 50
        assert summary["AAPL"]["lots_count"] == Decimal("2")

    @pytest.mark.asyncio()
    async def test_get_harvest_summary_multiple_symbols(self, make_pool: Any) -> None:
        """Summary groups by symbol correctly."""
        lot1_id = uuid4()
        lot2_id = uuid4()
        lot3_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=100)

        rows = [
            {
                "id": lot1_id,
                "symbol": "AAPL",
                "remaining_quantity": Decimal("100"),
                "cost_per_share": Decimal("200"),
                "acquired_at": acquired,
            },
            {
                "id": lot2_id,
                "symbol": "GOOG",
                "remaining_quantity": Decimal("50"),
                "cost_per_share": Decimal("3000"),
                "acquired_at": acquired,
            },
            {
                "id": lot3_id,
                "symbol": "AAPL",
                "remaining_quantity": Decimal("50"),
                "cost_per_share": Decimal("180"),
                "acquired_at": acquired,
            },
        ]

        pool = make_pool(rows=rows)
        harvester = TaxLossHarvester(pool)

        summary = await harvester.get_harvest_summary_by_symbol(
            user_id="user-123",
            current_prices={
                "AAPL": Decimal("150"),
                "GOOG": Decimal("2800"),
            },
        )

        assert len(summary) == 2
        assert "AAPL" in summary
        assert "GOOG" in summary
        assert summary["AAPL"]["lots_count"] == Decimal("2")
        assert summary["GOOG"]["lots_count"] == Decimal("1")

    @pytest.mark.asyncio()
    async def test_get_harvest_summary_includes_all_losses(self, make_pool: Any) -> None:
        """Summary includes all losses regardless of threshold (uses min_loss_threshold=0)."""
        lot_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=100)

        rows = [
            {
                "id": lot_id,
                "symbol": "AAPL",
                "remaining_quantity": Decimal("1"),
                "cost_per_share": Decimal("150"),  # $50 loss (below default threshold)
                "acquired_at": acquired,
            }
        ]

        pool = make_pool(rows=rows)
        harvester = TaxLossHarvester(pool)

        summary = await harvester.get_harvest_summary_by_symbol(
            user_id="user-123",
            current_prices={"AAPL": Decimal("100")},
        )

        # Should include even though loss is below default threshold
        assert "AAPL" in summary
        assert summary["AAPL"]["total_loss"] == Decimal("50")


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


class TestEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio()
    async def test_zero_remaining_quantity_filtered(self, make_pool: Any) -> None:
        """Lots with zero remaining quantity should not appear (filtered by SQL query)."""
        # Query already filters remaining_quantity > 0, so this shouldn't appear
        rows: list[dict[str, Any]] = []  # SQL query returns empty

        pool = make_pool(rows=rows)
        harvester = TaxLossHarvester(pool)

        recommendation = await harvester.find_opportunities(
            user_id="user-123",
            current_prices={"AAPL": Decimal("150")},
        )

        assert len(recommendation.opportunities) == 0

    @pytest.mark.asyncio()
    async def test_very_small_loss_below_threshold(self, make_pool: Any) -> None:
        """Very small losses below threshold are excluded."""
        lot_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=100)

        rows = [
            {
                "id": lot_id,
                "symbol": "AAPL",
                "remaining_quantity": Decimal("0.01"),
                "cost_per_share": Decimal("100"),
                "acquired_at": acquired,
            }
        ]

        pool = make_pool(rows=rows)
        harvester = TaxLossHarvester(pool)

        # 0.01 shares * $50 loss = $0.50 loss (way below threshold)
        recommendation = await harvester.find_opportunities(
            user_id="user-123",
            current_prices={"AAPL": Decimal("50")},
        )

        assert len(recommendation.opportunities) == 0

    @pytest.mark.asyncio()
    async def test_multiple_warnings_for_missing_prices(self, make_pool: Any) -> None:
        """Multiple lots with missing prices generate multiple warnings."""
        lot1_id = uuid4()
        lot2_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=100)

        rows = [
            {
                "id": lot1_id,
                "symbol": "UNKNOWN1",
                "remaining_quantity": Decimal("100"),
                "cost_per_share": Decimal("200"),
                "acquired_at": acquired,
            },
            {
                "id": lot2_id,
                "symbol": "UNKNOWN2",
                "remaining_quantity": Decimal("50"),
                "cost_per_share": Decimal("150"),
                "acquired_at": acquired,
            },
        ]

        pool = make_pool(rows=rows)
        harvester = TaxLossHarvester(pool)

        recommendation = await harvester.find_opportunities(
            user_id="user-123",
            current_prices={},
        )

        assert len(recommendation.opportunities) == 0
        assert len(recommendation.warnings) == 2
        assert any("UNKNOWN1" in w for w in recommendation.warnings)
        assert any("UNKNOWN2" in w for w in recommendation.warnings)

    @pytest.mark.asyncio()
    async def test_decimal_precision_in_calculations(self, make_pool: Any) -> None:
        """Decimal calculations maintain precision."""
        lot_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=400)  # Long-term

        rows = [
            {
                "id": lot_id,
                "symbol": "AAPL",
                "remaining_quantity": Decimal("33.333"),
                "cost_per_share": Decimal("100.001"),
                "acquired_at": acquired,
            }
        ]

        pool = make_pool(rows=rows)
        harvester = TaxLossHarvester(pool)

        recommendation = await harvester.find_opportunities(
            user_id="user-123",
            current_prices={"AAPL": Decimal("50.005")},
        )

        assert len(recommendation.opportunities) == 1
        opp = recommendation.opportunities[0]
        # Verify calculations use Decimal properly
        expected_cost = Decimal("33.333") * Decimal("100.001")
        expected_value = Decimal("33.333") * Decimal("50.005")
        expected_loss = expected_value - expected_cost
        assert opp.unrealized_loss == expected_loss
        # Tax savings should be quantized to cents
        assert recommendation.estimated_tax_savings.as_tuple().exponent == -2

    @pytest.mark.asyncio()
    async def test_empty_current_prices_dict(self, make_pool: Any) -> None:
        """Empty current_prices dict generates warnings for all lots."""
        lot1_id = uuid4()
        lot2_id = uuid4()
        acquired = datetime.now(UTC) - timedelta(days=100)

        rows = [
            {
                "id": lot1_id,
                "symbol": "AAPL",
                "remaining_quantity": Decimal("100"),
                "cost_per_share": Decimal("200"),
                "acquired_at": acquired,
            },
            {
                "id": lot2_id,
                "symbol": "GOOG",
                "remaining_quantity": Decimal("50"),
                "cost_per_share": Decimal("3000"),
                "acquired_at": acquired,
            },
        ]

        pool = make_pool(rows=rows)
        harvester = TaxLossHarvester(pool)

        recommendation = await harvester.find_opportunities(
            user_id="user-123",
            current_prices={},
        )

        assert len(recommendation.opportunities) == 0
        assert len(recommendation.warnings) == 2
