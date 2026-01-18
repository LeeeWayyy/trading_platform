"""Reconciliation service for execution gateway."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import redis
from prometheus_client import Counter, Gauge

from apps.execution_gateway.alpaca_client import AlpacaConnectionError, AlpacaExecutor
from apps.execution_gateway.database import TERMINAL_STATUSES, DatabaseClient, status_rank_for
from libs.core.redis_client import RedisClient
from libs.core.redis_client.keys import RedisKeys

logger = logging.getLogger(__name__)

# Pod label for Prometheus metrics (fallback to hostname)
POD_LABEL = os.getenv("POD_NAME") or os.getenv("HOSTNAME") or "unknown"

# Prometheus metrics
reconciliation_mismatches_total = Counter(
    "execution_gateway_reconciliation_mismatches_total",
    "Total order mismatches corrected by reconciliation",
    ["pod"],
)

reconciliation_conflicts_skipped_total = Counter(
    "execution_gateway_reconciliation_conflicts_skipped_total",
    "Total reconciliation updates skipped due to CAS conflict",
    ["pod"],
)

reconciliation_last_run_timestamp = Gauge(
    "execution_gateway_reconciliation_last_run_timestamp",
    "Last successful reconciliation timestamp (epoch seconds)",
    ["pod"],
)

symbols_quarantined_total = Counter(
    "execution_gateway_symbols_quarantined_total",
    "Total symbols quarantined due to orphan orders",
    ["pod", "symbol"],
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
        self._last_reconciliation_result: dict[str, Any] | None = None  # Track last recon result

        self._lock = asyncio.Lock()
        self._state_lock = threading.Lock()
        self._stop_event = asyncio.Event()

        self.poll_interval_seconds = int(os.getenv("RECONCILIATION_INTERVAL_SECONDS", "300"))
        self.timeout_seconds = int(os.getenv("RECONCILIATION_TIMEOUT_SECONDS", "300"))
        self.max_individual_lookups = int(os.getenv("RECONCILIATION_MAX_LOOKUPS", "100"))
        self.overlap_seconds = int(os.getenv("RECONCILIATION_OVERLAP_SECONDS", "60"))
        self.submitted_unconfirmed_grace_seconds = int(
            os.getenv("RECONCILIATION_SUBMITTED_UNCONFIRMED_GRACE_SECONDS", "300")
        )
        self.fills_backfill_enabled = os.getenv(
            "ALPACA_FILLS_BACKFILL_ENABLED", "false"
        ).lower() in {
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

    def is_startup_complete(self) -> bool:
        if self.dry_run:
            return True
        with self._state_lock:
            return self._startup_complete

    def startup_elapsed_seconds(self) -> float:
        return (datetime.now(UTC) - self._startup_started_at).total_seconds()

    def startup_timed_out(self) -> bool:
        return self.startup_elapsed_seconds() > self.timeout_seconds

    def mark_startup_complete(
        self, forced: bool = False, user_id: str | None = None, reason: str | None = None
    ) -> None:
        """Mark startup reconciliation as complete.

        Args:
            forced: If True, allow bypassing reconciliation gate. SECURITY: Requires
                   that at least one reconciliation attempt was made to prevent
                   completely skipping safety checks.
            user_id: User requesting the override (required if forced=True).
            reason: Reason for the override (required if forced=True).

        Raises:
            ValueError: If forced=True but no reconciliation was ever attempted.
                       This prevents operators from completely skipping safety checks.
        """
        if forced:
            # SECURITY: Require at least one reconciliation attempt before allowing forced bypass
            # This prevents completely skipping safety checks while still allowing emergency
            # scenarios where reconciliation fails but operator needs to proceed
            with self._state_lock:
                last_result = self._last_reconciliation_result
            if last_result is None:
                raise ValueError(
                    "Cannot force startup complete without running reconciliation first. "
                    "Run reconciliation at least once before using forced bypass. "
                    "This ensures broker state was checked even if reconciliation failed."
                )
            if not user_id or not reason:
                raise ValueError("Both user_id and reason are required for forced startup bypass")
            with self._state_lock:
                self._override_active = True
                self._override_context = {
                    "user_id": user_id,
                    "reason": reason,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "last_reconciliation_result": last_result,
                }
            logger.warning(
                "Startup reconciliation gate FORCED BYPASSED",
                extra={
                    "user_id": user_id,
                    "reason": reason,
                    "last_reconciliation_result": last_result,
                },
            )
        with self._state_lock:
            self._startup_complete = True

    def override_active(self) -> bool:
        with self._state_lock:
            return self._override_active

    def override_context(self) -> dict[str, Any]:
        with self._state_lock:
            return dict(self._override_context)

    async def _record_last_reconciliation_result(self, result: dict[str, Any]) -> None:
        """Record last reconciliation result under lock for thread safety."""
        with self._state_lock:
            self._last_reconciliation_result = result

    async def run_startup_reconciliation(self) -> bool:
        if self.dry_run:
            with self._state_lock:
                self._startup_complete = True
            return True

        try:
            await self.run_reconciliation_once("startup")
            return True
        except AlpacaConnectionError as exc:
            # Store failed result to enable forced bypass after failure
            # (SECURITY: operator can still force after seeing the error)
            await self._record_last_reconciliation_result(
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
            # Store failed result to enable forced bypass after failure
            # (SECURITY: operator can still force after seeing the error)
            await self._record_last_reconciliation_result(
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
            # Store failed result to enable forced bypass after failure
            # (SECURITY: operator can still force after seeing the error)
            await self._record_last_reconciliation_result(
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
        while not self._stop_event.is_set():
            try:
                await self.run_reconciliation_once("periodic")
                with self._state_lock:
                    if not self._startup_complete:
                        self._startup_complete = True
                        logger.info(
                            "Startup reconciliation gate opened after successful periodic run"
                        )
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

    def stop(self) -> None:
        self._stop_event.set()

    async def run_reconciliation_once(self, mode: str) -> None:
        if self.dry_run:
            return

        async with self._lock:
            result = await asyncio.to_thread(self._run_reconciliation, mode)
            await self._record_last_reconciliation_result(result)
            with self._state_lock:
                if not self._startup_complete:
                    self._startup_complete = True
                    logger.info(
                        "Startup reconciliation gate opened after successful run",
                        extra={"mode": mode},
                    )

    async def run_fills_backfill_once(
        self,
        *,
        lookback_hours: int | None = None,
        recalc_all_trades: bool = False,
    ) -> dict[str, Any]:
        if self.dry_run:
            return {"status": "skipped", "message": "DRY_RUN mode - reconciliation disabled"}
        async with self._lock:
            return await asyncio.to_thread(
                self._backfill_alpaca_fills,
                lookback_hours=lookback_hours,
                recalc_all_trades=recalc_all_trades,
            )

    def _run_reconciliation(self, mode: str) -> dict[str, Any]:
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

        # Backfill fill metadata for terminal orders not in db_orders (terminal in DB)
        for client_id, broker_order in orders_by_client.items():
            if client_id not in db_known_ids:
                continue
            status = str(broker_order.get("status") or "").lower()
            if status in ("filled", "partially_filled"):
                self._backfill_fill_metadata(client_id, broker_order)

        # Backfill fills using Alpaca account activities (granular fills API)
        try:
            self._backfill_alpaca_fills()
        except Exception as exc:
            logger.warning(
                "Reconciliation Alpaca fills backfill failed",
                extra={"error": str(exc)},
            )

        # Reconcile positions
        self._reconcile_positions()

        # Backfill fills for terminal orders missing metadata (local dev without webhooks)
        try:
            missing_fill_orders = self.db_client.get_filled_orders_missing_fills(limit=200)
            for db_order in missing_fill_orders:
                self._backfill_fill_metadata_from_order(db_order)
        except Exception as exc:
            logger.warning(
                "Reconciliation backfill scan failed",
                extra={"error": str(exc)},
            )

        # Update high-water mark on success
        self.db_client.set_reconciliation_high_water_mark(start_time)
        reconciliation_last_run_timestamp.labels(pod=POD_LABEL).set(start_time.timestamp())

        # Store result for forced bypass validation (SECURITY: enables safe emergency overrides)
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
        if not self.fills_backfill_enabled and lookback_hours is None:
            return {"status": "disabled"}

        now = datetime.now(UTC)
        last_check = self.db_client.get_reconciliation_high_water_mark("alpaca_fills")
        if lookback_hours is not None:
            after_time = now - timedelta(hours=lookback_hours)
        elif last_check:
            after_time = last_check - timedelta(seconds=self.overlap_seconds)
        else:
            after_time = now - timedelta(hours=self.fills_backfill_initial_lookback_hours)

        page_token: str | None = None
        last_activity_id: str | None = None
        pages = 0
        fills: list[dict[str, Any]] = []

        while pages < self.fills_backfill_max_pages:
            page_size = self.fills_backfill_page_size + (1 if page_token else 0)
            raw_page = self.alpaca_client.get_account_activities(
                "FILL",
                after=after_time,
                until=now,
                page_size=page_size,
                page_token=page_token,
                direction="desc",
            )
            if not raw_page:
                break
            page = raw_page
            if last_activity_id:
                page = [item for item in raw_page if str(item.get("id")) != last_activity_id]
            fills.extend(page)
            if len(raw_page) < page_size:
                break
            last_id = raw_page[-1].get("id")
            if not last_id:
                break
            last_activity_id = str(last_id)
            page_token = last_activity_id
            pages += 1

        if not fills:
            self.db_client.set_reconciliation_high_water_mark(now, name="alpaca_fills")
            return {"status": "ok", "fills_seen": 0, "fills_inserted": 0, "unmatched": 0}

        broker_ids = [str(fill.get("order_id")) for fill in fills if fill.get("order_id")]
        orders_by_broker = self.db_client.get_orders_by_broker_ids(broker_ids)

        fills_by_client: dict[str, list[dict[str, Any]]] = {}
        affected: set[tuple[str, str]] = set()
        unmatched = 0

        for fill in fills:
            broker_order_id = fill.get("order_id")
            if not broker_order_id:
                unmatched += 1
                continue
            order = orders_by_broker.get(str(broker_order_id))
            if not order:
                unmatched += 1
                continue

            fill_id = str(fill.get("id") or "").strip()
            if not fill_id:
                # Fallback: deterministic id using multiple fields to avoid collisions
                fallback_parts = {
                    "broker_order_id": str(broker_order_id),
                    "symbol": str(fill.get("symbol") or ""),
                    "side": str(fill.get("side") or ""),
                    "qty": str(fill.get("qty") or ""),
                    "price": str(fill.get("price") or ""),
                    "transaction_time": str(fill.get("transaction_time") or ""),
                    "activity_time": str(fill.get("activity_time") or ""),
                    "id_hint": str(fill.get("id") or ""),
                }
                fallback_payload = "|".join(
                    f"{key}={value}" for key, value in sorted(fallback_parts.items())
                )
                fill_id = hashlib.sha256(fallback_payload.encode()).hexdigest()[:32]

            qty = fill.get("qty")
            price = fill.get("price")
            timestamp = fill.get("transaction_time") or fill.get("activity_time")

            fill_data = {
                "fill_id": fill_id,
                "fill_qty": qty,
                "fill_price": price,
                "realized_pl": "0",
                "timestamp": timestamp,
                "synthetic": False,
                "source": "alpaca_activity",
            }
            fills_by_client.setdefault(order.client_order_id, []).append(fill_data)
            affected.add((order.strategy_id, order.symbol))

        inserted = 0
        pnl_updates = 0
        pnl_failures = 0
        with self.db_client.transaction() as conn:
            for client_order_id, fill_items in fills_by_client.items():
                for fill_data in fill_items:
                    updated = self.db_client.append_fill_to_order_metadata(
                        client_order_id=client_order_id,
                        fill_data=fill_data,
                        conn=conn,
                    )
                    if updated is not None:
                        inserted += 1

            for strategy_id, symbol in affected:
                try:
                    result = self.db_client.recalculate_trade_realized_pnl(
                        strategy_id,
                        symbol,
                        update_all=recalc_all_trades,
                        conn=conn,
                    )
                    pnl_updates += int(result.get("trades_updated", 0))
                except Exception as exc:
                    pnl_failures += 1
                    logger.error(
                        "Alpaca fills backfill P&L recalculation failed - rolling back",
                        extra={
                            "strategy_id": strategy_id,
                            "symbol": symbol,
                            "error": str(exc),
                        },
                        exc_info=True,
                    )
                    raise RuntimeError(
                        f"P&L recalculation failed for {strategy_id}:{symbol}"
                    ) from exc

        self.db_client.set_reconciliation_high_water_mark(now, name="alpaca_fills")
        logger.info(
            "Alpaca fills backfill completed",
            extra={
                "fills_seen": len(fills),
                "fills_inserted": inserted,
                "unmatched": unmatched,
                "pnl_updates": pnl_updates,
                "pnl_failures": pnl_failures,
                "after": after_time.isoformat(),
                "until": now.isoformat(),
            },
        )
        return {
            "status": "ok",
            "fills_seen": len(fills),
            "fills_inserted": inserted,
            "unmatched": unmatched,
            "pnl_updates": pnl_updates,
            "pnl_failures": pnl_failures,
        }

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
            reconciliation_conflicts_skipped_total.labels(pod=POD_LABEL).inc()
            logger.warning(
                "Reconciliation CAS skipped",
                extra={"client_order_id": client_order_id, "status": status},
            )

        # Backfill fill metadata when webhooks aren't available (e.g., local dev).
        # This powers Recent Activity and P&L history based on fills.
        if status in ("filled", "partially_filled"):
            self._backfill_fill_metadata(client_order_id, broker_order, updated_at, updated)

        if updated is not None:
            reconciliation_mismatches_total.labels(pod=POD_LABEL).inc()

    def _calculate_synthetic_fill(
        self,
        client_order_id: str,
        filled_qty: Decimal,
        filled_avg_price: Decimal,
        timestamp: datetime,
        existing_fills: list[dict[str, Any]],
        source: str,
    ) -> dict[str, Any] | None:
        """Calculate synthetic fill data if there's a quantity gap.

        Common logic extracted from _backfill_fill_metadata and
        _backfill_fill_metadata_from_order to reduce duplication.

        Returns fill_data dict if a synthetic fill is needed, None otherwise.
        """
        # Count real (non-synthetic) fills separately to avoid double-counting
        real_fill_qty = Decimal("0")
        synthetic_fill_qty = Decimal("0")
        for fill in existing_fills:
            try:
                # Skip superseded fills - they were replaced by real fills
                if fill.get("superseded"):
                    continue
                qty = Decimal(str(fill.get("fill_qty", 0)))
                if fill.get("synthetic"):
                    synthetic_fill_qty += qty
                else:
                    real_fill_qty += qty
            except (TypeError, ValueError):
                continue

        filled_qty_dec = Decimal(str(filled_qty))

        # If real fills cover the broker's filled_qty, no need for synthetic
        if filled_qty_dec <= real_fill_qty:
            return None

        # Calculate missing based on real fills only (synthetic may be stale/duplicated)
        missing_qty = filled_qty_dec - real_fill_qty - synthetic_fill_qty

        # If total (real + synthetic) already covers broker qty, skip
        if missing_qty <= Decimal("0"):
            return None

        # For fractional shares, store as string to preserve precision
        qty_value = str(missing_qty) if missing_qty % 1 != 0 else int(missing_qty)

        # Use both filled_qty and missing_qty in fill_id to ensure uniqueness
        fill_id_filled = str(filled_qty_dec).replace(".", "_")
        fill_id_missing = str(missing_qty).replace(".", "_")

        return {
            "fill_id": f"{client_order_id}_{source}_{fill_id_filled}_{fill_id_missing}",
            "fill_qty": qty_value,
            "fill_price": str(filled_avg_price),
            "realized_pl": "0",  # Synthetic: actual P&L unknown
            "timestamp": timestamp.isoformat(),
            "synthetic": True,  # AUDIT: Mark as reconciliation-generated
            "source": source,
            "_missing_qty": missing_qty,  # For logging, stripped before storage
        }

    def _backfill_fill_metadata(
        self,
        client_order_id: str,
        broker_order: dict[str, Any],
        updated_at: datetime | None = None,
        cached_order: Any | None = None,
    ) -> None:
        """Backfill fill metadata when webhooks are unavailable (local dev)."""
        try:
            if updated_at is None:
                updated_at = broker_order.get("updated_at") or broker_order.get("created_at")
            if updated_at is None:
                updated_at = datetime.now(UTC)

            filled_qty = broker_order.get("filled_qty") or Decimal("0")
            filled_avg_price = broker_order.get("filled_avg_price")
            if filled_avg_price is None:
                return

            with self.db_client.transaction() as conn:
                order = cached_order or self.db_client.get_order_for_update(client_order_id, conn)
                if not order:
                    return

                existing_fills = order.metadata.get("fills", [])
                fill_data = self._calculate_synthetic_fill(
                    client_order_id=client_order_id,
                    filled_qty=Decimal(str(filled_qty)),
                    filled_avg_price=Decimal(str(filled_avg_price)),
                    timestamp=updated_at,
                    existing_fills=existing_fills,
                    source="recon",
                )
                if fill_data is None:
                    return

                # Extract and remove internal field before storage
                missing_qty = fill_data.pop("_missing_qty")
                # Override source for storage (fill_id uses short prefix, source uses full name)
                fill_data["source"] = "reconciliation_backfill"

                self.db_client.append_fill_to_order_metadata(
                    client_order_id=client_order_id,
                    fill_data=fill_data,
                    conn=conn,
                )
                logger.info(
                    "reconciliation_backfill_fill",
                    extra={
                        "client_order_id": client_order_id,
                        "symbol": getattr(order, "symbol", None),
                        "strategy_id": getattr(order, "strategy_id", None),
                        "fill_qty": str(missing_qty),
                        "fill_price": str(filled_avg_price),
                        "action": "synthetic_fill_injected",
                    },
                )
        except Exception as exc:
            logger.warning(
                "Reconciliation fill backfill failed",
                extra={"client_order_id": client_order_id, "error": str(exc)},
            )

    def _backfill_fill_metadata_from_order(self, order: Any) -> None:
        """Backfill fill metadata using DB order data only."""
        try:
            filled_avg_price = getattr(order, "filled_avg_price", None)
            filled_qty = getattr(order, "filled_qty", None)
            if filled_avg_price is None or filled_qty is None:
                return

            timestamp = getattr(order, "filled_at", None) or getattr(order, "updated_at", None)
            if timestamp is None:
                timestamp = datetime.now(UTC)

            with self.db_client.transaction() as conn:
                locked = self.db_client.get_order_for_update(order.client_order_id, conn)
                if not locked:
                    return

                existing_fills = locked.metadata.get("fills", [])
                fill_data = self._calculate_synthetic_fill(
                    client_order_id=order.client_order_id,
                    filled_qty=Decimal(str(filled_qty)),
                    filled_avg_price=Decimal(str(filled_avg_price)),
                    timestamp=timestamp,
                    existing_fills=existing_fills,
                    source="recon_db",
                )
                if fill_data is None:
                    return

                # Extract and remove internal field before storage
                missing_qty = fill_data.pop("_missing_qty")
                # Override source for storage (fill_id uses short prefix, source uses full name)
                fill_data["source"] = "reconciliation_db_backfill"

                self.db_client.append_fill_to_order_metadata(
                    client_order_id=order.client_order_id,
                    fill_data=fill_data,
                    conn=conn,
                )
                logger.info(
                    "reconciliation_db_backfill_fill",
                    extra={
                        "client_order_id": order.client_order_id,
                        "symbol": getattr(order, "symbol", None),
                        "strategy_id": getattr(order, "strategy_id", None),
                        "fill_qty": str(missing_qty),
                        "fill_price": str(filled_avg_price),
                        "action": "synthetic_fill_injected",
                    },
                )
        except Exception as exc:
            logger.warning(
                "Reconciliation DB fill backfill failed",
                extra={
                    "client_order_id": getattr(order, "client_order_id", "?"),
                    "error": str(exc),
                },
            )

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
                    reconciliation_conflicts_skipped_total.labels(pod=POD_LABEL).inc()
                else:
                    reconciliation_mismatches_total.labels(pod=POD_LABEL).inc()

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
            symbols_quarantined_total.labels(pod=POD_LABEL, symbol=symbol).inc()
        except redis.RedisError as exc:
            logger.warning(
                "Failed to set quarantine key: Redis error",
                extra={
                    "symbol": symbol,
                    "strategy_id": strategy_id,
                    "error": str(exc),
                    "error_type": "redis",
                },
            )
        except ValueError as exc:
            logger.warning(
                "Failed to set quarantine key: Validation error",
                extra={
                    "symbol": symbol,
                    "strategy_id": strategy_id,
                    "error": str(exc),
                    "error_type": "validation",
                },
            )

    def _sync_orphan_exposure(self, symbol: str, strategy_id: str) -> None:
        if not self.redis_client:
            return
        try:
            exposure = self.db_client.get_orphan_exposure(symbol, strategy_id)
            key = RedisKeys.orphan_exposure(strategy_id=strategy_id, symbol=symbol)
            self.redis_client.set(key, str(exposure))
        except (psycopg.OperationalError, psycopg.IntegrityError) as exc:
            logger.warning(
                "Failed to sync orphan exposure: Database error",
                extra={
                    "symbol": symbol,
                    "strategy_id": strategy_id,
                    "error": str(exc),
                    "error_type": "database",
                },
            )
        except redis.RedisError as exc:
            logger.warning(
                "Failed to sync orphan exposure: Redis error",
                extra={
                    "symbol": symbol,
                    "strategy_id": strategy_id,
                    "error": str(exc),
                    "error_type": "redis",
                },
            )
        except ValueError as exc:
            logger.warning(
                "Failed to sync orphan exposure: Validation error",
                extra={
                    "symbol": symbol,
                    "strategy_id": strategy_id,
                    "error": str(exc),
                    "error_type": "validation",
                },
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
