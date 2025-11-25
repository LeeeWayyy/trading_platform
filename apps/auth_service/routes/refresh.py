"""Token refresh endpoint with rotation (no binding validation)."""

import logging
from typing import Any

from fastapi import APIRouter, Cookie, HTTPException, Request
from fastapi.responses import JSONResponse

from apps.auth_service.dependencies import get_oauth2_handler, get_rate_limiters

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/refresh")
async def refresh_token(
    request: Request,
    session_id: str = Cookie(None),
) -> Any:
    """Refresh access token without binding validation.

    Refreshes tokens and rotates refresh token, preserving absolute timeout.
    Relies on HttpOnly cookie + idle/absolute timeouts for security.

    FIX (Codex High): No binding validation since Streamlit background
    refreshes originate from the server, not the user's browser.

    Args:
        session_id: Session ID from HttpOnly cookie

    Returns:
        JSON response with success status
    """
    if not session_id:
        raise HTTPException(status_code=401, detail="No session cookie")

    # Rate limiting (5/min per session)
    rate_limiters = get_rate_limiters()
    if not await rate_limiters["refresh"].is_allowed(session_id):
        logger.warning("Refresh rate limit exceeded", extra={"session_id": session_id[:8] + "..."})
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # FIX (Codex High): Don't validate binding on refresh.
    # The HttpOnly session cookie itself proves authentication.
    # Binding validation would fail for Streamlit background refreshes
    # since they originate from the Streamlit server, not the user's browser.
    # Pass no IP/UA to skip binding validation.

    oauth2_handler = get_oauth2_handler()

    try:
        # Skip binding validation (ip_address=None, user_agent=None)
        session_data = await oauth2_handler.refresh_tokens(
            session_id=session_id,
        )
    except ValueError as e:
        logger.error("Token refresh failed", extra={"error": str(e)})
        raise HTTPException(status_code=401, detail=f"Refresh failed: {str(e)}") from e

    logger.info(
        "Tokens refreshed successfully",
        extra={
            "session_id": session_id[:8] + "...",
            "user_id": session_data.user_id,
        },
    )

    return JSONResponse(
        content={"status": "success", "message": "Tokens refreshed"},
        status_code=200,
    )
