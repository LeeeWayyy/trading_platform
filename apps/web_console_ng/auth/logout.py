from __future__ import annotations

import hmac
import json
import logging
from collections.abc import Callable
from typing import cast

import redis.asyncio as redis
from fastapi import HTTPException, Request
from nicegui import app
from starlette.responses import JSONResponse, Response

from apps.web_console_ng import config
from apps.web_console_ng.auth.client_ip import get_client_ip
from apps.web_console_ng.auth.cookie_config import CookieConfig
from apps.web_console_ng.auth.csrf import CSRF_HEADER_NAME
from apps.web_console_ng.auth.providers.oauth2 import OAuth2AuthHandler
from apps.web_console_ng.auth.session_store import get_session_store

logger = logging.getLogger(__name__)


@app.post("/auth/logout")
async def logout_post(request: Request) -> Response:
    """Logout via POST with CSRF validation against the session token."""
    cookie_cfg = CookieConfig.from_env()
    cookie_value = request.cookies.get(cookie_cfg.get_cookie_name())
    if not cookie_value:
        raise HTTPException(status_code=401, detail="session_missing")

    session_store = get_session_store()
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent", "")
    session = await session_store.validate_session(cookie_value, client_ip, user_agent)
    if not session:
        raise HTTPException(status_code=401, detail="session_invalid")

    csrf_header = request.headers.get(CSRF_HEADER_NAME)
    session_csrf = session.get("csrf_token")
    if not csrf_header or not session_csrf or not hmac.compare_digest(
        csrf_header, str(session_csrf)
    ):
        raise HTTPException(status_code=403, detail="csrf_invalid")

    response = JSONResponse({"logout_url": None})
    logout_url = await perform_logout(request=request, response=response)
    response.body = json.dumps({"logout_url": logout_url}).encode("utf-8")
    response.headers["content-length"] = str(len(response.body))
    return response


async def perform_logout(
    request: Request | None = None,
    response: Response | None = None,
) -> str | None:
    """Perform complete logout (called from layout logout button).

    1. Get cookie_value from request cookie
    2. Validate and get session data (for OAuth2 logout URL)
    3. Invalidate session in Redis
    4. Clear client storage
    5. For OAuth2: return logout URL

    NOTE: Cookie clearing requires the response object (NiceGUI provides it via request.state.response).

    Returns:
        OAuth2 logout URL if applicable, None otherwise.
    """
    try:
        session_store = get_session_store()
        cookie_cfg = CookieConfig.from_env()

        # Get cookie_value from the request (P5T1 API)
        request = request or getattr(app.storage, "request", None)
        if request is None:
            logger.warning("No request object available for logout")
            app.storage.user.clear()
            return None

        response = response or getattr(getattr(request, "state", None), "response", None)

        cookie_value = request.cookies.get(cookie_cfg.get_cookie_name())
        logout_url: str | None = None

        if cookie_value:
            # Validate session to get user data (for OAuth2 logout)
            client_ip = get_client_ip(request)
            user_agent = request.headers.get("user-agent", "")
            session = await session_store.validate_session(
                cookie_value, client_ip, user_agent
            )

            # Extract session_id from cookie_value for invalidation
            session_id = session_store.verify_cookie(cookie_value)
            if session_id:
                await session_store.invalidate_session(session_id)

            # Clear Streamlit session if exists (parallel run)
            if session:
                user_id = session.get("user", {}).get("user_id")
                if user_id:
                    r = _redis_from_url(config.REDIS_URL, decode_responses=False)
                    await r.delete(f"st_session:{user_id}")
                    await r.aclose()

                # Handle OAuth2 RP-initiated logout
                auth_method = session.get("user", {}).get("auth_method")
                id_token = session.get("user", {}).get("id_token")

                # Clear NiceGUI client storage
                app.storage.user.clear()

                if auth_method == "oauth2" and id_token:
                    handler = OAuth2AuthHandler()
                    logout_url = await handler.get_logout_url(id_token)
            else:
                # Session invalid but clear client storage anyway
                app.storage.user.clear()
        else:
            # No cookie, just clear client storage
            app.storage.user.clear()

        if response is not None:
            session_flags = cookie_cfg.get_cookie_flags()
            csrf_flags = cookie_cfg.get_csrf_flags()
            response.delete_cookie(
                key=cookie_cfg.get_cookie_name(),
                path=session_flags.get("path", "/"),
                domain=session_flags.get("domain"),
            )
            response.delete_cookie(
                key="ng_csrf",
                path=csrf_flags.get("path", "/"),
                domain=csrf_flags.get("domain"),
            )

        return logout_url

    except Exception as e:
        logger.error(f"Logout error: {e}")
        # Ensure client storage is cleared even on error
        try:
            app.storage.user.clear()
        except Exception:
            pass

    return None


def _redis_from_url(url: str, *, decode_responses: bool) -> redis.Redis:
    from_url = cast(Callable[..., redis.Redis], redis.Redis.from_url)
    return from_url(url, decode_responses=decode_responses)
