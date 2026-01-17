"""Tests for order reconciliation logic.

These tests cover the order sync and missing order handling in orders.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
