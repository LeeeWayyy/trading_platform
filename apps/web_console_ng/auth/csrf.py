"""CSRF validation for NiceGUI auth endpoints."""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

CSRF_COOKIE_NAME = "ng_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"

CSRF_EXEMPT_PATHS = {"/auth/login", "/auth/callback", "/dev/login", "/health"}


async def verify_csrf_token(request: Request) -> None:
    """Validate CSRF token using the double-submit cookie pattern."""
    if request.url.path in CSRF_EXEMPT_PATHS:
        return

    csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME)
    csrf_header = request.headers.get(CSRF_HEADER_NAME)

    if not csrf_cookie or not csrf_header:
        raise HTTPException(status_code=403, detail="csrf_missing")

    if not hmac.compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(status_code=403, detail="csrf_invalid")


__all__ = ["CSRF_EXEMPT_PATHS", "verify_csrf_token"]
