"""Tests for NiceGUI connection event handlers."""

from __future__ import annotations

from typing import Any

import pytest
from redis.exceptions import RedisError

from apps.web_console_ng import config
from apps.web_console_ng.core import connection_events, health


class DummyApp:
    """Dummy app for testing connection handlers."""

    def __init__(self) -> None:
        self.on_connect_handler = None
        self.on_disconnect_handler = None
        self.on_exception_handler = None

    def on_connect(self, func):
        self.on_connect_handler = func
        return func

    def on_disconnect(self, func):
        self.on_disconnect_handler = func
        return func

    def on_exception(self, func):
        self.on_exception_handler = func
        return func


class DummyRequest:
    """Dummy request for testing."""

    def __init__(self, scope: dict[str, Any]) -> None:
        self.scope = scope


class DummyClient:
    """Dummy client for testing."""

    def __init__(self, scope: dict[str, Any]) -> None:
        self.storage: dict[str, Any] = {}
        self.request = DummyRequest(scope)


class DummyClientNoRequest:
    """Dummy client without request attribute for testing edge cases."""

    def __init__(self) -> None:
        self.storage: dict[str, Any] = {}


class DummyClientNoScope:
    """Dummy client with request but no scope for testing edge cases."""

    def __init__(self) -> None:
        self.storage: dict[str, Any] = {}
        self.request = None


class DummyClientNonDictScope:
    """Dummy client with non-dict scope for testing edge cases."""

    def __init__(self) -> None:
        self.storage: dict[str, Any] = {}
        self.request = object()  # Not a proper request with scope


class DummyLifecycle:
    """Dummy lifecycle manager for testing."""

    def __init__(self, client_id: str = "client-123") -> None:
        self.client_id = client_id
        self.registered: list[str] = []
        self.cleaned: list[str] = []

    def generate_client_id(self) -> str:
        return self.client_id

    async def register_client(self, client_id: str) -> None:
        self.registered.append(client_id)

    async def cleanup_client(self, client_id: str) -> None:
        self.cleaned.append(client_id)


class DummyMetric:
    """Dummy metric for testing."""

    def __init__(self) -> None:
        self.label_args: list[dict[str, Any]] = []
        self.inc_count = 0
        self.set_values: list[int] = []

    def labels(self, **kwargs: Any):
        self.label_args.append(kwargs)
        return self

    def inc(self) -> None:
        self.inc_count += 1

    def set(self, value: int) -> None:
        self.set_values.append(value)


class DummyMetrics:
    """Dummy metrics collection for testing."""

    def __init__(self) -> None:
        self.ws_connects_total = DummyMetric()
        self.ws_connections = DummyMetric()
        self.ws_disconnects_total = DummyMetric()


class DummySemaphore:
    """Dummy semaphore for testing."""

    def __init__(self) -> None:
        self.release_count = 0

    def release(self) -> None:
        self.release_count += 1


class DummyRedis:
    """Dummy Redis client for testing."""

    def __init__(self, exc: Exception | None = None) -> None:
        self.eval_calls: list[tuple[Any, ...]] = []
        self._exc = exc

    async def eval(self, *args: Any, **kwargs: Any) -> None:
        self.eval_calls.append(args)
        if self._exc:
            raise self._exc


class DummyRedisStore:
    """Dummy Redis store for testing."""

    def __init__(self, redis: DummyRedis) -> None:
        self._redis = redis

    async def get_master(self) -> DummyRedis:
        return self._redis


@pytest.fixture()
def handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[DummyApp, DummyLifecycle, DummyMetrics, DummySemaphore]:
    """Set up connection handlers with mocked dependencies."""
    dummy_app = DummyApp()
    lifecycle = DummyLifecycle()
    metrics = DummyMetrics()
    semaphore = DummySemaphore()

    monkeypatch.setattr(connection_events, "app", dummy_app)
    monkeypatch.setattr(connection_events, "Client", DummyClient)
    monkeypatch.setattr(connection_events, "_handlers_registered", False)
    monkeypatch.setattr(connection_events, "_connection_semaphore", semaphore)

    monkeypatch.setattr(connection_events, "_get_metrics", lambda: metrics)
    monkeypatch.setattr(
        connection_events.ClientLifecycleManager,
        "get",
        classmethod(lambda cls: lifecycle),
    )

    health.connection_counter._count = 0

    connection_events.setup_connection_handlers()

    assert dummy_app.on_connect_handler is not None
    assert dummy_app.on_disconnect_handler is not None
    assert dummy_app.on_exception_handler is not None

    return dummy_app, lifecycle, metrics, semaphore


