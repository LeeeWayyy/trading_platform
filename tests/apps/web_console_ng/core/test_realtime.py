"""Tests for RealtimeUpdater and channel helpers."""

from __future__ import annotations

import asyncio
import contextlib
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError

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


# --- New tests to improve coverage ---


@pytest.mark.asyncio()
async def test_subscribe_already_subscribed_returns_early() -> None:
    """Test that subscribing to the same channel twice returns early (lines 55-56)."""
    client = DummyClient()
    updater = RealtimeUpdater("client-1", client)

    channel = "positions:user-1"
    # Pre-populate subscriptions to simulate already subscribed
    existing_task = asyncio.create_task(asyncio.sleep(3600))
    updater.subscriptions[channel] = existing_task

    # Mock the lifecycle manager
    mock_lifecycle = AsyncMock()
    with patch(
        "apps.web_console_ng.core.realtime.ClientLifecycleManager.get",
        return_value=mock_lifecycle,
    ):
        await updater.subscribe(channel, lambda x: x)

    # Should not have created new subscription
    assert updater.subscriptions[channel] is existing_task
    # Cleanup
    existing_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await existing_task


@pytest.mark.asyncio()
async def test_subscribe_creates_tasks_and_registers() -> None:
    """Test subscribe creates listener and worker tasks (lines 55-69)."""
    client = DummyClient()
    updater = RealtimeUpdater("client-1", client)

    # Mock dependencies
    mock_lifecycle = AsyncMock()
    mock_lifecycle.register_task = AsyncMock()

    fake_pubsub = FakePubSub([])
    fake_redis = Mock()
    fake_redis.pubsub.return_value = fake_pubsub

    fake_store = AsyncMock()
    fake_store.get_master = AsyncMock(return_value=fake_redis)
    updater._redis_store = fake_store

    channel = "positions:user-1"
    callback = Mock()

    with patch(
        "apps.web_console_ng.core.realtime.ClientLifecycleManager.get",
        return_value=mock_lifecycle,
    ):
        await updater.subscribe(channel, callback)

    # Verify tasks were created
    assert channel in updater.subscriptions
    assert channel in updater.workers
    assert channel in updater.queues
    assert channel in updater.last_update_times

    # Verify lifecycle registered both tasks
    assert mock_lifecycle.register_task.call_count == 2

    # Cleanup
    await updater.cleanup()


