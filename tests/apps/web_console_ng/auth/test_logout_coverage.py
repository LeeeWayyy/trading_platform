"""Comprehensive test coverage for apps/web_console_ng/auth/logout.py

Target: 85%+ branch coverage for HIGH RISK authentication module.

Coverage areas:
- logout_post endpoint: successful logout, session errors, CSRF validation
- perform_logout: OAuth2 redirects, Redis failures, error handling
- _clear_auth_cookies: cookie clearing logic
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from apps.web_console_ng.auth.cookie_config import CookieConfig
from apps.web_console_ng.auth.csrf import CSRF_HEADER_NAME
from apps.web_console_ng.auth.logout import _clear_auth_cookies, logout_post, perform_logout
from apps.web_console_ng.auth.session_store import SessionValidationError

# ============================================================================
# Helper Functions
# ============================================================================


def _create_mock_request(
    cookie_value: str | None = "valid_cookie",
    csrf_header: str | None = "valid_csrf",
    client_ip: str = "10.0.0.1",
    user_agent: str = "pytest-agent",
) -> MagicMock:
    """Create a mock FastAPI request object."""
    request = MagicMock()
    cookie_cfg = CookieConfig.from_env()
    request.cookies = {cookie_cfg.get_cookie_name(): cookie_value} if cookie_value else {}
    request.headers = {
        "user-agent": user_agent,
        **(
            {CSRF_HEADER_NAME: csrf_header}
            if csrf_header
            else {}
        ),
    }
    request.client = MagicMock()
    request.client.host = client_ip
    return request


def _create_mock_session_store(
    session_data: dict | None = None,
    session_id: str = "sess_123",
    validate_error: Exception | None = None,
    invalidate_error: Exception | None = None,
) -> MagicMock:
    """Create a mock session store."""
    mock_store = MagicMock()

    if validate_error:
        mock_store.validate_session = AsyncMock(side_effect=validate_error)
    else:
        mock_store.validate_session = AsyncMock(return_value=session_data)

    if invalidate_error:
        mock_store.invalidate_session = AsyncMock(side_effect=invalidate_error)
    else:
        mock_store.invalidate_session = AsyncMock()

    mock_store.verify_cookie.return_value = session_id
    return mock_store


# ============================================================================
# Tests for logout_post endpoint
# ============================================================================


@pytest.mark.asyncio()
async def test_logout_post_successful() -> None:
    """Test successful logout via POST with valid CSRF token."""
    request = _create_mock_request()
    session_data = {
        "user": {"user_id": "user_123", "auth_method": "dev"},
        "csrf_token": "valid_csrf",
    }
    mock_store = _create_mock_session_store(session_data=session_data)

    with (
        patch("apps.web_console_ng.auth.logout.get_session_store", return_value=mock_store),
        patch("apps.web_console_ng.auth.logout.perform_logout") as mock_perform,
    ):
        mock_perform.return_value = None

        response = await logout_post(request)

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["logout_url"] is None
        mock_store.validate_session.assert_called_once()
        mock_perform.assert_called_once()


@pytest.mark.asyncio()
async def test_logout_post_missing_session_cookie() -> None:
    """Test logout POST with missing session cookie returns 401."""
    request = _create_mock_request(cookie_value=None)

    with pytest.raises(HTTPException) as exc_info:
        await logout_post(request)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "session_missing"


@pytest.mark.asyncio()
async def test_logout_post_redis_unavailable_during_validation() -> None:
    """Test logout POST when Redis is unavailable returns 503 with Retry-After."""
    request = _create_mock_request()
    mock_store = _create_mock_session_store(validate_error=SessionValidationError("Redis down"))

    with patch("apps.web_console_ng.auth.logout.get_session_store", return_value=mock_store):
        response = await logout_post(request)

        assert response.status_code == 503
        assert response.headers["Retry-After"] == "5"
        body = json.loads(response.body)
        assert body["error"] == "Service temporarily unavailable"


@pytest.mark.asyncio()
async def test_logout_post_invalid_session() -> None:
    """Test logout POST with invalid session returns 401."""
    request = _create_mock_request()
    mock_store = _create_mock_session_store(session_data=None)

    with patch("apps.web_console_ng.auth.logout.get_session_store", return_value=mock_store):
        with pytest.raises(HTTPException) as exc_info:
            await logout_post(request)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "session_invalid"


@pytest.mark.asyncio()
async def test_logout_post_missing_csrf_header() -> None:
    """Test logout POST with missing CSRF header returns 403."""
    request = _create_mock_request(csrf_header=None)
    session_data = {
        "user": {"user_id": "user_123"},
        "csrf_token": "valid_csrf",
    }
    mock_store = _create_mock_session_store(session_data=session_data)

    with patch("apps.web_console_ng.auth.logout.get_session_store", return_value=mock_store):
        response = await logout_post(request)

        assert response.status_code == 403
        body = json.loads(response.body)
        assert body["error"] == "csrf_invalid"
        # Session should be invalidated on CSRF failure
        mock_store.invalidate_session.assert_called_once_with("sess_123")


@pytest.mark.asyncio()
async def test_logout_post_invalid_csrf_token() -> None:
    """Test logout POST with invalid CSRF token returns 403 and invalidates session."""
    request = _create_mock_request(csrf_header="wrong_csrf")
    session_data = {
        "user": {"user_id": "user_123"},
        "csrf_token": "valid_csrf",
    }
    mock_store = _create_mock_session_store(session_data=session_data)

    with patch("apps.web_console_ng.auth.logout.get_session_store", return_value=mock_store):
        response = await logout_post(request)

        assert response.status_code == 403
        body = json.loads(response.body)
        assert body["error"] == "csrf_invalid"
        # Session should be invalidated on CSRF failure
        mock_store.invalidate_session.assert_called_once_with("sess_123")


@pytest.mark.asyncio()
async def test_logout_post_csrf_failure_session_invalidation_fails() -> None:
    """Test logout POST CSRF failure when session invalidation also fails."""
    request = _create_mock_request(csrf_header="wrong_csrf")
    session_data = {
        "user": {"user_id": "user_123"},
        "csrf_token": "valid_csrf",
    }
    # Session invalidation will raise an error
    mock_store = _create_mock_session_store(
        session_data=session_data,
        invalidate_error=Exception("Redis error"),
    )

    with (
        patch("apps.web_console_ng.auth.logout.get_session_store", return_value=mock_store),
        patch("apps.web_console_ng.auth.logout.logger") as mock_logger,
    ):
        response = await logout_post(request)

        assert response.status_code == 403
        body = json.loads(response.body)
        assert body["error"] == "csrf_invalid"
        # Should log the invalidation failure
        mock_logger.warning.assert_called_once()
        assert "Failed to invalidate session on CSRF failure" in str(
            mock_logger.warning.call_args
        )


@pytest.mark.asyncio()
async def test_logout_post_missing_csrf_token_in_session() -> None:
    """Test logout POST when session doesn't have csrf_token returns 403."""
    request = _create_mock_request(csrf_header="some_csrf")
    session_data = {
        "user": {"user_id": "user_123"},
        # No csrf_token in session
    }
    mock_store = _create_mock_session_store(session_data=session_data)

    with patch("apps.web_console_ng.auth.logout.get_session_store", return_value=mock_store):
        response = await logout_post(request)

        assert response.status_code == 403
        body = json.loads(response.body)
        assert body["error"] == "csrf_invalid"


