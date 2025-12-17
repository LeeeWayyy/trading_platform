"""Unit tests for JWTManager.

Tests cover:
- Token generation (access + refresh tokens)
- Token validation (signature, expiration, type)
- Token revocation (blacklist storage and checking)
- Invalid token handling (malformed, expired, wrong signature, wrong type)
- Clock skew tolerance
- Structured logging (token redaction)
"""

import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fakeredis import FakeRedis

from libs.web_console_auth.config import AuthConfig
from libs.web_console_auth.exceptions import (
    InvalidTokenError,
    MissingJtiError,
    TokenExpiredError,
    TokenRevokedError,
)
from libs.web_console_auth.jwt_manager import JWTManager


class TestJWTManagerInitialization:
    """Tests for JWTManager initialization."""

    def test_initialization_with_valid_keys(self, jwt_keys):
        """Test JWTManager initializes with valid JWT keys from Component 1."""
        private_key_path, public_key_path = jwt_keys
        config = AuthConfig(
            jwt_private_key_path=private_key_path,
            jwt_public_key_path=public_key_path,
        )
        redis_client = FakeRedis()

        manager = JWTManager(config, redis_client)

        assert manager.config == config
        assert manager.redis == redis_client
        assert manager.private_key is not None
        assert manager.public_key is not None

    def test_initialization_missing_private_key(self):
        """Test initialization fails if private key missing."""
        config = AuthConfig(
            jwt_private_key_path=Path("/nonexistent/private.key"),
            jwt_public_key_path=Path("/nonexistent/public.pem"),
        )
        redis_client = FakeRedis()

        with pytest.raises(FileNotFoundError, match="JWT private key not found"):
            JWTManager(config, redis_client)


class TestAccessTokenGeneration:
    """Tests for access token generation."""

    def test_generate_access_token_valid_claims(self, jwt_manager):
        """Test access token has correct claims and structure."""
        token = jwt_manager.generate_access_token(
            user_id="test_user",
            session_id="session_123",
            client_ip="192.168.1.100",
            user_agent="Mozilla/5.0",
        )

        # Decode without validation to inspect claims
        payload = jwt_manager.decode_token(token)

        assert payload["sub"] == "test_user"
        assert payload["type"] == "access"
        assert payload["session_id"] == "session_123"
        assert payload["ip"] == "192.168.1.100"
        assert "user_agent_hash" in payload
        assert "jti" in payload
        assert "iat" in payload
        assert "exp" in payload

        # Verify expiration is 15 minutes from now (access_token_ttl)
        assert payload["exp"] - payload["iat"] == 900

    def test_generate_access_token_unique_jti(self, jwt_manager):
        """Test each access token has unique JTI."""
        token1 = jwt_manager.generate_access_token("user1", "session1", "127.0.0.1", "UA1")
        token2 = jwt_manager.generate_access_token("user1", "session1", "127.0.0.1", "UA1")

        payload1 = jwt_manager.decode_token(token1)
        payload2 = jwt_manager.decode_token(token2)

        assert payload1["jti"] != payload2["jti"]

    def test_generate_access_token_user_agent_hashed(self, jwt_manager):
        """Test user agent is hashed (SHA256) in token."""
        import hashlib

        user_agent = "Mozilla/5.0 Test Browser"
        expected_hash = hashlib.sha256(user_agent.encode()).hexdigest()

        token = jwt_manager.generate_access_token("user1", "session1", "127.0.0.1", user_agent)
        payload = jwt_manager.decode_token(token)

        assert payload["user_agent_hash"] == expected_hash

    @patch("libs.web_console_auth.jwt_manager.logger")
    def test_generate_access_token_logs_jti_only(self, mock_logger, jwt_manager):
        """Test logging redacts full token, logs jti only (Codex Recommendation #1)."""
        token = jwt_manager.generate_access_token("user1", "session1", "127.0.0.1", "UA1")
        payload = jwt_manager.decode_token(token)

        # Verify logger.info was called
        assert mock_logger.info.called

        # Get the log call
        log_call = mock_logger.info.call_args

        # Verify log message
        assert log_call[0][0] == "access_token_generated"

        # Verify extra fields contain jti but NOT full token
        extra = log_call[1]["extra"]
        assert "jti" in extra
        assert extra["jti"] == payload["jti"]
        assert token not in str(extra)  # Full token NEVER logged


