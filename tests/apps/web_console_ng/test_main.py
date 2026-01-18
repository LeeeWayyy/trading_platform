"""Tests for NiceGUI web console entry point."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from starlette.requests import Request
from starlette.responses import PlainTextResponse


class _DummyApp:
    def __init__(self) -> None:
        self.config = SimpleNamespace()
        self.routers: list[Any] = []
        self.middlewares: list[tuple[Any, dict[str, Any]]] = []
        self.static_files: list[tuple[str, str]] = []
        self.startup_handlers: list[Callable[[], Any]] = []
        self.shutdown_handlers: list[Callable[[], Any]] = []
        self.exception_handlers: dict[type[Exception], Callable[..., Any]] = {}
        self.routes: dict[tuple[str, str], Callable[..., Any]] = {}

    def include_router(self, router: Any) -> None:
        self.routers.append(router)

    def add_middleware(self, middleware: Any, **kwargs: Any) -> None:
        self.middlewares.append((middleware, kwargs))

    def add_static_files(self, mount: str, path: str) -> None:
        self.static_files.append((mount, path))

    def on_startup(self, func: Callable[[], Any]) -> None:
        self.startup_handlers.append(func)

    def on_shutdown(self, func: Callable[[], Any]) -> None:
        self.shutdown_handlers.append(func)

    def exception_handler(self, exc_type: type[Exception]) -> Callable[[Callable[..., Any]], Any]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.exception_handlers[exc_type] = func
            return func

        return decorator

    def get(self, path: str) -> Callable[[Callable[..., Any]], Any]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.routes[("GET", path)] = func
            return func

        return decorator

    def post(self, path: str) -> Callable[[Callable[..., Any]], Any]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.routes[("POST", path)] = func
            return func

        return decorator


class _DummyUI:
    def __init__(self) -> None:
        self.head_html: list[str] = []
        self.run_args: dict[str, Any] | None = None

    def add_head_html(self, html: str) -> None:
        self.head_html.append(html)

    def run(self, **kwargs: Any) -> None:
        self.run_args = kwargs


class _DummyPool:
    def __init__(self) -> None:
        self.open_called = False

    async def open(self) -> None:
        self.open_called = True


class _DummyTradingClient:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def startup(self) -> None:
        self.started = True

    async def shutdown(self) -> None:
        self.stopped = True


class _DummyAuditLogger:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    @classmethod
    def get(cls, **_kwargs: Any) -> _DummyAuditLogger:
        return cls()


def _install_dummy_modules(monkeypatch: pytest.MonkeyPatch) -> tuple[_DummyApp, _DummyUI]:
    dummy_app = _DummyApp()
    dummy_ui = _DummyUI()

    nicegui_module = ModuleType("nicegui")
    nicegui_module.app = dummy_app
    nicegui_module.ui = dummy_ui
    monkeypatch.setitem(sys.modules, "nicegui", nicegui_module)

    auth_audit_module = ModuleType("apps.web_console_ng.auth.audit")
    auth_audit_module.AuthAuditLogger = _DummyAuditLogger
    monkeypatch.setitem(sys.modules, "apps.web_console_ng.auth.audit", auth_audit_module)

    auth_middleware_module = ModuleType("apps.web_console_ng.auth.middleware")
    auth_middleware_module.AuthMiddleware = type("AuthMiddleware", (), {})
    auth_middleware_module.SessionMiddleware = type("SessionMiddleware", (), {})
    monkeypatch.setitem(sys.modules, "apps.web_console_ng.auth.middleware", auth_middleware_module)

    session_store_module = ModuleType("apps.web_console_ng.auth.session_store")

    def _get_session_store(**_kwargs: Any) -> str:
        return "session-store"

    session_store_module.get_session_store = _get_session_store
    monkeypatch.setitem(sys.modules, "apps.web_console_ng.auth.session_store", session_store_module)

    admission_module = ModuleType("apps.web_console_ng.core.admission")
    admission_module.AdmissionControlMiddleware = type("AdmissionControlMiddleware", (), {})
    monkeypatch.setitem(sys.modules, "apps.web_console_ng.core.admission", admission_module)

    client_module = ModuleType("apps.web_console_ng.core.client")
    trading_client = _DummyTradingClient()

    class _DummyAsyncTradingClient:
        @classmethod
        def get(cls) -> _DummyTradingClient:
            return trading_client

    client_module.AsyncTradingClient = _DummyAsyncTradingClient
    monkeypatch.setitem(sys.modules, "apps.web_console_ng.core.client", client_module)

    connection_events_module = ModuleType("apps.web_console_ng.core.connection_events")

    def _setup_connection_handlers() -> None:
        return None

    connection_events_module.setup_connection_handlers = _setup_connection_handlers
    monkeypatch.setitem(sys.modules, "apps.web_console_ng.core.connection_events", connection_events_module)

    database_module = ModuleType("apps.web_console_ng.core.database")
    pool = _DummyPool()

    def _init_db_pool() -> _DummyPool:
        return pool

    async def _close_db_pool() -> None:
        return None

    database_module.init_db_pool = _init_db_pool
    database_module.close_db_pool = _close_db_pool
    monkeypatch.setitem(sys.modules, "apps.web_console_ng.core.database", database_module)

    health_module = ModuleType("apps.web_console_ng.core.health")

    def _setup_health_endpoint() -> None:
        return None

    health_module.setup_health_endpoint = _setup_health_endpoint
    monkeypatch.setitem(sys.modules, "apps.web_console_ng.core.health", health_module)

    state_manager_module = ModuleType("apps.web_console_ng.core.state_manager")

    class _DummyStateManager:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    state_manager = _DummyStateManager()
    state_manager_module.get_state_manager = lambda: state_manager
    monkeypatch.setitem(sys.modules, "apps.web_console_ng.core.state_manager", state_manager_module)

    disconnect_module = ModuleType("apps.web_console_ng.ui.disconnect_overlay")
    disconnect_called = {"called": False}

    def _inject_disconnect_overlay() -> None:
        disconnect_called["called"] = True

    disconnect_module.inject_disconnect_overlay = _inject_disconnect_overlay
    monkeypatch.setitem(sys.modules, "apps.web_console_ng.ui.disconnect_overlay", disconnect_module)

    api_workspace_module = ModuleType("apps.web_console_ng.api.workspace")
    api_workspace_module.router = "workspace-router"
    monkeypatch.setitem(sys.modules, "apps.web_console_ng.api.workspace", api_workspace_module)

    auth_routes_module = ModuleType("apps.web_console_ng.auth.routes")
    auth_routes_module.auth_api_router = "auth-router"
    monkeypatch.setitem(sys.modules, "apps.web_console_ng.auth.routes", auth_routes_module)

    auth_logout_module = ModuleType("apps.web_console_ng.auth.logout")
    monkeypatch.setitem(sys.modules, "apps.web_console_ng.auth.logout", auth_logout_module)

    auth_parent = ModuleType("apps.web_console_ng.auth")
    auth_parent.routes = auth_routes_module
    auth_parent.logout = auth_logout_module
    monkeypatch.setitem(sys.modules, "apps.web_console_ng.auth", auth_parent)
    monkeypatch.setitem(sys.modules, "apps.web_console_ng.pages", ModuleType("apps.web_console_ng.pages"))

    dependencies_module = ModuleType("apps.web_console_ng.core.dependencies")
    dependencies_module.close_sync_db_pool = lambda: None
    dependencies_module.close_sync_redis_client = lambda: None
    monkeypatch.setitem(sys.modules, "apps.web_console_ng.core.dependencies", dependencies_module)

    redis_module = ModuleType("apps.web_console_ng.core.redis_ha")

    class _DummyRedis:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    redis_module.get_redis_store = lambda: _DummyRedis()
    monkeypatch.setitem(sys.modules, "apps.web_console_ng.core.redis_ha", redis_module)

    return dummy_app, dummy_ui


def _import_main(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    _install_dummy_modules(monkeypatch)
    if "apps.web_console_ng.main" in sys.modules:
        del sys.modules["apps.web_console_ng.main"]
    return importlib.import_module("apps.web_console_ng.main")


def test_app_configuration_and_middleware(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _import_main(monkeypatch)

    assert module.app.config.title == module.config.PAGE_TITLE
    assert module.app.config.viewport == "width=device-width, initial-scale=1"
    assert module.app.config.favicon is None
    assert module.app.config.dark is None
    assert module.app.config.language == "en-US"
    assert module.app.config.tailwind is True
    assert module.app.config.prod_js == (not module.config.DEBUG)
    assert module.app.config.reconnect_timeout == 3.0

    assert "auth-router" in module.app.routers
    assert "workspace-router" in module.app.routers
    assert module.app.static_files == [("/static", "apps/web_console_ng/static")]

    middleware_order = [entry[0].__name__ for entry in module.app.middlewares]
    assert middleware_order == [
        "AuthMiddleware",
        "SessionMiddleware",
        "AdmissionControlMiddleware",
        "TrustedHostMiddleware",
    ]


@pytest.mark.asyncio()
async def test_startup_and_shutdown_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _import_main(monkeypatch)

    await module.startup()

    assert module.db_pool.open_called is True
    assert module.trading_client.started is True
    assert module.audit_logger.started is True

    await module.shutdown()

    assert module.trading_client.stopped is True
    assert module.audit_logger.stopped is True
    assert module.state_manager.closed is True


@pytest.mark.asyncio()
async def test_log_unhandled_exception_returns_plaintext(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _import_main(monkeypatch)

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [],
        }
    )
    response = await module.log_unhandled_exception(request, RuntimeError("boom"))

    assert isinstance(response, PlainTextResponse)
    assert response.status_code == 500
    assert response.body == b"Server error"


@pytest.mark.asyncio()
async def test_socket_io_redirect_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _import_main(monkeypatch)

    response = await module.socket_io_redirect()

    assert response["error"] == "socket.io path changed"
    assert "new_path" in response


@pytest.mark.asyncio()
async def test_socket_io_redirect_with_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test socket.io redirect handler with path parameter."""
    module = _import_main(monkeypatch)

    response = await module.socket_io_redirect(path="transport=polling")

    assert response["error"] == "socket.io path changed"
    assert response["message"] == "Please refresh your browser (Ctrl+Shift+R) to clear cache"
    assert response["new_path"] == "/_nicegui_ws/socket.io/"


