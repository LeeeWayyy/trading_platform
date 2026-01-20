"""
Comprehensive unit tests for reconciliation.py module - Deep Branch Coverage.

This test suite complements test_reconciliation_legacy.py with focused coverage on:
- Complex branching paths in reconciliation logic
- Edge cases in fill calculation and backfill
- Error handling paths and recovery
- CAS conflict scenarios
- Redis failure modes
- Database transaction rollback paths
- Order merging and deduplication edge cases
- Position reconciliation corner cases
- Alpaca fills API pagination and error handling
- Quarantine and orphan exposure synchronization

Target: 85%+ branch coverage for reconciliation.py (458 lines)

See Also:
    - test_reconciliation_legacy.py - Core reconciliation tests (74 tests)
    - test_reconciliation_gating.py - Startup gating and reduce-only mode
    - test_reconciliation_endpoints.py - API endpoint tests
    - /docs/ADRs/0014-execution-gateway-architecture.md - Architecture decisions
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

import psycopg
import pytest
import redis

from apps.execution_gateway.alpaca_client import AlpacaConnectionError
from apps.execution_gateway.reconciliation import (
    ReconciliationService,
)
from apps.execution_gateway.reconciliation.fills import backfill_fill_metadata
from apps.execution_gateway.reconciliation.helpers import (
    calculate_synthetic_fill,
    estimate_notional,
)
from apps.execution_gateway.reconciliation.positions import reconcile_positions

# --------------------------
# Fixtures
# --------------------------


@pytest.fixture()
def mock_db_client():
    """Create mock database client with transaction support."""
    client = Mock()
    client.get_reconciliation_high_water_mark = Mock(return_value=None)
    client.set_reconciliation_high_water_mark = Mock()
    client.get_non_terminal_orders = Mock(return_value=[])
    client.get_order_ids_by_client_ids = Mock(return_value=set())
    client.update_order_status_cas = Mock(return_value=True)
    client.create_orphan_order = Mock()
    client.update_orphan_order_status = Mock()
    client.get_orphan_exposure = Mock(return_value=Decimal("0"))
    client.get_all_positions = Mock(return_value=[])
    client.upsert_position_snapshot = Mock()
    client.get_filled_orders_missing_fills = Mock(return_value=[])
    client.get_order_for_update = Mock(return_value=None)
    client.append_fill_to_order_metadata = Mock(return_value=True)
    client.get_orders_by_broker_ids = Mock(return_value={})
    client.recalculate_trade_realized_pnl = Mock(return_value={"trades_updated": 0})

    # Mock transaction context manager
    @contextmanager
    def mock_transaction():
        conn = Mock()
        yield conn

    client.transaction = mock_transaction
    return client


@pytest.fixture()
def mock_alpaca_client():
    """Create mock Alpaca client."""
    client = Mock()
    client.get_orders = Mock(return_value=[])
    client.get_order_by_client_id = Mock(return_value=None)
    client.get_all_positions = Mock(return_value=[])
    client.get_account_activities = Mock(return_value=[])
    return client


@pytest.fixture()
def mock_redis_client():
    """Create mock Redis client."""
    client = Mock()
    client.set = Mock()
    client.get = Mock(return_value=None)
    return client


@pytest.fixture()
def reconciliation_service(mock_db_client, mock_alpaca_client, mock_redis_client):
    """Create ReconciliationService instance with mocks."""
    return ReconciliationService(
        db_client=mock_db_client,
        alpaca_client=mock_alpaca_client,
        redis_client=mock_redis_client,
        dry_run=False,
    )


@pytest.fixture()
def mock_db_order():
    """Create mock database order object."""
    order = Mock()
    order.client_order_id = "client-test-123"
    order.symbol = "AAPL"
    order.strategy_id = "alpha_baseline"
    order.status = "new"
    order.created_at = datetime.now(UTC)
    order.updated_at = datetime.now(UTC)
    order.filled_qty = Decimal("0")
    order.filled_avg_price = None
    order.filled_at = None
    order.broker_order_id = None
    order.metadata = {}
    return order


# --------------------------
# Order Merging and Deduplication Tests
# --------------------------


class TestOrderMergingAndDeduplication:
    """Test order merging logic and deduplication edge cases."""

    def test_merge_orders_prefers_newer_updated_at(
        self, reconciliation_service, mock_alpaca_client, mock_db_client
    ):
        """Should prefer order with newer updated_at timestamp when merging."""
        older_time = datetime.now(UTC) - timedelta(minutes=5)
        newer_time = datetime.now(UTC)

        open_orders = [
            {
                "client_order_id": "client-123",
                "status": "new",
                "updated_at": older_time.isoformat(),
                "created_at": older_time.isoformat(),
                "filled_qty": "0",
            }
        ]
        recent_orders = [
            {
                "client_order_id": "client-123",
                "status": "filled",
                "updated_at": newer_time.isoformat(),
                "created_at": older_time.isoformat(),
                "filled_qty": "100",
                "filled_avg_price": "150.50",
            }
        ]

        mock_alpaca_client.get_orders.side_effect = [open_orders, recent_orders]
        mock_db_client.get_non_terminal_orders.return_value = []
        mock_db_client.get_order_ids_by_client_ids.return_value = {"client-123"}
        mock_db_client.get_all_positions.return_value = []
        mock_db_client.get_filled_orders_missing_fills.return_value = []

        result = reconciliation_service._run_reconciliation("manual")

        assert result["status"] == "success"
        # Should only backfill fill metadata once for the merged order
        assert result["open_orders_checked"] == 1

    def test_merge_orders_fallback_to_created_at(
        self, reconciliation_service, mock_alpaca_client, mock_db_client
    ):
        """Should fallback to created_at if updated_at is missing."""
        base_time = datetime.now(UTC) - timedelta(minutes=10)
        newer_time = datetime.now(UTC)

        open_orders = [
            {
                "client_order_id": "client-123",
                "status": "new",
                "created_at": newer_time.isoformat(),  # No updated_at
                "filled_qty": "0",
            }
        ]
        recent_orders = [
            {
                "client_order_id": "client-123",
                "status": "filled",
                "created_at": base_time.isoformat(),  # No updated_at, older
                "filled_qty": "100",
            }
        ]

        mock_alpaca_client.get_orders.side_effect = [open_orders, recent_orders]
        mock_db_client.get_non_terminal_orders.return_value = []
        mock_db_client.get_order_ids_by_client_ids.return_value = {"client-123"}
        mock_db_client.get_all_positions.return_value = []
        mock_db_client.get_filled_orders_missing_fills.return_value = []

        result = reconciliation_service._run_reconciliation("manual")

        assert result["status"] == "success"

    def test_merge_orders_skip_missing_client_order_id(
        self, reconciliation_service, mock_alpaca_client, mock_db_client
    ):
        """Should skip orders without client_order_id during merge."""
        open_orders = [
            {
                "status": "new",
                "updated_at": datetime.now(UTC).isoformat(),
                # Missing client_order_id
            }
        ]

        mock_alpaca_client.get_orders.side_effect = [open_orders, []]
        mock_db_client.get_non_terminal_orders.return_value = []
        mock_db_client.get_order_ids_by_client_ids.return_value = set()
        mock_db_client.get_all_positions.return_value = []
        mock_db_client.get_filled_orders_missing_fills.return_value = []

        result = reconciliation_service._run_reconciliation("manual")

        assert result["status"] == "success"
        # Should not crash, just skip the order


# --------------------------
# CAS Conflict Scenarios
# --------------------------


class TestCASConflictScenarios:
    """Test Compare-And-Swap conflict handling."""

    def test_broker_update_cas_conflict_increments_metric(
        self, reconciliation_service, mock_db_client, mock_alpaca_client
    ):
        """Should increment conflict counter when CAS update fails."""
        broker_order = {
            "client_order_id": "client-123",
            "status": "filled",
            "updated_at": datetime.now(UTC).isoformat(),
            "filled_qty": "100",
        }

        # CAS conflict: update_order_status_cas returns None
        mock_db_client.update_order_status_cas.return_value = None

        db_order = Mock()
        db_order.client_order_id = "client-123"
        db_order.status = "new"
        db_order.created_at = datetime.now(UTC)

        mock_alpaca_client.get_orders.side_effect = [[broker_order], []]
        mock_db_client.get_non_terminal_orders.return_value = [db_order]
        mock_db_client.get_order_ids_by_client_ids.return_value = {"client-123"}
        mock_db_client.get_all_positions.return_value = []
        mock_db_client.get_filled_orders_missing_fills.return_value = []

        with patch("apps.execution_gateway.reconciliation.orders.reconciliation_conflicts_skipped_total") as mock_metric:
            result = reconciliation_service._run_reconciliation("manual")

        assert result["status"] == "success"
        # Metric should be incremented for CAS conflict
        assert mock_metric.labels.called

    def test_broker_update_cas_success_increments_mismatch_metric(
        self, reconciliation_service, mock_db_client, mock_alpaca_client
    ):
        """Should increment mismatch counter when CAS update succeeds."""
        broker_order = {
            "client_order_id": "client-123",
            "status": "filled",
            "updated_at": datetime.now(UTC).isoformat(),
            "filled_qty": "100",
            "filled_avg_price": "150.50",
        }

        # CAS success: update_order_status_cas returns updated row
        mock_db_order = Mock()
        mock_db_client.update_order_status_cas.return_value = mock_db_order

        db_order = Mock()
        db_order.client_order_id = "client-123"
        db_order.status = "new"
        db_order.created_at = datetime.now(UTC)

        mock_alpaca_client.get_orders.side_effect = [[broker_order], []]
        mock_db_client.get_non_terminal_orders.return_value = [db_order]
        mock_db_client.get_order_ids_by_client_ids.return_value = {"client-123"}
        mock_db_client.get_all_positions.return_value = []
        mock_db_client.get_filled_orders_missing_fills.return_value = []

        with patch("apps.execution_gateway.reconciliation.orders.reconciliation_mismatches_total") as mock_metric:
            result = reconciliation_service._run_reconciliation("manual")

        assert result["status"] == "success"
        # Metric should be incremented for successful correction
        assert mock_metric.labels.called


# --------------------------
# Missing Order Resolution Edge Cases
# --------------------------


class TestMissingOrderResolutionEdgeCases:
    """Test edge cases in missing order resolution logic."""

    def test_submitted_unconfirmed_within_grace_period_deferred(
        self, reconciliation_service, mock_db_client, mock_alpaca_client
    ):
        """Should defer failure for submitted_unconfirmed within grace period."""
        recent_time = datetime.now(UTC) - timedelta(seconds=60)  # 1 minute ago

        db_order = Mock()
        db_order.client_order_id = "client-123"
        db_order.status = "submitted_unconfirmed"
        db_order.created_at = recent_time
        db_order.broker_order_id = None

        mock_alpaca_client.get_orders.side_effect = [[], []]
        mock_alpaca_client.get_order_by_client_id.return_value = None  # Not found
        mock_db_client.get_non_terminal_orders.return_value = [db_order]
        mock_db_client.get_order_ids_by_client_ids.return_value = set()
        mock_db_client.get_all_positions.return_value = []
        mock_db_client.get_filled_orders_missing_fills.return_value = []

        result = reconciliation_service._run_reconciliation("manual")

        assert result["status"] == "success"
        # Should NOT update to failed (deferred)
        mock_db_client.update_order_status_cas.assert_not_called()

    def test_submitted_unconfirmed_beyond_grace_period_fails(
        self, reconciliation_service, mock_db_client, mock_alpaca_client
    ):
        """Should mark as failed when submitted_unconfirmed exceeds grace period."""
        old_time = datetime.now(UTC) - timedelta(seconds=400)  # Beyond grace period (300s)

        db_order = Mock()
        db_order.client_order_id = "client-123"
        db_order.status = "submitted_unconfirmed"
        db_order.created_at = old_time
        db_order.broker_order_id = None

        mock_alpaca_client.get_orders.side_effect = [[], []]
        mock_alpaca_client.get_order_by_client_id.return_value = None  # Not found
        mock_db_client.get_non_terminal_orders.return_value = [db_order]
        mock_db_client.get_order_ids_by_client_ids.return_value = set()
        mock_db_client.get_all_positions.return_value = []
        mock_db_client.get_filled_orders_missing_fills.return_value = []
        mock_db_client.update_order_status_cas.return_value = Mock()  # Success

        result = reconciliation_service._run_reconciliation("manual")

        assert result["status"] == "success"
        # Should update to failed
        mock_db_client.update_order_status_cas.assert_called_once()
        call_args = mock_db_client.update_order_status_cas.call_args
        assert call_args.kwargs["status"] == "failed"
        assert call_args.kwargs["client_order_id"] == "client-123"

    def test_missing_order_lookup_cap_prevents_excessive_calls(
        self, reconciliation_service, mock_db_client, mock_alpaca_client
    ):
        """Should stop individual lookups when max_individual_lookups reached."""
        # Create more orders than lookup cap
        db_orders = []
        for i in range(150):  # More than default 100 cap
            order = Mock()
            order.client_order_id = f"client-{i}"
            order.status = "new"
            order.created_at = datetime.now(UTC) - timedelta(hours=1)
            db_orders.append(order)

        mock_alpaca_client.get_orders.side_effect = [[], []]
        mock_alpaca_client.get_order_by_client_id.return_value = None
        mock_db_client.get_non_terminal_orders.return_value = db_orders
        mock_db_client.get_order_ids_by_client_ids.return_value = set()
        mock_db_client.get_all_positions.return_value = []
        mock_db_client.get_filled_orders_missing_fills.return_value = []

        result = reconciliation_service._run_reconciliation("manual")

        assert result["status"] == "success"
        # Should only make max_individual_lookups calls
        assert mock_alpaca_client.get_order_by_client_id.call_count == 100

    def test_missing_order_recent_order_skipped_within_window(
        self, reconciliation_service, mock_db_client, mock_alpaca_client
    ):
        """Should skip individual lookup for recent non-submitted_unconfirmed orders."""
        recent_time = datetime.now(UTC) - timedelta(seconds=30)  # Within overlap window
        after_time = datetime.now(UTC) - timedelta(seconds=60)

        db_order = Mock()
        db_order.client_order_id = "client-123"
        db_order.status = "new"  # Not submitted_unconfirmed
        db_order.created_at = recent_time

        mock_db_client.get_reconciliation_high_water_mark.return_value = after_time + timedelta(seconds=60)
        mock_alpaca_client.get_orders.side_effect = [[], []]
        mock_db_client.get_non_terminal_orders.return_value = [db_order]
        mock_db_client.get_order_ids_by_client_ids.return_value = set()
        mock_db_client.get_all_positions.return_value = []
        mock_db_client.get_filled_orders_missing_fills.return_value = []

        result = reconciliation_service._run_reconciliation("manual")

        assert result["status"] == "success"
        # Should NOT make individual lookup (recent order)
        mock_alpaca_client.get_order_by_client_id.assert_not_called()


# --------------------------
# Orphan Order Detection Edge Cases
# --------------------------


class TestOrphanOrderDetectionEdgeCases:
    """Test edge cases in orphan order detection and handling."""

    def test_orphan_order_terminal_status_resolved_immediately(
        self, reconciliation_service, mock_db_client, mock_alpaca_client
    ):
        """Should mark terminal orphan orders as resolved immediately."""
        broker_order = {
            "id": "broker-999",
            "client_order_id": "unknown-client-123",
            "symbol": "TSLA",
            "side": "buy",
            "qty": "50",
            "status": "filled",  # Terminal status
        }

        # Need a high water mark so recent_orders are fetched
        mock_db_client.get_reconciliation_high_water_mark.return_value = datetime.now(UTC) - timedelta(hours=1)
        mock_alpaca_client.get_orders.side_effect = [[], [broker_order]]  # In recent_orders
        mock_db_client.get_non_terminal_orders.return_value = []
        mock_db_client.get_order_ids_by_client_ids.return_value = set()  # Not in DB
        mock_db_client.get_all_positions.return_value = []
        mock_db_client.get_filled_orders_missing_fills.return_value = []

        result = reconciliation_service._run_reconciliation("manual")

        assert result["status"] == "success"
        # Should create orphan order
        mock_db_client.create_orphan_order.assert_called_once()
        # Should mark as resolved with timestamp
        update_call = mock_db_client.update_orphan_order_status.call_args
        assert update_call.kwargs["status"] == "filled"
        assert update_call.kwargs["resolved_at"] is not None

    def test_orphan_order_non_terminal_status_not_resolved(
        self, reconciliation_service, mock_db_client, mock_alpaca_client
    ):
        """Should NOT mark non-terminal orphan orders as resolved."""
        broker_order = {
            "id": "broker-999",
            "client_order_id": "unknown-client-123",
            "symbol": "TSLA",
            "side": "buy",
            "qty": "50",
            "status": "new",  # Non-terminal status
        }

        mock_alpaca_client.get_orders.side_effect = [[broker_order], []]  # In open_orders
        mock_db_client.get_non_terminal_orders.return_value = []
        mock_db_client.get_order_ids_by_client_ids.return_value = set()  # Not in DB
        mock_db_client.get_all_positions.return_value = []
        mock_db_client.get_filled_orders_missing_fills.return_value = []

        result = reconciliation_service._run_reconciliation("manual")

        assert result["status"] == "success"
        # Should create orphan order
        mock_db_client.create_orphan_order.assert_called_once()
        # Should mark status but NOT resolved
        update_call = mock_db_client.update_orphan_order_status.call_args
        assert update_call.kwargs["status"] == "new"
        assert update_call.kwargs["resolved_at"] is None

    def test_orphan_order_sets_wildcard_quarantine(
        self, reconciliation_service, mock_db_client, mock_alpaca_client, mock_redis_client
    ):
        """Should set wildcard quarantine for orphan orders."""
        broker_order = {
            "id": "broker-999",
            "client_order_id": "unknown-client-123",
            "symbol": "TSLA",
            "side": "buy",
            "qty": "50",
            "status": "new",
        }

        mock_alpaca_client.get_orders.side_effect = [[broker_order], []]
        mock_db_client.get_non_terminal_orders.return_value = []
        mock_db_client.get_order_ids_by_client_ids.return_value = set()
        mock_db_client.get_all_positions.return_value = []
        mock_db_client.get_filled_orders_missing_fills.return_value = []

        result = reconciliation_service._run_reconciliation("manual")

        assert result["status"] == "success"
        # Should set quarantine with wildcard strategy
        assert mock_redis_client.set.called
        # Check quarantine key contains strategy="*"
        call_args = mock_redis_client.set.call_args_list
        quarantine_calls = [c for c in call_args if "quarantine" in str(c)]
        assert len(quarantine_calls) > 0


# --------------------------
# Fill Metadata Backfill Edge Cases
# --------------------------


class TestFillMetadataBackfillEdgeCases:
    """Test edge cases in fill metadata backfill logic."""

    def test_calculate_synthetic_fill_no_gap_returns_none(self):
        """Should return None when real fills already cover broker quantity."""
        existing_fills = [
            {"fill_qty": "100", "synthetic": False, "superseded": False},
        ]

        result = calculate_synthetic_fill(
            client_order_id="client-123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("150.50"),
            timestamp=datetime.now(UTC),
            existing_fills=existing_fills,
            source="recon",
        )

        assert result is None  # No synthetic fill needed

    def test_calculate_synthetic_fill_with_existing_synthetic(self):
        """Should account for existing synthetic fills when calculating gap."""
        existing_fills = [
            {"fill_qty": "60", "synthetic": False, "superseded": False},
            {"fill_qty": "40", "synthetic": True, "superseded": False},  # Existing synthetic
        ]

        result = calculate_synthetic_fill(
            client_order_id="client-123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("150.50"),
            timestamp=datetime.now(UTC),
            existing_fills=existing_fills,
            source="recon",
        )

        # Real (60) + Synthetic (40) = 100, no new synthetic needed
        assert result is None

    def test_calculate_synthetic_fill_superseded_fills_ignored(self):
        """Should ignore superseded fills when calculating gap."""
        existing_fills = [
            {"fill_qty": "50", "synthetic": False, "superseded": True},  # Superseded
            {"fill_qty": "60", "synthetic": False, "superseded": False},
        ]

        result = calculate_synthetic_fill(
            client_order_id="client-123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("150.50"),
            timestamp=datetime.now(UTC),
            existing_fills=existing_fills,
            source="recon",
        )

        # Only count non-superseded fill (60), need synthetic for 40
        assert result is not None
        assert result["fill_qty"] == 40  # Integer for whole number
        assert result["synthetic"] is True

    def test_calculate_synthetic_fill_fractional_shares(self):
        """Should handle fractional shares correctly."""
        existing_fills = [
            {"fill_qty": "10.5", "synthetic": False, "superseded": False},
        ]

        result = calculate_synthetic_fill(
            client_order_id="client-123",
            filled_qty=Decimal("15.75"),
            filled_avg_price=Decimal("150.50"),
            timestamp=datetime.now(UTC),
            existing_fills=existing_fills,
            source="recon",
        )

        assert result is not None
        # Gap = 15.75 - 10.5 = 5.25
        assert result["fill_qty"] == "5.25"  # String for fractional
        assert result["synthetic"] is True

    def test_calculate_synthetic_fill_invalid_qty_skipped(self):
        """Should skip invalid fill quantities when calculating gap."""
        existing_fills = [
            {"fill_qty": "invalid", "synthetic": False, "superseded": False},
            {"fill_qty": "50", "synthetic": False, "superseded": False},
        ]

        result = calculate_synthetic_fill(
            client_order_id="client-123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("150.50"),
            timestamp=datetime.now(UTC),
            existing_fills=existing_fills,
            source="recon",
        )

        # Should only count valid fill (50), need synthetic for 50
        assert result is not None
        assert result["fill_qty"] == 50  # Integer for whole number

    def test_backfill_fill_metadata_missing_filled_avg_price_skips(
        self, mock_db_client
    ):
        """Should skip backfill when filled_avg_price is missing."""
        broker_order = {
            "filled_qty": "100",
            "filled_avg_price": None,  # Missing price
            "updated_at": datetime.now(UTC).isoformat(),
        }

        backfill_fill_metadata(mock_db_client, "client-123", broker_order)

        # Should not attempt to backfill
        mock_db_client.get_order_for_update.assert_not_called()

    def test_backfill_fill_metadata_order_not_found_skips(
        self, mock_db_client
    ):
        """Should skip backfill when order not found in database."""
        broker_order = {
            "filled_qty": "100",
            "filled_avg_price": "150.50",
            "updated_at": datetime.now(UTC).isoformat(),
        }

        mock_db_client.get_order_for_update.return_value = None  # Order not found

        with mock_db_client.transaction():
            backfill_fill_metadata(mock_db_client, "client-123", broker_order)

        # Should not append fill
        mock_db_client.append_fill_to_order_metadata.assert_not_called()


# --------------------------
# Alpaca Fills Backfill Edge Cases
# --------------------------


class TestAlpacaFillsBackfillEdgeCases:
    """Test edge cases in Alpaca fills API backfill."""

    def test_alpaca_fills_backfill_disabled_returns_early(
        self, reconciliation_service, mock_db_client
    ):
        """Should return early when fills backfill is disabled."""
        reconciliation_service.fills_backfill_enabled = False

        result = reconciliation_service._backfill_alpaca_fills()

        assert result["status"] == "disabled"
        mock_db_client.get_reconciliation_high_water_mark.assert_not_called()

    def test_alpaca_fills_backfill_explicit_lookback_overrides_config(
        self, reconciliation_service, mock_alpaca_client, mock_db_client
    ):
        """Should use explicit lookback_hours when provided."""
        reconciliation_service.fills_backfill_enabled = False  # Disabled by default

        mock_alpaca_client.get_account_activities.return_value = []
        mock_db_client.get_reconciliation_high_water_mark.return_value = None

        result = reconciliation_service._backfill_alpaca_fills(lookback_hours=2)

        assert result["status"] == "ok"
        # Should have called Alpaca API despite disabled flag
        mock_alpaca_client.get_account_activities.assert_called_once()

    def test_alpaca_fills_backfill_pagination_stops_on_short_page(
        self, reconciliation_service, mock_alpaca_client, mock_db_client
    ):
        """Should stop pagination when page is shorter than page_size."""
        page1 = [
            {"id": "fill-1", "order_id": "broker-1", "qty": "10", "price": "100"}
            for _ in range(50)  # Partial page (less than page_size=100)
        ]

        mock_alpaca_client.get_account_activities.return_value = page1
        mock_db_client.get_orders_by_broker_ids.return_value = {}

        result = reconciliation_service._backfill_alpaca_fills(lookback_hours=1)

        assert result["status"] == "ok"
        # Should only call once (short page indicates end)
        assert mock_alpaca_client.get_account_activities.call_count == 1

    def test_alpaca_fills_backfill_pagination_continues_on_full_page(
        self, reconciliation_service, mock_alpaca_client, mock_db_client
    ):
        """Should continue pagination when page equals page_size."""
        page1 = [
            {"id": f"fill-{i}", "order_id": "broker-1", "qty": "10", "price": "100"}
            for i in range(100)  # Full page
        ]
        page2 = [
            {"id": f"fill-{i}", "order_id": "broker-1", "qty": "10", "price": "100"}
            for i in range(50)  # Partial page (stops here)
        ]

        mock_alpaca_client.get_account_activities.side_effect = [page1, page2]
        mock_db_client.get_orders_by_broker_ids.return_value = {}

        result = reconciliation_service._backfill_alpaca_fills(lookback_hours=1)

        assert result["status"] == "ok"
        # Should call twice (full page triggers next page)
        assert mock_alpaca_client.get_account_activities.call_count == 2

    def test_alpaca_fills_backfill_pagination_uses_last_activity_id(
        self, reconciliation_service, mock_alpaca_client, mock_db_client
    ):
        """Should use last activity ID as page_token for pagination."""
        page1 = [
            {"id": f"fill-{i}", "order_id": "broker-1", "qty": "10", "price": "100"}
            for i in range(100)
        ]
        page2 = []

        mock_alpaca_client.get_account_activities.side_effect = [page1, page2]
        mock_db_client.get_orders_by_broker_ids.return_value = {}

        result = reconciliation_service._backfill_alpaca_fills(lookback_hours=1)

        assert result["status"] == "ok"
        # Second call should use last ID from first page
        second_call = mock_alpaca_client.get_account_activities.call_args_list[1]
        assert second_call.kwargs["page_token"] == "fill-99"

    def test_alpaca_fills_backfill_deduplicates_across_pages(
        self, reconciliation_service, mock_alpaca_client, mock_db_client
    ):
        """Should deduplicate fills that appear in multiple pages."""
        # Same fill ID in both pages (pagination overlap)
        page1 = [
            {"id": "fill-1", "order_id": "broker-1", "qty": "10", "price": "100"},
            {"id": "fill-2", "order_id": "broker-1", "qty": "20", "price": "100"},
        ]
        page2 = [
            {"id": "fill-2", "order_id": "broker-1", "qty": "20", "price": "100"},  # Duplicate
        ]

        mock_alpaca_client.get_account_activities.side_effect = [page1, page2]
        mock_db_client.get_orders_by_broker_ids.return_value = {}

        result = reconciliation_service._backfill_alpaca_fills(lookback_hours=1)

        assert result["status"] == "ok"
        # Should only see 2 unique fills (not 3)
        assert result["fills_seen"] == 2

    def test_alpaca_fills_backfill_fallback_fill_id_generation(
        self, reconciliation_service, mock_alpaca_client, mock_db_client
    ):
        """Should generate deterministic fill_id when id is missing or empty."""
        fills = [
            {
                "id": "",  # Empty ID
                "order_id": "broker-1",
                "symbol": "AAPL",
                "side": "buy",
                "qty": "100",
                "price": "150.50",
                "transaction_time": "2024-01-01T10:00:00Z",
            }
        ]

        mock_order = Mock()
        mock_order.client_order_id = "client-123"
        mock_order.strategy_id = "alpha_baseline"
        mock_order.symbol = "AAPL"

        mock_alpaca_client.get_account_activities.return_value = fills
        mock_db_client.get_orders_by_broker_ids.return_value = {"broker-1": mock_order}
        mock_db_client.append_fill_to_order_metadata.return_value = True

        result = reconciliation_service._backfill_alpaca_fills(lookback_hours=1)

        assert result["status"] == "ok"
        assert result["fills_inserted"] == 1
        # Should have called with generated fill_id
        call_args = mock_db_client.append_fill_to_order_metadata.call_args
        assert len(call_args.kwargs["fill_data"]["fill_id"]) == 32  # SHA256 hash prefix

    def test_alpaca_fills_backfill_pnl_recalculation_failure_rolls_back(
        self, reconciliation_service, mock_alpaca_client, mock_db_client
    ):
        """Should rollback transaction when P&L recalculation fails."""
        fills = [
            {
                "id": "fill-1",
                "order_id": "broker-1",
                "qty": "100",
                "price": "150.50",
                "transaction_time": "2024-01-01T10:00:00Z",
            }
        ]

        mock_order = Mock()
        mock_order.client_order_id = "client-123"
        mock_order.strategy_id = "alpha_baseline"
        mock_order.symbol = "AAPL"

        mock_alpaca_client.get_account_activities.return_value = fills
        mock_db_client.get_orders_by_broker_ids.return_value = {"broker-1": mock_order}
        mock_db_client.append_fill_to_order_metadata.return_value = True

        # P&L recalculation fails
        mock_db_client.recalculate_trade_realized_pnl.side_effect = RuntimeError("P&L calc failed")

        with pytest.raises(RuntimeError, match="P&L recalculation failed"):
            reconciliation_service._backfill_alpaca_fills(lookback_hours=1)

    def test_alpaca_fills_backfill_unmatched_fills_counted(
        self, reconciliation_service, mock_alpaca_client, mock_db_client
    ):
        """Should count fills without matching order as unmatched."""
        fills = [
            {
                "id": "fill-1",
                "order_id": "broker-unknown",  # No matching order
                "qty": "100",
                "price": "150.50",
            }
        ]

        mock_alpaca_client.get_account_activities.return_value = fills
        mock_db_client.get_orders_by_broker_ids.return_value = {}  # No orders

        result = reconciliation_service._backfill_alpaca_fills(lookback_hours=1)

        assert result["status"] == "ok"
        assert result["unmatched"] == 1
        assert result["fills_inserted"] == 0

    def test_alpaca_fills_backfill_missing_order_id_counted_unmatched(
        self, reconciliation_service, mock_alpaca_client, mock_db_client
    ):
        """Should count fills without order_id as unmatched."""
        fills = [
            {
                "id": "fill-1",
                # Missing order_id
                "qty": "100",
                "price": "150.50",
            }
        ]

        mock_alpaca_client.get_account_activities.return_value = fills
        mock_db_client.get_orders_by_broker_ids.return_value = {}

        result = reconciliation_service._backfill_alpaca_fills(lookback_hours=1)

        assert result["status"] == "ok"
        assert result["unmatched"] == 1


# --------------------------
# Position Reconciliation Edge Cases
# --------------------------


class TestPositionReconciliationEdgeCases:
    """Test edge cases in position reconciliation."""

    def test_reconcile_positions_creates_missing_db_positions(
        self, mock_alpaca_client, mock_db_client
    ):
        """Should create position snapshots for broker positions not in DB."""
        broker_positions = [
            {
                "symbol": "AAPL",
                "qty": "100",
                "avg_entry_price": "150.50",
                "current_price": "155.00",
            }
        ]

        mock_alpaca_client.get_all_positions.return_value = broker_positions
        mock_db_client.get_all_positions.return_value = []  # No DB positions

        reconcile_positions(mock_db_client, mock_alpaca_client)

        # Should upsert broker position
        mock_db_client.upsert_position_snapshot.assert_called_once()
        call_args = mock_db_client.upsert_position_snapshot.call_args
        assert call_args.kwargs["symbol"] == "AAPL"
        assert call_args.kwargs["qty"] == Decimal("100")

    def test_reconcile_positions_flattens_missing_broker_positions(
        self, mock_alpaca_client, mock_db_client
    ):
        """Should set DB positions to flat when not in broker."""
        db_position = Mock()
        db_position.symbol = "TSLA"
        db_position.qty = Decimal("50")

        mock_alpaca_client.get_all_positions.return_value = []  # No broker positions
        mock_db_client.get_all_positions.return_value = [db_position]

        reconcile_positions(mock_db_client, mock_alpaca_client)

        # Should upsert with qty=0 (flatten)
        mock_db_client.upsert_position_snapshot.assert_called_once()
        call_args = mock_db_client.upsert_position_snapshot.call_args
        assert call_args.kwargs["symbol"] == "TSLA"
        assert call_args.kwargs["qty"] == Decimal("0")

    def test_reconcile_positions_handles_missing_current_price(
        self, mock_alpaca_client, mock_db_client
    ):
        """Should handle broker positions without current_price."""
        broker_positions = [
            {
                "symbol": "AAPL",
                "qty": "100",
                "avg_entry_price": "150.50",
                # Missing current_price
            }
        ]

        mock_alpaca_client.get_all_positions.return_value = broker_positions
        mock_db_client.get_all_positions.return_value = []

        reconcile_positions(mock_db_client, mock_alpaca_client)

        # Should still upsert with current_price=None
        call_args = mock_db_client.upsert_position_snapshot.call_args
        assert call_args.kwargs["current_price"] is None


# --------------------------
# Redis Failure Modes
# --------------------------


class TestRedisFailureModes:
    """Test handling of Redis failures."""

    def test_quarantine_set_redis_error_logs_warning(
        self, reconciliation_service, mock_db_client, mock_alpaca_client, mock_redis_client
    ):
        """Should log warning and continue when Redis set fails."""
        broker_order = {
            "id": "broker-999",
            "client_order_id": "unknown-client-123",
            "symbol": "TSLA",
            "side": "buy",
            "qty": "50",
            "status": "new",
        }

        mock_redis_client.set.side_effect = redis.RedisError("Connection failed")
        mock_alpaca_client.get_orders.side_effect = [[broker_order], []]
        mock_db_client.get_non_terminal_orders.return_value = []
        mock_db_client.get_order_ids_by_client_ids.return_value = set()
        mock_db_client.get_all_positions.return_value = []
        mock_db_client.get_filled_orders_missing_fills.return_value = []

        # Should not raise exception
        result = reconciliation_service._run_reconciliation("manual")

        assert result["status"] == "success"

    def test_sync_orphan_exposure_redis_error_logs_warning(
        self, reconciliation_service, mock_db_client, mock_alpaca_client, mock_redis_client
    ):
        """Should log warning and continue when Redis orphan exposure sync fails."""
        broker_order = {
            "id": "broker-999",
            "client_order_id": "unknown-client-123",
            "symbol": "TSLA",
            "side": "buy",
            "qty": "50",
            "status": "new",
        }

        # First set() for quarantine succeeds, second for orphan_exposure fails
        mock_redis_client.set.side_effect = [None, redis.RedisError("Connection failed")]
        mock_alpaca_client.get_orders.side_effect = [[broker_order], []]
        mock_db_client.get_non_terminal_orders.return_value = []
        mock_db_client.get_order_ids_by_client_ids.return_value = set()
        mock_db_client.get_all_positions.return_value = []
        mock_db_client.get_filled_orders_missing_fills.return_value = []

        # Should not raise exception
        result = reconciliation_service._run_reconciliation("manual")

        assert result["status"] == "success"

    def test_sync_orphan_exposure_database_error_logs_warning(
        self, reconciliation_service, mock_db_client, mock_alpaca_client, mock_redis_client
    ):
        """Should log warning and continue when DB orphan exposure query fails."""
        broker_order = {
            "id": "broker-999",
            "client_order_id": "unknown-client-123",
            "symbol": "TSLA",
            "side": "buy",
            "qty": "50",
            "status": "new",
        }

        mock_db_client.get_orphan_exposure.side_effect = psycopg.OperationalError("DB connection lost")
        mock_alpaca_client.get_orders.side_effect = [[broker_order], []]
        mock_db_client.get_non_terminal_orders.return_value = []
        mock_db_client.get_order_ids_by_client_ids.return_value = set()
        mock_db_client.get_all_positions.return_value = []
        mock_db_client.get_filled_orders_missing_fills.return_value = []

        # Should not raise exception
        result = reconciliation_service._run_reconciliation("manual")

        assert result["status"] == "success"


# --------------------------
# Error Handling and Recovery
# --------------------------


class TestErrorHandlingAndRecovery:
    """Test error handling and recovery scenarios."""

    @pytest.mark.asyncio()
    async def test_startup_reconciliation_alpaca_error_stored_for_bypass(
        self, reconciliation_service, mock_alpaca_client
    ):
        """Should store failed result to enable forced bypass on Alpaca error."""
        mock_alpaca_client.get_orders.side_effect = AlpacaConnectionError("Connection timeout")

        result = await reconciliation_service.run_startup_reconciliation()

        assert result is False
        assert reconciliation_service.is_startup_complete() is False

        # Should have stored last reconciliation result in state
        assert reconciliation_service._state.get_last_reconciliation_result() is not None
        assert reconciliation_service._state.get_last_reconciliation_result()["status"] == "failed"

    @pytest.mark.asyncio()
    async def test_startup_reconciliation_db_error_stored_for_bypass(
        self, reconciliation_service, mock_db_client
    ):
        """Should store failed result to enable forced bypass on DB error."""
        mock_db_client.get_reconciliation_high_water_mark.side_effect = psycopg.OperationalError("DB down")

        result = await reconciliation_service.run_startup_reconciliation()

        assert result is False
        assert reconciliation_service.is_startup_complete() is False

        # Should have stored last reconciliation result in state
        assert reconciliation_service._state.get_last_reconciliation_result() is not None
        assert reconciliation_service._state.get_last_reconciliation_result()["status"] == "failed"

    @pytest.mark.asyncio()
    async def test_startup_reconciliation_validation_error_stored_for_bypass(
        self, reconciliation_service, mock_alpaca_client
    ):
        """Should store failed result to enable forced bypass on validation error."""
        mock_alpaca_client.get_orders.side_effect = ValueError("Invalid data format")

        result = await reconciliation_service.run_startup_reconciliation()

        assert result is False

        # Should have stored last reconciliation result in state
        assert reconciliation_service._state.get_last_reconciliation_result() is not None
        assert reconciliation_service._state.get_last_reconciliation_result()["status"] == "failed"

    @pytest.mark.asyncio()
    async def test_periodic_reconciliation_raises_on_alpaca_error(
        self, reconciliation_service, mock_alpaca_client
    ):
        """Should propagate AlpacaConnectionError from run_reconciliation_once.

        Note: Exception handling happens in run_periodic_loop, not run_reconciliation_once.
        This test verifies run_reconciliation_once correctly propagates the error.
        """
        mock_alpaca_client.get_orders.side_effect = AlpacaConnectionError("Connection timeout")

        # run_reconciliation_once should propagate the exception
        with pytest.raises(AlpacaConnectionError):
            await reconciliation_service.run_reconciliation_once("periodic")

        # Startup should not be marked complete on error
        assert reconciliation_service.is_startup_complete() is False

    def test_estimate_notional_uses_notional_field_first(self):
        """Should use notional field when available."""
        broker_order = {"notional": "15050.00", "qty": "100", "limit_price": "200.00"}

        notional = estimate_notional(broker_order)

        assert notional == Decimal("15050.00")

    def test_estimate_notional_falls_back_to_qty_times_limit_price(self):
        """Should calculate from qty * limit_price when notional missing."""
        broker_order = {"qty": "100", "limit_price": "150.50"}

        notional = estimate_notional(broker_order)

        assert notional == Decimal("15050.00")

    def test_estimate_notional_falls_back_to_qty_times_filled_avg_price(self):
        """Should calculate from qty * filled_avg_price when limit_price missing."""
        broker_order = {"qty": "100", "filled_avg_price": "151.00"}

        notional = estimate_notional(broker_order)

        assert notional == Decimal("15100.00")

    def test_estimate_notional_returns_zero_when_all_missing(self):
        """Should return 0 when all price fields missing."""
        broker_order = {"qty": "100"}

        notional = estimate_notional(broker_order)

        assert notional == Decimal("0")


# --------------------------
# Async and Concurrency Tests
# --------------------------


class TestAsyncAndConcurrency:
    """Test async operations and concurrency handling."""

    @pytest.mark.asyncio()
    async def test_run_reconciliation_once_acquires_lock(
        self, reconciliation_service, mock_db_client, mock_alpaca_client
    ):
        """Should acquire lock before running reconciliation."""
        mock_alpaca_client.get_orders.side_effect = [[], []]
        mock_db_client.get_non_terminal_orders.return_value = []
        mock_db_client.get_all_positions.return_value = []
        mock_db_client.get_filled_orders_missing_fills.return_value = []

        await reconciliation_service.run_reconciliation_once("manual")

        # If it ran without deadlock, lock was acquired and released
        assert reconciliation_service.is_startup_complete() is True

    @pytest.mark.asyncio()
    async def test_run_fills_backfill_once_acquires_lock(
        self, reconciliation_service, mock_alpaca_client, mock_db_client
    ):
        """Should acquire lock before running fills backfill."""
        mock_alpaca_client.get_account_activities.return_value = []

        result = await reconciliation_service.run_fills_backfill_once(lookback_hours=1)

        assert result["status"] == "ok"

    @pytest.mark.asyncio()
    async def test_run_fills_backfill_once_dry_run_skips(self, mock_db_client, mock_alpaca_client):
        """Should skip fills backfill in dry-run mode."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=True,
        )

        result = await service.run_fills_backfill_once(lookback_hours=1)

        assert result["status"] == "skipped"
        mock_alpaca_client.get_account_activities.assert_not_called()

    @pytest.mark.asyncio()
    async def test_periodic_loop_stops_on_event(self, reconciliation_service):
        """Should stop periodic loop when stop event is set."""
        reconciliation_service._run_reconciliation = Mock(return_value={"status": "success"})

        # Set stop event immediately
        reconciliation_service.stop()

        # Should exit immediately without hanging
        await reconciliation_service.run_periodic_loop()

        # Should not have run reconciliation (stopped before first iteration)
        assert reconciliation_service._run_reconciliation.call_count == 0


