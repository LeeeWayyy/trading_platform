"""Circuit Breaker Dashboard page (T7.1).

This page provides real-time monitoring and control of the trading system's
circuit breaker. Operators can view the current state, trip/reset history,
and manually trip or reset the circuit breaker with proper authorization.

Features:
    - Real-time status display with color coding (OPEN/TRIPPED/QUIET_PERIOD)
    - Trip/reset history table
    - Manual trip control (requires TRIP_CIRCUIT permission)
    - Manual reset control with step-up confirmation (requires RESET_CIRCUIT permission)
    - Rate limiting on reset (max 1 per minute globally)
    - Auto-refresh every 5 seconds

Usage:
    This page is rendered via the web console navigation when
    FEATURE_CIRCUIT_BREAKER is enabled and the user has VIEW_CIRCUIT_BREAKER permission.
"""

from __future__ import annotations

import logging
import os
from typing import Any, cast

import pandas as pd
import psycopg
import streamlit as st
from streamlit_autorefresh import st_autorefresh  # type: ignore[import-untyped]

from apps.web_console.auth.operations_auth import operations_requires_auth
from apps.web_console.config import (
    FEATURE_CIRCUIT_BREAKER,
    MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH,
)
from apps.web_console.services.cb_service import (
    CircuitBreakerService,
    RateLimitExceeded,
    RBACViolation,
    ValidationError,
)
from libs.redis_client import RedisClient
from libs.risk_management.breaker import CircuitBreakerState
from libs.web_console_auth.permissions import Permission, has_permission

logger = logging.getLogger(__name__)


def _get_redis_client() -> RedisClient:
    """Get or create Redis client for circuit breaker operations.

    Uses cached client from session state to avoid reconnection on each refresh.
    Supports authentication via REDIS_PASSWORD.
    """
    if "cb_redis_client" not in st.session_state:
        host = os.getenv("REDIS_HOST", "localhost")
        port = int(os.getenv("REDIS_PORT", "6379"))
        db = int(os.getenv("REDIS_DB", "0"))
        password = os.getenv("REDIS_PASSWORD")
        # TODO: Add SSL support when RedisClient adds ssl parameter
        st.session_state["cb_redis_client"] = RedisClient(
            host=host, port=port, db=db, password=password
        )
    return cast(RedisClient, st.session_state["cb_redis_client"])


def _get_db_pool() -> Any:
    """Get database connection pool for audit logging.

    Returns None if not configured (audit logging will fall back to console).
    Logs a warning if pool creation fails so operators are aware audit is disabled.

    Catches specific exceptions:
        - ImportError: sync_db_pool module not available
        - psycopg.Error: Database connection issues
    """
    try:
        from apps.web_console.utils.sync_db_pool import get_sync_db_pool

        return get_sync_db_pool()
    except ImportError as e:
        logger.warning(
            "db_pool_module_not_found",
            extra={"error": str(e), "impact": "audit logging disabled"},
        )
        return None
    except (OSError, ConnectionError, psycopg.Error) as e:
        # OSError covers socket/network issues, ConnectionError for connection failures
        # psycopg.Error for PostgreSQL issues
        logger.warning(
            "db_pool_connection_failed",
            extra={"error": str(e), "impact": "audit logging disabled"},
        )
        return None


def _get_cb_service(db_pool: Any = None) -> CircuitBreakerService:
    """Get or create CircuitBreakerService instance.

    Uses cached service from session state for efficiency.
    If db_pool was None on first call but is now available, updates the service.

    Args:
        db_pool: Database connection pool for audit logging.
                 If provided, used instead of creating a new one.
    """
    if "cb_service" not in st.session_state:
        redis = _get_redis_client()
        # Use injected db_pool if provided, otherwise fall back to creating one
        pool = db_pool if db_pool is not None else _get_db_pool()
        st.session_state["cb_service"] = CircuitBreakerService(redis, pool)
    else:
        # If cached service has no db_pool but one is now available, update it
        # This handles recovery from transient DB failures
        service = st.session_state["cb_service"]
        if service.db_pool is None and db_pool is not None:
            service.db_pool = db_pool
    return cast(CircuitBreakerService, st.session_state["cb_service"])


def _render_status_section(cb_service: CircuitBreakerService) -> None:
    """Render circuit breaker status with color coding."""
    try:
        status = cb_service.get_status()
        state = status.get("state", "UNKNOWN")

        # Color-coded status display
        if state == CircuitBreakerState.OPEN.value:
            st.success(f"**Status: {state}**")
            st.caption("Trading is allowed")
        elif state == CircuitBreakerState.TRIPPED.value:
            st.error(f"**Status: {state}**")
            st.warning(f"**Reason:** {status.get('trip_reason', 'Unknown')}")
            st.info(f"**Tripped at:** {status.get('tripped_at', 'Unknown')}")
            if status.get("trip_details"):
                with st.expander("Trip Details"):
                    st.json(status["trip_details"])
        elif state == CircuitBreakerState.QUIET_PERIOD.value:
            st.warning(f"**Status: {state}** (recovering)")
            st.info(f"**Reset at:** {status.get('reset_at', 'Unknown')}")
            st.caption("System is in quiet period before returning to OPEN")
        else:
            st.info(f"**Status: {state}**")

        # Trip count today
        trip_count = status.get("trip_count_today", 0)
        if trip_count > 0:
            st.metric("Trips Today", trip_count)

    except RuntimeError as e:
        st.error(f"Cannot retrieve status: {e}")
        st.warning(
            "Circuit breaker state may be missing from Redis. "
            "Contact system administrator to initialize state."
        )


