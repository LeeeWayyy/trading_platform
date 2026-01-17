"""
TWAP Slicing Routes Module

Provides endpoints for TWAP (Time-Weighted Average Price) order slicing:
- POST /api/v1/orders/slice - Create sliced order with scheduled execution
- GET /api/v1/orders/{parent_id}/slices - Retrieve child slices
- DELETE /api/v1/orders/{parent_id}/slices - Cancel pending slices

Safety Gates (per spec):
- Auth → Scheduler Unavail → KS Unavail → Kill-Switch → Quarantine → Recon Gate → Liquidity → Slice
- Note: NO circuit-breaker gates for TWAP/slice (current behavior)

Design Pattern:
    - Router defined at module level (not inside factory function)
    - Dependencies injected via Depends() in route handlers
    - Dependencies retrieved from app.state via dependency providers
    - No closure over dependencies (cleaner, more testable)

Created: 2025-01-16
Phase: P6T2 Refactor - Route Extraction
Updated: Phase 2B - Converted to Depends() pattern
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, date, datetime
from typing import Any

import psycopg
import redis.exceptions
from fastapi import APIRouter, Depends, HTTPException, status
from psycopg.errors import UniqueViolation
from redis.exceptions import RedisError

from apps.execution_gateway.api.dependencies import build_gateway_authenticator
from apps.execution_gateway.app_context import AppContext
from apps.execution_gateway.config import ExecutionGatewayConfig, _get_float_env
from apps.execution_gateway.dependencies import get_config, get_context
from apps.execution_gateway.order_id_generator import reconstruct_order_params_hash
from apps.execution_gateway.schemas import (
    OrderDetail,
    OrderRequest,
    SliceDetail,
    SlicingPlan,
    SlicingRequest,
)
from libs.core.common.api_auth_dependency import (
    APIAuthConfig,
    AuthContext,
    api_auth,
)
from libs.core.common.rate_limit_dependency import RateLimitConfig, rate_limit
from libs.core.redis_client import RedisKeys
from libs.platform.web_console_auth.permissions import Permission

logger = logging.getLogger(__name__)

# Router defined at module level (Phase 2B refactoring)
router = APIRouter()

# Constants (from environment)
LEGACY_TWAP_INTERVAL_SECONDS = 60

LIQUIDITY_CHECK_ENABLED = os.getenv("LIQUIDITY_CHECK_ENABLED", "true").lower() in (
    "true",
    "1",
    "yes",
)
MAX_SLICE_PCT_OF_ADV = _get_float_env("MAX_SLICE_PCT_OF_ADV", 0.01)


# =============================================================================
# Auth and Rate Limiting Dependencies (Module Level)
# =============================================================================

order_slice_auth = api_auth(
    APIAuthConfig(
        action="order_slice",
        require_role=None,
        require_permission=Permission.SUBMIT_ORDER,
    ),
    authenticator_getter=build_gateway_authenticator,
)

order_slice_rl = rate_limit(
    RateLimitConfig(
        action="order_slice",
        max_requests=10,
        window_seconds=60,
        burst_buffer=5,
        fallback_mode="deny",
        global_limit=20,
    )
)

order_read_auth = api_auth(
    APIAuthConfig(
        action="order_read",
        require_role=None,
        require_permission=Permission.VIEW_POSITIONS,
    ),
    authenticator_getter=build_gateway_authenticator,
)

order_cancel_auth = api_auth(
    APIAuthConfig(
        action="order_cancel",
        require_role=None,
        require_permission=Permission.CANCEL_ORDER,
    ),
    authenticator_getter=build_gateway_authenticator,
)


# =============================================================================
# Helper Functions (Module Level)
# =============================================================================


def _is_reconciliation_ready(ctx: AppContext, config: ExecutionGatewayConfig) -> bool:
    """Return True when startup reconciliation gate is open."""
    if config.dry_run:
        return True
    if ctx.reconciliation_service is None:
        return False
    return ctx.reconciliation_service.is_startup_complete()


async def _check_quarantine(
    symbol: str, strategy_id: str, ctx: AppContext, config: ExecutionGatewayConfig
) -> None:
    """Block trading when symbol is quarantined."""
    if config.dry_run:
        return
    if not ctx.redis:
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
        values = await asyncio.to_thread(ctx.redis.mget, [strategy_key, wildcard_key])
        strategy_value, wildcard_value = (values + [None, None])[:2] if values else (None, None)
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


def _check_twap_prerequisites(ctx: AppContext) -> None:
    """
    Check prerequisites for TWAP order submission.

    Validates that slice scheduler is available and kill-switch allows trading.
    Follows fail-closed principle for kill-switch unavailability.

    Raises:
        HTTPException 503: If scheduler unavailable or kill-switch state unknown
        HTTPException 503: If kill-switch is engaged
    """
    slice_scheduler = ctx.recovery_manager.slice_scheduler
    kill_switch = ctx.recovery_manager.kill_switch

    # Check if slice scheduler is available
    if not slice_scheduler:
        logger.error("Slice scheduler unavailable - cannot accept TWAP orders")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TWAP order service unavailable (scheduler not initialized)",
        )

    # Check kill-switch availability (fail closed)
    if ctx.recovery_manager.is_kill_switch_unavailable() or kill_switch is None:
        logger.error("Kill-switch unavailable - cannot accept TWAP orders (fail closed)")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Kill-switch unavailable",
                "message": "Kill-switch state unknown (fail-closed for safety)",
                "fail_closed": True,
            },
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
    """
    slice_details = []
    for s in slices:
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
    request: SlicingRequest, slicing_plan: SlicingPlan, trade_date: date, ctx: AppContext
) -> SlicingPlan | None:
    """
    Check for existing TWAP order (idempotency + backward compatibility).

    First checks new hash format (with duration), then legacy hash (without duration)
    for backward compatibility with pre-fix orders.
    """
    existing_parent = ctx.db.get_order_by_client_id(slicing_plan.parent_order_id)

    if existing_parent:
        slicing_plan.parent_strategy_id = existing_parent.strategy_id
    else:
        if request.interval_seconds != LEGACY_TWAP_INTERVAL_SECONDS:
            logger.debug(
                "Skipping legacy TWAP hash fallback for non-default interval",
                extra={
                    "requested_interval_seconds": request.interval_seconds,
                    "legacy_interval_seconds": LEGACY_TWAP_INTERVAL_SECONDS,
                },
            )
            return None

        requested_total_slices = slicing_plan.total_slices
        fallback_strategies = [
            (f"twap_parent_{request.duration_minutes}m", "duration-based legacy hash"),
            ("twap_parent", "pre-duration legacy hash"),
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
            legacy_parent = ctx.db.get_order_by_client_id(legacy_parent_id)

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

    logger.info(
        f"TWAP order already exists (idempotent): parent={slicing_plan.parent_order_id}",
        extra={
            "parent_order_id": slicing_plan.parent_order_id,
            "status": existing_parent.status,
            "total_slices": existing_parent.total_slices,
        },
    )

    existing_slices = ctx.db.get_slices_by_parent_id(slicing_plan.parent_order_id)
    slice_details = _convert_slices_to_details(existing_slices, slicing_plan.parent_order_id)

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
    ctx: AppContext,
) -> SlicingPlan | None:
    """
    Create parent + child orders atomically in database.

    Uses database transaction for all-or-nothing behavior.
    """
    try:
        with ctx.db.transaction() as conn:
            parent_order_request = OrderRequest(
                symbol=request.symbol,
                side=request.side,
                qty=request.qty,
                order_type=request.order_type,
                limit_price=request.limit_price,
                stop_price=request.stop_price,
                time_in_force=request.time_in_force,
            )
            ctx.db.create_parent_order(
                client_order_id=slicing_plan.parent_order_id,
                strategy_id=slicing_plan.parent_strategy_id,
                order_request=parent_order_request,
                total_slices=slicing_plan.total_slices,
                metadata=parent_metadata,
                conn=conn,
            )

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
                ctx.db.create_child_slice(
                    client_order_id=slice_detail.client_order_id,
                    parent_order_id=slicing_plan.parent_order_id,
                    slice_num=slice_detail.slice_num,
                    strategy_id=slice_detail.strategy_id,
                    order_request=slice_order_request,
                    scheduled_time=slice_detail.scheduled_time,
                    conn=conn,
                )
    except UniqueViolation:
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

        existing_parent = ctx.db.get_order_by_client_id(slicing_plan.parent_order_id)
        if not existing_parent:
            logger.error(
                "UniqueViolation raised but parent order not found: "
                f"{slicing_plan.parent_order_id}",
                extra={"parent_order_id": slicing_plan.parent_order_id},
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database inconsistency: parent order not found after UniqueViolation",
            ) from None

        slicing_plan.parent_strategy_id = existing_parent.strategy_id

        existing_slices = ctx.db.get_slices_by_parent_id(slicing_plan.parent_order_id)
        slice_details = _convert_slices_to_details(existing_slices, slicing_plan.parent_order_id)

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

    return None


