"""
Atomic position reservation using Redis Lua scripts.

This module prevents race conditions in position limit checking by atomically
reserving positions before order submission. Without this, concurrent orders
could both pass the position limit check before either executes.

Race Condition Example (WITHOUT reservation):
    1. Thread A: current_position=0, wants to buy 1000, limit=1000 → PASS
    2. Thread B: current_position=0, wants to buy 1000, limit=1000 → PASS
    3. Both orders execute → position = 2000 (exceeds 1000 limit!)

With Atomic Reservation:
    1. Thread A: atomically reserves +1000 (reserved=1000) → SUCCESS
    2. Thread B: atomically tries +1000 (1000+1000=2000 > 1000) → BLOCKED
    3. Only Thread A's order executes → position = 1000 (within limit)

See Also:
    - docs/TASKS/P3T5_TASK.md#T5.2 for requirements
    - libs/risk_management/checker.py for integration
"""

import logging
import secrets
from dataclasses import dataclass
from typing import cast

from libs.redis_client.client import RedisClient

logger = logging.getLogger(__name__)

# Key prefix for position reservations
RESERVATION_KEY_PREFIX = "position_reservation"

# Default TTL for reservations (60 seconds matches order timeout)
DEFAULT_RESERVATION_TTL = 60


# Lua script for atomic position reservation
# KEYS[1]: position_reservation:{symbol}
# ARGV[1]: delta (position change: positive for buy, negative for sell)
# ARGV[2]: max_position_limit (absolute max position allowed)
# ARGV[3]: reservation_token (unique ID for rollback)
# ARGV[4]: TTL in seconds (for token key only)
# ARGV[5]: current_position fallback (used when key missing, e.g., after restart)
#
# Returns: [1, token] on success, [0, "LIMIT_EXCEEDED"] on failure
#
# CRITICAL: Aggregate position key NEVER expires (only token keys expire).
# This prevents position limits from resetting to 0 after TTL.
RESERVE_POSITION_LUA = """
-- Get current reserved position, using fallback if key missing
-- Gemini/Codex CRITICAL fix: Use current_position fallback instead of defaulting to 0
local fallback_position = tonumber(ARGV[5])
local stored = redis.call("GET", KEYS[1])
local current = stored and tonumber(stored) or fallback_position

local delta = tonumber(ARGV[1])
local max_limit = tonumber(ARGV[2])
local token = ARGV[3]
local ttl = tonumber(ARGV[4])

-- Calculate new position after this reservation
local new_position = current + delta

-- Check if new position exceeds limit (check absolute value)
if math.abs(new_position) > max_limit then
    return {0, "LIMIT_EXCEEDED", current, new_position}
end

-- Atomically reserve the position (NO TTL on aggregate key!)
-- Gemini/Codex CRITICAL fix: Never expire aggregate key to prevent reset to 0
redis.call("SET", KEYS[1], new_position)

-- Store reservation token for rollback (token keys DO expire)
local token_key = KEYS[1] .. ":token:" .. token
redis.call("SET", token_key, delta)
redis.call("EXPIRE", token_key, ttl)

return {1, token, current, new_position}
"""

# Lua script for releasing a reservation (rollback)
# KEYS[1]: position_reservation:{symbol}
# ARGV[1]: reservation_token
#
# Returns: [1, "RELEASED"] on success, [0, "TOKEN_NOT_FOUND"] if already released
RELEASE_RESERVATION_LUA = """
local token_key = KEYS[1] .. ":token:" .. ARGV[1]
local delta = redis.call("GET", token_key)

if not delta then
    return {0, "TOKEN_NOT_FOUND"}
end

-- Get current position and subtract the delta
local current = tonumber(redis.call("GET", KEYS[1]) or "0")
local new_position = current - tonumber(delta)

-- Update position
redis.call("SET", KEYS[1], new_position)

-- Delete the token to prevent double-release
redis.call("DEL", token_key)

return {1, "RELEASED", current, new_position}
"""

# Lua script for confirming a reservation (after successful order)
# KEYS[1]: position_reservation:{symbol}
# ARGV[1]: reservation_token
#
# Returns: [1, "CONFIRMED"] on success, [0, "TOKEN_NOT_FOUND"] if already confirmed
CONFIRM_RESERVATION_LUA = """
local token_key = KEYS[1] .. ":token:" .. ARGV[1]
local delta = redis.call("GET", token_key)

if not delta then
    return {0, "TOKEN_NOT_FOUND"}
end

-- Delete the token (position stays reserved)
redis.call("DEL", token_key)

return {1, "CONFIRMED"}
"""


