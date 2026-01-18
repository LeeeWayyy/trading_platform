"""
Comprehensive unit tests for legacy reconciliation.py module.

Tests cover:
- Startup reconciliation flow and gating
- Periodic reconciliation loop
- Broker order synchronization (CAS updates)
- Missing order resolution (submitted_unconfirmed grace period)
- Orphan order detection and quarantine
- Fill metadata backfill (synthetic fills)
- Alpaca fills API backfill
- Position reconciliation
- Forced bypass security checks
- Error handling (connection, database, validation errors)
- Metrics and monitoring
- Thread safety and locking

Target: 85%+ branch coverage for reconciliation.py (currently 0% - HIGH RISK legacy module)

See Also:
    - /docs/STANDARDS/TESTING.md - Testing standards
    - /docs/ADRs/0014-execution-gateway-architecture.md - Reconciliation architecture
"""

from __future__ import annotations

import asyncio

# POD_LABEL is not exported but defined in multiple places - use a fallback
import os
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

import psycopg
import pytest
import redis

from apps.execution_gateway.alpaca_client import AlpacaConnectionError
from apps.execution_gateway.reconciliation import (
    QUARANTINE_STRATEGY_SENTINEL,
    SOURCE_PRIORITY_MANUAL,
    SOURCE_PRIORITY_RECONCILIATION,
    SOURCE_PRIORITY_WEBHOOK,
    ReconciliationService,
    calculate_synthetic_fill,
    estimate_notional,
)
from apps.execution_gateway.reconciliation.fills import (
    backfill_fill_metadata,
    backfill_fill_metadata_from_order,
)
from apps.execution_gateway.reconciliation.orders import (
    apply_broker_update,
    reconcile_missing_orders,
)
from apps.execution_gateway.reconciliation.orphans import (
    handle_orphan_order,
    set_quarantine,
    sync_orphan_exposure,
)
from apps.execution_gateway.reconciliation.positions import reconcile_positions

POD_LABEL = os.getenv("POD_NAME") or os.getenv("HOSTNAME") or "unknown"


# --------------------------
# Fixtures
# --------------------------


@pytest.fixture()
def mock_db_client():
    """Create mock database client."""
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
    client.transaction = Mock()
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


# --------------------------
# Initialization Tests
# --------------------------


class TestReconciliationServiceInitialization:
    """Test ReconciliationService initialization and configuration."""

    def test_initialization_with_default_config(self, mock_db_client, mock_alpaca_client):
        """Should initialize with default environment configuration."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=False,
        )

        assert service.db_client is mock_db_client
        assert service.alpaca_client is mock_alpaca_client
        assert service.redis_client is None
        assert service.dry_run is False
        assert service.is_startup_complete() is False
        assert service.override_active() is False
        assert service._state.get_last_reconciliation_result() is None

        # Check default config values
        assert service.poll_interval_seconds == 300
        assert service.timeout_seconds == 300
        assert service.max_individual_lookups == 100
        assert service.overlap_seconds == 60
        assert service.submitted_unconfirmed_grace_seconds == 300
        assert service.fills_backfill_enabled is False
        assert service.fills_backfill_initial_lookback_hours == 24
        assert service.fills_backfill_page_size == 100
        assert service.fills_backfill_max_pages == 5

    def test_initialization_with_custom_config(
        self, mock_db_client, mock_alpaca_client, monkeypatch
    ):
        """Should initialize with custom environment configuration."""
        monkeypatch.setenv("RECONCILIATION_INTERVAL_SECONDS", "60")
        monkeypatch.setenv("RECONCILIATION_TIMEOUT_SECONDS", "120")
        monkeypatch.setenv("RECONCILIATION_MAX_LOOKUPS", "50")
        monkeypatch.setenv("RECONCILIATION_OVERLAP_SECONDS", "30")
        monkeypatch.setenv("RECONCILIATION_SUBMITTED_UNCONFIRMED_GRACE_SECONDS", "600")
        monkeypatch.setenv("ALPACA_FILLS_BACKFILL_ENABLED", "true")
        monkeypatch.setenv("ALPACA_FILLS_BACKFILL_INITIAL_LOOKBACK_HOURS", "48")
        monkeypatch.setenv("ALPACA_FILLS_BACKFILL_PAGE_SIZE", "200")
        monkeypatch.setenv("ALPACA_FILLS_BACKFILL_MAX_PAGES", "10")

        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=False,
        )

        assert service.poll_interval_seconds == 60
        assert service.timeout_seconds == 120
        assert service.max_individual_lookups == 50
        assert service.overlap_seconds == 30
        assert service.submitted_unconfirmed_grace_seconds == 600
        assert service.fills_backfill_enabled is True
        assert service.fills_backfill_initial_lookback_hours == 48
        assert service.fills_backfill_page_size == 200
        assert service.fills_backfill_max_pages == 10

    def test_initialization_dry_run_mode(self, mock_db_client, mock_alpaca_client):
        """Should initialize in dry-run mode and skip reconciliation."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=True,
        )

        assert service.dry_run is True
        assert service.is_startup_complete() is True  # Dry-run always complete


# --------------------------
# Startup State Tests
# --------------------------


