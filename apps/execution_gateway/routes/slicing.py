"""
TWAP Slicing Routes Module

Provides endpoints for TWAP (Time-Weighted Average Price) order slicing:
- POST /api/v1/orders/slice - Create sliced order with scheduled execution
- GET /api/v1/orders/{parent_id}/slices - Retrieve child slices
- DELETE /api/v1/orders/{parent_id}/slices - Cancel pending slices

Safety Gates (per spec):
- Auth → Scheduler Unavail → KS Unavail → Kill-Switch → Quarantine → Recon Gate → Liquidity → Slice
- Note: NO circuit-breaker gates for TWAP/slice (current behavior)

Created: 2025-01-16
Phase: P6T2 Refactor - Route Extraction
"""

import asyncio
import logging
import os
from datetime import UTC, date, datetime
from typing import Any

import psycopg
import redis.exceptions
from fastapi import APIRouter, Depends, HTTPException, Response, status
from psycopg.errors import UniqueViolation
from redis.exceptions import RedisError

from apps.execution_gateway.database import DatabaseClient
from apps.execution_gateway.liquidity_service import LiquidityService
from apps.execution_gateway.order_id_generator import (
    generate_client_order_id,
    reconstruct_order_params_hash,
)
from apps.execution_gateway.order_slicer import TWAPSlicer
from apps.execution_gateway.reconciliation import ReconciliationService
from apps.execution_gateway.recovery_manager import RecoveryManager
from apps.execution_gateway.schemas import (
    OrderDetail,
    OrderRequest,
    SliceDetail,
    SlicingPlan,
    SlicingRequest,
)
from apps.execution_gateway.slice_scheduler import SliceScheduler
from libs.core.common.api_auth_dependency import AuthContext, api_auth
from libs.core.common.rate_limit_dependency import RateLimitConfig, rate_limit
from libs.core.redis_client import RedisClient, RedisKeys

logger = logging.getLogger(__name__)

# Constants (from main.py config)
LEGACY_TWAP_INTERVAL_SECONDS = 60
STRATEGY_ID = os.getenv("STRATEGY_ID", "alpha_baseline")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"


