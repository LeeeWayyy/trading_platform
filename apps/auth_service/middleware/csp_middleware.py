"""Content Security Policy (CSP) middleware for FastAPI auth service.

Adds nonce-based CSP headers to all responses to prevent XSS attacks.
Generates unique nonce per request for inline script execution.

IMPORTANT: This middleware covers auth_service responses.
For Streamlit page CSP coverage, see Deliverable 5 (Streamlit CSP Integration).

Nonce Template Integration:
- Middleware stores nonce in request.state.csp_nonce
- FastAPI templates access nonce via {{ csp_nonce }}
- Example: <script nonce="{{ csp_nonce }}">...</script>
"""

import base64
import logging
import secrets
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from apps.auth_service.utils.csp_policy import build_csp_policy

logger = logging.getLogger(__name__)


class CSPMiddleware(BaseHTTPMiddleware):
    """Middleware to add Content Security Policy headers with nonces.

    Implements defense-in-depth XSS protection by:
    - Restricting script sources to 'self' and Auth0 domains (including cdn.auth0.com)
    - Using nonces for inline scripts (Streamlit compatibility)
    - Blocking unsafe-inline and unsafe-eval
    - Logging CSP violations via report-uri
    - Supporting Streamlit requirements: wss://*.trading-platform.local, blob:, data:

    Nonce generation:
    - 16-byte random nonce per request
    - Base64-encoded (CSP spec compliant), padding trimmed
    - Stored in request.state.csp_nonce for template access

    CSP Coverage:
    - auth_service endpoints: /login, /callback, /refresh, /logout
    - For Streamlit pages: See Deliverable 5 (Streamlit CSP Integration)
    """

    def __init__(
        self,
        app: FastAPI,
        auth0_domain: str,
        report_uri: str = "/csp-report",
        enable_report_only: bool = False,
    ) -> None:
        """Initialize CSP middleware.

        Args:
            app: FastAPI application
            auth0_domain: Auth0 tenant domain (e.g., "dev-xyz.us.auth0.com")
            report_uri: Endpoint for CSP violation reports
            enable_report_only: If True, use Content-Security-Policy-Report-Only
        """
        super().__init__(app)
        self.auth0_domain = auth0_domain
        self.report_uri = report_uri
        self.enable_report_only = enable_report_only

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Add CSP headers to response.

        Generates nonce, processes request, adds CSP header to response.
        Preserves HTTP status codes by letting HTTPException propagate.
        (Codex Code Review Iteration 5: Critical - preserve API semantics)
        """
        # Generate nonce for this request (16 bytes, base64-encoded for CSP spec compliance)
        # (Codex Code Review Iteration 5: High - use base64 instead of hex)
        nonce = base64.b64encode(secrets.token_bytes(16)).decode("ascii").rstrip("=")

        # Store nonce in request state for template access
        request.state.csp_nonce = nonce

        # Build CSP policy once (reused for success and error paths)
        # Use centralized utility to avoid duplication with exception handler
        csp_policy = build_csp_policy(nonce, self.auth0_domain, self.report_uri)
        header_name = (
            "Content-Security-Policy-Report-Only"
            if self.enable_report_only
            else "Content-Security-Policy"
        )

        try:
            # Process request
            response = await call_next(request)
        except HTTPException:
            # Let HTTPException propagate to preserve status codes (404, 401, 429, etc.)
            # CSP headers will be added via exception handler in main.py
            raise
        except Exception as e:
            # Only catch non-HTTP exceptions (framework errors, crashes)
            # Return 500 with CSP header for these cases
            from fastapi.responses import JSONResponse

            response = JSONResponse(
                status_code=500,
                content={"detail": "Internal server error"},
            )
            response.headers[header_name] = csp_policy
            logger.error(
                "Unhandled exception in request - returning 500 with CSP header",
                extra={
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "path": request.url.path,
                    "nonce": nonce[:8] + "...",
                },
                exc_info=True,  # Include full traceback for debugging
            )
            # Return 500 response with CSP header
            return response

        # Add CSP header to successful response
        response.headers[header_name] = csp_policy

        # Log CSP header for debugging
        logger.debug(
            "CSP header added",
            extra={
                "nonce": nonce[:8] + "...",
                "report_only": self.enable_report_only,
                "path": request.url.path,
            },
        )

        return response