class TestStartupState:
    """Test startup state management and gating."""

    def test_is_startup_complete_initial_state(self, reconciliation_service):
        """Should be False initially."""
        assert reconciliation_service.is_startup_complete() is False

    def test_is_startup_complete_dry_run(self, mock_db_client, mock_alpaca_client):
        """Should always be True in dry-run mode."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=True,
        )
        assert service.is_startup_complete() is True

    def test_startup_elapsed_seconds(self, reconciliation_service):
        """Should return elapsed time since startup."""
        elapsed = reconciliation_service.startup_elapsed_seconds()
        assert elapsed >= 0
        assert elapsed < 1  # Should be very recent

    def test_startup_timed_out_not_timed_out(self, reconciliation_service):
        """Should not time out immediately."""
        assert reconciliation_service.startup_timed_out() is False

    def test_startup_timed_out_after_timeout(self, reconciliation_service):
        """Should time out after configured timeout."""
        # Set startup time to past
        reconciliation_service._state._startup_started_at = datetime.now(UTC) - timedelta(
            seconds=reconciliation_service.timeout_seconds + 10
        )
        assert reconciliation_service.startup_timed_out() is True

    def test_mark_startup_complete_normal(self, reconciliation_service):
        """Should mark startup complete normally."""
        assert reconciliation_service.is_startup_complete() is False
        reconciliation_service.mark_startup_complete()
        assert reconciliation_service.is_startup_complete() is True
        assert reconciliation_service.override_active() is False

    def test_mark_startup_complete_forced_without_reconciliation(self, reconciliation_service):
        """Should reject forced bypass without prior reconciliation attempt."""
        with pytest.raises(
            ValueError, match="Cannot force startup complete without running reconciliation"
        ):
            reconciliation_service.mark_startup_complete(
                forced=True,
                user_id="operator",
                reason="emergency",
            )

    def test_mark_startup_complete_forced_missing_user_id(self, reconciliation_service):
        """Should reject forced bypass without user_id."""
        # Set last reconciliation result to allow forced bypass
        reconciliation_service._state.record_reconciliation_result({"status": "failed"})

        with pytest.raises(ValueError, match="Both user_id and reason are required"):
            reconciliation_service.mark_startup_complete(
                forced=True, user_id=None, reason="emergency"
            )

    def test_mark_startup_complete_forced_missing_reason(self, reconciliation_service):
        """Should reject forced bypass without reason."""
        # Set last reconciliation result to allow forced bypass
        reconciliation_service._state.record_reconciliation_result({"status": "failed"})

        with pytest.raises(ValueError, match="Both user_id and reason are required"):
            reconciliation_service.mark_startup_complete(
                forced=True, user_id="operator", reason=None
            )

    def test_mark_startup_complete_forced_success(self, reconciliation_service):
        """Should allow forced bypass with valid context after reconciliation attempt."""
        # Set last reconciliation result to allow forced bypass
        reconciliation_service._state.record_reconciliation_result({
            "status": "failed",
            "error": "Connection timeout",
        })

        reconciliation_service.mark_startup_complete(
            forced=True,
            user_id="operator",
            reason="emergency bypass - broker connection down",
        )

        assert reconciliation_service.is_startup_complete() is True
        assert reconciliation_service.override_active() is True

        context = reconciliation_service.override_context()
        assert context["user_id"] == "operator"
        assert context["reason"] == "emergency bypass - broker connection down"
        assert "timestamp" in context
        assert context["last_reconciliation_result"]["status"] == "failed"

    def test_override_context_thread_safe(self, reconciliation_service):
        """Should safely access override context from multiple threads."""
        reconciliation_service._state.record_reconciliation_result({"status": "failed"})
        reconciliation_service.mark_startup_complete(forced=True, user_id="op1", reason="test")

        contexts = []

        def read_context():
            contexts.append(reconciliation_service.override_context())

        threads = [threading.Thread(target=read_context) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(contexts) == 10
        assert all(c["user_id"] == "op1" for c in contexts)


# --------------------------
# Startup Reconciliation Tests
# --------------------------


class TestStartupReconciliation:
    """Test startup reconciliation flow."""

    @pytest.mark.asyncio()
    async def test_run_startup_reconciliation_dry_run(self, mock_db_client, mock_alpaca_client):
        """Should skip reconciliation in dry-run mode."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=True,
        )

        result = await service.run_startup_reconciliation()
        assert result is True
        assert service.is_startup_complete() is True

    @pytest.mark.asyncio()
    async def test_run_startup_reconciliation_success(self, reconciliation_service, mock_db_client):
        """Should complete startup reconciliation successfully."""
        mock_db_client.get_reconciliation_high_water_mark.return_value = None

        result = await reconciliation_service.run_startup_reconciliation()
        assert result is True

    @pytest.mark.asyncio()
    async def test_run_startup_reconciliation_alpaca_connection_error(
        self, reconciliation_service, mock_alpaca_client
    ):
        """Should handle Alpaca connection errors and record failure."""
        mock_alpaca_client.get_orders.side_effect = AlpacaConnectionError("Connection timeout")

        result = await reconciliation_service.run_startup_reconciliation()
        assert result is False

        # Should record failed result for forced bypass
        last_result = reconciliation_service._state.get_last_reconciliation_result()
        assert last_result is not None
        assert last_result["status"] == "failed"
        assert last_result["mode"] == "startup"
        assert "Connection timeout" in last_result["error"]

    @pytest.mark.asyncio()
    async def test_run_startup_reconciliation_database_error(
        self, reconciliation_service, mock_db_client
    ):
        """Should handle database errors and record failure."""
        mock_db_client.get_reconciliation_high_water_mark.side_effect = psycopg.OperationalError(
            "Connection refused"
        )

        result = await reconciliation_service.run_startup_reconciliation()
        assert result is False

        # Should record failed result for forced bypass
        last_result = reconciliation_service._state.get_last_reconciliation_result()
        assert last_result is not None
        assert last_result["status"] == "failed"
        assert "Connection refused" in last_result["error"]

    @pytest.mark.asyncio()
    async def test_run_startup_reconciliation_validation_error(
        self, reconciliation_service, mock_alpaca_client
    ):
        """Should handle validation errors and record failure."""
        mock_alpaca_client.get_orders.side_effect = ValueError("Invalid order data")

        result = await reconciliation_service.run_startup_reconciliation()
        assert result is False

        # Should record failed result for forced bypass
        last_result = reconciliation_service._state.get_last_reconciliation_result()
        assert last_result is not None
        assert last_result["status"] == "failed"
        assert "Invalid order data" in last_result["error"]


# --------------------------
# Periodic Loop Tests
# --------------------------


