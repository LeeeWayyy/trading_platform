"""
Comprehensive unit tests for PositionBasedSubscription.

Tests cover:
- Initialization and configuration
- Position fetching (HTTP client mocking)
- Subscription syncing (subscribe/unsubscribe logic)
- Error handling (timeouts, connection errors, HTTP errors)
- Background sync loop management
- State tracking and statistics

Target: Bring position_sync.py coverage from 13% to 85%+

See Also:
    - /docs/STANDARDS/TESTING.md - Testing standards
    - /docs/IMPLEMENTATION_GUIDES/p1t1-realtime-market-data.md - Implementation guide
"""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from apps.market_data_service.position_sync import PositionBasedSubscription
from libs.market_data.exceptions import SubscriptionError


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
        sync_interval=1,  # Short interval for testing
        initial_sync=False,  # Disable initial sync for most tests
    )


class TestPositionBasedSubscriptionInitialization:
    """Test initialization and configuration."""

    def test_initialization_with_defaults(self, mock_stream):
        """Should initialize with default configuration."""
        sync = PositionBasedSubscription(
            stream=mock_stream, execution_gateway_url="http://localhost:8002"
        )

        assert sync.stream == mock_stream
        assert sync.gateway_url == "http://localhost:8002"
        assert sync.sync_interval == 300  # Default 5 minutes
        assert sync.initial_sync is True  # Default enabled
        assert sync._running is False
        assert sync._last_position_symbols == set()

    def test_initialization_with_custom_config(self, mock_stream):
        """Should initialize with custom configuration."""
        sync = PositionBasedSubscription(
            stream=mock_stream,
            execution_gateway_url="http://localhost:8002/",  # Trailing slash
            sync_interval=60,
            initial_sync=False,
        )

        assert sync.gateway_url == "http://localhost:8002"  # Stripped trailing slash
        assert sync.sync_interval == 60
        assert sync.initial_sync is False

    def test_initialization_strips_trailing_slash(self, mock_stream):
        """Should strip trailing slash from gateway URL."""
        sync = PositionBasedSubscription(
            stream=mock_stream, execution_gateway_url="http://localhost:8002/////"
        )

        assert sync.gateway_url == "http://localhost:8002"


