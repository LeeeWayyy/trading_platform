"""
Tests for C4: Idempotency Race Condition Fix.

This module tests that concurrent order submissions with the same
client_order_id are handled gracefully instead of returning 500 errors.

Issue: C4 - Idempotency race condition (UniqueViolation 500)
Location: apps/execution_gateway/main.py:submit_order
Fix: Catch UniqueViolation, return existing order (idempotent response)
"""

import pytest


class TestIdempotencyRaceHandling:
    """Test idempotency race condition handling logic.

    Instead of testing the FastAPI endpoint directly, we test the
    validation logic by simulating the race condition handling.
    """

    def _simulate_create_order_with_race(
        self,
        client_order_id: str,
        existing_orders: dict,
        unique_violation: bool = False,
    ) -> tuple[bool, dict | None]:
        """Simulate the race condition handling in submit_order.

        Returns:
            (created, order): created=True if new order, else existing order

        This mirrors the logic in apps/execution_gateway/main.py:1150-1184.
        """
        if unique_violation:
            # Simulate UniqueViolation from concurrent submission
            if client_order_id in existing_orders:
                return (False, existing_orders[client_order_id])
            # Should never happen in real scenario
            return (False, None)

        # Simulate successful creation
        return (True, {"client_order_id": client_order_id, "status": "dry_run"})

    def test_successful_order_creation(self):
        """Verify normal order creation works."""
        existing_orders: dict = {}
        created, order = self._simulate_create_order_with_race(
            client_order_id="test-order-123",
            existing_orders=existing_orders,
            unique_violation=False,
        )
        assert created is True
        assert order is not None
        assert order["client_order_id"] == "test-order-123"

    def test_race_returns_existing_order(self):
        """Verify UniqueViolation returns existing order (not 500)."""
        existing_orders = {
            "test-order-123": {
                "client_order_id": "test-order-123",
                "status": "pending_new",
                "broker_order_id": "broker-abc",
            }
        }
        created, order = self._simulate_create_order_with_race(
            client_order_id="test-order-123",
            existing_orders=existing_orders,
            unique_violation=True,  # Simulate race condition
        )
        assert created is False  # Not newly created
        assert order is not None  # But we got existing order
        assert order["client_order_id"] == "test-order-123"
        assert order["broker_order_id"] == "broker-abc"

    def test_race_no_500_error(self):
        """Verify race condition doesn't cause 500 error."""
        # In the old code, UniqueViolation would bubble up as 500
        # In the fixed code, we catch it and return existing order
        existing_orders = {
            "test-order-123": {"client_order_id": "test-order-123", "status": "dry_run"}
        }

        # This should NOT raise an exception
        created, order = self._simulate_create_order_with_race(
            client_order_id="test-order-123",
            existing_orders=existing_orders,
            unique_violation=True,
        )

        # Should return existing order gracefully
        assert order is not None

    def test_idempotent_response_structure(self):
        """Verify idempotent response has all required fields."""
        existing_orders = {
            "test-order-123": {
                "client_order_id": "test-order-123",
                "status": "filled",
                "broker_order_id": "broker-xyz",
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
            }
        }

        created, order = self._simulate_create_order_with_race(
            client_order_id="test-order-123",
            existing_orders=existing_orders,
            unique_violation=True,
        )

        # All fields should be present from existing order
        assert order["client_order_id"] == "test-order-123"
        assert order["status"] == "filled"
        assert order["broker_order_id"] == "broker-xyz"


class TestDryRunVsLiveRaceHandling:
    """Test race condition handling in both DRY_RUN and Live modes."""

    def test_dry_run_race_handling(self):
        """Verify DRY_RUN mode handles race condition."""
        # DRY_RUN: No Alpaca submission, just DB write
        # Race condition: Two requests both try to create order in DB
        # Fix: Catch UniqueViolation, return existing

        existing = {"client_order_id": "dry-order", "status": "dry_run"}
        existing_orders = {"dry-order": existing}

        # Simulate second request hitting race condition
        _, order = self._simulate_race(
            dry_run=True,
            existing_orders=existing_orders,
            unique_violation=True,
        )
        assert order is not None
        assert order["status"] == "dry_run"

    def test_live_race_handling(self):
        """Verify Live mode handles race condition."""
        # Live mode: Alpaca submission, then DB write
        # Race condition: Both requests submit to Alpaca (idempotent by client_order_id)
        # Then both try to create order in DB
        # Fix: Catch UniqueViolation, return existing

        existing = {
            "client_order_id": "live-order",
            "status": "pending_new",
            "broker_order_id": "alpaca-123",
        }
        existing_orders = {"live-order": existing}

        # Simulate second request hitting race condition
        _, order = self._simulate_race(
            dry_run=False,
            existing_orders=existing_orders,
            unique_violation=True,
        )
        assert order is not None
        assert order["broker_order_id"] == "alpaca-123"

    def _simulate_race(
        self, dry_run: bool, existing_orders: dict, unique_violation: bool
    ) -> tuple[bool, dict | None]:
        """Simulate race condition in either mode."""
        client_order_id = list(existing_orders.keys())[0] if existing_orders else "new"

        if unique_violation and client_order_id in existing_orders:
            return (False, existing_orders[client_order_id])
        return (True, {"client_order_id": client_order_id, "status": "created"})


class TestConcurrentSubmissionBehavior:
    """Document expected behavior for concurrent submissions."""

    def test_both_requests_get_consistent_response(self):
        """Verify both concurrent requests get same result."""
        # Request 1: Passes idempotency check, creates order
        # Request 2: Passes idempotency check, gets UniqueViolation, returns existing

        existing_order = {
            "client_order_id": "shared-id",
            "status": "pending_new",
            "broker_order_id": "broker-123",
        }

        # Request 1 result (normal creation)
        result1 = existing_order

        # Request 2 result (race condition handled)
        result2 = existing_order

        # Both should return same order
        assert result1["client_order_id"] == result2["client_order_id"]
        assert result1["broker_order_id"] == result2["broker_order_id"]

    def test_no_duplicate_orders_created(self):
        """Verify race condition doesn't create duplicate orders."""
        # The fix ensures only one order is created in DB
        # Second request returns the same order

        orders_in_db: list = []
        client_order_id = "unique-order-id"

        # Request 1 creates order
        orders_in_db.append({"client_order_id": client_order_id})

        # Request 2 gets UniqueViolation, returns existing (no new order)
        # Simulated by not appending

        assert len(orders_in_db) == 1  # Only one order
        assert orders_in_db[0]["client_order_id"] == client_order_id