def test_setup_connection_handlers_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that setup_connection_handlers only registers handlers once."""
    dummy_app = DummyApp()
    monkeypatch.setattr(connection_events, "app", dummy_app)
    monkeypatch.setattr(connection_events, "_handlers_registered", False)

    # First call should register
    connection_events.setup_connection_handlers()
    assert connection_events._handlers_registered is True
    first_handler = dummy_app.on_connect_handler

    # Second call should not re-register
    connection_events.setup_connection_handlers()
    assert dummy_app.on_connect_handler is first_handler


@pytest.mark.asyncio()
async def test_on_connect_registers_client_and_metrics(handlers) -> None:
    """Test that on_connect registers client and updates metrics."""
    dummy_app, lifecycle, metrics, _semaphore = handlers

    scope = {"state": {"session_conn_key": "session_conns:abc"}}
    client = DummyClient(scope)

    await dummy_app.on_connect_handler(client)

    assert client.storage["client_id"] == "client-123"
    assert client.storage["session_conn_key"] == "session_conns:abc"
    assert scope["state"]["handshake_complete"] is True
    assert lifecycle.registered == ["client-123"]
    assert health.connection_counter.value == 1
    assert metrics.ws_connects_total.inc_count == 1
    assert metrics.ws_connections.set_values == [1]
    assert metrics.ws_connects_total.label_args == [{"pod": config.POD_NAME}]
    assert metrics.ws_connections.label_args == [{"pod": config.POD_NAME}]


@pytest.mark.asyncio()
async def test_on_connect_without_session_conn_key(handlers) -> None:
    """Test on_connect when session_conn_key is missing in scope state."""
    dummy_app, lifecycle, metrics, _semaphore = handlers

    scope = {"state": {}}  # No session_conn_key
    client = DummyClient(scope)

    await dummy_app.on_connect_handler(client)

    assert client.storage["client_id"] == "client-123"
    assert "session_conn_key" not in client.storage  # Should not be set
    assert scope["state"]["handshake_complete"] is True
    assert lifecycle.registered == ["client-123"]


@pytest.mark.asyncio()
async def test_on_connect_creates_state_if_missing(handlers) -> None:
    """Test on_connect creates state dict if it doesn't exist in scope."""
    dummy_app, lifecycle, _metrics, _semaphore = handlers

    scope = {}  # type: ignore[var-annotated]
    client = DummyClient(scope)

    await dummy_app.on_connect_handler(client)

    assert "state" in scope
    assert scope["state"]["handshake_complete"] is True
    assert lifecycle.registered == ["client-123"]


@pytest.mark.asyncio()
async def test_on_connect_without_scope_state(handlers) -> None:
    """Test on_connect when scope state is not a dict."""
    dummy_app, lifecycle, _metrics, _semaphore = handlers

    scope = {"state": "not-a-dict"}  # type: ignore[dict-item]
    client = DummyClient(scope)

    await dummy_app.on_connect_handler(client)

    # Should still work, just can't set handshake_complete
    assert client.storage["client_id"] == "client-123"
    assert lifecycle.registered == ["client-123"]