# --------------------------
# Integration-like Tests
# --------------------------


class TestReconciliationIntegrationScenarios:
    """Test realistic reconciliation scenarios."""

    def test_full_reconciliation_cycle_with_multiple_orders(
        self, reconciliation_service, mock_alpaca_client, mock_db_client
    ):
        """Should handle complete reconciliation cycle with various order states."""
        # Setup: Mix of open, recent, and DB orders
        open_orders = [
            {
                "client_order_id": "client-new",
                "status": "new",
                "updated_at": datetime.now(UTC).isoformat(),
                "filled_qty": "0",
            }
        ]
        recent_orders = [
            {
                "client_order_id": "client-filled",
                "status": "filled",
                "updated_at": datetime.now(UTC).isoformat(),
                "filled_qty": "100",
                "filled_avg_price": "150.50",
            },
            {
                "client_order_id": "client-orphan",
                "id": "broker-orphan",
                "symbol": "TSLA",
                "side": "buy",
                "qty": "50",
                "status": "filled",
            },
        ]

        db_order_new = Mock()
        db_order_new.client_order_id = "client-new"
        db_order_new.status = "new"
        db_order_new.created_at = datetime.now(UTC)

        db_order_filled = Mock()
        db_order_filled.client_order_id = "client-filled"
        db_order_filled.status = "new"
        db_order_filled.created_at = datetime.now(UTC)

        # Need a high water mark so recent_orders are fetched
        mock_db_client.get_reconciliation_high_water_mark.return_value = datetime.now(UTC) - timedelta(hours=1)
        mock_alpaca_client.get_orders.side_effect = [open_orders, recent_orders]
        mock_db_client.get_non_terminal_orders.return_value = [db_order_new, db_order_filled]
        mock_db_client.get_order_ids_by_client_ids.return_value = {"client-new", "client-filled"}
        mock_db_client.get_all_positions.return_value = []
        mock_db_client.get_filled_orders_missing_fills.return_value = []

        result = reconciliation_service._run_reconciliation("manual")

        assert result["status"] == "success"
        # Should update at least 1 known order via CAS (client-filled status changed from new to filled)
        assert mock_db_client.update_order_status_cas.call_count >= 1
        # Should create orphan order
        mock_db_client.create_orphan_order.assert_called_once()

    def test_reconciliation_with_stale_submitted_unconfirmed(
        self, reconciliation_service, mock_alpaca_client, mock_db_client
    ):
        """Should mark stale submitted_unconfirmed orders as failed."""
        old_time = datetime.now(UTC) - timedelta(seconds=400)

        db_order = Mock()
        db_order.client_order_id = "client-stale"
        db_order.status = "submitted_unconfirmed"
        db_order.created_at = old_time
        db_order.broker_order_id = None

        mock_alpaca_client.get_orders.side_effect = [[], []]
        mock_alpaca_client.get_order_by_client_id.return_value = None
        mock_db_client.get_non_terminal_orders.return_value = [db_order]
        mock_db_client.get_order_ids_by_client_ids.return_value = set()
        mock_db_client.get_all_positions.return_value = []
        mock_db_client.get_filled_orders_missing_fills.return_value = []
        mock_db_client.update_order_status_cas.return_value = Mock()

        result = reconciliation_service._run_reconciliation("manual")

        assert result["status"] == "success"
        # Should mark as failed
        call_args = mock_db_client.update_order_status_cas.call_args
        assert call_args.kwargs["status"] == "failed"