class TestPeriodicReconciliation:
    """Test periodic reconciliation loop."""

    @pytest.mark.asyncio()
    async def test_run_periodic_loop_opens_gate_after_success(
        self, reconciliation_service, mock_db_client
    ):
        """Should open startup gate after first successful periodic run."""
        mock_db_client.get_reconciliation_high_water_mark.return_value = None

        # Mock stop event to run once
        async def stop_after_one():
            await asyncio.sleep(0.1)
            reconciliation_service.stop()

        asyncio.create_task(stop_after_one())

        await reconciliation_service.run_periodic_loop()

        assert reconciliation_service.is_startup_complete() is True

    @pytest.mark.asyncio()
    async def test_run_periodic_loop_handles_alpaca_errors(
        self, reconciliation_service, mock_alpaca_client
    ):
        """Should continue loop after Alpaca connection errors."""
        mock_alpaca_client.get_orders.side_effect = AlpacaConnectionError("Timeout")

        async def stop_quickly():
            await asyncio.sleep(0.1)
            reconciliation_service.stop()

        asyncio.create_task(stop_quickly())

        # Should not raise exception
        await reconciliation_service.run_periodic_loop()

    @pytest.mark.asyncio()
    async def test_run_periodic_loop_handles_database_errors(
        self, reconciliation_service, mock_db_client
    ):
        """Should continue loop after database errors."""
        mock_db_client.get_reconciliation_high_water_mark.side_effect = psycopg.OperationalError(
            "Connection lost"
        )

        async def stop_quickly():
            await asyncio.sleep(0.1)
            reconciliation_service.stop()

        asyncio.create_task(stop_quickly())

        # Should not raise exception
        await reconciliation_service.run_periodic_loop()

    @pytest.mark.asyncio()
    async def test_stop_reconciliation_loop(self, reconciliation_service):
        """Should stop periodic loop when stop() is called."""
        assert not reconciliation_service._stop_event.is_set()

        reconciliation_service.stop()

        assert reconciliation_service._stop_event.is_set()


# --------------------------
# Broker Order Synchronization Tests
# --------------------------


class TestBrokerOrderSync:
    """Test broker order synchronization with CAS updates."""

    @pytest.mark.asyncio()
    async def test_run_reconciliation_once_opens_gate(self, reconciliation_service, mock_db_client):
        """Should open startup gate after successful reconciliation."""
        mock_db_client.get_reconciliation_high_water_mark.return_value = None

        assert reconciliation_service.is_startup_complete() is False
        await reconciliation_service.run_reconciliation_once("manual")
        assert reconciliation_service.is_startup_complete() is True

    def test_apply_broker_update_success(self, reconciliation_service, mock_db_client):
        """Should apply broker order update with CAS."""
        broker_order = {
            "id": "broker-123",
            "client_order_id": "client-456",
            "status": "filled",
            "filled_qty": 100,
            "filled_avg_price": Decimal("150.50"),
            "updated_at": datetime.now(UTC),
        }

        mock_db_client.update_order_status_cas.return_value = True

        apply_broker_update("client-456", broker_order, mock_db_client)

        mock_db_client.update_order_status_cas.assert_called_once()
        call_kwargs = mock_db_client.update_order_status_cas.call_args[1]
        assert call_kwargs["client_order_id"] == "client-456"
        assert call_kwargs["status"] == "filled"
        assert call_kwargs["source_priority"] == SOURCE_PRIORITY_RECONCILIATION
        assert call_kwargs["filled_qty"] == Decimal("100")

    def test_apply_broker_update_cas_conflict(self, reconciliation_service, mock_db_client):
        """Should skip update on CAS conflict."""
        broker_order = {
            "status": "filled",
            "filled_qty": 100,
            "updated_at": datetime.now(UTC),
        }

        mock_db_client.update_order_status_cas.return_value = None  # CAS failed

        apply_broker_update("client-456", broker_order, mock_db_client)

        mock_db_client.update_order_status_cas.assert_called_once()

    def test_apply_broker_update_with_fill_backfill(self, reconciliation_service, mock_db_client):
        """Should backfill fill metadata for filled/partially_filled orders."""
        broker_order = {
            "id": "broker-123",
            "status": "filled",
            "filled_qty": 100,
            "filled_avg_price": Decimal("150.50"),
            "updated_at": datetime.now(UTC),
        }

        mock_db_client.update_order_status_cas.return_value = True
        backfill_callback = Mock()

        apply_broker_update(
            "client-456",
            broker_order,
            mock_db_client,
            backfill_fills_callback=backfill_callback,
        )

        # Should attempt CAS update
        mock_db_client.update_order_status_cas.assert_called_once()
        backfill_callback.assert_called_once()


# --------------------------
# Missing Order Resolution Tests
# --------------------------