@pytest.mark.asyncio()
async def test_logout_post_with_oauth2_logout_url() -> None:
    """Test logout POST returns OAuth2 logout URL when applicable."""
    request = _create_mock_request()
    session_data = {
        "user": {
            "user_id": "user_123",
            "auth_method": "oauth2",
            "id_token": "id_token_xyz",
        },
        "csrf_token": "valid_csrf",
    }
    mock_store = _create_mock_session_store(session_data=session_data)

    with (
        patch("apps.web_console_ng.auth.logout.get_session_store", return_value=mock_store),
        patch("apps.web_console_ng.auth.logout.perform_logout") as mock_perform,
    ):
        mock_perform.return_value = "https://auth0.com/logout?returnTo=..."

        response = await logout_post(request)

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["logout_url"] == "https://auth0.com/logout?returnTo=..."


# ============================================================================
# Tests for perform_logout function
# ============================================================================


@pytest.mark.asyncio()
async def test_perform_logout_successful() -> None:
    """Test perform_logout successfully clears session and cookies."""
    mock_app = MagicMock()
    mock_user_storage = MagicMock()
    mock_app.storage.user = mock_user_storage

    cookie_cfg = CookieConfig.from_env()
    request = MagicMock()
    request.cookies = {cookie_cfg.get_cookie_name(): "cookie_value"}
    request.headers = {"user-agent": "pytest"}
    request.client = MagicMock()
    request.client.host = "10.0.0.1"
    request.state.response = MagicMock()
    response = MagicMock()

    session_data = {"user": {"user_id": "user_123", "auth_method": "dev"}}
    mock_store = _create_mock_session_store(session_data=session_data)

    with (
        patch("apps.web_console_ng.auth.logout.app", mock_app),
        patch("apps.web_console_ng.auth.logout.get_session_store", return_value=mock_store),
    ):
        url = await perform_logout(request=request, response=response)

        assert url is None
        mock_app.storage.user.clear.assert_called_once()
        mock_store.invalidate_session.assert_called_once_with("sess_123")
        # Verify cookies are deleted
        assert response.delete_cookie.call_count == 2


