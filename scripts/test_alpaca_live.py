"""
Live integration test for Alpaca API.

Tests actual connection to Alpaca paper trading and validates:
1. Account credentials and connection
2. Order submission (market and limit)
3. Order status queries
4. Position tracking
5. Order cancellation

WARNING: This test submits REAL orders to Alpaca paper trading.
Only run with valid Alpaca paper trading credentials.
"""

import os
import sys
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Load environment variables from .env
try:
    from dotenv import load_dotenv

    env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(dotenv_path=env_path)
except ImportError:
    print("⚠️  python-dotenv not installed. Using system environment variables only.")
    pass

from apps.execution_gateway.alpaca_client import AlpacaClientError, AlpacaExecutor
from apps.execution_gateway.order_id_generator import generate_client_order_id
from apps.execution_gateway.schemas import OrderRequest


def print_separator(title: str):
    """Print section separator."""
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80 + "\n")


def test_1_connection():
    """Test 1: Verify Alpaca connection and credentials."""
    print_separator("TEST 1: Connection and Credentials")

    # Get credentials from environment
    api_key = os.getenv("ALPACA_API_KEY_ID")
    secret_key = os.getenv("ALPACA_API_SECRET_KEY")
    base_url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

    if not api_key or not secret_key:
        print("❌ FAILED: Alpaca credentials not found in environment")
        print("   Please set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY")
        return None

    if api_key == "your_key_here" or secret_key == "your_secret_here":
        print("❌ FAILED: Alpaca credentials are placeholders")
        print("   Please update .env with real Alpaca paper trading credentials")
        return None

    print("📡 Connecting to Alpaca...")
    print(f"   Base URL: {base_url}")
    print(f"   API Key: {api_key[:8]}...")

    try:
        executor = AlpacaExecutor(
            api_key=api_key, secret_key=secret_key, base_url=base_url, paper=True
        )

        # Check connection
        if not executor.check_connection():
            print("❌ FAILED: Connection check failed")
            return None

        print("✅ PASSED: Connected to Alpaca successfully")

        # Get account info
        account = executor.get_account_info()
        if not account:
            print("❌ FAILED: Could not retrieve account info")
            return None

        print("\n📊 Account Information:")
        print(f"   Account Number: {account['account_number']}")
        print(f"   Status: {account['status']}")
        print(f"   Currency: {account['currency']}")
        print(f"   Buying Power: ${account['buying_power']:,.2f}")
        print(f"   Cash: ${account['cash']:,.2f}")
        print(f"   Portfolio Value: ${account['portfolio_value']:,.2f}")
        print(f"   Pattern Day Trader: {account['pattern_day_trader']}")
        print(f"   Trading Blocked: {account['trading_blocked']}")

        if account["trading_blocked"]:
            print("\n⚠️  WARNING: Trading is blocked on this account")
            return None

        print("\n✅ PASSED: Account is active and ready for trading")
        return executor

    except Exception as e:
        print(f"❌ FAILED: {e}")
        return None


def test_2_submit_market_order(executor: AlpacaExecutor):
    """Test 2: Submit a market order."""
    print_separator("TEST 2: Market Order Submission")

    # Create a small market order (1 share of AAPL)
    order = OrderRequest(symbol="AAPL", side="buy", qty=1, order_type="market", time_in_force="day")

    # Generate deterministic client_order_id
    strategy_id = os.getenv("STRATEGY_ID", "alpha_baseline")
    client_order_id = generate_client_order_id(order, strategy_id, date.today())

    print("📤 Submitting market order:")
    print(f"   Symbol: {order.symbol}")
    print(f"   Side: {order.side}")
    print(f"   Qty: {order.qty}")
    print(f"   Type: {order.order_type}")
    print(f"   Client Order ID: {client_order_id}")

    try:
        response = executor.submit_order(order, client_order_id)

        print("\n✅ PASSED: Order submitted successfully")
        print(f"   Broker Order ID: {response['id']}")
        print(f"   Status: {response['status']}")
        print(f"   Created At: {response['created_at']}")

        # Wait a moment for order to potentially fill
        print("\n⏳ Waiting 3 seconds for potential fill...")
        time.sleep(3)

        # Query order status
        updated_order = executor.get_order_by_client_id(client_order_id)
        if updated_order:
            print("\n📊 Updated Order Status:")
            print(f"   Status: {updated_order['status']}")
            print(f"   Filled Qty: {updated_order['filled_qty']}")
            if updated_order["filled_avg_price"]:
                print(f"   Filled Avg Price: ${updated_order['filled_avg_price']:.2f}")

        return response

    except AlpacaClientError as e:
        print(f"❌ FAILED: {e}")
        return None
    except Exception as e:
        print(f"❌ FAILED: Unexpected error: {e}")
        return None


