from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal, cast
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from nicegui import app, ui
from redis import exceptions as redis_exceptions
from starlette.requests import Request as StarletteRequest
from starlette.responses import RedirectResponse, Response

from apps.web_console_ng import config
from apps.web_console_ng.auth.auth_router import get_auth_handler
from apps.web_console_ng.auth.client_ip import extract_trusted_client_ip
from apps.web_console_ng.auth.cookie_config import CookieConfig
from apps.web_console_ng.auth.rate_limiter import AuthRateLimiter
from apps.web_console_ng.auth.redirects import sanitize_redirect_path

logger = logging.getLogger(__name__)

# Use FastAPI APIRouter for proper HTTP route handling
auth_api_router = APIRouter()


@auth_api_router.post("/auth/login")
async def login_post(request: Request) -> Response:
    """Handle login form submission and set session cookie.

    This endpoint exists because cookies can only be set in HTTP responses,
    not from WebSocket event handlers (NiceGUI button clicks).
    """
    form_data = await request.form()
    username = form_data.get("username", "")
    password = form_data.get("password", "")
    auth_type_raw = form_data.get("auth_type", config.AUTH_TYPE)
    next_url_raw = form_data.get("next", "/")

    # Ensure string types for form values (form can return UploadFile for file fields)
    auth_type_str = str(auth_type_raw) if auth_type_raw else config.AUTH_TYPE
    next_url = str(next_url_raw) if next_url_raw else "/"

    client_ip = extract_trusted_client_ip(request, config.TRUSTED_PROXY_IPS)
    user_agent = request.headers.get("user-agent", "")

    # Validate inputs
    if not username or not password:
        params = urlencode({"error": "Username and password required", "next": next_url})
        return RedirectResponse(f"/login?{params}", status_code=303)

    try:
        # Cast to Literal type - validation happens in get_auth_handler which raises ValueError
        auth_type = cast(Literal["dev", "basic", "mtls", "oauth2"], auth_type_str)
        handler = get_auth_handler(auth_type)
    except ValueError as e:
        params = urlencode({"error": str(e), "next": next_url})
        return RedirectResponse(f"/login?{params}", status_code=303)

    result = await handler.authenticate(
        username=str(username),
        password=str(password),
        client_ip=client_ip,
        user_agent=user_agent,
    )

    if result.success:
        if result.requires_mfa:
            # MFA REQUIRED: Set pending session cookie and redirect to MFA verify page
            # The cookie contains the pending session which /mfa-verify will validate
            redirect_to = sanitize_redirect_path(next_url)
            params = urlencode({"pending": "mfa", "next": redirect_to})
            response = RedirectResponse(f"/mfa-verify?{params}", status_code=303)

            # Set pending MFA session cookie so /mfa-verify can validate the user
            cookie_cfg = CookieConfig.from_env()
            if result.cookie_value:
                response.set_cookie(
                    key=cookie_cfg.get_cookie_name(),
                    value=result.cookie_value,
                    **cookie_cfg.get_cookie_flags(),
                )
            # Note: CSRF token set after MFA verification completes
            logger.info("MFA required for user: %s, redirecting to /mfa-verify", username)
            return response

        # Create redirect response with cookies
        redirect_to = sanitize_redirect_path(next_url)
        response = RedirectResponse(redirect_to, status_code=303)

        # Set session cookie
        cookie_cfg = CookieConfig.from_env()
        if result.cookie_value:
            response.set_cookie(
                key=cookie_cfg.get_cookie_name(),
                value=result.cookie_value,
                **cookie_cfg.get_cookie_flags(),
            )
        if result.csrf_token:
            response.set_cookie(
                key="ng_csrf",
                value=result.csrf_token,
                **cookie_cfg.get_csrf_flags(),
            )

        logger.info("Login successful for user: %s", username)
        return response
    else:
        error_msg = result.error_message or "Login failed"
        if result.locked_out:
            error_msg = f"Account locked. Try again in {result.lockout_remaining} seconds."
        elif result.rate_limited:
            error_msg = f"Too many attempts. Wait {result.retry_after} seconds."

        params = urlencode({"error": error_msg, "next": next_url})
        return RedirectResponse(f"/login?{params}", status_code=303)


