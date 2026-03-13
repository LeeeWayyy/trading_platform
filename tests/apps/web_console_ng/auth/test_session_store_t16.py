"""Unit tests for session store T16.2 features: force-logout and role-update."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from fakeredis.aioredis import FakeRedis

from apps.web_console_ng.auth.session_store import (
    ServerSessionStore,
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


# =============================================================================
# invalidate_redis_sessions_for_user tests
# =============================================================================


@pytest.mark.asyncio()
async def test_invalidate_sessions_for_user_deletes_all(
    session_store: ServerSessionStore,
    redis_client: FakeRedis,
) -> None:
    """SMEMBERS returns 3 session ids, all get deleted via pipeline."""
    user_id = "user-bulk-delete"

    # Create 3 sessions for the same user
    cookies = []
    for i in range(3):
        cookie_value, _ = await session_store.create_session(
            {"user_id": user_id, "username": f"user{i}"},
            {"user_agent": "ua"},
            "10.0.0.1",
        )
        cookies.append(cookie_value)

    # Verify the reverse index has 3 entries
    index_key = f"{session_store.user_sessions_prefix}{user_id}"
    members = await redis_client.smembers(index_key)
    assert len(members) == 3

    # Verify all 3 session keys exist in Redis
    for cookie in cookies:
        sid = extract_session_id(cookie)
        data = await redis_client.get(f"{session_store.session_prefix}{sid}")
        assert data is not None

    # Invalidate all sessions for the user
    count = await session_store.invalidate_redis_sessions_for_user(user_id)
    assert count == 3

    # All session keys should be deleted
    for cookie in cookies:
        sid = extract_session_id(cookie)
        data = await redis_client.get(f"{session_store.session_prefix}{sid}")
        assert data is None

    # Reverse index should also be deleted
    remaining = await redis_client.smembers(index_key)
    assert len(remaining) == 0


@pytest.mark.asyncio()
async def test_invalidate_sessions_for_user_empty(
    session_store: ServerSessionStore,
) -> None:
    """No sessions in reverse index returns 0."""
    count = await session_store.invalidate_redis_sessions_for_user("nonexistent-user")
    assert count == 0


# =============================================================================
# update_session_role tests
# =============================================================================


@pytest.mark.asyncio()
async def test_update_session_role_success(
    session_store: ServerSessionStore,
    redis_client: FakeRedis,
) -> None:
    """Session exists, role gets updated in encrypted payload."""
    # Create a session with role=viewer
    cookie_value, _ = await session_store.create_session(
        {"user_id": "user-role", "role": "viewer"},
        {"user_agent": "ua"},
        "10.0.0.1",
    )
    session_id = extract_session_id(cookie_value)

    # Update role to admin
    result = await session_store.update_session_role(session_id, "admin")
    assert result is True

    # Verify the encrypted payload now has the new role
    raw = await redis_client.get(f"{session_store.session_prefix}{session_id}")
    assert raw is not None
    decrypted = json.loads(session_store.fernet.decrypt(raw).decode("utf-8"))
    assert decrypted["user"]["role"] == "admin"


@pytest.mark.asyncio()
async def test_update_session_role_same_role_returns_true(
    session_store: ServerSessionStore,
) -> None:
    """When the session already has the requested role, returns True without re-encrypting."""
    cookie_value, _ = await session_store.create_session(
        {"user_id": "user-same", "role": "admin"},
        {"user_agent": "ua"},
        "10.0.0.1",
    )
    session_id = extract_session_id(cookie_value)

    result = await session_store.update_session_role(session_id, "admin")
    assert result is True


@pytest.mark.asyncio()
async def test_update_session_role_not_found(
    session_store: ServerSessionStore,
) -> None:
    """Session doesn't exist, returns False."""
    result = await session_store.update_session_role("nonexistent-session-id", "admin")
    assert result is False


