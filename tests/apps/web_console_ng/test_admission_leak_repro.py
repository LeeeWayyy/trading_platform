# tests/apps/web_console_ng/test_admission_leak_repro.py
from __future__ import annotations

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


async def _run_app(app, scope: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    async def _send(message: dict[str, Any]) -> None:
        events.append(message)

    async def _receive() -> dict[str, Any]:
        return {"type": "websocket.connect"}

    try:
        await app(scope, _receive, _send)
    except Exception:
        pass
    return events


@pytest.mark.asyncio()
async def test_semaphore_release_on_redis_error_before_incr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test scenario: Exception occurs getting Redis connection (before INCR)."""
    monkeypatch.setattr(admission.health, "is_draining", False)

    session_store = MagicMock()
    session_store.validate_session = AsyncMock(return_value={"user": {"user_id": "u1"}})
    monkeypatch.setattr(admission, "get_session_store", lambda: session_store)
    monkeypatch.setattr(admission, "_increment_rejection", lambda reason: None)

    # Mock Redis to raise exception on get_master
    redis_store = MagicMock()
    redis_store.get_master = AsyncMock(side_effect=Exception("Redis connection failed"))
    monkeypatch.setattr(admission, "get_redis_store", lambda: redis_store)

    # Mock semaphore
    semaphore = AsyncMock()
    semaphore.acquire = AsyncMock()
    semaphore.release = MagicMock()
    # We need to replace the semaphore in the module
    monkeypatch.setattr(admission, "_connection_semaphore", semaphore)

    # Force acquire to succeed
    monkeypatch.setattr(
        admission.AdmissionControlMiddleware,
        "_try_acquire_semaphore",
        AsyncMock(return_value=True),
    )

    cookie = f"{config.SESSION_COOKIE_NAME}=session"
    middleware = admission.AdmissionControlMiddleware(lambda s, r, se: None)

    await _run_app(middleware, _build_scope(cookie))

    # Verification: Release was called (CRITICAL check for leak fix)
    assert semaphore.release.called


@pytest.mark.asyncio()
async def test_semaphore_release_on_redis_error_during_incr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test scenario: Exception occurs during Redis INCR eval."""
    monkeypatch.setattr(admission.health, "is_draining", False)

    session_store = MagicMock()
    session_store.validate_session = AsyncMock(return_value={"user": {"user_id": "u1"}})
    monkeypatch.setattr(admission, "get_session_store", lambda: session_store)
    monkeypatch.setattr(admission, "_increment_rejection", lambda reason: None)

    # Mock Redis to raise exception on eval
    redis = AsyncMock()
    redis.eval = AsyncMock(side_effect=Exception("Redis Lua failed"))
    redis_store = MagicMock()
    redis_store.get_master = AsyncMock(return_value=redis)
    monkeypatch.setattr(admission, "get_redis_store", lambda: redis_store)

    # Mock semaphore
    semaphore = AsyncMock()
    semaphore.acquire = AsyncMock()
    semaphore.release = MagicMock()
    monkeypatch.setattr(admission, "_connection_semaphore", semaphore)

    # Force acquire to succeed
    monkeypatch.setattr(
        admission.AdmissionControlMiddleware,
        "_try_acquire_semaphore",
        AsyncMock(return_value=True),
    )

    cookie = f"{config.SESSION_COOKIE_NAME}=session"
    middleware = admission.AdmissionControlMiddleware(lambda s, r, se: None)

    await _run_app(middleware, _build_scope(cookie))

    # Verification: Release was called
    assert semaphore.release.called
    # Also verify DECR was NOT called (because INCR failed)
    assert redis.eval.call_count == 1


@pytest.mark.asyncio()
async def test_semaphore_release_and_decr_on_app_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test scenario: Exception occurs inside the application (or before) after Redis INCR."""
    monkeypatch.setattr(admission.health, "is_draining", False)

    session_store = MagicMock()
    session_store.validate_session = AsyncMock(return_value={"user": {"user_id": "u1"}})
    monkeypatch.setattr(admission, "get_session_store", lambda: session_store)
    monkeypatch.setattr(admission, "_increment_rejection", lambda reason: None)

    # Mock Redis success
    redis = AsyncMock()
    redis.eval = AsyncMock(side_effect=[1, 0])  # First INCR, then DECR
    redis_store = MagicMock()
    redis_store.get_master = AsyncMock(return_value=redis)
    monkeypatch.setattr(admission, "get_redis_store", lambda: redis_store)

    # Mock semaphore
    semaphore = AsyncMock()
    semaphore.acquire = AsyncMock()
    semaphore.release = MagicMock()
    monkeypatch.setattr(admission, "_connection_semaphore", semaphore)

    # Force acquire to succeed
    monkeypatch.setattr(
        admission.AdmissionControlMiddleware,
        "_try_acquire_semaphore",
        AsyncMock(return_value=True),
    )

    async def _inner_raises(scope, receive, send):
        raise RuntimeError("App crashed")

    cookie = f"{config.SESSION_COOKIE_NAME}=session"
    middleware = admission.AdmissionControlMiddleware(_inner_raises)

    await _run_app(middleware, _build_scope(cookie))

    # Verification: Release was called
    assert semaphore.release.called
    # Verify DECR was called (redis.eval called twice)
    assert redis.eval.call_count == 2
