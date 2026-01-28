"""Tests for apps/execution_gateway/api/dependencies.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import redis.asyncio as redis_async
from fastapi import HTTPException, Request
from psycopg_pool import AsyncConnectionPool

from apps.execution_gateway.api.dependencies import (
    _get_required_header,
    build_gateway_authenticator,
    error_detail,
    get_alpaca_executor,
    get_async_redis,
    get_audit_logger,
    get_database_url,
    get_db_client,
    get_db_pool,
    get_gateway_authenticator,
    get_jwks_validator,
    get_rate_limiter,
    get_sync_redis,
)


class TestErrorDetail:
    """Test error_detail() helper function."""

    def test_error_detail_without_retry_after(self) -> None:
        """Test error_detail returns dict with error, message, timestamp."""
        result = error_detail("invalid_token", "Token is invalid")

        assert result["error"] == "invalid_token"
        assert result["message"] == "Token is invalid"
        assert "timestamp" in result
        assert "retry_after" not in result

    def test_error_detail_with_retry_after(self) -> None:
        """Test error_detail includes retry_after when provided."""
        result = error_detail("rate_limited", "Too many requests", retry_after=60)

        assert result["error"] == "rate_limited"
        assert result["message"] == "Too many requests"
        assert result["retry_after"] == 60
        assert "timestamp" in result


class TestGetRequiredHeader:
    """Test _get_required_header() helper function."""

    def _make_request(self, headers: dict[str, str]) -> Request:
        """Create a mock Request with given headers."""
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 123),
            "scheme": "http",
        }
        return Request(scope)

    def test_get_required_header_success(self) -> None:
        """Test _get_required_header returns value when header present."""
        request = self._make_request({"X-User-ID": "user123"})
        result = _get_required_header(request, "X-User-ID", log_only=False)
        assert result == "user123"

    def test_get_required_header_missing_raises_exception(self) -> None:
        """Test _get_required_header raises HTTPException when header missing (enforce mode)."""
        request = self._make_request({})

        with pytest.raises(HTTPException) as exc_info:
            _get_required_header(request, "X-User-ID", log_only=False)

        assert exc_info.value.status_code == 400
        assert "X-User-ID" in str(exc_info.value.detail)

    def test_get_required_header_missing_log_only_returns_default(self) -> None:
        """Test _get_required_header returns default when header missing (log_only mode)."""
        request = self._make_request({})
        result = _get_required_header(
            request, "X-User-ID", log_only=True, default_value="anonymous"
        )
        assert result == "anonymous"

    def test_get_required_header_log_only_no_default(self) -> None:
        """Test _get_required_header returns empty string when no default provided."""
        request = self._make_request({})
        result = _get_required_header(request, "X-Request-ID", log_only=True)
        assert result == ""


class TestAuth0DomainParsing:
    """Test AUTH0_DOMAIN and AUTH0_ISSUER environment parsing."""

    def test_auth0_domain_parsing_from_env(self, monkeypatch) -> None:
        """Test AUTH0_DOMAIN is parsed correctly from environment."""
        # This tests lines 65-69
        # Set environment variable and reload module
        monkeypatch.setenv("AUTH0_DOMAIN", "example.auth0.com")

        # Re-import to trigger parsing (module-level code)
        import importlib

        import apps.execution_gateway.api.dependencies as dep_module

        importlib.reload(dep_module)

        # Verify domain and issuer were set correctly
        assert dep_module.AUTH0_DOMAIN == "example.auth0.com"
        assert dep_module.AUTH0_ISSUER == "https://example.auth0.com/"

    def test_auth0_domain_with_https_prefix(self, monkeypatch) -> None:
        """Test AUTH0_DOMAIN parsing handles https:// prefix."""
        monkeypatch.setenv("AUTH0_DOMAIN", "https://example.auth0.com")

        import importlib

        import apps.execution_gateway.api.dependencies as dep_module

        importlib.reload(dep_module)

        assert dep_module.AUTH0_DOMAIN == "example.auth0.com"
        assert dep_module.AUTH0_ISSUER == "https://example.auth0.com/"


