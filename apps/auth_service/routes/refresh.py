"""Token refresh endpoint with rotation and optional binding validation."""

import logging
import os
import secrets
from typing import Any

from fastapi import APIRouter, Cookie, HTTPException, Request
from fastapi.responses import JSONResponse

from apps.auth_service.dependencies import get_oauth2_handler, get_rate_limiters
from libs.common.network_utils import extract_client_ip_from_fastapi

logger = logging.getLogger(__name__)
router = APIRouter()

INTERNAL_REFRESH_HEADER = "X-Internal-Auth"
INTERNAL_REFRESH_SECRET = os.getenv("INTERNAL_REFRESH_SECRET") or None


@router.post("/refresh")
async def refresh_token(
    request: Request,
    session_id: str = Cookie(None),
) -> Any:
    """Refresh access token with binding validation and guarded internal bypass.

    Refreshes tokens and rotates refresh token, preserving absolute timeout.

    SECURITY:
    - Default: Enforce IP/UA binding for all requests.
    - Controlled bypass: If `INTERNAL_REFRESH_SECRET` is configured **and** the
      caller presents matching `X-Internal-Auth` header, binding checks are
      skipped to support background refreshers that lack client context.
    - Bypass is rejected if the header is missing/invalid while the secret is set.

    Args:
        request: FastAPI request object (for IP/UA extraction)
        session_id: Session ID from HttpOnly cookie

    Returns:
        JSON response with success status

    Raises:
        HTTPException: 401 if session invalid or binding validation fails
        HTTPException: 429 if rate limit exceeded
    """
    if not session_id:
        raise HTTPException(status_code=401, detail="No session cookie")

    # Rate limiting (5/min per session)
    rate_limiters = get_rate_limiters()
    if not await rate_limiters["refresh"].is_allowed(session_id):
        logger.warning("Refresh rate limit exceeded", extra={"session_id": session_id[:8] + "..."})
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # Extract client IP with trusted proxy validation
    # Uses TRUSTED_PROXY_IPS to prevent X-Forwarded-For spoofing
    def get_remote_addr() -> str:
        return request.client.host if request.client else ""

    client_ip = extract_client_ip_from_fastapi(request, get_remote_addr)
    user_agent = request.headers.get("User-Agent", "")

    # Determine if caller is authorized for internal bypass
    provided_internal_secret = request.headers.get(INTERNAL_REFRESH_HEADER)
    is_internal_bypass = bool(
        INTERNAL_REFRESH_SECRET
        and provided_internal_secret
        and secrets.compare_digest(provided_internal_secret, INTERNAL_REFRESH_SECRET)
    )

    if INTERNAL_REFRESH_SECRET and provided_internal_secret and not is_internal_bypass:
        logger.warning("Invalid internal auth header on /refresh", extra={"client_ip": client_ip})
        raise HTTPException(status_code=401, detail="Invalid internal auth header")

    oauth2_handler = get_oauth2_handler()

    try:
        if is_internal_bypass:
            # Internal background refresh: Skip binding validation but require shared secret
            session_data = await oauth2_handler.refresh_tokens(
                session_id=session_id,
                ip_address=None,
                user_agent=None,
                enforce_binding=False,
            )
            logger.debug(
                "Internal refresh (binding bypass via shared secret)",
                extra={
                    "client_ip": client_ip,
                    "trusted_proxy": bool(request.headers.get("X-Forwarded-For")),
                },
            )
        else:
            # Standard path: enforce binding validation
            session_data = await oauth2_handler.refresh_tokens(
                session_id=session_id,
                ip_address=client_ip,
                user_agent=user_agent,
            )
            logger.debug(
                "Refresh with binding validation",
                extra={"client_ip": client_ip, "has_ua": bool(user_agent)},
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