class TestRefreshTokenGeneration:
    """Tests for refresh token generation."""

    def test_generate_refresh_token_valid_claims(self, jwt_manager):
        """Test refresh token has correct claims."""
        access_jti = "access_token_jti_123"
        token = jwt_manager.generate_refresh_token(
            user_id="test_user",
            session_id="session_123",
            access_jti=access_jti,
        )

        payload = jwt_manager.decode_token(token)

        assert payload["sub"] == "test_user"
        assert payload["type"] == "refresh"
        assert payload["session_id"] == "session_123"
        assert payload["access_jti"] == access_jti
        assert "jti" in payload

        # Verify expiration is 4 hours from now (refresh_token_ttl)
        assert payload["exp"] - payload["iat"] == 14400

    def test_generate_refresh_token_links_to_access(self, jwt_manager):
        """Test refresh token links to access token via access_jti."""
        access_token = jwt_manager.generate_access_token("user1", "session1", "127.0.0.1", "UA1")
        access_payload = jwt_manager.decode_token(access_token)
        access_jti = access_payload["jti"]

        refresh_token = jwt_manager.generate_refresh_token("user1", "session1", access_jti)
        refresh_payload = jwt_manager.decode_token(refresh_token)

        assert refresh_payload["access_jti"] == access_jti


