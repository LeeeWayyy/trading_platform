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
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from apps.execution_gateway.alpaca_client import (
    AlpacaConnectionError,
    AlpacaExecutor,
    AlpacaRejectionError,
    AlpacaValidationError,
)
from apps.execution_gateway.database import DatabaseClient
from apps.execution_gateway.schemas import SliceDetail
from apps.execution_gateway.slice_scheduler import MarketClockSnapshot, SliceScheduler
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
                strategy_id="test_strategy",
                status="pending_new",
            ),
            SliceDetail(
                slice_num=1,
                qty=20,
                scheduled_time=now + timedelta(minutes=1),
                client_order_id="child1",
                strategy_id="test_strategy",
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
                strategy_id="test_strategy",
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


class TestZombieSliceRecovery:
    """Tests for zombie slice recovery on startup."""

    def _build_slice_order(self, **overrides: Any) -> SimpleNamespace:
        base_time = datetime(2025, 1, 1, tzinfo=UTC)
        base: dict[str, Any] = {
            "client_order_id": "child0",
            "parent_order_id": "parent123",
            "slice_num": 0,
            "qty": 10,
            "scheduled_time": base_time,
            "strategy_id": "twap_slice_parent123_0",
            "status": "pending_new",
            "symbol": "AAPL",
            "side": "buy",
            "order_type": "market",
            "limit_price": None,
            "stop_price": None,
            "time_in_force": "day",
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def _build_parent_order(self, status: str) -> SimpleNamespace:
        return SimpleNamespace(status=status)

    def test_recovery_cancels_when_parent_terminal(self):
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        slice_order = self._build_slice_order()
        parent_order = self._build_parent_order("canceled")
        db.get_pending_child_slices.return_value = [slice_order]
        db.get_order_by_client_id.return_value = parent_order
        db.cancel_pending_slices.return_value = 1

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
        )
        scheduler.scheduler.add_job = MagicMock()
        scheduler.scheduler.get_job = MagicMock(return_value=None)

        result = scheduler.recover_zombie_slices(now=datetime(2025, 1, 1, tzinfo=UTC))

        db.cancel_pending_slices.assert_called_once_with("parent123")
        scheduler.scheduler.add_job.assert_not_called()
        assert result["canceled"] == 1

    def test_recovery_blocks_when_breaker_tripped(self):
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = True
        breaker.get_trip_reason.return_value = "TEST_TRIP"
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        slice_order = self._build_slice_order()
        parent_order = self._build_parent_order("accepted")
        db.get_pending_child_slices.return_value = [slice_order]
        db.get_order_by_client_id.return_value = parent_order

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
        )
        scheduler.scheduler.add_job = MagicMock()
        scheduler.scheduler.get_job = MagicMock(return_value=None)

        recovery_now = datetime(2025, 1, 1, tzinfo=UTC)
        scheduler.recover_zombie_slices(now=recovery_now)

        db.update_order_status_cas.assert_called_once()
        call_kwargs = db.update_order_status_cas.call_args[1]
        assert call_kwargs["client_order_id"] == "child0"
        assert call_kwargs["status"] == "blocked_circuit_breaker"
        assert "Circuit breaker is tripped" in call_kwargs["error_message"]
        scheduler.scheduler.add_job.assert_not_called()

    def test_recovery_within_grace_executes_immediately_when_market_open(self):
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        now = datetime(2025, 1, 1, tzinfo=UTC)
        slice_order = self._build_slice_order(scheduled_time=now - timedelta(seconds=30))
        parent_order = self._build_parent_order("submitted")
        db.get_pending_child_slices.return_value = [slice_order]
        db.get_order_by_client_id.return_value = parent_order

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
            market_clock_provider=lambda: MarketClockSnapshot(is_open=True, next_open=None),
        )
        scheduler.scheduler.add_job = MagicMock()
        scheduler.scheduler.get_job = MagicMock(return_value=None)

        scheduler.recover_zombie_slices(now=now)

        db.update_order_scheduled_time.assert_called_once_with(
            client_order_id="child0",
            scheduled_time=now,
        )
        scheduler.scheduler.add_job.assert_called_once()
        run_date = scheduler.scheduler.add_job.call_args[1]["run_date"]
        assert run_date == now
        slice_detail = scheduler.scheduler.add_job.call_args[1]["kwargs"]["slice_detail"]
        assert slice_detail.scheduled_time == now

    def test_recovery_within_grace_market_closed_reschedules_next_open(self):
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        now = datetime(2025, 1, 1, tzinfo=UTC)
        next_open = now + timedelta(hours=1)
        slice_order = self._build_slice_order(scheduled_time=now - timedelta(seconds=30))
        parent_order = self._build_parent_order("accepted")
        db.get_pending_child_slices.return_value = [slice_order]
        db.get_order_by_client_id.return_value = parent_order

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
            market_clock_provider=lambda: MarketClockSnapshot(is_open=False, next_open=next_open),
        )
        scheduler.scheduler.add_job = MagicMock()
        scheduler.scheduler.get_job = MagicMock(return_value=None)

        scheduler.recover_zombie_slices(now=now)

        db.update_order_scheduled_time.assert_called_once_with(
            client_order_id="child0",
            scheduled_time=next_open,
        )
        run_date = scheduler.scheduler.add_job.call_args[1]["run_date"]
        assert run_date == next_open
        slice_detail = scheduler.scheduler.add_job.call_args[1]["kwargs"]["slice_detail"]
        assert slice_detail.scheduled_time == next_open

    def test_recovery_beyond_grace_executes_immediately_if_market_open(self):
        """Beyond grace period but market is open - execute immediately."""
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        now = datetime(2025, 1, 1, tzinfo=UTC)
        next_open = now + timedelta(hours=1)
        # 300 seconds = 5 minutes, beyond default grace period of 60 seconds
        slice_order = self._build_slice_order(scheduled_time=now - timedelta(seconds=300))
        parent_order = self._build_parent_order("submitted")
        db.get_pending_child_slices.return_value = [slice_order]
        db.get_order_by_client_id.return_value = parent_order

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
            market_clock_provider=lambda: MarketClockSnapshot(is_open=True, next_open=next_open),
        )
        scheduler.scheduler.add_job = MagicMock()
        scheduler.scheduler.get_job = MagicMock(return_value=None)

        scheduler.recover_zombie_slices(now=now)

        # Should execute immediately (at 'now'), not reschedule to next_open
        db.update_order_scheduled_time.assert_called_once_with(
            client_order_id="child0",
            scheduled_time=now,
        )
        run_date = scheduler.scheduler.add_job.call_args[1]["run_date"]
        assert run_date == now
        slice_detail = scheduler.scheduler.add_job.call_args[1]["kwargs"]["slice_detail"]
        assert slice_detail.scheduled_time == now

    def test_recovery_beyond_grace_market_closed_reschedules_to_next_open(self):
        """Beyond grace period, market closed, next_open available - reschedule."""
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        now = datetime(2025, 1, 1, tzinfo=UTC)
        next_open = now + timedelta(hours=1)
        slice_order = self._build_slice_order(scheduled_time=now - timedelta(seconds=300))
        parent_order = self._build_parent_order("submitted")
        db.get_pending_child_slices.return_value = [slice_order]
        db.get_order_by_client_id.return_value = parent_order

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
            market_clock_provider=lambda: MarketClockSnapshot(is_open=False, next_open=next_open),
        )
        scheduler.scheduler.add_job = MagicMock()
        scheduler.scheduler.get_job = MagicMock(return_value=None)

        scheduler.recover_zombie_slices(now=now)

        # Should reschedule to next_open since market is closed
        db.update_order_scheduled_time.assert_called_once_with(
            client_order_id="child0",
            scheduled_time=next_open,
        )
        run_date = scheduler.scheduler.add_job.call_args[1]["run_date"]
        assert run_date == next_open

    def test_recovery_beyond_grace_market_closed_no_next_open_fails(self):
        """Beyond grace period, market closed, no next_open - fail the slice."""
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        now = datetime(2025, 1, 1, tzinfo=UTC)
        slice_order = self._build_slice_order(scheduled_time=now - timedelta(seconds=300))
        parent_order = self._build_parent_order("submitted_unconfirmed")
        db.get_pending_child_slices.return_value = [slice_order]
        db.get_order_by_client_id.return_value = parent_order

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
            market_clock_provider=lambda: MarketClockSnapshot(is_open=False, next_open=None),
        )
        scheduler.scheduler.add_job = MagicMock()
        scheduler.scheduler.get_job = MagicMock(return_value=None)

        scheduler.recover_zombie_slices(now=now)

        db.update_order_status_cas.assert_called_once()
        call_kwargs = db.update_order_status_cas.call_args[1]
        assert call_kwargs["client_order_id"] == "child0"
        assert call_kwargs["status"] == "failed"
        assert "Slice missed grace period" in call_kwargs["error_message"]
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
            strategy_id="test_strategy",
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

        # Verify DB updated to blocked_kill_switch with error message
        db.update_order_status_cas.assert_called_once()
        call_kwargs = db.update_order_status_cas.call_args[1]
        assert call_kwargs["client_order_id"] == "child0"
        assert call_kwargs["status"] == "blocked_kill_switch"
        assert "Kill switch is engaged" in call_kwargs["error_message"]

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
            strategy_id="test_strategy",
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
        db.update_order_status_cas.assert_called()
        call_kwargs = db.update_order_status_cas.call_args[1]
        assert call_kwargs["client_order_id"] == "child0"
        assert call_kwargs["status"] == "submitted"
        assert call_kwargs["broker_order_id"] == "broker123"
        assert call_kwargs["error_message"] == ""  # Clears any error from previous retry attempts


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
            strategy_id="test_strategy",
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

        # Verify DB updated to blocked_circuit_breaker with error message
        db.update_order_status_cas.assert_called_once()
        call_kwargs = db.update_order_status_cas.call_args[1]
        assert call_kwargs["client_order_id"] == "child0"
        assert call_kwargs["status"] == "blocked_circuit_breaker"
        assert "Circuit breaker is tripped" in call_kwargs["error_message"]

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
            strategy_id="test_strategy",
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
        db.update_order_status_cas.assert_called_once()
        call_kwargs = db.update_order_status_cas.call_args[1]
        assert call_kwargs["client_order_id"] == "child0"
        assert call_kwargs["status"] == "submitted"
        assert call_kwargs["broker_order_id"] == "broker_abc123"
        assert call_kwargs["error_message"] == ""

    def test_execute_slice_db_failure_after_broker_submission_retries_then_fallback(self):
        """Test DB update failure after broker submission retries with backoff then falls back to submitted_unconfirmed."""

        kill_switch = MagicMock(spec=KillSwitch)
        kill_switch.is_engaged.return_value = False

        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False

        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)
        executor.submit_order.return_value = {"id": "broker_abc123"}

        # Mock DB to fail 3 times with status="submitted", then succeed with status="submitted_unconfirmed"
        db_error = Exception("DB connection lost")
        db.update_order_status_cas.side_effect = [
            db_error,  # Retry 1 fails
            db_error,  # Retry 2 fails
            db_error,  # Retry 3 fails (exhaust retries)
            None,  # Fallback to submitted_unconfirmed succeeds
        ]

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
            strategy_id="test_strategy",
            status="pending_new",
        )

        # Execute slice - fallback succeeds, allowing graceful job termination
        # No exception should be raised when submitted_unconfirmed update succeeds
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

        # Verify executor called (broker submission succeeded)
        executor.submit_order.assert_called_once()

        # Verify DB update_order_status_cas called 4 times:
        # - 3 attempts with status="submitted" (retries with exponential backoff)
        # - 1 attempt with status="submitted_unconfirmed" (fallback)
        assert db.update_order_status_cas.call_count == 4

        # Verify first 3 calls were retries for status="submitted"
        for i in range(3):
            call = db.update_order_status_cas.call_args_list[i]
            assert call[1]["client_order_id"] == "child0"
            assert call[1]["status"] == "submitted"
            assert call[1]["broker_order_id"] == "broker_abc123"
            assert call[1]["error_message"] == ""

        # Verify 4th call was fallback to submitted_unconfirmed
        fallback_call = db.update_order_status_cas.call_args_list[3]
        assert fallback_call[1]["client_order_id"] == "child0"
        assert fallback_call[1]["status"] == "submitted_unconfirmed"
        assert fallback_call[1]["broker_order_id"] == "broker_abc123"
        assert "DB update failed after broker submission" in fallback_call[1]["error_message"]
        assert "DB connection lost" in fallback_call[1]["error_message"]


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
            strategy_id="test_strategy",
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
        db.update_order_status_cas.assert_called_once()
        call_kwargs = db.update_order_status_cas.call_args[1]
        assert call_kwargs["client_order_id"] == "child0"
        assert call_kwargs["status"] == "dry_run"

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
            strategy_id="test_strategy",
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

        # Verify slice blocked by kill switch with error message (NOT executed in dry-run)
        db.update_order_status_cas.assert_called_once()
        call_kwargs = db.update_order_status_cas.call_args[1]
        assert call_kwargs["client_order_id"] == "child0"
        assert call_kwargs["status"] == "blocked_kill_switch"
        assert "Kill switch is engaged" in call_kwargs["error_message"]


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
            strategy_id="test_strategy",
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
        db.update_order_status_cas.assert_called_once()
        call_kwargs = db.update_order_status_cas.call_args[1]
        assert call_kwargs["client_order_id"] == "child0"
        assert call_kwargs["status"] == "rejected"
        assert call_kwargs["error_message"] == "Invalid qty"

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
            strategy_id="test_strategy",
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
        db.update_order_status_cas.assert_called_once()
        call_kwargs = db.update_order_status_cas.call_args[1]
        assert call_kwargs["client_order_id"] == "child0"
        assert call_kwargs["status"] == "rejected"
        assert call_kwargs["error_message"] == "Insufficient buying power"

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
            strategy_id="test_strategy",
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
        db.update_order_status_cas.assert_called()
        call_kwargs = db.update_order_status_cas.call_args[1]
        assert call_kwargs["client_order_id"] == "child0"
        assert call_kwargs["status"] == "failed"
        assert "Retry exhausted" in call_kwargs["error_message"]


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
            strategy_id="test_strategy",
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
        db.update_order_status_cas.assert_not_called()

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
            strategy_id="test_strategy",
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
        db.update_order_status_cas.assert_not_called()


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

        scheduler_count, db_count = scheduler.cancel_remaining_slices("parent123")

        # Verify 3 jobs removed (not the other_parent job) and 3 DB rows updated
        assert scheduler_count == 3
        assert db_count == 3
        assert scheduler.scheduler.remove_job.call_count == 3

        # Verify DB called
        db.cancel_pending_slices.assert_called_once_with("parent123")

    def test_cancel_remaining_slices_no_jobs_to_cancel(self):
        """Test canceling when no jobs exist returns (0, 0)."""
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

        scheduler_count, db_count = scheduler.cancel_remaining_slices("parent_no_jobs")

        assert scheduler_count == 0
        assert db_count == 0
        scheduler.scheduler.remove_job.assert_not_called()
        db.cancel_pending_slices.assert_called_once()


