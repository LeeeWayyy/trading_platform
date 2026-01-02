"""Client lifecycle management for NiceGUI WebSocket connections."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class ClientLifecycleManager:
    """Track per-client tasks and cleanup on disconnect.

    NOTE: In-memory tracking assumes single-process deployment (workers=1).
    """

    _instance: ClientLifecycleManager | None = None

    def __init__(self) -> None:
        self.client_tasks: dict[str, list[asyncio.Task[Any]]] = {}
        self.client_callbacks: dict[str, list[Callable[[], Any]]] = {}
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

    async def register_cleanup_callback(self, client_id: str, callback: Callable[[], Any]) -> None:
        """Register a cleanup callback for the client."""
        async with self._lock:
            self.client_callbacks.setdefault(client_id, []).append(callback)

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

        for callback in callbacks:
            try:
                result = callback()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.warning(
                    "cleanup_callback_error",
                    extra={"client_id": client_id, "error": str(exc)},
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