def _get_float_env(key: str, default: float) -> float:
    """Get float environment variable with default."""
    try:
        return float(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        logger.warning(f"Invalid float for {key}, using default: {default}")
        return default


LIQUIDITY_CHECK_ENABLED = os.getenv("LIQUIDITY_CHECK_ENABLED", "true").lower() in (
    "true",
    "1",
    "yes",
)
MAX_SLICE_PCT_OF_ADV = _get_float_env("MAX_SLICE_PCT_OF_ADV", 0.01)


def create_slicing_router(
    db_client: DatabaseClient,
    twap_slicer: TWAPSlicer,
    recovery_manager: RecoveryManager,
    liquidity_service: LiquidityService | None,
    reconciliation_service: ReconciliationService | None,
    redis_client: RedisClient | None,
    order_slice_auth: Any,
    order_slice_rl: Any,
    order_read_auth: Any,
    order_cancel_auth: Any,
) -> APIRouter:
    """
    Factory function to create slicing router with injected dependencies.

    Args:
        db_client: Database client for order persistence
        twap_slicer: TWAP slicing logic
        recovery_manager: Recovery manager with slice scheduler and kill-switch
        liquidity_service: ADV-based liquidity constraints (optional)
        reconciliation_service: Reconciliation service for gating (optional)
        redis_client: Redis client for quarantine checks (optional)
        order_slice_auth: Auth dependency for slice creation
        order_slice_rl: Rate limit dependency for slice creation
        order_read_auth: Auth dependency for read operations
        order_cancel_auth: Auth dependency for cancel operations

    Returns:
        Configured APIRouter with slicing endpoints
    """
    router = APIRouter()

    # Helper functions (closures with access to factory parameters)

    def _is_reconciliation_ready() -> bool:
        """Return True when startup reconciliation gate is open."""
        if DRY_RUN:
            return True
        if reconciliation_service is None:
            return False
        return reconciliation_service.is_startup_complete()

    async def _check_quarantine(symbol: str, strategy_id: str) -> None:
        """Block trading when symbol is quarantined."""
        if DRY_RUN:
            return
        if not redis_client:
            logger.error(
                "Redis unavailable for quarantine check; failing closed",
                extra={"symbol": symbol},
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "Quarantine check unavailable",
                    "message": "Redis unavailable for quarantine enforcement (fail-closed).",
                },
            )

        try:
            symbol = symbol.upper()
            strategy_key = RedisKeys.quarantine(strategy_id=strategy_id, symbol=symbol)
            wildcard_key = RedisKeys.quarantine(strategy_id="*", symbol=symbol)
            values = await asyncio.to_thread(redis_client.mget, [strategy_key, wildcard_key])
            strategy_value, wildcard_value = (
                (values + [None, None])[:2] if values else (None, None)
            )
            if strategy_value or wildcard_value:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "error": "Symbol quarantined",
                        "message": f"Trading blocked for {symbol} due to orphan order quarantine",
                        "symbol": symbol,
                    },
                )
        except HTTPException:
            raise
        except RedisError as exc:
            logger.error(
                "Quarantine check failed - Redis error",
                extra={"symbol": symbol, "error": str(exc), "error_type": type(exc).__name__},
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "Quarantine check unavailable",
                    "message": "Redis unavailable for quarantine enforcement (fail-closed).",
                },
            ) from exc
        except redis.exceptions.ConnectionError as exc:
            logger.error(
                "Quarantine check failed - Redis connection error",
                extra={"symbol": symbol, "error": str(exc)},
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "Quarantine check unavailable",
                    "message": "Redis connection unavailable for quarantine enforcement (fail-closed).",
                },
            ) from exc
        except (TypeError, KeyError, AttributeError) as exc:
            logger.error(
                "Quarantine check failed - data access error",
                extra={"symbol": symbol, "error": str(exc), "error_type": type(exc).__name__},
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "Quarantine check unavailable",
                    "message": "Data structure error during quarantine check (fail-closed).",
                },
            ) from exc

    def _check_twap_prerequisites() -> None:
        """
        Check prerequisites for TWAP order submission.

        Validates that slice scheduler is available and kill-switch allows trading.
        Follows fail-closed principle for kill-switch unavailability.

        Raises:
            HTTPException 503: If scheduler unavailable or kill-switch state unknown
            HTTPException 503: If kill-switch is engaged
        """
        slice_scheduler = recovery_manager.slice_scheduler
        kill_switch = recovery_manager.kill_switch

        # Check if slice scheduler is available
        if not slice_scheduler:
            logger.error("Slice scheduler unavailable - cannot accept TWAP orders")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="TWAP order service unavailable (scheduler not initialized)",
            )

        # Check kill-switch availability (fail closed)
        if recovery_manager.is_kill_switch_unavailable() or kill_switch is None:
            logger.error("Kill-switch unavailable - cannot accept TWAP orders (fail closed)")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="TWAP order service unavailable (kill-switch state unknown)",
            )

        # Check kill-switch status
        if kill_switch.is_engaged():
            status_info = kill_switch.get_status()
            logger.error("TWAP order blocked by kill-switch")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "Kill-switch engaged",
                    "message": "All trading halted by operator",
                    "engaged_by": status_info.get("engaged_by"),
                    "reason": status_info.get("engagement_reason"),
                },
            )

    def _convert_slices_to_details(
        slices: list[OrderDetail], parent_order_id: str
    ) -> list[SliceDetail]:
        """
        Convert OrderDetail list to SliceDetail list for response.

        Validates that each slice has required slice_num and scheduled_time fields.
        Raises HTTPException if data corruption detected.

        Args:
            slices: List of OrderDetail from database
            parent_order_id: Parent order ID for error logging

        Returns:
            List of SliceDetail for API response

        Raises:
            HTTPException 500: If slice data is corrupt (missing slice_num or scheduled_time)
        """
        slice_details = []
        for s in slices:
            # Child slices must have slice_num and scheduled_time
            # If these are None, it indicates data corruption
            if s.slice_num is None or s.scheduled_time is None:
                logger.error(
                    f"Corrupt slice data for parent {parent_order_id}: "
                    f"slice_num or scheduled_time is None for client_order_id={s.client_order_id}",
                    extra={
                        "parent_order_id": parent_order_id,
                        "client_order_id": s.client_order_id,
                        "slice_num": s.slice_num,
                        "scheduled_time": s.scheduled_time,
                    },
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Corrupt slice data found in database",
                ) from None

            slice_details.append(
                SliceDetail(
                    slice_num=s.slice_num,
                    qty=s.qty,
                    scheduled_time=s.scheduled_time,
                    client_order_id=s.client_order_id,
                    strategy_id=s.strategy_id,
                    status=s.status,
                )
            )
        return slice_details

    def _find_existing_twap_plan(
        request: SlicingRequest, slicing_plan: SlicingPlan, trade_date: date
    ) -> SlicingPlan | None:
        """
        Check for existing TWAP order (idempotency + backward compatibility).

        First checks new hash format (with duration), then legacy hash (without duration)
        for backward compatibility with pre-fix orders. If legacy order found, validates
        duration matches before returning to prevent hash collisions.

        Args:
            request: TWAP order request
            slicing_plan: Generated slicing plan with parent_order_id
            trade_date: Consistent trade date for idempotency

        Returns:
            SlicingPlan if existing order found, None if new order needed

        Raises:
            HTTPException 500: If corrupt slice data found
        """
        # Check if parent order already exists (idempotency)
        # First check new hash (with duration), then legacy hash (backward compatibility)
        existing_parent = db_client.get_order_by_client_id(slicing_plan.parent_order_id)

        if existing_parent:
            # Use the strategy_id from the DB to ensure consistency
            slicing_plan.parent_strategy_id = existing_parent.strategy_id
        else:
            # Legacy TWAP plans implicitly used 60-second spacing. If the caller is requesting
            # a different interval we must skip fallback checks to avoid returning an order
            # with mismatched pacing metadata.
            if request.interval_seconds != LEGACY_TWAP_INTERVAL_SECONDS:
                logger.debug(
                    "Skipping legacy TWAP hash fallback for non-default interval",
                    extra={
                        "requested_interval_seconds": request.interval_seconds,
                        "legacy_interval_seconds": LEGACY_TWAP_INTERVAL_SECONDS,
                    },
                )
                return None

            # Backward compatibility: check prior hash formats (without interval and/or duration)
            # CRITICAL: Use same trade_date for idempotency across midnight
            requested_total_slices = slicing_plan.total_slices
            fallback_strategies = [
                (
                    f"twap_parent_{request.duration_minutes}m",
                    "duration-based legacy hash",
                ),
                (
                    "twap_parent",
                    "pre-duration legacy hash",
                ),
            ]

            for strategy_id, label in fallback_strategies:
                legacy_parent_id = reconstruct_order_params_hash(
                    symbol=request.symbol,
                    side=request.side,
                    qty=request.qty,
                    limit_price=request.limit_price,
                    stop_price=request.stop_price,
                    order_type=request.order_type,
                    time_in_force=request.time_in_force,
                    strategy_id=strategy_id,
                    order_date=trade_date,
                )
                legacy_parent = db_client.get_order_by_client_id(legacy_parent_id)

                if not legacy_parent:
                    continue

                if legacy_parent.total_slices == requested_total_slices:
                    logger.info(
                        "Found %s TWAP order: legacy_id=%s",
                        label,
                        legacy_parent_id,
                        extra={
                            "legacy_parent_id": legacy_parent_id,
                            "new_parent_id": slicing_plan.parent_order_id,
                            "status": legacy_parent.status,
                            "total_slices": legacy_parent.total_slices,
                        },
                    )
                    slicing_plan.parent_order_id = legacy_parent_id
                    slicing_plan.parent_strategy_id = legacy_parent.strategy_id
                    existing_parent = legacy_parent
                    break

                logger.info(
                    "Legacy TWAP order found but slice count differs: legacy_total_slices=%s, "
                    "requested_total_slices=%s. Creating new order with new hash.",
                    legacy_parent.total_slices,
                    requested_total_slices,
                    extra={
                        "legacy_parent_id": legacy_parent_id,
                        "new_parent_id": slicing_plan.parent_order_id,
                        "legacy_total_slices": legacy_parent.total_slices,
                        "requested_total_slices": requested_total_slices,
                    },
                )

        if not existing_parent:
            return None

        # Existing order found - return it (idempotent response)
        logger.info(
            f"TWAP order already exists (idempotent): parent={slicing_plan.parent_order_id}",
            extra={
                "parent_order_id": slicing_plan.parent_order_id,
                "status": existing_parent.status,
                "total_slices": existing_parent.total_slices,
            },
        )

        # Fetch all child slices to return complete plan
        existing_slices = db_client.get_slices_by_parent_id(slicing_plan.parent_order_id)
        slice_details = _convert_slices_to_details(existing_slices, slicing_plan.parent_order_id)

        # Return existing slicing plan (idempotent response)
        return SlicingPlan(
            parent_order_id=slicing_plan.parent_order_id,
            parent_strategy_id=slicing_plan.parent_strategy_id,
            symbol=request.symbol,
            side=request.side,
            total_qty=request.qty,
            total_slices=len(slice_details),
            duration_minutes=request.duration_minutes,
            interval_seconds=slicing_plan.interval_seconds,
            slices=slice_details,
        )

    def _create_twap_in_db(
        request: SlicingRequest,
        slicing_plan: SlicingPlan,
        parent_metadata: dict[str, Any] | None,
    ) -> SlicingPlan | None:
        """
        Create parent + child orders atomically in database.

        Uses database transaction for all-or-nothing behavior. Handles race condition
        where concurrent identical requests both pass idempotency check: catches
        UniqueViolation and returns existing plan instead of 500 error.

        Args:
            request: TWAP order request
            slicing_plan: Generated slicing plan
            parent_metadata: Optional metadata to persist with the parent order

        Returns:
            SlicingPlan if concurrent submission detected, None if created successfully

        Raises:
            HTTPException 500: If database inconsistency after UniqueViolation
        """
        # 🔒 CRITICAL: Create parent + child orders atomically (defense against partial writes)
        # Use database transaction to ensure all-or-nothing behavior. If any insert fails,
        # the entire TWAP order creation rolls back to prevent orphaned parent orders.
        #
        # 🔒 RACE CONDITION DEFENSE: Handle concurrent submissions with identical client_order_ids.
        # Two simultaneous requests can both pass the pre-transaction idempotency check and attempt
        # to insert. The second insert will fail with UniqueViolation. We catch this and return
        # the existing plan to make concurrent submissions deterministic and idempotent.
        try:
            with db_client.transaction() as conn:
                # Create parent order in database
                parent_order_request = OrderRequest(
                    symbol=request.symbol,
                    side=request.side,
                    qty=request.qty,
                    order_type=request.order_type,
                    limit_price=request.limit_price,
                    stop_price=request.stop_price,
                    time_in_force=request.time_in_force,
                )
                db_client.create_parent_order(
                    client_order_id=slicing_plan.parent_order_id,
                    strategy_id=slicing_plan.parent_strategy_id,  # Use strategy_id from plan
                    order_request=parent_order_request,
                    total_slices=slicing_plan.total_slices,
                    metadata=parent_metadata,
                    conn=conn,  # Use shared transaction connection
                )

                # Create child slice orders in database
                for slice_detail in slicing_plan.slices:
                    slice_order_request = OrderRequest(
                        symbol=request.symbol,
                        side=request.side,
                        qty=slice_detail.qty,
                        order_type=request.order_type,
                        limit_price=request.limit_price,
                        stop_price=request.stop_price,
                        time_in_force=request.time_in_force,
                    )
                    db_client.create_child_slice(
                        client_order_id=slice_detail.client_order_id,
                        parent_order_id=slicing_plan.parent_order_id,
                        slice_num=slice_detail.slice_num,
                        strategy_id=slice_detail.strategy_id,  # Use strategy_id from slice details
                        order_request=slice_order_request,
                        scheduled_time=slice_detail.scheduled_time,
                        conn=conn,  # Use shared transaction connection
                    )
                # Transaction auto-commits on successful context exit
        except UniqueViolation:
            # Concurrent submission detected: Another request created this parent_order_id
            # between our idempotency check and transaction commit. Fetch and return the
            # existing plan to provide deterministic, idempotent response without 500 error.
            logger.info(
                "Concurrent TWAP submission detected (UniqueViolation): "
                f"parent={slicing_plan.parent_order_id}. Returning existing plan.",
                extra={
                    "parent_order_id": slicing_plan.parent_order_id,
                    "symbol": request.symbol,
                    "side": request.side,
                    "qty": request.qty,
                },
            )

            # Fetch existing parent and slices
            existing_parent = db_client.get_order_by_client_id(slicing_plan.parent_order_id)
            if not existing_parent:
                # Should never happen: UniqueViolation means the parent exists
                logger.error(
                    "UniqueViolation raised but parent order not found: "
                    f"{slicing_plan.parent_order_id}",
                    extra={"parent_order_id": slicing_plan.parent_order_id},
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Database inconsistency: parent order not found after UniqueViolation",
                ) from None

            # Use the strategy_id from the DB to ensure consistency
            slicing_plan.parent_strategy_id = existing_parent.strategy_id

            existing_slices = db_client.get_slices_by_parent_id(slicing_plan.parent_order_id)
            slice_details = _convert_slices_to_details(
                existing_slices, slicing_plan.parent_order_id
            )

            # Return existing plan (idempotent response for concurrent submission)
            return SlicingPlan(
                parent_order_id=slicing_plan.parent_order_id,
                parent_strategy_id=slicing_plan.parent_strategy_id,
                symbol=request.symbol,
                side=request.side,
                total_qty=request.qty,
                total_slices=len(slice_details),
                duration_minutes=request.duration_minutes,
                interval_seconds=slicing_plan.interval_seconds,
                slices=slice_details,
            )

        # Successfully created in database
        return None

    def _schedule_slices_with_compensation(
        request: SlicingRequest, slicing_plan: SlicingPlan
    ) -> list[str]:
        """
        Schedule slices for execution with failure compensation.

        Schedules all slices using APScheduler. If scheduling fails after database
        commit, compensates by canceling pending slices. Uses defense-in-depth:
        only cancels slices still in 'pending_new' status to avoid race conditions.

        Args:
            request: TWAP order request
            slicing_plan: Slicing plan with parent and child orders

        Returns:
            List of APScheduler job IDs

        Raises:
            Exception: Re-raises scheduling errors after compensation attempt
        """
        slice_scheduler = recovery_manager.slice_scheduler
        assert slice_scheduler is not None, "Slice scheduler must be initialized"

        # Schedule slices for execution
        # Note: Scheduling happens AFTER transaction commit, so we must compensate if it fails
        try:
            job_ids = slice_scheduler.schedule_slices(
                parent_order_id=slicing_plan.parent_order_id,
                slices=slicing_plan.slices,
                symbol=request.symbol,
                side=request.side,
                order_type=request.order_type,
                limit_price=request.limit_price,
                stop_price=request.stop_price,
                time_in_force=request.time_in_force,
            )
            return job_ids
        except (AttributeError, TypeError, ValueError) as e:
            # Scheduling failed after DB commit - compensate by canceling created orders
            logger.error(
                "Scheduling failed for parent=%s - data error, compensating by canceling pending orders",
                slicing_plan.parent_order_id,
                extra={
                    "parent_order_id": slicing_plan.parent_order_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
            # Cancel only orders still in 'pending_new' status to avoid race conditions
            # (First slice scheduled for "now" may have already executed and submitted to broker)
            try:
                # Cancel all child slices still in pending_new status
                canceled_count = db_client.cancel_pending_slices(slicing_plan.parent_order_id)

                # Check if any child slices have already progressed past pending_new
                all_slices = db_client.get_slices_by_parent_id(slicing_plan.parent_order_id)
                progressed_slices = [
                    s
                    for s in all_slices
                    if s.status != "pending_new" and s.status != "canceled"
                ]

                if not progressed_slices:
                    # All slices still pending or canceled - safe to cancel parent
                    db_client.update_order_status(
                        client_order_id=slicing_plan.parent_order_id,
                        status="canceled",
                        error_message=f"Scheduling failed: {str(e)}",
                    )
                    logger.info(
                        "Compensated scheduling failure: canceled parent and "
                        f"{canceled_count} pending slices",
                        extra={
                            "parent_order_id": slicing_plan.parent_order_id,
                            "canceled_slices": canceled_count,
                            "total_slices": len(all_slices),
                        },
                    )
                else:
                    # Some slices already submitted/executing - don't cancel
                    # parent to avoid inconsistency
                    progressed_statuses = [s.status for s in progressed_slices]
                    logger.warning(
                        f"Scheduling partially failed but {len(progressed_slices)} "
                        f"slices already progressed (statuses: {progressed_statuses}). "
                        f"Canceled {canceled_count} pending slices but leaving "
                        "parent active to track live orders.",
                        extra={
                            "parent_order_id": slicing_plan.parent_order_id,
                            "canceled_slices": canceled_count,
                            "progressed_slices": len(progressed_slices),
                            "progressed_statuses": [s.status for s in progressed_slices],
                        },
                    )
            except psycopg.OperationalError as cleanup_error:
                logger.error(
                    "Cleanup failed after scheduling error - database connection error",
                    extra={
                        "parent_order_id": slicing_plan.parent_order_id,
                        "error": str(cleanup_error),
                        "error_type": type(cleanup_error).__name__,
                    },
                    exc_info=True,
                )
            except (AttributeError, KeyError) as cleanup_error:
                logger.error(
                    "Cleanup failed after scheduling error - data access error",
                    extra={
                        "parent_order_id": slicing_plan.parent_order_id,
                        "error": str(cleanup_error),
                        "error_type": type(cleanup_error).__name__,
                    },
                    exc_info=True,
                )
            # Re-raise original scheduling error
            raise

    # Endpoint definitions

    @router.post("/api/v1/orders/slice", response_model=SlicingPlan, tags=["Orders"])
    async def submit_sliced_order(
        request: SlicingRequest,
        response: Response,
        # IMPORTANT: Auth must run BEFORE rate limiting to populate request.state with user context
        # This allows rate limiter to bucket by user/service instead of anonymous IP
        _auth_context: AuthContext = Depends(order_slice_auth),
        _rate_limit_remaining: int = Depends(order_slice_rl),
    ) -> SlicingPlan:
        """
        Submit TWAP order with automatic slicing and scheduled execution.

        Creates a parent order and multiple child slice orders distributed evenly
        over the specified duration. Each slice is scheduled for execution at the
        requested interval spacing with mandatory safety guards (kill switch,
        circuit breaker checks).

        Args:
            request: TWAP slicing request (symbol, side, qty, duration, etc.)

        Returns:
            SlicingPlan with parent_order_id and list of scheduled slices

        Raises:
            HTTPException 400: Invalid request parameters
            HTTPException 503: Required services unavailable (scheduler, kill-switch, etc.)
            HTTPException 500: Database or scheduling error

        Examples:
            Market order TWAP:
            >>> import requests
            >>> response = requests.post(
            ...     "http://localhost:8002/api/v1/orders/slice",
            ...     json={
            ...         "symbol": "AAPL",
            ...         "side": "buy",
            ...         "qty": 100,
            ...         "duration_minutes": 5,
            ...         "order_type": "market"
            ...     }
            ... )
            >>> response.json()
            {
                "parent_order_id": "abc123...",
                "symbol": "AAPL",
                "side": "buy",
                "total_qty": 100,
                "total_slices": 5,
                "duration_minutes": 5,
                "slices": [
                    {"slice_num": 0, "qty": 20, "scheduled_time": "...", ...},
                    {"slice_num": 1, "qty": 20, "scheduled_time": "...", ...},
                    ...
                ]
            }
        """
        # Step 1: Log request (before prerequisite checks for observability)
        logger.info(
            f"TWAP order request: {request.symbol} {request.side} {request.qty} "
            f"over {request.duration_minutes} min",
            extra={
                "symbol": request.symbol,
                "side": request.side,
                "qty": request.qty,
                "duration_minutes": request.duration_minutes,
                "interval_seconds": request.interval_seconds,
            },
        )

        # Step 2: Check prerequisites (scheduler availability, kill-switch)
        _check_twap_prerequisites()

        # Reconciliation gating (no reduce-only path for TWAP)
        await _check_quarantine(request.symbol, STRATEGY_ID)
        if not _is_reconciliation_ready():
            if reconciliation_service and reconciliation_service.override_active():
                logger.warning(
                    "Reconciliation override active; allowing TWAP order",
                    extra={
                        "symbol": request.symbol,
                        "override": reconciliation_service.override_context(),
                    },
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Reconciliation in progress - TWAP orders blocked",
                )

        try:
            # CRITICAL: Use consistent trade_date for idempotency across midnight
            # If client retries after midnight, must pass same trade_date to avoid duplicate orders
            trade_date = request.trade_date or datetime.now(UTC).date()

            # Step 3: Apply liquidity constraints (ADV-based) before slicing
            adv_20d: int | None = None
            max_slice_qty: int | None = None
            if LIQUIDITY_CHECK_ENABLED:
                if liquidity_service is None:
                    logger.warning(
                        "Liquidity check enabled but service unavailable; rejecting TWAP request",
                        extra={"symbol": request.symbol},
                    )
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Liquidity service unavailable; please retry",
                    )
                else:
                    adv_20d = await asyncio.to_thread(
                        liquidity_service.get_adv, request.symbol
                    )
                    if adv_20d is None:
                        logger.warning(
                            "ADV lookup unavailable with no cache; rejecting TWAP "
                            "request to preserve idempotency",
                            extra={"symbol": request.symbol},
                        )
                        raise HTTPException(
                            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Liquidity data unavailable (ADV lookup failed); please retry",
                        )
                    computed = int(adv_20d * MAX_SLICE_PCT_OF_ADV)
                    if computed < 1:
                        logger.warning(
                            "Computed max_slice_qty < 1; clamping to 1 share",
                            extra={
                                "symbol": request.symbol,
                                "adv_20d": adv_20d,
                                "max_slice_pct_of_adv": MAX_SLICE_PCT_OF_ADV,
                                "computed": computed,
                                "clamped": 1,
                            },
                        )
                    max_slice_qty = max(1, computed)

            liquidity_constraints: dict[str, bool | int | float | str | None] = {
                "enabled": LIQUIDITY_CHECK_ENABLED,
                "adv_20d": adv_20d,
                "max_slice_pct_of_adv": MAX_SLICE_PCT_OF_ADV,
                "max_slice_qty": max_slice_qty,
            }
            if adv_20d is not None:
                liquidity_constraints["calculated_at"] = datetime.now(UTC).isoformat()
                liquidity_constraints["source"] = "alpaca_bars_20d"

            # Step 4: Create slicing plan with consistent trade_date
            slicing_plan = twap_slicer.plan(
                symbol=request.symbol,
                side=request.side,
                qty=request.qty,
                duration_minutes=request.duration_minutes,
                interval_seconds=request.interval_seconds,
                max_slice_qty=max_slice_qty,
                order_type=request.order_type,
                limit_price=request.limit_price,
                stop_price=request.stop_price,
                time_in_force=request.time_in_force,
                trade_date=trade_date,  # Pass consistent trade_date
            )

            # Step 5: Check for existing order (idempotency + backward compatibility)
            existing_plan = _find_existing_twap_plan(request, slicing_plan, trade_date)
            if existing_plan:
                return existing_plan

            # Step 6: Create parent + child orders atomically in database
            # Handles concurrent submissions by catching UniqueViolation
            concurrent_plan = _create_twap_in_db(
                request, slicing_plan, {"liquidity_constraints": liquidity_constraints}
            )
            if concurrent_plan:
                return concurrent_plan

            # Step 7: Schedule slices for execution with failure compensation
            job_ids = _schedule_slices_with_compensation(request, slicing_plan)

            # Step 8: Log success and return
            logger.info(
                f"TWAP order created: parent={slicing_plan.parent_order_id}, slices={len(job_ids)}",
                extra={
                    "parent_order_id": slicing_plan.parent_order_id,
                    "total_slices": len(job_ids),
                    "symbol": request.symbol,
                },
            )

            return slicing_plan
        except ValueError as e:
            # Validation error from slicer
            logger.error(
                "TWAP validation error",
                extra={"error": str(e), "error_type": type(e).__name__},
                exc_info=True,
            )
            # Re-raise with 'from e' to preserve original traceback for debugging
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
            ) from e
        except HTTPException:
            # Re-raise HTTP exceptions from prerequisite checks
            raise
        except psycopg.OperationalError as e:
            # Database connection error
            logger.error(
                "TWAP order creation failed - database connection error",
                extra={"error": str(e), "error_type": type(e).__name__},
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="TWAP order creation failed: database error",
            ) from e
        except (AttributeError, TypeError, KeyError) as e:
            # Data structure or scheduling error
            logger.error(
                "TWAP order creation failed - data error",
                extra={"error": str(e), "error_type": type(e).__name__},
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"TWAP order creation failed: {str(e)}",
            ) from e

    @router.get(
        "/api/v1/orders/{parent_id}/slices",
        response_model=list[OrderDetail],
        tags=["Orders"],
    )
    async def get_slices_by_parent(
        parent_id: str,
        _auth_context: AuthContext = Depends(order_read_auth),
    ) -> list[OrderDetail]:
        """
        Get all child slices for a parent TWAP order.

        Retrieves all child slice orders (both pending and executed) for a given
        parent order ID, ordered by slice number.

        Args:
            parent_id: Parent order's client_order_id

        Returns:
            List of OrderDetail for all child slices (ordered by slice_num)

        Raises:
            HTTPException 404: Parent order not found
            HTTPException 500: Database error

        Examples:
            >>> import requests
            >>> response = requests.get(
            ...     "http://localhost:8002/api/v1/orders/parent123/slices"
            ... )
            >>> response.json()
            [
                {"client_order_id": "slice0_abc...", "slice_num": 0, "status": "filled", ...},
                {"client_order_id": "slice1_def...", "slice_num": 1, "status": "pending_new", ...},
                ...
            ]
        """
        try:
            slices = db_client.get_slices_by_parent_id(parent_id)
            if not slices:
                # Check if parent exists
                parent = db_client.get_order_by_client_id(parent_id)
                if not parent:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Parent order not found: {parent_id}",
                    )
                # Parent exists but has no slices (shouldn't happen for TWAP orders)
                return []
            return slices
        except HTTPException:
            raise
        except psycopg.OperationalError as e:
            logger.error(
                "Failed to fetch slices - database connection error",
                extra={
                    "parent_id": parent_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to fetch slices: database error",
            ) from e
        except (AttributeError, KeyError) as e:
            logger.error(
                "Failed to fetch slices - data access error",
                extra={
                    "parent_id": parent_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch slices: {str(e)}",
            ) from e

    @router.delete("/api/v1/orders/{parent_id}/slices", tags=["Orders"])
    async def cancel_slices(
        parent_id: str,
        _auth_context: AuthContext = Depends(order_cancel_auth),
    ) -> dict[str, Any]:
        """
        Cancel all pending child slices for a parent TWAP order.

        Removes scheduled jobs from the scheduler and updates database to mark
        all pending_new slices as canceled. Already-executed slices are not affected.

        Args:
            parent_id: Parent order's client_order_id

        Returns:
            Dictionary with cancellation counts

        Raises:
            HTTPException 404: Parent order not found
            HTTPException 503: Scheduler unavailable
            HTTPException 500: Cancellation error

        Examples:
            >>> import requests
            >>> response = requests.delete(
            ...     "http://localhost:8002/api/v1/orders/parent123/slices"
            ... )
            >>> response.json()
            {
                "parent_order_id": "parent123",
                "scheduler_canceled": 3,
                "db_canceled": 3,
                "message": "Canceled 3 pending slices"
            }
        """
        slice_scheduler = recovery_manager.slice_scheduler

        # Check scheduler availability
        if not slice_scheduler:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Slice scheduler unavailable - cannot cancel slices",
            )

        # Check if parent exists
        parent = db_client.get_order_by_client_id(parent_id)
        if not parent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Parent order not found: {parent_id}",
            )

        try:
            # Cancel remaining slices (removes from scheduler + updates DB)
            # Note: SliceScheduler updates DB first, then removes scheduler jobs
            (
                scheduler_canceled_count,
                db_canceled_count,
            ) = slice_scheduler.cancel_remaining_slices(parent_id)

            logger.info(
                f"Canceled slices for parent {parent_id}: "
                f"scheduler={scheduler_canceled_count}, db={db_canceled_count}",
                extra={
                    "parent_order_id": parent_id,
                    "scheduler_canceled": scheduler_canceled_count,
                    "db_canceled": db_canceled_count,
                },
            )

            return {
                "parent_order_id": parent_id,
                "scheduler_canceled": scheduler_canceled_count,
                "db_canceled": db_canceled_count,
                "message": (
                    f"Canceled {db_canceled_count} pending slices in DB, "
                    f"removed {scheduler_canceled_count} jobs from scheduler"
                ),
            }
        except psycopg.OperationalError as e:
            logger.error(
                "Failed to cancel slices - database connection error",
                extra={
                    "parent_id": parent_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to cancel slices: database error",
            ) from e
        except (AttributeError, RuntimeError) as e:
            logger.error(
                "Failed to cancel slices - scheduler error",
                extra={
                    "parent_id": parent_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to cancel slices: {str(e)}",
            ) from e

    return router
