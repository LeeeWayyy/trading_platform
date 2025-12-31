"""Tax report row definitions for export functionality.

Provides standardized data structures for tax report exports
including Form 8949, TXF, and CSV formats.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class TaxReportRow:
    """A row representing a taxable disposition for reporting.

    This dataclass captures all information needed for tax form exports.
    Maps to tax_lot_dispositions table with enriched lot data.

    IMPORTANT CONTRACT:
    - cost_basis is the ORIGINAL cost basis at time of disposition (NOT adjusted)
    - gain_loss is the raw proceeds - cost_basis (BEFORE wash sale adjustment)
    - wash_sale_adjustment is the IRS-disallowed loss (positive value)
    - Form 8949 export applies the adjustment via column (f)/(g)
    - Use adjusted_gain_loss property for the final reportable gain/loss

    Attributes:
        symbol: Ticker symbol of the security.
        quantity: Number of shares disposed.
        acquired_date: Date the shares were originally acquired.
        disposed_date: Date of the sale/disposition.
        cost_basis: ORIGINAL cost basis (before wash sale adjustment).
        proceeds: Total proceeds from sale.
        gain_loss: Raw gain or loss (proceeds - cost_basis, before adjustment).
        holding_period: "short_term" (<= 1 year) or "long_term" (> 1 year).
        wash_sale_adjustment: Disallowed loss from wash sale rule (positive value).
        lot_id: UUID of the source tax lot.
        disposition_id: UUID of the disposition record.
    """

    symbol: str
    quantity: Decimal
    acquired_date: date
    disposed_date: date
    cost_basis: Decimal
    proceeds: Decimal
    gain_loss: Decimal
    holding_period: str  # "short_term" or "long_term"
    wash_sale_adjustment: Decimal | None = None
    lot_id: str | None = None
    disposition_id: str | None = None

    @property
    def is_wash_sale(self) -> bool:
        """True if this disposition had a wash sale adjustment."""
        return self.wash_sale_adjustment is not None and self.wash_sale_adjustment > 0

    @property
    def adjusted_gain_loss(self) -> Decimal:
        """Gain/loss after wash sale adjustment.

        For wash sales, the disallowed loss is added back (reducing the loss).
        """
        if self.wash_sale_adjustment:
            return self.gain_loss + self.wash_sale_adjustment
        return self.gain_loss


__all__ = ["TaxReportRow"]
