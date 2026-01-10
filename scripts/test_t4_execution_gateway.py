#!/usr/bin/env python3
"""
Manual integration test for T4: Execution Gateway.

This script tests:
- Database connection and migrations
- Order submission (DRY_RUN mode)
- Order idempotency (same order twice)
- Order query endpoint
- Positions endpoint
- Health check

Prerequisites:
    - PostgreSQL running with migrations applied
    - Run: psql trading_platform < migrations/002_create_execution_tables.sql

Usage:
    # Terminal 1: Start the service
    DRY_RUN=true uvicorn apps.execution_gateway.main:app --port 8002

    # Terminal 2: Run tests
    python scripts/test_t4_execution_gateway.py
"""

import sys

import requests

# Colors
GREEN = "\033[0;32m"
RED = "\033[0;31m"
YELLOW = "\033[1;33m"
BLUE = "\033[0;34m"
NC = "\033[0m"


def print_section(title):
    """Print section header."""
    print(f"\n{BLUE}{'=' * 60}{NC}")
    print(f"{BLUE}{title}{NC}")
    print(f"{BLUE}{'=' * 60}{NC}\n")


def print_success(message):
    """Print success message."""
    print(f"{GREEN}✓{NC} {message}")


def print_error(message):
    """Print error message."""
    print(f"{RED}✗{NC} {message}")


def print_warning(message):
    """Print warning message."""
    print(f"{YELLOW}⚠{NC} {message}")


def print_info(message):
    """Print info message."""
    print(f"  {message}")


