"""FastAPI auth service for OAuth2 endpoints.

This microservice handles OAuth2 authentication flows:
- /login: Initiates OAuth2 authorization code flow with PKCE
- /callback: Handles Auth0 callback, sets HttpOnly session cookie
- /refresh: Refreshes access token with rotation
- /logout: Clears session and redirects to Auth0 logout

Runs on port 8001, proxied by nginx for /auth/* routes.
"""

import base64
import logging
import os
import secrets
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from apps.auth_service.dependencies import get_config, get_oauth2_handler
from apps.auth_service.middleware.csp_middleware import CSPMiddleware
from apps.auth_service.routes import callback, csp_report, example_page, logout, refresh
from apps.auth_service.utils.csp_policy import build_csp_policy

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

# Add CSP middleware
config = get_config()
enable_report_only = os.getenv("CSP_REPORT_ONLY", "false").lower() == "true"

app.add_middleware(
    CSPMiddleware,
    auth0_domain=config.auth0_domain,
    report_uri="/csp-report",
    enable_report_only=enable_report_only,
)


# Add exception handler for HTTPException to add CSP headers
# (Codex Code Review Fresh Review: HIGH - CSP headers missing on error responses)
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Add CSP headers to HTTPException responses.

    Ensures CSP protection on all error responses (404, 401, 429, etc.)
    Middleware re-raises HTTPException, so we add CSP headers here.

    Args:
        request: FastAPI request object
        exc: HTTPException being handled

    Returns:
        JSONResponse with CSP header and error details
    """
    # Generate nonce if not already in request state (shouldn't happen, but defensive)
    nonce = getattr(request.state, "csp_nonce", None)
    if nonce is None:
        nonce = base64.b64encode(secrets.token_bytes(16)).decode("ascii").rstrip("=")

    # Build CSP policy using centralized utility (Gemini + Codex Fresh Review: MEDIUM)
    # Avoids duplication with middleware CSP policy logic
    header_name = (
        "Content-Security-Policy-Report-Only"
        if enable_report_only
        else "Content-Security-Policy"
    )

    csp_policy = build_csp_policy(nonce, config.auth0_domain, "/csp-report")

    # Create error response
    response = JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )
    response.headers[header_name] = csp_policy

    logger.debug(
        "CSP header added to HTTPException response",
        extra={
            "status_code": exc.status_code,
            "path": request.url.path,
            "nonce": nonce[:8] + "...",
        },
    )

    return response


# Include routers
app.include_router(callback.router, tags=["auth"])
app.include_router(refresh.router, tags=["auth"])
app.include_router(logout.router, tags=["auth"])
app.include_router(csp_report.router, tags=["security"])
app.include_router(example_page.router, tags=["example"])


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


@app.get("/test/echo-ip")
async def echo_ip(request: Request) -> dict[str, str]:
    """Echo client IP for integration testing.

    Returns client_ip, X-Forwarded-For, and X-Real-IP headers.
    Used by integration tests to verify Nginx real_ip directive.

    Security: Feature-flagged via ENABLE_TEST_ENDPOINTS (Codex Code Review Iteration 1).
    Only enabled in test/dev environments.

    Returns:
        JSON response with client IP and forwarded headers

    Raises:
        HTTPException: 404 if test endpoints are disabled
    """
    # Feature flag: disable in production (Codex/Gemini Code Review Low Issue)
    if not os.getenv("ENABLE_TEST_ENDPOINTS", "false").lower() == "true":
        raise HTTPException(status_code=404, detail="Not found")

    return {
        "client_ip": request.client.host if request.client else "unknown",
        "x_forwarded_for": request.headers.get("X-Forwarded-For", ""),
        "x_real_ip": request.headers.get("X-Real-IP", ""),
    }


def _validate_trusted_proxy_ips() -> list[str]:
    """Ensure TRUSTED_PROXY_IPS is configured to prevent header spoofing.

    M6 Fix: Dev/test environments get safe localhost defaults (127.0.0.1, ::1).
    Prod/staging requires explicit configuration (fail-closed).
    Env var always overrides defaults when explicitly set.
    """
    env = os.getenv("ENVIRONMENT", "dev").lower()

    # Check if env var is explicitly set (vs using defaults)
    env_proxy_ips = os.getenv("TRUSTED_PROXY_IPS")

    if env_proxy_ips is not None:
        # Env var explicitly set - use it (override any defaults)
        raw_ips = env_proxy_ips
    elif env in {"dev", "test", "development", "testing"}:
        # Dev/test: use safe localhost defaults
        raw_ips = "127.0.0.1,::1"
    else:
        # Prod/staging: no defaults, must be configured
        raw_ips = ""

    trusted_proxies = [ip.strip() for ip in raw_ips.split(",") if ip.strip()]

    if not trusted_proxies:
        if env in {"prod", "production", "staging"}:
            raise RuntimeError("TRUSTED_PROXY_IPS must be configured in production/staging")
        logger.warning(
            "TRUSTED_PROXY_IPS not set - using empty list (dev/test only)",
            extra={"environment": env},
        )

    return trusted_proxies


def _validate_internal_refresh_secret() -> str | None:
    """Ensure INTERNAL_REFRESH_SECRET is configured to prevent bypass with default secret.

    Fail-closed in prod-like environments to avoid accidental misconfiguration.
    The refresh endpoint allows bypassing IP/UA binding when this secret is provided,
    so it must be strong and unique in production.

    Returns:
        Configured secret or None (indicating feature disabled)

    Raises:
        RuntimeError: If secret not configured in production/staging
    """
    secret = os.getenv("INTERNAL_REFRESH_SECRET", "").strip()
    env = os.getenv("ENVIRONMENT", "dev").lower()

    # Empty secret = feature disabled
    # In prod/staging: FAIL CLOSED - background refresh won't work without this secret,
    # causing users to lose sessions when tokens expire. This is a critical misconfiguration.
    if not secret:
        if env in {"prod", "production", "staging"}:
            raise RuntimeError(
                "INTERNAL_REFRESH_SECRET not set in production/staging - "
                "background token refresh will fail, causing session loss. "
                "Set a strong unique secret (min 32 chars recommended)."
            )
        logger.info(
            "INTERNAL_REFRESH_SECRET not set - internal refresh bypass disabled (dev/test)",
            extra={"environment": env},
        )
        return None

    # Reject known insecure defaults
    INSECURE_DEFAULTS = {"dev-internal-refresh-secret", "test", "secret", "password"}
    if secret in INSECURE_DEFAULTS:
        if env in {"prod", "production", "staging"}:
            raise RuntimeError(
                f"INTERNAL_REFRESH_SECRET uses insecure default '{secret}' - "
                "must use strong unique secret in production/staging"
            )
        logger.warning(
            f"INTERNAL_REFRESH_SECRET uses insecure default '{secret}' (dev/test only)",
            extra={"environment": env},
        )

    # Enforce minimum strength in prod/staging (32 chars ≈ 192 bits entropy if random)
    MIN_SECRET_LENGTH = 32
    if env in {"prod", "production", "staging"} and len(secret) < MIN_SECRET_LENGTH:
        raise RuntimeError(
            f"INTERNAL_REFRESH_SECRET too short ({len(secret)} chars) - "
            f"must be at least {MIN_SECRET_LENGTH} chars in production/staging. "
            "Use: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )

    return secret


@app.on_event("startup")
async def startup_event() -> None:
    """Log startup event and validate critical config."""
    _validate_trusted_proxy_ips()
    _validate_internal_refresh_secret()
    logger.info("Auth service started")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    """Log shutdown event."""
    logger.info("Auth service shutting down")