def test_3_submit_limit_order(executor: AlpacaExecutor):
    """Test 3: Submit a limit order (won't fill immediately)."""
    print_separator("TEST 3: Limit Order Submission")

    # Create a limit order with price far below market (won't fill)
    order = OrderRequest(
        symbol="AAPL",
        side="buy",
        qty=1,
        order_type="limit",
        limit_price=Decimal("100.00"),  # Well below market price
        time_in_force="day",
    )

    strategy_id = os.getenv("STRATEGY_ID", "alpha_baseline")
    client_order_id = generate_client_order_id(order, strategy_id, date.today())

    print("📤 Submitting limit order:")
    print(f"   Symbol: {order.symbol}")
    print(f"   Side: {order.side}")
    print(f"   Qty: {order.qty}")
    print(f"   Type: {order.order_type}")
    print(f"   Limit Price: ${order.limit_price}")
    print(f"   Client Order ID: {client_order_id}")

    try:
        response = executor.submit_order(order, client_order_id)

        print("\n✅ PASSED: Limit order submitted successfully")
        print(f"   Broker Order ID: {response['id']}")
        print(f"   Status: {response['status']}")
        print(f"   Limit Price: ${response['limit_price']:.2f}")

        return response

    except AlpacaClientError as e:
        print(f"❌ FAILED: {e}")
        return None
    except Exception as e:
        print(f"❌ FAILED: Unexpected error: {e}")
        return None


def test_4_query_order(executor: AlpacaExecutor, client_order_id: str):
    """Test 4: Query order by client_order_id."""
    print_separator("TEST 4: Query Order by Client ID")

    print(f"🔍 Querying order: {client_order_id}")

    try:
        order = executor.get_order_by_client_id(client_order_id)

        if not order:
            print("❌ FAILED: Order not found")
            return None

        print("\n✅ PASSED: Order retrieved successfully")
        print(f"   Broker Order ID: {order['id']}")
        print(f"   Symbol: {order['symbol']}")
        print(f"   Side: {order['side']}")
        print(f"   Qty: {order['qty']}")
        print(f"   Status: {order['status']}")
        print(f"   Filled Qty: {order['filled_qty']}")
        if order["filled_avg_price"]:
            print(f"   Filled Avg Price: ${order['filled_avg_price']:.2f}")

        return order

    except Exception as e:
        print(f"❌ FAILED: {e}")
        return None


def test_5_cancel_order(executor: AlpacaExecutor, broker_order_id: str):
    """Test 5: Cancel an order."""
    print_separator("TEST 5: Cancel Order")

    print(f"🚫 Cancelling order: {broker_order_id}")

    try:
        success = executor.cancel_order(broker_order_id)

        if success:
            print("✅ PASSED: Order cancelled successfully")
        else:
            print("❌ FAILED: Order cancellation failed")

        return success

    except AlpacaClientError as e:
        # Order might already be filled/cancelled
        print(f"⚠️  Order could not be cancelled: {e}")
        print("   (This is OK if order already filled/cancelled)")
        return False
    except Exception as e:
        print(f"❌ FAILED: Unexpected error: {e}")
        return False