class TestDatabaseDependencies:
    """Test database-related dependency functions."""

    @patch("apps.execution_gateway.api.dependencies.get_required_secret")
    def test_get_database_url(self, mock_get_secret) -> None:
        """Test get_database_url() calls get_required_secret."""
        # Clear lru_cache
        get_database_url.cache_clear()

        mock_get_secret.return_value = "postgresql://localhost/testdb"

        result = get_database_url()

        assert result == "postgresql://localhost/testdb"
        mock_get_secret.assert_called_once_with("database/url")

    @patch("apps.execution_gateway.api.dependencies.get_database_url")
    def test_get_db_pool(self, mock_get_db_url) -> None:
        """Test get_db_pool() creates AsyncConnectionPool."""
        # Clear lru_cache
        get_db_pool.cache_clear()

        mock_get_db_url.return_value = "postgresql://localhost/testdb"

        result = get_db_pool()

        assert isinstance(result, AsyncConnectionPool)
        # Pool is created with open=False (lazy initialization)
        assert result.closed

    @patch("apps.execution_gateway.api.dependencies.get_database_url")
    def test_get_db_client(self, mock_get_db_url) -> None:
        """Test get_db_client() creates DatabaseClient."""
        # Clear lru_cache
        get_db_client.cache_clear()

        mock_get_db_url.return_value = "postgresql://localhost/testdb"

        result = get_db_client()

        # Verify DatabaseClient was created (check it has expected attributes)
        assert hasattr(result, "db_conn_string")


class TestRedisDependencies:
    """Test Redis-related dependency functions."""

    @patch("apps.execution_gateway.api.dependencies.create_async_redis")
    def test_get_async_redis(self, mock_create_async) -> None:
        """Test get_async_redis() creates async Redis client."""
        # Clear lru_cache
        get_async_redis.cache_clear()

        mock_redis = MagicMock(spec=redis_async.Redis)
        mock_create_async.return_value = mock_redis

        result = get_async_redis()

        assert result is mock_redis
        mock_create_async.assert_called_once()

    @patch("apps.execution_gateway.api.dependencies.create_sync_redis")
    def test_get_sync_redis(self, mock_create_sync) -> None:
        """Test get_sync_redis() creates sync Redis client."""
        # Clear lru_cache
        get_sync_redis.cache_clear()

        mock_redis = MagicMock()
        mock_create_sync.return_value = mock_redis

        result = get_sync_redis()

        assert result is mock_redis
        mock_create_sync.assert_called_once()


class TestRateLimiterDependency:
    """Test rate limiter dependency."""

    def test_get_rate_limiter_creates_singleton(self) -> None:
        """Test get_rate_limiter() creates RateLimiter singleton."""
        # Reset global state
        import apps.execution_gateway.api.dependencies as dep_module

        dep_module._rate_limiter = None

        mock_redis = MagicMock(spec=redis_async.Redis)

        result = get_rate_limiter(redis_client=mock_redis)

        # Verify RateLimiter was created
        assert result is not None
        assert hasattr(result, "check_rate_limit")

        # Calling again returns the same instance (singleton)
        result2 = get_rate_limiter(redis_client=mock_redis)
        assert result2 is result


class TestAuditLoggerDependency:
    """Test audit logger dependency."""

    def test_get_audit_logger_creates_singleton(self) -> None:
        """Test get_audit_logger() creates AuditLogger singleton."""
        # Reset global state
        import apps.execution_gateway.api.dependencies as dep_module

        dep_module._audit_logger = None

        mock_pool = MagicMock(spec=AsyncConnectionPool)

        result = get_audit_logger(db_pool=mock_pool)

        # Verify AuditLogger was created
        assert result is not None
        assert hasattr(result, "log_action")

        # Calling again returns the same instance (singleton)
        result2 = get_audit_logger(db_pool=mock_pool)
        assert result2 is result


class TestGatewayAuthenticatorDependency:
    """Test gateway authenticator dependency."""

    @patch("apps.execution_gateway.api.dependencies.GatewayAuthenticator")
    @patch("apps.execution_gateway.api.dependencies.JWTManager")
    @patch("apps.execution_gateway.api.dependencies.get_db_pool")
    @patch("apps.execution_gateway.api.dependencies.get_async_redis")
    @patch("apps.execution_gateway.api.dependencies.get_sync_redis")
    @patch("apps.execution_gateway.api.dependencies.AuthConfig")
    def test_build_gateway_authenticator(
        self,
        mock_auth_config_cls,
        mock_get_sync_redis,
        mock_get_async_redis,
        mock_get_db_pool,
        mock_jwt_manager_cls,
        mock_gateway_auth_cls,
    ) -> None:
        """Test build_gateway_authenticator() creates GatewayAuthenticator."""
        # Reset global state
        import apps.execution_gateway.api.dependencies as dep_module

        dep_module._gateway_authenticator = None

        # Setup mocks
        mock_config = MagicMock()
        mock_auth_config_cls.from_env.return_value = mock_config
        mock_sync_redis = MagicMock()
        mock_get_sync_redis.return_value = mock_sync_redis
        mock_async_redis = MagicMock()
        mock_get_async_redis.return_value = mock_async_redis
        mock_pool = MagicMock()
        mock_get_db_pool.return_value = mock_pool
        mock_jwt_manager = MagicMock()
        mock_jwt_manager_cls.return_value = mock_jwt_manager
        mock_gateway_auth = MagicMock()
        mock_gateway_auth_cls.return_value = mock_gateway_auth

        result = build_gateway_authenticator()

        # Verify GatewayAuthenticator was created
        assert result is mock_gateway_auth
        mock_gateway_auth_cls.assert_called_once()

        # Calling again returns the same instance (singleton)
        result2 = build_gateway_authenticator()
        assert result2 is mock_gateway_auth

    @patch("apps.execution_gateway.api.dependencies.build_gateway_authenticator")
    def test_get_gateway_authenticator_delegates_to_builder(
        self, mock_build_gateway_authenticator
    ) -> None:
        """Test get_gateway_authenticator() delegates to build_gateway_authenticator()."""
        mock_authenticator = MagicMock()
        mock_build_gateway_authenticator.return_value = mock_authenticator

        mock_pool = MagicMock()
        mock_redis = MagicMock()

        result = get_gateway_authenticator(db_pool=mock_pool, redis_client=mock_redis)

        assert result is mock_authenticator
        mock_build_gateway_authenticator.assert_called_once()


