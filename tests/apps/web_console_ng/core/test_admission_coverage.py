"""Additional tests for AdmissionControlMiddleware to reach 85%+ branch coverage.

This test file complements test_admission.py by covering edge cases and branches
that were previously untested:
- Metrics increment failure handling
- Redis cleanup failures in finally block
- Scope state edge cases (non-dict state)
- OSError and ConnectionError handling
- Generic exception handler
- Session validation infrastructure errors
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.exceptions import RedisError

from apps.web_console_ng import config
from apps.web_console_ng.core import admission, health
from apps.web_console_ng.core.admission import AdmissionControlMiddleware


class DummySemaphore:
    """Mock semaphore for tracking release calls."""

    def __init__(self) -> None:
        self.release_count = 0

    def release(self) -> None:
        self.release_count += 1


class DummyRedis:
    """Mock Redis client for testing eval operations."""

    def __init__(
        self, eval_results: list[int] | None = None, exc: Exception | None = None
    ) -> None:
        self.eval_calls: list[tuple[Any, ...]] = []
        self._eval_results = eval_results or []
        self._exc = exc
        self._call_count = 0

    async def eval(self, *args: Any, **kwargs: Any) -> int:
        self.eval_calls.append(args)
        self._call_count += 1

        # Raise exception on specific call if configured
        if isinstance(self._exc, list):
            # If exc is a list, raise different exceptions per call
            if self._call_count <= len(self._exc):
                exc_to_raise = self._exc[self._call_count - 1]
                if exc_to_raise is not None:
                    raise exc_to_raise
        elif self._exc:
            raise self._exc

        if self._eval_results:
            return self._eval_results.pop(0)
        return 0


class DummyRedisStore:
    """Mock Redis store for testing."""

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
    headers.append((b"user-agent", b"pytest-client"))
    return {"type": "websocket", "headers": headers, "client": ("10.0.0.1", 8080)}


@pytest.fixture()
def rejection_tracker(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Track rejection reasons for verification."""
    reasons: list[str] = []

    def _record(reason: str) -> None:
        reasons.append(reason)

    monkeypatch.setattr(admission, "_increment_rejection", _record)
    return reasons


