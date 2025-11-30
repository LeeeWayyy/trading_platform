"""OAuth2 callback handler with HttpOnly cookie setting."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from apps.auth_service.dependencies import get_config, get_oauth2_handler, get_rate_limiters
from libs.common.network_utils import extract_client_ip_from_fastapi, extract_user_agent_from_fastapi

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/callback")
async def callback(
    request: Request,
    code: str,
    state: str,
) -> Any:
    """Handle OAuth2 callback from Auth0.

    Validates state, exchanges code for tokens, creates session,
    sets HttpOnly cookie, redirects to dashboard.

    Args:
        code: Authorization code from Auth0
        state: State parameter for CSRF protection

    Returns:
        RedirectResponse with Set-Cookie header
    """
    # Rate limiting (10/min per IP)
    rate_limiters = get_rate_limiters()

    # Get client info for session binding with trusted proxy validation
    # Fallback to "unknown" if request.client is None (test environments)
    def get_remote_addr() -> str:
        return request.client.host if request.client else "unknown"

    client_ip = extract_client_ip_from_fastapi(request, get_remote_addr)
    user_agent = extract_user_agent_from_fastapi(request)

    if not await rate_limiters["callback"].is_allowed(client_ip):
        logger.warning("Callback rate limit exceeded", extra={"ip": client_ip})
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # Handle callback
    oauth2_handler = get_oauth2_handler()

    try:
        session_id, session_data = await oauth2_handler.handle_callback(
            code=code,
            state=state,
            ip_address=client_ip,
            user_agent=user_agent,
        )
    except ValueError as e:
        logger.error("OAuth2 callback failed", extra={"error": str(e)})
        raise HTTPException(status_code=400, detail=f"Authentication failed: {str(e)}") from e

    # Build redirect response
    response = RedirectResponse(url="/", status_code=302)

    # Set HttpOnly session cookie
    config = get_config()
    response.set_cookie(
        key="session_id",
        value=session_id,
        max_age=14400,  # 4 hours
        path="/",
        domain=config.cookie_domain,
        secure=True,  # HTTPS only
        httponly=True,  # XSS protection
        samesite="lax",  # CSRF protection
    )

    logger.info(
        "OAuth2 callback successful, cookie set",
        extra={
            "session_id": session_id[:8] + "...",
            "user_id": session_data.user_id,
        },
    )

    return response
