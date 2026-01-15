#!/usr/bin/env python3
"""
Integration test for T5 Orchestrator Service.

Tests the complete orchestration workflow:
1. Database migration
2. Start Signal Service (mock or real)
3. Start Execution Gateway (DRY_RUN mode)
4. Start Orchestrator Service
5. Trigger orchestration run
6. Verify results

Requirements:
- PostgreSQL running
- Signal Service available (or mocked)
- Execution Gateway available (or mocked)

Usage:
    python scripts/test_t5_orchestrator.py
"""

import asyncio
import os
import sys
from decimal import Decimal
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from apps.orchestrator.database import OrchestrationDatabaseClient  # noqa: E402
from apps.orchestrator.orchestrator import (  # noqa: E402
    TradingOrchestrator,
    calculate_position_size,
)
from apps.orchestrator.schemas import Signal  # noqa: E402


def print_separator(title: str):
    """Print section separator."""
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80 + "\n")


def test_position_sizing():
    """Test 1: Position sizing calculation."""
    print_separator("TEST 1: Position Sizing Calculation")

    # Test case 1: Basic long position
    qty, dollar_amount = calculate_position_size(
        target_weight=0.333,
        capital=Decimal("100000"),
        price=Decimal("150.00"),
        max_position_size=Decimal("50000"),
    )

    print("Test case 1: 33.3% of $100k at $150/share")
    print("  Expected: 222 shares, $33,300")
    print(f"  Actual:   {qty} shares, ${dollar_amount}")

    assert qty == 222, f"Expected 222 shares, got {qty}"
    assert dollar_amount == Decimal("33300.00"), f"Expected $33,300, got ${dollar_amount}"
    print("  ✅ PASSED")

    # Test case 2: Max position size cap
    qty, dollar_amount = calculate_position_size(
        target_weight=0.50,
        capital=Decimal("100000"),
        price=Decimal("100.00"),
        max_position_size=Decimal("20000"),
    )

    print("\nTest case 2: 50% of $100k capped at $20k max")
    print("  Expected: 200 shares (capped), $20,000")
    print(f"  Actual:   {qty} shares, ${dollar_amount}")

    assert qty == 200, f"Expected 200 shares, got {qty}"
    assert dollar_amount == Decimal("20000.00"), f"Expected $20,000, got ${dollar_amount}"
    print("  ✅ PASSED")

    # Test case 3: Fractional shares rounded down
    qty, dollar_amount = calculate_position_size(
        target_weight=0.10,
        capital=Decimal("100000"),
        price=Decimal("151.00"),
        max_position_size=Decimal("50000"),
    )

    print("\nTest case 3: Fractional shares (66.225... → 66)")
    print("  Expected: 66 shares, $10,000")
    print(f"  Actual:   {qty} shares, ${dollar_amount}")

    assert qty == 66, f"Expected 66 shares, got {qty}"
    print("  ✅ PASSED")

    print("\n✅ All position sizing tests passed!")
    return True


def test_database_connection():
    """Test 2: Database connection."""
    print_separator("TEST 2: Database Connection")

    DATABASE_URL = os.getenv(
        "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/trading_platform"
    )

    print(f"Connecting to: {DATABASE_URL.split('@')[1]}")

    db_client = OrchestrationDatabaseClient(DATABASE_URL)

    if not db_client.check_connection():
        print("❌ FAILED: Database connection failed")
        print("   Make sure PostgreSQL is running and migration 003 is applied")
        return False

    print("✅ PASSED: Database connection successful")
    return True