class TestJWKSValidatorDependency:
    """Test JWKS validator dependency."""

    def test_get_jwks_validator_returns_none_when_auth0_not_configured(self, monkeypatch) -> None:
        """Test get_jwks_validator() returns None when Auth0 is not configured."""
        # Clear lru_cache
        get_jwks_validator.cache_clear()

        # Unset Auth0 configuration
        monkeypatch.setenv("AUTH0_DOMAIN", "")
        monkeypatch.setenv("AUTH0_CLIENT_ID", "")

        # Re-import to trigger environment parsing
        import importlib

        import apps.execution_gateway.api.dependencies as dep_module

        importlib.reload(dep_module)

        result = dep_module.get_jwks_validator()

        assert result is None

    def test_get_jwks_validator_creates_validator_when_auth0_configured(self, monkeypatch) -> None:
        """Test get_jwks_validator() creates JWKSValidator when Auth0 is configured."""
        # Set Auth0 configuration
        monkeypatch.setenv("AUTH0_DOMAIN", "example.auth0.com")
        monkeypatch.setenv("AUTH0_CLIENT_ID", "test_client_id")

        # Re-import to trigger environment parsing
        import importlib

        import apps.execution_gateway.api.dependencies as dep_module

        importlib.reload(dep_module)

        # Clear cache
        dep_module.get_jwks_validator.cache_clear()

        result = dep_module.get_jwks_validator()

        # Verify JWKSValidator was created
        assert result is not None
        assert hasattr(result, "validate_id_token")


class TestAlpacaExecutorDependency:
    """Test Alpaca executor dependency."""

    def test_get_alpaca_executor_returns_none_in_dry_run(self, monkeypatch) -> None:
        """Test get_alpaca_executor() returns None when DRY_RUN=true."""
        # Clear lru_cache
        get_alpaca_executor.cache_clear()

        monkeypatch.setenv("DRY_RUN", "true")

        # Re-import to trigger environment parsing
        import importlib

        import apps.execution_gateway.api.dependencies as dep_module

        importlib.reload(dep_module)

        result = dep_module.get_alpaca_executor()

        assert result is None

    def test_get_alpaca_executor_returns_none_when_secrets_missing(self, monkeypatch) -> None:
        """Test get_alpaca_executor() returns None when secrets are not configured."""
        monkeypatch.setenv("DRY_RUN", "false")

        # Re-import to get DRY_RUN=false
        import importlib

        import apps.execution_gateway.api.dependencies as dep_module

        importlib.reload(dep_module)

        # Clear lru_cache AFTER reload
        dep_module.get_alpaca_executor.cache_clear()

        # Patch get_required_secret on the reloaded module
        with patch.object(dep_module, "get_required_secret") as mock_get_secret:
            mock_get_secret.side_effect = RuntimeError("Secret not found")
            result = dep_module.get_alpaca_executor()

        assert result is None

    def test_get_alpaca_executor_handles_value_error(self, monkeypatch) -> None:
        """Test get_alpaca_executor() returns None on ValueError during initialization."""
        monkeypatch.setenv("DRY_RUN", "false")

        # Re-import to get DRY_RUN=false
        import importlib

        import apps.execution_gateway.api.dependencies as dep_module

        importlib.reload(dep_module)

        # Clear lru_cache AFTER reload
        dep_module.get_alpaca_executor.cache_clear()

        # Patch both dependencies on the reloaded module
        with (
            patch.object(dep_module, "get_required_secret") as mock_get_secret,
            patch.object(dep_module, "AlpacaExecutor") as mock_alpaca_cls,
        ):
            mock_get_secret.side_effect = ["test_api_key", "test_secret_key"]
            mock_alpaca_cls.side_effect = ValueError("Invalid credentials")
            result = dep_module.get_alpaca_executor()

        assert result is None

    def test_get_alpaca_executor_handles_connection_error(self, monkeypatch) -> None:
        """Test get_alpaca_executor() returns None on ConnectionError."""
        monkeypatch.setenv("DRY_RUN", "false")

        # Re-import to get DRY_RUN=false
        import importlib

        import apps.execution_gateway.api.dependencies as dep_module

        importlib.reload(dep_module)

        # Clear lru_cache AFTER reload
        dep_module.get_alpaca_executor.cache_clear()

        # Patch both dependencies on the reloaded module
        with (
            patch.object(dep_module, "get_required_secret") as mock_get_secret,
            patch.object(dep_module, "AlpacaExecutor") as mock_alpaca_cls,
        ):
            mock_get_secret.side_effect = ["test_api_key", "test_secret_key"]
            mock_alpaca_cls.side_effect = ConnectionError("Connection failed")
            result = dep_module.get_alpaca_executor()

        assert result is None

    def test_get_alpaca_executor_returns_none_when_credentials_empty(self, monkeypatch) -> None:
        """Test get_alpaca_executor() returns None when credentials are empty strings."""
        monkeypatch.setenv("DRY_RUN", "false")

        # Re-import to get DRY_RUN=false
        import importlib

        import apps.execution_gateway.api.dependencies as dep_module

        importlib.reload(dep_module)

        # Clear lru_cache AFTER reload
        dep_module.get_alpaca_executor.cache_clear()

        # Patch dependencies on the reloaded module
        with (
            patch.object(dep_module, "get_required_secret") as mock_get_secret,
            patch.object(dep_module, "AlpacaExecutor") as mock_alpaca_cls,
        ):
            # Return empty strings for credentials
            mock_get_secret.side_effect = ["", ""]
            result = dep_module.get_alpaca_executor()

        assert result is None
        # Should not have called AlpacaExecutor constructor
        mock_alpaca_cls.assert_not_called()


