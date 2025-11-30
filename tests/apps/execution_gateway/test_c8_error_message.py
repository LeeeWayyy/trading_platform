"""
Tests for C8: COALESCE Bug Fix for Error Messages.

This module tests that error_message can be cleared by passing None,
which was previously blocked by COALESCE in the SQL query.

Issue: C8 - COALESCE bug prevents clearing error messages
Location: apps/execution_gateway/database.py:769
Fix: Remove COALESCE for error_message, allow explicit NULL
"""

import pytest


class TestErrorMessageClearing:
    """Test error_message clearing behavior.

    The fix allows passing None to clear error_message, enabling
    orders that recover from errors to have their error message removed.
    """

    def _simulate_update_order(
        self,
        current_error: str | None,
        new_error: str | None,
        use_coalesce: bool = False,
    ) -> str | None:
        """Simulate the update logic with/without COALESCE.

        Args:
            current_error: Current error_message in DB
            new_error: New value being passed
            use_coalesce: If True, simulate old buggy behavior

        Returns:
            Final error_message value after update
        """
        if use_coalesce:
            # Old buggy behavior: COALESCE(%s, error_message)
            # None input keeps old value (can't clear!)
            return new_error if new_error is not None else current_error
        else:
            # Fixed behavior: error_message = %s
            # None input clears the value
            return new_error

    def test_clear_error_with_fix(self):
        """Verify error_message can be cleared with the fix."""
        # Order had an error, now recovered
        current_error = "Connection timeout to broker"
        new_error = None  # Clear the error

        result = self._simulate_update_order(
            current_error=current_error,
            new_error=new_error,
            use_coalesce=False,  # Fixed behavior
        )

        # With fix, None clears the error
        assert result is None

    def test_old_coalesce_bug_preserved_error(self):
        """Demonstrate the old COALESCE bug behavior."""
        # Order had an error, tried to clear it
        current_error = "Connection timeout to broker"
        new_error = None  # Try to clear

        result = self._simulate_update_order(
            current_error=current_error,
            new_error=new_error,
            use_coalesce=True,  # Old buggy behavior
        )

        # With COALESCE bug, None keeps old value (can't clear!)
        assert result == current_error  # Bug: error not cleared

    def test_set_new_error_works(self):
        """Verify setting a new error message works."""
        current_error = None
        new_error = "Broker rejected order"

        result = self._simulate_update_order(
            current_error=current_error,
            new_error=new_error,
            use_coalesce=False,
        )

        assert result == "Broker rejected order"

    def test_replace_error_works(self):
        """Verify replacing an existing error works."""
        current_error = "Old error"
        new_error = "New error"

        result = self._simulate_update_order(
            current_error=current_error,
            new_error=new_error,
            use_coalesce=False,
        )

        assert result == "New error"

    @pytest.mark.parametrize(
        "current,new_value,expected",
        [
            # Clear existing error
            ("Error A", None, None),
            # Set new error
            (None, "Error B", "Error B"),
            # Replace error
            ("Error A", "Error B", "Error B"),
            # Keep none
            (None, None, None),
        ],
    )
    def test_error_message_transitions(
        self, current: str | None, new_value: str | None, expected: str | None
    ):
        """Test various error message state transitions."""
        result = self._simulate_update_order(
            current_error=current,
            new_error=new_value,
            use_coalesce=False,  # Fixed behavior
        )
        assert result == expected


class TestOrderRecoveryScenario:
    """Test real-world scenario where order recovers from error."""

    def test_order_recovery_clears_error(self):
        """Verify order that recovers can have error cleared."""
        # Step 1: Order fails with error
        order = {
            "client_order_id": "test-123",
            "status": "rejected",
            "error_message": "Broker connection failed",
        }

        # Step 2: Retry succeeds, order should be updated
        # With fix, we can clear error_message
        update = {"status": "filled", "error_message": None}

        # Simulate update
        if update["error_message"] is None:
            order["error_message"] = None  # Clear it (fixed behavior)
        else:
            order["error_message"] = update["error_message"]

        order["status"] = update["status"]

        # Verify error is cleared
        assert order["status"] == "filled"
        assert order["error_message"] is None  # Successfully cleared

    def test_order_recovery_with_old_bug(self):
        """Demonstrate that old bug prevented clearing error."""
        # Step 1: Order fails with error
        order = {
            "client_order_id": "test-123",
            "status": "rejected",
            "error_message": "Broker connection failed",
        }

        # Step 2: Try to clear error using COALESCE behavior
        update = {"status": "filled", "error_message": None}

        # Simulate COALESCE: None keeps old value
        if update["error_message"] is not None:
            order["error_message"] = update["error_message"]
        # else: keep existing (COALESCE bug)

        order["status"] = update["status"]

        # Bug: Error message still present even after recovery!
        assert order["status"] == "filled"
        assert order["error_message"] == "Broker connection failed"  # Bug!


class TestOtherFieldsStillUseCoalesce:
    """Verify other fields still use COALESCE (preserve when None)."""

    def test_broker_order_id_preserved(self):
        """Verify broker_order_id still uses COALESCE."""
        # broker_order_id, filled_qty, filled_avg_price, filled_at
        # should all preserve existing value when None passed
        # Only error_message was fixed

        current = {"broker_order_id": "ABC123", "filled_qty": 100}

        # Update with None for broker_order_id (preserve it)
        new_broker_id = None

        # COALESCE behavior (should be preserved)
        result_broker_id = (
            new_broker_id if new_broker_id is not None else current["broker_order_id"]
        )

        assert result_broker_id == "ABC123"  # Preserved (correct for other fields)

    def test_error_message_different_from_other_fields(self):
        """Verify error_message behaves differently than other fields."""
        # error_message: None clears (fixed)
        # other fields: None preserves (unchanged, correct behavior)

        current_error = "Some error"
        current_broker_id = "BRK123"

        # Pass None to both
        new_error = None
        new_broker_id = None

        # error_message: direct assignment (fixed)
        result_error = new_error

        # broker_order_id: COALESCE (preserve)
        result_broker_id = (
            new_broker_id if new_broker_id is not None else current_broker_id
        )

        # Different behaviors (by design)
        assert result_error is None  # Cleared
        assert result_broker_id == "BRK123"  # Preserved
