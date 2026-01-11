from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from nicegui import app, ui
from starlette.requests import Request as StarletteRequest

from apps.web_console_ng import config
from apps.web_console_ng.auth.auth_router import get_auth_handler
from apps.web_console_ng.auth.cookie_config import CookieConfig
from apps.web_console_ng.auth.redirects import sanitize_redirect_path
from apps.web_console_ng.auth.session_store import SessionValidationError

logger = logging.getLogger(__name__)


def _get_request_from_storage(path: str) -> StarletteRequest:
    """Get request from NiceGUI context or create fallback for tests."""
    from nicegui import storage
    from nicegui import ui as nicegui_ui

    # Try NiceGUI's request context variable first
    try:
        request = storage.request_contextvar.get()
        if request is not None:
            return request
    except (LookupError, AttributeError) as e:
        logger.debug("storage.request_contextvar not available: %s", type(e).__name__)

    # Try ui.context.client.request (available in UI context)
    try:
        request = nicegui_ui.context.client.request
        if request is not None:
            return request
    except (AttributeError, RuntimeError) as e:
        logger.debug("ui.context.client.request not available: %s", type(e).__name__)

    # Fallback for tests
    scope = {
        "type": "http",
        "headers": [],
        "client": ("127.0.0.1", 0),
        "path": path,
    }
    return StarletteRequest(scope)


def _get_redirect_destination(request: StarletteRequest) -> str:
    """Get redirect destination from query param, storage, or default to '/'."""
    from urllib.parse import parse_qs

    # Check query parameter first (from AuthMiddleware redirect)
    query_string = request.scope.get("query_string", b"").decode("utf-8")
    query_params = parse_qs(query_string)
    next_param = query_params.get("next", [None])[0]
    if next_param:
        return sanitize_redirect_path(next_param)

    # Fall back to storage (from decorator redirect)
    stored_redirect = app.storage.user.get("redirect_after_login")
    if stored_redirect:
        return sanitize_redirect_path(stored_redirect)

    return "/"


@ui.page("/login")
async def login_page() -> None:
    """Login page with auth type selection."""
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

    # Check for error message from POST endpoint (passed via query param)
    from urllib.parse import parse_qs

    query_string = request.scope.get("query_string", b"").decode("utf-8")
    query_params = parse_qs(query_string)
    login_error = query_params.get("error", [None])[0]

    # mTLS auto-login: Delegate all header parsing and validation to MTLSAuthHandler
    mtls_error: str | None = None

    if config.AUTH_TYPE == "mtls":
        try:
            handler = get_auth_handler("mtls")
            # Use try_auto_login which encapsulates all mTLS header parsing logic
            result = await handler.try_auto_login(request)

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
                # Store session_id for cache validation (prevents stale cache bypass)
                if result.cookie_value:
                    app.storage.user["session_id"] = result.cookie_value
                    app.storage.user["last_validated_at"] = datetime.now(UTC).isoformat()

                # Show certificate expiry warning before redirect (from handler)
                if result.warning_message:
                    ui.notify(result.warning_message, type="warning", timeout=10000)

                redirect_to = _get_redirect_destination(request)
                if "redirect_after_login" in app.storage.user:
                    del app.storage.user["redirect_after_login"]
                ui.navigate.to(redirect_to)
                return
            else:
                mtls_error = result.error_message
        except ValueError as e:
            logger.error(
                "mTLS authentication failed - invalid certificate data",
                extra={"error": str(e), "page": "login"},
                exc_info=True,
            )
            mtls_error = "Invalid certificate. Please check your certificate configuration."
        except Exception as e:
            logger.error(
                "mTLS authentication failed - unexpected error",
                extra={"error": str(e), "page": "login"},
                exc_info=True,
            )
            mtls_error = "Authentication error. Please try again."

    # Get redirect reason if any
    reason = app.storage.user.get("login_reason")
    if reason == "session_expired":
        ui.notify("Your session has expired. Please log in again.", type="warning")
        if "login_reason" in app.storage.user:
            del app.storage.user["login_reason"]

    # Hidden form for HTTP POST login (cookies can only be set in HTTP responses)
    next_url = _get_redirect_destination(request)
    ui.html(f"""
        <form id="login-form" action="/auth/login" method="post" style="display:none">
            <input name="username" />
            <input name="password" />
            <input name="auth_type" />
            <input name="next" value="{next_url}" />
        </form>
    """)

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
        if login_error:
            error_label.set_text(login_error)
            error_label.classes(remove="hidden")

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
                """Submit login via hidden form (HTTP POST sets cookies)."""
                logger.info("submit_login called - username=%s", username_input.value)
                ui.notify("Logging in...", type="info")

                error_label.classes(add="hidden")
                rate_limit_label.classes(add="hidden")

                # Validate inputs
                if not username_input.value or not password_input.value:
                    error_label.set_text("Username and password are required")
                    error_label.classes(remove="hidden")
                    ui.notify("Username and password are required", type="warning")
                    return

                selected_auth = auth_type_select.value if auth_type_select else auth_type
                logger.info("Submitting form with auth_type=%s", selected_auth)

                # Submit via hidden form (HTTP POST allows setting cookies)
                # json.dumps prevents XSS by properly escaping values
                # Fire-and-forget to avoid server-side timeout if the browser is busy.
                try:
                    ui.run_javascript(f"""
                        console.log('Form submission starting...');
                        const f = document.getElementById('login-form');
                        if (!f) {{
                            console.error('Form not found!');
                            alert('Form not found - please refresh the page');
                            return;
                        }}
                        console.log('Form found, setting values...');
                        f.username.value = {json.dumps(username_input.value)};
                        f.password.value = {json.dumps(password_input.value)};
                        f.auth_type.value = {json.dumps(selected_auth)};
                        console.log('Submitting form...');
                        f.submit();
                    """)
                except TypeError as e:
                    logger.error(
                        "Login form submission failed - invalid input type",
                        extra={"error": str(e), "username": username_input.value, "page": "login"},
                        exc_info=True,
                    )
                    ui.notify("Invalid input. Please check your credentials.", type="negative")
                except (RuntimeError, AttributeError) as e:
                    logger.error(
                        "JavaScript execution failed during login",
                        extra={"error": str(e), "username": username_input.value, "page": "login"},
                        exc_info=True,
                    )
                    error_label.set_text("Login form submission failed. Please refresh and retry.")
                    error_label.classes(remove="hidden")
                    ui.notify("Login error. Please try again.", type="negative")

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
                except AttributeError as e:
                    logger.error(
                        "OAuth2 init failed - missing authorization URL method",
                        extra={"error": str(e), "page": "login"},
                        exc_info=True,
                    )
                    ui.notify("OAuth2 configuration error. Please contact support.", type="negative")
                except ValueError as e:
                    logger.error(
                        "OAuth2 init failed - invalid configuration",
                        extra={"error": str(e), "page": "login"},
                        exc_info=True,
                    )
                    ui.notify("OAuth2 configuration error. Please try again.", type="negative")
                except Exception as e:
                    logger.error(
                        "OAuth2 initialization failed",
                        extra={"error": str(e), "page": "login"},
                        exc_info=True,
                    )
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