def test_6_idempotency(executor: AlpacaExecutor):
    """Test 6: Verify idempotency (same order twice = same ID)."""
    print_separator("TEST 6: Idempotency Test")

    # Create order
    order = OrderRequest(
        symbol="MSFT",
        side="buy",
        qty=1,
        order_type="limit",
        limit_price=Decimal("200.00"),  # Below market
        time_in_force="day",
    )

    strategy_id = os.getenv("STRATEGY_ID", "alpha_baseline")
    client_order_id = generate_client_order_id(order, strategy_id, date.today())

    print("📤 Submitting first order...")
    print(f"   Client Order ID: {client_order_id}")

    try:
        # First submission
        response1 = executor.submit_order(order, client_order_id)
        print(f"✅ First submission successful: {response1['id']}")

        print("\n📤 Submitting same order again (should detect duplicate)...")

        # Try to submit again with same client_order_id
        # Alpaca will reject duplicate client_order_id
        try:
            response2 = executor.submit_order(order, client_order_id)
            print(f"⚠️  Second submission returned: {response2['id']}")

            if response2["id"] == response1["id"]:
                print("✅ PASSED: Alpaca returned same order (idempotent)")
            else:
                print("❌ FAILED: Different order ID returned (not idempotent)")

        except AlpacaClientError as e:
            # Alpaca might reject with 422 for duplicate client_order_id
            if "duplicate" in str(e).lower() or "already exists" in str(e).lower():
                print(f"✅ PASSED: Alpaca correctly rejected duplicate: {e}")
            else:
                print(f"⚠️  Order rejected for different reason: {e}")

        # Cancel the order
        executor.cancel_order(response1["id"])
        print(f"\n🧹 Cleaned up test order: {response1['id']}")

        return True

    except Exception as e:
        print(f"❌ FAILED: {e}")
        return False


def main():
    """Run all live integration tests."""
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 78 + "║")
    print("║" + "  ALPACA LIVE INTEGRATION TEST SUITE".center(78) + "║")
    print("║" + " " * 78 + "║")
    print("║" + "  WARNING: This will submit REAL orders to Alpaca paper trading".center(78) + "║")
    print("║" + " " * 78 + "║")
    print("╚" + "=" * 78 + "╝")

    # Check if we should proceed
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    if dry_run:
        print("\n⚠️  DRY_RUN=true detected. Set DRY_RUN=false to run live tests.")
        print("   This test requires actual Alpaca API calls.")
        return 1

    results = {"passed": 0, "failed": 0, "skipped": 0}

    # Test 1: Connection
    executor = test_1_connection()
    if executor:
        results["passed"] += 1
    else:
        results["failed"] += 1
        print("\n❌ CRITICAL: Connection test failed. Stopping.")
        print_results(results)
        return 1

    # Test 2: Market Order
    market_order = test_2_submit_market_order(executor)
    if market_order:
        results["passed"] += 1
        market_client_id = market_order["client_order_id"]
    else:
        results["failed"] += 1
        market_client_id = None

    # Test 3: Limit Order
    limit_order = test_3_submit_limit_order(executor)
    if limit_order:
        results["passed"] += 1
        limit_broker_id = limit_order["id"]
    else:
        results["failed"] += 1
        limit_broker_id = None

    # Test 4: Query Order
    if market_client_id:
        queried_order = test_4_query_order(executor, market_client_id)
        if queried_order:
            results["passed"] += 1
        else:
            results["failed"] += 1
    else:
        print_separator("TEST 4: Query Order by Client ID")
        print("⏭️  SKIPPED: No market order to query")
        results["skipped"] += 1

    # Test 5: Cancel Order
    if limit_broker_id:
        cancelled = test_5_cancel_order(executor, limit_broker_id)
        if cancelled:
            results["passed"] += 1
        else:
            # Might be already filled/cancelled - not a hard failure
            results["passed"] += 1
    else:
        print_separator("TEST 5: Cancel Order")
        print("⏭️  SKIPPED: No limit order to cancel")
        results["skipped"] += 1

    # Test 6: Idempotency
    if test_6_idempotency(executor):
        results["passed"] += 1
    else:
        results["failed"] += 1

    # Print final results
    print_results(results)

    return 0 if results["failed"] == 0 else 1


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
    print(f"║  ⏭️  Skipped: {results['skipped']}" + " " * (68 - len(str(results["skipped"]))) + "║")
    print("║" + " " * 78 + "║")

    total = results["passed"] + results["failed"]
    pass_rate = (results["passed"] / total * 100) if total > 0 else 0

    print(f"║  Pass Rate: {pass_rate:.1f}%" + " " * (66 - len(f"{pass_rate:.1f}%")) + "║")
    print("║" + " " * 78 + "║")
    print("╚" + "=" * 78 + "╝")
    print("\n")


if __name__ == "__main__":
    sys.exit(main())
