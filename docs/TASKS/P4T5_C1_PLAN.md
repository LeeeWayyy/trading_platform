# P4T5 C1: Circuit Breaker Dashboard - Implementation Plan

**Component:** C1 - T7.1 Circuit Breaker Dashboard
**Parent Task:** P4T5 Web Console Operations
**Status:** ✅ COMPLETE
**Estimated Effort:** 3-4 days
**Completed:** 2025-12-18

---

## Overview

Implement T7.1 Circuit Breaker Dashboard with real-time status display, manual trip/reset controls, and audit logging.

**Note:** T6.1 (Auth/RBAC) was delivered in PR#76 (2025-12-12). The CB dashboard integrates with the T6.1 permission system and uses `@operations_requires_auth` for development convenience (which delegates to real auth in production).

## Acceptance Criteria (from P4T5_TASK.md)

- [x] Real-time circuit breaker status display (OPEN/TRIPPED) with color coding
- [x] Redis state: `circuit_breaker:state` JSON blob contains state, trip_reason, tripped_at fields
- [x] Trip/reset history table with timestamps and reasons
- [x] Manual trip/reset controls with RBAC enforcement (operator/admin roles only)
- [x] Step-up confirmation for reset operations (min 20 chars reason + checkbox acknowledgment)
- [x] Rate limiting (max 1 reset per minute globally) to prevent accidental spam
- [x] Persistent audit log for all manual interventions
- [x] Auto-refresh via polling (≤5s staleness)
- [x] Prometheus metrics: `cb_status_checks_total`, `cb_trip_total`, `cb_reset_total`

**All acceptance criteria met.**

## Redis Key Clarification

The existing `CircuitBreaker` class in `libs/risk_management/breaker.py` stores a JSON blob at `circuit_breaker:state` containing all fields (state, tripped_at, trip_reason, trip_details). This is the canonical approach. The task's reference to "canonical Redis keys" refers to the logical fields within this JSON blob, not separate scalar keys. This avoids atomic consistency issues across multiple keys.

## Architecture

### Components

```
┌─────────────────────────────────────────────────────────────────┐
│                   Streamlit Page: circuit_breaker.py            │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │  Status Display │  │  Trip Control   │  │ History Table   │ │
│  │  (color-coded)  │  │  (with RBAC)    │  │ (paginated)     │ │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘ │
└───────────┼─────────────────────┼─────────────────────┼─────────┘
            │                     │                     │
            ▼                     ▼                     ▼
┌─────────────────────────────────────────────────────────────────┐
│              CircuitBreakerService (services/cb_service.py)     │
│  - get_status() → dict                                          │
│  - trip(reason, user, acknowledged) → bool  [RBAC enforced]     │
│  - reset(reason, user, acknowledged) → bool [RBAC enforced]     │
│  - get_history(limit) → list[dict]                              │
│  - _log_audit(action, user, reason, details) → None             │
└───────────────────────────────────────────────────────────────────┘
            │                     │                     │
            ▼                     ▼                     ▼
┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
│ CircuitBreaker  │   │  Global Rate    │   │  Audit Logger   │
│ (libs/risk_mgmt)│   │   Limiter       │   │ (PostgreSQL)    │
│ + get_history() │   │  (atomic INCR)  │   │ + DB writes     │
└─────────────────┘   └─────────────────┘   └─────────────────┘
```

### File Structure

```
libs/risk_management/
└── breaker.py                    # Add get_history() method

apps/web_console/
├── pages/
│   └── circuit_breaker.py        # Main Streamlit page
├── services/
│   └── cb_service.py             # Circuit breaker service layer
│   └── cb_rate_limiter.py        # Rate limiter for CB operations
├── components/
│   └── cb_status_display.py      # Status display component
│   └── cb_history_table.py       # History table component
│   └── cb_reset_dialog.py        # Reset confirmation dialog

tests/apps/web_console/
├── services/
│   └── test_cb_service.py        # Service tests with RBAC
│   └── test_cb_rate_limiter.py   # Rate limiter tests
├── pages/
│   └── test_circuit_breaker_page.py  # Page integration tests
```

## Implementation Details

### 1. Extend CircuitBreaker Class (`libs/risk_management/breaker.py`)

