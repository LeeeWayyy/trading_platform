"""Tests for order reconciliation logic.

These tests cover the order sync and missing order handling in orders.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from apps.execution_gateway.reconciliation.orders import (
    SOURCE_PRIORITY_MANUAL,
    SOURCE_PRIORITY_RECONCILIATION,
    SOURCE_PRIORITY_WEBHOOK,
    apply_broker_update,
    backfill_terminal_fills,
    reconcile_known_orders,
    reconcile_missing_orders,
)


@dataclass
class MockOrder:
    """Mock order object for testing."""

    client_order_id: str
    status: str
    created_at: datetime
    broker_order_id: str | None = None
    symbol: str = "AAPL"
    strategy_id: str = "test_strategy"
    filled_qty: Decimal = Decimal("0")
    filled_avg_price: Decimal | None = None
    updated_at: datetime | None = None


class TestSourcePriorityConstants:
    """Verify source priority ordering."""

    def test_manual_highest_priority(self) -> None:
        """Manual has highest priority (lowest number)."""
        assert SOURCE_PRIORITY_MANUAL < SOURCE_PRIORITY_RECONCILIATION
        assert SOURCE_PRIORITY_MANUAL < SOURCE_PRIORITY_WEBHOOK

    def test_reconciliation_middle_priority(self) -> None:
        """Reconciliation has middle priority."""
        assert SOURCE_PRIORITY_MANUAL < SOURCE_PRIORITY_RECONCILIATION < SOURCE_PRIORITY_WEBHOOK

    def test_webhook_lowest_priority(self) -> None:
        """Webhook has lowest priority (highest number)."""
        assert SOURCE_PRIORITY_WEBHOOK > SOURCE_PRIORITY_RECONCILIATION
        assert SOURCE_PRIORITY_WEBHOOK > SOURCE_PRIORITY_MANUAL


class TestApplyBrokerUpdate:
    """Tests for apply_broker_update function."""

    def test_successful_update(self) -> None:
        """Successful CAS update returns True."""
        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = MagicMock()  # Not None

        result = apply_broker_update(
            client_order_id="order_123",
            broker_order={
                "status": "filled",
                "filled_qty": "100",
                "filled_avg_price": "50.00",
                "updated_at": datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            },
            db_client=db_client,
        )

        assert result is True
        db_client.update_order_status_cas.assert_called_once()

    def test_cas_conflict_returns_false(self) -> None:
        """CAS conflict returns False."""
        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = None  # CAS conflict

        result = apply_broker_update(
            client_order_id="order_123",
            broker_order={"status": "filled"},
            db_client=db_client,
        )

        assert result is False

    def test_calls_backfill_on_filled_status(self) -> None:
        """Calls backfill callback when status is filled."""
        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = MagicMock()
        backfill_callback = MagicMock()

        apply_broker_update(
            client_order_id="order_123",
            broker_order={
                "status": "filled",
                "filled_qty": "100",
                "filled_avg_price": "50.00",
            },
            db_client=db_client,
            backfill_fills_callback=backfill_callback,
        )

        backfill_callback.assert_called_once()

    def test_calls_backfill_on_partially_filled(self) -> None:
        """Calls backfill callback when status is partially_filled."""
        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = MagicMock()
        backfill_callback = MagicMock()

        apply_broker_update(
            client_order_id="order_123",
            broker_order={
                "status": "partially_filled",
                "filled_qty": "50",
                "filled_avg_price": "50.00",
            },
            db_client=db_client,
            backfill_fills_callback=backfill_callback,
        )

        backfill_callback.assert_called_once()

    def test_no_backfill_on_non_filled_status(self) -> None:
        """Does not call backfill when status is not filled/partially_filled."""
        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = MagicMock()
        backfill_callback = MagicMock()

        apply_broker_update(
            client_order_id="order_123",
            broker_order={"status": "canceled"},
            db_client=db_client,
            backfill_fills_callback=backfill_callback,
        )

        backfill_callback.assert_not_called()

    def test_uses_current_time_when_updated_at_missing(self) -> None:
        """Uses current time when updated_at is missing."""
        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = MagicMock()

        with patch("apps.execution_gateway.reconciliation.orders.datetime") as mock_dt:
            mock_now = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            apply_broker_update(
                client_order_id="order_123",
                broker_order={"status": "filled"},  # No updated_at
                db_client=db_client,
            )

            call_args = db_client.update_order_status_cas.call_args
            # broker_updated_at should be the mocked now time
            assert call_args.kwargs.get("broker_updated_at") is not None


class TestReconcileKnownOrders:
    """Tests for reconcile_known_orders function."""

    def test_reconciles_matching_orders(self) -> None:
        """Updates orders that have matching broker orders."""
        db_order = MagicMock()
        db_order.client_order_id = "order_123"

        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = MagicMock()

        orders_by_client = {
            "order_123": {"status": "filled", "filled_qty": "100"},
        }

        result = reconcile_known_orders(
            db_orders=[db_order],
            orders_by_client=orders_by_client,
            db_client=db_client,
        )

        assert result == 1
        db_client.update_order_status_cas.assert_called_once()

    def test_skips_orders_without_broker_match(self) -> None:
        """Skips orders that don't have matching broker orders."""
        db_order = MagicMock()
        db_order.client_order_id = "order_123"

        db_client = MagicMock()

        result = reconcile_known_orders(
            db_orders=[db_order],
            orders_by_client={},  # No broker orders
            db_client=db_client,
        )

        assert result == 0
        db_client.update_order_status_cas.assert_not_called()


