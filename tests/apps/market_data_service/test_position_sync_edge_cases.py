"""
Comprehensive edge case tests for PositionBasedSubscription.

This test module complements test_position_sync.py and test_position_sync_comprehensive.py
by targeting specific edge cases and error paths to achieve 85%+ branch coverage.

Coverage targets:
- Initial sync error handling (lines 90-103)
- shutdown() method logic (lines 191-211)
- set_task() method (line 222)
- _sync_subscriptions error branches (lines 270-271, 287-306)
- _fetch_position_symbols error paths (lines 364-370)
- Sync loop error branches (lines 136, 144, 152)
"""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from apps.market_data_service.position_sync import PositionBasedSubscription
from libs.data.market_data.exceptions import SubscriptionError


@pytest.fixture()
def mock_stream():
    """Create mock AlpacaMarketDataStream."""
    stream = Mock()
    stream.subscribe_symbols = AsyncMock()
    stream.unsubscribe_symbols = AsyncMock()
    stream.get_subscribed_symbols = Mock(return_value=[])
    return stream


@pytest.fixture()
def position_sync(mock_stream):
    """Create PositionBasedSubscription with mocked dependencies."""
    return PositionBasedSubscription(
        stream=mock_stream,
        execution_gateway_url="http://localhost:8002",
        sync_interval=1,
        initial_sync=False,
    )


