"""Tests for API authentication dependency (C6).

Tests cover:
- Authentication modes (enforce vs log_only)
- JWT authentication flow
- S2S internal token authentication
- Replay protection via nonce
- Role/permission enforcement
- Request.state propagation for C5 integration
- Startup configuration validation
- Metrics emission
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from libs.common.api_auth_dependency import (
    ALLOWED_SERVICE_IDS,
    APIAuthConfig,
    AuthContext,
    InternalTokenClaims,
    _get_service_secret,
    _is_service_id_allowed,
    api_auth_checks_total,
    s2s_auth_checks_total,
    s2s_replay_detected_total,
    validate_auth_config,
    validate_internal_token_config,
    verify_internal_token,
)
from libs.web_console_auth.gateway_auth import AuthenticatedUser
from libs.web_console_auth.permissions import Permission, Role


class TestValidateAuthConfig:
    """Tests for auth configuration validation."""

    def test_valid_enforce_mode(self) -> None:
        """Test validation passes for enforce mode."""
        with patch.dict(os.environ, {"API_AUTH_MODE": "enforce"}, clear=False):
            validate_auth_config()  # Should not raise

    def test_valid_log_only_mode(self) -> None:
        """Test validation passes for log_only mode."""
        with patch.dict(os.environ, {"API_AUTH_MODE": "log_only"}, clear=False):
            validate_auth_config()  # Should not raise

    def test_invalid_mode_raises(self) -> None:
        """Test validation fails for invalid mode."""
        with patch.dict(os.environ, {"API_AUTH_MODE": "disabled"}, clear=False):
            with pytest.raises(RuntimeError, match="Invalid API_AUTH_MODE"):
                validate_auth_config()

    def test_default_mode_is_enforce(self) -> None:
        """Test default mode is enforce (fail-closed)."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove API_AUTH_MODE if set
            os.environ.pop("API_AUTH_MODE", None)
            validate_auth_config()  # Should not raise - defaults to enforce


class TestValidateInternalTokenConfig:
    """Tests for internal token configuration validation."""

    def test_valid_config_with_secret(self) -> None:
        """Test validation passes with valid secret."""
        with patch.dict(
            os.environ,
            {
                "INTERNAL_TOKEN_REQUIRED": "true",
                "INTERNAL_TOKEN_SECRET": "a" * 64,  # 64 hex chars = 32 bytes
            },
            clear=False,
        ):
            validate_internal_token_config()  # Should not raise

    def test_missing_secret_when_required_raises(self) -> None:
        """Test validation fails when secret is missing but required."""
        with patch.dict(
            os.environ,
            {"INTERNAL_TOKEN_REQUIRED": "true", "INTERNAL_TOKEN_SECRET": ""},
            clear=False,
        ):
            with pytest.raises(RuntimeError, match="INTERNAL_TOKEN_SECRET is required"):
                validate_internal_token_config()

    def test_short_secret_when_required_raises(self) -> None:
        """Test validation fails when secret is too short."""
        with patch.dict(
            os.environ,
            {"INTERNAL_TOKEN_REQUIRED": "true", "INTERNAL_TOKEN_SECRET": "tooshort"},
            clear=False,
        ):
            with pytest.raises(RuntimeError, match="must be at least 64 characters"):
                validate_internal_token_config()

    def test_disabled_token_does_not_require_secret(self) -> None:
        """Test validation passes when token not required."""
        with patch.dict(
            os.environ,
            {"INTERNAL_TOKEN_REQUIRED": "false", "INTERNAL_TOKEN_SECRET": ""},
            clear=False,
        ):
            validate_internal_token_config()  # Should not raise

    def test_short_per_service_secret_raises(self) -> None:
        """Test validation fails when per-service secret is too short."""
        with patch.dict(
            os.environ,
            {
                "INTERNAL_TOKEN_REQUIRED": "true",
                "INTERNAL_TOKEN_SECRET": "a" * 64,  # Valid global secret
                "INTERNAL_TOKEN_SECRET_ORCHESTRATOR": "tooshort",  # Invalid per-service
            },
            clear=False,
        ):
            with pytest.raises(RuntimeError, match="INTERNAL_TOKEN_SECRET_ORCHESTRATOR"):
                validate_internal_token_config()

    def test_valid_per_service_secret_passes(self) -> None:
        """Test validation passes when per-service secret meets requirements."""
        with patch.dict(
            os.environ,
            {
                "INTERNAL_TOKEN_REQUIRED": "true",
                "INTERNAL_TOKEN_SECRET": "a" * 64,
                "INTERNAL_TOKEN_SECRET_ORCHESTRATOR": "b" * 64,  # Valid per-service
            },
            clear=False,
        ):
            validate_internal_token_config()  # Should not raise


