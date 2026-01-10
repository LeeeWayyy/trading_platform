"""Tests for CSP middleware."""

import re

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from apps.auth_service.middleware.csp_middleware import CSPMiddleware


@pytest.fixture()
def app_with_csp():
    """Create test FastAPI app with CSP middleware."""
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint(request: Request):
        nonce = request.state.csp_nonce
        return {"nonce": nonce}

    app.add_middleware(
        CSPMiddleware,
        auth0_domain="dev-test.us.auth0.com",
        report_uri="/csp-report",
        enable_report_only=False,
    )

    return app


def test_csp_middleware_adds_header(app_with_csp):
    """Test CSP middleware adds Content-Security-Policy header."""
    client = TestClient(app_with_csp)
    response = client.get("/test")

    assert response.status_code == 200
    assert "Content-Security-Policy" in response.headers

    csp_header = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp_header
    assert "script-src 'self' 'nonce-" in csp_header
    assert "https://dev-test.us.auth0.com" in csp_header
    assert "https://cdn.auth0.com" in csp_header  # NEW: Auth0 CDN
    assert "object-src 'none'" in csp_header  # Gemini Code Review: Prevent object/embed
    assert "report-uri /csp-report" in csp_header


def test_csp_middleware_includes_streamlit_requirements(app_with_csp):
    """Test CSP middleware includes Streamlit-required directives."""
    client = TestClient(app_with_csp)
    response = client.get("/test")

    csp_header = response.headers["Content-Security-Policy"]

    # Streamlit WebSocket support
    assert "connect-src 'self' wss:" in csp_header

    # Streamlit blob URLs
    assert "blob:" in csp_header

    # Streamlit data URLs
    assert "data:" in csp_header

    # Streamlit inline styles (required)
    assert "style-src 'self' 'unsafe-inline'" in csp_header


def test_csp_middleware_generates_unique_nonces(app_with_csp):
    """Test CSP middleware generates unique nonces per request."""
    client = TestClient(app_with_csp)

    response1 = client.get("/test")
    response2 = client.get("/test")

    csp1 = response1.headers["Content-Security-Policy"]
    csp2 = response2.headers["Content-Security-Policy"]

    # Extract nonces from CSP headers (Gemini Fresh Review: MEDIUM - update for base64 nonces)
    nonce_pattern = r"'nonce-([A-Za-z0-9+/]+={0,2})'"

    nonce1 = re.search(nonce_pattern, csp1).group(1)
    nonce2 = re.search(nonce_pattern, csp2).group(1)

    assert nonce1 != nonce2, "Nonces should be unique per request"


def test_csp_middleware_report_only_mode():
    """Test CSP middleware in report-only mode."""
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint():
        return {"status": "ok"}

    app.add_middleware(
        CSPMiddleware,
        auth0_domain="dev-test.us.auth0.com",
        report_uri="/csp-report",
        enable_report_only=True,  # Report-only mode
    )

    client = TestClient(app)
    response = client.get("/test")

    assert "Content-Security-Policy-Report-Only" in response.headers
    assert "Content-Security-Policy" not in response.headers


def test_csp_middleware_nonce_stored_in_request_state(app_with_csp):
    """Test CSP middleware stores nonce in request.state for templates."""
    client = TestClient(app_with_csp)
    response = client.get("/test")

    # Endpoint returns nonce from request.state
    nonce_from_state = response.json()["nonce"]

    # Extract nonce from CSP header (Gemini Fresh Review: MEDIUM - update for base64 nonces)
    csp_header = response.headers["Content-Security-Policy"]
    nonce_pattern = r"'nonce-([A-Za-z0-9+/]+={0,2})'"
    nonce_from_header = re.search(nonce_pattern, csp_header).group(1)

    assert nonce_from_state == nonce_from_header


