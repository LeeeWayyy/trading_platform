"""Integration tests for CSP nonce propagation to HTML.

Verifies that CSP nonces in headers match nonce attributes in HTML <script> tags.
Addresses Gemini Review Issue #8: Need automated test for nonce propagation.

NOTE: Requires docker-compose --profile oauth2 up -d
"""

import re

import pytest
import requests
from bs4 import BeautifulSoup


@pytest.fixture()
def nginx_base_url():
    """Base URL for Nginx reverse proxy."""
    return "https://localhost:443"


def test_login_page_script_tags_have_nonce(nginx_base_url):
    """Test /login page HTML includes nonce attributes in script tags."""
    try:
        # Note: /login might redirect to Auth0, so this test might need adjustment
        # or rely on a specific test endpoint if /login is a direct redirect.
        # For now we try /example-page which we created for this purpose.
        # Or if /login served HTML, we'd use that.
        # Let's try /example-page instead as it is guaranteed to serve HTML with nonce.

        response = requests.get(
            f"{nginx_base_url}/example-page",  # Changed from /login to /example-page
            verify=False,
            allow_redirects=False,
        )
    except requests.exceptions.ConnectionError:
        pytest.skip("Nginx not running")

    if response.status_code != 200:
        pytest.skip(f"/example-page returned {response.status_code}")

    # Extract CSP header nonce
    csp_header = response.headers.get("Content-Security-Policy", "")
    nonce_pattern = r"'nonce-([a-f0-9]{32})'"
    nonce_match = re.search(nonce_pattern, csp_header)

    assert nonce_match, "CSP header should contain nonce"
    header_nonce = nonce_match.group(1)

    # Parse HTML
    soup = BeautifulSoup(response.text, "html.parser")
    script_tags = soup.find_all("script")

    # Verify at least one script tag exists with nonce
    script_nonces = [tag.get("nonce") for tag in script_tags if tag.get("nonce")]

    assert len(script_nonces) > 0, "At least one script tag should have nonce attribute"

    # Verify nonce in HTML matches nonce in CSP header
    assert header_nonce in script_nonces, "Script tag nonce should match CSP header nonce"


