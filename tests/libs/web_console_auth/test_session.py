"""Unit tests for SessionManager.

Tests cover:
- Session creation (token generation, Redis storage)
- Session refresh (token rotation, binding verification)
- Session validation (IP/UA binding, revocation checks)
- Session limits (max 3 concurrent, oldest eviction)
- Rate limiting (allow/block/expiry)
- Cookie security parameters
- Expired session cleanup
"""

import hashlib
import time

import pytest
from fakeredis import FakeRedis

from libs.web_console_auth.config import AuthConfig
from libs.web_console_auth.exceptions import InvalidTokenError, RateLimitExceededError
from libs.web_console_auth.jwt_manager import JWTManager
from libs.web_console_auth.session import SessionManager


class TestSessionCreation:
    """Tests for session creation."""

    def test_create_session_returns_token_pair(self, session_manager):
        """Test create_session returns access + refresh token pair."""
        access_token, refresh_token = session_manager.create_session(
            user_id="user1",
            client_ip="192.168.1.100",
            user_agent="Mozilla/5.0",
        )

        assert isinstance(access_token, str)
        assert isinstance(refresh_token, str)
        assert access_token != refresh_token

        # Verify tokens are valid
        access_payload = session_manager.jwt.decode_token(access_token)
        refresh_payload = session_manager.jwt.decode_token(refresh_token)

        assert access_payload["type"] == "access"
        assert refresh_payload["type"] == "refresh"
        assert access_payload["sub"] == "user1"
        assert refresh_payload["sub"] == "user1"

    def test_create_session_stores_in_redis(self, session_manager):
        """Test create_session stores session metadata in Redis."""
        access_token, refresh_token = session_manager.create_session(
            user_id="user1",
            client_ip="192.168.1.100",
            user_agent="Mozilla/5.0",
        )

        # Get session metadata
        access_payload = session_manager.jwt.decode_token(access_token)
        session_id = access_payload["session_id"]

        session_key = f"{session_manager.config.redis_session_prefix}{session_id}"
        session_data = session_manager.redis.hgetall(session_key)

        assert session_data is not None
        assert session_data[b"user_id"].decode() == "user1"
        assert session_data[b"ip"].decode() == "192.168.1.100"
        assert b"user_agent_hash" in session_data
        assert b"session_id" in session_data

    def test_create_session_enforces_limit(self, session_manager):
        """Test create_session evicts oldest when exceeding max sessions (3)."""
        user_id = "user1"
        client_ip = "192.168.1.100"
        user_agent = "Mozilla/5.0"

        # Create 4 sessions (max is 3)
        sessions = []
        for _ in range(4):
            access_token, _ = session_manager.create_session(user_id, client_ip, user_agent)
            payload = session_manager.jwt.decode_token(access_token)
            sessions.append(payload["session_id"])
            time.sleep(0.01)  # Ensure different creation times

        # First session should be evicted
        first_session_key = f"{session_manager.config.redis_session_prefix}{sessions[0]}"
        assert not session_manager.redis.exists(first_session_key)

        # Other 3 sessions should exist
        for session_id in sessions[1:]:
            session_key = f"{session_manager.config.redis_session_prefix}{session_id}"
            assert session_manager.redis.exists(session_key)

    def test_create_session_user_agent_hash_stored(self, session_manager):
        """Test user agent is hashed (SHA256) before storage."""
        user_agent = "Mozilla/5.0 Test Browser"
        expected_hash = hashlib.sha256(user_agent.encode()).hexdigest()

        access_token, _ = session_manager.create_session("user1", "192.168.1.100", user_agent)
        payload = session_manager.jwt.decode_token(access_token)
        session_id = payload["session_id"]

        session_key = f"{session_manager.config.redis_session_prefix}{session_id}"
        session_data = session_manager.redis.hgetall(session_key)

        assert session_data[b"user_agent_hash"].decode() == expected_hash