class TestInitialSyncErrorHandling:
    """Test error handling during initial sync on startup (lines 90-103)."""

    @pytest.mark.asyncio()
    async def test_initial_sync_http_status_error(self, mock_stream):
        """
        Should log error and continue when initial sync fails with HTTP error.

        Tests line 90-95 branch coverage for HTTPStatusError during initial sync.
        """
        manager = PositionBasedSubscription(
            stream=mock_stream,
            execution_gateway_url="http://localhost:8002",
            sync_interval=0.1,
            initial_sync=True,  # Enable initial sync
        )

        # Mock HTTP error during initial sync
        mock_response = Mock()
        mock_response.status_code = 503
        mock_response.request = Mock()
        mock_response.request.url = "http://localhost:8002/api/v1/positions"

        http_error = httpx.HTTPStatusError(
            "Service unavailable", request=mock_response.request, response=mock_response
        )

        with patch.object(manager, "_fetch_position_symbols", side_effect=http_error):
            # Start loop
            task = asyncio.create_task(manager.start_sync_loop())

            # Wait for initial sync to complete
            await asyncio.sleep(0.15)

            # Stop and verify it continues running despite error
            manager.stop()
            await task

        # Manager should have attempted sync and recovered
        assert not manager._running

    @pytest.mark.asyncio()
    async def test_initial_sync_connect_timeout(self, mock_stream):
        """
        Should log error and continue when initial sync times out.

        Tests line 96-101 branch coverage for ConnectTimeout during initial sync.
        """
        manager = PositionBasedSubscription(
            stream=mock_stream,
            execution_gateway_url="http://localhost:8002",
            sync_interval=0.1,
            initial_sync=True,
        )

        # Mock timeout error
        with patch.object(
            manager,
            "_fetch_position_symbols",
            side_effect=httpx.ConnectTimeout("Connection timeout"),
        ):
            task = asyncio.create_task(manager.start_sync_loop())
            await asyncio.sleep(0.15)
            manager.stop()
            await task

        assert not manager._running

    @pytest.mark.asyncio()
    async def test_initial_sync_connect_error(self, mock_stream):
        """
        Should log error and continue when initial sync connection fails.

        Tests line 96-101 branch coverage for ConnectError during initial sync.
        """
        manager = PositionBasedSubscription(
            stream=mock_stream,
            execution_gateway_url="http://localhost:8002",
            sync_interval=0.1,
            initial_sync=True,
        )

        with patch.object(
            manager,
            "_fetch_position_symbols",
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            task = asyncio.create_task(manager.start_sync_loop())
            await asyncio.sleep(0.15)
            manager.stop()
            await task

        assert not manager._running

    @pytest.mark.asyncio()
    async def test_initial_sync_network_error(self, mock_stream):
        """
        Should log error and continue when initial sync has network error.

        Tests line 96-101 branch coverage for NetworkError during initial sync.
        """
        manager = PositionBasedSubscription(
            stream=mock_stream,
            execution_gateway_url="http://localhost:8002",
            sync_interval=0.1,
            initial_sync=True,
        )

        with patch.object(
            manager,
            "_fetch_position_symbols",
            side_effect=httpx.NetworkError("Network unreachable"),
        ):
            task = asyncio.create_task(manager.start_sync_loop())
            await asyncio.sleep(0.15)
            manager.stop()
            await task

        assert not manager._running

    @pytest.mark.asyncio()
    async def test_initial_sync_unexpected_exception(self, mock_stream):
        """
        Should log error and continue when initial sync raises unexpected exception.

        Tests line 102-107 branch coverage for generic exception during initial sync.
        """
        manager = PositionBasedSubscription(
            stream=mock_stream,
            execution_gateway_url="http://localhost:8002",
            sync_interval=0.1,
            initial_sync=True,
        )

        with patch.object(
            manager,
            "_fetch_position_symbols",
            side_effect=ValueError("Unexpected error"),
        ):
            task = asyncio.create_task(manager.start_sync_loop())
            await asyncio.sleep(0.15)
            manager.stop()
            await task

        assert not manager._running


class TestSyncLoopErrorBranches:
    """Test error handling in sync loop (lines 136, 144, 152)."""

    @pytest.mark.asyncio()
    async def test_sync_loop_subscription_error(self, position_sync):
        """
        Should continue loop when SubscriptionError occurs during sync.

        Tests line 136 branch coverage for SubscriptionError in sync loop.
        """
        call_count = 0

        async def mock_sync_with_error():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise SubscriptionError("Failed to subscribe")

        with patch.object(position_sync, "_sync_subscriptions", side_effect=mock_sync_with_error):
            task = asyncio.create_task(position_sync.start_sync_loop())
            await asyncio.sleep(2.5)  # Wait for 3 sync intervals (1s each)
            position_sync.stop()
            await task

        # Should have called sync multiple times despite first error
        assert call_count >= 2

    @pytest.mark.asyncio()
    async def test_sync_loop_http_status_error(self, position_sync):
        """
        Should continue loop when HTTPStatusError occurs during sync.

        Tests line 144 branch coverage for HTTPStatusError in sync loop.
        """
        call_count = 0
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.request = Mock()
        mock_response.request.url = "http://localhost:8002/api/v1/positions"

        async def mock_sync_with_error():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.HTTPStatusError(
                    "Server error", request=mock_response.request, response=mock_response
                )

        with patch.object(position_sync, "_sync_subscriptions", side_effect=mock_sync_with_error):
            task = asyncio.create_task(position_sync.start_sync_loop())
            await asyncio.sleep(2.5)  # Wait for 3 sync intervals
            position_sync.stop()
            await task

        assert call_count >= 2

    @pytest.mark.asyncio()
    async def test_sync_loop_network_errors(self, position_sync):
        """
        Should continue loop when network errors occur during sync.

        Tests line 152 branch coverage for ConnectTimeout, ConnectError, NetworkError.
        """
        call_count = 0

        async def mock_sync_with_error():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectTimeout("Timeout")
            elif call_count == 2:
                raise httpx.ConnectError("Connection failed")
            elif call_count == 3:
                raise httpx.NetworkError("Network error")

        with patch.object(position_sync, "_sync_subscriptions", side_effect=mock_sync_with_error):
            task = asyncio.create_task(position_sync.start_sync_loop())
            await asyncio.sleep(4.5)  # Wait for 5 sync intervals
            position_sync.stop()
            await task

        assert call_count >= 4


class TestShutdownMethod:
    """Test shutdown() method logic (lines 191-211)."""

    @pytest.mark.asyncio()
    async def test_shutdown_graceful_completion(self, position_sync):
        """
        Should wait for task to complete gracefully within timeout.

        Tests lines 191-200 branch coverage for successful graceful shutdown.
        """
        task = asyncio.create_task(position_sync.start_sync_loop())
        position_sync.set_task(task)

        await asyncio.sleep(0.1)

        # Should complete gracefully
        await position_sync.shutdown(timeout=2.0)

        assert task.done()
        assert not position_sync._running
        assert position_sync._shutdown_event.is_set()

    @pytest.mark.asyncio()
    async def test_shutdown_timeout_cancellation(self, position_sync):
        """
        Should cancel task when it doesn't complete within timeout.

        Tests lines 201-209 branch coverage for timeout and forced cancellation.
        """

        # Create a task that won't complete quickly
        async def slow_task():
            try:
                while True:
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                raise

        task = asyncio.create_task(slow_task())
        position_sync.set_task(task)

        # Shutdown with very short timeout
        await position_sync.shutdown(timeout=0.1)

        # Task should be cancelled
        assert task.done()

    @pytest.mark.asyncio()
    async def test_shutdown_already_cancelled_task(self, position_sync):
        """
        Should handle case where task is already cancelled.

        Tests lines 210-211 branch coverage for CancelledError handling.
        """
        task = asyncio.create_task(position_sync.start_sync_loop())
        position_sync.set_task(task)

        await asyncio.sleep(0.1)

        # Cancel task before shutdown
        task.cancel()

        # Wait a bit for cancellation to process
        try:
            await asyncio.wait_for(task, timeout=0.5)
        except asyncio.CancelledError:
            pass

        # Shutdown should handle already-cancelled task
        await position_sync.shutdown(timeout=1.0)

        assert task.done()


class TestSetTaskMethod:
    """Test set_task() method (line 222)."""

    @pytest.mark.asyncio()
    async def test_set_task_stores_reference(self, position_sync):
        """
        Should store task reference for shutdown management.

        Tests line 222 coverage for set_task() method.
        """

        async def dummy():
            await asyncio.sleep(0.01)

        task = asyncio.create_task(dummy())
        position_sync.set_task(task)

        assert position_sync._sync_task is task

        # Cleanup
        await task


class TestSyncSubscriptionsErrorBranches:
    """Test error handling in _sync_subscriptions (lines 270-271, 287-306)."""

    @pytest.mark.asyncio()
    async def test_sync_subscribe_error_handling(self, position_sync, mock_stream):
        """
        Should log error when subscribe fails but continue.

        Tests line 270-271 branch coverage for subscription error during subscribe.
        """
        position_sync._fetch_position_symbols = AsyncMock(return_value={"AAPL"})
        mock_stream.get_subscribed_symbols.return_value = []
        mock_stream.subscribe_symbols.side_effect = SubscriptionError("Subscribe failed")

        # Should not raise
        await position_sync._sync_subscriptions()

        # Error should be logged, not raised
        mock_stream.subscribe_symbols.assert_called_once()

    @pytest.mark.asyncio()
    async def test_sync_unsubscribe_error_handling(self, position_sync, mock_stream):
        """
        Should log error when unsubscribe fails but continue.

        Tests line 270-271 branch coverage for subscription error during unsubscribe.
        """
        position_sync._last_position_symbols = {"AAPL"}
        position_sync._fetch_position_symbols = AsyncMock(return_value=set())
        mock_stream.get_subscribed_symbols.return_value = ["AAPL"]
        mock_stream.unsubscribe_symbols.side_effect = SubscriptionError("Unsubscribe failed")

        # Should not raise
        await position_sync._sync_subscriptions()

        # Error should be logged
        mock_stream.unsubscribe_symbols.assert_called_once()

    @pytest.mark.asyncio()
    async def test_sync_http_status_error(self, position_sync):
        """
        Should handle HTTPStatusError during sync.

        Tests line 293-298 branch coverage for HTTP errors in _sync_subscriptions.
        """
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.request = Mock()
        mock_response.request.url = "http://localhost:8002/api/v1/positions"

        position_sync._fetch_position_symbols = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Not found", request=mock_response.request, response=mock_response
            )
        )

        # Should not raise
        await position_sync._sync_subscriptions()

    @pytest.mark.asyncio()
    async def test_sync_network_timeout_error(self, position_sync):
        """
        Should handle ConnectTimeout during sync.

        Tests line 299-304 branch coverage for network errors in _sync_subscriptions.
        """
        position_sync._fetch_position_symbols = AsyncMock(
            side_effect=httpx.ConnectTimeout("Timeout")
        )

        # Should not raise
        await position_sync._sync_subscriptions()

    @pytest.mark.asyncio()
    async def test_sync_connect_error(self, position_sync):
        """
        Should handle ConnectError during sync.

        Tests line 299-304 branch coverage for connection errors in _sync_subscriptions.
        """
        position_sync._fetch_position_symbols = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        # Should not raise
        await position_sync._sync_subscriptions()

    @pytest.mark.asyncio()
    async def test_sync_network_error(self, position_sync):
        """
        Should handle NetworkError during sync.

        Tests line 299-304 branch coverage for generic network errors in _sync_subscriptions.
        """
        position_sync._fetch_position_symbols = AsyncMock(
            side_effect=httpx.NetworkError("Network unreachable")
        )

        # Should not raise
        await position_sync._sync_subscriptions()

    @pytest.mark.asyncio()
    async def test_sync_unexpected_exception(self, position_sync):
        """
        Should handle unexpected exceptions during sync.

        Tests line 305-310 branch coverage for generic exceptions in _sync_subscriptions.
        """
        position_sync._fetch_position_symbols = AsyncMock(
            side_effect=RuntimeError("Unexpected error")
        )

        # Should not raise
        await position_sync._sync_subscriptions()


