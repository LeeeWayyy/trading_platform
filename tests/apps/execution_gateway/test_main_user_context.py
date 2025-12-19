from __future__ import annotations

import hashlib
import hmac
import sys
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

fastapi = pytest.importorskip("fastapi")
HTTPException = pytest.importorskip("fastapi").HTTPException
TestClient = pytest.importorskip("fastapi.testclient").TestClient

# Stub redis + jwt before importing application modules to avoid cryptography/redis binaries
redis_stub = ModuleType("redis")
redis_stub.exceptions = ModuleType("redis.exceptions")
redis_stub.connection = ModuleType("redis.connection")
redis_stub.asyncio = ModuleType("redis.asyncio")
redis_stub.lock = ModuleType("redis.lock")


class _RedisError(Exception):
    pass


class _ConnectionPool:
    def __init__(self, *args, **kwargs):
        pass

    def disconnect(self):
        pass


redis_stub.exceptions.RedisError = _RedisError
redis_stub.exceptions.ConnectionError = _RedisError  # Alias for tests
redis_stub.exceptions.TimeoutError = _RedisError  # Alias for tests
redis_stub.connection.ConnectionPool = _ConnectionPool


class _RedisClient:
    def __init__(self, *args, **kwargs):
        pass

    def ping(self):
        return True


class _AsyncRedis:
    def __init__(self, *args, **kwargs):
        pass

    @classmethod
    def from_url(cls, *args, **kwargs):
        return cls()


class _Lock:
    def __init__(self, *args, **kwargs):
        pass


redis_stub.Redis = _RedisClient
redis_stub.asyncio.Redis = _AsyncRedis
redis_stub.lock.Lock = _Lock
sys.modules.setdefault("redis", redis_stub)
sys.modules.setdefault("redis.exceptions", redis_stub.exceptions)
sys.modules.setdefault("redis.connection", redis_stub.connection)
sys.modules.setdefault("redis.asyncio", redis_stub.asyncio)
sys.modules.setdefault("redis.lock", redis_stub.lock)

jwt_stub = ModuleType("jwt")
jwt_stub.api_jwk = SimpleNamespace(PyJWK=None, PyJWKSet=None)


# Stub algorithm classes for jwt.algorithms imports
class _StubAlgorithm:
    pass


jwt_stub.algorithms = SimpleNamespace(
    get_default_algorithms=lambda: {},
    has_crypto=lambda: False,
    requires_cryptography=False,
    ECAlgorithm=_StubAlgorithm,
    RSAAlgorithm=_StubAlgorithm,
)
jwt_stub.utils = SimpleNamespace()
sys.modules.setdefault("jwt", jwt_stub)
sys.modules.setdefault("jwt.api_jwk", jwt_stub.api_jwk)
sys.modules.setdefault("jwt.algorithms", jwt_stub.algorithms)
sys.modules.setdefault("jwt.utils", jwt_stub.utils)

from fastapi import Request

from apps.execution_gateway import main


def _make_user_context_override(user_ctx: dict) -> callable:
    """Create a dependency override with proper Request signature.

    FastAPI inspects function parameters for dependency resolution.
    Using lambda *_, **__: causes FastAPI to treat _ and __ as required
    query parameters, resulting in 422 errors.
    """

    def override(request: Request) -> dict:
        return user_ctx

    return override


class DummyQueryParams:
    def __init__(self, values=None):
        self._values = values or []

    def getlist(self, *_args, **_kwargs):
        return list(self._values)


def _make_request(user=None, strategies=None):
    req = SimpleNamespace()
    req.state = SimpleNamespace(user=user)
    req.query_params = DummyQueryParams(strategies)
    return req


@pytest.fixture()
def test_client():
    return TestClient(main.app)


def test_build_user_context_missing_user_fails_closed():
    req = _make_request(user=None)
    with pytest.raises(HTTPException) as exc:
        main._build_user_context(req)
    assert exc.value.status_code == 401


def test_build_user_context_missing_role_denied():
    req = _make_request(user={"strategies": ["s1"], "user_id": "u1"})
    with pytest.raises(HTTPException) as exc:
        main._build_user_context(req)
    assert exc.value.status_code == 401


def test_build_user_context_extracts_requested_strategies():
    req = _make_request(
        user={"role": "viewer", "strategies": ["s1"], "user_id": "u1"},
        strategies=["s1"],
    )
    ctx = main._build_user_context(req)
    assert ctx["role"] == "viewer"
    assert ctx["requested_strategies"] == ["s1"]