class TestSessionRefresh:
    """Tests for session refresh."""

    def test_refresh_session_rotates_access_token(self, session_manager):
        """Test refresh_session returns new access + refresh token pair."""
        # Create initial session
        access_token1, refresh_token1 = session_manager.create_session(
            "user1", "192.168.1.100", "Mozilla/5.0"
        )
        payload1 = session_manager.jwt.decode_token(access_token1)
        refresh_payload1 = session_manager.jwt.decode_token(refresh_token1)
        session_id = payload1["session_id"]

        # Refresh session (returns new access + refresh tokens)
        access_token2, refresh_token2 = session_manager.refresh_session(
            refresh_token1, "192.168.1.100", "Mozilla/5.0"
        )
        payload2 = session_manager.jwt.decode_token(access_token2)
        refresh_payload2 = session_manager.jwt.decode_token(refresh_token2)

        # Same session, different access token
        assert payload2["session_id"] == session_id
        assert payload2["jti"] != payload1["jti"]
        assert access_token2 != access_token1

        # Refresh token also rotated
        assert refresh_payload2["session_id"] == session_id
        assert refresh_payload2["jti"] != refresh_payload1["jti"]
        assert refresh_token2 != refresh_token1

    def test_refresh_session_validates_binding_ip(self, session_manager):
        """Test refresh_session rejects request from different IP."""
        _, refresh_token = session_manager.create_session("user1", "192.168.1.100", "Mozilla/5.0")

        # Try to refresh from different IP
        with pytest.raises(InvalidTokenError, match="Session IP mismatch"):
            session_manager.refresh_session(refresh_token, "192.168.1.200", "Mozilla/5.0")

    def test_refresh_session_validates_binding_ua(self, session_manager):
        """Test refresh_session rejects request from different User-Agent."""
        _, refresh_token = session_manager.create_session("user1", "192.168.1.100", "Mozilla/5.0")

        # Try to refresh from different User-Agent
        with pytest.raises(InvalidTokenError, match="Session User-Agent mismatch"):
            session_manager.refresh_session(refresh_token, "192.168.1.100", "Different Browser")

    def test_refresh_session_relaxed_binding(self, jwt_manager):
        """Test refresh_session allows binding mismatch when strict=False."""
        config = AuthConfig(session_binding_strict=False)
        session_manager = SessionManager(FakeRedis(), jwt_manager, config)

        _, refresh_token = session_manager.create_session("user1", "192.168.1.100", "Mozilla/5.0")

        # Should succeed even with different IP/UA (logs warning but doesn't raise)
        access_token, new_refresh_token = session_manager.refresh_session(
            refresh_token, "192.168.1.200", "Different Browser"
        )
        assert isinstance(access_token, str)
        assert isinstance(new_refresh_token, str)


class TestSessionValidation:
    """Tests for session validation."""

    def test_validate_session_succeeds_for_valid(self, session_manager):
        """Test validate_session succeeds for valid token with matching binding."""
        access_token, _ = session_manager.create_session("user1", "192.168.1.100", "Mozilla/5.0")

        payload = session_manager.validate_session(access_token, "192.168.1.100", "Mozilla/5.0")

        assert payload["sub"] == "user1"
        assert payload["type"] == "access"

    def test_validate_session_checks_ip_binding(self, session_manager):
        """Test validate_session rejects token from different IP."""
        access_token, _ = session_manager.create_session("user1", "192.168.1.100", "Mozilla/5.0")

        with pytest.raises(InvalidTokenError, match="Session IP mismatch"):
            session_manager.validate_session(access_token, "192.168.1.200", "Mozilla/5.0")

    def test_validate_session_checks_ua_binding(self, session_manager):
        """Test validate_session rejects token from different User-Agent."""
        access_token, _ = session_manager.create_session("user1", "192.168.1.100", "Mozilla/5.0")

        with pytest.raises(InvalidTokenError, match="Session User-Agent mismatch"):
            session_manager.validate_session(access_token, "192.168.1.100", "Different Browser")


class TestSessionTermination:
    """Tests for session termination."""

    def test_terminate_session_revokes_tokens(self, session_manager):
        """Test terminate_session blacklists both access and refresh tokens."""
        access_token, refresh_token = session_manager.create_session("user1", "192.168.1.100", "Mozilla/5.0")
        access_payload = session_manager.jwt.decode_token(access_token)
        refresh_payload = session_manager.jwt.decode_token(refresh_token)

        session_id = access_payload["session_id"]
        access_jti = access_payload["jti"]
        refresh_jti = refresh_payload["jti"]

        # Terminate session
        session_manager.terminate_session(session_id)

        # Tokens should be revoked
        assert session_manager.jwt.is_token_revoked(access_jti) is True
        assert session_manager.jwt.is_token_revoked(refresh_jti) is True

    def test_terminate_session_removes_from_redis(self, session_manager):
        """Test terminate_session removes session metadata from Redis."""
        access_token, _ = session_manager.create_session("user1", "192.168.1.100", "Mozilla/5.0")
        payload = session_manager.jwt.decode_token(access_token)
        session_id = payload["session_id"]

        session_key = f"{session_manager.config.redis_session_prefix}{session_id}"
        assert session_manager.redis.exists(session_key)

        # Terminate session
        session_manager.terminate_session(session_id)

        # Session should be removed
        assert not session_manager.redis.exists(session_key)

    def test_terminate_session_nonexistent(self, session_manager):
        """Test terminate_session handles nonexistent session gracefully."""
        # Should not raise error
        session_manager.terminate_session("nonexistent_session_id")