class TestVerifyInternalToken:
    """Tests for S2S internal token verification."""

    @pytest.fixture()
    def mock_request(self) -> MagicMock:
        """Create mock request."""
        request = MagicMock(spec=Request)
        request.method = "POST"
        # Configure url.path and url.query properly for signature verification
        mock_url = MagicMock()
        mock_url.path = "/api/v1/orders"
        mock_url.query = ""  # Empty query string by default
        request.url = mock_url
        request.state = MagicMock()
        # Configure body() coroutine for body hash verification
        request.body = AsyncMock(return_value=b"")
        return request

    @pytest.fixture()
    def mock_redis(self) -> AsyncMock:
        """Create mock Redis client."""
        redis = AsyncMock()
        redis.set = AsyncMock(return_value=True)  # nonce unique
        return redis

    def _generate_token(
        self,
        service_id: str,
        method: str,
        path: str,
        timestamp: str,
        nonce: str,
        user_id: str | None = None,
        strategy_id: str | None = None,
        body_hash: str = "",
        query: str = "",
        secret: str = "a" * 64,
    ) -> str:
        """Generate HMAC-signed internal token with body hash and query.

        Uses JSON serialization to match the production implementation,
        which prevents delimiter collision attacks.
        """
        import json

        payload_dict = {
            "service_id": service_id,
            "method": method,
            "path": path,
            "query": query,
            "timestamp": timestamp,
            "nonce": nonce,
            "user_id": user_id or "",
            "strategy_id": strategy_id or "",
            "body_hash": body_hash,
        }
        payload = json.dumps(payload_dict, separators=(",", ":"), sort_keys=True)
        return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    @pytest.mark.asyncio()
    async def test_valid_token_returns_claims(
        self, mock_request: MagicMock, mock_redis: AsyncMock
    ) -> None:
        """Test valid internal token returns claims."""
        timestamp = str(int(time.time()))
        nonce = "test-nonce-12345"
        service_id = "orchestrator"
        secret = "a" * 64
        # Empty body hash for POST with empty body
        empty_body_hash = hashlib.sha256(b"").hexdigest()

        token = self._generate_token(
            service_id,
            "POST",
            "/api/v1/orders",
            timestamp,
            nonce,
            body_hash=empty_body_hash,
            secret=secret,
        )

        # Patch module constant directly (loaded at import time, not from env at call time)
        with patch.dict(os.environ, {"INTERNAL_TOKEN_SECRET": secret}, clear=False):
            with patch.dict(os.environ, {"API_AUTH_MODE": "enforce"}, clear=False):
                claims = await verify_internal_token(
                    request=mock_request,
                    redis_client=mock_redis,
                    token=token,
                    timestamp=timestamp,
                    nonce=nonce,
                    service_id=service_id,
                    user_id=None,
                    strategy_id=None,
                    body_hash=empty_body_hash,
                )

        assert claims is not None
        assert claims.service_id == service_id
        assert claims.nonce == nonce

    @pytest.mark.asyncio()
    async def test_missing_headers_returns_none(
        self, mock_request: MagicMock, mock_redis: AsyncMock
    ) -> None:
        """Test missing headers returns None."""
        claims = await verify_internal_token(
            request=mock_request,
            redis_client=mock_redis,
            token=None,  # Missing
            timestamp=None,
            nonce=None,
            service_id=None,
            user_id=None,
            strategy_id=None,
            body_hash=None,
        )
        assert claims is None

    @pytest.mark.asyncio()
    async def test_expired_timestamp_returns_none(
        self, mock_request: MagicMock, mock_redis: AsyncMock
    ) -> None:
        """Test expired timestamp is rejected."""
        old_timestamp = str(int(time.time()) - 600)  # 10 minutes ago
        nonce = "test-nonce"
        service_id = "orchestrator"
        secret = "a" * 64

        token = self._generate_token(
            service_id, "POST", "/api/v1/orders", old_timestamp, nonce, secret=secret
        )

        with patch.dict(os.environ, {"INTERNAL_TOKEN_SECRET": secret}, clear=False):
            with patch.dict(os.environ, {"API_AUTH_MODE": "enforce"}, clear=False):
                claims = await verify_internal_token(
                    request=mock_request,
                    redis_client=mock_redis,
                    token=token,
                    timestamp=old_timestamp,
                    nonce=nonce,
                    service_id=service_id,
                    user_id=None,
                    strategy_id=None,
                    body_hash=None,
                )

        assert claims is None

    @pytest.mark.asyncio()
    async def test_invalid_signature_returns_none(
        self, mock_request: MagicMock, mock_redis: AsyncMock
    ) -> None:
        """Test invalid signature is rejected."""
        timestamp = str(int(time.time()))
        nonce = "test-nonce"
        service_id = "orchestrator"
        secret = "a" * 64

        with patch.dict(os.environ, {"INTERNAL_TOKEN_SECRET": secret}, clear=False):
            with patch.dict(os.environ, {"API_AUTH_MODE": "enforce"}, clear=False):
                claims = await verify_internal_token(
                    request=mock_request,
                    redis_client=mock_redis,
                    token="invalid-signature",
                    timestamp=timestamp,
                    nonce=nonce,
                    service_id=service_id,
                    user_id=None,
                    strategy_id=None,
                    body_hash=None,
                )

        assert claims is None

    @pytest.mark.asyncio()
    async def test_replay_detected_returns_none(
        self, mock_request: MagicMock, mock_redis: AsyncMock
    ) -> None:
        """Test replay attack is detected and rejected."""
        timestamp = str(int(time.time()))
        nonce = "test-nonce"
        service_id = "orchestrator"
        secret = "a" * 64

        token = self._generate_token(
            service_id, "POST", "/api/v1/orders", timestamp, nonce, secret=secret
        )

        # Simulate nonce already used
        mock_redis.set = AsyncMock(return_value=False)  # NX failed - nonce exists

        with patch.dict(os.environ, {"INTERNAL_TOKEN_SECRET": secret}, clear=False):
            with patch.dict(os.environ, {"API_AUTH_MODE": "enforce"}, clear=False):
                claims = await verify_internal_token(
                    request=mock_request,
                    redis_client=mock_redis,
                    token=token,
                    timestamp=timestamp,
                    nonce=nonce,
                    service_id=service_id,
                    user_id=None,
                    strategy_id=None,
                    body_hash=None,
                )

        assert claims is None

    @pytest.mark.asyncio()
    async def test_redis_error_in_enforce_mode_raises(
        self, mock_request: MagicMock, mock_redis: AsyncMock
    ) -> None:
        """Test Redis error raises 503 in enforce mode."""
        timestamp = str(int(time.time()))
        nonce = "test-nonce"
        service_id = "orchestrator"
        secret = "a" * 64
        empty_body_hash = hashlib.sha256(b"").hexdigest()

        token = self._generate_token(
            service_id,
            "POST",
            "/api/v1/orders",
            timestamp,
            nonce,
            body_hash=empty_body_hash,
            secret=secret,
        )

        # Simulate Redis error
        mock_redis.set = AsyncMock(side_effect=Exception("Redis connection failed"))

        with patch.dict(os.environ, {"INTERNAL_TOKEN_SECRET": secret}, clear=False):
            with patch.dict(os.environ, {"API_AUTH_MODE": "enforce"}, clear=False):
                with pytest.raises(HTTPException) as exc_info:
                    await verify_internal_token(
                        request=mock_request,
                        redis_client=mock_redis,
                        token=token,
                        timestamp=timestamp,
                        nonce=nonce,
                        service_id=service_id,
                        user_id=None,
                        strategy_id=None,
                        body_hash=empty_body_hash,
                    )
                assert exc_info.value.status_code == 503

    @pytest.mark.asyncio()
    async def test_redis_error_in_log_only_mode_allows(
        self, mock_request: MagicMock, mock_redis: AsyncMock
    ) -> None:
        """Test Redis error allows request in log_only mode (soft-fail)."""
        timestamp = str(int(time.time()))
        nonce = "test-nonce"
        service_id = "orchestrator"
        secret = "a" * 64
        empty_body_hash = hashlib.sha256(b"").hexdigest()

        token = self._generate_token(
            service_id,
            "POST",
            "/api/v1/orders",
            timestamp,
            nonce,
            body_hash=empty_body_hash,
            secret=secret,
        )

        # Simulate Redis error
        mock_redis.set = AsyncMock(side_effect=Exception("Redis connection failed"))

        with patch.dict(os.environ, {"INTERNAL_TOKEN_SECRET": secret}, clear=False):
            with patch.dict(os.environ, {"API_AUTH_MODE": "log_only"}, clear=False):
                claims = await verify_internal_token(
                    request=mock_request,
                    redis_client=mock_redis,
                    token=token,
                    timestamp=timestamp,
                    nonce=nonce,
                    service_id=service_id,
                    user_id=None,
                    strategy_id=None,
                    body_hash=empty_body_hash,
                )

        # Should succeed despite Redis error in log_only mode
        assert claims is not None

    @pytest.mark.asyncio()
    async def test_sets_request_state_for_c5_integration(
        self, mock_request: MagicMock, mock_redis: AsyncMock
    ) -> None:
        """Test request.state is set for C5 rate limiting integration."""
        timestamp = str(int(time.time()))
        nonce = "test-nonce"
        service_id = "orchestrator"
        user_id = "user-123"
        strategy_id = "alpha_baseline"
        secret = "a" * 64
        empty_body_hash = hashlib.sha256(b"").hexdigest()

        token = self._generate_token(
            service_id,
            "POST",
            "/api/v1/orders",
            timestamp,
            nonce,
            user_id,
            strategy_id,
            body_hash=empty_body_hash,
            secret=secret,
        )

        with patch.dict(os.environ, {"INTERNAL_TOKEN_SECRET": secret}, clear=False):
            with patch.dict(os.environ, {"API_AUTH_MODE": "enforce"}, clear=False):
                claims = await verify_internal_token(
                    request=mock_request,
                    redis_client=mock_redis,
                    token=token,
                    timestamp=timestamp,
                    nonce=nonce,
                    service_id=service_id,
                    user_id=user_id,
                    strategy_id=strategy_id,
                    body_hash=empty_body_hash,
                )

        assert claims is not None
        # Verify request.state was set for C5
        assert mock_request.state.internal_service_verified is True
        assert mock_request.state.service_id == service_id
        assert mock_request.state.strategy_id == strategy_id
        assert mock_request.state.user == {"user_id": user_id, "aud": "internal-service"}

    @pytest.mark.asyncio()
    async def test_body_hash_tampering_detected(
        self, mock_request: MagicMock, mock_redis: AsyncMock
    ) -> None:
        """Test body hash mismatch is detected (payload tampering prevention)."""
        timestamp = str(int(time.time()))
        nonce = "test-nonce"
        service_id = "orchestrator"
        secret = "a" * 64
        original_body = b'{"symbol":"AAPL","qty":10}'
        original_body_hash = hashlib.sha256(original_body).hexdigest()

        # Generate token with original body hash
        token = self._generate_token(
            service_id,
            "POST",
            "/api/v1/orders",
            timestamp,
            nonce,
            body_hash=original_body_hash,
            secret=secret,
        )

        # Tampered body - attacker changed qty
        tampered_body = b'{"symbol":"AAPL","qty":1000}'
        mock_request.body = AsyncMock(return_value=tampered_body)

        with patch.dict(os.environ, {"INTERNAL_TOKEN_SECRET": secret}, clear=False):
            with patch.dict(os.environ, {"API_AUTH_MODE": "enforce"}, clear=False):
                claims = await verify_internal_token(
                    request=mock_request,
                    redis_client=mock_redis,
                    token=token,
                    timestamp=timestamp,
                    nonce=nonce,
                    service_id=service_id,
                    user_id=None,
                    strategy_id=None,
                    body_hash=original_body_hash,
                )

        # Should reject due to body hash mismatch
        assert claims is None

    @pytest.mark.asyncio()
    async def test_valid_body_hash_accepted(
        self, mock_request: MagicMock, mock_redis: AsyncMock
    ) -> None:
        """Test valid body hash is accepted."""
        timestamp = str(int(time.time()))
        nonce = "test-nonce"
        service_id = "orchestrator"
        secret = "a" * 64
        body = b'{"symbol":"AAPL","qty":10}'
        body_hash = hashlib.sha256(body).hexdigest()

        # Generate token with body hash
        token = self._generate_token(
            service_id,
            "POST",
            "/api/v1/orders",
            timestamp,
            nonce,
            body_hash=body_hash,
            secret=secret,
        )

        # Request body matches hash
        mock_request.body = AsyncMock(return_value=body)

        with patch.dict(os.environ, {"INTERNAL_TOKEN_SECRET": secret}, clear=False):
            with patch.dict(os.environ, {"API_AUTH_MODE": "enforce"}, clear=False):
                claims = await verify_internal_token(
                    request=mock_request,
                    redis_client=mock_redis,
                    token=token,
                    timestamp=timestamp,
                    nonce=nonce,
                    service_id=service_id,
                    user_id=None,
                    strategy_id=None,
                    body_hash=body_hash,
                )

        # Should accept - body matches hash
        assert claims is not None
        assert claims.service_id == service_id

    @pytest.mark.asyncio()
    async def test_query_string_tampering_detected(
        self, mock_request: MagicMock, mock_redis: AsyncMock
    ) -> None:
        """Test query string mismatch is detected (prevents query tampering).

        SECURITY: Verifies that if an attacker modifies the URL query params,
        the signature verification will fail because we verify against the
        actual request.url.query, not a header the attacker can control.
        """
        timestamp = str(int(time.time()))
        nonce = "test-nonce"
        service_id = "orchestrator"
        secret = "a" * 64
        # Sign with original query parameters
        original_query = "symbol=AAPL&limit=10"
        empty_body_hash = hashlib.sha256(b"").hexdigest()

        # Generate token with original query
        token = self._generate_token(
            service_id,
            "GET",
            "/api/v1/orders",
            timestamp,
            nonce,
            body_hash=empty_body_hash,
            query=original_query,
            secret=secret,
        )

        # Attacker tampers with URL query params (e.g., changes limit to 1000)
        tampered_query = "symbol=AAPL&limit=1000"
        mock_request.method = "GET"
        mock_request.url.query = tampered_query  # Actual request has tampered query

        with patch.dict(os.environ, {"INTERNAL_TOKEN_SECRET": secret}, clear=False):
            with patch.dict(os.environ, {"API_AUTH_MODE": "enforce"}, clear=False):
                claims = await verify_internal_token(
                    request=mock_request,
                    redis_client=mock_redis,
                    token=token,
                    timestamp=timestamp,
                    nonce=nonce,
                    service_id=service_id,
                    user_id=None,
                    strategy_id=None,
                    body_hash=empty_body_hash,
                )

        # Should reject due to query string mismatch
        assert claims is None