@pytest.mark.asyncio()
async def test_perform_logout_no_cookie() -> None:
    """Test perform_logout when no session cookie exists."""
    mock_app = MagicMock()
    mock_user_storage = MagicMock()
    mock_app.storage.user = mock_user_storage

    request = MagicMock()
    request.cookies = {}
    response = MagicMock()

    with patch("apps.web_console_ng.auth.logout.app", mock_app):
        url = await perform_logout(request=request, response=response)

        assert url is None
        mock_app.storage.user.clear.assert_called_once()


@pytest.mark.asyncio()
async def test_perform_logout_no_request_object() -> None:
    """Test perform_logout when no request object is available."""
    mock_app = MagicMock()
    mock_user_storage = MagicMock()
    mock_app.storage.user = mock_user_storage
    mock_app.storage.request = None

    with (
        patch("apps.web_console_ng.auth.logout.app", mock_app),
        patch("apps.web_console_ng.auth.logout.logger") as mock_logger,
    ):
        url = await perform_logout()

        assert url is None
        mock_app.storage.user.clear.assert_called_once()
        mock_logger.warning.assert_called_once()
        assert "No request object available for logout" in str(
            mock_logger.warning.call_args
        )


@pytest.mark.asyncio()
async def test_perform_logout_no_response_object() -> None:
    """Test perform_logout when request.state has no response."""
    mock_app = MagicMock()
    mock_user_storage = MagicMock()
    mock_app.storage.user = mock_user_storage

    cookie_cfg = CookieConfig.from_env()
    request = MagicMock()
    request.cookies = {cookie_cfg.get_cookie_name(): "cookie_value"}
    request.headers = {"user-agent": "pytest"}
    request.client = MagicMock()
    request.client.host = "10.0.0.1"
    # No state.response available
    request.state = MagicMock()
    request.state.response = None

    session_data = {"user": {"user_id": "user_123", "auth_method": "dev"}}
    mock_store = _create_mock_session_store(session_data=session_data)

    with (
        patch("apps.web_console_ng.auth.logout.app", mock_app),
        patch("apps.web_console_ng.auth.logout.get_session_store", return_value=mock_store),
    ):
        # Should not crash when response is None
        url = await perform_logout(request=request, response=None)

        assert url is None
        mock_app.storage.user.clear.assert_called_once()
        mock_store.invalidate_session.assert_called_once()


@pytest.mark.asyncio()
async def test_perform_logout_redis_unavailable_during_validation() -> None:
    """Test perform_logout proceeds with local cleanup when Redis unavailable."""
    mock_app = MagicMock()
    mock_user_storage = MagicMock()
    mock_app.storage.user = mock_user_storage

    cookie_cfg = CookieConfig.from_env()
    request = MagicMock()
    request.cookies = {cookie_cfg.get_cookie_name(): "cookie_value"}
    request.headers = {"user-agent": "pytest"}
    request.client = MagicMock()
    request.client.host = "10.0.0.1"
    response = MagicMock()

    mock_store = _create_mock_session_store(
        validate_error=SessionValidationError("Redis down")
    )

    with (
        patch("apps.web_console_ng.auth.logout.app", mock_app),
        patch("apps.web_console_ng.auth.logout.get_session_store", return_value=mock_store),
        patch("apps.web_console_ng.auth.logout.logger") as mock_logger,
    ):
        url = await perform_logout(request=request, response=response)

        assert url is None
        # Should still clear local storage
        mock_app.storage.user.clear.assert_called_once()
        # Should log warning
        mock_logger.warning.assert_called()
        assert "Redis unavailable during logout" in str(mock_logger.warning.call_args)


