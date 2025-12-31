"""Tests for UserStateManager."""

from __future__ import annotations

import pytest
from fakeredis.aioredis import FakeRedis

from apps.web_console_ng.core.state_manager import UserStateManager


@pytest.mark.asyncio()
async def test_save_load_state() -> None:
    redis_client = FakeRedis(decode_responses=True)
    manager = UserStateManager(redis_url="redis://localhost:6379/1", ttl_seconds=60, redis_client=redis_client)

    await manager.save_state("user-1", "prefs", {"theme": "light"})
    state = await manager.load_state("user-1", "prefs")
    assert state == {"theme": "light"}


@pytest.mark.asyncio()
async def test_missing_key_returns_none() -> None:
    redis_client = FakeRedis(decode_responses=True)
    manager = UserStateManager(redis_url="redis://localhost:6379/1", ttl_seconds=60, redis_client=redis_client)

    assert await manager.load_state("user-1", "missing") is None


@pytest.mark.asyncio()
async def test_ttl_expiry() -> None:
    """Test that TTL is set correctly on keys.

    Note: FakeRedis doesn't actively expire keys like real Redis does.
    We verify TTL was set rather than actual expiration behavior.
    """
    redis_client = FakeRedis(decode_responses=True)
    manager = UserStateManager(redis_url="redis://localhost:6379/1", ttl_seconds=60, redis_client=redis_client)

    await manager.save_state("user-1", "prefs", {"theme": "dark"})

    # Verify key exists and TTL was set
    ttl = await redis_client.ttl("ng_ui_state:user-1:prefs")
    assert ttl > 0, "TTL should be set on the key"
    assert ttl <= 60, "TTL should not exceed configured value"