class TestGetAuthenticatedUser:
    """Test get_authenticated_user() async function."""

    def _make_request(self, headers: dict[str, str]) -> Request:
        """Create a mock Request with given headers."""
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 123),
            "scheme": "http",
        }
        return Request(scope)

    @pytest.mark.asyncio()
    async def test_missing_auth_header_raises_401(self, monkeypatch) -> None:
        """Test 401 when Authorization header is missing."""
        monkeypatch.setenv("API_AUTH_MODE", "enforce")

        from apps.execution_gateway.api.dependencies import get_authenticated_user

        request = self._make_request({})
        mock_authenticator = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_user(request, mock_authenticator)

        assert exc_info.value.status_code == 401
        assert "Authorization required" in str(exc_info.value.detail)

    @pytest.mark.asyncio()
    async def test_invalid_bearer_format_raises_401(self, monkeypatch) -> None:
        """Test 401 when Authorization header doesn't start with Bearer."""
        monkeypatch.setenv("API_AUTH_MODE", "enforce")

        from apps.execution_gateway.api.dependencies import get_authenticated_user

        request = self._make_request({"Authorization": "Basic token123"})
        mock_authenticator = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_user(request, mock_authenticator)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio()
    async def test_invalid_request_id_raises_400(self, monkeypatch) -> None:
        """Test 400 when X-Request-ID is not a valid UUID."""
        monkeypatch.setenv("API_AUTH_MODE", "enforce")

        from apps.execution_gateway.api.dependencies import get_authenticated_user

        request = self._make_request(
            {
                "Authorization": "Bearer token123",
                "X-User-ID": "user1",
                "X-Request-ID": "not-a-uuid",
                "X-Session-Version": "1",
            }
        )
        mock_authenticator = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_user(request, mock_authenticator)

        assert exc_info.value.status_code == 400
        assert "UUID" in str(exc_info.value.detail)

    @pytest.mark.asyncio()
    async def test_invalid_session_version_raises_400(self, monkeypatch) -> None:
        """Test 400 when X-Session-Version is not an integer."""
        monkeypatch.setenv("API_AUTH_MODE", "enforce")

        import uuid

        from apps.execution_gateway.api.dependencies import get_authenticated_user

        request = self._make_request(
            {
                "Authorization": "Bearer token123",
                "X-User-ID": "user1",
                "X-Request-ID": str(uuid.uuid4()),
                "X-Session-Version": "not-an-int",
            }
        )
        mock_authenticator = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_user(request, mock_authenticator)

        assert exc_info.value.status_code == 400
        assert "integer" in str(exc_info.value.detail)

    @pytest.mark.asyncio()
    async def test_log_only_mode_returns_dev_user_on_missing_auth(self, monkeypatch) -> None:
        """Test log_only mode returns dev user when auth header missing."""
        monkeypatch.setenv("API_AUTH_MODE", "log_only")
        monkeypatch.setenv("DEBUG", "true")
        monkeypatch.setenv("DRY_RUN", "true")
        # Clear dev user environment variables to get default "dev" user
        monkeypatch.delenv("WEB_CONSOLE_DEV_USER_ID", raising=False)
        monkeypatch.delenv("WEB_CONSOLE_DEV_ROLE", raising=False)
        monkeypatch.delenv("WEB_CONSOLE_DEV_STRATEGIES", raising=False)

        import importlib

        import apps.execution_gateway.api.dependencies as dep_module

        importlib.reload(dep_module)

        request = self._make_request({})
        mock_authenticator = MagicMock()

        result = await dep_module.get_authenticated_user(request, mock_authenticator)

        assert result is not None
        assert result.user_id == "dev"

    @pytest.mark.asyncio()
    async def test_log_only_mode_falls_back_to_enforce_without_debug(self, monkeypatch) -> None:
        """Test log_only mode falls back to enforce when DEBUG=false."""
        monkeypatch.setenv("API_AUTH_MODE", "log_only")
        monkeypatch.setenv("DEBUG", "false")
        monkeypatch.setenv("DRY_RUN", "true")

        import importlib

        import apps.execution_gateway.api.dependencies as dep_module

        importlib.reload(dep_module)

        request = self._make_request({})
        mock_authenticator = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            await dep_module.get_authenticated_user(request, mock_authenticator)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio()
    async def test_successful_authentication(self, monkeypatch) -> None:
        """Test successful authentication with valid headers."""
        monkeypatch.setenv("API_AUTH_MODE", "enforce")

        import uuid

        from apps.execution_gateway.api.dependencies import get_authenticated_user
        from libs.platform.web_console_auth.gateway_auth import AuthenticatedUser
        from libs.platform.web_console_auth.permissions import Role

        request = self._make_request(
            {
                "Authorization": "Bearer valid_token",
                "X-User-ID": "user123",
                "X-Request-ID": str(uuid.uuid4()),
                "X-Session-Version": "1",
            }
        )

        expected_user = AuthenticatedUser(
            user_id="user123",
            role=Role.OPERATOR,
            strategies=["strat1"],
            session_version=1,
            request_id="req1",
        )

        mock_authenticator = MagicMock()
        mock_authenticator.authenticate = MagicMock(return_value=expected_user)

        # Make authenticate an async function
        async def mock_authenticate(**kwargs):
            return expected_user

        mock_authenticator.authenticate = mock_authenticate

        result = await get_authenticated_user(request, mock_authenticator)

        assert result.user_id == "user123"

    @pytest.mark.asyncio()
    async def test_auth_error_mapped_to_http_exception(self, monkeypatch) -> None:
        """Test AuthError exceptions are mapped to appropriate HTTP errors."""
        monkeypatch.setenv("API_AUTH_MODE", "enforce")

        import uuid

        from apps.execution_gateway.api.dependencies import get_authenticated_user
        from libs.platform.web_console_auth.exceptions import TokenExpiredError

        request = self._make_request(
            {
                "Authorization": "Bearer expired_token",
                "X-User-ID": "user123",
                "X-Request-ID": str(uuid.uuid4()),
                "X-Session-Version": "1",
            }
        )

        mock_authenticator = MagicMock()

        async def mock_authenticate(**kwargs):
            raise TokenExpiredError("Token expired")

        mock_authenticator.authenticate = mock_authenticate

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_user(request, mock_authenticator)

        assert exc_info.value.status_code == 401
        assert "token_expired" in str(exc_info.value.detail)


