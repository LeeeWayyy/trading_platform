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