class TestReconcileMissingOrders:
    """Tests for reconcile_missing_orders function."""

    def test_individual_lookup_for_old_orders(self) -> None:
        """Does individual lookups for orders outside query window."""
        db_order = MagicMock()
        db_order.client_order_id = "order_123"
        db_order.status = "pending_new"
        db_order.created_at = datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC)

        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = MagicMock()

        alpaca_client = MagicMock()
        alpaca_client.get_order_by_client_id.return_value = {
            "status": "filled",
            "filled_qty": "100",
        }

        after_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

        result = reconcile_missing_orders(
            db_orders=[db_order],
            after_time=after_time,
            db_client=db_client,
            alpaca_client=alpaca_client,
        )

        assert result["lookups"] == 1
        alpaca_client.get_order_by_client_id.assert_called_once_with("order_123")

    def test_submitted_unconfirmed_marked_failed_after_grace(self) -> None:
        """Marks submitted_unconfirmed as failed after grace period."""
        db_order = MagicMock()
        db_order.client_order_id = "order_123"
        db_order.status = "submitted_unconfirmed"
        db_order.created_at = datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC)
        db_order.broker_order_id = None

        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = MagicMock()

        alpaca_client = MagicMock()
        alpaca_client.get_order_by_client_id.return_value = None  # Not at broker

        with patch("apps.execution_gateway.reconciliation.orders.datetime") as mock_dt:
            # Set now to 10 minutes after created_at (beyond 5min grace)
            mock_now = datetime(2024, 1, 1, 10, 10, 0, tzinfo=UTC)
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            result = reconcile_missing_orders(
                db_orders=[db_order],
                after_time=None,
                db_client=db_client,
                alpaca_client=alpaca_client,
                submitted_unconfirmed_grace_seconds=300,  # 5 minutes
            )

        assert result["marked_failed"] == 1

    def test_submitted_unconfirmed_within_grace_deferred(self) -> None:
        """Defers submitted_unconfirmed within grace period."""
        db_order = MagicMock()
        db_order.client_order_id = "order_123"
        db_order.status = "submitted_unconfirmed"
        db_order.created_at = datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC)

        db_client = MagicMock()
        alpaca_client = MagicMock()
        alpaca_client.get_order_by_client_id.return_value = None

        with patch("apps.execution_gateway.reconciliation.orders.datetime") as mock_dt:
            # Set now to 2 minutes after created_at (within 5min grace)
            mock_now = datetime(2024, 1, 1, 10, 2, 0, tzinfo=UTC)
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            result = reconcile_missing_orders(
                db_orders=[db_order],
                after_time=None,
                db_client=db_client,
                alpaca_client=alpaca_client,
                submitted_unconfirmed_grace_seconds=300,
            )

        assert result["marked_failed"] == 0
        db_client.update_order_status_cas.assert_not_called()

    def test_lookup_cap_respected(self) -> None:
        """Stops after reaching max_individual_lookups."""
        db_orders = [MagicMock() for _ in range(10)]
        for i, order in enumerate(db_orders):
            order.client_order_id = f"order_{i}"
            order.status = "pending_new"
            order.created_at = datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC)

        db_client = MagicMock()
        alpaca_client = MagicMock()
        alpaca_client.get_order_by_client_id.return_value = None

        result = reconcile_missing_orders(
            db_orders=db_orders,
            after_time=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            db_client=db_client,
            alpaca_client=alpaca_client,
            max_individual_lookups=3,
        )

        assert result["lookups"] == 3
        assert alpaca_client.get_order_by_client_id.call_count == 3


