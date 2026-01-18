"""
Position and Performance Routes for Execution Gateway.

This module provides REST API endpoints for querying positions, performance metrics,
account information, and real-time market prices with RBAC enforcement.

Endpoints:
    GET /api/v1/positions - Query current positions with P&L
    GET /api/v1/performance/daily - Daily performance metrics with caching
    GET /api/v1/positions/pnl/realtime - Real-time P&L with market prices
    GET /api/v1/account - Account information from Alpaca
    GET /api/v1/market_prices - Current market prices for positions

Key Features:
    - RBAC filtering based on user's strategy access
    - Performance data caching with Redis (5-minute TTL)
    - Real-time price resolution from Redis/WebSocket feeds
    - Position metrics for Prometheus monitoring
    - Comprehensive error handling with empty result guards

Design Pattern:
    - Router defined at module level (not inside factory function)
    - Dependencies injected via Depends() in route handlers
    - Dependencies retrieved from app.state via dependency providers
    - No closure over dependencies (cleaner, more testable)

See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 2B for design decisions.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import ValidationError
from redis.exceptions import RedisError

from apps.execution_gateway.api.dependencies import build_gateway_authenticator
from apps.execution_gateway.app_context import AppContext
from apps.execution_gateway.config import ExecutionGatewayConfig
from apps.execution_gateway.dependencies import get_config, get_context
from apps.execution_gateway.metrics import pnl_dollars, positions_current
from apps.execution_gateway.schemas import (
    AccountInfoResponse,
    DailyPerformanceResponse,
    MarketPricePoint,
    PerformanceRequest,
    PositionsResponse,
    RealtimePnLResponse,
)
from apps.execution_gateway.services.auth_helpers import build_user_context
from apps.execution_gateway.services.order_helpers import batch_fetch_realtime_prices_from_redis
from apps.execution_gateway.services.performance_cache import (
    create_performance_cache_key,
    register_performance_cache,
)
from apps.execution_gateway.services.pnl_calculator import (
    compute_daily_performance,
    resolve_and_calculate_pnl,
)
from libs.core.common.api_auth_dependency import (
    APIAuthConfig,
    AuthContext,
    api_auth,
)
from libs.platform.web_console_auth.permissions import (
    Permission,
    get_authorized_strategies,
    has_permission,
    require_permission,
)

logger = logging.getLogger(__name__)

# Router defined at module level (Phase 2B refactoring)
router = APIRouter()

# Feature flags and configuration
PERFORMANCE_CACHE_TTL = int(os.getenv("PERFORMANCE_CACHE_TTL", "300"))
FEATURE_PERFORMANCE_DASHBOARD = os.getenv("FEATURE_PERFORMANCE_DASHBOARD", "false").lower() in (
    "true",
    "1",
    "yes",
)

# Metrics are defined in apps.execution_gateway.metrics

# Auth dependency (module level)
order_read_auth = api_auth(
    APIAuthConfig(
        action="order_read",
        require_role=None,
        require_permission=Permission.VIEW_POSITIONS,
    ),
    authenticator_getter=build_gateway_authenticator,
)


# =============================================================================
# Position Endpoints
# =============================================================================


@router.get("/api/v1/positions", response_model=PositionsResponse, tags=["Positions"])
async def get_positions(
    _auth_context: AuthContext = Depends(order_read_auth),
    ctx: AppContext = Depends(get_context),
) -> PositionsResponse:
    """
    Get all current positions.

    Returns list of positions with P&L calculations.

    Args:
        _auth_context: Authentication context (injected)
        ctx: Application context with all dependencies (injected)

    Returns:
        PositionsResponse with list of positions
    """
    positions = ctx.db.get_all_positions()

    # Protect access to the shared tracked_position_symbols set and fix memory leak
    async with ctx.position_metrics_lock:
        current_symbols = {pos.symbol for pos in positions}

        # Reset metrics for symbols that are no longer in our portfolio
        symbols_to_remove = ctx.tracked_position_symbols - current_symbols
        for symbol in symbols_to_remove:
            positions_current.labels(symbol=symbol).set(0)

        # Update position metrics for each current symbol
        for pos in positions:
            positions_current.labels(symbol=pos.symbol).set(float(pos.qty))

        # Update the set of tracked symbols to match the current portfolio
        ctx.tracked_position_symbols.clear()
        ctx.tracked_position_symbols.update(current_symbols)

    # Fill unrealized P&L if missing but prices are available (local dev parity).
    for pos in positions:
        if pos.unrealized_pl is None and pos.current_price is not None:
            pos.unrealized_pl = (pos.current_price - pos.avg_entry_price) * pos.qty

    # Calculate totals
    total_unrealized_pl = sum(
        ((pos.unrealized_pl or Decimal("0")) for pos in positions), Decimal("0")
    )
    total_realized_pl = sum((pos.realized_pl for pos in positions), Decimal("0"))

    return PositionsResponse(
        positions=positions,
        total_positions=len(positions),
        total_unrealized_pl=total_unrealized_pl if positions else None,
        total_realized_pl=total_realized_pl,
    )


# =============================================================================
# Performance Endpoints
# =============================================================================


@router.get(
    "/api/v1/performance/daily", response_model=DailyPerformanceResponse, tags=["Performance"]
)
@require_permission(Permission.VIEW_PNL)
async def get_daily_performance(
    request: Request,
    start_date: date = Query(default_factory=lambda: date.today() - timedelta(days=30)),
    end_date: date = Query(default_factory=date.today),
    user: dict[str, Any] = Depends(build_user_context),
    ctx: AppContext = Depends(get_context),
) -> DailyPerformanceResponse:
    """Daily realized P&L (equity & drawdown) for performance dashboard.

    Args:
        request: FastAPI request object
        start_date: Start date for performance data
        end_date: End date for performance data
        user: User context from authentication (injected)
        ctx: Application context with all dependencies (injected)

    Returns:
        DailyPerformanceResponse with daily P&L data
    """
    if not FEATURE_PERFORMANCE_DASHBOARD:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Performance dashboard disabled"
        )

    perf_request = PerformanceRequest(start_date=start_date, end_date=end_date)
    authorized_strategies = get_authorized_strategies(user.get("user"))
    requested_strategies = cast(
        list[str], user.get("requested_strategies", []) if isinstance(user, dict) else []
    )
    user_id = user.get("user_id") if isinstance(user, dict) else None
    if not authorized_strategies:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No strategy access")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Missing user id for RBAC"
        )

    invalid_strategies = set(requested_strategies) - set(authorized_strategies)
    if invalid_strategies:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Strategy access denied: {sorted(invalid_strategies)}",
        )

    effective_strategies = requested_strategies or authorized_strategies
    if not effective_strategies:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No strategy access")

    cache_key = create_performance_cache_key(
        perf_request.start_date, perf_request.end_date, tuple(effective_strategies), user_id
    )

    # Serve from cache if available; scoped to user+strategies
    if ctx.redis:
        try:
            cached = ctx.redis.get(cache_key)
            if cached:
                return DailyPerformanceResponse.model_validate_json(cached)
        except RedisError as e:
            logger.warning(
                "Performance cache read failed - Redis error",
                extra={"error": str(e), "error_type": type(e).__name__},
            )
        except (ValidationError, ValueError) as e:
            logger.warning(
                "Performance cache read failed - data validation error",
                extra={"error": str(e), "error_type": type(e).__name__},
            )

    rows = ctx.db.get_daily_pnl_history(
        perf_request.start_date, perf_request.end_date, effective_strategies
    )
    daily, total_realized, max_drawdown = compute_daily_performance(
        rows, perf_request.start_date, perf_request.end_date
    )

    data_available_from = ctx.db.get_data_availability_date()

    response = DailyPerformanceResponse(
        daily_pnl=daily,
        total_realized_pl=total_realized,
        max_drawdown_pct=max_drawdown,
        start_date=perf_request.start_date,
        end_date=perf_request.end_date,
        data_available_from=data_available_from,
        last_updated=datetime.now(UTC),
    )

    # Cache response and register index for targeted invalidation
    if ctx.redis:
        try:
            ctx.redis.set(cache_key, response.model_dump_json(), ttl=PERFORMANCE_CACHE_TTL)
            register_performance_cache(
                ctx.redis,
                cache_key,
                perf_request.start_date,
                perf_request.end_date,
                PERFORMANCE_CACHE_TTL,
            )
        except RedisError as e:
            logger.warning(
                "Performance cache write failed - Redis error",
                extra={"error": str(e), "error_type": type(e).__name__},
            )
        except (AttributeError, TypeError) as e:
            logger.warning(
                "Performance cache write failed - data serialization error",
                extra={"error": str(e), "error_type": type(e).__name__},
            )

    return response


# =============================================================================
# Real-time P&L Endpoints
# =============================================================================


@router.get(
    "/api/v1/positions/pnl/realtime", response_model=RealtimePnLResponse, tags=["Positions"]
)
@require_permission(Permission.VIEW_PNL)
async def get_realtime_pnl(
    user: dict[str, Any] = Depends(build_user_context),
    ctx: AppContext = Depends(get_context),
) -> RealtimePnLResponse:
    """
    Get real-time P&L with latest market prices.

    Fetches latest prices from Redis cache (populated by Market Data Service).
    Falls back to database prices if real-time data is unavailable.

    Price source priority:
    1. real-time: Latest price from Redis (Market Data Service via WebSocket)
    2. database: Last known price from database (closing price or last fill)
    3. fallback: Entry price (if no other price available)

    Args:
        user: User context from authentication (injected)
        ctx: Application context with all dependencies (injected)

    Returns:
        RealtimePnLResponse with real-time P&L for all positions
    """
    # Resolve strategy access (fail closed)
    authorized_strategies = get_authorized_strategies(user.get("user"))
    if not authorized_strategies and not has_permission(
        user.get("user"), Permission.VIEW_ALL_STRATEGIES
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No strategy access")

    # DESIGN DECISION: Separate try/except for DB call vs empty-result guard.
    try:
        if has_permission(user.get("user"), Permission.VIEW_ALL_STRATEGIES):
            db_positions = ctx.db.get_all_positions()
        else:
            db_positions = ctx.db.get_positions_for_strategies(authorized_strategies)
    except psycopg.OperationalError as exc:  # pragma: no cover
        logger.error(
            "Failed to load positions for real-time P&L - database connection error",
            extra={"error": str(exc), "error_type": type(exc).__name__},
            exc_info=True,
        )
        return RealtimePnLResponse(
            positions=[],
            total_positions=0,
            total_unrealized_pl=Decimal("0"),
            total_unrealized_pl_pct=None,
            realtime_prices_available=0,
            timestamp=datetime.now(UTC),
        )
    except (AttributeError, KeyError) as exc:  # pragma: no cover
        logger.error(
            "Failed to load positions for real-time P&L - data access error",
            extra={"error": str(exc), "error_type": type(exc).__name__},
            exc_info=True,
        )
        return RealtimePnLResponse(
            positions=[],
            total_positions=0,
            total_unrealized_pl=Decimal("0"),
            total_unrealized_pl_pct=None,
            realtime_prices_available=0,
            timestamp=datetime.now(UTC),
        )

    # Additional guard: if strategy-scoped request returns no positions but DB call succeeded
    if not has_permission(user.get("user"), Permission.VIEW_ALL_STRATEGIES) and not db_positions:
        return RealtimePnLResponse(
            positions=[],
            total_positions=0,
            total_unrealized_pl=Decimal("0"),
            total_unrealized_pl_pct=None,
            realtime_prices_available=0,
            timestamp=datetime.now(UTC),
        )

    if not db_positions:
        # Reset P&L gauges to 0 when no positions (prevent stale values)
        pnl_dollars.labels(type="unrealized").set(0)
        pnl_dollars.labels(type="realized").set(0)
        pnl_dollars.labels(type="total").set(0)
        return RealtimePnLResponse(
            positions=[],
            total_positions=0,
            total_unrealized_pl=Decimal("0"),
            total_unrealized_pl_pct=None,
            realtime_prices_available=0,
            timestamp=datetime.now(UTC),
        )

    # Batch fetch real-time prices for all symbols (solves N+1 query problem)
    symbols = [pos.symbol for pos in db_positions]
    realtime_prices = batch_fetch_realtime_prices_from_redis(symbols, ctx.redis)

    # Calculate real-time P&L for each position
    realtime_positions = []
    realtime_count = 0
    total_investment = Decimal("0")

    for pos in db_positions:
        # Using .get() for safer access (though all symbols should be in dict from batch fetch)
        realtime_price_data = realtime_prices.get(pos.symbol, (None, None))

        # Resolve price and calculate P&L (extracted for modularity)
        position_pnl, is_realtime = resolve_and_calculate_pnl(pos, realtime_price_data)

        if is_realtime:
            realtime_count += 1

        realtime_positions.append(position_pnl)

        # Track total investment for portfolio-level percentage
        total_investment += pos.avg_entry_price * abs(pos.qty)

    # Calculate totals
    total_unrealized_pl = sum((p.unrealized_pl for p in realtime_positions), Decimal("0"))
    total_unrealized_pl_pct = (
        (total_unrealized_pl / total_investment) * Decimal("100") if total_investment > 0 else None
    )

    # Update P&L metrics
    # Note: Using total unrealized from realtime calculation, not database values
    total_realized_pl = sum((pos.realized_pl for pos in db_positions), Decimal("0"))
    pnl_dollars.labels(type="unrealized").set(float(total_unrealized_pl))
    pnl_dollars.labels(type="realized").set(float(total_realized_pl))
    pnl_dollars.labels(type="total").set(float(total_unrealized_pl + total_realized_pl))

    return RealtimePnLResponse(
        positions=realtime_positions,
        total_positions=len(realtime_positions),
        total_unrealized_pl=total_unrealized_pl,
        total_unrealized_pl_pct=total_unrealized_pl_pct,
        realtime_prices_available=realtime_count,
        timestamp=datetime.now(UTC),
    )


# =============================================================================
# Account Endpoints
# =============================================================================


@router.get("/api/v1/account", response_model=AccountInfoResponse, tags=["Account"])
@require_permission(Permission.VIEW_PNL)
async def get_account_info(
    user: dict[str, Any] = Depends(build_user_context),
    ctx: AppContext = Depends(get_context),
    config: ExecutionGatewayConfig = Depends(get_config),
) -> AccountInfoResponse:
    """Return account info from Alpaca (buying power, cash, etc.).

    Args:
        user: User context from authentication (injected)
        ctx: Application context with all dependencies (injected)
        config: Application configuration (injected)

    Returns:
        AccountInfoResponse with account details
    """
    if config.dry_run or not ctx.alpaca:
        return AccountInfoResponse()

    account = await asyncio.to_thread(ctx.alpaca.get_account_info)
    if not account:
        return AccountInfoResponse()

    return AccountInfoResponse(**account)


# =============================================================================
# Market Price Endpoints
# =============================================================================


@router.get("/api/v1/market_prices", response_model=list[MarketPricePoint], tags=["MarketData"])
@require_permission(Permission.VIEW_PNL)
async def get_market_prices(
    user: dict[str, Any] = Depends(build_user_context),
    ctx: AppContext = Depends(get_context),
) -> list[MarketPricePoint]:
    """Return market price snapshots for current positions.

    Args:
        user: User context from authentication (injected)
        ctx: Application context with all dependencies (injected)

    Returns:
        List of MarketPricePoint with current prices
    """
    # user is the context dict from build_user_context, extract actual user object
    authorized_strategies = get_authorized_strategies(user.get("user"))
    if not authorized_strategies and not has_permission(
        user.get("user"), Permission.VIEW_ALL_STRATEGIES
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No strategy access")

    if has_permission(user.get("user"), Permission.VIEW_ALL_STRATEGIES):
        db_positions = ctx.db.get_all_positions()
    else:
        db_positions = ctx.db.get_positions_for_strategies(authorized_strategies)

    symbols = [pos.symbol for pos in db_positions]
    prices = batch_fetch_realtime_prices_from_redis(symbols, ctx.redis)

    points: list[MarketPricePoint] = []
    for symbol in symbols:
        price, ts = prices.get(symbol, (None, None))
        points.append(MarketPricePoint(symbol=symbol, mid=price, timestamp=ts))

    return points


# =============================================================================
# Legacy Factory Function (Deprecated)
# =============================================================================
# The create_positions_router factory function has been deprecated in favor of
# module-level router definition with Depends() pattern.
# This factory is kept temporarily for backward compatibility during Phase 2B
# transition. It will be removed after all routes are migrated.
#
# See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 2B for migration details.