class TestFetchPositionSymbols:
    """Test position fetching from Execution Gateway."""

    @pytest.mark.asyncio()
    async def test_fetch_success_with_positions(self, position_sync):
        """Should fetch position symbols from gateway successfully."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json = Mock(
            return_value={
                "positions": [
                    {"symbol": "AAPL", "qty": 100},
                    {"symbol": "MSFT", "qty": 50},
                    {"symbol": "GOOGL", "qty": 25},
                ]
            }
        )

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            symbols = await position_sync._fetch_position_symbols()

        assert symbols == {"AAPL", "MSFT", "GOOGL"}

    @pytest.mark.asyncio()
    async def test_fetch_success_empty_positions(self, position_sync):
        """Should return empty set when no positions exist."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json = Mock(return_value={"positions": []})

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            symbols = await position_sync._fetch_position_symbols()

        assert symbols == set()

    @pytest.mark.asyncio()
    async def test_fetch_http_error_returns_none(self, position_sync):
        """Should return None on HTTP error status."""
        mock_response = Mock()
        mock_response.status_code = 500

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            symbols = await position_sync._fetch_position_symbols()

        assert symbols is None

    @pytest.mark.asyncio()
    async def test_fetch_timeout_returns_none(self, position_sync):
        """Should return None on timeout."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
            mock_client_class.return_value.__aenter__.return_value = mock_client

            symbols = await position_sync._fetch_position_symbols()

        assert symbols is None

    @pytest.mark.asyncio()
    async def test_fetch_connection_error_returns_none(self, position_sync):
        """Should return None on connection error."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection failed"))
            mock_client_class.return_value.__aenter__.return_value = mock_client

            symbols = await position_sync._fetch_position_symbols()

        assert symbols is None

    @pytest.mark.asyncio()
    async def test_fetch_unexpected_exception_returns_none(self, position_sync):
        """Should return None on unexpected exception."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=ValueError("Unexpected error"))
            mock_client_class.return_value.__aenter__.return_value = mock_client

            symbols = await position_sync._fetch_position_symbols()

        assert symbols is None


class TestSyncSubscriptions:
    """Test subscription syncing logic."""

    @pytest.mark.asyncio()
    async def test_sync_subscribe_to_new_symbols(self, position_sync, mock_stream):
        """Should subscribe to new position symbols."""
        # Mock fetch to return new positions
        position_sync._fetch_position_symbols = AsyncMock(return_value={"AAPL", "MSFT"})

        # No currently subscribed symbols
        mock_stream.get_subscribed_symbols.return_value = []

        await position_sync._sync_subscriptions()

        # Should subscribe to both new symbols
        mock_stream.subscribe_symbols.assert_called_once()
        subscribed_symbols = mock_stream.subscribe_symbols.call_args[0][0]
        assert set(subscribed_symbols) == {"AAPL", "MSFT"}

    @pytest.mark.asyncio()
    async def test_sync_unsubscribe_from_closed_positions(self, position_sync, mock_stream):
        """Should unsubscribe from symbols with closed positions."""
        # Previously had AAPL and MSFT
        position_sync._last_position_symbols = {"AAPL", "MSFT"}

        # Now only have AAPL
        position_sync._fetch_position_symbols = AsyncMock(return_value={"AAPL"})

        # Mock subscribed symbols
        mock_stream.get_subscribed_symbols.return_value = ["AAPL", "MSFT"]

        await position_sync._sync_subscriptions()

        # Should unsubscribe from MSFT with source="position" (H5 fix: ref-counting)
        mock_stream.unsubscribe_symbols.assert_called_once_with(["MSFT"], source="position")

    @pytest.mark.asyncio()
    async def test_sync_no_changes(self, position_sync, mock_stream):
        """Should handle no changes gracefully."""
        # Previously had AAPL
        position_sync._last_position_symbols = {"AAPL"}

        # Still have AAPL
        position_sync._fetch_position_symbols = AsyncMock(return_value={"AAPL"})

        # Mock subscribed symbols
        mock_stream.get_subscribed_symbols.return_value = ["AAPL"]

        await position_sync._sync_subscriptions()

        # Should not subscribe or unsubscribe
        mock_stream.subscribe_symbols.assert_not_called()
        mock_stream.unsubscribe_symbols.assert_not_called()

    @pytest.mark.asyncio()
    async def test_sync_handles_subscription_error(self, position_sync, mock_stream):
        """Should handle SubscriptionError gracefully."""
        position_sync._fetch_position_symbols = AsyncMock(return_value={"AAPL"})
        mock_stream.get_subscribed_symbols.return_value = []
        mock_stream.subscribe_symbols.side_effect = SubscriptionError("Subscribe failed")

        # Should not raise, just log error
        await position_sync._sync_subscriptions()

    @pytest.mark.asyncio()
    async def test_sync_skips_when_fetch_fails(self, position_sync, mock_stream):
        """Should skip sync when position fetch returns None."""
        position_sync._fetch_position_symbols = AsyncMock(return_value=None)

        await position_sync._sync_subscriptions()

        # Should not attempt subscribe/unsubscribe
        mock_stream.subscribe_symbols.assert_not_called()
        mock_stream.unsubscribe_symbols.assert_not_called()

    @pytest.mark.asyncio()
    async def test_sync_updates_tracking_state(self, position_sync):
        """Should update _last_position_symbols after successful sync."""
        position_sync._fetch_position_symbols = AsyncMock(return_value={"AAPL", "MSFT"})

        await position_sync._sync_subscriptions()

        # Should update tracked symbols
        assert position_sync._last_position_symbols == {"AAPL", "MSFT"}


class TestSyncLoop:
    """Test background sync loop."""

    @pytest.mark.asyncio()
    async def test_sync_loop_runs_initial_sync(self, mock_stream):
        """Should run initial sync on startup when enabled."""
        sync = PositionBasedSubscription(
            stream=mock_stream,
            execution_gateway_url="http://localhost:8002",
            sync_interval=10,
            initial_sync=True,
        )

        # Mock _sync_subscriptions
        sync._sync_subscriptions = AsyncMock()  # type: ignore[method-assign]

        # Start loop and immediately stop it
        loop_task = asyncio.create_task(sync.start_sync_loop())
        await asyncio.sleep(0.1)  # Let initial sync run
        sync.stop()
        await loop_task

        # Should have called sync at least once (initial sync)
        assert sync._sync_subscriptions.call_count >= 1

    @pytest.mark.asyncio()
    async def test_sync_loop_skips_initial_sync_when_disabled(self, position_sync):
        """Should skip initial sync when initial_sync=False."""
        position_sync._sync_subscriptions = AsyncMock()

        # Start loop and immediately stop it
        loop_task = asyncio.create_task(position_sync.start_sync_loop())
        await asyncio.sleep(0.1)
        position_sync.stop()
        await loop_task

        # Should not have called sync (initial_sync=False and didn't wait for interval)
        position_sync._sync_subscriptions.assert_not_called()

    @pytest.mark.asyncio()
    async def test_sync_loop_periodic_syncing(self, position_sync):
        """Should run periodic syncs at configured interval."""
        position_sync._sync_subscriptions = AsyncMock()

        # Start loop with 0.2s interval
        position_sync.sync_interval = 0.2

        loop_task = asyncio.create_task(position_sync.start_sync_loop())
        await asyncio.sleep(0.5)  # Wait for 2-3 syncs
        position_sync.stop()
        await loop_task

        # Should have called sync multiple times
        assert position_sync._sync_subscriptions.call_count >= 2

    @pytest.mark.asyncio()
    async def test_sync_loop_handles_cancelled_error(self, position_sync):
        """Should handle CancelledError gracefully and clean up _running flag."""
        position_sync._sync_subscriptions = AsyncMock()

        loop_task = asyncio.create_task(position_sync.start_sync_loop())
        await asyncio.sleep(0.1)
        loop_task.cancel()

        try:
            await loop_task
        except asyncio.CancelledError:
            pass  # Expected

        # M2 Fix: _running is now properly cleaned up in finally block
        # This ensures health checks report accurate status after cancellation
        assert position_sync._running is False  # Cleaned up in finally block

    @pytest.mark.asyncio()
    async def test_sync_loop_continues_on_sync_error(self, position_sync):
        """Should continue loop even when sync raises exception."""
        # First call raises, second call succeeds
        position_sync._sync_subscriptions = AsyncMock(side_effect=[ValueError("Sync error"), None])
        position_sync.sync_interval = 0.1

        loop_task = asyncio.create_task(position_sync.start_sync_loop())
        await asyncio.sleep(0.3)  # Wait for multiple syncs
        position_sync.stop()
        await loop_task

        # Should have attempted multiple syncs despite error
        assert position_sync._sync_subscriptions.call_count >= 2


class TestStopAndStats:
    """Test stop method and statistics."""

    def test_stop_sets_running_flag(self, position_sync):
        """Should set _running to False when stopped."""
        position_sync._running = True

        position_sync.stop()

        assert position_sync._running is False

    def test_get_stats_returns_status(self, position_sync, mock_stream):
        """Should return subscription manager statistics."""
        position_sync._running = True
        position_sync._last_position_symbols = {"AAPL", "MSFT"}
        mock_stream.get_subscribed_symbols.return_value = ["AAPL", "MSFT", "GOOGL"]

        stats = position_sync.get_stats()

        assert stats["running"] is True
        assert stats["gateway_url"] == "http://localhost:8002"
        assert stats["sync_interval"] == 1
        assert stats["last_position_count"] == 2
        assert set(stats["last_position_symbols"]) == {"AAPL", "MSFT"}
        assert stats["current_subscribed"] == ["AAPL", "MSFT", "GOOGL"]