@pytest.mark.asyncio()
async def test_perform_logout_session_invalidation_fails() -> None:
    """Test perform_logout continues when session invalidation fails."""
    mock_app = MagicMock()
    mock_user_storage = MagicMock()
    mock_app.storage.user = mock_user_storage

    cookie_cfg = CookieConfig.from_env()
    request = MagicMock()
    request.cookies = {cookie_cfg.get_cookie_name(): "cookie_value"}
    request.headers = {"user-agent": "pytest"}
    request.client = MagicMock()
    request.client.host = "10.0.0.1"
    response = MagicMock()

    session_data = {"user": {"user_id": "user_123", "auth_method": "dev"}}
    mock_store = _create_mock_session_store(
        session_data=session_data,
        invalidate_error=Exception("Redis error"),
    )

    with (
        patch("apps.web_console_ng.auth.logout.app", mock_app),
        patch("apps.web_console_ng.auth.logout.get_session_store", return_value=mock_store),
        patch("apps.web_console_ng.auth.logout.logger") as mock_logger,
    ):
        url = await perform_logout(request=request, response=response)

        assert url is None
        # Should still clear local storage
        mock_app.storage.user.clear.assert_called_once()
        # Should log warning
        mock_logger.warning.assert_called()
        assert "Failed to invalidate session in Redis" in str(
            mock_logger.warning.call_args
        )


@pytest.mark.asyncio()
async def test_perform_logout_invalid_session_still_clears_storage() -> None:
    """Test perform_logout clears storage even when session is invalid."""
    mock_app = MagicMock()
    mock_user_storage = MagicMock()
    mock_app.storage.user = mock_user_storage

    cookie_cfg = CookieConfig.from_env()
    request = MagicMock()
    request.cookies = {cookie_cfg.get_cookie_name(): "cookie_value"}
    request.headers = {"user-agent": "pytest"}
    request.client = MagicMock()
    request.client.host = "10.0.0.1"
    response = MagicMock()

    # Session validation returns None (invalid)
    mock_store = _create_mock_session_store(session_data=None)

    with (
        patch("apps.web_console_ng.auth.logout.app", mock_app),
        patch("apps.web_console_ng.auth.logout.get_session_store", return_value=mock_store),
    ):
        url = await perform_logout(request=request, response=response)

        assert url is None
        # Should still clear local storage
        mock_app.storage.user.clear.assert_called_once()


@pytest.mark.asyncio()
async def test_perform_logout_oauth2_returns_logout_url() -> None:
    """Test perform_logout returns OAuth2 logout URL for OAuth2 sessions."""
    mock_app = MagicMock()
    mock_user_storage = MagicMock()
    mock_app.storage.user = mock_user_storage

    cookie_cfg = CookieConfig.from_env()
    request = MagicMock()
    request.cookies = {cookie_cfg.get_cookie_name(): "cookie_value"}
    request.headers = {"user-agent": "pytest"}
    request.client = MagicMock()
    request.client.host = "10.0.0.1"
    response = MagicMock()

    session_data = {
        "user": {
            "user_id": "user_123",
            "auth_method": "oauth2",
            "id_token": "id_token_xyz",
        }
    }
    mock_store = _create_mock_session_store(session_data=session_data)

    with (
        patch("apps.web_console_ng.auth.logout.app", mock_app),
        patch("apps.web_console_ng.auth.logout.get_session_store", return_value=mock_store),
        patch("apps.web_console_ng.auth.logout.OAuth2AuthHandler") as MockHandler,
    ):
        mock_handler = AsyncMock()
        MockHandler.return_value = mock_handler
        mock_handler.get_logout_url.return_value = "https://auth0.com/logout"

        url = await perform_logout(request=request, response=response)

        assert url == "https://auth0.com/logout"
        mock_app.storage.user.clear.assert_called_once()
        mock_handler.get_logout_url.assert_called_once_with("id_token_xyz")


@pytest.mark.asyncio()
async def test_perform_logout_oauth2_without_id_token() -> None:
    """Test perform_logout with OAuth2 but no id_token."""
    mock_app = MagicMock()
    mock_user_storage = MagicMock()
    mock_app.storage.user = mock_user_storage

    cookie_cfg = CookieConfig.from_env()
    request = MagicMock()
    request.cookies = {cookie_cfg.get_cookie_name(): "cookie_value"}
    request.headers = {"user-agent": "pytest"}
    request.client = MagicMock()
    request.client.host = "10.0.0.1"
    response = MagicMock()

    session_data = {
        "user": {
            "user_id": "user_123",
            "auth_method": "oauth2",
            # No id_token
        }
    }
    mock_store = _create_mock_session_store(session_data=session_data)

    with (
        patch("apps.web_console_ng.auth.logout.app", mock_app),
        patch("apps.web_console_ng.auth.logout.get_session_store", return_value=mock_store),
    ):
        url = await perform_logout(request=request, response=response)

        # Should not return logout URL without id_token
        assert url is None
        mock_app.storage.user.clear.assert_called_once()


