"""
Tests for TWAP order slicer.

Validates quantity distribution, remainder handling, scheduled time calculation,
deterministic ID generation, and error handling.

Test Coverage:
    - Standard TWAP slicing (even distribution)
    - Remainder distribution (front-loaded)
    - Edge cases (single slice, qty == num_slices)
    - Scheduled time accuracy
    - Deterministic client_order_id generation
    - Price/TIF preservation
    - Validation errors (qty, duration, missing prices)
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from apps.execution_gateway.order_slicer import TWAPSlicer


class TestTWAPSlicer:
    """Test suite for TWAPSlicer class."""

    def test_standard_twap_even_distribution(self) -> None:
        """
        Standard TWAP: 100 shares over 5 minutes → 5 slices of 20 each.

        Validates:
            - Total quantity preserved
            - Even distribution (no remainder)
            - Correct number of slices
            - All slices have expected quantity
        """
        slicer = TWAPSlicer()
        plan = slicer.plan(
            symbol="AAPL",
            side="buy",
            qty=100,
            duration_minutes=5,
            order_type="market",
        )

        assert plan.symbol == "AAPL"
        assert plan.side == "buy"
        assert plan.total_qty == 100
        assert plan.total_slices == 5
        assert plan.duration_minutes == 5
        assert plan.interval_seconds == 60
        assert len(plan.slices) == 5

        # All slices should have equal quantity (no remainder)
        for slice_detail in plan.slices:
            assert slice_detail.qty == 20

        # Verify total quantity preserved
        total_qty = sum(s.qty for s in plan.slices)
        assert total_qty == 100

    def test_remainder_distribution_front_loaded(self) -> None:
        """
        Remainder distribution: 103 shares over 5 minutes → [21, 21, 21, 20, 20].

        Validates:
            - Front-loaded remainder (first slices get +1)
            - Total quantity preserved
            - Correct distribution pattern
        """
        slicer = TWAPSlicer()
        plan = slicer.plan(
            symbol="AAPL",
            side="sell",
            qty=103,
            duration_minutes=5,
            order_type="market",
        )

        assert plan.total_qty == 103
        assert plan.total_slices == 5
        assert plan.interval_seconds == 60

        # Expected: base_qty = 20, remainder = 3
        # First 3 slices get +1 (front-loaded)
        expected_qtys = [21, 21, 21, 20, 20]
        actual_qtys = [s.qty for s in plan.slices]
        assert actual_qtys == expected_qtys

        # Verify total
        assert sum(actual_qtys) == 103

    def test_large_remainder_distribution(self) -> None:
        """
        Large remainder: 109 shares over 5 minutes → [22, 22, 22, 22, 21].

        Validates:
            - Front-loaded remainder with 4 slices getting +1
            - Total quantity preserved
        """
        slicer = TWAPSlicer()
        plan = slicer.plan(
            symbol="TSLA",
            side="buy",
            qty=109,
            duration_minutes=5,
            order_type="market",
        )

        # Expected: base_qty = 21, remainder = 4
        # First 4 slices get +1
        expected_qtys = [22, 22, 22, 22, 21]
        actual_qtys = [s.qty for s in plan.slices]
        assert actual_qtys == expected_qtys
        assert sum(actual_qtys) == 109
        assert plan.interval_seconds == 60

    def test_qty_equals_num_slices(self) -> None:
        """
        Edge case: qty == num_slices → 5 shares over 5 minutes → [1, 1, 1, 1, 1].

        Validates:
            - Minimum valid case (qty == duration)
            - Each slice gets exactly 1 share
        """
        slicer = TWAPSlicer()
        plan = slicer.plan(
            symbol="GOOG",
            side="buy",
            qty=5,
            duration_minutes=5,
            order_type="market",
        )

        expected_qtys = [1, 1, 1, 1, 1]
        actual_qtys = [s.qty for s in plan.slices]
        assert actual_qtys == expected_qtys
        assert sum(actual_qtys) == 5
        assert plan.interval_seconds == 60

    def test_single_slice(self) -> None:
        """
        Single slice: 100 shares over 1 minute → [100].

        Validates:
            - duration_minutes=1 creates single slice
            - All quantity in one slice
        """
        slicer = TWAPSlicer()
        plan = slicer.plan(
            symbol="MSFT",
            side="sell",
            qty=100,
            duration_minutes=1,
            order_type="market",
        )

        assert plan.total_slices == 1
        assert len(plan.slices) == 1
        assert plan.slices[0].qty == 100
        assert plan.slices[0].slice_num == 0
        assert plan.interval_seconds == 60

    def test_scheduled_time_calculation(self) -> None:
        """
        Scheduled time accuracy: Times should be exactly 1 minute apart.

        Validates:
            - First slice scheduled immediately (now)
            - Each subsequent slice scheduled +1 minute
            - Time deltas are accurate
        """
        slicer = TWAPSlicer()
        before = datetime.now(UTC)
        plan = slicer.plan(
            symbol="AAPL",
            side="buy",
            qty=50,
            duration_minutes=3,
            order_type="market",
        )
        after = datetime.now(UTC)

        # First slice should be scheduled close to now (within test execution time)
        first_scheduled = plan.slices[0].scheduled_time
        assert before <= first_scheduled <= after + timedelta(seconds=1)

        # Each subsequent slice should be spaced by interval_seconds (default 60s)
        for i in range(1, len(plan.slices)):
            prev_time = plan.slices[i - 1].scheduled_time
            curr_time = plan.slices[i].scheduled_time
            delta = curr_time - prev_time
            assert delta == timedelta(seconds=plan.interval_seconds)

        # Verify slice_num matches index
        for i, slice_detail in enumerate(plan.slices):
            assert slice_detail.slice_num == i

    def test_custom_interval_spacing(self) -> None:
        """Custom interval produces expected slice count and schedule."""

        slicer = TWAPSlicer()
        duration_minutes = 60
        interval_seconds = 360  # 6 minutes
        qty = 1000

        before = datetime.now(UTC)
        plan = slicer.plan(
            symbol="AAPL",
            side="buy",
            qty=qty,
            duration_minutes=duration_minutes,
            interval_seconds=interval_seconds,
            order_type="market",
        )
        after = datetime.now(UTC)

        assert plan.total_slices == 10
        assert plan.interval_seconds == interval_seconds
        assert len(plan.slices) == 10
        assert sum(slice_detail.qty for slice_detail in plan.slices) == qty

        expected_delta = timedelta(seconds=interval_seconds)
        first_time = plan.slices[0].scheduled_time
        assert before <= first_time <= after + timedelta(seconds=1)

        for idx in range(1, len(plan.slices)):
            delta = plan.slices[idx].scheduled_time - plan.slices[idx - 1].scheduled_time
            assert delta == expected_delta

        # Ensure deterministic slice_num ordering
        for idx, slice_detail in enumerate(plan.slices):
            assert slice_detail.slice_num == idx

    def test_deterministic_client_order_id(self) -> None:
        """
        Deterministic IDs: Same inputs → same IDs.

        Validates:
            - Parent order ID is deterministic
            - Child order IDs are deterministic
            - Same inputs produce same IDs
            - Different inputs produce different IDs
        """
        slicer = TWAPSlicer()

        # Generate plan twice with identical inputs
        plan1 = slicer.plan(
            symbol="AAPL",
            side="buy",
            qty=100,
            duration_minutes=5,
            order_type="market",
        )
        plan2 = slicer.plan(
            symbol="AAPL",
            side="buy",
            qty=100,
            duration_minutes=5,
            order_type="market",
        )

        # Parent order IDs should match
        assert plan1.parent_order_id == plan2.parent_order_id

        # Child order IDs should match (same indices)
        for i in range(len(plan1.slices)):
            assert plan1.slices[i].client_order_id == plan2.slices[i].client_order_id

        # Different qty should produce different parent ID
        plan3 = slicer.plan(
            symbol="AAPL",
            side="buy",
            qty=101,  # Different
            duration_minutes=5,
            order_type="market",
        )
        assert plan3.parent_order_id != plan1.parent_order_id

    def test_client_order_id_format(self) -> None:
        """
        Client order ID format: Should be 24-character hex string.

        Validates:
            - Parent order ID length
            - Child order IDs length
            - Valid hex format
        """
        slicer = TWAPSlicer()
        plan = slicer.plan(
            symbol="AAPL",
            side="buy",
            qty=100,
            duration_minutes=3,
            order_type="market",
        )

        # Parent ID should be 24-char hex
        assert len(plan.parent_order_id) == 24
        assert all(c in "0123456789abcdef" for c in plan.parent_order_id)

        # All child IDs should be 24-char hex
        for slice_detail in plan.slices:
            assert len(slice_detail.client_order_id) == 24
            assert all(c in "0123456789abcdef" for c in slice_detail.client_order_id)

    def test_limit_order_price_preservation(self) -> None:
        """
        Limit order: limit_price should be preserved in slicing plan.

        Validates:
            - Limit price doesn't affect quantity distribution
            - Plan metadata captures limit price
        """
        slicer = TWAPSlicer()
        plan = slicer.plan(
            symbol="AAPL",
            side="buy",
            qty=100,
            duration_minutes=5,
            order_type="limit",
            limit_price=Decimal("150.50"),
        )

        # Quantity distribution unaffected by price
        assert plan.total_qty == 100
        assert len(plan.slices) == 5

        # Plan should preserve original parameters (no limit_price field in SlicingPlan,
        # but it's used for ID generation, so changing it changes IDs)
        plan2 = slicer.plan(
            symbol="AAPL",
            side="buy",
            qty=100,
            duration_minutes=5,
            order_type="limit",
            limit_price=Decimal("151.00"),  # Different price
        )
        # Different limit_price should produce different IDs
        assert plan.parent_order_id != plan2.parent_order_id

    def test_stop_order_price_preservation(self) -> None:
        """
        Stop order: stop_price should be preserved in ID generation.

        Validates:
            - Stop price affects ID generation
            - Quantity distribution unaffected
        """
        slicer = TWAPSlicer()
        plan1 = slicer.plan(
            symbol="TSLA",
            side="sell",
            qty=50,
            duration_minutes=3,
            order_type="stop",
            stop_price=Decimal("200.00"),
        )
        plan2 = slicer.plan(
            symbol="TSLA",
            side="sell",
            qty=50,
            duration_minutes=3,
            order_type="stop",
            stop_price=Decimal("201.00"),  # Different
        )

        # Different stop_price → different IDs
        assert plan1.parent_order_id != plan2.parent_order_id
        assert plan1.slices[0].client_order_id != plan2.slices[0].client_order_id

    def test_initial_slice_status(self) -> None:
        """
        Initial status: All slices should start with status="pending_new".

        Validates:
            - All slices initialized to pending_new
            - No slices are pre-executed
        """
        slicer = TWAPSlicer()
        plan = slicer.plan(
            symbol="AAPL",
            side="buy",
            qty=100,
            duration_minutes=5,
            order_type="market",
        )

        for slice_detail in plan.slices:
            assert slice_detail.status == "pending_new"

    def test_explicit_trade_date_for_idempotency(self) -> None:
        """
        Explicit trade_date: Ensures idempotency across midnight UTC.

        Validates:
            - Same trade_date → same IDs (even on different days)
            - Different trade_date → different IDs
            - Prevents duplicate orders from midnight retry
        """
        from datetime import date

        slicer = TWAPSlicer()
        trade_date_today = date(2025, 10, 26)

        # Generate plan with explicit trade_date
        plan1 = slicer.plan(
            symbol="AAPL",
            side="buy",
            qty=100,
            duration_minutes=3,
            order_type="market",
            trade_date=trade_date_today,
        )

        # Retry with same trade_date (simulates cross-midnight retry)
        plan2 = slicer.plan(
            symbol="AAPL",
            side="buy",
            qty=100,
            duration_minutes=3,
            order_type="market",
            trade_date=trade_date_today,  # Same date
        )

        # Should produce identical IDs (idempotent)
        assert plan1.parent_order_id == plan2.parent_order_id
        for i in range(len(plan1.slices)):
            assert plan1.slices[i].client_order_id == plan2.slices[i].client_order_id

        # Different trade_date should produce different IDs
        plan3 = slicer.plan(
            symbol="AAPL",
            side="buy",
            qty=100,
            duration_minutes=3,
            order_type="market",
            trade_date=date(2025, 10, 27),  # Next day
        )
        assert plan3.parent_order_id != plan1.parent_order_id

    # Error cases

    def test_error_qty_less_than_num_slices(self) -> None:
        """
        Error: qty < required_slices → ValueError.

        Validates:
            - Cannot create zero-qty slices
            - Clear error message
        """
        slicer = TWAPSlicer()
        with pytest.raises(ValueError, match="number of slices"):
            slicer.plan(
                symbol="AAPL",
                side="buy",
                qty=3,  # Too small
                duration_minutes=5,
                order_type="market",
            )

    def test_error_qty_less_than_required_slices_custom_interval(self) -> None:
        """Error: qty smaller than computed slices when using custom interval."""

        slicer = TWAPSlicer()
        with pytest.raises(ValueError, match="number of slices"):
            slicer.plan(
                symbol="AAPL",
                side="buy",
                qty=2,
                duration_minutes=10,
                interval_seconds=120,
                order_type="market",
            )

    def test_error_zero_qty(self) -> None:
        """
        Error: qty=0 → ValueError.

        Validates:
            - Zero quantity rejected
            - Clear error message
        """
        slicer = TWAPSlicer()
        with pytest.raises(ValueError, match="qty must be at least 1"):
            slicer.plan(
                symbol="AAPL",
                side="buy",
                qty=0,
                duration_minutes=5,
                order_type="market",
            )

    def test_error_negative_qty(self) -> None:
        """
        Error: qty < 0 → ValueError.

        Validates:
            - Negative quantity rejected
        """
        slicer = TWAPSlicer()
        with pytest.raises(ValueError, match="qty must be at least 1"):
            slicer.plan(
                symbol="AAPL",
                side="buy",
                qty=-10,
                duration_minutes=5,
                order_type="market",
            )

    def test_error_zero_duration(self) -> None:
        """
        Error: duration_minutes=0 → ValueError.

        Validates:
            - Zero duration rejected
        """
        slicer = TWAPSlicer()
        with pytest.raises(ValueError, match="duration_minutes must be at least 1"):
            slicer.plan(
                symbol="AAPL",
                side="buy",
                qty=100,
                duration_minutes=0,
                order_type="market",
            )

    def test_error_invalid_interval(self) -> None:
        """Error: interval_seconds must be positive."""

        slicer = TWAPSlicer()
        with pytest.raises(ValueError, match="interval_seconds must be at least 1"):
            slicer.plan(
                symbol="AAPL",
                side="buy",
                qty=100,
                duration_minutes=5,
                interval_seconds=0,
                order_type="market",
            )

    def test_error_negative_duration(self) -> None:
        """
        Error: duration_minutes < 0 → ValueError.

        Validates:
            - Negative duration rejected
        """
        slicer = TWAPSlicer()
        with pytest.raises(ValueError, match="duration_minutes must be at least 1"):
            slicer.plan(
                symbol="AAPL",
                side="buy",
                qty=100,
                duration_minutes=-5,
                order_type="market",
            )

    def test_error_limit_order_missing_limit_price(self) -> None:
        """
        Error: limit order without limit_price → ValueError.

        Validates:
            - limit_price required for limit orders
            - Clear error message
        """
        slicer = TWAPSlicer()
        with pytest.raises(ValueError, match="limit orders require limit_price"):
            slicer.plan(
                symbol="AAPL",
                side="buy",
                qty=100,
                duration_minutes=5,
                order_type="limit",
                limit_price=None,  # Missing
            )

    def test_error_stop_order_missing_stop_price(self) -> None:
        """
        Error: stop order without stop_price → ValueError.

        Validates:
            - stop_price required for stop orders
        """
        slicer = TWAPSlicer()
        with pytest.raises(ValueError, match="stop orders require stop_price"):
            slicer.plan(
                symbol="TSLA",
                side="sell",
                qty=50,
                duration_minutes=3,
                order_type="stop",
                stop_price=None,  # Missing
            )

    def test_error_stop_limit_order_missing_prices(self) -> None:
        """
        Error: stop_limit order without both prices → ValueError.

        Validates:
            - Both limit_price and stop_price required for stop_limit
        """
        slicer = TWAPSlicer()

        # Missing limit_price
        with pytest.raises(ValueError, match="stop_limit orders require limit_price"):
            slicer.plan(
                symbol="AAPL",
                side="buy",
                qty=100,
                duration_minutes=5,
                order_type="stop_limit",
                limit_price=None,  # Missing
                stop_price=Decimal("150.00"),
            )

        # Missing stop_price
        with pytest.raises(ValueError, match="stop_limit orders require stop_price"):
            slicer.plan(
                symbol="AAPL",
                side="buy",
                qty=100,
                duration_minutes=5,
                order_type="stop_limit",
                limit_price=Decimal("150.50"),
                stop_price=None,  # Missing
            )


class TestCrossMidnightIdempotency:
    """
    Test cross-midnight idempotency for TWAP order IDs.

    Regression test for P1 bug: Idempotent retries fail after midnight.

    Without explicit trade_date, a client submitting the same TWAP order
    before and after midnight would get different parent_order_ids, creating
    duplicate orders.
    """

    def test_same_trade_date_produces_identical_parent_ids(self):
        """Explicit trade_date ensures same parent_order_id across midnight."""
        slicer = TWAPSlicer()
        monday = date(2025, 10, 27)

        # Submit same order on Monday
        plan_monday = slicer.plan(
            symbol="AAPL",
            side="buy",
            qty=100,
            duration_minutes=5,
            order_type="market",
            trade_date=monday,
        )

        # Retry same order on Tuesday with same trade_date
        plan_tuesday = slicer.plan(
            symbol="AAPL",
            side="buy",
            qty=100,
            duration_minutes=5,
            order_type="market",
            trade_date=monday,  # Same trade_date as Monday submission
        )

        # Parent IDs must match for idempotency
        assert plan_monday.parent_order_id == plan_tuesday.parent_order_id, (
            "Retrying same TWAP order after midnight with same trade_date "
            "must produce identical parent_order_id for idempotency"
        )

    def test_different_trade_dates_produce_different_parent_ids(self):
        """Different trade_dates produce different parent_order_ids (expected)."""
        slicer = TWAPSlicer()
        monday = date(2025, 10, 27)
        tuesday = date(2025, 10, 28)

        # Submit on Monday
        plan_monday = slicer.plan(
            symbol="AAPL",
            side="buy",
            qty=100,
            duration_minutes=5,
            order_type="market",
            trade_date=monday,
        )

        # Submit on Tuesday (NEW order for Tuesday's trading day)
        plan_tuesday = slicer.plan(
            symbol="AAPL",
            side="buy",
            qty=100,
            duration_minutes=5,
            order_type="market",
            trade_date=tuesday,
        )

        # Parent IDs must differ (different trading days)
        assert plan_monday.parent_order_id != plan_tuesday.parent_order_id, (
            "Same TWAP order on different trade_dates must produce "
            "different parent_order_ids (different trading days)"
        )

    def test_child_slice_ids_stable_across_trade_date(self):
        """Child slice IDs remain stable when using same trade_date."""
        slicer = TWAPSlicer()
        monday = date(2025, 10, 27)

        # Generate slices twice with same trade_date
        plan1 = slicer.plan(
            symbol="AAPL",
            side="buy",
            qty=100,
            duration_minutes=5,
            order_type="market",
            trade_date=monday,
        )

        plan2 = slicer.plan(
            symbol="AAPL",
            side="buy",
            qty=100,
            duration_minutes=5,
            order_type="market",
            trade_date=monday,
        )

        # All child slice IDs must match
        for slice1, slice2 in zip(plan1.slices, plan2.slices, strict=False):
            assert slice1.client_order_id == slice2.client_order_id, (
                f"Slice {slice1.slice_num} client_order_id mismatch: "
                f"{slice1.client_order_id} != {slice2.client_order_id}"
            )
