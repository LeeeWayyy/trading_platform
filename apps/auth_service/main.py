"""FastAPI auth service for OAuth2 endpoints.

This microservice handles OAuth2 authentication flows:
- /login: Initiates OAuth2 authorization code flow with PKCE
- /callback: Handles Auth0 callback, sets HttpOnly session cookie
- /refresh: Refreshes access token with rotation
- /logout: Clears session and redirects to Auth0 logout

Runs on port 8001, proxied by nginx for /auth/* routes.
"""

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from apps.auth_service.dependencies import get_oauth2_handler
from apps.auth_service.routes import callback, logout, refresh

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="Auth Service",
    description="OAuth2 authentication endpoints with PKCE",
    version="1.0.0",
)

# Include routers
app.include_router(callback.router, tags=["auth"])
app.include_router(refresh.router, tags=["auth"])
app.include_router(logout.router, tags=["auth"])


@app.get("/login")
async def login() -> Any:
    """Initiate OAuth2 login flow.

    Generates PKCE challenge, stores state in Redis, redirects to Auth0
    authorization endpoint with proper parameters.

    Returns:
        RedirectResponse to Auth0 authorization endpoint
    """
    oauth2_handler = get_oauth2_handler()
    authorization_url, oauth_state = await oauth2_handler.initiate_login()

    logger.info(
        "OAuth2 login initiated",
        extra={"state": oauth_state.state[:8] + "..."},
    )

    return RedirectResponse(url=authorization_url, status_code=302)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint.

    Returns:
        JSON response with service status
    """
    return {"status": "healthy", "service": "auth_service"}


@app.on_event("startup")
async def startup_event() -> None:
    """Log startup event."""
    logger.info("Auth service started")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    """Log shutdown event."""
    logger.info("Auth service shutting down")