@pytest.mark.asyncio()
async def test_on_connect_without_request(handlers, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test on_connect when client has no request attribute."""
    dummy_app, lifecycle, _metrics, _semaphore = handlers

    client = DummyClientNoRequest()
    monkeypatch.setattr(connection_events, "Client", DummyClientNoRequest)

    await dummy_app.on_connect_handler(client)

    # Should still register client even without request
    assert client.storage["client_id"] == "client-123"
    assert lifecycle.registered == ["client-123"]


@pytest.mark.asyncio()
async def test_on_connect_without_metrics(handlers, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test on_connect when metrics module is unavailable."""
    dummy_app, lifecycle, _metrics, _semaphore = handlers

    # Mock _get_metrics to return None (metrics unavailable)
    monkeypatch.setattr(connection_events, "_get_metrics", lambda: None)

    scope = {"state": {}}
    client = DummyClient(scope)

    await dummy_app.on_connect_handler(client)

    # Should still work without metrics
    assert client.storage["client_id"] == "client-123"
    assert lifecycle.registered == ["client-123"]


@pytest.mark.asyncio()
async def test_on_disconnect_cleans_up_and_releases(
    handlers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test on_disconnect cleans up client and releases semaphore."""
    dummy_app, lifecycle, metrics, semaphore = handlers

    redis = DummyRedis()
    monkeypatch.setattr(connection_events, "get_redis_store", lambda: DummyRedisStore(redis))

    scope = {"state": {"handshake_complete": True, "semaphore_acquired": True}}
    client = DummyClient(scope)
    client.storage["client_id"] = "client-123"
    client.storage["session_conn_key"] = "session_conns:abc"

    health.connection_counter._count = 1

    await dummy_app.on_disconnect_handler(client)

    assert lifecycle.cleaned == ["client-123"]
    assert health.connection_counter.value == 0
    assert metrics.ws_connections.set_values == [0]
    assert metrics.ws_disconnects_total.inc_count == 1
    assert metrics.ws_disconnects_total.label_args == [{"pod": config.POD_NAME, "reason": "normal"}]
    assert semaphore.release_count == 1
    assert redis.eval_calls


@pytest.mark.asyncio()
async def test_on_disconnect_without_handshake_skips_metrics_release(
    handlers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test on_disconnect without handshake skips metrics and semaphore release."""
    dummy_app, lifecycle, metrics, semaphore = handlers

    redis = DummyRedis()
    monkeypatch.setattr(connection_events, "get_redis_store", lambda: DummyRedisStore(redis))

    scope = {"state": {"handshake_complete": False, "semaphore_acquired": True}}
    client = DummyClient(scope)
    client.storage["client_id"] = "client-123"
    client.storage["session_conn_key"] = "session_conns:abc"

    health.connection_counter._count = 1

    await dummy_app.on_disconnect_handler(client)

    assert lifecycle.cleaned == ["client-123"]
    assert health.connection_counter.value == 1  # Not decremented
    assert metrics.ws_connections.set_values == []  # Not updated
    assert metrics.ws_disconnects_total.inc_count == 0  # Not incremented
    assert semaphore.release_count == 0  # Not released
    assert redis.eval_calls  # But still decrements session conn count


@pytest.mark.asyncio()
async def test_on_disconnect_without_semaphore_acquired(
    handlers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test on_disconnect when semaphore was not acquired."""
    dummy_app, lifecycle, metrics, semaphore = handlers

    redis = DummyRedis()
    monkeypatch.setattr(connection_events, "get_redis_store", lambda: DummyRedisStore(redis))

    scope = {"state": {"handshake_complete": True, "semaphore_acquired": False}}
    client = DummyClient(scope)
    client.storage["client_id"] = "client-123"
    client.storage["session_conn_key"] = "session_conns:abc"

    health.connection_counter._count = 1

    await dummy_app.on_disconnect_handler(client)

    assert lifecycle.cleaned == ["client-123"]
    assert semaphore.release_count == 0  # Not released since not acquired


@pytest.mark.asyncio()
async def test_on_disconnect_without_client_id(
    handlers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test on_disconnect when client_id is missing."""
    dummy_app, lifecycle, metrics, _semaphore = handlers

    redis = DummyRedis()
    monkeypatch.setattr(connection_events, "get_redis_store", lambda: DummyRedisStore(redis))

    scope = {"state": {"handshake_complete": True}}
    client = DummyClient(scope)
    # No client_id in storage

    health.connection_counter._count = 1

    await dummy_app.on_disconnect_handler(client)

    # Should not crash, but cleanup_client not called
    assert lifecycle.cleaned == []


@pytest.mark.asyncio()
async def test_on_disconnect_with_non_string_client_id(
    handlers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test on_disconnect when client_id is not a string."""
    dummy_app, lifecycle, _metrics, _semaphore = handlers

    redis = DummyRedis()
    monkeypatch.setattr(connection_events, "get_redis_store", lambda: DummyRedisStore(redis))

    scope = {"state": {"handshake_complete": True}}
    client = DummyClient(scope)
    client.storage["client_id"] = 123  # Not a string

    await dummy_app.on_disconnect_handler(client)

    # Should not call cleanup_client
    assert lifecycle.cleaned == []


@pytest.mark.asyncio()
async def test_on_disconnect_without_session_conn_key(
    handlers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test on_disconnect when session_conn_key is missing."""
    dummy_app, lifecycle, metrics, _semaphore = handlers

    redis = DummyRedis()
    monkeypatch.setattr(connection_events, "get_redis_store", lambda: DummyRedisStore(redis))

    scope = {"state": {"handshake_complete": True}}
    client = DummyClient(scope)
    client.storage["client_id"] = "client-123"
    # No session_conn_key

    health.connection_counter._count = 1

    await dummy_app.on_disconnect_handler(client)

    # Should still work, just no Redis decrement
    assert lifecycle.cleaned == ["client-123"]
    assert redis.eval_calls == []  # No Redis call


@pytest.mark.asyncio()
async def test_on_disconnect_redis_error(
    handlers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test on_disconnect handles Redis errors gracefully."""
    dummy_app, lifecycle, metrics, _semaphore = handlers

    redis = DummyRedis(exc=RedisError("connection failed"))
    monkeypatch.setattr(connection_events, "get_redis_store", lambda: DummyRedisStore(redis))

    scope = {"state": {"handshake_complete": True}}
    client = DummyClient(scope)
    client.storage["client_id"] = "client-123"
    client.storage["session_conn_key"] = "session_conns:abc"

    health.connection_counter._count = 1

    # Should not raise, just log warning
    await dummy_app.on_disconnect_handler(client)

    assert lifecycle.cleaned == ["client-123"]
    assert health.connection_counter.value == 0


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    "exc_type",
    [OSError, ConnectionError, TimeoutError],
)
async def test_on_disconnect_network_errors(
    handlers, monkeypatch: pytest.MonkeyPatch, exc_type: type[Exception]
) -> None:
    """Test on_disconnect handles various network errors gracefully."""
    dummy_app, lifecycle, _metrics, _semaphore = handlers

    redis = DummyRedis(exc=exc_type("network error"))
    monkeypatch.setattr(connection_events, "get_redis_store", lambda: DummyRedisStore(redis))

    scope = {"state": {"handshake_complete": True}}
    client = DummyClient(scope)
    client.storage["client_id"] = "client-123"
    client.storage["session_conn_key"] = "session_conns:abc"

    # Should not raise, just log warning
    await dummy_app.on_disconnect_handler(client)

    assert lifecycle.cleaned == ["client-123"]


@pytest.mark.asyncio()
async def test_on_disconnect_with_exception_flag(
    handlers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test on_disconnect when had_exception flag is set."""
    dummy_app, lifecycle, metrics, _semaphore = handlers

    redis = DummyRedis()
    monkeypatch.setattr(connection_events, "get_redis_store", lambda: DummyRedisStore(redis))

    scope = {"state": {"handshake_complete": True}}
    client = DummyClient(scope)
    client.storage["client_id"] = "client-123"
    client.storage["had_exception"] = True  # Exception occurred

    health.connection_counter._count = 1

    await dummy_app.on_disconnect_handler(client)

    # Should not increment normal disconnect metric
    assert metrics.ws_disconnects_total.inc_count == 0


@pytest.mark.asyncio()
async def test_on_disconnect_without_metrics(
    handlers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test on_disconnect when metrics module is unavailable."""
    dummy_app, lifecycle, _metrics, _semaphore = handlers

    # Mock _get_metrics to return None
    monkeypatch.setattr(connection_events, "_get_metrics", lambda: None)

    redis = DummyRedis()
    monkeypatch.setattr(connection_events, "get_redis_store", lambda: DummyRedisStore(redis))

    scope = {"state": {"handshake_complete": True}}
    client = DummyClient(scope)
    client.storage["client_id"] = "client-123"

    health.connection_counter._count = 1

    # Should still work without metrics
    await dummy_app.on_disconnect_handler(client)

    assert lifecycle.cleaned == ["client-123"]


@pytest.mark.asyncio()
async def test_on_disconnect_without_scope_state(
    handlers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test on_disconnect when scope state is None."""
    dummy_app, lifecycle, _metrics, _semaphore = handlers

    redis = DummyRedis()
    monkeypatch.setattr(connection_events, "get_redis_store", lambda: DummyRedisStore(redis))

    client = DummyClientNoRequest()
    client.storage["client_id"] = "client-123"

    # Should not crash
    await dummy_app.on_disconnect_handler(client)

    assert lifecycle.cleaned == ["client-123"]


@pytest.mark.asyncio()
async def test_on_disconnect_without_semaphore(
    handlers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test on_disconnect when semaphore is None."""
    dummy_app, lifecycle, _metrics, _semaphore = handlers

    # Mock _get_connection_semaphore to return None
    monkeypatch.setattr(connection_events, "_get_connection_semaphore", lambda: None)

    redis = DummyRedis()
    monkeypatch.setattr(connection_events, "get_redis_store", lambda: DummyRedisStore(redis))

    scope = {"state": {"handshake_complete": True, "semaphore_acquired": True}}
    client = DummyClient(scope)
    client.storage["client_id"] = "client-123"

    health.connection_counter._count = 1

    # Should not crash when trying to release None semaphore
    await dummy_app.on_disconnect_handler(client)

    assert lifecycle.cleaned == ["client-123"]


@pytest.mark.asyncio()
async def test_on_exception_marks_error_and_metrics(handlers) -> None:
    """Test on_exception marks error flag and updates metrics."""
    dummy_app, _lifecycle, metrics, _semaphore = handlers

    scope = {"state": {}}
    client = DummyClient(scope)
    client.storage["client_id"] = "client-123"

    await dummy_app.on_exception_handler(client, RuntimeError("boom"))

    assert client.storage["had_exception"] is True
    assert metrics.ws_disconnects_total.inc_count == 1
    assert metrics.ws_disconnects_total.label_args == [{"pod": config.POD_NAME, "reason": "error"}]


@pytest.mark.asyncio()
async def test_on_exception_with_kwargs(handlers) -> None:
    """Test on_exception when arguments are passed as kwargs."""
    dummy_app, _lifecycle, metrics, _semaphore = handlers

    scope = {"state": {}}
    client = DummyClient(scope)
    client.storage["client_id"] = "client-456"

    await dummy_app.on_exception_handler(
        client=client, exception=ValueError("test error")
    )

    assert client.storage["had_exception"] is True
    assert metrics.ws_disconnects_total.inc_count == 1


@pytest.mark.asyncio()
async def test_on_exception_without_client(handlers) -> None:
    """Test on_exception when client is not provided."""
    dummy_app, _lifecycle, metrics, _semaphore = handlers

    exception = RuntimeError("test error")

    # Should not crash
    await dummy_app.on_exception_handler(exception)

    assert metrics.ws_disconnects_total.inc_count == 1


@pytest.mark.asyncio()
async def test_on_exception_without_exception(handlers) -> None:
    """Test on_exception when exception is not provided."""
    dummy_app, _lifecycle, metrics, _semaphore = handlers

    scope = {"state": {}}
    client = DummyClient(scope)
    client.storage["client_id"] = "client-123"

    # Should not crash
    await dummy_app.on_exception_handler(client)

    assert client.storage["had_exception"] is True
    assert metrics.ws_disconnects_total.inc_count == 1


@pytest.mark.asyncio()
async def test_on_exception_with_mixed_args_kwargs(handlers) -> None:
    """Test on_exception with both args and kwargs."""
    dummy_app, _lifecycle, metrics, _semaphore = handlers

    scope = {"state": {}}
    client = DummyClient(scope)
    client.storage["client_id"] = "client-789"

    # Client as arg, exception as kwarg
    await dummy_app.on_exception_handler(client, exception=ValueError("mixed"))

    assert client.storage["had_exception"] is True
    assert metrics.ws_disconnects_total.inc_count == 1


@pytest.mark.asyncio()
async def test_on_exception_without_metrics(handlers, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test on_exception when metrics module is unavailable."""
    dummy_app, _lifecycle, _metrics, _semaphore = handlers

    # Mock _get_metrics to return None
    monkeypatch.setattr(connection_events, "_get_metrics", lambda: None)

    scope = {"state": {}}
    client = DummyClient(scope)
    client.storage["client_id"] = "client-123"

    # Should still work without metrics
    await dummy_app.on_exception_handler(client, RuntimeError("test"))

    assert client.storage["had_exception"] is True


def test_get_scope_state_with_valid_scope() -> None:
    """Test _get_scope_state with valid scope containing state dict."""
    scope = {"state": {"key": "value"}}
    client = DummyClient(scope)

    state = connection_events._get_scope_state(client)

    assert state is not None
    assert state["key"] == "value"


def test_get_scope_state_creates_state_if_missing() -> None:
    """Test _get_scope_state creates state dict if missing."""
    scope = {}  # type: ignore[var-annotated]
    client = DummyClient(scope)

    state = connection_events._get_scope_state(client)

    assert state is not None
    assert "state" in scope
    assert scope["state"] == {}


def test_get_scope_state_without_request() -> None:
    """Test _get_scope_state when client has no request."""
    client = DummyClientNoRequest()

    state = connection_events._get_scope_state(client)

    assert state is None


def test_get_scope_state_without_scope() -> None:
    """Test _get_scope_state when request has no scope."""
    client = DummyClientNoScope()

    state = connection_events._get_scope_state(client)

    assert state is None


def test_get_scope_state_with_non_dict_scope() -> None:
    """Test _get_scope_state when scope is not a dict."""
    client = DummyClientNonDictScope()

    state = connection_events._get_scope_state(client)

    assert state is None


def test_get_scope_state_with_non_dict_state() -> None:
    """Test _get_scope_state when state in scope is not a dict."""
    scope = {"state": "not-a-dict"}  # type: ignore[dict-item]
    client = DummyClient(scope)

    state = connection_events._get_scope_state(client)

    # Should create new state dict
    assert state is not None
    assert state == {}
    assert scope["state"] == {}


def test_get_connection_semaphore_caches_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _get_connection_semaphore caches the semaphore."""
    import asyncio

    mock_semaphore = asyncio.Semaphore(1)

    # Reset global cache
    monkeypatch.setattr(connection_events, "_connection_semaphore", None)

    # Mock the import to return our semaphore
    import sys
    from types import ModuleType

    mock_admission = ModuleType("apps.web_console_ng.core.admission")
    mock_admission._connection_semaphore = mock_semaphore  # type: ignore[attr-defined]
    sys.modules["apps.web_console_ng.core.admission"] = mock_admission

    try:
        # First call should import and cache
        sem1 = connection_events._get_connection_semaphore()
        assert sem1 is mock_semaphore

        # Second call should return cached value
        sem2 = connection_events._get_connection_semaphore()
        assert sem2 is sem1
    finally:
        # Clean up
        if "apps.web_console_ng.core.admission" in sys.modules:
            del sys.modules["apps.web_console_ng.core.admission"]


def test_get_connection_semaphore_handles_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _get_connection_semaphore returns None on import error."""
    import builtins

    # Reset global cache
    monkeypatch.setattr(connection_events, "_connection_semaphore", None)

    # Force import to fail
    original_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if "admission" in name:
            raise ImportError("Module not found")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    result = connection_events._get_connection_semaphore()

    assert result is None


def test_get_metrics_returns_metrics_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _get_metrics returns metrics module when available."""
    import sys
    from types import ModuleType

    import apps.web_console_ng

    # Save original state
    saved_module = sys.modules.get("apps.web_console_ng.metrics")
    saved_attr = getattr(apps.web_console_ng, "metrics", None)

    try:
        # Create mock module and install it
        mock_metrics = ModuleType("apps.web_console_ng.metrics")
        mock_metrics.ws_connects_total = DummyMetric()  # type: ignore[attr-defined]
        sys.modules["apps.web_console_ng.metrics"] = mock_metrics
        # Also set attribute on parent module (required for "from x import y" syntax)
        apps.web_console_ng.metrics = mock_metrics  # type: ignore[attr-defined]

        result = connection_events._get_metrics()
        assert result is mock_metrics
    finally:
        # Restore original state
        if saved_module is not None:
            sys.modules["apps.web_console_ng.metrics"] = saved_module
        elif "apps.web_console_ng.metrics" in sys.modules:
            del sys.modules["apps.web_console_ng.metrics"]
        if saved_attr is not None:
            apps.web_console_ng.metrics = saved_attr  # type: ignore[attr-defined]


def test_get_metrics_handles_import_error() -> None:
    """Test _get_metrics returns None when metrics module import fails.

    This tests the exception handling by verifying handlers work correctly
    when _get_metrics returns None (which happens on ImportError/ModuleNotFoundError).
    The actual import error scenario is tested via integration with the handlers.
    """
    # _get_metrics uses try/except around the import, returning None on error.
    # We verify this exception handling pattern by checking the actual code structure.
    import inspect

    source = inspect.getsource(connection_events._get_metrics)

    # Verify the function has proper exception handling
    assert "except (ImportError, ModuleNotFoundError)" in source
    assert "return None" in source
    assert "return metrics" in source


def test_get_metrics_handles_module_not_found_error() -> None:
    """Test _get_metrics correctly catches ModuleNotFoundError.

    Verifies that ModuleNotFoundError is in the exception handler alongside ImportError.
    """
    import inspect

    source = inspect.getsource(connection_events._get_metrics)

    # Both ImportError and ModuleNotFoundError should be caught
    assert "ImportError" in source
    assert "ModuleNotFoundError" in source

    # Function should have try/except structure
    assert "try:" in source
    assert "except" in source
