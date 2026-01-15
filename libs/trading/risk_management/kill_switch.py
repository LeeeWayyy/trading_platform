"""
Global kill-switch for emergency trading halt.

The kill-switch is an operator-controlled mechanism to immediately stop ALL
trading activity across all services. Unlike the circuit breaker (which trips
automatically on risk conditions), the kill-switch is manual-only.

States:
    ACTIVE: Normal trading allowed
    ENGAGED: ALL trading blocked (no new signals, no order submissions)

Storage:
    State persisted in Redis for fast access and cross-service consistency.

Example:
    >>> from libs.core.redis_client import RedisClient
    >>> from libs.trading.risk_management.kill_switch import KillSwitch
    >>>
    >>> redis = RedisClient(host="localhost", port=6379)
    >>> kill_switch = KillSwitch(redis_client=redis)
    >>>
    >>> # Check if trading allowed
    >>> if kill_switch.is_engaged():
    ...     raise KillSwitchEngaged("All trading halted by operator")
    >>>
    >>> # Engage kill-switch (operator action)
    >>> kill_switch.engage(reason="Market anomaly detected", operator="ops_team")
    >>>
    >>> # Disengage (after situation resolved)
    >>> kill_switch.disengage(operator="ops_team")

See Also:
    - docs/CONCEPTS/risk-management.md#kill-switch
    - docs/RUNBOOKS/ops.md#emergency-procedures
"""

import json
import logging
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from libs.core.redis_client import RedisClient

logger = logging.getLogger(__name__)


class KillSwitchState(str, Enum):
    """
    Kill-switch states.

    ACTIVE: Normal trading allowed
    ENGAGED: All trading blocked (operator-controlled emergency halt)
    """

    ACTIVE = "ACTIVE"
    ENGAGED = "ENGAGED"


class KillSwitchEngaged(Exception):
    """Raised when operation blocked by engaged kill-switch."""

    pass


