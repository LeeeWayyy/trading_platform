"""Tests for libs/platform/tax/export.py."""

from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal

import pytest

from libs.platform.tax.export import TaxReportRow


class TestTaxReportRow:
    """Tests for TaxReportRow data and computed properties."""

    @pytest.mark.unit()
    def test_is_wash_sale_false_when_adjustment_none(self) -> None:
        row = TaxReportRow(
            symbol="AAPL",
            quantity=Decimal("10"),
            acquired_date=date(2025, 1, 1),
            disposed_date=date(2025, 6, 1),
            cost_basis=Decimal("1000"),
            proceeds=Decimal("1200"),
            gain_loss=Decimal("200"),
            holding_period="short_term",
            wash_sale_adjustment=None,
        )
        assert row.is_wash_sale is False
        assert row.adjusted_gain_loss == Decimal("200")

    @pytest.mark.unit()
    def test_is_wash_sale_false_when_adjustment_zero(self) -> None:
        row = TaxReportRow(
            symbol="AAPL",
            quantity=Decimal("10"),
            acquired_date=date(2025, 1, 1),
            disposed_date=date(2025, 6, 1),
            cost_basis=Decimal("1000"),
            proceeds=Decimal("1200"),
            gain_loss=Decimal("200"),
            holding_period="short_term",
            wash_sale_adjustment=Decimal("0"),
        )
        assert row.is_wash_sale is False
        assert row.adjusted_gain_loss == Decimal("200")

    @pytest.mark.unit()
    def test_is_wash_sale_true_when_adjustment_positive(self) -> None:
        row = TaxReportRow(
            symbol="AAPL",
            quantity=Decimal("10"),
            acquired_date=date(2025, 1, 1),
            disposed_date=date(2025, 6, 1),
            cost_basis=Decimal("1000"),
            proceeds=Decimal("800"),
            gain_loss=Decimal("-200"),
            holding_period="short_term",
            wash_sale_adjustment=Decimal("50"),
        )
        assert row.is_wash_sale is True
        assert row.adjusted_gain_loss == Decimal("-150")

    @pytest.mark.unit()
    def test_row_is_frozen(self) -> None:
        row = TaxReportRow(
            symbol="MSFT",
            quantity=Decimal("1"),
            acquired_date=date(2025, 2, 1),
            disposed_date=date(2025, 12, 1),
            cost_basis=Decimal("100"),
            proceeds=Decimal("150"),
            gain_loss=Decimal("50"),
            holding_period="short_term",
        )
        with pytest.raises(FrozenInstanceError):
            row.symbol = "GOOG"