@pytest.mark.asyncio()
async def test_listener_handles_unicode_decode_error() -> None:
    """Test listener handles UnicodeDecodeError gracefully (lines 88-99)."""
    client = DummyClient()
    updater = RealtimeUpdater("client-1", client)
    updater.RECONNECT_DELAY = 0.0

    channel = "positions:user-1"
    updater.queues[channel] = asyncio.Queue(maxsize=10)

    # Create a mock bytes object that raises UnicodeDecodeError
    bad_bytes = Mock(spec=bytes)
    bad_bytes.decode.side_effect = UnicodeDecodeError("utf-8", b"", 0, 1, "mock error")

    messages = [
        {"type": "message", "data": bad_bytes},  # Will cause UnicodeDecodeError
        {"type": "message", "data": json.dumps({"value": "good"})},  # Valid message
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

    # Only the valid message should be in the queue
    assert updater.queues[channel].qsize() == 1
    msg = updater.queues[channel].get_nowait()
    assert msg["value"] == "good"


@pytest.mark.asyncio()
async def test_listener_handles_json_decode_error() -> None:
    """Test listener handles JSON decode errors gracefully (lines 103-108)."""
    client = DummyClient()
    updater = RealtimeUpdater("client-1", client)
    updater.RECONNECT_DELAY = 0.0

    channel = "positions:user-1"
    updater.queues[channel] = asyncio.Queue(maxsize=10)

    messages = [
        {"type": "message", "data": "not valid json {{{"},  # Invalid JSON
        {"type": "message", "data": json.dumps({"value": "good"})},  # Valid message
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

    # Only the valid message should be in the queue
    assert updater.queues[channel].qsize() == 1
    msg = updater.queues[channel].get_nowait()
    assert msg["value"] == "good"


@pytest.mark.asyncio()
async def test_listener_handles_redis_connection_error() -> None:
    """Test listener handles RedisConnectionError and retries (lines 125-129)."""
    client = DummyClient()
    updater = RealtimeUpdater("client-1", client)
    updater.RECONNECT_DELAY = 0.01

    channel = "positions:user-1"
    updater.queues[channel] = asyncio.Queue(maxsize=10)

    call_count = 0

    async def mock_get_master():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RedisConnectionError("Connection refused")
        # Second call: return a working redis
        fake_pubsub = FakePubSub([])
        fake_redis = Mock()
        fake_redis.pubsub.return_value = fake_pubsub
        return fake_redis

    fake_store = AsyncMock()
    fake_store.get_master = mock_get_master
    updater._redis_store = fake_store

    task = asyncio.create_task(updater._listener(channel))
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    # Should have retried after connection error
    assert call_count >= 2


@pytest.mark.asyncio()
async def test_listener_handles_redis_error() -> None:
    """Test listener handles general RedisError and retries (lines 130-134)."""
    client = DummyClient()
    updater = RealtimeUpdater("client-1", client)
    updater.RECONNECT_DELAY = 0.01

    channel = "positions:user-1"
    updater.queues[channel] = asyncio.Queue(maxsize=10)

    call_count = 0

    async def mock_get_master():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RedisError("Redis error")
        # Second call: return a working redis
        fake_pubsub = FakePubSub([])
        fake_redis = Mock()
        fake_redis.pubsub.return_value = fake_pubsub
        return fake_redis

    fake_store = AsyncMock()
    fake_store.get_master = mock_get_master
    updater._redis_store = fake_store

    task = asyncio.create_task(updater._listener(channel))
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert call_count >= 2


@pytest.mark.asyncio()
async def test_listener_handles_oserror() -> None:
    """Test listener handles OSError and retries (lines 135-139)."""
    client = DummyClient()
    updater = RealtimeUpdater("client-1", client)
    updater.RECONNECT_DELAY = 0.01

    channel = "positions:user-1"
    updater.queues[channel] = asyncio.Queue(maxsize=10)

    call_count = 0

    async def mock_get_master():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OSError("Network error")
        # Second call: return a working redis
        fake_pubsub = FakePubSub([])
        fake_redis = Mock()
        fake_redis.pubsub.return_value = fake_pubsub
        return fake_redis

    fake_store = AsyncMock()
    fake_store.get_master = mock_get_master
    updater._redis_store = fake_store

    task = asyncio.create_task(updater._listener(channel))
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert call_count >= 2


@pytest.mark.asyncio()
async def test_listener_pubsub_close_error() -> None:
    """Test listener handles errors during pubsub cleanup (lines 146-154)."""
    client = DummyClient()
    updater = RealtimeUpdater("client-1", client)
    updater.RECONNECT_DELAY = 0.01

    channel = "positions:user-1"
    updater.queues[channel] = asyncio.Queue(maxsize=10)

    call_count = 0

    class ErrorClosePubSub(FakePubSub):
        async def unsubscribe(self, channel: str) -> None:
            raise RedisError("Unsubscribe failed")

        async def close(self) -> None:
            raise OSError("Close failed")

    async def mock_get_master():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            fake_pubsub = ErrorClosePubSub([])
            fake_redis = Mock()
            fake_redis.pubsub.return_value = fake_pubsub
            return fake_redis
        # Subsequent calls raise error to trigger cleanup
        raise RedisConnectionError("Retry error")

    fake_store = AsyncMock()
    fake_store.get_master = mock_get_master
    updater._redis_store = fake_store

    task = asyncio.create_task(updater._listener(channel))
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio()
async def test_worker_throttling() -> None:
    """Test worker respects throttling interval (line 180)."""
    client = DummyClient()
    updater = RealtimeUpdater("client-1", client)
    updater.MAX_UPDATES_PER_SECOND = 10  # 100ms interval

    channel = "orders:user-1"
    updater.queues[channel] = asyncio.Queue()
    # Set last update to now, so next update must wait
    import time

    updater.last_update_times[channel] = time.time()

    received: list[dict] = []
    delivered = asyncio.Event()

    async def callback(data):
        received.append(data)
        delivered.set()

    task = asyncio.create_task(updater._worker(channel, callback))

    # Put a message
    await updater.queues[channel].put({"value": 1})

    # Wait for delivery (should take ~100ms due to throttling)
    await asyncio.wait_for(delivered.wait(), timeout=1)

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert received == [{"value": 1}]


@pytest.mark.asyncio()
async def test_worker_handles_error() -> None:
    """Test worker handles errors during processing (lines 186-190)."""
    client = DummyClient()
    updater = RealtimeUpdater("client-1", client)
    updater.MAX_UPDATES_PER_SECOND = 1000

    channel = "orders:user-1"
    updater.queues[channel] = asyncio.Queue()
    updater.last_update_times[channel] = 0.0

    call_count = 0
    delivered = asyncio.Event()

    async def callback(data):
        nonlocal call_count
        call_count += 1
        delivered.set()

    # Mock _deliver_update to raise an error on first call, succeed on second
    original_deliver = updater._deliver_update
    deliver_count = 0

    async def mock_deliver(ch, data, cb):
        nonlocal deliver_count
        deliver_count += 1
        if deliver_count == 1:
            raise OSError("Delivery error")
        await original_deliver(ch, data, cb)

    updater._deliver_update = mock_deliver

    task = asyncio.create_task(updater._worker(channel, callback))

    # First message will cause error during delivery
    await updater.queues[channel].put({"error": True})
    await asyncio.sleep(0.01)  # Give time for error to be handled
    # Second message should succeed
    await updater.queues[channel].put({"value": "good"})

    await asyncio.wait_for(delivered.wait(), timeout=1)

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    # The worker should have recovered from the error
    assert call_count >= 1


@pytest.mark.asyncio()
async def test_deliver_update_handles_callback_error() -> None:
    """Test _deliver_update handles callback errors (lines 206-210)."""
    client = DummyClient()
    updater = RealtimeUpdater("client-1", client)

    def error_callback(data):
        raise ValueError("Callback failed")

    # Should not raise, should log error
    await updater._deliver_update("positions", {"value": 1}, error_callback)

    # Client context was still entered/exited
    assert client.entered == 1
    assert client.exited == 1


@pytest.mark.asyncio()
async def test_deliver_update_handles_async_callback() -> None:
    """Test _deliver_update handles async callbacks correctly."""
    client = DummyClient()
    updater = RealtimeUpdater("client-1", client)

    received = []

    async def async_callback(data):
        received.append(data)

    await updater._deliver_update("positions", {"value": 1}, async_callback)

    assert received == [{"value": 1}]
    assert client.entered == 1
    assert client.exited == 1


@pytest.mark.asyncio()
async def test_unsubscribe_handles_already_done_tasks() -> None:
    """Test unsubscribe handles already done tasks gracefully."""
    client = DummyClient()
    updater = RealtimeUpdater("client-1", client)

    channel = "fills:user-1"

    # Create tasks that are already done
    async def quick_task():
        pass

    listener_task = asyncio.create_task(quick_task())
    worker_task = asyncio.create_task(quick_task())
    await listener_task
    await worker_task

    updater.subscriptions[channel] = listener_task
    updater.workers[channel] = worker_task
    updater.queues[channel] = asyncio.Queue()

    # Should not raise
    await updater.unsubscribe(channel)

    assert channel not in updater.subscriptions
    assert channel not in updater.workers
    assert channel not in updater.queues


@pytest.mark.asyncio()
async def test_unsubscribe_handles_pubsub_error() -> None:
    """Test unsubscribe handles pubsub close errors (lines 235-239)."""
    client = DummyClient()
    updater = RealtimeUpdater("client-1", client)

    channel = "fills:user-1"

    class ErrorPubSub:
        async def unsubscribe(self, channel: str) -> None:
            raise RedisError("Unsubscribe failed")

        async def close(self) -> None:
            raise OSError("Close failed")

    updater.pubsubs[channel] = ErrorPubSub()
    updater.queues[channel] = asyncio.Queue()

    # Should not raise, should log warning
    await updater.unsubscribe(channel)

    assert channel not in updater.pubsubs
    assert channel not in updater.queues


@pytest.mark.asyncio()
async def test_unsubscribe_nonexistent_channel() -> None:
    """Test unsubscribe handles non-existent channel gracefully."""
    client = DummyClient()
    updater = RealtimeUpdater("client-1", client)

    # Should not raise
    await updater.unsubscribe("nonexistent:channel")


@pytest.mark.asyncio()
async def test_listener_skips_non_message_types() -> None:
    """Test listener skips non-message type messages (line 84)."""
    client = DummyClient()
    updater = RealtimeUpdater("client-1", client)
    updater.RECONNECT_DELAY = 0.0

    channel = "positions:user-1"
    updater.queues[channel] = asyncio.Queue(maxsize=10)

    messages = [
        {"type": "subscribe", "data": "subscribed"},  # Non-message type
        {"type": "psubscribe", "data": "pattern"},  # Non-message type
        {"type": "message", "data": json.dumps({"value": "real"})},  # Real message
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

    # Only the real message should be in the queue
    assert updater.queues[channel].qsize() == 1
    msg = updater.queues[channel].get_nowait()
    assert msg["value"] == "real"


@pytest.mark.asyncio()
async def test_worker_drains_queue_before_delivery() -> None:
    """Test worker conflates multiple messages, delivering only the latest (lines 169-174)."""
    client = DummyClient()
    updater = RealtimeUpdater("client-1", client)
    updater.MAX_UPDATES_PER_SECOND = 1000

    channel = "orders:user-1"
    queue: asyncio.Queue = asyncio.Queue()
    updater.queues[channel] = queue
    updater.last_update_times[channel] = 0.0

    # Pre-fill queue with multiple messages
    for i in range(5):
        await queue.put({"value": i})

    received: list[dict] = []
    delivered = asyncio.Event()

    async def callback(data):
        received.append(data)
        delivered.set()

    task = asyncio.create_task(updater._worker(channel, callback))

    await asyncio.wait_for(delivered.wait(), timeout=1)

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    # Should have conflated to only the latest message
    assert received == [{"value": 4}]


@pytest.mark.asyncio()
async def test_cleanup_with_multiple_channels() -> None:
    """Test cleanup handles multiple channels."""
    client = DummyClient()
    updater = RealtimeUpdater("client-1", client)

    channels = ["positions:user-1", "orders:user-1", "fills:user-1"]

    for ch in channels:
        listener_task = asyncio.create_task(asyncio.sleep(3600))
        worker_task = asyncio.create_task(asyncio.sleep(3600))
        updater.subscriptions[ch] = listener_task
        updater.workers[ch] = worker_task
        updater.pubsubs[ch] = FakePubSub([])
        updater.queues[ch] = asyncio.Queue()

    await updater.cleanup()

    assert len(updater.subscriptions) == 0
    assert len(updater.workers) == 0
    assert len(updater.pubsubs) == 0
    assert len(updater.queues) == 0


@pytest.mark.asyncio()
async def test_listener_queue_full_race_condition() -> None:
    """Test listener handles race condition when queue becomes non-full during drop (lines 115-116)."""
    client = DummyClient()
    updater = RealtimeUpdater("client-1", client)
    updater.RECONNECT_DELAY = 0.0

    channel = "positions:user-1"
    # Create a queue that will behave specially
    queue = asyncio.Queue(maxsize=1)
    updater.queues[channel] = queue

    # Pre-fill with one message
    await queue.put({"value": "old"})

    # Create a pubsub that yields messages
    messages = [
        {"type": "message", "data": json.dumps({"value": "new"})},
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

    # Queue should have either the old or new value (oldest dropped)
    assert queue.qsize() == 1


@pytest.mark.asyncio()
async def test_listener_queue_put_race_condition() -> None:
    """Test listener handles race condition when put fails due to race (lines 120-121)."""
    client = DummyClient()
    updater = RealtimeUpdater("client-1", client)
    updater.RECONNECT_DELAY = 0.0

    channel = "positions:user-1"

    # Create a mock queue that's full on first put, but succeeds on task_done
    class RacyQueue:
        def __init__(self):
            self.items = []
            self.put_count = 0

        def full(self):
            return len(self.items) >= 1

        def get_nowait(self):
            if self.items:
                return self.items.pop(0)
            raise asyncio.QueueEmpty()

        def put_nowait(self, item):
            self.put_count += 1
            if self.put_count == 1:
                raise asyncio.QueueFull()  # Simulate race condition
            self.items.append(item)

        def task_done(self):
            pass

        def qsize(self):
            return len(self.items)

    racy_queue = RacyQueue()
    # Pre-fill with one item
    racy_queue.items = [{"value": "existing"}]
    updater.queues[channel] = racy_queue

    messages = [
        {"type": "message", "data": json.dumps({"value": "new1"})},
        {"type": "message", "data": json.dumps({"value": "new2"})},
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

    # Despite the race condition, the listener should have continued processing
    assert racy_queue.put_count >= 1
