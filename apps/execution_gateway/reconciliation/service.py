"""Reconciliation service orchestrator.

This module provides the ReconciliationService class that maintains the
exact same public interface as the original monolithic implementation
while delegating to focused sub-modules.

IMPORTANT: The public API of this class MUST NOT change to maintain
backward compatibility with existing callers.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import psycopg
from prometheus_client import Gauge

from apps.execution_gateway.alpaca_client import AlpacaConnectionError
from apps.execution_gateway.reconciliation.fills import (
    backfill_alpaca_fills,
    backfill_fill_metadata,
    backfill_missing_fills_scan,
)
from apps.execution_gateway.reconciliation.helpers import (
    extract_broker_client_ids,
    merge_broker_orders,
)
from apps.execution_gateway.reconciliation.orders import (
    backfill_terminal_fills,
    reconcile_known_orders,
    reconcile_missing_orders,
)
from apps.execution_gateway.reconciliation.orphans import detect_orphans
from apps.execution_gateway.reconciliation.positions import reconcile_positions
from apps.execution_gateway.reconciliation.state import ReconciliationState

if TYPE_CHECKING:
    from apps.execution_gateway.alpaca_client import AlpacaExecutor
    from apps.execution_gateway.database import DatabaseClient
    from libs.core.redis_client import RedisClient

logger = logging.getLogger(__name__)

# Pod label for Prometheus metrics
POD_LABEL = os.getenv("POD_NAME") or os.getenv("HOSTNAME") or "unknown"

# Prometheus gauge for last run timestamp
reconciliation_last_run_timestamp = Gauge(
    "execution_gateway_reconciliation_last_run_timestamp",
    "Last successful reconciliation timestamp (epoch seconds)",
    ["pod"],
)


class ReconciliationService:
    """Synchronize broker state with local database.

    This class maintains the exact public interface of the original
    monolithic ReconciliationService while delegating to focused
    sub-modules for better testability and maintainability.

    Public API (MUST NOT CHANGE):
        - __init__(db_client, alpaca_client, redis_client, dry_run=False)
        - async run_startup_reconciliation() -> bool
        - async run_periodic_loop() -> None
        - async run_reconciliation_once(mode: str) -> None
        - async run_fills_backfill_once(*, lookback_hours=None, recalc_all_trades=False)
        - is_startup_complete() -> bool
        - startup_elapsed_seconds() -> float
        - startup_timed_out() -> bool
        - mark_startup_complete(forced=False, user_id=None, reason=None) -> None
        - override_active() -> bool
        - override_context() -> dict
        - stop() -> None
    """

    def __init__(
        self,
        db_client: DatabaseClient,
        alpaca_client: AlpacaExecutor,
        redis_client: RedisClient | None,
        dry_run: bool = False,
    ) -> None:
        """Initialize reconciliation service.

        Args:
            db_client: Database client for order/position operations.
            alpaca_client: Alpaca API client for broker state.
            redis_client: Redis client for quarantine. May be None.
            dry_run: If True, skip all reconciliation operations.
        """
        self.db_client = db_client
        self.alpaca_client = alpaca_client
        self.redis_client = redis_client
        self.dry_run = dry_run

        # Load config from environment
        self.poll_interval_seconds = int(os.getenv("RECONCILIATION_INTERVAL_SECONDS", "300"))
        self.timeout_seconds = int(os.getenv("RECONCILIATION_TIMEOUT_SECONDS", "300"))
        self.max_individual_lookups = int(os.getenv("RECONCILIATION_MAX_LOOKUPS", "100"))
        self.overlap_seconds = int(os.getenv("RECONCILIATION_OVERLAP_SECONDS", "60"))
        self.submitted_unconfirmed_grace_seconds = int(
            os.getenv("RECONCILIATION_SUBMITTED_UNCONFIRMED_GRACE_SECONDS", "300")
        )
        self.fills_backfill_enabled = os.getenv("ALPACA_FILLS_BACKFILL_ENABLED", "false").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.fills_backfill_initial_lookback_hours = int(
            os.getenv("ALPACA_FILLS_BACKFILL_INITIAL_LOOKBACK_HOURS", "24")
        )
        self.fills_backfill_page_size = int(os.getenv("ALPACA_FILLS_BACKFILL_PAGE_SIZE", "100"))
        self.fills_backfill_max_pages = int(os.getenv("ALPACA_FILLS_BACKFILL_MAX_PAGES", "5"))

        # State management (delegated)
        self._state = ReconciliationState(
            dry_run=dry_run,
            timeout_seconds=self.timeout_seconds,
        )

        # Async coordination
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()

    # -------------------------------------------------------------------------
    # Public API: State queries (delegated to ReconciliationState)
    # -------------------------------------------------------------------------

    def is_startup_complete(self) -> bool:
        """Check if startup reconciliation is complete."""
        return self._state.is_startup_complete()

    def startup_elapsed_seconds(self) -> float:
        """Get seconds elapsed since startup began."""
        return self._state.startup_elapsed_seconds()

    def startup_timed_out(self) -> bool:
        """Check if startup reconciliation has timed out."""
        return self._state.startup_timed_out()

    def mark_startup_complete(
        self,
        forced: bool = False,
        user_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Mark startup reconciliation as complete.

        See ReconciliationState.mark_startup_complete for details.
        """
        self._state.mark_startup_complete(forced=forced, user_id=user_id, reason=reason)

    def override_active(self) -> bool:
        """Check if a forced override is currently active."""
        return self._state.override_active()

    def override_context(self) -> dict[str, Any]:
        """Get the context of the active override."""
        return self._state.override_context()

    # -------------------------------------------------------------------------
    # Public API: Lifecycle methods
    # -------------------------------------------------------------------------

    def stop(self) -> None:
        """Stop the periodic reconciliation loop."""
        self._stop_event.set()

    async def run_startup_reconciliation(self) -> bool:
        """Run initial reconciliation at service startup.

        Returns:
            True if reconciliation succeeded, False if it failed.
            In dry-run mode, always returns True.
        """
        if self.dry_run:
            return True

        try:
            await self.run_reconciliation_once("startup")
            return True
        except AlpacaConnectionError as exc:
            self._state.record_reconciliation_result(
                {
                    "status": "failed",
                    "mode": "startup",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "error": str(exc),
                }
            )
            logger.error(
                "Startup reconciliation failed: Alpaca connection error",
                exc_info=True,
                extra={
                    "error": str(exc),
                    "error_type": "alpaca_connection",
                    "reconciliation_mode": "startup",
                },
            )
            return False
        except (psycopg.OperationalError, psycopg.IntegrityError) as exc:
            self._state.record_reconciliation_result(
                {
                    "status": "failed",
                    "mode": "startup",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "error": str(exc),
                }
            )
            logger.error(
                "Startup reconciliation failed: Database error",
                exc_info=True,
                extra={
                    "error": str(exc),
                    "error_type": "database",
                    "reconciliation_mode": "startup",
                },
            )
            return False
        except ValueError as exc:
            self._state.record_reconciliation_result(
                {
                    "status": "failed",
                    "mode": "startup",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "error": str(exc),
                }
            )
            logger.error(
                "Startup reconciliation failed: Data validation error",
                exc_info=True,
                extra={
                    "error": str(exc),
                    "error_type": "validation",
                    "reconciliation_mode": "startup",
                },
            )
            return False

    async def run_periodic_loop(self) -> None:
        """Run reconciliation periodically until stopped."""
        while not self._stop_event.is_set():
            try:
                await self.run_reconciliation_once("periodic")
                self._state.open_gate_after_successful_run("periodic")
            except AlpacaConnectionError as exc:
                logger.error(
                    "Periodic reconciliation failed: Alpaca connection error",
                    exc_info=True,
                    extra={
                        "error": str(exc),
                        "error_type": "alpaca_connection",
                        "reconciliation_mode": "periodic",
                    },
                )
            except (psycopg.OperationalError, psycopg.IntegrityError) as exc:
                logger.error(
                    "Periodic reconciliation failed: Database error",
                    exc_info=True,
                    extra={
                        "error": str(exc),
                        "error_type": "database",
                        "reconciliation_mode": "periodic",
                    },
                )
            except ValueError as exc:
                logger.error(
                    "Periodic reconciliation failed: Data validation error",
                    exc_info=True,
                    extra={
                        "error": str(exc),
                        "error_type": "validation",
                        "reconciliation_mode": "periodic",
                    },
                )
            await asyncio.sleep(self.poll_interval_seconds)

    async def run_reconciliation_once(self, mode: str) -> None:
        """Run a single reconciliation cycle.

        Args:
            mode: Reconciliation mode (startup, periodic, manual).
        """
        if self.dry_run:
            return

        async with self._lock:
            result = await asyncio.to_thread(self._run_reconciliation, mode)
            self._state.record_reconciliation_result(result)
            self._state.open_gate_after_successful_run(mode)

    async def run_fills_backfill_once(
        self,
        *,
        lookback_hours: int | None = None,
        recalc_all_trades: bool = False,
    ) -> dict[str, Any]:
        """Run a single fills backfill cycle.

        Args:
            lookback_hours: Override lookback window.
            recalc_all_trades: Whether to recalculate all trades.

        Returns:
            Dict with backfill results.
        """
        if self.dry_run:
            return {"status": "skipped", "message": "DRY_RUN mode - reconciliation disabled"}
        async with self._lock:
            return await asyncio.to_thread(
                self._backfill_alpaca_fills,
                lookback_hours=lookback_hours,
                recalc_all_trades=recalc_all_trades,
            )

    # -------------------------------------------------------------------------
    # Internal: Synchronous reconciliation logic
    # -------------------------------------------------------------------------

    def _run_reconciliation(self, mode: str) -> dict[str, Any]:
        """Run the core reconciliation logic synchronously.

        This method orchestrates calls to the sub-modules:
        - orders.py for order sync
        - fills.py for fill backfill
        - orphans.py for orphan detection
        - positions.py for position sync
        """
        start_time = datetime.now(UTC)
        logger.info("Reconciliation started", extra={"mode": mode})

        # Calculate time window
        last_check = self.db_client.get_reconciliation_high_water_mark()
        after_time = None
        if last_check:
            after_time = last_check - timedelta(seconds=self.overlap_seconds)

        # Pull orders from broker
        open_orders = self.alpaca_client.get_orders(status="open", limit=500, after=None)
        recent_orders: list[dict[str, Any]] = []
        if after_time:
            recent_orders = self.alpaca_client.get_orders(status="all", limit=500, after=after_time)

        # Merge and deduplicate orders
        orders_by_client = merge_broker_orders(open_orders, recent_orders)

        # Get DB orders for reconciliation
        db_orders = self.db_client.get_non_terminal_orders()

        # Get known order IDs for orphan detection
        broker_client_ids = extract_broker_client_ids(open_orders + recent_orders)
        db_known_ids: set[str] = set()
        if broker_client_ids:
            db_known_ids = self.db_client.get_order_ids_by_client_ids(broker_client_ids)

        # Create fill backfill callback
        def _backfill_callback(
            client_order_id: str,
            broker_order: dict[str, Any],
            updated_at: datetime | None = None,
            cached_order: Any | None = None,
        ) -> None:
            backfill_fill_metadata(
                client_order_id,
                broker_order,
                self.db_client,
                updated_at,
                cached_order,
            )

        # 1. Reconcile known orders
        reconcile_known_orders(
            db_orders,
            orders_by_client,
            self.db_client,
            _backfill_callback,
        )

        # 2. Handle missing orders
        reconcile_missing_orders(
            db_orders,
            after_time,
            self.db_client,
            self.alpaca_client,
            self.max_individual_lookups,
            self.submitted_unconfirmed_grace_seconds,
            _backfill_callback,
        )

        # 3. Detect orphan orders
        detect_orphans(
            open_orders,
            recent_orders,
            db_known_ids,
            self.db_client,
            self.redis_client,
        )

        # 4. Backfill fills for terminal orders
        def _simple_backfill_callback(
            client_order_id: str,
            broker_order: dict[str, Any],
        ) -> None:
            backfill_fill_metadata(client_order_id, broker_order, self.db_client)

        backfill_terminal_fills(
            orders_by_client,
            db_known_ids,
            _simple_backfill_callback,
        )

        # 5. Backfill from Alpaca activities API
        try:
            self._backfill_alpaca_fills()
        except Exception as exc:
            logger.warning(
                "Reconciliation Alpaca fills backfill failed",
                extra={"error": str(exc)},
            )

        # 6. Reconcile positions
        reconcile_positions(self.db_client, self.alpaca_client)

        # 7. Backfill missing fills from DB order data
        backfill_missing_fills_scan(self.db_client)

        # Update high-water mark
        self.db_client.set_reconciliation_high_water_mark(start_time)
        reconciliation_last_run_timestamp.labels(pod=POD_LABEL).set(start_time.timestamp())

        result = {
            "status": "success",
            "mode": mode,
            "timestamp": start_time.isoformat(),
            "open_orders_checked": len(open_orders),
            "db_orders_checked": len(db_orders),
        }
        logger.info("Reconciliation completed", extra={"mode": mode})
        return result

    def _backfill_alpaca_fills(
        self,
        *,
        lookback_hours: int | None = None,
        recalc_all_trades: bool = False,
    ) -> dict[str, Any]:
        """Backfill fills from Alpaca activities API."""
        return backfill_alpaca_fills(
            self.db_client,
            self.alpaca_client,
            lookback_hours=lookback_hours,
            recalc_all_trades=recalc_all_trades,
            fills_backfill_enabled=self.fills_backfill_enabled,
            fills_backfill_initial_lookback_hours=self.fills_backfill_initial_lookback_hours,
            fills_backfill_page_size=self.fills_backfill_page_size,
            fills_backfill_max_pages=self.fills_backfill_max_pages,
            overlap_seconds=self.overlap_seconds,
        )
