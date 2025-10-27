"""
Tests for SliceScheduler APScheduler-based execution.

Validates scheduler lifecycle, slice execution with safety guards,
retry logic, and job cancellation.

Test Coverage:
    - Initialization and lifecycle (start/shutdown)
    - Slice scheduling and job creation
    - Kill switch blocking (MANDATORY safety check)
    - Circuit breaker blocking (MANDATORY safety check)
    - Retry logic on connection errors
    - Non-retryable error handling
    - Job cancellation
    - Edge cases (empty slices, already canceled, etc.)
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

from apps.execution_gateway.alpaca_client import (
    AlpacaConnectionError,
    AlpacaExecutor,
    AlpacaRejectionError,
    AlpacaValidationError,
)
from apps.execution_gateway.database import DatabaseClient
from apps.execution_gateway.schemas import SliceDetail
from apps.execution_gateway.slice_scheduler import SliceScheduler
from libs.risk_management.breaker import CircuitBreaker
from libs.risk_management.kill_switch import KillSwitch


class TestSliceSchedulerInitialization:
    """Tests for SliceScheduler initialization and lifecycle."""

    def test_initialization_creates_scheduler(self):
        """Test successful initialization creates BackgroundScheduler."""
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
        )

        assert scheduler.kill_switch is kill_switch
        assert scheduler.breaker is breaker
        assert scheduler.db is db
        assert scheduler.executor is executor
        assert scheduler.scheduler is not None

    def test_start_starts_scheduler(self):
        """Test start() starts the background scheduler."""
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
        )

        # Mock the scheduler start method
        scheduler.scheduler.start = MagicMock()

        scheduler.start()

        scheduler.scheduler.start.assert_called_once()

    def test_shutdown_stops_scheduler(self):
        """Test shutdown() stops the scheduler and waits for jobs."""
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
        )

        # Mock the scheduler shutdown method
        scheduler.scheduler.shutdown = MagicMock()

        scheduler.shutdown(wait=True)

        scheduler.scheduler.shutdown.assert_called_once_with(wait=True)


class TestScheduleSlices:
    """Tests for schedule_slices method."""

    def test_schedule_slices_creates_jobs(self):
        """Test scheduling slices creates APScheduler jobs with correct IDs."""
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
        )

        # Mock add_job
        scheduler.scheduler.add_job = MagicMock()

        now = datetime.now(UTC)
        slices = [
            SliceDetail(
                slice_num=0,
                qty=20,
                scheduled_time=now,
                client_order_id="child0",
                status="pending_new",
            ),
            SliceDetail(
                slice_num=1,
                qty=20,
                scheduled_time=now + timedelta(minutes=1),
                client_order_id="child1",
                status="pending_new",
            ),
        ]

        job_ids = scheduler.schedule_slices(
            parent_order_id="parent123",
            slices=slices,
            symbol="AAPL",
            side="buy",
            order_type="market",
            limit_price=None,
            stop_price=None,
            time_in_force="day",
        )

        # Verify jobs created
        assert len(job_ids) == 2
        assert job_ids[0] == "parent123_slice_0"
        assert job_ids[1] == "parent123_slice_1"

        # Verify add_job called twice
        assert scheduler.scheduler.add_job.call_count == 2

    def test_schedule_slices_with_limit_order(self):
        """Test scheduling limit order slices includes limit_price."""
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
        )

        scheduler.scheduler.add_job = MagicMock()

        now = datetime.now(UTC)
        slices = [
            SliceDetail(
                slice_num=0,
                qty=20,
                scheduled_time=now,
                client_order_id="child0",
                status="pending_new",
            ),
        ]

        job_ids = scheduler.schedule_slices(
            parent_order_id="parent456",
            slices=slices,
            symbol="TSLA",
            side="sell",
            order_type="limit",
            limit_price=Decimal("200.00"),
            stop_price=None,
            time_in_force="day",
        )

        assert len(job_ids) == 1
        assert job_ids[0] == "parent456_slice_0"

        # Verify kwargs passed to add_job
        call_kwargs = scheduler.scheduler.add_job.call_args[1]["kwargs"]
        assert call_kwargs["limit_price"] == Decimal("200.00")

    def test_schedule_empty_slices_list(self):
        """Test scheduling empty slices list returns empty job list."""
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
        )

        scheduler.scheduler.add_job = MagicMock()

        job_ids = scheduler.schedule_slices(
            parent_order_id="parent_empty",
            slices=[],
            symbol="GOOG",
            side="buy",
            order_type="market",
            limit_price=None,
            stop_price=None,
            time_in_force="day",
        )

        assert job_ids == []
        scheduler.scheduler.add_job.assert_not_called()


class TestExecuteSliceKillSwitch:
    """Tests for execute_slice kill switch blocking."""

    def test_execute_slice_blocked_by_kill_switch(self):
        """Test slice execution blocked when kill switch engaged."""
        kill_switch = MagicMock(spec=KillSwitch)
        kill_switch.is_engaged.return_value = True  # Kill switch ENGAGED

        breaker = MagicMock(spec=CircuitBreaker)
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
        )

        now = datetime.now(UTC)
        slice_detail = SliceDetail(
            slice_num=0,
            qty=20,
            scheduled_time=now,
            client_order_id="child0",
            status="pending_new",
        )

        # Execute slice (should be blocked)
        scheduler._execute_slice(
            parent_order_id="parent123",
            slice_detail=slice_detail,
            symbol="AAPL",
            side="buy",
            order_type="market",
            limit_price=None,
            stop_price=None,
            time_in_force="day",
        )

        # Verify kill switch checked
        kill_switch.is_engaged.assert_called_once()

        # Verify DB updated to blocked_kill_switch
        db.update_order_status.assert_called_once_with(
            client_order_id="child0",
            status="blocked_kill_switch",
        )

        # Verify executor NOT called (blocked)
        executor.submit_order.assert_not_called()

    def test_execute_slice_allowed_when_kill_switch_off(self):
        """Test slice execution proceeds when kill switch not engaged."""
        kill_switch = MagicMock(spec=KillSwitch)
        kill_switch.is_engaged.return_value = False  # Kill switch OFF

        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False  # Breaker OPEN

        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)
        executor.submit_order.return_value = {"id": "broker123"}

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
        )

        now = datetime.now(UTC)
        slice_detail = SliceDetail(
            slice_num=0,
            qty=20,
            scheduled_time=now,
            client_order_id="child0",
            status="pending_new",
        )

        scheduler._execute_slice(
            parent_order_id="parent123",
            slice_detail=slice_detail,
            symbol="AAPL",
            side="buy",
            order_type="market",
            limit_price=None,
            stop_price=None,
            time_in_force="day",
        )

        # Verify executor called (not blocked)
        executor.submit_order.assert_called_once()

        # Verify DB updated to submitted with error_message cleared
        db.update_order_status.assert_called_with(
            client_order_id="child0",
            status="submitted",
            broker_order_id="broker123",
            error_message="",  # Clears any error from previous retry attempts
        )


class TestExecuteSliceCircuitBreaker:
    """Tests for execute_slice circuit breaker blocking."""

    def test_execute_slice_blocked_by_circuit_breaker(self):
        """Test slice execution blocked when circuit breaker tripped."""
        kill_switch = MagicMock(spec=KillSwitch)
        kill_switch.is_engaged.return_value = False  # Kill switch OFF

        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = True  # Breaker TRIPPED
        breaker.get_trip_reason.return_value = "DRAWDOWN_BREACH"

        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
        )

        now = datetime.now(UTC)
        slice_detail = SliceDetail(
            slice_num=0,
            qty=20,
            scheduled_time=now,
            client_order_id="child0",
            status="pending_new",
        )

        scheduler._execute_slice(
            parent_order_id="parent123",
            slice_detail=slice_detail,
            symbol="AAPL",
            side="buy",
            order_type="market",
            limit_price=None,
            stop_price=None,
            time_in_force="day",
        )

        # Verify breaker checked
        breaker.is_tripped.assert_called_once()
        breaker.get_trip_reason.assert_called_once()

        # Verify DB updated to blocked_circuit_breaker
        db.update_order_status.assert_called_once_with(
            client_order_id="child0",
            status="blocked_circuit_breaker",
        )

        # Verify executor NOT called (blocked)
        executor.submit_order.assert_not_called()


class TestExecuteSliceSuccess:
    """Tests for successful slice execution."""

    def test_execute_slice_successful_submission(self):
        """Test successful slice submission updates DB correctly."""
        kill_switch = MagicMock(spec=KillSwitch)
        kill_switch.is_engaged.return_value = False

        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False

        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)
        executor.submit_order.return_value = {"id": "broker_abc123"}

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
        )

        now = datetime.now(UTC)
        slice_detail = SliceDetail(
            slice_num=0,
            qty=20,
            scheduled_time=now,
            client_order_id="child0",
            status="pending_new",
        )

        scheduler._execute_slice(
            parent_order_id="parent123",
            slice_detail=slice_detail,
            symbol="AAPL",
            side="buy",
            order_type="market",
            limit_price=None,
            stop_price=None,
            time_in_force="day",
        )

        # Verify executor called with correct OrderRequest
        executor.submit_order.assert_called_once()
        call_args = executor.submit_order.call_args
        assert call_args[1]["client_order_id"] == "child0"
        assert call_args[1]["order"].symbol == "AAPL"
        assert call_args[1]["order"].qty == 20

        # Verify DB updated to submitted with broker_order_id and error_message cleared
        db.update_order_status.assert_called_once_with(
            client_order_id="child0",
            status="submitted",
            broker_order_id="broker_abc123",
            error_message="",  # Clears any error from previous retry attempts
        )


class TestExecuteSliceDryRun:
    """Tests for DRY_RUN mode (executor=None)."""

    def test_execute_slice_dry_run_mode(self):
        """Test DRY_RUN mode logs without broker submission and updates DB to dry_run status."""
        kill_switch = MagicMock(spec=KillSwitch)
        kill_switch.is_engaged.return_value = False

        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False

        db = MagicMock(spec=DatabaseClient)
        db.get_order_by_client_id.return_value = None  # Not canceled

        # DRY_RUN mode: executor is None
        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=None,  # DRY_RUN mode
        )

        now = datetime.now(UTC)
        slice_detail = SliceDetail(
            slice_num=0,
            qty=20,
            scheduled_time=now,
            client_order_id="child0",
            status="pending_new",
        )

        scheduler._execute_slice(
            parent_order_id="parent123",
            slice_detail=slice_detail,
            symbol="AAPL",
            side="buy",
            order_type="market",
            limit_price=None,
            stop_price=None,
            time_in_force="day",
        )

        # Verify DB updated to dry_run status (NOT submitted)
        db.update_order_status.assert_called_once_with(
            client_order_id="child0",
            status="dry_run",
        )

        # Verify executor was NOT called (since it's None)
        assert scheduler.executor is None

    def test_execute_slice_dry_run_respects_safety_guards(self):
        """Test DRY_RUN mode still respects kill switch and circuit breaker."""
        kill_switch = MagicMock(spec=KillSwitch)
        kill_switch.is_engaged.return_value = True  # Kill switch engaged

        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False

        db = MagicMock(spec=DatabaseClient)
        db.get_order_by_client_id.return_value = None

        # DRY_RUN mode: executor is None
        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=None,  # DRY_RUN mode
        )

        now = datetime.now(UTC)
        slice_detail = SliceDetail(
            slice_num=0,
            qty=20,
            scheduled_time=now,
            client_order_id="child0",
            status="pending_new",
        )

        scheduler._execute_slice(
            parent_order_id="parent123",
            slice_detail=slice_detail,
            symbol="AAPL",
            side="buy",
            order_type="market",
            limit_price=None,
            stop_price=None,
            time_in_force="day",
        )

        # Verify slice blocked by kill switch (NOT executed in dry-run)
        db.update_order_status.assert_called_once_with(
            client_order_id="child0",
            status="blocked_kill_switch",
        )


class TestExecuteSliceErrors:
    """Tests for slice execution error handling."""

    def test_execute_slice_validation_error_updates_db(self):
        """Test validation error updates DB to rejected (non-retryable)."""
        kill_switch = MagicMock(spec=KillSwitch)
        kill_switch.is_engaged.return_value = False

        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False

        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)
        executor.submit_order.side_effect = AlpacaValidationError("Invalid qty")

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
        )

        now = datetime.now(UTC)
        slice_detail = SliceDetail(
            slice_num=0,
            qty=20,
            scheduled_time=now,
            client_order_id="child0",
            status="pending_new",
        )

        scheduler._execute_slice(
            parent_order_id="parent123",
            slice_detail=slice_detail,
            symbol="AAPL",
            side="buy",
            order_type="market",
            limit_price=None,
            stop_price=None,
            time_in_force="day",
        )

        # Verify DB updated to rejected
        db.update_order_status.assert_called_once_with(
            client_order_id="child0",
            status="rejected",
            error_message="Invalid qty",
        )

    def test_execute_slice_rejection_error_updates_db(self):
        """Test rejection error updates DB to rejected (non-retryable)."""
        kill_switch = MagicMock(spec=KillSwitch)
        kill_switch.is_engaged.return_value = False

        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False

        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)
        executor.submit_order.side_effect = AlpacaRejectionError("Insufficient buying power")

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
        )

        now = datetime.now(UTC)
        slice_detail = SliceDetail(
            slice_num=0,
            qty=20,
            scheduled_time=now,
            client_order_id="child0",
            status="pending_new",
        )

        scheduler._execute_slice(
            parent_order_id="parent123",
            slice_detail=slice_detail,
            symbol="AAPL",
            side="buy",
            order_type="market",
            limit_price=None,
            stop_price=None,
            time_in_force="day",
        )

        # Verify DB updated to rejected
        db.update_order_status.assert_called_once_with(
            client_order_id="child0",
            status="rejected",
            error_message="Insufficient buying power",
        )

    def test_execute_slice_connection_error_retries_then_fails(self):
        """Test connection error retries 3 times then updates DB to failed.

        The wrapper method _execute_slice_job_wrapper handles marking as failed
        when all retries are exhausted. The inner _execute_slice method only
        logs and re-raises connection errors for tenacity to handle.
        """
        kill_switch = MagicMock(spec=KillSwitch)
        kill_switch.is_engaged.return_value = False

        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False

        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)
        # Fail all 3 retry attempts
        executor.submit_order.side_effect = AlpacaConnectionError("Connection timeout")

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
        )

        now = datetime.now(UTC)
        slice_detail = SliceDetail(
            slice_num=0,
            qty=20,
            scheduled_time=now,
            client_order_id="child0",
            status="pending_new",
        )

        # Call the wrapper method (used by APScheduler) instead of the inner method
        # The wrapper catches the final exception and marks as failed
        scheduler._execute_slice_job_wrapper(
            parent_order_id="parent123",
            slice_detail=slice_detail,
            symbol="AAPL",
            side="buy",
            order_type="market",
            limit_price=None,
            stop_price=None,
            time_in_force="day",
        )

        # Verify executor called 3 times (retry policy)
        assert executor.submit_order.call_count == 3

        # Verify DB updated to failed (by the wrapper after all retries exhausted)
        db.update_order_status.assert_called_with(
            client_order_id="child0",
            status="failed",
            error_message="Retry exhausted: Connection timeout",
        )


class TestExecuteSliceCancellation:
    """Tests for cancellation guards in _execute_slice (defense in depth)."""

    def test_execute_slice_aborted_when_db_shows_canceled_at_early_guard(self):
        """Test _execute_slice aborts at early guard when DB shows 'canceled'."""
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        # Mock DB to return an order with status='canceled' at early guard
        canceled_order = MagicMock()
        canceled_order.status = "canceled"
        db.get_order_by_client_id.return_value = canceled_order

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
        )

        now = datetime.now(UTC)
        slice_detail = SliceDetail(
            slice_num=0,
            qty=20,
            scheduled_time=now,
            client_order_id="child0",
            status="pending_new",
        )

        # Execute slice
        scheduler._execute_slice(
            parent_order_id="parent123",
            slice_detail=slice_detail,
            symbol="AAPL",
            side="buy",
            order_type="market",
            limit_price=None,
            stop_price=None,
            time_in_force="day",
        )

        # Verify DB was queried for cancellation status
        db.get_order_by_client_id.assert_called_once_with("child0")

        # Verify executor was NOT called (aborted before submission)
        executor.submit_order.assert_not_called()

        # Verify DB status was NOT updated (already canceled)
        db.update_order_status.assert_not_called()

    def test_execute_slice_aborted_when_db_shows_canceled_at_presubmit_guard(self):
        """Test _execute_slice aborts at pre-submit guard when DB shows 'canceled'."""
        kill_switch = MagicMock(spec=KillSwitch)
        kill_switch.is_engaged.return_value = False

        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False

        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        # Mock DB: first call returns pending (passes early guard),
        # second call returns canceled (triggers pre-submit guard)
        pending_order = MagicMock()
        pending_order.status = "pending_new"
        canceled_order = MagicMock()
        canceled_order.status = "canceled"
        db.get_order_by_client_id.side_effect = [pending_order, canceled_order]

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
        )

        now = datetime.now(UTC)
        slice_detail = SliceDetail(
            slice_num=0,
            qty=20,
            scheduled_time=now,
            client_order_id="child0",
            status="pending_new",
        )

        # Execute slice
        scheduler._execute_slice(
            parent_order_id="parent123",
            slice_detail=slice_detail,
            symbol="AAPL",
            side="buy",
            order_type="market",
            limit_price=None,
            stop_price=None,
            time_in_force="day",
        )

        # Verify DB was queried twice (early guard + pre-submit guard)
        assert db.get_order_by_client_id.call_count == 2

        # Verify executor was NOT called (aborted at pre-submit guard)
        executor.submit_order.assert_not_called()

        # Verify DB status was NOT updated
        db.update_order_status.assert_not_called()


class TestCancelRemainingSlices:
    """Tests for cancel_remaining_slices method."""

    def test_cancel_remaining_slices_removes_jobs(self):
        """Test canceling slices removes scheduler jobs and updates DB."""
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        db = MagicMock(spec=DatabaseClient)
        db.cancel_pending_slices.return_value = 3  # 3 slices canceled in DB
        executor = MagicMock(spec=AlpacaExecutor)

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
        )

        # Mock get_jobs to return 3 matching jobs
        mock_job1 = MagicMock()
        mock_job1.id = "parent123_slice_0"
        mock_job2 = MagicMock()
        mock_job2.id = "parent123_slice_1"
        mock_job3 = MagicMock()
        mock_job3.id = "parent123_slice_2"
        mock_job_other = MagicMock()
        mock_job_other.id = "other_parent_slice_0"

        scheduler.scheduler.get_jobs = MagicMock(
            return_value=[mock_job1, mock_job2, mock_job3, mock_job_other]
        )
        scheduler.scheduler.remove_job = MagicMock()

        canceled_count = scheduler.cancel_remaining_slices("parent123")

        # Verify 3 jobs removed (not the other_parent job)
        assert canceled_count == 3
        assert scheduler.scheduler.remove_job.call_count == 3

        # Verify DB called
        db.cancel_pending_slices.assert_called_once_with("parent123")

    def test_cancel_remaining_slices_no_jobs_to_cancel(self):
        """Test canceling when no jobs exist returns 0."""
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        db = MagicMock(spec=DatabaseClient)
        db.cancel_pending_slices.return_value = 0
        executor = MagicMock(spec=AlpacaExecutor)

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
        )

        scheduler.scheduler.get_jobs = MagicMock(return_value=[])
        scheduler.scheduler.remove_job = MagicMock()

        canceled_count = scheduler.cancel_remaining_slices("parent_no_jobs")

        assert canceled_count == 0
        scheduler.scheduler.remove_job.assert_not_called()
        db.cancel_pending_slices.assert_called_once()
