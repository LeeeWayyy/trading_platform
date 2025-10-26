#!/usr/bin/env python3
"""
Manual integration test for P1-P2: Model Registry.

This script tests:
- Database connection
- Model registry table access
- Model loading from file
- Hot reload mechanism
- Model metadata handling

Usage:
    python scripts/test_p1_p2_model_registry.py

Prerequisites:
    - PostgreSQL running
    - Database 'trading_platform' exists
    - Model registry table created
    - Model registered in database
    - Model file exists

See: docs/TESTING_SETUP.md for setup instructions
"""

import logging
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import numpy as np  # noqa: E402

from apps.signal_service.model_registry import ModelRegistry  # noqa: E402

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Colors for terminal output
GREEN = "\033[0;32m"
RED = "\033[0;31m"
YELLOW = "\033[1;33m"
BLUE = "\033[0;34m"
NC = "\033[0m"  # No Color


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


def print_info(message):
    """Print info message."""
    print(f"  {message}")


def main():
    """Run P1-P2 integration tests."""
    print_section("Testing P1-P2: Model Registry")

    # Configuration
    db_url = "postgresql://postgres:postgres@localhost:5432/trading_platform"
    strategy = "alpha_baseline"

    # Track test results
    tests_passed = 0
    tests_failed = 0

    # ========================================================================
    # Test 1: Initialize ModelRegistry
    # ========================================================================
    print_section("Test 1: Initialize ModelRegistry")

    try:
        registry = ModelRegistry(db_url)
        print_success("ModelRegistry initialized")
        print_info(f"Database: {db_url.split('@')[1]}")
        print_info(f"Model loaded: {registry.is_loaded}")
        print_info(f"Current metadata: {registry.current_metadata}")
        tests_passed += 1
    except Exception as e:
        print_error(f"Failed to initialize: {e}")
        tests_failed += 1
        return 1

    # ========================================================================
    # Test 2: Load Model from Database
    # ========================================================================
    print_section("Test 2: Load Model from Database")

    try:
        reloaded = registry.reload_if_changed(strategy)
        print_success(f"Model loaded: {reloaded}")

        if not registry.is_loaded:
            print_error("Model not loaded after reload_if_changed()")
            tests_failed += 1
        else:
            metadata = registry.current_metadata
            print_info(f"Strategy: {metadata.strategy_name}")
            print_info(f"Version: {metadata.version}")
            print_info(f"Status: {metadata.status}")
            print_info(f"Model path: {metadata.model_path}")
            print_info(f"Activated at: {metadata.activated_at}")

            # Check performance metrics
            if metadata.performance_metrics:
                print_info("Performance metrics:")
                for key, value in metadata.performance_metrics.items():
                    print_info(f"  - {key}: {value}")

            # Check config
            if metadata.config:
                print_info("Config:")
                for key, value in list(metadata.config.items())[:5]:  # First 5
                    print_info(f"  - {key}: {value}")

            tests_passed += 1

    except Exception as e:
        print_error(f"Failed to load model: {e}")
        tests_failed += 1

    # ========================================================================
    # Test 3: Verify Model is Usable
    # ========================================================================
    print_section("Test 3: Verify Model is Usable")

    try:
        model = registry.current_model

        if model is None:
            print_error("Model is None")
            tests_failed += 1
        else:
            # Get model info
            num_trees = model.num_trees()
            num_features = model.num_feature()

            print_success("Model is accessible")
            print_info(f"Number of trees: {num_trees}")
            print_info(f"Number of features: {num_features}")

            # Test prediction
            print_info("Testing prediction with dummy data...")
            dummy_features = np.random.randn(5, num_features)
            predictions = model.predict(dummy_features)

            print_success("Prediction successful")
            print_info(f"Input shape: {dummy_features.shape}")
            print_info(f"Output shape: {predictions.shape}")
            print_info(f"Sample predictions: {predictions[:3]}")

            tests_passed += 1

    except Exception as e:
        print_error(f"Model validation failed: {e}")
        tests_failed += 1

    # ========================================================================
    # Test 4: Hot Reload (No Change)
    # ========================================================================
    print_section("Test 4: Hot Reload (No Change)")

    try:
        old_version = registry.current_metadata.version
        reloaded = registry.reload_if_changed(strategy)

        if reloaded:
            print_error("Model reloaded when version didn't change")
            tests_failed += 1
        else:
            print_success("No reload (version unchanged)")
            print_info(f"Version: {old_version}")
            print_info(f"Reloaded: {reloaded}")
            tests_passed += 1

    except Exception as e:
        print_error(f"Hot reload test failed: {e}")
        tests_failed += 1

    # ========================================================================
    # Test 5: Model Registry Properties
    # ========================================================================
    print_section("Test 5: Model Registry Properties")

    try:
        print_success("Testing properties...")
        print_info(f"is_loaded: {registry.is_loaded}")
        print_info(f"current_model: {registry.current_model is not None}")
        print_info(f"current_metadata: {registry.current_metadata is not None}")
        print_info(f"last_check: {registry.last_check}")

        if registry.is_loaded:
            tests_passed += 1
        else:
            print_error("Model should be loaded")
            tests_failed += 1

    except Exception as e:
        print_error(f"Properties test failed: {e}")
        tests_failed += 1

    # ========================================================================
    # Test 6: Database Query Functions
    # ========================================================================
    print_section("Test 6: Database Query Functions")

    try:
        # Test get_active_model_metadata
        metadata = registry.get_active_model_metadata(strategy)
        print_success("get_active_model_metadata() works")
        print_info(f"Retrieved metadata for {metadata.strategy_name} v{metadata.version}")

        # Verify metadata structure
        assert metadata.id is not None
        assert metadata.strategy_name == strategy
        assert metadata.version is not None
        assert metadata.model_path is not None
        assert metadata.status == "active"

        print_success("Metadata structure valid")
        tests_passed += 1

    except Exception as e:
        print_error(f"Database query test failed: {e}")
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
        print(f"\n{GREEN}✓ All tests passed!{NC}")
        print("\nNext step:")
        print("  python scripts/test_p3_signal_generator.py")
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