class TestRateLimiting:
    """Tests for rate limiting."""

    def test_check_rate_limit_allows_within_limit(self, session_manager):
        """Test check_rate_limit allows requests within limit."""
        client_ip = "192.168.1.100"

        # First 5 requests should succeed (limit is 5/15min)
        for _ in range(5):
            assert session_manager.check_rate_limit(client_ip, "test_action") is True

    def test_check_rate_limit_blocks_exceeded(self, session_manager):
        """Test check_rate_limit blocks requests exceeding limit."""
        client_ip = "192.168.1.100"

        # Consume limit (5 attempts)
        for _ in range(5):
            session_manager.check_rate_limit(client_ip, "test_action")

        # 6th attempt should be blocked
        assert session_manager.check_rate_limit(client_ip, "test_action") is False

    def test_check_rate_limit_auto_expires(self, session_manager):
        """Test rate limit counter has TTL and expires."""
        client_ip = "192.168.1.100"
        action = "test_action"

        session_manager.check_rate_limit(client_ip, action)

        # Check that Redis key has TTL set
        key = f"{session_manager.config.redis_rate_limit_prefix}{action}:{client_ip}"
        ttl = session_manager.redis.ttl(key)

        assert ttl > 0
        assert ttl <= session_manager.config.rate_limit_window

    def test_check_rate_limit_disabled(self, jwt_manager):
        """Test check_rate_limit returns True when rate limiting disabled."""
        config = AuthConfig(rate_limit_enabled=False)
        session_manager = SessionManager(FakeRedis(), jwt_manager, config)

        # Should always return True when disabled
        for _ in range(100):
            assert session_manager.check_rate_limit("192.168.1.100", "test") is True

    def test_create_session_rate_limited(self, session_manager):
        """Test create_session raises RateLimitExceededError when limit exceeded."""
        client_ip = "192.168.1.100"

        # Consume rate limit
        for idx in range(5):
            session_manager.create_session(f"user{idx}", client_ip, "Mozilla/5.0")

        # Next attempt should fail
        with pytest.raises(RateLimitExceededError, match="Rate limit exceeded"):
            session_manager.create_session("user6", client_ip, "Mozilla/5.0")


class TestCookieParams:
    """Tests for cookie security parameters."""

    def test_get_session_cookie_params_secure_defaults(self, session_manager):
        """Test get_session_cookie_params returns secure defaults."""
        params = session_manager.get_session_cookie_params()

        assert params["secure"] is True  # HTTPS-only
        assert params["httponly"] is True  # No JavaScript access
        assert params["samesite"] == "Strict"  # CSRF protection
        assert params["path"] == "/"
        assert params["max_age"] == 14400  # Defaults to refresh_token_ttl

    def test_get_session_cookie_params_custom_max_age(self, jwt_manager):
        """Test cookie max_age uses config value when set."""
        config = AuthConfig(cookie_max_age=7200)  # 2 hours
        session_manager = SessionManager(FakeRedis(), jwt_manager, config)

        params = session_manager.get_session_cookie_params()

        assert params["max_age"] == 7200

    def test_get_session_cookie_params_custom_domain(self, jwt_manager):
        """Test cookie domain can be customized."""
        config = AuthConfig(cookie_domain=".example.com")
        session_manager = SessionManager(FakeRedis(), jwt_manager, config)

        params = session_manager.get_session_cookie_params()

        assert params["domain"] == ".example.com"


class TestCleanupExpiredSessions:
    """Tests for cleanup_expired_sessions."""

    def test_cleanup_expired_sessions(self, session_manager):
        """Test cleanup_expired_sessions (TTL-based cleanup automatic)."""
        # This is mostly a placeholder test as cleanup is TTL-based
        count = session_manager.cleanup_expired_sessions()

        # Should return 0 (automatic TTL cleanup)
        assert count == 0


# Fixtures


@pytest.fixture()
def jwt_keys(tmp_path):
    """Generate temporary JWT RSA key pair for testing."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    private_key_path = tmp_path / "jwt_private.key"
    with private_key_path.open("wb") as f:
        f.write(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

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
    )
    return JWTManager(config, FakeRedis())


@pytest.fixture()
def session_manager(jwt_manager):
    """Create SessionManager with fake Redis and JWTManager."""
    config = AuthConfig()
    return SessionManager(FakeRedis(), jwt_manager, config)