@dataclass
class ReservationResult:
    """Result of a position reservation attempt."""

    success: bool
    token: str | None
    reason: str
    previous_position: int
    new_position: int


@dataclass
class ReleaseResult:
    """Result of a reservation release/rollback."""

    success: bool
    reason: str
    previous_position: int | None = None
    new_position: int | None = None


class PositionReservation:
    """
    Atomic position reservation manager using Redis Lua scripts.

    Prevents race conditions by atomically checking and reserving position
    changes before order submission.

    Attributes:
        redis: Redis client for atomic operations
        ttl: Time-to-live for reservations in seconds

    Example:
        >>> reservation = PositionReservation(redis_client)
        >>>
        >>> # Reserve before submitting order
        >>> result = reservation.reserve("AAPL", side="buy", qty=100, max_limit=1000)
        >>> if not result.success:
        ...     raise RiskViolation(f"Position limit: {result.reason}")
        >>>
        >>> try:
        ...     submit_order_to_broker(...)
        ...     reservation.confirm("AAPL", result.token)
        ... except BrokerError:
        ...     reservation.release("AAPL", result.token)  # Rollback on failure

    Notes:
        - All operations are atomic (no race conditions)
        - TTL prevents orphaned reservations (default: 60s)
        - Tokens ensure only the reserver can release
        - Thread-safe (Redis Lua scripts are atomic)
    """

    def __init__(
        self,
        redis: RedisClient,
        ttl: int = DEFAULT_RESERVATION_TTL,
    ):
        """
        Initialize position reservation manager.

        Args:
            redis: Redis client instance
            ttl: Time-to-live for reservations in seconds (default: 60)
        """
        self.redis = redis
        self.ttl = ttl

    def _get_key(self, symbol: str) -> str:
        """Get Redis key for symbol's position reservation."""
        return f"{RESERVATION_KEY_PREFIX}:{symbol}"

    def reserve(
        self,
        symbol: str,
        side: str,
        qty: int,
        max_limit: int,
        current_position: int = 0,
    ) -> ReservationResult:
        """
        Atomically reserve a position change.

        Checks if adding this order would exceed the position limit and,
        if not, atomically reserves the position.

        Args:
            symbol: Stock symbol (e.g., "AAPL")
            side: Order side ("buy" or "sell")
            qty: Order quantity (positive integer)
            max_limit: Maximum allowed position (absolute value)
            current_position: Current actual position (for initial sync)

        Returns:
            ReservationResult with success status and token (if successful)

        Example:
            >>> result = reservation.reserve("AAPL", "buy", 100, max_limit=1000)
            >>> if result.success:
            ...     # Safe to submit order
            ...     order_id = submit_order(...)
            ... else:
            ...     # Position limit would be exceeded
            ...     logger.warning(f"Blocked: {result.reason}")

        Notes:
            - Token must be saved for later release/confirm
            - On broker rejection: call release() with token
            - On broker success: call confirm() with token
            - Reservation auto-expires after TTL
        """
        key = self._get_key(symbol)
        token = secrets.token_hex(8)  # 16-char unique token

        # Calculate delta based on side
        delta = qty if side == "buy" else -qty

        # Execute atomic Lua script
        # Gemini/Codex CRITICAL fix: Pass current_position as fallback (ARGV[5])
        result = self.redis.eval(
            RESERVE_POSITION_LUA,
            1,  # Number of keys
            key,
            str(delta),
            str(max_limit),
            token,
            str(self.ttl),
            str(current_position),  # Fallback when Redis key missing
        )

        # Parse result: [success, token_or_reason, prev_position, new_position]
        result_list = cast(list[int | str], result)
        success = result_list[0] == 1
        _reason = str(result_list[1])  # noqa: F841 - may be used in future logging
        prev_pos = int(result_list[2])
        new_pos = int(result_list[3])

        if success:
            logger.debug(
                f"Position reserved: {symbol} {side} {qty}, "
                f"reserved_position: {prev_pos} -> {new_pos}, token={token}"
            )
            return ReservationResult(
                success=True,
                token=token,
                reason="",
                previous_position=prev_pos,
                new_position=new_pos,
            )
        else:
            logger.info(
                f"Position reservation blocked: {symbol} {side} {qty}, "
                f"would exceed limit ({prev_pos} + {delta} = {new_pos} > {max_limit})"
            )
            return ReservationResult(
                success=False,
                token=None,
                reason=f"Position limit exceeded: {abs(new_pos)} > {max_limit}",
                previous_position=prev_pos,
                new_position=new_pos,
            )

    def release(self, symbol: str, token: str) -> ReleaseResult:
        """
        Release a reservation (rollback on order failure).

        Call this when an order fails after reservation to return
        the reserved position back to the pool.

        Args:
            symbol: Stock symbol
            token: Reservation token from reserve()

        Returns:
            ReleaseResult with success status

        Example:
            >>> try:
            ...     submit_order_to_broker(...)
            ... except BrokerError:
            ...     # Order failed, release the reservation
            ...     release_result = reservation.release("AAPL", token)
            ...     if release_result.success:
            ...         logger.info("Reservation released successfully")

        Notes:
            - Safe to call multiple times (idempotent after first)
            - Returns success=False if token not found (already released)
        """
        key = self._get_key(symbol)

        result = self.redis.eval(
            RELEASE_RESERVATION_LUA,
            1,
            key,
            token,
        )

        result_list = cast(list[int | str], result)
        success = result_list[0] == 1
        reason = str(result_list[1])

        if success:
            prev_pos = int(result_list[2])
            new_pos = int(result_list[3])
            logger.debug(
                f"Reservation released: {symbol} token={token}, "
                f"reserved_position: {prev_pos} -> {new_pos}"
            )
            return ReleaseResult(
                success=True,
                reason="RELEASED",
                previous_position=prev_pos,
                new_position=new_pos,
            )
        else:
            logger.warning(
                f"Reservation release failed: {symbol} token={token}, "
                f"reason={reason} (may already be released)"
            )
            return ReleaseResult(success=False, reason=reason)

    def confirm(self, symbol: str, token: str) -> ReleaseResult:
        """
        Confirm a reservation (after successful order).

        Call this after the broker accepts the order. The reserved position
        stays but the token is deleted (position is now "real").

        Args:
            symbol: Stock symbol
            token: Reservation token from reserve()

        Returns:
            ReleaseResult with success status

        Example:
            >>> result = broker.submit_order(...)
            >>> if result.success:
            ...     # Order accepted, confirm reservation
            ...     reservation.confirm("AAPL", token)

        Notes:
            - After confirm, release() will fail (token deleted)
            - Safe to call multiple times (idempotent)
        """
        key = self._get_key(symbol)

        result = self.redis.eval(
            CONFIRM_RESERVATION_LUA,
            1,
            key,
            token,
        )

        result_list = cast(list[int | str], result)
        success = result_list[0] == 1
        reason = str(result_list[1])

        if success:
            logger.debug(f"Reservation confirmed: {symbol} token={token}")
            return ReleaseResult(success=True, reason="CONFIRMED")
        else:
            logger.warning(
                f"Reservation confirm failed: {symbol} token={token}, "
                f"reason={reason} (may already be confirmed/released)"
            )
            return ReleaseResult(success=False, reason=reason)

    def get_reserved_position(self, symbol: str) -> int:
        """
        Get current reserved position for a symbol.

        Useful for debugging and monitoring.

        Args:
            symbol: Stock symbol

        Returns:
            Current reserved position (0 if none)

        Example:
            >>> reserved = reservation.get_reserved_position("AAPL")
            >>> print(f"Currently reserved: {reserved} shares")
        """
        key = self._get_key(symbol)
        value = self.redis.get(key)
        return int(value) if value else 0

    def sync_position(self, symbol: str, actual_position: int) -> None:
        """
        Sync reserved position with actual position.

        Call this during startup or reconciliation to reset the
        reserved position to match actual broker positions.

        Args:
            symbol: Stock symbol
            actual_position: Actual position from broker

        Example:
            >>> # During startup reconciliation
            >>> for symbol, position in broker_positions.items():
            ...     reservation.sync_position(symbol, position)
        """
        key = self._get_key(symbol)
        # Gemini/Codex CRITICAL fix: NO TTL on aggregate position key
        # TTL would cause position to reset to 0 after expiry
        self.redis.set(key, str(actual_position))
        logger.info(f"Synced reserved position: {symbol} = {actual_position}")

    def clear_all(self, symbol: str) -> None:
        """
        Clear all reservations for a symbol.

        Use during system reset or reconciliation.

        Args:
            symbol: Stock symbol
        """
        key = self._get_key(symbol)
        self.redis.delete(key)
        logger.info(f"Cleared all reservations: {symbol}")
