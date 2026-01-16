"""
Unit tests for libs.platform.web_console_auth.oauth2_flow.

Tests cover:
- OAuth2 flow initialization and configuration
- Login initiation with PKCE challenge generation
- Callback handling with token exchange and validation
- Token refresh with session binding and absolute timeout enforcement
- Logout with binding validation and token revocation
- RBAC data loading (role and strategy access)
- Error handling paths (HTTP errors, network errors, validation failures)
- Edge cases (missing tokens, invalid states, expired sessions)

Target: 85%+ branch coverage (baseline from 0%)

Phase 3 Step 4 - Zero Coverage Module Tests (oauth2_flow.py: 226 statements, 40 branches)
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, Mock, patch

import httpx
import jwt
import pytest
import redis.asyncio

from libs.platform.web_console_auth.oauth2_flow import (
    OAuth2Config,
    OAuth2FlowHandler,
    _fetch_user_role_data,
    _fetch_user_strategies,
)
from libs.platform.web_console_auth.oauth2_state import OAuth2State
from libs.platform.web_console_auth.session_store import SessionData


@pytest.fixture
def oauth2_config() -> OAuth2Config:
    """Create OAuth2 configuration for testing."""
    return OAuth2Config(
        auth0_domain="test.auth0.com",
        client_id="test_client_id",
        client_secret="test_client_secret",
        audience="https://api.example.com",
        redirect_uri="https://example.com/callback",
        logout_redirect_uri="https://example.com/",
    )


@pytest.fixture
def mock_session_store() -> Mock:
    """Create mock Redis session store."""
    store = Mock()
    store.absolute_timeout = timedelta(hours=4)
    store.redis = AsyncMock(spec=redis.asyncio.Redis)
    store.redis.setex = AsyncMock()  # Explicitly make setex async
    store._encrypt = Mock(return_value=b"encrypted_data")
    store.create_session = AsyncMock()
    store.get_session = AsyncMock()
    store.delete_session = AsyncMock()
    return store


@pytest.fixture
def mock_state_store() -> Mock:
    """Create mock OAuth2 state store."""
    store = Mock()
    store.store_state = AsyncMock()
    store.get_and_delete_state = AsyncMock()
    return store


@pytest.fixture
def mock_jwks_validator() -> Mock:
    """Create mock JWKS validator."""
    validator = Mock()
    validator.validate_id_token = AsyncMock()
    return validator


@pytest.fixture
def mock_db_pool() -> Mock:
    """Create mock database pool."""
    return Mock()


@pytest.fixture
def mock_audit_logger() -> Mock:
    """Create mock audit logger."""
    logger = Mock()
    logger.log_auth_event = AsyncMock()
    return logger


@pytest.fixture
def oauth2_handler(
    oauth2_config: OAuth2Config,
    mock_session_store: Mock,
    mock_state_store: Mock,
    mock_jwks_validator: Mock,
    mock_db_pool: Mock,
    mock_audit_logger: Mock,
) -> OAuth2FlowHandler:
    """Create OAuth2FlowHandler instance for testing."""
    return OAuth2FlowHandler(
        config=oauth2_config,
        session_store=mock_session_store,
        state_store=mock_state_store,
        jwks_validator=mock_jwks_validator,
        db_pool=mock_db_pool,
        audit_logger=mock_audit_logger,
    )


@pytest.fixture
def sample_oauth_state() -> OAuth2State:
    """Create sample OAuth2 state for testing."""
    return OAuth2State(
        state="test_state_123",
        code_verifier="test_code_verifier",
        nonce="test_nonce",
        code_challenge="test_code_challenge",
        redirect_uri="https://example.com/callback",
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def sample_tokens() -> dict[str, Any]:
    """Create sample token response for testing."""
    return {
        "access_token": "test_access_token",
        "refresh_token": "test_refresh_token",
        "id_token": "test_id_token",
        "expires_in": 3600,
        "token_type": "Bearer",
    }


@pytest.fixture
def sample_id_token_claims() -> dict[str, Any]:
    """Create sample ID token claims for testing."""
    return {
        "sub": "auth0|12345",
        "email": "user@example.com",
        "email_verified": True,
        "aud": "test_client_id",
        "iss": "https://test.auth0.com/",
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
    }


@pytest.fixture
def sample_session_data() -> SessionData:
    """Create sample session data for testing."""
    now = datetime.now(UTC)
    return SessionData(
        access_token="test_access_token",
        refresh_token="test_refresh_token",
        id_token="test_id_token",
        user_id="auth0|12345",
        email="user@example.com",
        created_at=now,
        last_activity=now,
        ip_address="192.168.1.1",
        user_agent="Mozilla/5.0",
        access_token_expires_at=now + timedelta(hours=1),
        role="operator",
        strategies=["strategy_1", "strategy_2"],
        session_version=1,
    )


class TestOAuth2Config:
    """Tests for OAuth2Config model."""

    def test_oauth2_config_initialization(self, oauth2_config: OAuth2Config):
        """Test OAuth2Config initializes with all required fields."""
        assert oauth2_config.auth0_domain == "test.auth0.com"
        assert oauth2_config.client_id == "test_client_id"
        assert oauth2_config.client_secret == "test_client_secret"
        assert oauth2_config.audience == "https://api.example.com"
        assert oauth2_config.redirect_uri == "https://example.com/callback"
        assert oauth2_config.logout_redirect_uri == "https://example.com/"


class TestOAuth2FlowHandlerInit:
    """Tests for OAuth2FlowHandler initialization."""

    def test_init_with_all_parameters(
        self,
        oauth2_config: OAuth2Config,
        mock_session_store: Mock,
        mock_state_store: Mock,
        mock_jwks_validator: Mock,
        mock_db_pool: Mock,
        mock_audit_logger: Mock,
    ):
        """Test handler initializes with all parameters."""
        handler = OAuth2FlowHandler(
            config=oauth2_config,
            session_store=mock_session_store,
            state_store=mock_state_store,
            jwks_validator=mock_jwks_validator,
            db_pool=mock_db_pool,
            audit_logger=mock_audit_logger,
        )

        assert handler.config == oauth2_config
        assert handler.session_store == mock_session_store
        assert handler.state_store == mock_state_store
        assert handler.jwks_validator == mock_jwks_validator
        assert handler.db_pool == mock_db_pool
        assert handler.audit_logger == mock_audit_logger
        assert handler.authorization_endpoint == "https://test.auth0.com/authorize"
        assert handler.token_endpoint == "https://test.auth0.com/oauth/token"
        assert handler.logout_endpoint == "https://test.auth0.com/v2/logout"

    def test_init_without_audit_logger_creates_default(
        self,
        oauth2_config: OAuth2Config,
        mock_session_store: Mock,
        mock_state_store: Mock,
        mock_jwks_validator: Mock,
        mock_db_pool: Mock,
    ):
        """Test handler creates default audit logger if not provided."""
        handler = OAuth2FlowHandler(
            config=oauth2_config,
            session_store=mock_session_store,
            state_store=mock_state_store,
            jwks_validator=mock_jwks_validator,
            db_pool=mock_db_pool,
        )

        assert handler.audit_logger is not None


class TestInitiateLogin:
    """Tests for initiate_login() method."""

    @pytest.mark.asyncio
    async def test_initiate_login_success(
        self,
        oauth2_handler: OAuth2FlowHandler,
        mock_state_store: Mock,
    ):
        """Test login initiation generates valid authorization URL and stores state."""
        with patch(
            "libs.platform.web_console_auth.oauth2_flow.generate_pkce_challenge"
        ) as mock_pkce, patch(
            "libs.platform.web_console_auth.oauth2_flow.generate_state"
        ) as mock_state, patch(
            "libs.platform.web_console_auth.oauth2_flow.generate_nonce"
        ) as mock_nonce:
            # Setup mocks
            mock_pkce.return_value = Mock(
                code_verifier="test_verifier", code_challenge="test_challenge"
            )
            mock_state.return_value = "test_state"
            mock_nonce.return_value = "test_nonce"

            # Execute
            auth_url, oauth_state = await oauth2_handler.initiate_login()

            # Verify state stored
            mock_state_store.store_state.assert_called_once()
            stored_state = mock_state_store.store_state.call_args[0][0]
            assert stored_state.state == "test_state"
            assert stored_state.code_verifier == "test_verifier"
            assert stored_state.nonce == "test_nonce"
            assert stored_state.code_challenge == "test_challenge"
            assert stored_state.redirect_uri == "https://example.com/callback"

            # Verify authorization URL structure
            assert auth_url.startswith("https://test.auth0.com/authorize?")
            assert "response_type=code" in auth_url
            assert "client_id=test_client_id" in auth_url
            assert "redirect_uri=https%3A%2F%2Fexample.com%2Fcallback" in auth_url
            assert "scope=openid+profile+email+offline_access" in auth_url
            assert "audience=https%3A%2F%2Fapi.example.com" in auth_url
            assert "state=test_state" in auth_url
            assert "nonce=test_nonce" in auth_url
            assert "code_challenge=test_challenge" in auth_url
            assert "code_challenge_method=S256" in auth_url

            # Verify returned state
            assert oauth_state.state == "test_state"


class TestHandleCallback:
    """Tests for handle_callback() method."""

    @pytest.mark.asyncio
    async def test_handle_callback_success(
        self,
        oauth2_handler: OAuth2FlowHandler,
        mock_state_store: Mock,
        mock_jwks_validator: Mock,
        mock_session_store: Mock,
        mock_audit_logger: Mock,
        mock_db_pool: Mock,
        sample_oauth_state: OAuth2State,
        sample_tokens: dict[str, Any],
        sample_id_token_claims: dict[str, Any],
    ):
        """Test successful callback handling with token exchange and session creation."""
        # Setup mocks
        mock_state_store.get_and_delete_state.return_value = sample_oauth_state
        mock_jwks_validator.validate_id_token.return_value = sample_id_token_claims

        # Mock HTTP token exchange
        mock_response = Mock()
        mock_response.json.return_value = sample_tokens
        mock_response.raise_for_status = Mock()

        # Mock database queries
        mock_cursor = Mock()
        mock_cursor.fetchone = AsyncMock(
            return_value={"role": "operator", "session_version": 1}
        )
        mock_cursor.fetchall = AsyncMock(
            return_value=[{"strategy_id": "strategy_1"}, {"strategy_id": "strategy_2"}]
        )

        mock_conn = Mock()
        mock_conn.execute = AsyncMock(return_value=mock_cursor)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "libs.platform.web_console_auth.oauth2_flow.acquire_connection"
        ) as mock_acquire, patch("httpx.AsyncClient") as mock_http_client:
            mock_acquire.return_value = mock_conn
            mock_http_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            # Execute
            session_id, session_data = await oauth2_handler.handle_callback(
                code="test_auth_code",
                state="test_state_123",
                ip_address="192.168.1.1",
                user_agent="Mozilla/5.0",
                db_pool=mock_db_pool,
                audit_logger=mock_audit_logger,
            )

            # Verify state consumed
            mock_state_store.get_and_delete_state.assert_called_once_with("test_state_123")

            # Verify ID token validated
            mock_jwks_validator.validate_id_token.assert_called_once_with(
                id_token="test_id_token",
                expected_nonce="test_nonce",
                expected_audience="test_client_id",
                expected_issuer="https://test.auth0.com/",
            )

            # Verify session created
            mock_session_store.create_session.assert_called_once()
            assert session_data.user_id == "auth0|12345"
            assert session_data.email == "user@example.com"
            assert session_data.role == "operator"
            assert session_data.strategies == ["strategy_1", "strategy_2"]

            # Verify audit log
            mock_audit_logger.log_auth_event.assert_called_once_with(
                user_id="auth0|12345",
                action="login",
                outcome="success",
                details={"strategies": ["strategy_1", "strategy_2"]},
            )

    @pytest.mark.asyncio
    async def test_handle_callback_invalid_state(
        self,
        oauth2_handler: OAuth2FlowHandler,
        mock_state_store: Mock,
        mock_audit_logger: Mock,
    ):
        """Test callback fails with invalid state."""
        mock_state_store.get_and_delete_state.return_value = None

        with pytest.raises(ValueError, match="Invalid or expired state parameter"):
            await oauth2_handler.handle_callback(
                code="test_code",
                state="invalid_state",
                ip_address="192.168.1.1",
                user_agent="Mozilla/5.0",
            )

        # Verify audit log
        mock_audit_logger.log_auth_event.assert_called_once()
        call_args = mock_audit_logger.log_auth_event.call_args[1]
        assert call_args["action"] == "login"
        assert call_args["outcome"] == "denied"

    @pytest.mark.asyncio
    async def test_handle_callback_missing_tokens(
        self,
        oauth2_handler: OAuth2FlowHandler,
        mock_state_store: Mock,
        mock_audit_logger: Mock,
        sample_oauth_state: OAuth2State,
    ):
        """Test callback fails when token response missing required fields."""
        mock_state_store.get_and_delete_state.return_value = sample_oauth_state

        # Mock HTTP response missing refresh_token
        mock_response = Mock()
        mock_response.json.return_value = {
            "access_token": "test_access_token",
            "id_token": "test_id_token",
            # Missing refresh_token
        }
        mock_response.raise_for_status = Mock()

        with patch("httpx.AsyncClient") as mock_http_client:
            mock_http_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            with pytest.raises(ValueError, match="Token exchange response incomplete"):
                await oauth2_handler.handle_callback(
                    code="test_code",
                    state="test_state_123",
                    ip_address="192.168.1.1",
                    user_agent="Mozilla/5.0",
                )

    @pytest.mark.asyncio
    async def test_handle_callback_http_error(
        self,
        oauth2_handler: OAuth2FlowHandler,
        mock_state_store: Mock,
        mock_audit_logger: Mock,
        sample_oauth_state: OAuth2State,
    ):
        """Test callback handles HTTP errors from token endpoint."""
        mock_state_store.get_and_delete_state.return_value = sample_oauth_state

        # Mock HTTP error response
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = "Invalid authorization code"

        with patch("httpx.AsyncClient") as mock_http_client:
            mock_http_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "Bad Request", request=Mock(), response=mock_response
                )
            )

            with pytest.raises(ValueError, match="Token exchange failed: 400"):
                await oauth2_handler.handle_callback(
                    code="test_code",
                    state="test_state_123",
                    ip_address="192.168.1.1",
                    user_agent="Mozilla/5.0",
                )

    @pytest.mark.asyncio
    async def test_handle_callback_network_error(
        self,
        oauth2_handler: OAuth2FlowHandler,
        mock_state_store: Mock,
        mock_audit_logger: Mock,
        sample_oauth_state: OAuth2State,
    ):
        """Test callback handles network errors during token exchange."""
        mock_state_store.get_and_delete_state.return_value = sample_oauth_state

        with patch("httpx.AsyncClient") as mock_http_client:
            mock_http_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.RequestError("Connection timeout")
            )

            with pytest.raises(ValueError, match="Token exchange network error"):
                await oauth2_handler.handle_callback(
                    code="test_code",
                    state="test_state_123",
                    ip_address="192.168.1.1",
                    user_agent="Mozilla/5.0",
                )

    @pytest.mark.asyncio
    async def test_handle_callback_invalid_id_token(
        self,
        oauth2_handler: OAuth2FlowHandler,
        mock_state_store: Mock,
        mock_jwks_validator: Mock,
        mock_audit_logger: Mock,
        sample_oauth_state: OAuth2State,
        sample_tokens: dict[str, Any],
    ):
        """Test callback fails when ID token validation fails."""
        mock_state_store.get_and_delete_state.return_value = sample_oauth_state
        mock_jwks_validator.validate_id_token.side_effect = jwt.InvalidTokenError(
            "Invalid signature"
        )

        mock_response = Mock()
        mock_response.json.return_value = sample_tokens
        mock_response.raise_for_status = Mock()

        with patch("httpx.AsyncClient") as mock_http_client:
            mock_http_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            with pytest.raises(ValueError, match="ID token validation failed"):
                await oauth2_handler.handle_callback(
                    code="test_code",
                    state="test_state_123",
                    ip_address="192.168.1.1",
                    user_agent="Mozilla/5.0",
                )

    @pytest.mark.asyncio
    async def test_handle_callback_user_not_provisioned(
        self,
        oauth2_handler: OAuth2FlowHandler,
        mock_state_store: Mock,
        mock_jwks_validator: Mock,
        mock_audit_logger: Mock,
        mock_db_pool: Mock,
        sample_oauth_state: OAuth2State,
        sample_tokens: dict[str, Any],
        sample_id_token_claims: dict[str, Any],
    ):
        """Test callback fails when user not provisioned in database."""
        mock_state_store.get_and_delete_state.return_value = sample_oauth_state
        mock_jwks_validator.validate_id_token.return_value = sample_id_token_claims

        mock_response = Mock()
        mock_response.json.return_value = sample_tokens
        mock_response.raise_for_status = Mock()

        # Mock database returning no role data
        mock_cursor = Mock()
        mock_cursor.fetchone = AsyncMock(return_value=None)

        mock_conn = Mock()
        mock_conn.execute = AsyncMock(return_value=mock_cursor)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "libs.platform.web_console_auth.oauth2_flow.acquire_connection"
        ) as mock_acquire, patch("httpx.AsyncClient") as mock_http_client:
            mock_acquire.return_value = mock_conn
            mock_http_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            with pytest.raises(ValueError, match="User not provisioned"):
                await oauth2_handler.handle_callback(
                    code="test_code",
                    state="test_state_123",
                    ip_address="192.168.1.1",
                    user_agent="Mozilla/5.0",
                    db_pool=mock_db_pool,
                    audit_logger=mock_audit_logger,
                )

            # Verify audit log for denied login
            assert mock_audit_logger.log_auth_event.call_count == 1
            call_args = mock_audit_logger.log_auth_event.call_args[1]
            assert call_args["action"] == "login"
            assert call_args["outcome"] == "denied"
            assert call_args["details"]["reason"] == "user_not_provisioned"

    @pytest.mark.asyncio
    async def test_handle_callback_without_db_pool(
        self,
        mock_state_store: Mock,
        mock_jwks_validator: Mock,
        mock_session_store: Mock,
        oauth2_config: OAuth2Config,
        sample_oauth_state: OAuth2State,
        sample_tokens: dict[str, Any],
        sample_id_token_claims: dict[str, Any],
    ):
        """Test callback succeeds without database pool (default role/strategies)."""
        # Create handler without db_pool
        handler = OAuth2FlowHandler(
            config=oauth2_config,
            session_store=mock_session_store,
            state_store=mock_state_store,
            jwks_validator=mock_jwks_validator,
            db_pool=None,
            audit_logger=None,
        )

        mock_state_store.get_and_delete_state.return_value = sample_oauth_state
        mock_jwks_validator.validate_id_token.return_value = sample_id_token_claims

        mock_response = Mock()
        mock_response.json.return_value = sample_tokens
        mock_response.raise_for_status = Mock()

        with patch("httpx.AsyncClient") as mock_http_client:
            mock_http_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            # Execute without db_pool
            session_id, session_data = await handler.handle_callback(
                code="test_code",
                state="test_state_123",
                ip_address="192.168.1.1",
                user_agent="Mozilla/5.0",
                db_pool=None,
                audit_logger=None,
            )

            # Verify default values used
            assert session_data.role == "viewer"
            assert session_data.strategies == []
            assert session_data.session_version == 1


class TestConsumeState:
    """Tests for _consume_state() internal method."""

    @pytest.mark.asyncio
    async def test_consume_state_success(
        self,
        oauth2_handler: OAuth2FlowHandler,
        mock_state_store: Mock,
        sample_oauth_state: OAuth2State,
    ):
        """Test state consumption returns valid OAuth state."""
        mock_state_store.get_and_delete_state.return_value = sample_oauth_state

        result = await oauth2_handler._consume_state("test_state_123")

        assert result == sample_oauth_state
        mock_state_store.get_and_delete_state.assert_called_once_with("test_state_123")

    @pytest.mark.asyncio
    async def test_consume_state_invalid(
        self,
        oauth2_handler: OAuth2FlowHandler,
        mock_state_store: Mock,
    ):
        """Test state consumption fails for invalid state."""
        mock_state_store.get_and_delete_state.return_value = None

        with pytest.raises(ValueError, match="Invalid or expired state parameter"):
            await oauth2_handler._consume_state("invalid_state")


class TestAssertRequiredTokens:
    """Tests for _assert_required_tokens() internal method."""

    def test_assert_required_tokens_success(self, oauth2_handler: OAuth2FlowHandler):
        """Test token assertion passes with all required tokens."""
        tokens = {
            "access_token": "test_access",
            "refresh_token": "test_refresh",
            "id_token": "test_id",
        }

        # Should not raise
        oauth2_handler._assert_required_tokens(tokens)

    def test_assert_required_tokens_missing_access_token(
        self, oauth2_handler: OAuth2FlowHandler
    ):
        """Test token assertion fails when access_token missing."""
        tokens = {
            "refresh_token": "test_refresh",
            "id_token": "test_id",
        }

        with pytest.raises(ValueError, match="missing \\['access_token'\\]"):
            oauth2_handler._assert_required_tokens(tokens)

    def test_assert_required_tokens_missing_multiple(
        self, oauth2_handler: OAuth2FlowHandler
    ):
        """Test token assertion fails when multiple tokens missing."""
        tokens = {
            "access_token": "test_access",
        }

        with pytest.raises(ValueError, match="Token exchange response incomplete"):
            oauth2_handler._assert_required_tokens(tokens)


class TestLoadRBACData:
    """Tests for _load_rbac_data() internal method."""

    @pytest.mark.asyncio
    async def test_load_rbac_data_success(
        self,
        oauth2_handler: OAuth2FlowHandler,
        mock_db_pool: Mock,
    ):
        """Test RBAC data loading returns role and strategies."""
        # Mock database queries
        mock_cursor_role = Mock()
        mock_cursor_role.fetchone = AsyncMock(
            return_value={"role": "admin", "session_version": 2}
        )

        mock_cursor_strategies = Mock()
        mock_cursor_strategies.fetchall = AsyncMock(
            return_value=[{"strategy_id": "s1"}, {"strategy_id": "s2"}]
        )

        mock_conn = Mock()

        async def mock_execute(query, params):
            if "user_roles" in query:
                return mock_cursor_role
            else:
                return mock_cursor_strategies

        mock_conn.execute = AsyncMock(side_effect=mock_execute)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)

        with patch("libs.platform.web_console_auth.oauth2_flow.acquire_connection") as mock_acquire:
            mock_acquire.return_value = mock_conn

            role_data, strategies = await oauth2_handler._load_rbac_data(
                user_id="auth0|12345",
                db_pool=mock_db_pool,
            )

            assert role_data == {"role": "admin", "session_version": 2}
            assert strategies == ["s1", "s2"]

    @pytest.mark.asyncio
    async def test_load_rbac_data_user_not_found(
        self,
        oauth2_handler: OAuth2FlowHandler,
        mock_db_pool: Mock,
    ):
        """Test RBAC data loading raises when user not found."""
        mock_cursor = Mock()
        mock_cursor.fetchone = AsyncMock(return_value=None)

        mock_conn = Mock()
        mock_conn.execute = AsyncMock(return_value=mock_cursor)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)

        with patch("libs.platform.web_console_auth.oauth2_flow.acquire_connection") as mock_acquire:
            mock_acquire.return_value = mock_conn

            with pytest.raises(ValueError, match="User not provisioned"):
                await oauth2_handler._load_rbac_data(
                    user_id="auth0|12345",
                    db_pool=mock_db_pool,
                )

    @pytest.mark.asyncio
    async def test_load_rbac_data_without_pool(
        self,
        oauth2_handler: OAuth2FlowHandler,
    ):
        """Test RBAC data loading returns defaults when pool is None."""
        role_data, strategies = await oauth2_handler._load_rbac_data(
            user_id="auth0|12345",
            db_pool=None,
        )

        assert role_data is None
        assert strategies == []


class TestBuildSessionData:
    """Tests for _build_session_data() internal method."""

    def test_build_session_data_with_rbac(
        self,
        oauth2_handler: OAuth2FlowHandler,
        sample_tokens: dict[str, Any],
        sample_id_token_claims: dict[str, Any],
    ):
        """Test session data building with RBAC data."""
        role_data = {"role": "operator", "session_version": 3}
        strategies = ["strategy_1", "strategy_2"]

        with patch(
            "libs.platform.web_console_auth.oauth2_flow.generate_session_id"
        ) as mock_session_id:
            mock_session_id.return_value = "test_session_id"

            session_id, session_data = oauth2_handler._build_session_data(
                user_id="auth0|12345",
                tokens=sample_tokens,
                id_token_claims=sample_id_token_claims,
                role_data=role_data,
                strategies=strategies,
                ip_address="192.168.1.1",
                user_agent="Mozilla/5.0",
            )

            assert session_id == "test_session_id"
            assert session_data.user_id == "auth0|12345"
            assert session_data.email == "user@example.com"
            assert session_data.role == "operator"
            assert session_data.session_version == 3
            assert session_data.strategies == ["strategy_1", "strategy_2"]
            assert session_data.access_token == "test_access_token"
            assert session_data.ip_address == "192.168.1.1"

    def test_build_session_data_without_rbac(
        self,
        oauth2_handler: OAuth2FlowHandler,
        sample_tokens: dict[str, Any],
        sample_id_token_claims: dict[str, Any],
    ):
        """Test session data building without RBAC data (defaults)."""
        session_id, session_data = oauth2_handler._build_session_data(
            user_id="auth0|12345",
            tokens=sample_tokens,
            id_token_claims=sample_id_token_claims,
            role_data=None,
            strategies=[],
            ip_address="192.168.1.1",
            user_agent="Mozilla/5.0",
        )

        assert session_data.role == "viewer"
        assert session_data.session_version == 1
        assert session_data.strategies == []

    def test_build_session_data_missing_email_in_claims(
        self,
        oauth2_handler: OAuth2FlowHandler,
        sample_tokens: dict[str, Any],
    ):
        """Test session data building defaults email when missing from claims."""
        id_token_claims = {"sub": "auth0|12345"}  # No email

        session_id, session_data = oauth2_handler._build_session_data(
            user_id="auth0|12345",
            tokens=sample_tokens,
            id_token_claims=id_token_claims,
            role_data=None,
            strategies=[],
            ip_address="192.168.1.1",
            user_agent="Mozilla/5.0",
        )

        assert session_data.email == "unknown@example.com"


class TestRefreshTokens:
    """Tests for refresh_tokens() method."""

    @pytest.mark.asyncio
    async def test_refresh_tokens_success(
        self,
        oauth2_config: OAuth2Config,
        mock_session_store: Mock,
        mock_state_store: Mock,
        mock_jwks_validator: Mock,
        mock_audit_logger: Mock,
        sample_session_data: SessionData,
    ):
        """Test successful token refresh with session binding."""
        # Create handler without db_pool to avoid validation
        handler = OAuth2FlowHandler(
            config=oauth2_config,
            session_store=mock_session_store,
            state_store=mock_state_store,
            jwks_validator=mock_jwks_validator,
            db_pool=None,
            audit_logger=mock_audit_logger,
        )

        mock_session_store.get_session.return_value = sample_session_data

        # Mock HTTP refresh response
        new_tokens = {
            "access_token": "new_access_token",
            "refresh_token": "new_refresh_token",
            "expires_in": 3600,
        }
        mock_response = Mock()
        mock_response.json.return_value = new_tokens
        mock_response.raise_for_status = Mock()

        with patch("httpx.AsyncClient") as mock_http_client:
            mock_http_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            updated_session = await handler.refresh_tokens(
                session_id="test_session",
                ip_address="192.168.1.1",
                user_agent="Mozilla/5.0",
                enforce_binding=True,
            )

            # Verify session retrieved with binding
            mock_session_store.get_session.assert_called_once_with(
                "test_session",
                current_ip="192.168.1.1",
                current_user_agent="Mozilla/5.0",
                update_activity=False,
            )

            # Verify tokens updated
            assert updated_session.access_token == "new_access_token"
            assert updated_session.refresh_token == "new_refresh_token"

            # Verify session stored with remaining TTL
            assert mock_session_store.redis.setex.called

    @pytest.mark.asyncio
    async def test_refresh_tokens_binding_required_missing_params(
        self,
        oauth2_handler: OAuth2FlowHandler,
        mock_audit_logger: Mock,
    ):
        """Test refresh fails when binding required but IP/UA missing."""
        with pytest.raises(ValueError, match="Session binding required"):
            await oauth2_handler.refresh_tokens(
                session_id="test_session",
                ip_address=None,
                user_agent=None,
                enforce_binding=True,
            )

        # Verify audit log
        mock_audit_logger.log_auth_event.assert_called_once()
        call_args = mock_audit_logger.log_auth_event.call_args[1]
        assert call_args["action"] == "refresh"
        assert call_args["outcome"] == "denied"

    @pytest.mark.asyncio
    async def test_refresh_tokens_without_binding(
        self,
        oauth2_config: OAuth2Config,
        mock_session_store: Mock,
        mock_state_store: Mock,
        mock_jwks_validator: Mock,
        sample_session_data: SessionData,
    ):
        """Test token refresh without binding validation."""
        handler = OAuth2FlowHandler(
            config=oauth2_config,
            session_store=mock_session_store,
            state_store=mock_state_store,
            jwks_validator=mock_jwks_validator,
            db_pool=None,
        )

        mock_session_store.get_session.return_value = sample_session_data

        new_tokens = {
            "access_token": "new_access_token",
            "refresh_token": "new_refresh_token",
            "expires_in": 3600,
        }
        mock_response = Mock()
        mock_response.json.return_value = new_tokens
        mock_response.raise_for_status = Mock()

        with patch("httpx.AsyncClient") as mock_http_client:
            mock_http_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            await handler.refresh_tokens(
                session_id="test_session",
                ip_address=None,
                user_agent=None,
                enforce_binding=False,
            )

            # Verify session retrieved without binding
            mock_session_store.get_session.assert_called_once_with(
                "test_session",
                current_ip=None,
                current_user_agent=None,
                update_activity=False,
            )

    @pytest.mark.asyncio
    async def test_refresh_tokens_session_not_found(
        self,
        oauth2_handler: OAuth2FlowHandler,
        mock_session_store: Mock,
        mock_audit_logger: Mock,
    ):
        """Test refresh fails when session not found."""
        mock_session_store.get_session.return_value = None

        with pytest.raises(ValueError, match="Session not found or invalid"):
            await oauth2_handler.refresh_tokens(
                session_id="invalid_session",
                ip_address="192.168.1.1",
                user_agent="Mozilla/5.0",
                enforce_binding=True,
            )

    @pytest.mark.asyncio
    async def test_refresh_tokens_http_error(
        self,
        oauth2_config: OAuth2Config,
        mock_session_store: Mock,
        mock_state_store: Mock,
        mock_jwks_validator: Mock,
        sample_session_data: SessionData,
    ):
        """Test refresh handles HTTP errors from token endpoint."""
        handler = OAuth2FlowHandler(
            config=oauth2_config,
            session_store=mock_session_store,
            state_store=mock_state_store,
            jwks_validator=mock_jwks_validator,
            db_pool=None,
        )

        mock_session_store.get_session.return_value = sample_session_data

        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "Invalid refresh token"

        with patch("httpx.AsyncClient") as mock_http_client:
            mock_http_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "Unauthorized", request=Mock(), response=mock_response
                )
            )

            with pytest.raises(ValueError, match="Token refresh failed: 401"):
                await handler.refresh_tokens(
                    session_id="test_session",
                    ip_address="192.168.1.1",
                    user_agent="Mozilla/5.0",
                )

    @pytest.mark.asyncio
    async def test_refresh_tokens_network_error(
        self,
        oauth2_config: OAuth2Config,
        mock_session_store: Mock,
        mock_state_store: Mock,
        mock_jwks_validator: Mock,
        sample_session_data: SessionData,
    ):
        """Test refresh handles network errors."""
        handler = OAuth2FlowHandler(
            config=oauth2_config,
            session_store=mock_session_store,
            state_store=mock_state_store,
            jwks_validator=mock_jwks_validator,
            db_pool=None,
        )

        mock_session_store.get_session.return_value = sample_session_data

        with patch("httpx.AsyncClient") as mock_http_client:
            mock_http_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.RequestError("Connection timeout")
            )

            with pytest.raises(ValueError, match="Token refresh network error"):
                await handler.refresh_tokens(
                    session_id="test_session",
                    ip_address="192.168.1.1",
                    user_agent="Mozilla/5.0",
                )

    @pytest.mark.asyncio
    async def test_refresh_tokens_missing_access_token(
        self,
        oauth2_config: OAuth2Config,
        mock_session_store: Mock,
        mock_state_store: Mock,
        mock_jwks_validator: Mock,
        sample_session_data: SessionData,
    ):
        """Test refresh fails when response missing access_token."""
        handler = OAuth2FlowHandler(
            config=oauth2_config,
            session_store=mock_session_store,
            state_store=mock_state_store,
            jwks_validator=mock_jwks_validator,
            db_pool=None,
        )

        mock_session_store.get_session.return_value = sample_session_data

        mock_response = Mock()
        mock_response.json.return_value = {}  # No access_token
        mock_response.raise_for_status = Mock()

        with patch("httpx.AsyncClient") as mock_http_client:
            mock_http_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            with pytest.raises(ValueError, match="missing access_token"):
                await handler.refresh_tokens(
                    session_id="test_session",
                    ip_address="192.168.1.1",
                    user_agent="Mozilla/5.0",
                )

    @pytest.mark.asyncio
    async def test_refresh_tokens_with_new_id_token_valid(
        self,
        oauth2_config: OAuth2Config,
        mock_session_store: Mock,
        mock_state_store: Mock,
        mock_jwks_validator: Mock,
        sample_session_data: SessionData,
    ):
        """Test refresh with new ID token validates subject matches."""
        handler = OAuth2FlowHandler(
            config=oauth2_config,
            session_store=mock_session_store,
            state_store=mock_state_store,
            jwks_validator=mock_jwks_validator,
            db_pool=None,
        )

        mock_session_store.get_session.return_value = sample_session_data

        new_tokens = {
            "access_token": "new_access_token",
            "refresh_token": "new_refresh_token",
            "id_token": "new_id_token",
            "expires_in": 3600,
        }
        mock_response = Mock()
        mock_response.json.return_value = new_tokens
        mock_response.raise_for_status = Mock()

        # Mock ID token validation with matching subject
        mock_jwks_validator.validate_id_token.return_value = {"sub": "auth0|12345"}

        with patch("httpx.AsyncClient") as mock_http_client:
            mock_http_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            updated_session = await handler.refresh_tokens(
                session_id="test_session",
                ip_address="192.168.1.1",
                user_agent="Mozilla/5.0",
            )

            # Verify ID token updated
            assert updated_session.id_token == "new_id_token"

    @pytest.mark.asyncio
    async def test_refresh_tokens_id_token_subject_mismatch(
        self,
        oauth2_config: OAuth2Config,
        mock_session_store: Mock,
        mock_state_store: Mock,
        mock_jwks_validator: Mock,
        sample_session_data: SessionData,
    ):
        """Test refresh fails when new ID token subject doesn't match session."""
        handler = OAuth2FlowHandler(
            config=oauth2_config,
            session_store=mock_session_store,
            state_store=mock_state_store,
            jwks_validator=mock_jwks_validator,
            db_pool=None,
        )

        mock_session_store.get_session.return_value = sample_session_data

        new_tokens = {
            "access_token": "new_access_token",
            "refresh_token": "new_refresh_token",
            "id_token": "new_id_token",
            "expires_in": 3600,
        }
        mock_response = Mock()
        mock_response.json.return_value = new_tokens
        mock_response.raise_for_status = Mock()

        # Mock ID token validation with different subject
        mock_jwks_validator.validate_id_token.return_value = {"sub": "auth0|different"}

        with patch("httpx.AsyncClient") as mock_http_client:
            mock_http_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            with pytest.raises(ValueError, match="ID token subject mismatch"):
                await handler.refresh_tokens(
                    session_id="test_session",
                    ip_address="192.168.1.1",
                    user_agent="Mozilla/5.0",
                )

            # Verify session deleted
            assert mock_session_store.delete_session.called
            assert mock_session_store.delete_session.call_args[0][0] == "test_session"

    @pytest.mark.asyncio
    async def test_refresh_tokens_id_token_validation_fails(
        self,
        oauth2_config: OAuth2Config,
        mock_session_store: Mock,
        mock_state_store: Mock,
        mock_jwks_validator: Mock,
        sample_session_data: SessionData,
    ):
        """Test refresh fails when new ID token validation fails."""
        handler = OAuth2FlowHandler(
            config=oauth2_config,
            session_store=mock_session_store,
            state_store=mock_state_store,
            jwks_validator=mock_jwks_validator,
            db_pool=None,
        )

        mock_session_store.get_session.return_value = sample_session_data

        new_tokens = {
            "access_token": "new_access_token",
            "refresh_token": "new_refresh_token",
            "id_token": "new_id_token",
            "expires_in": 3600,
        }
        mock_response = Mock()
        mock_response.json.return_value = new_tokens
        mock_response.raise_for_status = Mock()

        # Mock ID token validation failure
        mock_jwks_validator.validate_id_token.side_effect = jwt.InvalidTokenError(
            "Invalid signature"
        )

        with patch("httpx.AsyncClient") as mock_http_client:
            mock_http_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            with pytest.raises(ValueError, match="ID token validation failed"):
                await handler.refresh_tokens(
                    session_id="test_session",
                    ip_address="192.168.1.1",
                    user_agent="Mozilla/5.0",
                )

            # Verify session deleted
            mock_session_store.delete_session.assert_called_once_with("test_session")

    @pytest.mark.asyncio
    async def test_refresh_tokens_session_version_mismatch(
        self,
        oauth2_handler: OAuth2FlowHandler,
        mock_session_store: Mock,
        mock_db_pool: Mock,
        sample_session_data: SessionData,
    ):
        """Test refresh fails when session version doesn't match database."""
        mock_session_store.get_session.return_value = sample_session_data

        with patch(
            "libs.platform.web_console_auth.oauth2_flow.validate_session_version"
        ) as mock_validate:
            mock_validate.return_value = False  # Version mismatch

            with pytest.raises(ValueError, match="Session invalidated"):
                await oauth2_handler.refresh_tokens(
                    session_id="test_session",
                    ip_address="192.168.1.1",
                    user_agent="Mozilla/5.0",
                    db_pool=mock_db_pool,
                )

            # Verify session deleted
            mock_session_store.delete_session.assert_called_once_with("test_session")


