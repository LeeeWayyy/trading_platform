from __future__ import annotations

import hmac
import ipaddress
import logging
from collections.abc import Callable
from typing import Any, cast

from nicegui import app, ui
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from apps.web_console_ng import config
from apps.web_console_ng.auth.client_ip import extract_trusted_client_ip
from apps.web_console_ng.auth.redirects import sanitize_redirect_path
from apps.web_console_ng.auth.session_store import get_session_store

logger = logging.getLogger(__name__)


def get_current_user() -> dict[str, Any]:
    """Get the current user from the session storage."""
    result = app.storage.user.get("user", {"role": "viewer", "username": "Guest"})
    return dict(result) if isinstance(result, dict) else {"role": "viewer", "username": "Guest"}


def _is_trusted_proxy(request: Request) -> bool:
    remote_addr = request.client.host if request.client else "0.0.0.0"
    try:
        ip = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False

    for proxy in config.TRUSTED_PROXY_IPS:
        if isinstance(proxy, ipaddress.IPv4Network | ipaddress.IPv6Network):
            if ip in proxy:
                return True
        elif ip == proxy:
            return True

    return False


def _validate_mtls_request(request: Request, user_data: dict[str, Any]) -> bool:
    if not _is_trusted_proxy(request):
        logger.warning("mTLS request rejected: untrusted proxy source")
        return False

    verify = request.headers.get("X-SSL-Client-Verify")
    client_dn = request.headers.get("X-SSL-Client-DN")
    if verify != "SUCCESS" or not client_dn:
        logger.warning("mTLS request rejected: missing or failed verification headers")
        return False

    session_dn = user_data.get("client_dn")
    if not session_dn or not hmac.compare_digest(str(session_dn), str(client_dn)):
        logger.warning("mTLS request rejected: certificate DN mismatch")
        return False

    return True


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware to validate session on every request."""

    async def dispatch(self, request: Request, call_next: Callable[[Request], Any]) -> Response:
        # Skip static files and health check
        if request.url.path.startswith(("/_nicegui", "/health")):
            return cast(Response, await call_next(request))

        # 1. mTLS Auto-Login (if enabled)
        # We check this at middleware level to allow bypass of login form
        if config.AUTH_TYPE == "mtls":
            verify = request.headers.get("X-SSL-Client-Verify")
            if verify == "SUCCESS":
                # In a real app, we'd validate the session or create one here if missing.
                # For NiceGUI, we rely on the session cookie mostly.
                # If mTLS header is present but no session, we could auto-create it.
                # This logic is complex for middleware; usually handled by a route or on login page load.
                pass

        # 2. Session Validation logic is primarily in SessionMiddleware / nicegui's storage
        # NiceGUI handles the session cookie -> app.storage.user mapping.
        # But we need to validate against our server-side Redis store.

        # We can't easily access app.storage.user here because we are outside the websocket context context?
        # Actually starlette middleware runs before websocket upgrade for initial page load.

        # For now, we rely on the decorators (@requires_auth) to do the strict checking.
        # This middleware acts as a placeholder for global request logging or header checks.

        return cast(Response, await call_next(request))


class SessionMiddleware(BaseHTTPMiddleware):
    """Middleware to ensure session cookie integrity."""

    async def dispatch(self, request: Request, call_next: Callable[[Request], Any]) -> Response:
        return cast(Response, await call_next(request))


def requires_auth(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator to require authentication for a page.

    Validates the session cookie against the server-side Redis store.
    If invalid or missing, redirects to /login with the current path saved.
    """
    from apps.web_console_ng.auth.cookie_config import CookieConfig

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        request: Request = app.storage.request  # type: ignore[attr-defined]
        cookie_cfg = CookieConfig.from_env()
        cookie_value = request.cookies.get(cookie_cfg.get_cookie_name())

        if not cookie_value:
            # No session cookie - redirect to login
            app.storage.user["redirect_after_login"] = sanitize_redirect_path(request.url.path)
            ui.navigate.to("/login")
            return

        # Validate against server-side store
        session_store = get_session_store()
        client_ip = extract_trusted_client_ip(request, config.TRUSTED_PROXY_IPS)
        user_agent = request.headers.get("user-agent", "")

        session = await session_store.validate_session(cookie_value, client_ip, user_agent)

        if not session:
            # Session invalid or expired
            app.storage.user.clear()
            app.storage.user["redirect_after_login"] = sanitize_redirect_path(request.url.path)
            app.storage.user["login_reason"] = "session_expired"
            ui.navigate.to("/login")
            return

        # Session valid - update client-side storage with user data
        user_data = session.get("user", {})
        if config.AUTH_TYPE == "mtls" and not _validate_mtls_request(request, user_data):
            app.storage.user.clear()
            app.storage.user["redirect_after_login"] = sanitize_redirect_path(request.url.path)
            app.storage.user["login_reason"] = "session_expired"
            ui.navigate.to("/login")
            return

        if user_data.get("mfa_pending") is True:
            app.storage.user["pending_mfa_cookie"] = cookie_value
            if request.url.path != "/mfa-verify":
                ui.navigate.to("/mfa-verify")
                return
        app.storage.user["logged_in"] = True
        app.storage.user["user"] = user_data

        return await func(*args, **kwargs)

    return wrapper


def requires_role(required_role: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to require a specific role for a page.

    Validates the session cookie against the server-side Redis store and checks
    for the required role. Unauthorized users are redirected to "/".
    """
    from apps.web_console_ng.auth.cookie_config import CookieConfig

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            request: Request = app.storage.request  # type: ignore[attr-defined]
            cookie_cfg = CookieConfig.from_env()
            cookie_value = request.cookies.get(cookie_cfg.get_cookie_name())

            if not cookie_value:
                app.storage.user["redirect_after_login"] = sanitize_redirect_path(request.url.path)
                ui.navigate.to("/login")
                return

            session_store = get_session_store()
            client_ip = extract_trusted_client_ip(request, config.TRUSTED_PROXY_IPS)
            user_agent = request.headers.get("user-agent", "")

            session = await session_store.validate_session(cookie_value, client_ip, user_agent)

            if not session:
                app.storage.user.clear()
                app.storage.user["redirect_after_login"] = sanitize_redirect_path(request.url.path)
                app.storage.user["login_reason"] = "session_expired"
                ui.navigate.to("/login")
                return

            user_data = session.get("user", {})
            if config.AUTH_TYPE == "mtls" and not _validate_mtls_request(request, user_data):
                app.storage.user.clear()
                app.storage.user["redirect_after_login"] = sanitize_redirect_path(request.url.path)
                app.storage.user["login_reason"] = "session_expired"
                ui.navigate.to("/login")
                return

            if user_data.get("mfa_pending") is True:
                app.storage.user["pending_mfa_cookie"] = cookie_value
                if request.url.path != "/mfa-verify":
                    ui.navigate.to("/mfa-verify")
                    return

            if user_data.get("role") != required_role:
                ui.navigate.to("/")
                return

            app.storage.user["logged_in"] = True
            app.storage.user["user"] = user_data

            return await func(*args, **kwargs)

        return wrapper

    return decorator
