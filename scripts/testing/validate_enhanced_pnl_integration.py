#!/usr/bin/env python3
"""
Integration test for enhanced P&L calculation.

Tests the complete enhanced P&L workflow with realistic data:
1. Mock positions data (simulating T4 response)
2. Mock price data (simulating Alpaca response)
3. Calculate enhanced P&L
4. Validate results against expected values

Usage:
    python scripts/test_enhanced_pnl_integration.py
"""

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.ops.paper_run import calculate_enhanced_pnl  # noqa: E402


async def test_scenario_1_all_profitable():
    """Test scenario with all profitable positions."""
    print("\n" + "=" * 80)
    print("SCENARIO 1: All Profitable Positions")
    print("=" * 80)

    positions = [
        {"symbol": "AAPL", "qty": 100, "avg_entry_price": "150.00", "realized_pl": "0.00"},
        {"symbol": "MSFT", "qty": 50, "avg_entry_price": "300.00", "realized_pl": "100.00"},
    ]

    current_prices = {
        "AAPL": Decimal("155.00"),  # +$5 per share
        "MSFT": Decimal("310.00"),  # +$10 per share
    }

    pnl = await calculate_enhanced_pnl(positions, current_prices)

    # Expected results:
    # AAPL: (155 - 150) * 100 = +$500 unrealized
    # MSFT: (310 - 300) * 50 = +$500 unrealized, +$100 realized
    expected_unrealized = Decimal("1000.00")  # 500 + 500
    expected_realized = Decimal("100.00")
    expected_total = Decimal("1100.00")

    print("\n📊 Results:")
    print(f"   Realized P&L:       ${pnl['realized_pnl']:+,.2f}")
    print(f"   Unrealized P&L:     ${pnl['unrealized_pnl']:+,.2f}")
    print(f"   Total P&L:          ${pnl['total_pnl']:+,.2f}")
    print(f"\n   Open Positions:     {pnl['num_open_positions']}")
    print(f"   Closed Positions:   {pnl['num_closed_positions']}")

    assert (
        pnl["unrealized_pnl"] == expected_unrealized
    ), f"Expected unrealized {expected_unrealized}, got {pnl['unrealized_pnl']}"
    assert (
        pnl["realized_pnl"] == expected_realized
    ), f"Expected realized {expected_realized}, got {pnl['realized_pnl']}"
    assert (
        pnl["total_pnl"] == expected_total
    ), f"Expected total {expected_total}, got {pnl['total_pnl']}"
    assert pnl["num_open_positions"] == 2
    assert pnl["num_closed_positions"] == 0

    print("\n✅ PASSED: All assertions correct")


async def test_scenario_2_mixed_positions():
    """Test scenario with mixed long/short and open/closed positions."""
    print("\n" + "=" * 80)
    print("SCENARIO 2: Mixed Positions (Long/Short, Open/Closed)")
    print("=" * 80)

    positions = [
        {
            "symbol": "AAPL",
            "qty": 100,  # Long position
            "avg_entry_price": "150.00",
            "realized_pl": "0.00",
        },
        {
            "symbol": "MSFT",
            "qty": -50,  # Short position
            "avg_entry_price": "300.00",
            "realized_pl": "200.00",  # Partial close profit
        },
        {
            "symbol": "GOOGL",
            "qty": 0,  # Closed position
            "avg_entry_price": "140.00",
            "realized_pl": "-400.00",  # Loss
        },
    ]

    current_prices = {
        "AAPL": Decimal("152.00"),  # +$2 profit (long)
        "MSFT": Decimal("295.00"),  # -$5 profit (short)
    }

    pnl = await calculate_enhanced_pnl(positions, current_prices)

    # Expected results:
    # AAPL: (152 - 150) * 100 = +$200 unrealized
    # MSFT: (300 - 295) * 50 = +$250 unrealized (short profit), +$200 realized
    # GOOGL: -$400 realized
    expected_unrealized = Decimal("450.00")  # 200 + 250
    expected_realized = Decimal("-200.00")  # 0 + 200 + (-400)
    expected_total = Decimal("250.00")

    print("\n📊 Results:")
    print(f"   Realized P&L:       ${pnl['realized_pnl']:+,.2f}")
    print(f"   Unrealized P&L:     ${pnl['unrealized_pnl']:+,.2f}")
    print(f"   Total P&L:          ${pnl['total_pnl']:+,.2f}")
    print(f"\n   Open Positions:     {pnl['num_open_positions']}")
    print(f"   Closed Positions:   {pnl['num_closed_positions']}")

    print("\n📋 Per-Symbol Breakdown:")
    for symbol, info in pnl["per_symbol"].items():
        if info["status"] == "open":
            print(
                f"   {symbol:6} ({info['qty']:>5} shares): "
                f"Realized: ${info['realized']:+,.2f}, "
                f"Unrealized: ${info['unrealized']:+,.2f}"
            )
        else:
            print(f"   {symbol:6} (closed): Realized: ${info['realized']:+,.2f}")

    assert (
        pnl["unrealized_pnl"] == expected_unrealized
    ), f"Expected unrealized {expected_unrealized}, got {pnl['unrealized_pnl']}"
    assert (
        pnl["realized_pnl"] == expected_realized
    ), f"Expected realized {expected_realized}, got {pnl['realized_pnl']}"
    assert (
        pnl["total_pnl"] == expected_total
    ), f"Expected total {expected_total}, got {pnl['total_pnl']}"
    assert pnl["num_open_positions"] == 2
    assert pnl["num_closed_positions"] == 1

    print("\n✅ PASSED: All assertions correct")


