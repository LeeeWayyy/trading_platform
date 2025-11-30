"""Background token refresh monitor for Streamlit (Component 3).

This module monitors access token expiration and triggers automatic refresh
10 minutes before the token expires to prevent API call failures.

FIX (Codex Critical #2): Uses actual token expiry time (access_token_expires_at)
instead of last_activity which would never trigger refresh during active use.

Usage in Streamlit:
    from apps.web_console.auth.token_refresh_monitor import TokenRefreshMonitor

    monitor = TokenRefreshMonitor()
    if monitor.should_refresh_token(st.session_state["user_info"]):
        await monitor.refresh_token_via_api(session_id)
        st.rerun()  # Refresh UI with new token expiry
"""

import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

DEFAULT_REFRESH_URL = "https://nginx_oauth2/refresh"
INTERNAL_REFRESH_SECRET = os.getenv("INTERNAL_REFRESH_SECRET")
INTERNAL_CA_BUNDLE = os.getenv("INTERNAL_CA_BUNDLE", "/etc/nginx/certs/ca.crt")


def parse_iso_datetime(iso_string: str) -> datetime:
    """Parse ISO datetime with Z support.

    FIX (Codex High #3): Handles '...Z' format from session schema.
    """
    return datetime.fromisoformat(iso_string.replace("Z", "+00:00"))


class TokenRefreshMonitor:
    """Monitors access token expiration and triggers refresh.

    FIX (Codex Critical #2): Uses actual token expiry (access_token_expires_at)
    instead of last_activity which resets on every request and would never
    reach the 50-minute threshold during active use.
    """

    def __init__(
        self,
        refresh_threshold_seconds: int = 600,  # Refresh 10 minutes before expiry
    ):
        """Initialize token refresh monitor.

        Args:
            refresh_threshold_seconds: Time before expiry to trigger refresh (default 600s = 10min)
        """
        self.refresh_threshold = timedelta(seconds=refresh_threshold_seconds)

    def should_refresh_token(self, user_info: dict[str, str]) -> bool:
        """Determine if access token should be refreshed.

        FIX (Codex Critical #2): Uses access_token_expires_at (actual expiry)
        instead of last_activity (which tracks user interaction, not token validity).

        Args:
            user_info: User metadata from st.session_state containing access_token_expires_at

        Returns:
            True if token should be refreshed (10 minutes or less until expiry)
        """
        if "access_token_expires_at" not in user_info:
            logger.warning(
                "access_token_expires_at missing from user_info - cannot determine refresh need"
            )
            return False

        try:
            expires_at = parse_iso_datetime(user_info["access_token_expires_at"])
        except Exception as e:
            logger.error(f"Failed to parse access_token_expires_at: {e}")
            return False

        now = datetime.now(UTC)
        time_until_expiry = expires_at - now

        # Refresh if less than 10 minutes remaining
        should_refresh = time_until_expiry <= self.refresh_threshold

        if should_refresh:
            logger.info(
                "Token expiring soon, triggering refresh",
                extra={
                    "time_until_expiry_seconds": time_until_expiry.total_seconds(),
                    "threshold_seconds": self.refresh_threshold.total_seconds(),
                    "expires_at": user_info["access_token_expires_at"],
                },
            )

        return should_refresh

    async def refresh_token_via_api(self, session_id: str) -> bool:
        """Call /refresh endpoint to refresh access token.

        Args:
            session_id: Session ID from HttpOnly cookie

        Returns:
            True if refresh successful, False otherwise
        """
        refresh_url = os.getenv("AUTH_REFRESH_URL", DEFAULT_REFRESH_URL)
        headers = {}
        if INTERNAL_REFRESH_SECRET:
            headers["X-Internal-Auth"] = INTERNAL_REFRESH_SECRET

        verify: bool | str = True
        ca_path = Path(INTERNAL_CA_BUNDLE)
        if ca_path.exists():
            verify = str(ca_path)

        async with httpx.AsyncClient(timeout=10.0, verify=verify) as client:
            try:
                response = await client.post(
                    refresh_url,
                    cookies={"session_id": session_id},
                    headers=headers,
                )
                response.raise_for_status()
                logger.info("Access token refreshed automatically")
                return True
            except httpx.HTTPStatusError as e:
                logger.error(
                    f"Token refresh failed: {e.response.status_code}",
                    extra={"response_text": e.response.text},
                )
                return False
            except Exception as e:
                logger.error(f"Token refresh error: {e}")
                return False

    def get_time_until_expiry(self, user_info: dict[str, str]) -> timedelta | None:
        """Get time remaining until token expiry.

        Args:
            user_info: User metadata from st.session_state

        Returns:
            Time remaining until expiry, or None if cannot determine
        """
        if "access_token_expires_at" not in user_info:
            return None

        try:
            expires_at = parse_iso_datetime(user_info["access_token_expires_at"])
            now = datetime.now(UTC)
            return expires_at - now
        except Exception as e:
            logger.error(f"Failed to calculate time until expiry: {e}")
            return None