class TestFetchPositionSymbolsErrorPaths:
    """Test error handling in _fetch_position_symbols (lines 364-370)."""

    @pytest.mark.asyncio()
    async def test_fetch_data_processing_value_error(self, position_sync):
        """
        Should return None when JSON parsing fails with ValueError.

        Tests line 356-362 branch coverage for data processing errors.
        """
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("Invalid JSON")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await position_sync._fetch_position_symbols()

        assert result is None

    @pytest.mark.asyncio()
    async def test_fetch_data_processing_key_error(self, position_sync):
        """
        Should return empty set when response is missing expected keys.

        Tests line 356-362 branch coverage - code gracefully handles missing keys
        by using .get() with default empty list.
        """
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}  # Missing 'positions' key

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await position_sync._fetch_position_symbols()

        # Code uses .get("positions", []) which returns empty list, resulting in empty set
        assert result == set()

    @pytest.mark.asyncio()
    async def test_fetch_data_processing_type_error(self, position_sync):
        """
        Should return None when response data has wrong type.

        Tests line 356-362 branch coverage for TypeError in data processing.
        """
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"positions": "not a list"}

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await position_sync._fetch_position_symbols()

        assert result is None

    @pytest.mark.asyncio()
    async def test_fetch_generic_exception(self, position_sync):
        """
        Should return None on unexpected exception during fetch.

        Tests line 364-370 branch coverage for generic exception handling.
        """
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=RuntimeError("Unexpected error"))
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await position_sync._fetch_position_symbols()

        assert result is None


