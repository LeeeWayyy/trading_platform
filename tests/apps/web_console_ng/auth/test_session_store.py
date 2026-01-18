from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from fakeredis.aioredis import FakeRedis
import redis.asyncio as redis

from apps.web_console_ng.auth.session_store import (
    ServerSessionStore,
    SessionValidationError,
    extract_session_id,
)


def _make_store(redis_client: FakeRedis) -> ServerSessionStore:
    return ServerSessionStore(
        redis_url="redis://localhost:6379/1",
        encryption_keys=[Fernet.generate_key()],
        signing_keys={"01": b"a" * 32, "02": b"b" * 32},
        current_signing_key_id="01",
        redis_client=redis_client,
    )


@pytest.fixture()
def redis_client() -> FakeRedis:
    return FakeRedis()


@pytest.fixture()
def session_store(redis_client: FakeRedis) -> ServerSessionStore:
    return _make_store(redis_client)


@pytest.mark.asyncio()
async def test_verify_cookie_and_extract_session_id(session_store: ServerSessionStore) -> None:
    cookie_value, _ = await session_store.create_session(
        {"user_id": "user-1"},
        {"user_agent": "ua"},
        "10.0.0.1",
    )

    session_id = session_store.verify_cookie(cookie_value)
    assert session_id is not None
    assert extract_session_id(cookie_value) == session_id

    tampered = cookie_value.replace("01:", "02:")
    assert session_store.verify_cookie(tampered) is None


@pytest.mark.asyncio()
async def test_validate_session_bad_signature_returns_none(
    session_store: ServerSessionStore,
) -> None:
    cookie_value, _ = await session_store.create_session(
        {"user_id": "user-1"},
        {"user_agent": "ua"},
        "10.0.0.1",
    )

    tampered = cookie_value[:-1] + ("0" if cookie_value[-1] != "0" else "1")
    assert await session_store.validate_session(tampered, "10.0.0.1", "ua") is None


@pytest.mark.asyncio()
async def test_validate_session_json_decode_error_invalidates(
    session_store: ServerSessionStore,
    redis_client: FakeRedis,
) -> None:
    session_id = "session-123"
    cookie_value = session_store._build_cookie_value(session_id)

    encrypted = session_store.fernet.encrypt(b"not-json")
    await redis_client.setex(f"{session_store.session_prefix}{session_id}", 60, encrypted)

    assert await session_store.validate_session(cookie_value, "10.0.0.1", "ua") is None
    assert await redis_client.get(f"{session_store.session_prefix}{session_id}") is None


@pytest.mark.asyncio()
async def test_check_rate_limit_fallback_when_eval_missing(
    session_store: ServerSessionStore,
) -> None:
    session_store.redis.eval = AsyncMock(side_effect=redis.RedisError("unknown command"))

    allowed = await session_store._check_rate_limit("10.0.0.9", "validate", 1)
    blocked = await session_store._check_rate_limit("10.0.0.9", "validate", 1)

    assert allowed is True
    assert blocked is False


@pytest.mark.asyncio()
async def test_validate_session_redis_error_raises(session_store: ServerSessionStore) -> None:
    session_store.redis.get = AsyncMock(side_effect=redis.RedisError("boom"))

    with pytest.raises(SessionValidationError):
        cookie_value = session_store._build_cookie_value("session-xyz")
        await session_store.validate_session(cookie_value, "10.0.0.1", "ua")


@pytest.mark.asyncio()
async def test_rotate_session_fallback_when_eval_missing(
    session_store: ServerSessionStore,
) -> None:
    cookie_value, _ = await session_store.create_session(
        {"user_id": "user-1"},
        {"user_agent": "ua"},
        "10.0.0.1",
    )
    session_id = extract_session_id(cookie_value)

    session_store.redis.eval = AsyncMock(side_effect=redis.RedisError("unknown command"))

    rotated = await session_store.rotate_session(session_id)
    assert rotated is not None
    new_cookie, new_csrf = rotated
    assert new_cookie != cookie_value
    assert new_csrf

    assert await session_store.redis.get(f"{session_store.session_prefix}{session_id}") is None


def test_extract_session_id_handles_dots() -> None:
    signed_cookie = "part1.part2.part3.01:signature"
    assert extract_session_id(signed_cookie) == "part1.part2.part3"


def test_extract_session_id_invalid_cookie_raises() -> None:
    with pytest.raises(ValueError, match="Empty cookie"):
        extract_session_id("")
    with pytest.raises(ValueError, match="missing signature"):
        extract_session_id("missing-signature")
    with pytest.raises(ValueError, match="empty session ID"):
        extract_session_id(".01:signature")
