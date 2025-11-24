"""Token refresh endpoint with rotation and binding validation."""

import logging
from typing import Any

from fastapi import APIRouter, Cookie, HTTPException, Request
from fastapi.responses import JSONResponse

from apps.auth_service.dependencies import get_oauth2_handler, get_rate_limiters
from apps.web_console.utils import extract_client_ip_from_fastapi, extract_user_agent_from_fastapi

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/refresh")
async def refresh_token(
    request: Request,
    session_id: str = Cookie(None),
) -> Any:
    """Refresh access token.

    Validates session binding, refreshes tokens, rotates refresh token,
    preserves absolute timeout.

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

    # Get client info for session binding with trusted proxy validation
    # Fallback to "unknown" if request.client is None (test environments)
    def get_remote_addr() -> str:
        return request.client.host if request.client else "unknown"

    client_ip = extract_client_ip_from_fastapi(request, get_remote_addr)
    user_agent = extract_user_agent_from_fastapi(request)

    # Refresh tokens
    oauth2_handler = get_oauth2_handler()

    try:
        session_data = await oauth2_handler.refresh_tokens(
            session_id=session_id,
            ip_address=client_ip,
            user_agent=user_agent,
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
