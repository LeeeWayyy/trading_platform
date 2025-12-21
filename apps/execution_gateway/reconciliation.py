"""Reconciliation service for execution gateway."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from prometheus_client import Counter, Gauge

from apps.execution_gateway.alpaca_client import AlpacaExecutor
from apps.execution_gateway.database import TERMINAL_STATUSES, DatabaseClient, status_rank_for
from libs.redis_client import RedisClient
from libs.redis_client.keys import RedisKeys

logger = logging.getLogger(__name__)

# Prometheus metrics
reconciliation_mismatches_total = Counter(
    "execution_gateway_reconciliation_mismatches_total",
    "Total order mismatches corrected by reconciliation",
)

reconciliation_conflicts_skipped_total = Counter(
    "execution_gateway_reconciliation_conflicts_skipped_total",
    "Total reconciliation updates skipped due to CAS conflict",
)

reconciliation_last_run_timestamp = Gauge(
    "execution_gateway_reconciliation_last_run_timestamp",
    "Last successful reconciliation timestamp (epoch seconds)",
)

symbols_quarantined_total = Counter(
    "execution_gateway_symbols_quarantined_total",
    "Total symbols quarantined due to orphan orders",
    ["symbol"],
)

# Source priority ordering (lower number = higher priority)
# Used as a tie-breaker in CAS when timestamp, status_rank, and filled_qty are equal.
# Manual interventions have highest authority and can overwrite any other source.
# Reconciliation (batch sync) can overwrite webhooks on timestamp ties.
# Webhooks are real-time but lowest priority on ties to preserve authoritative corrections.
#
# CAS condition: `source_priority > %s` allows lower numbers to win
# (e.g., Manual=1 overwrites Webhook=3 because 3 > 1 is True)
SOURCE_PRIORITY_MANUAL = 1
SOURCE_PRIORITY_RECONCILIATION = 2
SOURCE_PRIORITY_WEBHOOK = 3

QUARANTINE_STRATEGY_SENTINEL = "external"


class ReconciliationService:
    """Synchronize broker state with local database."""

    def __init__(
        self,
        db_client: DatabaseClient,
        alpaca_client: AlpacaExecutor,
        redis_client: RedisClient | None,
        dry_run: bool = False,
    ) -> None:
        self.db_client = db_client
        self.alpaca_client = alpaca_client
        self.redis_client = redis_client
        self.dry_run = dry_run

        self._startup_complete = False
        self._startup_started_at = datetime.now(UTC)
        self._override_active = False
        self._override_context: dict[str, Any] = {}

        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()

        self.poll_interval_seconds = int(os.getenv("RECONCILIATION_INTERVAL_SECONDS", "300"))
        self.timeout_seconds = int(os.getenv("RECONCILIATION_TIMEOUT_SECONDS", "300"))
        self.max_individual_lookups = int(os.getenv("RECONCILIATION_MAX_LOOKUPS", "100"))
        self.overlap_seconds = int(os.getenv("RECONCILIATION_OVERLAP_SECONDS", "60"))
        self.submitted_unconfirmed_grace_seconds = int(
            os.getenv("RECONCILIATION_SUBMITTED_UNCONFIRMED_GRACE_SECONDS", "300")
        )

    def is_startup_complete(self) -> bool:
        if self.dry_run:
            return True
        return self._startup_complete

    def startup_elapsed_seconds(self) -> float:
        return (datetime.now(UTC) - self._startup_started_at).total_seconds()

    def startup_timed_out(self) -> bool:
        return self.startup_elapsed_seconds() > self.timeout_seconds

    def mark_startup_complete(
        self, forced: bool = False, user_id: str | None = None, reason: str | None = None
    ) -> None:
        self._startup_complete = True
        if forced:
            self._override_active = True
            self._override_context = {
                "user_id": user_id,
                "reason": reason,
                "timestamp": datetime.now(UTC).isoformat(),
            }

    def override_active(self) -> bool:
        return self._override_active

    def override_context(self) -> dict[str, Any]:
        return dict(self._override_context)

    async def run_startup_reconciliation(self) -> bool:
        if self.dry_run:
            self._startup_complete = True
            return True

        try:
            await self.run_reconciliation_once("startup")
            return True
        except Exception as exc:
            logger.error("Startup reconciliation failed", exc_info=True, extra={"error": str(exc)})
            return False

    async def run_periodic_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.run_reconciliation_once("periodic")
                if not self._startup_complete:
                    self._startup_complete = True
                    logger.info("Startup reconciliation gate opened after successful periodic run")
            except Exception as exc:
                logger.error(
                    "Periodic reconciliation failed", exc_info=True, extra={"error": str(exc)}
                )
            await asyncio.sleep(self.poll_interval_seconds)

    def stop(self) -> None:
        self._stop_event.set()

    async def run_reconciliation_once(self, mode: str) -> None:
        if self.dry_run:
            return

        async with self._lock:
            await asyncio.to_thread(self._run_reconciliation, mode)
            if not self._startup_complete:
                self._startup_complete = True
                logger.info(
                    "Startup reconciliation gate opened after successful run",
                    extra={"mode": mode},
                )

    def _run_reconciliation(self, mode: str) -> None:
        start_time = datetime.now(UTC)
        logger.info("Reconciliation started", extra={"mode": mode})

        last_check = self.db_client.get_reconciliation_high_water_mark()
        after_time = None
        if last_check:
            after_time = last_check - timedelta(seconds=self.overlap_seconds)

        # Pull open orders every run
        open_orders = self.alpaca_client.get_orders(status="open", limit=500, after=None)

        # Pull recent orders using created_at window (best-effort)
        recent_orders: list[dict[str, Any]] = []
        if after_time:
            recent_orders = self.alpaca_client.get_orders(status="all", limit=500, after=after_time)

        # Merge order lists by client_order_id (prefer newest updated_at)
        orders_by_client: dict[str, dict[str, Any]] = {}
        for order in open_orders + recent_orders:
            client_id = order.get("client_order_id")
            if not client_id:
                continue
            existing = orders_by_client.get(client_id)
            if not existing:
                orders_by_client[client_id] = order
                continue
            existing_updated = existing.get("updated_at") or existing.get("created_at")
            current_updated = order.get("updated_at") or order.get("created_at")
            if current_updated and existing_updated and current_updated > existing_updated:
                orders_by_client[client_id] = order

        # DB non-terminal orders (for active reconciliation)
        db_orders = self.db_client.get_non_terminal_orders()

        # Gather known order ids for orphan detection (include terminal orders)
        broker_client_ids: list[str] = [
            cid
            for order in open_orders + recent_orders
            if (cid := order.get("client_order_id")) is not None
        ]
        db_known_ids = set()
        if broker_client_ids:
            db_known_ids = self.db_client.get_order_ids_by_client_ids(broker_client_ids)

        # Reconcile broker -> DB for known orders
        for db_order in db_orders:
            broker_order = orders_by_client.get(db_order.client_order_id)
            if broker_order:
                self._apply_broker_update(db_order.client_order_id, broker_order)
                continue

        # Handle submitted_unconfirmed orders or stale non-terminals
        self._reconcile_missing_orders(db_orders, after_time)

        # Detect orphans (broker orders not in DB)
        for order in open_orders:
            client_id = order.get("client_order_id")
            if client_id and client_id in db_known_ids:
                continue
            self._handle_orphan_order(order, resolve_terminal=False)

        for order in recent_orders:
            client_id = order.get("client_order_id")
            if client_id and client_id in db_known_ids:
                continue
            self._handle_orphan_order(order, resolve_terminal=True)

        # Reconcile positions
        self._reconcile_positions()

        # Update high-water mark on success
        self.db_client.set_reconciliation_high_water_mark(start_time)
        reconciliation_last_run_timestamp.set(start_time.timestamp())
        logger.info("Reconciliation completed", extra={"mode": mode})

    def _apply_broker_update(self, client_order_id: str, broker_order: dict[str, Any]) -> None:
        status = broker_order.get("status") or ""
        status_rank = status_rank_for(status)
        updated_at = broker_order.get("updated_at") or broker_order.get("created_at")
        if updated_at is None:
            updated_at = datetime.now(UTC)
        filled_qty = broker_order.get("filled_qty") or Decimal("0")
        filled_avg_price = broker_order.get("filled_avg_price")
        filled_at = broker_order.get("filled_at") if status == "filled" else None

        updated = self.db_client.update_order_status_cas(
            client_order_id=client_order_id,
            status=status,
            broker_updated_at=updated_at,
            status_rank=status_rank,
            source_priority=SOURCE_PRIORITY_RECONCILIATION,
            filled_qty=Decimal(str(filled_qty)),
            filled_avg_price=filled_avg_price,
            filled_at=filled_at,
            broker_order_id=broker_order.get("id"),
        )

        if updated is None:
            reconciliation_conflicts_skipped_total.inc()
            logger.warning(
                "Reconciliation CAS skipped",
                extra={"client_order_id": client_order_id, "status": status},
            )
            return

        reconciliation_mismatches_total.inc()

    def _reconcile_missing_orders(self, db_orders: list[Any], after_time: datetime | None) -> None:
        lookups = 0
        now = datetime.now(UTC)
        for db_order in db_orders:
            if db_order.status != "submitted_unconfirmed" and after_time is not None:
                if db_order.created_at >= after_time:
                    continue

            if lookups >= self.max_individual_lookups:
                logger.warning(
                    "Reconciliation lookup cap reached",
                    extra={"max_lookups": self.max_individual_lookups},
                )
                break

            lookups += 1
            broker_order = self.alpaca_client.get_order_by_client_id(db_order.client_order_id)
            if broker_order:
                self._apply_broker_update(db_order.client_order_id, broker_order)
                continue

            if db_order.status == "submitted_unconfirmed":
                age_seconds = (now - db_order.created_at).total_seconds()
                if age_seconds < self.submitted_unconfirmed_grace_seconds:
                    logger.info(
                        "Submitted_unconfirmed within grace period; deferring failure",
                        extra={
                            "client_order_id": db_order.client_order_id,
                            "age_seconds": age_seconds,
                            "grace_seconds": self.submitted_unconfirmed_grace_seconds,
                        },
                    )
                    continue
                # Use the original order timestamp to avoid blocking later broker updates
                # that may arrive with earlier broker_updated_at values.
                broker_updated_at = db_order.created_at
                updated = self.db_client.update_order_status_cas(
                    client_order_id=db_order.client_order_id,
                    status="failed",
                    broker_updated_at=broker_updated_at,
                    status_rank=status_rank_for("failed"),
                    source_priority=SOURCE_PRIORITY_RECONCILIATION,
                    filled_qty=Decimal("0"),
                    filled_avg_price=None,
                    filled_at=None,
                    broker_order_id=db_order.broker_order_id,
                )
                if updated is None:
                    reconciliation_conflicts_skipped_total.inc()
                else:
                    reconciliation_mismatches_total.inc()

    def _handle_orphan_order(self, broker_order: dict[str, Any], resolve_terminal: bool) -> None:
        symbol = broker_order.get("symbol")
        if not symbol:
            return

        broker_order_id = broker_order.get("id")
        if not broker_order_id:
            return

        side = broker_order.get("side") or "unknown"
        qty = int(Decimal(str(broker_order.get("qty") or 0)))
        estimated_notional = self._estimate_notional(broker_order)

        status = broker_order.get("status") or "untracked"

        self.db_client.create_orphan_order(
            broker_order_id=str(broker_order_id),
            client_order_id=broker_order.get("client_order_id"),
            symbol=symbol,
            strategy_id=QUARANTINE_STRATEGY_SENTINEL,
            side=side,
            qty=qty,
            estimated_notional=estimated_notional,
            status=status,
        )

        resolved_at = None
        if resolve_terminal and status in TERMINAL_STATUSES:
            resolved_at = datetime.now(UTC)
        self.db_client.update_orphan_order_status(
            broker_order_id=str(broker_order_id), status=status, resolved_at=resolved_at
        )

        # Fail-closed quarantine for unknown strategy
        self._set_quarantine(symbol=symbol, strategy_id="*")

        # Update exposure cache for external sentinel
        self._sync_orphan_exposure(symbol, QUARANTINE_STRATEGY_SENTINEL)

    def _estimate_notional(self, broker_order: dict[str, Any]) -> Decimal:
        notional = broker_order.get("notional")
        if notional is not None:
            return Decimal(str(notional))

        qty = Decimal(str(broker_order.get("qty") or 0))
        limit_price = broker_order.get("limit_price")
        if limit_price is not None:
            return qty * Decimal(str(limit_price))

        filled_avg_price = broker_order.get("filled_avg_price")
        if filled_avg_price is not None:
            return qty * Decimal(str(filled_avg_price))

        # As a last resort, return 0 (quarantine will still block trading)
        return Decimal("0")

    def _set_quarantine(self, symbol: str, strategy_id: str) -> None:
        if not self.redis_client:
            return
        try:
            key = RedisKeys.quarantine(strategy_id=strategy_id, symbol=symbol)
            self.redis_client.set(key, "orphan_order_detected")
            symbols_quarantined_total.labels(symbol=symbol).inc()
        except Exception as exc:
            logger.warning(
                "Failed to set quarantine key",
                extra={"symbol": symbol, "strategy_id": strategy_id, "error": str(exc)},
            )

    def _sync_orphan_exposure(self, symbol: str, strategy_id: str) -> None:
        if not self.redis_client:
            return
        try:
            exposure = self.db_client.get_orphan_exposure(symbol, strategy_id)
            key = RedisKeys.orphan_exposure(strategy_id=strategy_id, symbol=symbol)
            self.redis_client.set(key, str(exposure))
        except Exception as exc:
            logger.warning(
                "Failed to sync orphan exposure",
                extra={"symbol": symbol, "strategy_id": strategy_id, "error": str(exc)},
            )

    def _reconcile_positions(self) -> None:
        broker_positions = self.alpaca_client.get_all_positions()
        broker_by_symbol = {pos["symbol"]: pos for pos in broker_positions}

        db_positions = self.db_client.get_all_positions()
        db_symbols = {pos.symbol for pos in db_positions}

        for symbol, broker_pos in broker_by_symbol.items():
            qty = Decimal(str(broker_pos.get("qty") or 0))
            avg_entry_price = Decimal(str(broker_pos.get("avg_entry_price") or 0))
            current_price = broker_pos.get("current_price")
            updated_at = datetime.now(UTC)

            self.db_client.upsert_position_snapshot(
                symbol=symbol,
                qty=qty,
                avg_entry_price=avg_entry_price,
                current_price=current_price,
                updated_at=updated_at,
            )

        # Positions in DB but not in broker -> set to flat
        for db_symbol in db_symbols - set(broker_by_symbol.keys()):
            self.db_client.upsert_position_snapshot(
                symbol=db_symbol,
                qty=Decimal("0"),
                avg_entry_price=Decimal("0"),
                current_price=None,
                updated_at=datetime.now(UTC),
            )
