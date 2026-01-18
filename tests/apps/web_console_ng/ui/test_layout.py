"""Tests for shared layout helpers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from apps.web_console_ng.ui import layout as layout_module


class _DummyElement:
    def __init__(self, text: str | None = None) -> None:
        self.text = text
        self.classes_calls: list[dict[str, Any]] = []
        self.tooltip_text: str | None = None
        self.disabled = False

    def __enter__(self) -> _DummyElement:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def classes(self, value: str | None = None, **kwargs: Any) -> _DummyElement:
        self.classes_calls.append({"value": value, **kwargs})
        return self

    def props(self, *_args: Any, **_kwargs: Any) -> _DummyElement:
        return self

    def tooltip(self, text: str) -> None:
        self.tooltip_text = text

    def set_text(self, text: str) -> None:
        self.text = text

    def disable(self) -> None:
        self.disabled = True

    def enable(self) -> None:
        self.disabled = False


class _DummyTimer:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _DummyUI:
    def __init__(self) -> None:
        self.head_html: list[str] = []
        self.body_html: list[str] = []

    def left_drawer(self, *_args: Any, **_kwargs: Any) -> _DummyElement:
        return _DummyElement()

    def column(self, *_args: Any, **_kwargs: Any) -> _DummyElement:
        return _DummyElement()

    def row(self, *_args: Any, **_kwargs: Any) -> _DummyElement:
        return _DummyElement()

    def header(self, *_args: Any, **_kwargs: Any) -> _DummyElement:
        return _DummyElement()

    def link(self, *_args: Any, **_kwargs: Any) -> _DummyElement:
        return _DummyElement()

    def label(self, *_args: Any, **_kwargs: Any) -> _DummyElement:
        return _DummyElement()

    def icon(self, *_args: Any, **_kwargs: Any) -> _DummyElement:
        return _DummyElement()

    def button(self, *_args: Any, **_kwargs: Any) -> _DummyElement:
        return _DummyElement()

    def badge(self, *_args: Any, **_kwargs: Any) -> _DummyElement:
        return _DummyElement()

    def space(self) -> None:
        return None

    def timer(self, *_args: Any, **_kwargs: Any) -> _DummyTimer:
        return _DummyTimer()

    def notify(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def add_head_html(self, html: str) -> None:
        self.head_html.append(html)

    def add_body_html(self, html: str) -> None:
        self.body_html.append(html)

    def run_javascript(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _DummyMarketClock:
    def __init__(self, exchanges: list[str] | None = None) -> None:
        self.updated = False

    def update(self, *, force: bool = False) -> None:
        self.updated = True


class _DummyStatusBar:
    def __init__(self) -> None:
        self.state: str | None = None

    def update_state(self, state: str) -> None:
        self.state = state


class _DummyHeaderMetrics:
    def __init__(self) -> None:
        self.stale_marked = False

    async def update(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def is_stale(self) -> bool:
        return True

    def mark_stale(self) -> None:
        self.stale_marked = True


class _DummyLatencyMonitor:
    def __init__(self) -> None:
        self.closed = False

    async def measure(self) -> float | None:
        return None

    def format_display(self) -> str:
        return "--"

    def format_tooltip(self) -> str:
        return "API Latency: --"

    def get_status_color_class(self) -> str:
        return "bg-gray-500 text-white"

    async def close(self) -> None:
        self.closed = True


class _DummyConnectionMonitor:
    def __init__(self) -> None:
        self.reconnects = 0

    def should_attempt(self) -> bool:
        return False

    def start_reconnect(self) -> None:
        self.reconnects += 1

    def record_success(self) -> None:
        return None

    def record_failure(self) -> None:
        return None

    def record_latency(self, _latency_ms: float) -> None:
        return None

    def get_connection_state(self) -> Any:
        class _State:
            value = "connected"

        return _State()

    def is_read_only(self) -> bool:
        return False

    def get_badge_text(self) -> str:
        return "Connected"

    def get_badge_class(self) -> str:
        return "bg-green-500 text-white"


class _DummyLifecycleManager:
    def __init__(self) -> None:
        self.callbacks: list[Any] = []

    async def register_cleanup_callback(self, _client_id: str, callback: Any) -> None:
        self.callbacks.append(callback)


class _DummyClient:
    async def fetch_kill_switch_status(self, *_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"state": "DISENGAGED"}

    async def fetch_circuit_breaker_status(self, *_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"state": "OPEN"}


async def _run_layout(monkeypatch: pytest.MonkeyPatch, *, current_path: str) -> tuple[_DummyUI, _DummyLifecycleManager]:
    dummy_ui = _DummyUI()
    dummy_app = SimpleNamespace(
        storage=SimpleNamespace(user={}, request=SimpleNamespace(url=SimpleNamespace(path=current_path)))
    )
    lifecycle_manager = _DummyLifecycleManager()

    monkeypatch.setattr(layout_module, "ui", dummy_ui)
    monkeypatch.setattr(layout_module, "app", dummy_app)
    monkeypatch.setattr(layout_module, "enable_dark_mode", lambda: None)
    monkeypatch.setattr(layout_module, "get_current_user", lambda: {"role": "admin", "username": "user", "user_id": "u1", "strategies": []})
    monkeypatch.setattr(layout_module, "MarketClock", _DummyMarketClock)
    monkeypatch.setattr(layout_module, "StatusBar", _DummyStatusBar)
    monkeypatch.setattr(layout_module, "HeaderMetrics", _DummyHeaderMetrics)
    monkeypatch.setattr(layout_module, "LatencyMonitor", _DummyLatencyMonitor)
    monkeypatch.setattr(layout_module, "ConnectionMonitor", _DummyConnectionMonitor)
    class _LifecycleWrapper:
        @classmethod
        def get(cls) -> _DummyLifecycleManager:
            return lifecycle_manager

    monkeypatch.setattr(layout_module, "ClientLifecycleManager", _LifecycleWrapper)
    monkeypatch.setattr(layout_module, "get_or_create_client_id", lambda: "client-1")
    monkeypatch.setattr(layout_module, "get_all_monitors", lambda: {})
    monkeypatch.setattr(layout_module.AsyncTradingClient, "get", classmethod(lambda cls: _DummyClient()))

    async def _page() -> None:
        return None

    wrapped = layout_module.main_layout(_page)
    await wrapped()
    return dummy_ui, lifecycle_manager


@pytest.mark.asyncio()
async def test_layout_injects_assets_and_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRID_DEGRADE_THRESHOLD", "150")
    monkeypatch.setenv("GRID_DEBUG", "true")

    dummy_ui, _ = await _run_layout(monkeypatch, current_path="/")

    head_html = "\n".join(dummy_ui.head_html)
    assert "/static/js/trading_state_listener.js" in head_html
    assert "/static/js/grid_throttle.js" in head_html
    assert "/static/js/grid_state_manager.js" in head_html
    assert "/static/css/density.css" in head_html
    assert "/static/css/custom.css" in head_html

    body_html = "\n".join(dummy_ui.body_html)
    assert "gridDegradeThreshold" in body_html
    assert "150" in body_html
    assert "gridDebug" in body_html
    assert "true" in body_html


@pytest.mark.asyncio()
async def test_layout_sets_current_path_and_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy_ui, lifecycle_manager = await _run_layout(monkeypatch, current_path="/risk")

    assert layout_module.app.storage.user["current_path"] == "/risk"
    assert len(lifecycle_manager.callbacks) == 3
    assert dummy_ui.head_html
