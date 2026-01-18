"""Tests for NiceGUI connection event handlers."""

from __future__ import annotations

from typing import Any

import pytest

from apps.web_console_ng import config
from apps.web_console_ng.core import connection_events
from apps.web_console_ng.core import health


class DummyApp:
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
    def __init__(self, scope: dict[str, Any]) -> None:
        self.scope = scope


class DummyClient:
    def __init__(self, scope: dict[str, Any]) -> None:
        self.storage: dict[str, Any] = {}
        self.request = DummyRequest(scope)


class DummyLifecycle:
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
    def __init__(self) -> None:
        self.ws_connects_total = DummyMetric()
        self.ws_connections = DummyMetric()
        self.ws_disconnects_total = DummyMetric()


class DummySemaphore:
    def __init__(self) -> None:
        self.release_count = 0

    def release(self) -> None:
        self.release_count += 1


class DummyRedis:
    def __init__(self) -> None:
        self.eval_calls: list[tuple[Any, ...]] = []

    async def eval(self, *args: Any, **kwargs: Any) -> None:
        self.eval_calls.append(args)


class DummyRedisStore:
    def __init__(self, redis: DummyRedis) -> None:
        self._redis = redis

    async def get_master(self) -> DummyRedis:
        return self._redis


@pytest.fixture()
def handlers(monkeypatch: pytest.MonkeyPatch) -> tuple[DummyApp, DummyLifecycle, DummyMetrics, DummySemaphore]:
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


@pytest.mark.asyncio()
async def test_on_connect_registers_client_and_metrics(handlers) -> None:
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
async def test_on_disconnect_cleans_up_and_releases(handlers, monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert health.connection_counter.value == 1
    assert metrics.ws_connections.set_values == []
    assert metrics.ws_disconnects_total.inc_count == 0
    assert semaphore.release_count == 0
    assert redis.eval_calls


@pytest.mark.asyncio()
async def test_on_exception_marks_error_and_metrics(handlers) -> None:
    dummy_app, _lifecycle, metrics, _semaphore = handlers

    scope = {"state": {}}
    client = DummyClient(scope)
    client.storage["client_id"] = "client-123"

    await dummy_app.on_exception_handler(client, RuntimeError("boom"))

    assert client.storage["had_exception"] is True
    assert metrics.ws_disconnects_total.inc_count == 1
    assert metrics.ws_disconnects_total.label_args == [{"pod": config.POD_NAME, "reason": "error"}]
