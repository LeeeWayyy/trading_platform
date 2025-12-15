"""Unit tests for token refresh monitoring (Component 3).

FIX (Codex Critical #2): Tests verify auto-refresh uses actual token expiry
(access_token_expires_at) instead of last_activity.
"""

from datetime import UTC, datetime, timedelta

from apps.web_console.auth.token_refresh_monitor import (
    TokenRefreshMonitor,
    parse_iso_datetime,
)


class TestTokenRefreshMonitor:
    """Test token refresh monitoring with actual expiry tracking."""

    def test_should_refresh_token_at_50_minutes(self) -> None:
        """Test refresh triggers 10 minutes before 1-hour expiry (at 50 min).

        CRITICAL (Codex Critical #2): This tests the FIX - uses access_token_expires_at
        (actual expiry) instead of last_activity (which would never trigger during active use).
        """
        monitor = TokenRefreshMonitor(refresh_threshold_seconds=600)  # 10 minutes

        # Token expires in 9 minutes (should trigger refresh)
        expires_at = datetime.now(UTC) + timedelta(minutes=9)
        user_info = {
            "user_id": "auth0|12345",
            "email": "test@example.com",
            "access_token_expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        }

        assert monitor.should_refresh_token(user_info) is True

    def test_should_not_refresh_token_at_20_minutes_remaining(self) -> None:
        """Test refresh does NOT trigger with 20 minutes remaining."""
        monitor = TokenRefreshMonitor(refresh_threshold_seconds=600)

        # Token expires in 20 minutes (should NOT trigger)
        expires_at = datetime.now(UTC) + timedelta(minutes=20)
        user_info = {
            "access_token_expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        }

        assert monitor.should_refresh_token(user_info) is False

    def test_should_refresh_token_at_exact_threshold(self) -> None:
        """Test refresh triggers at exact threshold (10 minutes remaining)."""
        monitor = TokenRefreshMonitor(refresh_threshold_seconds=600)

        # Token expires in exactly 10 minutes
        expires_at = datetime.now(UTC) + timedelta(minutes=10)
        user_info = {
            "access_token_expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        }

        assert monitor.should_refresh_token(user_info) is True

    def test_should_refresh_token_when_expired(self) -> None:
        """Test refresh triggers when token already expired."""
        monitor = TokenRefreshMonitor(refresh_threshold_seconds=600)

        # Token expired 5 minutes ago
        expires_at = datetime.now(UTC) - timedelta(minutes=5)
        user_info = {
            "access_token_expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        }

        assert monitor.should_refresh_token(user_info) is True

    def test_should_refresh_token_missing_expires_at(self) -> None:
        """Test returns False when access_token_expires_at missing."""
        monitor = TokenRefreshMonitor()

        user_info = {
            "user_id": "auth0|12345",
            "email": "test@example.com",
            # Missing access_token_expires_at
        }

        assert monitor.should_refresh_token(user_info) is False

    def test_should_refresh_token_invalid_datetime_format(self) -> None:
        """Test returns False when datetime format is invalid."""
        monitor = TokenRefreshMonitor()

        user_info = {
            "access_token_expires_at": "invalid-datetime",
        }

        assert monitor.should_refresh_token(user_info) is False

    def test_get_time_until_expiry_valid(self) -> None:
        """Test get_time_until_expiry with valid data."""
        monitor = TokenRefreshMonitor()

        expires_at = datetime.now(UTC) + timedelta(minutes=15)
        user_info = {
            "access_token_expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        }

        time_remaining = monitor.get_time_until_expiry(user_info)

        assert time_remaining is not None
        # Should be approximately 15 minutes
        assert 14 <= time_remaining.total_seconds() / 60 <= 16

    def test_get_time_until_expiry_missing_field(self) -> None:
        """Test get_time_until_expiry returns None when field missing."""
        monitor = TokenRefreshMonitor()

        user_info = {"user_id": "auth0|12345"}

        assert monitor.get_time_until_expiry(user_info) is None

    def test_get_time_until_expiry_invalid_format(self) -> None:
        """Test get_time_until_expiry returns None for invalid format."""
        monitor = TokenRefreshMonitor()

        user_info = {"access_token_expires_at": "invalid"}

        assert monitor.get_time_until_expiry(user_info) is None

    def test_custom_refresh_threshold(self) -> None:
        """Test custom refresh threshold (e.g., 5 minutes)."""
        monitor = TokenRefreshMonitor(refresh_threshold_seconds=300)  # 5 minutes

        # Token expires in 4 minutes (should trigger with 5-min threshold)
        expires_at = datetime.now(UTC) + timedelta(minutes=4)
        user_info = {
            "access_token_expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        }

        assert monitor.should_refresh_token(user_info) is True

        # Token expires in 6 minutes (should NOT trigger)
        expires_at = datetime.now(UTC) + timedelta(minutes=6)
        user_info = {
            "access_token_expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        }

        assert monitor.should_refresh_token(user_info) is False


class TestParseIsoDatetimeHelper:
    """Test parse_iso_datetime helper (shared with idle_timeout_monitor)."""

    def test_parse_iso_datetime_z_format(self) -> None:
        """Test parsing with Z suffix (Codex High #3 fix)."""
        dt = parse_iso_datetime("2025-11-23T10:00:00Z")
        assert dt.tzinfo == UTC

    def test_parse_iso_datetime_offset_format(self) -> None:
        """Test parsing with +00:00 offset."""
        dt = parse_iso_datetime("2025-11-23T10:00:00+00:00")
        assert dt.tzinfo is not None
