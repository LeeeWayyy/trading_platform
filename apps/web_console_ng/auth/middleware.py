from __future__ import annotations

import asyncio
import functools
import hmac
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, cast

from nicegui import app, ui
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from apps.web_console_ng import config
from apps.web_console_ng.auth.client_ip import extract_trusted_client_ip, is_trusted_ip
from apps.web_console_ng.auth.cookie_config import CookieConfig
from apps.web_console_ng.auth.redirects import sanitize_redirect_path
from apps.web_console_ng.auth.session_store import (
    SessionValidationError,
    extract_session_id,
    get_session_store,
)
from apps.web_console_ng.core.database import get_db_pool
from apps.web_console_ng.core.redis_ha import get_redis_store

logger = logging.getLogger(__name__)


def _get_request_from_storage() -> Request:
    """Return current request from NiceGUI context or a minimal fallback for tests.

    NiceGUI provides request context via:
    1. storage.request_contextvar - set during request handling
    2. ui.context.client.request - available in UI context
    """
    from nicegui import storage
    from nicegui import ui as nicegui_ui

    # Try NiceGUI's request context variable first
    try:
        request = storage.request_contextvar.get()
        if request is not None:
            logger.debug("Got request from storage.request_contextvar")
            return request
    except (LookupError, AttributeError) as e:
        logger.debug("storage.request_contextvar not available: %s", type(e).__name__)

    # Try ui.context.client.request (available in UI context)
    try:
        request = nicegui_ui.context.client.request
        if request is not None:
            logger.debug("Got request from ui.context.client.request")
            return request
    except (AttributeError, RuntimeError) as e:
        logger.debug("ui.context.client.request not available: %s", type(e).__name__)

    # Fallback for tests only
    if config.DEBUG:
        logger.warning(
            "Creating fallback request - NiceGUI context missing. "
            "This is expected in tests but indicates an issue in production."
        )
        # SECURITY: Use TEST-NET-1 (RFC 5737) IP that won't be in production allowlists
        # Never use 127.0.0.1 as it may bypass localhost-trusting security checks
        scope = {
            "type": "http",
            "headers": [],
            "client": ("192.0.2.1", 0),  # TEST-NET-1 (RFC 5737) - never routable
            "path": "/",
        }
        return Request(scope)

    # In production, raise an error instead of silently failing
    raise RuntimeError("No request context available - NiceGUI context not initialized")


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
    cookie_cfg = CookieConfig.from_env()
    cookie_name = cookie_cfg.get_cookie_name()
    cookie_value = request.cookies.get(cookie_name)

    logger.debug(
        "session_validation: cookie_name=%s, has_cookie=%s, path=%s",
        cookie_name,
        cookie_value is not None,
        request.url.path,
    )

    if not cookie_value:
        logger.debug("session_validation: no cookie found")
        return None, None

    session_store = get_session_store()
    client_ip = extract_trusted_client_ip(request, config.TRUSTED_PROXY_IPS)
    user_agent = request.headers.get("user-agent", "")

    # Let SessionValidationError propagate - callers should handle with 503
    session = await session_store.validate_session(cookie_value, client_ip, user_agent)

    logger.debug(
        "session_validation: session_valid=%s, client_ip=%s",
        session is not None,
        client_ip,
    )

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

    # ACCEPTED_LIMITATION(ADR-0038): WebSocket session staleness
    #
    # /_nicegui and /socket.io are exempt because NiceGUI requires them
    # for UI interactivity (WebSocket transport).  This means role-override
    # middleware does NOT fire on WebSocket callbacks — only on HTTP requests.
    #
    # After a role demotion or force-logout, an already-open browser tab
    # retains the cached role in app.storage.user until the next HTTP request
    # triggers middleware.  NiceGUI does not expose an API to disconnect
    # individual clients by user ID, so immediate eviction is not possible.
    #
    # Mitigations (defense-in-depth):
    #   1. Every privileged callback re-checks get_current_user() +
    #      has_permission() at execution time.
    #   2. The 60s Redis role-cache TTL ensures the DB-authoritative role
    #      propagates on the next HTTP request (page nav, AJAX, etc.).
    #   3. invalidate_redis_sessions_for_user() deletes all Redis session
    #      keys, so the next page load forces re-authentication.
    #   4. Backend service calls (change_user_role, close_lot) re-validate
    #      permissions independently of the UI layer.
    #   5. update_session_role() uses WATCH/MULTI optimistic locking to
    #      prevent stale-role restoration from concurrent writes.
    _EXEMPT_PATH_PREFIXES = (
        "/_nicegui",
        "/socket.io",  # NiceGUI WebSocket/socket.io endpoint (required for UI interactivity)
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
                user_data, cookie_value = await _validate_session_and_get_user(request)
                if user_data:
                    request.state.user = user_data
                    user = user_data
                    # Also store in NiceGUI's app.storage.user for WebSocket context
                    try:
                        app.storage.user["logged_in"] = True
                        app.storage.user["user"] = user_data
                        # Store session_id for cache validation (prevents stale cache bypass)
                        if cookie_value:
                            app.storage.user["session_id"] = cookie_value
                            app.storage.user["last_validated_at"] = datetime.now(UTC).isoformat()
                        logger.debug("Stored user in app.storage.user from AuthMiddleware")
                    except (RuntimeError, AttributeError, KeyError) as storage_err:
                        # Storage may not be available in all contexts (e.g., non-NiceGUI requests)
                        logger.debug(
                            "Failed to store user in app.storage",
                            extra={"error_type": type(storage_err).__name__},
                        )
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

        # [T16.2] DB role override: make admin role changes authoritative
        if user and isinstance(user, dict) and user.get("user_id"):
            await self._override_role_from_db(request, user)

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
                response = RedirectResponse(url=redirect_url, status_code=302)

                # Clear potentially invalid cookie to prevent redirect loops
                # (e.g. if browser holds an old cookie with invalid signature)
                cookie_cfg = CookieConfig.from_env()
                cookie_name = cookie_cfg.get_cookie_name()

                # Delete with same path/domain settings as creation
                effective_path = "/" if cookie_cfg.secure else cookie_cfg.path
                domain = None if cookie_cfg.secure else cookie_cfg.domain

                response.delete_cookie(
                    key=cookie_name,
                    path=effective_path,
                    domain=domain,
                    secure=cookie_cfg.secure,
                    httponly=cookie_cfg.httponly,
                    samesite=cast(Literal["lax", "strict", "none"], cookie_cfg.samesite),
                )

                return response
            return Response(status_code=401)

        return cast(Response, await call_next(request))

    async def _override_role_from_db(self, request: Request, user: dict[str, Any]) -> None:
        """Override provider-derived session role with DB role from user_roles.

        Fail-open by design (ADR-0038): on any error (DB/Redis timeout, network
        partition), keep the provider-derived role unchanged so the user is not
        blocked.  The DB role is authoritative when reachable; when not, the
        provider role is the best available signal.
        Uses optional Redis cache (ng_role_cache:{user_id}, TTL 60s) to minimize DB load.
        """
        user_id = user.get("user_id")
        if not user_id:
            return

        redis_client = None
        cache_key = f"ng_role_cache:{user_id}"

        try:
            # Check Redis cache first
            try:
                store = get_redis_store()
                redis_client = await store.get_master()
                cached_role = await redis_client.get(cache_key)
                if cached_role is not None:
                    db_role = (
                        cached_role.decode("utf-8")
                        if isinstance(cached_role, bytes)
                        else str(cached_role)
                    )
                    # Sentinel: no DB row exists — keep provider role
                    if db_role == "__none__":
                        return
                    if db_role != user.get("role"):
                        self._apply_role_override(request, user, db_role)
                        # Best-effort: also persist role change in Redis session
                        try:
                            await self._update_session_payload(request, db_role)
                        except Exception as exc:
                            logger.debug("session_role_update_on_cache_hit_failed", extra={"user_id": user_id, "error": str(exc)})
                    return
            except Exception as exc:
                logger.debug("role_cache_miss_or_error", extra={"user_id": user_id, "error": str(exc)})

            # Query DB with timeout
            db_pool = get_db_pool()
            if db_pool is None:
                return  # Fail-open (ADR-0038): keep provider role when DB unavailable

            try:

                async def _db_role_lookup() -> Any:
                    async with db_pool.connection() as conn:
                        cursor = await conn.execute(
                            "SELECT role FROM user_roles WHERE user_id = %s",
                            (user_id,),
                        )
                        return await cursor.fetchone()

                row = await asyncio.wait_for(_db_role_lookup(), timeout=0.5)
            except Exception as exc:
                # Fail-open (ADR-0038): keep provider role on DB timeout/error
                logger.debug(
                    "db_role_lookup_timeout_or_error",
                    extra={"user_id": user_id, "error": str(exc)},
                )
                return

            if not row:
                # Cache "no row" sentinel to avoid repeated DB queries for
                # unprovisioned users (P6T16 feedback).
                if redis_client is not None:
                    try:
                        await redis_client.setex(cache_key, 60, "__none__")
                    except Exception as exc:
                        logger.debug("role_cache_write_none_failed", extra={"user_id": user_id, "error": str(exc)})
                return  # No DB row — keep provider role

            db_role = row[0]
            if db_role == user.get("role"):
                # Same role — still cache it to avoid repeated DB queries
                if redis_client is not None:
                    try:
                        await redis_client.setex(cache_key, 60, db_role)
                    except Exception as exc:
                        logger.debug("role_cache_write_failed", extra={"user_id": user_id, "error": str(exc)})
                return

            # Role differs — apply override
            self._apply_role_override(request, user, db_role)

            # Cache the DB role
            if redis_client is not None:
                try:
                    await redis_client.setex(cache_key, 60, db_role)
                except Exception as exc:
                    logger.debug("role_cache_write_failed_post_override", extra={"user_id": user_id, "error": str(exc)})

            # Update Redis session payload
            try:
                await self._update_session_payload(request, db_role)
            except Exception as exc:
                logger.debug("session_role_update_failed", extra={"user_id": user_id, "error": str(exc)})

        except Exception as exc:
            # Fail-open: never block requests due to role override errors
            logger.debug("role_override_failed", extra={"user_id": user_id, "error": str(exc)})

    @staticmethod
    def _apply_role_override(request: Request, user: dict[str, Any], db_role: str) -> None:
        """Apply DB role to all in-memory locations."""
        user["role"] = db_role
        request.state.user = user
        try:
            stored = app.storage.user.get("user")
            if isinstance(stored, dict):
                stored["role"] = db_role
                app.storage.user["user"] = stored
        except (RuntimeError, AttributeError, KeyError):
            pass  # Storage not available

    @staticmethod
    async def _update_session_payload(request: Request, db_role: str) -> None:
        """Best-effort: persist role change in Redis session payload."""
        cookie_name = CookieConfig.from_env().get_cookie_name()
        signed_cookie = request.cookies.get(cookie_name)
        if signed_cookie:
            session_id = extract_session_id(signed_cookie)
            session_store = get_session_store()
            await session_store.update_session_role(session_id, db_role)


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
                    user_data = session.get("user", {})
                    request.state.user = user_data
                    # Also store in NiceGUI's app.storage.user for WebSocket context
                    try:
                        app.storage.user["logged_in"] = True
                        app.storage.user["user"] = user_data
                        # Store session_id for cache validation (prevents stale cache bypass)
                        app.storage.user["session_id"] = cookie_value
                        app.storage.user["last_validated_at"] = datetime.now(UTC).isoformat()
                    except (RuntimeError, AttributeError, KeyError) as storage_err:
                        # Storage may not be available in all contexts (e.g., non-NiceGUI requests)
                        logger.debug(
                            "Failed to store user in app.storage from SessionMiddleware",
                            extra={"error_type": type(storage_err).__name__},
                        )
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
    # Max age before requiring Redis revalidation (prevents stale cache after session revocation)
    CACHE_REVALIDATION_SECONDS = 60

    cookie_cfg = CookieConfig.from_env()
    cookie_value = request.cookies.get(cookie_cfg.get_cookie_name())

    # FIRST: Check NiceGUI's app.storage.user - this persists across HTTP/WebSocket contexts
    # and is set by AuthMiddleware when session is valid
    stored_user = app.storage.user.get("user")
    cached_session_id = app.storage.user.get("session_id")
    last_validated_at = app.storage.user.get("last_validated_at")

    # Check if cache is stale (requires periodic Redis revalidation)
    cache_is_stale = True
    if last_validated_at:
        try:
            validated_time = datetime.fromisoformat(last_validated_at)
            age_seconds = (datetime.now(UTC) - validated_time).total_seconds()
            cache_is_stale = age_seconds > CACHE_REVALIDATION_SECONDS
        except (ValueError, TypeError):
            cache_is_stale = True

    # SECURITY: Only trust cached user if:
    # 1. Cookie is present (session not cleared by user/logout)
    # 2. Cookie matches cached session_id (prevents stale cache after re-login)
    # 3. Cache is not stale (ensures Redis TTL/revocation is respected)
    if (
        stored_user
        and app.storage.user.get("logged_in")
        and cookie_value
        and cached_session_id
        and cookie_value == cached_session_id
        and not cache_is_stale
    ):
        logger.debug("Using cached user from app.storage.user (session_id matches, cache fresh)")
        return stored_user, cookie_value, False
    elif stored_user and app.storage.user.get("logged_in"):
        # Cache exists but cookie missing/mismatched/stale - clear and revalidate
        reason = "stale" if cache_is_stale else "cookie_mismatch"
        logger.debug(
            "Cache invalidated: reason=%s, cookie_present=%s, session_id_match=%s",
            reason,
            cookie_value is not None,
            cookie_value == cached_session_id if cached_session_id else False,
        )
        app.storage.user.clear()

    # SECOND: Check request.state.user (set by SessionMiddleware on HTTP requests)
    user_data = getattr(request.state, "user", None)

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

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        request = _get_request_from_storage()
        user_data, cookie_value, should_return = await _validate_and_get_user_for_decorator(request)

        if should_return:
            return

        app.storage.user["logged_in"] = True
        app.storage.user["user"] = user_data
        # Store session_id for cache validation (prevents stale cache bypass)
        if cookie_value:
            app.storage.user["session_id"] = cookie_value
            app.storage.user["last_validated_at"] = datetime.now(UTC).isoformat()

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
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            request = _get_request_from_storage()
            user_data, cookie_value, should_return = await _validate_and_get_user_for_decorator(
                request
            )

            if should_return:
                return

            # Check role
            if user_data is not None and user_data.get("role") != required_role:
                ui.navigate.to("/")
                return

            app.storage.user["logged_in"] = True
            app.storage.user["user"] = user_data
            # Store session_id for cache validation (prevents stale cache bypass)
            if cookie_value:
                app.storage.user["session_id"] = cookie_value
                app.storage.user["last_validated_at"] = datetime.now(UTC).isoformat()

            return await func(*args, **kwargs)

        return wrapper

    return decorator