def test_csp_middleware_template_rendering_with_nonce():
    """Test CSP middleware nonce appears in rendered HTML template.

    Addresses Codex Iteration 2 Issue #1: Missing template integration example.
    """
    # Create app with template endpoint
    app = FastAPI()

    # Mock templates using in-memory template
    # Note: Jinja2Templates normally requires a directory, but we can mock the response manually
    # or use a temporary directory. Here we just simulate the response.

    @app.get("/page", response_class=HTMLResponse)
    async def test_page(request: Request):
        # Simulate template rendering with nonce
        nonce = request.state.csp_nonce
        html = f"""<!DOCTYPE html>
<html>
<head><title>Test</title></head>
<body>
    <script nonce="{nonce}">console.log('test');</script>
</body>
</html>"""
        return HTMLResponse(content=html)

    app.add_middleware(
        CSPMiddleware,
        auth0_domain="dev-test.us.auth0.com",
        report_uri="/csp-report",
        enable_report_only=False,
    )

    client = TestClient(app)
    response = client.get("/page")

    # Extract nonce from CSP header (Gemini Fresh Review: MEDIUM - update for base64 nonces)
    csp_header = response.headers["Content-Security-Policy"]
    nonce_pattern = r"'nonce-([A-Za-z0-9+/]+={0,2})'"
    nonce_match = re.search(nonce_pattern, csp_header)
    assert nonce_match, "CSP header should contain nonce"
    header_nonce = nonce_match.group(1)

    # Verify nonce appears in HTML
    assert (
        f'nonce="{header_nonce}"' in response.text
    ), "HTML should contain nonce matching CSP header"


def test_csp_middleware_adds_header_on_http_exception():
    """Test CSP middleware adds headers to HTTPException responses via exception handler.

    Addresses Codex Fresh Review: MEDIUM - Missing test coverage for CSP on error responses.
    The middleware re-raises HTTPException, which is then handled by a global exception
    handler that adds CSP headers. This test verifies the full flow.
    """
    import base64
    import secrets

    from fastapi.responses import JSONResponse

    from apps.auth_service.utils.csp_policy import build_csp_policy

    app = FastAPI()

    # Add exception handler (mirrors main.py implementation)
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        # Generate nonce if not in request state
        nonce = getattr(request.state, "csp_nonce", None)
        if nonce is None:
            nonce = base64.b64encode(secrets.token_bytes(16)).decode("ascii").rstrip("=")

        header_name = "Content-Security-Policy"
        csp_policy = build_csp_policy(nonce, "dev-test.us.auth0.com", "/csp-report")

        response = JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )
        response.headers[header_name] = csp_policy
        return response

    @app.get("/error-404")
    async def trigger_404():
        raise HTTPException(status_code=404, detail="Not found")

    app.add_middleware(
        CSPMiddleware,
        auth0_domain="dev-test.us.auth0.com",
        report_uri="/csp-report",
        enable_report_only=False,
    )

    client = TestClient(app)
    response = client.get("/error-404")

    # Verify 404 status preserved
    assert response.status_code == 404

    # Verify CSP header present on error response
    assert "Content-Security-Policy" in response.headers

    csp_header = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp_header
    assert "script-src 'self' 'nonce-" in csp_header
    assert "https://dev-test.us.auth0.com" in csp_header


def test_csp_middleware_adds_report_only_header_on_http_exception():
    """Test CSP middleware adds report-only headers to HTTPException responses."""
    import base64
    import secrets

    from fastapi.responses import JSONResponse

    from apps.auth_service.utils.csp_policy import build_csp_policy

    app = FastAPI()

    # Add exception handler in report-only mode
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        nonce = getattr(request.state, "csp_nonce", None)
        if nonce is None:
            nonce = base64.b64encode(secrets.token_bytes(16)).decode("ascii").rstrip("=")

        header_name = "Content-Security-Policy-Report-Only"  # Report-only mode
        csp_policy = build_csp_policy(nonce, "dev-test.us.auth0.com", "/csp-report")

        response = JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )
        response.headers[header_name] = csp_policy
        return response

    @app.get("/error-401")
    async def trigger_401():
        raise HTTPException(status_code=401, detail="Unauthorized")

    app.add_middleware(
        CSPMiddleware,
        auth0_domain="dev-test.us.auth0.com",
        report_uri="/csp-report",
        enable_report_only=True,  # Report-only mode
    )

    client = TestClient(app)
    response = client.get("/error-401")

    # Verify 401 status preserved
    assert response.status_code == 401

    # Verify report-only header present on error response
    assert "Content-Security-Policy-Report-Only" in response.headers
    assert "Content-Security-Policy" not in response.headers


