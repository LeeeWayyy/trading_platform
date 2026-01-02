from __future__ import annotations

import hmac
import logging
from collections.abc import Callable
from typing import Any, cast

from nicegui import app, ui
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from apps.web_console_ng import config
from apps.web_console_ng.auth.client_ip import extract_trusted_client_ip, is_trusted_ip
from apps.web_console_ng.auth.redirects import sanitize_redirect_path
from apps.web_console_ng.auth.session_store import SessionValidationError, get_session_store

logger = logging.getLogger(__name__)


def _get_request_from_storage() -> Request:
    """Return current request or a minimal fallback for tests."""
    request = getattr(app.storage, "request", None)
    if isinstance(request, Request):
        return request
    if request is None:
        # Fallback for when NiceGUI context is missing (e.g., tests)
        # Log warning in non-test mode to detect context propagation issues
        if not config.DEBUG:
            logger.warning(
                "Creating fallback request - NiceGUI context missing. "
                "This may indicate a context propagation issue."
            )
        scope = {
            "type": "http",
            "headers": [],
            "client": ("127.0.0.1", 0),
            "path": "/",
        }
        return Request(scope)
    return cast(Request, request)


def get_current_user() -> dict[str, Any]:
    """Get the current user from the session storage."""
    result = app.storage.user.get("user", {"role": "viewer", "username": "Guest"})
    return dict(result) if isinstance(result, dict) else {"role": "viewer", "username": "Guest"}


def _validate_mtls_request(request: Request, user_data: dict[str, Any]) -> bool:
    """Validate mTLS request against session data."""
    remote_addr = request.client.host if request.client else "0.0.0.0"
    if not is_trusted_ip(remote_addr):
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


