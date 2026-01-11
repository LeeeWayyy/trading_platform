#!/usr/bin/env python3
"""
Manual integration test for P3: Signal Generator.

This script tests:
- Signal generation with real model and data
- Feature parity (uses same Alpha158 code as research)
- Portfolio weight computation
- Weight validation
- Prediction accuracy

Usage:
    python scripts/test_p3_signal_generator.py

Prerequisites:
    - P1-P2 tests passed (model registry working)
    - T1 data exists (data/adjusted/)
    - Qlib dependencies installed

See: docs/TESTING_SETUP.md for setup instructions
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import numpy as np  # noqa: E402

from apps.signal_service.model_registry import ModelRegistry  # noqa: E402
from apps.signal_service.signal_generator import SignalGenerator  # noqa: E402

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

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
    """Run P3 integration tests."""
    print_section("Testing P3: Signal Generator")

    # Configuration
    db_url = "postgresql://postgres:postgres@localhost:5432/trading_platform"
    strategy = "alpha_baseline"
    data_dir = Path("data/adjusted")
    symbols = ["AAPL", "MSFT", "GOOGL"]  # Only test symbols we have data for
    test_date = datetime(2024, 12, 31)  # Use date with available data (last date in dataset)

    # Track results
    tests_passed = 0
    tests_failed = 0

    # ========================================================================
    # Test 1: Initialize Signal Generator
    # ========================================================================
    print_section("Test 1: Initialize Signal Generator")

    try:
        # Load model first
        registry = ModelRegistry(db_url)
        registry.reload_if_changed(strategy)

        if not registry.is_loaded:
            print_error("Model not loaded (run test_p1_p2_model_registry.py first)")
            return 1

        print_success("Model loaded")

        # Initialize generator
        # Note: Using top_n=1, bottom_n=1 because we only have 3 symbols
        generator = SignalGenerator(
            model_registry=registry,
            data_dir=data_dir,
            top_n=1,
            bottom_n=1,
        )

        print_success("SignalGenerator initialized")
        print_info(f"Data directory: {data_dir}")
        print_info(f"Top N (long): {generator.top_n}")
        print_info(f"Bottom N (short): {generator.bottom_n}")
        tests_passed += 1

    except (ValueError, KeyError, RuntimeError, OSError) as e:
        print_error(f"Initialization failed: {e}")
        tests_failed += 1
        return 1

    # ========================================================================
    # Test 2: Generate Signals
    # ========================================================================
    print_section("Test 2: Generate Signals")

    try:
        print_info(f"Generating signals for {len(symbols)} symbols...")
        print_info(f"Date: {test_date.date()}")

        signals = generator.generate_signals(
            symbols=symbols,
            as_of_date=test_date,
        )

        print_success("Signals generated successfully")
        print_info(f"Signals shape: {signals.shape}")
        print_info(f"Columns: {list(signals.columns)}")

        # Display signals
        print(f"\n{signals.to_string()}")

        tests_passed += 1

    except FileNotFoundError as e:
        print_error(f"Data not found: {e}")
        print_warning(f"Check that T1 data exists for {test_date.date()}")
        print_warning(f"Expected: {data_dir}/{test_date.strftime('%Y-%m-%d')}/*.parquet")
        tests_failed += 1
        return 1

    except (ValueError, KeyError, RuntimeError, OSError) as e:
        print_error(f"Signal generation failed: {e}")
        import traceback

        traceback.print_exc()
        tests_failed += 1
        return 1

    # ========================================================================
    # Test 3: Validate Signal Structure
    # ========================================================================
    print_section("Test 3: Validate Signal Structure")

    try:
        # Check columns
        expected_columns = ["symbol", "predicted_return", "rank", "target_weight"]
        assert list(signals.columns) == expected_columns, f"Unexpected columns: {signals.columns}"
        print_success("Columns correct")

        # Check data types
        assert signals["symbol"].dtype == object, "symbol should be object"
        assert signals["predicted_return"].dtype in [
            np.float64,
            np.float32,
        ], "predicted_return should be float"
        assert signals["rank"].dtype in [np.int64, np.int32], "rank should be int"
        assert signals["target_weight"].dtype in [
            np.float64,
            np.float32,
        ], "target_weight should be float"
        print_success("Data types correct")

        # Check number of rows
        assert len(signals) == len(symbols), f"Expected {len(symbols)} signals, got {len(signals)}"
        print_success(f"Number of signals correct ({len(signals)})")

        tests_passed += 1

    except AssertionError as e:
        print_error(f"Structure validation failed: {e}")
        tests_failed += 1

    # ========================================================================
    # Test 4: Validate Portfolio Weights
    # ========================================================================
    print_section("Test 4: Validate Portfolio Weights")

    try:
        # Check position counts
        long_count = (signals["target_weight"] > 0).sum()
        short_count = (signals["target_weight"] < 0).sum()
        neutral_count = (signals["target_weight"] == 0).sum()

        print_info(f"Long positions: {long_count}")
        print_info(f"Short positions: {short_count}")
        print_info(f"Neutral positions: {neutral_count}")

        assert long_count == generator.top_n, f"Expected {generator.top_n} long, got {long_count}"
        assert (
            short_count == generator.bottom_n
        ), f"Expected {generator.bottom_n} short, got {short_count}"
        print_success("Position counts correct")

        # Check weight sums
        long_sum = signals[signals["target_weight"] > 0]["target_weight"].sum()
        short_sum = signals[signals["target_weight"] < 0]["target_weight"].sum()

        print_info(f"Long weight sum: {long_sum:.6f}")
        print_info(f"Short weight sum: {short_sum:.6f}")

        assert np.isclose(
            long_sum, 1.0, atol=1e-6
        ), f"Long weights should sum to 1.0, got {long_sum}"
        assert np.isclose(
            short_sum, -1.0, atol=1e-6
        ), f"Short weights should sum to -1.0, got {short_sum}"
        print_success("Weight sums correct")

        # Check weight bounds
        max_weight = signals["target_weight"].abs().max()
        assert max_weight <= 1.0, f"Weights should be in [-1, 1], max is {max_weight}"
        print_success("Weight bounds correct")

        tests_passed += 1

    except AssertionError as e:
        print_error(f"Weight validation failed: {e}")
        tests_failed += 1

    # ========================================================================
    # Test 5: Validate Weight Computation
    # ========================================================================
    print_section("Test 5: Validate Weight Computation")

    try:
        # Use generator's built-in validation
        is_valid = generator.validate_weights(signals)

        if is_valid:
            print_success("Weight validation passed")
        else:
            print_error("Weight validation failed")
            tests_failed += 1

        tests_passed += 1 if is_valid else 0

    except (ValueError, KeyError, RuntimeError, OSError) as e:
        print_error(f"Weight validation error: {e}")
        tests_failed += 1

    # ========================================================================
    # Test 6: Check Ranks
    # ========================================================================
    print_section("Test 6: Check Ranks")

    try:
        ranks = signals["rank"].tolist()
        unique_ranks = sorted(set(ranks))

        print_info(f"Ranks: {ranks}")
        print_info(f"Unique ranks: {unique_ranks}")

        # Check that ranks start at 1
        assert min(ranks) == 1, f"Ranks should start at 1, got {min(ranks)}"
        print_success("Ranks start at 1")

        # Check that max rank is reasonable (at most number of symbols)
        assert max(ranks) <= len(symbols), f"Max rank should be <= {len(symbols)}, got {max(ranks)}"
        print_success(f"Max rank is {max(ranks)} (within bounds)")

        # Verify rank 1 has highest predicted return
        rank_1_signals = signals[signals["rank"] == 1]
        rank_1_returns = rank_1_signals["predicted_return"].values
        max_return = signals["predicted_return"].max()

        assert all(
            np.isclose(ret, max_return) for ret in rank_1_returns
        ), "All rank 1 symbols should have highest predicted return"
        print_success(f"Rank 1 has highest return(s): {rank_1_returns[0]:.4f}")

        # Check that lower ranks have lower or equal returns
        for rank in unique_ranks[1:]:
            rank_returns = signals[signals["rank"] == rank]["predicted_return"].values
            prev_rank_returns = signals[signals["rank"] < rank]["predicted_return"].values
            assert all(
                ret <= max(prev_rank_returns) for ret in rank_returns
            ), f"Rank {rank} should have lower or equal returns than ranks above it"

        print_success("Rank ordering is correct")
        tests_passed += 1

    except AssertionError as e:
        print_error(f"Rank validation failed: {e}")
        tests_failed += 1

    # ========================================================================
    # Test 7: Predicted Returns are Reasonable
    # ========================================================================
    print_section("Test 7: Validate Predicted Returns")

    try:
        pred_returns = signals["predicted_return"]

        print_info(f"Mean: {pred_returns.mean():.4f}")
        print_info(f"Std: {pred_returns.std():.4f}")
        print_info(f"Min: {pred_returns.min():.4f}")
        print_info(f"Max: {pred_returns.max():.4f}")

        # Sanity checks (predicted returns should be reasonable)
        assert pred_returns.min() > -1.0, "Predicted return too negative (< -100%)"
        assert pred_returns.max() < 1.0, "Predicted return too high (> 100%)"
        print_success("Predicted returns are in reasonable range")

        # Check for NaN or inf
        assert not pred_returns.isna().any(), "Predicted returns contain NaN"
        assert not np.isinf(pred_returns).any(), "Predicted returns contain inf"
        print_success("No NaN or inf in predictions")

        tests_passed += 1

    except AssertionError as e:
        print_error(f"Predicted return validation failed: {e}")
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
        print(f"\n{GREEN}✓ All P1-P3 tests passed!{NC}")
        print("\nSignal generation is working correctly.")
        print("Ready to proceed with Phase 4 (FastAPI Application).")
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
    except (ValueError, KeyError, RuntimeError) as e:
        print(f"\n{RED}Test execution error: {e}{NC}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