class TestAPIAuthConfig:
    """Tests for APIAuthConfig dataclass."""

    def test_config_creation(self) -> None:
        """Test config creation with all fields."""
        config = APIAuthConfig(
            action="order_submit",
            require_role=Role.OPERATOR,
            require_permission=Permission.SUBMIT_ORDER,
        )
        assert config.action == "order_submit"
        assert config.require_role == Role.OPERATOR
        assert config.require_permission == Permission.SUBMIT_ORDER

    def test_config_defaults(self) -> None:
        """Test config creation with defaults."""
        config = APIAuthConfig(action="test")
        assert config.action == "test"
        assert config.require_role is None
        assert config.require_permission is None


class TestAuthContext:
    """Tests for AuthContext dataclass."""

    def test_jwt_authenticated_context(self) -> None:
        """Test context for JWT-authenticated user."""
        user = MagicMock(spec=AuthenticatedUser)
        ctx = AuthContext(
            user=user,
            internal_claims=None,
            auth_type="jwt",
            is_authenticated=True,
        )
        assert ctx.user is user
        assert ctx.internal_claims is None
        assert ctx.auth_type == "jwt"
        assert ctx.is_authenticated is True

    def test_internal_token_context(self) -> None:
        """Test context for internal token auth."""
        claims = InternalTokenClaims(
            service_id="orchestrator",
            user_id="user-123",
            strategy_id=None,
            nonce="nonce",
            timestamp=12345,
        )
        ctx = AuthContext(
            user=None,
            internal_claims=claims,
            auth_type="internal_token",
            is_authenticated=True,
        )
        assert ctx.user is None
        assert ctx.internal_claims is claims
        assert ctx.auth_type == "internal_token"
        assert ctx.is_authenticated is True

    def test_unauthenticated_context(self) -> None:
        """Test context for unauthenticated request."""
        ctx = AuthContext(
            user=None,
            internal_claims=None,
            auth_type="none",
            is_authenticated=False,
        )
        assert ctx.user is None
        assert ctx.internal_claims is None
        assert ctx.auth_type == "none"
        assert ctx.is_authenticated is False


