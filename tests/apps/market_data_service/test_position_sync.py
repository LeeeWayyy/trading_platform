"""
Tests for PositionBasedSubscription.

Tests auto-subscription logic with mocked HTTP requests and WebSocket client.
"""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from apps.market_data_service.position_sync import PositionBasedSubscription
from libs.data.market_data import AlpacaMarketDataStream


@pytest.fixture()
def mock_stream():
    """Mock AlpacaMarketDataStream."""
    stream = AsyncMock(spec=AlpacaMarketDataStream)
    stream.get_subscribed_symbols.return_value = []
    stream.subscribe_symbols = AsyncMock()
    stream.unsubscribe_symbols = AsyncMock()
    return stream


@pytest.fixture()
def subscription_manager(mock_stream):
    """Create PositionBasedSubscription with mocked stream."""
    return PositionBasedSubscription(
        stream=mock_stream,
        execution_gateway_url="http://localhost:8002",
        sync_interval=1,  # Fast sync for testing
        initial_sync=False,  # Disable initial sync for controlled testing
    )


class TestPositionBasedSubscription:
    """Tests for PositionBasedSubscription class."""

    def test_initialization(self, subscription_manager):
        """Test subscription manager initialization."""
        assert subscription_manager.gateway_url == "http://localhost:8002"
        assert subscription_manager.sync_interval == 1
        assert subscription_manager.initial_sync is False
        assert subscription_manager._running is False

    @pytest.mark.asyncio()
    async def test_fetch_position_symbols_success(self, subscription_manager):
        """Test fetching position symbols succeeds."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "positions": [
                {"symbol": "AAPL", "qty": "10"},
                {"symbol": "MSFT", "qty": "5"},
            ],
            "total_positions": 2,
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            symbols = await subscription_manager._fetch_position_symbols()

        assert symbols == {"AAPL", "MSFT"}

    @pytest.mark.asyncio()
    async def test_fetch_position_symbols_empty(self, subscription_manager):
        """Test fetching positions when no positions exist."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "positions": [],
            "total_positions": 0,
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            symbols = await subscription_manager._fetch_position_symbols()

        assert symbols == set()

    @pytest.mark.asyncio()
    async def test_fetch_position_symbols_http_error(self, subscription_manager):
        """Test fetching positions when HTTP request fails."""
        mock_response = Mock()
        mock_response.status_code = 500

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            symbols = await subscription_manager._fetch_position_symbols()

        assert symbols is None

    @pytest.mark.asyncio()
    async def test_fetch_position_symbols_timeout(self, subscription_manager):
        """Test fetching positions when request times out."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.get.side_effect = httpx.TimeoutException("Timeout")
            mock_client_class.return_value = mock_client

            symbols = await subscription_manager._fetch_position_symbols()

        assert symbols is None

    @pytest.mark.asyncio()
    async def test_fetch_position_symbols_connection_error(self, subscription_manager):
        """Test fetching positions when connection fails."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.get.side_effect = httpx.ConnectError("Connection failed")
            mock_client_class.return_value = mock_client

            symbols = await subscription_manager._fetch_position_symbols()

        assert symbols is None

    @pytest.mark.asyncio()
    async def test_sync_subscriptions_new_symbols(self, subscription_manager, mock_stream):
        """Test sync subscribes to new symbols."""
        # Mock stream has no current subscriptions
        mock_stream.get_subscribed_symbols.return_value = []

        # Mock HTTP response with positions
        with patch.object(
            subscription_manager, "_fetch_position_symbols", return_value={"AAPL", "MSFT"}
        ):
            await subscription_manager._sync_subscriptions()

        # Verify subscribe was called with new symbols
        mock_stream.subscribe_symbols.assert_called_once()
        call_args = mock_stream.subscribe_symbols.call_args[0][0]
        assert set(call_args) == {"AAPL", "MSFT"}

    @pytest.mark.asyncio()
    async def test_sync_subscriptions_closed_symbols(self, subscription_manager, mock_stream):
        """
        Test sync unsubscribes from closed symbols.

        HIGH priority fix: Only unsubscribe from symbols that THIS auto-subscriber
        was managing (tracked in _last_position_symbols), not ALL subscribed symbols.
        This prevents accidentally unsubscribing from manually-added symbols.
        """
        # Set up: last sync had AAPL and MSFT positions
        subscription_manager._last_position_symbols = {"AAPL", "MSFT"}

        # Mock stream is currently subscribed to AAPL, MSFT, and GOOGL
        # (GOOGL was manually added via API, not by this auto-subscriber)
        mock_stream.get_subscribed_symbols.return_value = ["AAPL", "MSFT", "GOOGL"]

        # Mock HTTP response: only AAPL position remains (MSFT closed)
        with patch.object(subscription_manager, "_fetch_position_symbols", return_value={"AAPL"}):
            await subscription_manager._sync_subscriptions()

        # Verify unsubscribe was called ONLY with MSFT (not GOOGL)
        # GOOGL should remain subscribed since it wasn't managed by auto-subscriber
        mock_stream.unsubscribe_symbols.assert_called_once()
        call_args = mock_stream.unsubscribe_symbols.call_args[0][0]
        assert set(call_args) == {"MSFT"}  # Only MSFT, NOT GOOGL

    @pytest.mark.asyncio()
    async def test_sync_subscriptions_no_changes(self, subscription_manager, mock_stream):
        """Test sync when subscriptions already match positions."""
        # Mock stream already subscribed to correct symbols
        mock_stream.get_subscribed_symbols.return_value = ["AAPL", "MSFT"]

        # Mock HTTP response with same positions
        with patch.object(
            subscription_manager, "_fetch_position_symbols", return_value={"AAPL", "MSFT"}
        ):
            await subscription_manager._sync_subscriptions()

        # Verify no subscribe/unsubscribe calls
        mock_stream.subscribe_symbols.assert_not_called()
        mock_stream.unsubscribe_symbols.assert_not_called()

    @pytest.mark.asyncio()
    async def test_sync_subscriptions_fetch_failure(self, subscription_manager, mock_stream):
        """Test sync handles fetch failure gracefully."""
        # Mock fetch failure (returns None)
        with patch.object(subscription_manager, "_fetch_position_symbols", return_value=None):
            await subscription_manager._sync_subscriptions()

        # Verify no subscribe/unsubscribe calls when fetch fails
        mock_stream.subscribe_symbols.assert_not_called()
        mock_stream.unsubscribe_symbols.assert_not_called()

    @pytest.mark.asyncio()
    async def test_start_sync_loop_with_initial_sync(self, mock_stream):
        """Test sync loop runs initial sync on startup."""
        manager = PositionBasedSubscription(
            stream=mock_stream,
            execution_gateway_url="http://localhost:8002",
            sync_interval=10,
            initial_sync=True,
        )

        # Mock position fetch
        with patch.object(manager, "_fetch_position_symbols", return_value={"AAPL"}):
            # Start loop and cancel after initial sync
            task = asyncio.create_task(manager.start_sync_loop())
            await asyncio.sleep(0.1)  # Let initial sync complete
            manager.stop()
            await task

        # Verify subscribe was called during initial sync
        mock_stream.subscribe_symbols.assert_called_once()

    @pytest.mark.asyncio()
    async def test_start_sync_loop_periodic_sync(self, subscription_manager, mock_stream):
        """Test sync loop runs periodically."""
        sync_count = 0

        async def mock_sync():
            nonlocal sync_count
            sync_count += 1

        with patch.object(subscription_manager, "_sync_subscriptions", side_effect=mock_sync):
            # Start loop
            task = asyncio.create_task(subscription_manager.start_sync_loop())

            # Wait for 2 sync intervals (1 second each)
            await asyncio.sleep(2.5)

            # Stop loop
            subscription_manager.stop()
            await task

        # Verify sync was called at least twice
        assert sync_count >= 2

    @pytest.mark.asyncio()
    async def test_sync_loop_continues_on_error(self, subscription_manager):
        """Test sync loop continues even when sync fails."""
        sync_count = 0

        async def mock_sync_with_error():
            nonlocal sync_count
            sync_count += 1
            if sync_count == 1:
                raise Exception("Test error")

        with patch.object(
            subscription_manager, "_sync_subscriptions", side_effect=mock_sync_with_error
        ):
            # Start loop
            task = asyncio.create_task(subscription_manager.start_sync_loop())

            # Wait for 2 sync intervals
            await asyncio.sleep(2.5)

            # Stop loop
            subscription_manager.stop()
            await task

        # Verify sync continued after error
        assert sync_count >= 2

    def test_get_stats(self, subscription_manager, mock_stream):
        """Test getting subscription manager stats."""
        subscription_manager._last_position_symbols = {"AAPL", "MSFT"}
        subscription_manager._running = True
        mock_stream.get_subscribed_symbols.return_value = ["AAPL", "MSFT", "GOOGL"]

        stats = subscription_manager.get_stats()

        assert stats["running"] is True
        assert stats["gateway_url"] == "http://localhost:8002"
        assert stats["sync_interval"] == 1
        assert stats["last_position_count"] == 2
        assert set(stats["last_position_symbols"]) == {"AAPL", "MSFT"}
        assert set(stats["current_subscribed"]) == {"AAPL", "MSFT", "GOOGL"}

    def test_stop(self, subscription_manager):
        """Test stopping subscription manager."""
        subscription_manager._running = True

        subscription_manager.stop()

        assert subscription_manager._running is False
