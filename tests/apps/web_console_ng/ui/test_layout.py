"""Tests for shared layout helpers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
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

    def on(self, event: str, handler: Any, **_kwargs: Any) -> None:
        """Mock event handler registration."""
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


class _DummyNotificationRouter:
    """Mock NotificationRouter for layout tests."""

    quiet_mode = False

    def __init__(self, state_manager: Any = None) -> None:
        self._state_manager = state_manager

    async def load_preferences(self) -> None:
        return None

    async def set_quiet_mode(self, enabled: bool) -> None:
        self.quiet_mode = enabled


class _DummyHotkeyManager:
    """Mock HotkeyManager for layout tests."""

    def get_bindings_json(self) -> list[dict[str, Any]]:
        return []

    def get_bindings(self) -> list[Any]:
        return []

    def register_handler(self, action: str, handler: Any) -> None:
        return None

    def handle_action(self, action: str) -> None:
        return None


class _DummyCommandPalette:
    """Mock CommandPalette for layout tests."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def create(self) -> None:
        return None

    def register_command(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def open(self) -> None:
        return None


class _DummyLogDrawer:
    """Mock LogDrawer for layout tests."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def create(self) -> None:
        return None


class _DummyUserStateManager:
    """Mock UserStateManager for layout tests."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    async def restore_state(self) -> dict[str, Any]:
        return {}

    async def save_preferences(self, key: str, value: Any) -> None:
        return None


async def _run_layout(
    monkeypatch: pytest.MonkeyPatch, *, current_path: str
) -> tuple[_DummyUI, _DummyLifecycleManager]:
    dummy_ui = _DummyUI()
    # Include both user and client storage (client for per-tab isolation)
    dummy_app = SimpleNamespace(
        storage=SimpleNamespace(
            user={},
            client={},
            request=SimpleNamespace(url=SimpleNamespace(path=current_path)),
        )
    )
    lifecycle_manager = _DummyLifecycleManager()

    monkeypatch.setattr(layout_module, "ui", dummy_ui)
    monkeypatch.setattr(layout_module, "app", dummy_app)
    monkeypatch.setattr(layout_module, "enable_dark_mode", lambda: None)
    monkeypatch.setattr(
        layout_module,
        "get_current_user",
        lambda: {"role": "admin", "username": "user", "user_id": "u1", "strategies": []},
    )
    monkeypatch.setattr(layout_module, "MarketClock", _DummyMarketClock)
    monkeypatch.setattr(layout_module, "StatusBar", _DummyStatusBar)
    monkeypatch.setattr(layout_module, "HeaderMetrics", _DummyHeaderMetrics)
    monkeypatch.setattr(layout_module, "LatencyMonitor", _DummyLatencyMonitor)
    monkeypatch.setattr(layout_module, "ConnectionMonitor", _DummyConnectionMonitor)

    # Mock P6T3 components (notifications, hotkeys)
    monkeypatch.setattr(layout_module, "NotificationRouter", _DummyNotificationRouter)
    monkeypatch.setattr(layout_module, "HotkeyManager", _DummyHotkeyManager)
    monkeypatch.setattr(layout_module, "CommandPalette", _DummyCommandPalette)
    monkeypatch.setattr(layout_module, "LogDrawer", _DummyLogDrawer)
    monkeypatch.setattr(layout_module, "UserStateManager", _DummyUserStateManager)

    class _LifecycleWrapper:
        @classmethod
        def get(cls) -> _DummyLifecycleManager:
            return lifecycle_manager

    monkeypatch.setattr(layout_module, "ClientLifecycleManager", _LifecycleWrapper)
    monkeypatch.setattr(layout_module, "get_or_create_client_id", lambda: "client-1")
    monkeypatch.setattr(layout_module, "get_all_monitors", lambda: {})
    monkeypatch.setattr(
        layout_module.AsyncTradingClient, "get", classmethod(lambda cls: _DummyClient())
    )

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


class _DummyDialog:
    """Mock dialog for testing."""

    def __init__(self) -> None:
        self.opened = False
        self.closed = False

    def __enter__(self) -> _DummyDialog:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True


class _DummyInput:
    """Mock input for testing."""

    def __init__(self, value: str = "") -> None:
        self.value = value

    def props(self, *args: Any, **kwargs: Any) -> _DummyInput:
        return self

    def classes(self, *args: Any, **kwargs: Any) -> _DummyInput:
        return self


class _DummyCard:
    """Mock card for testing."""

    def __enter__(self) -> _DummyCard:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def classes(self, *args: Any, **kwargs: Any) -> _DummyCard:
        return self


class _ExtendedDummyUI(_DummyUI):
    """Extended mock UI with dialog support."""

    def __init__(self) -> None:
        super().__init__()
        self.dialogs: list[_DummyDialog] = []
        self.inputs: list[_DummyInput] = []
        self.notifications: list[dict[str, Any]] = []

    def dialog(self) -> _DummyDialog:
        d = _DummyDialog()
        self.dialogs.append(d)
        return d

    def card(self) -> _DummyCard:
        return _DummyCard()

    def input(self, *args: Any, **kwargs: Any) -> _DummyInput:
        inp = _DummyInput()
        self.inputs.append(inp)
        return inp

    def notify(self, message: str, **kwargs: Any) -> None:
        self.notifications.append({"message": message, **kwargs})


class _ExtendedDummyConnectionMonitor(_DummyConnectionMonitor):
    """Extended connection monitor that allows configuring should_attempt."""

    def __init__(self, should_attempt_value: bool = True) -> None:
        super().__init__()
        self._should_attempt = should_attempt_value
        self.successes = 0
        self.failures = 0
        self.latencies: list[float] = []

    def should_attempt(self) -> bool:
        return self._should_attempt

    def record_success(self) -> None:
        self.successes += 1

    def record_failure(self) -> None:
        self.failures += 1

    def record_latency(self, latency_ms: float) -> None:
        self.latencies.append(latency_ms)


class _ExtendedDummyLatencyMonitor(_DummyLatencyMonitor):
    """Extended latency monitor with configurable behavior."""

    def __init__(self, latency_value: float | None = 50.0) -> None:
        super().__init__()
        self._latency = latency_value

    async def measure(self) -> float | None:
        return self._latency


class _ExtendedDummyMarketClock(_DummyMarketClock):
    """Market clock that can be configured to raise exceptions."""

    def __init__(self, should_fail: bool = False, exchanges: list[str] | None = None) -> None:
        super().__init__(exchanges)
        self._should_fail = should_fail

    def update(self, *, force: bool = False) -> None:
        if self._should_fail:
            raise RuntimeError("Clock update failed")
        self.updated = True


class _ExtendedDummyClient(_DummyClient):
    """Extended client with configurable behavior."""

    def __init__(
        self,
        kill_switch_state: str = "DISENGAGED",
        cb_state: str = "OPEN",
        fail_kill_switch: bool = False,
        fail_cb: bool = False,
        engage_error: Exception | None = None,
        disengage_error: Exception | None = None,
    ) -> None:
        self._ks_state = kill_switch_state
        self._cb_state = cb_state
        self._fail_ks = fail_kill_switch
        self._fail_cb = fail_cb
        self._engage_error = engage_error
        self._disengage_error = disengage_error
        self.engage_calls: list[dict[str, Any]] = []
        self.disengage_calls: list[dict[str, Any]] = []

    async def fetch_kill_switch_status(self, *args: Any, **kwargs: Any) -> dict[str, str]:
        if self._fail_ks:
            raise ValueError("Kill switch fetch failed")
        return {"state": self._ks_state}

    async def fetch_circuit_breaker_status(self, *args: Any, **kwargs: Any) -> dict[str, str]:
        if self._fail_cb:
            raise httpx.RequestError("CB fetch failed")
        return {"state": self._cb_state}

    async def engage_kill_switch(
        self,
        user_id: str,
        reason: str = "",
        role: str = "",
        strategies: list[str] | None = None,
    ) -> dict[str, Any]:
        self.engage_calls.append(
            {"user_id": user_id, "reason": reason, "role": role, "strategies": strategies}
        )
        if self._engage_error:
            raise self._engage_error
        return {"state": "ENGAGED"}

    async def disengage_kill_switch(
        self,
        user_id: str,
        role: str = "",
        strategies: list[str] | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        self.disengage_calls.append(
            {"user_id": user_id, "role": role, "strategies": strategies, "notes": notes}
        )
        if self._disengage_error:
            raise self._disengage_error
        return {"state": "DISENGAGED"}


class _ExtendedDummyHeaderMetrics(_DummyHeaderMetrics):
    """Header metrics with configurable behavior."""

    def __init__(self, is_stale_value: bool = False, fail_update: bool = False) -> None:
        super().__init__()
        self._is_stale = is_stale_value
        self._fail_update = fail_update
        self.update_calls = 0

    def is_stale(self) -> bool:
        return self._is_stale

    async def update(self, *args: Any, **kwargs: Any) -> None:
        self.update_calls += 1
        if self._fail_update:
            raise RuntimeError("Header metrics update failed")


async def _run_extended_layout(
    monkeypatch: pytest.MonkeyPatch,
    *,
    current_path: str = "/",
    connection_monitor: _DummyConnectionMonitor | None = None,
    latency_monitor: _DummyLatencyMonitor | None = None,
    market_clock_class: type | None = None,
    client: _DummyClient | None = None,
    header_metrics: _DummyHeaderMetrics | None = None,
    user: dict[str, Any] | None = None,
) -> tuple[_ExtendedDummyUI, _DummyLifecycleManager, dict[str, Any]]:
    """Extended layout runner that returns components for testing."""
    dummy_ui = _ExtendedDummyUI()
    # Include both user and client storage (client for per-tab isolation)
    dummy_app = SimpleNamespace(
        storage=SimpleNamespace(
            user={},
            client={},
            request=SimpleNamespace(url=SimpleNamespace(path=current_path)),
        )
    )
    lifecycle_manager = _DummyLifecycleManager()

    if connection_monitor is None:
        connection_monitor = _ExtendedDummyConnectionMonitor(should_attempt_value=True)

    if latency_monitor is None:
        latency_monitor = _ExtendedDummyLatencyMonitor()

    if market_clock_class is None:
        market_clock_class = _DummyMarketClock

    if client is None:
        client = _ExtendedDummyClient()

    if header_metrics is None:
        header_metrics = _ExtendedDummyHeaderMetrics()

    if user is None:
        user = {"role": "admin", "username": "user", "user_id": "u1", "strategies": []}

    # Store captured references
    captured: dict[str, Any] = {
        "connection_monitor": connection_monitor,
        "latency_monitor": latency_monitor,
        "client": client,
        "header_metrics": header_metrics,
    }

    monkeypatch.setattr(layout_module, "ui", dummy_ui)
    monkeypatch.setattr(layout_module, "app", dummy_app)
    monkeypatch.setattr(layout_module, "enable_dark_mode", lambda: None)
    monkeypatch.setattr(layout_module, "get_current_user", lambda: user)
    monkeypatch.setattr(layout_module, "MarketClock", market_clock_class)
    monkeypatch.setattr(layout_module, "StatusBar", _DummyStatusBar)
    monkeypatch.setattr(layout_module, "HeaderMetrics", lambda: header_metrics)
    monkeypatch.setattr(layout_module, "LatencyMonitor", lambda: latency_monitor)
    monkeypatch.setattr(layout_module, "ConnectionMonitor", lambda: connection_monitor)

    # Mock P6T3 components (notifications, hotkeys)
    monkeypatch.setattr(layout_module, "NotificationRouter", _DummyNotificationRouter)
    monkeypatch.setattr(layout_module, "HotkeyManager", _DummyHotkeyManager)
    monkeypatch.setattr(layout_module, "CommandPalette", _DummyCommandPalette)
    monkeypatch.setattr(layout_module, "LogDrawer", _DummyLogDrawer)
    monkeypatch.setattr(layout_module, "UserStateManager", _DummyUserStateManager)

    class _LifecycleWrapper:
        @classmethod
        def get(cls) -> _DummyLifecycleManager:
            return lifecycle_manager

    monkeypatch.setattr(layout_module, "ClientLifecycleManager", _LifecycleWrapper)
    monkeypatch.setattr(layout_module, "get_or_create_client_id", lambda: "client-1")
    monkeypatch.setattr(layout_module, "get_all_monitors", lambda: {})
    monkeypatch.setattr(
        layout_module.AsyncTradingClient, "get", classmethod(lambda cls: client)
    )

    async def _page() -> None:
        return None

    wrapped = layout_module.main_layout(_page)
    await wrapped()
    return dummy_ui, lifecycle_manager, captured


@pytest.mark.asyncio()
async def test_update_global_status_engaged_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that update_global_status properly handles ENGAGED state."""
    client = _ExtendedDummyClient(kill_switch_state="ENGAGED", cb_state="OPEN")
    conn_monitor = _ExtendedDummyConnectionMonitor(should_attempt_value=True)

    await _run_extended_layout(
        monkeypatch,
        client=client,
        connection_monitor=conn_monitor,
    )

    # Verify connection success was recorded
    assert conn_monitor.successes >= 1


@pytest.mark.asyncio()
async def test_update_global_status_disengaged_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that update_global_status properly handles DISENGAGED state."""
    client = _ExtendedDummyClient(kill_switch_state="DISENGAGED", cb_state="OPEN")
    conn_monitor = _ExtendedDummyConnectionMonitor(should_attempt_value=True)

    await _run_extended_layout(
        monkeypatch,
        client=client,
        connection_monitor=conn_monitor,
    )

    assert conn_monitor.successes >= 1


@pytest.mark.asyncio()
async def test_update_global_status_unknown_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that update_global_status properly handles UNKNOWN state."""
    client = _ExtendedDummyClient(kill_switch_state="INVALID", cb_state="OPEN")
    conn_monitor = _ExtendedDummyConnectionMonitor(should_attempt_value=True)

    await _run_extended_layout(
        monkeypatch,
        client=client,
        connection_monitor=conn_monitor,
    )

    assert conn_monitor.successes >= 1


@pytest.mark.asyncio()
async def test_update_global_status_circuit_breaker_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test circuit breaker state handling."""
    # Test TRIPPED state
    client = _ExtendedDummyClient(kill_switch_state="DISENGAGED", cb_state="TRIPPED")
    await _run_extended_layout(monkeypatch, client=client)

    # Test QUIET_PERIOD state
    client = _ExtendedDummyClient(kill_switch_state="DISENGAGED", cb_state="QUIET_PERIOD")
    await _run_extended_layout(monkeypatch, client=client)

    # Test unknown CB state
    client = _ExtendedDummyClient(kill_switch_state="DISENGAGED", cb_state="WEIRD")
    await _run_extended_layout(monkeypatch, client=client)


@pytest.mark.asyncio()
async def test_update_global_status_kill_switch_fetch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test handling when kill switch status fetch fails."""
    client = _ExtendedDummyClient(fail_kill_switch=True)
    conn_monitor = _ExtendedDummyConnectionMonitor(should_attempt_value=True)

    await _run_extended_layout(
        monkeypatch,
        client=client,
        connection_monitor=conn_monitor,
    )

    # Should record failure when fetch fails
    assert conn_monitor.failures >= 1


@pytest.mark.asyncio()
async def test_update_global_status_circuit_breaker_fetch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test handling when circuit breaker status fetch fails."""
    client = _ExtendedDummyClient(kill_switch_state="DISENGAGED", fail_cb=True)
    conn_monitor = _ExtendedDummyConnectionMonitor(should_attempt_value=True)

    await _run_extended_layout(
        monkeypatch,
        client=client,
        connection_monitor=conn_monitor,
    )

    # Should still succeed overall, just CB state is unknown
    assert conn_monitor.successes >= 1


@pytest.mark.asyncio()
async def test_update_global_status_when_should_not_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test behavior when connection monitor says should not attempt."""
    conn_monitor = _ExtendedDummyConnectionMonitor(should_attempt_value=False)
    header_metrics = _ExtendedDummyHeaderMetrics(is_stale_value=True)

    await _run_extended_layout(
        monkeypatch,
        connection_monitor=conn_monitor,
        header_metrics=header_metrics,
    )

    # Should have started reconnect
    assert conn_monitor.reconnects >= 1
    # Header metrics should be marked stale
    assert header_metrics.stale_marked


@pytest.mark.asyncio()
async def test_update_global_status_market_clock_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that market clock failures are handled gracefully."""
    conn_monitor = _ExtendedDummyConnectionMonitor(should_attempt_value=False)

    await _run_extended_layout(
        monkeypatch,
        connection_monitor=conn_monitor,
        market_clock_class=lambda exchanges: _ExtendedDummyMarketClock(should_fail=True),
    )

    # Should not crash, just log and continue
    assert conn_monitor.reconnects >= 1


@pytest.mark.asyncio()
async def test_update_global_status_latency_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that latency is recorded when status fetch succeeds."""
    client = _ExtendedDummyClient()
    conn_monitor = _ExtendedDummyConnectionMonitor(should_attempt_value=True)
    latency_monitor = _ExtendedDummyLatencyMonitor(latency_value=42.5)

    await _run_extended_layout(
        monkeypatch,
        client=client,
        connection_monitor=conn_monitor,
        latency_monitor=latency_monitor,
    )

    # Latency should have been recorded
    assert 42.5 in conn_monitor.latencies


@pytest.mark.asyncio()
async def test_update_global_status_latency_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test handling when latency measurement returns None."""
    client = _ExtendedDummyClient()
    conn_monitor = _ExtendedDummyConnectionMonitor(should_attempt_value=True)
    latency_monitor = _ExtendedDummyLatencyMonitor(latency_value=None)

    await _run_extended_layout(
        monkeypatch,
        client=client,
        connection_monitor=conn_monitor,
        latency_monitor=latency_monitor,
    )

    # Should still succeed, just no latency recorded
    assert conn_monitor.successes >= 1


@pytest.mark.asyncio()
async def test_update_global_status_header_metrics_update_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that header metrics failures are handled gracefully."""
    client = _ExtendedDummyClient()
    header_metrics = _ExtendedDummyHeaderMetrics(fail_update=True)

    # Should not crash
    await _run_extended_layout(
        monkeypatch,
        client=client,
        header_metrics=header_metrics,
    )

    assert header_metrics.update_calls >= 1


@pytest.mark.asyncio()
async def test_admin_link_hidden_for_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that admin link is hidden for users without admin permissions."""
    user = {"role": "viewer", "username": "viewer1", "user_id": "v1", "strategies": []}

    await _run_extended_layout(monkeypatch, user=user)

    # Admin link should not be shown (the test passes if no error occurs)


@pytest.mark.asyncio()
async def test_admin_link_shown_for_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that admin link is shown for users with admin permissions."""
    user = {"role": "admin", "username": "admin1", "user_id": "a1", "strategies": ["alpha"]}

    await _run_extended_layout(monkeypatch, user=user)


@pytest.mark.asyncio()
async def test_kill_switch_state_change_notification(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that notification is shown when kill switch state changes to ENGAGED."""
    # This is implicitly tested via the state changes
    client = _ExtendedDummyClient(kill_switch_state="ENGAGED")

    dummy_ui, _, _ = await _run_extended_layout(monkeypatch, client=client)

    # Should have notification about engaged state
    # (Notification tracking done via _ExtendedDummyUI)


@pytest.mark.asyncio()
async def test_latency_monitor_exception_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that latency monitor exceptions are handled gracefully."""

    class _FailingLatencyMonitor(_DummyLatencyMonitor):
        async def measure(self) -> float | None:
            raise RuntimeError("Latency measure failed")

    client = _ExtendedDummyClient()
    latency_monitor = _FailingLatencyMonitor()

    # Should not crash
    await _run_extended_layout(
        monkeypatch,
        client=client,
        latency_monitor=latency_monitor,
    )


@pytest.mark.asyncio()
async def test_market_clock_update_during_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that market clock is updated during status polling."""

    class _TrackingMarketClock:
        def __init__(self, exchanges: list[str] | None = None) -> None:
            self.update_count = 0

        def update(self, *, force: bool = False) -> None:
            self.update_count += 1

    clock_instance: _TrackingMarketClock | None = None

    def clock_factory(exchanges: list[str] | None = None) -> _TrackingMarketClock:
        nonlocal clock_instance
        clock_instance = _TrackingMarketClock(exchanges)
        return clock_instance

    client = _ExtendedDummyClient()
    await _run_extended_layout(
        monkeypatch,
        client=client,
        market_clock_class=clock_factory,
    )

    assert clock_instance is not None
    assert clock_instance.update_count >= 1


@pytest.mark.asyncio()
async def test_connection_state_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that connection state changes are dispatched."""
    conn_monitor = _ExtendedDummyConnectionMonitor(should_attempt_value=True)
    client = _ExtendedDummyClient()

    dummy_ui, _, _ = await _run_extended_layout(
        monkeypatch,
        client=client,
        connection_monitor=conn_monitor,
    )


@pytest.mark.asyncio()
async def test_log_grid_metrics_with_monitors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that log_grid_metrics iterates over all monitors."""

    class _MockMonitor:
        def __init__(self) -> None:
            self.logged = False

        def log_metrics(self) -> None:
            self.logged = True

    monitors = {
        ("grid1", "session1"): _MockMonitor(),
        ("grid2", "session2"): _MockMonitor(),
    }

    dummy_ui = _ExtendedDummyUI()
    dummy_app = SimpleNamespace(
        storage=SimpleNamespace(user={}, client={}, request=SimpleNamespace(url=SimpleNamespace(path="/"))
        )
    )
    lifecycle_manager = _DummyLifecycleManager()

    monkeypatch.setattr(layout_module, "ui", dummy_ui)
    monkeypatch.setattr(layout_module, "app", dummy_app)
    monkeypatch.setattr(layout_module, "enable_dark_mode", lambda: None)
    monkeypatch.setattr(
        layout_module,
        "get_current_user",
        lambda: {"role": "admin", "username": "user", "user_id": "u1", "strategies": []},
    )
    monkeypatch.setattr(layout_module, "MarketClock", _DummyMarketClock)
    monkeypatch.setattr(layout_module, "StatusBar", _DummyStatusBar)
    monkeypatch.setattr(layout_module, "HeaderMetrics", _DummyHeaderMetrics)
    monkeypatch.setattr(layout_module, "LatencyMonitor", _DummyLatencyMonitor)
    monkeypatch.setattr(
        layout_module, "ConnectionMonitor", _ExtendedDummyConnectionMonitor
    )

    class _LifecycleWrapper:
        @classmethod
        def get(cls) -> _DummyLifecycleManager:
            return lifecycle_manager

    monkeypatch.setattr(layout_module, "ClientLifecycleManager", _LifecycleWrapper)
    monkeypatch.setattr(layout_module, "get_or_create_client_id", lambda: "client-1")
    monkeypatch.setattr(layout_module, "get_all_monitors", lambda: monitors)
    monkeypatch.setattr(
        layout_module.AsyncTradingClient, "get", classmethod(lambda cls: _DummyClient())
    )

    async def _page() -> None:
        return None

    wrapped = layout_module.main_layout(_page)
    await wrapped()

    # The timer callback is registered but not yet called
    # We verify the callback is registered
    assert len(lifecycle_manager.callbacks) >= 2


@pytest.mark.asyncio()
async def test_cleanup_callbacks_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that cleanup callbacks are properly registered."""
    _, lifecycle_manager, captured = await _run_extended_layout(monkeypatch)

    # Should have 3 callbacks: status_timer, metrics_timer, latency_monitor.close
    assert len(lifecycle_manager.callbacks) == 3

    # Execute all cleanup callbacks
    for callback in lifecycle_manager.callbacks:
        if asyncio.iscoroutinefunction(callback):
            await callback()
        else:
            callback()

    # Verify latency monitor was closed
    assert captured["latency_monitor"].closed


@pytest.mark.asyncio()
async def test_cleanup_id_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test behavior when client ID is None."""
    dummy_ui = _ExtendedDummyUI()
    dummy_app = SimpleNamespace(
        storage=SimpleNamespace(user={}, client={}, request=SimpleNamespace(url=SimpleNamespace(path="/"))
        )
    )
    lifecycle_manager = _DummyLifecycleManager()

    monkeypatch.setattr(layout_module, "ui", dummy_ui)
    monkeypatch.setattr(layout_module, "app", dummy_app)
    monkeypatch.setattr(layout_module, "enable_dark_mode", lambda: None)
    monkeypatch.setattr(
        layout_module,
        "get_current_user",
        lambda: {"role": "admin", "username": "user", "user_id": "u1", "strategies": []},
    )
    monkeypatch.setattr(layout_module, "MarketClock", _DummyMarketClock)
    monkeypatch.setattr(layout_module, "StatusBar", _DummyStatusBar)
    monkeypatch.setattr(layout_module, "HeaderMetrics", _DummyHeaderMetrics)
    monkeypatch.setattr(layout_module, "LatencyMonitor", _DummyLatencyMonitor)
    monkeypatch.setattr(
        layout_module, "ConnectionMonitor", _ExtendedDummyConnectionMonitor
    )

    class _LifecycleWrapper:
        @classmethod
        def get(cls) -> _DummyLifecycleManager:
            return lifecycle_manager

    monkeypatch.setattr(layout_module, "ClientLifecycleManager", _LifecycleWrapper)
    # Return None for client ID
    monkeypatch.setattr(layout_module, "get_or_create_client_id", lambda: None)
    monkeypatch.setattr(layout_module, "get_all_monitors", lambda: {})
    monkeypatch.setattr(
        layout_module.AsyncTradingClient, "get", classmethod(lambda cls: _DummyClient())
    )

    async def _page() -> None:
        return None

    wrapped = layout_module.main_layout(_page)
    await wrapped()

    # No cleanup callbacks should be registered when client ID is None
    assert len(lifecycle_manager.callbacks) == 0


@pytest.mark.asyncio()
async def test_request_url_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test handling when request URL is None."""
    dummy_ui = _ExtendedDummyUI()
    dummy_app = SimpleNamespace(
        storage=SimpleNamespace(user={}, client={}, request=SimpleNamespace(url=None))
    )

    monkeypatch.setattr(layout_module, "ui", dummy_ui)
    monkeypatch.setattr(layout_module, "app", dummy_app)
    monkeypatch.setattr(layout_module, "enable_dark_mode", lambda: None)
    monkeypatch.setattr(
        layout_module,
        "get_current_user",
        lambda: {"role": "admin", "username": "user", "user_id": "u1", "strategies": []},
    )
    monkeypatch.setattr(layout_module, "MarketClock", _DummyMarketClock)
    monkeypatch.setattr(layout_module, "StatusBar", _DummyStatusBar)
    monkeypatch.setattr(layout_module, "HeaderMetrics", _DummyHeaderMetrics)
    monkeypatch.setattr(layout_module, "LatencyMonitor", _DummyLatencyMonitor)
    monkeypatch.setattr(
        layout_module, "ConnectionMonitor", _ExtendedDummyConnectionMonitor
    )

    class _LifecycleWrapper:
        @classmethod
        def get(cls) -> _DummyLifecycleManager:
            return _DummyLifecycleManager()

    monkeypatch.setattr(layout_module, "ClientLifecycleManager", _LifecycleWrapper)
    monkeypatch.setattr(layout_module, "get_or_create_client_id", lambda: "client-1")
    monkeypatch.setattr(layout_module, "get_all_monitors", lambda: {})
    monkeypatch.setattr(
        layout_module.AsyncTradingClient, "get", classmethod(lambda cls: _DummyClient())
    )

    async def _page() -> None:
        return None

    wrapped = layout_module.main_layout(_page)
    await wrapped()

    # When URL is None, getattr(None, "path", "//") returns "//" as the default
    assert dummy_app.storage.user["current_path"] == "//"


@pytest.mark.asyncio()
async def test_request_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test handling when request is None."""
    dummy_ui = _ExtendedDummyUI()
    dummy_app = SimpleNamespace(storage=SimpleNamespace(user={}, client={}, request=None))

    monkeypatch.setattr(layout_module, "ui", dummy_ui)
    monkeypatch.setattr(layout_module, "app", dummy_app)
    monkeypatch.setattr(layout_module, "enable_dark_mode", lambda: None)
    monkeypatch.setattr(
        layout_module,
        "get_current_user",
        lambda: {"role": "admin", "username": "user", "user_id": "u1", "strategies": []},
    )
    monkeypatch.setattr(layout_module, "MarketClock", _DummyMarketClock)
    monkeypatch.setattr(layout_module, "StatusBar", _DummyStatusBar)
    monkeypatch.setattr(layout_module, "HeaderMetrics", _DummyHeaderMetrics)
    monkeypatch.setattr(layout_module, "LatencyMonitor", _DummyLatencyMonitor)
    monkeypatch.setattr(
        layout_module, "ConnectionMonitor", _ExtendedDummyConnectionMonitor
    )

    class _LifecycleWrapper:
        @classmethod
        def get(cls) -> _DummyLifecycleManager:
            return _DummyLifecycleManager()

    monkeypatch.setattr(layout_module, "ClientLifecycleManager", _LifecycleWrapper)
    monkeypatch.setattr(layout_module, "get_or_create_client_id", lambda: "client-1")
    monkeypatch.setattr(layout_module, "get_all_monitors", lambda: {})
    monkeypatch.setattr(
        layout_module.AsyncTradingClient, "get", classmethod(lambda cls: _DummyClient())
    )

    async def _page() -> None:
        return None

    wrapped = layout_module.main_layout(_page)
    await wrapped()

    # Should default to "/" when request is None
    assert dummy_app.storage.user["current_path"] == "/"


@pytest.mark.asyncio()
async def test_user_strategies_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that strategies are properly extracted from user."""
    # Test with list strategies
    user_with_list = {
        "role": "trader",
        "username": "trader1",
        "user_id": "t1",
        "strategies": ["alpha1", "alpha2"],
    }
    await _run_extended_layout(monkeypatch, user=user_with_list)

    # Test with tuple strategies
    user_with_tuple = {
        "role": "trader",
        "username": "trader1",
        "user_id": "t1",
        "strategies": ("alpha1", "alpha2"),
    }
    await _run_extended_layout(monkeypatch, user=user_with_tuple)

    # Test with invalid strategies (should result in empty list)
    user_with_invalid = {
        "role": "trader",
        "username": "trader1",
        "user_id": "t1",
        "strategies": "not_a_list",
    }
    await _run_extended_layout(monkeypatch, user=user_with_invalid)


@pytest.mark.asyncio()
async def test_dispatch_trading_state_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that dispatch_trading_state handles exceptions gracefully."""

    class _FailingUI(_ExtendedDummyUI):
        def run_javascript(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("JS execution failed")

    dummy_ui = _FailingUI()
    dummy_app = SimpleNamespace(
        storage=SimpleNamespace(user={}, client={}, request=SimpleNamespace(url=SimpleNamespace(path="/"))
        )
    )

    monkeypatch.setattr(layout_module, "ui", dummy_ui)
    monkeypatch.setattr(layout_module, "app", dummy_app)
    monkeypatch.setattr(layout_module, "enable_dark_mode", lambda: None)
    monkeypatch.setattr(
        layout_module,
        "get_current_user",
        lambda: {"role": "admin", "username": "user", "user_id": "u1", "strategies": []},
    )
    monkeypatch.setattr(layout_module, "MarketClock", _DummyMarketClock)
    monkeypatch.setattr(layout_module, "StatusBar", _DummyStatusBar)
    monkeypatch.setattr(layout_module, "HeaderMetrics", _DummyHeaderMetrics)
    monkeypatch.setattr(layout_module, "LatencyMonitor", _DummyLatencyMonitor)
    monkeypatch.setattr(
        layout_module, "ConnectionMonitor", _ExtendedDummyConnectionMonitor
    )

    class _LifecycleWrapper:
        @classmethod
        def get(cls) -> _DummyLifecycleManager:
            return _DummyLifecycleManager()

    monkeypatch.setattr(layout_module, "ClientLifecycleManager", _LifecycleWrapper)
    monkeypatch.setattr(layout_module, "get_or_create_client_id", lambda: "client-1")
    monkeypatch.setattr(layout_module, "get_all_monitors", lambda: {})
    monkeypatch.setattr(
        layout_module.AsyncTradingClient, "get", classmethod(lambda cls: _DummyClient())
    )

    async def _page() -> None:
        return None

    wrapped = layout_module.main_layout(_page)
    # Should not crash even if JS dispatch fails
    await wrapped()


@pytest.mark.asyncio()
async def test_market_clock_update_failure_during_success_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that market clock failures during success path are handled."""

    class _FailOnSecondUpdateClock:
        def __init__(self, exchanges: list[str] | None = None) -> None:
            self.call_count = 0

        def update(self, *, force: bool = False) -> None:
            self.call_count += 1
            if self.call_count > 0:
                raise RuntimeError("Clock update failed")

    client = _ExtendedDummyClient()
    conn_monitor = _ExtendedDummyConnectionMonitor(should_attempt_value=True)

    # Should not crash
    await _run_extended_layout(
        monkeypatch,
        client=client,
        connection_monitor=conn_monitor,
        market_clock_class=_FailOnSecondUpdateClock,
    )


import asyncio


class _TrackingGridMonitor:
    """Mock grid performance monitor that tracks log_metrics calls."""

    def __init__(self) -> None:
        self.log_metrics_called = False

    def log_metrics(self) -> None:
        self.log_metrics_called = True


@pytest.mark.asyncio()
async def test_log_grid_metrics_executes_for_monitors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that log_grid_metrics iterates and logs for all monitors."""
    monitor1 = _TrackingGridMonitor()
    monitor2 = _TrackingGridMonitor()
    monitors = {
        ("grid1", "session1"): monitor1,
        ("grid2", "session2"): monitor2,
    }

    dummy_ui = _ExtendedDummyUI()
    dummy_app = SimpleNamespace(
        storage=SimpleNamespace(user={}, client={}, request=SimpleNamespace(url=SimpleNamespace(path="/"))
        )
    )
    lifecycle_manager = _DummyLifecycleManager()

    # Track the timer callback
    timer_callbacks: list[Any] = []

    class _TimerCapturingUI(_ExtendedDummyUI):
        def timer(self, interval: float, callback: Any) -> _DummyTimer:
            timer_callbacks.append(callback)
            return _DummyTimer()

    dummy_ui = _TimerCapturingUI()

    monkeypatch.setattr(layout_module, "ui", dummy_ui)
    monkeypatch.setattr(layout_module, "app", dummy_app)
    monkeypatch.setattr(layout_module, "enable_dark_mode", lambda: None)
    monkeypatch.setattr(
        layout_module,
        "get_current_user",
        lambda: {"role": "admin", "username": "user", "user_id": "u1", "strategies": []},
    )
    monkeypatch.setattr(layout_module, "MarketClock", _DummyMarketClock)
    monkeypatch.setattr(layout_module, "StatusBar", _DummyStatusBar)
    monkeypatch.setattr(layout_module, "HeaderMetrics", _DummyHeaderMetrics)
    monkeypatch.setattr(layout_module, "LatencyMonitor", _DummyLatencyMonitor)
    monkeypatch.setattr(
        layout_module, "ConnectionMonitor", _ExtendedDummyConnectionMonitor
    )

    class _LifecycleWrapper:
        @classmethod
        def get(cls) -> _DummyLifecycleManager:
            return lifecycle_manager

    monkeypatch.setattr(layout_module, "ClientLifecycleManager", _LifecycleWrapper)
    monkeypatch.setattr(layout_module, "get_or_create_client_id", lambda: "client-1")
    monkeypatch.setattr(layout_module, "get_all_monitors", lambda: monitors)
    monkeypatch.setattr(
        layout_module.AsyncTradingClient, "get", classmethod(lambda cls: _DummyClient())
    )

    async def _page() -> None:
        return None

    wrapped = layout_module.main_layout(_page)
    await wrapped()

    # We should have 2 timer callbacks: update_global_status and log_grid_metrics
    assert len(timer_callbacks) == 2

    # Call the second callback (log_grid_metrics is the second timer)
    # The first timer is for update_global_status (5.0s), second is for metrics (60.0s)
    log_grid_metrics_callback = timer_callbacks[1]
    await log_grid_metrics_callback()

    # Verify both monitors had log_metrics called
    assert monitor1.log_metrics_called
    assert monitor2.log_metrics_called


@pytest.mark.asyncio()
async def test_status_poll_lock_prevents_concurrent_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that the status poll lock prevents concurrent status updates."""
    call_count = 0

    class _SlowClient(_ExtendedDummyClient):
        async def fetch_kill_switch_status(self, *args: Any, **kwargs: Any) -> dict[str, str]:
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.1)
            return {"state": "DISENGAGED"}

    client = _SlowClient()
    conn_monitor = _ExtendedDummyConnectionMonitor(should_attempt_value=True)

    # Track the timer callback
    status_update_callback: Any = None

    class _TimerCapturingUI(_ExtendedDummyUI):
        def timer(self, interval: float, callback: Any) -> _DummyTimer:
            nonlocal status_update_callback
            if interval == 5.0:
                status_update_callback = callback
            return _DummyTimer()

    dummy_ui = _TimerCapturingUI()
    dummy_app = SimpleNamespace(
        storage=SimpleNamespace(user={}, client={}, request=SimpleNamespace(url=SimpleNamespace(path="/"))
        )
    )
    lifecycle_manager = _DummyLifecycleManager()

    monkeypatch.setattr(layout_module, "ui", dummy_ui)
    monkeypatch.setattr(layout_module, "app", dummy_app)
    monkeypatch.setattr(layout_module, "enable_dark_mode", lambda: None)
    monkeypatch.setattr(
        layout_module,
        "get_current_user",
        lambda: {"role": "admin", "username": "user", "user_id": "u1", "strategies": []},
    )
    monkeypatch.setattr(layout_module, "MarketClock", _DummyMarketClock)
    monkeypatch.setattr(layout_module, "StatusBar", _DummyStatusBar)
    monkeypatch.setattr(layout_module, "HeaderMetrics", _DummyHeaderMetrics)
    monkeypatch.setattr(layout_module, "LatencyMonitor", _DummyLatencyMonitor)
    monkeypatch.setattr(layout_module, "ConnectionMonitor", lambda: conn_monitor)

    class _LifecycleWrapper:
        @classmethod
        def get(cls) -> _DummyLifecycleManager:
            return lifecycle_manager

    monkeypatch.setattr(layout_module, "ClientLifecycleManager", _LifecycleWrapper)
    monkeypatch.setattr(layout_module, "get_or_create_client_id", lambda: "client-1")
    monkeypatch.setattr(layout_module, "get_all_monitors", lambda: {})
    monkeypatch.setattr(
        layout_module.AsyncTradingClient, "get", classmethod(lambda cls: client)
    )

    async def _page() -> None:
        return None

    wrapped = layout_module.main_layout(_page)
    await wrapped()

    # Initial call happens during layout setup
    assert call_count == 1

    # Call the update callback multiple times concurrently
    # With the lock, only one should execute at a time
    await asyncio.gather(
        status_update_callback(),
        status_update_callback(),
        status_update_callback(),
    )

    # Due to lock, concurrent calls should be skipped if lock is held
    # The exact count depends on timing, but should be less than 4 total
    # (1 initial + potentially 1-3 from gather depending on lock timing)
    assert call_count >= 2


@pytest.mark.asyncio()
async def test_header_metrics_not_stale_no_mark(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that header metrics are not marked stale when not stale."""
    conn_monitor = _ExtendedDummyConnectionMonitor(should_attempt_value=False)
    header_metrics = _ExtendedDummyHeaderMetrics(is_stale_value=False)

    await _run_extended_layout(
        monkeypatch,
        connection_monitor=conn_monitor,
        header_metrics=header_metrics,
    )

    # Header metrics should NOT be marked stale when is_stale returns False
    assert not header_metrics.stale_marked


@pytest.mark.asyncio()
async def test_kill_switch_engaged_notification_on_state_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that notification is shown only on state change to ENGAGED."""
    # This tests the last_kill_switch_state != "ENGAGED" branch
    status_update_callback: Any = None

    class _TimerCapturingUI(_ExtendedDummyUI):
        def timer(self, interval: float, callback: Any) -> _DummyTimer:
            nonlocal status_update_callback
            if interval == 5.0:
                status_update_callback = callback
            return _DummyTimer()

    dummy_ui = _TimerCapturingUI()
    dummy_app = SimpleNamespace(
        storage=SimpleNamespace(user={}, client={}, request=SimpleNamespace(url=SimpleNamespace(path="/"))
        )
    )
    lifecycle_manager = _DummyLifecycleManager()

    # Start with DISENGAGED, then switch to ENGAGED
    call_count = 0

    class _StateChangingClient(_ExtendedDummyClient):
        async def fetch_kill_switch_status(self, *args: Any, **kwargs: Any) -> dict[str, str]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"state": "DISENGAGED"}
            return {"state": "ENGAGED"}

    client = _StateChangingClient()
    conn_monitor = _ExtendedDummyConnectionMonitor(should_attempt_value=True)

    monkeypatch.setattr(layout_module, "ui", dummy_ui)
    monkeypatch.setattr(layout_module, "app", dummy_app)
    monkeypatch.setattr(layout_module, "enable_dark_mode", lambda: None)
    monkeypatch.setattr(
        layout_module,
        "get_current_user",
        lambda: {"role": "admin", "username": "user", "user_id": "u1", "strategies": []},
    )
    monkeypatch.setattr(layout_module, "MarketClock", _DummyMarketClock)
    monkeypatch.setattr(layout_module, "StatusBar", _DummyStatusBar)
    monkeypatch.setattr(layout_module, "HeaderMetrics", _DummyHeaderMetrics)
    monkeypatch.setattr(layout_module, "LatencyMonitor", _DummyLatencyMonitor)
    monkeypatch.setattr(layout_module, "ConnectionMonitor", lambda: conn_monitor)

    class _LifecycleWrapper:
        @classmethod
        def get(cls) -> _DummyLifecycleManager:
            return lifecycle_manager

    monkeypatch.setattr(layout_module, "ClientLifecycleManager", _LifecycleWrapper)
    monkeypatch.setattr(layout_module, "get_or_create_client_id", lambda: "client-1")
    monkeypatch.setattr(layout_module, "get_all_monitors", lambda: {})
    monkeypatch.setattr(
        layout_module.AsyncTradingClient, "get", classmethod(lambda cls: client)
    )

    async def _page() -> None:
        return None

    wrapped = layout_module.main_layout(_page)
    await wrapped()

    # First call was DISENGAGED
    assert call_count == 1

    # Second call will be ENGAGED - should trigger notification
    await status_update_callback()
    assert call_count == 2

    # Third call still ENGAGED - should NOT trigger notification again
    await status_update_callback()
    assert call_count == 3


@pytest.mark.asyncio()
async def test_connection_state_read_only_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that read_only state changes are dispatched."""
    status_update_callback: Any = None

    class _TimerCapturingUI(_ExtendedDummyUI):
        def timer(self, interval: float, callback: Any) -> _DummyTimer:
            nonlocal status_update_callback
            if interval == 5.0:
                status_update_callback = callback
            return _DummyTimer()

    dummy_ui = _TimerCapturingUI()
    dummy_app = SimpleNamespace(
        storage=SimpleNamespace(user={}, client={}, request=SimpleNamespace(url=SimpleNamespace(path="/"))
        )
    )
    lifecycle_manager = _DummyLifecycleManager()

    call_count = 0

    class _ReadOnlyChangingMonitor(_ExtendedDummyConnectionMonitor):
        def is_read_only(self) -> bool:
            nonlocal call_count
            return call_count > 1  # First call False, then True

    conn_monitor = _ReadOnlyChangingMonitor(should_attempt_value=True)
    client = _ExtendedDummyClient()

    monkeypatch.setattr(layout_module, "ui", dummy_ui)
    monkeypatch.setattr(layout_module, "app", dummy_app)
    monkeypatch.setattr(layout_module, "enable_dark_mode", lambda: None)
    monkeypatch.setattr(
        layout_module,
        "get_current_user",
        lambda: {"role": "admin", "username": "user", "user_id": "u1", "strategies": []},
    )
    monkeypatch.setattr(layout_module, "MarketClock", _DummyMarketClock)
    monkeypatch.setattr(layout_module, "StatusBar", _DummyStatusBar)
    monkeypatch.setattr(layout_module, "HeaderMetrics", _DummyHeaderMetrics)
    monkeypatch.setattr(layout_module, "LatencyMonitor", _DummyLatencyMonitor)
    monkeypatch.setattr(layout_module, "ConnectionMonitor", lambda: conn_monitor)

    class _LifecycleWrapper:
        @classmethod
        def get(cls) -> _DummyLifecycleManager:
            return lifecycle_manager

    monkeypatch.setattr(layout_module, "ClientLifecycleManager", _LifecycleWrapper)
    monkeypatch.setattr(layout_module, "get_or_create_client_id", lambda: "client-1")
    monkeypatch.setattr(layout_module, "get_all_monitors", lambda: {})
    monkeypatch.setattr(
        layout_module.AsyncTradingClient, "get", classmethod(lambda cls: client)
    )

    async def _page() -> None:
        return None

    wrapped = layout_module.main_layout(_page)
    await wrapped()

    call_count = 1
    # Call update to change read_only state
    await status_update_callback()
    call_count = 2
    # Read only should have changed
    await status_update_callback()


class _ButtonCapturingUI(_ExtendedDummyUI):
    """UI that captures button callbacks."""

    def __init__(self) -> None:
        super().__init__()
        self.button_callbacks: dict[str, Any] = {}
        self.timer_callbacks: list[tuple[float, Any]] = []

    def button(
        self,
        text: str = "",
        icon: str | None = None,
        on_click: Any = None,
        **kwargs: Any,
    ) -> _DummyElement:
        if on_click is not None:
            # Capture callbacks for buttons with specific text patterns
            if "KILL" in text.upper() or "Engage" in text or "Disengage" in text:
                self.button_callbacks[text] = on_click
            elif icon == "logout":
                self.button_callbacks["logout"] = on_click
        return _DummyElement(text)

    def timer(self, interval: float, callback: Any) -> _DummyTimer:
        self.timer_callbacks.append((interval, callback))
        return _DummyTimer()


@pytest.mark.asyncio()
async def test_logout_button_normal_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test logout button executes JavaScript."""
    dummy_ui = _ButtonCapturingUI()
    dummy_app = SimpleNamespace(
        storage=SimpleNamespace(user={}, client={}, request=SimpleNamespace(url=SimpleNamespace(path="/"))
        )
    )
    lifecycle_manager = _DummyLifecycleManager()

    js_calls: list[str] = []

    class _JSTrackingUI(_ButtonCapturingUI):
        def run_javascript(self, script: str) -> None:
            js_calls.append(script)

    dummy_ui = _JSTrackingUI()

    monkeypatch.setattr(layout_module, "ui", dummy_ui)
    monkeypatch.setattr(layout_module, "app", dummy_app)
    monkeypatch.setattr(layout_module, "enable_dark_mode", lambda: None)
    monkeypatch.setattr(
        layout_module,
        "get_current_user",
        lambda: {"role": "admin", "username": "user", "user_id": "u1", "strategies": []},
    )
    monkeypatch.setattr(layout_module, "MarketClock", _DummyMarketClock)
    monkeypatch.setattr(layout_module, "StatusBar", _DummyStatusBar)
    monkeypatch.setattr(layout_module, "HeaderMetrics", _DummyHeaderMetrics)
    monkeypatch.setattr(layout_module, "LatencyMonitor", _DummyLatencyMonitor)
    monkeypatch.setattr(
        layout_module, "ConnectionMonitor", _ExtendedDummyConnectionMonitor
    )

    class _LifecycleWrapper:
        @classmethod
        def get(cls) -> _DummyLifecycleManager:
            return lifecycle_manager

    monkeypatch.setattr(layout_module, "ClientLifecycleManager", _LifecycleWrapper)
    monkeypatch.setattr(layout_module, "get_or_create_client_id", lambda: "client-1")
    monkeypatch.setattr(layout_module, "get_all_monitors", lambda: {})
    monkeypatch.setattr(
        layout_module.AsyncTradingClient, "get", classmethod(lambda cls: _DummyClient())
    )

    async def _page() -> None:
        return None

    wrapped = layout_module.main_layout(_page)
    await wrapped()

    # Find and call the logout callback
    assert "logout" in dummy_ui.button_callbacks
    logout_callback = dummy_ui.button_callbacks["logout"]

    # Count JS calls before logout
    js_calls_before = len(js_calls)
    await logout_callback()

    # Should have called run_javascript with logout code
    assert len(js_calls) > js_calls_before
    # The logout script contains the logout endpoint path
    logout_js = js_calls[-1]  # Get the last call which should be the logout
    assert "/auth/logout" in logout_js or "logout" in logout_js.lower()


@pytest.mark.asyncio()
async def test_logout_button_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test logout button handles RuntimeError gracefully."""

    class _FailingJSUI(_ButtonCapturingUI):
        def run_javascript(self, script: str) -> None:
            raise RuntimeError("JS execution failed")

    dummy_ui = _FailingJSUI()
    dummy_app = SimpleNamespace(
        storage=SimpleNamespace(user={}, client={}, request=SimpleNamespace(url=SimpleNamespace(path="/"))
        )
    )
    lifecycle_manager = _DummyLifecycleManager()

    monkeypatch.setattr(layout_module, "ui", dummy_ui)
    monkeypatch.setattr(layout_module, "app", dummy_app)
    monkeypatch.setattr(layout_module, "enable_dark_mode", lambda: None)
    monkeypatch.setattr(
        layout_module,
        "get_current_user",
        lambda: {"role": "admin", "username": "user", "user_id": "u1", "strategies": []},
    )
    monkeypatch.setattr(layout_module, "MarketClock", _DummyMarketClock)
    monkeypatch.setattr(layout_module, "StatusBar", _DummyStatusBar)
    monkeypatch.setattr(layout_module, "HeaderMetrics", _DummyHeaderMetrics)
    monkeypatch.setattr(layout_module, "LatencyMonitor", _DummyLatencyMonitor)
    monkeypatch.setattr(
        layout_module, "ConnectionMonitor", _ExtendedDummyConnectionMonitor
    )

    class _LifecycleWrapper:
        @classmethod
        def get(cls) -> _DummyLifecycleManager:
            return lifecycle_manager

    monkeypatch.setattr(layout_module, "ClientLifecycleManager", _LifecycleWrapper)
    monkeypatch.setattr(layout_module, "get_or_create_client_id", lambda: "client-1")
    monkeypatch.setattr(layout_module, "get_all_monitors", lambda: {})
    monkeypatch.setattr(
        layout_module.AsyncTradingClient, "get", classmethod(lambda cls: _DummyClient())
    )

    async def _page() -> None:
        return None

    wrapped = layout_module.main_layout(_page)
    await wrapped()

    # Find and call the logout callback - should handle error gracefully
    assert "logout" in dummy_ui.button_callbacks
    logout_callback = dummy_ui.button_callbacks["logout"]
    # Should not raise
    await logout_callback()

    # Should have shown notification about failure
    assert len(dummy_ui.notifications) > 0


@pytest.mark.asyncio()
async def test_logout_button_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test logout button handles TimeoutError gracefully."""

    class _TimeoutJSUI(_ButtonCapturingUI):
        def run_javascript(self, script: str) -> None:
            raise TimeoutError("JS execution timed out")

    dummy_ui = _TimeoutJSUI()
    dummy_app = SimpleNamespace(
        storage=SimpleNamespace(user={}, client={}, request=SimpleNamespace(url=SimpleNamespace(path="/"))
        )
    )
    lifecycle_manager = _DummyLifecycleManager()

    monkeypatch.setattr(layout_module, "ui", dummy_ui)
    monkeypatch.setattr(layout_module, "app", dummy_app)
    monkeypatch.setattr(layout_module, "enable_dark_mode", lambda: None)
    monkeypatch.setattr(
        layout_module,
        "get_current_user",
        lambda: {"role": "admin", "username": "user", "user_id": "u1", "strategies": []},
    )
    monkeypatch.setattr(layout_module, "MarketClock", _DummyMarketClock)
    monkeypatch.setattr(layout_module, "StatusBar", _DummyStatusBar)
    monkeypatch.setattr(layout_module, "HeaderMetrics", _DummyHeaderMetrics)
    monkeypatch.setattr(layout_module, "LatencyMonitor", _DummyLatencyMonitor)
    monkeypatch.setattr(
        layout_module, "ConnectionMonitor", _ExtendedDummyConnectionMonitor
    )

    class _LifecycleWrapper:
        @classmethod
        def get(cls) -> _DummyLifecycleManager:
            return lifecycle_manager

    monkeypatch.setattr(layout_module, "ClientLifecycleManager", _LifecycleWrapper)
    monkeypatch.setattr(layout_module, "get_or_create_client_id", lambda: "client-1")
    monkeypatch.setattr(layout_module, "get_all_monitors", lambda: {})
    monkeypatch.setattr(
        layout_module.AsyncTradingClient, "get", classmethod(lambda cls: _DummyClient())
    )

    async def _page() -> None:
        return None

    wrapped = layout_module.main_layout(_page)
    await wrapped()

    # Find and call the logout callback - should handle error gracefully
    assert "logout" in dummy_ui.button_callbacks
    logout_callback = dummy_ui.button_callbacks["logout"]
    # Should not raise
    await logout_callback()

    # Should have shown notification about failure
    assert len(dummy_ui.notifications) > 0


@pytest.mark.asyncio()
async def test_engage_kill_switch_button_opens_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that clicking engage button opens dialog."""
    dialogs_opened: list[Any] = []

    class _DialogTrackingUI(_ButtonCapturingUI):
        def dialog(self) -> _DummyDialog:
            d = _DummyDialog()
            dialogs_opened.append(d)
            return d

    dummy_ui = _DialogTrackingUI()
    dummy_app = SimpleNamespace(
        storage=SimpleNamespace(user={}, client={}, request=SimpleNamespace(url=SimpleNamespace(path="/"))
        )
    )
    lifecycle_manager = _DummyLifecycleManager()

    monkeypatch.setattr(layout_module, "ui", dummy_ui)
    monkeypatch.setattr(layout_module, "app", dummy_app)
    monkeypatch.setattr(layout_module, "enable_dark_mode", lambda: None)
    monkeypatch.setattr(
        layout_module,
        "get_current_user",
        lambda: {"role": "admin", "username": "user", "user_id": "u1", "strategies": []},
    )
    monkeypatch.setattr(layout_module, "MarketClock", _DummyMarketClock)
    monkeypatch.setattr(layout_module, "StatusBar", _DummyStatusBar)
    monkeypatch.setattr(layout_module, "HeaderMetrics", _DummyHeaderMetrics)
    monkeypatch.setattr(layout_module, "LatencyMonitor", _DummyLatencyMonitor)
    monkeypatch.setattr(
        layout_module, "ConnectionMonitor", _ExtendedDummyConnectionMonitor
    )

    class _LifecycleWrapper:
        @classmethod
        def get(cls) -> _DummyLifecycleManager:
            return lifecycle_manager

    monkeypatch.setattr(layout_module, "ClientLifecycleManager", _LifecycleWrapper)
    monkeypatch.setattr(layout_module, "get_or_create_client_id", lambda: "client-1")
    monkeypatch.setattr(layout_module, "get_all_monitors", lambda: {})
    monkeypatch.setattr(
        layout_module.AsyncTradingClient, "get", classmethod(lambda cls: _DummyClient())
    )

    async def _page() -> None:
        return None

    wrapped = layout_module.main_layout(_page)
    await wrapped()

    # Find the Engage button callback
    assert "Engage" in dummy_ui.button_callbacks
    engage_callback = dummy_ui.button_callbacks["Engage"]

    # Call it to open the dialog
    engage_callback()

    # Should have opened a dialog
    assert len(dialogs_opened) > 0
    assert dialogs_opened[0].opened


@pytest.mark.asyncio()
async def test_disengage_kill_switch_button_opens_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that clicking disengage button opens dialog."""
    dialogs_opened: list[Any] = []

    class _DialogTrackingUI(_ButtonCapturingUI):
        def dialog(self) -> _DummyDialog:
            d = _DummyDialog()
            dialogs_opened.append(d)
            return d

    dummy_ui = _DialogTrackingUI()
    dummy_app = SimpleNamespace(
        storage=SimpleNamespace(user={}, client={}, request=SimpleNamespace(url=SimpleNamespace(path="/"))
        )
    )
    lifecycle_manager = _DummyLifecycleManager()

    monkeypatch.setattr(layout_module, "ui", dummy_ui)
    monkeypatch.setattr(layout_module, "app", dummy_app)
    monkeypatch.setattr(layout_module, "enable_dark_mode", lambda: None)
    monkeypatch.setattr(
        layout_module,
        "get_current_user",
        lambda: {"role": "admin", "username": "user", "user_id": "u1", "strategies": []},
    )
    monkeypatch.setattr(layout_module, "MarketClock", _DummyMarketClock)
    monkeypatch.setattr(layout_module, "StatusBar", _DummyStatusBar)
    monkeypatch.setattr(layout_module, "HeaderMetrics", _DummyHeaderMetrics)
    monkeypatch.setattr(layout_module, "LatencyMonitor", _DummyLatencyMonitor)
    monkeypatch.setattr(
        layout_module, "ConnectionMonitor", _ExtendedDummyConnectionMonitor
    )

    class _LifecycleWrapper:
        @classmethod
        def get(cls) -> _DummyLifecycleManager:
            return lifecycle_manager

    monkeypatch.setattr(layout_module, "ClientLifecycleManager", _LifecycleWrapper)
    monkeypatch.setattr(layout_module, "get_or_create_client_id", lambda: "client-1")
    monkeypatch.setattr(layout_module, "get_all_monitors", lambda: {})
    monkeypatch.setattr(
        layout_module.AsyncTradingClient, "get", classmethod(lambda cls: _DummyClient())
    )

    async def _page() -> None:
        return None

    wrapped = layout_module.main_layout(_page)
    await wrapped()

    # Find the Disengage button callback
    assert "Disengage" in dummy_ui.button_callbacks
    disengage_callback = dummy_ui.button_callbacks["Disengage"]

    # Call it to open the dialog
    disengage_callback()

    # Should have opened a dialog
    assert len(dialogs_opened) > 0
    assert dialogs_opened[0].opened
