# tests/apps/web_console_ng/test_admission.py
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.web_console_ng import config
from apps.web_console_ng.core import admission


def _build_scope(cookie: str | None = None) -> dict[str, Any]:
    headers = []
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    headers.append((b"user-agent", b"pytest"))
    return {
        "type": "websocket",
        "headers": headers,
        "client": ("10.0.0.5", 12345),
        "state": {},
    }


def _session_cookie(session_id: str = "session") -> str:
    # Minimal signed cookie format: {session_id}.{key_id}:{signature}
    return f"{config.SESSION_COOKIE_NAME}={session_id}.k1:signature"


async def _run_app(app, scope: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    async def _send(message: dict[str, Any]) -> None:
        events.append(message)

    async def _receive() -> dict[str, Any]:
        return {"type": "websocket.connect"}

    await app(scope, _receive, _send)
    return events


@pytest.mark.asyncio()
async def test_admission_rejects_when_draining(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admission.health, "is_draining", True)
    monkeypatch.setattr(admission, "_increment_rejection", lambda reason: None)

    called = {"inner": False}

    async def _inner(scope, receive, send):
        called["inner"] = True

    middleware = admission.AdmissionControlMiddleware(_inner)
    events = await _run_app(middleware, _build_scope())

    assert called["inner"] is False
    assert events[0]["type"] == "websocket.http.response.start"
    assert events[0]["status"] == 503


@pytest.mark.asyncio()
async def test_invalid_session_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admission.health, "is_draining", False)

    session_store = MagicMock()
    session_store.validate_session = AsyncMock(return_value=None)
    monkeypatch.setattr(admission, "get_session_store", lambda: session_store)
    monkeypatch.setattr(admission, "_increment_rejection", lambda reason: None)

    cookie = _session_cookie()
    middleware = admission.AdmissionControlMiddleware(lambda s, r, se: None)
    events = await _run_app(middleware, _build_scope(cookie))

    assert events[0]["status"] == 401


@pytest.mark.asyncio()
async def test_session_limit_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admission.health, "is_draining", False)

    session_store = MagicMock()
    session_store.validate_session = AsyncMock(return_value={"user": {"user_id": "u1"}})
    monkeypatch.setattr(admission, "get_session_store", lambda: session_store)
    monkeypatch.setattr(admission, "_increment_rejection", lambda reason: None)

    redis = AsyncMock()
    redis.eval = AsyncMock(return_value=admission.MAX_CONNECTIONS_PER_SESSION + 1)
    redis.decr = AsyncMock()
    redis_store = MagicMock()
    redis_store.get_master = AsyncMock(return_value=redis)
    monkeypatch.setattr(admission, "get_redis_store", lambda: redis_store)

    # Mock the semaphore acquisition to always succeed for this test
    monkeypatch.setattr(
        admission.AdmissionControlMiddleware,
        "_try_acquire_semaphore",
        AsyncMock(return_value=True),
    )

    cookie = _session_cookie()
    middleware = admission.AdmissionControlMiddleware(lambda s, r, se: None)
    events = await _run_app(middleware, _build_scope(cookie))

    assert events[0]["status"] == 429
    redis.decr.assert_called_once()


@pytest.mark.asyncio()
async def test_capacity_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admission.health, "is_draining", False)
    monkeypatch.setattr(admission, "_increment_rejection", lambda reason: None)
    monkeypatch.setattr(admission, "_connection_semaphore", asyncio.Semaphore(0))

    middleware = admission.AdmissionControlMiddleware(lambda s, r, se: None)
    events = await _run_app(middleware, _build_scope())

    assert events[0]["status"] == 503