class TestBuildDevFallbackUser:
    """Test _build_dev_fallback_user() function."""

    def _make_request(self, headers: dict[str, str]) -> Request:
        """Create a mock Request with given headers."""
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 123),
            "scheme": "http",
        }
        return Request(scope)

    def test_default_dev_user(self, monkeypatch) -> None:
        """Test default dev user creation."""
        monkeypatch.delenv("WEB_CONSOLE_DEV_USER_ID", raising=False)
        monkeypatch.delenv("WEB_CONSOLE_DEV_ROLE", raising=False)
        monkeypatch.delenv("WEB_CONSOLE_DEV_STRATEGIES", raising=False)
        monkeypatch.delenv("STRATEGY_ID", raising=False)

        from apps.execution_gateway.api.dependencies import _build_dev_fallback_user

        request = self._make_request({})
        result = _build_dev_fallback_user(request)

        assert result.user_id == "dev"
        assert result.role.value == "operator"

    def test_custom_dev_user_from_env(self, monkeypatch) -> None:
        """Test dev user with custom environment variables."""
        monkeypatch.setenv("WEB_CONSOLE_DEV_USER_ID", "custom_dev")
        monkeypatch.setenv("WEB_CONSOLE_DEV_ROLE", "admin")
        monkeypatch.setenv("WEB_CONSOLE_DEV_STRATEGIES", "strat1,strat2")

        from apps.execution_gateway.api.dependencies import _build_dev_fallback_user

        request = self._make_request({})
        result = _build_dev_fallback_user(request)

        assert result.user_id == "custom_dev"
        assert result.role.value == "admin"
        assert result.strategies == ["strat1", "strat2"]

    def test_user_id_from_header(self, monkeypatch) -> None:
        """Test user ID is taken from X-User-ID header if present."""
        monkeypatch.delenv("WEB_CONSOLE_DEV_USER_ID", raising=False)

        from apps.execution_gateway.api.dependencies import _build_dev_fallback_user

        request = self._make_request({"X-User-ID": "header_user"})
        result = _build_dev_fallback_user(request)

        assert result.user_id == "header_user"

    def test_invalid_role_falls_back_to_operator(self, monkeypatch) -> None:
        """Test invalid role value falls back to operator."""
        monkeypatch.setenv("WEB_CONSOLE_DEV_ROLE", "invalid_role")

        from apps.execution_gateway.api.dependencies import _build_dev_fallback_user

        request = self._make_request({})
        result = _build_dev_fallback_user(request)

        assert result.role.value == "operator"