@pytest.mark.asyncio()
async def test_perform_logout_exception_still_clears_storage() -> None:
    """Test perform_logout clears storage even when exception occurs."""
    mock_app = MagicMock()
    mock_user_storage = MagicMock()
    mock_app.storage.user = mock_user_storage

    cookie_cfg = CookieConfig.from_env()
    request = MagicMock()
    request.cookies = {cookie_cfg.get_cookie_name(): "cookie_value"}
    request.headers = {"user-agent": "pytest"}
    request.client = MagicMock()
    request.client.host = "10.0.0.1"

    with (
        patch("apps.web_console_ng.auth.logout.app", mock_app),
        patch(
            "apps.web_console_ng.auth.logout.get_session_store",
            side_effect=Exception("Unexpected error"),
        ),
        patch("apps.web_console_ng.auth.logout.logger") as mock_logger,
    ):
        url = await perform_logout(request=request, response=None)

        assert url is None
        # Should still clear storage
        mock_app.storage.user.clear.assert_called_once()
        # Should log error
        mock_logger.error.assert_called_once()
        assert "Logout error" in str(mock_logger.error.call_args)


@pytest.mark.asyncio()
async def test_perform_logout_storage_clear_fails() -> None:
    """Test perform_logout handles storage clear failure gracefully."""
    mock_app = MagicMock()
    mock_user_storage = MagicMock()
    mock_user_storage.clear.side_effect = Exception("Storage error")
    mock_app.storage.user = mock_user_storage

    with (
        patch("apps.web_console_ng.auth.logout.app", mock_app),
        patch(
            "apps.web_console_ng.auth.logout.get_session_store",
            side_effect=Exception("Trigger exception path"),
        ),
        patch("apps.web_console_ng.auth.logout.logger") as mock_logger,
    ):
        url = await perform_logout()

        assert url is None
        # Should log debug message about storage clear failure
        assert mock_logger.debug.call_count >= 1
        debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
        assert any("Failed to clear user storage" in call for call in debug_calls)


# ============================================================================
# Tests for _clear_auth_cookies helper
# ============================================================================


def test_clear_auth_cookies() -> None:
    """Test _clear_auth_cookies deletes both session and CSRF cookies."""
    response = MagicMock()
    cookie_cfg = CookieConfig.from_env()

    _clear_auth_cookies(response, cookie_cfg)

    # Should delete session cookie
    session_flags = cookie_cfg.get_cookie_flags()
    response.delete_cookie.assert_any_call(
        key=cookie_cfg.get_cookie_name(),
        path=session_flags.get("path", "/"),
        domain=session_flags.get("domain"),
    )

    # Should delete CSRF cookie
    csrf_flags = cookie_cfg.get_csrf_flags()
    response.delete_cookie.assert_any_call(
        key="ng_csrf",
        path=csrf_flags.get("path", "/"),
        domain=csrf_flags.get("domain"),
    )

    assert response.delete_cookie.call_count == 2


def test_clear_auth_cookies_with_custom_domain() -> None:
    """Test _clear_auth_cookies with custom cookie domain."""
    response = MagicMock()

    # Mock cookie config with custom domain
    with patch.object(CookieConfig, "from_env") as mock_from_env:
        mock_cfg = MagicMock()
        mock_cfg.get_cookie_name.return_value = "ng_session"
        mock_cfg.get_cookie_flags.return_value = {"path": "/app", "domain": ".example.com"}
        mock_cfg.get_csrf_flags.return_value = {"path": "/app", "domain": ".example.com"}
        mock_from_env.return_value = mock_cfg

        cookie_cfg = CookieConfig.from_env()
        _clear_auth_cookies(response, cookie_cfg)

        # Verify domain is passed through
        response.delete_cookie.assert_any_call(
            key="ng_session",
            path="/app",
            domain=".example.com",
        )
        response.delete_cookie.assert_any_call(
            key="ng_csrf",
            path="/app",
            domain=".example.com",
        )