def _schedule_slices_with_compensation(
    request: SlicingRequest, slicing_plan: SlicingPlan, ctx: AppContext
) -> list[str]:
    """
    Schedule slices for execution with failure compensation.
    """
    slice_scheduler = ctx.recovery_manager.slice_scheduler
    assert slice_scheduler is not None, "Slice scheduler must be initialized"

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
        try:
            canceled_count = ctx.db.cancel_pending_slices(slicing_plan.parent_order_id)
            all_slices = ctx.db.get_slices_by_parent_id(slicing_plan.parent_order_id)
            progressed_slices = [
                s for s in all_slices if s.status != "pending_new" and s.status != "canceled"
            ]

            if not progressed_slices:
                ctx.db.update_order_status(
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
        raise


# =============================================================================
# POST /api/v1/orders/slice - Submit TWAP Order
# =============================================================================


@router.post("/api/v1/orders/slice", response_model=SlicingPlan, tags=["Orders"])
async def submit_sliced_order(
    request: SlicingRequest,
    _auth_context: AuthContext = Depends(order_slice_auth),
    _rate_limit_remaining: int = Depends(order_slice_rl),
    ctx: AppContext = Depends(get_context),
    config: ExecutionGatewayConfig = Depends(get_config),
) -> SlicingPlan:
    """
    Submit TWAP order with automatic slicing and scheduled execution.

    Creates a parent order and multiple child slice orders distributed evenly
    over the specified duration.

    Args:
        request: TWAP slicing request (symbol, side, qty, duration, etc.)
        response: FastAPI response object
        _auth_context: Authentication context (injected)
        _rate_limit_remaining: Rate limit remaining (injected)
        ctx: Application context with all dependencies (injected)
        config: Application configuration (injected)

    Returns:
        SlicingPlan with parent_order_id and list of scheduled slices
    """
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

    _check_twap_prerequisites(ctx)

    await _check_quarantine(request.symbol, config.strategy_id, ctx, config)
    if not _is_reconciliation_ready(ctx, config):
        if ctx.reconciliation_service and ctx.reconciliation_service.override_active():
            logger.warning(
                "Reconciliation override active; allowing TWAP order",
                extra={
                    "symbol": request.symbol,
                    "override": ctx.reconciliation_service.override_context(),
                },
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Reconciliation in progress - TWAP orders blocked",
            )

    try:
        trade_date = request.trade_date or datetime.now(UTC).date()

        adv_20d: int | None = None
        max_slice_qty: int | None = None
        if LIQUIDITY_CHECK_ENABLED:
            if ctx.liquidity_service is None:
                logger.warning(
                    "Liquidity check enabled but service unavailable; rejecting TWAP request",
                    extra={"symbol": request.symbol},
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Liquidity service unavailable; please retry",
                )
            else:
                adv_20d = await asyncio.to_thread(ctx.liquidity_service.get_adv, request.symbol)
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

        slicing_plan = ctx.twap_slicer.plan(
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
            trade_date=trade_date,
        )

        existing_plan = _find_existing_twap_plan(request, slicing_plan, trade_date, ctx)
        if existing_plan:
            return existing_plan

        concurrent_plan = _create_twap_in_db(
            request, slicing_plan, {"liquidity_constraints": liquidity_constraints}, ctx
        )
        if concurrent_plan:
            return concurrent_plan

        job_ids = _schedule_slices_with_compensation(request, slicing_plan, ctx)

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
        logger.error(
            "TWAP validation error",
            extra={"error": str(e), "error_type": type(e).__name__},
            exc_info=True,
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except HTTPException:
        raise
    except psycopg.OperationalError as e:
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
        logger.error(
            "TWAP order creation failed - data error",
            extra={"error": str(e), "error_type": type(e).__name__},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"TWAP order creation failed: {str(e)}",
        ) from e


# =============================================================================
# GET /api/v1/orders/{parent_id}/slices - Get Slices
# =============================================================================


@router.get(
    "/api/v1/orders/{parent_id}/slices",
    response_model=list[OrderDetail],
    tags=["Orders"],
)
async def get_slices_by_parent(
    parent_id: str,
    _auth_context: AuthContext = Depends(order_read_auth),
    ctx: AppContext = Depends(get_context),
) -> list[OrderDetail]:
    """
    Get all child slices for a parent TWAP order.

    Args:
        parent_id: Parent order's client_order_id
        _auth_context: Authentication context (injected)
        ctx: Application context with all dependencies (injected)

    Returns:
        List of OrderDetail for all child slices (ordered by slice_num)
    """
    try:
        slices = ctx.db.get_slices_by_parent_id(parent_id)
        if not slices:
            parent = ctx.db.get_order_by_client_id(parent_id)
            if not parent:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Parent order not found: {parent_id}",
                )
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


# =============================================================================
# DELETE /api/v1/orders/{parent_id}/slices - Cancel Slices
# =============================================================================


@router.delete("/api/v1/orders/{parent_id}/slices", tags=["Orders"])
async def cancel_slices(
    parent_id: str,
    _auth_context: AuthContext = Depends(order_cancel_auth),
    ctx: AppContext = Depends(get_context),
) -> dict[str, Any]:
    """
    Cancel all pending child slices for a parent TWAP order.

    Args:
        parent_id: Parent order's client_order_id
        _auth_context: Authentication context (injected)
        ctx: Application context with all dependencies (injected)

    Returns:
        Dictionary with cancellation counts
    """
    slice_scheduler = ctx.recovery_manager.slice_scheduler

    if not slice_scheduler:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Slice scheduler unavailable - cannot cancel slices",
        )

    parent = ctx.db.get_order_by_client_id(parent_id)
    if not parent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Parent order not found: {parent_id}",
        )

    try:
        (scheduler_canceled_count, db_canceled_count) = slice_scheduler.cancel_remaining_slices(
            parent_id
        )

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


# =============================================================================
# Legacy Factory Function (Deprecated)
# =============================================================================
# The create_slicing_router factory function has been deprecated in favor of
# module-level router definition with Depends() pattern.
# This factory is kept temporarily for backward compatibility during Phase 2B
# transition. It will be removed after all routes are migrated.
#
# See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 2B for migration details.
