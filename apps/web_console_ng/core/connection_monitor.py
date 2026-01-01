"""WebSocket connection monitoring and validation for NiceGUI clients."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from http.cookies import SimpleCookie
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import urlparse

from nicegui import Client, app
from starlette.requests import Request

from apps.web_console_ng import config
from apps.web_console_ng.auth.client_ip import get_client_ip
from apps.web_console_ng.auth.session_store import ServerSessionStore, get_session_store

logger = logging.getLogger(__name__)


def _parse_cookies(cookie_header: str) -> dict[str, str]:
    cookie = SimpleCookie()
    cookie.load(cookie_header)
    return {key: morsel.value for key, morsel in cookie.items()}


def _origin_host(origin: str) -> tuple[str, str]:
    parsed = urlparse(origin)
    hostname = (parsed.hostname or "").lower()
    port = f":{parsed.port}" if parsed.port else ""
    return hostname, f"{hostname}{port}" if hostname else ""


def _request_from_environ(environ: dict[str, Any]) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    for key, value in environ.items():
        if not isinstance(key, str):
            continue
        if key.startswith("HTTP_"):
            header_name = key[5:].replace("_", "-").lower().encode("utf-8")
            headers.append((header_name, str(value).encode("utf-8")))
    if "CONTENT_TYPE" in environ:
        headers.append((b"content-type", str(environ["CONTENT_TYPE"]).encode("utf-8")))
    if "CONTENT_LENGTH" in environ:
        headers.append((b"content-length", str(environ["CONTENT_LENGTH"]).encode("utf-8")))

    client_addr = str(environ.get("REMOTE_ADDR", ""))
    scope = {
        "type": "http",
        "headers": headers,
        "client": (client_addr, 0),
        "scheme": "http",
        "server": ("", 0),
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "method": "GET",
    }

    async def _receive() -> dict[str, str]:
        return {"type": "http.request"}

    return Request(scope, _receive)


class ConnectionMonitorRegistry:
    """Singleton registry for WS lifecycle hooks and client tracking."""

    _instance: ConnectionMonitorRegistry | None = None

    def __init__(
        self,
        session_store: ServerSessionStore,
        allowed_hosts: list[str] | None = None,
        require_origin: bool = True,
    ) -> None:
        self.session_store = session_store
        self.allowed_hosts = allowed_hosts or list(config.ALLOWED_HOSTS)
        self.require_origin = require_origin
        self._hooks_registered = False
        self._connected: dict[str, Client] = {}

    @classmethod
    def get(
        cls,
        session_store: ServerSessionStore | None = None,
        allowed_hosts: list[str] | None = None,
        require_origin: bool | None = None,
    ) -> ConnectionMonitorRegistry:
        if cls._instance is None:
            store = session_store or get_session_store()
            cls._instance = cls(
                session_store=store,
                allowed_hosts=allowed_hosts,
                require_origin=require_origin if require_origin is not None else True,
            )
        else:
            if session_store is not None:
                cls._instance.session_store = session_store
            if allowed_hosts is not None:
                cls._instance.allowed_hosts = allowed_hosts
            if require_origin is not None:
                cls._instance.require_origin = require_origin
        return cls._instance

    def register_hooks_once(self) -> None:
        """Register NiceGUI lifecycle hooks once per process."""
        if self._hooks_registered:
            return
        app.on_connect(self._handle_connect)
        app.on_disconnect(self._handle_disconnect)
        self._hooks_registered = True

    def connected_count(self) -> int:
        return len(self._connected)

    async def _handle_connect(self, client: Client) -> None:
        if not self._validate_origin(client):
            await self._disconnect(client, "origin_not_allowed")
            return

        session = await self._validate_session(client)
        if session is None:
            await self._disconnect(client, "session_invalid")
            return

        self._attach_user(client, session)
        client_id = getattr(client, "id", None)
        if isinstance(client_id, str):
            self._connected[client_id] = client
        logger.info("ws_client_connected", extra={"client_id": client_id})

    async def _handle_disconnect(self, client: Client) -> None:
        client_id = getattr(client, "id", None)
        if isinstance(client_id, str):
            self._connected.pop(client_id, None)
        logger.info("ws_client_disconnected", extra={"client_id": client_id})

    def _validate_origin(self, client: Client) -> bool:
        environ = getattr(client, "environ", None)
        if not isinstance(environ, dict):
            logger.warning(
                "ws_origin_missing_environ", extra={"client_id": getattr(client, "id", None)}
            )
            return False

        origin = environ.get("HTTP_ORIGIN")
        if not origin:
            if self.require_origin:
                logger.warning(
                    "ws_origin_missing", extra={"client_id": getattr(client, "id", None)}
                )
                return False
            logger.warning(
                "ws_origin_missing_allowed", extra={"client_id": getattr(client, "id", None)}
            )
            return True

        if "*" in self.allowed_hosts:
            return True

        hostname, hostport = _origin_host(origin)
        allowed = {host.lower() for host in self.allowed_hosts}
        return hostname in allowed or hostport in allowed

    async def _validate_session(self, client: Client) -> dict[str, Any] | None:
        environ = getattr(client, "environ", None)
        if not isinstance(environ, dict):
            return None

        cookie_header = environ.get("HTTP_COOKIE", "")
        if not cookie_header:
            return None

        cookies = _parse_cookies(cookie_header)
        cookie_value = cookies.get(config.SESSION_COOKIE_NAME)
        if not cookie_value:
            return None

        request = _request_from_environ(environ)
        client_ip = get_client_ip(request, config.TRUSTED_PROXY_IPS)
        user_agent = environ.get("HTTP_USER_AGENT")

        try:
            return await self.session_store.validate_session(
                cookie_value=cookie_value,
                client_ip=client_ip,
                user_agent=user_agent,
            )
        except Exception as exc:
            logger.warning("ws_session_validation_error", exc_info=exc)
            return None

    def _attach_user(self, client: Client, session: dict[str, Any]) -> None:
        state = getattr(client, "state", None)
        if state is None:
            state = SimpleNamespace()
            client.state = state  # type: ignore[attr-defined]

        user = session.get("user") if isinstance(session, dict) else None
        if isinstance(user, dict):
            state.user = user
        else:
            state.user = None

        session_id = session.get("session_id") if isinstance(session, dict) else None
        state.session_id = session_id

    async def _disconnect(self, client: Client, reason: str) -> None:
        logger.warning(
            "ws_client_forced_disconnect",
            extra={"client_id": getattr(client, "id", None), "reason": reason},
        )
        disconnect = getattr(client, "disconnect", None)
        if disconnect is None:
            return
        try:
            disconnect_fn = cast(Callable[[], Any], disconnect)
            result = disconnect_fn()
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            logger.warning("ws_disconnect_failed", exc_info=exc)


__all__ = ["ConnectionMonitorRegistry"]
