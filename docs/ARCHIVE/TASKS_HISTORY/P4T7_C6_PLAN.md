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

# Import Protocol from C5 to avoid layering violation (libs importing from apps)
from libs.platform.tax.cost_basis import AsyncConnectionPool

if TYPE_CHECKING:
    from libs.platform.tax.cost_basis import TaxLot

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

    def __init__(self, db_adapter: AsyncConnectionPool):
        """Initialize with database adapter.

        Args:
            db_adapter: Any AsyncConnectionPool implementation (Protocol).
        """
        self._db = db_adapter

    async def detect_wash_sales(
        self,
        user_id: str,
        symbol: str,
        sale_date: datetime,
        loss_amount: Decimal,
        shares_sold: Decimal,
    ) -> list[WashSaleMatch]:
        """Detect wash sales for a loss transaction.

        Args:
            user_id: User that sold (VARCHAR to match RBAC)
            symbol: Symbol sold at a loss
            sale_date: Date of sale
            loss_amount: Total loss on sale (negative)
            shares_sold: Number of shares sold

        Returns:
            List of WashSaleMatch for any matching replacements

        Raises:
            ValueError: If shares_sold <= 0 (invalid input)
        """
        if shares_sold <= 0:
            raise ValueError(f"shares_sold must be positive, got {shares_sold}")

        if loss_amount >= 0:
            # No loss, no wash sale possible
            return []

        window_start = sale_date - timedelta(days=WASH_SALE_WINDOW_DAYS)
        window_end = sale_date + timedelta(days=WASH_SALE_WINDOW_DAYS)

        async with self._db.connection() as conn:
            async with conn.cursor() as cur:
                # Find purchases in wash sale window (psycopg cursor pattern)
                # IRS wash sale rule includes same-day repurchases, so no exclusion
                # NOTE: Do NOT filter by remaining_quantity - wash sales are triggered
                # by replacement purchases even if those lots were later fully sold.
                # The purchase itself triggers the wash sale rule, not the current state.
                await cur.execute(
                    """
                    SELECT id, symbol, quantity, remaining_quantity, cost_per_share, acquired_at
                    FROM tax_lots
                    WHERE user_id = %s
                      AND symbol = %s
                      AND acquired_at >= %s
                      AND acquired_at <= %s
                    ORDER BY acquired_at
                    """,
                    (user_id, symbol, window_start, window_end),
                )
                replacements = await cur.fetchall()

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

                    # psycopg with dict_row returns dicts
                    lot_id = row["id"]
                    lot_quantity = row["quantity"]  # Original purchase quantity (not remaining)
                    acquired_at = row["acquired_at"]

                    # Match shares using ORIGINAL purchase quantity for wash sale purposes
                    # Per IRS: The replacement purchase amount matters, not current remaining
                    # e.g., if bought 100 shares (replacement), then sold 50, the full 100
                    # can still trigger wash sale for up to 100 shares of the original loss
                    matching_shares = min(lot_quantity, remaining_shares)
                    matching_loss = matching_shares * loss_per_share

                    matches.append(WashSaleMatch(
                        loss_disposition_id=UUID("00000000-0000-0000-0000-000000000000"),  # Set later
                        replacement_lot_id=lot_id,
                        symbol=symbol,
                        disallowed_loss=matching_loss,
                        matching_shares=matching_shares,
                        sale_date=sale_date,
                        replacement_date=acquired_at,
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

        async with self._db.connection() as conn:
            async with conn.cursor() as cur:
                for match in matches:
                    # Update disposition total disallowed amount (psycopg cursor pattern)
                    await cur.execute(
                        """
                        UPDATE tax_lot_dispositions
                        SET wash_sale_disallowed = wash_sale_disallowed + %s
                        WHERE id = %s
                        """,
                        (match.disallowed_loss, loss_disposition_id),
                    )

                    # Get original lot dates for holding period calculation FIRST
                    # (needed for both junction table insert and basis adjustment)
                    await cur.execute(
                        """
                        SELECT acquired_at FROM tax_lots
                        WHERE id = (
                            SELECT lot_id FROM tax_lot_dispositions WHERE id = %s
                        )
                        """,
                        (loss_disposition_id,),
                    )
                    original_lot = await cur.fetchone()

                    # Calculate holding period adjustment
                    # Per IRS: Add original holding period to replacement
                    # psycopg with dict_row returns dicts
                    if original_lot:
                        days_held = (match.sale_date - original_lot["acquired_at"]).days
                    else:
                        days_held = 0

                    # Insert per-lot adjustment into junction table for audit trail
                    # Includes holding_period_adjustment_days for proper long/short-term classification
                    # UNIQUE constraint handles idempotency; ON CONFLICT updates
                    await cur.execute(
                        """
                        INSERT INTO tax_wash_sale_adjustments
                        (disposition_id, replacement_lot_id, matching_shares, disallowed_loss,
                         holding_period_adjustment_days)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (disposition_id, replacement_lot_id) DO UPDATE SET
                            matching_shares = EXCLUDED.matching_shares,
                            disallowed_loss = EXCLUDED.disallowed_loss,
                            holding_period_adjustment_days = EXCLUDED.holding_period_adjustment_days
                        """,
                        (loss_disposition_id, match.replacement_lot_id,
                         match.matching_shares, match.disallowed_loss, days_held),
                    )

                    # Update replacement lot with adjusted basis
                    # ALWAYS update total_cost (required for accurate tax reporting,
                    # even if lot is fully disposed - preserves audit trail).
                    # Only update cost_per_share if remaining_quantity > 0
                    # (meaningless when fully disposed; use NULLIF to avoid div-by-zero).
                    await cur.execute(
                        """
                        UPDATE tax_lots
                        SET total_cost = total_cost + %s,
                            cost_per_share = CASE
                                WHEN remaining_quantity > 0 THEN
                                    cost_per_share + (%s / LEAST(%s, remaining_quantity))
                                ELSE cost_per_share  -- Keep existing when fully disposed
                            END
                        WHERE id = %s
                        """,
                        (match.disallowed_loss, match.disallowed_loss,
                         match.matching_shares, match.replacement_lot_id),
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
                            "matching_shares": str(match.matching_shares),
                            "disallowed_loss": str(match.disallowed_loss),
                        },
                    )
            # psycopg3 requires explicit commit (autocommit=False by default)
            await conn.commit()

        return adjustments

    async def get_wash_sale_summary(
        self,
        user_id: str,
        tax_year: int,
    ) -> dict:
        """Get wash sale summary for tax year.

        Uses psycopg cursor pattern with %s placeholders.
        """
        async with self._db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        COUNT(*) as wash_sale_count,
                        SUM(wash_sale_disallowed) as total_disallowed
                    FROM tax_lot_dispositions d
                    JOIN tax_lots l ON d.lot_id = l.id
                    WHERE l.user_id = %s
                      AND date_trunc('year', d.disposed_at) = date_trunc('year', %s::date)
                      AND wash_sale_disallowed > 0
                    """,
                    (user_id, datetime(tax_year, 1, 1)),
                )
                result = await cur.fetchone()

                # psycopg with dict_row returns dicts
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
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

# Import Protocol for type hint (not TYPE_CHECKING since used at runtime)
from libs.platform.tax.cost_basis import AsyncConnectionPool

if TYPE_CHECKING:
    from libs.platform.tax.cost_basis import TaxLot
    from libs.platform.tax.wash_sale_detector import WashSaleDetector

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
        db_adapter: AsyncConnectionPool,
        wash_sale_detector: WashSaleDetector,
    ):
        """Initialize with database adapter.

        Args:
            db_adapter: Any AsyncConnectionPool implementation (Protocol).
            wash_sale_detector: WashSaleDetector for checking wash sale rules.
        """
        self._db = db_adapter
        self._wash_detector = wash_sale_detector

    async def find_opportunities(
        self,
        user_id: str,
        current_prices: dict[str, Decimal],
        min_loss_threshold: Decimal = Decimal("100"),
    ) -> HarvestingRecommendation:
        """Find tax-loss harvesting opportunities.

        Args:
            user_id: User to analyze (VARCHAR to match RBAC)
            current_prices: Current market prices by symbol
            min_loss_threshold: Minimum loss to consider

        Returns:
            HarvestingRecommendation with opportunities
        """
        async with self._db.connection() as conn:
            async with conn.cursor() as cur:
                # Get all open lots (psycopg cursor pattern)
                await cur.execute(
                    """
                    SELECT id, symbol, remaining_quantity, cost_per_share, acquired_at
                    FROM tax_lots
                    WHERE user_id = %s AND remaining_quantity > 0
                    ORDER BY symbol, acquired_at
                    """,
                    (user_id,),
                )
                lots = await cur.fetchall()

                opportunities = []
                warnings = []
                total_loss = Decimal(0)

                for lot in lots:
                    # psycopg with dict_row returns dicts
                    lot_id = lot["id"]
                    symbol = lot["symbol"]
                    remaining_qty = lot["remaining_quantity"]
                    cost_per_share = lot["cost_per_share"]
                    acquired_at = lot["acquired_at"]
                    current_price = current_prices.get(symbol)

                    if current_price is None:
                        warnings.append(f"No price available for {symbol}")
                        continue

                    # Calculate unrealized loss
                    current_value = remaining_qty * current_price
                    cost_basis = remaining_qty * cost_per_share
                    unrealized_pnl = current_value - cost_basis

                    if unrealized_pnl >= 0:
                        # Not a loss, skip
                        continue

                    if abs(unrealized_pnl) < min_loss_threshold:
                        # Below threshold
                        continue

                    # Check wash sale risk
                    wash_sale_risk, clear_date = await self._check_wash_sale_risk(
                        cur, user_id, symbol, lot_id
                    )

                    # Determine holding period
                    # Use UTC-aware datetime to match timestamptz columns
                    days_held = (datetime.now(UTC) - acquired_at).days
                    holding_period = "long_term" if days_held > 365 else "short_term"

                    opportunities.append(HarvestingOpportunity(
                        lot_id=lot_id,
                        symbol=symbol,
                        shares=remaining_qty,
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
        cur,  # psycopg cursor
        user_id: str,
        symbol: str,
        lot_id: UUID,
    ) -> tuple[bool, date | None]:
        """Check if selling this lot would trigger wash sale.

        Uses psycopg cursor pattern with %s placeholders.

        Returns:
            (has_risk, clear_date) - clear_date is when wash window ends
        """
        # Use UTC-aware datetime to match timestamptz columns
        today = datetime.now(UTC)
        lookback = today - timedelta(days=30)

        # Check for recent purchases (excluding this lot)
        await cur.execute(
            """
            SELECT COUNT(*) FROM tax_lots
            WHERE user_id = %s
              AND symbol = %s
              AND id != %s
              AND acquired_at >= %s
            """,
            (user_id, symbol, lot_id, lookback),
        )
        # psycopg with dict_row returns dicts
        result = await cur.fetchone()
        recent = result["count"]

        if recent > 0:
            # Find when window clears
            await cur.execute(
                """
                SELECT MAX(acquired_at) as max_acquired FROM tax_lots
                WHERE user_id = %s
                  AND symbol = %s
                  AND id != %s
                  AND acquired_at >= %s
                """,
                (user_id, symbol, lot_id, lookback),
            )
            result = await cur.fetchone()
            latest = result["max_acquired"]
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

from libs.platform.tax.export import TaxReportRow


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

        Note: TaxReportRow.wash_sale_adjustment corresponds to the
        tax_lot_dispositions.wash_sale_disallowed column. The mapping:
        - Database: wash_sale_disallowed (IRS-disallowed loss amount)
        - Export: wash_sale_adjustment (Form 8949 adjustment column 'g')

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

from libs.platform.tax.wash_sale_detector import WashSaleDetector


class TestWashSaleDetection:
    """Test wash sale detection per IRS rules."""

    async def test_no_wash_sale_without_replacement(self, detector):
        """No wash sale if no replacement purchase."""
        matches = await detector.detect_wash_sales(
            user_id="test_user_123",  # VARCHAR to match RBAC
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
            user_id=setup_lots["user_id"],  # VARCHAR to match RBAC
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
