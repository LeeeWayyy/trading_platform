"""
Unit tests for position sizing logic.

Tests the calculate_position_size utility function that converts
target weights to executable order quantities.
"""

from decimal import Decimal

from apps.orchestrator.orchestrator import calculate_position_size


class TestPositionSizing:
    """Test position sizing calculations."""

    def test_basic_long_position(self):
        """Test basic long position calculation."""
        # 33.3% of $100k at $150/share = 222 shares
        qty, dollar_amount = calculate_position_size(
            target_weight=0.333,
            capital=Decimal("100000"),
            price=Decimal("150.00"),
            max_position_size=Decimal("50000")
        )

        assert qty == 222
        assert dollar_amount == Decimal("33300.00")

    def test_basic_short_position(self):
        """Test basic short position calculation (negative weight)."""
        # -33.3% of $100k at $150/share = 222 shares (qty positive, side determined elsewhere)
        qty, dollar_amount = calculate_position_size(
            target_weight=-0.333,
            capital=Decimal("100000"),
            price=Decimal("150.00"),
            max_position_size=Decimal("50000")
        )

        assert qty == 222
        assert dollar_amount == Decimal("33300.00")

    def test_max_position_size_cap(self):
        """Test that position size is capped at max_position_size."""
        # 50% of $100k = $50k, but max is $20k
        # $20k / $100/share = 200 shares
        qty, dollar_amount = calculate_position_size(
            target_weight=0.50,
            capital=Decimal("100000"),
            price=Decimal("100.00"),
            max_position_size=Decimal("20000")
        )

        assert qty == 200
        assert dollar_amount == Decimal("20000.00")

    def test_fractional_shares_rounded_down(self):
        """Test that fractional shares are rounded down."""
        # $10k / $151/share = 66.225... shares → 66 shares
        qty, dollar_amount = calculate_position_size(
            target_weight=0.10,
            capital=Decimal("100000"),
            price=Decimal("151.00"),
            max_position_size=Decimal("50000")
        )

        assert qty == 66
        assert dollar_amount == Decimal("10000.00")

    def test_small_position_less_than_one_share(self):
        """Test position smaller than 1 share results in qty=0."""
        # $50 / $100/share = 0.5 shares → 0 shares
        qty, dollar_amount = calculate_position_size(
            target_weight=0.0005,
            capital=Decimal("100000"),
            price=Decimal("100.00"),
            max_position_size=Decimal("50000")
        )

        assert qty == 0
        assert dollar_amount == Decimal("50.00")

    def test_zero_weight(self):
        """Test zero weight results in zero shares."""
        qty, dollar_amount = calculate_position_size(
            target_weight=0.0,
            capital=Decimal("100000"),
            price=Decimal("150.00"),
            max_position_size=Decimal("50000")
        )

        assert qty == 0
        assert dollar_amount == Decimal("0.00")

    def test_full_capital_allocation(self):
        """Test 100% capital allocation."""
        # 100% of $100k at $100/share = 1000 shares
        # But capped at max_position_size $50k = 500 shares
        qty, dollar_amount = calculate_position_size(
            target_weight=1.0,
            capital=Decimal("100000"),
            price=Decimal("100.00"),
            max_position_size=Decimal("50000")
        )

        assert qty == 500
        assert dollar_amount == Decimal("50000.00")

    def test_high_price_stock(self):
        """Test position sizing for expensive stock (e.g., BRK.A)."""
        # 10% of $100k = $10k at $500k/share = 0.02 shares → 0 shares
        qty, dollar_amount = calculate_position_size(
            target_weight=0.10,
            capital=Decimal("100000"),
            price=Decimal("500000.00"),
            max_position_size=Decimal("50000")
        )

        assert qty == 0
        assert dollar_amount == Decimal("10000.00")

    def test_penny_stock(self):
        """Test position sizing for penny stock."""
        # 10% of $100k = $10k at $0.50/share = 20,000 shares
        qty, dollar_amount = calculate_position_size(
            target_weight=0.10,
            capital=Decimal("100000"),
            price=Decimal("0.50"),
            max_position_size=Decimal("50000")
        )

        assert qty == 20000
        assert dollar_amount == Decimal("10000.00")

    def test_decimal_precision(self):
        """Test that Decimal precision is maintained."""
        qty, dollar_amount = calculate_position_size(
            target_weight=0.123456,
            capital=Decimal("100000.00"),
            price=Decimal("123.45"),
            max_position_size=Decimal("50000.00")
        )

        # 12.3456% of $100k = $12,345.60
        # $12,345.60 / $123.45 = 100.004... shares → 100 shares
        assert qty == 100
        assert isinstance(dollar_amount, Decimal)
        assert dollar_amount == Decimal("12345.60")
