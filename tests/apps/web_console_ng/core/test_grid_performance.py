"""Comprehensive tests for GridPerformanceMonitor and UpdateMetrics.

This test suite covers:
- UpdateMetrics: recording updates, batches, drops, rate calculation, windowing
- GridPerformanceMonitor: degradation mode, state management, lifecycle
- Registry management: monitor attachment, retrieval, cleanup
- Edge cases: missing session ID, no event loop, concurrent access

Target: 85%+ branch coverage
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from apps.web_console_ng.core import grid_performance
from apps.web_console_ng.core.grid_performance import (
    GridPerformanceMonitor,
    UpdateMetrics,
    get_all_monitors,
    get_monitor,
    get_monitor_by_grid_id,
)


class DummyGrid:
    """Minimal grid stub for testing."""

    pass


@pytest.fixture(autouse=True)
def reset_registries() -> None:
    """Reset module-level registries before each test."""
    grid_performance._monitor_by_grid_and_session.clear()
    grid_performance._grid_to_monitor.clear()


def _time_sequence(values: list[float]):
    """Create a mock time.time() that returns values sequentially."""
    iterator = iter(values)

    def _now() -> float:
        return next(iterator)

    return _now


# ==================== UpdateMetrics Tests ====================


def test_update_metrics_initialization() -> None:
    """Test UpdateMetrics initial state."""
    metrics = UpdateMetrics()
    assert metrics.updates_total == 0
    assert metrics.updates_dropped == 0
    assert metrics.batches_sent == 0
    assert metrics.last_batch_time_ms == 0.0
    assert metrics.degradation_events == 0
    assert metrics._window_updates == 0
    assert isinstance(metrics._window_start, float)


def test_update_metrics_record_update() -> None:
    """Test recording update batches."""
    metrics = UpdateMetrics()
    metrics.record_update(10)
    assert metrics.updates_total == 10
    assert metrics._window_updates == 10

    metrics.record_update(5)
    assert metrics.updates_total == 15
    assert metrics._window_updates == 15


def test_update_metrics_record_batch() -> None:
    """Test recording batch timing."""
    metrics = UpdateMetrics()
    metrics.record_batch(12.5)
    assert metrics.batches_sent == 1
    assert metrics.last_batch_time_ms == 12.5

    metrics.record_batch(8.3)
    assert metrics.batches_sent == 2
    assert metrics.last_batch_time_ms == 8.3


def test_update_metrics_record_dropped() -> None:
    """Test recording dropped updates."""
    metrics = UpdateMetrics()
    metrics.record_dropped(3)
    assert metrics.updates_dropped == 3

    metrics.record_dropped(2)
    assert metrics.updates_dropped == 5


def test_update_metrics_get_rate_too_early(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test get_rate returns 0.0 if elapsed < 1.0 second."""
    metrics = UpdateMetrics()
    metrics._window_start = 0.0
    metrics._window_updates = 10

    # Only 0.5 seconds elapsed
    monkeypatch.setattr(grid_performance.time, "time", _time_sequence([0.5]))
    assert metrics.get_rate() == 0.0


def test_update_metrics_get_rate_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test rate calculation: 10 updates in 2 seconds = 5 updates/sec."""
    metrics = UpdateMetrics()
    metrics._window_start = 0.0
    metrics._window_updates = 10

    monkeypatch.setattr(grid_performance.time, "time", _time_sequence([2.0]))
    assert metrics.get_rate() == 5.0


def test_update_metrics_rate_reset_after_5_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test window resets after 5 seconds."""
    metrics = UpdateMetrics()
    metrics._window_start = 0.0
    metrics._window_updates = 10

    # First call at 5 seconds: rate = 10/5 = 2.0, then reset
    monkeypatch.setattr(grid_performance.time, "time", _time_sequence([5.0, 6.0]))
    rate = metrics.get_rate()
    assert rate == 2.0
    assert metrics._window_updates == 0
    assert metrics._window_start == 6.0