class TestCheckRateLimitWithFallback:
    """Test check_rate_limit_with_fallback() async function."""

    @pytest.mark.asyncio()
    async def test_successful_rate_limit_check(self) -> None:
        """Test successful rate limit check returns allowed, remaining, False."""
        from apps.execution_gateway.api.dependencies import check_rate_limit_with_fallback

        mock_limiter = MagicMock()

        async def mock_check(user_id, action, max_req, window):
            return True, 5

        mock_limiter.check_rate_limit = mock_check

        allowed, remaining, is_fallback = await check_rate_limit_with_fallback(
            mock_limiter, "user1", "test_action", 10, 60
        )

        assert allowed is True
        assert remaining == 5
        assert is_fallback is False

    @pytest.mark.asyncio()
    async def test_connection_error_fails_closed(self) -> None:
        """Test ConnectionError returns False, 0, True (fail-closed)."""
        import redis.exceptions

        from apps.execution_gateway.api.dependencies import check_rate_limit_with_fallback

        mock_limiter = MagicMock()

        async def mock_check(user_id, action, max_req, window):
            raise redis.exceptions.ConnectionError("Connection failed")

        mock_limiter.check_rate_limit = mock_check

        allowed, remaining, is_fallback = await check_rate_limit_with_fallback(
            mock_limiter, "user1", "test_action", 10, 60
        )

        assert allowed is False
        assert remaining == 0
        assert is_fallback is True

    @pytest.mark.asyncio()
    async def test_redis_error_fails_closed(self) -> None:
        """Test RedisError returns False, 0, True (fail-closed)."""
        import redis.exceptions

        from apps.execution_gateway.api.dependencies import check_rate_limit_with_fallback

        mock_limiter = MagicMock()

        async def mock_check(user_id, action, max_req, window):
            raise redis.exceptions.RedisError("Redis error")

        mock_limiter.check_rate_limit = mock_check

        allowed, remaining, is_fallback = await check_rate_limit_with_fallback(
            mock_limiter, "user1", "test_action", 10, 60
        )

        assert allowed is False
        assert remaining == 0
        assert is_fallback is True

    @pytest.mark.asyncio()
    async def test_value_error_fails_closed(self) -> None:
        """Test ValueError returns False, 0, True (fail-closed)."""
        from apps.execution_gateway.api.dependencies import check_rate_limit_with_fallback

        mock_limiter = MagicMock()

        async def mock_check(user_id, action, max_req, window):
            raise ValueError("Invalid data")

        mock_limiter.check_rate_limit = mock_check

        allowed, remaining, is_fallback = await check_rate_limit_with_fallback(
            mock_limiter, "user1", "test_action", 10, 60
        )

        assert allowed is False
        assert remaining == 0
        assert is_fallback is True

    @pytest.mark.asyncio()
    async def test_unexpected_error_fails_closed(self) -> None:
        """Test unexpected Exception returns False, 0, True (fail-closed)."""
        from apps.execution_gateway.api.dependencies import check_rate_limit_with_fallback

        mock_limiter = MagicMock()

        async def mock_check(user_id, action, max_req, window):
            raise RuntimeError("Unexpected error")

        mock_limiter.check_rate_limit = mock_check

        allowed, remaining, is_fallback = await check_rate_limit_with_fallback(
            mock_limiter, "user1", "test_action", 10, 60
        )

        assert allowed is False
        assert remaining == 0
        assert is_fallback is True