@ui.page("/auth/callback")
async def auth_callback(code: str, state: str) -> None:
    """Handle OAuth2 callback.

    Note: CSRF protection is provided by the OAuth2 `state` parameter which is
    validated in handle_callback() against the value stored in Redis during the
    authorization request. The state parameter provides equivalent CSRF protection
    to the double-submit cookie pattern used on other endpoints.
    """
    # Get request info for validation - use same IP extraction as requires_auth
    from nicegui import storage
    from nicegui import ui as nicegui_ui

    # Get request from NiceGUI context
    request: StarletteRequest | None = None
    try:
        request = storage.request_contextvar.get()
    except (LookupError, AttributeError) as e:
        logger.debug("storage.request_contextvar not available: %s", type(e).__name__)
    if request is None:
        try:
            request = nicegui_ui.context.client.request
        except (AttributeError, RuntimeError) as e:
            logger.debug("ui.context.client.request not available: %s", type(e).__name__)
    if request is None:
        ui.label("Error: No request context").classes("text-red-500")
        return
    client_ip = extract_trusted_client_ip(request, config.TRUSTED_PROXY_IPS)
    user_agent = request.headers.get("user-agent", "")

    # Rate limit OAuth2 callbacks to prevent IdP/Redis abuse
    # Use check_and_increment_ip since we don't have username yet (comes from IdP after token exchange)
    rate_limiter = AuthRateLimiter()
    try:
        is_blocked, retry_after, reason = await rate_limiter.check_and_increment_ip(client_ip)
    except redis_exceptions.RedisError as exc:
        logger.error("OAuth2 callback rate limiting failed: %s", exc)
        ui.label("Service Temporarily Unavailable").classes("text-h4 text-red-500 q-mb-md")
        ui.label("Please try again in a moment.").classes("text-body1")
        ui.button("Back to Login", on_click=lambda: ui.navigate.to("/login")).classes("q-mt-md")
        return

    if is_blocked:
        ui.label("Too Many Requests").classes("text-h4 text-red-500 q-mb-md")
        ui.label(f"Please wait {retry_after} seconds before trying again.").classes("text-body1")
        ui.button("Back to Login", on_click=lambda: ui.navigate.to("/login")).classes("q-mt-md")
        return

    handler = get_auth_handler("oauth2")

    result = await handler.handle_callback(
        code=code,
        state=state,
        client_ip=client_ip,
        user_agent=user_agent,
        redirect_uri=str(request.url.replace(query="")),
    )

    if result.success:
        # Set cookies
        cookie_cfg = CookieConfig.from_env()

        if hasattr(request.state, "response"):
            response = request.state.response

            if result.cookie_value:
                response.set_cookie(
                    key=cookie_cfg.get_cookie_name(),
                    value=result.cookie_value,
                    **cookie_cfg.get_cookie_flags(),
                )

            if result.csrf_token:
                response.set_cookie(
                    key="ng_csrf",
                    value=result.csrf_token,
                    **cookie_cfg.get_csrf_flags(),
                )
        else:
            logger.warning(
                "OAuth2 callback: request.state.response not available for cookie setting"
            )

        app.storage.user["logged_in"] = True
        app.storage.user["user"] = result.user_data
        # Store session_id for cache validation (prevents stale cache bypass)
        if result.cookie_value:
            app.storage.user["session_id"] = result.cookie_value
            app.storage.user["last_validated_at"] = datetime.now(UTC).isoformat()

        redirect_to = sanitize_redirect_path(app.storage.user.get("redirect_after_login"))
        if "redirect_after_login" in app.storage.user:
            del app.storage.user["redirect_after_login"]
        ui.navigate.to(redirect_to)
    else:
        ui.label("Login Failed").classes("text-h4 text-red-500 q-mb-md")
        ui.label(result.error_message or "Unknown error").classes("text-body1")
        ui.button("Back to Login", on_click=lambda: ui.navigate.to("/login")).classes("q-mt-md")
