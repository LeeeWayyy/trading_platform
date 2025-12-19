"""Circuit breaker service with RBAC enforcement.

This module provides a service layer for circuit breaker operations in the
web console. It enforces RBAC permissions, rate limiting, and audit logging
for all circuit breaker operations.

Key Features:
    - RBAC enforcement at service level (not just UI)
    - Global rate limiting for reset operations (1 per minute)
    - Audit logging to PostgreSQL
    - Prometheus metrics for monitoring
    - History fallback to audit log when Redis unavailable

Usage:
    from apps.web_console.services.cb_service import CircuitBreakerService
    from libs.redis_client import RedisClient

    redis = RedisClient(host="localhost", port=6379)
    service = CircuitBreakerService(redis_client=redis, db_pool=db_pool)

    # Get status (any authenticated user)
    status = service.get_status()

    # Trip (requires TRIP_CIRCUIT permission)
    service.trip("MANUAL", user=user_session, acknowledged=True)

    # Reset (requires RESET_CIRCUIT permission + rate limit)
    service.reset("Conditions cleared, verified system health", user=user_session, acknowledged=True)
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from apps.web_console.config import MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH
from libs.risk_management.breaker import CircuitBreaker
from libs.web_console_auth.permissions import Permission, has_permission

from .cb_metrics import CB_RESET_TOTAL, CB_STATUS_CHECKS, CB_TRIP_TOTAL
from .cb_rate_limiter import CBRateLimiter

if TYPE_CHECKING:
    from libs.redis_client import RedisClient

logger = logging.getLogger(__name__)


class CBServiceError(Exception):
    """Base exception for CB service errors."""


class RateLimitExceeded(CBServiceError):
    """Raised when rate limit is exceeded."""


class RBACViolation(CBServiceError):
    """Raised when RBAC check fails."""


class ValidationError(CBServiceError):
    """Raised when validation fails."""


class CircuitBreakerService:
    """Service layer for circuit breaker operations with RBAC enforcement.

    This service wraps the CircuitBreaker class with additional features:
    - RBAC permission checks at the service level
    - Rate limiting for reset operations
    - Audit logging to PostgreSQL
    - Prometheus metrics

    Attributes:
        redis: RedisClient for state storage
        db_pool: Database connection pool for audit logging
        breaker: Underlying CircuitBreaker instance
        rate_limiter: Global rate limiter for reset operations
    """

    def __init__(
        self,
        redis_client: RedisClient,
        db_pool: Any = None,
    ) -> None:
        """Initialize the circuit breaker service.

        Args:
            redis_client: RedisClient for circuit breaker state
            db_pool: Database connection pool for audit logging (optional)
        """
        self.redis = redis_client
        self.db_pool = db_pool
        self.breaker = CircuitBreaker(redis_client)
        self.rate_limiter = CBRateLimiter(redis_client)

    def get_status(self) -> dict[str, Any]:
        """Get current circuit breaker status.

        Returns:
            Dict with state, trip_reason, tripped_at, etc.

        Raises:
            RuntimeError: If circuit breaker state is missing (fail-closed)
        """
        status = self.breaker.get_status()
        CB_STATUS_CHECKS.inc()
        return status

    def trip(
        self,
        reason: str,
        user: dict[str, Any],
        *,
        acknowledged: bool = False,
    ) -> bool:
        """Trip the circuit breaker with RBAC enforcement.

        Args:
            reason: Trip reason (from TripReason enum or custom string)
            user: User session dict with user_id, role, etc.
            acknowledged: Whether user acknowledged the action (required)

        Returns:
            True if trip succeeded

        Raises:
            RBACViolation: If user lacks TRIP_CIRCUIT permission
            ValidationError: If not acknowledged
        """
        # RBAC enforcement at service level
        if not has_permission(user, Permission.TRIP_CIRCUIT):
            logger.warning(
                "rbac_violation",
                extra={
                    "user_id": user.get("user_id"),
                    "role": user.get("role"),
                    "permission": "TRIP_CIRCUIT",
                },
            )
            raise RBACViolation(
                f"User {user.get('user_id')} lacks TRIP_CIRCUIT permission"
            )

        # Server-side acknowledgement validation (fail-closed)
        if not acknowledged:
            raise ValidationError("Trip must be explicitly acknowledged")

        # Perform trip
        user_id = user.get("user_id", "unknown")
        trip_details = {"tripped_by": user_id}
        self.breaker.trip(reason, details=trip_details)

        # Audit log with full context
        self._log_audit(
            action="CIRCUIT_BREAKER_TRIP",
            user=user,
            resource_type="circuit_breaker",
            resource_id="global",
            reason=reason,
            details=trip_details,
            outcome="success",
        )

        CB_TRIP_TOTAL.inc()
        logger.info(
            "circuit_breaker_tripped",
            extra={
                "reason": reason,
                "tripped_by": user_id,
            },
        )
        return True

    def reset(
        self,
        reason: str,
        user: dict[str, Any],
        *,
        acknowledged: bool = False,
    ) -> bool:
        """Reset circuit breaker with rate limit, RBAC enforcement, and audit.

        Args:
            reason: Reset reason (min 20 chars required)
            user: User session dict with user_id, role, etc.
            acknowledged: Whether user acknowledged the action (required)

        Returns:
            True if reset succeeded

        Raises:
            RBACViolation: If user lacks RESET_CIRCUIT permission
            ValidationError: If reason too short or not acknowledged
            RateLimitExceeded: If global rate limit exceeded
            CircuitBreakerError: If not currently TRIPPED
        """
        # RBAC enforcement at service level
        if not has_permission(user, Permission.RESET_CIRCUIT):
            logger.warning(
                "rbac_violation",
                extra={
                    "user_id": user.get("user_id"),
                    "role": user.get("role"),
                    "permission": "RESET_CIRCUIT",
                },
            )
            raise RBACViolation(
                f"User {user.get('user_id')} lacks RESET_CIRCUIT permission"
            )

        # Server-side validation (uses same constant as UI for consistency)
        if len(reason) < MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH:
            raise ValidationError(
                f"Reset reason must be at least {MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH} characters"
            )
        if not acknowledged:
            raise ValidationError("Reset must be explicitly acknowledged")

        # Check if breaker is in TRIPPED state before consuming rate limit token
        # This prevents wasting the rate limit on invalid reset attempts
        status = self.breaker.get_status()
        if status.get("state") != "TRIPPED":
            raise ValidationError(
                f"Cannot reset: circuit breaker is {status.get('state')}, not TRIPPED"
            )

        # Global rate limit (1 reset per minute, regardless of user)
        # Only consume token after validating reset is possible
        if not self.rate_limiter.check_global(limit=1, window=60):
            logger.warning(
                "rate_limit_exceeded",
                extra={
                    "user_id": user.get("user_id"),
                    "operation": "reset",
                },
            )
            raise RateLimitExceeded("Max 1 reset per minute (global)")

        # Perform reset - clear rate limit if it fails to avoid blocking retries
        user_id = user.get("user_id", "unknown")
        try:
            self.breaker.reset(reset_by=user_id)
        except Exception:
            # Clear rate limit on failure so operator can retry immediately
            self.rate_limiter.clear()
            raise

        # Post-reset bookkeeping: update history, audit log, metrics
        # Wrap in try/except so bookkeeping failures don't mask successful reset
        reset_at = datetime.now(UTC).isoformat()
        try:
            self.breaker.update_history_with_reset(
                reset_at, reset_by=user_id, reset_reason=reason
            )
        except Exception as e:
            logger.warning(
                "reset_history_update_failed",
                extra={"error": str(e), "reset_by": user_id},
            )

        try:
            reset_details = {"reset_by": user_id}
            self._log_audit(
                action="CIRCUIT_BREAKER_RESET",
                user=user,
                resource_type="circuit_breaker",
                resource_id="global",
                reason=reason,
                details=reset_details,
                outcome="success",
            )
        except Exception as e:
            logger.warning(
                "reset_audit_log_failed",
                extra={"error": str(e), "user_id": user.get("user_id")},
            )

        CB_RESET_TOTAL.inc()
        logger.info(
            "circuit_breaker_reset",
            extra={
                "reason": reason,
                "reset_by": user_id,
            },
        )
        return True

    def get_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get trip/reset history.

        Falls back to audit log if Redis unavailable.

        Args:
            limit: Maximum number of entries to return

        Returns:
            List of history entries with trip/reset details
        """
        try:
            return self.breaker.get_history(limit=limit)
        except Exception as e:
            logger.warning(
                "redis_history_unavailable",
                extra={"error": str(e)},
            )
            return self._get_history_from_audit(limit=limit)

    def _get_history_from_audit(self, limit: int) -> list[dict[str, Any]]:
        """Fallback: get history from PostgreSQL audit_log.

        Reads CIRCUIT_BREAKER_TRIP and CIRCUIT_BREAKER_RESET events and
        pairs them to match the Redis history shape (single entry per trip cycle).

        Pairing Strategy:
            1. Query most recent events (DESC) to get relevant history
            2. Reverse to process oldest-to-newest for chronological pairing
            3. Track pending_trip: when we see a TRIP, store it
            4. When we see a RESET, pair it with pending_trip and complete the entry
            5. Multiple TRIPs before a RESET: each TRIP saves previous pending_trip
            6. Unpaired TRIP at end: currently active trip (not yet reset)
            7. Return newest-first to match Redis ZREVRANGE order

        Args:
            limit: Maximum number of entries to return

        Returns:
            List of history entries with shape:
            {tripped_at, reason, details, reset_at?, reset_by?, reset_reason?}
        """
        if not self.db_pool:
            logger.warning("audit_fallback_no_db_pool")
            return []

        try:
            # Use sync connection from pool
            with self.db_pool.connection() as conn:
                with conn.cursor() as cur:
                    # Fetch most recent events (2x limit for TRIP+RESET pairs)
                    # Use DESC to get newest first, then reverse for chronological pairing
                    cur.execute(
                        """
                        SELECT timestamp, action, details, user_id
                        FROM audit_log
                        WHERE action IN ('CIRCUIT_BREAKER_TRIP', 'CIRCUIT_BREAKER_RESET')
                        ORDER BY timestamp DESC
                        LIMIT %s
                        """,
                        (limit * 2,),
                    )
                    rows = list(cur.fetchall())

            # Reverse to process from oldest to newest for correct pairing
            rows.reverse()

            # Pair TRIP and RESET events to match Redis history shape
            # Redis stores single entries that get updated with reset info
            history: list[dict[str, Any]] = []
            pending_trip: dict[str, Any] | None = None

            for row in rows:
                timestamp, action, details, user_id = row
                details_dict = (
                    details if isinstance(details, dict) else json.loads(details or "{}")
                )
                ts_str = timestamp.isoformat() if timestamp else None

                if action == "CIRCUIT_BREAKER_TRIP":
                    # Save any pending trip before starting new one
                    if pending_trip is not None:
                        history.append(pending_trip)
                    # Start new trip entry with details for consistency with Redis shape
                    pending_trip = {
                        "tripped_at": ts_str,
                        "reason": details_dict.get("reason"),
                        "details": {"tripped_by": str(user_id)} if user_id else {},
                    }
                elif action == "CIRCUIT_BREAKER_RESET" and pending_trip is not None:
                    # Pair reset with pending trip
                    pending_trip["reset_at"] = ts_str
                    pending_trip["reset_by"] = str(user_id) if user_id else None
                    pending_trip["reset_reason"] = details_dict.get("reason")
                    history.append(pending_trip)
                    pending_trip = None

            # Add any unpaired trip (currently tripped, not yet reset)
            if pending_trip is not None:
                history.append(pending_trip)

            # Return newest-first to match Redis history order, limited to requested count
            return list(reversed(history))[:limit]

        except Exception as e:
            logger.exception(
                "audit_history_fetch_failed",
                extra={"error": str(e)},
            )
            return []

    def _log_audit(
        self,
        action: str,
        user: dict[str, Any],
        resource_type: str,
        resource_id: str,
        reason: str,
        details: dict[str, Any] | None = None,
        outcome: str = "success",
    ) -> None:
        """Write to PostgreSQL audit_log table.

        Args:
            action: Action performed (e.g., CIRCUIT_BREAKER_TRIP)
            user: User session dict
            resource_type: Type of resource (e.g., circuit_breaker)
            resource_id: ID of resource (e.g., global)
            reason: Reason for the action
            details: Additional context (e.g., tripped_by, reset_by)
            outcome: Outcome of the action (success/failure)
        """
        # Merge reason with additional details
        audit_details: dict[str, Any] = {"reason": str(reason)}
        if details:
            audit_details.update(details)

        if not self.db_pool:
            logger.info(
                "audit_log_fallback",
                extra={
                    "action": action,
                    "user_id": user.get("user_id"),
                    "details": audit_details,
                },
            )
            return

        user_id = user.get("user_id")
        user_name = user.get("username") or user.get("name")
        ip_address = user.get("ip_address")

        try:
            # Use sync connection from pool
            with self.db_pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO audit_log (
                            timestamp, action, resource_type, resource_id,
                            user_id, user_name, details, ip_address, outcome
                        ) VALUES (
                            NOW(), %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            action,
                            resource_type,
                            resource_id,
                            user_id,
                            user_name,
                            json.dumps(audit_details),
                            ip_address,
                            outcome,
                        ),
                    )
                conn.commit()
        except Exception as e:
            # Don't fail the operation if audit logging fails
            logger.exception(
                "audit_log_write_failed",
                extra={
                    "action": action,
                    "user_id": user_id,
                    "error": str(e),
                },
            )


__all__ = [
    "CircuitBreakerService",
    "CBServiceError",
    "RateLimitExceeded",
    "RBACViolation",
    "ValidationError",
]