class TestHandleLogout:
    """Tests for handle_logout() method."""

    @pytest.mark.asyncio
    async def test_handle_logout_success(
        self,
        oauth2_handler: OAuth2FlowHandler,
        mock_session_store: Mock,
        sample_session_data: SessionData,
    ):
        """Test successful logout with token revocation."""
        mock_session_store.get_session.return_value = sample_session_data

        # Mock HTTP revocation
        mock_response = Mock()
        mock_response.raise_for_status = Mock()

        with patch("httpx.AsyncClient") as mock_http_client:
            mock_http_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            logout_url = await oauth2_handler.handle_logout(
                session_id="test_session",
                current_ip="192.168.1.1",
                current_user_agent="Mozilla/5.0",
            )

            # Verify session retrieved with binding
            mock_session_store.get_session.assert_called_once_with(
                "test_session",
                current_ip="192.168.1.1",
                current_user_agent="Mozilla/5.0",
                update_activity=False,
            )

            # Verify session deleted
            mock_session_store.delete_session.assert_called_once_with("test_session")

            # Verify logout URL
            assert logout_url.startswith("https://test.auth0.com/v2/logout?")
            assert "client_id=test_client_id" in logout_url
            assert "returnTo=https%3A%2F%2Fexample.com%2F" in logout_url

    @pytest.mark.asyncio
    async def test_handle_logout_binding_failed(
        self,
        oauth2_handler: OAuth2FlowHandler,
        mock_session_store: Mock,
    ):
        """Test logout with binding failure deletes session but doesn't revoke token."""
        mock_session_store.get_session.return_value = None  # Binding failed

        logout_url = await oauth2_handler.handle_logout(
            session_id="test_session",
            current_ip="192.168.1.1",
            current_user_agent="Mozilla/5.0",
        )

        # Verify session deleted locally
        mock_session_store.delete_session.assert_called_once_with("test_session")

        # Verify logout URL still returned
        assert logout_url.startswith("https://test.auth0.com/v2/logout?")

    @pytest.mark.asyncio
    async def test_handle_logout_revocation_http_error(
        self,
        oauth2_handler: OAuth2FlowHandler,
        mock_session_store: Mock,
        sample_session_data: SessionData,
    ):
        """Test logout continues when token revocation fails (non-critical)."""
        mock_session_store.get_session.return_value = sample_session_data

        mock_response = Mock()
        mock_response.status_code = 500

        with patch("httpx.AsyncClient") as mock_http_client:
            mock_http_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "Server Error", request=Mock(), response=mock_response
                )
            )

            # Should not raise (non-critical failure)
            logout_url = await oauth2_handler.handle_logout(
                session_id="test_session",
                current_ip="192.168.1.1",
                current_user_agent="Mozilla/5.0",
            )

            # Verify session still deleted
            mock_session_store.delete_session.assert_called_once_with("test_session")
            assert logout_url is not None

    @pytest.mark.asyncio
    async def test_handle_logout_revocation_network_error(
        self,
        oauth2_handler: OAuth2FlowHandler,
        mock_session_store: Mock,
        sample_session_data: SessionData,
    ):
        """Test logout continues when revocation has network error (non-critical)."""
        mock_session_store.get_session.return_value = sample_session_data

        with patch("httpx.AsyncClient") as mock_http_client:
            mock_http_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.RequestError("Connection timeout")
            )

            # Should not raise (non-critical failure)
            logout_url = await oauth2_handler.handle_logout(
                session_id="test_session",
                current_ip="192.168.1.1",
                current_user_agent="Mozilla/5.0",
            )

            # Verify session still deleted
            mock_session_store.delete_session.assert_called_once_with("test_session")
            assert logout_url is not None


