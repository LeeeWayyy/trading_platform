#!/usr/bin/env python3
"""
Manual integration test for P4: FastAPI Application.

This script tests:
- FastAPI application startup
- Health check endpoint
- Model info endpoint
- Signal generation endpoint
- Error handling

Usage:
    # Terminal 1: Start the service
    python -m uvicorn apps.signal_service.main:app --host 0.0.0.0 --port 8001

    # Terminal 2: Run tests
    python scripts/test_p4_fastapi.py

Prerequisites:
    - P1-P3 tests passed
    - PostgreSQL running
    - Model registered
    - T1 data available

See: docs/TESTING_SETUP.md for setup instructions
"""

import sys
from pathlib import Path
import time
import requests
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Colors
GREEN = '\033[0;32m'
RED = '\033[0;31m'
YELLOW = '\033[1;33m'
BLUE = '\033[0;34m'
NC = '\033[0m'


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
    """Run P4 integration tests."""
    print_section("Testing P4: FastAPI Application")

    # Configuration
    base_url = "http://localhost:8001"
    test_symbols = ["AAPL", "MSFT", "GOOGL"]
    test_date = "2024-12-31"

    # Track results
    tests_passed = 0
    tests_failed = 0

    # ========================================================================
    # Test 0: Check Service is Running
    # ========================================================================
    print_section("Test 0: Check Service is Running")

    try:
        response = requests.get(f"{base_url}/", timeout=5)
        if response.status_code == 200:
            print_success("Service is running")
            data = response.json()
            print_info(f"Service: {data.get('service')}")
            print_info(f"Version: {data.get('version')}")
            tests_passed += 1
        else:
            print_error(f"Service returned status {response.status_code}")
            tests_failed += 1
            return 1

    except requests.exceptions.ConnectionError:
        print_error("Cannot connect to service")
        print_warning("Make sure service is running:")
        print_info("  python -m uvicorn apps.signal_service.main:app --host 0.0.0.0 --port 8001")
        return 1
    except Exception as e:
        print_error(f"Error: {e}")
        tests_failed += 1
        return 1

    # ========================================================================
    # Test 1: Health Check Endpoint
    # ========================================================================
    print_section("Test 1: Health Check Endpoint")

    try:
        response = requests.get(f"{base_url}/health", timeout=5)

        if response.status_code == 200:
            print_success("Health check passed")
            data = response.json()

            # Validate structure
            assert "status" in data, "Missing 'status' field"
            assert "model_loaded" in data, "Missing 'model_loaded' field"
            assert "timestamp" in data, "Missing 'timestamp' field"

            print_info(f"Status: {data['status']}")
            print_info(f"Model loaded: {data['model_loaded']}")

            if data.get('model_info'):
                print_info(f"Model: {data['model_info'].get('strategy')} {data['model_info'].get('version')}")

            tests_passed += 1
        else:
            print_error(f"Health check failed with status {response.status_code}")
            print_info(f"Response: {response.text}")
            tests_failed += 1

    except Exception as e:
        print_error(f"Health check error: {e}")
        tests_failed += 1

    # ========================================================================
    # Test 2: Model Info Endpoint
    # ========================================================================
    print_section("Test 2: Model Info Endpoint")

    try:
        response = requests.get(f"{base_url}/api/v1/model/info", timeout=5)

        if response.status_code == 200:
            print_success("Model info retrieved")
            data = response.json()

            print_info(f"Strategy: {data.get('strategy_name')}")
            print_info(f"Version: {data.get('version')}")
            print_info(f"Status: {data.get('status')}")

            if data.get('performance_metrics'):
                print_info("Performance metrics:")
                for key, value in list(data['performance_metrics'].items())[:3]:
                    print_info(f"  - {key}: {value}")

            tests_passed += 1
        else:
            print_error(f"Model info failed with status {response.status_code}")
            print_info(f"Response: {response.text}")
            tests_failed += 1

    except Exception as e:
        print_error(f"Model info error: {e}")
        tests_failed += 1

    # ========================================================================
    # Test 3: Generate Signals (Basic)
    # ========================================================================
    print_section("Test 3: Generate Signals (Basic)")

    try:
        payload = {
            "symbols": test_symbols,
            "as_of_date": test_date,
            "top_n": 1,  # Use 1 long + 1 short for 3 symbols
            "bottom_n": 1,
        }

        response = requests.post(
            f"{base_url}/api/v1/signals/generate",
            json=payload,
            timeout=30,
        )

        if response.status_code == 200:
            print_success("Signals generated successfully")
            data = response.json()

            # Validate structure
            assert "signals" in data, "Missing 'signals' field"
            assert "metadata" in data, "Missing 'metadata' field"

            signals = data["signals"]
            metadata = data["metadata"]

            print_info(f"Number of signals: {len(signals)}")
            print_info(f"Model version: {metadata.get('model_version')}")
            print_info(f"As of date: {metadata.get('as_of_date')}")

            # Display first few signals
            print(f"\nFirst 3 signals:")
            for signal in signals[:3]:
                print(f"  {signal['symbol']}: "
                      f"return={signal['predicted_return']:.4f}, "
                      f"rank={signal['rank']}, "
                      f"weight={signal['target_weight']:.4f}")

            tests_passed += 1
        else:
            print_error(f"Signal generation failed with status {response.status_code}")
            print_info(f"Response: {response.text}")
            tests_failed += 1

    except Exception as e:
        print_error(f"Signal generation error: {e}")
        import traceback
        traceback.print_exc()
        tests_failed += 1

    # ========================================================================
    # Test 4: Generate Signals (With Overrides)
    # ========================================================================
    print_section("Test 4: Generate Signals (With top_n/bottom_n Override)")

    try:
        payload = {
            "symbols": test_symbols,
            "as_of_date": test_date,
            "top_n": 1,
            "bottom_n": 1,
        }

        response = requests.post(
            f"{base_url}/api/v1/signals/generate",
            json=payload,
            timeout=30,
        )

        if response.status_code == 200:
            print_success("Signals with overrides generated")
            data = response.json()

            signals = data["signals"]
            metadata = data["metadata"]

            # Check overrides were applied
            assert metadata["top_n"] == 1, f"Expected top_n=1, got {metadata['top_n']}"
            assert metadata["bottom_n"] == 1, f"Expected bottom_n=1, got {metadata['bottom_n']}"

            print_info(f"Top N: {metadata['top_n']}")
            print_info(f"Bottom N: {metadata['bottom_n']}")

            # Count positions
            long_count = sum(1 for s in signals if s["target_weight"] > 0)
            short_count = sum(1 for s in signals if s["target_weight"] < 0)

            print_info(f"Long positions: {long_count}")
            print_info(f"Short positions: {short_count}")

            assert long_count == 1, f"Expected 1 long, got {long_count}"
            assert short_count == 1, f"Expected 1 short, got {short_count}"

            print_success("Overrides applied correctly")
            tests_passed += 1
        else:
            print_error(f"Signal generation with overrides failed: {response.status_code}")
            print_info(f"Response: {response.text}")
            tests_failed += 1

    except AssertionError as e:
        print_error(f"Override validation failed: {e}")
        tests_failed += 1
    except Exception as e:
        print_error(f"Signal generation with overrides error: {e}")
        tests_failed += 1

    # ========================================================================
    # Test 5: Error Handling (Invalid Date)
    # ========================================================================
    print_section("Test 5: Error Handling (Invalid Date)")

    try:
        payload = {
            "symbols": test_symbols,
            "as_of_date": "invalid-date",
        }

        response = requests.post(
            f"{base_url}/api/v1/signals/generate",
            json=payload,
            timeout=5,
        )

        if response.status_code == 422:  # Validation error
            print_success("Invalid date rejected (422 Validation Error)")
            tests_passed += 1
        elif response.status_code == 400:  # Bad request
            print_success("Invalid date rejected (400 Bad Request)")
            tests_passed += 1
        else:
            print_error(f"Expected 4xx error, got {response.status_code}")
            tests_failed += 1

    except Exception as e:
        print_error(f"Error handling test failed: {e}")
        tests_failed += 1

    # ========================================================================
    # Test 6: Error Handling (Invalid Symbols)
    # ========================================================================
    print_section("Test 6: Error Handling (Empty Symbols)")

    try:
        payload = {
            "symbols": [],  # Empty list
            "as_of_date": test_date,
        }

        response = requests.post(
            f"{base_url}/api/v1/signals/generate",
            json=payload,
            timeout=5,
        )

        if response.status_code == 422:  # Validation error
            print_success("Empty symbols rejected (422 Validation Error)")
            tests_passed += 1
        else:
            print_error(f"Expected 422 error, got {response.status_code}")
            tests_failed += 1

    except Exception as e:
        print_error(f"Error handling test failed: {e}")
        tests_failed += 1

    # ========================================================================
    # Test 7: Error Handling (Too Many Positions)
    # ========================================================================
    print_section("Test 7: Error Handling (Invalid top_n/bottom_n)")

    try:
        payload = {
            "symbols": ["AAPL"],  # Only 1 symbol
            "top_n": 5,  # Can't select 5 from 1
            "bottom_n": 5,
        }

        response = requests.post(
            f"{base_url}/api/v1/signals/generate",
            json=payload,
            timeout=5,
        )

        if response.status_code == 400:  # Bad request
            print_success("Invalid parameters rejected (400 Bad Request)")
            data = response.json()
            print_info(f"Error: {data.get('detail')}")
            tests_passed += 1
        else:
            print_error(f"Expected 400 error, got {response.status_code}")
            tests_failed += 1

    except Exception as e:
        print_error(f"Error handling test failed: {e}")
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
        print(f"\n{GREEN}✓ All P4 tests passed!{NC}")
        print("\nFastAPI application is working correctly.")
        print("Ready to proceed with Phase 5 (Hot Reload).")
        return 0
    else:
        print(f"\n{RED}✗ Some tests failed{NC}")
        print("\nCheck logs above for errors.")
        print("See docs/TESTING_SETUP.md for troubleshooting.")
        return 1


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n{RED}Unexpected error: {e}{NC}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
