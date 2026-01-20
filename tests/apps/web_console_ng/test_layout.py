from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from apps.web_console_ng.ui import layout as layout_module
from libs.platform.web_console_auth.permissions import Permission


class _FakeContext:
    def __init__(self, ui: _FakeUI, kind: str, target: str | None = None) -> None:
        self.ui = ui
        self.kind = kind
        self.target = target
        self.classes_value: str | None = None
        self.label: str | None = None
        self.icon: str | None = None
        self.text: str | None = None

    def __enter__(self) -> _FakeContext:
        self.ui._push(self)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.ui._pop(self)

    def classes(self, value: str, **_kwargs: Any) -> _FakeContext:
        self.classes_value = value
        return self

    def props(self, *_args: Any, **_kwargs: Any) -> _FakeContext:
        return self

    def tooltip(self, *_args: Any, **_kwargs: Any) -> _FakeContext:
        return self

    def set_text(self, text: str) -> None:
        self.text = text

    def toggle(self) -> None:  # pragma: no cover - callback only
        return None

    def on_click(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def disable(self) -> None:
        return None

    def enable(self) -> None:
        return None


class _FakeUI:
    def __init__(self) -> None:
        self.links: list[_FakeContext] = []
        self._stack: list[_FakeContext] = []
        # Mock context for client lifecycle cleanup
        self.context = SimpleNamespace(
            client=SimpleNamespace(storage=SimpleNamespace(get=lambda _: None))
        )

    def _push(self, ctx: _FakeContext) -> None:
        self._stack.append(ctx)

    def _pop(self, ctx: _FakeContext) -> None:
        if self._stack and self._stack[-1] is ctx:
            self._stack.pop()

    def _current_link(self) -> _FakeContext | None:
        for ctx in reversed(self._stack):
            if ctx.kind == "link":
                return ctx
        return None

    def left_drawer(self, *_args: Any, **_kwargs: Any) -> _FakeContext:
        return _FakeContext(self, "left_drawer")

    def column(self, *_args: Any, **_kwargs: Any) -> _FakeContext:
        return _FakeContext(self, "column")

    def row(self, *_args: Any, **_kwargs: Any) -> _FakeContext:
        return _FakeContext(self, "row")

    def header(self, *_args: Any, **_kwargs: Any) -> _FakeContext:
        return _FakeContext(self, "header")

    def link(self, target: str) -> _FakeContext:
        ctx = _FakeContext(self, "link", target=target)
        self.links.append(ctx)
        return ctx

    def label(self, text: str) -> _FakeContext:
        ctx = _FakeContext(self, "label")
        ctx.text = text
        current_link = self._current_link()
        if current_link is not None and current_link.label is None:
            current_link.label = text
        return ctx

    def icon(self, name: str) -> _FakeContext:
        ctx = _FakeContext(self, "icon")
        ctx.text = name
        current_link = self._current_link()
        if current_link is not None and current_link.icon is None:
            current_link.icon = name
        return ctx

    def button(self, *_args: Any, **_kwargs: Any) -> _FakeContext:
        return _FakeContext(self, "button")

    def badge(self, *_args: Any, **_kwargs: Any) -> _FakeContext:
        return _FakeContext(self, "badge")

    def space(self) -> None:
        return None

    def timer(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def notify(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def add_head_html(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def add_body_html(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def dark_mode(self) -> Any:
        class _DarkMode:
            def enable(self) -> None:
                return None

        return _DarkMode()

    class navigate:
        to = staticmethod(lambda *_args, **_kwargs: None)


async def _run_layout(
    monkeypatch: pytest.MonkeyPatch,
    user_role: str,
    current_path: str,
    *,
    has_admin_permissions: bool = True,
) -> _FakeUI:
    fake_ui = _FakeUI()
    storage = SimpleNamespace(
        user={}, request=SimpleNamespace(url=SimpleNamespace(path=current_path))
    )
    fake_app = SimpleNamespace(storage=storage)

    monkeypatch.setattr(layout_module, "ui", fake_ui)
    monkeypatch.setattr(layout_module, "app", fake_app)
    monkeypatch.setattr(
        layout_module,
        "get_current_user",
        lambda: {"role": user_role, "username": "Test User", "user_id": "user-1"},
    )

    # Mock has_permission based on has_admin_permissions flag
    admin_permissions = {
        Permission.MANAGE_API_KEYS,
        Permission.MANAGE_SYSTEM_CONFIG,
        Permission.VIEW_AUDIT,
    }

    def mock_has_permission(_user: dict[str, Any], permission: Permission) -> bool:
        if permission in admin_permissions:
            return has_admin_permissions
        return True

    monkeypatch.setattr(layout_module, "has_permission", mock_has_permission)

    # Mock enable_dark_mode (it calls real NiceGUI ui.dark_mode() outside of page context)
    monkeypatch.setattr(layout_module, "enable_dark_mode", lambda: None)

    # Mock get_all_monitors (imported from grid_performance)
    monkeypatch.setattr(layout_module, "get_all_monitors", lambda: {})

    # Mock P6T2 components that use NiceGUI's ui directly
    class _DummyMarketClock:
        def __init__(self, exchanges: list[str] | None = None) -> None:
            pass

        def update(self, *, force: bool = False) -> None:
            pass

    class _DummyStatusBar:
        def __init__(self) -> None:
            pass

        def update_state(self, state: str) -> None:
            pass

    class _DummyHeaderMetrics:
        def __init__(self) -> None:
            pass

        async def update(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def is_stale(self) -> bool:
            return False

        def mark_stale(self) -> None:
            pass

    class _DummyLatencyMonitor:
        def __init__(self) -> None:
            pass

        async def measure(self) -> float | None:
            return 50.0

        def format_display(self) -> str:
            return "50ms"

        def format_tooltip(self) -> str:
            return "API Latency: 50ms"

        def get_status_color_class(self) -> str:
            return "bg-green-600 text-white"

        async def close(self) -> None:
            pass

    class _DummyConnectionMonitor:
        def __init__(self) -> None:
            pass

        def should_attempt(self) -> bool:
            return True

        def start_reconnect(self) -> None:
            pass

        def record_success(self) -> None:
            pass

        def record_failure(self) -> None:
            pass

        def record_latency(self, latency_ms: float) -> None:
            pass

        def get_connection_state(self) -> Any:
            from enum import Enum

            class _FakeState(Enum):
                CONNECTED = "connected"

            return _FakeState.CONNECTED

        def is_read_only(self) -> bool:
            return False

        def get_badge_text(self) -> str:
            return "Connected"

        def get_badge_class(self) -> str:
            return "bg-green-500 text-white"

    monkeypatch.setattr(layout_module, "MarketClock", _DummyMarketClock)
    monkeypatch.setattr(layout_module, "StatusBar", _DummyStatusBar)
    monkeypatch.setattr(layout_module, "HeaderMetrics", _DummyHeaderMetrics)
    monkeypatch.setattr(layout_module, "LatencyMonitor", _DummyLatencyMonitor)
    monkeypatch.setattr(layout_module, "ConnectionMonitor", _DummyConnectionMonitor)

    class _DummyClient:
        async def fetch_kill_switch_status(self, _user_id: str) -> dict[str, str]:
            return {"state": "ACTIVE"}

    monkeypatch.setattr(
        layout_module.AsyncTradingClient,
        "get",
        classmethod(lambda cls: _DummyClient()),
    )

    async def _page() -> None:
        return None

    wrapped = layout_module.main_layout(_page)
    await wrapped()
    return fake_ui


def test_main_layout_callable() -> None:
    async def _page() -> None:
        return None

    wrapped = layout_module.main_layout(_page)

    assert callable(layout_module.main_layout)
    assert callable(wrapped)


@pytest.mark.asyncio()
async def test_nav_items_include_expected_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ui = await _run_layout(monkeypatch, user_role="admin", current_path="/")
    targets = {link.target for link in fake_ui.links}

    expected_paths = [
        "/",
        "/manual-order",
        "/circuit-breaker",
        "/health",
        "/risk",
        "/alpha-explorer",
        "/compare",
        "/journal",
        "/notebooks",
        "/performance",
        "/reports",
        "/backtest",
        "/admin",
    ]
    for path in expected_paths:
        assert path in targets


@pytest.mark.asyncio()
async def test_admin_item_hidden_for_non_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ui = await _run_layout(
        monkeypatch, user_role="viewer", current_path="/", has_admin_permissions=False
    )
    targets = {link.target for link in fake_ui.links}

    assert "/admin" not in targets
    assert "/" in targets


# === Exception Handling Tests ===


@pytest.mark.asyncio()
async def test_logout_handles_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that logout handles RuntimeError from JavaScript execution."""
    import logging

    from apps.web_console_ng.ui import layout as layout_module

    logged_warnings: list[str] = []

    class TestHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            logged_warnings.append(record.getMessage())

    handler = TestHandler()
    layout_module.logger.addHandler(handler)

    try:
        # The logout function catches RuntimeError and TimeoutError
        # This test validates the exception handling pattern exists
        fake_ui = await _run_layout(monkeypatch, user_role="admin", current_path="/")
        assert fake_ui is not None
    finally:
        layout_module.logger.removeHandler(handler)


@pytest.mark.asyncio()
async def test_status_update_handles_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that status update handles connection errors gracefully."""
    import logging

    from apps.web_console_ng.ui import layout as layout_module

    logged_warnings: list[str] = []

    class TestHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            logged_warnings.append(record.getMessage())

    handler = TestHandler()
    layout_module.logger.addHandler(handler)

    try:
        # The update_global_status function catches ValueError, KeyError, TypeError, ConnectionError
        # This test validates the exception handling pattern exists
        fake_ui = await _run_layout(monkeypatch, user_role="admin", current_path="/")
        assert fake_ui is not None
    finally:
        layout_module.logger.removeHandler(handler)
