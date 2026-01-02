"""Tests for ClientLifecycleManager."""

from __future__ import annotations

import asyncio

import pytest

from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    ClientLifecycleManager._instance = None


@pytest.mark.asyncio()
async def test_register_client_tracks_active() -> None:
    manager = ClientLifecycleManager.get()
    await manager.register_client("client-1")

    assert await manager.get_active_client_count() == 1
    assert await manager.is_client_active("client-1") is True


def test_generate_client_id_uniqueness() -> None:
    manager = ClientLifecycleManager.get()
    ids = {manager.generate_client_id() for _ in range(50)}
    assert len(ids) == 50


@pytest.mark.asyncio()
async def test_cleanup_uses_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = ClientLifecycleManager.get()
    await manager.register_client("client-2")

    async def _never_finish() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return

    task = asyncio.create_task(_never_finish())
    await manager.register_task("client-2", task)

    calls: list[float] = []

    async def _fake_wait_for(awaitable, timeout: float):
        calls.append(timeout)
        raise TimeoutError

    monkeypatch.setattr(
        "apps.web_console_ng.core.client_lifecycle.asyncio.wait_for",
        _fake_wait_for,
    )

    await manager.cleanup_client("client-2")

    await asyncio.sleep(0)
    await task

    assert calls == [5.0]
