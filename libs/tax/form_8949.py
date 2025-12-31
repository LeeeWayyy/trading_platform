"""IRS Form 8949 export functionality.

Form 8949 is used to report sales and exchanges of capital assets to the IRS.
This module generates Form 8949 compatible output in CSV format.

Form Structure:
- Part I: Short-term transactions (assets held <= 1 year)
- Part II: Long-term transactions (assets held > 1 year)

Each part has boxes:
- Box A: Reported to IRS with basis shown (Form 1099-B)
- Box B: Reported to IRS, basis not shown
- Box C: Not reported to IRS

Columns:
- (a) Description of property
- (b) Date acquired
- (c) Date sold or disposed of
- (d) Proceeds (sales price)
- (e) Cost or other basis
- (f) Adjustment code (W = wash sale, etc.)
- (g) Amount of adjustment
- (h) Gain or loss

References:
- IRS Form 8949 Instructions: https://www.irs.gov/instructions/i8949
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from libs.tax.export import TaxReportRow


@dataclass(frozen=True)
class Form8949Row:
    """A row for IRS Form 8949.

    Attributes:
        description: Description of property - Column (a).
        date_acquired: Date the property was acquired - Column (b).
        date_sold: Date the property was sold - Column (c).
        proceeds: Sales proceeds - Column (d).
        cost_basis: Cost or other basis - Column (e).
        adjustment_code: Adjustment code (e.g., "W" for wash sale) - Column (f).
        adjustment_amount: Amount of adjustment - Column (g).
        gain_or_loss: Gain or loss - Column (h).
    """

    description: str
    date_acquired: date
    date_sold: date
    proceeds: Decimal
    cost_basis: Decimal
    adjustment_code: str | None
    adjustment_amount: Decimal | None
    gain_or_loss: Decimal


class Form8949Exporter:
    """Exports transactions in IRS Form 8949 format.

    Form 8949 is used to report sales and exchanges of capital assets.
    This exporter generates CSV output matching the form's column structure.

    Example:
        >>> exporter = Form8949Exporter()
        >>> rows = [
        ...     TaxReportRow(
        ...         symbol="AAPL",
        ...         quantity=Decimal("100"),
        ...         acquired_date=date(2023, 1, 15),
        ...         disposed_date=date(2024, 6, 15),
        ...         cost_basis=Decimal("15000"),
        ...         proceeds=Decimal("17500"),
        ...         gain_loss=Decimal("2500"),
        ...         holding_period="long_term",
        ...     )
        ... ]
        >>> formatted = exporter.format_rows(rows)
        >>> csv_output = exporter.to_csv(formatted)
    """

    # Standard adjustment codes
    CODE_WASH_SALE = "W"  # Wash sale loss disallowed
    CODE_SHORT_SALE = "S"  # Short sale
    CODE_COLLECTIBLES = "C"  # Collectibles (28% rate)
    CODE_QOF = "Q"  # Qualified Opportunity Fund
    CODE_MULTIPLE = "M"  # Multiple codes apply

    def format_rows(
        self,
        rows: list[TaxReportRow],
    ) -> dict[str, list[Form8949Row]]:
        """Format tax report rows for Form 8949.

        Splits transactions into short-term and long-term categories
        and formats them according to IRS Form 8949 requirements.

        Note: TaxReportRow.wash_sale_adjustment corresponds to the
        tax_lot_dispositions.wash_sale_disallowed column. The mapping:
        - Database: wash_sale_disallowed (IRS-disallowed loss amount)
        - Export: wash_sale_adjustment (Form 8949 adjustment column 'g')

        For wash sales, the adjustment is added back to the gain/loss,
        effectively reducing the deductible loss.

        Args:
            rows: List of TaxReportRow to format.

        Returns:
            Dict with keys 'short_term' and 'long_term' containing Form8949Row lists.
        """
        result: dict[str, list[Form8949Row]] = {
            "short_term": [],
            "long_term": [],
        }

        for row in rows:
            # Calculate adjusted gain/loss
            # For wash sales: disallowed loss is added back (reducing the loss)
            adjusted_gain_loss = row.gain_loss
            if row.wash_sale_adjustment:
                adjusted_gain_loss = row.gain_loss + row.wash_sale_adjustment

            form_row = Form8949Row(
                description=self._format_description(row.symbol, row.quantity),
                date_acquired=row.acquired_date,
                date_sold=row.disposed_date,
                proceeds=row.proceeds,
                cost_basis=row.cost_basis,
                adjustment_code=self.CODE_WASH_SALE if row.wash_sale_adjustment else None,
                adjustment_amount=row.wash_sale_adjustment if row.wash_sale_adjustment else None,
                gain_or_loss=adjusted_gain_loss,
            )

            if row.holding_period == "short_term":
                result["short_term"].append(form_row)
            else:
                result["long_term"].append(form_row)

        return result

    def to_csv(
        self,
        rows: dict[str, list[Form8949Row]],
        *,
        include_headers: bool = True,
    ) -> str:
        """Export formatted rows to CSV matching Form 8949 columns.

        Generates a CSV with Part I (short-term) and Part II (long-term)
        sections, matching the IRS Form 8949 structure.

        Args:
            rows: Dict with 'short_term' and 'long_term' Form8949Row lists.
            include_headers: Whether to include section headers (default True).

        Returns:
            CSV string with Form 8949 formatted data.
        """
        output = io.StringIO()
        writer = csv.writer(output)

        if include_headers:
            # Part I header
            writer.writerow(["PART I - SHORT-TERM CAPITAL GAINS AND LOSSES"])
            writer.writerow(["(Assets held one year or less)"])
            writer.writerow([])

        writer.writerow(
            [
                "(a) Description",
                "(b) Date acquired",
                "(c) Date sold",
                "(d) Proceeds",
                "(e) Cost basis",
                "(f) Code",
                "(g) Adjustment",
                "(h) Gain or loss",
            ]
        )

        for row in rows.get("short_term", []):
            writer.writerow(self._row_to_csv_values(row))

        # Part I totals
        if rows.get("short_term"):
            short_term_totals = self._calculate_totals(rows["short_term"])
            writer.writerow([])
            writer.writerow(
                [
                    "TOTALS",
                    "",
                    "",
                    f"{short_term_totals['proceeds']:.2f}",
                    f"{short_term_totals['cost_basis']:.2f}",
                    "",
                    (
                        f"{short_term_totals['adjustments']:.2f}"
                        if short_term_totals["adjustments"]
                        else ""
                    ),
                    f"{short_term_totals['gain_loss']:.2f}",
                ]
            )

        writer.writerow([])

        if include_headers:
            # Part II header
            writer.writerow(["PART II - LONG-TERM CAPITAL GAINS AND LOSSES"])
            writer.writerow(["(Assets held more than one year)"])
            writer.writerow([])

        writer.writerow(
            [
                "(a) Description",
                "(b) Date acquired",
                "(c) Date sold",
                "(d) Proceeds",
                "(e) Cost basis",
                "(f) Code",
                "(g) Adjustment",
                "(h) Gain or loss",
            ]
        )

        for row in rows.get("long_term", []):
            writer.writerow(self._row_to_csv_values(row))

        # Part II totals
        if rows.get("long_term"):
            long_term_totals = self._calculate_totals(rows["long_term"])
            writer.writerow([])
            writer.writerow(
                [
                    "TOTALS",
                    "",
                    "",
                    f"{long_term_totals['proceeds']:.2f}",
                    f"{long_term_totals['cost_basis']:.2f}",
                    "",
                    (
                        f"{long_term_totals['adjustments']:.2f}"
                        if long_term_totals["adjustments"]
                        else ""
                    ),
                    f"{long_term_totals['gain_loss']:.2f}",
                ]
            )

        return output.getvalue()

    def to_json(
        self,
        rows: dict[str, list[Form8949Row]],
    ) -> dict[str, list[dict[str, str | None]]]:
        """Export formatted rows to JSON-serializable format.

        Args:
            rows: Dict with 'short_term' and 'long_term' Form8949Row lists.

        Returns:
            Dict with 'short_term' and 'long_term' lists of dicts.
        """
        result: dict[str, list[dict[str, str | None]]] = {
            "short_term": [],
            "long_term": [],
        }

        for period in ["short_term", "long_term"]:
            for row in rows.get(period, []):
                result[period].append(
                    {
                        "description": row.description,
                        "date_acquired": row.date_acquired.isoformat(),
                        "date_sold": row.date_sold.isoformat(),
                        "proceeds": str(row.proceeds),
                        "cost_basis": str(row.cost_basis),
                        "adjustment_code": row.adjustment_code,
                        "adjustment_amount": (
                            str(row.adjustment_amount) if row.adjustment_amount else None
                        ),
                        "gain_or_loss": str(row.gain_or_loss),
                    }
                )

        return result

    def _format_description(self, symbol: str, quantity: Decimal) -> str:
        """Format property description per IRS requirements.

        Example: "100 sh AAPL"
        """
        # Use Decimal's normalize to remove trailing zeros after decimal point
        # but preserve whole number precision
        normalized = quantity.normalize()
        # Handle case where normalize returns scientific notation for large numbers
        qty_str = f"{normalized:f}"
        return f"{qty_str} sh {symbol}"

    def _row_to_csv_values(self, row: Form8949Row) -> list[str]:
        """Convert Form8949Row to CSV row values."""
        return [
            row.description,
            row.date_acquired.strftime("%m/%d/%Y"),
            row.date_sold.strftime("%m/%d/%Y"),
            f"{row.proceeds:.2f}",
            f"{row.cost_basis:.2f}",
            row.adjustment_code or "",
            f"{row.adjustment_amount:.2f}" if row.adjustment_amount else "",
            f"{row.gain_or_loss:.2f}",
        ]

    def _calculate_totals(
        self,
        rows: list[Form8949Row],
    ) -> dict[str, Decimal]:
        """Calculate totals for a list of rows."""
        totals = {
            "proceeds": Decimal(0),
            "cost_basis": Decimal(0),
            "adjustments": Decimal(0),
            "gain_loss": Decimal(0),
        }

        for row in rows:
            totals["proceeds"] += row.proceeds
            totals["cost_basis"] += row.cost_basis
            if row.adjustment_amount:
                totals["adjustments"] += row.adjustment_amount
            totals["gain_loss"] += row.gain_or_loss

        return totals


__all__ = [
    "Form8949Exporter",
    "Form8949Row",
]
