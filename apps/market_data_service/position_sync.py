"""
Position-Based Auto-Subscription

Automatically subscribes to real-time market data for symbols with open positions.
Queries Execution Gateway every 5 minutes to sync subscriptions.
"""

import asyncio
import logging
from typing import Any

import httpx

from libs.market_data import AlpacaMarketDataStream
from libs.market_data.exceptions import SubscriptionError

logger = logging.getLogger(__name__)


class PositionBasedSubscription:
    """
    Automatically subscribe to symbols with open positions.

    Queries Execution Gateway periodically to get list of symbols with open
    positions, then subscribes to real-time quotes for those symbols. Auto-
    unsubscribes from symbols when positions are closed.

    Example:
        subscription_manager = PositionBasedSubscription(
            stream=market_data_stream,
            execution_gateway_url="http://localhost:8002",
            sync_interval=300  # 5 minutes
        )

        # Start background sync (runs indefinitely)
        asyncio.create_task(subscription_manager.start_sync_loop())
    """

    def __init__(
        self,
        stream: AlpacaMarketDataStream,
        execution_gateway_url: str,
        sync_interval: int = 300,  # 5 minutes
        initial_sync: bool = True,
    ):
        """
        Initialize position-based subscription manager.

        Args:
            stream: AlpacaMarketDataStream instance
            execution_gateway_url: Execution Gateway base URL
            sync_interval: Seconds between syncs (default: 300 = 5 minutes)
            initial_sync: Run initial sync on startup (default: True)
        """
        self.stream = stream
        self.gateway_url = execution_gateway_url.rstrip("/")
        self.sync_interval = sync_interval
        self.initial_sync = initial_sync

        self._running = False
        self._last_position_symbols: set[str] = set()

        logger.info(
            f"PositionBasedSubscription initialized: "
            f"gateway={self.gateway_url}, interval={self.sync_interval}s"
        )

    async def start_sync_loop(self) -> None:
        """
        Start background sync loop.

        Runs indefinitely until stopped. Queries Execution Gateway for open
        positions and syncs subscriptions.

        Runs initial sync immediately if initial_sync=True, then waits for
        sync_interval before next sync.
        """
        self._running = True
        logger.info("Starting position-based subscription sync loop")

        # Run initial sync immediately
        if self.initial_sync:
            try:
                await self._sync_subscriptions()
            except Exception as e:
                logger.error(f"Initial subscription sync failed: {e}")

        # Main sync loop
        while self._running:
            try:
                # Wait for next sync interval
                await asyncio.sleep(self.sync_interval)

                # Sync subscriptions
                if self._running:  # Check again after sleep
                    await self._sync_subscriptions()

            except asyncio.CancelledError:
                logger.info("Subscription sync loop cancelled")
                break

            except Exception as e:
                logger.error(f"Subscription sync error: {e}", exc_info=True)
                # Continue loop even on error

        logger.info("Subscription sync loop stopped")

    def stop(self) -> None:
        """Stop background sync loop."""
        logger.info("Stopping subscription sync loop")
        self._running = False

    async def _sync_subscriptions(self) -> None:
        """
        Fetch open positions and sync subscriptions.

        Queries Execution Gateway for current positions, then:
        1. Subscribes to new symbols (not currently subscribed)
        2. Unsubscribes from closed symbols (no longer have position)
        """
        try:
            # Fetch positions from Execution Gateway
            position_symbols = await self._fetch_position_symbols()

            if position_symbols is None:
                logger.warning("Failed to fetch positions, skipping sync")
                return

            # Determine what changed
            # Only subscribe to symbols that aren't already subscribed
            current_subscribed_set = set(self.stream.get_subscribed_symbols())
            new_symbols = position_symbols - current_subscribed_set

            # Only unsubscribe from symbols that THIS auto-subscriber was managing
            # (don't touch manually-subscribed symbols)
            closed_symbols = self._last_position_symbols - position_symbols

            # Subscribe to new symbols
            if new_symbols:
                try:
                    await self.stream.subscribe_symbols(list(new_symbols))
                    logger.info(
                        f"Auto-subscribed to {len(new_symbols)} new symbols: "
                        f"{sorted(new_symbols)}"
                    )
                except SubscriptionError as e:
                    logger.error(f"Failed to subscribe to new symbols: {e}")

            # Unsubscribe from closed positions
            if closed_symbols:
                try:
                    await self.stream.unsubscribe_symbols(list(closed_symbols))
                    logger.info(
                        f"Auto-unsubscribed from {len(closed_symbols)} closed symbols: "
                        f"{sorted(closed_symbols)}"
                    )
                except SubscriptionError as e:
                    logger.error(f"Failed to unsubscribe from closed symbols: {e}")

            # Log summary
            if not new_symbols and not closed_symbols:
                logger.debug(
                    f"Subscription sync complete: {len(position_symbols)} symbols, " f"no changes"
                )
            else:
                logger.info(
                    f"Subscription sync complete: {len(position_symbols)} total symbols, "
                    f"+{len(new_symbols)} new, -{len(closed_symbols)} closed"
                )

            # Update tracking
            self._last_position_symbols = position_symbols

        except Exception as e:
            logger.error(f"Subscription sync failed: {e}", exc_info=True)

    async def _fetch_position_symbols(self) -> set[str] | None:
        """
        Fetch symbols with open positions from Execution Gateway.

        Returns:
            Set of symbols with open positions, or None if fetch failed

        Raises:
            No exceptions raised, returns None on error
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.gateway_url}/api/v1/positions")

                if response.status_code != 200:
                    logger.error(f"Failed to fetch positions: HTTP {response.status_code}")
                    return None

                data = response.json()
                positions = data.get("positions", [])

                # Extract symbols
                symbols = {pos["symbol"] for pos in positions if pos.get("symbol")}

                logger.debug(f"Fetched {len(symbols)} position symbols from gateway")

                return symbols

        except httpx.TimeoutException:
            logger.error("Timeout fetching positions from Execution Gateway")
            return None

        except httpx.ConnectError:
            logger.error(
                f"Cannot connect to Execution Gateway at {self.gateway_url}. " f"Is it running?"
            )
            return None

        except Exception as e:
            logger.error(f"Error fetching positions: {e}", exc_info=True)
            return None

    def get_stats(self) -> dict[str, Any]:
        """
        Get subscription manager statistics.

        Returns:
            Dictionary with sync status and metrics
        """
        return {
            "running": self._running,
            "gateway_url": self.gateway_url,
            "sync_interval": self.sync_interval,
            "last_position_count": len(self._last_position_symbols),
            "last_position_symbols": sorted(self._last_position_symbols),
            "current_subscribed": self.stream.get_subscribed_symbols(),
        }
