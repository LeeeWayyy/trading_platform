"""CSP policy builder utility.

Centralized CSP policy construction to avoid duplication between middleware
and exception handler.

Addresses Gemini + Codex Fresh Review: MEDIUM - Extract duplicated CSP policy logic.
"""


def build_csp_policy(nonce: str, auth0_domain: str, report_uri: str) -> str:
    """Build CSP policy string with nonce.

    Centralized CSP policy construction used by both:
    - CSPMiddleware (normal response path)
    - HTTPException handler (error response path)

    Updated to include:
    - Auth0 CDN domains (cdn.auth0.com) for Universal Login
    - Streamlit requirements: wss:, blob:, data:
    - Comprehensive allowlist for all required sources
    - frame-ancestors directive for clickjacking protection

    Args:
        nonce: Random nonce for this request (base64-encoded)
        auth0_domain: Auth0 tenant domain (e.g., "dev-xyz.us.auth0.com")
        report_uri: Endpoint for CSP violation reports

    Returns:
        CSP policy string ready for Content-Security-Policy header
    """
    # Note: unsafe-inline for style-src required for Streamlit/FastAPI templating
    # TODO: Replace with nonce-based styles when Streamlit supports it
    policy_directives = [
        "default-src 'self'",
        # script-src: Allow Auth0 wildcard + CDN + nonce
        f"script-src 'self' 'nonce-{nonce}' https://{auth0_domain} https://cdn.auth0.com",
        # style-src: Streamlit requires unsafe-inline
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        # img-src: Include blob: for Streamlit
        "img-src 'self' data: blob: https:",
        # connect-src: Restrict WebSockets to platform domains only (Codex Code Review Iteration 5: Medium)
        f"connect-src 'self' wss://*.trading-platform.local https://{auth0_domain} https://cdn.auth0.com https://*.trading-platform.local",
        f"frame-src 'self' https://{auth0_domain}",
        # Codex Fresh Review: MEDIUM - add frame-ancestors for clickjacking protection
        "frame-ancestors 'none'",
        # Gemini Code Review: Tighten security (prevent object/embed)
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "upgrade-insecure-requests",
        "block-all-mixed-content",
        f"report-uri {report_uri}",
    ]

    return "; ".join(policy_directives)
