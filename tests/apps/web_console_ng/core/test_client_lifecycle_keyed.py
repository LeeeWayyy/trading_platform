"""Tests for ClientLifecycleManager keyed callback API (P6T13 prerequisite)."""

from __future__ import annotations

import pytest

from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    """Reset singleton between tests."""
    ClientLifecycleManager._instance = None


@pytest.fixture()
def manager() -> ClientLifecycleManager:
    return ClientLifecycleManager.get()


# ============================================================================
# Keyed Callback Registration
# ============================================================================


@pytest.mark.asyncio()
async def test_keyed_callback_replaces_same_key(manager: ClientLifecycleManager) -> None:
    """Registering a callback with the same owner_key replaces the previous one."""
    await manager.register_client("c1")
    call_log: list[str] = []

    await manager.register_cleanup_callback(
        "c1", lambda: call_log.append("first"), owner_key="timers"
    )
    await manager.register_cleanup_callback(
        "c1", lambda: call_log.append("second"), owner_key="timers"
    )

    await manager.cleanup_client("c1")
    assert call_log == ["second"], "Only the latest keyed callback should run"


@pytest.mark.asyncio()
async def test_different_keys_coexist(manager: ClientLifecycleManager) -> None:
    """Callbacks with different owner_keys coexist independently."""
    await manager.register_client("c1")
    call_log: list[str] = []

    await manager.register_cleanup_callback(
        "c1", lambda: call_log.append("timers"), owner_key="timers"
    )
    await manager.register_cleanup_callback(
        "c1", lambda: call_log.append("sockets"), owner_key="sockets"
    )

    await manager.cleanup_client("c1")
    assert sorted(call_log) == ["sockets", "timers"]


@pytest.mark.asyncio()
async def test_unkeyed_callbacks_append(manager: ClientLifecycleManager) -> None:
    """Callbacks without owner_key append (legacy behaviour)."""
    await manager.register_client("c1")
    call_log: list[str] = []

    await manager.register_cleanup_callback("c1", lambda: call_log.append("a"))
    await manager.register_cleanup_callback("c1", lambda: call_log.append("b"))

    await manager.cleanup_client("c1")
    assert call_log == ["a", "b"]


@pytest.mark.asyncio()
async def test_mixed_keyed_and_unkeyed(manager: ClientLifecycleManager) -> None:
    """Keyed and unkeyed callbacks coexist correctly."""
    await manager.register_client("c1")
    call_log: list[str] = []

    await manager.register_cleanup_callback("c1", lambda: call_log.append("unkeyed1"))
    await manager.register_cleanup_callback(
        "c1", lambda: call_log.append("keyed_v1"), owner_key="timers"
    )
    await manager.register_cleanup_callback("c1", lambda: call_log.append("unkeyed2"))
    # Replace keyed callback
    await manager.register_cleanup_callback(
        "c1", lambda: call_log.append("keyed_v2"), owner_key="timers"
    )

    await manager.cleanup_client("c1")
    assert "keyed_v1" not in call_log
    assert "keyed_v2" in call_log
    assert "unkeyed1" in call_log
    assert "unkeyed2" in call_log


@pytest.mark.asyncio()
async def test_keyed_callback_atomic_replacement(manager: ClientLifecycleManager) -> None:
    """Keyed replacement is atomic (single-assignment, not filter-then-append)."""
    await manager.register_client("c1")

    # Register three keyed callbacks with different keys
    await manager.register_cleanup_callback(
        "c1", lambda: None, owner_key="a"
    )
    await manager.register_cleanup_callback(
        "c1", lambda: None, owner_key="b"
    )
    await manager.register_cleanup_callback(
        "c1", lambda: None, owner_key="c"
    )

    # Replace "b" — should leave "a" and "c" untouched
    await manager.register_cleanup_callback(
        "c1", lambda: None, owner_key="b"
    )

    async with manager._lock:
        entries = manager.client_callbacks["c1"]
    assert len(entries) == 3
    keys = [e[1] for e in entries if isinstance(e, tuple)]
    assert keys == ["a", "c", "b"]  # b moved to end after replacement


# ============================================================================
# Cleanup Handles Both Shapes (Migration Tolerance)
# ============================================================================


@pytest.mark.asyncio()
async def test_cleanup_handles_async_callbacks(manager: ClientLifecycleManager) -> None:
    """Async cleanup callbacks are awaited."""
    await manager.register_client("c1")
    call_log: list[str] = []

    async def async_cleanup() -> None:
        call_log.append("async_done")

    await manager.register_cleanup_callback("c1", async_cleanup, owner_key="async")
    await manager.cleanup_client("c1")
    assert call_log == ["async_done"]


@pytest.mark.asyncio()
async def test_cleanup_callback_exception_does_not_block_others(
    manager: ClientLifecycleManager,
) -> None:
    """A failing callback does not prevent subsequent callbacks from running."""
    await manager.register_client("c1")
    call_log: list[str] = []

    def fail() -> None:
        raise RuntimeError("boom")

    await manager.register_cleanup_callback("c1", fail, owner_key="fail")
    await manager.register_cleanup_callback(
        "c1", lambda: call_log.append("ok"), owner_key="ok"
    )

    await manager.cleanup_client("c1")
    assert call_log == ["ok"]


@pytest.mark.asyncio()
async def test_cleanup_empty_client(manager: ClientLifecycleManager) -> None:
    """Cleanup on unknown client_id does not raise."""
    await manager.cleanup_client("nonexistent")


# ============================================================================
# Singleton and Basic Operations
# ============================================================================


@pytest.mark.asyncio()
async def test_singleton_returns_same_instance() -> None:
    a = ClientLifecycleManager.get()
    b = ClientLifecycleManager.get()
    assert a is b


@pytest.mark.asyncio()
async def test_register_and_check_active(manager: ClientLifecycleManager) -> None:
    await manager.register_client("c1")
    assert await manager.is_client_active("c1")
    await manager.cleanup_client("c1")
    assert not await manager.is_client_active("c1")


@pytest.mark.asyncio()
async def test_active_client_count(manager: ClientLifecycleManager) -> None:
    await manager.register_client("c1")
    await manager.register_client("c2")
    assert await manager.get_active_client_count() == 2
    await manager.cleanup_client("c1")
    assert await manager.get_active_client_count() == 1