class TestBackfillTerminalFills:
    """Tests for backfill_terminal_fills function."""

    def test_backfills_filled_orders(self) -> None:
        """Calls backfill for filled orders in DB."""
        orders_by_client = {
            "order_123": {"status": "filled", "filled_qty": "100"},
        }
        db_known_ids = {"order_123"}
        backfill_callback = MagicMock()

        result = backfill_terminal_fills(
            orders_by_client=orders_by_client,
            db_known_ids=db_known_ids,
            backfill_fills_callback=backfill_callback,
        )

        assert result == 1
        backfill_callback.assert_called_once_with("order_123", orders_by_client["order_123"])

    def test_skips_orders_not_in_db(self) -> None:
        """Skips orders not in db_known_ids."""
        orders_by_client = {
            "order_123": {"status": "filled"},
        }
        db_known_ids: set[str] = set()  # Empty - order not in DB
        backfill_callback = MagicMock()

        result = backfill_terminal_fills(
            orders_by_client=orders_by_client,
            db_known_ids=db_known_ids,
            backfill_fills_callback=backfill_callback,
        )

        assert result == 0
        backfill_callback.assert_not_called()

    def test_skips_non_filled_orders(self) -> None:
        """Skips orders with non-filled status."""
        orders_by_client = {
            "order_123": {"status": "canceled"},
        }
        db_known_ids = {"order_123"}
        backfill_callback = MagicMock()

        result = backfill_terminal_fills(
            orders_by_client=orders_by_client,
            db_known_ids=db_known_ids,
            backfill_fills_callback=backfill_callback,
        )

        assert result == 0
        backfill_callback.assert_not_called()