async def test_scenario_3_short_positions():
    """Test scenario focusing on short position P&L calculation."""
    print("\n" + "=" * 80)
    print("SCENARIO 3: Short Positions (Profit and Loss)")
    print("=" * 80)

    positions = [
        {
            "symbol": "TSLA",
            "qty": -100,  # Short at $250
            "avg_entry_price": "250.00",
            "realized_pl": "0.00",
        },
        {
            "symbol": "NVDA",
            "qty": -50,  # Short at $500
            "avg_entry_price": "500.00",
            "realized_pl": "0.00",
        },
    ]

    current_prices = {
        "TSLA": Decimal("240.00"),  # Down $10 (profit for short)
        "NVDA": Decimal("520.00"),  # Up $20 (loss for short)
    }

    pnl = await calculate_enhanced_pnl(positions, current_prices)

    # Expected results:
    # TSLA: (250 - 240) * 100 = +$1000 unrealized (profit on short)
    # NVDA: (500 - 520) * 50 = -$1000 unrealized (loss on short)
    expected_unrealized = Decimal("0.00")  # 1000 + (-1000)
    expected_realized = Decimal("0.00")
    expected_total = Decimal("0.00")

    print("\n📊 Results:")
    print(f"   Realized P&L:       ${pnl['realized_pnl']:+,.2f}")
    print(f"   Unrealized P&L:     ${pnl['unrealized_pnl']:+,.2f}")
    print(f"   Total P&L:          ${pnl['total_pnl']:+,.2f}")

    print("\n📋 Per-Symbol Breakdown:")
    for symbol, info in pnl["per_symbol"].items():
        print(
            f"   {symbol:6} ({info['qty']:>5} shares @ ${info['avg_entry_price']:.2f}): "
            f"Current: ${info['current_price']:.2f}, "
            f"Unrealized: ${info['unrealized']:+,.2f}"
        )

    assert (
        pnl["unrealized_pnl"] == expected_unrealized
    ), f"Expected unrealized {expected_unrealized}, got {pnl['unrealized_pnl']}"
    assert (
        pnl["realized_pnl"] == expected_realized
    ), f"Expected realized {expected_realized}, got {pnl['realized_pnl']}"
    assert (
        pnl["total_pnl"] == expected_total
    ), f"Expected total {expected_total}, got {pnl['total_pnl']}"

    print("\n✅ PASSED: All assertions correct")


async def test_scenario_4_missing_prices():
    """Test graceful handling of missing price data."""
    print("\n" + "=" * 80)
    print("SCENARIO 4: Missing Price Data (Graceful Degradation)")
    print("=" * 80)

    positions = [
        {"symbol": "AAPL", "qty": 100, "avg_entry_price": "150.00", "realized_pl": "0.00"},
        {"symbol": "MSFT", "qty": 50, "avg_entry_price": "300.00", "realized_pl": "0.00"},
    ]

    # Missing prices (simulating Alpaca API failure)
    current_prices = {}

    print("\n⚠️  Simulating missing price data (Alpaca API failure)")

    pnl = await calculate_enhanced_pnl(positions, current_prices)

    # Expected results:
    # Both positions fall back to avg_entry_price -> zero unrealized P&L
    expected_unrealized = Decimal("0.00")
    expected_realized = Decimal("0.00")
    expected_total = Decimal("0.00")

    print("\n📊 Results:")
    print(f"   Realized P&L:       ${pnl['realized_pnl']:+,.2f}")
    print(f"   Unrealized P&L:     ${pnl['unrealized_pnl']:+,.2f}")
    print(f"   Total P&L:          ${pnl['total_pnl']:+,.2f}")

    assert (
        pnl["unrealized_pnl"] == expected_unrealized
    ), f"Expected unrealized {expected_unrealized}, got {pnl['unrealized_pnl']}"
    assert (
        pnl["realized_pnl"] == expected_realized
    ), f"Expected realized {expected_realized}, got {pnl['realized_pnl']}"
    assert (
        pnl["total_pnl"] == expected_total
    ), f"Expected total {expected_total}, got {pnl['total_pnl']}"

    print("\n✅ PASSED: Gracefully degraded to zero unrealized P&L")


async def main():
    """Run all integration test scenarios."""
    print("\n" + "=" * 80)
    print("ENHANCED P&L CALCULATION - INTEGRATION TESTS")
    print("=" * 80)

    try:
        await test_scenario_1_all_profitable()
        await test_scenario_2_mixed_positions()
        await test_scenario_3_short_positions()
        await test_scenario_4_missing_prices()

        print("\n" + "=" * 80)
        print("✅ ALL INTEGRATION TESTS PASSED")
        print("=" * 80)
        print("\nSummary:")
        print("  ✅ Scenario 1: All profitable positions")
        print("  ✅ Scenario 2: Mixed long/short and open/closed")
        print("  ✅ Scenario 3: Short position profit/loss")
        print("  ✅ Scenario 4: Missing price data handling")
        print("\nThe enhanced P&L calculation is working correctly!")
        print("=" * 80 + "\n")

        return 0

    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        return 1
    except (ValueError, KeyError, RuntimeError, OSError) as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