def test_daily_performance_invalid_strategy_subset(monkeypatch, test_client):
    """Requested strategies outside authorized set should 403."""
    req_user = {
        "role": "viewer",
        "strategies": ["s1"],
        "requested_strategies": ["s1", "s2"],
        "user_id": "u1",
        "user": {"role": "viewer", "strategies": ["s1"], "user_id": "u1"},
    }
    main.app.dependency_overrides[main._build_user_context] = _make_user_context_override(req_user)

    with (monkeypatch.context() as m,):
        m.setattr(main, "db_client", main.db_client)
        m.setattr(main, "redis_client", main.redis_client)
        m.setattr(main, "FEATURE_PERFORMANCE_DASHBOARD", True)
        resp = test_client.get(
            "/api/v1/performance/daily",
            headers={"X-User-Role": "viewer", "X-User-Id": "u1"},
            params={"strategies": ["s1", "s2"]},
        )
    assert resp.status_code == 403


# ============================================================================
# Internal Token Validation Tests
# ============================================================================


def _generate_internal_token(
    user_id: str, role: str, strategies: str, timestamp: int, secret: str
) -> str:
    """Generate a valid internal token for testing.

    Token format: HMAC-SHA256(secret, "user_id:role:strategies:timestamp")
    """
    payload = f"{user_id}:{role}:{strategies}:{timestamp}"
    return hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class MockSecretStr:
    """Mock SecretStr for testing."""

    def __init__(self, value: str):
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


class MockSettings:
    """Mock settings for internal token tests."""

    def __init__(
        self,
        internal_token_required: bool = False,
        internal_token_secret: str = "",
        internal_token_timestamp_tolerance_seconds: int = 300,
    ):
        self.internal_token_required = internal_token_required
        self.internal_token_secret = MockSecretStr(internal_token_secret)
        self.internal_token_timestamp_tolerance_seconds = internal_token_timestamp_tolerance_seconds