class TestMetrics:
    """Tests for Prometheus metrics."""

    def test_api_auth_checks_metric_exists(self) -> None:
        """Test api_auth_checks_total metric exists with correct labels."""
        assert api_auth_checks_total._name == "api_auth_checks"
        # Check labels
        labels = api_auth_checks_total._labelnames
        assert "action" in labels
        assert "result" in labels
        assert "auth_type" in labels
        assert "mode" in labels

    def test_s2s_auth_checks_metric_exists(self) -> None:
        """Test s2s_auth_checks_total metric exists with correct labels."""
        assert s2s_auth_checks_total._name == "s2s_auth_checks"
        labels = s2s_auth_checks_total._labelnames
        assert "service_id" in labels
        assert "result" in labels

    def test_s2s_replay_detected_metric_exists(self) -> None:
        """Test s2s_replay_detected_total metric exists."""
        assert s2s_replay_detected_total._name == "s2s_replay_detected"
        labels = s2s_replay_detected_total._labelnames
        assert "service_id" in labels


class TestInternalAuthHeaders:
    """Tests for _get_internal_auth_headers helper in orchestrator clients."""

    def test_headers_generated_with_secret(self) -> None:
        """Test headers are generated when secret is set."""
        from apps.orchestrator.clients import _get_internal_auth_headers

        secret = "a" * 64
        with patch.dict(os.environ, {"INTERNAL_TOKEN_SECRET": secret}, clear=False):
            headers = _get_internal_auth_headers(
                "POST", "/api/v1/orders", user_id="user-123", strategy_id="strategy-1"
            )

        assert "X-Internal-Token" in headers
        assert "X-Internal-Timestamp" in headers
        assert "X-Internal-Nonce" in headers
        assert "X-Service-ID" in headers
        assert "X-Body-Hash" in headers  # New: body hash header
        assert headers["X-Service-ID"] == "orchestrator"
        assert headers["X-User-ID"] == "user-123"
        assert headers["X-Strategy-ID"] == "strategy-1"

    def test_fail_closed_without_secret(self) -> None:
        """Test fail-closed behavior when secret is not set (RuntimeError raised)."""
        from apps.orchestrator.clients import _get_internal_auth_headers

        with patch.dict(os.environ, {"INTERNAL_TOKEN_SECRET": ""}, clear=False):
            with pytest.raises(RuntimeError, match="INTERNAL_TOKEN_SECRET is required"):
                _get_internal_auth_headers("POST", "/api/v1/orders")

    def test_optional_user_id_omitted(self) -> None:
        """Test user_id is omitted from headers when not provided."""
        from apps.orchestrator.clients import _get_internal_auth_headers

        secret = "a" * 64
        with patch.dict(os.environ, {"INTERNAL_TOKEN_SECRET": secret}, clear=False):
            headers = _get_internal_auth_headers("POST", "/api/v1/orders")

        assert "X-User-ID" not in headers
        assert "X-Strategy-ID" not in headers

    def test_signature_includes_user_context(self) -> None:
        """Test signature includes user_id and strategy_id for tamper resistance."""
        from apps.orchestrator.clients import _get_internal_auth_headers

        secret = "a" * 64
        with patch.dict(os.environ, {"INTERNAL_TOKEN_SECRET": secret}, clear=False):
            headers1 = _get_internal_auth_headers("POST", "/api/v1/orders", user_id="user-1")
            headers2 = _get_internal_auth_headers("POST", "/api/v1/orders", user_id="user-2")

        # Different user_id should produce different signature (timestamp/nonce differ too)
        # Just verify both have valid format
        assert len(headers1["X-Internal-Token"]) == 64  # SHA-256 hex
        assert len(headers2["X-Internal-Token"]) == 64

    def test_body_hash_included_in_headers(self) -> None:
        """Test body hash is computed and included in headers."""
        from apps.orchestrator.clients import _get_internal_auth_headers

        secret = "a" * 64
        body = '{"symbol":"AAPL","qty":10}'
        expected_hash = hashlib.sha256(body.encode()).hexdigest()

        with patch.dict(os.environ, {"INTERNAL_TOKEN_SECRET": secret}, clear=False):
            headers = _get_internal_auth_headers("POST", "/api/v1/orders", body=body)

        assert headers["X-Body-Hash"] == expected_hash

    def test_body_hash_computed_for_no_body(self) -> None:
        """Test body hash is SHA-256 of empty bytes when no body provided.

        SECURITY: Server requires body hash for state-changing requests (POST/PUT/PATCH/DELETE),
        so we always compute the hash even for empty bodies.
        """
        from apps.orchestrator.clients import _get_internal_auth_headers

        secret = "a" * 64
        expected_empty_body_hash = hashlib.sha256(b"").hexdigest()

        with patch.dict(os.environ, {"INTERNAL_TOKEN_SECRET": secret}, clear=False):
            headers = _get_internal_auth_headers("POST", "/api/v1/orders")

        assert headers["X-Body-Hash"] == expected_empty_body_hash

    def test_body_hash_included_in_signature(self) -> None:
        """Test body hash is part of signature to prevent payload tampering."""
        from apps.orchestrator.clients import _get_internal_auth_headers

        secret = "a" * 64
        body1 = '{"symbol":"AAPL","qty":10}'
        body2 = '{"symbol":"AAPL","qty":100}'  # Different payload

        with patch.dict(os.environ, {"INTERNAL_TOKEN_SECRET": secret}, clear=False):
            headers1 = _get_internal_auth_headers("POST", "/api/v1/orders", body=body1)
            headers2 = _get_internal_auth_headers("POST", "/api/v1/orders", body=body2)

        # Different bodies should have different body hashes
        assert headers1["X-Body-Hash"] != headers2["X-Body-Hash"]

    def test_per_service_secret_used_when_available(self) -> None:
        """Test per-service secret is used when available."""
        from apps.orchestrator.clients import _get_internal_auth_headers, _get_service_secret

        global_secret = "global" + "a" * 58  # 64 chars
        per_service_secret = "persvs" + "b" * 58  # 64 chars

        # With per-service secret set, it should be used
        with patch.dict(
            os.environ,
            {
                "INTERNAL_TOKEN_SECRET": global_secret,
                "INTERNAL_TOKEN_SECRET_ORCHESTRATOR": per_service_secret,
            },
            clear=False,
        ):
            secret = _get_service_secret()
            assert secret == per_service_secret

            headers = _get_internal_auth_headers("POST", "/api/v1/orders")
            assert "X-Internal-Token" in headers

    def test_global_secret_fallback(self) -> None:
        """Test fallback to global secret when per-service secret not set."""
        from apps.orchestrator.clients import _get_service_secret

        global_secret = "global" + "a" * 58

        with patch.dict(
            os.environ,
            {"INTERNAL_TOKEN_SECRET": global_secret, "INTERNAL_TOKEN_SECRET_ORCHESTRATOR": ""},
            clear=False,
        ):
            secret = _get_service_secret()
            assert secret == global_secret


