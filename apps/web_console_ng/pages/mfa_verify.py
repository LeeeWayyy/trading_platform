from __future__ import annotations

from nicegui import app, ui
from starlette.requests import Request as StarletteRequest

from apps.web_console_ng import config
from apps.web_console_ng.auth.client_ip import extract_trusted_client_ip
from apps.web_console_ng.auth.cookie_config import CookieConfig
from apps.web_console_ng.auth.mfa import MFAHandler
from apps.web_console_ng.auth.middleware import requires_auth


@ui.page("/mfa-verify")
@requires_auth
async def mfa_verify_page() -> None:
    """MFA verification step-up page."""
    pending_cookie = app.storage.user.get("pending_mfa_cookie")
    if not pending_cookie:
        ui.navigate.to("/login")
        return

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
                request: StarletteRequest = app.storage.request  # type: ignore[attr-defined]
                client_ip = extract_trusted_client_ip(request, config.TRUSTED_PROXY_IPS)
                user_agent = request.headers.get("user-agent", "")

                result = await handler.verify(
                    pending_cookie,
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

                    app.storage.user["logged_in"] = True
                    app.storage.user["user"] = result.user_data

                    if "pending_mfa_cookie" in app.storage.user:
                        del app.storage.user["pending_mfa_cookie"]

                    ui.notify("Verification successful", type="positive")
                    ui.navigate.to("/")
                else:
                    ui.notify(result.error_message or "Invalid code", type="negative")
                    code_input.value = ""
            except Exception as e:
                ui.notify(f"Verification error: {e}", type="negative")

        ui.button("Verify", on_click=verify).classes("w-full bg-blue-600 text-white")

        ui.button("Back to Login", on_click=lambda: ui.navigate.to("/login")).classes(
            "w-full text-gray-600 mt-2"
        ).props("flat")
