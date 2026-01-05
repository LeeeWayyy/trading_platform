"""Integration tests for Nginx OAuth2 routing.

NOTE: These tests require docker-compose --profile oauth2 up -d
See Codex Review Issue #6 for test harness usage.
"""

import pytest
import requests

# Mark all tests in this module as e2e (require full system setup)
pytestmark = pytest.mark.e2e


@pytest.fixture()
def nginx_base_url():
    """Base URL for Nginx reverse proxy."""
    return "https://localhost:443"


def _is_nginx_oauth2_available(base_url: str) -> bool:
    """Check if nginx with OAuth2 profile is available and responding correctly.

    Returns True only if nginx responds properly without requiring mTLS.
    When mTLS is configured, nginx returns 400 "No required SSL certificate was sent"
    which means the OAuth2 profile tests cannot run.
    """
    try:
        response = requests.get(f"{base_url}/", verify=False, timeout=2)
        server = response.headers.get("Server", "")

        # Check if nginx is running
        if "nginx" not in server.lower():
            return False

        # If nginx returns 400 with SSL certificate error, mTLS is enabled
        # OAuth2 tests require non-mTLS nginx setup (docker-compose --profile oauth2)
        if response.status_code == 400 and "SSL certificate" in response.text:
            return False

        # OAuth2 profile should return 200, 302, or 401 for /
        return response.status_code in [200, 302, 401]
    except Exception:
        return False


@pytest.mark.e2e()
def test_nginx_routes_login_to_auth_service(nginx_base_url):
    """Test /login routes to auth_service."""
    # Note: This test requires Nginx + auth_service running
    # Use docker-compose --profile oauth2 up -d

    if not _is_nginx_oauth2_available(nginx_base_url):
        pytest.skip("Nginx OAuth2 profile not running (requires docker-compose --profile oauth2)")

    try:
        response = requests.get(
            f"{nginx_base_url}/login",
            allow_redirects=False,
            verify=False,  # Skip cert verification for self-signed certs
        )
    except requests.exceptions.ConnectionError:
        pytest.skip("Nginx not running")

    # Should redirect to Auth0 (302 Found)
    assert response.status_code == 302
    assert "auth0.com" in response.headers.get("Location", "")


def test_nginx_routes_callback_to_auth_service(nginx_base_url):
    """Test /callback routes to auth_service."""
    # Send invalid callback (missing code/state)
    # Should return 400 Bad Request from auth_service

    if not _is_nginx_oauth2_available(nginx_base_url):
        pytest.skip("Nginx OAuth2 profile not running (requires docker-compose --profile oauth2)")

    try:
        response = requests.get(
            f"{nginx_base_url}/callback",
            verify=False,
        )
    except requests.exceptions.ConnectionError:
        pytest.skip("Nginx not running")

    # FastAPI should return 422 Unprocessable Entity (missing query params)
    assert response.status_code == 422


def test_nginx_routes_logout_to_auth_service(nginx_base_url):
    """Test /logout routes to auth_service."""
    # Send logout without session cookie
    # Should redirect to Auth0 logout (or return error)

    if not _is_nginx_oauth2_available(nginx_base_url):
        pytest.skip("Nginx OAuth2 profile not running (requires docker-compose --profile oauth2)")

    try:
        response = requests.post(
            f"{nginx_base_url}/logout",
            allow_redirects=False,
            verify=False,
        )
    except requests.exceptions.ConnectionError:
        pytest.skip("Nginx not running")

    # Should return 401 Unauthorized (no session cookie)
    # or 302 redirect to Auth0 logout
    assert response.status_code in [302, 401]


def test_nginx_csp_report_rate_limiting(nginx_base_url):
    """Test /csp-report endpoint has rate limiting (Codex Review Issue #5)."""
    if not _is_nginx_oauth2_available(nginx_base_url):
        pytest.skip("Nginx OAuth2 profile not running (requires docker-compose --profile oauth2)")

    # Send 15 CSP reports rapidly (limit is 10/min)
    responses = []
    try:
        for _i in range(15):
            response = requests.post(
                f"{nginx_base_url}/csp-report",
                json={
                    "csp-report": {
                        "document-uri": "https://localhost/test",
                        "violated-directive": "script-src",
                        "effective-directive": "script-src",
                        "original-policy": "default-src 'self'",
                        "blocked-uri": "https://evil.com/script.js",
                        "status-code": 200,
                    }
                },
                verify=False,
            )
            responses.append(response)
    except requests.exceptions.ConnectionError:
        pytest.skip("Nginx not running")

    # At least one request should be rate limited (429)
    status_codes = [r.status_code for r in responses]
    assert 429 in status_codes, "CSP report endpoint should enforce rate limiting"


def test_nginx_csp_report_payload_size_limit(nginx_base_url):
    """Test /csp-report rejects large payloads (Codex Review Issue #5)."""
    if not _is_nginx_oauth2_available(nginx_base_url):
        pytest.skip("Nginx OAuth2 profile not running (requires docker-compose --profile oauth2)")

    # Send CSP report with 20KB payload (limit is 10KB)
    large_payload = {
        "csp-report": {
            "document-uri": "https://localhost/test",
            "violated-directive": "script-src",
            "effective-directive": "script-src",
            "original-policy": "default-src 'self'",
            "blocked-uri": "https://evil.com/script.js",
            "status-code": 200,
            "sample": "x" * 20000,  # 20KB sample (exceeds 10KB limit)
        }
    }

    try:
        response = requests.post(
            f"{nginx_base_url}/csp-report",
            json=large_payload,
            verify=False,
        )
    except requests.exceptions.ConnectionError:
        pytest.skip("Nginx not running")

    # Should return 413 Request Entity Too Large
    assert response.status_code == 413


def test_nginx_blocks_forged_x_forwarded_for(nginx_base_url):
    """Test Nginx ignores X-Forwarded-For from untrusted IPs.

    Addresses Codex Iteration 3 Issue #2: Verify actual IP blocking.

    NOTE: This test simulates an attacker sending forged X-Forwarded-For from untrusted IP.
    Nginx should ignore forged header and use actual client IP.
    """
    if not _is_nginx_oauth2_available(nginx_base_url):
        pytest.skip("Nginx OAuth2 profile not running (requires docker-compose --profile oauth2)")

    # Send request with forged X-Forwarded-For header
    # Nginx should ignore this because request comes from untrusted IP
    try:
        response = requests.get(
            f"{nginx_base_url}/test/echo-ip",
            headers={
                "X-Forwarded-For": "10.0.0.1",  # Forged header
            },
            verify=False,
        )
    except requests.exceptions.ConnectionError:
        pytest.skip("Nginx not running")

    assert response.status_code == 200
    data = response.json()

    # Verify Nginx ignored forged header
    # Client IP should NOT be the forged IP (10.0.0.1)
    assert data["client_ip"] != "10.0.0.1", "Nginx should ignore forged X-Forwarded-For"

    # Client IP should be real IP (127.x, 172.x, or ::1)
    # Docker network assigns IPs in 172.x range, localhost is 127.x or ::1
    assert (
        data["client_ip"].startswith("127.")
        or data["client_ip"].startswith("172.")
        or data["client_ip"] == "::1"
    ), f"Expected real IP, got: {data['client_ip']}"