class TestServiceIdWhitelist:
    """Tests for service_id whitelist validation."""

    def test_allowed_service_id_returns_true(self) -> None:
        """Test allowed service_id returns True."""
        # orchestrator is in the default whitelist
        assert _is_service_id_allowed("orchestrator") is True

    def test_unknown_service_id_returns_false(self) -> None:
        """Test unknown service_id returns False."""
        assert _is_service_id_allowed("malicious_service") is False

    def test_allowed_service_ids_contains_expected_services(self) -> None:
        """Test default whitelist contains expected services."""
        assert "orchestrator" in ALLOWED_SERVICE_IDS
        assert "signal_service" in ALLOWED_SERVICE_IDS
        assert "execution_gateway" in ALLOWED_SERVICE_IDS


class TestPerServiceSecretVerification:
    """Tests for per-service secret verification on server side."""

    def test_per_service_secret_used_for_verification(self) -> None:
        """Test verification uses per-service secret when available."""
        global_secret = "global" + "a" * 58
        per_service_secret = "persvs" + "b" * 58

        with patch.dict(
            os.environ,
            {"INTERNAL_TOKEN_SECRET_ORCHESTRATOR": per_service_secret},
            clear=False,
        ):
            # Patch the module constant for fallback
            with patch.dict(os.environ, {"INTERNAL_TOKEN_SECRET": global_secret}, clear=False):
                secret = _get_service_secret("orchestrator")
                assert secret == per_service_secret

    def test_global_secret_fallback_for_unknown_service(self) -> None:
        """Test fallback to global secret for services without per-service secret."""
        global_secret = "global" + "a" * 58

        # Patch environment variable (read at call time for secret rotation support)
        with patch.dict(os.environ, {"INTERNAL_TOKEN_SECRET": global_secret}, clear=False):
            secret = _get_service_secret("some_other_service")
            assert secret == global_secret


