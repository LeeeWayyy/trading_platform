"""Health check endpoints for the Execution Gateway.

This module provides health check and root endpoints using FastAPI's native
dependency injection pattern (Depends()) instead of factory functions.

Design Pattern:
    - Router defined at module level (not inside factory function)
    - Dependencies injected via Depends() in route handlers
    - Dependencies retrieved from app.state via dependency providers
    - No closure over dependencies (cleaner, more testable)

See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 2B for design decisions.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends

from apps.execution_gateway.alpaca_client import (
    AlpacaConnectionError,
    AlpacaRejectionError,
    AlpacaValidationError,
)
from apps.execution_gateway.app_context import AppContext
from apps.execution_gateway.config import ExecutionGatewayConfig
from apps.execution_gateway.dependencies import (
    get_config,
    get_context,
    get_metrics,
    get_version,
)
from apps.execution_gateway.schemas import HealthResponse
from apps.execution_gateway.slice_scheduler import SliceScheduler
from libs.trading.risk_management import CircuitBreaker, KillSwitch, PositionReservation

logger = logging.getLogger(__name__)

# Router defined at module level (Phase 2B refactoring)
router = APIRouter()


@router.get("/")
async def root(
    version: str = Depends(get_version),
    config: ExecutionGatewayConfig = Depends(get_config),
) -> dict[str, Any]:
    """Root endpoint.

    Returns basic service information.

    Args:
        version: Service version (injected)
        config: Application configuration (injected)

    Returns:
        dict: Service metadata
    """
    return {
        "service": "execution_gateway",
        "version": version,
        "status": "running",
        "dry_run": config.dry_run,
    }


@router.get("/health", tags=["health"])
async def health_check(
    ctx: AppContext = Depends(get_context),
    config: ExecutionGatewayConfig = Depends(get_config),
    version: str = Depends(get_version),
    metrics: dict[str, Any] = Depends(get_metrics),
) -> HealthResponse:
    """
    Health check endpoint.

    Returns service health status including:
    - Overall status (healthy, degraded, unhealthy)
    - Database connection status
    - Alpaca connection status (if not DRY_RUN)
    - Service version and configuration

    Args:
        ctx: Application context with all dependencies (injected)
        config: Application configuration (injected)
        version: Service version (injected)
        metrics: Prometheus metrics registry (injected)

    Returns:
        HealthResponse with service health details

    Examples:
        >>> import requests
        >>> response = requests.get("http://localhost:8002/health")
        >>> response.json()
        {
            "status": "healthy",
            "service": "execution_gateway",
            "version": "0.1.0",
            "dry_run": true,
            "database_connected": true,
            "alpaca_connected": true,
            "timestamp": "2024-10-17T16:30:00Z"
        }
    """
    # Check database connection
    db_connected = ctx.db.check_connection()

    # Check Redis connection and attempt infrastructure recovery via RecoveryManager
    redis_connected = False
    if ctx.redis:
        redis_connected = ctx.redis.health_check()

        if redis_connected:
            # Factories only called when components verified available by RecoveryManager
            ctx.recovery_manager.attempt_recovery(
                kill_switch_factory=lambda: KillSwitch(redis_client=ctx.redis),
                circuit_breaker_factory=lambda: CircuitBreaker(redis_client=ctx.redis),
                position_reservation_factory=lambda: PositionReservation(redis=ctx.redis),
                slice_scheduler_factory=lambda: SliceScheduler(
                    kill_switch=ctx.recovery_manager.kill_switch,  # type: ignore[arg-type]
                    breaker=ctx.recovery_manager.circuit_breaker,  # type: ignore[arg-type]
                    db_client=ctx.db,
                    executor=ctx.alpaca,  # Can be None in DRY_RUN mode
                ),
            )

    # Check Alpaca connection (if not DRY_RUN)
    alpaca_connected = True
    if not config.dry_run and ctx.alpaca:
        alpaca_api_status = "success"
        try:
            alpaca_connected = ctx.alpaca.check_connection()
        except AlpacaConnectionError:
            logger.debug("Alpaca connection check failed - connection error")
            alpaca_api_status = "error"
            alpaca_connected = False
        except (AlpacaValidationError, AlpacaRejectionError):
            logger.debug("Alpaca connection check failed - validation/rejection error")
            alpaca_api_status = "error"
            alpaca_connected = False
        except OSError:
            logger.debug("Alpaca connection check failed - network/IO error")
            alpaca_api_status = "error"
            alpaca_connected = False
        finally:
            # Always track Alpaca API request metric
            metrics["alpaca_api_requests_total"].labels(
                operation="check_connection", status=alpaca_api_status
            ).inc()

    # Update health metrics
    metrics["database_connection_status"].set(1 if db_connected else 0)
    metrics["redis_connection_status"].set(1 if redis_connected else 0)
    metrics["alpaca_connection_status"].set(1 if (not config.dry_run and alpaca_connected) else 0)

    # Determine overall status
    overall_status: Literal["healthy", "degraded", "unhealthy"]
    if ctx.recovery_manager.needs_recovery():
        # Safety mechanisms unavailable means we're in fail-closed mode - report degraded
        overall_status = "degraded"
    elif db_connected and (config.dry_run or alpaca_connected):
        overall_status = "healthy"
    elif db_connected:
        overall_status = "degraded"  # DB OK but Alpaca down
    else:
        overall_status = "unhealthy"  # DB down

    return HealthResponse(
        status=overall_status,
        service="execution_gateway",
        version=version,
        dry_run=config.dry_run,
        database_connected=db_connected,
        alpaca_connected=alpaca_connected,
        timestamp=datetime.now(UTC),
        details={
            "strategy_id": config.strategy_id,
            "alpaca_base_url": config.alpaca_base_url if not config.dry_run else None,
        },
    )