Add `get_history()` method for encapsulation:

```python
def get_history(self, limit: int = 50) -> list[dict[str, Any]]:
    """Get trip/reset history from Redis sorted set.

    Args:
        limit: Maximum number of entries to return (newest first)

    Returns:
        List of history entries with trip/reset details

    Example:
        >>> history = breaker.get_history(limit=10)
        >>> history[0]
        {'tripped_at': '2025-12-18T15:30:00+00:00', 'reason': 'MANUAL', ...}
    """
    # Use ZREVRANGE for newest-first ordering
    entries_raw = self.redis.zrevrange(self.history_key, 0, limit - 1)
    # Redis returns bytes - decode before JSON parsing
    return [
        json.loads(entry.decode("utf-8") if isinstance(entry, bytes) else entry)
        for entry in entries_raw
    ]

def update_history_with_reset(self, reset_at: str, reset_by: str) -> None:
    """Update the most recent trip entry with reset information.

    Called after a successful reset to record the reset in history.
    """
    # Get the most recent entry
    entries = self.redis.zrevrange(self.history_key, 0, 0, withscores=True)
    if entries:
        entry_raw, score = entries[0]
        # Redis returns bytes - decode before JSON parsing
        entry_str = entry_raw.decode("utf-8") if isinstance(entry_raw, bytes) else entry_raw
        entry = json.loads(entry_str)
        if entry.get("reset_at") is None:
            # Update the entry with reset info
            entry["reset_at"] = reset_at
            entry["reset_by"] = reset_by
            # Remove old entry (use same type as received) and add updated one
            self.redis.zrem(self.history_key, entry_raw)
            self.redis.zadd(self.history_key, {json.dumps(entry): score})
```

### 2. Circuit Breaker Service (`services/cb_service.py`)

