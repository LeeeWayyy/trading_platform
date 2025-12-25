# P4T7 C6: Tax Lot Reporter - Advanced - Component Plan (STRETCH)

**Component:** C6 - T9.6 Tax Lot Reporter - Advanced
**Parent Task:** P4T7 Web Console Research & Reporting
**Status:** PLANNING (STRETCH)
**Estimated Effort:** 2-3 days
**Dependencies:** C5 (Tax Lot Reporter - Core)

---

## Overview

Implement T9.6 Tax Lot Reporter - Advanced with wash sale detection, adjustments, and tax-loss harvesting recommendations.

**Note:** This is a STRETCH item. Implement only if schedule permits after core items complete.

## Acceptance Criteria (from P4T7_TASK.md)

- [ ] Wash sale rule detection (30-day window before/after sale)
- [ ] Wash sale adjustment calculations (disallowed loss added to replacement cost basis)
- [ ] Disallowed loss tracking with carry-forward display
- [ ] IRS Form 8949 format export
- [ ] Tax-loss harvesting recommendations (identify losses to realize while avoiding wash sales)
- [ ] Estimated tax liability calculator
- [ ] Multi-year wash sale tracking across tax years
- [ ] RBAC: VIEW_TAX_REPORTS permission required

---

## Architecture

### Wash Sale Detection Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                      Sale Transaction                            │
│  Symbol: AAPL, Qty: 100, Proceeds: $150/share                   │
│  Loss: $1,000 (sold at $150, cost basis was $160)               │
└───────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Wash Sale Detector                             │
│                                                                  │
│  1. Look back 30 days for purchases of same/similar security   │
│  2. Look forward 30 days for purchases of same/similar security │
│  3. If replacement found: WASH SALE TRIGGERED                   │
└───────────────────────────────────────────────────────────────────┘
                            │
                    ┌───────┴───────┐
                    │               │
            No Replacement    Replacement Found
                    │               │
                    ▼               ▼
            Loss Allowed    ┌─────────────────────┐
                            │  Wash Sale Handling │
                            │                     │
                            │  1. Disallow loss   │
                            │  2. Add to new lot  │
                            │     cost basis      │
                            │  3. Extend holding  │
                            │     period          │
                            └─────────────────────┘
```

### File Structure

```
libs/tax/
├── wash_sale_detector.py        # Wash sale detection
├── tax_loss_harvesting.py       # Harvesting recommendations
├── form_8949.py                 # IRS form export

tests/libs/tax/
├── test_wash_sale.py
├── test_tax_loss_harvesting.py
└── fixtures/
    └── wash_sale_scenarios.json  # IRS example scenarios