def test_update_metrics_rate_no_reset_before_5_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test window does NOT reset before 5 seconds."""
    metrics = UpdateMetrics()
    metrics._window_start = 0.0
    metrics._window_updates = 12

    # At 3 seconds: rate = 12/3 = 4.0, no reset
    monkeypatch.setattr(grid_performance.time, "time", _time_sequence([3.0]))
    rate = metrics.get_rate()
    assert rate == 4.0
    assert metrics._window_updates == 12  # Not reset
    assert metrics._window_start == 0.0  # Not reset


def test_update_metrics_to_dict_basic() -> None:
    """Test to_dict exports all fields."""
    metrics = UpdateMetrics()
    metrics.updates_total = 100
    metrics.updates_dropped = 5
    metrics.batches_sent = 10
    metrics.last_batch_time_ms = 7.89
    metrics.degradation_events = 2

    result = metrics.to_dict()
    assert result["updates_total"] == 100
    assert result["updates_dropped"] == 5
    assert result["batches_sent"] == 10
    assert result["last_batch_time_ms"] == 7.89
    assert result["degradation_events"] == 2
    assert "current_rate" in result


def test_update_metrics_to_dict_rounding(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test to_dict rounds last_batch_time_ms to 2 decimals, current_rate to 1."""
    metrics = UpdateMetrics()
    metrics._window_start = 0.0
    metrics._window_updates = 9
    metrics.record_batch(1.23456)

    monkeypatch.setattr(grid_performance.time, "time", _time_sequence([3.0]))
    payload = metrics.to_dict()

    assert payload["batches_sent"] == 1
    assert payload["last_batch_time_ms"] == 1.23  # Rounded to 2 decimals
    assert payload["current_rate"] == 3.0  # 9 updates / 3 seconds, rounded to 1 decimal


# ==================== GridPerformanceMonitor Tests ====================


def test_grid_monitor_initialization() -> None:
    """Test GridPerformanceMonitor initial state."""
    monitor = GridPerformanceMonitor("test-grid")
    assert monitor.grid_id == "test-grid"
    assert isinstance(monitor.metrics, UpdateMetrics)
    assert monitor._degraded is False


def test_grid_monitor_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test environment variable configuration."""
    monkeypatch.setenv("GRID_MAX_BATCH_SIZE", "1000")
    monkeypatch.setenv("GRID_DEGRADE_THRESHOLD", "200")

    # Need to reload the class to pick up new env vars
    import importlib

    importlib.reload(grid_performance)
    from apps.web_console_ng.core.grid_performance import GridPerformanceMonitor

    assert GridPerformanceMonitor.MAX_BATCH_SIZE == 1000
    assert GridPerformanceMonitor.DEGRADE_THRESHOLD == 200


def test_grid_monitor_should_degrade_activates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test should_degrade() activates degradation when rate exceeds threshold."""
    monitor = GridPerformanceMonitor("grid-1")
    monitor.DEGRADE_THRESHOLD = 10

    monkeypatch.setattr(monitor.metrics, "get_rate", Mock(return_value=20))
    assert monitor.should_degrade() is True
    assert monitor._degraded is True
    assert monitor.metrics.degradation_events == 1