class TestApplyBrokerUpdateEdgeCases:
    """Edge case tests for apply_broker_update."""

    def test_missing_status_defaults_to_empty_string(self) -> None:
        """Handles missing status field gracefully."""
        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = MagicMock()

        result = apply_broker_update(
            client_order_id="order_123",
            broker_order={},  # No status field
            db_client=db_client,
        )

        assert result is True
        call_args = db_client.update_order_status_cas.call_args
        assert call_args.kwargs["status"] == ""

    def test_missing_filled_qty_defaults_to_zero(self) -> None:
        """Defaults filled_qty to 0 when missing."""
        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = MagicMock()

        apply_broker_update(
            client_order_id="order_123",
            broker_order={"status": "filled"},  # No filled_qty
            db_client=db_client,
        )

        call_args = db_client.update_order_status_cas.call_args
        assert call_args.kwargs["filled_qty"] == Decimal("0")

    def test_uses_created_at_when_updated_at_missing(self) -> None:
        """Falls back to created_at when updated_at is None."""
        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = MagicMock()

        created_at = datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC)
        apply_broker_update(
            client_order_id="order_123",
            broker_order={
                "status": "filled",
                "created_at": created_at,
                "updated_at": None,
            },
            db_client=db_client,
        )

        call_args = db_client.update_order_status_cas.call_args
        assert call_args.kwargs["broker_updated_at"] == created_at

    def test_filled_at_set_only_for_filled_status(self) -> None:
        """Sets filled_at only when status is 'filled'."""
        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = MagicMock()

        filled_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        apply_broker_update(
            client_order_id="order_123",
            broker_order={
                "status": "filled",
                "filled_at": filled_at,
            },
            db_client=db_client,
        )

        call_args = db_client.update_order_status_cas.call_args
        assert call_args.kwargs["filled_at"] == filled_at

    def test_filled_at_not_set_for_partial_filled(self) -> None:
        """Does not set filled_at for partially_filled status."""
        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = MagicMock()

        filled_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        apply_broker_update(
            client_order_id="order_123",
            broker_order={
                "status": "partially_filled",
                "filled_at": filled_at,
            },
            db_client=db_client,
        )

        call_args = db_client.update_order_status_cas.call_args
        assert call_args.kwargs["filled_at"] is None

    def test_passes_broker_order_id(self) -> None:
        """Passes broker_order_id from broker order."""
        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = MagicMock()

        apply_broker_update(
            client_order_id="order_123",
            broker_order={
                "status": "filled",
                "id": "broker_abc123",
            },
            db_client=db_client,
        )

        call_args = db_client.update_order_status_cas.call_args
        assert call_args.kwargs["broker_order_id"] == "broker_abc123"

    def test_source_priority_is_reconciliation(self) -> None:
        """Uses reconciliation source priority."""
        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = MagicMock()

        apply_broker_update(
            client_order_id="order_123",
            broker_order={"status": "filled"},
            db_client=db_client,
        )

        call_args = db_client.update_order_status_cas.call_args
        assert call_args.kwargs["source_priority"] == SOURCE_PRIORITY_RECONCILIATION

    def test_backfill_callback_receives_updated_record(self) -> None:
        """Backfill callback receives the updated record from CAS."""
        db_client = MagicMock()
        updated_record = MagicMock()
        db_client.update_order_status_cas.return_value = updated_record
        backfill_callback = MagicMock()

        updated_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        broker_order = {
            "status": "filled",
            "filled_qty": "100",
            "updated_at": updated_at,
        }

        apply_broker_update(
            client_order_id="order_123",
            broker_order=broker_order,
            db_client=db_client,
            backfill_fills_callback=backfill_callback,
        )

        backfill_callback.assert_called_once_with(
            "order_123", broker_order, updated_at, updated_record
        )

    def test_no_backfill_when_cas_fails(self) -> None:
        """Does not call backfill when CAS returns None."""
        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = None  # CAS conflict
        backfill_callback = MagicMock()

        apply_broker_update(
            client_order_id="order_123",
            broker_order={"status": "filled"},
            db_client=db_client,
            backfill_fills_callback=backfill_callback,
        )

        backfill_callback.assert_not_called()

    @patch("apps.execution_gateway.reconciliation.orders.reconciliation_mismatches_total")
    def test_increments_mismatch_counter_on_success(self, mock_counter: MagicMock) -> None:
        """Increments mismatch counter when update succeeds."""
        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = MagicMock()

        apply_broker_update(
            client_order_id="order_123",
            broker_order={"status": "filled"},
            db_client=db_client,
        )

        mock_counter.labels.assert_called_once()
        mock_counter.labels.return_value.inc.assert_called_once()

    @patch("apps.execution_gateway.reconciliation.orders.reconciliation_conflicts_skipped_total")
    def test_increments_conflict_counter_on_cas_failure(self, mock_counter: MagicMock) -> None:
        """Increments conflict counter when CAS fails."""
        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = None  # CAS conflict

        apply_broker_update(
            client_order_id="order_123",
            broker_order={"status": "filled"},
            db_client=db_client,
        )

        mock_counter.labels.assert_called_once()
        mock_counter.labels.return_value.inc.assert_called_once()