docs/ADRs/
└── ADR-0031-tax-lot-tracking.md
```

---

## Implementation Details

### 1. Wash Sale Detector

```python
# libs/tax/wash_sale_detector.py
"""Wash sale rule detection per IRS Publication 550."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    import asyncpg
    from libs.tax.cost_basis import TaxLot

logger = logging.getLogger(__name__)

# IRS wash sale window: 30 days before to 30 days after
WASH_SALE_WINDOW_DAYS = 30


@dataclass
class WashSaleMatch:
    """A wash sale match between a loss sale and replacement purchase."""
    loss_disposition_id: UUID
    replacement_lot_id: UUID
    symbol: str
    disallowed_loss: Decimal
    matching_shares: Decimal
    sale_date: datetime
    replacement_date: datetime


@dataclass
class WashSaleAdjustment:
    """Adjustment to apply for wash sale."""
    lot_id: UUID
    disallowed_loss: Decimal
    basis_adjustment: Decimal
    holding_period_adjustment_days: int


class WashSaleDetector:
    """Detects wash sales per IRS rules.

    A wash sale occurs when you sell a security at a loss and buy
    substantially identical securities within 30 days before or after
    the sale.

    Per IRS Publication 550:
    - Window is 61 days total (30 before + sale day + 30 after)
    - Substantially identical includes:
      - Same stock
      - Options on same stock
      - Contracts to acquire same stock
    """

    def __init__(self, db_pool: asyncpg.Pool):
        self._db = db_pool

    async def detect_wash_sales(
        self,
        account_id: UUID,
        symbol: str,
        sale_date: datetime,
        loss_amount: Decimal,
        shares_sold: Decimal,
    ) -> list[WashSaleMatch]:
        """Detect wash sales for a loss transaction.

        Args:
            account_id: Account that sold
            symbol: Symbol sold at a loss
            sale_date: Date of sale
            loss_amount: Total loss on sale (negative)
            shares_sold: Number of shares sold

        Returns:
            List of WashSaleMatch for any matching replacements
        """
        if loss_amount >= 0:
            # No loss, no wash sale possible
            return []

        window_start = sale_date - timedelta(days=WASH_SALE_WINDOW_DAYS)
        window_end = sale_date + timedelta(days=WASH_SALE_WINDOW_DAYS)

        async with self._db.acquire() as conn:
            # Find purchases in wash sale window
            replacements = await conn.fetch(
                """
                SELECT id, symbol, quantity, cost_per_share, acquired_at
                FROM tax_lots
                WHERE account_id = $1
                  AND symbol = $2
                  AND acquired_at >= $3
                  AND acquired_at <= $4
                  AND acquired_at != $5
                ORDER BY acquired_at
                """,
                account_id,
                symbol,
                window_start,
                window_end,
                sale_date,  # Exclude same-day purchases counted elsewhere
            )

            if not replacements:
                return []

            # Calculate wash sale matches
            matches = []
            remaining_loss = abs(loss_amount)
            remaining_shares = shares_sold
            loss_per_share = remaining_loss / shares_sold

            for row in replacements:
                if remaining_shares <= 0:
                    break

                # Match shares proportionally
                matching_shares = min(row["quantity"], remaining_shares)
                matching_loss = matching_shares * loss_per_share

                matches.append(WashSaleMatch(
                    loss_disposition_id=UUID("00000000-0000-0000-0000-000000000000"),  # Set later
                    replacement_lot_id=row["id"],
                    symbol=symbol,
                    disallowed_loss=matching_loss,
                    matching_shares=matching_shares,
                    sale_date=sale_date,
                    replacement_date=row["acquired_at"],
                ))

                remaining_shares -= matching_shares
                remaining_loss -= matching_loss

            return matches

    async def apply_wash_sale_adjustments(
        self,
        matches: list[WashSaleMatch],
        loss_disposition_id: UUID,
    ) -> list[WashSaleAdjustment]:
        """Apply wash sale adjustments to replacement lots.

        For each match:
        1. Record disallowed loss in disposition
        2. Add disallowed loss to replacement lot cost basis
        3. Extend holding period of replacement lot

        Args:
            matches: Wash sale matches to apply
            loss_disposition_id: Disposition ID that triggered wash sale

        Returns:
            List of adjustments applied
        """
        adjustments = []

        async with self._db.acquire() as conn:
            async with conn.transaction():
                for match in matches:
                    # Update disposition with disallowed amount
                    await conn.execute(
                        """
                        UPDATE tax_lot_dispositions
                        SET wash_sale_disallowed = wash_sale_disallowed + $1,
                            wash_sale_adjustment_lot_id = $2
                        WHERE id = $3
                        """,
                        match.disallowed_loss,
                        match.replacement_lot_id,
                        loss_disposition_id,
                    )

                    # Get original lot dates for holding period calculation
                    original_lot = await conn.fetchrow(
                        """
                        SELECT acquired_at FROM tax_lots
                        WHERE id = (
                            SELECT lot_id FROM tax_lot_dispositions WHERE id = $1
                        )
                        """,
                        loss_disposition_id,
                    )

                    # Calculate holding period adjustment
                    # Per IRS: Add original holding period to replacement
                    if original_lot:
                        days_held = (match.sale_date - original_lot["acquired_at"]).days
                    else:
                        days_held = 0

                    # Update replacement lot with adjusted basis
                    await conn.execute(
                        """
                        UPDATE tax_lots
                        SET cost_per_share = cost_per_share + ($1 / remaining_quantity),
                            total_cost = total_cost + $1
                        WHERE id = $2
                        """,
                        match.disallowed_loss,
                        match.replacement_lot_id,
                    )

                    adjustments.append(WashSaleAdjustment(
                        lot_id=match.replacement_lot_id,
                        disallowed_loss=match.disallowed_loss,
                        basis_adjustment=match.disallowed_loss,
                        holding_period_adjustment_days=days_held,
                    ))

                    logger.info(
                        "Applied wash sale adjustment",
                        extra={
                            "disposition_id": str(loss_disposition_id),
                            "replacement_lot_id": str(match.replacement_lot_id),
                            "disallowed_loss": str(match.disallowed_loss),
                        },
                    )

        return adjustments

    async def get_wash_sale_summary(
        self,
        account_id: UUID,
        tax_year: int,
    ) -> dict:
        """Get wash sale summary for tax year."""
        async with self._db.acquire() as conn:
            result = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) as wash_sale_count,
                    SUM(wash_sale_disallowed) as total_disallowed
                FROM tax_lot_dispositions d
                JOIN tax_lots l ON d.lot_id = l.id
                WHERE l.account_id = $1
                  AND date_trunc('year', d.disposed_at) = date_trunc('year', $2::date)
                  AND wash_sale_disallowed > 0
                """,
                account_id,
                datetime(tax_year, 1, 1),
            )

            return {
                "wash_sale_count": result["wash_sale_count"] or 0,
                "total_disallowed": result["total_disallowed"] or Decimal(0),
            }
