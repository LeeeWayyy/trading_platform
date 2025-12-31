"""Tests for Form 8949 export functionality."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from libs.tax.export import TaxReportRow
from libs.tax.form_8949 import Form8949Exporter, Form8949Row


class TestForm8949Row:
    """Tests for Form8949Row dataclass."""

    def test_create_row(self) -> None:
        """Can create a Form8949Row."""
        row = Form8949Row(
            description="100 sh AAPL",
            date_acquired=date(2023, 1, 15),
            date_sold=date(2024, 6, 15),
            proceeds=Decimal("17500"),
            cost_basis=Decimal("15000"),
            adjustment_code=None,
            adjustment_amount=None,
            gain_or_loss=Decimal("2500"),
        )

        assert row.description == "100 sh AAPL"
        assert row.proceeds == Decimal("17500")
        assert row.gain_or_loss == Decimal("2500")

    def test_row_with_wash_sale(self) -> None:
        """Form8949Row can have wash sale adjustment."""
        row = Form8949Row(
            description="50 sh TSLA",
            date_acquired=date(2024, 1, 1),
            date_sold=date(2024, 3, 15),
            proceeds=Decimal("8000"),
            cost_basis=Decimal("10000"),
            adjustment_code="W",
            adjustment_amount=Decimal("1500"),
            gain_or_loss=Decimal("-500"),  # -2000 + 1500 adjustment
        )

        assert row.adjustment_code == "W"
        assert row.adjustment_amount == Decimal("1500")


class TestTaxReportRow:
    """Tests for TaxReportRow dataclass."""

    def test_is_wash_sale_true(self) -> None:
        """is_wash_sale returns True when adjustment > 0."""
        row = TaxReportRow(
            symbol="AAPL",
            quantity=Decimal("100"),
            acquired_date=date(2024, 1, 1),
            disposed_date=date(2024, 6, 15),
            cost_basis=Decimal("15000"),
            proceeds=Decimal("14000"),
            gain_loss=Decimal("-1000"),
            holding_period="short_term",
            wash_sale_adjustment=Decimal("500"),
        )

        assert row.is_wash_sale is True

    def test_is_wash_sale_false(self) -> None:
        """is_wash_sale returns False when no adjustment."""
        row = TaxReportRow(
            symbol="AAPL",
            quantity=Decimal("100"),
            acquired_date=date(2024, 1, 1),
            disposed_date=date(2024, 6, 15),
            cost_basis=Decimal("15000"),
            proceeds=Decimal("14000"),
            gain_loss=Decimal("-1000"),
            holding_period="short_term",
            wash_sale_adjustment=None,
        )

        assert row.is_wash_sale is False

    def test_adjusted_gain_loss(self) -> None:
        """adjusted_gain_loss adds wash sale adjustment."""
        row = TaxReportRow(
            symbol="AAPL",
            quantity=Decimal("100"),
            acquired_date=date(2024, 1, 1),
            disposed_date=date(2024, 6, 15),
            cost_basis=Decimal("15000"),
            proceeds=Decimal("14000"),
            gain_loss=Decimal("-1000"),
            holding_period="short_term",
            wash_sale_adjustment=Decimal("600"),
        )

        # -1000 + 600 = -400
        assert row.adjusted_gain_loss == Decimal("-400")


class TestForm8949Exporter:
    """Tests for Form8949Exporter."""

    @pytest.fixture()
    def exporter(self) -> Form8949Exporter:
        """Create exporter instance."""
        return Form8949Exporter()

    def test_format_rows_separates_by_holding_period(self, exporter: Form8949Exporter) -> None:
        """format_rows separates short-term and long-term transactions."""
        rows = [
            TaxReportRow(
                symbol="AAPL",
                quantity=Decimal("100"),
                acquired_date=date(2024, 1, 1),
                disposed_date=date(2024, 6, 15),
                cost_basis=Decimal("15000"),
                proceeds=Decimal("17500"),
                gain_loss=Decimal("2500"),
                holding_period="short_term",
            ),
            TaxReportRow(
                symbol="GOOG",
                quantity=Decimal("50"),
                acquired_date=date(2022, 1, 1),
                disposed_date=date(2024, 6, 15),
                cost_basis=Decimal("50000"),
                proceeds=Decimal("60000"),
                gain_loss=Decimal("10000"),
                holding_period="long_term",
            ),
        ]

        result = exporter.format_rows(rows)

        assert len(result["short_term"]) == 1
        assert len(result["long_term"]) == 1
        assert result["short_term"][0].description == "100 sh AAPL"
        assert result["long_term"][0].description == "50 sh GOOG"

    def test_format_rows_handles_wash_sale(self, exporter: Form8949Exporter) -> None:
        """format_rows adds wash sale code and adjustment."""
        rows = [
            TaxReportRow(
                symbol="TSLA",
                quantity=Decimal("25"),
                acquired_date=date(2024, 2, 1),
                disposed_date=date(2024, 5, 15),
                cost_basis=Decimal("10000"),
                proceeds=Decimal("8000"),
                gain_loss=Decimal("-2000"),
                holding_period="short_term",
                wash_sale_adjustment=Decimal("1500"),
            ),
        ]

        result = exporter.format_rows(rows)

        assert len(result["short_term"]) == 1
        form_row = result["short_term"][0]
        assert form_row.adjustment_code == "W"
        assert form_row.adjustment_amount == Decimal("1500")
        # Gain/loss adjusted: -2000 + 1500 = -500
        assert form_row.gain_or_loss == Decimal("-500")

    def test_to_csv_basic(self, exporter: Form8949Exporter) -> None:
        """to_csv generates valid CSV output."""
        rows = {
            "short_term": [
                Form8949Row(
                    description="100 sh AAPL",
                    date_acquired=date(2024, 1, 15),
                    date_sold=date(2024, 6, 15),
                    proceeds=Decimal("17500"),
                    cost_basis=Decimal("15000"),
                    adjustment_code=None,
                    adjustment_amount=None,
                    gain_or_loss=Decimal("2500"),
                ),
            ],
            "long_term": [],
        }

        csv_output = exporter.to_csv(rows)

        assert "PART I - SHORT-TERM" in csv_output
        assert "PART II - LONG-TERM" in csv_output
        assert "100 sh AAPL" in csv_output
        assert "01/15/2024" in csv_output
        assert "06/15/2024" in csv_output
        assert "17500.00" in csv_output
        assert "15000.00" in csv_output
        assert "2500.00" in csv_output

    def test_to_csv_with_wash_sale(self, exporter: Form8949Exporter) -> None:
        """to_csv includes wash sale code and adjustment."""
        rows = {
            "short_term": [
                Form8949Row(
                    description="50 sh TSLA",
                    date_acquired=date(2024, 2, 1),
                    date_sold=date(2024, 5, 15),
                    proceeds=Decimal("8000"),
                    cost_basis=Decimal("10000"),
                    adjustment_code="W",
                    adjustment_amount=Decimal("1500"),
                    gain_or_loss=Decimal("-500"),
                ),
            ],
            "long_term": [],
        }

        csv_output = exporter.to_csv(rows)

        assert "W" in csv_output
        assert "1500.00" in csv_output
        assert "-500.00" in csv_output

    def test_to_csv_includes_totals(self, exporter: Form8949Exporter) -> None:
        """to_csv includes totals row."""
        rows = {
            "short_term": [
                Form8949Row(
                    description="100 sh AAPL",
                    date_acquired=date(2024, 1, 15),
                    date_sold=date(2024, 6, 15),
                    proceeds=Decimal("17500"),
                    cost_basis=Decimal("15000"),
                    adjustment_code=None,
                    adjustment_amount=None,
                    gain_or_loss=Decimal("2500"),
                ),
                Form8949Row(
                    description="50 sh GOOG",
                    date_acquired=date(2024, 2, 1),
                    date_sold=date(2024, 6, 20),
                    proceeds=Decimal("10000"),
                    cost_basis=Decimal("9000"),
                    adjustment_code=None,
                    adjustment_amount=None,
                    gain_or_loss=Decimal("1000"),
                ),
            ],
            "long_term": [],
        }

        csv_output = exporter.to_csv(rows)

        assert "TOTALS" in csv_output
        # Total proceeds: 17500 + 10000 = 27500
        assert "27500.00" in csv_output

    def test_to_json(self, exporter: Form8949Exporter) -> None:
        """to_json returns JSON-serializable dict."""
        rows = {
            "short_term": [
                Form8949Row(
                    description="100 sh AAPL",
                    date_acquired=date(2024, 1, 15),
                    date_sold=date(2024, 6, 15),
                    proceeds=Decimal("17500"),
                    cost_basis=Decimal("15000"),
                    adjustment_code=None,
                    adjustment_amount=None,
                    gain_or_loss=Decimal("2500"),
                ),
            ],
            "long_term": [],
        }

        json_output = exporter.to_json(rows)

        assert len(json_output["short_term"]) == 1
        assert len(json_output["long_term"]) == 0
        assert json_output["short_term"][0]["description"] == "100 sh AAPL"
        assert json_output["short_term"][0]["date_acquired"] == "2024-01-15"
        assert json_output["short_term"][0]["proceeds"] == "17500"

    def test_format_description_whole_shares(self, exporter: Form8949Exporter) -> None:
        """Description format for whole shares."""
        desc = exporter._format_description("AAPL", Decimal("100"))
        assert desc == "100 sh AAPL"

    def test_format_description_fractional_shares(self, exporter: Form8949Exporter) -> None:
        """Description format for fractional shares."""
        desc = exporter._format_description("AAPL", Decimal("100.5"))
        assert desc == "100.5 sh AAPL"


class TestForm8949ExporterAdjustmentCodes:
    """Test Form 8949 adjustment code constants."""

    def test_wash_sale_code(self) -> None:
        """Wash sale code is W."""
        assert Form8949Exporter.CODE_WASH_SALE == "W"

    def test_short_sale_code(self) -> None:
        """Short sale code is S."""
        assert Form8949Exporter.CODE_SHORT_SALE == "S"
