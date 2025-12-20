"""Unit tests for libs.admin.api_keys."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import RedisError

from libs.admin.api_keys import (
    KEY_PREFIX_PATTERN,
    REVOKED_KEY_CACHE_TTL,
    ApiKeyScopes,
    generate_api_key,
    hash_api_key,
    is_key_revoked,
    parse_key_prefix,
    update_last_used,
    validate_api_key,
)


class _FakeCursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class _FakeDB:
    def __init__(self, row=None):
        self.row = row
        self.executed: list[tuple[str, tuple]] = []

    async def execute(self, query: str, params: tuple) -> _FakeCursor:
        self.executed.append((query, params))
        return _FakeCursor(self.row)


class _NoExecuteDB:
    async def execute(self, *_args, **_kwargs):
        raise AssertionError("DB should not be called")


def test_generate_api_key_format():
    full_key, prefix, salt = generate_api_key()

    assert len(full_key) == 43
    assert prefix.startswith("tp_live_")
    assert prefix.endswith(full_key[:8])
    assert KEY_PREFIX_PATTERN.match(prefix)
    assert len(salt) == 32


def test_generate_api_key_entropy():
    seen_keys = {generate_api_key()[0] for _ in range(10)}
    assert len(seen_keys) == 10  # All unique thanks to cryptographic randomness


def test_hash_verify_roundtrip():
    full_key, _, salt = generate_api_key()
    key_hash = hash_api_key(full_key, salt)

    assert validate_api_key(full_key, key_hash, salt) is True


def test_hash_wrong_key_fails():
    full_key, _, salt = generate_api_key()
    key_hash = hash_api_key(full_key, salt)
    wrong_key, _, _ = generate_api_key()

    assert validate_api_key(wrong_key, key_hash, salt) is False


def test_hash_wrong_salt_fails():
    full_key, _, salt = generate_api_key()
    key_hash = hash_api_key(full_key, salt)
    other_salt = generate_api_key()[2]

    assert validate_api_key(full_key, key_hash, other_salt) is False


def test_parse_key_prefix_valid():
    full_key, prefix, _ = generate_api_key()
    assert parse_key_prefix(full_key) == prefix


def test_parse_key_prefix_invalid():
    assert parse_key_prefix("invalid-key") is None
    assert parse_key_prefix("") is None


def test_api_key_scopes_validation():
    scopes = ApiKeyScopes(read_positions=True, write_orders=False)
    assert scopes.read_positions is True
    assert scopes.write_orders is False
    assert scopes.read_orders is False
    assert scopes.read_strategies is False


@pytest.mark.asyncio()
async def test_is_key_revoked_cache_hit():
    redis_client = AsyncMock()
    redis_client.exists.return_value = True
    db_pool = _FakeDB()

    assert await is_key_revoked("tp_live_abcdefgh", redis_client, db_pool) is True
    redis_client.exists.assert_awaited_once_with("api_key_revoked:tp_live_abcdefgh")
    assert db_pool.executed == []


@pytest.mark.asyncio()
async def test_is_key_revoked_cache_miss_db_revoked():
    revoked_time = datetime.now(UTC)
    db_pool = _FakeDB({"revoked_at": revoked_time})
    redis_client = AsyncMock()
    redis_client.exists.return_value = False

    assert await is_key_revoked("tp_live_rev0ked1", redis_client, db_pool) is True
    redis_client.setex.assert_awaited_once_with(
        "api_key_revoked:tp_live_rev0ked1", REVOKED_KEY_CACHE_TTL, "1"
    )


@pytest.mark.asyncio()
async def test_is_key_revoked_not_revoked():
    db_pool = _FakeDB({"revoked_at": None})
    redis_client = AsyncMock()
    redis_client.exists.return_value = False

    assert await is_key_revoked("tp_live_valid123", redis_client, db_pool) is False
    redis_client.setex.assert_not_awaited()


@pytest.mark.asyncio()
async def test_is_key_revoked_redis_unavailable():
    db_pool = _FakeDB({"revoked_at": None})
    redis_client = AsyncMock()
    redis_client.exists.side_effect = RedisError("down")

    assert await is_key_revoked("tp_live_valid123", redis_client, db_pool) is False


@pytest.mark.asyncio()
async def test_update_last_used_debounce():
    """When atomic SET NX EX returns False (key exists), debounce skips DB update."""
    redis_client = AsyncMock()
    # set() returns False/None when key already exists (nx=True)
    redis_client.set.return_value = False

    await update_last_used("tp_live_skip1234", _NoExecuteDB(), redis_client)
    redis_client.set.assert_awaited_once_with(
        "api_key_last_used:tp_live_skip1234", "1", nx=True, ex=60
    )


@pytest.mark.asyncio()
async def test_update_last_used_no_debounce():
    """When atomic SET NX EX returns True (key was set), DB update proceeds."""
    redis_client = AsyncMock()
    # set() returns True when key was set (didn't exist before)
    redis_client.set.return_value = True
    db_pool = _FakeDB()

    await update_last_used("tp_live_upd1234", db_pool, redis_client)

    redis_client.set.assert_awaited_once_with(
        "api_key_last_used:tp_live_upd1234", "1", nx=True, ex=60
    )
    assert len(db_pool.executed) == 1
    _, params = db_pool.executed[0]
    timestamp, prefix = params
    assert prefix == "tp_live_upd1234"
    assert timestamp.second == 0
    assert timestamp.microsecond == 0
