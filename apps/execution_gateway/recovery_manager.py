"""
Recovery Manager for Execution Gateway Infrastructure.

Encapsulates thread-safe recovery of:
- KillSwitch
- CircuitBreaker
- PositionReservation
- SliceScheduler

Replaces scattered module-level locks and getter/setter functions in main.py
with a centralized, testable class following the fail-closed pattern.

Design Decisions:
- All safety mechanisms fail closed (trading blocked until recovered)
- Double-checked locking prevents concurrent recovery attempts
- Recovery order: KillSwitch -> CircuitBreaker -> PositionReservation -> SliceScheduler
  (SliceScheduler depends on KillSwitch and CircuitBreaker)
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apps.execution_gateway.database import DatabaseClient
    from apps.execution_gateway.slice_scheduler import SliceScheduler
    from libs.redis_client import RedisClient
    from libs.risk_management import CircuitBreaker, KillSwitch, PositionReservation

logger = logging.getLogger(__name__)


@dataclass
class RecoveryState:
    """State container for recovery-managed components.

    Note on thread safety:
    - The per-component locks (_kill_switch_lock, etc.) ONLY protect the
      boolean unavailable flags, NOT the component instance references.
    - Component instance assignments rely on Python's atomic reference assignment.
    - The recovery_lock prevents concurrent recovery attempts.
    - For strict instance synchronization, callers should use the recovery_lock.
    """

    # Component instances (None = not initialized)
    kill_switch: KillSwitch | None = None
    circuit_breaker: CircuitBreaker | None = None
    position_reservation: PositionReservation | None = None
    slice_scheduler: SliceScheduler | None = None

    # Unavailable flags (True = component failed, trading blocked)
    # Default to True (fail-closed) until explicitly initialized
    kill_switch_unavailable: bool = True
    circuit_breaker_unavailable: bool = True
    position_reservation_unavailable: bool = True

    # Per-component locks for thread-safe flag access
    _kill_switch_lock: threading.Lock = field(default_factory=threading.Lock)
    _circuit_breaker_lock: threading.Lock = field(default_factory=threading.Lock)
    _position_reservation_lock: threading.Lock = field(default_factory=threading.Lock)

    # Recovery lock to prevent concurrent recovery attempts
    _recovery_lock: threading.Lock = field(default_factory=threading.Lock)


class RecoveryManager:
    """Orchestrates recovery of infrastructure components.

    Thread-safe manager for kill-switch, circuit breaker, position reservation,
    and slice scheduler. Implements fail-closed pattern where any unavailable
    safety mechanism blocks all trading.

    Example:
        >>> manager = RecoveryManager(
        ...     redis_client=redis_client,
        ...     db_client=db_client,
        ...     executor=alpaca_client,
        ... )
        >>> if manager.needs_recovery():
        ...     result = manager.attempt_recovery()
        ...     if not result["all_recovered"]:
        ...         logger.warning("Some components still unavailable")
    """

    def __init__(
        self,
        redis_client: RedisClient | None,
        db_client: DatabaseClient | None = None,
        executor: Any | None = None,  # AlpacaExecutor, optional in DRY_RUN
    ) -> None:
        """Initialize recovery manager.

        Args:
            redis_client: Redis client for KillSwitch, CircuitBreaker, PositionReservation
            db_client: Database client for SliceScheduler
            executor: Alpaca executor for SliceScheduler (can be None in DRY_RUN)
        """
        self._state = RecoveryState()
        self._redis_client = redis_client
        self._db_client = db_client
        self._executor = executor

    # =========================================================================
    # Thread-safe flag accessors
    # =========================================================================

    def is_kill_switch_unavailable(self) -> bool:
        """Thread-safe check if kill-switch is unavailable."""
        with self._state._kill_switch_lock:
            return self._state.kill_switch_unavailable

    def set_kill_switch_unavailable(self, value: bool) -> None:
        """Thread-safe set kill-switch unavailable state."""
        with self._state._kill_switch_lock:
            self._state.kill_switch_unavailable = value

    def is_circuit_breaker_unavailable(self) -> bool:
        """Thread-safe check if circuit breaker is unavailable."""
        with self._state._circuit_breaker_lock:
            return self._state.circuit_breaker_unavailable

    def set_circuit_breaker_unavailable(self, value: bool) -> None:
        """Thread-safe set circuit breaker unavailable state."""
        with self._state._circuit_breaker_lock:
            self._state.circuit_breaker_unavailable = value

    def is_position_reservation_unavailable(self) -> bool:
        """Thread-safe check if position reservation is unavailable."""
        with self._state._position_reservation_lock:
            return self._state.position_reservation_unavailable

    def set_position_reservation_unavailable(self, value: bool) -> None:
        """Thread-safe set position reservation unavailable state."""
        with self._state._position_reservation_lock:
            self._state.position_reservation_unavailable = value

    # =========================================================================
    # Component accessors
    # =========================================================================

    @property
    def kill_switch(self) -> KillSwitch | None:
        """Get kill switch instance."""
        return self._state.kill_switch

    @kill_switch.setter
    def kill_switch(self, value: KillSwitch | None) -> None:
        """Set kill switch instance."""
        self._state.kill_switch = value

    @property
    def circuit_breaker(self) -> CircuitBreaker | None:
        """Get circuit breaker instance."""
        return self._state.circuit_breaker

    @circuit_breaker.setter
    def circuit_breaker(self, value: CircuitBreaker | None) -> None:
        """Set circuit breaker instance."""
        self._state.circuit_breaker = value

    @property
    def position_reservation(self) -> PositionReservation | None:
        """Get position reservation instance."""
        return self._state.position_reservation

    @position_reservation.setter
    def position_reservation(self, value: PositionReservation | None) -> None:
        """Set position reservation instance."""
        self._state.position_reservation = value

    @property
    def slice_scheduler(self) -> SliceScheduler | None:
        """Get slice scheduler instance."""
        return self._state.slice_scheduler

    @slice_scheduler.setter
    def slice_scheduler(self, value: SliceScheduler | None) -> None:
        """Set slice scheduler instance."""
        self._state.slice_scheduler = value

    # =========================================================================
    # Recovery orchestration
    # =========================================================================

    def needs_recovery(self) -> bool:
        """Check if any safety mechanism is unavailable.

        Note: SliceScheduler is intentionally NOT included here because:
        1. It's not a safety mechanism - it's an optional scheduling component
        2. Trading can proceed with single orders even if TWAP slicing is unavailable
        3. SliceScheduler depends on KillSwitch + CircuitBreaker, so it's recovered
           opportunistically when those are available (see _should_recover_slice_scheduler)

        Returns:
            True if any SAFETY component needs recovery, False if all healthy.
            Also returns True if component instances are missing (fail-closed).
        """
        # Check flags
        if (
            self.is_kill_switch_unavailable()
            or self.is_circuit_breaker_unavailable()
            or self.is_position_reservation_unavailable()
        ):
            return True

        # Fail-closed: also check for missing instances
        # (e.g., if reference was cleared but flag wasn't set)
        if (
            self._state.kill_switch is None
            or self._state.circuit_breaker is None
            or self._state.position_reservation is None
        ):
            return True

        return False

    def can_recover(self) -> bool:
        """Check if recovery is possible.

        Returns:
            True if Redis client is available and connected.
            Returns False (fail-closed) on any exception.
        """
        if self._redis_client is None:
            return False
        try:
            return self._redis_client.health_check()
        except Exception as e:
            logger.warning(f"Redis health check failed during recovery check: {e}")
            return False

    def attempt_recovery(
        self,
        kill_switch_factory: Callable[[], KillSwitch] | None = None,
        circuit_breaker_factory: Callable[[], CircuitBreaker] | None = None,
        position_reservation_factory: Callable[[], PositionReservation] | None = None,
        slice_scheduler_factory: Callable[[], SliceScheduler] | None = None,
    ) -> dict[str, bool]:
        """Attempt to recover unavailable components.

        Uses double-checked locking to prevent concurrent recovery attempts.
        Recovery order: KillSwitch -> CircuitBreaker -> PositionReservation -> SliceScheduler

        Args:
            kill_switch_factory: Factory function to create KillSwitch
            circuit_breaker_factory: Factory function to create CircuitBreaker
            position_reservation_factory: Factory function to create PositionReservation
            slice_scheduler_factory: Factory function to create SliceScheduler

        Returns:
            Dict with recovery status for each component and overall status:
            {
                "kill_switch_recovered": bool,
                "circuit_breaker_recovered": bool,
                "position_reservation_recovered": bool,
                "slice_scheduler_recovered": bool,
                "all_recovered": bool,
            }
        """
        result = {
            "kill_switch_recovered": False,
            "circuit_breaker_recovered": False,
            "position_reservation_recovered": False,
            "slice_scheduler_recovered": False,
            "all_recovered": False,
        }

        # Pre-check: do we need any recovery?
        # Check both safety components AND slice scheduler (which can stop independently)
        needs_safety_recovery = self.needs_recovery()
        needs_scheduler_recovery = self._should_recover_slice_scheduler()

        if not needs_safety_recovery and not needs_scheduler_recovery:
            result["all_recovered"] = True
            return result

        # Pre-check: can we recover safety components?
        # Note: Scheduler recovery doesn't require Redis - it only needs healthy safety components
        can_recover_safety = self.can_recover()
        if needs_safety_recovery and not can_recover_safety:
            logger.warning("Safety component recovery not possible - Redis unavailable")
            # Can still attempt scheduler-only recovery below if safety components are healthy

        # Double-checked locking
        with self._state._recovery_lock:
            # Re-check under lock
            needs_safety_recovery = self.needs_recovery()
            needs_scheduler_recovery = self._should_recover_slice_scheduler()

            if not needs_safety_recovery and not needs_scheduler_recovery:
                result["all_recovered"] = True
                return result

            # Isolate each component recovery to prevent one failure from aborting others
            # Safety components require Redis to be available
            if can_recover_safety:
                # 1. Recover Kill Switch (flag=True OR instance missing)
                if self.is_kill_switch_unavailable() or self._state.kill_switch is None:
                    result["kill_switch_recovered"] = self._recover_kill_switch(kill_switch_factory)

                # 2. Recover Circuit Breaker (flag=True OR instance missing)
                if self.is_circuit_breaker_unavailable() or self._state.circuit_breaker is None:
                    result["circuit_breaker_recovered"] = self._recover_circuit_breaker(
                        circuit_breaker_factory
                    )

                # 3. Recover Position Reservation (flag=True OR instance missing)
                if (
                    self.is_position_reservation_unavailable()
                    or self._state.position_reservation is None
                ):
                    result["position_reservation_recovered"] = self._recover_position_reservation(
                        position_reservation_factory
                    )

            # 4. Recover Slice Scheduler (doesn't require Redis - only healthy safety components)
            if self._should_recover_slice_scheduler():
                result["slice_scheduler_recovered"] = self._recover_slice_scheduler(
                    slice_scheduler_factory
                )

            logger.info(
                "Infrastructure recovery attempt completed",
                extra={
                    "kill_switch_available": not self.is_kill_switch_unavailable(),
                    "breaker_available": not self.is_circuit_breaker_unavailable(),
                    "position_reservation_available": not self.is_position_reservation_unavailable(),
                    **result,
                },
            )

        # all_recovered = ALL safety components healthy
        # Note: SliceScheduler is intentionally excluded as it's optional for trading
        # (see needs_recovery() docstring). Scheduler status is in slice_scheduler_recovered.
        result["all_recovered"] = not self.needs_recovery()
        return result

    def _recover_kill_switch(self, factory: Callable[[], KillSwitch] | None) -> bool:
        """Recover kill switch component.

        Returns:
            True if recovery successful, False otherwise.
        """
        try:
            if self._state.kill_switch is None and factory is not None:
                self._state.kill_switch = factory()
                logger.info(
                    "Kill-switch re-initialized after Redis recovery",
                    extra={"kill_switch_recovered": True},
                )

            if self._state.kill_switch is not None:
                # Verify it works
                self._state.kill_switch.is_engaged()
                self.set_kill_switch_unavailable(False)
                logger.info("Kill-switch recovered and validated")
                return True
            else:
                # No instance and no factory - fail closed
                logger.warning("Kill-switch recovery failed: no instance and no factory")
                self.set_kill_switch_unavailable(True)
                return False

        except Exception as e:
            logger.warning(f"Kill-switch recovery failed: {e}", exc_info=True)
            # Fail-closed: ensure unavailable flag is set on any failure
            self.set_kill_switch_unavailable(True)

        return False

    def _recover_circuit_breaker(self, factory: Callable[[], CircuitBreaker] | None) -> bool:
        """Recover circuit breaker component.

        Returns:
            True if recovery successful, False otherwise.
        """
        try:
            if self._state.circuit_breaker is None and factory is not None:
                self._state.circuit_breaker = factory()
                logger.info(
                    "Circuit breaker re-initialized after Redis recovery",
                    extra={"breaker_recovered": True},
                )

            if self._state.circuit_breaker is not None:
                # Verify it works (check trip status)
                try:
                    self._state.circuit_breaker.is_tripped()
                    self.set_circuit_breaker_unavailable(False)
                    logger.info("Circuit breaker recovered and validated")
                    return True
                except RuntimeError:
                    # State still missing, keep unavailable (fail-closed)
                    logger.warning("Circuit breaker re-initialized but state still missing")
                    self.set_circuit_breaker_unavailable(True)
                    return False
            else:
                # No instance and no factory - fail closed
                logger.warning("Circuit breaker recovery failed: no instance and no factory")
                self.set_circuit_breaker_unavailable(True)
                return False

        except Exception as e:
            logger.warning(f"Circuit breaker recovery failed: {e}", exc_info=True)
            # Fail-closed: ensure unavailable flag is set on any failure
            self.set_circuit_breaker_unavailable(True)

        return False

    def _recover_position_reservation(
        self, factory: Callable[[], PositionReservation] | None
    ) -> bool:
        """Recover position reservation component.

        Returns:
            True if recovery successful, False otherwise.
        """
        try:
            if self._state.position_reservation is None and factory is not None:
                self._state.position_reservation = factory()
                logger.info(
                    "Position reservation re-initialized after Redis recovery",
                    extra={"position_reservation_recovered": True},
                )

            # Fail-closed: only clear flag if we have a valid instance AND Redis is healthy
            if self._state.position_reservation is not None:
                # Validate Redis is reachable before marking available
                # (PositionReservation is Redis-backed)
                if self._redis_client is None or not self._redis_client.health_check():
                    logger.warning("Position reservation instance exists but Redis unavailable")
                    self.set_position_reservation_unavailable(True)
                    return False

                self.set_position_reservation_unavailable(False)
                logger.info("Position reservation recovered and validated")
                return True
            else:
                logger.warning("Position reservation recovery failed: no instance available")
                # Fail-closed: ensure unavailable flag is set
                self.set_position_reservation_unavailable(True)
                return False

        except Exception as e:
            logger.warning(f"Position reservation recovery failed: {e}", exc_info=True)
            # Fail-closed: ensure unavailable flag is set on any failure
            self.set_position_reservation_unavailable(True)

        return False

    def _should_recover_slice_scheduler(self) -> bool:
        """Check if slice scheduler should be recovered or restarted.

        Slice scheduler depends on both kill switch and circuit breaker.
        Recovery is needed when:
        1. No scheduler exists and we have healthy safety components, OR
        2. Scheduler exists but its internal scheduler stopped
        """
        # Safety components must be available
        if (
            self.is_kill_switch_unavailable()
            or self.is_circuit_breaker_unavailable()
            or self._state.kill_switch is None
            or self._state.circuit_breaker is None
        ):
            return False

        # Need recovery if no scheduler exists
        if self._state.slice_scheduler is None:
            return True

        # Need recovery if scheduler exists but stopped
        try:
            if not self._state.slice_scheduler.scheduler.running:
                return True
        except Exception:
            # If we can't check, assume needs recovery
            return True

        return False

    def _recover_slice_scheduler(self, factory: Callable[[], SliceScheduler] | None) -> bool:
        """Recover or restart slice scheduler component.

        Returns:
            True if recovery successful AND scheduler is running, False otherwise.
        """
        try:
            # Case 1: Existing scheduler stopped - restart it
            if self._state.slice_scheduler is not None:
                if not self._state.slice_scheduler.scheduler.running:
                    self._state.slice_scheduler.start()
                    # Verify it actually started (fail-closed)
                    if not self._state.slice_scheduler.scheduler.running:
                        logger.warning("Slice scheduler start() called but still not running")
                        return False
                    logger.info(
                        "Slice scheduler restarted",
                        extra={"scheduler_recovered": True, "scheduler_restarted": True},
                    )
                    return True
                # Already running, nothing to do
                return True

            # Case 2: No scheduler - create new one
            if factory is not None:
                self._state.slice_scheduler = factory()

                # Start the scheduler if not running
                if not self._state.slice_scheduler.scheduler.running:
                    self._state.slice_scheduler.start()
                    # Verify it actually started (fail-closed)
                    if not self._state.slice_scheduler.scheduler.running:
                        logger.warning("New slice scheduler start() called but still not running")
                        return False
                    logger.info(
                        "Slice scheduler re-initialized and started after Redis recovery",
                        extra={"scheduler_recovered": True, "scheduler_started": True},
                    )
                else:
                    logger.info(
                        "Slice scheduler re-initialized but already running",
                        extra={"scheduler_recovered": True, "scheduler_already_running": True},
                    )
                return True

        except Exception as e:
            logger.warning(f"Slice scheduler recovery failed: {e}", exc_info=True)

        return False

    # =========================================================================
    # Initial setup helpers
    # =========================================================================

    def initialize_kill_switch(self, factory: Callable[[], KillSwitch]) -> KillSwitch | None:
        """Initialize kill switch at startup.

        Fails closed if initialization fails (sets unavailable flag).

        Returns:
            KillSwitch instance if successful, None otherwise.
        """
        if self._redis_client is None:
            logger.error(
                "Kill-switch not initialized (Redis unavailable). "
                "FAILING CLOSED - all trading blocked until Redis available."
            )
            self.set_kill_switch_unavailable(True)
            return None

        try:
            instance = factory()
            if instance is None:
                logger.error(
                    "Kill-switch factory returned None. "
                    "FAILING CLOSED - all trading blocked until Redis available."
                )
                self.set_kill_switch_unavailable(True)
                return None

            # Validate health before marking available (same as recovery path)
            try:
                instance.is_engaged()
            except Exception as health_err:
                logger.error(
                    f"Kill-switch health check failed during init: {health_err}. "
                    "FAILING CLOSED - all trading blocked until Redis available."
                )
                self._state.kill_switch = instance  # Store for later recovery
                self.set_kill_switch_unavailable(True)
                return None

            self._state.kill_switch = instance
            self.set_kill_switch_unavailable(False)
            logger.info("Kill-switch initialized and validated successfully")
            return self._state.kill_switch
        except Exception as e:
            logger.error(
                f"Failed to initialize kill-switch: {e}. "
                "FAILING CLOSED - all trading blocked until Redis available."
            )
            self.set_kill_switch_unavailable(True)
            return None

    def initialize_circuit_breaker(
        self, factory: Callable[[], CircuitBreaker]
    ) -> CircuitBreaker | None:
        """Initialize circuit breaker at startup.

        Fails closed if initialization fails (sets unavailable flag).

        Returns:
            CircuitBreaker instance if successful, None otherwise.
        """
        if self._redis_client is None:
            logger.error(
                "Circuit breaker not initialized (Redis unavailable). "
                "FAILING CLOSED - all trading blocked until Redis available."
            )
            self.set_circuit_breaker_unavailable(True)
            return None

        try:
            instance = factory()
            if instance is None:
                logger.error(
                    "Circuit breaker factory returned None. "
                    "FAILING CLOSED - all trading blocked until Redis available."
                )
                self.set_circuit_breaker_unavailable(True)
                return None

            # Validate health before marking available (same as recovery path)
            try:
                instance.is_tripped()
            except RuntimeError as state_err:
                logger.error(
                    f"Circuit breaker state missing during init: {state_err}. "
                    "FAILING CLOSED - all trading blocked until Redis available."
                )
                self._state.circuit_breaker = instance  # Store for later recovery
                self.set_circuit_breaker_unavailable(True)
                return None
            except Exception as health_err:
                logger.error(
                    f"Circuit breaker health check failed during init: {health_err}. "
                    "FAILING CLOSED - all trading blocked until Redis available."
                )
                self._state.circuit_breaker = instance  # Store for later recovery
                self.set_circuit_breaker_unavailable(True)
                return None

            self._state.circuit_breaker = instance
            self.set_circuit_breaker_unavailable(False)
            logger.info("Circuit breaker initialized and validated successfully")
            return self._state.circuit_breaker
        except Exception as e:
            logger.error(
                f"Failed to initialize circuit breaker: {e}. "
                "FAILING CLOSED - all trading blocked until Redis available."
            )
            self.set_circuit_breaker_unavailable(True)
            return None

    def initialize_position_reservation(
        self, factory: Callable[[], PositionReservation]
    ) -> PositionReservation | None:
        """Initialize position reservation at startup.

        Fails closed if initialization fails (sets unavailable flag).

        Returns:
            PositionReservation instance if successful, None otherwise.
        """
        if self._redis_client is None:
            logger.error(
                "Position reservation not initialized (Redis unavailable). "
                "FAILING CLOSED - all trading blocked until Redis available."
            )
            self.set_position_reservation_unavailable(True)
            return None

        try:
            instance = factory()
            if instance is None:
                logger.error(
                    "Position reservation factory returned None. "
                    "FAILING CLOSED - all trading blocked until Redis available."
                )
                self.set_position_reservation_unavailable(True)
                return None

            # Validate Redis is healthy before marking available
            # (PositionReservation is Redis-backed)
            if not self._redis_client.health_check():
                logger.error(
                    "Position reservation created but Redis health check failed. "
                    "FAILING CLOSED - all trading blocked until Redis available."
                )
                self._state.position_reservation = instance  # Store for later recovery
                self.set_position_reservation_unavailable(True)
                return None

            self._state.position_reservation = instance
            self.set_position_reservation_unavailable(False)
            logger.info("Position reservation initialized and validated successfully")
            return self._state.position_reservation
        except Exception as e:
            logger.error(
                f"Failed to initialize position reservation: {e}. "
                "FAILING CLOSED - all trading blocked until Redis available."
            )
            self.set_position_reservation_unavailable(True)
            return None
