"""Reconciliation admin endpoints.

Provides operator controls for reconciliation gating, manual triggers,
fills backfill, and override mechanisms.

This module uses FastAPI's native dependency injection pattern (Depends())
instead of factory functions for cleaner, more testable code.

Design Pattern:
    - Router defined at module level (not inside factory function)
    - Dependencies injected via Depends() in route handlers
    - Dependencies retrieved from app.state via dependency providers
    - No closure over dependencies (cleaner, more testable)

See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 2B for design decisions.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from structlog import get_logger

from apps.execution_gateway.app_context import AppContext
from apps.execution_gateway.config import ExecutionGatewayConfig
from apps.execution_gateway.dependencies import get_config, get_context
from apps.execution_gateway.schemas import (
    ReconciliationFillsBackfillRequest,
    ReconciliationForceCompleteRequest,
)
from apps.execution_gateway.services.auth_helpers import build_user_context
from libs.core.common.rate_limit_dependency import RateLimitConfig, rate_limit
from libs.platform.web_console_auth.permissions import (
    Permission,
    require_permission,
)

logger = get_logger(__name__)

# Router defined at module level (Phase 2B refactoring)
router = APIRouter(prefix="/api/v1/reconciliation", tags=["Reconciliation"])


# Rate limiter for fills backfill endpoint
# Note: fills_backfill_limit and fills_backfill_window_seconds come from config
# but rate_limit() creates the dependency at module level, so we use constants here
# (config values are loaded during app startup and used when calling this endpoint)
def create_fills_backfill_rate_limiter(
    config: ExecutionGatewayConfig = Depends(get_config),
) -> RateLimitConfig:
    """Create rate limiter config for fills backfill endpoint.

    This dependency function creates a rate limiter configuration using
    values from ExecutionGatewayConfig. It's called during request handling
    to get the current configuration.

    Args:
        config: Application configuration (injected)

    Returns:
        RateLimitConfig: Rate limiter configuration
    """
    return RateLimitConfig(
        action="fills_backfill",
        max_requests=config.fills_backfill_limit,
        window_seconds=config.fills_backfill_window_seconds,
        burst_buffer=1,
        fallback_mode="deny",
        global_limit=config.fills_backfill_limit,
    )


@router.get("/status")
async def get_reconciliation_status(
    ctx: AppContext = Depends(get_context),
    config: ExecutionGatewayConfig = Depends(get_config),
) -> dict[str, Any]:
    """Return reconciliation gating status and override state.

    Args:
        ctx: Application context with all dependencies (injected)
        config: Application configuration (injected)

    Returns:
        dict: Reconciliation status information
    """
    if config.dry_run:
        return {
            "startup_complete": True,
            "dry_run": True,
            "message": "DRY_RUN mode - reconciliation gating disabled",
        }

    if not ctx.reconciliation_service:
        return {
            "startup_complete": False,
            "dry_run": False,
            "message": "Reconciliation service not initialized",
        }

    return {
        "startup_complete": ctx.reconciliation_service.is_startup_complete(),
        "dry_run": config.dry_run,
        "startup_elapsed_seconds": ctx.reconciliation_service.startup_elapsed_seconds(),
        "startup_timed_out": ctx.reconciliation_service.startup_timed_out(),
        "override_active": ctx.reconciliation_service.override_active(),
        "override_context": ctx.reconciliation_service.override_context(),
    }


@router.post("/run")
@require_permission(Permission.MANAGE_RECONCILIATION)
async def run_reconciliation(
    ctx: AppContext = Depends(get_context),
    config: ExecutionGatewayConfig = Depends(get_config),
    user: dict[str, Any] = Depends(build_user_context),
) -> dict[str, Any]:
    """Manually trigger reconciliation.

    Args:
        ctx: Application context with all dependencies (injected)
        config: Application configuration (injected)
        user: Authenticated user context (injected)

    Returns:
        dict: Reconciliation run status

    Raises:
        HTTPException: If reconciliation service not initialized
    """
    if config.dry_run:
        return {"status": "skipped", "message": "DRY_RUN mode - reconciliation disabled"}
    if not ctx.reconciliation_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Reconciliation service not initialized",
        )

    await ctx.reconciliation_service.run_reconciliation_once("manual")
    return {"status": "ok", "message": "Reconciliation run complete"}


@router.post("/fills-backfill")
@require_permission(Permission.MANAGE_RECONCILIATION)
async def run_fills_backfill(
    payload: ReconciliationFillsBackfillRequest | None = None,
    ctx: AppContext = Depends(get_context),
    config: ExecutionGatewayConfig = Depends(get_config),
    user: dict[str, Any] = Depends(build_user_context),
    _rate_limit_config: RateLimitConfig = Depends(create_fills_backfill_rate_limiter),
) -> dict[str, Any]:
    """Manually trigger Alpaca fills backfill.

    Args:
        payload: Optional request payload with lookback hours and recalc flag
        ctx: Application context with all dependencies (injected)
        config: Application configuration (injected)
        user: Authenticated user context (injected)
        _rate_limit_config: Rate limiter configuration (injected, enforced by decorator)

    Returns:
        dict: Fills backfill result

    Raises:
        HTTPException: If reconciliation service not initialized
    """
    if config.dry_run:
        return {"status": "skipped", "message": "DRY_RUN mode - reconciliation disabled"}
    if not ctx.reconciliation_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Reconciliation service not initialized",
        )

    lookback_hours = None
    recalc_all_trades = False
    if payload:
        lookback_hours = payload.lookback_hours
        recalc_all_trades = payload.recalc_all_trades

    result = await ctx.reconciliation_service.run_fills_backfill_once(
        lookback_hours=lookback_hours,
        recalc_all_trades=recalc_all_trades,
    )
    return {
        "status": "ok",
        "message": "Fills backfill complete",
        "result": result,
    }


@router.post("/force-complete")
@require_permission(Permission.MANAGE_RECONCILIATION)
async def force_complete_reconciliation(
    payload: ReconciliationForceCompleteRequest,
    ctx: AppContext = Depends(get_context),
    config: ExecutionGatewayConfig = Depends(get_config),
    user: dict[str, Any] = Depends(build_user_context),
) -> dict[str, Any]:
    """Force-complete reconciliation (operator override).

    Args:
        payload: Request payload with override reason
        ctx: Application context with all dependencies (injected)
        config: Application configuration (injected)
        user: Authenticated user context (injected)

    Returns:
        dict: Override status

    Raises:
        HTTPException: If reconciliation service not initialized
    """
    if config.dry_run:
        return {"status": "skipped", "message": "DRY_RUN mode - reconciliation disabled"}
    if not ctx.reconciliation_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Reconciliation service not initialized",
        )

    user_id = user.get("user_id") if isinstance(user, dict) else None

    ctx.reconciliation_service.mark_startup_complete(
        forced=True, user_id=user_id, reason=payload.reason
    )
    logger.warning(
        "Reconciliation force-complete invoked",
        extra={"user_id": user_id, "reason": payload.reason},
    )
    return {
        "status": "override_enabled",
        "message": "Reconciliation marked complete by operator override",
        "user_id": user_id,
        "reason": payload.reason,
    }