class TestInternalTokenValidation:
    """Tests for _verify_internal_token helper function."""

    def test_disabled_mode_always_passes(self):
        """When INTERNAL_TOKEN_REQUIRED=false, validation always passes."""
        settings = MockSettings(internal_token_required=False)
        is_valid, error = main._verify_internal_token(
            token=None,
            timestamp_str=None,
            user_id="user1",
            role="admin",
            strategies="",
            settings=settings,
        )
        assert is_valid is True
        assert error == ""

    def test_valid_token_passes(self):
        """Valid token with correct signature and timestamp passes."""
        secret = "test-secret-key-12345"
        user_id = "user123"
        role = "trader"
        strategies = "s1,s2"
        timestamp = int(time.time())
        token = _generate_internal_token(user_id, role, strategies, timestamp, secret)

        settings = MockSettings(
            internal_token_required=True,
            internal_token_secret=secret,
            internal_token_timestamp_tolerance_seconds=300,
        )
        is_valid, error = main._verify_internal_token(
            token=token,
            timestamp_str=str(timestamp),
            user_id=user_id,
            role=role,
            strategies=strategies,
            settings=settings,
        )
        assert is_valid is True
        assert error == ""

    def test_invalid_signature_fails(self):
        """Invalid token signature is rejected."""
        secret = "test-secret-key-12345"
        user_id = "user123"
        role = "trader"
        strategies = "s1"
        timestamp = int(time.time())
        wrong_token = "a" * 64  # Invalid signature

        settings = MockSettings(
            internal_token_required=True,
            internal_token_secret=secret,
            internal_token_timestamp_tolerance_seconds=300,
        )
        is_valid, error = main._verify_internal_token(
            token=wrong_token,
            timestamp_str=str(timestamp),
            user_id=user_id,
            role=role,
            strategies=strategies,
            settings=settings,
        )
        assert is_valid is False
        assert error == "invalid_signature"

    def test_expired_timestamp_fails(self):
        """Timestamp outside tolerance window is rejected."""
        secret = "test-secret-key-12345"
        user_id = "user123"
        role = "trader"
        strategies = "s1"
        # Timestamp from 10 minutes ago (outside 5 min tolerance)
        old_timestamp = int(time.time()) - 600
        token = _generate_internal_token(user_id, role, strategies, old_timestamp, secret)

        settings = MockSettings(
            internal_token_required=True,
            internal_token_secret=secret,
            internal_token_timestamp_tolerance_seconds=300,  # 5 minutes
        )
        is_valid, error = main._verify_internal_token(
            token=token,
            timestamp_str=str(old_timestamp),
            user_id=user_id,
            role=role,
            strategies=strategies,
            settings=settings,
        )
        assert is_valid is False
        assert error == "timestamp_expired"

    def test_future_timestamp_within_tolerance_passes(self):
        """Future timestamp within tolerance window passes."""
        secret = "test-secret-key-12345"
        user_id = "user123"
        role = "trader"
        strategies = "s1"
        # Timestamp 2 minutes in the future (within 5 min tolerance)
        future_timestamp = int(time.time()) + 120
        token = _generate_internal_token(user_id, role, strategies, future_timestamp, secret)

        settings = MockSettings(
            internal_token_required=True,
            internal_token_secret=secret,
            internal_token_timestamp_tolerance_seconds=300,
        )
        is_valid, error = main._verify_internal_token(
            token=token,
            timestamp_str=str(future_timestamp),
            user_id=user_id,
            role=role,
            strategies=strategies,
            settings=settings,
        )
        assert is_valid is True
        assert error == ""

    def test_missing_token_fails(self):
        """Missing token when required is rejected."""
        settings = MockSettings(
            internal_token_required=True,
            internal_token_secret="secret",
            internal_token_timestamp_tolerance_seconds=300,
        )
        is_valid, error = main._verify_internal_token(
            token=None,
            timestamp_str=str(int(time.time())),
            user_id="user123",
            role="trader",
            strategies="s1",
            settings=settings,
        )
        assert is_valid is False
        assert error == "missing_token"

    def test_missing_timestamp_fails(self):
        """Missing timestamp when required is rejected."""
        settings = MockSettings(
            internal_token_required=True,
            internal_token_secret="secret",
            internal_token_timestamp_tolerance_seconds=300,
        )
        is_valid, error = main._verify_internal_token(
            token="some-token",
            timestamp_str=None,
            user_id="user123",
            role="trader",
            strategies="s1",
            settings=settings,
        )
        assert is_valid is False
        assert error == "missing_timestamp"

    def test_invalid_timestamp_format_fails(self):
        """Non-integer timestamp is rejected."""
        settings = MockSettings(
            internal_token_required=True,
            internal_token_secret="secret",
            internal_token_timestamp_tolerance_seconds=300,
        )
        is_valid, error = main._verify_internal_token(
            token="some-token",
            timestamp_str="not-a-number",
            user_id="user123",
            role="trader",
            strategies="s1",
            settings=settings,
        )
        assert is_valid is False
        assert error == "invalid_timestamp_format"

    def test_missing_secret_fails(self):
        """Empty secret when required fails closed."""
        settings = MockSettings(
            internal_token_required=True,
            internal_token_secret="",  # Empty secret
            internal_token_timestamp_tolerance_seconds=300,
        )
        is_valid, error = main._verify_internal_token(
            token="some-token",
            timestamp_str=str(int(time.time())),
            user_id="user123",
            role="trader",
            strategies="s1",
            settings=settings,
        )
        assert is_valid is False
        assert error == "token_secret_not_configured"

    def test_case_insensitive_token_comparison(self):
        """Token comparison is case-insensitive."""
        secret = "test-secret-key-12345"
        user_id = "user123"
        role = "trader"
        strategies = "s1"
        timestamp = int(time.time())
        token = _generate_internal_token(user_id, role, strategies, timestamp, secret)

        settings = MockSettings(
            internal_token_required=True,
            internal_token_secret=secret,
            internal_token_timestamp_tolerance_seconds=300,
        )
        # Test with uppercase token
        is_valid, error = main._verify_internal_token(
            token=token.upper(),
            timestamp_str=str(timestamp),
            user_id=user_id,
            role=role,
            strategies=strategies,
            settings=settings,
        )
        assert is_valid is True
        assert error == ""

    def test_whitespace_in_headers_stripped(self):
        """Whitespace in user_id/role/strategies is stripped before signature."""
        secret = "test-secret-key-12345"
        user_id = "user123"
        role = "trader"
        strategies = "s1,s2"
        timestamp = int(time.time())
        # Generate token with clean values
        token = _generate_internal_token(user_id, role, strategies, timestamp, secret)

        settings = MockSettings(
            internal_token_required=True,
            internal_token_secret=secret,
            internal_token_timestamp_tolerance_seconds=300,
        )
        # Verify with whitespace-padded values
        is_valid, error = main._verify_internal_token(
            token=token,
            timestamp_str=f" {timestamp} ",
            user_id=f" {user_id} ",
            role=f" {role} ",
            strategies=f" {strategies} ",
            settings=settings,
        )
        assert is_valid is True
        assert error == ""

    def test_strategy_tampering_rejected(self):
        """Changing strategies after token generation is rejected."""
        secret = "test-secret-key-12345"
        user_id = "user123"
        role = "trader"
        original_strategies = "s1"
        timestamp = int(time.time())
        # Generate token with original strategies
        token = _generate_internal_token(user_id, role, original_strategies, timestamp, secret)

        settings = MockSettings(
            internal_token_required=True,
            internal_token_secret=secret,
            internal_token_timestamp_tolerance_seconds=300,
        )
        # Attempt to use token with different strategies (privilege escalation attempt)
        tampered_strategies = "s1,s2,admin_strategy"
        is_valid, error = main._verify_internal_token(
            token=token,
            timestamp_str=str(timestamp),
            user_id=user_id,
            role=role,
            strategies=tampered_strategies,
            settings=settings,
        )
        assert is_valid is False
        assert error == "invalid_signature"


