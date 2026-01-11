"""Tests for RealtimeUpdater and channel helpers."""

from __future__ import annotations

import asyncio
import contextlib
import json
from unittest.mock import AsyncMock, Mock

import pytest

from apps.web_console_ng.core.realtime import (
    RealtimeUpdater,
    circuit_breaker_channel,
    fills_channel,
    kill_switch_channel,
    orders_channel,
    position_channel,
)


class DummyClient:
    """Minimal NiceGUI client context stub.

    NiceGUI Client uses sync context manager (with ... :), not async.
    """

    def __init__(self) -> None:
        self.entered = 0
        self.exited = 0

    def __enter__(self) -> DummyClient:
        self.entered += 1
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.exited += 1
        return None


class FakePubSub:
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self._messages = messages
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def unsubscribe(self, channel: str) -> None:
        self.unsubscribed.append(channel)

    async def close(self) -> None:
        self.closed = True

    async def listen(self):
        for message in self._messages:
            yield message
        while True:
            await asyncio.sleep(3600)


@pytest.mark.asyncio()
async def test_listener_drops_oldest_when_queue_full() -> None:
    client = DummyClient()
    updater = RealtimeUpdater("client-1", client)
    updater.MAX_QUEUE_SIZE = 2
    updater.RECONNECT_DELAY = 0.0

    channel = "positions:user-1"
    updater.queues[channel] = asyncio.Queue(maxsize=2)

    messages = [
        {"type": "message", "data": json.dumps({"value": 1})},
        {"type": "message", "data": json.dumps({"value": 2})},
        {"type": "message", "data": json.dumps({"value": 3})},
    ]
    fake_pubsub = FakePubSub(messages)
    fake_redis = Mock()
    fake_redis.pubsub.return_value = fake_pubsub

    fake_store = AsyncMock()
    fake_store.get_master = AsyncMock(return_value=fake_redis)
    updater._redis_store = fake_store

    task = asyncio.create_task(updater._listener(channel))
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert updater.queues[channel].qsize() == 2
    first = updater.queues[channel].get_nowait()
    second = updater.queues[channel].get_nowait()
    assert first["value"] == 2
    assert second["value"] == 3


@pytest.mark.asyncio()
async def test_worker_conflates_and_delivers_latest() -> None:
    client = DummyClient()
    updater = RealtimeUpdater("client-1", client)
    updater.MAX_UPDATES_PER_SECOND = 1000

    channel = "orders:user-1"
    updater.queues[channel] = asyncio.Queue()
    updater.last_update_times[channel] = 0.0

    received: list[dict[str, int]] = []
    delivered = asyncio.Event()

    async def callback(data):
        received.append(data)
        delivered.set()

    task = asyncio.create_task(updater._worker(channel, callback))

    await updater.queues[channel].put({"value": 1})
    await updater.queues[channel].put({"value": 2})
    await updater.queues[channel].put({"value": 3})

    await asyncio.wait_for(delivered.wait(), timeout=1)

    task.cancel()
    await task

    assert received == [{"value": 3}]


@pytest.mark.asyncio()
async def test_deliver_update_uses_client_context() -> None:
    client = DummyClient()
    updater = RealtimeUpdater("client-1", client)

    called: list[dict[str, int]] = []

    def callback(data):
        called.append(data)

    await updater._deliver_update("positions", {"value": 1}, callback)

    assert client.entered == 1
    assert client.exited == 1
    assert called == [{"value": 1}]


@pytest.mark.asyncio()
async def test_cleanup_cancels_tasks_and_closes_pubsub() -> None:
    client = DummyClient()
    updater = RealtimeUpdater("client-1", client)

    channel = "fills:user-1"
    listener_task = asyncio.create_task(asyncio.sleep(3600))
    worker_task = asyncio.create_task(asyncio.sleep(3600))
    updater.subscriptions[channel] = listener_task
    updater.workers[channel] = worker_task

    pubsub = FakePubSub([])
    updater.pubsubs[channel] = pubsub
    updater.queues[channel] = asyncio.Queue()

    await updater.cleanup()

    assert listener_task.cancelled()
    assert worker_task.cancelled()
    assert pubsub.closed is True
    assert pubsub.unsubscribed == [channel]


def test_channel_helpers() -> None:
    assert position_channel("user-1") == "positions:user-1"
    assert orders_channel("user-1") == "orders:user-1"
    assert fills_channel("user-1") == "fills:user-1"
    assert kill_switch_channel() == "kill_switch:state"
    assert circuit_breaker_channel() == "circuit_breaker:state"