class TestReconcileKnownOrdersEdgeCases:
    """Edge case tests for reconcile_known_orders."""

    def test_handles_empty_db_orders_list(self) -> None:
        """Handles empty db_orders list."""
        db_client = MagicMock()

        result = reconcile_known_orders(
            db_orders=[],
            orders_by_client={"order_123": {"status": "filled"}},
            db_client=db_client,
        )

        assert result == 0
        db_client.update_order_status_cas.assert_not_called()

    def test_handles_empty_broker_orders(self) -> None:
        """Handles empty orders_by_client dict."""
        db_order = MockOrder(
            client_order_id="order_123",
            status="pending_new",
            created_at=datetime.now(UTC),
        )
        db_client = MagicMock()

        result = reconcile_known_orders(
            db_orders=[db_order],
            orders_by_client={},
            db_client=db_client,
        )

        assert result == 0
        db_client.update_order_status_cas.assert_not_called()

    def test_multiple_orders_some_matched(self) -> None:
        """Updates only orders with broker matches."""
        db_order1 = MockOrder(
            client_order_id="order_123",
            status="pending_new",
            created_at=datetime.now(UTC),
        )
        db_order2 = MockOrder(
            client_order_id="order_456",
            status="pending_new",
            created_at=datetime.now(UTC),
        )

        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = MagicMock()

        orders_by_client = {
            "order_123": {"status": "filled"},
            # order_456 not in broker results
        }

        result = reconcile_known_orders(
            db_orders=[db_order1, db_order2],
            orders_by_client=orders_by_client,
            db_client=db_client,
        )

        assert result == 1
        assert db_client.update_order_status_cas.call_count == 1

    def test_counts_only_successful_updates(self) -> None:
        """Counts only updates that succeed CAS."""
        db_order1 = MockOrder(
            client_order_id="order_123",
            status="pending_new",
            created_at=datetime.now(UTC),
        )
        db_order2 = MockOrder(
            client_order_id="order_456",
            status="pending_new",
            created_at=datetime.now(UTC),
        )

        db_client = MagicMock()
        # First CAS succeeds, second fails
        db_client.update_order_status_cas.side_effect = [MagicMock(), None]

        orders_by_client = {
            "order_123": {"status": "filled"},
            "order_456": {"status": "filled"},
        }

        result = reconcile_known_orders(
            db_orders=[db_order1, db_order2],
            orders_by_client=orders_by_client,
            db_client=db_client,
        )

        assert result == 1
        assert db_client.update_order_status_cas.call_count == 2

    def test_passes_backfill_callback_to_apply_update(self) -> None:
        """Passes backfill callback through to apply_broker_update."""
        db_order = MockOrder(
            client_order_id="order_123",
            status="pending_new",
            created_at=datetime.now(UTC),
        )

        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = MagicMock()
        backfill_callback = MagicMock()

        orders_by_client = {
            "order_123": {"status": "filled", "filled_qty": "100"},
        }

        reconcile_known_orders(
            db_orders=[db_order],
            orders_by_client=orders_by_client,
            db_client=db_client,
            backfill_fills_callback=backfill_callback,
        )

        # Backfill should have been called
        backfill_callback.assert_called_once()