@pytest.mark.asyncio()
async def test_handshake_failure_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admission.health, "is_draining", False)

    session_store = MagicMock()
    session_store.validate_session = AsyncMock(return_value={"user": {"user_id": "u1"}})
    monkeypatch.setattr(admission, "get_session_store", lambda: session_store)
    monkeypatch.setattr(admission, "_increment_rejection", lambda reason: None)

    redis = AsyncMock()
    redis.eval = AsyncMock(return_value=1)
    redis_store = MagicMock()
    redis_store.get_master = AsyncMock(return_value=redis)
    monkeypatch.setattr(admission, "get_redis_store", lambda: redis_store)

    # Mock the semaphore acquisition to always succeed for this test
    # Also track that release was called for cleanup verification
    semaphore_released = {"count": 0}

    def mock_release():
        semaphore_released["count"] += 1

    monkeypatch.setattr(
        admission.AdmissionControlMiddleware,
        "_try_acquire_semaphore",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(admission._connection_semaphore, "release", mock_release)

    async def _inner(scope, receive, send):
        # Simulate handshake failure (do not set handshake_complete)
        return None

    cookie = _session_cookie()
    middleware = admission.AdmissionControlMiddleware(_inner)
    await _run_app(middleware, _build_scope(cookie))

    # Verify Redis eval called for both INCR and DECR Lua scripts
    assert redis.eval.call_count >= 2
    # Verify semaphore was released (handshake failed, so admission.py releases it)
    assert semaphore_released["count"] == 1


@pytest.mark.asyncio()
async def test_non_websocket_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-websocket requests should pass through without admission control."""
    monkeypatch.setattr(admission.health, "is_draining", False)

    called = {"inner": False}

    async def _inner(scope, receive, send):
        called["inner"] = True

    middleware = admission.AdmissionControlMiddleware(_inner)

    # HTTP scope instead of websocket
    scope: dict[str, Any] = {"type": "http", "headers": [], "client": ("10.0.0.5", 12345)}
    await middleware(scope, lambda: None, lambda msg: None)

    assert called["inner"] is True


@pytest.mark.asyncio()
async def test_session_validation_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """TimeoutError during session validation returns 503."""
    monkeypatch.setattr(admission.health, "is_draining", False)

    session_store = MagicMock()
    session_store.validate_session = AsyncMock(side_effect=TimeoutError("Validation timeout"))
    monkeypatch.setattr(admission, "get_session_store", lambda: session_store)
    monkeypatch.setattr(admission, "_increment_rejection", lambda reason: None)

    cookie = _session_cookie()
    middleware = admission.AdmissionControlMiddleware(lambda s, r, se: None)
    events = await _run_app(middleware, _build_scope(cookie))

    assert events[0]["status"] == 503
    # Check for retry-after header
    headers = dict(events[0]["headers"])
    assert b"retry-after" in headers


@pytest.mark.asyncio()
async def test_session_validation_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Generic exception during session validation returns 503."""
    monkeypatch.setattr(admission.health, "is_draining", False)

    session_store = MagicMock()
    session_store.validate_session = AsyncMock(side_effect=Exception("Database error"))
    monkeypatch.setattr(admission, "get_session_store", lambda: session_store)
    monkeypatch.setattr(admission, "_increment_rejection", lambda reason: None)

    cookie = _session_cookie()
    middleware = admission.AdmissionControlMiddleware(lambda s, r, se: None)
    events = await _run_app(middleware, _build_scope(cookie))

    assert events[0]["status"] == 503


@pytest.mark.asyncio()
async def test_non_session_success_releases_semaphore(monkeypatch: pytest.MonkeyPatch) -> None:
    """Successful non-session request acquires and releases semaphore."""
    monkeypatch.setattr(admission.health, "is_draining", False)
    monkeypatch.setattr(admission, "_increment_rejection", lambda reason: None)

    # Track semaphore release
    semaphore_released = {"count": 0}

    def mock_release() -> None:
        semaphore_released["count"] += 1

    monkeypatch.setattr(
        admission.AdmissionControlMiddleware,
        "_try_acquire_semaphore",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(admission._connection_semaphore, "release", mock_release)

    called = {"inner": False}

    async def _inner(scope, receive, send):
        called["inner"] = True

    middleware = admission.AdmissionControlMiddleware(_inner)
    # No cookie = non-session path
    await _run_app(middleware, _build_scope())

    assert called["inner"] is True
    assert semaphore_released["count"] == 1


@pytest.mark.asyncio()
async def test_session_success_with_handshake_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    """Successful session with handshake_complete=True does NOT release semaphore in admission."""
    monkeypatch.setattr(admission.health, "is_draining", False)

    session_store = MagicMock()
    session_store.validate_session = AsyncMock(return_value={"user": {"user_id": "u1"}})
    monkeypatch.setattr(admission, "get_session_store", lambda: session_store)
    monkeypatch.setattr(admission, "_increment_rejection", lambda reason: None)

    redis = AsyncMock()
    redis.eval = AsyncMock(return_value=1)
    redis_store = MagicMock()
    redis_store.get_master = AsyncMock(return_value=redis)
    monkeypatch.setattr(admission, "get_redis_store", lambda: redis_store)

    # Track semaphore release
    semaphore_released = {"count": 0}

    def mock_release():
        semaphore_released["count"] += 1

    monkeypatch.setattr(
        admission.AdmissionControlMiddleware,
        "_try_acquire_semaphore",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(admission._connection_semaphore, "release", mock_release)

    async def _inner(scope, receive, send):
        # Simulate successful handshake
        scope["state"]["handshake_complete"] = True

    cookie = _session_cookie()
    middleware = admission.AdmissionControlMiddleware(_inner)
    await _run_app(middleware, _build_scope(cookie))

    # Semaphore should NOT be released by admission.py (connection_events.py handles it)
    assert semaphore_released["count"] == 0
    # Redis INCR was called but DECR was NOT (handshake completed successfully)
    assert redis.eval.call_count == 1