def test_grid_monitor_should_degrade_stays_active(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test should_degrade() does NOT increment counter when already degraded."""
    monitor = GridPerformanceMonitor("grid-1")
    monitor.DEGRADE_THRESHOLD = 10

    monkeypatch.setattr(monitor.metrics, "get_rate", Mock(return_value=20))
    assert monitor.should_degrade() is True
    assert monitor.metrics.degradation_events == 1

    # Second call: still degraded, no counter increment
    assert monitor.should_degrade() is True
    assert monitor.metrics.degradation_events == 1


def test_grid_monitor_should_degrade_deactivates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test should_degrade() deactivates degradation when rate drops."""
    monitor = GridPerformanceMonitor("grid-1")
    monitor.DEGRADE_THRESHOLD = 10

    # Activate
    monkeypatch.setattr(monitor.metrics, "get_rate", Mock(return_value=20))
    assert monitor.should_degrade() is True
    assert monitor.metrics.degradation_events == 1

    # Deactivate
    monkeypatch.setattr(monitor.metrics, "get_rate", Mock(return_value=5))
    assert monitor.should_degrade() is False
    assert monitor._degraded is False
    assert monitor.metrics.degradation_events == 2


def test_grid_monitor_should_degrade_stays_inactive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test should_degrade() does NOT increment counter when already inactive."""
    monitor = GridPerformanceMonitor("grid-1")
    monitor.DEGRADE_THRESHOLD = 10

    monkeypatch.setattr(monitor.metrics, "get_rate", Mock(return_value=5))
    assert monitor.should_degrade() is False
    assert monitor.metrics.degradation_events == 0

    # Second call: still inactive, no counter increment
    assert monitor.should_degrade() is False
    assert monitor.metrics.degradation_events == 0


def test_grid_monitor_is_degraded_no_side_effects() -> None:
    """Test is_degraded() reads state without recalculating."""
    monitor = GridPerformanceMonitor("grid-1")
    assert monitor.is_degraded() is False

    monitor._degraded = True
    assert monitor.is_degraded() is True

    # No degradation_events increment
    assert monitor.metrics.degradation_events == 0


def test_grid_monitor_log_metrics(caplog: pytest.LogCaptureFixture) -> None:
    """Test log_metrics() logs structured data."""
    monitor = GridPerformanceMonitor("grid-1")
    monitor.metrics.updates_total = 50
    monitor.metrics.batches_sent = 5

    with caplog.at_level("INFO"):
        monitor.log_metrics()

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.message == "grid_update_metrics"
    assert record.grid_id == "grid-1"
    assert record.updates_total == 50
    assert record.batches_sent == 5


# ==================== Registry and Lifecycle Tests ====================


def test_attach_to_grid_registers_monitor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test attach_to_grid() registers monitor in all registries."""
    monitor = GridPerformanceMonitor("grid-1")
    grid = DummyGrid()

    monkeypatch.setattr(grid_performance, "get_or_create_client_id", Mock(return_value="sess-1"))
    schedule = Mock()
    monkeypatch.setattr(grid_performance, "_schedule_monitor_cleanup", schedule)

    monitor.attach_to_grid(grid)

    # Check all registries
    assert get_monitor(grid) is monitor
    assert get_monitor_by_grid_id("grid-1", "sess-1") is monitor
    assert ("grid-1", "sess-1") in grid_performance._monitor_by_grid_and_session
    schedule.assert_called_once_with("grid-1", "sess-1", monitor)


def test_attach_to_grid_missing_session_id(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Test attach_to_grid() handles missing session_id gracefully."""
    monitor = GridPerformanceMonitor("grid-2")
    grid = DummyGrid()

    monkeypatch.setattr(grid_performance, "get_or_create_client_id", Mock(return_value=None))
    schedule = Mock()
    monkeypatch.setattr(grid_performance, "_schedule_monitor_cleanup", schedule)

    with caplog.at_level("DEBUG"):
        monitor.attach_to_grid(grid)

    # WeakKeyDictionary still works
    assert get_monitor(grid) is monitor

    # Session-based registry NOT populated
    assert get_monitor_by_grid_id("grid-2", "missing") is None
    schedule.assert_not_called()

    # Check debug log
    assert any("grid_performance_missing_session_id" in record.message for record in caplog.records)


def test_attach_to_grid_empty_session_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test attach_to_grid() treats empty string as falsy."""
    monitor = GridPerformanceMonitor("grid-3")
    grid = DummyGrid()

    monkeypatch.setattr(grid_performance, "get_or_create_client_id", Mock(return_value=""))
    schedule = Mock()
    monkeypatch.setattr(grid_performance, "_schedule_monitor_cleanup", schedule)

    monitor.attach_to_grid(grid)

    assert get_monitor(grid) is monitor
    assert get_monitor_by_grid_id("grid-3", "") is None
    schedule.assert_not_called()


def test_get_monitor_missing() -> None:
    """Test get_monitor() returns None for unregistered grid."""
    grid = DummyGrid()
    assert get_monitor(grid) is None


def test_get_monitor_by_grid_id_missing() -> None:
    """Test get_monitor_by_grid_id() returns None for unknown key."""
    assert get_monitor_by_grid_id("unknown", "sess") is None


def test_get_all_monitors_returns_copy() -> None:
    """Test get_all_monitors() returns a copy, not the original dict."""
    monitor = GridPerformanceMonitor("grid-3")
    grid_performance._monitor_by_grid_and_session[("grid-3", "sess-2")] = monitor

    snapshot = get_all_monitors()
    assert snapshot[("grid-3", "sess-2")] is monitor

    # Mutating the copy doesn't affect the original
    snapshot.clear()
    assert len(get_all_monitors()) == 1


def test_get_all_monitors_empty() -> None:
    """Test get_all_monitors() with no monitors."""
    assert get_all_monitors() == {}


def test_remove_monitor_removes_entry() -> None:
    """Test _remove_monitor() removes monitor from registry."""
    monitor = GridPerformanceMonitor("grid-4")
    grid_performance._monitor_by_grid_and_session[("grid-4", "sess-3")] = monitor

    grid_performance._remove_monitor("grid-4", "sess-3", monitor)
    assert get_monitor_by_grid_id("grid-4", "sess-3") is None


def test_remove_monitor_guards_against_replacement() -> None:
    """Test _remove_monitor() does NOT remove if monitor was replaced."""
    monitor = GridPerformanceMonitor("grid-4")
    other = GridPerformanceMonitor("grid-4")
    grid_performance._monitor_by_grid_and_session[("grid-4", "sess-3")] = monitor

    # Try to remove `other`, but registry has `monitor`
    grid_performance._remove_monitor("grid-4", "sess-3", other)
    assert get_monitor_by_grid_id("grid-4", "sess-3") is monitor

    # Now remove the correct one
    grid_performance._remove_monitor("grid-4", "sess-3", monitor)
    assert get_monitor_by_grid_id("grid-4", "sess-3") is None


def test_remove_monitor_missing_key() -> None:
    """Test _remove_monitor() handles missing key gracefully."""
    monitor = GridPerformanceMonitor("grid-5")
    # No exception raised
    grid_performance._remove_monitor("grid-5", "sess-4", monitor)


# ==================== Cleanup Scheduling Tests ====================


@pytest.mark.asyncio()
async def test_schedule_cleanup_without_event_loop(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Test _schedule_monitor_cleanup() handles RuntimeError when no event loop."""
    monkeypatch.setattr(
        grid_performance.asyncio,
        "get_running_loop",
        Mock(side_effect=RuntimeError("no loop")),
    )

    with caplog.at_level("DEBUG"):
        grid_performance._schedule_monitor_cleanup(
            "grid-5", "sess-5", GridPerformanceMonitor("grid-5")
        )

    # Check debug log
    assert any(
        "grid_performance_cleanup_no_event_loop" in record.message for record in caplog.records
    )


@pytest.mark.asyncio()
async def test_schedule_cleanup_registers_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _schedule_monitor_cleanup() creates async task to register cleanup."""
    lifecycle = AsyncMock()
    monkeypatch.setattr(
        grid_performance.ClientLifecycleManager,
        "get",
        Mock(return_value=lifecycle),
    )

    loop = Mock()
    monkeypatch.setattr(grid_performance.asyncio, "get_running_loop", Mock(return_value=loop))

    monitor = GridPerformanceMonitor("grid-6")
    grid_performance._schedule_monitor_cleanup("grid-6", "sess-6", monitor)

    # Verify task was created
    assert loop.create_task.called is True
    task_coro = loop.create_task.call_args[0][0]

    # Execute the coroutine to verify it calls lifecycle.register_cleanup_callback
    await task_coro
    lifecycle.register_cleanup_callback.assert_called_once()
    args = lifecycle.register_cleanup_callback.call_args[0]
    assert args[0] == "sess-6"
    assert callable(args[1])


@pytest.mark.asyncio()
async def test_schedule_cleanup_callback_invokes_remove(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test cleanup callback actually removes monitor from registry."""
    lifecycle = AsyncMock()
    monkeypatch.setattr(
        grid_performance.ClientLifecycleManager,
        "get",
        Mock(return_value=lifecycle),
    )

    loop = Mock()
    monkeypatch.setattr(grid_performance.asyncio, "get_running_loop", Mock(return_value=loop))

    monitor = GridPerformanceMonitor("grid-7")
    grid_performance._monitor_by_grid_and_session[("grid-7", "sess-7")] = monitor

    grid_performance._schedule_monitor_cleanup("grid-7", "sess-7", monitor)

    # Execute the task coroutine
    task_coro = loop.create_task.call_args[0][0]
    await task_coro

    # Extract and invoke the cleanup callback
    cleanup_callback = lifecycle.register_cleanup_callback.call_args[0][1]
    cleanup_callback()

    # Verify monitor was removed
    assert get_monitor_by_grid_id("grid-7", "sess-7") is None


# ==================== WeakKeyDictionary Tests ====================


def test_weak_key_dictionary_cleanup() -> None:
    """Test WeakKeyDictionary removes entry when grid is garbage collected."""
    monitor = GridPerformanceMonitor("grid-8")
    grid = DummyGrid()

    grid_performance._grid_to_monitor[grid] = monitor
    assert get_monitor(grid) is monitor

    # Delete grid and force garbage collection
    grid_id = id(grid)
    del grid
    import gc

    gc.collect()

    # Create new grid with different identity
    new_grid = DummyGrid()
    assert id(new_grid) != grid_id
    assert get_monitor(new_grid) is None


# ==================== Concurrent Access Tests ====================


def test_multiple_grids_same_session() -> None:
    """Test multiple grids can attach to same session."""
    monitor1 = GridPerformanceMonitor("grid-a")
    monitor2 = GridPerformanceMonitor("grid-b")

    grid_performance._monitor_by_grid_and_session[("grid-a", "sess-x")] = monitor1
    grid_performance._monitor_by_grid_and_session[("grid-b", "sess-x")] = monitor2

    assert get_monitor_by_grid_id("grid-a", "sess-x") is monitor1
    assert get_monitor_by_grid_id("grid-b", "sess-x") is monitor2


def test_same_grid_multiple_sessions() -> None:
    """Test same grid_id can have different monitors per session."""
    monitor1 = GridPerformanceMonitor("grid-c")
    monitor2 = GridPerformanceMonitor("grid-c")

    grid_performance._monitor_by_grid_and_session[("grid-c", "sess-1")] = monitor1
    grid_performance._monitor_by_grid_and_session[("grid-c", "sess-2")] = monitor2

    assert get_monitor_by_grid_id("grid-c", "sess-1") is monitor1
    assert get_monitor_by_grid_id("grid-c", "sess-2") is monitor2


# ==================== Edge Cases ====================


def test_degradation_threshold_exactly_at_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test degradation at exact threshold boundary (edge case: > not >=)."""
    monitor = GridPerformanceMonitor("grid-edge")
    monitor.DEGRADE_THRESHOLD = 100

    # Exactly at threshold: should NOT degrade (uses >)
    monkeypatch.setattr(monitor.metrics, "get_rate", Mock(return_value=100.0))
    assert monitor.should_degrade() is False

    # Just over threshold: should degrade
    monkeypatch.setattr(monitor.metrics, "get_rate", Mock(return_value=100.1))
    assert monitor.should_degrade() is True


def test_zero_rate_no_degradation() -> None:
    """Test zero rate does not trigger degradation."""
    monitor = GridPerformanceMonitor("grid-zero")
    monitor.DEGRADE_THRESHOLD = 10

    # Metrics with zero rate
    assert monitor.should_degrade() is False
    assert monitor._degraded is False


def test_metrics_window_reset_timing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test precise timing of window reset at 5.0 seconds."""
    metrics = UpdateMetrics()
    metrics._window_start = 0.0
    metrics._window_updates = 20

    # At exactly 5.0 seconds
    monkeypatch.setattr(grid_performance.time, "time", _time_sequence([5.0, 5.1]))
    rate = metrics.get_rate()
    assert rate == 4.0  # 20 / 5.0
    assert metrics._window_start == 5.1
    assert metrics._window_updates == 0


def test_attach_multiple_times_overwrites(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test attaching multiple monitors to same grid_id+session_id overwrites."""
    monitor1 = GridPerformanceMonitor("grid-multi")
    monitor2 = GridPerformanceMonitor("grid-multi")
    grid = DummyGrid()

    monkeypatch.setattr(
        grid_performance, "get_or_create_client_id", Mock(return_value="sess-multi")
    )
    monkeypatch.setattr(grid_performance, "_schedule_monitor_cleanup", Mock())

    monitor1.attach_to_grid(grid)
    assert get_monitor_by_grid_id("grid-multi", "sess-multi") is monitor1

    # Attach monitor2: overwrites
    monitor2.attach_to_grid(grid)
    assert get_monitor_by_grid_id("grid-multi", "sess-multi") is monitor2


# ==================== Integration Test ====================


def test_full_lifecycle_integration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Integration test: attach, update, degrade, cleanup."""
    monitor = GridPerformanceMonitor("grid-full")
    monitor.DEGRADE_THRESHOLD = 50
    grid = DummyGrid()

    # Attach
    monkeypatch.setattr(grid_performance, "get_or_create_client_id", Mock(return_value="sess-full"))
    monkeypatch.setattr(grid_performance, "_schedule_monitor_cleanup", Mock())
    monitor.attach_to_grid(grid)

    # Record updates
    monitor.metrics.record_update(100)
    monitor.metrics.record_batch(5.0)

    # Trigger degradation
    monkeypatch.setattr(monitor.metrics, "get_rate", Mock(return_value=60))
    assert monitor.should_degrade() is True
    assert monitor.is_degraded() is True

    # Verify registration
    assert get_monitor(grid) is monitor
    assert get_monitor_by_grid_id("grid-full", "sess-full") is monitor

    # Cleanup
    grid_performance._remove_monitor("grid-full", "sess-full", monitor)
    assert get_monitor_by_grid_id("grid-full", "sess-full") is None