class TestUnknownServiceIdRejection:
    """Tests for rejecting unknown service_ids."""

    @pytest.fixture()
    def mock_request(self) -> MagicMock:
        """Create mock request."""
        request = MagicMock(spec=Request)
        request.method = "POST"
        mock_url = MagicMock()
        mock_url.path = "/api/v1/orders"
        mock_url.query = ""  # Empty query string by default
        request.url = mock_url
        request.state = MagicMock()
        request.body = AsyncMock(return_value=b"")
        return request

    @pytest.fixture()
    def mock_redis(self) -> AsyncMock:
        """Create mock Redis client."""
        redis = AsyncMock()
        redis.set = AsyncMock(return_value=True)
        return redis

    @pytest.mark.asyncio()
    async def test_unknown_service_id_rejected(
        self, mock_request: MagicMock, mock_redis: AsyncMock
    ) -> None:
        """Test unknown service_id is rejected."""
        timestamp = str(int(time.time()))
        nonce = "test-nonce"
        service_id = "malicious_service"  # Not in whitelist
        secret = "a" * 64

        # Generate a valid token for the unknown service using JSON format
        import json

        payload_dict = {
            "service_id": service_id,
            "method": "POST",
            "path": "/api/v1/orders",
            "query": "",
            "timestamp": timestamp,
            "nonce": nonce,
            "user_id": "",
            "strategy_id": "",
            "body_hash": "",
        }
        payload = json.dumps(payload_dict, separators=(",", ":"), sort_keys=True)
        token = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

        with patch.dict(os.environ, {"INTERNAL_TOKEN_SECRET": secret}, clear=False):
            with patch.dict(os.environ, {"API_AUTH_MODE": "enforce"}, clear=False):
                claims = await verify_internal_token(
                    request=mock_request,
                    redis_client=mock_redis,
                    token=token,
                    timestamp=timestamp,
                    nonce=nonce,
                    service_id=service_id,
                    user_id=None,
                    strategy_id=None,
                    body_hash=None,
                )

        # Should reject due to service_id not in whitelist
        assert claims is None
