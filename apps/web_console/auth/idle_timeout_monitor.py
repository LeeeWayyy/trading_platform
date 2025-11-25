"""Idle timeout monitoring and warning UI for Streamlit (Component 3).

This module provides idle timeout warnings to users 2 minutes before their
session expires due to inactivity (15-minute idle timeout).

FIX (Codex High #3): Handles ISO datetime with 'Z' timezone format correctly.

Usage in Streamlit:
    from apps.web_console.auth.idle_timeout_monitor import (
        should_show_idle_warning,
        render_idle_timeout_warning,
        extend_session_via_refresh,
    )

    if "user_info" in st.session_state:
        if should_show_idle_warning(st.session_state["user_info"]["last_activity"]):
            render_idle_timeout_warning(st.session_state["user_info"]["last_activity"])
"""

import asyncio
import logging
import os
import time
from datetime import UTC, datetime, timedelta

import httpx
import streamlit as st

logger = logging.getLogger(__name__)


def parse_iso_datetime(iso_string: str) -> datetime:
    """Parse ISO datetime string with Z timezone support.

    FIX (Codex High #3): Handles '...Z' format from session schema.

    Args:
        iso_string: ISO datetime string (e.g., "2025-11-23T10:00:00Z")

    Returns:
        Timezone-aware datetime object (UTC)

    Example:
        >>> parse_iso_datetime("2025-11-23T10:00:00Z")
        datetime.datetime(2025, 11, 23, 10, 0, tzinfo=datetime.timezone.utc)
    """
    # Replace 'Z' with '+00:00' for fromisoformat compatibility
    normalized = iso_string.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def get_idle_timeout_warning_threshold() -> timedelta:
    """Get threshold for displaying idle timeout warning.

    Returns:
        Warning threshold (13 minutes = 2 minutes before 15-minute timeout)
    """
    return timedelta(minutes=13)


def get_time_until_idle_timeout(last_activity_str: str) -> timedelta:
    """Calculate time remaining until idle timeout.

    Args:
        last_activity_str: ISO datetime string from session metadata

    Returns:
        Time remaining until idle timeout (may be negative if expired)
    """
    last_activity = parse_iso_datetime(last_activity_str)
    idle_timeout = timedelta(minutes=15)
    now = datetime.now(UTC)
    elapsed = now - last_activity
    return idle_timeout - elapsed


def should_show_idle_warning(last_activity_str: str) -> bool:
    """Determine if idle timeout warning should be shown.

    Args:
        last_activity_str: ISO datetime string from session metadata

    Returns:
        True if warning should be shown (2 minutes or less remaining)
    """
    time_remaining = get_time_until_idle_timeout(last_activity_str)
    warning_threshold = timedelta(minutes=2)

    # Show warning if less than 2 minutes remaining (but not expired)
    return time_remaining <= warning_threshold and time_remaining > timedelta(0)


def render_idle_timeout_warning(last_activity_str: str) -> None:
    """Render idle timeout warning banner with countdown.

    Uses st.rerun() with timer instead of meta-refresh (CSP-friendly).
    FIX (Codex High #4): No meta-refresh, uses Streamlit native refresh.

    Args:
        last_activity_str: ISO datetime string from session metadata
    """
    time_remaining = get_time_until_idle_timeout(last_activity_str)

    if time_remaining.total_seconds() <= 0:
        st.error("⏰ Your session has expired due to inactivity. Please log in again.")
        st.markdown("[Login](/login)")
        st.stop()

    minutes_remaining = int(time_remaining.total_seconds() / 60)
    seconds_remaining = int(time_remaining.total_seconds() % 60)

    st.warning(
        f"⏰ Your session will expire in {minutes_remaining}m {seconds_remaining}s due to inactivity. "
        "Interact with the page to extend your session."
    )

    # Use st.rerun() with sleep instead of meta-refresh (CSP-friendly)
    # Refreshes page every 5 seconds to update countdown
    time.sleep(5)
    st.rerun()


async def extend_session_via_refresh() -> None:
    """Extend session by calling /refresh endpoint.

    Calls the FastAPI auth_service /refresh endpoint which:
    - Validates session binding
    - Refreshes access token
    - Rotates refresh token
    - Updates last_activity timestamp
    """
    session_id = st.context.cookies.get("session_id")
    if not session_id:
        st.error("No session cookie found")
        return

    # FIX (Codex Medium): Use AUTH_SERVICE_URL env var instead of hardcoded URL
    auth_service_url = os.getenv("AUTH_SERVICE_URL", "http://auth_service:8000")
    refresh_url = f"{auth_service_url}/refresh"

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(
                refresh_url,
                cookies={"session_id": session_id},
            )
            response.raise_for_status()
            st.success("✅ Session extended successfully")
            # FIX (Codex Low): Use await asyncio.sleep instead of blocking time.sleep
            await asyncio.sleep(1)
            st.rerun()
        except httpx.HTTPStatusError as e:
            logger.error(f"Session extension failed: {e.response.status_code}")
            st.error(f"Failed to extend session: {e.response.text}")
        except Exception as e:
            logger.error(f"Session extension error: {e}")
            st.error(f"Failed to extend session: {str(e)}")


def extend_session_sync() -> None:
    """Synchronous wrapper for extend_session_via_refresh.

    Use this from Streamlit button callbacks which cannot be async.
    """
    asyncio.run(extend_session_via_refresh())
