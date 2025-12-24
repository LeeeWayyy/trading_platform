"""Integration tests for FastAPI auth service.

Tests the complete API routes including dependency injection and OAuth2 flow.
"""

import base64
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Generate valid base64-encoded 32-byte key
test_key = base64.b64encode(b"0" * 32).decode()

# Set environment variables before importing main
os.environ["AUTH0_DOMAIN"] = "test.auth0.com"
os.environ["AUTH0_CLIENT_ID"] = "test_client_id"
os.environ["AUTH0_CLIENT_SECRET"] = "test_client_secret"
os.environ["AUTH0_AUDIENCE"] = "https://api.test.local"
os.environ["OAUTH2_REDIRECT_URI"] = "https://test.local/callback"
os.environ["OAUTH2_LOGOUT_REDIRECT_URI"] = "https://test.local"
os.environ["COOKIE_DOMAIN"] = ".test.local"
os.environ["SESSION_ENCRYPTION_KEY"] = test_key
os.environ["REDIS_HOST"] = "localhost"  # Not used, but prevents config errors
os.environ["REDIS_PORT"] = "6379"
os.environ["TRUSTED_PROXY_IPS"] = "127.0.0.1"
os.environ["INTERNAL_REFRESH_SECRET"] = "test-internal-secret"

from apps.auth_service.main import app


@pytest.fixture()
def client():
    """Create test client for FastAPI app."""
    # Mock Redis to avoid connection errors
    with patch("apps.auth_service.dependencies.get_redis_client") as mock_redis:
        mock_redis_instance = AsyncMock()
        # Fix: delete should return integer (number of keys deleted)
        mock_redis_instance.delete.return_value = 1
        mock_redis.return_value = mock_redis_instance
        yield TestClient(app)


@pytest.fixture()
def mock_oauth2_handler():
    """Mock OAuth2FlowHandler with all methods."""
    # Patch where it is USED in routes/main
    with (
        patch("apps.auth_service.main.get_oauth2_handler") as mock_main,
        patch("apps.auth_service.routes.callback.get_oauth2_handler") as mock_callback,
        patch("apps.auth_service.routes.refresh.get_oauth2_handler") as mock_refresh,
        patch("apps.auth_service.routes.logout.get_oauth2_handler") as mock_logout,
    ):
        mock_handler = MagicMock()

        # Mock initiate_login
        mock_state = MagicMock()
        mock_state.state = "test_state_123"
        mock_handler.initiate_login = AsyncMock(
            return_value=("https://auth0.com/authorize?state=test_state_123", mock_state)
        )

        # Mock handle_callback
        mock_session_data = MagicMock()
        mock_session_data.user_id = "auth0|123"
        mock_session_data.email = "test@example.com"
        mock_handler.handle_callback = AsyncMock(return_value=("session_id_123", mock_session_data))

        # Mock refresh_tokens
        mock_handler.refresh_tokens = AsyncMock(return_value=mock_session_data)

        # Mock handle_logout
        mock_handler.handle_logout = AsyncMock(return_value="https://auth0.com/logout")

        # Configure all patches to return the same handler
        mock_main.return_value = mock_handler
        mock_callback.return_value = mock_handler
        mock_refresh.return_value = mock_handler
        mock_logout.return_value = mock_handler

        yield mock_handler


@pytest.fixture()
def mock_rate_limiters():
    """Mock rate limiters."""
    # Patch where it is USED in routes
    with (
        patch("apps.auth_service.routes.callback.get_rate_limiters") as mock_callback_get,
        patch("apps.auth_service.routes.refresh.get_rate_limiters") as mock_refresh_get,
    ):
        mock_callback_limiter = AsyncMock()
        mock_callback_limiter.is_allowed = AsyncMock(return_value=True)

        mock_refresh_limiter = AsyncMock()
        mock_refresh_limiter.is_allowed = AsyncMock(return_value=True)

        limiters = {
            "callback": mock_callback_limiter,
            "refresh": mock_refresh_limiter,
        }

        mock_callback_get.return_value = limiters
        mock_refresh_get.return_value = limiters

        yield limiters


