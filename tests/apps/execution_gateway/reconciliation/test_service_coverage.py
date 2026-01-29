"""P0 Coverage Tests for ReconciliationService - Additional branch coverage to reach 95%+ target.

Coverage gaps addressed (42% → 95%):
- __init__ environment variable parsing
- State delegation methods
- Error handling in startup reconciliation
- Dry-run mode paths
- run_fills_backfill_once paths
- _run_reconciliation core logic branches
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import Mock, patch

import psycopg
import pytest

from apps.execution_gateway.alpaca_client import AlpacaConnectionError
from apps.execution_gateway.reconciliation.service import ReconciliationService


@pytest.fixture()
def mock_db_client():
    """Create mock database client."""
    client = Mock()
    client.get_reconciliation_high_water_mark.return_value = None
    client.get_non_terminal_orders.return_value = []
    client.get_order_ids_by_client_ids.return_value = set()
    client.set_reconciliation_high_water_mark.return_value = None
    return client


@pytest.fixture()
def mock_alpaca_client():
    """Create mock Alpaca client."""
    client = Mock()
    client.get_orders.return_value = []
    return client


@pytest.fixture()
def mock_redis_client():
    """Create mock Redis client."""
    return Mock()


class TestReconciliationServiceInit:
    """Tests for ReconciliationService initialization."""

    def test_default_config_values(self, mock_db_client, mock_alpaca_client, mock_redis_client):
        """Test default configuration values from environment."""
        with patch.dict("os.environ", {}, clear=True):
            service = ReconciliationService(
                db_client=mock_db_client,
                alpaca_client=mock_alpaca_client,
                redis_client=mock_redis_client,
                dry_run=False,
            )

            assert service.poll_interval_seconds == 300
            assert service.timeout_seconds == 300
            assert service.max_individual_lookups == 100
            assert service.overlap_seconds == 60
            assert service.fills_backfill_enabled is False

    def test_custom_config_from_env(self, mock_db_client, mock_alpaca_client, mock_redis_client):
        """Test custom configuration from environment variables."""
        env_vars = {
            "RECONCILIATION_INTERVAL_SECONDS": "600",
            "RECONCILIATION_TIMEOUT_SECONDS": "120",
            "RECONCILIATION_MAX_LOOKUPS": "50",
            "RECONCILIATION_OVERLAP_SECONDS": "30",
            "RECONCILIATION_SUBMITTED_UNCONFIRMED_GRACE_SECONDS": "600",
            "ALPACA_FILLS_BACKFILL_ENABLED": "true",
            "ALPACA_FILLS_BACKFILL_INITIAL_LOOKBACK_HOURS": "48",
            "ALPACA_FILLS_BACKFILL_PAGE_SIZE": "200",
            "ALPACA_FILLS_BACKFILL_MAX_PAGES": "10",
        }
        with patch.dict("os.environ", env_vars, clear=True):
            service = ReconciliationService(
                db_client=mock_db_client,
                alpaca_client=mock_alpaca_client,
                redis_client=mock_redis_client,
                dry_run=False,
            )

            assert service.poll_interval_seconds == 600
            assert service.timeout_seconds == 120
            assert service.max_individual_lookups == 50
            assert service.overlap_seconds == 30
            assert service.submitted_unconfirmed_grace_seconds == 600
            assert service.fills_backfill_enabled is True
            assert service.fills_backfill_initial_lookback_hours == 48
            assert service.fills_backfill_page_size == 200
            assert service.fills_backfill_max_pages == 10

    def test_fills_backfill_enabled_variations(self, mock_db_client, mock_alpaca_client):
        """Test fills_backfill_enabled accepts various truthy values."""
        for value in ["1", "true", "yes", "on", "TRUE", "Yes"]:
            with patch.dict("os.environ", {"ALPACA_FILLS_BACKFILL_ENABLED": value}):
                service = ReconciliationService(
                    db_client=mock_db_client,
                    alpaca_client=mock_alpaca_client,
                    redis_client=None,
                    dry_run=False,
                )
                assert service.fills_backfill_enabled is True

    def test_dry_run_mode(self, mock_db_client, mock_alpaca_client):
        """Test dry_run mode is stored."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=True,
        )
        assert service.dry_run is True


