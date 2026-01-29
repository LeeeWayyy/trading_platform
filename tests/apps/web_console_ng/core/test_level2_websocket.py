from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

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


class FailingRedis:
    async def publish(self, channel: str, message: str) -> None:
        raise RuntimeError("boom")


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


@pytest.mark.asyncio()
async def test_duplicate_subscription_does_not_increment_cap() -> None:
    service = Level2WebSocketService(max_symbols=1, mock_mode=True)
    service._running = True

    assert await service.subscribe("user1", "AAPL") is True
    assert await service.subscribe("user1", "AAPL") is True
    assert await service.subscribe("user2", "MSFT") is False

    assert service._symbol_refcounts == {"AAPL": 1}
    assert service._user_symbol_counts[("user1", "AAPL")] == 2

    await service.unsubscribe("user1", "AAPL")
    assert service._symbol_refcounts == {"AAPL": 1}

    await service.unsubscribe("user1", "AAPL")
    assert service._symbol_refcounts == {}


@pytest.mark.asyncio()
async def test_publish_update_handles_errors() -> None:
    service = Level2WebSocketService(max_symbols=1, mock_mode=True)
    service._redis_store = FakeRedisStore(FailingRedis())  # type: ignore[arg-type]
    service._running = True
    await service.subscribe("user1", "AAPL")

    await service.publish_update("AAPL", {"T": "o", "S": "AAPL"})


def test_entitlement_status_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPACA_L2_USE_MOCK", "true")
    entitled, reason = Level2WebSocketService.entitlement_status()
    assert entitled is True
    assert "Mock mode" in reason

    monkeypatch.delenv("ALPACA_L2_USE_MOCK", raising=False)
    monkeypatch.setenv("ALPACA_L2_ENABLED", "false")
    entitled, reason = Level2WebSocketService.entitlement_status()
    assert entitled is False
    assert "not enabled" in reason

    monkeypatch.setenv("ALPACA_L2_ENABLED", "true")
    monkeypatch.setenv("ALPACA_PRO_API_KEY", "")
    monkeypatch.setenv("ALPACA_PRO_API_SECRET", "")
    entitled, reason = Level2WebSocketService.entitlement_status()
    assert entitled is False
    assert "credentials missing" in reason


@pytest.mark.asyncio()
async def test_stop_if_idle_cancels_task() -> None:
    service = Level2WebSocketService(max_symbols=1, mock_mode=True)
    service._symbol_refcounts = {}
    service._running = True
    service._task = asyncio.create_task(asyncio.sleep(10))

    await service._stop_if_idle()
    assert service._running is False
    assert service._task is None


@pytest.mark.asyncio()
async def test_connection_loop_falls_back_to_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    service = Level2WebSocketService(max_symbols=1, mock_mode=False)
    mock_loop = AsyncMock()
    monkeypatch.setattr(service, "_mock_loop", mock_loop)
    await service._connection_loop()
    assert service._mock_mode is True
    mock_loop.assert_awaited_once()


def test_generate_mock_snapshot_shape() -> None:
    service = Level2WebSocketService(max_symbols=1, mock_mode=True)
    snapshot = service._generate_mock_snapshot("AAPL", 0.0)
    assert snapshot["T"] == "o"
    assert snapshot["S"] == "AAPL"
    assert len(snapshot["b"]) == 10
    assert len(snapshot["a"]) == 10
