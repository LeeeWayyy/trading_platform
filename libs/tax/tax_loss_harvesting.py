"""Tax-loss harvesting recommendations.

Tax-loss harvesting involves selling securities at a loss to offset capital
gains, while optionally maintaining market exposure via similar (but not
identical) securities to avoid wash sales.

Key considerations:
- Avoid wash sales (30-day rule before and after)
- Consider short-term vs long-term classification
- Factor in transaction costs
- Maintain portfolio allocation

References:
- IRS Publication 550 on wash sales
- Form 8949 for reporting capital gains/losses
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from psycopg.rows import dict_row

if TYPE_CHECKING:
    from libs.tax.protocols import AsyncConnectionPool
    from libs.tax.wash_sale_detector import WashSaleDetector

logger = logging.getLogger(__name__)

# Default minimum loss to consider for harvesting
DEFAULT_MIN_LOSS_THRESHOLD = Decimal("100")

# ILLUSTRATIVE tax rates for estimation purposes only.
# These are example rates and do NOT represent actual tax liability.
# They do NOT account for:
# - State/local taxes
# - Individual tax bracket (actual rates vary by income)
# - Net Investment Income Tax (3.8%)
# - AMT considerations
# Users should consult a tax professional for accurate calculations.
SHORT_TERM_TAX_RATE = Decimal("0.35")  # Example ordinary income rate
LONG_TERM_TAX_RATE = Decimal("0.15")  # Example capital gains rate


@dataclass(frozen=True)
class HarvestingOpportunity:
    """A potential tax-loss harvesting opportunity.

    Attributes:
        lot_id: UUID of the tax lot with unrealized loss.
        symbol: Ticker symbol.
        shares: Number of shares available to sell.
        cost_basis: Total cost basis of the lot.
        current_price: Current market price per share.
        unrealized_loss: Unrealized loss (negative value).
        holding_period: "short_term" (<= 1 year) or "long_term" (> 1 year).
        wash_sale_risk: True if recent purchases exist that would trigger wash sale.
        wash_sale_clear_date: Date when wash sale window ends for past purchases (safe sale date).
        repurchase_restricted_until: Date after which repurchase is safe (today + 31 days).
            ALWAYS set for ALL opportunities - selling creates a 30-day forward restriction
            on repurchases regardless of past purchases. Do not repurchase until this date.
    """

    lot_id: UUID
    symbol: str
    shares: Decimal
    cost_basis: Decimal
    current_price: Decimal
    unrealized_loss: Decimal
    holding_period: str
    wash_sale_risk: bool
    wash_sale_clear_date: date | None
    repurchase_restricted_until: date | None = None


@dataclass(frozen=True)
class HarvestingRecommendation:
    """Recommendation for tax-loss harvesting.

    Attributes:
        opportunities: List of potential harvesting opportunities.
        total_harvestable_loss: Total loss available to harvest (excludes wash sale risks).
        estimated_tax_savings: ILLUSTRATIVE estimate using example rates (35%/15%).
            Does NOT represent actual tax liability. Consult a tax professional.
        warnings: List of warning messages (e.g., missing prices).
    """

    opportunities: list[HarvestingOpportunity]
    total_harvestable_loss: Decimal
    estimated_tax_savings: Decimal  # Illustrative estimate only
    warnings: list[str]


class TaxLossHarvester:
    """Identifies tax-loss harvesting opportunities.

    Tax-loss harvesting involves selling securities at a loss to offset
    capital gains, while maintaining market exposure (optionally via
    similar but not identical securities).

    Example:
        >>> harvester = TaxLossHarvester(db_pool, wash_detector)
        >>> prices = {"AAPL": Decimal("150"), "GOOG": Decimal("2800")}
        >>> recommendation = await harvester.find_opportunities(
        ...     user_id="user-123",
        ...     current_prices=prices,
        ...     min_loss_threshold=Decimal("500"),
        ... )
        >>> for opp in recommendation.opportunities:
        ...     print(f"{opp.symbol}: ${opp.unrealized_loss} loss")
    """

    def __init__(
        self,
        db_pool: AsyncConnectionPool,
        wash_sale_detector: WashSaleDetector | None = None,
    ) -> None:
        """Initialize with database pool and optional wash sale detector.

        Args:
            db_pool: Any AsyncConnectionPool implementation (Protocol).
            wash_sale_detector: Optional WashSaleDetector for checking wash sale rules.
                If None, wash sale risk checks will be performed directly.
        """
        self._db = db_pool
        self._wash_detector = wash_sale_detector

    async def find_opportunities(
        self,
        user_id: str,
        current_prices: dict[str, Decimal],
        min_loss_threshold: Decimal = DEFAULT_MIN_LOSS_THRESHOLD,
    ) -> HarvestingRecommendation:
        """Find tax-loss harvesting opportunities.

        Scans all open lots for unrealized losses and identifies opportunities
        to realize those losses for tax purposes, while flagging wash sale risks.

        Args:
            user_id: User to analyze (VARCHAR to match RBAC).
            current_prices: Current market prices by symbol.
            min_loss_threshold: Minimum loss to consider (default $100).

        Returns:
            HarvestingRecommendation with opportunities and summary.
        """
        async with self._db.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # Get all open lots
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

                opportunities: list[HarvestingOpportunity] = []
                warnings: list[str] = []
                total_loss = Decimal(0)
                # Use single timestamp for consistency (avoid midnight drift)
                now = datetime.now(UTC)

                for lot in lots:
                    lot_id_raw = lot["id"]
                    lot_id = lot_id_raw if isinstance(lot_id_raw, UUID) else UUID(str(lot_id_raw))
                    symbol = lot["symbol"]
                    remaining_qty = Decimal(str(lot["remaining_quantity"]))
                    cost_per_share = Decimal(str(lot["cost_per_share"]))
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
                        cur, user_id, symbol, lot_id, now
                    )

                    # Determine holding period
                    days_held = (now - acquired_at).days
                    holding_period = "long_term" if days_held > 365 else "short_term"

                    # Calculate when repurchase becomes safe (31 days from today).
                    # Note: wash sale window is 30 days, so safe to buy on day 31.
                    # ALWAYS set this for ALL opportunities: selling at a loss creates
                    # a 30-day forward restriction on repurchases, regardless of past risk.
                    # Users need this warning to avoid triggering wash sales after harvest.
                    repurchase_safe_date = (now + timedelta(days=31)).date()

                    opportunities.append(
                        HarvestingOpportunity(
                            lot_id=lot_id,
                            symbol=symbol,
                            shares=remaining_qty,
                            cost_basis=cost_basis,
                            current_price=current_price,
                            unrealized_loss=unrealized_pnl,  # Negative
                            holding_period=holding_period,
                            wash_sale_risk=wash_sale_risk,
                            wash_sale_clear_date=clear_date,
                            repurchase_restricted_until=repurchase_safe_date,
                        )
                    )

                    if not wash_sale_risk:
                        total_loss += abs(unrealized_pnl)

        # Sort opportunities by loss (most negative first)
        sorted_opportunities = sorted(
            opportunities,
            key=lambda x: x.unrealized_loss,
        )

        # Estimate tax savings
        estimated_savings = self._estimate_tax_savings(sorted_opportunities)

        logger.info(
            "tax_loss_harvesting_scan_complete",
            extra={
                "user_id": user_id,
                "opportunities_found": len(opportunities),
                "total_harvestable_loss": str(total_loss),
                "estimated_tax_savings": str(estimated_savings),
            },
        )

        return HarvestingRecommendation(
            opportunities=sorted_opportunities,
            total_harvestable_loss=total_loss,
            estimated_tax_savings=estimated_savings,
            warnings=warnings,
        )

    async def _check_wash_sale_risk(
        self,
        cur: object,  # psycopg cursor
        user_id: str,
        symbol: str,
        lot_id: UUID,
        now: datetime,
    ) -> tuple[bool, date | None]:
        """Check if selling this lot would trigger wash sale.

        Args:
            cur: Database cursor.
            user_id: User ID.
            symbol: Symbol to check.
            lot_id: Lot ID to exclude from check.
            now: Current UTC timestamp for consistency.

        Returns:
            (has_risk, clear_date) - clear_date is when wash window ends.
        """
        lookback = now - timedelta(days=30)

        # Check for recent purchases (excluding this lot).
        # Use upper bound <= now to exclude future-dated lots (clock skew, scheduled).
        await cur.execute(  # type: ignore[attr-defined]
            """
            SELECT COUNT(*) as count FROM tax_lots
            WHERE user_id = %s
              AND symbol = %s
              AND id != %s
              AND acquired_at >= %s
              AND acquired_at <= %s
            """,
            (user_id, symbol, lot_id, lookback, now),
        )
        result = await cur.fetchone()  # type: ignore[attr-defined]
        recent_count = result.get("count", 0) if result else 0

        if recent_count > 0:
            # Find when window clears
            await cur.execute(  # type: ignore[attr-defined]
                """
                SELECT MAX(acquired_at) as max_acquired FROM tax_lots
                WHERE user_id = %s
                  AND symbol = %s
                  AND id != %s
                  AND acquired_at >= %s
                  AND acquired_at <= %s
                """,
                (user_id, symbol, lot_id, lookback, now),
            )
            result = await cur.fetchone()  # type: ignore[attr-defined]
            if result and result.get("max_acquired"):
                latest = result["max_acquired"]
                clear_date = (latest + timedelta(days=31)).date()
                return True, clear_date

        return False, None

    def _estimate_tax_savings(
        self,
        opportunities: list[HarvestingOpportunity],
    ) -> Decimal:
        """Estimate tax savings from harvesting.

        Uses simplified tax rates:
        - Short-term: 35% (ordinary income)
        - Long-term: 15% (capital gains)

        Args:
            opportunities: List of harvesting opportunities.

        Returns:
            Estimated tax savings in dollars.
        """
        savings = Decimal(0)

        for opp in opportunities:
            if opp.wash_sale_risk:
                # Skip opportunities with wash sale risk
                continue

            loss = abs(opp.unrealized_loss)
            if opp.holding_period == "short_term":
                savings += loss * SHORT_TERM_TAX_RATE
            else:
                savings += loss * LONG_TERM_TAX_RATE

        return savings.quantize(Decimal("0.01"))

    async def get_harvest_summary_by_symbol(
        self,
        user_id: str,
        current_prices: dict[str, Decimal],
    ) -> dict[str, dict[str, Decimal]]:
        """Get harvesting summary grouped by symbol.

        Args:
            user_id: User to analyze.
            current_prices: Current market prices by symbol.

        Returns:
            Dict mapping symbol to summary (total_loss, shares, lots_count).
        """
        recommendation = await self.find_opportunities(
            user_id=user_id,
            current_prices=current_prices,
            min_loss_threshold=Decimal("0"),  # Include all
        )

        summary: dict[str, dict[str, Decimal]] = {}
        for opp in recommendation.opportunities:
            if opp.symbol not in summary:
                summary[opp.symbol] = {
                    "total_loss": Decimal(0),
                    "shares": Decimal(0),
                    "lots_count": Decimal(0),
                }
            summary[opp.symbol]["total_loss"] += abs(opp.unrealized_loss)
            summary[opp.symbol]["shares"] += opp.shares
            summary[opp.symbol]["lots_count"] += Decimal(1)

        return summary


__all__ = [
    "DEFAULT_MIN_LOSS_THRESHOLD",
    "HarvestingOpportunity",
    "HarvestingRecommendation",
    "LONG_TERM_TAX_RATE",
    "SHORT_TERM_TAX_RATE",
    "TaxLossHarvester",
]
