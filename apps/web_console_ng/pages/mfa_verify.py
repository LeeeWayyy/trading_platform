from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import cast
from urllib.parse import parse_qs

from nicegui import app, ui
from starlette.requests import Request as StarletteRequest

from apps.web_console_ng import config
from apps.web_console_ng.auth.client_ip import extract_trusted_client_ip
from apps.web_console_ng.auth.cookie_config import CookieConfig
from apps.web_console_ng.auth.mfa import MFAHandler
from apps.web_console_ng.auth.middleware import requires_auth
from apps.web_console_ng.auth.redirects import (
    sanitize_redirect_path,
    with_root_path,
    with_root_path_once,
)

logger = logging.getLogger(__name__)


def _get_request_from_storage(path: str) -> StarletteRequest:
    request = getattr(app.storage, "request", None)
    if isinstance(request, StarletteRequest):
        return request
    if request is None:
        scope = {
            "type": "http",
            "headers": [],
            "client": ("127.0.0.1", 0),
            "path": path,
        }
        return StarletteRequest(scope)
    return cast(StarletteRequest, request)


def _storage_user_get(key: str) -> object | None:
    """Read app.storage.user safely when request context is unavailable."""
    try:
        return app.storage.user.get(key)
    except RuntimeError as exc:
        logger.debug("app.storage.user unavailable for get", extra={"key": key, "error": str(exc)})
        return None


def _storage_user_pop(key: str) -> None:
    """Delete app.storage.user key safely when request context is unavailable."""
    try:
        app.storage.user.pop(key, None)
    except RuntimeError as exc:
        logger.debug("app.storage.user unavailable for pop", extra={"key": key, "error": str(exc)})


def _resolve_post_verify_redirect(request: StarletteRequest) -> str:
    """Resolve and sanitize post-MFA redirect destination."""
    root_path = str(request.scope.get("root_path", ""))
    query_string = request.scope.get("query_string", b"").decode("utf-8")
    query_params = parse_qs(query_string)
    next_param = query_params.get("next", [None])[0]
    if next_param:
        return sanitize_redirect_path(next_param, root_path=root_path)
    stored_redirect = _storage_user_get("redirect_after_login")
    if stored_redirect is not None:
        return sanitize_redirect_path(str(stored_redirect), root_path=root_path)
    return "/"


@ui.page("/mfa-verify")
@requires_auth
async def mfa_verify_page() -> None:
    """MFA verification step-up page."""
    request = _get_request_from_storage("/mfa-verify")
    root_path = str(request.scope.get("root_path", ""))
    login_path = with_root_path("/login", root_path=root_path)
    pending_cookie = _storage_user_get("pending_mfa_cookie")
    if not pending_cookie:
        ui.navigate.to(login_path)
        return
    pending_cookie_value = str(pending_cookie)

    with ui.card().classes("absolute-center w-96 p-8"):
        ui.label("Two-Factor Authentication").classes("text-xl font-bold mb-4 w-full text-center")
        ui.label("Enter the code from your authenticator app.").classes(
            "text-gray-500 text-sm mb-6 w-full text-center"
        )

        code_input = ui.input("Authentication Code").classes("w-full mb-4").props("outlined")

        async def verify() -> None:
            handler = MFAHandler()
            try:
                # Get request context for session validation
                request = _get_request_from_storage("/mfa-verify")
                request_root_path = str(request.scope.get("root_path", ""))
                client_ip = extract_trusted_client_ip(request, config.TRUSTED_PROXY_IPS)
                user_agent = request.headers.get("user-agent", "")

                result = await handler.verify(
                    pending_cookie_value,
                    code_input.value,
                    client_ip=client_ip,
                    user_agent=user_agent,
                )

                if result.success:
                    # Finalize session - set cookies and CSRF token
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
                            "MFA verify: request.state.response not available for cookie setting"
                        )

                    app.storage.user["logged_in"] = True
                    app.storage.user["user"] = result.user_data
                    # Store session_id for cache validation (prevents stale cache bypass)
                    if result.cookie_value:
                        app.storage.user["session_id"] = result.cookie_value
                        app.storage.user["last_validated_at"] = datetime.now(UTC).isoformat()

                    redirect_to = _resolve_post_verify_redirect(request)
                    _storage_user_pop("pending_mfa_cookie")
                    _storage_user_pop("redirect_after_login")

                    ui.notify("Verification successful", type="positive")
                    ui.navigate.to(with_root_path_once(redirect_to, root_path=request_root_path))
                else:
                    ui.notify(result.error_message or "Invalid code", type="negative")
                    code_input.value = ""
            except ValueError as e:
                # SECURITY: Log error details but show generic message to user
                logger.error(
                    "MFA verification error - invalid code format",
                    extra={"error": str(e), "page": "mfa_verify"},
                    exc_info=True,
                )
                ui.notify("Invalid code format. Please try again.", type="negative")
            except Exception as e:
                # SECURITY: Log error details but show generic message to user
                logger.error(
                    "MFA verification error",
                    extra={"error": str(e), "page": "mfa_verify"},
                    exc_info=True,
                )
                ui.notify("Verification failed. Please try again.", type="negative")

        ui.button("Verify", on_click=verify).classes("w-full bg-blue-600 text-white")

        ui.button(
            "Back to Login",
            on_click=lambda: ui.navigate.to(login_path),
        ).classes(
            "w-full text-gray-600 mt-2"
        ).props("flat")
