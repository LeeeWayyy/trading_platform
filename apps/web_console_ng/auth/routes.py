from __future__ import annotations

from nicegui import app, ui
from starlette.requests import Request as StarletteRequest

from apps.web_console_ng import config
from apps.web_console_ng.auth.auth_router import get_auth_handler
from apps.web_console_ng.auth.client_ip import extract_trusted_client_ip
from apps.web_console_ng.auth.cookie_config import CookieConfig
from apps.web_console_ng.auth.csrf import verify_csrf_token
from apps.web_console_ng.auth.redirects import sanitize_redirect_path


@ui.page("/auth/callback")
async def auth_callback(code: str, state: str) -> None:
    """Handle OAuth2 callback."""
    handler = get_auth_handler("oauth2")

    # Get request info for validation - use same IP extraction as requires_auth
    request: StarletteRequest = app.storage.request  # type: ignore[attr-defined]
    if request.method == "POST":
        await verify_csrf_token(request)
    client_ip = extract_trusted_client_ip(request, config.TRUSTED_PROXY_IPS)
    user_agent = request.headers.get("user-agent", "")

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

        redirect_to = sanitize_redirect_path(app.storage.user.get("redirect_after_login"))
        if "redirect_after_login" in app.storage.user:
            del app.storage.user["redirect_after_login"]
        ui.navigate.to(redirect_to)
    else:
        ui.label("Login Failed").classes("text-h4 text-red-500 q-mb-md")
        ui.label(result.error_message or "Unknown error").classes("text-body1")
        ui.button("Back to Login", on_click=lambda: ui.navigate.to("/login")).classes("q-mt-md")
