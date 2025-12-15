"""Unit tests for idle timeout monitoring (Component 3)."""

from datetime import UTC, datetime, timedelta

from apps.web_console.auth.idle_timeout_monitor import (
    get_idle_timeout_warning_threshold,
    get_time_until_idle_timeout,
    parse_iso_datetime,
    should_show_idle_warning,
)


class TestParseIsoDatetime:
    """Test ISO datetime parsing with Z timezone support (Codex High #3 Fix)."""

    def test_parse_iso_datetime_with_z_suffix(self) -> None:
        """Test parsing ISO datetime with Z suffix."""
        iso_string = "2025-11-23T10:00:00Z"
        result = parse_iso_datetime(iso_string)

        assert result.year == 2025
        assert result.month == 11
        assert result.day == 23
        assert result.hour == 10
        assert result.minute == 0
        assert result.second == 0
        assert result.tzinfo == UTC

    def test_parse_iso_datetime_with_offset(self) -> None:
        """Test parsing ISO datetime with +00:00 offset."""
        iso_string = "2025-11-23T10:00:00+00:00"
        result = parse_iso_datetime(iso_string)

        assert result.year == 2025
        assert result.hour == 10
        assert result.tzinfo is not None

    def test_parse_iso_datetime_with_microseconds(self) -> None:
        """Test parsing ISO datetime with microseconds and Z suffix."""
        iso_string = "2025-11-23T10:00:00.123456Z"
        result = parse_iso_datetime(iso_string)

        assert result.microsecond == 123456
        assert result.tzinfo == UTC


class TestIdleTimeoutThresholds:
    """Test idle timeout threshold calculations."""

    def test_get_idle_timeout_warning_threshold(self) -> None:
        """Test warning threshold is 13 minutes (2 min before 15 min timeout)."""
        threshold = get_idle_timeout_warning_threshold()
        assert threshold == timedelta(minutes=13)

    def test_get_time_until_idle_timeout_not_expired(self) -> None:
        """Test time remaining calculation when session is active."""
        # Last activity 5 minutes ago
        last_activity = datetime.now(UTC) - timedelta(minutes=5)
        last_activity_str = last_activity.isoformat().replace("+00:00", "Z")

        time_remaining = get_time_until_idle_timeout(last_activity_str)

        # Should have ~10 minutes remaining (15 - 5)
        assert 9 <= time_remaining.total_seconds() / 60 <= 11

    def test_get_time_until_idle_timeout_expired(self) -> None:
        """Test time remaining is negative when session expired."""
        # Last activity 20 minutes ago (expired)
        last_activity = datetime.now(UTC) - timedelta(minutes=20)
        last_activity_str = last_activity.isoformat().replace("+00:00", "Z")

        time_remaining = get_time_until_idle_timeout(last_activity_str)

        # Should be negative (session expired)
        assert time_remaining.total_seconds() < 0

    def test_get_time_until_idle_timeout_near_expiry(self) -> None:
        """Test time remaining when session is near expiry (14 minutes)."""
        # Last activity 14 minutes ago
        last_activity = datetime.now(UTC) - timedelta(minutes=14)
        last_activity_str = last_activity.isoformat().replace("+00:00", "Z")

        time_remaining = get_time_until_idle_timeout(last_activity_str)

        # Should have ~1 minute remaining
        assert 0 <= time_remaining.total_seconds() / 60 <= 2


class TestShouldShowIdleWarning:
    """Test idle timeout warning trigger logic."""

    def test_should_show_idle_warning_at_13_minutes(self) -> None:
        """Test warning shows at 13 minutes (2 min before expiry)."""
        # Last activity 13 minutes ago
        last_activity = datetime.now(UTC) - timedelta(minutes=13)
        last_activity_str = last_activity.isoformat().replace("+00:00", "Z")

        assert should_show_idle_warning(last_activity_str) is True

    def test_should_show_idle_warning_at_14_minutes(self) -> None:
        """Test warning shows at 14 minutes (1 min before expiry)."""
        # Last activity 14 minutes ago
        last_activity = datetime.now(UTC) - timedelta(minutes=14)
        last_activity_str = last_activity.isoformat().replace("+00:00", "Z")

        assert should_show_idle_warning(last_activity_str) is True

    def test_should_not_show_warning_at_12_minutes(self) -> None:
        """Test warning does NOT show at 12 minutes (3 min before expiry)."""
        # Last activity 12 minutes ago
        last_activity = datetime.now(UTC) - timedelta(minutes=12)
        last_activity_str = last_activity.isoformat().replace("+00:00", "Z")

        assert should_show_idle_warning(last_activity_str) is False

    def test_should_not_show_warning_at_5_minutes(self) -> None:
        """Test warning does NOT show at 5 minutes (session active)."""
        # Last activity 5 minutes ago
        last_activity = datetime.now(UTC) - timedelta(minutes=5)
        last_activity_str = last_activity.isoformat().replace("+00:00", "Z")

        assert should_show_idle_warning(last_activity_str) is False

    def test_should_not_show_warning_when_expired(self) -> None:
        """Test warning does NOT show when session already expired."""
        # Last activity 20 minutes ago (expired)
        last_activity = datetime.now(UTC) - timedelta(minutes=20)
        last_activity_str = last_activity.isoformat().replace("+00:00", "Z")

        # Should be False because session is expired (not just warning)
        assert should_show_idle_warning(last_activity_str) is False

    def test_should_show_warning_boundary_at_13_min_1_sec(self) -> None:
        """Test warning shows just past 13 minute boundary."""
        # Last activity 13 minutes 1 second ago
        last_activity = datetime.now(UTC) - timedelta(minutes=13, seconds=1)
        last_activity_str = last_activity.isoformat().replace("+00:00", "Z")

        assert should_show_idle_warning(last_activity_str) is True