class KillSwitch:
    """
    Global kill-switch for emergency trading halt.

    Provides operator-controlled emergency stop for all trading activities.
    Unlike circuit breaker (automatic), kill-switch is manual-only.

    Attributes:
        redis: Redis client for state storage
        state_key: Redis key for kill-switch state
        history_key: Redis key for engagement history (append-only log)

    Example:
        >>> kill_switch = KillSwitch(redis_client=redis)
        >>> kill_switch.get_state()
        <KillSwitchState.ACTIVE: 'ACTIVE'>
        >>> kill_switch.engage(reason="Emergency", operator="ops")
        >>> kill_switch.is_engaged()
        True
        >>> kill_switch.disengage(operator="ops")
        >>> kill_switch.get_state()
        <KillSwitchState.ACTIVE: 'ACTIVE'>

    Notes:
        - State transitions are atomic (Redis operations)
        - All operations logged to history
        - Thread-safe via Redis atomic operations
        - Requires operator identification for audit trail
    """

    def __init__(self, redis_client: RedisClient):
        """
        Initialize kill-switch.

        Args:
            redis_client: Redis client for state persistence

        Example:
            >>> from libs.core.redis_client import RedisClient
            >>> redis = RedisClient(host="localhost", port=6379)
            >>> kill_switch = KillSwitch(redis_client=redis)
        """
        self.redis = redis_client
        self.state_key = "kill_switch:state"
        self.history_key = "kill_switch:history"
        self.max_history_entries = 1000  # Keep last 1000 events

        # Initialize state if not exists
        state_json = self.redis.get(self.state_key)
        if not state_json:
            self._initialize_state()

    def _initialize_state(self) -> None:
        """
        Initialize kill-switch state in Redis.

        Creates default ACTIVE state.
        """
        default_state = {
            "state": KillSwitchState.ACTIVE.value,
            "engaged_at": None,
            "engaged_by": None,
            "engagement_reason": None,
            "disengaged_at": None,
            "disengaged_by": None,
            "engagement_count_today": 0,
        }
        self.redis.set(self.state_key, json.dumps(default_state))
        logger.info("Kill-switch initialized: state=ACTIVE")

    def get_state(self) -> KillSwitchState:
        """
        Get current kill-switch state.

        Returns:
            Current state (ACTIVE or ENGAGED)

        Raises:
            RuntimeError: If kill-switch state is missing from Redis (fail-closed)

        Example:
            >>> kill_switch.get_state()
            <KillSwitchState.ACTIVE: 'ACTIVE'>

        Notes:
            - If Redis state is missing after initialization, this is a critical
              error (e.g., Redis flush while ENGAGED) and we must fail closed
              rather than silently resuming trading
        """
        state_json = self.redis.get(self.state_key)
        if not state_json:
            # State should exist after __init__ - if missing, Redis was flushed/restarted
            # FAIL CLOSED: Do not auto-reinitialize to ACTIVE (could resume trading unsafely)
            logger.error(
                "CRITICAL: Kill-switch state missing from Redis (possible flush/restart). "
                "Failing closed to prevent unsafe trading resumption."
            )
            raise RuntimeError(
                "Kill-switch state missing from Redis. System must fail closed - "
                "operator must verify safety and manually disengage if appropriate."
            )

        state_data = json.loads(state_json)
        return KillSwitchState(state_data["state"])

    def is_engaged(self) -> bool:
        """
        Check if kill-switch is currently ENGAGED.

        Returns:
            True if ENGAGED, False if ACTIVE

        Example:
            >>> if kill_switch.is_engaged():
            ...     raise KillSwitchEngaged("Trading halted")
        """
        return self.get_state() == KillSwitchState.ENGAGED

    def engage(
        self,
        reason: str,
        operator: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """
        Engage kill-switch (halt all trading).

        This is an operator-controlled action that blocks ALL trading activities
        across all services until manually disengaged.

        Args:
            reason: Human-readable reason for engagement (required)
            operator: Operator ID/name who engaged kill-switch (required for audit)
            details: Optional additional context (dict)

        Raises:
            ValueError: If kill-switch already engaged

        Example:
            >>> kill_switch.engage(
            ...     reason="Market anomaly detected",
            ...     operator="ops_team",
            ...     details={"anomaly_type": "flash_crash"}
            ... )

        Notes:
            - All trading blocked immediately
            - Logs to history for audit trail
            - Requires operator identification
        """
        # Atomic state transition using Lua script to prevent race conditions
        engaged_at = datetime.now(UTC)
        engagement_details_json = json.dumps(details) if details else "null"

        lua_script = """
        local state_key = KEYS[1]
        local engaged_value = ARGV[1]
        local active_value = ARGV[2]
        local engaged_at = ARGV[3]
        local operator = ARGV[4]
        local reason = ARGV[5]
        local details = ARGV[6]

        -- Get current state
        local state_json = redis.call('GET', state_key)
        if not state_json then
            return redis.error_reply('Kill-switch state missing')
        end

        local state_data = cjson.decode(state_json)

        -- Check if already engaged
        if state_data.state == engaged_value then
            return redis.error_reply('Kill-switch already engaged')
        end

        -- Update state atomically
        state_data.state = engaged_value
        state_data.engaged_at = engaged_at
        state_data.engaged_by = operator
        state_data.engagement_reason = reason
        state_data.engagement_details = cjson.decode(details)
        state_data.disengaged_at = cjson.null
        state_data.disengaged_by = cjson.null
        state_data.engagement_count_today = (state_data.engagement_count_today or 0) + 1

        redis.call('SET', state_key, cjson.encode(state_data))
        return 1
        """

        try:
            self.redis.eval(
                lua_script,
                1,  # numkeys
                self.state_key,  # KEYS[1]
                KillSwitchState.ENGAGED.value,  # ARGV[1]
                KillSwitchState.ACTIVE.value,  # ARGV[2]
                engaged_at.isoformat(),  # ARGV[3]
                operator,  # ARGV[4]
                reason,  # ARGV[5]
                engagement_details_json,  # ARGV[6]
            )
        except Exception as e:
            error_msg = str(e)
            if "Kill-switch already engaged" in error_msg:
                logger.warning(
                    f"Kill-switch already engaged (operator={operator}, reason={reason})"
                )
                raise ValueError("Kill-switch already engaged") from e
            elif "Kill-switch state missing" in error_msg:
                raise RuntimeError("Kill-switch state missing from Redis") from e
            else:
                logger.error(f"Failed to engage kill-switch: {e}")
                raise

        # Log to history
        history_entry = {
            "event": "ENGAGED",
            "timestamp": engaged_at.isoformat(),
            "operator": operator,
            "reason": reason,
            "details": details,
        }
        self.redis.rpush(self.history_key, json.dumps(history_entry))

        # Trim history to max entries
        self.redis.ltrim(self.history_key, -self.max_history_entries, -1)

        logger.critical(
            f"🔴 KILL-SWITCH ENGAGED: reason={reason}, operator={operator}",
            extra={
                "kill_switch_state": "ENGAGED",
                "operator": operator,
                "reason": reason,
                "details": details,
            },
        )

    def disengage(
        self,
        operator: str,
        notes: str | None = None,
    ) -> None:
        """
        Disengage kill-switch (resume trading).

        This operator action re-enables trading after kill-switch was engaged.

        Args:
            operator: Operator ID/name who disengaged kill-switch (required for audit)
            notes: Optional notes about resolution

        Raises:
            ValueError: If kill-switch not currently engaged

        Example:
            >>> kill_switch.disengage(
            ...     operator="ops_team",
            ...     notes="Market conditions normalized"
            ... )

        Notes:
            - Trading resumes immediately
            - Logs to history for audit trail
            - Requires operator identification
        """
        # Atomic state transition using Lua script to prevent race conditions
        disengaged_at = datetime.now(UTC)
        notes_json = json.dumps(notes) if notes else "null"

        lua_script = """
        local state_key = KEYS[1]
        local active_value = ARGV[1]
        local engaged_value = ARGV[2]
        local disengaged_at = ARGV[3]
        local operator = ARGV[4]
        local notes = ARGV[5]

        -- Get current state
        local state_json = redis.call('GET', state_key)
        if not state_json then
            return redis.error_reply('Kill-switch state missing')
        end

        local state_data = cjson.decode(state_json)

        -- Check if not engaged
        if state_data.state == active_value then
            return redis.error_reply('Kill-switch not engaged')
        end

        -- Update state atomically
        state_data.state = active_value
        state_data.disengaged_at = disengaged_at
        state_data.disengaged_by = operator
        state_data.disengagement_notes = cjson.decode(notes)

        redis.call('SET', state_key, cjson.encode(state_data))
        return 1
        """

        try:
            self.redis.eval(
                lua_script,
                1,  # numkeys
                self.state_key,  # KEYS[1]
                KillSwitchState.ACTIVE.value,  # ARGV[1]
                KillSwitchState.ENGAGED.value,  # ARGV[2]
                disengaged_at.isoformat(),  # ARGV[3]
                operator,  # ARGV[4]
                notes_json,  # ARGV[5]
            )
        except Exception as e:
            error_msg = str(e)
            if "Kill-switch not engaged" in error_msg:
                logger.warning(f"Kill-switch already active (operator={operator})")
                raise ValueError("Kill-switch not engaged") from e
            elif "Kill-switch state missing" in error_msg:
                raise RuntimeError("Kill-switch state missing from Redis") from e
            else:
                logger.error(f"Failed to disengage kill-switch: {e}")
                raise

        # Log to history
        history_entry = {
            "event": "DISENGAGED",
            "timestamp": disengaged_at.isoformat(),
            "operator": operator,
            "notes": notes,
        }
        self.redis.rpush(self.history_key, json.dumps(history_entry))

        # Trim history to max entries
        self.redis.ltrim(self.history_key, -self.max_history_entries, -1)

        logger.info(
            f"✅ Kill-switch disengaged: operator={operator}",
            extra={
                "kill_switch_state": "ACTIVE",
                "operator": operator,
                "notes": notes,
            },
        )

    def get_status(self) -> dict[str, Any]:
        """
        Get detailed kill-switch status.

        Returns:
            Dictionary with current state, last engagement/disengagement details

        Raises:
            RuntimeError: If kill-switch state is missing from Redis (fail-closed)

        Example:
            >>> status = kill_switch.get_status()
            >>> print(status["state"])
            'ACTIVE'
            >>> print(status["last_engaged_by"])
            'ops_team'

        Notes:
            - Status checks NEVER mutate state (fail-closed principle)
            - If state is missing (Redis flush/crash), raises RuntimeError
            - Operator must manually verify safety and reinitialize if appropriate
        """
        state_json = self.redis.get(self.state_key)
        if not state_json:
            # FAIL CLOSED: Do not auto-reinitialize to ACTIVE
            # Missing state could mean Redis flush while kill-switch was ENGAGED
            # Status queries must NOT mutate state or resume trading
            logger.error(
                "CRITICAL: Kill-switch state missing from Redis (possible flush/restart). "
                "Failing closed to prevent unsafe trading resumption."
            )
            raise RuntimeError(
                "Kill-switch state missing from Redis. System must fail closed - "
                "operator must verify safety and manually reinitialize if appropriate."
            )

        state_data: dict[str, Any] = json.loads(state_json)
        return state_data

    def get_history(self, limit: int = 100) -> list[dict[str, Any]]:
        """
        Get kill-switch engagement/disengagement history.

        Args:
            limit: Maximum number of history entries to return (default 100)

        Returns:
            List of history entries (most recent first)

        Example:
            >>> history = kill_switch.get_history(limit=10)
            >>> for entry in history:
            ...     print(f"{entry['timestamp']}: {entry['event']} by {entry['operator']}")
        """
        history_raw = self.redis.lrange(self.history_key, -limit, -1)
        history = [json.loads(entry) for entry in history_raw]
        return list(reversed(history))  # Most recent first