@pytest.mark.asyncio()
async def test_update_session_role_preserves_ttl(
    session_store: ServerSessionStore,
    redis_client: FakeRedis,
) -> None:
    """After role update, the Redis key retains its TTL."""
    cookie_value, _ = await session_store.create_session(
        {"user_id": "user-ttl", "role": "viewer"},
        {"user_agent": "ua"},
        "10.0.0.1",
    )
    session_id = extract_session_id(cookie_value)
    session_key = f"{session_store.session_prefix}{session_id}"

    # Get the TTL before update
    ttl_before = await redis_client.ttl(session_key)
    assert ttl_before > 0

    result = await session_store.update_session_role(session_id, "admin")
    assert result is True

    # TTL should be preserved (allow 1 second tolerance)
    ttl_after = await redis_client.ttl(session_key)
    assert ttl_after > 0
    assert abs(ttl_after - ttl_before) <= 1


@pytest.mark.asyncio()
async def test_update_session_role_deleted_key_returns_false(
    session_store: ServerSessionStore,
    redis_client: FakeRedis,
) -> None:
    """If the session key is deleted between GET and SETEX, returns False (no recreation)."""
    # Create a session
    cookie_value, _ = await session_store.create_session(
        {"user_id": "user-deleted", "role": "viewer"},
        {"user_agent": "ua"},
        "10.0.0.1",
    )
    session_id = extract_session_id(cookie_value)
    session_key = f"{session_store.session_prefix}{session_id}"

    # Read the encrypted data, then delete the key (simulating force-logout)
    raw = await redis_client.get(session_key)
    assert raw is not None
    await redis_client.delete(session_key)

    # Now manually replicate the update_session_role flow:
    # The key is gone, so TTL returns -2 and update should return False
    result = await session_store.update_session_role(session_id, "admin")
    assert result is False

    # Verify the key was NOT recreated
    data = await redis_client.get(session_key)
    assert data is None


@pytest.mark.asyncio()
async def test_invalidate_then_update_does_not_recreate(
    session_store: ServerSessionStore,
    redis_client: FakeRedis,
) -> None:
    """Force-logout followed by role-update must not recreate the session."""
    user_id = "user-invalidate-update"
    cookie_value, _ = await session_store.create_session(
        {"user_id": user_id, "role": "viewer"},
        {"user_agent": "ua"},
        "10.0.0.1",
    )
    session_id = extract_session_id(cookie_value)
    session_key = f"{session_store.session_prefix}{session_id}"

    # Force-logout: remove all sessions
    count = await session_store.invalidate_redis_sessions_for_user(user_id)
    assert count == 1

    # Session key should be gone
    assert await redis_client.get(session_key) is None

    # Attempted role update must NOT recreate the session
    result = await session_store.update_session_role(session_id, "admin")
    assert result is False
    assert await redis_client.get(session_key) is None


# =============================================================================
# create_session reverse index tracking tests
# =============================================================================


@pytest.mark.asyncio()
async def test_create_session_tracks_reverse_index(
    session_store: ServerSessionStore,
    redis_client: FakeRedis,
) -> None:
    """After create_session, session_id is in the user's SET."""
    user_id = "user-index-create"
    cookie_value, _ = await session_store.create_session(
        {"user_id": user_id, "username": "alice"},
        {"user_agent": "ua"},
        "10.0.0.1",
    )
    session_id = extract_session_id(cookie_value)

    # Check the reverse index SET
    index_key = f"{session_store.user_sessions_prefix}{user_id}"
    members = await redis_client.smembers(index_key)
    member_strs = {m.decode("utf-8") if isinstance(m, bytes) else str(m) for m in members}
    assert session_id in member_strs


@pytest.mark.asyncio()
async def test_create_session_no_reverse_index_without_user_id(
    session_store: ServerSessionStore,
    redis_client: FakeRedis,
) -> None:
    """When user_data has no user_id, no reverse index is created."""
    cookie_value, _ = await session_store.create_session(
        {"username": "anonymous"},
        {"user_agent": "ua"},
        "10.0.0.1",
    )

    # No reverse index keys should exist (scan for the prefix)
    keys = []
    async for key in redis_client.scan_iter(f"{session_store.user_sessions_prefix}*"):
        keys.append(key)
    assert len(keys) == 0


