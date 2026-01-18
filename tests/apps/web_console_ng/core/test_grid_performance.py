from __future__ import annotations

import asyncio
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
    pass


@pytest.fixture(autouse=True)
def reset_registries() -> None:
    grid_performance._monitor_by_grid_and_session.clear()
    grid_performance._grid_to_monitor.clear()


def _time_sequence(values: list[float]):
    iterator = iter(values)

    def _now() -> float:
        return next(iterator)

    return _now


def test_update_metrics_rate_and_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    metrics = UpdateMetrics()
    metrics._window_start = 0.0
    metrics._window_updates = 10

    monkeypatch.setattr(grid_performance.time, "time", _time_sequence([2.0]))
    assert metrics.get_rate() == 5.0

    metrics._window_start = 0.0
    metrics._window_updates = 10
    monkeypatch.setattr(grid_performance.time, "time", _time_sequence([5.0, 6.0]))
    rate = metrics.get_rate()
    assert rate == 2.0
    assert metrics._window_updates == 0
    assert metrics._window_start == 6.0


def test_update_metrics_to_dict_rounding(monkeypatch: pytest.MonkeyPatch) -> None:
    metrics = UpdateMetrics()
    metrics._window_start = 0.0
    metrics._window_updates = 9
    metrics.record_batch(1.234)

    monkeypatch.setattr(grid_performance.time, "time", _time_sequence([3.0]))
    payload = metrics.to_dict()

    assert payload["batches_sent"] == 1
    assert payload["last_batch_time_ms"] == 1.23
    assert payload["current_rate"] == 3.0


def test_grid_monitor_degradation_toggle(monkeypatch: pytest.MonkeyPatch) -> None:
    monitor = GridPerformanceMonitor("grid-1")
    monitor.DEGRADE_THRESHOLD = 10

    monkeypatch.setattr(monitor.metrics, "get_rate", Mock(return_value=20))
    assert monitor.should_degrade() is True
    assert monitor.metrics.degradation_events == 1

    assert monitor.should_degrade() is True
    assert monitor.metrics.degradation_events == 1

    monkeypatch.setattr(monitor.metrics, "get_rate", Mock(return_value=5))
    assert monitor.should_degrade() is False
    assert monitor.metrics.degradation_events == 2


def test_attach_to_grid_registers_monitor(monkeypatch: pytest.MonkeyPatch) -> None:
    monitor = GridPerformanceMonitor("grid-1")
    grid = DummyGrid()

    monkeypatch.setattr(grid_performance, "get_or_create_client_id", Mock(return_value="sess-1"))
    schedule = Mock()
    monkeypatch.setattr(grid_performance, "_schedule_monitor_cleanup", schedule)

    monitor.attach_to_grid(grid)

    assert get_monitor(grid) is monitor
    assert get_monitor_by_grid_id("grid-1", "sess-1") is monitor
    schedule.assert_called_once_with("grid-1", "sess-1", monitor)


def test_attach_to_grid_missing_session_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monitor = GridPerformanceMonitor("grid-2")
    grid = DummyGrid()

    monkeypatch.setattr(grid_performance, "get_or_create_client_id", Mock(return_value=None))
    schedule = Mock()
    monkeypatch.setattr(grid_performance, "_schedule_monitor_cleanup", schedule)

    monitor.attach_to_grid(grid)

    assert get_monitor(grid) is monitor
    assert get_monitor_by_grid_id("grid-2", "missing") is None
    schedule.assert_not_called()


def test_get_all_monitors_returns_copy() -> None:
    monitor = GridPerformanceMonitor("grid-3")
    grid_performance._monitor_by_grid_and_session[("grid-3", "sess-2")] = monitor

    snapshot = get_all_monitors()
    assert snapshot[("grid-3", "sess-2")] is monitor
    snapshot.clear()

    assert get_all_monitors() != {}


def test_remove_monitor_guarded() -> None:
    monitor = GridPerformanceMonitor("grid-4")
    other = GridPerformanceMonitor("grid-4")
    grid_performance._monitor_by_grid_and_session[("grid-4", "sess-3")] = monitor

    grid_performance._remove_monitor("grid-4", "sess-3", other)
    assert get_monitor_by_grid_id("grid-4", "sess-3") is monitor

    grid_performance._remove_monitor("grid-4", "sess-3", monitor)
    assert get_monitor_by_grid_id("grid-4", "sess-3") is None


@pytest.mark.asyncio()
async def test_schedule_cleanup_without_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        grid_performance.asyncio,
        "get_running_loop",
        Mock(side_effect=RuntimeError("no loop")),
    )

    grid_performance._schedule_monitor_cleanup("grid-5", "sess-5", GridPerformanceMonitor("grid-5"))


@pytest.mark.asyncio()
async def test_schedule_cleanup_registers_task(monkeypatch: pytest.MonkeyPatch) -> None:
    lifecycle = AsyncMock()
    monkeypatch.setattr(
        grid_performance.ClientLifecycleManager,
        "get",
        Mock(return_value=lifecycle),
    )

    loop = Mock()
    monkeypatch.setattr(grid_performance.asyncio, "get_running_loop", Mock(return_value=loop))

    grid_performance._schedule_monitor_cleanup("grid-6", "sess-6", GridPerformanceMonitor("grid-6"))

    assert loop.create_task.called is True