class TestTokenValidation:
    """Tests for token validation."""

    def test_validate_token_valid_access_token(self, jwt_manager):
        """Test validation succeeds for valid access token."""
        token = jwt_manager.generate_access_token("user1", "session1", "127.0.0.1", "UA1")

        payload = jwt_manager.validate_token(token, "access")

        assert payload["sub"] == "user1"
        assert payload["type"] == "access"

    def test_validate_token_valid_refresh_token(self, jwt_manager):
        """Test validation succeeds for valid refresh token."""
        token = jwt_manager.generate_refresh_token("user1", "session1", "access_jti_123")

        payload = jwt_manager.validate_token(token, "refresh")

        assert payload["sub"] == "user1"
        assert payload["type"] == "refresh"

    def test_validate_token_expired_token(self, jwt_manager):
        """Test validation rejects expired token."""
        # Create token with past expiration
        from datetime import UTC, datetime, timedelta

        import jwt as pyjwt

        now = datetime.now(UTC)
        expired_payload = {
            "sub": "user1",
            "iat": int((now - timedelta(hours=2)).timestamp()),
            "exp": int((now - timedelta(hours=1)).timestamp()),  # Expired 1 hour ago
            "jti": "test_jti",
            "iss": jwt_manager.config.jwt_issuer,
            "aud": jwt_manager.config.jwt_audience,
            "type": "access",
        }

        expired_token = pyjwt.encode(expired_payload, jwt_manager.private_key, algorithm="RS256")

        with pytest.raises(TokenExpiredError, match="Token has expired"):
            jwt_manager.validate_token(expired_token, "access")

    def test_validate_token_wrong_type(self, jwt_manager):
        """Test validation rejects access token when refresh expected."""
        access_token = jwt_manager.generate_access_token("user1", "session1", "127.0.0.1", "UA1")

        with pytest.raises(InvalidTokenError, match="Expected refresh token, got access"):
            jwt_manager.validate_token(access_token, "refresh")

    def test_validate_token_invalid_signature(self, jwt_manager, jwt_keys):
        """Test validation rejects tampered token (invalid signature)."""
        token = jwt_manager.generate_access_token("user1", "session1", "127.0.0.1", "UA1")

        # Tamper with token by changing payload
        parts = token.split(".")
        tampered_token = parts[0] + ".eyJzdWIiOiJoYWNrZXIifQ." + parts[2]

        with pytest.raises(InvalidTokenError, match="Token signature verification failed"):
            jwt_manager.validate_token(tampered_token, "access")

    def test_validate_token_malformed(self, jwt_manager):
        """Test validation rejects malformed token."""
        malformed_token = "not.a.valid.jwt.token"

        with pytest.raises(InvalidTokenError, match="Invalid token"):
            jwt_manager.validate_token(malformed_token, "access")

    def test_validate_token_missing_jti(self, jwt_manager):
        """Test validation rejects token without jti claim."""
        from datetime import UTC, datetime, timedelta

        import jwt as pyjwt

        now = datetime.now(UTC)
        payload_no_jti = {
            "sub": "user1",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
            "iss": jwt_manager.config.jwt_issuer,
            "aud": jwt_manager.config.jwt_audience,
            "type": "access",
            # Missing jti
        }

        token_no_jti = pyjwt.encode(payload_no_jti, jwt_manager.private_key, algorithm="RS256")

        with pytest.raises(MissingJtiError, match="Token missing jti claim"):
            jwt_manager.validate_token(token_no_jti, "access")

    def test_validate_token_clock_skew_tolerance(self, jwt_manager):
        """Test validation accepts tokens issued slightly in future (clock skew)."""
        from datetime import UTC, datetime, timedelta

        import jwt as pyjwt

        now = datetime.now(UTC)
        # Token issued 15 seconds in the future (within 30s clock skew)
        future_payload = {
            "sub": "user1",
            "iat": int((now + timedelta(seconds=15)).timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
            "jti": "future_jti",
            "iss": jwt_manager.config.jwt_issuer,
            "aud": jwt_manager.config.jwt_audience,
            "type": "access",
        }

        future_token = pyjwt.encode(future_payload, jwt_manager.private_key, algorithm="RS256")

        # Should succeed due to clock skew tolerance (30s)
        payload = jwt_manager.validate_token(future_token, "access")
        assert payload["sub"] == "user1"


class TestTokenRevocation:
    """Tests for token revocation."""

    def test_revoke_token_blacklists_jti(self, jwt_manager):
        """Test revoke_token adds jti to Redis blacklist."""
        jti = "test_jti_to_revoke"
        exp = int(time.time()) + 3600  # Expires in 1 hour

        jwt_manager.revoke_token(jti, exp)

        # Verify token is in blacklist
        assert jwt_manager.is_token_revoked(jti) is True

    def test_revoke_token_ttl_calculation(self, jwt_manager):
        """Test blacklist TTL = exp - current_time (Codex Recommendation #5)."""
        jti = "test_jti_ttl"
        now = int(time.time())
        exp = now + 1800  # Expires in 30 minutes
        expected_ttl = 1800

        jwt_manager.revoke_token(jti, exp)

        # Verify Redis TTL is approximately correct (allow 1s tolerance)
        key = f"{jwt_manager.config.redis_blacklist_prefix}{jti}"
        actual_ttl = jwt_manager.redis.ttl(key)
        assert abs(actual_ttl - expected_ttl) <= 1

    def test_revoke_token_expired_token_min_ttl(self, jwt_manager):
        """Test revoke_token uses min TTL=1s for already-expired tokens."""
        jti = "expired_jti"
        exp = int(time.time()) - 3600  # Expired 1 hour ago

        jwt_manager.revoke_token(jti, exp)

        # Should still be in blacklist with min TTL
        assert jwt_manager.is_token_revoked(jti) is True

        key = f"{jwt_manager.config.redis_blacklist_prefix}{jti}"
        ttl = jwt_manager.redis.ttl(key)
        assert ttl >= 0  # At least some TTL set

    def test_validate_revoked_token_rejected(self, jwt_manager):
        """Test validation rejects revoked token."""
        token = jwt_manager.generate_access_token("user1", "session1", "127.0.0.1", "UA1")
        payload = jwt_manager.decode_token(token)
        jti = payload["jti"]
        exp = payload["exp"]

        # Revoke the token
        jwt_manager.revoke_token(jti, exp)

        # Validation should fail
        with pytest.raises(TokenRevokedError, match="Token has been revoked"):
            jwt_manager.validate_token(token, "access")

    def test_is_token_revoked_false_for_valid(self, jwt_manager):
        """Test is_token_revoked returns False for non-revoked token."""
        jti = "never_revoked_jti"

        assert jwt_manager.is_token_revoked(jti) is False


class TestDecodeToken:
    """Tests for decode_token (unvalidated)."""

    def test_decode_token_returns_payload(self, jwt_manager):
        """Test decode_token returns payload without validation."""
        token = jwt_manager.generate_access_token("user1", "session1", "127.0.0.1", "UA1")

        payload = jwt_manager.decode_token(token)

        assert payload["sub"] == "user1"
        assert payload["type"] == "access"

    def test_decode_token_works_for_expired(self, jwt_manager):
        """Test decode_token works even for expired tokens (no validation)."""
        from datetime import UTC, datetime, timedelta

        import jwt as pyjwt

        now = datetime.now(UTC)
        expired_payload = {
            "sub": "user1",
            "iat": int((now - timedelta(hours=2)).timestamp()),
            "exp": int((now - timedelta(hours=1)).timestamp()),
            "jti": "expired_jti",
            "type": "access",
        }

        expired_token = pyjwt.encode(expired_payload, jwt_manager.private_key, algorithm="RS256")

        # decode_token should work (no validation)
        payload = jwt_manager.decode_token(expired_token)
        assert payload["sub"] == "user1"


# Fixtures


@pytest.fixture()
def jwt_keys(tmp_path):
    """Generate temporary JWT RSA key pair for testing.

    Returns:
        Tuple of (private_key_path, public_key_path)
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    # Generate RSA key pair (2048 for speed in tests, production uses 4096)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    # Write private key
    private_key_path = tmp_path / "jwt_private.key"
    with private_key_path.open("wb") as f:
        f.write(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    # Write public key
    public_key_path = tmp_path / "jwt_public.pem"
    with public_key_path.open("wb") as f:
        f.write(
            public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )

    return private_key_path, public_key_path


@pytest.fixture()
def jwt_manager(jwt_keys):
    """Create JWTManager with temporary keys and fake Redis."""
    private_key_path, public_key_path = jwt_keys
    config = AuthConfig(
        jwt_private_key_path=private_key_path,
        jwt_public_key_path=public_key_path,
        access_token_ttl=900,
        refresh_token_ttl=14400,
        clock_skew_seconds=30,
    )
    redis_client = FakeRedis()

    return JWTManager(config, redis_client)