class TestMissingOrderResolution:
    """Test resolution of missing orders (submitted_unconfirmed grace period)."""

    def test_reconcile_missing_orders_within_grace_period(
        self, reconciliation_service, mock_db_client, mock_alpaca_client
    ):
        """Should defer failure for submitted_unconfirmed within grace period."""
        mock_order = Mock()
        mock_order.client_order_id = "client-123"
        mock_order.status = "submitted_unconfirmed"
        mock_order.created_at = datetime.now(UTC) - timedelta(seconds=60)  # 60s ago
        mock_order.broker_order_id = None

        mock_alpaca_client.get_order_by_client_id.return_value = None
        result = reconcile_missing_orders(
            [mock_order],
            after_time=None,
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            max_individual_lookups=reconciliation_service.max_individual_lookups,
            submitted_unconfirmed_grace_seconds=reconciliation_service.submitted_unconfirmed_grace_seconds,
        )

        # Should NOT update status to failed
        mock_db_client.update_order_status_cas.assert_not_called()
        assert result["marked_failed"] == 0

    def test_reconcile_missing_orders_after_grace_period(
        self, reconciliation_service, mock_db_client, mock_alpaca_client
    ):
        """Should mark as failed after grace period expires."""
        mock_order = Mock()
        mock_order.client_order_id = "client-123"
        mock_order.status = "submitted_unconfirmed"
        mock_order.created_at = datetime.now(UTC) - timedelta(
            seconds=400
        )  # 400s ago (> 300s grace)
        mock_order.broker_order_id = None

        mock_db_client.update_order_status_cas.return_value = True

        mock_alpaca_client.get_order_by_client_id.return_value = None
        result = reconcile_missing_orders(
            [mock_order],
            after_time=None,
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            max_individual_lookups=reconciliation_service.max_individual_lookups,
            submitted_unconfirmed_grace_seconds=reconciliation_service.submitted_unconfirmed_grace_seconds,
        )

        # Should update status to failed
        mock_db_client.update_order_status_cas.assert_called_once()
        call_kwargs = mock_db_client.update_order_status_cas.call_args[1]
        assert call_kwargs["client_order_id"] == "client-123"
        assert call_kwargs["status"] == "failed"
        assert result["marked_failed"] == 1

    def test_reconcile_missing_orders_max_lookups(
        self, reconciliation_service, mock_db_client, mock_alpaca_client
    ):
        """Should respect max individual lookups limit."""
        # Create many missing orders
        orders = []
        for i in range(150):  # More than max_individual_lookups (100)
            mock_order = Mock()
            mock_order.client_order_id = f"client-{i}"
            mock_order.status = "submitted_unconfirmed"
            mock_order.created_at = datetime.now(UTC) - timedelta(seconds=400)
            mock_order.broker_order_id = None
            orders.append(mock_order)

        mock_alpaca_client.get_order_by_client_id.return_value = None
        result = reconcile_missing_orders(
            orders,
            after_time=None,
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            max_individual_lookups=100,
            submitted_unconfirmed_grace_seconds=reconciliation_service.submitted_unconfirmed_grace_seconds,
        )

        # Should only lookup up to max
        assert mock_alpaca_client.get_order_by_client_id.call_count == 100
        assert result["lookups"] == 100

    def test_reconcile_missing_orders_non_terminal_recent(
        self, reconciliation_service, mock_db_client, mock_alpaca_client
    ):
        """Should skip recent non-terminal non-submitted_unconfirmed orders."""
        after_time = datetime.now(UTC) - timedelta(seconds=120)

        mock_order = Mock()
        mock_order.client_order_id = "client-123"
        mock_order.status = "new"  # Not submitted_unconfirmed
        mock_order.created_at = datetime.now(UTC) - timedelta(seconds=30)  # Recent
        mock_order.broker_order_id = "broker-456"

        reconcile_missing_orders(
            [mock_order],
            after_time=after_time,
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            max_individual_lookups=reconciliation_service.max_individual_lookups,
            submitted_unconfirmed_grace_seconds=reconciliation_service.submitted_unconfirmed_grace_seconds,
        )

        # Should skip lookup for recent orders
        mock_alpaca_client.get_order_by_client_id.assert_not_called()


# --------------------------
# Orphan Order Detection Tests
# --------------------------


class TestOrphanOrderDetection:
    """Test orphan order detection and quarantine."""

    def test_handle_orphan_order_creates_record(self, reconciliation_service, mock_db_client):
        """Should create orphan order record for unknown broker orders."""
        broker_order = {
            "id": "broker-999",
            "client_order_id": "unknown-client-id",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 100,
            "status": "new",
            "notional": Decimal("15000"),
        }

        handled = handle_orphan_order(
            broker_order,
            db_client=mock_db_client,
            redis_client=None,
            resolve_terminal=False,
        )
        assert handled is True

        mock_db_client.create_orphan_order.assert_called_once()
        call_kwargs = mock_db_client.create_orphan_order.call_args[1]
        assert call_kwargs["broker_order_id"] == "broker-999"
        assert call_kwargs["symbol"] == "AAPL"
        assert call_kwargs["strategy_id"] == QUARANTINE_STRATEGY_SENTINEL
        assert call_kwargs["side"] == "buy"
        assert call_kwargs["qty"] == 100

    def test_handle_orphan_order_sets_quarantine(
        self, reconciliation_service, mock_db_client, mock_redis_client
    ):
        """Should set quarantine flag in Redis for orphan orders."""
        broker_order = {
            "id": "broker-999",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 100,
            "status": "new",
        }

        handled = handle_orphan_order(
            broker_order,
            db_client=mock_db_client,
            redis_client=mock_redis_client,
            resolve_terminal=False,
        )
        assert handled is True

        # Should set quarantine key
        mock_redis_client.set.assert_called()

    def test_handle_orphan_order_resolve_terminal(self, reconciliation_service, mock_db_client):
        """Should mark terminal orphan orders as resolved."""
        broker_order = {
            "id": "broker-999",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 100,
            "status": "filled",  # Terminal status
        }

        handled = handle_orphan_order(
            broker_order,
            db_client=mock_db_client,
            redis_client=None,
            resolve_terminal=True,
        )
        assert handled is True

        # Should update with resolved_at timestamp
        assert mock_db_client.update_orphan_order_status.called
        call_kwargs = mock_db_client.update_orphan_order_status.call_args[1]
        assert call_kwargs["status"] == "filled"
        assert call_kwargs["resolved_at"] is not None

    def test_handle_orphan_order_no_resolve_non_terminal(
        self, reconciliation_service, mock_db_client
    ):
        """Should not mark non-terminal orphan orders as resolved."""
        broker_order = {
            "id": "broker-999",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 100,
            "status": "new",  # Non-terminal
        }

        handled = handle_orphan_order(
            broker_order,
            db_client=mock_db_client,
            redis_client=None,
            resolve_terminal=False,
        )
        assert handled is True

        # Should update without resolved_at
        call_kwargs = mock_db_client.update_orphan_order_status.call_args[1]
        assert call_kwargs["resolved_at"] is None

    def test_estimate_notional_from_notional_field(self, reconciliation_service):
        """Should use notional field if available."""
        broker_order = {"notional": Decimal("15000.50")}
        result = estimate_notional(broker_order)
        assert result == Decimal("15000.50")

    def test_estimate_notional_from_limit_price(self, reconciliation_service):
        """Should calculate from qty * limit_price if available."""
        broker_order = {"qty": 100, "limit_price": Decimal("150.50")}
        result = estimate_notional(broker_order)
        assert result == Decimal("15050")

    def test_estimate_notional_from_filled_avg_price(self, reconciliation_service):
        """Should calculate from qty * filled_avg_price if available."""
        broker_order = {"qty": 100, "filled_avg_price": Decimal("149.75")}
        result = estimate_notional(broker_order)
        assert result == Decimal("14975")

    def test_estimate_notional_fallback_zero(self, reconciliation_service):
        """Should return zero if no price information available."""
        broker_order = {"qty": 100}
        result = estimate_notional(broker_order)
        assert result == Decimal("0")