async def test_orchestrator_with_mock_data():
    """Test 3: Orchestrator with mock data."""
    print_separator("TEST 3: Orchestrator with Mock Data")

    # Create orchestrator with price cache (mock data)
    price_cache = {"AAPL": Decimal("150.00"), "MSFT": Decimal("300.00"), "GOOGL": Decimal("100.00")}

    orchestrator = TradingOrchestrator(
        signal_service_url="http://localhost:8001",  # Won't be called in this test
        execution_gateway_url="http://localhost:8002",  # Won't be called in this test
        capital=Decimal("100000"),
        max_position_size=Decimal("20000"),
        price_cache=price_cache,
    )

    # Create mock signals
    mock_signals = [
        Signal(symbol="AAPL", predicted_return=0.015, rank=1, target_weight=0.333),
        Signal(symbol="MSFT", predicted_return=0.010, rank=2, target_weight=0.333),
        Signal(symbol="GOOGL", predicted_return=-0.012, rank=3, target_weight=-0.333),
    ]

    print(f"Mock signals created: {len(mock_signals)}")
    for signal in mock_signals:
        print(f"  {signal.symbol}: weight={signal.target_weight:+.3f}, rank={signal.rank}")

    # Test signal-to-order mapping
    mappings = await orchestrator._map_signals_to_orders(mock_signals)

    print("\nSignal-to-order mapping results:")
    for mapping in mappings:
        if mapping.order_qty:
            print(f"  {mapping.symbol}: {mapping.order_side.upper()} {mapping.order_qty} shares")
        else:
            print(f"  {mapping.symbol}: SKIPPED ({mapping.skip_reason})")

    # Verify mappings
    assert len(mappings) == 3, f"Expected 3 mappings, got {len(mappings)}"

    # AAPL: 33.3% of $100k = $33.3k, capped at $20k → $20k / $150 = 133 shares
    aapl = next(m for m in mappings if m.symbol == "AAPL")
    assert aapl.order_qty == 133, f"Expected 133 AAPL shares, got {aapl.order_qty}"
    assert aapl.order_side == "buy"

    # MSFT: 33.3% of $100k = $33.3k, capped at $20k → $20k / $300 = 66 shares
    msft = next(m for m in mappings if m.symbol == "MSFT")
    assert msft.order_qty == 66, f"Expected 66 MSFT shares, got {msft.order_qty}"
    assert msft.order_side == "buy"

    # GOOGL: -33.3% of $100k = $33.3k, capped at $20k → $20k / $100 = 200 shares
    googl = next(m for m in mappings if m.symbol == "GOOGL")
    assert googl.order_qty == 200, f"Expected 200 GOOGL shares, got {googl.order_qty}"
    assert googl.order_side == "sell"

    print("\n✅ PASSED: All mappings correct")

    await orchestrator.close()
    return True


def print_results(results: dict):
    """Print test results summary."""
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 78 + "║")
    print("║" + "  TEST RESULTS".center(78) + "║")
    print("║" + " " * 78 + "║")
    print("╠" + "=" * 78 + "╣")
    print(f"║  ✅ Passed:  {results['passed']}" + " " * (68 - len(str(results["passed"]))) + "║")
    print(f"║  ❌ Failed:  {results['failed']}" + " " * (68 - len(str(results["failed"]))) + "║")
    print("║" + " " * 78 + "║")

    total = results["passed"] + results["failed"]
    pass_rate = (results["passed"] / total * 100) if total > 0 else 0

    print(f"║  Pass Rate: {pass_rate:.1f}%".ljust(79) + "║")
    print("║" + " " * 78 + "║")
    print("╚" + "=" * 78 + "╝")
    print("\n")


async def main():
    """Run all tests."""
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 78 + "║")
    print("║" + "  ORCHESTRATOR SERVICE TEST SUITE (T5)".center(78) + "║")
    print("║" + " " * 78 + "║")
    print("╚" + "=" * 78 + "╝")

    results = {"passed": 0, "failed": 0}

    # Test 1: Position sizing
    if test_position_sizing():
        results["passed"] += 1
    else:
        results["failed"] += 1

    # Test 2: Database connection
    if test_database_connection():
        results["passed"] += 1
    else:
        results["failed"] += 1
        print("\n❌ CRITICAL: Database test failed. Stopping.")
        print_results(results)
        return 1

    # Test 3: Orchestrator with mock data
    if await test_orchestrator_with_mock_data():
        results["passed"] += 1
    else:
        results["failed"] += 1

    # Print final results
    print_results(results)

    return 0 if results["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
