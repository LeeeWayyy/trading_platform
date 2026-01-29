from __future__ import annotations

import json

import pytest

from apps.web_console_ng.core.level2_websocket import Level2WebSocketService, l2_channel


class FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, json.loads(message)))


class FakeRedisStore:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis

    async def get_master(self) -> FakeRedis:
        return self._redis


@pytest.mark.asyncio()
async def test_symbol_cap_enforced() -> None:
    service = Level2WebSocketService(max_symbols=1, mock_mode=True)
    service._running = True

    ok = await service.subscribe("user1", "AAPL")
    assert ok is True

    denied = await service.subscribe("user1", "MSFT")
    assert denied is False


@pytest.mark.asyncio()
async def test_publish_update_fans_out_to_users() -> None:
    fake = FakeRedis()
    service = Level2WebSocketService(max_symbols=30, mock_mode=True)
    service._redis_store = FakeRedisStore(fake)
    service._running = True

    await service.subscribe("user1", "AAPL")
    await service.subscribe("user2", "AAPL")

    payload = {"T": "o", "S": "AAPL", "b": [], "a": []}
    await service.publish_update("AAPL", payload)

    channels = {entry[0] for entry in fake.published}
    assert l2_channel("user1", "AAPL") in channels
    assert l2_channel("user2", "AAPL") in channels
