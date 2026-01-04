"""Real-time update infrastructure for NiceGUI via Redis Pub/Sub."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from nicegui import Client
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError

from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from apps.web_console_ng.core.redis_ha import get_redis_store

logger = logging.getLogger(__name__)


class RealtimeUpdater:
    """
    Push real-time updates to connected clients via Redis Pub/Sub.

    Architecture:
    - Listener task: receives messages from Redis, puts in bounded queue (non-blocking)
    - Worker task: processes queue, throttles, delivers to UI callback
    - Decoupled: slow callbacks don't block message reception

    Features:
    - Throttle: max 10 updates/second per channel with trailing-edge flush
    - Backpressure: bounded queue, drop oldest when full
    - Automatic cleanup on disconnect
    - NiceGUI client context enforcement
    - Redis connection retry on failure
    """

    MAX_UPDATES_PER_SECOND = 10
    MAX_QUEUE_SIZE = 100
    RECONNECT_DELAY = 1.0

    def __init__(self, client_id: str, nicegui_client: Client) -> None:
        self.client_id = client_id
        self.nicegui_client = nicegui_client
        self._redis_store = get_redis_store()
        self.subscriptions: dict[str, asyncio.Task[None]] = {}
        self.workers: dict[str, asyncio.Task[None]] = {}
        self.pubsubs: dict[str, Any] = {}
        self.queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self.last_update_times: dict[str, float] = {}

    async def subscribe(self, channel: str, callback: Callable[[dict[str, Any]], Any]) -> None:
        """Subscribe to a Redis Pub/Sub channel."""
        if channel in self.subscriptions:
            return

        self.last_update_times[channel] = 0.0
        self.queues[channel] = asyncio.Queue(maxsize=self.MAX_QUEUE_SIZE)

        listener_task = asyncio.create_task(self._listener(channel))
        self.subscriptions[channel] = listener_task

        worker_task = asyncio.create_task(self._worker(channel, callback))
        self.workers[channel] = worker_task

        lifecycle = ClientLifecycleManager.get()
        await lifecycle.register_task(self.client_id, listener_task)
        await lifecycle.register_task(self.client_id, worker_task)

    async def _listener(self, channel: str) -> None:
        """Listener task: receives messages from Redis Pub/Sub."""
        pubsub: Any | None = None

        while True:
            try:
                redis_client = await self._redis_store.get_master()
                pubsub = redis_client.pubsub()
                await pubsub.subscribe(channel)
                self.pubsubs[channel] = pubsub

                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue

                    raw = message.get("data")
                    if isinstance(raw, bytes):
                        try:
                            raw = raw.decode("utf-8")
                        except Exception as exc:
                            logger.warning(
                                "realtime_pubsub_decode_error",
                                extra={"channel": channel, "error": str(exc)},
                            )
                            continue

                    try:
                        data = json.loads(raw)
                    except (json.JSONDecodeError, TypeError) as exc:
                        logger.warning(
                            "realtime_pubsub_json_error",
                            extra={"channel": channel, "error": str(exc)},
                        )
                        continue

                    queue = self.queues[channel]
                    if queue.full():
                        try:
                            queue.get_nowait()
                            queue.task_done()
                        except asyncio.QueueEmpty:
                            pass

                    try:
                        queue.put_nowait(data)
                    except asyncio.QueueFull:
                        pass

            except asyncio.CancelledError:
                break
            except RedisConnectionError as exc:
                logger.warning(
                    "realtime_pubsub_connection_lost",
                    extra={"channel": channel, "error": str(exc)},
                )
            except RedisError as exc:
                logger.warning(
                    "realtime_pubsub_error",
                    extra={"channel": channel, "error": str(exc)},
                )
            except Exception as exc:
                logger.exception(
                    "realtime_listener_error",
                    extra={"channel": channel, "error": str(exc)},
                )
            finally:
                if pubsub is not None:
                    self.pubsubs.pop(channel, None)
                    try:
                        await pubsub.unsubscribe(channel)
                        await pubsub.close()
                    except Exception as exc:
                        logger.warning(
                            "realtime_pubsub_close_error",
                            extra={"channel": channel, "error": str(exc)},
                        )
                    pubsub = None

            await asyncio.sleep(self.RECONNECT_DELAY)

    async def _worker(self, channel: str, callback: Callable[[dict[str, Any]], Any]) -> None:
        """Worker task: processes queued messages with throttling."""
        min_interval = 1.0 / self.MAX_UPDATES_PER_SECOND
        queue = self.queues[channel]

        while True:
            try:
                latest_data = await queue.get()
                queue.task_done()

                while not queue.empty():
                    try:
                        latest_data = queue.get_nowait()
                        queue.task_done()
                    except asyncio.QueueEmpty:
                        break

                now = time.time()
                last_update = self.last_update_times.get(channel, 0.0)
                wait_time = min_interval - (now - last_update)
                if wait_time > 0:
                    await asyncio.sleep(wait_time)

                await self._deliver_update(channel, latest_data, callback)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(
                    "realtime_worker_error",
                    extra={"channel": channel, "error": str(exc)},
                )

    async def _deliver_update(
        self,
        channel: str,
        data: dict[str, Any],
        callback: Callable[[dict[str, Any]], Any],
    ) -> None:
        """Deliver update to callback with NiceGUI client context."""
        self.last_update_times[channel] = time.time()

        try:
            async with self.nicegui_client:  # type: ignore[attr-defined]
                result = callback(data)
                if asyncio.iscoroutine(result):
                    await result
        except Exception as exc:
            logger.error(
                "realtime_callback_error",
                extra={"channel": channel, "error": str(exc)},
            )

    async def unsubscribe(self, channel: str) -> None:
        """Unsubscribe from a channel and cleanup resources."""
        listener_task = self.subscriptions.pop(channel, None)
        if listener_task and not listener_task.done():
            listener_task.cancel()
            try:
                await listener_task
            except asyncio.CancelledError:
                pass

        worker_task = self.workers.pop(channel, None)
        if worker_task and not worker_task.done():
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

        pubsub = self.pubsubs.pop(channel, None)
        if pubsub is not None:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.close()
            except Exception as exc:
                logger.warning(
                    "realtime_pubsub_close_error",
                    extra={"channel": channel, "error": str(exc)},
                )

        self.queues.pop(channel, None)

    async def cleanup(self) -> None:
        """Cleanup all subscriptions and connections (called on disconnect)."""
        for channel in list(self.subscriptions.keys()):
            await self.unsubscribe(channel)


def position_channel(user_id: str) -> str:
    return f"positions:{user_id}"


def orders_channel(user_id: str) -> str:
    """Channel for order status updates."""
    return f"orders:{user_id}"


def fills_channel(user_id: str) -> str:
    """Channel for fill/execution events."""
    return f"fills:{user_id}"


def kill_switch_channel() -> str:
    """Global kill switch state channel."""
    return "kill_switch:state"


def circuit_breaker_channel() -> str:
    """Global circuit breaker state channel."""
    return "circuit_breaker:state"


__all__ = [
    "RealtimeUpdater",
    "position_channel",
    "orders_channel",
    "fills_channel",
    "kill_switch_channel",
    "circuit_breaker_channel",
]