class TestInternalTokenMiddleware:
    """Integration tests for populate_user_from_headers middleware with token validation."""

    def test_middleware_rejects_invalid_token_when_required(self, monkeypatch, test_client):
        """Middleware returns 401 when token validation fails."""
        mock_settings = MockSettings(
            internal_token_required=True,
            internal_token_secret="secret",
            internal_token_timestamp_tolerance_seconds=300,
        )

        with patch("apps.execution_gateway.main.get_settings", return_value=mock_settings):
            resp = test_client.get(
                "/health",
                headers={
                    "X-User-Role": "admin",
                    "X-User-Id": "user123",
                    # Missing X-Internal-Token
                },
            )
        assert resp.status_code == 401
        assert "Invalid or missing internal authentication token" in resp.json()["detail"]

    def test_middleware_passes_valid_token_when_required(self, monkeypatch, test_client):
        """Middleware passes request when token is valid."""
        secret = "test-secret-key"
        user_id = "user123"
        role = "admin"
        strategies = "s1,s2"
        timestamp = int(time.time())
        token = _generate_internal_token(user_id, role, strategies, timestamp, secret)

        mock_settings = MockSettings(
            internal_token_required=True,
            internal_token_secret=secret,
            internal_token_timestamp_tolerance_seconds=300,
        )

        with patch("apps.execution_gateway.main.get_settings", return_value=mock_settings):
            resp = test_client.get(
                "/health",
                headers={
                    "X-User-Role": role,
                    "X-User-Id": user_id,
                    "X-User-Strategies": strategies,
                    "X-Internal-Token": token,
                    "X-Request-Timestamp": str(timestamp),
                },
            )
        # Health endpoint should return 200 regardless of auth
        assert resp.status_code == 200

    def test_middleware_skips_validation_when_disabled(self, monkeypatch, test_client):
        """Middleware skips token validation when INTERNAL_TOKEN_REQUIRED=false."""
        mock_settings = MockSettings(
            internal_token_required=False,  # Disabled
            internal_token_secret="",
            internal_token_timestamp_tolerance_seconds=300,
        )

        with patch("apps.execution_gateway.main.get_settings", return_value=mock_settings):
            resp = test_client.get(
                "/health",
                headers={
                    "X-User-Role": "admin",
                    "X-User-Id": "user123",
                    # No token headers needed when disabled
                },
            )
        assert resp.status_code == 200

    def test_middleware_rejects_strategy_tampering(self, monkeypatch, test_client):
        """Middleware rejects when X-User-Strategies is changed after token generation."""
        secret = "test-secret-key"
        user_id = "user123"
        role = "admin"
        original_strategies = "s1"
        timestamp = int(time.time())
        # Generate token with original strategies
        token = _generate_internal_token(user_id, role, original_strategies, timestamp, secret)

        mock_settings = MockSettings(
            internal_token_required=True,
            internal_token_secret=secret,
            internal_token_timestamp_tolerance_seconds=300,
        )

        # Attempt to use token with different strategies (privilege escalation)
        with patch("apps.execution_gateway.main.get_settings", return_value=mock_settings):
            resp = test_client.get(
                "/health",
                headers={
                    "X-User-Role": role,
                    "X-User-Id": user_id,
                    "X-User-Strategies": "s1,s2,admin_strategy",  # Tampered!
                    "X-Internal-Token": token,
                    "X-Request-Timestamp": str(timestamp),
                },
            )
        assert resp.status_code == 401
        assert "Invalid or missing internal authentication token" in resp.json()["detail"]
