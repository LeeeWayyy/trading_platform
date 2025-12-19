"""Tests for RecoveryManager.

Tests cover:
- Thread-safe flag accessors
- Recovery orchestration with double-checked locking
- Individual component recovery
- Needs recovery / can recover logic
- Fail-closed behavior
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from apps.execution_gateway.recovery_manager import RecoveryManager, RecoveryState

# =============================================================================
# Mock objects
# =============================================================================


class MockRedisClient:
    """Mock Redis client for testing."""

    def __init__(self, healthy: bool = True) -> None:
        self._healthy = healthy

    def health_check(self) -> bool:
        return self._healthy


class MockKillSwitch:
    """Mock kill switch for testing."""

    def __init__(self, raise_on_check: bool = False) -> None:
        self._raise_on_check = raise_on_check
        self.is_engaged_called = False

    def is_engaged(self) -> bool:
        self.is_engaged_called = True
        if self._raise_on_check:
            raise RuntimeError("Kill switch check failed")
        return False


class MockCircuitBreaker:
    """Mock circuit breaker for testing."""

    def __init__(self, raise_on_check: bool = False, state_missing: bool = False) -> None:
        self._raise_on_check = raise_on_check
        self._state_missing = state_missing
        self.is_tripped_called = False

    def is_tripped(self) -> bool:
        self.is_tripped_called = True
        if self._state_missing:
            raise RuntimeError("State missing")
        if self._raise_on_check:
            raise RuntimeError("Circuit breaker check failed")
        return False


class MockPositionReservation:
    """Mock position reservation for testing."""

    pass


class MockSliceScheduler:
    """Mock slice scheduler for testing."""

    def __init__(self, running: bool = False) -> None:
        self.scheduler = MagicMock()
        self.scheduler.running = running
        self.start_called = False

    def start(self) -> None:
        self.start_called = True
        self.scheduler.running = True


class MockDatabaseClient:
    """Mock database client for testing."""

    pass


# =============================================================================
# RecoveryState Tests
# =============================================================================


class TestRecoveryState:
    """Tests for RecoveryState dataclass."""

    def test_default_state(self) -> None:
        """Verify default state has all components unavailable = True (Fail-Closed)."""
        state = RecoveryState()

        assert state.kill_switch is None
        assert state.circuit_breaker is None
        assert state.position_reservation is None
        assert state.slice_scheduler is None
        assert state.kill_switch_unavailable is True
        assert state.circuit_breaker_unavailable is True
        assert state.position_reservation_unavailable is True

    def test_locks_are_independent(self) -> None:
        """Verify each component has its own lock."""
        state = RecoveryState()

        assert state._kill_switch_lock is not state._circuit_breaker_lock
        assert state._circuit_breaker_lock is not state._position_reservation_lock
        assert state._recovery_lock is not state._kill_switch_lock


# =============================================================================
# Thread-safe Flag Tests
# =============================================================================


class TestThreadSafeFlags:
    """Tests for thread-safe flag accessors."""

    def test_kill_switch_flag_operations(self) -> None:
        """Test kill switch unavailable flag get/set."""
        manager = RecoveryManager(redis_client=None)

        assert manager.is_kill_switch_unavailable() is True
        manager.set_kill_switch_unavailable(False)
        assert manager.is_kill_switch_unavailable() is False
        manager.set_kill_switch_unavailable(True)
        assert manager.is_kill_switch_unavailable() is True

    def test_circuit_breaker_flag_operations(self) -> None:
        """Test circuit breaker unavailable flag get/set."""
        manager = RecoveryManager(redis_client=None)

        assert manager.is_circuit_breaker_unavailable() is True
        manager.set_circuit_breaker_unavailable(False)
        assert manager.is_circuit_breaker_unavailable() is False

    def test_position_reservation_flag_operations(self) -> None:
        """Test position reservation unavailable flag get/set."""
        manager = RecoveryManager(redis_client=None)

        assert manager.is_position_reservation_unavailable() is True
        manager.set_position_reservation_unavailable(False)
        assert manager.is_position_reservation_unavailable() is False

    def test_concurrent_flag_access(self) -> None:
        """Test thread safety of flag operations under concurrent access."""
        manager = RecoveryManager(redis_client=None)
        errors: list[Exception] = []

        def toggle_flags() -> None:
            try:
                for _ in range(100):
                    manager.set_kill_switch_unavailable(True)
                    _ = manager.is_kill_switch_unavailable()
                    manager.set_kill_switch_unavailable(False)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=toggle_flags) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Concurrent access caused errors: {errors}"


# =============================================================================
# Needs Recovery Tests
# =============================================================================


class TestNeedsRecovery:
    """Tests for needs_recovery() method."""

    def test_no_recovery_needed_when_all_available(self) -> None:
        """Verify needs_recovery returns False when all components available."""
        manager = RecoveryManager(redis_client=MockRedisClient())
        manager.set_kill_switch_unavailable(False)
        manager.set_circuit_breaker_unavailable(False)
        manager.set_position_reservation_unavailable(False)
        # Also need instances (fail-closed check)
        manager.kill_switch = MockKillSwitch()
        manager.circuit_breaker = MockCircuitBreaker()
        manager.position_reservation = MockPositionReservation()

        assert manager.needs_recovery() is False

    def test_recovery_needed_when_kill_switch_unavailable(self) -> None:
        """Verify needs_recovery returns True when kill switch unavailable."""
        manager = RecoveryManager(redis_client=MockRedisClient())
        manager.set_circuit_breaker_unavailable(False)
        manager.set_position_reservation_unavailable(False)
        manager.set_kill_switch_unavailable(True)

        assert manager.needs_recovery() is True

    def test_recovery_needed_when_circuit_breaker_unavailable(self) -> None:
        """Verify needs_recovery returns True when circuit breaker unavailable."""
        manager = RecoveryManager(redis_client=MockRedisClient())
        manager.set_kill_switch_unavailable(False)
        manager.set_position_reservation_unavailable(False)
        manager.set_circuit_breaker_unavailable(True)

        assert manager.needs_recovery() is True

    def test_recovery_needed_when_position_reservation_unavailable(self) -> None:
        """Verify needs_recovery returns True when position reservation unavailable."""
        manager = RecoveryManager(redis_client=MockRedisClient())
        manager.set_kill_switch_unavailable(False)
        manager.set_circuit_breaker_unavailable(False)
        manager.set_position_reservation_unavailable(True)

        assert manager.needs_recovery() is True

    def test_recovery_needed_when_multiple_unavailable(self) -> None:
        """Verify needs_recovery returns True when multiple components unavailable."""
        manager = RecoveryManager(redis_client=MockRedisClient())
        # All default to True, but let's be explicit
        manager.set_kill_switch_unavailable(True)
        manager.set_circuit_breaker_unavailable(True)
        manager.set_position_reservation_unavailable(False)

        assert manager.needs_recovery() is True

    def test_recovery_needed_when_instance_missing_but_flag_false(self) -> None:
        """Verify needs_recovery returns True when instance is None despite flag=False.

        This is the fail-closed check: if someone clears a component reference
        without setting the unavailable flag, we still need recovery.
        """
        manager = RecoveryManager(redis_client=MockRedisClient())

        # Set all flags to False (available)
        manager.set_kill_switch_unavailable(False)
        manager.set_circuit_breaker_unavailable(False)
        manager.set_position_reservation_unavailable(False)

        # But instances are still None (not initialized)
        assert manager.kill_switch is None

        # Should still need recovery (fail-closed)
        assert manager.needs_recovery() is True

    def test_recovery_recreates_missing_instance_even_when_flag_false(self) -> None:
        """Verify attempt_recovery recreates component when instance is None but flag=False.

        Regression test: if a component ref is cleared without setting the flag,
        recovery should still attempt to recreate the component using the factory.
        """
        manager = RecoveryManager(redis_client=MockRedisClient())

        # Set flags to False but leave instances as None
        manager.set_kill_switch_unavailable(False)
        manager.set_circuit_breaker_unavailable(False)
        manager.set_position_reservation_unavailable(False)

        # Instances are None (simulating cleared references)
        assert manager.kill_switch is None

        # Recovery should detect missing instances and recreate them
        mock_ks = MockKillSwitch()
        mock_cb = MockCircuitBreaker()
        mock_pr = MockPositionReservation()
        mock_ss = MockSliceScheduler(running=False)

        result = manager.attempt_recovery(
            kill_switch_factory=lambda: mock_ks,
            circuit_breaker_factory=lambda: mock_cb,
            position_reservation_factory=lambda: mock_pr,
            slice_scheduler_factory=lambda: mock_ss,
        )

        # Components should be recreated
        assert manager.kill_switch is mock_ks
        assert manager.circuit_breaker is mock_cb
        assert manager.position_reservation is mock_pr
        assert result["kill_switch_recovered"] is True
        assert result["circuit_breaker_recovered"] is True
        assert result["position_reservation_recovered"] is True
        assert result["all_recovered"] is True


# =============================================================================
# Can Recover Tests
# =============================================================================


class TestCanRecover:
    """Tests for can_recover() method."""

    def test_cannot_recover_without_redis(self) -> None:
        """Verify can_recover returns False without Redis client."""
        manager = RecoveryManager(redis_client=None)

        assert manager.can_recover() is False

    def test_cannot_recover_when_redis_unhealthy(self) -> None:
        """Verify can_recover returns False when Redis unhealthy."""
        manager = RecoveryManager(redis_client=MockRedisClient(healthy=False))

        assert manager.can_recover() is False

    def test_can_recover_when_redis_healthy(self) -> None:
        """Verify can_recover returns True when Redis healthy."""
        manager = RecoveryManager(redis_client=MockRedisClient(healthy=True))

        assert manager.can_recover() is True

    def test_can_recover_returns_false_on_exception(self) -> None:
        """Verify can_recover returns False (fail-closed) when health check raises."""

        class ExceptionRedisClient:
            def health_check(self) -> bool:
                raise RuntimeError("Redis connection error")

        manager = RecoveryManager(redis_client=ExceptionRedisClient())  # type: ignore[arg-type]

        # Should return False instead of propagating exception
        assert manager.can_recover() is False


# =============================================================================
# Attempt Recovery Tests
# =============================================================================


class TestAttemptRecovery:
    """Tests for attempt_recovery() method."""

    def test_early_return_when_no_recovery_needed(self) -> None:
        """Verify early return when no recovery needed."""
        manager = RecoveryManager(redis_client=MockRedisClient())
        manager.set_kill_switch_unavailable(False)
        manager.set_circuit_breaker_unavailable(False)
        manager.set_position_reservation_unavailable(False)
        # Also need instances (fail-closed check)
        manager.kill_switch = MockKillSwitch()
        manager.circuit_breaker = MockCircuitBreaker()
        manager.position_reservation = MockPositionReservation()
        # Need running scheduler too (all_recovered checks scheduler status)
        manager.slice_scheduler = MockSliceScheduler(running=True)

        result = manager.attempt_recovery()

        assert result["all_recovered"] is True

    def test_early_return_when_cannot_recover(self) -> None:
        """Verify early return when Redis unavailable."""
        manager = RecoveryManager(redis_client=None)
        # Defaults to unavailable=True

        result = manager.attempt_recovery()

        assert result["all_recovered"] is False
        assert result["kill_switch_recovered"] is False

    def test_recover_kill_switch(self) -> None:
        """Test successful kill switch recovery."""
        manager = RecoveryManager(redis_client=MockRedisClient())
        # Default is True, so we don't need to set it
        # But we should ensure other components don't interfere with this specific test check if we only care about KS
        # Actually attempt_recovery tries all if they are unavailable.
        # But here we only pass KS factory.

        mock_ks = MockKillSwitch()
        result = manager.attempt_recovery(
            kill_switch_factory=lambda: mock_ks,
        )

        assert result["kill_switch_recovered"] is True
        assert manager.is_kill_switch_unavailable() is False
        assert mock_ks.is_engaged_called is True

    def test_recover_circuit_breaker(self) -> None:
        """Test successful circuit breaker recovery."""
        manager = RecoveryManager(redis_client=MockRedisClient())
        # Default is True

        mock_cb = MockCircuitBreaker()
        result = manager.attempt_recovery(
            circuit_breaker_factory=lambda: mock_cb,
        )

        assert result["circuit_breaker_recovered"] is True
        assert manager.is_circuit_breaker_unavailable() is False
        assert mock_cb.is_tripped_called is True

    def test_circuit_breaker_recovery_fails_when_state_missing(self) -> None:
        """Test circuit breaker recovery fails when state missing."""
        manager = RecoveryManager(redis_client=MockRedisClient())
        # Default is True

        mock_cb = MockCircuitBreaker(state_missing=True)
        result = manager.attempt_recovery(
            circuit_breaker_factory=lambda: mock_cb,
        )

        assert result["circuit_breaker_recovered"] is False
        assert manager.is_circuit_breaker_unavailable() is True

    def test_kill_switch_recovery_sets_unavailable_on_validation_failure(self) -> None:
        """Test kill switch recovery re-asserts unavailable flag on validation failure.

        Fail-closed: if health check fails during recovery, the flag must be True.
        """
        manager = RecoveryManager(redis_client=MockRedisClient())

        # Start with flag as False (simulating a stale state)
        manager.set_kill_switch_unavailable(False)

        # Kill switch that fails health check
        failing_ks = MockKillSwitch(raise_on_check=True)
        result = manager.attempt_recovery(
            kill_switch_factory=lambda: failing_ks,
        )

        assert result["kill_switch_recovered"] is False
        # Flag must be True (fail-closed)
        assert manager.is_kill_switch_unavailable() is True

    def test_circuit_breaker_recovery_sets_unavailable_on_failure(self) -> None:
        """Test circuit breaker recovery re-asserts unavailable flag on failure.

        Fail-closed: if health check fails during recovery, the flag must be True.
        """
        manager = RecoveryManager(redis_client=MockRedisClient())

        # Start with flag as False (simulating a stale state)
        manager.set_circuit_breaker_unavailable(False)

        # Circuit breaker with missing state
        failing_cb = MockCircuitBreaker(state_missing=True)
        result = manager.attempt_recovery(
            circuit_breaker_factory=lambda: failing_cb,
        )

        assert result["circuit_breaker_recovered"] is False
        # Flag must be True (fail-closed)
        assert manager.is_circuit_breaker_unavailable() is True

    def test_recover_position_reservation(self) -> None:
        """Test successful position reservation recovery."""
        manager = RecoveryManager(redis_client=MockRedisClient())
        # Default is True

        mock_pr = MockPositionReservation()
        result = manager.attempt_recovery(
            position_reservation_factory=lambda: mock_pr,
        )

        assert result["position_reservation_recovered"] is True
        assert manager.is_position_reservation_unavailable() is False

    def test_position_reservation_fail_closed_without_factory(self) -> None:
        """Test position reservation stays unavailable when no factory provided.

        This is the fail-closed pattern: without a factory to create a new instance,
        the component remains unavailable to prevent trading without reservation controls.
        """
        manager = RecoveryManager(redis_client=MockRedisClient())
        # Default is True

        # No factory provided
        result = manager.attempt_recovery(
            position_reservation_factory=None,
        )

        # Should remain unavailable (fail-closed)
        assert result["position_reservation_recovered"] is False
        assert manager.is_position_reservation_unavailable() is True

    def test_recover_slice_scheduler_requires_ks_and_cb(self) -> None:
        """Test slice scheduler recovery requires KS and CB available.

        Slice scheduler recovery only happens during infrastructure recovery
        (when at least one safety mechanism was unavailable and recovered).
        """
        manager = RecoveryManager(redis_client=MockRedisClient())

        # Set up: Position reservation unavailable to trigger recovery
        # KS and CB available, no slice scheduler yet

        # Explicitly make KS and CB available (default is unavailable)
        manager.set_kill_switch_unavailable(False)
        manager.set_circuit_breaker_unavailable(False)

        mock_ks = MockKillSwitch()
        mock_cb = MockCircuitBreaker()
        manager.kill_switch = mock_ks
        manager.circuit_breaker = mock_cb

        mock_pr = MockPositionReservation()
        mock_ss = MockSliceScheduler()
        result = manager.attempt_recovery(
            position_reservation_factory=lambda: mock_pr,
            slice_scheduler_factory=lambda: mock_ss,
        )

        assert result["position_reservation_recovered"] is True
        assert result["slice_scheduler_recovered"] is True
        assert mock_ss.start_called is True

    def test_slice_scheduler_not_recovered_when_ks_unavailable(self) -> None:
        """Test slice scheduler NOT recovered when kill switch unavailable."""
        manager = RecoveryManager(redis_client=MockRedisClient())
        # Default KS unavailable = True

        mock_ss = MockSliceScheduler()
        result = manager.attempt_recovery(
            slice_scheduler_factory=lambda: mock_ss,
        )

        assert result["slice_scheduler_recovered"] is False
        assert mock_ss.start_called is False

    def test_stopped_slice_scheduler_restarted(self) -> None:
        """Test existing scheduler is restarted if it stopped.

        Even when all safety components are healthy, a stopped scheduler
        should be detected and restarted by attempt_recovery.
        """
        manager = RecoveryManager(redis_client=MockRedisClient())

        # Set up safety components as available (all healthy)
        manager.set_kill_switch_unavailable(False)
        manager.set_circuit_breaker_unavailable(False)
        manager.set_position_reservation_unavailable(False)
        manager.kill_switch = MockKillSwitch()
        manager.circuit_breaker = MockCircuitBreaker()
        manager.position_reservation = MockPositionReservation()

        # Existing scheduler that stopped
        stopped_scheduler = MockSliceScheduler(running=False)
        manager.slice_scheduler = stopped_scheduler

        # Recovery should detect stopped scheduler even with healthy safety components
        result = manager.attempt_recovery()

        # Scheduler should be restarted
        assert result["slice_scheduler_recovered"] is True
        assert stopped_scheduler.start_called is True
        assert result["all_recovered"] is True

    def test_all_recovered_flag(self) -> None:
        """Test all_recovered flag reflects actual state.

        all_recovered is True only when:
        1. All safety components are available (not unavailable)
        2. Scheduler doesn't need recovery (either running or not needed)
        """
        manager = RecoveryManager(redis_client=MockRedisClient())
        # All default to True

        result = manager.attempt_recovery(
            kill_switch_factory=lambda: MockKillSwitch(),
            circuit_breaker_factory=lambda: MockCircuitBreaker(),
            position_reservation_factory=lambda: MockPositionReservation(),
            slice_scheduler_factory=lambda: MockSliceScheduler(running=False),
        )

        assert result["all_recovered"] is True

    def test_scheduler_failure_does_not_affect_all_recovered(self) -> None:
        """Test all_recovered reflects only safety components, not scheduler.

        SliceScheduler is optional for trading (see needs_recovery docstring).
        Even if scheduler fails to restart, all_recovered should be True
        as long as safety components are healthy.
        """
        manager = RecoveryManager(redis_client=MockRedisClient())

        # Set up all safety components as healthy
        manager.set_kill_switch_unavailable(False)
        manager.set_circuit_breaker_unavailable(False)
        manager.set_position_reservation_unavailable(False)
        manager.kill_switch = MockKillSwitch()
        manager.circuit_breaker = MockCircuitBreaker()
        manager.position_reservation = MockPositionReservation()

        # Stopped scheduler that will fail to restart
        class FailingScheduler(MockSliceScheduler):
            def start(self) -> None:
                raise RuntimeError("Failed to start scheduler")

        stopped_scheduler = FailingScheduler(running=False)
        manager.slice_scheduler = stopped_scheduler

        result = manager.attempt_recovery()

        # Scheduler recovery should fail
        assert result["slice_scheduler_recovered"] is False
        # But all_recovered is True because it only reflects safety components
        # (scheduler is optional for trading)
        assert result["all_recovered"] is True

    def test_scheduler_recovery_fails_when_start_doesnt_actually_start(self) -> None:
        """Test scheduler recovery fails if start() doesn't actually set running=True.

        This verifies the post-start check: even if start() doesn't raise,
        if the scheduler is still not running, recovery should fail.
        """
        manager = RecoveryManager(redis_client=MockRedisClient())

        # Set up all safety components as healthy
        manager.set_kill_switch_unavailable(False)
        manager.set_circuit_breaker_unavailable(False)
        manager.set_position_reservation_unavailable(False)
        manager.kill_switch = MockKillSwitch()
        manager.circuit_breaker = MockCircuitBreaker()
        manager.position_reservation = MockPositionReservation()

        # Scheduler where start() is a no-op (doesn't actually start)
        class NoOpStartScheduler(MockSliceScheduler):
            def start(self) -> None:
                # Intentionally don't set scheduler.running = True
                self.start_called = True

        stopped_scheduler = NoOpStartScheduler(running=False)
        manager.slice_scheduler = stopped_scheduler

        result = manager.attempt_recovery()

        # start() was called but scheduler didn't actually start
        assert stopped_scheduler.start_called is True
        assert result["slice_scheduler_recovered"] is False
        # all_recovered is True because it only reflects safety components
        # (scheduler is optional for trading)
        assert result["all_recovered"] is True

    def test_scheduler_only_recovery_works_when_redis_down(self) -> None:
        """Test scheduler can be restarted even when Redis is down.

        When safety components are already healthy but Redis is temporarily
        unavailable, scheduler-only recovery should still proceed since
        the scheduler doesn't directly depend on Redis.
        """
        # Redis client that reports unhealthy
        unhealthy_redis = MockRedisClient(healthy=False)
        manager = RecoveryManager(redis_client=unhealthy_redis)

        # All safety components are healthy with instances
        manager.set_kill_switch_unavailable(False)
        manager.set_circuit_breaker_unavailable(False)
        manager.set_position_reservation_unavailable(False)
        manager.kill_switch = MockKillSwitch()
        manager.circuit_breaker = MockCircuitBreaker()
        manager.position_reservation = MockPositionReservation()

        # Stopped scheduler that needs restart
        stopped_scheduler = MockSliceScheduler(running=False)
        manager.slice_scheduler = stopped_scheduler

        result = manager.attempt_recovery()

        # Scheduler should be restarted even with Redis down
        assert stopped_scheduler.start_called is True
        assert result["slice_scheduler_recovered"] is True
        # Safety components weren't touched (Redis down)
        assert result["kill_switch_recovered"] is False
        assert result["circuit_breaker_recovered"] is False
        assert result["position_reservation_recovered"] is False
        # all_recovered is True because safety components are healthy
        assert result["all_recovered"] is True


# =============================================================================
# Initialize Component Tests
# =============================================================================


class TestInitializeComponents:
    """Tests for component initialization methods."""

    def test_initialize_kill_switch_success(self) -> None:
        """Test successful kill switch initialization."""
        manager = RecoveryManager(redis_client=MockRedisClient())

        mock_ks = MockKillSwitch()
        result = manager.initialize_kill_switch(lambda: mock_ks)

        assert result is mock_ks
        assert manager.kill_switch is mock_ks
        assert manager.is_kill_switch_unavailable() is False

    def test_initialize_kill_switch_fails_without_redis(self) -> None:
        """Test kill switch initialization fails without Redis."""
        manager = RecoveryManager(redis_client=None)

        result = manager.initialize_kill_switch(lambda: MockKillSwitch())

        assert result is None
        assert manager.is_kill_switch_unavailable() is True

    def test_initialize_kill_switch_fails_on_exception(self) -> None:
        """Test kill switch initialization fails on exception."""
        manager = RecoveryManager(redis_client=MockRedisClient())

        def failing_factory() -> MockKillSwitch:
            raise RuntimeError("Init failed")

        result = manager.initialize_kill_switch(failing_factory)

        assert result is None
        assert manager.is_kill_switch_unavailable() is True

    def test_initialize_kill_switch_fails_when_factory_returns_none(self) -> None:
        """Test kill switch stays unavailable when factory returns None."""
        manager = RecoveryManager(redis_client=MockRedisClient())

        result = manager.initialize_kill_switch(lambda: None)  # type: ignore[arg-type,return-value]

        assert result is None
        assert manager.is_kill_switch_unavailable() is True

    def test_initialize_kill_switch_fails_when_health_check_fails(self) -> None:
        """Test kill switch stays unavailable when health check fails during init.

        This validates the fail-closed pattern: even if we can construct the instance,
        if its health check (is_engaged) fails, we must not mark it available.
        """
        manager = RecoveryManager(redis_client=MockRedisClient())

        # Kill switch that fails health check
        failing_ks = MockKillSwitch(raise_on_check=True)
        result = manager.initialize_kill_switch(lambda: failing_ks)

        assert result is None
        assert manager.is_kill_switch_unavailable() is True
        # Instance should be stored for later recovery attempts
        assert manager.kill_switch is failing_ks

    def test_initialize_circuit_breaker_success(self) -> None:
        """Test successful circuit breaker initialization."""
        manager = RecoveryManager(redis_client=MockRedisClient())

        mock_cb = MockCircuitBreaker()
        result = manager.initialize_circuit_breaker(lambda: mock_cb)

        assert result is mock_cb
        assert manager.circuit_breaker is mock_cb
        assert manager.is_circuit_breaker_unavailable() is False

    def test_initialize_circuit_breaker_fails_when_factory_returns_none(self) -> None:
        """Test circuit breaker stays unavailable when factory returns None."""
        manager = RecoveryManager(redis_client=MockRedisClient())

        result = manager.initialize_circuit_breaker(lambda: None)  # type: ignore[arg-type,return-value]

        assert result is None
        assert manager.is_circuit_breaker_unavailable() is True

    def test_initialize_circuit_breaker_fails_when_state_missing(self) -> None:
        """Test circuit breaker stays unavailable when state missing during init.

        This validates the fail-closed pattern: even if we can construct the instance,
        if its health check (is_tripped) raises RuntimeError for missing state,
        we must not mark it available.
        """
        manager = RecoveryManager(redis_client=MockRedisClient())

        # Circuit breaker with missing state (raises RuntimeError)
        failing_cb = MockCircuitBreaker(state_missing=True)
        result = manager.initialize_circuit_breaker(lambda: failing_cb)

        assert result is None
        assert manager.is_circuit_breaker_unavailable() is True
        # Instance should be stored for later recovery attempts
        assert manager.circuit_breaker is failing_cb

    def test_initialize_circuit_breaker_fails_when_health_check_fails(self) -> None:
        """Test circuit breaker stays unavailable when health check fails during init."""
        manager = RecoveryManager(redis_client=MockRedisClient())

        # Circuit breaker that fails health check (generic exception)
        failing_cb = MockCircuitBreaker(raise_on_check=True)
        result = manager.initialize_circuit_breaker(lambda: failing_cb)

        assert result is None
        assert manager.is_circuit_breaker_unavailable() is True
        # Instance should be stored for later recovery attempts
        assert manager.circuit_breaker is failing_cb

    def test_initialize_position_reservation_success(self) -> None:
        """Test successful position reservation initialization."""
        manager = RecoveryManager(redis_client=MockRedisClient())

        mock_pr = MockPositionReservation()
        result = manager.initialize_position_reservation(lambda: mock_pr)

        assert result is mock_pr
        assert manager.position_reservation is mock_pr
        assert manager.is_position_reservation_unavailable() is False

    def test_initialize_position_reservation_fails_when_factory_returns_none(self) -> None:
        """Test position reservation stays unavailable when factory returns None."""
        manager = RecoveryManager(redis_client=MockRedisClient())

        result = manager.initialize_position_reservation(lambda: None)  # type: ignore[arg-type,return-value]

        assert result is None
        assert manager.is_position_reservation_unavailable() is True


# =============================================================================
# Double-Checked Locking Tests
# =============================================================================


class TestDoubleCheckedLocking:
    """Tests for double-checked locking in attempt_recovery."""

    def test_second_check_prevents_double_recovery(self) -> None:
        """Test second check under lock prevents redundant recovery."""
        manager = RecoveryManager(redis_client=MockRedisClient())
        # Default KS unavailable = True

        recovery_count = 0

        def counting_factory() -> MockKillSwitch:
            nonlocal recovery_count
            recovery_count += 1
            return MockKillSwitch()

        # First recovery
        manager.attempt_recovery(kill_switch_factory=counting_factory)

        # Second attempt should early-exit
        manager.attempt_recovery(kill_switch_factory=counting_factory)

        # Factory should only be called once
        assert recovery_count == 1

    def test_concurrent_recovery_attempts(self) -> None:
        """Test concurrent recovery attempts don't cause multiple recoveries."""
        manager = RecoveryManager(redis_client=MockRedisClient())
        # Default KS unavailable = True

        recovery_count = 0
        recovery_lock = threading.Lock()

        def counting_factory() -> MockKillSwitch:
            nonlocal recovery_count
            with recovery_lock:
                recovery_count += 1
            return MockKillSwitch()

        def attempt() -> None:
            manager.attempt_recovery(kill_switch_factory=counting_factory)

        threads = [threading.Thread(target=attempt) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Factory should only be called once due to double-checked locking
        assert recovery_count == 1


# =============================================================================
# Component Property Tests
# =============================================================================


class TestComponentProperties:
    """Tests for component getter/setter properties."""

    def test_kill_switch_property(self) -> None:
        """Test kill switch property get/set."""
        manager = RecoveryManager(redis_client=None)
        mock_ks = MockKillSwitch()

        assert manager.kill_switch is None
        manager.kill_switch = mock_ks
        assert manager.kill_switch is mock_ks

    def test_circuit_breaker_property(self) -> None:
        """Test circuit breaker property get/set."""
        manager = RecoveryManager(redis_client=None)
        mock_cb = MockCircuitBreaker()

        assert manager.circuit_breaker is None
        manager.circuit_breaker = mock_cb
        assert manager.circuit_breaker is mock_cb

    def test_position_reservation_property(self) -> None:
        """Test position reservation property get/set."""
        manager = RecoveryManager(redis_client=None)
        mock_pr = MockPositionReservation()

        assert manager.position_reservation is None
        manager.position_reservation = mock_pr
        assert manager.position_reservation is mock_pr

    def test_slice_scheduler_property(self) -> None:
        """Test slice scheduler property get/set."""
        manager = RecoveryManager(redis_client=None)
        mock_ss = MockSliceScheduler()

        assert manager.slice_scheduler is None
        manager.slice_scheduler = mock_ss
        assert manager.slice_scheduler is mock_ss
