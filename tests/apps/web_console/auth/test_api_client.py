"""Unit tests for secure API client (Component 3).

FIX (Codex Critical #1): Tests verify tokens are fetched from Redis (NEVER session_state).
"""

import pytest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from apps.web_console.auth.api_client import (
    get_access_token_from_redis,
    call_api_with_auth,
)
from apps.web_console.auth.session_store import SessionData


class TestGetAccessTokenFromRedis:
    """Test secure token fetching from Redis (Codex Critical #1 Fix)."""

    @pytest.mark.asyncio
    async def test_get_access_token_from_redis_valid_session(self) -> None:
        """Test fetching access token with valid session."""
        # Mock session store
        mock_session_store = AsyncMock()

        # Create mock session data
        now = datetime.now(UTC)
        mock_session_data = SessionData(
            access_token="test_access_token_12345",
            refresh_token="test_refresh_token",
            id_token="test_id_token",
            user_id="auth0|12345",
            email="test@example.com",
            created_at=now,
            last_activity=now,
            ip_address="192.168.1.100",
            user_agent="Mozilla/5.0",
            access_token_expires_at=now + timedelta(hours=1),
        )

        mock_session_store.get_session.return_value = mock_session_data

        # Fetch token
        token = await get_access_token_from_redis(
            session_id="test_session_id",
            session_store=mock_session_store,
            client_ip="192.168.1.100",
            user_agent="Mozilla/5.0",
        )

        # Verify
        assert token == "test_access_token_12345"
        mock_session_store.get_session.assert_called_once_with(
            "test_session_id",
            current_ip="192.168.1.100",
            current_user_agent="Mozilla/5.0",
            update_activity=False,
        )

    @pytest.mark.asyncio
    async def test_get_access_token_from_redis_invalid_session(self) -> None:
        """Test returns None when session invalid."""
        # Mock session store returning None (invalid session)
        mock_session_store = AsyncMock()
        mock_session_store.get_session.return_value = None

        # Fetch token
        token = await get_access_token_from_redis(
            session_id="invalid_session_id",
            session_store=mock_session_store,
            client_ip="192.168.1.100",
            user_agent="Mozilla/5.0",
        )

        # Verify returns None
        assert token is None

    @pytest.mark.asyncio
    async def test_get_access_token_from_redis_binding_mismatch(self) -> None:
        """Test returns None when binding validation fails (IP mismatch)."""
        # Mock session store returning None (binding failed)
        mock_session_store = AsyncMock()
        mock_session_store.get_session.return_value = None

        # Fetch token with different IP
        token = await get_access_token_from_redis(
            session_id="test_session_id",
            session_store=mock_session_store,
            client_ip="10.0.0.1",  # Different IP
            user_agent="Mozilla/5.0",
        )

        # Verify returns None
        assert token is None


