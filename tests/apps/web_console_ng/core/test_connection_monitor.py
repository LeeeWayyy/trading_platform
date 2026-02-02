"""Unit tests for ConnectionMonitor."""

from __future__ import annotations

import pytest

from apps.web_console_ng.core import connection_monitor as monitor_module
from apps.web_console_ng.core.connection_monitor import (
    BACKOFF_BASE_SECONDS,
    DEGRADED_LATENCY_MS,
    ConnectionMonitor,
    ConnectionState,
)


class FakeTime:
    """Deterministic monotonic clock for tests."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def fake_time(monkeypatch: pytest.MonkeyPatch) -> FakeTime:
    clock = FakeTime()
    monkeypatch.setattr(monitor_module.time, "monotonic", clock.monotonic)
    return clock


def test_initial_state_is_connected(fake_time: FakeTime) -> None:
    monitor = ConnectionMonitor()
    assert monitor.get_connection_state() == ConnectionState.CONNECTED
    assert monitor.is_read_only() is False
    assert monitor.should_attempt() is True


def test_degraded_after_consecutive_latency_spikes(fake_time: FakeTime) -> None:
    monitor = ConnectionMonitor(degraded_required_count=3)
    monitor.record_success()

    monitor.record_latency(DEGRADED_LATENCY_MS + 1)
    assert monitor.get_connection_state() == ConnectionState.CONNECTED

    monitor.record_latency(DEGRADED_LATENCY_MS + 1)
    assert monitor.get_connection_state() == ConnectionState.CONNECTED

    monitor.record_latency(DEGRADED_LATENCY_MS + 1)
    assert monitor.get_connection_state() == ConnectionState.DEGRADED


def test_degraded_recovers_on_good_latency(fake_time: FakeTime) -> None:
    monitor = ConnectionMonitor(degraded_required_count=2)
    monitor.record_success()
    monitor.record_latency(DEGRADED_LATENCY_MS + 10)
    monitor.record_latency(DEGRADED_LATENCY_MS + 10)
    assert monitor.get_connection_state() == ConnectionState.DEGRADED

    monitor.record_latency(DEGRADED_LATENCY_MS - 50)
    assert monitor.get_connection_state() == ConnectionState.CONNECTED


def test_record_failure_schedules_backoff(fake_time: FakeTime) -> None:
    monitor = ConnectionMonitor()
    monitor.record_failure()

    assert monitor.get_connection_state() == ConnectionState.DISCONNECTED
    assert monitor.get_reconnect_countdown() == pytest.approx(BACKOFF_BASE_SECONDS)

    monitor.start_reconnect()
    assert monitor.get_connection_state() == ConnectionState.RECONNECTING


def test_should_attempt_respects_backoff(fake_time: FakeTime) -> None:
    monitor = ConnectionMonitor()
    monitor.record_failure()
    monitor.start_reconnect()

    assert monitor.should_attempt() is False
    fake_time.advance(BACKOFF_BASE_SECONDS)
    assert monitor.should_attempt() is True


def test_stale_detection(fake_time: FakeTime) -> None:
    monitor = ConnectionMonitor(stale_threshold_seconds=30.0)
    monitor.record_success()
    assert monitor.is_stale() is False

    fake_time.advance(31.0)
    assert monitor.is_stale() is True
    assert monitor.get_badge_text() == "Stale data"


def test_read_only_for_reconnecting(fake_time: FakeTime) -> None:
    monitor = ConnectionMonitor()
    monitor.record_failure()
    monitor.start_reconnect()
    assert monitor.is_read_only() is True
    assert monitor.get_connection_state() == ConnectionState.RECONNECTING


def test_record_success_preserves_degraded_counter(fake_time: FakeTime) -> None:
    """Test that record_success() does not reset the consecutive_degraded counter.

    This ensures that degraded state is properly maintained when there are
    sustained high latency measurements, even across multiple record_success() calls.
    """
    monitor = ConnectionMonitor(degraded_required_count=3)
    monitor.record_success()

    # Record two high-latency measurements
    monitor.record_latency(DEGRADED_LATENCY_MS + 10)
    monitor.record_latency(DEGRADED_LATENCY_MS + 10)
    assert monitor._consecutive_degraded == 2
    assert monitor.get_connection_state() == ConnectionState.CONNECTED

    # record_success() should NOT reset the counter
    monitor.record_success()
    assert monitor._consecutive_degraded == 2

    # Third high latency should still trigger DEGRADED
    monitor.record_latency(DEGRADED_LATENCY_MS + 10)
    assert monitor._consecutive_degraded == 3
    assert monitor.get_connection_state() == ConnectionState.DEGRADED


def test_record_success_with_latency_updates_degraded_state(fake_time: FakeTime) -> None:
    """Test that record_success(latency_ms=...) properly updates degraded state."""
    monitor = ConnectionMonitor(degraded_required_count=2)
    monitor.record_success()

    # High latency via record_success with latency_ms
    monitor.record_success(latency_ms=DEGRADED_LATENCY_MS + 10)
    assert monitor._consecutive_degraded == 1

    monitor.record_success(latency_ms=DEGRADED_LATENCY_MS + 10)
    assert monitor._consecutive_degraded == 2
    assert monitor.get_connection_state() == ConnectionState.DEGRADED

    # Low latency should recover
    monitor.record_success(latency_ms=DEGRADED_LATENCY_MS - 50)
    assert monitor._consecutive_degraded == 0
    assert monitor.get_connection_state() == ConnectionState.CONNECTED


def test_is_stale_returns_false_when_no_success_recorded(fake_time: FakeTime) -> None:
    """Test that is_stale returns False if no successful connection recorded yet."""
    monitor = ConnectionMonitor()
    # No record_success called, so _last_success_time is None
    assert monitor.is_stale() is False


def test_record_success_transitions_from_reconnecting_to_connected(
    fake_time: FakeTime,
) -> None:
    """Test that record_success transitions from RECONNECTING to CONNECTED."""
    monitor = ConnectionMonitor()
    monitor.record_failure()
    monitor.start_reconnect()
    assert monitor.get_connection_state() == ConnectionState.RECONNECTING

    # record_success should transition to CONNECTED
    monitor.record_success()
    assert monitor.get_connection_state() == ConnectionState.CONNECTED


def test_record_failure_extended_backoff_after_max_attempts(fake_time: FakeTime) -> None:
    """Test that after max_reconnect_attempts, backoff is capped at max."""
    monitor = ConnectionMonitor(max_reconnect_attempts=3, backoff_max_seconds=30.0)

    # Exhaust max_reconnect_attempts
    for _ in range(3):
        monitor.record_failure()

    # Next failure exceeds max_reconnect_attempts
    fake_time.advance(30.0)
    monitor.record_failure()  # This is attempt 4, exceeds max_reconnect_attempts=3

    # Should use max backoff and log a warning
    assert monitor.get_reconnect_countdown() == pytest.approx(30.0)


def test_should_attempt_returns_false_when_no_retry_scheduled(
    fake_time: FakeTime,
) -> None:
    """Test should_attempt returns False when _next_retry_at is None and disconnected."""
    monitor = ConnectionMonitor()
    # Manually set state to DISCONNECTED without scheduling retry
    monitor._state = ConnectionState.DISCONNECTED
    monitor._next_retry_at = None
    assert monitor.should_attempt() is False


def test_get_reconnect_countdown_returns_none_when_not_scheduled(
    fake_time: FakeTime,
) -> None:
    """Test get_reconnect_countdown returns None when no retry scheduled."""
    monitor = ConnectionMonitor()
    assert monitor.get_reconnect_countdown() is None


def test_get_badge_text_for_reconnecting_states(fake_time: FakeTime) -> None:
    """Test get_badge_text returns correct text for reconnecting states."""
    monitor = ConnectionMonitor()
    monitor.record_failure()
    monitor.start_reconnect()

    # Should show countdown
    badge_text = monitor.get_badge_text()
    assert "Reconnecting in" in badge_text

    # Advance time past the backoff
    fake_time.advance(BACKOFF_BASE_SECONDS + 0.1)

    # When countdown is <= 0, should show "Reconnecting"
    assert monitor.get_badge_text() == "Reconnecting"


def test_get_badge_text_for_degraded_state(fake_time: FakeTime) -> None:
    """Test get_badge_text returns 'Degraded' for degraded state."""
    monitor = ConnectionMonitor(degraded_required_count=2)
    monitor.record_success()
    monitor.record_latency(DEGRADED_LATENCY_MS + 10)
    monitor.record_latency(DEGRADED_LATENCY_MS + 10)
    assert monitor.get_connection_state() == ConnectionState.DEGRADED
    assert monitor.get_badge_text() == "Degraded"


def test_get_badge_text_for_disconnected_state(fake_time: FakeTime) -> None:
    """Test get_badge_text returns 'Disconnected' for disconnected state."""
    monitor = ConnectionMonitor()
    monitor.record_failure()
    assert monitor.get_connection_state() == ConnectionState.DISCONNECTED
    assert monitor.get_badge_text() == "Disconnected"


def test_get_badge_text_for_connected_state(fake_time: FakeTime) -> None:
    """Test get_badge_text returns 'Connected' for connected state."""
    monitor = ConnectionMonitor()
    monitor.record_success()
    assert monitor.get_badge_text() == "Connected"


def test_get_badge_class_for_all_states(fake_time: FakeTime) -> None:
    """Test get_badge_class returns correct CSS class for all states."""
    from apps.web_console_ng.ui.theme import (
        CONNECTION_CONNECTED,
        CONNECTION_DEGRADED,
        CONNECTION_DISCONNECTED,
        CONNECTION_RECONNECTING,
        CONNECTION_STALE,
    )

    monitor = ConnectionMonitor(degraded_required_count=2, stale_threshold_seconds=30.0)

    # CONNECTED state
    monitor.record_success()
    assert monitor.get_badge_class() == CONNECTION_CONNECTED

    # DEGRADED state
    monitor.record_latency(DEGRADED_LATENCY_MS + 10)
    monitor.record_latency(DEGRADED_LATENCY_MS + 10)
    assert monitor.get_badge_class() == CONNECTION_DEGRADED

    # STALE state (when connected but data is stale)
    fake_time.advance(31.0)
    assert monitor.get_badge_class() == CONNECTION_STALE

    # DISCONNECTED state
    monitor.record_failure()
    assert monitor.get_badge_class() == CONNECTION_DISCONNECTED

    # RECONNECTING state
    monitor.start_reconnect()
    assert monitor.get_badge_class() == CONNECTION_RECONNECTING


def test_get_badge_class_stale_overrides_degraded(fake_time: FakeTime) -> None:
    """Test that stale data overrides degraded badge class."""
    from apps.web_console_ng.ui.theme import CONNECTION_STALE

    monitor = ConnectionMonitor(degraded_required_count=2, stale_threshold_seconds=30.0)
    monitor.record_success()
    monitor.record_latency(DEGRADED_LATENCY_MS + 10)
    monitor.record_latency(DEGRADED_LATENCY_MS + 10)
    assert monitor.get_connection_state() == ConnectionState.DEGRADED

    # Advance time to make data stale
    fake_time.advance(31.0)
    assert monitor.is_stale() is True
    # Stale should override degraded
    assert monitor.get_badge_class() == CONNECTION_STALE


def test_record_latency_does_not_update_state_when_disconnected(
    fake_time: FakeTime,
) -> None:
    """Test record_latency does not change state when DISCONNECTED or RECONNECTING."""
    monitor = ConnectionMonitor(degraded_required_count=2)

    # Put monitor in DISCONNECTED state
    monitor.record_failure()
    assert monitor.get_connection_state() == ConnectionState.DISCONNECTED

    # Recording high latency should NOT change state to DEGRADED
    monitor.record_latency(DEGRADED_LATENCY_MS + 100)
    monitor.record_latency(DEGRADED_LATENCY_MS + 100)
    assert monitor.get_connection_state() == ConnectionState.DISCONNECTED

    # The internal counter increments but state is unchanged
    assert monitor._consecutive_degraded == 2


def test_start_reconnect_no_op_when_not_disconnected(fake_time: FakeTime) -> None:
    """Test start_reconnect is a no-op when not in DISCONNECTED state."""
    monitor = ConnectionMonitor()
    monitor.record_success()
    assert monitor.get_connection_state() == ConnectionState.CONNECTED

    # start_reconnect should not change state when CONNECTED
    monitor.start_reconnect()
    assert monitor.get_connection_state() == ConnectionState.CONNECTED


def test_start_reconnect_no_op_when_no_retry_scheduled(fake_time: FakeTime) -> None:
    """Test start_reconnect is a no-op when _next_retry_at is None."""
    monitor = ConnectionMonitor()
    # Manually set disconnected but no retry scheduled
    monitor._state = ConnectionState.DISCONNECTED
    monitor._next_retry_at = None

    monitor.start_reconnect()
    # State should remain DISCONNECTED since no retry is scheduled
    assert monitor.get_connection_state() == ConnectionState.DISCONNECTED