class TestStaleSliceExpiry:
    """Tests for stale slice expiry feature (STALE_SLICE_EXPIRY_SECONDS)."""

    def _build_slice_order(self, **overrides: Any) -> SimpleNamespace:
        base_time = datetime(2025, 1, 1, tzinfo=UTC)
        base: dict[str, Any] = {
            "client_order_id": "child0",
            "parent_order_id": "parent123",
            "slice_num": 0,
            "qty": 10,
            "scheduled_time": base_time,
            "strategy_id": "twap_slice_parent123_0",
            "status": "pending_new",
            "symbol": "AAPL",
            "side": "buy",
            "order_type": "market",
            "limit_price": None,
            "stop_price": None,
            "time_in_force": "day",
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def _build_parent_order(self, status: str) -> SimpleNamespace:
        return SimpleNamespace(status=status)

    def test_slice_older_than_threshold_marked_expired(self):
        """Slices older than STALE_SLICE_EXPIRY_SECONDS are expired."""
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        # Scheduled 2 hours ago (7200 seconds), threshold is 1 hour (3600 seconds)
        old_scheduled_time = now - timedelta(hours=2)
        slice_order = self._build_slice_order(scheduled_time=old_scheduled_time)
        parent_order = self._build_parent_order("submitted")
        db.get_pending_child_slices.return_value = [slice_order]
        db.get_order_by_client_id.return_value = parent_order

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
            stale_slice_expiry_seconds=3600,  # 1 hour threshold
        )
        scheduler.scheduler.add_job = MagicMock()
        scheduler.scheduler.get_job = MagicMock(return_value=None)

        result = scheduler.recover_zombie_slices(now=now)

        # Verify slice was marked expired
        assert result["expired"] == 1
        assert result["scheduled"] == 0

        # Verify DB call has all required fields
        db.update_order_status_cas.assert_called_once()
        call_kwargs = db.update_order_status_cas.call_args[1]
        assert call_kwargs["client_order_id"] == "child0"
        assert call_kwargs["status"] == "expired"
        assert call_kwargs["broker_updated_at"] == now
        assert "status_rank" in call_kwargs
        assert "source_priority" in call_kwargs
        assert "3600s threshold" in call_kwargs["error_message"]

        # Verify scheduler job was NOT added
        scheduler.scheduler.add_job.assert_not_called()

    def test_fresh_slice_recovered_normally(self):
        """Slices within threshold are recovered normally."""
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        # Scheduled 30 minutes ago (1800 seconds), threshold is 24 hours (86400 seconds)
        fresh_scheduled_time = now - timedelta(minutes=30)
        slice_order = self._build_slice_order(scheduled_time=fresh_scheduled_time)
        parent_order = self._build_parent_order("submitted")
        db.get_pending_child_slices.return_value = [slice_order]
        db.get_order_by_client_id.return_value = parent_order

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
            stale_slice_expiry_seconds=86400,  # 24 hours threshold
            market_clock_provider=lambda: MarketClockSnapshot(is_open=True, next_open=None),
        )
        scheduler.scheduler.add_job = MagicMock()
        scheduler.scheduler.get_job = MagicMock(return_value=None)

        result = scheduler.recover_zombie_slices(now=now)

        # Verify slice was scheduled (not expired)
        assert result["expired"] == 0
        assert result["scheduled"] == 1

        # Verify scheduler job was added
        scheduler.scheduler.add_job.assert_called_once()

    def test_expiry_disabled_when_zero(self):
        """STALE_SLICE_EXPIRY_SECONDS=0 disables expiry."""
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        # Scheduled 7 days ago - would be stale if expiry was enabled
        very_old_scheduled_time = now - timedelta(days=7)
        slice_order = self._build_slice_order(scheduled_time=very_old_scheduled_time)
        parent_order = self._build_parent_order("submitted")
        db.get_pending_child_slices.return_value = [slice_order]
        db.get_order_by_client_id.return_value = parent_order

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
            stale_slice_expiry_seconds=0,  # Expiry disabled
            market_clock_provider=lambda: MarketClockSnapshot(is_open=True, next_open=None),
        )
        scheduler.scheduler.add_job = MagicMock()
        scheduler.scheduler.get_job = MagicMock(return_value=None)

        # Test helper method directly
        # Note: slice_order is SimpleNamespace, duck-typed for OrderDetail
        assert scheduler._is_slice_stale(slice_order, now) is False  # type: ignore[arg-type]

        # Also verify through recovery
        result = scheduler.recover_zombie_slices(now=now)

        # Slice should be scheduled, not expired (expiry is disabled)
        assert result["expired"] == 0
        assert result["scheduled"] == 1

    def test_negative_age_does_not_expire(self):
        """Future scheduled times (clock skew) don't trigger expiry."""
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        # Scheduled 1 hour in the future (clock skew scenario)
        future_scheduled_time = now + timedelta(hours=1)
        slice_order = self._build_slice_order(scheduled_time=future_scheduled_time)
        parent_order = self._build_parent_order("submitted")
        db.get_pending_child_slices.return_value = [slice_order]
        db.get_order_by_client_id.return_value = parent_order

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
            stale_slice_expiry_seconds=3600,  # 1 hour threshold
        )
        scheduler.scheduler.add_job = MagicMock()
        scheduler.scheduler.get_job = MagicMock(return_value=None)

        # Test helper method directly
        # Note: slice_order is SimpleNamespace, duck-typed for OrderDetail
        assert scheduler._is_slice_stale(slice_order, now) is False  # type: ignore[arg-type]

        # Also verify through recovery
        result = scheduler.recover_zombie_slices(now=now)

        # Slice should be scheduled at its future time, not expired
        assert result["expired"] == 0
        assert result["scheduled"] == 1

    def test_parent_terminal_cancels_before_expiry_check(self):
        """Parent terminal status cascades cancel, not individual expiry."""
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        # Slice is old enough to be stale
        old_scheduled_time = now - timedelta(hours=2)
        slice_order = self._build_slice_order(scheduled_time=old_scheduled_time)
        # But parent is canceled (terminal state)
        parent_order = self._build_parent_order("canceled")
        db.get_pending_child_slices.return_value = [slice_order]
        db.get_order_by_client_id.return_value = parent_order
        db.cancel_pending_slices.return_value = 1

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
            stale_slice_expiry_seconds=3600,  # 1 hour threshold
        )
        scheduler.scheduler.add_job = MagicMock()
        scheduler.scheduler.get_job = MagicMock(return_value=None)

        result = scheduler.recover_zombie_slices(now=now)

        # Parent terminal takes precedence - slice should be canceled, not expired
        assert result["canceled"] == 1
        assert result["expired"] == 0

        # Verify cancel_pending_slices was called (not update_order_status_cas for expired)
        db.cancel_pending_slices.assert_called_once_with("parent123")

    def test_is_slice_stale_missing_scheduled_time_returns_false(self):
        """Missing scheduled_time returns False (validation error, not stale)."""
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
            stale_slice_expiry_seconds=3600,
        )

        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        slice_order = self._build_slice_order(scheduled_time=None)

        # Missing scheduled_time should return False (not stale)
        # Note: slice_order is SimpleNamespace, duck-typed for OrderDetail
        assert scheduler._is_slice_stale(slice_order, now) is False  # type: ignore[arg-type]

    def test_is_slice_stale_naive_datetime_handled(self):
        """Naive datetime (no tzinfo) is handled by adding UTC."""
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
            stale_slice_expiry_seconds=3600,  # 1 hour
        )

        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        # Naive datetime (no tzinfo) - 2 hours before now
        naive_scheduled_time = datetime(2025, 1, 1, 10, 0, 0)  # No tzinfo
        slice_order = self._build_slice_order(scheduled_time=naive_scheduled_time)

        # Should be stale (2 hours old > 1 hour threshold)
        # Note: slice_order is SimpleNamespace, duck-typed for OrderDetail
        assert scheduler._is_slice_stale(slice_order, now) is True  # type: ignore[arg-type]

    def test_stale_slice_expiry_default_from_env(self):
        """Default value comes from STALE_SLICE_EXPIRY_SECONDS constant."""
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        # Don't pass stale_slice_expiry_seconds - should use default from env
        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
        )

        # Default is 86400 (24 hours) from STALE_SLICE_EXPIRY_SECONDS constant
        assert scheduler.stale_slice_expiry_seconds == 86400

    def test_non_allowed_parent_skips_before_expiry_check(self):
        """Slices with non-allowed parent states are skipped, not expired.

        This test guards the recovery ordering: non-allowed parent check must
        come BEFORE staleness check to preserve existing behavior.
        """
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        # Slice is old enough to be stale
        old_scheduled_time = now - timedelta(hours=2)
        slice_order = self._build_slice_order(scheduled_time=old_scheduled_time)
        # Parent is in non-allowed state (not terminal, but not recoverable)
        parent_order = self._build_parent_order("pending_new")
        db.get_pending_child_slices.return_value = [slice_order]
        db.get_order_by_client_id.return_value = parent_order

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
            stale_slice_expiry_seconds=3600,  # 1 hour threshold
        )
        scheduler.scheduler.add_job = MagicMock()
        scheduler.scheduler.get_job = MagicMock(return_value=None)

        result = scheduler.recover_zombie_slices(now=now)

        # Slice should be skipped (not expired) because parent is not in allowed state
        assert result["expired"] == 0
        assert result["scheduled"] == 0
        assert result["canceled"] == 0

        # Verify NO DB update was made (slice was skipped)
        db.update_order_status_cas.assert_not_called()
        db.cancel_pending_slices.assert_not_called()
        scheduler.scheduler.add_job.assert_not_called()

    def test_parent_expired_when_all_children_expired(self):
        """Parent is marked expired when all its children are expired.

        When all children of a parent end up in terminal states after expiry,
        the parent should also be marked as expired to prevent it from being
        left in an active state with no executable children.
        """
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        # Create two stale slices for the same parent
        old_scheduled_time = now - timedelta(hours=2)
        slice_order_1 = self._build_slice_order(
            client_order_id="child0",
            scheduled_time=old_scheduled_time,
            slice_num=0,
        )
        slice_order_2 = self._build_slice_order(
            client_order_id="child1",
            scheduled_time=old_scheduled_time,
            slice_num=1,
        )
        parent_order = self._build_parent_order("submitted")
        db.get_pending_child_slices.return_value = [slice_order_1, slice_order_2]
        db.get_order_by_client_id.return_value = parent_order
        # After expiring both children, no non-terminal children remain
        db.count_non_terminal_children.return_value = 0

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
            stale_slice_expiry_seconds=3600,  # 1 hour threshold
        )
        scheduler.scheduler.add_job = MagicMock()
        scheduler.scheduler.get_job = MagicMock(return_value=None)

        result = scheduler.recover_zombie_slices(now=now)

        # Both children should be expired
        assert result["expired"] == 2
        # Parent should also be expired
        assert result.get("parents_expired") == 1

        # Verify update_order_status_cas was called 3 times:
        # 2 for children + 1 for parent
        assert db.update_order_status_cas.call_count == 3

        # Verify the parent was marked as expired
        parent_call = db.update_order_status_cas.call_args_list[2]
        assert parent_call[1]["client_order_id"] == "parent123"
        assert parent_call[1]["status"] == "expired"
        assert "all child slices are in terminal state" in parent_call[1]["error_message"]

    def test_parent_not_expired_when_some_children_scheduled(self):
        """Parent is NOT expired when some children are still schedulable.

        If at least one child can be scheduled, the parent should remain active.
        """
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        # One stale slice, one fresh slice
        old_scheduled_time = now - timedelta(hours=2)
        fresh_scheduled_time = now - timedelta(minutes=30)
        stale_slice = self._build_slice_order(
            client_order_id="child0",
            scheduled_time=old_scheduled_time,
            slice_num=0,
        )
        fresh_slice = self._build_slice_order(
            client_order_id="child1",
            scheduled_time=fresh_scheduled_time,
            slice_num=1,
        )
        parent_order = self._build_parent_order("submitted")
        db.get_pending_child_slices.return_value = [stale_slice, fresh_slice]
        db.get_order_by_client_id.return_value = parent_order
        # After expiring one child, one non-terminal child remains (scheduled)
        db.count_non_terminal_children.return_value = 1

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
            stale_slice_expiry_seconds=3600,  # 1 hour threshold
            market_clock_provider=lambda: MarketClockSnapshot(is_open=True, next_open=None),
        )
        scheduler.scheduler.add_job = MagicMock()
        scheduler.scheduler.get_job = MagicMock(return_value=None)

        result = scheduler.recover_zombie_slices(now=now)

        # One child expired, one scheduled
        assert result["expired"] == 1
        assert result["scheduled"] == 1
        # Parent should NOT be expired (still has pending children)
        assert result.get("parents_expired") is None or result.get("parents_expired") == 0

        # Verify update_order_status_cas was called only for the expired child
        # (not for the parent)
        assert db.update_order_status_cas.call_count == 1
        call_kwargs = db.update_order_status_cas.call_args[1]
        assert call_kwargs["client_order_id"] == "child0"
        assert call_kwargs["status"] == "expired"

    def test_parent_not_expired_when_accepted_child_exists(self):
        """Regression test: Parent NOT expired when one child is accepted at broker.

        This is the critical fix for the count_non_terminal_children method.
        Even if one child is stale (and gets expired), if another child is in
        'accepted' state (live at broker), the parent must NOT be expired.
        This prevents marking a parent as expired while live broker orders
        may still execute.

        Previous bug: count_pending_children only counted 'pending_new' status,
        missing 'submitted', 'accepted', 'submitted_unconfirmed' children.
        """
        kill_switch = MagicMock(spec=KillSwitch)
        breaker = MagicMock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False
        db = MagicMock(spec=DatabaseClient)
        executor = MagicMock(spec=AlpacaExecutor)

        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        # One stale slice (will be expired)
        old_scheduled_time = now - timedelta(hours=2)
        stale_slice = self._build_slice_order(
            client_order_id="child0",
            scheduled_time=old_scheduled_time,
            slice_num=0,
        )
        # Note: The second child is 'accepted' at broker, so it won't appear
        # in get_pending_child_slices (which returns pending_new only).
        # However, count_non_terminal_children counts ALL non-terminal children.
        parent_order = self._build_parent_order("submitted")
        db.get_pending_child_slices.return_value = [stale_slice]
        db.get_order_by_client_id.return_value = parent_order
        # Critical: One child is 'accepted' at broker (non-terminal)
        # This simulates an accepted child that won't be in pending list
        db.count_non_terminal_children.return_value = 1

        scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=breaker,
            db_client=db,
            executor=executor,
            stale_slice_expiry_seconds=3600,  # 1 hour threshold
        )
        scheduler.scheduler.add_job = MagicMock()
        scheduler.scheduler.get_job = MagicMock(return_value=None)

        result = scheduler.recover_zombie_slices(now=now)

        # Stale child should be expired
        assert result["expired"] == 1
        # Parent must NOT be expired (has live child at broker)
        assert result.get("parents_expired") is None or result.get("parents_expired") == 0

        # Verify count_non_terminal_children was called for the parent
        db.count_non_terminal_children.assert_called_with("parent123")

        # Verify only the stale child was marked as expired
        assert db.update_order_status_cas.call_count == 1
        call_kwargs = db.update_order_status_cas.call_args[1]
        assert call_kwargs["client_order_id"] == "child0"
        assert call_kwargs["status"] == "expired"