```python
"""Circuit breaker service with RBAC enforcement."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from libs.risk_management.breaker import CircuitBreaker
from libs.risk_management.exceptions import CircuitBreakerError
from libs.web_console_auth.permissions import Permission, has_permission

from .cb_metrics import CB_RESET_TOTAL, CB_STATUS_CHECKS, CB_TRIP_TOTAL
from .cb_rate_limiter import CBRateLimiter

if TYPE_CHECKING:
    from libs.redis_client import RedisClient
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


class CBServiceError(Exception):
    """Base exception for CB service errors."""


class RateLimitExceeded(CBServiceError):
    """Raised when rate limit is exceeded."""


class RBACViolation(CBServiceError):
    """Raised when RBAC check fails."""


class ValidationError(CBServiceError):
    """Raised when validation fails."""


MIN_RESET_REASON_LENGTH = 20


class CircuitBreakerService:
    """Service layer for circuit breaker operations with RBAC enforcement."""

    def __init__(self, redis_client: RedisClient, db_engine: AsyncEngine):
        self.redis = redis_client
        self.db = db_engine
        self.breaker = CircuitBreaker(redis_client)
        self.rate_limiter = CBRateLimiter(redis_client)

    def get_status(self) -> dict[str, Any]:
        """Get current CB status with staleness check."""
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
        """Trip CB with RBAC enforcement and audit logging.

        Args:
            reason: Trip reason (from TripReason enum or custom string)
            user: User session dict with user_id, role, etc.
            acknowledged: Whether user acknowledged the action

        Raises:
            RBACViolation: If user lacks TRIP_CIRCUIT permission
        """
        # RBAC enforcement at service level
        if not has_permission(user, Permission.TRIP_CIRCUIT):
            raise RBACViolation(
                f"User {user.get('user_id')} lacks TRIP_CIRCUIT permission"
            )

        self.breaker.trip(reason, details={"tripped_by": user.get("user_id")})
        self._log_audit(
            action="CIRCUIT_BREAKER_TRIP",
            user=user,
            resource_type="circuit_breaker",
            resource_id="global",
            reason=reason,
        )
        CB_TRIP_TOTAL.inc()
        return True

    def reset(
        self,
        reason: str,
        user: dict[str, Any],
        *,
        acknowledged: bool = False,
    ) -> bool:
        """Reset CB with rate limit, RBAC enforcement, and audit.

        Args:
            reason: Reset reason (min 20 chars required)
            user: User session dict with user_id, role, etc.
            acknowledged: Whether user acknowledged the action (required)

        Raises:
            RBACViolation: If user lacks RESET_CIRCUIT permission
            ValidationError: If reason too short or not acknowledged
            RateLimitExceeded: If global rate limit exceeded
            CircuitBreakerError: If not currently TRIPPED
        """
        # RBAC enforcement at service level
        if not has_permission(user, Permission.RESET_CIRCUIT):
            raise RBACViolation(
                f"User {user.get('user_id')} lacks RESET_CIRCUIT permission"
            )

        # Server-side validation
        if len(reason) < MIN_RESET_REASON_LENGTH:
            raise ValidationError(
                f"Reset reason must be at least {MIN_RESET_REASON_LENGTH} characters"
            )
        if not acknowledged:
            raise ValidationError("Reset must be explicitly acknowledged")

        # Global rate limit (1 reset per minute, regardless of user)
        if not self.rate_limiter.check_global(limit=1, window=60):
            raise RateLimitExceeded("Max 1 reset per minute (global)")

        # Perform reset
        user_id = user.get("user_id", "unknown")
        self.breaker.reset(reset_by=user_id)

        # Update history with reset info
        reset_at = datetime.now(UTC).isoformat()
        self.breaker.update_history_with_reset(reset_at, reset_by=user_id)

        # Audit log
        self._log_audit(
            action="CIRCUIT_BREAKER_RESET",
            user=user,
            resource_type="circuit_breaker",
            resource_id="global",
            reason=reason,
        )
        CB_RESET_TOTAL.inc()
        return True

    def get_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get trip/reset history.

        Falls back to audit log if Redis unavailable.
        """
        try:
            return self.breaker.get_history(limit=limit)
        except Exception as e:
            logger.warning(f"Redis history unavailable, falling back to audit: {e}")
            return self._get_history_from_audit(limit=limit)

    def _get_history_from_audit(self, limit: int) -> list[dict[str, Any]]:
        """Fallback: get history from PostgreSQL audit_log.

        Reads CIRCUIT_BREAKER_TRIP and CIRCUIT_BREAKER_RESET events and
        maps them to the same shape as Redis history entries.
        """
        import asyncio

        async def _fetch_from_db() -> list[dict[str, Any]]:
            from sqlalchemy import text

            async with self.db.connect() as conn:
                result = await conn.execute(
                    text("""
                        SELECT timestamp, action, details, user_name
                        FROM audit_log
                        WHERE action IN ('CIRCUIT_BREAKER_TRIP', 'CIRCUIT_BREAKER_RESET')
                        ORDER BY timestamp DESC
                        LIMIT :limit
                    """),
                    {"limit": limit},
                )
                rows = result.fetchall()

            # Map to same shape as Redis history entries
            history = []
            for row in rows:
                entry: dict[str, Any] = {
                    "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                    "action": row.action,
                    "reason": row.details.get("reason") if row.details else None,
                    "user": row.user_name,
                }
                if row.action == "CIRCUIT_BREAKER_TRIP":
                    entry["tripped_at"] = entry["timestamp"]
                elif row.action == "CIRCUIT_BREAKER_RESET":
                    entry["reset_at"] = entry["timestamp"]
                    entry["reset_by"] = row.user_name
                history.append(entry)
            return history

        try:
            return asyncio.get_event_loop().run_until_complete(_fetch_from_db())
        except Exception as e:
            logger.error(f"Failed to fetch history from audit log: {e}")
            return []

    def _log_audit(
        self,
        action: str,
        user: dict[str, Any],
        resource_type: str,
        resource_id: str,
        reason: str,
    ) -> None:
        """Write to PostgreSQL audit_log table."""
        import asyncio

        request_id = str(uuid4())
        user_id = user.get("user_id")
        user_name = user.get("username") or user.get("name")
        ip_address = user.get("ip_address")

        async def _write_audit() -> None:
            from sqlalchemy import text

            async with self.db.begin() as conn:
                await conn.execute(
                    text("""
                        INSERT INTO audit_log (
                            request_id, action, resource_type, resource_id,
                            user_id, user_name, details, ip_address
                        ) VALUES (
                            :request_id, :action, :resource_type, :resource_id,
                            :user_id, :user_name, :details, :ip_address
                        )
                    """),
                    {
                        "request_id": request_id,
                        "action": action,
                        "resource_type": resource_type,
                        "resource_id": resource_id,
                        "user_id": user_id,
                        "user_name": user_name,
                        "details": {"reason": reason},
                        "ip_address": ip_address,
                    },
                )

        try:
            asyncio.get_event_loop().run_until_complete(_write_audit())
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")
```