class TestRevokeRefreshToken:
    """Tests for _revoke_refresh_token() internal method."""

    @pytest.mark.asyncio
    async def test_revoke_refresh_token_success(self, oauth2_handler: OAuth2FlowHandler):
        """Test successful refresh token revocation."""
        mock_response = Mock()
        mock_response.raise_for_status = Mock()

        with patch("httpx.AsyncClient") as mock_http_client:
            mock_http_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            # Should not raise
            await oauth2_handler._revoke_refresh_token("test_refresh_token")

    @pytest.mark.asyncio
    async def test_revoke_refresh_token_http_error(self, oauth2_handler: OAuth2FlowHandler):
        """Test revocation raises on HTTP error."""
        mock_response = Mock()
        mock_response.status_code = 401

        with patch("httpx.AsyncClient") as mock_http_client:
            mock_http_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "Unauthorized", request=Mock(), response=mock_response
                )
            )

            with pytest.raises(httpx.HTTPStatusError):
                await oauth2_handler._revoke_refresh_token("test_refresh_token")


class TestFetchUserRoleData:
    """Tests for _fetch_user_role_data() standalone function."""

    @pytest.mark.asyncio
    async def test_fetch_user_role_data_dict_row(self, mock_db_pool: Mock):
        """Test fetching user role data when row is dict."""
        mock_cursor = Mock()
        mock_cursor.fetchone = AsyncMock(return_value={"role": "admin", "session_version": 5})

        mock_conn = Mock()
        mock_conn.execute = AsyncMock(return_value=mock_cursor)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)

        with patch("libs.platform.web_console_auth.oauth2_flow.acquire_connection") as mock_acquire:
            mock_acquire.return_value = mock_conn

            result = await _fetch_user_role_data("auth0|12345", mock_db_pool)

            assert result == {"role": "admin", "session_version": 5}

    @pytest.mark.asyncio
    async def test_fetch_user_role_data_tuple_row(self, mock_db_pool: Mock):
        """Test fetching user role data when row is tuple."""
        mock_cursor = Mock()
        mock_cursor.fetchone = AsyncMock(return_value=("operator", 3))

        mock_conn = Mock()
        mock_conn.execute = AsyncMock(return_value=mock_cursor)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)

        with patch("libs.platform.web_console_auth.oauth2_flow.acquire_connection") as mock_acquire:
            mock_acquire.return_value = mock_conn

            result = await _fetch_user_role_data("auth0|12345", mock_db_pool)

            assert result == {"role": "operator", "session_version": 3}

    @pytest.mark.asyncio
    async def test_fetch_user_role_data_not_found(self, mock_db_pool: Mock):
        """Test fetching user role data returns None when user not found."""
        mock_cursor = Mock()
        mock_cursor.fetchone = AsyncMock(return_value=None)

        mock_conn = Mock()
        mock_conn.execute = AsyncMock(return_value=mock_cursor)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)

        with patch("libs.platform.web_console_auth.oauth2_flow.acquire_connection") as mock_acquire:
            mock_acquire.return_value = mock_conn

            result = await _fetch_user_role_data("auth0|nonexistent", mock_db_pool)

            assert result is None


