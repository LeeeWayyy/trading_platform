"""Auth routes for the NiceGUI web console."""

from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request, Response
from nicegui import app
from starlette.responses import RedirectResponse

from apps.web_console_ng import config
from apps.web_console_ng.auth.audit import AuthAuditLogger
from apps.web_console_ng.auth.client_ip import get_client_ip
from apps.web_console_ng.auth.cookie_config import CookieConfig
from apps.web_console_ng.auth.csrf import verify_csrf_token
from apps.web_console_ng.auth.providers.dev import DevAuthProvider
from apps.web_console_ng.auth.session_store import (
    RateLimitExceeded,
    get_session_store,
)

audit_logger = AuthAuditLogger.get(db_enabled=config.AUDIT_LOG_DB_ENABLED)
logger = logging.getLogger(__name__)


@app.post("/dev/login", dependencies=[Depends(verify_csrf_token)])
async def dev_login(request: Request, response: Response) -> dict[str, str]:
    """DEV ONLY: Create test session for session store validation."""
    if config.AUTH_TYPE != "dev":
        raise HTTPException(status_code=404, detail="Not found")

    provider = DevAuthProvider()
    user_data = await provider.authenticate(request)
    if not user_data:
        audit_logger.log_event(
            event_type="login_failure",
            user_id=None,
            session_id=None,
            client_ip=get_client_ip(request, config.TRUSTED_PROXY_IPS),
            user_agent=request.headers.get("user-agent", ""),
            auth_type=config.AUTH_TYPE,
            outcome="failure",
            failure_reason="dev_auth_failed",
        )
        raise HTTPException(status_code=401, detail="Authentication failed")

    client_ip = get_client_ip(request, config.TRUSTED_PROXY_IPS)
    device_info = {"user_agent": request.headers.get("user-agent", "")}
    session_store = get_session_store()
    try:
        cookie_value, csrf_token = await session_store.create_session(
            user_data, device_info, client_ip
        )
    except RateLimitExceeded:
        raise HTTPException(status_code=429, detail="rate_limited") from None

    cookie_config = CookieConfig.from_env()
    response.set_cookie(
        cookie_config.get_cookie_name(),
        cookie_value,
        **cookie_config.get_cookie_flags(),
    )
    response.set_cookie(
        "ng_csrf",
        csrf_token,
        **cookie_config.get_csrf_flags(),
    )

    return {"status": "ok", "user_id": str(user_data.get("user_id"))}


@app.post("/auth/login", dependencies=[Depends(verify_csrf_token)])
async def auth_login() -> RedirectResponse:
    if config.AUTH_TYPE == "dev":
        return RedirectResponse("/dev/login", status_code=307)

    raise HTTPException(
        status_code=503,
        detail={"error": "auth_provider_not_configured", "auth_type": config.AUTH_TYPE},
    )


@app.post("/auth/logout", dependencies=[Depends(verify_csrf_token)])
async def auth_logout(request: Request, response: Response) -> dict[str, str]:
    """Logout: always clears cookies regardless of provider state."""
    cookie_config = CookieConfig.from_env()

    cookie_value = request.cookies.get(cookie_config.get_cookie_name())
    if cookie_value:
        session_store = get_session_store()
        session_id = session_store.verify_cookie(cookie_value)
        if session_id:
            try:
                await session_store.invalidate_session(session_id)
            except Exception as exc:
                logger.warning("logout_session_invalidation_failed: %s", exc)
                audit_logger.log_event(
                    event_type="logout_invalidation_failed",
                    user_id=None,
                    session_id=session_id,
                    client_ip=get_client_ip(request, config.TRUSTED_PROXY_IPS),
                    user_agent=request.headers.get("user-agent", ""),
                    auth_type=config.AUTH_TYPE,
                    outcome="failure",
                    failure_reason="session_invalidation_failed",
                )

    response.delete_cookie(
        cookie_config.get_cookie_name(),
        **cookie_config.get_cookie_flags(),
    )
    response.delete_cookie(
        "ng_csrf",
        **cookie_config.get_csrf_flags(),
    )

    audit_logger.log_event(
        event_type="logout",
        user_id=None,
        session_id=None,
        client_ip=get_client_ip(request, config.TRUSTED_PROXY_IPS),
        user_agent=request.headers.get("user-agent", ""),
        auth_type=config.AUTH_TYPE,
        outcome="success",
    )

    return {"status": "logged_out"}


__all__ = ["dev_login", "auth_login", "auth_logout"]
