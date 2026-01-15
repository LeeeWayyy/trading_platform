"""
Unit tests for enhanced P&L calculation (P1T0).

Tests coverage for AC5: Multi-day scenarios, partial fills, buy-sell sequences.

The enhanced P&L calculator computes:
- Realized P&L from closed positions (qty=0)
- Unrealized P&L from open positions with mark-to-market pricing
- Per-symbol breakdown with position details
- Total P&L (realized + unrealized)

Test Strategy:
- Unit tests with deterministic inputs (no external dependencies)
- Mock Alpaca price data for reproducibility
- Cover all position states (open, closed, long, short)
- Edge cases (missing prices, zero positions, negative quantities)

See Also:
    - scripts/paper_run.py:calculate_enhanced_pnl() - Function under test
    - ADR-0008: Enhanced P&L calculation architecture
    - docs/CONCEPTS/pnl-calculation.md - P&L formulas
"""

import sys
from decimal import Decimal
from pathlib import Path

import pytest

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.ops.paper_run import calculate_enhanced_pnl  # noqa: E402


class TestCalculateEnhancedPNL:
    """Test suite for calculate_enhanced_pnl() function."""

    @pytest.mark.asyncio()
    async def test_single_open_long_position_profit(self) -> None:
        """
        Test single open long position with profit.

        Scenario:
            - AAPL: Long 100 shares @ $150.00 entry
            - Current price: $152.00
            - Expected unrealized P&L: (152 - 150) * 100 = +$200.00
        """
        positions = [
            {
                "symbol": "AAPL",
                "qty": 100,
                "avg_entry_price": "150.00",
                "realized_pl": "0",
            }
        ]
        current_prices = {"AAPL": Decimal("152.00")}

        result = await calculate_enhanced_pnl(positions, current_prices)

        assert result["realized_pnl"] == Decimal("0")
        assert result["unrealized_pnl"] == Decimal("200.00")
        assert result["total_pnl"] == Decimal("200.00")
        assert result["num_open_positions"] == 1
        assert result["num_closed_positions"] == 0
        assert result["total_positions"] == 1

        # Check per-symbol breakdown
        assert "AAPL" in result["per_symbol"]
        aapl_pnl = result["per_symbol"]["AAPL"]
        assert aapl_pnl["realized"] == Decimal("0")
        assert aapl_pnl["unrealized"] == Decimal("200.00")
        assert aapl_pnl["qty"] == 100
        assert aapl_pnl["avg_entry_price"] == Decimal("150.00")
        assert aapl_pnl["current_price"] == Decimal("152.00")
        assert aapl_pnl["status"] == "open"

    @pytest.mark.asyncio()
    async def test_single_open_long_position_loss(self) -> None:
        """
        Test single open long position with loss.

        Scenario:
            - MSFT: Long 50 shares @ $300.00 entry
            - Current price: $295.00
            - Expected unrealized P&L: (295 - 300) * 50 = -$250.00
        """
        positions = [
            {
                "symbol": "MSFT",
                "qty": 50,
                "avg_entry_price": "300.00",
                "realized_pl": "0",
            }
        ]
        current_prices = {"MSFT": Decimal("295.00")}

        result = await calculate_enhanced_pnl(positions, current_prices)

        assert result["unrealized_pnl"] == Decimal("-250.00")
        assert result["total_pnl"] == Decimal("-250.00")

    @pytest.mark.asyncio()
    async def test_single_open_short_position_profit(self) -> None:
        """
        Test single open short position with profit.

        Scenario:
            - GOOGL: Short 30 shares @ $140.00 entry (qty = -30)
            - Current price: $135.00
            - Expected unrealized P&L: (135 - 140) * (-30) = +$150.00
        """
        positions = [
            {
                "symbol": "GOOGL",
                "qty": -30,
                "avg_entry_price": "140.00",
                "realized_pl": "0",
            }
        ]
        current_prices = {"GOOGL": Decimal("135.00")}

        result = await calculate_enhanced_pnl(positions, current_prices)

        assert result["unrealized_pnl"] == Decimal("150.00")
        assert result["total_pnl"] == Decimal("150.00")

    @pytest.mark.asyncio()
    async def test_single_open_short_position_loss(self) -> None:
        """
        Test single open short position with loss.

        Scenario:
            - TSLA: Short 20 shares @ $250.00 entry (qty = -20)
            - Current price: $260.00
            - Expected unrealized P&L: (260 - 250) * (-20) = -$200.00
        """
        positions = [
            {
                "symbol": "TSLA",
                "qty": -20,
                "avg_entry_price": "250.00",
                "realized_pl": "0",
            }
        ]
        current_prices = {"TSLA": Decimal("260.00")}

        result = await calculate_enhanced_pnl(positions, current_prices)

        assert result["unrealized_pnl"] == Decimal("-200.00")
        assert result["total_pnl"] == Decimal("-200.00")

    @pytest.mark.asyncio()
    async def test_single_closed_position_with_realized_profit(self) -> None:
        """
        Test single closed position with realized profit.

        Scenario:
            - AAPL: Closed position (qty=0)
            - Realized P&L: +$500.00
            - Expected: Only realized P&L counted, no unrealized
        """
        positions = [
            {
                "symbol": "AAPL",
                "qty": 0,
                "avg_entry_price": "150.00",
                "realized_pl": "500.00",
            }
        ]
        current_prices = {}

        result = await calculate_enhanced_pnl(positions, current_prices)

        assert result["realized_pnl"] == Decimal("500.00")
        assert result["unrealized_pnl"] == Decimal("0")
        assert result["total_pnl"] == Decimal("500.00")
        assert result["num_open_positions"] == 0
        assert result["num_closed_positions"] == 1

        # Check per-symbol breakdown
        aapl_pnl = result["per_symbol"]["AAPL"]
        assert aapl_pnl["realized"] == Decimal("500.00")
        assert aapl_pnl["unrealized"] == Decimal("0")
        assert aapl_pnl["qty"] == 0
        assert aapl_pnl["status"] == "closed"

    @pytest.mark.asyncio()
    async def test_single_closed_position_with_realized_loss(self) -> None:
        """
        Test single closed position with realized loss.

        Scenario:
            - MSFT: Closed position (qty=0)
            - Realized P&L: -$300.00
        """
        positions = [
            {
                "symbol": "MSFT",
                "qty": 0,
                "avg_entry_price": "300.00",
                "realized_pl": "-300.00",
            }
        ]
        current_prices = {}

        result = await calculate_enhanced_pnl(positions, current_prices)

        assert result["realized_pnl"] == Decimal("-300.00")
        assert result["unrealized_pnl"] == Decimal("0")
        assert result["total_pnl"] == Decimal("-300.00")
        assert result["num_closed_positions"] == 1

    @pytest.mark.asyncio()
    async def test_multiple_open_positions_mixed_pnl(self) -> None:
        """
        Test multiple open positions with mixed P&L (some profit, some loss).

        Scenario:
            - AAPL: Long 100 @ $150, current $152 → +$200
            - MSFT: Long 50 @ $300, current $295 → -$250
            - GOOGL: Short 30 @ $140, current $135 → +$150
            - Total unrealized: $200 - $250 + $150 = +$100
        """
        positions = [
            {"symbol": "AAPL", "qty": 100, "avg_entry_price": "150.00", "realized_pl": "0"},
            {"symbol": "MSFT", "qty": 50, "avg_entry_price": "300.00", "realized_pl": "0"},
            {"symbol": "GOOGL", "qty": -30, "avg_entry_price": "140.00", "realized_pl": "0"},
        ]
        current_prices = {
            "AAPL": Decimal("152.00"),
            "MSFT": Decimal("295.00"),
            "GOOGL": Decimal("135.00"),
        }

        result = await calculate_enhanced_pnl(positions, current_prices)

        assert result["realized_pnl"] == Decimal("0")
        assert result["unrealized_pnl"] == Decimal("100.00")
        assert result["total_pnl"] == Decimal("100.00")
        assert result["num_open_positions"] == 3
        assert result["num_closed_positions"] == 0

    @pytest.mark.asyncio()
    async def test_mixed_open_and_closed_positions(self) -> None:
        """
        Test mix of open and closed positions.

        Scenario:
            - AAPL: Open long 100 @ $150, current $152 → unrealized +$200
            - MSFT: Closed with realized +$500
            - GOOGL: Open short 30 @ $140, current $135 → unrealized +$150
            - Total realized: +$500
            - Total unrealized: +$350
            - Total P&L: +$850
        """
        positions = [
            {"symbol": "AAPL", "qty": 100, "avg_entry_price": "150.00", "realized_pl": "0"},
            {"symbol": "MSFT", "qty": 0, "avg_entry_price": "300.00", "realized_pl": "500.00"},
            {"symbol": "GOOGL", "qty": -30, "avg_entry_price": "140.00", "realized_pl": "0"},
        ]
        current_prices = {
            "AAPL": Decimal("152.00"),
            "GOOGL": Decimal("135.00"),
        }

        result = await calculate_enhanced_pnl(positions, current_prices)

        assert result["realized_pnl"] == Decimal("500.00")
        assert result["unrealized_pnl"] == Decimal("350.00")
        assert result["total_pnl"] == Decimal("850.00")
        assert result["num_open_positions"] == 2
        assert result["num_closed_positions"] == 1
        assert result["total_positions"] == 3

    @pytest.mark.asyncio()
    async def test_open_position_with_both_realized_and_unrealized(self) -> None:
        """
        Test open position that has both realized P&L and unrealized P&L.

        Scenario:
            - AAPL: Currently 50 shares @ $151 avg entry
            - Previously closed 50 shares with realized P&L +$100
            - Current price: $153
            - Expected realized: +$100
            - Expected unrealized: (153 - 151) * 50 = +$100
            - Expected total: +$200
        """
        positions = [
            {
                "symbol": "AAPL",
                "qty": 50,
                "avg_entry_price": "151.00",
                "realized_pl": "100.00",
            }
        ]
        current_prices = {"AAPL": Decimal("153.00")}

        result = await calculate_enhanced_pnl(positions, current_prices)

        assert result["realized_pnl"] == Decimal("100.00")
        assert result["unrealized_pnl"] == Decimal("100.00")
        assert result["total_pnl"] == Decimal("200.00")

        aapl_pnl = result["per_symbol"]["AAPL"]
        assert aapl_pnl["realized"] == Decimal("100.00")
        assert aapl_pnl["unrealized"] == Decimal("100.00")

    @pytest.mark.asyncio()
    async def test_missing_current_price_fallback_to_entry(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        Test fallback when current price is missing for a symbol.

        Scenario:
            - AAPL: Long 100 @ $150
            - Current price NOT available
            - Expected: Falls back to avg_entry_price → zero unrealized P&L
            - Should print warning to stderr
        """
        positions = [
            {"symbol": "AAPL", "qty": 100, "avg_entry_price": "150.00", "realized_pl": "0"}
        ]
        current_prices = {}  # No price for AAPL

        result = await calculate_enhanced_pnl(positions, current_prices)

        # Should have zero unrealized P&L (current = entry)
        assert result["unrealized_pnl"] == Decimal("0")
        assert result["total_pnl"] == Decimal("0")

        # Check per-symbol breakdown
        aapl_pnl = result["per_symbol"]["AAPL"]
        assert aapl_pnl["current_price"] == Decimal("150.00")  # Fell back to entry
        assert aapl_pnl["unrealized"] == Decimal("0")

        # Check warning was printed
        captured = capsys.readouterr()
        assert "Warning: No current price for AAPL" in captured.err

    @pytest.mark.asyncio()
    async def test_empty_positions_list(self) -> None:
        """
        Test empty positions list.

        Scenario:
            - No positions
            - Expected: All P&L values zero
        """
        positions: list[dict[str, str | int]] = []
        current_prices: dict[str, Decimal] = {}

        result = await calculate_enhanced_pnl(positions, current_prices)

        assert result["realized_pnl"] == Decimal("0")
        assert result["unrealized_pnl"] == Decimal("0")
        assert result["total_pnl"] == Decimal("0")
        assert result["num_open_positions"] == 0
        assert result["num_closed_positions"] == 0
        assert result["total_positions"] == 0
        assert result["per_symbol"] == {}

    @pytest.mark.asyncio()
    async def test_partial_fills_scenario(self) -> None:
        """
        Test partial fills scenario (AC5 requirement).

        Scenario:
            - Bought 100 AAPL @ $150
            - Sold 60 AAPL @ $152 → realized +$120
            - Still holding 40 AAPL @ $150 avg, current $153
            - Expected realized: +$120
            - Expected unrealized: (153 - 150) * 40 = +$120
            - Expected total: +$240
        """
        positions = [
            {
                "symbol": "AAPL",
                "qty": 40,
                "avg_entry_price": "150.00",
                "realized_pl": "120.00",
            }
        ]
        current_prices = {"AAPL": Decimal("153.00")}

        result = await calculate_enhanced_pnl(positions, current_prices)

        assert result["realized_pnl"] == Decimal("120.00")
        assert result["unrealized_pnl"] == Decimal("120.00")
        assert result["total_pnl"] == Decimal("240.00")

    @pytest.mark.asyncio()
    async def test_buy_sell_sequence_fully_closed(self) -> None:
        """
        Test buy-sell sequence resulting in fully closed position (AC5 requirement).

        Scenario:
            - Bought 100 AAPL @ $150
            - Sold 100 AAPL @ $155
            - Position closed: qty=0, realized P&L = +$500
        """
        positions = [
            {
                "symbol": "AAPL",
                "qty": 0,
                "avg_entry_price": "150.00",
                "realized_pl": "500.00",
            }
        ]
        current_prices = {}

        result = await calculate_enhanced_pnl(positions, current_prices)

        assert result["realized_pnl"] == Decimal("500.00")
        assert result["unrealized_pnl"] == Decimal("0")
        assert result["total_pnl"] == Decimal("500.00")
        assert result["num_closed_positions"] == 1

    @pytest.mark.asyncio()
    async def test_multi_day_scenario(self) -> None:
        """
        Test multi-day trading scenario (AC5 requirement).

        Scenario (simulating 3 days of trading):
            - Day 1: Bought 100 AAPL @ $150
            - Day 2: Bought 50 more AAPL @ $152 → avg $151
            - Day 3: Current price $155
            - Expected unrealized: (155 - 151) * 150 = +$600
        """
        positions = [
            {
                "symbol": "AAPL",
                "qty": 150,
                "avg_entry_price": "151.00",  # Averaged from 100@150 + 50@152
                "realized_pl": "0",
            }
        ]
        current_prices = {"AAPL": Decimal("155.00")}

        result = await calculate_enhanced_pnl(positions, current_prices)

        assert result["unrealized_pnl"] == Decimal("600.00")
        assert result["total_pnl"] == Decimal("600.00")

    @pytest.mark.asyncio()
    async def test_decimal_precision(self) -> None:
        """
        Test Decimal precision for accurate P&L calculation.

        Scenario:
            - AAPL: 33 shares @ $150.333 entry, current $150.666
            - Expected unrealized: (150.666 - 150.333) * 33 = +$10.989
        """
        positions = [
            {
                "symbol": "AAPL",
                "qty": 33,
                "avg_entry_price": "150.333",
                "realized_pl": "0",
            }
        ]
        current_prices = {"AAPL": Decimal("150.666")}

        result = await calculate_enhanced_pnl(positions, current_prices)

        # Check precision (should be 10.989)
        assert result["unrealized_pnl"] == Decimal("10.989")

    @pytest.mark.asyncio()
    async def test_large_position_values(self) -> None:
        """
        Test large position values (stress test).

        Scenario:
            - AAPL: 10,000 shares @ $150, current $151
            - Expected unrealized: (151 - 150) * 10,000 = +$10,000
        """
        positions = [
            {
                "symbol": "AAPL",
                "qty": 10000,
                "avg_entry_price": "150.00",
                "realized_pl": "0",
            }
        ]
        current_prices = {"AAPL": Decimal("151.00")}

        result = await calculate_enhanced_pnl(positions, current_prices)

        assert result["unrealized_pnl"] == Decimal("10000.00")
        assert result["total_pnl"] == Decimal("10000.00")

    @pytest.mark.asyncio()
    async def test_comprehensive_scenario(self) -> None:
        """
        Comprehensive test with all features (AC5 comprehensive coverage).

        Scenario:
            - AAPL: Open long 100 @ $150, current $152 → unrealized +$200, realized $50
            - MSFT: Closed with realized +$500
            - GOOGL: Open short 30 @ $140, current $135 → unrealized +$150
            - TSLA: Open long 20 @ $250, no current price → unrealized $0 (fallback)
            - NVDA: Open long 40 @ $400, current $390 → unrealized -$400

        Expected:
            - Realized: $50 + $500 = +$550
            - Unrealized: $200 + $150 + $0 - $400 = -$50
            - Total: +$500
        """
        positions = [
            {"symbol": "AAPL", "qty": 100, "avg_entry_price": "150.00", "realized_pl": "50.00"},
            {"symbol": "MSFT", "qty": 0, "avg_entry_price": "300.00", "realized_pl": "500.00"},
            {"symbol": "GOOGL", "qty": -30, "avg_entry_price": "140.00", "realized_pl": "0"},
            {"symbol": "TSLA", "qty": 20, "avg_entry_price": "250.00", "realized_pl": "0"},
            {"symbol": "NVDA", "qty": 40, "avg_entry_price": "400.00", "realized_pl": "0"},
        ]
        current_prices = {
            "AAPL": Decimal("152.00"),
            "GOOGL": Decimal("135.00"),
            "NVDA": Decimal("390.00"),
            # Note: TSLA missing → will fallback
        }

        result = await calculate_enhanced_pnl(positions, current_prices)

        assert result["realized_pnl"] == Decimal("550.00")
        assert result["unrealized_pnl"] == Decimal("-50.00")
        assert result["total_pnl"] == Decimal("500.00")
        assert result["num_open_positions"] == 4
        assert result["num_closed_positions"] == 1
        assert result["total_positions"] == 5

        # Check individual symbols
        assert result["per_symbol"]["AAPL"]["unrealized"] == Decimal("200.00")
        assert result["per_symbol"]["MSFT"]["status"] == "closed"
        assert result["per_symbol"]["GOOGL"]["unrealized"] == Decimal("150.00")
        assert result["per_symbol"]["TSLA"]["unrealized"] == Decimal("0")  # Fallback
        assert result["per_symbol"]["NVDA"]["unrealized"] == Decimal("-400.00")
