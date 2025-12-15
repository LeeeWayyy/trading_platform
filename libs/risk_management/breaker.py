"""
Circuit breaker state machine for automatic trading halts.

The circuit breaker automatically halts trading when risk conditions are violated
(e.g., daily loss limit exceeded, max drawdown breached, data staleness).

State Machine:
    OPEN → (violation detected) → TRIPPED → (manual reset) → QUIET_PERIOD → OPEN

Storage:
    State persisted in Redis for fast access and cross-service consistency.

Example:
    >>> from libs.redis_client import RedisClient
    >>> from libs.risk_management.breaker import CircuitBreaker
    >>>
    >>> redis = RedisClient(host="localhost", port=6379)
    >>> breaker = CircuitBreaker(redis_client=redis)
    >>>
    >>> # Check if trading allowed
    >>> if breaker.is_tripped():
    ...     raise CircuitBreakerTripped(f"Cannot trade: {breaker.get_trip_reason()}")
    >>>
    >>> # Trip on violation
    >>> breaker.trip("DAILY_LOSS_EXCEEDED", details={"daily_loss": -5234.56})
    >>>
    >>> # Manual reset (after conditions cleared)
    >>> breaker.reset()

See Also:
    - docs/CONCEPTS/risk-management.md#circuit-breakers
    - docs/ADRs/0011-risk-management-system.md#circuit-breaker-design
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, cast

import redis.exceptions

from libs.redis_client import RedisClient
from libs.risk_management.exceptions import CircuitBreakerError

logger = logging.getLogger(__name__)


class CircuitBreakerState(str, Enum):
    """
    Circuit breaker states.

    OPEN: Normal trading allowed
    TRIPPED: Trading blocked (new entries forbidden)
    QUIET_PERIOD: Monitoring only after reset (5 min before returning to OPEN)
    """

    OPEN = "OPEN"
    TRIPPED = "TRIPPED"
    QUIET_PERIOD = "QUIET_PERIOD"


class TripReason(str, Enum):
    """
    Predefined trip reasons.

    Each reason corresponds to a specific risk condition violation.
    """

    DAILY_LOSS_EXCEEDED = "DAILY_LOSS_EXCEEDED"
    MAX_DRAWDOWN = "MAX_DRAWDOWN"
    DATA_STALE = "DATA_STALE"
    BROKER_ERRORS = "BROKER_ERRORS"
    MANUAL = "MANUAL"


class CircuitBreaker:
    """
    Circuit breaker for automatic trading halts.

    Manages state transitions, trip/reset operations, and Redis persistence.

    Attributes:
        redis: Redis client for state storage
        state_key: Redis key for breaker state
        history_key: Redis key for trip history (append-only log)

    Example:
        >>> breaker = CircuitBreaker(redis_client=redis)
        >>> breaker.get_state()
        <CircuitBreakerState.OPEN: 'OPEN'>
        >>> breaker.trip("DAILY_LOSS_EXCEEDED")
        >>> breaker.is_tripped()
        True
        >>> breaker.reset()
        >>> breaker.get_state()
        <CircuitBreakerState.QUIET_PERIOD: 'QUIET_PERIOD'>

    Notes:
        - State transitions are atomic (Redis operations)
        - All operations logged to trip history
        - Quiet period lasts 5 minutes after reset
        - Thread-safe via Redis atomic operations
    """

    QUIET_PERIOD_DURATION = 300  # 5 minutes in seconds

    def __init__(self, redis_client: RedisClient, auto_initialize: bool = False):
        """
        Initialize circuit breaker.

        Args:
            redis_client: Redis client for state persistence
            auto_initialize: If True, auto-create OPEN state when missing.
                SECURITY WARNING: Set to False in production to ensure fail-closed
                behavior when Redis is flushed/restarted. Only set to True in tests
                or when explicit operator verification has occurred.

        Example:
            >>> from libs.redis_client import RedisClient
            >>> redis = RedisClient(host="localhost", port=6379)
            >>> breaker = CircuitBreaker(redis_client=redis)  # Fail closed if state missing
            >>>
            >>> # For tests only - auto-initialize state
            >>> breaker = CircuitBreaker(redis_client=redis, auto_initialize=True)

        Security:
            - Default auto_initialize=False ensures fail-closed behavior
            - When Redis is flushed/restarted, get_state() will raise RuntimeError
            - Operator must explicitly reinitialize via initialize_state() after verification
        """
        self.redis = redis_client
        self.state_key = "circuit_breaker:state"
        self.history_key = "circuit_breaker:trip_history"
        self.max_history_entries = 1000  # Keep last 1000 trip events

        # SECURITY: Do NOT auto-initialize state by default (fail-closed)
        # Auto-initialization after Redis flush would silently resume trading
        # without operator verification - a critical safety violation
        if auto_initialize and not self.redis.get(self.state_key):
            logger.warning(
                "Circuit breaker auto-initializing to OPEN state. "
                "This should only happen in tests or after explicit operator verification."
            )
            self._initialize_state()

    def initialize_state(self, force: bool = False) -> bool:
        """
        Explicitly initialize circuit breaker state in Redis.

        This method should be called by operators after verifying system safety
        following a Redis flush/restart. It creates the initial OPEN state.

        Args:
            force: If True, overwrite existing state. If False, only initialize
                   if state doesn't exist.

        Returns:
            True if state was initialized, False if state already exists and force=False

        Example:
            >>> # After Redis restart, operator verifies system is safe
            >>> breaker = CircuitBreaker(redis_client=redis)
            >>> breaker.initialize_state()  # Explicitly initialize
            True

        Security:
            - Call this ONLY after verifying no positions are at risk
            - Consider tripping the breaker first for safety:
              breaker.initialize_state() then breaker.trip("MANUAL", {"reason": "post-restart"})
        """
        if not force and self.redis.get(self.state_key):
            logger.info("Circuit breaker state already exists, skipping initialization")
            return False

        self._initialize_state()
        return True

    def _initialize_state(self) -> None:
        """
        Internal: Initialize circuit breaker state in Redis.

        Creates default OPEN state with zero trip count.
        For external use, call initialize_state() instead.
        """
        default_state = {
            "state": CircuitBreakerState.OPEN.value,
            "tripped_at": None,
            "trip_reason": None,
            "trip_details": None,
            "reset_at": None,
            "reset_by": None,
            "trip_count_today": 0,
        }
        self.redis.set(self.state_key, json.dumps(default_state))
        logger.info("Circuit breaker initialized: state=OPEN")

    def get_state(self) -> CircuitBreakerState:
        """
        Get current circuit breaker state.

        Automatically transitions from QUIET_PERIOD to OPEN if quiet period expired.

        Returns:
            Current state (OPEN, TRIPPED, or QUIET_PERIOD)

        Raises:
            RuntimeError: If circuit breaker state is missing from Redis (fail-closed).
                This can happen after a Redis flush/restart. The system MUST fail closed
                rather than silently resuming trading - operator must verify safety
                and manually reinitialize if appropriate.

        Example:
            >>> breaker.get_state()
            <CircuitBreakerState.OPEN: 'OPEN'>

        Notes:
            - State should exist after __init__ - if missing, Redis was flushed/restarted
            - FAIL CLOSED: Do not auto-reinitialize to OPEN (could resume trading unsafely)
            - Operator must manually verify conditions and reinitialize if appropriate
        """
        state_json = self.redis.get(self.state_key)
        if not state_json:
            # State should exist after __init__ - if missing, Redis was flushed/restarted
            # FAIL CLOSED: Do not auto-reinitialize to OPEN (could resume trading unsafely)
            # This matches KillSwitch behavior (kill_switch.py:155-166)
            logger.error(
                "CRITICAL: Circuit breaker state missing from Redis (possible flush/restart). "
                "Failing closed to prevent unsafe trading resumption."
            )
            raise RuntimeError(
                "Circuit breaker state missing from Redis. System must fail closed - "
                "operator must verify safety and manually reinitialize if appropriate."
            )

        state_data = json.loads(state_json)

        # Check if quiet period expired
        if state_data["state"] == CircuitBreakerState.QUIET_PERIOD.value:
            if state_data.get("reset_at"):
                reset_at = datetime.fromisoformat(state_data["reset_at"])
                elapsed = datetime.now(UTC) - reset_at
                if elapsed > timedelta(seconds=self.QUIET_PERIOD_DURATION):
                    # Auto-transition to OPEN
                    logger.info(
                        f"Quiet period expired after {elapsed.total_seconds():.0f}s, "
                        f"transitioning to OPEN"
                    )
                    self._transition_to_open()
                    return CircuitBreakerState.OPEN

        return CircuitBreakerState(state_data["state"])

    def is_tripped(self) -> bool:
        """
        Check if circuit breaker is currently TRIPPED.

        Returns:
            True if TRIPPED, False otherwise

        Example:
            >>> if breaker.is_tripped():
            ...     raise CircuitBreakerTripped("Trading blocked")
        """
        return self.get_state() == CircuitBreakerState.TRIPPED

    def trip(
        self,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """
        Trip the circuit breaker atomically.

        Transitions state from OPEN/QUIET_PERIOD to TRIPPED and logs reason.
        Uses WATCH/MULTI/EXEC transaction to prevent race conditions in
        multi-process/multi-threaded environments.

        Args:
            reason: Trip reason (from TripReason enum or custom string)
            details: Optional dict with additional context (e.g., {"daily_loss": -5234.56})

        Example:
            >>> breaker.trip("DAILY_LOSS_EXCEEDED", details={"daily_loss": -5234.56})
            >>> breaker.is_tripped()
            True

        Notes:
            - This operation is atomic and safe for concurrent use
            - If already TRIPPED, logs warning but doesn't error (idempotent)
            - Increments trip_count_today atomically
            - Appends to trip history log
            - Retries automatically on concurrent modification (WatchError)
        """
        # Use public RedisClient pipeline() method (Gemini HIGH fix)
        with self.redis.pipeline() as pipe:
            while True:
                try:
                    # Watch the state key for changes from other clients
                    pipe.watch(self.state_key)

                    state_json = pipe.get(self.state_key)
                    if not state_json:
                        # FAIL CLOSED: State missing during trip - do not auto-initialize to OPEN
                        # Codex CRITICAL fix: trip() must fail-closed, not silently create OPEN state
                        pipe.unwatch()
                        logger.error(
                            "CRITICAL: Circuit breaker state missing during trip. "
                            "Failing closed - operator must reinitialize if appropriate."
                        )
                        raise RuntimeError(
                            "Circuit breaker state missing from Redis during trip. "
                            "System must fail closed - operator must verify safety and "
                            "manually reinitialize if appropriate."
                        )

                    state_data: dict[str, Any] = json.loads(cast(str, state_json))

                    # Check if already tripped (idempotent behavior)
                    if state_data["state"] == CircuitBreakerState.TRIPPED.value:
                        logger.warning(
                            f"Circuit breaker already TRIPPED: {state_data.get('trip_reason')}"
                        )
                        pipe.unwatch()
                        return

                    # Start atomic transaction
                    pipe.multi()

                    # Update state
                    now = datetime.now(UTC).isoformat()
                    state_data.update(
                        {
                            "state": CircuitBreakerState.TRIPPED.value,
                            "tripped_at": now,
                            "trip_reason": reason,
                            "trip_details": details,
                            "trip_count_today": state_data.get("trip_count_today", 0) + 1,
                        }
                    )

                    pipe.set(self.state_key, json.dumps(state_data))

                    # Execute transaction atomically
                    pipe.execute()

                    # Log to history (outside transaction)
                    history_entry = {
                        "tripped_at": now,
                        "reason": reason,
                        "details": details,
                        "reset_at": None,
                        "reset_by": None,
                    }
                    self._append_to_history(history_entry)

                    logger.warning(
                        f"Circuit breaker TRIPPED: reason={reason}, details={details}, "
                        f"trip_count_today={state_data['trip_count_today']}"
                    )

                    break  # Success

                except redis.exceptions.WatchError:
                    # The key was modified by another client. Retry the transaction.
                    logger.warning("WatchError on circuit breaker state, retrying trip transaction")
                    continue

    def reset(self, reset_by: str = "system") -> None:
        """
        Reset circuit breaker from TRIPPED to QUIET_PERIOD atomically.

        Requires manual intervention. Starts 5-minute quiet period before
        returning to OPEN state. Uses WATCH/MULTI/EXEC transaction to prevent
        race conditions.

        Args:
            reset_by: Identifier of who/what reset the breaker (e.g., "operator", "system")

        Raises:
            CircuitBreakerError: If not currently TRIPPED

        Example:
            >>> breaker.trip("DAILY_LOSS_EXCEEDED")
            >>> # ... conditions cleared ...
            >>> breaker.reset(reset_by="operator")
            >>> breaker.get_state()
            <CircuitBreakerState.QUIET_PERIOD: 'QUIET_PERIOD'>

        Notes:
            - Only valid when state is TRIPPED
            - Automatically transitions to OPEN after 5 minutes
            - Updates trip history with reset timestamp
            - Retries automatically on concurrent modification (WatchError)
        """
        # Use public RedisClient pipeline() method (Gemini HIGH fix)
        with self.redis.pipeline() as pipe:
            while True:
                try:
                    # Watch the state key for changes from other clients
                    pipe.watch(self.state_key)

                    state_json = pipe.get(self.state_key)
                    if not state_json:
                        # FAIL CLOSED: State missing during reset - do not auto-initialize to OPEN
                        # Codex CRITICAL fix: reset() must fail-closed, not silently reopen trading
                        pipe.unwatch()
                        logger.error(
                            "CRITICAL: Circuit breaker state missing during reset. "
                            "Failing closed - operator must reinitialize if appropriate."
                        )
                        raise RuntimeError(
                            "Circuit breaker state missing from Redis during reset. "
                            "System must fail closed - operator must verify safety and "
                            "manually reinitialize if appropriate."
                        )

                    state_data: dict[str, Any] = json.loads(cast(str, state_json))

                    # Validate current state
                    current_state = CircuitBreakerState(state_data["state"])
                    if current_state != CircuitBreakerState.TRIPPED:
                        pipe.unwatch()
                        raise CircuitBreakerError(
                            f"Cannot reset circuit breaker: current state is {current_state.value}, "
                            f"must be TRIPPED"
                        )

                    # Start atomic transaction
                    pipe.multi()

                    # Transition to QUIET_PERIOD
                    now = datetime.now(UTC).isoformat()
                    state_data.update(
                        {
                            "state": CircuitBreakerState.QUIET_PERIOD.value,
                            "reset_at": now,
                            "reset_by": reset_by,
                        }
                    )

                    pipe.set(self.state_key, json.dumps(state_data))

                    # Execute transaction atomically
                    pipe.execute()

                    logger.info(
                        f"Circuit breaker reset to QUIET_PERIOD: reset_by={reset_by}, "
                        f"duration={self.QUIET_PERIOD_DURATION}s"
                    )

                    break  # Success

                except redis.exceptions.WatchError:
                    # The key was modified by another client. Retry the transaction.
                    logger.warning(
                        "WatchError on circuit breaker state, retrying reset transaction"
                    )
                    continue

    def _transition_to_open(self) -> None:
        """
        Internal method to transition from QUIET_PERIOD to OPEN atomically.

        Called automatically when quiet period expires. Uses WATCH/MULTI/EXEC
        transaction to prevent race conditions.

        Raises:
            RuntimeError: If circuit breaker state is missing from Redis (fail-closed).
        """
        # Use public RedisClient pipeline() method (Gemini HIGH fix)
        with self.redis.pipeline() as pipe:
            while True:
                try:
                    # Watch the state key for changes from other clients
                    pipe.watch(self.state_key)

                    state_json = pipe.get(self.state_key)
                    if not state_json:
                        # FAIL CLOSED: State deleted during transition - do not auto-initialize
                        pipe.unwatch()
                        logger.error(
                            "CRITICAL: Circuit breaker state missing during transition. "
                            "Failing closed - operator must reinitialize."
                        )
                        raise RuntimeError(
                            "Circuit breaker state missing from Redis during transition. "
                            "System must fail closed - operator must verify safety and "
                            "manually reinitialize if appropriate."
                        )

                    state_data: dict[str, Any] = json.loads(cast(str, state_json))

                    # Start atomic transaction
                    pipe.multi()

                    state_data.update(
                        {
                            "state": CircuitBreakerState.OPEN.value,
                            "tripped_at": None,
                            "trip_reason": None,
                            "trip_details": None,
                        }
                    )

                    pipe.set(self.state_key, json.dumps(state_data))

                    # Execute transaction atomically
                    pipe.execute()

                    logger.info("Circuit breaker transitioned to OPEN")

                    break  # Success

                except redis.exceptions.WatchError:
                    # The key was modified by another client. Retry the transaction.
                    logger.warning(
                        "WatchError on circuit breaker state, retrying transition to OPEN"
                    )
                    continue

    def get_trip_reason(self) -> str | None:
        """
        Get reason for current trip (if TRIPPED).

        Returns:
            Trip reason string, or None if not TRIPPED

        Example:
            >>> breaker.trip("DAILY_LOSS_EXCEEDED")
            >>> breaker.get_trip_reason()
            'DAILY_LOSS_EXCEEDED'
        """
        state_json = self.redis.get(self.state_key)
        if not state_json:
            return None

        state_data = json.loads(state_json)
        return state_data.get("trip_reason")  # type: ignore[no-any-return]

    def get_trip_details(self) -> dict[str, Any] | None:
        """
        Get details for current trip (if TRIPPED).

        Returns:
            Trip details dict, or None if not TRIPPED or no details

        Example:
            >>> breaker.trip("DAILY_LOSS_EXCEEDED", details={"daily_loss": -5234.56})
            >>> breaker.get_trip_details()
            {'daily_loss': -5234.56}
        """
        state_json = self.redis.get(self.state_key)
        if not state_json:
            return None

        state_data = json.loads(state_json)
        return state_data.get("trip_details")  # type: ignore[no-any-return]

    def _append_to_history(self, entry: dict[str, Any]) -> None:
        """
        Append trip event to history log using Redis Sorted Set.

        Args:
            entry: History entry dict with trip/reset details

        Notes:
            - Uses Redis Sorted Set (ZADD) with score = timestamp
            - Automatically trims to last `max_history_entries` (default 1000)
            - Prevents unbounded growth while maintaining recent history
            - Score allows chronological ordering and range queries
        """
        # Use current timestamp (microseconds since epoch) as score for chronological ordering
        timestamp = datetime.now(UTC).timestamp()

        # Serialize entry to JSON
        history_json = json.dumps(entry)

        # Add to sorted set with timestamp as score
        # Use public RedisClient zadd method (Gemini HIGH fix)
        self.redis.zadd(self.history_key, {history_json: timestamp})

        # Trim to keep only last max_history_entries (oldest entries removed first)
        # Only trim if we exceed the limit
        current_count = self.redis.zcard(self.history_key)
        if current_count > self.max_history_entries:
            # Remove oldest entries (lowest scores/ranks)
            # Keep ranks from (current_count - max_history_entries) onwards
            # Example: 10 entries, keep last 5 → remove ranks 0-4, keep 5-9
            num_to_remove = current_count - self.max_history_entries
            self.redis.zremrangebyrank(self.history_key, 0, num_to_remove - 1)

    def get_status(self) -> dict[str, Any]:
        """
        Get comprehensive circuit breaker status.

        Returns:
            Dict with state, trip reason, timestamps, etc.

        Raises:
            RuntimeError: If circuit breaker state is missing from Redis (fail-closed).

        Example:
            >>> status = breaker.get_status()
            >>> status
            {
                'state': 'TRIPPED',
                'tripped_at': '2025-10-19T15:30:00+00:00',
                'trip_reason': 'DAILY_LOSS_EXCEEDED',
                'trip_details': {'daily_loss': -5234.56},
                'trip_count_today': 1,
                'reset_at': None,
                'reset_by': None
            }
        """
        state_json = self.redis.get(self.state_key)
        if not state_json:
            # FAIL CLOSED: Do not auto-reinitialize (matches get_state behavior)
            logger.error(
                "CRITICAL: Circuit breaker state missing from Redis. "
                "Failing closed - operator must reinitialize."
            )
            raise RuntimeError(
                "Circuit breaker state missing from Redis. System must fail closed - "
                "operator must verify safety and manually reinitialize if appropriate."
            )

        return json.loads(state_json)  # type: ignore[no-any-return]
