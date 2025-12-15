"""
Tests for M2: Position Sync Task Cancellation.

M2 Fix: Ensures position sync task is properly cancelled on shutdown using
asyncio.Event instead of long sleep().

Contract:
- Event.wait() with timeout replaces asyncio.sleep() for interruptible waits
- shutdown() method properly cancels task and waits for completion
- Graceful shutdown completes within timeout (not blocked by 300s sleep)
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.market_data_service.position_sync import PositionBasedSubscription


class TestShutdownEventSignal:
    """Test that asyncio.Event is used for clean cancellation."""

    def test_shutdown_event_initialized(self) -> None:
        """Shutdown event should be initialized as asyncio.Event."""
        mock_stream = MagicMock()
        manager = PositionBasedSubscription(
            stream=mock_stream,
            execution_gateway_url="http://localhost:8002",
            sync_interval=300,
            initial_sync=False,
        )

        assert hasattr(manager, "_shutdown_event")
        assert isinstance(manager._shutdown_event, asyncio.Event)
        # Event should start unset
        assert not manager._shutdown_event.is_set()

    def test_stop_sets_shutdown_event(self) -> None:
        """stop() should set the shutdown event to interrupt waits."""
        mock_stream = MagicMock()
        manager = PositionBasedSubscription(
            stream=mock_stream,
            execution_gateway_url="http://localhost:8002",
            sync_interval=300,
            initial_sync=False,
        )

        # Initially not set
        assert not manager._shutdown_event.is_set()

        # Call stop()
        manager.stop()

        # Should be set after stop()
        assert manager._shutdown_event.is_set()
        assert not manager._running


class TestShutdownCancelsTasks:
    """Test that shutdown properly cancels background tasks."""

    @pytest.mark.asyncio()
    async def test_shutdown_with_no_task(self) -> None:
        """shutdown() should handle case where no task is running."""
        mock_stream = MagicMock()
        manager = PositionBasedSubscription(
            stream=mock_stream,
            execution_gateway_url="http://localhost:8002",
            sync_interval=300,
            initial_sync=False,
        )

        # No task set
        assert manager._sync_task is None

        # Should complete without error
        await manager.shutdown(timeout=1.0)

        # Event should be set
        assert manager._shutdown_event.is_set()

    @pytest.mark.asyncio()
    async def test_shutdown_waits_for_task_completion(self) -> None:
        """shutdown() should wait for task to complete gracefully."""
        mock_stream = MagicMock()
        manager = PositionBasedSubscription(
            stream=mock_stream,
            execution_gateway_url="http://localhost:8002",
            sync_interval=1,  # 1 second for fast test
            initial_sync=False,
        )

        # Start the sync loop
        task = asyncio.create_task(manager.start_sync_loop())
        manager.set_task(task)

        # Wait a bit for task to start
        await asyncio.sleep(0.1)

        # Shutdown should complete quickly (not wait full 1s interval)
        start = asyncio.get_event_loop().time()
        await manager.shutdown(timeout=2.0)
        elapsed = asyncio.get_event_loop().time() - start

        # Should complete well under the timeout
        assert elapsed < 1.5
        assert task.done()

    @pytest.mark.asyncio()
    async def test_set_task_stores_handle(self) -> None:
        """set_task() should store the task handle for shutdown management."""
        mock_stream = MagicMock()
        manager = PositionBasedSubscription(
            stream=mock_stream,
            execution_gateway_url="http://localhost:8002",
            sync_interval=300,
            initial_sync=False,
        )

        # Create a dummy task
        async def dummy_coro() -> None:
            await asyncio.sleep(0.01)

        task = asyncio.create_task(dummy_coro())
        manager.set_task(task)

        assert manager._sync_task is task

        # Cleanup
        await task


class TestShutdownActiveSleep:
    """Test that task in long wait is cancelled promptly."""

    @pytest.mark.asyncio()
    async def test_event_wait_is_interruptible(self) -> None:
        """Event.wait() with timeout should be interruptible, unlike sleep()."""
        mock_stream = MagicMock()
        mock_stream.get_subscribed_symbols.return_value = []

        # Use a very long sync interval (300s) to simulate the issue
        manager = PositionBasedSubscription(
            stream=mock_stream,
            execution_gateway_url="http://localhost:8002",
            sync_interval=300,  # 5 minutes - would block without Event
            initial_sync=False,
        )

        # Start the sync loop
        task = asyncio.create_task(manager.start_sync_loop())
        manager.set_task(task)

        # Wait for task to enter the Event.wait()
        await asyncio.sleep(0.1)

        # Now shutdown - should complete quickly (not wait 300s)
        start = asyncio.get_event_loop().time()
        await manager.shutdown(timeout=2.0)
        elapsed = asyncio.get_event_loop().time() - start

        # Should complete in under 1 second (not 300 seconds!)
        assert elapsed < 1.0, f"Shutdown took {elapsed}s - Event wait not interruptible"
        assert task.done()

    @pytest.mark.asyncio()
    async def test_shutdown_cancels_on_timeout(self) -> None:
        """shutdown() should forcefully cancel task if it doesn't complete in time."""
        mock_stream = MagicMock()

        # Create a task that ignores the shutdown event
        async def stubborn_task() -> None:
            try:
                # Ignore the event and just block
                while True:
                    await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                # Re-raise as expected
                raise

        mock_stream.get_subscribed_symbols.return_value = []

        manager = PositionBasedSubscription(
            stream=mock_stream,
            execution_gateway_url="http://localhost:8002",
            sync_interval=300,
            initial_sync=False,
        )

        # Replace start_sync_loop with a stubborn task
        task = asyncio.create_task(stubborn_task())
        manager.set_task(task)

        # Shutdown with short timeout - should cancel
        await manager.shutdown(timeout=0.2)

        # Task should be cancelled
        assert task.cancelled() or task.done()


