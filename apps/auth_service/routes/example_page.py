"""Example page route demonstrating nonce injection into templates."""

import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="apps/auth_service/templates")


@router.get("/example-page", response_class=HTMLResponse)
async def example_page(request: Request) -> HTMLResponse:
    """Example page demonstrating nonce injection into templates.

    Security: Feature-flagged via ENABLE_TEST_ENDPOINTS (Codex Code Review Iteration 3).
    Only enabled in test/dev environments to prevent information disclosure.

    Returns:
        HTML response with CSP nonce demonstration

    Raises:
        HTTPException: 404 if test endpoints are disabled
    """
    # Feature flag: disable in production (Codex Code Review Low Issue)
    if not os.getenv("ENABLE_TEST_ENDPOINTS", "false").lower() == "true":
        raise HTTPException(status_code=404, detail="Not found")

    # Nonce is automatically available via request.state.csp_nonce
    # (set by CSPMiddleware)
    return templates.TemplateResponse(
        "example.html",
        {
            "request": request,
            "csp_nonce": request.state.csp_nonce,  # Pass nonce to template
            "some_data": "example",
        },
    )