def test_socket_io_routes_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify both GET and POST routes are registered for socket.io redirect."""
    module = _import_main(monkeypatch)

    assert ("GET", "/socket.io/{path:path}") in module.app.routes
    assert ("POST", "/socket.io/{path:path}") in module.app.routes


def test_exception_handler_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify exception handler is registered for all exceptions."""
    module = _import_main(monkeypatch)

    assert Exception in module.app.exception_handlers
    assert module.app.exception_handlers[Exception] == module.log_unhandled_exception


def test_head_html_injection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify AG Grid renderer script is injected into head."""
    module = _import_main(monkeypatch)

    assert len(module.ui.head_html) == 1
    assert '<script src="/static/js/aggrid_renderers.js"></script>' in module.ui.head_html[0]


def test_startup_and_shutdown_handlers_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify startup and shutdown handlers are registered."""
    module = _import_main(monkeypatch)

    assert len(module.app.startup_handlers) == 1
    assert len(module.app.shutdown_handlers) == 1
    assert module.startup in module.app.startup_handlers
    assert module.shutdown in module.app.shutdown_handlers


def test_session_middleware_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify SessionMiddleware is configured with correct parameters."""
    module = _import_main(monkeypatch)

    session_middleware = next(
        (entry for entry in module.app.middlewares if entry[0].__name__ == "SessionMiddleware"),
        None,
    )

    assert session_middleware is not None
    _, kwargs = session_middleware
    assert "session_store" in kwargs
    assert kwargs["session_store"] == "session-store"
    assert "trusted_proxies" in kwargs


def test_trusted_host_middleware_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify TrustedHostMiddleware is configured with allowed hosts."""
    module = _import_main(monkeypatch)

    trusted_host = next(
        (entry for entry in module.app.middlewares if entry[0].__name__ == "TrustedHostMiddleware"),
        None,
    )

    assert trusted_host is not None
    _, kwargs = trusted_host
    assert "allowed_hosts" in kwargs