class TestFetchUserStrategies:
    """Tests for _fetch_user_strategies() standalone function."""

    @pytest.mark.asyncio
    async def test_fetch_user_strategies_dict_rows(self, mock_db_pool: Mock):
        """Test fetching user strategies when rows are dicts."""
        mock_cursor = Mock()
        mock_cursor.fetchall = AsyncMock(
            return_value=[
                {"strategy_id": "strategy_1"},
                {"strategy_id": "strategy_2"},
                {"strategy_id": "strategy_3"},
            ]
        )

        mock_conn = Mock()
        mock_conn.execute = AsyncMock(return_value=mock_cursor)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)

        with patch("libs.platform.web_console_auth.oauth2_flow.acquire_connection") as mock_acquire:
            mock_acquire.return_value = mock_conn

            result = await _fetch_user_strategies("auth0|12345", mock_db_pool)

            assert result == ["strategy_1", "strategy_2", "strategy_3"]

    @pytest.mark.asyncio
    async def test_fetch_user_strategies_tuple_rows(self, mock_db_pool: Mock):
        """Test fetching user strategies when rows are tuples."""
        mock_cursor = Mock()
        mock_cursor.fetchall = AsyncMock(return_value=[("strat_a",), ("strat_b",)])

        mock_conn = Mock()
        mock_conn.execute = AsyncMock(return_value=mock_cursor)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)

        with patch("libs.platform.web_console_auth.oauth2_flow.acquire_connection") as mock_acquire:
            mock_acquire.return_value = mock_conn

            result = await _fetch_user_strategies("auth0|12345", mock_db_pool)

            assert result == ["strat_a", "strat_b"]

    @pytest.mark.asyncio
    async def test_fetch_user_strategies_empty(self, mock_db_pool: Mock):
        """Test fetching user strategies returns empty list when no strategies."""
        mock_cursor = Mock()
        mock_cursor.fetchall = AsyncMock(return_value=[])

        mock_conn = Mock()
        mock_conn.execute = AsyncMock(return_value=mock_cursor)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)

        with patch("libs.platform.web_console_auth.oauth2_flow.acquire_connection") as mock_acquire:
            mock_acquire.return_value = mock_conn

            result = await _fetch_user_strategies("auth0|12345", mock_db_pool)

            assert result == []
