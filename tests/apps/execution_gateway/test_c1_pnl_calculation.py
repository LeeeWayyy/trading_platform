"""
Tests for C1: P&L Calculation Verification.

This module provides comprehensive test coverage for the P&L calculation
logic in update_position_on_fill to ensure correct realized P&L.

Issue: C1 - P&L calculation verification
Location: apps/execution_gateway/database.py:31 (calculate_position_update function)
Status: Tests directly import and test the production function
"""

from decimal import Decimal

import pytest

from apps.execution_gateway.database import calculate_position_update


class TestPnLCalculation:
    """Test P&L calculation logic.

    The P&L calculation in calculate_position_update handles various scenarios:
    - Opening positions (long/short)
    - Closing positions (realize P&L)
    - Adding to positions (update avg price)
    - Partial closes (realize partial P&L)
    - Flipping positions (e.g., long to short)

    Tests directly import and call the production function from database.py
    to ensure tests validate the actual codebase.
    """

    def _calculate_pnl(
        self,
        old_qty: int,
        old_avg_price: Decimal,
        old_realized_pl: Decimal,
        fill_qty: int,
        fill_price: Decimal,
        side: str,
    ) -> tuple[int, Decimal, Decimal]:
        """Wrapper that calls the actual production function.

        This ensures tests validate the real codebase, not a copy.
        """
        return calculate_position_update(
            old_qty=old_qty,
            old_avg_price=old_avg_price,
            old_realized_pl=old_realized_pl,
            fill_qty=fill_qty,
            fill_price=fill_price,
            side=side,
        )

    # ================== Opening Positions ==================

    def test_open_long_position(self):
        """Verify opening a new long position."""
        new_qty, new_avg, new_pnl = self._calculate_pnl(
            old_qty=0,
            old_avg_price=Decimal("0"),
            old_realized_pl=Decimal("0"),
            fill_qty=100,
            fill_price=Decimal("150.00"),
            side="buy",
        )

        assert new_qty == 100
        assert new_avg == Decimal("150.00")
        assert new_pnl == Decimal("0")  # No P&L on open

    def test_open_short_position(self):
        """Verify opening a new short position."""
        new_qty, new_avg, new_pnl = self._calculate_pnl(
            old_qty=0,
            old_avg_price=Decimal("0"),
            old_realized_pl=Decimal("0"),
            fill_qty=100,
            fill_price=Decimal("150.00"),
            side="sell",
        )

        assert new_qty == -100  # Short position (negative)
        assert new_avg == Decimal("150.00")
        assert new_pnl == Decimal("0")

    # ================== Closing Positions ==================

    def test_close_long_profit(self):
        """Verify closing long position with profit."""
        # Bought 100 @ $100, sell 100 @ $120 = $2000 profit
        new_qty, new_avg, new_pnl = self._calculate_pnl(
            old_qty=100,
            old_avg_price=Decimal("100.00"),
            old_realized_pl=Decimal("0"),
            fill_qty=100,
            fill_price=Decimal("120.00"),
            side="sell",
        )

        assert new_qty == 0
        assert new_pnl == Decimal("2000.00")

    def test_close_long_loss(self):
        """Verify closing long position with loss."""
        # Bought 100 @ $100, sell 100 @ $80 = -$2000 loss
        new_qty, new_avg, new_pnl = self._calculate_pnl(
            old_qty=100,
            old_avg_price=Decimal("100.00"),
            old_realized_pl=Decimal("0"),
            fill_qty=100,
            fill_price=Decimal("80.00"),
            side="sell",
        )

        assert new_qty == 0
        assert new_pnl == Decimal("-2000.00")

    def test_close_short_profit(self):
        """Verify closing short position with profit."""
        # Shorted 100 @ $100, buy 100 @ $80 = $2000 profit
        new_qty, new_avg, new_pnl = self._calculate_pnl(
            old_qty=-100,
            old_avg_price=Decimal("100.00"),
            old_realized_pl=Decimal("0"),
            fill_qty=100,
            fill_price=Decimal("80.00"),
            side="buy",
        )

        assert new_qty == 0
        assert new_pnl == Decimal("2000.00")

    def test_close_short_loss(self):
        """Verify closing short position with loss."""
        # Shorted 100 @ $100, buy 100 @ $120 = -$2000 loss
        new_qty, new_avg, new_pnl = self._calculate_pnl(
            old_qty=-100,
            old_avg_price=Decimal("100.00"),
            old_realized_pl=Decimal("0"),
            fill_qty=100,
            fill_price=Decimal("120.00"),
            side="buy",
        )

        assert new_qty == 0
        assert new_pnl == Decimal("-2000.00")

    # ================== Adding to Positions ==================

    def test_add_to_long_position(self):
        """Verify adding to long position updates avg price."""
        # 100 @ $100, buy 100 more @ $120 = 200 @ $110
        new_qty, new_avg, new_pnl = self._calculate_pnl(
            old_qty=100,
            old_avg_price=Decimal("100.00"),
            old_realized_pl=Decimal("0"),
            fill_qty=100,
            fill_price=Decimal("120.00"),
            side="buy",
        )

        assert new_qty == 200
        assert new_avg == Decimal("110.00")  # Weighted average
        assert new_pnl == Decimal("0")  # No P&L on adding

    def test_add_to_short_position(self):
        """Verify adding to short position updates avg price."""
        # -100 @ $100, sell 100 more @ $120 = -200 @ $110
        new_qty, new_avg, new_pnl = self._calculate_pnl(
            old_qty=-100,
            old_avg_price=Decimal("100.00"),
            old_realized_pl=Decimal("0"),
            fill_qty=100,
            fill_price=Decimal("120.00"),
            side="sell",
        )

        assert new_qty == -200
        assert new_avg == Decimal("110.00")
        assert new_pnl == Decimal("0")

    # ================== Partial Closes ==================
    # FIXED: Partial closes now correctly realize P&L on the closed portion
    # while keeping the average entry price unchanged for remaining position.

    def test_partial_close_long_realizes_pnl(self):
        """Verify partial close of long position realizes P&L on closed portion.

        100 @ $100, sell 50 @ $120 = $1000 profit on closed portion
        Remaining: 50 @ $100 (avg price unchanged)
        """
        new_qty, new_avg, new_pnl = self._calculate_pnl(
            old_qty=100,
            old_avg_price=Decimal("100.00"),
            old_realized_pl=Decimal("0"),
            fill_qty=50,
            fill_price=Decimal("120.00"),
            side="sell",
        )

        assert new_qty == 50
        # FIXED: Avg price stays the same (not weighted with exit price)
        assert new_avg == Decimal("100.00")
        # FIXED: P&L realized immediately on partial close
        assert new_pnl == Decimal("1000.00")  # (120 - 100) * 50

    def test_partial_close_short_realizes_pnl(self):
        """Verify partial close of short position realizes P&L on closed portion.

        -100 @ $100, buy 50 @ $80 = $1000 profit on closed portion
        Remaining: -50 @ $100 (avg price unchanged)
        """
        new_qty, new_avg, new_pnl = self._calculate_pnl(
            old_qty=-100,
            old_avg_price=Decimal("100.00"),
            old_realized_pl=Decimal("0"),
            fill_qty=50,
            fill_price=Decimal("80.00"),
            side="buy",
        )

        assert new_qty == -50
        # FIXED: Avg price stays the same
        assert new_avg == Decimal("100.00")
        # FIXED: P&L realized immediately on partial close
        assert new_pnl == Decimal("1000.00")  # (100 - 80) * 50

    # ================== Cumulative P&L ==================

    def test_cumulative_pnl_with_partial_closes(self):
        """Verify P&L is realized at each partial close, not batched.

        FIXED: Each partial close realizes P&L immediately.
        """
        # Trade 1: Open 100 @ $100
        qty1, avg1, pnl1 = self._calculate_pnl(
            old_qty=0,
            old_avg_price=Decimal("0"),
            old_realized_pl=Decimal("0"),
            fill_qty=100,
            fill_price=Decimal("100.00"),
            side="buy",
        )
        assert qty1 == 100
        assert avg1 == Decimal("100.00")
        assert pnl1 == Decimal("0")

        # Trade 2: Sell 50 @ $120 (partial close - P&L realized!)
        # P&L = (120 - 100) * 50 = $1000 profit
        qty2, avg2, pnl2 = self._calculate_pnl(
            old_qty=qty1,
            old_avg_price=avg1,
            old_realized_pl=pnl1,
            fill_qty=50,
            fill_price=Decimal("120.00"),
            side="sell",
        )
        assert qty2 == 50
        assert avg2 == Decimal("100.00")  # Avg price unchanged
        assert pnl2 == Decimal("1000.00")  # P&L realized on partial close

        # Trade 3: Sell remaining 50 @ $80 (full close)
        # P&L = (80 - 100) * 50 = -$1000 loss on this trade
        # Cumulative P&L = $1000 + (-$1000) = $0
        qty3, avg3, pnl3 = self._calculate_pnl(
            old_qty=qty2,
            old_avg_price=avg2,
            old_realized_pl=pnl2,
            fill_qty=50,
            fill_price=Decimal("80.00"),
            side="sell",
        )
        assert qty3 == 0
        assert avg3 == Decimal("0")  # Position closed
        # Total P&L: $1000 (first partial) + (-$1000) (second partial) = $0
        assert pnl3 == Decimal("0")

    # ================== Edge Cases ==================

    def test_breakeven_trade(self):
        """Verify breakeven trade has zero P&L."""
        new_qty, new_avg, new_pnl = self._calculate_pnl(
            old_qty=100,
            old_avg_price=Decimal("100.00"),
            old_realized_pl=Decimal("0"),
            fill_qty=100,
            fill_price=Decimal("100.00"),
            side="sell",
        )

        assert new_qty == 0
        assert new_pnl == Decimal("0")

    def test_fractional_prices(self):
        """Verify P&L calculation handles fractional prices."""
        # 100 @ $99.50, sell @ $100.25 = $75 profit
        new_qty, new_avg, new_pnl = self._calculate_pnl(
            old_qty=100,
            old_avg_price=Decimal("99.50"),
            old_realized_pl=Decimal("0"),
            fill_qty=100,
            fill_price=Decimal("100.25"),
            side="sell",
        )

        assert new_qty == 0
        assert new_pnl == Decimal("75.00")

    def test_large_position(self):
        """Verify P&L calculation handles large positions."""
        # 10000 shares @ $500, sell @ $510 = $100,000 profit
        new_qty, new_avg, new_pnl = self._calculate_pnl(
            old_qty=10000,
            old_avg_price=Decimal("500.00"),
            old_realized_pl=Decimal("0"),
            fill_qty=10000,
            fill_price=Decimal("510.00"),
            side="sell",
        )

        assert new_qty == 0
        assert new_pnl == Decimal("100000.00")

    # ================== Position Flips ==================
    # When a trade crosses through flat (e.g., long 50, sell 100 → short 50)

    def test_flip_long_to_short_profit(self):
        """Verify flipping from long to short with profit.

        Long 50 @ $100, sell 100 @ $120:
        - Close 50 long: P&L = (120 - 100) * 50 = $1000 profit
        - Open 50 short @ $120 (new avg price)
        """
        new_qty, new_avg, new_pnl = self._calculate_pnl(
            old_qty=50,
            old_avg_price=Decimal("100.00"),
            old_realized_pl=Decimal("0"),
            fill_qty=100,
            fill_price=Decimal("120.00"),
            side="sell",
        )

        assert new_qty == -50  # Now short 50
        assert new_avg == Decimal("120.00")  # New position at fill price
        assert new_pnl == Decimal("1000.00")  # P&L only on closed 50 shares

    def test_flip_long_to_short_loss(self):
        """Verify flipping from long to short with loss.

        Long 50 @ $100, sell 100 @ $80:
        - Close 50 long: P&L = (80 - 100) * 50 = -$1000 loss
        - Open 50 short @ $80 (new avg price)
        """
        new_qty, new_avg, new_pnl = self._calculate_pnl(
            old_qty=50,
            old_avg_price=Decimal("100.00"),
            old_realized_pl=Decimal("0"),
            fill_qty=100,
            fill_price=Decimal("80.00"),
            side="sell",
        )

        assert new_qty == -50  # Now short 50
        assert new_avg == Decimal("80.00")  # New position at fill price
        assert new_pnl == Decimal("-1000.00")  # P&L only on closed 50 shares

    def test_flip_short_to_long_profit(self):
        """Verify flipping from short to long with profit.

        Short 50 @ $100, buy 100 @ $80:
        - Close 50 short: P&L = (100 - 80) * 50 = $1000 profit
        - Open 50 long @ $80 (new avg price)
        """
        new_qty, new_avg, new_pnl = self._calculate_pnl(
            old_qty=-50,
            old_avg_price=Decimal("100.00"),
            old_realized_pl=Decimal("0"),
            fill_qty=100,
            fill_price=Decimal("80.00"),
            side="buy",
        )

        assert new_qty == 50  # Now long 50
        assert new_avg == Decimal("80.00")  # New position at fill price
        assert new_pnl == Decimal("1000.00")  # P&L only on closed 50 shares

    def test_flip_short_to_long_loss(self):
        """Verify flipping from short to long with loss.

        Short 50 @ $100, buy 100 @ $120:
        - Close 50 short: P&L = (100 - 120) * 50 = -$1000 loss
        - Open 50 long @ $120 (new avg price)
        """
        new_qty, new_avg, new_pnl = self._calculate_pnl(
            old_qty=-50,
            old_avg_price=Decimal("100.00"),
            old_realized_pl=Decimal("0"),
            fill_qty=100,
            fill_price=Decimal("120.00"),
            side="buy",
        )

        assert new_qty == 50  # Now long 50
        assert new_avg == Decimal("120.00")  # New position at fill price
        assert new_pnl == Decimal("-1000.00")  # P&L only on closed 50 shares
