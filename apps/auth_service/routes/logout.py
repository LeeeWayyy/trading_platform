"""Logout endpoint with cookie clearing."""

import logging
from typing import Any

from fastapi import APIRouter, Cookie, Request
from fastapi.responses import RedirectResponse

from apps.auth_service.dependencies import get_config, get_oauth2_handler

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/logout")
async def logout(
    request: Request,
    session_id: str = Cookie(None),
) -> Any:
    """Handle logout.

    Deletes session from Redis, clears cookie, redirects to Auth0 logout.

    Args:
        session_id: Session ID from HttpOnly cookie

    Returns:
        RedirectResponse to Auth0 logout with cleared cookie
    """
    if not session_id:
        # No session, just redirect to login
        return RedirectResponse(url="/login", status_code=302)

    # Delete session
    oauth2_handler = get_oauth2_handler()
    logout_url = await oauth2_handler.handle_logout(session_id)

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