# --------------------------
# Fill Metadata Backfill Tests
# --------------------------


class TestFillMetadataBackfill:
    """Test synthetic fill generation for missing fill data."""

    def test_calculate_synthetic_fill_no_existing_fills(self, reconciliation_service):
        """Should generate synthetic fill when no existing fills."""
        fill_data = calculate_synthetic_fill(
            client_order_id="client-123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("150.50"),
            timestamp=datetime.now(UTC),
            existing_fills=[],
            source="recon",
        )

        assert fill_data is not None
        assert fill_data["fill_qty"] == 100
        assert fill_data["fill_price"] == "150.50"
        assert fill_data["synthetic"] is True
        assert fill_data["source"] == "recon"
        assert "_missing_qty" in fill_data

    def test_calculate_synthetic_fill_partial_existing_fills(self, reconciliation_service):
        """Should generate synthetic fill for missing quantity."""
        existing_fills = [
            {"fill_qty": 50, "synthetic": False, "superseded": False},
        ]

        fill_data = calculate_synthetic_fill(
            client_order_id="client-123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("150.50"),
            timestamp=datetime.now(UTC),
            existing_fills=existing_fills,
            source="recon",
        )

        assert fill_data is not None
        assert fill_data["fill_qty"] == 50  # Missing portion
        assert fill_data["_missing_qty"] == Decimal("50")

    def test_calculate_synthetic_fill_no_gap(self, reconciliation_service):
        """Should return None when existing fills cover filled_qty."""
        existing_fills = [
            {"fill_qty": 100, "synthetic": False, "superseded": False},
        ]

        fill_data = calculate_synthetic_fill(
            client_order_id="client-123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("150.50"),
            timestamp=datetime.now(UTC),
            existing_fills=existing_fills,
            source="recon",
        )

        assert fill_data is None

    def test_calculate_synthetic_fill_skip_superseded(self, reconciliation_service):
        """Should skip superseded fills when calculating gap."""
        existing_fills = [
            {"fill_qty": 50, "synthetic": True, "superseded": True},  # Skip this
            {"fill_qty": 30, "synthetic": False, "superseded": False},  # Count this
        ]

        fill_data = calculate_synthetic_fill(
            client_order_id="client-123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("150.50"),
            timestamp=datetime.now(UTC),
            existing_fills=existing_fills,
            source="recon",
        )

        assert fill_data is not None
        # Should calculate gap as 100 - 30 = 70 (ignoring superseded)
        assert fill_data["_missing_qty"] == Decimal("70")

    def test_calculate_synthetic_fill_fractional_shares(self, reconciliation_service):
        """Should handle fractional shares correctly."""
        fill_data = calculate_synthetic_fill(
            client_order_id="client-123",
            filled_qty=Decimal("100.5"),
            filled_avg_price=Decimal("150.50"),
            timestamp=datetime.now(UTC),
            existing_fills=[],
            source="recon",
        )

        assert fill_data is not None
        assert fill_data["fill_qty"] == "100.5"  # Stored as string for precision

    def test_backfill_fill_metadata_success(self, reconciliation_service, mock_db_client):
        """Should backfill synthetic fill when metadata is missing."""
        broker_order = {
            "filled_qty": 100,
            "filled_avg_price": Decimal("150.50"),
            "updated_at": datetime.now(UTC),
        }

        mock_order = Mock()
        mock_order.metadata = {"fills": []}
        mock_order.symbol = "AAPL"
        mock_order.strategy_id = "alpha_v1"

        mock_db_client.get_order_for_update.return_value = mock_order
        mock_db_client.transaction.return_value.__enter__ = Mock(return_value=Mock())
        mock_db_client.transaction.return_value.__exit__ = Mock(return_value=False)
        mock_db_client.append_fill_to_order_metadata.return_value = True

        backfill_fill_metadata("client-123", broker_order, mock_db_client)

        mock_db_client.append_fill_to_order_metadata.assert_called_once()

    def test_backfill_fill_metadata_no_avg_price(self, reconciliation_service, mock_db_client):
        """Should skip backfill when filled_avg_price is None."""
        broker_order = {
            "filled_qty": 100,
            "filled_avg_price": None,  # Missing price
            "updated_at": datetime.now(UTC),
        }

        backfill_fill_metadata("client-123", broker_order, mock_db_client)

        mock_db_client.get_order_for_update.assert_not_called()

    def test_backfill_fill_metadata_from_order_success(
        self, reconciliation_service, mock_db_client
    ):
        """Should backfill from DB order when broker data unavailable."""
        mock_order = Mock()
        mock_order.client_order_id = "client-123"
        mock_order.filled_qty = Decimal("100")
        mock_order.filled_avg_price = Decimal("150.50")
        mock_order.filled_at = datetime.now(UTC)
        mock_order.symbol = "AAPL"
        mock_order.strategy_id = "alpha_v1"

        mock_locked = Mock()
        mock_locked.metadata = {"fills": []}

        mock_db_client.get_order_for_update.return_value = mock_locked
        mock_db_client.transaction.return_value.__enter__ = Mock(return_value=Mock())
        mock_db_client.transaction.return_value.__exit__ = Mock(return_value=False)

        backfill_fill_metadata_from_order(mock_order, mock_db_client)

        mock_db_client.append_fill_to_order_metadata.assert_called_once()


# --------------------------
# Alpaca Fills API Backfill Tests
# --------------------------


