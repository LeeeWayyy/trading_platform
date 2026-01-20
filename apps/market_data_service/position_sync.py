"""
Position-Based Auto-Subscription

Automatically subscribes to real-time market data for symbols with open positions.
Queries Execution Gateway every 5 minutes to sync subscriptions.
"""

import asyncio
import logging
from typing import Any

import httpx

from libs.data.market_data import AlpacaMarketDataStream
from libs.data.market_data.exceptions import SubscriptionError

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
        # M2 Fix: Use asyncio.Event for clean shutdown instead of long sleep()
        self._shutdown_event: asyncio.Event = asyncio.Event()
        # M2 Fix: Store task handle for proper cancellation
        self._sync_task: asyncio.Task[None] | None = None

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

        try:
            # Run initial sync immediately
            if self.initial_sync:
                try:
                    await self._sync_subscriptions()
                except httpx.HTTPStatusError as e:
                    logger.error(
                        "Initial subscription sync failed - HTTP error",
                        extra={"status_code": e.response.status_code, "url": str(e.request.url)},
                        exc_info=True,
                    )
                except (httpx.ConnectTimeout, httpx.ConnectError, httpx.NetworkError) as e:
                    logger.error(
                        "Initial subscription sync failed - Network error",
                        extra={
                            "error": str(e),
                            "error_type": type(e).__name__,
                            "gateway_url": self.gateway_url,
                        },
                        exc_info=True,
                    )
                except Exception as e:
                    logger.error(
                        "Initial subscription sync failed - Unexpected error",
                        extra={"error": str(e), "error_type": type(e).__name__},
                        exc_info=True,
                    )

            # Main sync loop
            # M2 Fix: Use Event.wait() with timeout instead of sleep() for clean shutdown
            while self._running:
                try:
                    # Wait for shutdown event OR timeout (whichever comes first)
                    # This replaces asyncio.sleep() allowing immediate shutdown response
                    try:
                        await asyncio.wait_for(
                            self._shutdown_event.wait(),
                            timeout=float(self.sync_interval),
                        )
                        # If we get here, shutdown event was set
                        logger.info("Subscription sync loop received shutdown signal")
                        break
                    except TimeoutError:
                        # Normal timeout - time for next sync
                        pass

                    # Sync subscriptions (only if still running after wait)
                    if self._running:
                        await self._sync_subscriptions()

                except asyncio.CancelledError:
                    logger.info("Subscription sync loop cancelled")
                    raise  # Re-raise to exit the loop

                except SubscriptionError as e:
                    logger.error(
                        "Subscription sync error - SubscriptionError",
                        extra={"error": str(e), "error_type": type(e).__name__},
                        exc_info=True,
                    )
                    # Continue loop even on error

                except httpx.HTTPStatusError as e:
                    logger.error(
                        "Subscription sync error - HTTP error",
                        extra={"status_code": e.response.status_code, "url": str(e.request.url)},
                        exc_info=True,
                    )
                    # Continue loop even on error

                except (httpx.ConnectTimeout, httpx.ConnectError, httpx.NetworkError) as e:
                    logger.error(
                        "Subscription sync error - Network error",
                        extra={
                            "error": str(e),
                            "error_type": type(e).__name__,
                            "gateway_url": self.gateway_url,
                        },
                        exc_info=True,
                    )
                    # Continue loop even on error

                except Exception as e:
                    logger.error(
                        "Subscription sync error - Unexpected error",
                        extra={"error": str(e), "error_type": type(e).__name__},
                        exc_info=True,
                    )
                    # Continue loop even on error

        finally:
            # Ensure _running is always reset regardless of exit path
            # This fixes health reporting after cancellation
            self._running = False
            logger.info("Subscription sync loop stopped")

    def stop(self) -> None:
        """Stop background sync loop (non-blocking, signals shutdown)."""
        logger.info("Stopping subscription sync loop")
        self._running = False
        # M2 Fix: Set shutdown event to immediately interrupt any wait()
        self._shutdown_event.set()

    async def shutdown(self, timeout: float = 5.0) -> None:
        """
        Gracefully shutdown the sync loop with task cancellation.

        M2 Fix: Proper async shutdown that:
        1. Sets shutdown event to interrupt Event.wait()
        2. Waits for task to complete or cancels on timeout

        Args:
            timeout: Max seconds to wait for graceful shutdown before cancel
        """
        logger.info("Initiating graceful shutdown of subscription sync loop")

        # Signal shutdown via stop()
        self.stop()

        # Wait for task to complete gracefully
        if self._sync_task is not None and not self._sync_task.done():
            try:
                await asyncio.wait_for(self._sync_task, timeout=timeout)
                logger.info("Subscription sync task completed gracefully")
            except TimeoutError:
                logger.warning(
                    f"Subscription sync task did not complete within {timeout}s, cancelling"
                )
                self._sync_task.cancel()
                try:
                    await self._sync_task
                except asyncio.CancelledError:
                    logger.info("Subscription sync task cancelled")
            except asyncio.CancelledError:
                logger.info("Subscription sync task already cancelled")

    def set_task(self, task: asyncio.Task[None]) -> None:
        """
        Store the task handle for shutdown management.

        M2 Fix: Called by main.py after create_task() to enable proper cancellation.

        Args:
            task: The asyncio.Task running start_sync_loop()
        """
        self._sync_task = task

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

            # H5 Fix: Subscribe with source="position" for ref-counting
            # This ensures position-based subscriptions don't interfere with manual ones
            if new_symbols:
                try:
                    await self.stream.subscribe_symbols(list(new_symbols), source="position")
                    logger.info(
                        f"Auto-subscribed to {len(new_symbols)} new symbols (source=position): "
                        f"{sorted(new_symbols)}"
                    )
                except SubscriptionError as e:
                    logger.error(f"Failed to subscribe to new symbols: {e}")

            # H5 Fix: Unsubscribe with source="position" - only removes position source
            # If manual subscription exists, symbol stays subscribed
            if closed_symbols:
                try:
                    await self.stream.unsubscribe_symbols(list(closed_symbols), source="position")
                    logger.info(
                        f"Auto-unsubscribed from {len(closed_symbols)} closed symbols (source=position): "
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

        except SubscriptionError as e:
            logger.error(
                "Subscription sync failed - SubscriptionError",
                extra={"error": str(e), "error_type": type(e).__name__},
                exc_info=True,
            )
        except httpx.HTTPStatusError as e:
            logger.error(
                "Subscription sync failed - HTTP error",
                extra={"status_code": e.response.status_code, "url": str(e.request.url)},
                exc_info=True,
            )
        except (httpx.ConnectTimeout, httpx.ConnectError, httpx.NetworkError) as e:
            logger.error(
                "Subscription sync failed - Network error",
                extra={
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "gateway_url": self.gateway_url,
                },
                exc_info=True,
            )
        except Exception as e:
            logger.error(
                "Subscription sync failed - Unexpected error",
                extra={"error": str(e), "error_type": type(e).__name__},
                exc_info=True,
            )

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

        except httpx.TimeoutException as e:
            logger.error(
                "Timeout fetching positions from Execution Gateway",
                extra={"error": str(e), "gateway_url": self.gateway_url, "timeout": 10.0},
                exc_info=True,
            )
            return None

        except httpx.ConnectError as e:
            logger.error(
                "Cannot connect to Execution Gateway",
                extra={"error": str(e), "gateway_url": self.gateway_url},
                exc_info=True,
            )
            return None

        except (ValueError, KeyError, TypeError) as e:
            logger.error(
                "Data processing failed - invalid response structure",
                extra={"error": str(e), "error_type": type(e).__name__},
                exc_info=True,
            )
            return None

        except Exception as e:
            logger.error(
                "Error fetching positions - Unexpected error",
                extra={
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "gateway_url": self.gateway_url,
                },
                exc_info=True,
            )
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
