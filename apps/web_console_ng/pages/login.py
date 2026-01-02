from __future__ import annotations

import logging
from typing import Any, cast

from nicegui import app, ui
from starlette.requests import Request as StarletteRequest

from apps.web_console_ng import config
from apps.web_console_ng.auth.auth_router import get_auth_handler
from apps.web_console_ng.auth.client_ip import get_client_ip, is_trusted_ip
from apps.web_console_ng.auth.cookie_config import CookieConfig
from apps.web_console_ng.auth.redirects import sanitize_redirect_path
from apps.web_console_ng.auth.session_store import SessionValidationError

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


@ui.page("/login")
async def login_page() -> None:
    """Login page with auth type selection."""
    from datetime import UTC, datetime

    from apps.web_console_ng.auth.client_ip import extract_trusted_client_ip
    from apps.web_console_ng.auth.session_store import get_session_store

    # Check for existing valid session via server-side validation
    request = _get_request_from_storage("/login")
    cookie_cfg = CookieConfig.from_env()
    cookie_value = request.cookies.get(cookie_cfg.get_cookie_name())

    if cookie_value:
        session_store = get_session_store()
        client_ip = extract_trusted_client_ip(request, config.TRUSTED_PROXY_IPS)
        user_agent = request.headers.get("user-agent", "")
        try:
            session = await session_store.validate_session(cookie_value, client_ip, user_agent)
            if session:
                # Already logged in with valid session
                ui.navigate.to("/")
                return
        except SessionValidationError:
            # Redis unavailable - show service unavailable message
            with ui.card().classes("w-96 mx-auto mt-16 p-8"):
                ui.label("Service Temporarily Unavailable").classes("text-xl font-bold")
                ui.label("Please try again in a few moments.").classes("text-gray-600")
                ui.button("Retry", on_click=lambda: ui.navigate.to("/login")).classes("mt-4")
            return

    # mTLS auto-login: Check for client certificate from trusted proxy
    mtls_error: str | None = None
    cert_expiry_warning: str | None = None

    if config.AUTH_TYPE == "mtls":
        client_ip = extract_trusted_client_ip(request, config.TRUSTED_PROXY_IPS)
        # Only trust certificate headers from trusted proxies
        # Use is_trusted_ip helper to avoid code duplication
        remote_host = request.client.host if request.client else None
        is_from_trusted_proxy = is_trusted_ip(remote_host) if remote_host else False

        cert_verify = request.headers.get("X-SSL-Client-Verify", "")
        cert_dn = request.headers.get("X-SSL-Client-DN", "")
        cert_not_after = request.headers.get("X-SSL-Client-Not-After", "")

        if is_from_trusted_proxy:
            if cert_verify == "SUCCESS" and cert_dn:
                # Check certificate expiry warning (30 days) or expired
                if cert_not_after:
                    try:
                        # Parse nginx date format: "Dec 31 23:59:59 2025 GMT"
                        expiry = datetime.strptime(cert_not_after, "%b %d %H:%M:%S %Y %Z")
                        expiry = expiry.replace(tzinfo=UTC)
                        days_until_expiry = (expiry - datetime.now(UTC)).days
                        if days_until_expiry <= 0:
                            cert_expiry_warning = (
                                "Your certificate has expired. Please renew it immediately."
                            )
                        elif days_until_expiry < 30:
                            cert_expiry_warning = (
                                f"Your certificate expires in {days_until_expiry} days. "
                                "Please renew it soon."
                            )
                    except ValueError:
                        pass  # Ignore parse errors

                # Attempt auto-login with mTLS handler
                try:
                    handler = get_auth_handler("mtls")
                    result = await handler.authenticate(
                        client_dn=cert_dn,
                        client_ip=client_ip,
                        user_agent=request.headers.get("user-agent", ""),
                    )

                    if result.success:
                        # Set cookies and redirect
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

                        app.storage.user["logged_in"] = True
                        app.storage.user["user"] = result.user_data

                        # Show expiry warning before redirect
                        if cert_expiry_warning:
                            ui.notify(cert_expiry_warning, type="warning", timeout=10000)

                        redirect_to = sanitize_redirect_path(
                            app.storage.user.get("redirect_after_login")
                        )
                        if "redirect_after_login" in app.storage.user:
                            del app.storage.user["redirect_after_login"]
                        ui.navigate.to(redirect_to)
                        return
                    else:
                        mtls_error = result.error_message
                except Exception:
                    logger.exception("mTLS authentication failed")
                    mtls_error = "Authentication error. Please try again."
            elif cert_verify and cert_verify != "SUCCESS":
                mtls_error = f"Certificate verification failed: {cert_verify}"
            else:
                mtls_error = "Client certificate required for mTLS authentication."
        else:
            mtls_error = "Client certificate required for mTLS authentication."

    # Get redirect reason if any
    reason = app.storage.user.get("login_reason")
    if reason == "session_expired":
        ui.notify("Your session has expired. Please log in again.", type="warning")
        if "login_reason" in app.storage.user:
            del app.storage.user["login_reason"]

    with ui.card().classes("absolute-center w-96 p-8"):
        ui.label("Trading Console").classes("text-2xl font-bold text-center mb-2 w-full")
        ui.label("Sign in to continue").classes("text-gray-500 text-center mb-6 w-full")

        # Auth type selector
        auth_type = config.AUTH_TYPE
        if config.SHOW_AUTH_TYPE_SELECTOR:
            auth_type_select = ui.select(
                ["dev", "basic", "mtls", "oauth2"],
                value=auth_type,
                label="Authentication Method",
            ).classes("w-full mb-4")
        else:
            auth_type_select = None

        # Error message area
        error_label = ui.label("").classes("text-red-500 text-sm text-center hidden w-full mb-2")
        mtls_error_label = ui.label("").classes(
            "text-red-500 text-sm text-center hidden w-full mb-2"
        )
        if mtls_error:
            mtls_error_label.set_text(mtls_error)

        # Username/password form
        with ui.column().classes("w-full gap-4") as form_section:
            username_input = (
                ui.input(
                    label="Username",
                    placeholder="Enter your username",
                )
                .classes("w-full")
                .props("outlined")
            )

            password_input = (
                ui.input(
                    label="Password",
                    placeholder="Enter your password",
                    password=True,
                    password_toggle_button=True,
                )
                .classes("w-full")
                .props("outlined")
            )

            # Rate limit message
            rate_limit_label = ui.label("").classes("text-orange-500 text-sm hidden")

            async def submit_login() -> None:
                error_label.classes(add="hidden")
                rate_limit_label.classes(add="hidden")

                if not username_input.value or not password_input.value:
                    error_label.set_text("Username and password are required")
                    error_label.classes(remove="hidden")
                    return

                selected_auth = auth_type_select.value if auth_type_select else auth_type
                try:
                    handler = get_auth_handler(selected_auth)
                except ValueError as e:
                    error_label.set_text(str(e))
                    error_label.classes(remove="hidden")
                    return

                try:
                    # Get request info (reuse the request from scope or re-fetch)
                    inner_request = request

                    result = await handler.authenticate(
                        username=username_input.value,
                        password=password_input.value,
                        client_ip=get_client_ip(inner_request),
                        user_agent=inner_request.headers.get("user-agent", ""),
                    )

                    if result.success:
                        if result.requires_mfa:
                            # MFA required - set session cookie with mfa_pending flag
                            # The @requires_auth decorator will detect mfa_pending and allow
                            # access only to /mfa-verify (see middleware.py:162-166)
                            cookie_cfg = CookieConfig.from_env()
                            if hasattr(inner_request.state, "response"):
                                response = inner_request.state.response
                                if result.cookie_value:
                                    response.set_cookie(
                                        key=cookie_cfg.get_cookie_name(),
                                        value=result.cookie_value,
                                        **cookie_cfg.get_cookie_flags(),
                                    )
                            app.storage.user["pending_mfa_cookie"] = result.cookie_value
                            ui.navigate.to("/mfa-verify")
                        else:
                            # Set cookies
                            cookie_cfg = CookieConfig.from_env()
                            # Access Starlette response object
                            if hasattr(inner_request.state, "response"):
                                response = inner_request.state.response
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

                            app.storage.user["logged_in"] = True
                            app.storage.user["user"] = result.user_data

                            redirect_to = sanitize_redirect_path(
                                app.storage.user.get("redirect_after_login")
                            )
                            if "redirect_after_login" in app.storage.user:
                                del app.storage.user["redirect_after_login"]
                            ui.navigate.to(redirect_to)
                    else:
                        error_label.set_text(result.error_message or "Login failed")
                        error_label.classes(remove="hidden")

                        if result.locked_out:
                            remaining = result.lockout_remaining
                            rate_limit_label.set_text(
                                "Account locked due to repeated failures. "
                                f"Try again in {remaining} seconds."
                            )
                            rate_limit_label.classes(remove="hidden")
                        elif result.rate_limited:
                            rate_limit_label.set_text(
                                "Too many attempts. "
                                f"Please wait {result.retry_after} seconds before retrying."
                            )
                            rate_limit_label.classes(remove="hidden")

                except Exception:
                    logger.exception("Login error")
                    error_label.set_text("Authentication error. Please try again.")
                    error_label.classes(remove="hidden")

            ui.button("Sign In", on_click=submit_login).classes("w-full bg-blue-600 text-white")

            # Forgot password link (only for basic auth)
            forgot_password_link = ui.link("Forgot Password?", target="/forgot-password").classes(
                "text-sm text-blue-600 hover:underline mt-2 text-center w-full hidden"
            )

        # OAuth2 button section (always create, toggle visibility)
        with ui.column().classes("w-full") as oauth2_section:
            ui.separator().classes("my-4")
            ui.label("Or").classes("text-center text-gray-500 text-sm w-full")

            async def oauth2_login() -> None:
                try:
                    handler = get_auth_handler("oauth2")
                    # Should be cast to OAuth2AuthHandler to see get_authorization_url
                    # but runtime duck typing works
                    auth_url = await handler.get_authorization_url()
                    ui.navigate.to(auth_url, new_tab=False)
                except Exception:
                    logger.exception("OAuth2 init failed")
                    ui.notify("Authentication error. Please try again.", type="negative")

            ui.button("Sign in with Auth0", on_click=oauth2_login).classes(
                "w-full bg-orange-500 text-white mt-4"
            ).props("icon=login")

        # mTLS info section (always create, toggle visibility)
        with ui.column().classes("w-full hidden") as mtls_section:
            if mtls_error:
                ui.label("Certificate Authentication Failed").classes(
                    "text-red-500 font-bold text-center w-full"
                )
                ui.label(mtls_error).classes("text-red-500 text-sm text-center w-full mb-4")
                ui.label("Please use an alternative login method or contact support.").classes(
                    "text-gray-500 text-sm text-center w-full"
                )
            else:
                ui.label("Authenticating via client certificate...").classes("text-center w-full")
                ui.label(
                    "No valid certificate detected. Please ensure your certificate is installed."
                ).classes("text-gray-500 text-sm text-center w-full")

        # Visibility control
        def update_visibility(e: Any = None) -> None:
            selected = auth_type_select.value if auth_type_select else auth_type

            # Form section: hide for mTLS unless mTLS failed (fallback)
            if selected == "mtls":
                if mtls_error:
                    form_section.classes(remove="hidden")
                    mtls_section.classes(add="hidden")
                    mtls_error_label.classes(remove="hidden")
                else:
                    form_section.classes(add="hidden")
                    mtls_section.classes(remove="hidden")
                    mtls_error_label.classes(add="hidden")
            else:
                form_section.classes(remove="hidden")
                mtls_section.classes(add="hidden")
                mtls_error_label.classes(add="hidden")

            # OAuth2 section: show only for oauth2
            if selected == "oauth2":
                oauth2_section.classes(remove="hidden")
            else:
                oauth2_section.classes(add="hidden")

            # Forgot password link: show only for basic auth
            if selected == "basic":
                forgot_password_link.classes(remove="hidden")
            else:
                forgot_password_link.classes(add="hidden")

        if auth_type_select:
            auth_type_select.on_value_change(update_visibility)

        # Set initial visibility
        update_visibility()