class TestSyncLoopBehavior:
    """Test that sync loop properly responds to shutdown signal."""

    @pytest.mark.asyncio()
    async def test_sync_loop_exits_on_shutdown_event(self) -> None:
        """Sync loop should exit when shutdown event is set."""
        mock_stream = MagicMock()
        mock_stream.get_subscribed_symbols.return_value = []
        mock_stream.subscribe_symbols = AsyncMock()
        mock_stream.unsubscribe_symbols = AsyncMock()

        manager = PositionBasedSubscription(
            stream=mock_stream,
            execution_gateway_url="http://localhost:8002",
            sync_interval=10,  # 10 seconds
            initial_sync=False,
        )

        # Start the loop
        task = asyncio.create_task(manager.start_sync_loop())
        manager.set_task(task)

        # Wait for it to enter the wait
        await asyncio.sleep(0.1)
        assert manager._running

        # Set the shutdown event directly
        manager._shutdown_event.set()

        # Wait for loop to exit
        await asyncio.wait_for(task, timeout=1.0)

        assert task.done()
        assert not task.cancelled()  # Should exit cleanly, not cancel

    @pytest.mark.asyncio()
    async def test_sync_loop_handles_cancelled_error(self) -> None:
        """Sync loop should handle CancelledError gracefully."""
        mock_stream = MagicMock()
        mock_stream.get_subscribed_symbols.return_value = []

        manager = PositionBasedSubscription(
            stream=mock_stream,
            execution_gateway_url="http://localhost:8002",
            sync_interval=300,
            initial_sync=False,
        )

        # Start the loop
        task = asyncio.create_task(manager.start_sync_loop())
        manager.set_task(task)

        # Wait for it to start
        await asyncio.sleep(0.1)

        # Cancel directly
        task.cancel()

        # Should complete without raising (CancelledError caught)
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except asyncio.CancelledError:
            pass  # Expected

        assert task.done()
