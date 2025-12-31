"""Session and authorization middleware for NiceGUI web console."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from apps.web_console_ng import config
from apps.web_console_ng.auth.client_ip import TrustedProxy, get_client_ip
from apps.web_console_ng.auth.session_store import ServerSessionStore

logger = logging.getLogger(__name__)


class SessionMiddleware:
    """Validate session cookie and populate request.state.user."""

    def __init__(
        self,
        app: ASGIApp,
        session_store: ServerSessionStore,
        trusted_proxies: Iterable[TrustedProxy] | None = None,
    ) -> None:
        self.app = app
        self.session_store = session_store
        self.trusted_proxies = list(trusted_proxies or [])

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        scope.setdefault("state", {})
        request = Request(scope, receive)
        cookie_name = config.SESSION_COOKIE_NAME
        cookie_value = request.cookies.get(cookie_name)
        client_ip = get_client_ip(request, self.trusted_proxies)
        user_agent = request.headers.get("user-agent", "")

        scope["state"]["user"] = None

        if cookie_value:
            session = await self.session_store.validate_session(
                cookie_value=cookie_value,
                client_ip=client_ip,
                user_agent=user_agent,
            )
            if session:
                user = session.get("user") if isinstance(session, dict) else None
                if isinstance(user, dict):
                    user = {
                        **user,
                        "csrf_token": session.get("csrf_token"),
                        "session_id": session.get("session_id"),
                    }
                scope["state"]["user"] = user

        await self.app(scope, receive, send)


class AuthMiddleware:
    """Authorization middleware enforcing authenticated access."""

    AUTH_EXEMPT_PATHS = {"/auth/login", "/auth/callback", "/dev/login", "/health", "/auth/logout"}

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        path = request.url.path

        if path in self.AUTH_EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        user = getattr(request.state, "user", None)
        if user is None:
            response = JSONResponse({"error": "authentication_required"}, status_code=401)
            await response(scope, receive, send)
            return

        scope.setdefault("state", {})
        scope["state"]["user"] = user
        await self.app(scope, receive, send)


__all__ = ["SessionMiddleware", "AuthMiddleware"]
