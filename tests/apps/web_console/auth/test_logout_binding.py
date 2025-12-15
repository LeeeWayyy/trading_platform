"""Unit tests for logout binding validation (Component 3).

FIX (Codex Medium #5): Tests verify logout validates session binding before
revoking refresh token to prevent attacker with stolen cookie from revoking
real user's token.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

import httpx
import pytest

from apps.web_console.auth.oauth2_flow import OAuth2FlowHandler
from apps.web_console.auth.session_store import SessionData


@pytest.fixture()
def mock_session_store():
    """Create mock session store."""
    return AsyncMock()


@pytest.fixture()
def mock_config():
    """Create mock OAuth2 config."""
    from apps.web_console.auth.oauth2_flow import OAuth2Config

    return OAuth2Config(
        client_id="test_client_id",
        client_secret="test_client_secret",
        auth0_domain="test.auth0.com",
        audience="https://api.trading-platform.local",
        redirect_uri="https://trading-platform.local/callback",
        logout_redirect_uri="https://trading-platform.local/login",
    )


@pytest.fixture()
def oauth2_handler(mock_session_store, mock_config):
    """Create OAuth2FlowHandler instance."""
    # Mock state_store and jwks_validator (not needed for logout tests)
    mock_state_store = AsyncMock()
    mock_jwks_validator = AsyncMock()

    return OAuth2FlowHandler(
        config=mock_config,
        session_store=mock_session_store,
        state_store=mock_state_store,
        jwks_validator=mock_jwks_validator,
    )


@pytest.fixture()
def sample_session_data():
    """Create sample session data."""
    now = datetime.now(UTC)
    return SessionData(
        access_token="test_access_token",
        refresh_token="test_refresh_token",
        id_token="test_id_token",
        user_id="auth0|12345",
        email="test@example.com",
        created_at=now,
        last_activity=now,
        ip_address="192.168.1.100",
        user_agent="Mozilla/5.0 (Test)",
        access_token_expires_at=now + timedelta(hours=1),
    )


class TestLogoutBindingValidation:
    """Test logout validates binding before token revocation (Codex Medium #5 Fix)."""

    @pytest.mark.asyncio()
    async def test_logout_with_valid_binding_revokes_token(
        self, oauth2_handler, mock_session_store, sample_session_data
    ) -> None:
        """Test logout revokes token when binding is valid."""
        # Mock session store returns session (binding valid)
        mock_session_store.get_session.return_value = sample_session_data

        # Mock HTTP client for token revocation
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = AsyncMock()
            mock_response.raise_for_status = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Call handle_logout
            logout_url = await oauth2_handler.handle_logout(
                session_id="test_session",
                current_ip="192.168.1.100",  # Matches sample_session_data
                current_user_agent="Mozilla/5.0 (Test)",  # Matches sample_session_data
            )

            # Verify binding validation called with correct IP/UA
            mock_session_store.get_session.assert_called_once_with(
                "test_session",
                current_ip="192.168.1.100",
                current_user_agent="Mozilla/5.0 (Test)",
                update_activity=False,
            )

            # Verify refresh token revocation called
            mock_client.post.assert_called_once()
            revoke_call = mock_client.post.call_args
            assert revoke_call[0][0] == "https://test.auth0.com/oauth/revoke"
            assert revoke_call[1]["data"]["token"] == "test_refresh_token"
            assert revoke_call[1]["data"]["client_id"] == "test_client_id"

            # Verify session deleted
            mock_session_store.delete_session.assert_called_once_with("test_session")

            # Verify logout URL returned
            expected_params = {
                "client_id": "test_client_id",
                "returnTo": "https://trading-platform.local/login",
            }
            expected_url = f"https://test.auth0.com/v2/logout?{urlencode(expected_params)}"
            assert logout_url == expected_url

    @pytest.mark.asyncio()
    async def test_logout_with_invalid_binding_skips_revocation(
        self, oauth2_handler, mock_session_store
    ) -> None:
        """Test logout does NOT revoke token when binding is invalid (IP mismatch).

        FIX (Codex Medium #5): Prevents attacker with stolen cookie from
        revoking real user's refresh token.
        """
        # Mock session store returns None (binding invalid - IP mismatch)
        mock_session_store.get_session.return_value = None

        # Mock HTTP client for token revocation (should NOT be called)
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Call handle_logout with different IP
            logout_url = await oauth2_handler.handle_logout(
                session_id="test_session",
                current_ip="10.0.0.99",  # Different IP (attacker)
                current_user_agent="Mozilla/5.0 (Test)",
            )

            # Verify binding validation called
            mock_session_store.get_session.assert_called_once_with(
                "test_session",
                current_ip="10.0.0.99",
                current_user_agent="Mozilla/5.0 (Test)",
                update_activity=False,
            )

            # CRITICAL: Verify refresh token revocation NOT called
            mock_client.post.assert_not_called()

            # Verify session still deleted locally
            mock_session_store.delete_session.assert_called_once_with("test_session")

            # Verify logout URL still returned
            assert "https://test.auth0.com/v2/logout?" in logout_url

    @pytest.mark.asyncio()
    async def test_logout_with_user_agent_mismatch_skips_revocation(
        self, oauth2_handler, mock_session_store
    ) -> None:
        """Test logout does NOT revoke token when User-Agent doesn't match."""
        # Mock session store returns None (binding invalid - User-Agent mismatch)
        mock_session_store.get_session.return_value = None

        # Mock HTTP client
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Call handle_logout with different User-Agent
            await oauth2_handler.handle_logout(
                session_id="test_session",
                current_ip="192.168.1.100",
                current_user_agent="Chrome/100.0 (Attacker)",  # Different UA
            )

            # Verify binding validation called
            mock_session_store.get_session.assert_called_once()

            # CRITICAL: Verify refresh token revocation NOT called
            mock_client.post.assert_not_called()

            # Verify session deleted locally
            mock_session_store.delete_session.assert_called_once_with("test_session")

    @pytest.mark.asyncio()
    async def test_logout_revocation_failure_non_critical(
        self, oauth2_handler, mock_session_store, sample_session_data
    ) -> None:
        """Test logout continues even if token revocation fails at Auth0."""
        # Mock session store returns session (binding valid)
        mock_session_store.get_session.return_value = sample_session_data

        # Mock HTTP client that raises error on revocation
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = AsyncMock()
            mock_response.status_code = 500
            mock_response.text = "Auth0 error"
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Revocation failed",
                request=AsyncMock(),
                response=mock_response,
            )
            mock_client.post.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Call handle_logout - should succeed despite revocation failure
            logout_url = await oauth2_handler.handle_logout(
                session_id="test_session",
                current_ip="192.168.1.100",
                current_user_agent="Mozilla/5.0 (Test)",
            )

            # Verify revocation was attempted
            mock_client.post.assert_called_once()

            # Verify session still deleted (non-critical failure)
            mock_session_store.delete_session.assert_called_once_with("test_session")

            # Verify logout URL still returned
            assert "https://test.auth0.com/v2/logout?" in logout_url

    @pytest.mark.asyncio()
    async def test_logout_without_refresh_token(
        self, oauth2_handler, mock_session_store, sample_session_data
    ) -> None:
        """Test logout handles missing refresh token gracefully."""
        # Session without refresh token
        sample_session_data.refresh_token = ""
        mock_session_store.get_session.return_value = sample_session_data

        # Mock HTTP client (should NOT be called since no token)
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Call handle_logout
            logout_url = await oauth2_handler.handle_logout(
                session_id="test_session",
                current_ip="192.168.1.100",
                current_user_agent="Mozilla/5.0 (Test)",
            )

            # Verify revocation NOT called (no token to revoke)
            mock_client.post.assert_not_called()

            # Verify session deleted
            mock_session_store.delete_session.assert_called_once_with("test_session")

            # Verify logout URL returned
            assert "https://test.auth0.com/v2/logout?" in logout_url