### 3. Prometheus Metrics (`services/cb_metrics.py`)

Metrics defined at module level (registered once at import):

```python
"""Prometheus metrics for circuit breaker operations."""

from prometheus_client import Counter

# No labels - simple counters avoid label mismatch errors
CB_STATUS_CHECKS = Counter(
    "cb_status_checks_total",
    "Total circuit breaker status checks"
)

CB_TRIP_TOTAL = Counter(
    "cb_trip_total",
    "Total circuit breaker trips"
)

CB_RESET_TOTAL = Counter(
    "cb_reset_total",
    "Total circuit breaker resets"
)
```

### 4. Global Rate Limiter (`services/cb_rate_limiter.py`)

Atomic rate limiting with global key:

```python
"""Atomic rate limiter for circuit breaker reset operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from libs.redis_client import RedisClient


class CBRateLimiter:
    """Atomic rate limiter for CB reset operations.

    Uses a GLOBAL key (not per-user) to prevent ANY user from
    resetting the circuit breaker more than once per minute.
    """

    KEY = "cb_ratelimit:reset:global"

    def __init__(self, redis_client: RedisClient):
        self.redis = redis_client

    def check_global(self, limit: int = 1, window: int = 60) -> bool:
        """Check if global reset is allowed (atomic).

        Uses atomic INCR with conditional EXPIRE to prevent race conditions.
        Two concurrent resets will not both pass.

        Args:
            limit: Max resets allowed in window
            window: Window size in seconds

        Returns:
            True if reset allowed, False if rate limited
        """
        # Atomic: INCR returns new value; set EXPIRE only if this is first increment
        new_count = self.redis.incr(self.KEY)

        if new_count == 1:
            # First increment in this window - set expiry
            self.redis.expire(self.KEY, window)

        if new_count > limit:
            return False

        return True
```

### 5. Streamlit Page (`pages/circuit_breaker.py`)