@pytest.mark.asyncio()
async def test_startup_with_none_db_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test startup when db_pool is None (gracefully skips pool.open())."""
    dummy_app, dummy_ui = _install_dummy_modules(monkeypatch)

    database_module = ModuleType("apps.web_console_ng.core.database")

    def _init_db_pool() -> None:
        return None

    async def _close_db_pool() -> None:
        return None

    database_module.init_db_pool = _init_db_pool
    database_module.close_db_pool = _close_db_pool
    monkeypatch.setitem(sys.modules, "apps.web_console_ng.core.database", database_module)

    if "apps.web_console_ng.main" in sys.modules:
        del sys.modules["apps.web_console_ng.main"]
    module = importlib.import_module("apps.web_console_ng.main")

    await module.startup()

    assert module.db_pool is None
    assert module.trading_client.started is True
    assert module.audit_logger.started is True


@pytest.mark.asyncio()
async def test_shutdown_redis_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test shutdown handles Redis ConnectionError gracefully."""
    module = _import_main(monkeypatch)

    redis_module = ModuleType("apps.web_console_ng.core.redis_ha")

    class _FailingRedis:
        async def close(self) -> None:
            raise ConnectionError("Connection lost")

    redis_module.get_redis_store = lambda: _FailingRedis()
    monkeypatch.setitem(sys.modules, "apps.web_console_ng.core.redis_ha", redis_module)

    if "apps.web_console_ng.main" in sys.modules:
        del sys.modules["apps.web_console_ng.main"]
    module = importlib.import_module("apps.web_console_ng.main")

    await module.shutdown()


