"""Tests for GatewayAuthenticator service-to-service authentication."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fakeredis import FakeRedis as SyncFakeRedis
from fakeredis.aioredis import FakeRedis as AsyncFakeRedis

from libs.web_console_auth.config import AuthConfig
from libs.web_console_auth.exceptions import (
    ImmatureSignatureError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidSignatureError,
    InvalidTokenError,
    MissingJtiError,
    SessionExpiredError,
    SubjectMismatchError,
    TokenExpiredError,
    TokenReplayedError,
    TokenRevokedError,
)
from libs.web_console_auth.gateway_auth import GatewayAuthenticator
from libs.web_console_auth.jwt_manager import JWTManager
from libs.web_console_auth.permissions import Role


def _generate_rsa_pair(tmp_path: Path) -> tuple[Path, Path]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    priv_path = tmp_path / "priv.key"
    pub_path = tmp_path / "pub.pem"

    priv_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    pub_path.write_bytes(
        public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return priv_path, pub_path


class _FakeCursor:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    async def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[Any]:
        return self._rows


class _FakeDB:
    def __init__(self, role: str = "operator", strategies: list[str] | None = None, session_version: int = 1):
        self.role = role
        self.strategies = strategies or ["alpha"]
        self.session_version = session_version

    async def execute(self, query: str, params: tuple[Any, ...]) -> _FakeCursor:
        lowered = query.lower()
        if "session_version" in lowered:
            return _FakeCursor([{"session_version": self.session_version}])
        if "strategy_id" in lowered:
            return _FakeCursor([{"strategy_id": s} for s in self.strategies])
        if "role" in lowered:
            return _FakeCursor([{"role": self.role}])
        raise ValueError(f"Unexpected query: {query}")


@pytest.fixture()
def rsa_paths(tmp_path):
    return _generate_rsa_pair(tmp_path)


@pytest.fixture()
def jwt_manager(rsa_paths):
    priv, pub = rsa_paths
    config = AuthConfig(
        jwt_private_key_path=priv,
        jwt_public_key_path=pub,
        jwt_audience="execution-gateway",
        jwt_issuer="trading-platform-web-console",
    )
    return JWTManager(config, SyncFakeRedis())


@pytest.fixture()
def async_redis():
    return AsyncFakeRedis()


@pytest.fixture()
def gateway_auth(jwt_manager, async_redis):
    return GatewayAuthenticator(jwt_manager=jwt_manager, db_pool=_FakeDB(), redis_client=async_redis)


def _make_service_token(jwt_manager: JWTManager, *, overrides: dict[str, Any] | None = None) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": "user-123",
        "iss": jwt_manager.config.jwt_issuer,
        "aud": jwt_manager.config.jwt_audience,
        "exp": int((now + timedelta(seconds=60)).timestamp()),
        "nbf": int((now - timedelta(seconds=1)).timestamp()),
        "iat": int(now.timestamp()),
        "jti": str(uuid.uuid4()),
        "type": "service",
    }
    if overrides:
        payload.update(overrides)
        # allow explicit removal of a claim
        for key, value in list(payload.items()):
            if value is None:
                payload.pop(key, None)
    token = jwt.encode(payload, jwt_manager.private_key, algorithm=jwt_manager.config.jwt_algorithm)
    return token


@pytest.mark.asyncio()
async def test_authenticate_success(gateway_auth):
    token = _make_service_token(gateway_auth.jwt_manager)

    user = await gateway_auth.authenticate(
        token=token,
        x_user_id="user-123",
        x_request_id="req-1",
        x_session_version=1,
    )

    assert user.user_id == "user-123"
    assert user.role is Role.OPERATOR
    assert user.strategies == ["alpha"]
    assert user.request_id == "req-1"


@pytest.mark.asyncio()
async def test_invalid_signature_rejected(jwt_manager, async_redis, tmp_path):
    # Build authenticator with trusted keys
    authenticator = GatewayAuthenticator(jwt_manager, _FakeDB(), async_redis)

    # Sign token with a different key
    alt_priv, _ = _generate_rsa_pair(tmp_path)
    alt_private_key = serialization.load_pem_private_key(
        open(alt_priv, "rb").read(), password=None
    )
    token = jwt.encode(
        {"sub": "user-123", "iss": jwt_manager.config.jwt_issuer, "aud": jwt_manager.config.jwt_audience,
         "exp": int(datetime.now(UTC).timestamp()) + 60, "jti": str(uuid.uuid4()), "type": "service"},
        alt_private_key,
        algorithm=jwt_manager.config.jwt_algorithm,
    )

    with pytest.raises(InvalidSignatureError):
        await authenticator.authenticate(token, "user-123", "req-2", 1)


@pytest.mark.asyncio()
async def test_issuer_mismatch(jwt_manager, async_redis):
    authenticator = GatewayAuthenticator(jwt_manager, _FakeDB(), async_redis)
    token = _make_service_token(jwt_manager, overrides={"iss": "unknown-issuer"})

    with pytest.raises(InvalidIssuerError):
        await authenticator.authenticate(token, "user-123", "req-3", 1)


@pytest.mark.asyncio()
async def test_audience_mismatch(jwt_manager, async_redis):
    authenticator = GatewayAuthenticator(jwt_manager, _FakeDB(), async_redis)
    token = _make_service_token(jwt_manager, overrides={"aud": "other-service"})

    with pytest.raises(InvalidAudienceError):
        await authenticator.authenticate(token, "user-123", "req-4", 1)


@pytest.mark.asyncio()
async def test_subject_binding_enforced(jwt_manager, async_redis):
    authenticator = GatewayAuthenticator(jwt_manager, _FakeDB(), async_redis)
    token = _make_service_token(jwt_manager)

    with pytest.raises(SubjectMismatchError):
        await authenticator.authenticate(token, "different-user", "req-5", 1)


@pytest.mark.asyncio()
async def test_one_time_jti_enforced(jwt_manager, async_redis):
    authenticator = GatewayAuthenticator(jwt_manager, _FakeDB(), async_redis)
    token = _make_service_token(jwt_manager)

    await authenticator.authenticate(token, "user-123", "req-6", 1)
    with pytest.raises(TokenReplayedError):
        await authenticator.authenticate(token, "user-123", "req-6b", 1)


@pytest.mark.asyncio()
async def test_missing_jti_rejected(jwt_manager, async_redis):
    authenticator = GatewayAuthenticator(jwt_manager, _FakeDB(), async_redis)
    token = _make_service_token(jwt_manager, overrides={"jti": None})

    with pytest.raises(MissingJtiError):
        await authenticator.authenticate(token, "user-123", "req-7", 1)


@pytest.mark.asyncio()
async def test_session_version_mismatch(jwt_manager, async_redis):
    authenticator = GatewayAuthenticator(jwt_manager, _FakeDB(session_version=2), async_redis)
    token = _make_service_token(jwt_manager)

    with pytest.raises(SessionExpiredError):
        await authenticator.authenticate(token, "user-123", "req-8", 1)


@pytest.mark.asyncio()
async def test_strategy_fetching(jwt_manager, async_redis):
    db = _FakeDB(role="viewer", strategies=["strat_a", "strat_b"], session_version=1)
    authenticator = GatewayAuthenticator(jwt_manager, db, async_redis)
    token = _make_service_token(jwt_manager)

    user = await authenticator.authenticate(token, "user-123", "req-9", 1)

    assert user.role is Role.VIEWER
    assert user.strategies == ["strat_a", "strat_b"]


@pytest.mark.asyncio()
async def test_type_must_be_service(jwt_manager, async_redis):
    authenticator = GatewayAuthenticator(jwt_manager, _FakeDB(), async_redis)
    token = _make_service_token(jwt_manager, overrides={"type": "access"})

    with pytest.raises(InvalidTokenError):
        await authenticator.authenticate(token, "user-123", "req-10", 1)


@pytest.mark.asyncio()
async def test_revoked_token_rejected(gateway_auth):
    token = _make_service_token(gateway_auth.jwt_manager)
    payload = jwt.decode(
        token,
        options={"verify_signature": False},
    )
    jti = payload["jti"]
    blacklist_key = f"{gateway_auth.jwt_manager.config.redis_blacklist_prefix}{jti}"
    gateway_auth.jwt_manager.redis.set(blacklist_key, "revoked")

    with pytest.raises(TokenRevokedError):
        await gateway_auth.authenticate(token, "user-123", "req-11", 1)


@pytest.mark.asyncio()
async def test_expired_token_rejected(jwt_manager, async_redis):
    authenticator = GatewayAuthenticator(jwt_manager, _FakeDB(), async_redis)
    past = datetime.now(UTC) - timedelta(minutes=5)
    token = _make_service_token(jwt_manager, overrides={"exp": int(past.timestamp())})

    with pytest.raises(TokenExpiredError):
        await authenticator.authenticate(token, "user-123", "req-12", 1)


@pytest.mark.asyncio()
async def test_immature_token_rejected(jwt_manager, async_redis):
    authenticator = GatewayAuthenticator(jwt_manager, _FakeDB(), async_redis)
    future = datetime.now(UTC) + timedelta(minutes=5)
    token = _make_service_token(jwt_manager, overrides={"nbf": int(future.timestamp())})

    with pytest.raises(ImmatureSignatureError):
        await authenticator.authenticate(token, "user-123", "req-13", 1)