def test_csp_middleware_handles_os_error():
    """Test CSP middleware handles OSError during request processing.

    Tests specific exception handling for network errors.
    """

    app = FastAPI()

    @app.get("/trigger-os-error")
    async def trigger_os_error():
        raise OSError("Network connection failed")

    app.add_middleware(
        CSPMiddleware,
        auth0_domain="dev-test.us.auth0.com",
        report_uri="/csp-report",
        enable_report_only=False,
    )

    client = TestClient(app)
    response = client.get("/trigger-os-error")

    # Should return 500 with CSP header
    assert response.status_code == 500
    assert "Content-Security-Policy" in response.headers


def test_csp_middleware_handles_io_error():
    """Test CSP middleware handles IOError during request processing.

    Tests specific exception handling for I/O errors.
    """
    app = FastAPI()

    @app.get("/trigger-io-error")
    async def trigger_io_error():
        raise OSError("Broken pipe")

    app.add_middleware(
        CSPMiddleware,
        auth0_domain="dev-test.us.auth0.com",
        report_uri="/csp-report",
        enable_report_only=False,
    )

    client = TestClient(app)
    response = client.get("/trigger-io-error")

    # Should return 500 with CSP header
    assert response.status_code == 500
    assert "Content-Security-Policy" in response.headers


def test_csp_middleware_handles_value_error():
    """Test CSP middleware handles ValueError during request processing.

    Tests specific exception handling for validation errors.
    """
    app = FastAPI()

    @app.get("/trigger-value-error")
    async def trigger_value_error():
        raise ValueError("Invalid data format")

    app.add_middleware(
        CSPMiddleware,
        auth0_domain="dev-test.us.auth0.com",
        report_uri="/csp-report",
        enable_report_only=False,
    )

    client = TestClient(app)
    response = client.get("/trigger-value-error")

    # Should return 500 with CSP header
    assert response.status_code == 500
    assert "Content-Security-Policy" in response.headers


def test_csp_middleware_handles_type_error():
    """Test CSP middleware handles TypeError during request processing.

    Tests specific exception handling for type errors.
    """
    app = FastAPI()

    @app.get("/trigger-type-error")
    async def trigger_type_error():
        raise TypeError("Expected str, got int")

    app.add_middleware(
        CSPMiddleware,
        auth0_domain="dev-test.us.auth0.com",
        report_uri="/csp-report",
        enable_report_only=False,
    )

    client = TestClient(app)
    response = client.get("/trigger-type-error")

    # Should return 500 with CSP header
    assert response.status_code == 500
    assert "Content-Security-Policy" in response.headers


def test_csp_middleware_handles_runtime_error():
    """Test CSP middleware handles RuntimeError during request processing.

    Tests specific exception handling for runtime errors.
    """
    app = FastAPI()

    @app.get("/trigger-runtime-error")
    async def trigger_runtime_error():
        raise RuntimeError("Application state error")

    app.add_middleware(
        CSPMiddleware,
        auth0_domain="dev-test.us.auth0.com",
        report_uri="/csp-report",
        enable_report_only=False,
    )

    client = TestClient(app)
    response = client.get("/trigger-runtime-error")

    # Should return 500 with CSP header
    assert response.status_code == 500
    assert "Content-Security-Policy" in response.headers

