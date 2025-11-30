"""Logout endpoint with cookie clearing and binding validation."""

import logging
from typing import Any

from fastapi import APIRouter, Cookie, Request
from fastapi.responses import RedirectResponse

from apps.auth_service.dependencies import get_config, get_oauth2_handler
from libs.common.network_utils import (
    extract_client_ip_from_fastapi,
    extract_user_agent_from_fastapi,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/logout")
async def logout(
    request: Request,
    session_id: str = Cookie(None),
) -> Any:
    """Handle logout with binding validation.

    Validates session binding, deletes session, revokes refresh token at Auth0,
    clears cookie, redirects to Auth0 logout.

    FIX (Component 3 - Codex Medium #5): Validates binding before token revocation
    to prevent attacker with stolen cookie from revoking real user's refresh token.

    Args:
        session_id: Session ID from HttpOnly cookie

    Returns:
        RedirectResponse to Auth0 logout with cleared cookie
    """
    if not session_id:
        # No session, just redirect to login
        return RedirectResponse(url="/login", status_code=302)

    # Get client info for binding validation
    def get_remote_addr() -> str:
        return request.client.host if request.client else "unknown"

    client_ip = extract_client_ip_from_fastapi(request, get_remote_addr)
    user_agent = extract_user_agent_from_fastapi(request)

    # Delete session with binding validation and token revocation
    oauth2_handler = get_oauth2_handler()
    logout_url = await oauth2_handler.handle_logout(
        session_id,
        current_ip=client_ip,
        current_user_agent=user_agent,
    )

    # Build redirect response
    response = RedirectResponse(url=logout_url, status_code=302)

    # Clear session cookie
    config = get_config()
    response.set_cookie(
        key="session_id",
        value="",
        max_age=0,  # Expire immediately
        path="/",
        domain=config.cookie_domain,
        secure=True,
        httponly=True,
        samesite="lax",
    )

    logger.info(
        "User logged out, cookie cleared",
        extra={"session_id": session_id[:8] + "..."},
    )

    return response