class TestSourceParameterInSubscriptions:
    """Test that source='position' parameter is used in subscribe/unsubscribe calls."""

    @pytest.mark.asyncio()
    async def test_subscribe_uses_source_parameter(self, position_sync, mock_stream):
        """
        Should call subscribe_symbols with source='position' for ref-counting.

        Tests that H5 fix (ref-counting) is properly implemented.
        """
        position_sync._fetch_position_symbols = AsyncMock(return_value={"AAPL", "MSFT"})
        mock_stream.get_subscribed_symbols.return_value = []

        await position_sync._sync_subscriptions()

        # Verify subscribe was called with source='position'
        mock_stream.subscribe_symbols.assert_called_once()
        args, kwargs = mock_stream.subscribe_symbols.call_args
        assert "source" in kwargs
        assert kwargs["source"] == "position"

    @pytest.mark.asyncio()
    async def test_unsubscribe_uses_source_parameter(self, position_sync, mock_stream):
        """
        Should call unsubscribe_symbols with source='position' for ref-counting.

        Tests that H5 fix (ref-counting) is properly implemented for unsubscribe.
        """
        position_sync._last_position_symbols = {"AAPL"}
        position_sync._fetch_position_symbols = AsyncMock(return_value=set())
        mock_stream.get_subscribed_symbols.return_value = ["AAPL"]

        await position_sync._sync_subscriptions()

        # Verify unsubscribe was called with source='position'
        mock_stream.unsubscribe_symbols.assert_called_once()
        args, kwargs = mock_stream.unsubscribe_symbols.call_args
        assert "source" in kwargs
        assert kwargs["source"] == "position"


class TestComplexScenarios:
    """Test complex scenarios involving multiple state transitions."""

    @pytest.mark.asyncio()
    async def test_positions_missing_symbol_field(self, position_sync):
        """
        Should skip positions without symbol field gracefully.

        Tests that malformed position data doesn't break subscription logic.
        """
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "positions": [
                {"symbol": "AAPL", "qty": 100},
                {"qty": 50},  # Missing symbol
                {"symbol": "MSFT", "qty": 25},
            ]
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            symbols = await position_sync._fetch_position_symbols()

        # Should only include positions with symbols
        assert symbols == {"AAPL", "MSFT"}

    @pytest.mark.asyncio()
    async def test_mixed_success_and_failure(self, position_sync, mock_stream):
        """
        Should handle case where subscribe succeeds but unsubscribe fails.

        Tests robustness when operations partially fail.
        """
        position_sync._last_position_symbols = {"MSFT"}
        position_sync._fetch_position_symbols = AsyncMock(return_value={"AAPL"})
        mock_stream.get_subscribed_symbols.return_value = ["MSFT"]

        # Subscribe succeeds, unsubscribe fails
        mock_stream.subscribe_symbols = AsyncMock()
        mock_stream.unsubscribe_symbols = AsyncMock(
            side_effect=SubscriptionError("Unsubscribe failed")
        )

        # Should not raise
        await position_sync._sync_subscriptions()

        # Both operations should have been attempted
        mock_stream.subscribe_symbols.assert_called_once()
        mock_stream.unsubscribe_symbols.assert_called_once()

        # State should update despite partial failure
        assert position_sync._last_position_symbols == {"AAPL"}
