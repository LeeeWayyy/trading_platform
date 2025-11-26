"""Integration tests for Streamlit CSP coverage.

Addresses Codex Review Issue #2: CSP must cover Streamlit pages.
"""

import pytest
import requests


@pytest.fixture
def nginx_base_url():
    """Base URL for Nginx reverse proxy."""
    return "https://localhost:443"


def test_streamlit_homepage_has_csp_header(nginx_base_url):
    """Test Streamlit homepage includes CSP header."""
    try:
        response = requests.get(
            f"{nginx_base_url}/",
            verify=False,
        )
    except requests.exceptions.ConnectionError:
        pytest.skip("Nginx not running")

    assert response.status_code == 200
    assert "Content-Security-Policy" in response.headers

    csp_header = response.headers["Content-Security-Policy"]

    # Verify CSP includes Streamlit requirements
    assert "wss:" in csp_header  # WebSocket support
    assert "blob:" in csp_header  # Blob URLs
    assert "data:" in csp_header  # Data URLs
    assert "report-uri /csp-report" in csp_header


def test_streamlit_websocket_has_csp_header(nginx_base_url):
    """Test Streamlit WebSocket endpoint includes CSP header."""
    # Note: WebSocket upgrade requires special handling
    # This test verifies CSP header is present in initial HTTP response

    try:
        response = requests.get(
            f"{nginx_base_url}/_stcore/stream",
            verify=False,
            headers={
                "Upgrade": "websocket",
                "Connection": "Upgrade",
            },
        )
    except requests.exceptions.ConnectionError:
        pytest.skip("Nginx not running")

    # WebSocket upgrade may return 101 or 426 (browser requirement)
    # CSP header should be present regardless
    assert "Content-Security-Policy" in response.headers


def test_streamlit_csp_includes_cdn_allowlist(nginx_base_url):
    """Test Streamlit CSP includes required CDN domains."""
    try:
        response = requests.get(
            f"{nginx_base_url}/",
            verify=False,
        )
    except requests.exceptions.ConnectionError:
        pytest.skip("Nginx not running")

    csp_header = response.headers.get("Content-Security-Policy", "")

    # Streamlit dependencies
    assert "cdn.jsdelivr.net" in csp_header  # Streamlit assets
    assert "cdn.plot.ly" in csp_header  # Plotly charts