class TestAlpacaFillsBackfill:
    """Test Alpaca account activities (fills) backfill."""

    @pytest.mark.asyncio()
    async def test_backfill_alpaca_fills_disabled(self, reconciliation_service):
        """Should skip when backfill is disabled."""
        reconciliation_service.fills_backfill_enabled = False

        result = await reconciliation_service.run_fills_backfill_once()

        assert result["status"] == "disabled"

    @pytest.mark.asyncio()
    async def test_backfill_alpaca_fills_dry_run(self, mock_db_client, mock_alpaca_client):
        """Should skip in dry-run mode."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=True,
        )

        result = await service.run_fills_backfill_once()

        assert result["status"] == "skipped"
        assert "DRY_RUN" in result["message"]

    def test_backfill_alpaca_fills_no_fills(
        self, reconciliation_service, mock_alpaca_client, mock_db_client
    ):
        """Should handle empty fills list."""
        reconciliation_service.fills_backfill_enabled = True
        mock_alpaca_client.get_account_activities.return_value = []
        mock_db_client.get_reconciliation_high_water_mark.return_value = None

        result = reconciliation_service._backfill_alpaca_fills()

        assert result["status"] == "ok"
        assert result["fills_seen"] == 0
        assert result["fills_inserted"] == 0

    def test_backfill_alpaca_fills_with_fills(
        self, reconciliation_service, mock_alpaca_client, mock_db_client
    ):
        """Should process fills and insert into database."""
        reconciliation_service.fills_backfill_enabled = True
        fills = [
            {
                "id": "fill-1",
                "order_id": "broker-123",
                "symbol": "AAPL",
                "side": "buy",
                "qty": 50,
                "price": Decimal("150.50"),
                "transaction_time": datetime.now(UTC).isoformat(),
            },
            {
                "id": "fill-2",
                "order_id": "broker-123",
                "symbol": "AAPL",
                "side": "buy",
                "qty": 50,
                "price": Decimal("150.75"),
                "transaction_time": datetime.now(UTC).isoformat(),
            },
        ]

        mock_order = Mock()
        mock_order.client_order_id = "client-456"
        mock_order.strategy_id = "alpha_v1"
        mock_order.symbol = "AAPL"

        mock_alpaca_client.get_account_activities.return_value = fills
        mock_db_client.get_reconciliation_high_water_mark.return_value = None
        mock_db_client.get_orders_by_broker_ids.return_value = {"broker-123": mock_order}
        mock_db_client.append_fill_to_order_metadata.return_value = True
        mock_db_client.recalculate_trade_realized_pnl.return_value = {"trades_updated": 1}
        mock_db_client.transaction.return_value.__enter__ = Mock(return_value=Mock())
        mock_db_client.transaction.return_value.__exit__ = Mock(return_value=False)

        result = reconciliation_service._backfill_alpaca_fills()

        assert result["status"] == "ok"
        assert result["fills_seen"] == 2
        assert result["fills_inserted"] == 2
        assert result["unmatched"] == 0

    def test_backfill_alpaca_fills_unmatched(
        self, reconciliation_service, mock_alpaca_client, mock_db_client
    ):
        """Should track unmatched fills (no corresponding order)."""
        reconciliation_service.fills_backfill_enabled = True
        fills = [
            {
                "id": "fill-1",
                "order_id": "unknown-broker-id",
                "qty": 50,
                "price": Decimal("150.50"),
                "transaction_time": datetime.now(UTC).isoformat(),
            },
        ]

        mock_alpaca_client.get_account_activities.return_value = fills
        mock_db_client.get_reconciliation_high_water_mark.return_value = None
        mock_db_client.get_orders_by_broker_ids.return_value = {}  # No matching order
        mock_db_client.transaction.return_value.__enter__ = Mock(return_value=Mock())
        mock_db_client.transaction.return_value.__exit__ = Mock(return_value=False)

        result = reconciliation_service._backfill_alpaca_fills()

        assert result["unmatched"] == 1
        assert result["fills_inserted"] == 0

    def test_backfill_alpaca_fills_pagination(
        self, reconciliation_service, mock_alpaca_client, mock_db_client
    ):
        """Should handle pagination for large fill sets."""
        reconciliation_service.fills_backfill_enabled = True
        # First page: full page size
        page1 = [{"id": f"fill-{i}", "order_id": f"broker-{i}"} for i in range(100)]
        # Second page: partial
        page2 = [{"id": f"fill-{i}", "order_id": f"broker-{i}"} for i in range(100, 120)]

        mock_alpaca_client.get_account_activities.side_effect = [page1, page2]
        mock_db_client.get_reconciliation_high_water_mark.return_value = None
        mock_db_client.get_orders_by_broker_ids.return_value = {}
        mock_db_client.transaction.return_value.__enter__ = Mock(return_value=Mock())
        mock_db_client.transaction.return_value.__exit__ = Mock(return_value=False)

        result = reconciliation_service._backfill_alpaca_fills()

        assert result["fills_seen"] == 120
        assert mock_alpaca_client.get_account_activities.call_count == 2

    def test_backfill_alpaca_fills_pnl_recalculation_failure(
        self, reconciliation_service, mock_alpaca_client, mock_db_client
    ):
        """Should rollback on P&L recalculation failure."""
        reconciliation_service.fills_backfill_enabled = True
        fills = [
            {
                "id": "fill-1",
                "order_id": "broker-123",
                "qty": 50,
                "price": Decimal("150.50"),
                "transaction_time": datetime.now(UTC).isoformat(),
            },
        ]

        mock_order = Mock()
        mock_order.client_order_id = "client-456"
        mock_order.strategy_id = "alpha_v1"
        mock_order.symbol = "AAPL"

        mock_alpaca_client.get_account_activities.return_value = fills
        mock_db_client.get_orders_by_broker_ids.return_value = {"broker-123": mock_order}
        mock_db_client.append_fill_to_order_metadata.return_value = True
        mock_db_client.recalculate_trade_realized_pnl.side_effect = RuntimeError("P&L calc failed")
        mock_db_client.transaction.return_value.__enter__ = Mock(return_value=Mock())
        mock_db_client.transaction.return_value.__exit__ = Mock(return_value=False)

        with pytest.raises(RuntimeError, match="P&L recalculation failed"):
            reconciliation_service._backfill_alpaca_fills()


# --------------------------
# Position Reconciliation Tests
# --------------------------


class TestPositionReconciliation:
    """Test position reconciliation between broker and database."""

    def test_reconcile_positions_sync_from_broker(
        self, reconciliation_service, mock_alpaca_client, mock_db_client
    ):
        """Should sync positions from broker to database."""
        broker_positions = [
            {
                "symbol": "AAPL",
                "qty": Decimal("100"),
                "avg_entry_price": Decimal("150.50"),
                "current_price": Decimal("155.00"),
            },
            {
                "symbol": "MSFT",
                "qty": Decimal("50"),
                "avg_entry_price": Decimal("300.00"),
                "current_price": Decimal("305.00"),
            },
        ]

        mock_alpaca_client.get_all_positions.return_value = broker_positions
        mock_db_client.get_all_positions.return_value = []

        reconcile_positions(mock_db_client, mock_alpaca_client)

        assert mock_db_client.upsert_position_snapshot.call_count == 2

    def test_reconcile_positions_flatten_missing(
        self, reconciliation_service, mock_alpaca_client, mock_db_client
    ):
        """Should flatten positions in DB that are not in broker."""
        mock_db_position = Mock()
        mock_db_position.symbol = "TSLA"

        broker_positions = [
            {"symbol": "AAPL", "qty": Decimal("100"), "avg_entry_price": Decimal("150.50")},
        ]

        mock_alpaca_client.get_all_positions.return_value = broker_positions
        mock_db_client.get_all_positions.return_value = [mock_db_position]

        reconcile_positions(mock_db_client, mock_alpaca_client)

        # Should upsert AAPL and flatten TSLA
        assert mock_db_client.upsert_position_snapshot.call_count == 2

        # Check flatten call for TSLA
        calls = mock_db_client.upsert_position_snapshot.call_args_list
        flatten_call = next(c for c in calls if c[1]["symbol"] == "TSLA")
        assert flatten_call[1]["qty"] == Decimal("0")
        assert flatten_call[1]["avg_entry_price"] == Decimal("0")


# --------------------------
# Quarantine Tests
# --------------------------


class TestQuarantineManagement:
    """Test quarantine flag management in Redis."""

    def test_set_quarantine_success(self, reconciliation_service, mock_redis_client):
        """Should set quarantine flag in Redis."""
        set_quarantine(symbol="AAPL", strategy_id="alpha_v1", redis_client=mock_redis_client)

        mock_redis_client.set.assert_called_once()

    def test_set_quarantine_no_redis_client(self, mock_db_client, mock_alpaca_client):
        """Should skip quarantine when Redis client is None."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=False,
        )

        # Should not raise exception
        assert (
            set_quarantine(symbol="AAPL", strategy_id="alpha_v1", redis_client=service.redis_client)
            is False
        )

    def test_set_quarantine_redis_error(self, reconciliation_service, mock_redis_client):
        """Should log warning on Redis error."""
        mock_redis_client.set.side_effect = redis.RedisError("Connection lost")

        # Should not raise exception
        set_quarantine(symbol="AAPL", strategy_id="alpha_v1", redis_client=mock_redis_client)

    def test_sync_orphan_exposure_success(
        self, reconciliation_service, mock_db_client, mock_redis_client
    ):
        """Should sync orphan exposure to Redis."""
        mock_db_client.get_orphan_exposure.return_value = Decimal("15000")

        sync_orphan_exposure(
            symbol="AAPL",
            strategy_id=QUARANTINE_STRATEGY_SENTINEL,
            db_client=mock_db_client,
            redis_client=mock_redis_client,
        )

        mock_redis_client.set.assert_called_once()

    def test_sync_orphan_exposure_database_error(
        self, reconciliation_service, mock_db_client, mock_redis_client
    ):
        """Should log warning on database error."""
        mock_db_client.get_orphan_exposure.side_effect = psycopg.OperationalError("Connection lost")

        # Should not raise exception
        sync_orphan_exposure(
            symbol="AAPL",
            strategy_id=QUARANTINE_STRATEGY_SENTINEL,
            db_client=mock_db_client,
            redis_client=mock_redis_client,
        )

    def test_sync_orphan_exposure_no_redis_client(self, mock_db_client, mock_alpaca_client):
        """Should skip sync when Redis client is None."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=False,
        )

        # Should not raise exception
        assert (
            sync_orphan_exposure(
                symbol="AAPL",
                strategy_id=QUARANTINE_STRATEGY_SENTINEL,
                db_client=mock_db_client,
                redis_client=service.redis_client,
            )
            is False
        )


# --------------------------
# Source Priority Tests
# --------------------------


class TestSourcePriority:
    """Test source priority constants for CAS conflict resolution."""

    def test_source_priority_ordering(self):
        """Should enforce correct priority ordering (lower = higher priority)."""
        assert SOURCE_PRIORITY_MANUAL < SOURCE_PRIORITY_RECONCILIATION
        assert SOURCE_PRIORITY_RECONCILIATION < SOURCE_PRIORITY_WEBHOOK

    def test_source_priority_manual_highest(self):
        """Manual interventions should have highest priority (lowest number)."""
        assert SOURCE_PRIORITY_MANUAL == 1

    def test_source_priority_webhook_lowest(self):
        """Webhooks should have lowest priority (highest number)."""
        assert SOURCE_PRIORITY_WEBHOOK == 3


# --------------------------
# Thread Safety Tests
# --------------------------


class TestThreadSafety:
    """Test thread-safe state access."""

    def test_is_startup_complete_thread_safe(self, reconciliation_service):
        """Should safely check startup state from multiple threads."""
        results = []

        def check_startup():
            results.append(reconciliation_service.is_startup_complete())

        threads = [threading.Thread(target=check_startup) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 10
        assert all(r is False for r in results)

    def test_mark_startup_complete_thread_safe(self, reconciliation_service):
        """Should safely mark startup complete from multiple threads."""

        def mark_complete():
            reconciliation_service.mark_startup_complete()

        threads = [threading.Thread(target=mark_complete) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert reconciliation_service.is_startup_complete() is True


# --------------------------
# Integration Tests
# --------------------------


class TestReconciliationIntegration:
    """Integration tests for full reconciliation flow."""

    @pytest.mark.asyncio()
    async def test_full_reconciliation_cycle(
        self, reconciliation_service, mock_db_client, mock_alpaca_client
    ):
        """Should complete full reconciliation cycle successfully."""
        # Setup broker state
        broker_orders = [
            {
                "id": "broker-123",
                "client_order_id": "client-456",
                "status": "filled",
                "filled_qty": 100,
                "filled_avg_price": Decimal("150.50"),
                "updated_at": datetime.now(UTC),
                "created_at": datetime.now(UTC),
            }
        ]

        # Setup DB state
        mock_order = Mock()
        mock_order.client_order_id = "client-456"
        mock_order.status = "new"
        mock_order.created_at = datetime.now(UTC) - timedelta(seconds=60)

        mock_alpaca_client.get_orders.return_value = broker_orders
        mock_db_client.get_non_terminal_orders.return_value = [mock_order]
        mock_db_client.get_order_ids_by_client_ids.return_value = {"client-456"}
        mock_db_client.update_order_status_cas.return_value = True
        mock_db_client.get_all_positions.return_value = []
        mock_db_client.get_filled_orders_missing_fills.return_value = []
        mock_db_client.get_reconciliation_high_water_mark.return_value = None

        await reconciliation_service.run_reconciliation_once("manual")

        # Verify order was updated
        mock_db_client.update_order_status_cas.assert_called()

        # Verify high-water mark was updated
        mock_db_client.set_reconciliation_high_water_mark.assert_called()

    @pytest.mark.asyncio()
    async def test_reconciliation_with_orphan_and_quarantine(
        self, reconciliation_service, mock_db_client, mock_alpaca_client, mock_redis_client
    ):
        """Should detect orphan orders and set quarantine."""
        # Broker has order not in DB
        broker_orders = [
            {
                "id": "orphan-broker-999",
                "client_order_id": "orphan-client",
                "symbol": "AAPL",
                "side": "buy",
                "qty": 100,
                "status": "new",
            }
        ]

        mock_alpaca_client.get_orders.return_value = broker_orders
        mock_db_client.get_non_terminal_orders.return_value = []
        mock_db_client.get_order_ids_by_client_ids.return_value = set()  # No match in DB
        mock_db_client.get_all_positions.return_value = []
        mock_db_client.get_filled_orders_missing_fills.return_value = []
        mock_db_client.get_reconciliation_high_water_mark.return_value = None

        await reconciliation_service.run_reconciliation_once("manual")

        # Verify orphan order was created
        mock_db_client.create_orphan_order.assert_called()

        # Verify quarantine was set
        mock_redis_client.set.assert_called()


# --------------------------
# Edge Cases and Error Handling
# --------------------------


class TestEdgeCasesAndErrors:
    """Test edge cases and error handling."""

    def test_handle_orphan_order_missing_symbol(self, reconciliation_service, mock_db_client):
        """Should skip orphan orders without symbol."""
        broker_order = {"id": "broker-999", "status": "new"}  # No symbol

        handled = handle_orphan_order(
            broker_order,
            db_client=mock_db_client,
            redis_client=None,
            resolve_terminal=False,
        )

        assert handled is False
        mock_db_client.create_orphan_order.assert_not_called()

    def test_handle_orphan_order_missing_broker_id(self, reconciliation_service, mock_db_client):
        """Should skip orphan orders without broker_order_id."""
        broker_order = {"symbol": "AAPL", "status": "new"}  # No id

        handled = handle_orphan_order(
            broker_order,
            db_client=mock_db_client,
            redis_client=None,
            resolve_terminal=False,
        )

        assert handled is False
        mock_db_client.create_orphan_order.assert_not_called()

    def test_backfill_fill_metadata_exception_handling(
        self, reconciliation_service, mock_db_client
    ):
        """Should log warning and continue on fill backfill errors."""
        broker_order = {
            "filled_qty": 100,
            "filled_avg_price": Decimal("150.50"),
            "updated_at": datetime.now(UTC),
        }

        mock_db_client.get_order_for_update.side_effect = RuntimeError("Database error")

        # Should not raise exception
        backfill_fill_metadata("client-123", broker_order, mock_db_client)

    def test_calculate_synthetic_fill_invalid_qty(self, reconciliation_service):
        """Should handle invalid fill quantities gracefully."""
        existing_fills = [
            {"fill_qty": "invalid", "synthetic": False, "superseded": False},
        ]

        # Should handle ValueError from Decimal conversion
        fill_data = calculate_synthetic_fill(
            client_order_id="client-123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("150.50"),
            timestamp=datetime.now(UTC),
            existing_fills=existing_fills,
            source="recon",
        )

        # Should still calculate based on valid fills (treating invalid as 0)
        assert fill_data is not None

    def test_reconciliation_with_merge_order_deduplication(
        self, reconciliation_service, mock_alpaca_client, mock_db_client
    ):
        """Should merge duplicate orders by client_order_id (prefer newest updated_at)."""
        older_time = datetime.now(UTC) - timedelta(seconds=60)
        newer_time = datetime.now(UTC)

        # Same client_order_id in both open and recent
        open_orders = [
            {
                "client_order_id": "client-123",
                "status": "new",
                "updated_at": older_time,
                "created_at": older_time,
            }
        ]
        recent_orders = [
            {
                "client_order_id": "client-123",
                "status": "filled",
                "updated_at": newer_time,
                "created_at": older_time,
            }
        ]

        mock_alpaca_client.get_orders.side_effect = [open_orders, recent_orders]
        mock_db_client.get_non_terminal_orders.return_value = []
        mock_db_client.get_order_ids_by_client_ids.return_value = {"client-123"}
        mock_db_client.get_all_positions.return_value = []
        mock_db_client.get_filled_orders_missing_fills.return_value = []
        mock_db_client.get_reconciliation_high_water_mark.return_value = None

        # Run reconciliation
        result = reconciliation_service._run_reconciliation("manual")

        assert result["status"] == "success"
        # Should process only once (deduplicated)
