"""WebSocket admission control middleware for NiceGUI."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

from apps.web_console_ng import config
from apps.web_console_ng.auth.client_ip import extract_trusted_client_ip
from apps.web_console_ng.auth.session_store import extract_session_id, get_session_store
from apps.web_console_ng.core import health
from apps.web_console_ng.core.redis_ha import get_redis_store

logger = logging.getLogger(__name__)

POD_NAME = config.POD_NAME
SESSION_COOKIE_NAME = config.SESSION_COOKIE_NAME

# Use configurable limits from config.py
MAX_CONNECTIONS = config.WS_MAX_CONNECTIONS
SESSION_VALIDATION_TIMEOUT = config.WS_SESSION_VALIDATION_TIMEOUT
MAX_CONNECTIONS_PER_SESSION = config.WS_MAX_CONNECTIONS_PER_SESSION
SESSION_CONN_TTL = config.WS_SESSION_CONN_TTL

_connection_semaphore = asyncio.Semaphore(MAX_CONNECTIONS)


def _increment_rejection(reason: str) -> None:
    try:
        from apps.web_console_ng import metrics

        metrics.connections_rejected_total.labels(pod=POD_NAME, reason=reason).inc()
    except Exception:
        # Metrics may not be initialized yet (C3); don't block admission.
        return


class AdmissionControlMiddleware:
    """ASGI middleware for connection admission control (WebSocket only)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "websocket":
            await self.app(scope, receive, send)
            return

        if health.is_draining:
            _increment_rejection("draining")
            await self._send_http_error(send, 503, "Server draining", retry_after=30)
            return

        conn = HTTPConnection(scope)
        session_cookie = conn.cookies.get(SESSION_COOKIE_NAME)

        if session_cookie:
            try:
                session_store = get_session_store()
                client_ip = extract_trusted_client_ip(conn, config.TRUSTED_PROXY_IPS)
                user_agent = conn.headers.get("user-agent", "")

                session_valid: dict[str, Any] | None = await asyncio.wait_for(
                    session_store.validate_session(session_cookie, client_ip, user_agent),
                    timeout=SESSION_VALIDATION_TIMEOUT,
                )

                if session_valid:
                    # Enforce global capacity limit for authenticated sessions too
                    # This prevents pod exhaustion when many valid sessions connect
                    acquired = await self._try_acquire_semaphore()
                    if not acquired:
                        _increment_rejection("capacity")
                        await self._send_http_error(send, 503, "Server at capacity", retry_after=5)
                        return

                    # CRITICAL: Wrap everything after semaphore acquisition in try/finally
                    # to ensure semaphore is released if Redis or any other operation fails
                    session_conn_key: str | None = None
                    redis_incr_done = False
                    scope_state: dict[str, object] | None = None

                    try:
                        session_id = extract_session_id(session_cookie)
                        session_conn_key = f"session_conns:{session_id}"
                        redis = await get_redis_store().get_master()

                        lua_incr = """
                        local count = redis.call('INCR', KEYS[1])
                        if redis.call('TTL', KEYS[1]) == -1 then
                            redis.call('EXPIRE', KEYS[1], ARGV[1])
                        end
                        return count
                        """
                        current_conns = await redis.eval(  # type: ignore[misc]
                            lua_incr, 1, session_conn_key, str(SESSION_CONN_TTL)
                        )
                        redis_incr_done = True

                        if current_conns > MAX_CONNECTIONS_PER_SESSION:
                            await redis.decr(session_conn_key)
                            _increment_rejection("session_limit")
                            await self._send_http_error(
                                send, 429, "Too many connections for session"
                            )
                            return

                        scope_state = scope.setdefault("state", {})
                        if not isinstance(scope_state, dict):
                            scope_state = {}
                            scope["state"] = scope_state
                        scope_state["session_conn_key"] = session_conn_key
                        scope_state["handshake_complete"] = False
                        scope_state["semaphore_acquired"] = True  # Track for cleanup

                        await self.app(scope, receive, send)

                    finally:
                        # Determine if we need to release semaphore here
                        # Cases where WE release (not connection_events.py):
                        # 1. Exception before app runs (scope_state is None or no handshake_complete)
                        # 2. App ran but handshake never completed
                        should_release = True
                        if scope_state is not None and scope_state.get("handshake_complete", False):
                            # Handshake completed - connection_events.py handles release
                            should_release = False

                        if should_release:
                            # Decrement Redis counter if we incremented it
                            if redis_incr_done and session_conn_key:
                                lua_decr = """
                                local count = redis.call('GET', KEYS[1])
                                if count and tonumber(count) > 0 then
                                    count = redis.call('DECR', KEYS[1])
                                    if tonumber(count) <= 0 then
                                        redis.call('DEL', KEYS[1])
                                    end
                                    return count
                                end
                                return 0
                                """
                                try:
                                    redis = await get_redis_store().get_master()
                                    await redis.eval(  # type: ignore[misc]
                                        lua_decr, 1, session_conn_key
                                    )
                                except Exception:
                                    pass
                            # Release semaphore
                            _connection_semaphore.release()
                    return

                _increment_rejection("invalid_session")
                await self._send_http_error(send, 401, "Session expired")
                return

            except TimeoutError:
                _increment_rejection("timeout")
                await self._send_http_error(send, 503, "Service timeout", retry_after=5)
                return
            except Exception as exc:
                logger.error(
                    "Admission control error: %s",
                    exc,
                    extra={"pod": POD_NAME},
                )
                _increment_rejection("error")
                await self._send_http_error(send, 503, "Service error", retry_after=5)
                return

        # NON-SESSION PATH (unauthenticated or missing session cookie)
        # Uses semaphore-only capacity control. The semaphore is ALWAYS released in
        # the finally block below - this is intentional and differs from the session
        # path which uses Redis counters. The semaphore release is self-contained
        # here and MUST NOT be released elsewhere (e.g., in on_disconnect handlers).
        # This pattern prevents double-release bugs and keeps capacity enforcement
        # atomic to this middleware for non-session connections.
        acquired = await self._try_acquire_semaphore()
        if not acquired:
            _increment_rejection("capacity")
            await self._send_http_error(send, 503, "Server at capacity", retry_after=5)
            return

        try:
            await self.app(scope, receive, send)
        finally:
            # Always release semaphore for non-session path - see comment above
            _connection_semaphore.release()

    async def _send_http_error(
        self, send: Send, status: int, message: str, retry_after: int | None = None
    ) -> None:
        headers = [(b"content-type", b"application/json")]
        if retry_after:
            headers.append((b"retry-after", str(retry_after).encode()))

        await send(
            {
                "type": "websocket.http.response.start",
                "status": status,
                "headers": headers,
            }
        )
        await send(
            {
                "type": "websocket.http.response.body",
                "body": f'{{"error": "{message}"}}'.encode(),
            }
        )

    async def _try_acquire_semaphore(self) -> bool:
        try:
            await asyncio.wait_for(_connection_semaphore.acquire(), timeout=0)
            return True
        except TimeoutError:
            return False
