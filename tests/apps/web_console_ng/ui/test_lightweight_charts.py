"""Tests for Lightweight Charts loading and chart initialization wiring."""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.web_console_ng.components.price_chart import PriceChartComponent
from apps.web_console_ng.ui import lightweight_charts


@pytest.mark.asyncio()
async def test_loader_retries_after_failed_first_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loader should recover from a failed first attempt and allow retry."""
    lightweight_charts.LightweightChartsLoader.reset()
    load_attempts = 0

    async def fake_run_javascript(script: str, *_args: object, **_kwargs: object) -> object:
        nonlocal load_attempts
        if "window.__lwc_ready === true" in script:
            return True
        if "document.createElement('script')" in script:
            load_attempts += 1
            if load_attempts == 1:
                raise RuntimeError("first load attempt failed")
            return None
        return None

    async def fake_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(lightweight_charts.ui, "run_javascript", fake_run_javascript)
    monkeypatch.setattr(lightweight_charts.asyncio, "sleep", fake_sleep)

    with pytest.raises(RuntimeError, match="first load attempt failed"):
        await lightweight_charts.LightweightChartsLoader.ensure_loaded()

    assert lightweight_charts.LightweightChartsLoader._loaded is False
    assert lightweight_charts.LightweightChartsLoader._ready is False

    await lightweight_charts.LightweightChartsLoader.ensure_loaded()

    assert load_attempts == 2
    assert lightweight_charts.LightweightChartsLoader._ready is True


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
