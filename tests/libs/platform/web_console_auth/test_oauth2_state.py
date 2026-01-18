"""Unit tests for libs.platform.web_console_auth.oauth2_state."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from libs.platform.web_console_auth.oauth2_state import OAuth2State, OAuth2StateStore


class _Pipeline:
    def __init__(self, redis_client: FakeRedis) -> None:
        self._redis = redis_client
        self._ops: list[tuple[str, str]] = []

    async def __aenter__(self) -> _Pipeline:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, key: str) -> None:
        self._ops.append(("get", key))

    async def delete(self, key: str) -> None:
        self._ops.append(("delete", key))

    async def execute(self) -> list[object]:
        results: list[object] = []
        for op, key in self._ops:
            if op == "get":
                results.append(self._redis.store.get(key))
            elif op == "delete":
                results.append(self._redis.delete_sync(key))
        return results


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

    def delete_sync(self, key: str) -> int:
        if key in self.store:
            del self.store[key]
            self.ttl.pop(key, None)
            return 1
        return 0

    def pipeline(self) -> _Pipeline:
        return _Pipeline(self)


def _sample_state() -> OAuth2State:
    return OAuth2State(
        state="state-123",
        code_verifier="verifier",
        nonce="nonce",
        code_challenge="challenge",
        redirect_uri="https://example.com/callback",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )


@pytest.mark.asyncio()
async def test_store_state_persists_payload_and_ttl() -> None:
    redis_client = FakeRedis()
    store = OAuth2StateStore(redis_client, ttl_seconds=321)

    state = _sample_state()
    await store.store_state(state)

    assert redis_client.setex_calls
    key, ttl, payload = redis_client.setex_calls[0]
    assert key == "oauth_state:state-123"
    assert ttl == 321

    loaded = OAuth2State.model_validate_json(payload)
    assert loaded == state


@pytest.mark.asyncio()
async def test_get_and_delete_state_returns_and_clears_entry() -> None:
    redis_client = FakeRedis()
    store = OAuth2StateStore(redis_client)

    state = _sample_state()
    await store.store_state(state)

    fetched = await store.get_and_delete_state(state.state)

    assert fetched == state
    assert "oauth_state:state-123" not in redis_client.store


@pytest.mark.asyncio()
async def test_get_and_delete_state_missing_returns_none() -> None:
    redis_client = FakeRedis()
    store = OAuth2StateStore(redis_client)

    fetched = await store.get_and_delete_state("missing")

    assert fetched is None
    assert redis_client.store == {}