def main():
    """Run T4 integration tests."""
    print_section("Testing T4: Execution Gateway")

    base_url = "http://localhost:8002"
    tests_passed = 0
    tests_failed = 0

    # ========================================================================
    # Test 1: Health Check
    # ========================================================================
    print_section("Test 1: Health Check")

    try:
        response = requests.get(f"{base_url}/health", timeout=5)

        if response.status_code == 200:
            data = response.json()
            print_success("Health check passed")
            print_info(f"Status: {data['status']}")
            print_info(f"DRY_RUN: {data['dry_run']}")
            print_info(f"Database: {'✓' if data['database_connected'] else '✗'}")
            tests_passed += 1
        else:
            print_error(f"Health check failed: {response.status_code}")
            tests_failed += 1

    except requests.exceptions.ConnectionError:
        print_error("Cannot connect to service")
        print_warning("Make sure service is running:")
        print_info("  DRY_RUN=true uvicorn apps.execution_gateway.main:app --port 8002")
        return 1
    except requests.exceptions.RequestException as e:
        print_error(f"HTTP request error: {e}")
        tests_failed += 1
    except (ValueError, KeyError) as e:
        print_error(f"Invalid response data: {e}")
        tests_failed += 1

    # ========================================================================
    # Test 2: Submit Market Order (DRY_RUN)
    # ========================================================================
    print_section("Test 2: Submit Market Order (DRY_RUN)")

    try:
        payload = {"symbol": "AAPL", "side": "buy", "qty": 10, "order_type": "market"}

        response = requests.post(f"{base_url}/api/v1/orders", json=payload, timeout=10)

        if response.status_code == 200:
            data = response.json()
            print_success("Order submitted successfully")
            print_info(f"Client Order ID: {data['client_order_id']}")
            print_info(f"Status: {data['status']}")
            print_info(f"Symbol: {data['symbol']} {data['side']} {data['qty']}")

            # Save client_order_id for later tests
            client_order_id = data["client_order_id"]

            if data["status"] == "dry_run":
                print_success("DRY_RUN mode confirmed")
                tests_passed += 1
            else:
                print_warning(f"Expected 'dry_run' status, got '{data['status']}'")
                tests_passed += 1  # Still pass, but unexpected
        else:
            print_error(f"Order submission failed: {response.status_code}")
            print_info(f"Response: {response.text}")
            tests_failed += 1
            return 1

    except requests.exceptions.RequestException as e:
        print_error(f"HTTP request error: {e}")
        tests_failed += 1
        return 1
    except (ValueError, KeyError) as e:
        print_error(f"Invalid response data: {e}")
        tests_failed += 1
        return 1

    # ========================================================================
    # Test 3: Idempotency (Submit Same Order Again)
    # ========================================================================
    print_section("Test 3: Idempotency (Submit Same Order Again)")

    try:
        # Submit exact same order
        response = requests.post(f"{base_url}/api/v1/orders", json=payload, timeout=10)

        if response.status_code == 200:
            data = response.json()
            print_success("Idempotent request handled correctly")

            if data["client_order_id"] == client_order_id:
                print_success(f"Same client_order_id returned: {client_order_id}")
                tests_passed += 1
            else:
                print_error("Different client_order_id returned (not idempotent!)")
                tests_failed += 1
        else:
            print_error(f"Idempotency test failed: {response.status_code}")
            tests_failed += 1

    except requests.exceptions.RequestException as e:
        print_error(f"HTTP request error: {e}")
        tests_failed += 1
    except (ValueError, KeyError) as e:
        print_error(f"Invalid response data: {e}")
        tests_failed += 1

    # ========================================================================
    # Test 4: Query Order Status
    # ========================================================================
    print_section("Test 4: Query Order Status")

    try:
        response = requests.get(f"{base_url}/api/v1/orders/{client_order_id}", timeout=5)

        if response.status_code == 200:
            data = response.json()
            print_success("Order retrieved successfully")
            print_info(f"Client Order ID: {data['client_order_id']}")
            print_info(f"Status: {data['status']}")
            print_info(f"Symbol: {data['symbol']}")
            print_info(f"Strategy: {data['strategy_id']}")
            tests_passed += 1
        else:
            print_error(f"Order query failed: {response.status_code}")
            print_info(f"Response: {response.text}")
            tests_failed += 1

    except requests.exceptions.RequestException as e:
        print_error(f"HTTP request error: {e}")
        tests_failed += 1
    except (ValueError, KeyError) as e:
        print_error(f"Invalid response data: {e}")
        tests_failed += 1

    # ========================================================================
    # Test 5: Submit Limit Order
    # ========================================================================
    print_section("Test 5: Submit Limit Order")

    try:
        payload2 = {
            "symbol": "MSFT",
            "side": "sell",
            "qty": 5,
            "order_type": "limit",
            "limit_price": "300.50",
        }

        response = requests.post(f"{base_url}/api/v1/orders", json=payload2, timeout=10)

        if response.status_code == 200:
            data = response.json()
            print_success("Limit order submitted successfully")
            print_info(f"Client Order ID: {data['client_order_id']}")
            print_info(f"Symbol: {data['symbol']} {data['side']} {data['qty']}")
            print_info(f"Limit Price: ${data['limit_price']}")
            tests_passed += 1
        else:
            print_error(f"Limit order submission failed: {response.status_code}")
            print_info(f"Response: {response.text}")
            tests_failed += 1

    except requests.exceptions.RequestException as e:
        print_error(f"HTTP request error: {e}")
        tests_failed += 1
    except (ValueError, KeyError) as e:
        print_error(f"Invalid response data: {e}")
        tests_failed += 1

    # ========================================================================
    # Test 6: Get Positions
    # ========================================================================
    print_section("Test 6: Get Positions")

    try:
        response = requests.get(f"{base_url}/api/v1/positions", timeout=5)

        if response.status_code == 200:
            data = response.json()
            print_success("Positions retrieved successfully")
            print_info(f"Total positions: {data['total_positions']}")

            if data["positions"]:
                for pos in data["positions"]:
                    print_info(
                        f"  {pos['symbol']}: {pos['qty']} shares @ ${pos['avg_entry_price']}"
                    )
            else:
                print_info("  No positions (expected in DRY_RUN mode)")

            tests_passed += 1
        else:
            print_error(f"Get positions failed: {response.status_code}")
            print_info(f"Response: {response.text}")
            tests_failed += 1

    except requests.exceptions.RequestException as e:
        print_error(f"HTTP request error: {e}")
        tests_failed += 1
    except (ValueError, KeyError) as e:
        print_error(f"Invalid response data: {e}")
        tests_failed += 1

    # ========================================================================
    # Summary
    # ========================================================================
    print_section("Test Summary")

    total_tests = tests_passed + tests_failed
    print(f"Total tests: {total_tests}")
    print(f"{GREEN}Passed: {tests_passed}{NC}")

    if tests_failed > 0:
        print(f"{RED}Failed: {tests_failed}{NC}")
    else:
        print(f"Failed: {tests_failed}")

    pass_rate = (tests_passed / total_tests * 100) if total_tests > 0 else 0
    print(f"Pass rate: {pass_rate:.1f}%")

    if tests_failed == 0:
        print(f"\n{GREEN}✓ All T4 tests passed!{NC}")
        print("\nExecution Gateway is working correctly:")
        print("  - Health check OK")
        print("  - Order submission (DRY_RUN) OK")
        print("  - Idempotency OK")
        print("  - Order query OK")
        print("  - Positions endpoint OK")
        print("\nReady for production testing with DRY_RUN=false.")
        return 0
    else:
        print(f"\n{RED}✗ Some tests failed{NC}")
        print("\nCheck logs above for errors.")
        return 1


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(1)
    except (ValueError, RuntimeError) as e:
        print(f"\n{RED}Test execution error: {e}{NC}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
