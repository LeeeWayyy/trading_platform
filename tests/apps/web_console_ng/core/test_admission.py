"""Tests for AdmissionControlMiddleware."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import RedisError

from apps.web_console_ng import config
from apps.web_console_ng.auth.session_store import SessionValidationError
from apps.web_console_ng.core import admission, health
from apps.web_console_ng.core.admission import AdmissionControlMiddleware


class DummySemaphore:
    def __init__(self) -> None:
        self.release_count = 0

    def release(self) -> None:
        self.release_count += 1


class DummyRedis:
    def __init__(self, eval_results: list[int] | None = None, exc: Exception | None = None) -> None:
        self.eval_calls: list[tuple[Any, ...]] = []
        self._eval_results = eval_results or []
        self._exc = exc

    async def eval(self, *args: Any, **kwargs: Any) -> int:
        self.eval_calls.append(args)
        if self._exc:
            raise self._exc
        if self._eval_results:
            return self._eval_results.pop(0)
        return 0


class DummyRedisStore:
    def __init__(self, redis: DummyRedis) -> None:
        self._redis = redis

    async def get_master(self) -> DummyRedis:
        return self._redis


async def _noop_receive() -> dict[str, str]:
    return {"type": "websocket.connect"}


def _make_send_collector() -> tuple[list[dict[str, Any]], Any]:
    messages: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    return messages, send


def _make_scope(cookie_value: str | None = None) -> dict[str, Any]:
    headers: list[tuple[bytes, bytes]] = []
    if cookie_value:
        headers.append((b"cookie", cookie_value.encode()))
    return {"type": "websocket", "headers": headers}


@pytest.fixture()
def rejection_tracker(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    reasons: list[str] = []

    def _record(reason: str) -> None:
        reasons.append(reason)

    monkeypatch.setattr(admission, "_increment_rejection", _record)
    return reasons


@pytest.fixture()
def reset_draining(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "is_draining", False)


@pytest.mark.asyncio()
async def test_non_websocket_passthrough(rejection_tracker: list[str]) -> None:
    called: list[dict[str, Any]] = []

    async def app(scope, receive, send):
        called.append(scope)

    middleware = AdmissionControlMiddleware(app)

    scope = {"type": "http"}
    messages, send = _make_send_collector()
    await middleware(scope, _noop_receive, send)

    assert called == [scope]
    assert messages == []
    assert rejection_tracker == []


@pytest.mark.asyncio()
async def test_draining_rejects(reset_draining: None, rejection_tracker: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "is_draining", True)

    async def app(scope, receive, send):
        raise AssertionError("app should not be called")

    middleware = AdmissionControlMiddleware(app)
    messages, send = _make_send_collector()

    await middleware(_make_scope(), _noop_receive, send)

    assert rejection_tracker == ["draining"]
    assert messages[0]["status"] == 503
    assert (b"retry-after", b"30") in messages[0]["headers"]
    assert b"Server draining" in messages[1]["body"]


@pytest.mark.asyncio()
async def test_invalid_session_rejected(
    reset_draining: None, rejection_tracker: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    session_store = AsyncMock()
    session_store.validate_session = AsyncMock(return_value=None)
    monkeypatch.setattr(admission, "get_session_store", lambda: session_store)
    monkeypatch.setattr(admission, "extract_trusted_client_ip", lambda *_: "1.2.3.4")

    middleware = AdmissionControlMiddleware(lambda *_: None)
    cookie = f"{config.SESSION_COOKIE_NAME}=cookie"
    messages, send = _make_send_collector()

    await middleware(_make_scope(cookie), _noop_receive, send)

    assert rejection_tracker == ["invalid_session"]
    assert messages[0]["status"] == 401
    assert b"Session expired" in messages[1]["body"]


@pytest.mark.asyncio()
async def test_session_validation_timeout(
    reset_draining: None, rejection_tracker: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    session_store = AsyncMock()
    session_store.validate_session = AsyncMock()
    monkeypatch.setattr(admission, "get_session_store", lambda: session_store)

    async def _raise_timeout(*args, **kwargs):
        raise TimeoutError

    monkeypatch.setattr(admission.asyncio, "wait_for", _raise_timeout)

    middleware = AdmissionControlMiddleware(lambda *_: None)
    cookie = f"{config.SESSION_COOKIE_NAME}=cookie"
    messages, send = _make_send_collector()

    await middleware(_make_scope(cookie), _noop_receive, send)

    assert rejection_tracker == ["timeout"]
    assert messages[0]["status"] == 503
    assert (b"retry-after", b"5") in messages[0]["headers"]
    assert b"Service timeout" in messages[1]["body"]


@pytest.mark.asyncio()
async def test_session_validation_error(
    reset_draining: None, rejection_tracker: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    session_store = AsyncMock()
    session_store.validate_session = AsyncMock(side_effect=SessionValidationError("boom"))
    monkeypatch.setattr(admission, "get_session_store", lambda: session_store)
    monkeypatch.setattr(admission, "extract_trusted_client_ip", lambda *_: "1.2.3.4")

    middleware = AdmissionControlMiddleware(lambda *_: None)
    cookie = f"{config.SESSION_COOKIE_NAME}=cookie"
    messages, send = _make_send_collector()

    await middleware(_make_scope(cookie), _noop_receive, send)

    assert rejection_tracker == ["service_unavailable"]
    assert messages[0]["status"] == 503
    assert b"Service unavailable" in messages[1]["body"]


@pytest.mark.asyncio()
async def test_value_error_invalid_session(
    reset_draining: None, rejection_tracker: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    session_store = AsyncMock()
    session_store.validate_session = AsyncMock(return_value={"user": "ok"})
    monkeypatch.setattr(admission, "get_session_store", lambda: session_store)
    monkeypatch.setattr(admission, "extract_trusted_client_ip", lambda *_: "1.2.3.4")
    monkeypatch.setattr(admission, "extract_session_id", lambda *_: (_ for _ in ()).throw(ValueError("bad")))

    middleware = AdmissionControlMiddleware(lambda *_: None)
    cookie = f"{config.SESSION_COOKIE_NAME}=cookie"
    messages, send = _make_send_collector()

    await middleware(_make_scope(cookie), _noop_receive, send)

    assert rejection_tracker == ["invalid_session"]
    assert messages[0]["status"] == 401
    assert b"Invalid session" in messages[1]["body"]


@pytest.mark.asyncio()
async def test_session_limit_rejected_and_semaphore_released(
    reset_draining: None, rejection_tracker: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    session_store = AsyncMock()
    session_store.validate_session = AsyncMock(return_value={"user": "ok"})
    monkeypatch.setattr(admission, "get_session_store", lambda: session_store)
    monkeypatch.setattr(admission, "extract_trusted_client_ip", lambda *_: "1.2.3.4")
    monkeypatch.setattr(admission, "extract_session_id", lambda *_: "sess-1")

    redis = DummyRedis(eval_results=[admission.MAX_CONNECTIONS_PER_SESSION + 1, 0])
    monkeypatch.setattr(admission, "get_redis_store", lambda: DummyRedisStore(redis))

    async def _acquire_ok(self) -> bool:
        return True

    monkeypatch.setattr(AdmissionControlMiddleware, "_try_acquire_semaphore", _acquire_ok)

    dummy_semaphore = DummySemaphore()
    monkeypatch.setattr(admission, "_connection_semaphore", dummy_semaphore)

    middleware = AdmissionControlMiddleware(lambda *_: None)
    cookie = f"{config.SESSION_COOKIE_NAME}=cookie"
    messages, send = _make_send_collector()

    await middleware(_make_scope(cookie), _noop_receive, send)

    assert rejection_tracker == ["session_limit"]
    assert messages[0]["status"] == 429
    assert b"Too many connections for session" in messages[1]["body"]
    assert dummy_semaphore.release_count == 1
    assert redis.eval_calls[0][0] == admission._INCR_SESSION_CONN_LUA
    assert redis.eval_calls[1][0] == admission._DECR_SESSION_CONN_LUA


@pytest.mark.asyncio()
async def test_session_success_handshake_complete_skips_release(
    reset_draining: None, rejection_tracker: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    session_store = AsyncMock()
    session_store.validate_session = AsyncMock(return_value={"user": "ok"})
    monkeypatch.setattr(admission, "get_session_store", lambda: session_store)
    monkeypatch.setattr(admission, "extract_trusted_client_ip", lambda *_: "1.2.3.4")
    monkeypatch.setattr(admission, "extract_session_id", lambda *_: "sess-1")

    redis = DummyRedis(eval_results=[1])
    monkeypatch.setattr(admission, "get_redis_store", lambda: DummyRedisStore(redis))

    async def _acquire_ok(self) -> bool:
        return True

    monkeypatch.setattr(AdmissionControlMiddleware, "_try_acquire_semaphore", _acquire_ok)

    dummy_semaphore = DummySemaphore()
    monkeypatch.setattr(admission, "_connection_semaphore", dummy_semaphore)

    async def app(scope, receive, send):
        state = scope.setdefault("state", {})
        state["handshake_complete"] = True

    middleware = AdmissionControlMiddleware(app)
    cookie = f"{config.SESSION_COOKIE_NAME}=cookie"

    messages, send = _make_send_collector()
    scope = _make_scope(cookie)
    await middleware(scope, _noop_receive, send)

    assert rejection_tracker == []
    assert scope["state"]["session_conn_key"] == "session_conns:sess-1"
    assert scope["state"]["handshake_complete"] is True
    assert dummy_semaphore.release_count == 0


@pytest.mark.asyncio()
async def test_session_redis_error_fails_closed(
    reset_draining: None, rejection_tracker: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    session_store = AsyncMock()
    session_store.validate_session = AsyncMock(return_value={"user": "ok"})
    monkeypatch.setattr(admission, "get_session_store", lambda: session_store)
    monkeypatch.setattr(admission, "extract_trusted_client_ip", lambda *_: "1.2.3.4")
    monkeypatch.setattr(admission, "extract_session_id", lambda *_: "sess-1")

    redis = DummyRedis(exc=RedisError("boom"))
    monkeypatch.setattr(admission, "get_redis_store", lambda: DummyRedisStore(redis))

    async def _acquire_ok(self) -> bool:
        return True

    monkeypatch.setattr(AdmissionControlMiddleware, "_try_acquire_semaphore", _acquire_ok)

    dummy_semaphore = DummySemaphore()
    monkeypatch.setattr(admission, "_connection_semaphore", dummy_semaphore)

    middleware = AdmissionControlMiddleware(lambda *_: None)
    cookie = f"{config.SESSION_COOKIE_NAME}=cookie"
    messages, send = _make_send_collector()

    await middleware(_make_scope(cookie), _noop_receive, send)

    assert rejection_tracker == ["error"]
    assert messages[0]["status"] == 503
    assert b"Service error" in messages[1]["body"]
    assert dummy_semaphore.release_count == 1


@pytest.mark.asyncio()
async def test_non_session_capacity_rejected(
    reset_draining: None, rejection_tracker: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _acquire_fail(self) -> bool:
        return False

    monkeypatch.setattr(AdmissionControlMiddleware, "_try_acquire_semaphore", _acquire_fail)

    middleware = AdmissionControlMiddleware(lambda *_: None)
    messages, send = _make_send_collector()

    await middleware(_make_scope(), _noop_receive, send)

    assert rejection_tracker == ["capacity"]
    assert messages[0]["status"] == 503
    assert b"Server at capacity" in messages[1]["body"]


@pytest.mark.asyncio()
async def test_non_session_success_releases_semaphore(
    reset_draining: None, rejection_tracker: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _acquire_ok(self) -> bool:
        return True

    monkeypatch.setattr(AdmissionControlMiddleware, "_try_acquire_semaphore", _acquire_ok)

    dummy_semaphore = DummySemaphore()
    monkeypatch.setattr(admission, "_connection_semaphore", dummy_semaphore)

    called: list[dict[str, Any]] = []

    async def app(scope, receive, send):
        called.append(scope)

    middleware = AdmissionControlMiddleware(app)
    messages, send = _make_send_collector()

    await middleware(_make_scope(), _noop_receive, send)

    assert called
    assert rejection_tracker == []
    assert dummy_semaphore.release_count == 1
    assert messages == []


@pytest.mark.asyncio()
async def test_try_acquire_semaphore_true(monkeypatch: pytest.MonkeyPatch) -> None:
    semaphore = asyncio.Semaphore(1)
    monkeypatch.setattr(admission, "_connection_semaphore", semaphore)

    middleware = AdmissionControlMiddleware(lambda *_: None)
    assert await middleware._try_acquire_semaphore() is True
    semaphore.release()


@pytest.mark.asyncio()
async def test_try_acquire_semaphore_false(monkeypatch: pytest.MonkeyPatch) -> None:
    semaphore = asyncio.Semaphore(0)
    monkeypatch.setattr(admission, "_connection_semaphore", semaphore)

    middleware = AdmissionControlMiddleware(lambda *_: None)
    assert await middleware._try_acquire_semaphore() is False
