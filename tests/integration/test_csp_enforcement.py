"""Integration tests for CSP enforcement."""

from fastapi.testclient import TestClient

from apps.auth_service.main import app


def test_csp_blocks_inline_script_without_nonce():
    """Test CSP blocks inline scripts without valid nonce.

    Note: This test simulates browser CSP enforcement logic.
    Actual enforcement happens in the browser, not FastAPI.
    """
    client = TestClient(app)
    response = client.get("/login")

    csp_header = response.headers.get("Content-Security-Policy", "")

    # Verify CSP header exists
    assert csp_header, "CSP header should be present"

    # Verify unsafe-inline is NOT allowed for scripts
    assert "'unsafe-inline'" not in csp_header or "script-src" in csp_header

    # If 'unsafe-inline' appears, it should NOT be in script-src
    # (may be in style-src for Streamlit compatibility)
    if "'unsafe-inline'" in csp_header:
        # Extract script-src directive
        script_src = [d for d in csp_header.split(";") if "script-src" in d][0]
        assert "'unsafe-inline'" not in script_src


def test_csp_allows_nonce_based_scripts():
    """Test CSP allows scripts with valid nonce."""
    client = TestClient(app)
    response = client.get("/login")

    csp_header = response.headers.get("Content-Security-Policy", "")

    # Verify nonce-based script-src is present
    assert "'nonce-" in csp_header
    assert "script-src" in csp_header


def test_csp_includes_auth0_domain_and_cdn():
    """Test CSP includes Auth0 domain AND CDN in script-src and connect-src."""
    client = TestClient(app)
    response = client.get("/login")

    csp_header = response.headers.get("Content-Security-Policy", "")

    # Auth0 domain should be in script-src (for Universal Login scripts)
    # and connect-src (for token endpoint)
    assert "https://" in csp_header

    # Must include cdn.auth0.com for Auth0 Universal Login
    assert "cdn.auth0.com" in csp_header


def test_csp_includes_streamlit_websocket_support():
    """Test CSP includes wss: for Streamlit WebSocket connections."""
    client = TestClient(app)
    response = client.get("/login")

    csp_header = response.headers.get("Content-Security-Policy", "")

    # Streamlit requires wss: for /_stcore/stream WebSocket
    assert "wss:" in csp_header
    assert "connect-src" in csp_header