@pytest.fixture()
def reset_draining(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure health.is_draining is False."""
    monkeypatch.setattr(health, "is_draining", False)


@pytest.mark.asyncio()
async def test_increment_rejection_metrics_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _increment_rejection handles missing metrics gracefully (ImportError).

    This covers lines 62-72 where metrics import may fail during C3 (cold start).
    The function should not block admission control if metrics are unavailable.
    """
    # Temporarily hide metrics module
    import sys

    original_metrics = sys.modules.get("apps.web_console_ng.metrics")
    if "apps.web_console_ng.metrics" in sys.modules:
        del sys.modules["apps.web_console_ng.metrics"]

    try:
        # Reload admission to trigger import-time metrics check
        import importlib

        importlib.reload(admission)

        # _increment_rejection should not raise even if metrics unavailable
        admission._increment_rejection("test_reason")

    finally:
        # Restore metrics module
        if original_metrics:
            sys.modules["apps.web_console_ng.metrics"] = original_metrics


@pytest.mark.asyncio()
async def test_increment_rejection_metrics_attribute_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _increment_rejection handles AttributeError on metrics object.

    This covers the AttributeError branch in lines 62-72 where metrics may
    be imported but connections_rejected_total is not initialized yet.
    """
    # Mock metrics module without connections_rejected_total
    mock_metrics = MagicMock()
    del mock_metrics.connections_rejected_total  # Make attribute access raise

    monkeypatch.setattr(admission, "metrics", mock_metrics, raising=False)

    # Should not raise
    admission._increment_rejection("test_reason")


@pytest.mark.asyncio()
async def test_redis_decrement_failure_in_cleanup(
    reset_draining: None,
    rejection_tracker: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test Redis decrement failure during finally block cleanup.

    This covers lines 172-178 where Redis decrement may fail with OSError
    or ConnectionError during cleanup. The middleware should log but not crash.
    """
    session_store = AsyncMock()
    session_store.validate_session = AsyncMock(return_value={"user": "ok"})
    monkeypatch.setattr(admission, "get_session_store", lambda: session_store)
    monkeypatch.setattr(admission, "extract_trusted_client_ip", lambda *_: "1.2.3.4")
    monkeypatch.setattr(admission, "extract_session_id", lambda *_: "sess-cleanup")

    # Redis succeeds on INCR but fails on DECR with ConnectionError
    redis = DummyRedis(eval_results=[1], exc=[None, ConnectionError("Redis down")])
    monkeypatch.setattr(admission, "get_redis_store", lambda: DummyRedisStore(redis))

    async def _acquire_ok(self) -> bool:
        return True

    monkeypatch.setattr(AdmissionControlMiddleware, "_try_acquire_semaphore", _acquire_ok)

    dummy_semaphore = DummySemaphore()
    monkeypatch.setattr(admission, "_connection_semaphore", dummy_semaphore)

    async def app(scope, receive, send):
        # Handshake fails - triggers cleanup path
        pass

    middleware = AdmissionControlMiddleware(app)
    cookie = f"{config.SESSION_COOKIE_NAME}=cookie"
    messages, send = _make_send_collector()

    # Should not raise despite Redis failure in cleanup
    await middleware(_make_scope(cookie), _noop_receive, send)

    # Verify cleanup was attempted
    assert redis.eval_calls  # INCR and attempted DECR
    assert dummy_semaphore.release_count == 1  # Semaphore still released


@pytest.mark.asyncio()
async def test_redis_oserror_during_connection_increment(
    reset_draining: None,
    rejection_tracker: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test OSError during Redis connection increment.

    This covers lines 214-223 where OSError can occur during get_master() or eval().
    The middleware should fail closed with 503 and release the semaphore.
    """
    session_store = AsyncMock()
    session_store.validate_session = AsyncMock(return_value={"user": "ok"})
    monkeypatch.setattr(admission, "get_session_store", lambda: session_store)
    monkeypatch.setattr(admission, "extract_trusted_client_ip", lambda *_: "1.2.3.4")
    monkeypatch.setattr(admission, "extract_session_id", lambda *_: "sess-os")

    # Redis raises OSError immediately
    redis = DummyRedis(exc=OSError("Network unreachable"))
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
async def test_connection_error_during_session_validation(
    reset_draining: None,
    rejection_tracker: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test ConnectionError during session validation (before Redis).

    This covers the ConnectionError branch in lines 214-223 where the error
    occurs during validate_session() before reaching Redis operations.
    """
    session_store = AsyncMock()
    session_store.validate_session = AsyncMock(
        side_effect=ConnectionError("Database connection lost")
    )
    monkeypatch.setattr(admission, "get_session_store", lambda: session_store)
    monkeypatch.setattr(admission, "extract_trusted_client_ip", lambda *_: "1.2.3.4")

    middleware = AdmissionControlMiddleware(lambda *_: None)
    cookie = f"{config.SESSION_COOKIE_NAME}=cookie"
    messages, send = _make_send_collector()

    await middleware(_make_scope(cookie), _noop_receive, send)

    assert rejection_tracker == ["error"]
    assert messages[0]["status"] == 503
    assert b"Service error" in messages[1]["body"]


@pytest.mark.asyncio()
async def test_generic_exception_handler(
    reset_draining: None,
    rejection_tracker: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test generic exception handler for unexpected errors.

    This covers lines 224-234 where an unexpected exception (not covered by
    specific handlers) should be caught and return 503. This ensures fail-closed
    behavior for all error scenarios.
    """
    session_store = AsyncMock()
    # Simulate an unexpected error type
    session_store.validate_session = AsyncMock(
        side_effect=RuntimeError("Unexpected internal error")
    )
    monkeypatch.setattr(admission, "get_session_store", lambda: session_store)
    monkeypatch.setattr(admission, "extract_trusted_client_ip", lambda *_: "1.2.3.4")

    middleware = AdmissionControlMiddleware(lambda *_: None)
    cookie = f"{config.SESSION_COOKIE_NAME}=cookie"
    messages, send = _make_send_collector()

    await middleware(_make_scope(cookie), _noop_receive, send)

    assert rejection_tracker == ["error"]
    assert messages[0]["status"] == 503
    assert b"Service error" in messages[1]["body"]


@pytest.mark.asyncio()
async def test_scope_state_non_dict_replacement(
    reset_draining: None,
    rejection_tracker: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test scope state replacement when state is not a dict.

    This covers lines 146-148 where scope["state"] might exist but not be a dict.
    The middleware should replace it with a dict to ensure proper state tracking.
    """
    session_store = AsyncMock()
    session_store.validate_session = AsyncMock(return_value={"user": "ok"})
    monkeypatch.setattr(admission, "get_session_store", lambda: session_store)
    monkeypatch.setattr(admission, "extract_trusted_client_ip", lambda *_: "1.2.3.4")
    monkeypatch.setattr(admission, "extract_session_id", lambda *_: "sess-state")

    redis = DummyRedis(eval_results=[1])
    monkeypatch.setattr(admission, "get_redis_store", lambda: DummyRedisStore(redis))

    async def _acquire_ok(self) -> bool:
        return True

    monkeypatch.setattr(AdmissionControlMiddleware, "_try_acquire_semaphore", _acquire_ok)

    dummy_semaphore = DummySemaphore()
    monkeypatch.setattr(admission, "_connection_semaphore", dummy_semaphore)

    async def app(scope, receive, send):
        # Verify state was properly set
        assert isinstance(scope["state"], dict)
        assert scope["state"]["session_conn_key"] == "session_conns:sess-state"
        scope["state"]["handshake_complete"] = True

    middleware = AdmissionControlMiddleware(app)
    cookie = f"{config.SESSION_COOKIE_NAME}=cookie"

    # Create scope with non-dict state
    scope = _make_scope(cookie)
    scope["state"] = "not_a_dict"  # Non-dict state that should be replaced

    messages, send = _make_send_collector()
    await middleware(scope, _noop_receive, send)

    assert rejection_tracker == []
    # Verify state was replaced and used correctly
    assert isinstance(scope["state"], dict)
    assert scope["state"]["handshake_complete"] is True


@pytest.mark.asyncio()
async def test_session_authenticated_capacity_rejection(
    reset_draining: None,
    rejection_tracker: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test authenticated session rejected at capacity limit.

    This covers lines 108-112 where even valid authenticated sessions must
    respect the global capacity limit to prevent pod exhaustion.
    """
    session_store = AsyncMock()
    session_store.validate_session = AsyncMock(return_value={"user": "ok"})
    monkeypatch.setattr(admission, "get_session_store", lambda: session_store)
    monkeypatch.setattr(admission, "extract_trusted_client_ip", lambda *_: "1.2.3.4")

    async def _acquire_fail(self) -> bool:
        return False

    monkeypatch.setattr(
        AdmissionControlMiddleware, "_try_acquire_semaphore", _acquire_fail
    )

    middleware = AdmissionControlMiddleware(lambda *_: None)
    cookie = f"{config.SESSION_COOKIE_NAME}=cookie"
    messages, send = _make_send_collector()

    await middleware(_make_scope(cookie), _noop_receive, send)

    assert rejection_tracker == ["capacity"]
    assert messages[0]["status"] == 503
    assert b"Server at capacity" in messages[1]["body"]


@pytest.mark.asyncio()
async def test_redis_error_during_decrement_after_limit(
    reset_draining: None,
    rejection_tracker: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test Redis decrement failure after session limit exceeded.

    When DECR fails with RedisError after detecting session limit, the error
    is caught by the outer RedisError handler and returns 503, not 429.
    This is correct behavior - infrastructure failures should return 503.
    """
    session_store = AsyncMock()
    session_store.validate_session = AsyncMock(return_value={"user": "ok"})
    monkeypatch.setattr(admission, "get_session_store", lambda: session_store)
    monkeypatch.setattr(admission, "extract_trusted_client_ip", lambda *_: "1.2.3.4")
    monkeypatch.setattr(admission, "extract_session_id", lambda *_: "sess-limit")

    # INCR succeeds showing limit exceeded, but DECR raises RedisError
    redis = DummyRedis(
        eval_results=[admission.MAX_CONNECTIONS_PER_SESSION + 1],
        exc=[None, RedisError("DECR failed")],
    )
    monkeypatch.setattr(admission, "get_redis_store", lambda: DummyRedisStore(redis))

    async def _acquire_ok(self) -> bool:
        return True

    monkeypatch.setattr(AdmissionControlMiddleware, "_try_acquire_semaphore", _acquire_ok)

    dummy_semaphore = DummySemaphore()
    monkeypatch.setattr(admission, "_connection_semaphore", dummy_semaphore)

    middleware = AdmissionControlMiddleware(lambda *_: None)
    cookie = f"{config.SESSION_COOKIE_NAME}=cookie"
    messages, send = _make_send_collector()

    # RedisError during DECR causes fail-closed 503 response
    await middleware(_make_scope(cookie), _noop_receive, send)

    assert rejection_tracker == ["error"]
    assert messages[0]["status"] == 503
    assert b"Service error" in messages[1]["body"]
    # Semaphore should still be released
    assert dummy_semaphore.release_count == 1


@pytest.mark.asyncio()
async def test_non_session_exception_in_app_releases_semaphore(
    reset_draining: None,
    rejection_tracker: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test non-session path releases semaphore even if app raises exception.

    This verifies the finally block in lines 251-253 releases the semaphore
    even when the inner app raises an exception.
    """

    async def _acquire_ok(self) -> bool:
        return True

    monkeypatch.setattr(
        AdmissionControlMiddleware, "_try_acquire_semaphore", _acquire_ok
    )

    dummy_semaphore = DummySemaphore()
    monkeypatch.setattr(admission, "_connection_semaphore", dummy_semaphore)

    async def app(scope, receive, send):
        raise RuntimeError("App crashed")

    middleware = AdmissionControlMiddleware(app)
    messages, send = _make_send_collector()

    # Exception should propagate but semaphore should be released
    with pytest.raises(RuntimeError, match="App crashed"):
        await middleware(_make_scope(), _noop_receive, send)

    # Verify semaphore was released despite exception
    assert dummy_semaphore.release_count == 1


@pytest.mark.asyncio()
async def test_session_handshake_incomplete_cleanup(
    reset_draining: None,
    rejection_tracker: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test session path cleanup when app completes but handshake incomplete.

    This verifies the finally block properly detects that handshake_complete
    was never set and performs cleanup (Redis DECR + semaphore release).
    When app completes normally without setting handshake_complete=True,
    the middleware assumes the handshake failed and cleans up resources.
    """
    session_store = AsyncMock()
    session_store.validate_session = AsyncMock(return_value={"user": "ok"})
    monkeypatch.setattr(admission, "get_session_store", lambda: session_store)
    monkeypatch.setattr(admission, "extract_trusted_client_ip", lambda *_: "1.2.3.4")
    monkeypatch.setattr(admission, "extract_session_id", lambda *_: "sess-incomplete")

    redis = DummyRedis(eval_results=[1, 0])  # INCR then DECR
    monkeypatch.setattr(admission, "get_redis_store", lambda: DummyRedisStore(redis))

    async def _acquire_ok(self) -> bool:
        return True

    monkeypatch.setattr(AdmissionControlMiddleware, "_try_acquire_semaphore", _acquire_ok)

    dummy_semaphore = DummySemaphore()
    monkeypatch.setattr(admission, "_connection_semaphore", dummy_semaphore)

    async def app(scope, receive, send):
        # App runs but never sets handshake_complete
        pass

    middleware = AdmissionControlMiddleware(app)
    cookie = f"{config.SESSION_COOKIE_NAME}=cookie"
    messages, send = _make_send_collector()

    # App completes normally but cleanup should still happen
    await middleware(_make_scope(cookie), _noop_receive, send)

    # Verify cleanup happened: INCR + DECR
    assert len(redis.eval_calls) == 2
    assert redis.eval_calls[0][0] == admission._INCR_SESSION_CONN_LUA
    assert redis.eval_calls[1][0] == admission._DECR_SESSION_CONN_LUA
    assert dummy_semaphore.release_count == 1


@pytest.mark.asyncio()
async def test_send_http_error_with_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _send_http_error includes retry-after header when specified.

    This verifies the retry_after parameter properly adds the header to
    the response (lines 259-260).
    """
    middleware = AdmissionControlMiddleware(lambda *_: None)
    messages, send = _make_send_collector()

    await middleware._send_http_error(send, 503, "Test message", retry_after=10)

    assert len(messages) == 2
    assert messages[0]["type"] == "websocket.http.response.start"
    assert messages[0]["status"] == 503
    headers = dict(messages[0]["headers"])
    assert headers[b"retry-after"] == b"10"
    assert messages[1]["type"] == "websocket.http.response.body"
    assert b"Test message" in messages[1]["body"]


@pytest.mark.asyncio()
async def test_send_http_error_without_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _send_http_error without retry-after header.

    This verifies the retry_after parameter is optional and headers are
    constructed correctly when not provided.
    """
    middleware = AdmissionControlMiddleware(lambda *_: None)
    messages, send = _make_send_collector()

    await middleware._send_http_error(send, 429, "Rate limited")

    assert len(messages) == 2
    assert messages[0]["status"] == 429
    headers = dict(messages[0]["headers"])
    assert b"retry-after" not in headers
    assert b"content-type" in headers
    assert headers[b"content-type"] == b"application/json"
    assert b"Rate limited" in messages[1]["body"]