class TestStateDelegation:
    """Tests for state query methods delegated to ReconciliationState."""

    def test_is_startup_complete_initially_false(self, mock_db_client, mock_alpaca_client):
        """Test is_startup_complete delegates to state and returns False initially."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=False,  # Use non-dry_run mode
        )
        # Initially not complete
        assert service.is_startup_complete() is False

    def test_is_startup_complete_in_dry_run_mode(self, mock_db_client, mock_alpaca_client):
        """Test is_startup_complete returns True in dry_run mode."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=True,  # dry_run mode
        )
        # In dry_run mode, startup is considered complete
        assert service.is_startup_complete() is True

    def test_startup_elapsed_seconds_delegates(self, mock_db_client, mock_alpaca_client):
        """Test startup_elapsed_seconds delegates to state."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=False,
        )
        # Should return a float >= 0
        assert service.startup_elapsed_seconds() >= 0.0

    def test_startup_timed_out_delegates(self, mock_db_client, mock_alpaca_client):
        """Test startup_timed_out delegates to state."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=False,
        )
        # Initially not timed out
        assert service.startup_timed_out() is False

    def test_mark_startup_complete_non_forced(self, mock_db_client, mock_alpaca_client):
        """Test mark_startup_complete without forced flag."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=False,
        )
        assert service.is_startup_complete() is False

        # Non-forced mark complete works without prior reconciliation
        service.mark_startup_complete(forced=False)

        assert service.is_startup_complete() is True
        assert service.override_active() is False  # No override when not forced

    def test_mark_startup_complete_forced_requires_prior_recon(
        self, mock_db_client, mock_alpaca_client
    ):
        """Test mark_startup_complete with forced flag requires prior reconciliation."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=False,
        )

        # Forced complete without prior reconciliation raises ValueError
        with pytest.raises(ValueError, match="Cannot force startup complete"):
            service.mark_startup_complete(forced=True, user_id="test_user", reason="Test")

    def test_mark_startup_complete_forced_after_recon(self, mock_db_client, mock_alpaca_client):
        """Test mark_startup_complete with forced flag after reconciliation attempt."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=False,
        )
        # First, record a failed reconciliation result
        service._state.record_reconciliation_result({"status": "failed"})

        # Now forced complete should work
        service.mark_startup_complete(forced=True, user_id="test_user", reason="Test")

        assert service.is_startup_complete() is True
        assert service.override_active() is True

    def test_override_context_delegates(self, mock_db_client, mock_alpaca_client):
        """Test override_context delegates to state."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=False,
        )
        # Record a reconciliation result first
        service._state.record_reconciliation_result({"status": "failed"})
        service.mark_startup_complete(forced=True, user_id="test_user", reason="Testing")

        context = service.override_context()

        assert context["user_id"] == "test_user"
        assert context["reason"] == "Testing"


class TestLifecycleMethods:
    """Tests for lifecycle methods."""

    def test_stop_sets_event(self, mock_db_client, mock_alpaca_client):
        """Test stop() sets the stop event."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=False,
        )
        assert service._stop_event.is_set() is False

        service.stop()

        assert service._stop_event.is_set() is True


class TestStartupReconciliation:
    """Tests for run_startup_reconciliation."""

    @pytest.mark.asyncio()
    async def test_dry_run_returns_true(self, mock_db_client, mock_alpaca_client):
        """Test dry_run mode returns True immediately."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=True,
        )

        result = await service.run_startup_reconciliation()

        assert result is True

    @pytest.mark.asyncio()
    async def test_success_returns_true(self, mock_db_client, mock_alpaca_client):
        """Test successful reconciliation returns True."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=False,
        )
        # Mock _run_reconciliation to succeed
        service._run_reconciliation = Mock(return_value={"status": "success"})

        result = await service.run_startup_reconciliation()

        assert result is True
        assert service.is_startup_complete() is True

    @pytest.mark.asyncio()
    async def test_alpaca_error_returns_false(self, mock_db_client, mock_alpaca_client):
        """Test AlpacaConnectionError returns False."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=False,
        )
        # Mock _run_reconciliation to raise AlpacaConnectionError
        service._run_reconciliation = Mock(side_effect=AlpacaConnectionError("Connection failed"))

        result = await service.run_startup_reconciliation()

        assert result is False

    @pytest.mark.asyncio()
    async def test_database_error_returns_false(self, mock_db_client, mock_alpaca_client):
        """Test database error returns False."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=False,
        )
        # Mock _run_reconciliation to raise database error
        service._run_reconciliation = Mock(side_effect=psycopg.OperationalError("DB error"))

        result = await service.run_startup_reconciliation()

        assert result is False

    @pytest.mark.asyncio()
    async def test_integrity_error_returns_false(self, mock_db_client, mock_alpaca_client):
        """Test IntegrityError returns False."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=False,
        )
        # Mock _run_reconciliation to raise integrity error
        service._run_reconciliation = Mock(
            side_effect=psycopg.IntegrityError("Constraint violation")
        )

        result = await service.run_startup_reconciliation()

        assert result is False

    @pytest.mark.asyncio()
    async def test_value_error_returns_false(self, mock_db_client, mock_alpaca_client):
        """Test ValueError returns False."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=False,
        )
        # Mock _run_reconciliation to raise value error
        service._run_reconciliation = Mock(side_effect=ValueError("Invalid data"))

        result = await service.run_startup_reconciliation()

        assert result is False


class TestRunReconciliationOnce:
    """Tests for run_reconciliation_once."""

    @pytest.mark.asyncio()
    async def test_dry_run_skips_reconciliation(self, mock_db_client, mock_alpaca_client):
        """Test dry_run mode skips reconciliation."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=True,
        )
        service._run_reconciliation = Mock()

        await service.run_reconciliation_once("test")

        # Should not call _run_reconciliation
        service._run_reconciliation.assert_not_called()