class TestVerify2faToken:
    """Test verify_2fa_token() async function."""

    @pytest.mark.asyncio()
    async def test_no_jwks_validator_returns_misconfigured(self) -> None:
        """Test returns mfa_misconfigured when no validator available."""
        from apps.execution_gateway.api.dependencies import verify_2fa_token

        # Mock get_jwks_validator to return None, simulating unconfigured Auth0
        with patch("apps.execution_gateway.api.dependencies.get_jwks_validator", return_value=None):
            valid, error, method = await verify_2fa_token("token", "user1", None)

        assert valid is False
        assert error == "mfa_misconfigured"

    @pytest.mark.asyncio()
    async def test_expired_token(self) -> None:
        """Test expired token returns token_expired error."""
        import jwt

        from apps.execution_gateway.api.dependencies import verify_2fa_token

        mock_validator = MagicMock()

        async def mock_validate(**kwargs):
            raise jwt.ExpiredSignatureError("Token expired")

        mock_validator.validate_id_token = mock_validate

        valid, error, method = await verify_2fa_token("token", "user1", mock_validator)

        assert valid is False
        assert error == "token_expired"

    @pytest.mark.asyncio()
    async def test_invalid_issuer(self) -> None:
        """Test invalid issuer returns invalid_issuer error."""
        import jwt

        from apps.execution_gateway.api.dependencies import verify_2fa_token

        mock_validator = MagicMock()

        async def mock_validate(**kwargs):
            raise jwt.InvalidIssuerError("Invalid issuer")

        mock_validator.validate_id_token = mock_validate

        valid, error, method = await verify_2fa_token("token", "user1", mock_validator)

        assert valid is False
        assert error == "invalid_issuer"

    @pytest.mark.asyncio()
    async def test_invalid_audience(self) -> None:
        """Test invalid audience returns invalid_audience error."""
        import jwt

        from apps.execution_gateway.api.dependencies import verify_2fa_token

        mock_validator = MagicMock()

        async def mock_validate(**kwargs):
            raise jwt.InvalidAudienceError("Invalid audience")

        mock_validator.validate_id_token = mock_validate

        valid, error, method = await verify_2fa_token("token", "user1", mock_validator)

        assert valid is False
        assert error == "invalid_audience"

    @pytest.mark.asyncio()
    async def test_subject_mismatch(self) -> None:
        """Test subject mismatch returns token_mismatch error."""
        from datetime import UTC, datetime

        from apps.execution_gateway.api.dependencies import verify_2fa_token

        mock_validator = MagicMock()

        async def mock_validate(**kwargs):
            return {
                "sub": "different_user",
                "amr": ["mfa"],
                "auth_time": int(datetime.now(UTC).timestamp()),
            }

        mock_validator.validate_id_token = mock_validate

        valid, error, method = await verify_2fa_token("token", "user1", mock_validator)

        assert valid is False
        assert error == "token_mismatch"

    @pytest.mark.asyncio()
    async def test_missing_mfa_method(self) -> None:
        """Test missing MFA method returns mfa_required error."""
        from datetime import UTC, datetime

        from apps.execution_gateway.api.dependencies import verify_2fa_token

        mock_validator = MagicMock()

        async def mock_validate(**kwargs):
            return {"sub": "user1", "amr": [], "auth_time": int(datetime.now(UTC).timestamp())}

        mock_validator.validate_id_token = mock_validate

        valid, error, method = await verify_2fa_token("token", "user1", mock_validator)

        assert valid is False
        assert error == "mfa_required"

    @pytest.mark.asyncio()
    async def test_successful_2fa_validation(self) -> None:
        """Test successful 2FA validation returns True with method."""
        from datetime import UTC, datetime

        from apps.execution_gateway.api.dependencies import verify_2fa_token

        mock_validator = MagicMock()

        async def mock_validate(**kwargs):
            return {
                "sub": "user1",
                "amr": ["otp"],
                "auth_time": int(datetime.now(UTC).timestamp()),
            }

        mock_validator.validate_id_token = mock_validate

        valid, error, method = await verify_2fa_token("token", "user1", mock_validator)

        assert valid is True
        assert error is None
        assert method == "otp"


