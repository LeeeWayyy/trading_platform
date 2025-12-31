"""Tests for ServerSessionStore."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fakeredis.aioredis import FakeRedis

from apps.web_console_ng import config
from apps.web_console_ng.auth.session_store import RateLimitExceeded, ServerSessionStore


def _make_store(redis_client: FakeRedis, *, keys: list[bytes] | None = None) -> ServerSessionStore:
    encryption_keys = keys or [Fernet.generate_key()]
    signing_keys = {"01": b"a" * 32, "02": b"b" * 32}
    return ServerSessionStore(
        redis_url="redis://localhost:6379/1",
        encryption_keys=encryption_keys,
        signing_keys=signing_keys,
        current_signing_key_id="01",
        redis_client=redis_client,
    )


@pytest.mark.asyncio()
async def test_create_validate_rotate_invalidate() -> None:
    redis_client = FakeRedis()
    store = _make_store(redis_client)

    cookie_value, csrf = await store.create_session(
        {"user_id": "user-1", "role": "admin"},
        {"user_agent": "ua"},
        "10.0.0.1",
    )
    assert csrf

    session = await store.validate_session(cookie_value, "10.0.0.1", "ua")
    assert session is not None
    assert session["user"]["user_id"] == "user-1"

    session_id, _ = store._parse_cookie(cookie_value)
    assert session_id

    rotated = await store.rotate_session(session_id)
    assert rotated is not None
    new_cookie, new_csrf = rotated
    assert new_cookie != cookie_value
    assert new_csrf

    assert await store.validate_session(cookie_value, "10.0.0.1", "ua") is None
    assert await store.validate_session(new_cookie, "10.0.0.1", "ua") is not None

    await store.invalidate_session(session_id)


@pytest.mark.asyncio()
async def test_rate_limiting() -> None:
    redis_client = FakeRedis()
    store = _make_store(redis_client)

    for _ in range(10):
        await store.create_session({"user_id": "user-1"}, {}, "10.0.0.2")

    with pytest.raises(RateLimitExceeded):
        await store.create_session({"user_id": "user-1"}, {}, "10.0.0.2")


@pytest.mark.asyncio()
async def test_device_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_client = FakeRedis()
    store = _make_store(redis_client)

    monkeypatch.setattr(config, "DEVICE_BINDING_ENABLED", True)
    monkeypatch.setattr(config, "DEVICE_BINDING_SUBNET_MASK", 24)

    cookie_value, _ = await store.create_session(
        {"user_id": "user-1"},
        {"user_agent": "ua-1"},
        "10.0.0.5",
    )

    assert await store.validate_session(cookie_value, "10.0.0.8", "ua-1") is not None
    assert await store.validate_session(cookie_value, "10.0.1.5", "ua-1") is None
    assert await store.validate_session(cookie_value, "10.0.0.8", "ua-2") is None


@pytest.mark.asyncio()
async def test_encryption_key_rotation() -> None:
    redis_client = FakeRedis()
    key1 = Fernet.generate_key()
    key2 = Fernet.generate_key()

    store_v1 = _make_store(redis_client, keys=[key1])
    cookie_value, _ = await store_v1.create_session(
        {"user_id": "user-1"},
        {"user_agent": "ua"},
        "10.0.0.1",
    )

    store_v2 = _make_store(redis_client, keys=[key2, key1])
    session = await store_v2.validate_session(cookie_value, "10.0.0.1", "ua")
    assert session is not None