class TestRunFillsBackfillOnce:
    """Tests for run_fills_backfill_once."""

    @pytest.mark.asyncio()
    async def test_dry_run_returns_skipped(self, mock_db_client, mock_alpaca_client):
        """Test dry_run mode returns skipped status."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=True,
        )

        result = await service.run_fills_backfill_once()

        assert result["status"] == "skipped"
        assert "DRY_RUN" in result["message"]

    @pytest.mark.asyncio()
    async def test_normal_mode_calls_backfill(self, mock_db_client, mock_alpaca_client):
        """Test normal mode calls _backfill_alpaca_fills."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=False,
        )
        # Mock _backfill_alpaca_fills
        service._backfill_alpaca_fills = Mock(return_value={"status": "success"})

        result = await service.run_fills_backfill_once(lookback_hours=12, recalc_all_trades=True)

        assert result["status"] == "success"
        service._backfill_alpaca_fills.assert_called_once_with(
            lookback_hours=12,
            recalc_all_trades=True,
        )


class TestRunReconciliation:
    """Tests for _run_reconciliation core logic."""

    def test_full_reconciliation_flow(self, mock_db_client, mock_alpaca_client, mock_redis_client):
        """Test full reconciliation flow with mocked sub-modules."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=mock_redis_client,
            dry_run=False,
        )

        # Mock sub-module functions
        with patch(
            "apps.execution_gateway.reconciliation.service.reconcile_known_orders"
        ) as mock_known:
            with patch(
                "apps.execution_gateway.reconciliation.service.reconcile_missing_orders"
            ) as mock_missing:
                with patch(
                    "apps.execution_gateway.reconciliation.service.detect_orphans"
                ) as mock_orphans:
                    with patch(
                        "apps.execution_gateway.reconciliation.service.backfill_terminal_fills"
                    ) as mock_terminal:
                        with patch(
                            "apps.execution_gateway.reconciliation.service.reconcile_positions"
                        ) as mock_positions:
                            with patch(
                                "apps.execution_gateway.reconciliation.service.backfill_missing_fills_scan"
                            ) as mock_scan:
                                with patch(
                                    "apps.execution_gateway.reconciliation.service.reconcile_pending_modifications"
                                ) as mock_mods:
                                    # Mock _backfill_alpaca_fills to avoid complexity
                                    service._backfill_alpaca_fills = Mock(return_value={})

                                    result = service._run_reconciliation("startup")

        assert result["status"] == "success"
        assert result["mode"] == "startup"
        mock_known.assert_called_once()
        mock_missing.assert_called_once()
        mock_orphans.assert_called_once()
        mock_terminal.assert_called_once()
        mock_positions.assert_called_once()
        mock_scan.assert_called_once()
        mock_mods.assert_called_once()
        mock_db_client.set_reconciliation_high_water_mark.assert_called_once()

    def test_with_last_check_time(self, mock_db_client, mock_alpaca_client):
        """Test reconciliation with existing high water mark."""
        mock_db_client.get_reconciliation_high_water_mark.return_value = datetime.now(UTC)

        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=False,
        )

        with patch("apps.execution_gateway.reconciliation.service.reconcile_known_orders"):
            with patch("apps.execution_gateway.reconciliation.service.reconcile_missing_orders"):
                with patch("apps.execution_gateway.reconciliation.service.detect_orphans"):
                    with patch(
                        "apps.execution_gateway.reconciliation.service.backfill_terminal_fills"
                    ):
                        with patch(
                            "apps.execution_gateway.reconciliation.service.reconcile_positions"
                        ):
                            with patch(
                                "apps.execution_gateway.reconciliation.service.backfill_missing_fills_scan"
                            ):
                                with patch(
                                    "apps.execution_gateway.reconciliation.service.reconcile_pending_modifications"
                                ):
                                    service._backfill_alpaca_fills = Mock(return_value={})

                                    result = service._run_reconciliation("periodic")

        # Should call get_orders twice (open and recent)
        assert mock_alpaca_client.get_orders.call_count == 2
        assert result["status"] == "success"

    def test_backfill_failure_logged_not_raised(self, mock_db_client, mock_alpaca_client):
        """Test that backfill failure is logged but doesn't fail reconciliation."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=False,
        )
        # Mock _backfill_alpaca_fills to raise an exception
        service._backfill_alpaca_fills = Mock(side_effect=Exception("Backfill failed"))

        with patch("apps.execution_gateway.reconciliation.service.reconcile_known_orders"):
            with patch("apps.execution_gateway.reconciliation.service.reconcile_missing_orders"):
                with patch("apps.execution_gateway.reconciliation.service.detect_orphans"):
                    with patch(
                        "apps.execution_gateway.reconciliation.service.backfill_terminal_fills"
                    ):
                        with patch(
                            "apps.execution_gateway.reconciliation.service.reconcile_positions"
                        ):
                            with patch(
                                "apps.execution_gateway.reconciliation.service.backfill_missing_fills_scan"
                            ):
                                with patch(
                                    "apps.execution_gateway.reconciliation.service.reconcile_pending_modifications"
                                ):
                                    # Should not raise, just log warning
                                    result = service._run_reconciliation("test")

        assert result["status"] == "success"

    def test_with_broker_orders(self, mock_db_client, mock_alpaca_client):
        """Test reconciliation with actual broker orders."""
        mock_order = {"client_order_id": "order_123", "status": "filled"}
        mock_alpaca_client.get_orders.return_value = [mock_order]
        mock_db_client.get_order_ids_by_client_ids.return_value = {"order_123"}

        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=False,
        )

        with patch("apps.execution_gateway.reconciliation.service.reconcile_known_orders"):
            with patch("apps.execution_gateway.reconciliation.service.reconcile_missing_orders"):
                with patch("apps.execution_gateway.reconciliation.service.detect_orphans"):
                    with patch(
                        "apps.execution_gateway.reconciliation.service.backfill_terminal_fills"
                    ):
                        with patch(
                            "apps.execution_gateway.reconciliation.service.reconcile_positions"
                        ):
                            with patch(
                                "apps.execution_gateway.reconciliation.service.backfill_missing_fills_scan"
                            ):
                                with patch(
                                    "apps.execution_gateway.reconciliation.service.reconcile_pending_modifications"
                                ):
                                    service._backfill_alpaca_fills = Mock(return_value={})

                                    result = service._run_reconciliation("test")

        assert result["open_orders_checked"] == 1


class TestBackfillAlpacaFills:
    """Tests for _backfill_alpaca_fills."""

    def test_delegates_to_fills_module(self, mock_db_client, mock_alpaca_client):
        """Test _backfill_alpaca_fills delegates to fills module."""
        service = ReconciliationService(
            db_client=mock_db_client,
            alpaca_client=mock_alpaca_client,
            redis_client=None,
            dry_run=False,
        )

        with patch(
            "apps.execution_gateway.reconciliation.service.backfill_alpaca_fills"
        ) as mock_backfill:
            mock_backfill.return_value = {"fills_processed": 10}

            result = service._backfill_alpaca_fills(lookback_hours=24, recalc_all_trades=True)

            mock_backfill.assert_called_once()
            assert result["fills_processed"] == 10
