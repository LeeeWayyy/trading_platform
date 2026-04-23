"""Tests for Lightweight Charts loading and chart initialization wiring."""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.web_console_ng.components.price_chart import PriceChartComponent
from apps.web_console_ng.ui import lightweight_charts


def test_chart_init_js_resets_loading_promise_after_failure() -> None:
    """Browser loader should reset promise on failure so later inits can retry."""
    assert "window.__lwc_loading_promise = null;" in lightweight_charts.CHART_INIT_JS
    assert "throw loadError;" in lightweight_charts.CHART_INIT_JS


def test_chart_init_js_recreates_failed_script_nodes() -> None:
    """Retry path should remove failed script nodes to avoid hanging listeners."""
    assert "existing.dataset.failed === 'true'" in lightweight_charts.CHART_INIT_JS
    assert "existing.remove();" in lightweight_charts.CHART_INIT_JS
    assert "script.dataset.failed = 'true';" in lightweight_charts.CHART_INIT_JS
    assert "window.__lwc_load_script_once" in lightweight_charts.CHART_INIT_JS


def test_chart_init_js_uses_minimum_chart_dimensions() -> None:
    """Initialization should avoid ultra-small 1x1 chart bootstrap sizes."""
    assert "const MIN_CHART_WIDTH = 320;" in lightweight_charts.CHART_INIT_JS
    assert "const MIN_CHART_HEIGHT = 180;" in lightweight_charts.CHART_INIT_JS


def test_chart_init_js_stores_resize_observer_reference() -> None:
    """ResizeObserver should be attached to chart registry for disposal cleanup."""
    assert "resizeObserver: null" in lightweight_charts.CHART_INIT_JS
    assert "resizeObserver = resizeObserver" in lightweight_charts.CHART_INIT_JS


@pytest.mark.asyncio()
async def test_price_chart_uses_sync_timer_callback_for_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Price chart init timer callback should be sync and schedule async work."""
    chart = PriceChartComponent(trading_client=MagicMock())
    callbacks: list[object] = []
    tracked_timers: list[object] = []

    class _DummyTimer:
        def cancel(self) -> None:
            return None

    def fake_timer(_interval: float, callback: object, *, once: bool = False) -> _DummyTimer:
        assert once is True
        callbacks.append(callback)
        return _DummyTimer()

    ensure_chart_initialized = AsyncMock()

    monkeypatch.setattr("apps.web_console_ng.components.price_chart.ui.timer", fake_timer)
    monkeypatch.setattr(chart, "_ensure_chart_initialized", ensure_chart_initialized)
    monkeypatch.setattr(chart, "_start_realtime_staleness_monitor", lambda _tracker: None)

    await chart.initialize(timer_tracker=tracked_timers.append)

    assert len(callbacks) == 1
    assert len(tracked_timers) == 1
    init_callback = callbacks[0]
    assert not inspect.iscoroutinefunction(init_callback)

    assert callable(init_callback)
    init_callback()  # type: ignore[operator]
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    ensure_chart_initialized.assert_awaited_once()