class TestRevokRefreshTokenMethod:
    """Test _revoke_refresh_token internal method."""

    @pytest.mark.asyncio()
    async def test_revoke_refresh_token_success(self, oauth2_handler) -> None:
        """Test _revoke_refresh_token makes correct Auth0 API call."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = AsyncMock()
            mock_response.raise_for_status = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Call _revoke_refresh_token
            await oauth2_handler._revoke_refresh_token("test_refresh_token_xyz")

            # Verify correct Auth0 API call
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert call_args[0][0] == "https://test.auth0.com/oauth/revoke"
            assert call_args[1]["data"] == {
                "client_id": "test_client_id",
                "client_secret": "test_client_secret",
                "token": "test_refresh_token_xyz",
            }
            assert call_args[1]["headers"]["Content-Type"] == "application/x-www-form-urlencoded"

    @pytest.mark.asyncio()
    async def test_revoke_refresh_token_auth0_error(self, oauth2_handler) -> None:
        """Test _revoke_refresh_token raises exception on Auth0 error."""
        from unittest.mock import MagicMock

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = MagicMock()  # Use MagicMock for sync raise_for_status
            mock_response.status_code = 401
            mock_response.text = "Invalid client credentials"
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Unauthorized",
                request=MagicMock(),
                response=mock_response,
            )
            mock_client.post.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Call _revoke_refresh_token - should raise
            with pytest.raises(httpx.HTTPStatusError):
                await oauth2_handler._revoke_refresh_token("test_refresh_token")