@pytest.mark.asyncio()
async def test_shutdown_redis_os_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test shutdown handles Redis OSError gracefully."""
    module = _import_main(monkeypatch)

    redis_module = ModuleType("apps.web_console_ng.core.redis_ha")

    class _FailingRedis:
        async def close(self) -> None:
            raise OSError("Socket error")

    redis_module.get_redis_store = lambda: _FailingRedis()
    monkeypatch.setitem(sys.modules, "apps.web_console_ng.core.redis_ha", redis_module)

    if "apps.web_console_ng.main" in sys.modules:
        del sys.modules["apps.web_console_ng.main"]
    module = importlib.import_module("apps.web_console_ng.main")

    await module.shutdown()


@pytest.mark.asyncio()
async def test_log_unhandled_exception_logs_traceback(monkeypatch: pytest.MonkeyPatch, caplog: Any) -> None:
    """Test exception handler logs with full details."""
    import logging

    module = _import_main(monkeypatch)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/test",
            "headers": [],
        }
    )

    with caplog.at_level(logging.ERROR):
        response = await module.log_unhandled_exception(request, ValueError("Test error"))

    assert response.status_code == 500
    assert any("unhandled_exception" in record.message for record in caplog.records)
    assert any("ValueError" in record.message for record in caplog.records)


def test_app_config_prod_js_when_not_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test prod_js is True when DEBUG is False."""
    # Install all dummy modules first
    dummy_app, dummy_ui = _install_dummy_modules(monkeypatch)

    # Override config after installation
    config_module = sys.modules["apps.web_console_ng.config"]
    monkeypatch.setattr(config_module, "DEBUG", False)

    if "apps.web_console_ng.main" in sys.modules:
        del sys.modules["apps.web_console_ng.main"]
    module = importlib.import_module("apps.web_console_ng.main")

    assert module.app.config.prod_js is True


@pytest.mark.asyncio()
async def test_disconnect_overlay_injection_called(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify inject_disconnect_overlay is called during startup."""
    disconnect_called_tracker = {"called": False}

    # Install all dummy modules first
    dummy_app, dummy_ui = _install_dummy_modules(monkeypatch)

    # Override the disconnect overlay module after installation
    disconnect_module = ModuleType("apps.web_console_ng.ui.disconnect_overlay")

    def _inject_disconnect_overlay() -> None:
        disconnect_called_tracker["called"] = True

    disconnect_module.inject_disconnect_overlay = _inject_disconnect_overlay
    monkeypatch.setitem(sys.modules, "apps.web_console_ng.ui.disconnect_overlay", disconnect_module)

    if "apps.web_console_ng.main" in sys.modules:
        del sys.modules["apps.web_console_ng.main"]
    module = importlib.import_module("apps.web_console_ng.main")

    await module.startup()

    assert disconnect_called_tracker["called"] is True