```python
"""Circuit Breaker Dashboard page."""

from __future__ import annotations

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from apps.web_console.auth.operations_auth import operations_requires_auth
from apps.web_console.services.cb_service import (
    CircuitBreakerService,
    RateLimitExceeded,
    RBACViolation,
    ValidationError,
)
from libs.risk_management.breaker import CircuitBreakerState
from libs.web_console_auth.permissions import Permission, has_permission


@operations_requires_auth
def circuit_breaker_page() -> None:
    """Circuit Breaker Dashboard."""
    st.title("⚡ Circuit Breaker Dashboard")

    # Auto-refresh every 5 seconds using streamlit-autorefresh
    st_autorefresh(interval=5000, key="cb_autorefresh")

    # Initialize service
    cb_service = _get_cb_service()

    # Status display
    _render_status_section(cb_service)

    st.divider()

    # Controls section (RBAC-gated at UI level + service level)
    _render_controls_section(cb_service)

    st.divider()

    # History table
    _render_history_section(cb_service)


def _render_status_section(cb_service: CircuitBreakerService) -> None:
    """Render CB status with color coding."""
    try:
        status = cb_service.get_status()
        state = status.get("state", "UNKNOWN")

        # Color coding
        if state == CircuitBreakerState.OPEN.value:
            st.success(f"🟢 **Status: {state}**")
        elif state == CircuitBreakerState.TRIPPED.value:
            st.error(f"🔴 **Status: {state}**")
            st.warning(f"Reason: {status.get('trip_reason', 'Unknown')}")
            st.info(f"Tripped at: {status.get('tripped_at', 'Unknown')}")
        elif state == CircuitBreakerState.QUIET_PERIOD.value:
            st.warning(f"🟡 **Status: {state}** (recovering)")
        else:
            st.info(f"❓ **Status: {state}**")
    except RuntimeError as e:
        st.error(f"⚠️ Cannot retrieve status: {e}")


def _render_controls_section(cb_service: CircuitBreakerService) -> None:
    """Render trip/reset controls with RBAC gating."""
    user = dict(st.session_state)

    col1, col2 = st.columns(2)

    with col1:
        # Trip control
        if has_permission(user, Permission.TRIP_CIRCUIT):
            with st.expander("🔴 Manual Trip"):
                reason = st.selectbox(
                    "Trip Reason",
                    ["MANUAL", "DATA_STALE", "BROKER_ERRORS", "Other"],
                    key="trip_reason_select",
                )
                if reason == "Other":
                    reason = st.text_input("Custom reason", key="trip_reason_custom")

                if st.button("Trip Circuit Breaker", type="primary", key="btn_trip"):
                    try:
                        cb_service.trip(reason or "MANUAL", user, acknowledged=True)
                        st.success("Circuit breaker TRIPPED")
                        st.rerun()
                    except RBACViolation as e:
                        st.error(f"Permission denied: {e}")
                    except Exception as e:
                        st.error(f"Error: {e}")

    with col2:
        # Reset control (step-up confirmation)
        if has_permission(user, Permission.RESET_CIRCUIT):
            with st.expander("🟢 Reset Circuit Breaker"):
                reason = st.text_area(
                    "Reset Reason (minimum 20 characters)",
                    key="reset_reason",
                    help="Explain why it's safe to resume trading",
                )
                char_count = len(reason) if reason else 0
                st.caption(f"{char_count}/20 characters")

                acknowledged = st.checkbox(
                    "I acknowledge that resetting will allow trading to resume",
                    key="reset_ack",
                )

                can_reset = char_count >= 20 and acknowledged
                if st.button(
                    "Confirm Reset",
                    disabled=not can_reset,
                    type="primary",
                    key="btn_reset",
                ):
                    try:
                        cb_service.reset(reason, user, acknowledged=acknowledged)
                        st.success("Circuit breaker RESET - entering quiet period")
                        st.rerun()
                    except RateLimitExceeded as e:
                        st.error(f"Rate limit: {e}")
                    except ValidationError as e:
                        st.error(f"Validation error: {e}")
                    except RBACViolation as e:
                        st.error(f"Permission denied: {e}")
                    except Exception as e:
                        st.error(f"Error: {e}")


def _render_history_section(cb_service: CircuitBreakerService) -> None:
    """Render trip/reset history table."""
    st.subheader("📜 Trip/Reset History")

    history = cb_service.get_history(limit=50)

    if not history:
        st.info("No trip history recorded")
        return

    # Display as table
    import pandas as pd

    df = pd.DataFrame(history)
    st.dataframe(df, use_container_width=True)


def _get_cb_service() -> CircuitBreakerService:
    """Get or create CB service instance."""
    if "cb_service" not in st.session_state:
        from apps.web_console.utils.db import get_async_engine
        from libs.redis_client import RedisClient

        redis = RedisClient.from_env()
        db = get_async_engine()
        st.session_state["cb_service"] = CircuitBreakerService(redis, db)
    return st.session_state["cb_service"]
```

### 6. RBAC Permissions

Extend `libs/web_console_auth/permissions.py` (source of truth):

```python
class Permission(str, Enum):
    # Existing permissions...

    # Circuit Breaker permissions (T7.1)
    VIEW_CIRCUIT_BREAKER = "view_circuit_breaker"
    TRIP_CIRCUIT = "trip_circuit"
    RESET_CIRCUIT = "reset_circuit"


# Role matrix
ROLE_PERMISSIONS = {
    "viewer": {
        Permission.VIEW_CIRCUIT_BREAKER,
    },
    "operator": {
        Permission.VIEW_CIRCUIT_BREAKER,
        Permission.TRIP_CIRCUIT,
        Permission.RESET_CIRCUIT,
    },
    "admin": {
        Permission.VIEW_CIRCUIT_BREAKER,
        Permission.TRIP_CIRCUIT,
        Permission.RESET_CIRCUIT,
    },
}
```

### 7. Database Migration

Valid PostgreSQL syntax for audit_log table:

```python
# migrations/versions/xxx_add_audit_log_for_cb.py
"""Add audit_log table for circuit breaker operations.

Revision ID: xxx
Revises: previous_revision
Create Date: 2025-12-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


def upgrade() -> None:
    # Check if table already exists using inspector
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    if "audit_log" not in existing_tables:
        # Create audit_log table
        op.create_table(
            "audit_log",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("action", sa.String(50), nullable=False),
            sa.Column("resource_type", sa.String(100), nullable=False),
            sa.Column("resource_id", sa.String(100), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("user_name", sa.String(200), nullable=True),
            sa.Column("details", postgresql.JSONB(), nullable=True),
            sa.Column("ip_address", postgresql.INET(), nullable=True),
            sa.Column("resource_state", postgresql.JSONB(), nullable=True),
        )

    # Create indexes if they don't exist (check via inspector)
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("audit_log")} if "audit_log" in existing_tables else set()

    if "idx_audit_timestamp" not in existing_indexes:
        op.create_index("idx_audit_timestamp", "audit_log", ["timestamp"])
    if "idx_audit_user" not in existing_indexes:
        op.create_index("idx_audit_user", "audit_log", ["user_id"])
    if "idx_audit_action" not in existing_indexes:
        op.create_index("idx_audit_action", "audit_log", ["action"])
    if "idx_audit_request_id" not in existing_indexes:
        op.create_index("idx_audit_request_id", "audit_log", ["request_id"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)

    if "audit_log" in inspector.get_table_names():
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("audit_log")}

        if "idx_audit_request_id" in existing_indexes:
            op.drop_index("idx_audit_request_id", table_name="audit_log")
        if "idx_audit_action" in existing_indexes:
            op.drop_index("idx_audit_action", table_name="audit_log")
        if "idx_audit_user" in existing_indexes:
            op.drop_index("idx_audit_user", table_name="audit_log")
        if "idx_audit_timestamp" in existing_indexes:
            op.drop_index("idx_audit_timestamp", table_name="audit_log")

        op.drop_table("audit_log")
```

## Testing Strategy

### Unit Tests

1. **Service Tests** (`test_cb_service.py`)
   - `test_get_status_returns_current_state`
   - `test_get_status_increments_metric`
   - `test_trip_logs_to_history`
   - `test_trip_writes_audit_log`
   - `test_trip_increments_metric`
   - `test_trip_fails_without_permission` (RBAC)
   - `test_reset_enforces_global_rate_limit`
   - `test_reset_requires_tripped_state`
   - `test_reset_validates_reason_length`
   - `test_reset_requires_acknowledgment`
   - `test_reset_writes_audit_log`
   - `test_reset_updates_history_with_reset_info`
   - `test_reset_increments_metric`
   - `test_reset_fails_without_permission` (RBAC)
   - `test_get_history_returns_ordered_entries`
   - `test_get_history_falls_back_to_audit_on_redis_error`
   - `test_get_history_fallback_maps_audit_to_history_shape`
   - `test_get_history_fallback_handles_db_error_gracefully`

2. **Rate Limiter Tests** (`test_cb_rate_limiter.py`)
   - `test_first_reset_allowed`
   - `test_second_reset_within_minute_blocked`
   - `test_concurrent_resets_only_one_passes` (race condition)
   - `test_reset_allowed_after_window_expires`

3. **RBAC Tests**
   - `test_viewer_cannot_trip_cb`
   - `test_viewer_cannot_reset_cb`
   - `test_operator_can_trip_cb`
   - `test_operator_can_reset_cb`
   - `test_admin_can_trip_cb`
   - `test_admin_can_reset_cb`

4. **CircuitBreaker Extension Tests**
   - `test_get_history_returns_newest_first`
   - `test_get_history_respects_limit`
   - `test_update_history_with_reset_modifies_latest_entry`
   - `test_fail_closed_when_state_missing`

5. **Metrics Tests**
   - `test_cb_status_checks_counter_increments`
   - `test_cb_trip_counter_increments`
   - `test_cb_reset_counter_increments`

### Integration Tests (`test_circuit_breaker_page.py`)