```

### 2. Tax-Loss Harvesting Recommender

```python
# libs/tax/tax_loss_harvesting.py
"""Tax-loss harvesting recommendations."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    import asyncpg
    from libs.tax.cost_basis import TaxLot
    from libs.tax.wash_sale_detector import WashSaleDetector

logger = logging.getLogger(__name__)


@dataclass
class HarvestingOpportunity:
    """A potential tax-loss harvesting opportunity."""
    lot_id: UUID
    symbol: str
    shares: Decimal
    cost_basis: Decimal
    current_price: Decimal
    unrealized_loss: Decimal
    holding_period: str  # short_term, long_term
    wash_sale_risk: bool  # True if recent purchases exist
    wash_sale_clear_date: date | None  # When wash sale window ends


@dataclass
class HarvestingRecommendation:
    """Recommendation for tax-loss harvesting."""
    opportunities: list[HarvestingOpportunity]
    total_harvestable_loss: Decimal
    estimated_tax_savings: Decimal
    warnings: list[str]


class TaxLossHarvester:
    """Identifies tax-loss harvesting opportunities.

    Tax-loss harvesting involves selling securities at a loss to offset
    capital gains, while maintaining market exposure (optionally via
    similar but not identical securities).

    Key considerations:
    - Avoid wash sales (30-day rule)
    - Consider short-term vs long-term classification
    - Factor in transaction costs
    - Maintain portfolio allocation
    """

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        wash_sale_detector: WashSaleDetector,
    ):
        self._db = db_pool
        self._wash_detector = wash_sale_detector

    async def find_opportunities(
        self,
        account_id: UUID,
        current_prices: dict[str, Decimal],
        min_loss_threshold: Decimal = Decimal("100"),
    ) -> HarvestingRecommendation:
        """Find tax-loss harvesting opportunities.

        Args:
            account_id: Account to analyze
            current_prices: Current market prices by symbol
            min_loss_threshold: Minimum loss to consider

        Returns:
            HarvestingRecommendation with opportunities
        """
        async with self._db.acquire() as conn:
            # Get all open lots
            lots = await conn.fetch(
                """
                SELECT * FROM tax_lots
                WHERE account_id = $1 AND remaining_quantity > 0
                ORDER BY symbol, acquired_at
                """,
                account_id,
            )

            opportunities = []
            warnings = []
            total_loss = Decimal(0)

            for lot in lots:
                symbol = lot["symbol"]
                current_price = current_prices.get(symbol)

                if current_price is None:
                    warnings.append(f"No price available for {symbol}")
                    continue

                # Calculate unrealized loss
                current_value = lot["remaining_quantity"] * current_price
                cost_basis = lot["remaining_quantity"] * lot["cost_per_share"]
                unrealized_pnl = current_value - cost_basis

                if unrealized_pnl >= 0:
                    # Not a loss, skip
                    continue

                if abs(unrealized_pnl) < min_loss_threshold:
                    # Below threshold
                    continue

                # Check wash sale risk
                wash_sale_risk, clear_date = await self._check_wash_sale_risk(
                    conn, account_id, symbol, lot["id"]
                )

                # Determine holding period
                days_held = (datetime.now() - lot["acquired_at"]).days
                holding_period = "long_term" if days_held > 365 else "short_term"

                opportunities.append(HarvestingOpportunity(
                    lot_id=lot["id"],
                    symbol=symbol,
                    shares=lot["remaining_quantity"],
                    cost_basis=cost_basis,
                    current_price=current_price,
                    unrealized_loss=unrealized_pnl,  # Negative
                    holding_period=holding_period,
                    wash_sale_risk=wash_sale_risk,
                    wash_sale_clear_date=clear_date,
                ))

                if not wash_sale_risk:
                    total_loss += abs(unrealized_pnl)

            # Estimate tax savings (simplified)
            # Short-term: ordinary income rate (~35%)
            # Long-term: capital gains rate (~15%)
            estimated_savings = self._estimate_tax_savings(opportunities)

            return HarvestingRecommendation(
                opportunities=sorted(
                    opportunities,
                    key=lambda x: x.unrealized_loss,  # Most negative first
                ),
                total_harvestable_loss=total_loss,
                estimated_tax_savings=estimated_savings,
                warnings=warnings,
            )

    async def _check_wash_sale_risk(
        self,
        conn,
        account_id: UUID,
        symbol: str,
        lot_id: UUID,
    ) -> tuple[bool, date | None]:
        """Check if selling this lot would trigger wash sale.

        Returns:
            (has_risk, clear_date) - clear_date is when wash window ends
        """
        today = datetime.now()
        lookback = today - timedelta(days=30)

        # Check for recent purchases (excluding this lot)
        recent = await conn.fetchval(
            """
            SELECT COUNT(*) FROM tax_lots
            WHERE account_id = $1
              AND symbol = $2
              AND id != $3
              AND acquired_at >= $4
            """,
            account_id,
            symbol,
            lot_id,
            lookback,
        )

        if recent > 0:
            # Find when window clears
            latest = await conn.fetchval(
                """
                SELECT MAX(acquired_at) FROM tax_lots
                WHERE account_id = $1
                  AND symbol = $2
                  AND id != $3
                  AND acquired_at >= $4
                """,
                account_id,
                symbol,
                lot_id,
                lookback,
            )
            clear_date = (latest + timedelta(days=31)).date()
            return True, clear_date

        return False, None

    def _estimate_tax_savings(
        self,
        opportunities: list[HarvestingOpportunity],
    ) -> Decimal:
        """Estimate tax savings from harvesting.

        Uses simplified rates:
        - Short-term: 35% (ordinary income)
        - Long-term: 15% (capital gains)
        """
        savings = Decimal(0)

        for opp in opportunities:
            if opp.wash_sale_risk:
                continue

            loss = abs(opp.unrealized_loss)
            if opp.holding_period == "short_term":
                savings += loss * Decimal("0.35")
            else:
                savings += loss * Decimal("0.15")

        return savings
```

### 3. Form 8949 Export

```python
# libs/tax/form_8949.py
"""IRS Form 8949 export."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from libs.tax.export import TaxReportRow