class TestReconcileMissingOrdersEdgeCases:
    """Edge case tests for reconcile_missing_orders."""

    def test_handles_empty_db_orders_list(self) -> None:
        """Handles empty db_orders list."""
        db_client = MagicMock()
        alpaca_client = MagicMock()

        result = reconcile_missing_orders(
            db_orders=[],
            after_time=None,
            db_client=db_client,
            alpaca_client=alpaca_client,
        )

        assert result == {"lookups": 0, "updated": 0, "marked_failed": 0}
        alpaca_client.get_order_by_client_id.assert_not_called()

    def test_skips_recent_orders_within_window(self) -> None:
        """Skips orders created after after_time (within query window)."""
        db_order = MockOrder(
            client_order_id="order_123",
            status="pending_new",
            created_at=datetime(2024, 1, 1, 13, 0, 0, tzinfo=UTC),
        )

        db_client = MagicMock()
        alpaca_client = MagicMock()

        after_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

        result = reconcile_missing_orders(
            db_orders=[db_order],
            after_time=after_time,
            db_client=db_client,
            alpaca_client=alpaca_client,
        )

        # Should skip because order is after after_time
        assert result["lookups"] == 0
        alpaca_client.get_order_by_client_id.assert_not_called()

    def test_lookups_submitted_unconfirmed_regardless_of_window(self) -> None:
        """Always looks up submitted_unconfirmed orders."""
        db_order = MockOrder(
            client_order_id="order_123",
            status="submitted_unconfirmed",
            created_at=datetime(2024, 1, 1, 13, 0, 0, tzinfo=UTC),
        )

        db_client = MagicMock()
        alpaca_client = MagicMock()
        alpaca_client.get_order_by_client_id.return_value = None

        after_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

        with patch("apps.execution_gateway.reconciliation.orders.datetime") as mock_dt:
            # Within grace period
            mock_now = datetime(2024, 1, 1, 13, 2, 0, tzinfo=UTC)
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            result = reconcile_missing_orders(
                db_orders=[db_order],
                after_time=after_time,
                db_client=db_client,
                alpaca_client=alpaca_client,
            )

        # Should do lookup despite being after after_time
        assert result["lookups"] == 1
        alpaca_client.get_order_by_client_id.assert_called_once()

    def test_broker_lookup_found_updates_order(self) -> None:
        """Updates order when individual lookup finds it at broker."""
        db_order = MockOrder(
            client_order_id="order_123",
            status="pending_new",
            created_at=datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC),
        )

        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = MagicMock()

        alpaca_client = MagicMock()
        alpaca_client.get_order_by_client_id.return_value = {
            "status": "filled",
            "filled_qty": "100",
        }

        after_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

        result = reconcile_missing_orders(
            db_orders=[db_order],
            after_time=after_time,
            db_client=db_client,
            alpaca_client=alpaca_client,
        )

        assert result["lookups"] == 1
        assert result["updated"] == 1
        assert result["marked_failed"] == 0

    def test_no_update_when_lookup_cas_fails(self) -> None:
        """Does not count update when CAS fails."""
        db_order = MockOrder(
            client_order_id="order_123",
            status="pending_new",
            created_at=datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC),
        )

        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = None  # CAS conflict

        alpaca_client = MagicMock()
        alpaca_client.get_order_by_client_id.return_value = {
            "status": "filled",
            "filled_qty": "100",
        }

        after_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

        result = reconcile_missing_orders(
            db_orders=[db_order],
            after_time=after_time,
            db_client=db_client,
            alpaca_client=alpaca_client,
        )

        assert result["updated"] == 0

    def test_submitted_unconfirmed_cas_conflict_not_counted(self) -> None:
        """Does not count marked_failed when CAS fails."""
        db_order = MockOrder(
            client_order_id="order_123",
            status="submitted_unconfirmed",
            created_at=datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC),
        )

        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = None  # CAS conflict

        alpaca_client = MagicMock()
        alpaca_client.get_order_by_client_id.return_value = None

        with patch("apps.execution_gateway.reconciliation.orders.datetime") as mock_dt:
            # Beyond grace period
            mock_now = datetime(2024, 1, 1, 10, 10, 0, tzinfo=UTC)
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            result = reconcile_missing_orders(
                db_orders=[db_order],
                after_time=None,
                db_client=db_client,
                alpaca_client=alpaca_client,
                submitted_unconfirmed_grace_seconds=300,
            )

        assert result["marked_failed"] == 0

    def test_uses_order_created_at_for_failed_timestamp(self) -> None:
        """Uses order's created_at as broker_updated_at when marking failed."""
        created_at = datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC)
        db_order = MockOrder(
            client_order_id="order_123",
            status="submitted_unconfirmed",
            created_at=created_at,
        )

        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = MagicMock()

        alpaca_client = MagicMock()
        alpaca_client.get_order_by_client_id.return_value = None

        with patch("apps.execution_gateway.reconciliation.orders.datetime") as mock_dt:
            mock_now = datetime(2024, 1, 1, 10, 10, 0, tzinfo=UTC)
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            reconcile_missing_orders(
                db_orders=[db_order],
                after_time=None,
                db_client=db_client,
                alpaca_client=alpaca_client,
                submitted_unconfirmed_grace_seconds=300,
            )

        call_args = db_client.update_order_status_cas.call_args
        assert call_args.kwargs["broker_updated_at"] == created_at

    def test_passes_backfill_callback_on_lookup_success(self) -> None:
        """Passes backfill callback when individual lookup succeeds."""
        db_order = MockOrder(
            client_order_id="order_123",
            status="pending_new",
            created_at=datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC),
        )

        db_client = MagicMock()
        db_client.update_order_status_cas.return_value = MagicMock()
        backfill_callback = MagicMock()

        alpaca_client = MagicMock()
        alpaca_client.get_order_by_client_id.return_value = {
            "status": "filled",
            "filled_qty": "100",
        }

        after_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

        reconcile_missing_orders(
            db_orders=[db_order],
            after_time=after_time,
            db_client=db_client,
            alpaca_client=alpaca_client,
            backfill_fills_callback=backfill_callback,
        )

        backfill_callback.assert_called_once()

    def test_lookup_cap_stops_at_exact_limit(self) -> None:
        """Stops at exact max_individual_lookups."""
        db_orders = [
            MockOrder(
                client_order_id=f"order_{i}",
                status="pending_new",
                created_at=datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC),
            )
            for i in range(5)
        ]

        db_client = MagicMock()
        alpaca_client = MagicMock()
        alpaca_client.get_order_by_client_id.return_value = None

        after_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

        result = reconcile_missing_orders(
            db_orders=db_orders,
            after_time=after_time,
            db_client=db_client,
            alpaca_client=alpaca_client,
            max_individual_lookups=3,
        )

        assert result["lookups"] == 3
        assert alpaca_client.get_order_by_client_id.call_count == 3

    def test_no_lookup_when_after_time_is_none(self) -> None:
        """Does lookups when after_time is None (boot reconciliation)."""
        db_order = MockOrder(
            client_order_id="order_123",
            status="pending_new",
            created_at=datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC),
        )

        db_client = MagicMock()
        alpaca_client = MagicMock()
        alpaca_client.get_order_by_client_id.return_value = None

        result = reconcile_missing_orders(
            db_orders=[db_order],
            after_time=None,  # Boot reconciliation
            db_client=db_client,
            alpaca_client=alpaca_client,
        )

        assert result["lookups"] == 1
        alpaca_client.get_order_by_client_id.assert_called_once()