1. **Page Tests**
   - `test_status_display_shows_open`
   - `test_status_display_shows_tripped_with_reason`
   - `test_status_display_shows_quiet_period`
   - `test_trip_button_visible_for_operator`
   - `test_trip_button_hidden_for_viewer`
   - `test_reset_requires_20_char_reason`
   - `test_reset_requires_acknowledgment_checkbox`
   - `test_auto_refresh_interval_configured`

## Dependencies

- Existing: `libs/risk_management/breaker.py` (CircuitBreaker class) - extend with get_history
- Existing: `libs/redis_client` (RedisClient)
- Existing: `libs/web_console_auth/permissions.py` (Permission enum) - extend with CB permissions
- Existing: `streamlit-autorefresh` (in pyproject.toml)
- New: `apps/web_console/services/cb_service.py`
- New: `apps/web_console/services/cb_rate_limiter.py`
- New: `apps/web_console/services/cb_metrics.py`
- New: `apps/web_console/pages/circuit_breaker.py`

## Rollout Plan

1. Extend CircuitBreaker class with get_history and update_history_with_reset
2. Create CB metrics module
3. Create CB rate limiter with atomic global rate limiting
4. Create CB service with RBAC enforcement and audit logging
5. Create migration for audit_log table
6. Create Streamlit page with auto-refresh
7. Add RBAC permissions to libs/web_console_auth/permissions.py
8. Unit and integration tests
9. Review and commit

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Redis unavailable | Cannot display status | Show error message, don't fail silently |
| Rate limit bypass | Spam reset attempts | Atomic global rate limit (INCR + EXPIRE) |
| RBAC bypass | Unauthorized trip/reset | UI + service-level enforcement |
| Audit log failure | Missing audit trail | Log error but don't block operation |
| State missing | Fail-closed behavior | RuntimeError raised, logged |

## Metrics & Observability

- `cb_status_checks_total` - Counter for status check frequency
- `cb_trip_total` - Counter for trips
- `cb_reset_total` - Counter for resets
- Grafana dashboard: "Circuit Breaker Operations"
- Alert: `cb_reset_total > 5` in 5 min → warn to #alerts-ops

---

## Implementation Summary (Actual vs Planned)

**Status:** ✅ COMPLETE

### Files Created

| File | Purpose |
|------|---------|
| `apps/web_console/services/cb_service.py` | Service layer with RBAC, rate limiting, audit logging |
| `apps/web_console/services/cb_rate_limiter.py` | Atomic global rate limiter using SET NX EX pattern |
| `apps/web_console/services/cb_metrics.py` | Prometheus metrics (status_checks, trip, reset) |
| `apps/web_console/pages/circuit_breaker.py` | Streamlit dashboard page with auto-refresh |
| `tests/apps/web_console/services/test_cb_service.py` | 23 service tests |
| `tests/apps/web_console/services/test_cb_rate_limiter.py` | 9 rate limiter tests |

### Files Modified

| File | Changes |
|------|---------|
| `libs/risk_management/breaker.py` | Added `get_history()`, `update_history_with_reset()` methods |
| `libs/redis_client/client.py` | Added `zrevrange()`, `zrem()` with retry decorators |
| `libs/web_console_auth/permissions.py` | Added CB permissions (VIEW, TRIP, RESET) to T6.1 permission system |

### Key Implementation Differences from Plan

1. **Rate Limiter Pattern:** Used SET NX EX for limit=1 (crash-safe) instead of INCR+EXPIRE
2. **Audit Logging:** Uses sync psycopg3 connection pool instead of async SQLAlchemy
3. **db_pool Injection:** Page accepts injected db_pool for testability
4. **Post-reset Bookkeeping:** Wrapped in try/except to prevent masking successful resets
5. **Reset Reason in History:** Added `reset_reason` to Redis history for dashboard visibility

### Review Iterations

- **Gemini:** APPROVED (Production Ready)
- **Codex:** APPROVED after fixes for:
  - Rate limit token rollback on failure
  - Pipeline context manager usage
  - Retry decorators on sorted set operations
  - Post-reset bookkeeping error handling

### Test Coverage

- 23 service tests (RBAC, validation, rate limiting, audit fallback)
- 9 rate limiter tests (atomicity, environment namespacing)
- Breaker extension tests (history, fail-closed behavior)