async def _validate_session_and_get_user(
    request: Request,
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate session cookie and return user data.

    Returns:
        Tuple of (user_data, cookie_value) if valid, (None, None) if invalid.

    Raises:
        SessionValidationError: If Redis is unavailable (callers should return 503).
    """
    from apps.web_console_ng.auth.cookie_config import CookieConfig

    cookie_cfg = CookieConfig.from_env()
    cookie_value = request.cookies.get(cookie_cfg.get_cookie_name())

    if not cookie_value:
        return None, None

    session_store = get_session_store()
    client_ip = extract_trusted_client_ip(request, config.TRUSTED_PROXY_IPS)
    user_agent = request.headers.get("user-agent", "")

    # Let SessionValidationError propagate - callers should handle with 503
    session = await session_store.validate_session(cookie_value, client_ip, user_agent)

    if not session:
        return None, cookie_value

    user_data = session.get("user", {})

    # Validate mTLS if enabled
    if config.AUTH_TYPE == "mtls" and not _validate_mtls_request(request, user_data):
        return None, cookie_value

    return user_data, cookie_value


def _redirect_to_login(request: Request, reason: str = "session_expired") -> None:
    """Clear storage and redirect to login."""
    app.storage.user.clear()
    app.storage.user["redirect_after_login"] = sanitize_redirect_path(request.url.path)
    app.storage.user["login_reason"] = reason
    ui.navigate.to("/login")


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware to validate session on every request.

    Important: This middleware depends on SessionMiddleware to set request.state.user.
    If SessionMiddleware hasn't run (wrong ordering or not installed), this middleware
    provides a fallback by validating the session directly.
    """

    _EXEMPT_PATH_PREFIXES = (
        "/_nicegui",
        "/health",
        "/healthz",
        "/readyz",
        "/dev/login",
        "/login",
        "/mfa-verify",
        "/auth/callback",  # OAuth2 callback must be accessible without session
        "/auth/login",  # OAuth2 login redirect
        "/forgot-password",
    )

    async def dispatch(self, request: Request, call_next: Callable[[Request], Any]) -> Response:
        # Skip static files, health checks, and auth entrypoints
        if request.url.path.startswith(self._EXEMPT_PATH_PREFIXES):
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

        # 2. Session Validation
        # Check if SessionMiddleware has already validated and set request.state.user
        user = getattr(request.state, "user", None)

        # Fallback: If SessionMiddleware didn't run, validate session directly
        # This ensures correct behavior regardless of middleware ordering
        if not user:
            try:
                user_data, _ = await _validate_session_and_get_user(request)
                if user_data:
                    request.state.user = user_data
                    user = user_data
            except SessionValidationError:
                # Redis unavailable - for API requests return JSON 503
                # For HTML requests, let request proceed so page decorators can render error UI
                accept_header = request.headers.get("accept", "")
                if "text/html" not in accept_header:
                    return Response(
                        content='{"error": "Service temporarily unavailable"}',
                        status_code=503,
                        media_type="application/json",
                        headers={"Retry-After": "5"},
                    )
                # For HTML requests, proceed without user - decorators will handle error UI

        if not user:
            # For browser requests (Accept: text/html), redirect to login
            # For API requests, return 401
            accept_header = request.headers.get("accept", "")
            if "text/html" in accept_header:
                from urllib.parse import quote

                from starlette.responses import RedirectResponse

                # Preserve the original path for post-login redirect
                original_path = sanitize_redirect_path(request.url.path)
                redirect_url = f"/login?next={quote(original_path)}"
                return RedirectResponse(url=redirect_url, status_code=302)
            return Response(status_code=401)

        return cast(Response, await call_next(request))


class SessionMiddleware(BaseHTTPMiddleware):
    """Middleware to validate session cookies and populate request.state.user.

    This middleware validates session cookies on every request and sets request.state.user
    with the user data if valid. The @requires_auth and @requires_role decorators then
    check request.state.user to avoid redundant Redis validation, improving performance.

    Important: trusted_proxies should match config.TRUSTED_PROXY_IPS to avoid device binding
    mismatches when running behind a reverse proxy. The default is config.TRUSTED_PROXY_IPS.
    """

    def __init__(
        self,
        app: Any,
        *,
        session_store: Any = None,
        trusted_proxies: Any = None,
    ) -> None:
        super().__init__(app)
        self._session_store = session_store
        # Default to config.TRUSTED_PROXY_IPS if not explicitly provided
        # This ensures consistent IP extraction across all auth flows
        self._trusted_proxies = (
            trusted_proxies if trusted_proxies is not None else config.TRUSTED_PROXY_IPS
        )

    async def dispatch(self, request: Request, call_next: Callable[[Request], Any]) -> Response:
        from apps.web_console_ng.auth.cookie_config import CookieConfig

        cookie_cfg = CookieConfig.from_env()
        cookie_name = cookie_cfg.get_cookie_name()
        cookie_value = request.cookies.get(cookie_name)

        if cookie_value:
            session_store = self._session_store or get_session_store()
            client_ip = extract_trusted_client_ip(request, self._trusted_proxies or [])
            user_agent = request.headers.get("user-agent", "")
            try:
                session = await session_store.validate_session(cookie_value, client_ip, user_agent)
                if session:
                    request.state.user = session.get("user", {})
            except SessionValidationError:
                # Redis unavailable - for API requests return JSON 503
                # For HTML requests, let request proceed so page decorators can render error UI
                accept_header = request.headers.get("accept", "")
                if "text/html" not in accept_header:
                    return Response(
                        content='{"error": "Service temporarily unavailable"}',
                        status_code=503,
                        media_type="application/json",
                        headers={"Retry-After": "5"},
                    )
                # For HTML requests, proceed without user - decorators will handle error UI

        return cast(Response, await call_next(request))


async def _validate_and_get_user_for_decorator(
    request: Request,
) -> tuple[dict[str, Any] | None, str | None, bool]:
    """Shared session validation logic for auth decorators.

    Returns:
        Tuple of (user_data, cookie_value, should_return_early).
        If should_return_early is True, the decorator should return immediately
        (either because UI was rendered or redirect was triggered).
    """
    from apps.web_console_ng.auth.cookie_config import CookieConfig

    # Optimization: If SessionMiddleware already validated, use cached user
    user_data = getattr(request.state, "user", None)
    cookie_value = None

    if user_data is None:
        # SessionMiddleware didn't validate - do full validation
        try:
            user_data, cookie_value = await _validate_session_and_get_user(request)
        except SessionValidationError:
            # Redis unavailable - show service unavailable UI
            with ui.card().classes("w-96 mx-auto mt-16 p-8"):
                ui.label("Service Temporarily Unavailable").classes("text-xl font-bold")
                ui.label("Please try again in a few moments.").classes("text-gray-600")
                ui.button("Retry", on_click=lambda: ui.navigate.reload()).classes("mt-4")
            return None, None, True
    else:
        # User was cached by SessionMiddleware - still need cookie for MFA flow
        cookie_cfg = CookieConfig.from_env()
        cookie_value = request.cookies.get(cookie_cfg.get_cookie_name())

    if user_data is None:
        if cookie_value is None:
            # No session cookie - redirect to login
            app.storage.user["redirect_after_login"] = sanitize_redirect_path(request.url.path)
            ui.navigate.to("/login")
        else:
            # Session invalid or expired
            _redirect_to_login(request)
        return None, None, True

    # Handle MFA pending
    if user_data.get("mfa_pending") is True:
        app.storage.user["pending_mfa_cookie"] = cookie_value
        if request.url.path != "/mfa-verify":
            ui.navigate.to("/mfa-verify")
            return None, None, True

    return user_data, cookie_value, False


def requires_auth(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator to require authentication for a page.

    Validates the session cookie against the server-side Redis store.
    If invalid or missing, redirects to /login with the current path saved.

    Optimization: If SessionMiddleware has already validated and set request.state.user,
    we skip re-validation to avoid redundant Redis round-trips.
    """

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        request = _get_request_from_storage()
        user_data, _, should_return = await _validate_and_get_user_for_decorator(request)

        if should_return:
            return

        app.storage.user["logged_in"] = True
        app.storage.user["user"] = user_data

        return await func(*args, **kwargs)

    return wrapper


def requires_role(required_role: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to require a specific role for a page.

    Validates the session cookie against the server-side Redis store and checks
    for the required role. Unauthorized users are redirected to "/".

    Optimization: If SessionMiddleware has already validated and set request.state.user,
    we skip re-validation to avoid redundant Redis round-trips.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            request = _get_request_from_storage()
            user_data, _, should_return = await _validate_and_get_user_for_decorator(request)

            if should_return:
                return

            # Check role
            if user_data is not None and user_data.get("role") != required_role:
                ui.navigate.to("/")
                return

            app.storage.user["logged_in"] = True
            app.storage.user["user"] = user_data

            return await func(*args, **kwargs)

        return wrapper

    return decorator