@dataclass
class Form8949Row:
    """A row for IRS Form 8949."""
    description: str  # Column (a)
    date_acquired: date  # Column (b)
    date_sold: date  # Column (c)
    proceeds: Decimal  # Column (d)
    cost_basis: Decimal  # Column (e)
    adjustment_code: str | None  # Column (f) - W for wash sale
    adjustment_amount: Decimal | None  # Column (g)
    gain_or_loss: Decimal  # Column (h)


class Form8949Exporter:
    """Exports transactions in IRS Form 8949 format.

    Form 8949 is used to report sales and exchanges of capital assets.

    Part I: Short-term transactions (held <= 1 year)
    Part II: Long-term transactions (held > 1 year)

    Boxes:
    - Box A: Reported to IRS with basis shown
    - Box B: Reported to IRS, basis not shown
    - Box C: Not reported to IRS
    """

    def format_rows(
        self,
        rows: list[TaxReportRow],
    ) -> dict[str, list[Form8949Row]]:
        """Format rows for Form 8949.

        Returns:
            Dict with keys 'short_term' and 'long_term'
        """
        result = {
            "short_term": [],
            "long_term": [],
        }

        for row in rows:
            form_row = Form8949Row(
                description=f"{row.quantity} sh {row.symbol}",
                date_acquired=row.acquired_date,
                date_sold=row.disposed_date,
                proceeds=row.proceeds,
                cost_basis=row.cost_basis,
                adjustment_code="W" if row.wash_sale_adjustment else None,
                adjustment_amount=row.wash_sale_adjustment if row.wash_sale_adjustment else None,
                gain_or_loss=row.gain_loss + (row.wash_sale_adjustment or Decimal(0)),
            )

            if row.holding_period == "short_term":
                result["short_term"].append(form_row)
            else:
                result["long_term"].append(form_row)

        return result

    def to_csv(self, rows: dict[str, list[Form8949Row]]) -> str:
        """Export to CSV format matching Form 8949 columns."""
        import csv
        import io

        output = io.StringIO()
        writer = csv.writer(output)

        # Part I header
        writer.writerow(["PART I - SHORT-TERM CAPITAL GAINS AND LOSSES"])
        writer.writerow([
            "(a) Description",
            "(b) Date acquired",
            "(c) Date sold",
            "(d) Proceeds",
            "(e) Cost basis",
            "(f) Code",
            "(g) Adjustment",
            "(h) Gain or loss",
        ])

        for row in rows["short_term"]:
            writer.writerow([
                row.description,
                row.date_acquired.strftime("%m/%d/%Y"),
                row.date_sold.strftime("%m/%d/%Y"),
                f"{row.proceeds:.2f}",
                f"{row.cost_basis:.2f}",
                row.adjustment_code or "",
                f"{row.adjustment_amount:.2f}" if row.adjustment_amount else "",
                f"{row.gain_or_loss:.2f}",
            ])

        writer.writerow([])

        # Part II header
        writer.writerow(["PART II - LONG-TERM CAPITAL GAINS AND LOSSES"])
        writer.writerow([
            "(a) Description",
            "(b) Date acquired",
            "(c) Date sold",
            "(d) Proceeds",
            "(e) Cost basis",
            "(f) Code",
            "(g) Adjustment",
            "(h) Gain or loss",
        ])

        for row in rows["long_term"]:
            writer.writerow([
                row.description,
                row.date_acquired.strftime("%m/%d/%Y"),
                row.date_sold.strftime("%m/%d/%Y"),
                f"{row.proceeds:.2f}",
                f"{row.cost_basis:.2f}",
                row.adjustment_code or "",
                f"{row.adjustment_amount:.2f}" if row.adjustment_amount else "",
                f"{row.gain_or_loss:.2f}",
            ])

        return output.getvalue()
