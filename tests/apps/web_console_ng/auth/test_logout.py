from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.web_console_ng.auth.cookie_config import CookieConfig
from apps.web_console_ng.auth.logout import perform_logout


@pytest.mark.asyncio()
async def test_logout_clears_session() -> None:
    # Mock the app object imported in logout.py
    mock_app = MagicMock()
    # Mock storage.user as a dict-like object that also has a clear method
    mock_user_storage = MagicMock()
    mock_user_storage.get.side_effect = lambda k: {"session_id": "sess_123", "user_id": "user_abc", "auth_method": "dev"}.get(k)
    # Allow dict-style access for other potential uses (though get() is used in implementation)

    mock_app.storage.user = mock_user_storage
    cookie_cfg = CookieConfig.from_env()
    request = MagicMock()
    request.cookies = {cookie_cfg.get_cookie_name(): "cookie_value"}
    request.headers = {"user-agent": "pytest"}
    request.client = MagicMock()
    request.client.host = "10.0.0.1"
    response = MagicMock()
    request.state.response = response

    with patch("apps.web_console_ng.auth.logout.app", mock_app):
        with patch("apps.web_console_ng.auth.logout.get_session_store") as mock_get_store, \
             patch("redis.asyncio.from_url") as mock_redis_from_url:

            # Mock session store
            mock_store = AsyncMock()
            mock_get_store.return_value = mock_store
            mock_store.validate_session.return_value = {
                "user": {"user_id": "user_abc", "auth_method": "dev"}
            }
            mock_store.verify_cookie.return_value = "sess_123"

            # Mock redis for streamlit
            mock_redis_client = AsyncMock()
            mock_redis_from_url.return_value = mock_redis_client

            url = await perform_logout(request=request, response=response)

            # Verify NiceGUI invalidation
            mock_store.invalidate_session.assert_called_once_with("sess_123")

            # Verify Streamlit invalidation
            mock_redis_client.delete.assert_called_once_with("st_session:user_abc")

            # Verify storage cleared
            mock_app.storage.user.clear.assert_called_once()
            response.delete_cookie.assert_any_call(
                key=cookie_cfg.get_cookie_name(),
                path=cookie_cfg.get_cookie_flags().get("path", "/"),
                domain=cookie_cfg.get_cookie_flags().get("domain"),
            )
            response.delete_cookie.assert_any_call(
                key="ng_csrf",
                path=cookie_cfg.get_csrf_flags().get("path", "/"),
                domain=cookie_cfg.get_csrf_flags().get("domain"),
            )
            assert url is None


@pytest.mark.asyncio()
async def test_logout_oauth2_redirect() -> None:
    # Mock the app object imported in logout.py
    mock_app = MagicMock()
    mock_user_storage = MagicMock()
    mock_user_storage.get.side_effect = lambda k: {
        "session_id": "sess_456",
        "auth_method": "oauth2",
        "id_token": "id_token_xyz"
    }.get(k)

    mock_app.storage.user = mock_user_storage
    cookie_cfg = CookieConfig.from_env()
    request = MagicMock()
    request.cookies = {cookie_cfg.get_cookie_name(): "cookie_value"}
    request.headers = {"user-agent": "pytest"}
    request.client = MagicMock()
    request.client.host = "10.0.0.1"
    response = MagicMock()
    request.state.response = response

    with patch("apps.web_console_ng.auth.logout.app", mock_app):
        with patch("apps.web_console_ng.auth.logout.get_session_store") as mock_get_store, \
             patch("apps.web_console_ng.auth.logout.OAuth2AuthHandler") as MockHandler:

            mock_store = AsyncMock()
            mock_get_store.return_value = mock_store
            mock_store.validate_session.return_value = {
                "user": {
                    "user_id": "user_abc",
                    "auth_method": "oauth2",
                    "id_token": "id_token_xyz",
                }
            }
            mock_store.verify_cookie.return_value = "sess_456"

            mock_handler_instance = AsyncMock()
            MockHandler.return_value = mock_handler_instance
            mock_handler_instance.get_logout_url.return_value = "https://auth0.com/logout"

            url = await perform_logout(request=request, response=response)

            assert url == "https://auth0.com/logout"
            mock_app.storage.user.clear.assert_called_once()
            response.delete_cookie.assert_any_call(
                key=cookie_cfg.get_cookie_name(),
                path=cookie_cfg.get_cookie_flags().get("path", "/"),
                domain=cookie_cfg.get_cookie_flags().get("domain"),
            )
            response.delete_cookie.assert_any_call(
                key="ng_csrf",
                path=cookie_cfg.get_csrf_flags().get("path", "/"),
                domain=cookie_cfg.get_csrf_flags().get("domain"),
            )