class TestCallApiWithAuth:
    """Test authenticated API calls with OAuth2 bearer tokens."""

    @pytest.mark.asyncio
    async def test_call_api_with_auth_missing_parameters(self) -> None:
        """Test raises ValueError when required parameters missing."""
        with pytest.raises(ValueError, match="Missing required parameters"):
            await call_api_with_auth(
                url="https://api.example.com/data",
                method="GET",
                session_id=None,  # Missing
                session_store=None,
                client_ip=None,
                user_agent=None,
            )

    @pytest.mark.asyncio
    async def test_call_api_with_auth_invalid_session(self) -> None:
        """Test raises ValueError when session invalid."""
        # Mock session store returning None
        mock_session_store = AsyncMock()
        mock_session_store.get_session.return_value = None

        with pytest.raises(ValueError, match="Session invalid or expired"):
            await call_api_with_auth(
                url="https://api.example.com/data",
                method="GET",
                session_id="invalid_session",
                session_store=mock_session_store,
                client_ip="192.168.1.100",
                user_agent="Mozilla/5.0",
            )

    @pytest.mark.asyncio
    async def test_call_api_with_auth_successful_get_request(self) -> None:
        """Test successful GET request with Authorization header."""
        # Mock session store
        mock_session_store = AsyncMock()
        now = datetime.now(UTC)
        mock_session_data = SessionData(
            access_token="test_access_token_12345",
            refresh_token="test_refresh_token",
            id_token="test_id_token",
            user_id="auth0|12345",
            email="test@example.com",
            created_at=now,
            last_activity=now,
            ip_address="192.168.1.100",
            user_agent="Mozilla/5.0",
            access_token_expires_at=now + timedelta(hours=1),
        )
        mock_session_store.get_session.return_value = mock_session_data

        # Mock httpx client
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": "test_data"}

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Make API call
            response = await call_api_with_auth(
                url="https://api.example.com/data",
                method="GET",
                session_id="test_session",
                session_store=mock_session_store,
                client_ip="192.168.1.100",
                user_agent="Mozilla/5.0",
            )

            # Verify Authorization header added
            mock_client.request.assert_called_once()
            call_kwargs = mock_client.request.call_args[1]
            assert call_kwargs["headers"]["Authorization"] == "Bearer test_access_token_12345"

            # Verify response
            assert response.status_code == 200
            assert response.json() == {"data": "test_data"}

    @pytest.mark.asyncio
    async def test_call_api_with_auth_successful_post_request(self) -> None:
        """Test successful POST request with JSON body."""
        # Mock session store
        mock_session_store = AsyncMock()
        now = datetime.now(UTC)
        mock_session_data = SessionData(
            access_token="test_access_token_12345",
            refresh_token="test_refresh_token",
            id_token="test_id_token",
            user_id="auth0|12345",
            email="test@example.com",
            created_at=now,
            last_activity=now,
            ip_address="192.168.1.100",
            user_agent="Mozilla/5.0",
            access_token_expires_at=now + timedelta(hours=1),
        )
        mock_session_store.get_session.return_value = mock_session_data

        # Mock httpx client
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 201

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Make API call with JSON body
            response = await call_api_with_auth(
                url="https://api.example.com/create",
                method="POST",
                session_id="test_session",
                session_store=mock_session_store,
                client_ip="192.168.1.100",
                user_agent="Mozilla/5.0",
                json={"name": "test", "value": 123},
            )

            # Verify Authorization header and JSON body
            mock_client.request.assert_called_once()
            call_args = mock_client.request.call_args
            assert call_args[0] == ("POST", "https://api.example.com/create")
            assert call_args[1]["headers"]["Authorization"] == "Bearer test_access_token_12345"
            assert call_args[1]["json"] == {"name": "test", "value": 123}

            # Verify response
            assert response.status_code == 201

    @pytest.mark.asyncio
    async def test_call_api_with_auth_custom_headers_preserved(self) -> None:
        """Test custom headers are preserved alongside Authorization header."""
        # Mock session store
        mock_session_store = AsyncMock()
        now = datetime.now(UTC)
        mock_session_data = SessionData(
            access_token="test_access_token_12345",
            refresh_token="test_refresh_token",
            id_token="test_id_token",
            user_id="auth0|12345",
            email="test@example.com",
            created_at=now,
            last_activity=now,
            ip_address="192.168.1.100",
            user_agent="Mozilla/5.0",
            access_token_expires_at=now + timedelta(hours=1),
        )
        mock_session_store.get_session.return_value = mock_session_data

        # Mock httpx client
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Make API call with custom headers
            custom_headers = {
                "X-Custom-Header": "custom_value",
                "Content-Type": "application/json",
            }

            response = await call_api_with_auth(
                url="https://api.example.com/data",
                method="GET",
                session_id="test_session",
                session_store=mock_session_store,
                client_ip="192.168.1.100",
                user_agent="Mozilla/5.0",
                headers=custom_headers,
            )

            # Verify both Authorization and custom headers present
            mock_client.request.assert_called_once()
            call_kwargs = mock_client.request.call_args[1]
            headers = call_kwargs["headers"]
            assert headers["Authorization"] == "Bearer test_access_token_12345"
            assert headers["X-Custom-Header"] == "custom_value"
            assert headers["Content-Type"] == "application/json"