```

---

## Testing Strategy

### Wash Sale Test Scenarios

Based on IRS Publication 550 examples:

```python
# tests/libs/tax/test_wash_sale.py

import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from libs.tax.wash_sale_detector import WashSaleDetector


class TestWashSaleDetection:
    """Test wash sale detection per IRS rules."""

    async def test_no_wash_sale_without_replacement(self, detector):
        """No wash sale if no replacement purchase."""
        matches = await detector.detect_wash_sales(
            account_id=uuid4(),
            symbol="AAPL",
            sale_date=datetime(2024, 6, 15),
            loss_amount=Decimal("-1000"),
            shares_sold=Decimal(100),
        )

        assert len(matches) == 0

    async def test_wash_sale_same_day_repurchase(self, detector, setup_lots):
        """Wash sale triggered by same-day repurchase."""
        # Sold at loss, bought same day
        matches = await detector.detect_wash_sales(
            account_id=setup_lots["account_id"],
            symbol="AAPL",
            sale_date=setup_lots["repurchase_date"],
            loss_amount=Decimal("-1000"),
            shares_sold=Decimal(100),
        )

        assert len(matches) == 1
        assert matches[0].disallowed_loss == Decimal("1000")

    async def test_wash_sale_30_days_before(self, detector, setup_lots):
        """Wash sale triggered by purchase 30 days before sale."""
        # Bought 30 days ago, sold today at loss
        pass

    async def test_wash_sale_30_days_after(self, detector, setup_lots):
        """Wash sale triggered by purchase 30 days after sale."""
        # Sold at loss, bought 30 days later
        pass

    async def test_no_wash_sale_31_days(self, detector, setup_lots):
        """No wash sale if replacement is 31+ days away."""
        pass

    async def test_partial_wash_sale(self, detector, setup_lots):
        """Partial wash sale when replacement < shares sold."""
        # Sold 100 shares at loss, bought only 50 back
        # Only 50 shares subject to wash sale
        pass
```

---

## Deliverables

1. **WashSaleDetector:** Detection and adjustment logic
2. **TaxLossHarvester:** Harvesting recommendations
3. **Form8949Exporter:** IRS form export
4. **UI Integration:** Add wash sale display to C5 UI
5. **Tests:** Comprehensive wash sale tests
6. **Documentation:** `docs/ADRs/ADR-0031-tax-lot-tracking.md`

---

## Verification Checklist

- [ ] Wash sale detection works for 30-day window
- [ ] Basis adjustment applied correctly
- [ ] Holding period extended properly
- [ ] Tax-loss harvesting identifies valid opportunities
- [ ] Wash sale risk warnings accurate
- [ ] Form 8949 format correct
- [ ] Multi-year tracking works
- [ ] All IRS example scenarios pass
- [ ] All tests pass
