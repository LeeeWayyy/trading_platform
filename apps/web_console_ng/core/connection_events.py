"""NiceGUI connection event handlers for lifecycle and metrics."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

from nicegui import Client, app

from apps.web_console_ng import config
from apps.web_console_ng.core import health
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from apps.web_console_ng.core.redis_ha import get_redis_store

# Import the semaphore from admission module for cleanup on disconnect
# This is needed because session connections acquire the semaphore in admission.py
# and need to release it when disconnecting
_connection_semaphore: asyncio.Semaphore | None = None


def _get_connection_semaphore() -> asyncio.Semaphore | None:
    """Lazy import to avoid circular dependencies."""
    global _connection_semaphore
    if _connection_semaphore is None:
        try:
            from apps.web_console_ng.core.admission import _connection_semaphore as sem

            _connection_semaphore = sem
        except ImportError:
            pass
    return _connection_semaphore


logger = logging.getLogger(__name__)

_handlers_registered = False


def _get_scope_state(client: Client) -> dict[str, object] | None:
    request = getattr(client, "request", None)
    scope = getattr(request, "scope", None)
    if isinstance(scope, dict):
        state = scope.get("state")
        if isinstance(state, dict):
            return cast(dict[str, object], state)
        scope["state"] = {}
        return cast(dict[str, object], scope["state"])
    return None


def _get_metrics() -> Any | None:
    try:
        from apps.web_console_ng import metrics

        return metrics
    except Exception:
        return None


def setup_connection_handlers() -> None:
    """Register NiceGUI connection handlers once."""
    global _handlers_registered
    if _handlers_registered:
        return
    _handlers_registered = True

    @app.on_connect
    async def on_client_connect(client: Client) -> None:
        lifecycle = ClientLifecycleManager.get()

        client_id = lifecycle.generate_client_id()
        client.storage["client_id"] = client_id

        scope_state = _get_scope_state(client)
        if scope_state is not None:
            scope_state["handshake_complete"] = True

            # Copy session_conn_key from scope state (set by admission.py) to client storage
            # This avoids re-parsing the cookie and ensures cleanup key consistency
            session_conn_key = scope_state.get("session_conn_key")
            if session_conn_key:
                client.storage["session_conn_key"] = session_conn_key

        await lifecycle.register_client(client_id)

        count = health.connection_counter.increment()
        metrics = _get_metrics()
        if metrics is not None:
            metrics.ws_connects_total.labels(pod=config.POD_NAME).inc()
            metrics.ws_connections.labels(pod=config.POD_NAME).set(count)

        logger.info("ws_client_connected", extra={"client_id": client_id})

    @app.on_disconnect
    async def on_client_disconnect(client: Client) -> None:
        lifecycle = ClientLifecycleManager.get()
        client_id = client.storage.get("client_id")

        scope_state = _get_scope_state(client)
        handshake_complete = False
        if scope_state is not None:
            handshake_complete = bool(scope_state.get("handshake_complete", False))

        if isinstance(client_id, str):
            await lifecycle.cleanup_client(client_id)

        session_conn_key = client.storage.get("session_conn_key")
        if session_conn_key:
            try:
                from apps.web_console_ng.core.admission import _DECR_SESSION_CONN_LUA

                redis = await get_redis_store().get_master()
                await redis.eval(_DECR_SESSION_CONN_LUA, 1, session_conn_key)  # type: ignore[misc]
            except Exception as exc:
                logger.warning(
                    "session_conn_decr_failed",
                    extra={"client_id": client_id, "error": str(exc)},
                )

        if handshake_complete:
            count = health.connection_counter.decrement()
            metrics = _get_metrics()
            had_exception = bool(client.storage.get("had_exception", False))
            if metrics is not None:
                metrics.ws_connections.labels(pod=config.POD_NAME).set(count)
                if not had_exception:
                    metrics.ws_disconnects_total.labels(pod=config.POD_NAME, reason="normal").inc()

            # Release semaphore for session connections (acquired in admission.py)
            # Only release if handshake completed (otherwise admission.py handles it)
            if scope_state is not None and scope_state.get("semaphore_acquired"):
                semaphore = _get_connection_semaphore()
                if semaphore is not None:
                    semaphore.release()

        logger.info("ws_client_disconnected", extra={"client_id": client_id})

    @app.on_exception
    async def on_client_exception(client: Client, exception: Exception) -> None:
        client_id = client.storage.get("client_id")
        client.storage["had_exception"] = True
        logger.error(
            "ws_client_exception",
            extra={"client_id": client_id, "error": str(exception)},
        )

        metrics = _get_metrics()
        if metrics is not None:
            metrics.ws_disconnects_total.labels(pod=config.POD_NAME, reason="error").inc()


__all__ = ["setup_connection_handlers"]