class TestEnsurePermission:
    """Test ensure_permission() function."""

    def test_permission_granted_no_exception(self) -> None:
        """Test no exception when user has permission."""
        from apps.execution_gateway.api.dependencies import ensure_permission
        from libs.platform.web_console_auth.gateway_auth import AuthenticatedUser
        from libs.platform.web_console_auth.permissions import Permission, Role

        user = AuthenticatedUser(
            user_id="admin1",
            role=Role.ADMIN,
            strategies=["strat1"],
            session_version=1,
            request_id="req1",
        )

        # Should not raise
        ensure_permission(user, Permission.VIEW_POSITIONS)

    def test_permission_denied_raises_403(self) -> None:
        """Test 403 when user lacks permission."""
        from apps.execution_gateway.api.dependencies import ensure_permission
        from libs.platform.web_console_auth.gateway_auth import AuthenticatedUser
        from libs.platform.web_console_auth.permissions import Permission, Role

        user = AuthenticatedUser(
            user_id="viewer1",
            role=Role.VIEWER,
            strategies=["strat1"],
            session_version=1,
            request_id="req1",
        )

        with pytest.raises(HTTPException) as exc_info:
            ensure_permission(user, Permission.MANAGE_USERS)

        assert exc_info.value.status_code == 403
        assert "permission_denied" in str(exc_info.value.detail)


class TestAdditionalCoverage:
    """Additional tests to increase coverage."""

    @pytest.mark.asyncio()
    async def test_verify_2fa_token_immature_signature(self) -> None:
        """Test immature signature returns token_not_yet_valid error."""
        import jwt

        from apps.execution_gateway.api.dependencies import verify_2fa_token

        mock_validator = MagicMock()

        async def mock_validate(**kwargs):
            raise jwt.ImmatureSignatureError("Token not yet valid")

        mock_validator.validate_id_token = mock_validate

        valid, error, method = await verify_2fa_token("token", "user1", mock_validator)

        assert valid is False
        assert error == "token_not_yet_valid"

    @pytest.mark.asyncio()
    async def test_verify_2fa_token_network_error(self) -> None:
        """Test network error returns mfa_unavailable."""
        import httpx

        from apps.execution_gateway.api.dependencies import verify_2fa_token

        mock_validator = MagicMock()

        async def mock_validate(**kwargs):
            raise httpx.RequestError("Network error")

        mock_validator.validate_id_token = mock_validate

        valid, error, method = await verify_2fa_token("token", "user1", mock_validator)

        assert valid is False
        assert error == "mfa_unavailable"

    @pytest.mark.asyncio()
    async def test_verify_2fa_token_invalid_jwt(self) -> None:
        """Test invalid JWT returns invalid_jwt error."""
        import jwt

        from apps.execution_gateway.api.dependencies import verify_2fa_token

        mock_validator = MagicMock()

        async def mock_validate(**kwargs):
            raise jwt.InvalidTokenError("Invalid token")

        mock_validator.validate_id_token = mock_validate

        valid, error, method = await verify_2fa_token("token", "user1", mock_validator)

        assert valid is False
        assert error == "invalid_jwt"

    @pytest.mark.asyncio()
    async def test_verify_2fa_token_missing_auth_time(self) -> None:
        """Test missing auth_time returns mfa_required error."""
        from apps.execution_gateway.api.dependencies import verify_2fa_token

        mock_validator = MagicMock()

        async def mock_validate(**kwargs):
            return {"sub": "user1", "amr": ["otp"]}  # Missing auth_time

        mock_validator.validate_id_token = mock_validate

        valid, error, method = await verify_2fa_token("token", "user1", mock_validator)

        assert valid is False
        assert error == "mfa_required"

    @pytest.mark.asyncio()
    async def test_verify_2fa_token_expired_mfa(self) -> None:
        """Test expired MFA (auth_time too old) returns mfa_expired error."""
        from datetime import UTC, datetime

        from apps.execution_gateway.api.dependencies import verify_2fa_token

        mock_validator = MagicMock()
        old_time = int(datetime.now(UTC).timestamp()) - 120  # 2 minutes ago (exceeds 60s max)

        async def mock_validate(**kwargs):
            return {"sub": "user1", "amr": ["otp"], "auth_time": old_time}

        mock_validator.validate_id_token = mock_validate

        valid, error, method = await verify_2fa_token("token", "user1", mock_validator)

        assert valid is False
        assert error == "mfa_expired"

    @pytest.mark.asyncio()
    async def test_get_authenticated_user_unmapped_auth_error(self, monkeypatch) -> None:
        """Test unmapped AuthError returns generic 401."""
        monkeypatch.setenv("API_AUTH_MODE", "enforce")

        import uuid

        from apps.execution_gateway.api.dependencies import get_authenticated_user
        from libs.platform.web_console_auth.exceptions import AuthError

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [
                (b"authorization", b"Bearer token123"),
                (b"x-user-id", b"user123"),
                (b"x-request-id", str(uuid.uuid4()).encode()),
                (b"x-session-version", b"1"),
            ],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 123),
            "scheme": "http",
        }
        request = Request(scope)

        mock_authenticator = MagicMock()

        async def mock_authenticate(**kwargs):
            raise AuthError("Unmapped error")

        mock_authenticator.authenticate = mock_authenticate

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_user(request, mock_authenticator)

        assert exc_info.value.status_code == 401
        assert "invalid_token" in str(exc_info.value.detail)
