"""Unit tests for libs.platform.web_console_auth.session_store."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from libs.platform.web_console_auth import session_store
from libs.platform.web_console_auth.session_store import RedisSessionStore, SessionData


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.ttl: dict[str, int] = {}
        self.setex_calls: list[tuple[str, int, bytes]] = []

    async def setex(self, key: str, ttl: int, value: str | bytes) -> None:
        data = value.encode() if isinstance(value, str) else value
        self.store[key] = data
        self.ttl[key] = ttl
        self.setex_calls.append((key, ttl, data))

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                self.ttl.pop(key, None)
                deleted += 1
        return deleted

    async def scan(self, cursor: int, match: str | None = None, count: int = 1000):
        keys = list(self.store.keys())
        if match:
            if match.endswith("*"):
                prefix = match[:-1]
                keys = [key for key in keys if key.startswith(prefix)]
            else:
                keys = [key for key in keys if key == match]
        return 0, keys


def _sample_session(now: datetime) -> SessionData:
    return SessionData(
        access_token="access",
        refresh_token="refresh",
        id_token="id",
        user_id="auth0|123",
        email="user@example.com",
        created_at=now,
        last_activity=now,
        ip_address="192.168.1.10",
        user_agent="Mozilla/5.0",
        access_token_expires_at=now + timedelta(hours=1),
        role="viewer",
        strategies=["alpha"],
        session_version=1,
    )


def _freeze_time(monkeypatch: pytest.MonkeyPatch, now: datetime) -> None:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            if tz is not None:
                return now
            return now.replace(tzinfo=None)

    monkeypatch.setattr(session_store, "datetime", FrozenDateTime)


def test_init_rejects_invalid_key_lengths() -> None:
    redis_client = FakeRedis()
    with pytest.raises(ValueError, match="Encryption key must be exactly 32 bytes"):
        RedisSessionStore(redis_client, encryption_key=b"short")
    with pytest.raises(ValueError, match="Secondary key must be exactly 32 bytes"):
        RedisSessionStore(redis_client, encryption_key=b"a" * 32, secondary_key=b"short")


@pytest.mark.asyncio()
async def test_create_and_get_session_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_client = FakeRedis()
    key = b"k" * 32
    store = RedisSessionStore(redis_client, encryption_key=key)

    fixed_now = datetime(2025, 1, 1, tzinfo=UTC)
    _freeze_time(monkeypatch, fixed_now)

    data = _sample_session(fixed_now)
    await store.create_session("sess1", data)

    session = await store.get_session("sess1", current_ip="192.168.1.10", current_user_agent="Mozilla/5.0")

    assert session is not None
    assert session.user_id == data.user_id
    assert session.last_activity == fixed_now
    assert redis_client.ttl["session:sess1"] == int(store.absolute_timeout.total_seconds())


@pytest.mark.asyncio()
async def test_get_session_updates_activity_with_remaining_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_client = FakeRedis()
    key = b"k" * 32
    store = RedisSessionStore(redis_client, encryption_key=key, absolute_timeout_hours=4)

    fixed_now = datetime(2025, 1, 1, 1, 0, 0, tzinfo=UTC)
    _freeze_time(monkeypatch, fixed_now)

    created_at = fixed_now - timedelta(hours=1)
    data = _sample_session(fixed_now)
    data.created_at = created_at
    await store.create_session("sess2", data)

    session = await store.get_session("sess2")

    assert session is not None
    assert session.last_activity == fixed_now

    expected_remaining = int((store.absolute_timeout - (fixed_now - created_at)).total_seconds())
    assert redis_client.setex_calls[-1][1] == expected_remaining


@pytest.mark.asyncio()
async def test_get_session_rejects_ip_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_client = FakeRedis()
    key = b"k" * 32
    store = RedisSessionStore(redis_client, encryption_key=key)

    fixed_now = datetime(2025, 1, 1, tzinfo=UTC)
    _freeze_time(monkeypatch, fixed_now)

    data = _sample_session(fixed_now)
    await store.create_session("sess3", data)

    session = await store.get_session("sess3", current_ip="10.0.0.1")

    assert session is None
    assert "session:sess3" not in redis_client.store


@pytest.mark.asyncio()
async def test_get_session_rejects_user_agent_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_client = FakeRedis()
    key = b"k" * 32
    store = RedisSessionStore(redis_client, encryption_key=key)

    fixed_now = datetime(2025, 1, 1, tzinfo=UTC)
    _freeze_time(monkeypatch, fixed_now)

    data = _sample_session(fixed_now)
    await store.create_session("sess4", data)

    session = await store.get_session("sess4", current_user_agent="Different")

    assert session is None
    assert "session:sess4" not in redis_client.store


@pytest.mark.asyncio()
async def test_get_session_enforces_idle_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_client = FakeRedis()
    key = b"k" * 32
    store = RedisSessionStore(redis_client, encryption_key=key, idle_timeout_minutes=15)

    fixed_now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
    _freeze_time(monkeypatch, fixed_now)

    stale_activity = fixed_now - timedelta(minutes=16)
    data = _sample_session(fixed_now)
    data.last_activity = stale_activity

    await store.create_session("sess5", data)

    session = await store.get_session("sess5")

    assert session is None
    assert "session:sess5" not in redis_client.store


@pytest.mark.asyncio()
async def test_get_session_enforces_absolute_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_client = FakeRedis()
    key = b"k" * 32
    store = RedisSessionStore(redis_client, encryption_key=key, absolute_timeout_hours=4)

    fixed_now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
    _freeze_time(monkeypatch, fixed_now)

    created_at = fixed_now - timedelta(hours=5)
    data = _sample_session(fixed_now)
    data.created_at = created_at

    await store.create_session("sess6", data)

    session = await store.get_session("sess6")

    assert session is None
    assert "session:sess6" not in redis_client.store


@pytest.mark.asyncio()
async def test_update_session_fields_persists_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_client = FakeRedis()
    key = b"k" * 32
    store = RedisSessionStore(redis_client, encryption_key=key)

    fixed_now = datetime(2025, 1, 1, tzinfo=UTC)
    _freeze_time(monkeypatch, fixed_now)

    data = _sample_session(fixed_now)
    await store.create_session("sess7", data)

    updated = await store.update_session_fields("sess7", role="admin")

    assert updated is True
    session = await store.get_session("sess7", update_activity=False)
    assert session is not None
    assert session.role == "admin"


@pytest.mark.asyncio()
async def test_update_session_fields_fails_closed_on_missing_session() -> None:
    redis_client = FakeRedis()
    key = b"k" * 32
    store = RedisSessionStore(redis_client, encryption_key=key)

    updated = await store.update_session_fields("missing", role="admin")

    assert updated is False


def test_decrypt_falls_back_to_secondary_key() -> None:
    redis_client = FakeRedis()
    primary = b"p" * 32
    secondary = b"s" * 32
    store = RedisSessionStore(redis_client, encryption_key=primary, secondary_key=secondary)

    payload = "secret"
    nonce = b"\x00" * 12
    ciphertext = AESGCM(secondary).encrypt(nonce, payload.encode(), None)
    encrypted = base64.b64encode(nonce + ciphertext).decode()

    original_primary_decrypt = store.cipher_primary.decrypt
    store.cipher_primary.decrypt = lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad"))  # type: ignore[assignment]
    try:
        assert store._decrypt(encrypted) == payload
    finally:
        store.cipher_primary.decrypt = original_primary_decrypt


@pytest.mark.asyncio()
async def test_cleanup_all_sessions_deletes_matching_prefix() -> None:
    redis_client = FakeRedis()
    key = b"k" * 32
    store = RedisSessionStore(redis_client, encryption_key=key)

    redis_client.store["session:one"] = b"data"
    redis_client.store["session:two"] = b"data"
    redis_client.store["other:three"] = b"data"

    deleted = await store.cleanup_all_sessions()

    assert deleted == 2
    assert "session:one" not in redis_client.store
    assert "session:two" not in redis_client.store
    assert "other:three" in redis_client.store