# =============================================================================
# invalidate_session reverse index cleanup tests
# =============================================================================


@pytest.mark.asyncio()
async def test_invalidate_session_cleans_reverse_index(
    session_store: ServerSessionStore,
    redis_client: FakeRedis,
) -> None:
    """After invalidate_session, session_id is removed from the user's SET."""
    user_id = "user-index-invalidate"
    cookie_value, _ = await session_store.create_session(
        {"user_id": user_id, "username": "bob"},
        {"user_agent": "ua"},
        "10.0.0.1",
    )
    session_id = extract_session_id(cookie_value)

    # Verify session_id is in reverse index
    index_key = f"{session_store.user_sessions_prefix}{user_id}"
    members_before = await redis_client.smembers(index_key)
    assert len(members_before) == 1

    # Invalidate the session
    await session_store.invalidate_session(session_id)

    # Session key should be gone
    data = await redis_client.get(f"{session_store.session_prefix}{session_id}")
    assert data is None

    # Reverse index should no longer contain this session_id
    members_after = await redis_client.smembers(index_key)
    member_strs = {m.decode("utf-8") if isinstance(m, bytes) else str(m) for m in members_after}
    assert session_id not in member_strs


@pytest.mark.asyncio()
async def test_invalidate_session_reverse_index_partial_cleanup(
    session_store: ServerSessionStore,
    redis_client: FakeRedis,
) -> None:
    """When user has 2 sessions and one is invalidated, only that one is removed."""
    user_id = "user-partial"

    # Create 2 sessions
    cookie1, _ = await session_store.create_session(
        {"user_id": user_id}, {"user_agent": "ua"}, "10.0.0.1"
    )
    cookie2, _ = await session_store.create_session(
        {"user_id": user_id}, {"user_agent": "ua"}, "10.0.0.1"
    )
    sid1 = extract_session_id(cookie1)
    sid2 = extract_session_id(cookie2)

    index_key = f"{session_store.user_sessions_prefix}{user_id}"
    members_before = await redis_client.smembers(index_key)
    assert len(members_before) == 2

    # Invalidate only session 1
    await session_store.invalidate_session(sid1)

    members_after = await redis_client.smembers(index_key)
    member_strs = {m.decode("utf-8") if isinstance(m, bytes) else str(m) for m in members_after}
    assert sid1 not in member_strs
    assert sid2 in member_strs


# =============================================================================
# rotate_session reverse index tracking tests
# =============================================================================


@pytest.mark.asyncio()
async def test_rotate_session_updates_reverse_index(
    session_store: ServerSessionStore,
    redis_client: FakeRedis,
) -> None:
    """After rotation, old session_id removed and new session_id added to reverse index."""
    user_id = "user-rotate-idx"
    cookie_value, _ = await session_store.create_session(
        {"user_id": user_id, "username": "carol"},
        {"user_agent": "ua"},
        "10.0.0.1",
    )
    old_sid = extract_session_id(cookie_value)

    index_key = f"{session_store.user_sessions_prefix}{user_id}"

    # Verify old session_id is in the reverse index
    members_before = await redis_client.smembers(index_key)
    before_strs = {m.decode("utf-8") if isinstance(m, bytes) else str(m) for m in members_before}
    assert old_sid in before_strs

    # Rotate the session (use fallback path since fakeredis may not support Lua)
    import redis.asyncio as aioredis

    session_store.redis.eval = AsyncMock(side_effect=aioredis.RedisError("unknown command"))
    result = await session_store.rotate_session(old_sid)
    assert result is not None
    new_cookie, _ = result
    new_sid = extract_session_id(new_cookie)

    # Old session_id should be removed, new one added
    members_after = await redis_client.smembers(index_key)
    after_strs = {m.decode("utf-8") if isinstance(m, bytes) else str(m) for m in members_after}
    assert old_sid not in after_strs
    assert new_sid in after_strs