def _render_controls_section(cb_service: CircuitBreakerService) -> None:
    """Render trip/reset controls with RBAC gating."""
    # Create user dict with only string keys (session_state can have int keys)
    user: dict[str, Any] = {k: v for k, v in st.session_state.items() if isinstance(k, str)}

    col1, col2 = st.columns(2)

    with col1:
        # Trip control
        if has_permission(user, Permission.TRIP_CIRCUIT):
            with st.expander("Manual Trip", expanded=False):
                st.caption("Trip the circuit breaker to halt trading")

                reason = st.selectbox(
                    "Trip Reason",
                    ["MANUAL", "DATA_STALE", "BROKER_ERRORS", "Other"],
                    key="trip_reason_select",
                )
                custom_reason = None
                if reason == "Other":
                    custom_reason = st.text_input(
                        "Custom reason",
                        key="trip_reason_custom",
                    )

                final_reason = custom_reason if reason == "Other" else reason

                if st.button("Trip Circuit Breaker", type="primary", key="btn_trip"):
                    if not final_reason:
                        st.error("Please provide a reason")
                    else:
                        try:
                            cb_service.trip(final_reason, user, acknowledged=True)
                            st.success("Circuit breaker TRIPPED")
                            st.rerun()
                        except ValidationError as e:
                            st.error(f"Validation error: {e}")
                        except RBACViolation as e:
                            st.error(f"Permission denied: {e}")
                        except Exception as e:
                            st.error(f"Error: {e}")
        else:
            st.info("TRIP_CIRCUIT permission required")

    with col2:
        # Reset control (step-up confirmation)
        if has_permission(user, Permission.RESET_CIRCUIT):
            with st.expander("Reset Circuit Breaker", expanded=False):
                st.caption("Reset to allow trading to resume")
                st.warning(
                    "Resetting will enter a 5-minute quiet period before "
                    "returning to normal OPEN state."
                )

                reason = st.text_area(
                    f"Reset Reason (minimum {MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH} characters)",
                    key="reset_reason",
                    help="Explain why it's safe to resume trading",
                )
                char_count = len(reason) if reason else 0
                st.caption(f"{char_count}/{MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH} characters")

                acknowledged = st.checkbox(
                    "I acknowledge that resetting will allow trading to resume",
                    key="reset_ack",
                )

                can_reset = char_count >= MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH and acknowledged

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
                        st.error(f"Rate limit exceeded: {e}")
                    except ValidationError as e:
                        st.error(f"Validation error: {e}")
                    except RBACViolation as e:
                        st.error(f"Permission denied: {e}")
                    except Exception as e:
                        st.error(f"Error: {e}")
        else:
            st.info("RESET_CIRCUIT permission required")


def _render_history_section(cb_service: CircuitBreakerService) -> None:
    """Render trip/reset history table."""
    st.subheader("Trip/Reset History")

    history = cb_service.get_history(limit=50)

    if not history:
        st.info("No trip history recorded")
        return

    # Convert to DataFrame for display
    df = pd.DataFrame(history)

    # Reorder columns for better display (Pythonic list comprehension)
    preferred_order = ["tripped_at", "reason", "reset_at", "reset_by", "reset_reason", "details"]
    existing_cols = [col for col in preferred_order if col in df.columns]
    other_cols = [col for col in df.columns if col not in existing_cols]
    display_columns = existing_cols + other_cols

    if display_columns:
        df = df[display_columns]

    st.dataframe(df, use_container_width=True)


@operations_requires_auth
def render_circuit_breaker(user: dict[str, Any], db_pool: Any) -> None:
    """Render the Circuit Breaker Dashboard page.

    Args:
        user: Current user session dict
        db_pool: Database connection pool for audit logging
    """
    # Feature flag check
    if not FEATURE_CIRCUIT_BREAKER:
        st.info("Circuit Breaker Dashboard feature is disabled.")
        st.caption("Set FEATURE_CIRCUIT_BREAKER=true to enable.")
        return

    # Permission check
    if not has_permission(user, Permission.VIEW_CIRCUIT_BREAKER):
        st.error("Permission denied: VIEW_CIRCUIT_BREAKER required")
        st.stop()

    st.title("Circuit Breaker Dashboard")

    # Auto-refresh every 5 seconds using streamlit-autorefresh
    st_autorefresh(interval=5000, key="cb_autorefresh")

    # Initialize service with injected db_pool for audit logging
    cb_service = _get_cb_service(db_pool)

    # Status display
    _render_status_section(cb_service)

    st.divider()

    # Controls section (RBAC-gated at UI level + service level)
    _render_controls_section(cb_service)

    st.divider()

    # History table
    _render_history_section(cb_service)


# For direct page access (alternative to app.py navigation)
def main() -> None:
    """Entry point for direct page access."""
    user = dict(st.session_state)
    render_circuit_breaker(user=user, db_pool=None)


__all__ = [
    "render_circuit_breaker",
    "main",
]