def test_health_endpoint(client):
    """Test /health endpoint returns healthy status."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "auth_service"


def test_login_redirects_to_auth0(client, mock_oauth2_handler):
    """Test /login initiates OAuth2 flow and redirects to Auth0."""
    response = client.get("/login", follow_redirects=False)

    # Should return 302 redirect
    assert response.status_code == 302

    # Should redirect to Auth0 authorization URL
    # Note: The mock returns https://auth0.com/authorize, so we check for that
    assert response.headers["location"].startswith("https://auth0.com/authorize")
    assert "state=test_state_123" in response.headers["location"]

    # Should have called initiate_login
    mock_oauth2_handler.initiate_login.assert_called_once()


def test_callback_success_sets_cookie(client, mock_oauth2_handler, mock_rate_limiters):
    """Test /callback with valid code/state sets HttpOnly session cookie."""
    response = client.get(
        "/callback?code=test_code&state=test_state",
        follow_redirects=False,
        headers={"X-Real-IP": "127.0.0.1", "User-Agent": "test-agent"},
    )

    # Should return 302 redirect to home
    assert response.status_code == 302
    assert response.headers["location"] == "/"

    # Should set HttpOnly session cookie
    set_cookie_header = response.headers["set-cookie"]
    assert "session_id=session_id_123" in set_cookie_header
    assert "HttpOnly" in set_cookie_header
    assert "Secure" in set_cookie_header
    assert "SameSite=lax" in set_cookie_header or "SameSite=Lax" in set_cookie_header

    # Should have called handle_callback
    mock_oauth2_handler.handle_callback.assert_called_once()


def test_callback_rate_limit_exceeded(client, mock_oauth2_handler, mock_rate_limiters):
    """Test /callback rejects when rate limit exceeded."""
    # Mock rate limiter to return False
    mock_rate_limiters["callback"].is_allowed = AsyncMock(return_value=False)

    response = client.get(
        "/callback?code=test_code&state=test_state",
        follow_redirects=False,
        headers={"X-Real-IP": "127.0.0.1", "User-Agent": "test-agent"},
    )

    # Should return 429 Too Many Requests
    assert response.status_code == 429
    data = response.json()
    assert "rate limit" in data["detail"].lower()

    # Should NOT have called handle_callback
    mock_oauth2_handler.handle_callback.assert_not_called()


def test_callback_invalid_state(client, mock_oauth2_handler, mock_rate_limiters):
    """Test /callback with invalid state returns 400."""
    # Mock handle_callback to raise ValueError
    mock_oauth2_handler.handle_callback = AsyncMock(side_effect=ValueError("Invalid state"))

    response = client.get(
        "/callback?code=test_code&state=invalid_state",
        follow_redirects=False,
        headers={"X-Real-IP": "127.0.0.1", "User-Agent": "test-agent"},
    )

    # Should return 400 Bad Request
    assert response.status_code == 400
    data = response.json()
    assert "authentication failed" in data["detail"].lower()


def test_refresh_success(client, mock_oauth2_handler, mock_rate_limiters):
    """Test /refresh with valid session cookie refreshes tokens."""
    response = client.post(
        "/refresh",
        cookies={"session_id": "session_id_123"},
    )

    # Should return 200 with success message
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["message"] == "Tokens refreshed"

    # Should have called refresh_tokens
    mock_oauth2_handler.refresh_tokens.assert_called_once()


def test_refresh_no_cookie(client, mock_oauth2_handler, mock_rate_limiters):
    """Test /refresh without session cookie returns 401."""
    response = client.post("/refresh")

    # Should return 401 Unauthorized
    assert response.status_code == 401
    data = response.json()
    assert "no session cookie" in data["detail"].lower()

    # Should NOT have called refresh_tokens
    mock_oauth2_handler.refresh_tokens.assert_not_called()


def test_refresh_rate_limit_exceeded(client, mock_oauth2_handler, mock_rate_limiters):
    """Test /refresh rejects when rate limit exceeded."""
    # Mock rate limiter to return False
    mock_rate_limiters["refresh"].is_allowed = AsyncMock(return_value=False)

    response = client.post(
        "/refresh",
        cookies={"session_id": "session_id_123"},
    )

    # Should return 429 Too Many Requests
    assert response.status_code == 429
    data = response.json()
    assert "rate limit" in data["detail"].lower()

    # Should NOT have called refresh_tokens
    mock_oauth2_handler.refresh_tokens.assert_not_called()


def test_refresh_internal_bypass_uses_shared_secret(
    client, mock_oauth2_handler, mock_rate_limiters
):
    """Internal callers with shared secret should bypass binding validation."""
    # Patch the module-level constant directly (env var is evaluated at import time)
    with patch("apps.auth_service.routes.refresh.INTERNAL_REFRESH_SECRET", "test-internal-secret"):
        response = client.post(
            "/refresh",
            cookies={"session_id": "session_id_123"},
            headers={"X-Internal-Auth": "test-internal-secret"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"

        mock_oauth2_handler.refresh_tokens.assert_called_with(
            session_id="session_id_123",
            ip_address=None,
            user_agent=None,
            enforce_binding=False,
        )


def test_refresh_internal_invalid_secret_rejected(client, mock_oauth2_handler, mock_rate_limiters):
    """Invalid internal secret should not bypass binding and returns 401."""
    # Patch the module-level constant directly (env var is evaluated at import time)
    with patch("apps.auth_service.routes.refresh.INTERNAL_REFRESH_SECRET", "test-internal-secret"):
        response = client.post(
            "/refresh",
            cookies={"session_id": "session_id_123"},
            headers={"X-Internal-Auth": "wrong-secret"},
        )

        assert response.status_code == 401
        assert "invalid internal auth" in response.json()["detail"].lower()


def test_refresh_invalid_session(client, mock_oauth2_handler, mock_rate_limiters):
    """Test /refresh with invalid session returns 401."""
    # Mock refresh_tokens to raise ValueError
    mock_oauth2_handler.refresh_tokens = AsyncMock(side_effect=ValueError("Session not found"))

    response = client.post(
        "/refresh",
        cookies={"session_id": "invalid_session"},
    )

    # Should return 401 Unauthorized
    assert response.status_code == 401
    data = response.json()
    assert "refresh failed" in data["detail"].lower()


def test_logout_redirects_and_clears_cookie(client, mock_oauth2_handler):
    """Test /logout clears session cookie and redirects to Auth0 logout."""
    response = client.get(
        "/logout",
        cookies={"session_id": "session_id_123"},
        follow_redirects=False,
    )

    # Should return 302 redirect to Auth0 logout
    assert response.status_code == 302
    assert response.headers["location"] == "https://auth0.com/logout"

    # Should clear session cookie (max_age=0)
    set_cookie_header = response.headers["set-cookie"]
    assert "session_id=" in set_cookie_header
    assert "Max-Age=0" in set_cookie_header or "max-age=0" in set_cookie_header

    # Should have called handle_logout with session ID, IP, and User-Agent
    # TestClient provides "testclient" as User-Agent, and IP extraction may fallback to "unknown" in tests
    mock_oauth2_handler.handle_logout.assert_called_once_with(
        "session_id_123",
        current_ip="unknown",
        current_user_agent="testclient",
    )


def test_logout_no_cookie_redirects_to_login(client, mock_oauth2_handler):
    """Test /logout without session cookie redirects to login."""
    response = client.get("/logout", follow_redirects=False)

    # Should return 302 redirect to login
    assert response.status_code == 302
    assert response.headers["location"] == "/login"

    # Should NOT have called handle_logout
    mock_oauth2_handler.handle_logout.assert_not_called()