class TestBackfillTerminalFillsEdgeCases:
    """Edge case tests for backfill_terminal_fills."""

    def test_handles_empty_orders_by_client(self) -> None:
        """Handles empty orders_by_client dict."""
        backfill_callback = MagicMock()

        result = backfill_terminal_fills(
            orders_by_client={},
            db_known_ids={"order_123"},
            backfill_fills_callback=backfill_callback,
        )

        assert result == 0
        backfill_callback.assert_not_called()

    def test_handles_empty_db_known_ids(self) -> None:
        """Handles empty db_known_ids set."""
        orders_by_client = {
            "order_123": {"status": "filled"},
        }
        backfill_callback = MagicMock()

        result = backfill_terminal_fills(
            orders_by_client=orders_by_client,
            db_known_ids=set(),
            backfill_fills_callback=backfill_callback,
        )

        assert result == 0
        backfill_callback.assert_not_called()

    def test_backfills_partially_filled_orders(self) -> None:
        """Calls backfill for partially_filled orders."""
        orders_by_client = {
            "order_123": {"status": "partially_filled", "filled_qty": "50"},
        }
        db_known_ids = {"order_123"}
        backfill_callback = MagicMock()

        result = backfill_terminal_fills(
            orders_by_client=orders_by_client,
            db_known_ids=db_known_ids,
            backfill_fills_callback=backfill_callback,
        )

        assert result == 1
        backfill_callback.assert_called_once_with("order_123", orders_by_client["order_123"])

    def test_handles_multiple_orders_mixed_statuses(self) -> None:
        """Processes only filled/partially_filled orders."""
        orders_by_client = {
            "order_123": {"status": "filled"},
            "order_456": {"status": "canceled"},
            "order_789": {"status": "partially_filled"},
            "order_abc": {"status": "pending_new"},
        }
        db_known_ids = {"order_123", "order_456", "order_789", "order_abc"}
        backfill_callback = MagicMock()

        result = backfill_terminal_fills(
            orders_by_client=orders_by_client,
            db_known_ids=db_known_ids,
            backfill_fills_callback=backfill_callback,
        )

        assert result == 2
        assert backfill_callback.call_count == 2

    def test_case_insensitive_status_matching(self) -> None:
        """Handles uppercase status values."""
        orders_by_client = {
            "order_123": {"status": "FILLED"},
        }
        db_known_ids = {"order_123"}
        backfill_callback = MagicMock()

        result = backfill_terminal_fills(
            orders_by_client=orders_by_client,
            db_known_ids=db_known_ids,
            backfill_fills_callback=backfill_callback,
        )

        # Status is lowercased in code (line 274)
        assert result == 1
        backfill_callback.assert_called_once()

    def test_handles_none_status(self) -> None:
        """Handles None status gracefully."""
        orders_by_client = {
            "order_123": {"status": None},
        }
        db_known_ids = {"order_123"}
        backfill_callback = MagicMock()

        result = backfill_terminal_fills(
            orders_by_client=orders_by_client,
            db_known_ids=db_known_ids,
            backfill_fills_callback=backfill_callback,
        )

        assert result == 0
        backfill_callback.assert_not_called()

    def test_skips_order_not_in_both_collections(self) -> None:
        """Only processes orders in both collections."""
        orders_by_client = {
            "order_123": {"status": "filled"},
            "order_456": {"status": "filled"},
        }
        db_known_ids = {"order_123"}  # Only order_123 in DB
        backfill_callback = MagicMock()

        result = backfill_terminal_fills(
            orders_by_client=orders_by_client,
            db_known_ids=db_known_ids,
            backfill_fills_callback=backfill_callback,
        )

        assert result == 1
        # Should only be called for order_123
        backfill_callback.assert_called_once_with("order_123", orders_by_client["order_123"])
