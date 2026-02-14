"""Client lifecycle management for NiceGUI WebSocket connections."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Callback entry: (callable, owner_key). owner_key=None for unkeyed (legacy) callbacks.
_CallbackEntry = tuple[Callable[[], Any], str | None]


class ClientLifecycleManager:
    """Track per-client tasks and cleanup on disconnect.

    NOTE: In-memory tracking assumes single-process deployment (workers=1).

    Callbacks support an optional ``owner_key`` for per-module deduplication.
    When a callback is registered with an ``owner_key``, any existing callback
    with the same key is atomically replaced (single-assignment). Callbacks
    without an ``owner_key`` are appended without dedup (legacy behaviour).
    """

    _instance: ClientLifecycleManager | None = None

    def __init__(self) -> None:
        self.client_tasks: dict[str, list[asyncio.Task[Any]]] = {}
        self.client_callbacks: dict[str, list[_CallbackEntry]] = {}
        self.active_clients: set[str] = set()
        self._lock = asyncio.Lock()

    @classmethod
    def get(cls) -> ClientLifecycleManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def generate_client_id(self) -> str:
        """Generate a unique client ID for a connection."""
        return str(uuid.uuid4())

    async def register_client(self, client_id: str) -> None:
        """Register a new client connection."""
        async with self._lock:
            self.active_clients.add(client_id)
            self.client_tasks.setdefault(client_id, [])
            self.client_callbacks.setdefault(client_id, [])
        logger.info("client_registered", extra={"client_id": client_id})

    async def register_task(self, client_id: str, task: asyncio.Task[Any]) -> None:
        """Register a background task for the client."""
        async with self._lock:
            self.client_tasks.setdefault(client_id, []).append(task)

    async def register_cleanup_callback(
        self,
        client_id: str,
        callback: Callable[[], Any],
        *,
        owner_key: str | None = None,
    ) -> None:
        """Register a cleanup callback for the client.

        Args:
            client_id: The client connection ID.
            callback: Callable invoked on disconnect.
            owner_key: Optional key for per-module deduplication. When provided,
                any existing callback with the same key is atomically replaced.
                When ``None``, the callback is appended without dedup.
        """
        async with self._lock:
            callbacks = self.client_callbacks.setdefault(client_id, [])
            if owner_key is not None:
                # Atomic single-assignment: filter + append in one expression
                filtered = [
                    item
                    for item in callbacks
                    if not isinstance(item, tuple) or item[1] != owner_key
                ]
                self.client_callbacks[client_id] = [*filtered, (callback, owner_key)]
            else:
                callbacks.append((callback, None))

    async def cleanup_client(self, client_id: str) -> None:
        """Cancel tasks and run cleanup callbacks for a client."""
        task_cancel_timeout = 5.0

        async with self._lock:
            self.active_clients.discard(client_id)
            tasks = self.client_tasks.pop(client_id, [])
            callbacks = self.client_callbacks.pop(client_id, [])

        async def _cancel_task(task: asyncio.Task[Any]) -> None:
            if task.done():
                return
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=task_cancel_timeout)
            except TimeoutError:
                logger.warning("task_cancel_timeout", extra={"client_id": client_id})
            except asyncio.CancelledError:
                return

        await asyncio.gather(*[_cancel_task(task) for task in tasks], return_exceptions=True)

        for item in callbacks:
            # Migration-tolerant: handle both tuple entries and legacy bare
            # callables. TODO: Remove bare-callable path once all callers use
            # the (callback, owner_key) tuple format.
            cb: Callable[[], Any] = item[0] if isinstance(item, tuple) else item
            try:
                result = cb()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception(
                    "cleanup_callback_error",
                    extra={"client_id": client_id},
                )

        logger.info(
            "client_cleaned",
            extra={
                "client_id": client_id,
                "tasks": len(tasks),
                "callbacks": len(callbacks),
            },
        )

    async def get_active_client_count(self) -> int:
        async with self._lock:
            return len(self.active_clients)

    async def is_client_active(self, client_id: str) -> bool:
        async with self._lock:
            return client_id in self.active_clients


__all__ = ["ClientLifecycleManager"]
