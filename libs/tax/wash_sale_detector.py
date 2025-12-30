"""Wash sale rule detection per IRS Publication 550.

A wash sale occurs when you sell a security at a loss and buy substantially
identical securities within 30 days before or after the sale. The loss is
disallowed and added to the cost basis of the replacement shares.

References:
- IRS Publication 550: https://www.irs.gov/publications/p550
- 61-day window: 30 days before + sale day + 30 days after
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from psycopg.rows import dict_row

if TYPE_CHECKING:
    from libs.tax.protocols import AsyncConnectionPool

logger = logging.getLogger(__name__)

# IRS wash sale window: 30 days before to 30 days after
WASH_SALE_WINDOW_DAYS = 30


@dataclass(frozen=True)
class WashSaleMatch:
    """A wash sale match between a loss sale and replacement purchase.

    Attributes:
        loss_disposition_id: UUID of the disposition that triggered wash sale.
        replacement_lot_id: UUID of the replacement lot that causes wash sale.
        symbol: Ticker symbol.
        disallowed_loss: Amount of loss disallowed by wash sale rule.
        matching_shares: Number of shares matched for wash sale.
        sale_date: Date of the loss sale.
        replacement_date: Date of the replacement purchase.
    """

    loss_disposition_id: UUID
    replacement_lot_id: UUID
    symbol: str
    disallowed_loss: Decimal
    matching_shares: Decimal
    sale_date: datetime
    replacement_date: datetime


@dataclass(frozen=True)
class WashSaleAdjustment:
    """Adjustment to apply for wash sale.

    Per IRS rules:
    1. Disallowed loss is added to the cost basis of replacement shares
    2. Holding period of original lot transfers to replacement lot

    Attributes:
        lot_id: UUID of the replacement lot being adjusted.
        disallowed_loss: Loss amount disallowed.
        basis_adjustment: Amount to add to cost basis (equals disallowed_loss).
        holding_period_adjustment_days: Days to add to holding period.
    """

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

    Example:
        >>> detector = WashSaleDetector(db_pool)
        >>> matches = await detector.detect_wash_sales(
        ...     user_id="user-123",
        ...     symbol="AAPL",
        ...     sale_date=datetime(2024, 6, 15),
        ...     loss_amount=Decimal("-1000"),
        ...     shares_sold=Decimal(100),
        ... )
        >>> if matches:
        ...     adjustments = await detector.apply_wash_sale_adjustments(
        ...         matches, disposition_id
        ...     )
    """

    def __init__(self, db_pool: AsyncConnectionPool) -> None:
        """Initialize with database pool.

        Args:
            db_pool: Any AsyncConnectionPool implementation (Protocol).
        """
        self._db = db_pool

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
            user_id: User that sold (VARCHAR to match RBAC).
            symbol: Symbol sold at a loss.
            sale_date: Date of sale.
            loss_amount: Total loss on sale (negative value expected).
            shares_sold: Number of shares sold.

        Returns:
            List of WashSaleMatch for any matching replacement purchases.

        Raises:
            ValueError: If shares_sold <= 0 (invalid input).
        """
        if shares_sold <= 0:
            raise ValueError(f"shares_sold must be positive, got {shares_sold}")

        if loss_amount >= 0:
            # No loss, no wash sale possible
            return []

        window_start = sale_date - timedelta(days=WASH_SALE_WINDOW_DAYS)
        window_end = sale_date + timedelta(days=WASH_SALE_WINDOW_DAYS)

        async with self._db.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # Find purchases in wash sale window with available capacity.
                # IRS wash sale rule includes same-day repurchases, so no exclusion.
                #
                # LIMITATION: We only consider replacement lots that still have shares
                # (remaining_quantity > 0). Technically, IRS wash sale rules apply even
                # to replacement shares that were already sold, but adjusting basis
                # retroactively on closed lots requires complex propagation to their
                # dispositions. This is a conservative simplification - we may miss some
                # wash sales for fully-sold replacement lots, but we won't incorrectly
                # report wash sales without proper basis adjustments.
                #
                # CRITICAL: We must track shares already used in prior wash sales to prevent
                # double-counting. A replacement lot can only "wash" up to its original quantity
                # across ALL loss dispositions combined.
                #
                # TEMPORAL CORRECTNESS: Only count shares used by dispositions that occurred
                # on or before the current sale_date. This ensures backfill/reprocessing
                # scenarios allocate capacity correctly based on disposition order.
                # Use conditional SUM to count only shares from prior/same-day dispositions.
                # This keeps ALL replacement lots visible (even those with only future
                # disposition adjustments) while correctly computing available capacity.
                await cur.execute(
                    """
                    SELECT
                        l.id,
                        l.symbol,
                        l.quantity,
                        l.remaining_quantity,
                        l.cost_per_share,
                        l.acquired_at,
                        COALESCE(SUM(
                            CASE
                                WHEN d.disposed_at IS NULL OR d.disposed_at <= %s
                                THEN w.matching_shares
                                ELSE 0
                            END
                        ), 0) AS shares_already_used
                    FROM tax_lots l
                    LEFT JOIN tax_wash_sale_adjustments w ON w.replacement_lot_id = l.id
                    LEFT JOIN tax_lot_dispositions d ON w.disposition_id = d.id
                    WHERE l.user_id = %s
                      AND l.symbol = %s
                      AND l.acquired_at >= %s
                      AND l.acquired_at <= %s
                      AND l.remaining_quantity > 0
                    GROUP BY l.id, l.symbol, l.quantity, l.remaining_quantity, l.cost_per_share, l.acquired_at
                    ORDER BY l.acquired_at
                    """,
                    (sale_date, user_id, symbol, window_start, window_end),
                )
                replacements = await cur.fetchall()

        if not replacements:
            return []

        # Calculate wash sale matches
        matches: list[WashSaleMatch] = []
        remaining_loss = abs(loss_amount)
        remaining_shares = shares_sold
        loss_per_share = remaining_loss / shares_sold

        for row in replacements:
            if remaining_shares <= 0:
                break

            lot_id = row["id"]
            lot_quantity = Decimal(str(row["quantity"]))  # Original purchase quantity
            lot_remaining = Decimal(str(row["remaining_quantity"]))  # Current remaining
            shares_already_used = Decimal(str(row.get("shares_already_used", 0) or 0))
            acquired_at = row["acquired_at"]

            # Calculate available capacity: minimum of:
            # 1. Original quantity minus shares already used in prior wash sales
            # 2. Current remaining quantity (can't allocate to already-sold shares)
            # This prevents both double-counting AND over-allocation to sold shares.
            available_shares = min(lot_remaining, lot_quantity - shares_already_used)
            if available_shares <= 0:
                continue  # This lot is fully utilized or has no remaining shares

            # Match shares using available capacity (not full original quantity)
            matching_shares = min(available_shares, remaining_shares)
            matching_loss = matching_shares * loss_per_share

            matches.append(
                WashSaleMatch(
                    # Set to placeholder UUID - will be updated when applying adjustments
                    loss_disposition_id=UUID("00000000-0000-0000-0000-000000000000"),
                    replacement_lot_id=lot_id if isinstance(lot_id, UUID) else UUID(str(lot_id)),
                    symbol=symbol,
                    disallowed_loss=matching_loss,
                    matching_shares=matching_shares,
                    sale_date=sale_date,
                    replacement_date=acquired_at,
                )
            )

            remaining_shares -= matching_shares
            remaining_loss -= matching_loss

        logger.info(
            "wash_sale_detection_complete",
            extra={
                "user_id": user_id,
                "symbol": symbol,
                "sale_date": sale_date.isoformat(),
                "loss_amount": str(loss_amount),
                "matches_found": len(matches),
            },
        )

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
        3. Track holding period adjustment in junction table

        Args:
            matches: Wash sale matches to apply.
            loss_disposition_id: UUID of the disposition that triggered wash sale.

        Returns:
            List of adjustments applied.
        """
        if not matches:
            return []

        adjustments: list[WashSaleAdjustment] = []

        async with self._db.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # Get original lot dates for holding period calculation (once)
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

                for match in matches:
                    # Calculate holding period adjustment
                    # Per IRS: Add original holding period to replacement
                    if original_lot and original_lot.get("acquired_at"):
                        days_held = (match.sale_date - original_lot["acquired_at"]).days
                    else:
                        days_held = 0

                    # IDEMPOTENT: Check for existing adjustment first to compute delta.
                    # This ensures retries don't double-add to totals.
                    await cur.execute(
                        """
                        SELECT disallowed_loss FROM tax_wash_sale_adjustments
                        WHERE disposition_id = %s AND replacement_lot_id = %s
                        """,
                        (loss_disposition_id, match.replacement_lot_id),
                    )
                    existing = await cur.fetchone()
                    old_disallowed = Decimal(str(existing["disallowed_loss"])) if existing else Decimal(0)
                    delta_disallowed = match.disallowed_loss - old_disallowed

                    # Insert/update per-lot adjustment into junction table for audit trail.
                    # UNIQUE constraint handles idempotency; ON CONFLICT updates.
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
                        (
                            loss_disposition_id,
                            match.replacement_lot_id,
                            match.matching_shares,
                            match.disallowed_loss,
                            days_held,
                        ),
                    )

                    # IDEMPOTENT: Only apply delta to disposition total (not full amount)
                    if delta_disallowed != 0:
                        await cur.execute(
                            """
                            UPDATE tax_lot_dispositions
                            SET wash_sale_disallowed = wash_sale_disallowed + %s
                            WHERE id = %s
                            """,
                            (delta_disallowed, loss_disposition_id),
                        )

                    # IDEMPOTENT: Update replacement lot basis by delta only.
                    # Recompute cost_per_share from total_cost/remaining_quantity to
                    # maintain consistency (matching_shares may differ from remaining_quantity).
                    #
                    # ASSUMPTION: total_cost represents cost of REMAINING shares and is
                    # reduced proportionally on each disposition. If total_cost is the
                    # original lot cost (constant), cost_per_share will inflate as shares
                    # are sold. The tax_lot_service must maintain this invariant.
                    if delta_disallowed != 0:
                        await cur.execute(
                            """
                            UPDATE tax_lots
                            SET total_cost = total_cost + %s,
                                cost_per_share = CASE
                                    WHEN remaining_quantity > 0 THEN
                                        (total_cost + %s) / remaining_quantity
                                    ELSE cost_per_share
                                END
                            WHERE id = %s
                            """,
                            (
                                delta_disallowed,
                                delta_disallowed,
                                match.replacement_lot_id,
                            ),
                        )

                    adjustments.append(
                        WashSaleAdjustment(
                            lot_id=match.replacement_lot_id,
                            disallowed_loss=match.disallowed_loss,
                            basis_adjustment=match.disallowed_loss,
                            holding_period_adjustment_days=days_held,
                        )
                    )

                    logger.info(
                        "wash_sale_adjustment_applied",
                        extra={
                            "disposition_id": str(loss_disposition_id),
                            "replacement_lot_id": str(match.replacement_lot_id),
                            "matching_shares": str(match.matching_shares),
                            "disallowed_loss": str(match.disallowed_loss),
                            "holding_period_adjustment_days": days_held,
                        },
                    )

            # psycopg3 requires explicit commit (autocommit=False by default)
            await conn.commit()

        return adjustments

    async def get_wash_sale_summary(
        self,
        user_id: str,
        tax_year: int,
    ) -> dict[str, Decimal | int]:
        """Get wash sale summary for tax year.

        Args:
            user_id: User to query.
            tax_year: Tax year (e.g., 2024).

        Returns:
            Dict with wash_sale_count and total_disallowed.
        """
        year_start = datetime(tax_year, 1, 1)

        async with self._db.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT
                        COUNT(*) as wash_sale_count,
                        COALESCE(SUM(wash_sale_disallowed), 0) as total_disallowed
                    FROM tax_lot_dispositions d
                    JOIN tax_lots l ON d.lot_id = l.id
                    WHERE l.user_id = %s
                      AND date_trunc('year', d.disposed_at) = date_trunc('year', %s::date)
                      AND wash_sale_disallowed > 0
                    """,
                    (user_id, year_start),
                )
                result = await cur.fetchone()

        if not result:
            return {"wash_sale_count": 0, "total_disallowed": Decimal(0)}

        return {
            "wash_sale_count": result.get("wash_sale_count", 0) or 0,
            "total_disallowed": Decimal(str(result.get("total_disallowed", 0) or 0)),
        }


__all__ = [
    "WASH_SALE_WINDOW_DAYS",
    "WashSaleAdjustment",
    "WashSaleDetector",
    "WashSaleMatch",
]
