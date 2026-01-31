"""TCA (Transaction Cost Analysis) routes for Execution Gateway (P6T8).

Provides endpoints for execution quality analysis:
- GET /api/v1/tca/analysis - TCA metrics summary for date range
- GET /api/v1/tca/analysis/{client_order_id} - TCA metrics for specific order
- GET /api/v1/tca/benchmarks - Benchmark comparison time series

Security:
- Requires VIEW_TCA permission
- Strategy-scoped access control

Design Pattern:
    - Router defined at module level
    - Dependencies injected via Depends()
    - Uses ExecutionQualityAnalyzer from libs/platform/analytics
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from apps.execution_gateway.api.dependencies import build_gateway_authenticator
from apps.execution_gateway.app_context import AppContext
from apps.execution_gateway.dependencies import get_context
from apps.execution_gateway.schemas.tca import (
    TCAAnalysisSummary,
    TCABenchmarkPoint,
    TCABenchmarkResponse,
    TCAOrderDetail,
    TCASummaryResponse,
)
from apps.execution_gateway.services.auth_helpers import build_user_context
from libs.core.common.api_auth_dependency import APIAuthConfig, AuthContext, api_auth
from libs.platform.web_console_auth.permissions import Permission, get_authorized_strategies

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tca", tags=["TCA"])

# TCA auth dependency - requires VIEW_TCA permission
tca_auth = api_auth(
    APIAuthConfig(
        action="view_tca",
        require_role=None,
        require_permission=Permission.VIEW_TCA,
    ),
    authenticator_getter=build_gateway_authenticator,
)


# =============================================================================
# Demo Data Generators
# =============================================================================
# NOTE: These generate placeholder data. Real implementation will use
# ExecutionQualityAnalyzer from libs/platform/analytics/execution_quality.py


def _generate_demo_summary(
    start_date: date,
    end_date: date,
    symbol: str | None = None,
    strategy_id: str | None = None,
) -> TCAAnalysisSummary:
    """Generate demo TCA summary data.

    TODO: Replace with real implementation using ExecutionQualityAnalyzer
    and fills from database.
    """
    # Generate realistic-looking demo metrics
    import random

    random.seed(hash((start_date, end_date, symbol or "", strategy_id or "")))

    num_days = (end_date - start_date).days + 1
    base_orders = max(10, num_days * 5)
    total_orders = random.randint(base_orders - 5, base_orders + 10)

    return TCAAnalysisSummary(
        start_date=start_date,
        end_date=end_date,
        computation_timestamp=datetime.now(UTC),
        total_orders=total_orders,
        total_fills=total_orders * random.randint(2, 5),
        total_notional=total_orders * random.uniform(50000, 200000),
        total_shares=total_orders * random.randint(500, 2000),
        avg_fill_rate=random.uniform(0.92, 0.99),
        avg_implementation_shortfall_bps=random.uniform(-5, 15),
        avg_price_shortfall_bps=random.uniform(-3, 8),
        avg_vwap_slippage_bps=random.uniform(-2, 5),
        avg_fee_cost_bps=random.uniform(0.5, 2.0),
        avg_opportunity_cost_bps=random.uniform(0, 3),
        avg_market_impact_bps=random.uniform(0, 5),
        avg_timing_cost_bps=random.uniform(0.5, 3),
        warnings=["Demo data - not real execution analysis"],
    )


def _generate_demo_orders(
    start_date: date,
    end_date: date,
    num_orders: int,
    symbol: str | None = None,
) -> list[TCAOrderDetail]:
    """Generate demo order TCA details.

    TODO: Replace with real implementation.
    """
    import random
    import uuid

    random.seed(hash((start_date, end_date, symbol or "")))

    symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA"]
    if symbol:
        symbols = [symbol]

    orders = []
    for _ in range(min(num_orders, 50)):  # Cap at 50 for demo
        order_date = start_date + timedelta(
            days=random.randint(0, (end_date - start_date).days)
        )
        sym = random.choice(symbols)
        side: Literal["buy", "sell"] = random.choice(["buy", "sell"])
        target_qty = random.randint(100, 5000)
        filled_qty = int(target_qty * random.uniform(0.85, 1.0))
        arrival = random.uniform(100, 500)
        slippage_pct = random.uniform(-0.005, 0.01)
        exec_price = arrival * (1 + slippage_pct) if side == "buy" else arrival * (1 - slippage_pct)

        orders.append(
            TCAOrderDetail(
                client_order_id=f"demo-{uuid.uuid4().hex[:12]}",
                symbol=sym,
                side=side,
                strategy_id="alpha_baseline",
                execution_date=order_date,
                arrival_price=round(arrival, 2),
                execution_price=round(exec_price, 2),
                vwap_benchmark=round(arrival * (1 + random.uniform(-0.002, 0.002)), 2),
                twap_benchmark=round(arrival * (1 + random.uniform(-0.003, 0.003)), 2),
                target_qty=target_qty,
                filled_qty=filled_qty,
                fill_rate=round(filled_qty / target_qty, 4),
                total_notional=round(exec_price * filled_qty, 2),
                implementation_shortfall_bps=round(slippage_pct * 10000, 2),
                price_shortfall_bps=round(slippage_pct * 10000 * 0.7, 2),
                vwap_slippage_bps=round(random.uniform(-3, 5), 2),
                fee_cost_bps=round(random.uniform(0.5, 1.5), 2),
                opportunity_cost_bps=round((1 - filled_qty / target_qty) * 10, 2),
                market_impact_bps=round(random.uniform(0, 3), 2),
                timing_cost_bps=round(random.uniform(0.5, 2), 2),
                num_fills=random.randint(1, 10),
                execution_duration_seconds=random.uniform(30, 600),
                total_fees=round(exec_price * filled_qty * 0.0001, 2),
                warnings=["Demo data"],
                vwap_coverage_pct=random.uniform(90, 100),
            )
        )

    return sorted(orders, key=lambda o: o.execution_date, reverse=True)


def _generate_demo_benchmarks(
    order: TCAOrderDetail,
    benchmark_type: Literal["vwap", "twap", "arrival"],
) -> list[TCABenchmarkPoint]:
    """Generate demo benchmark time series.

    TODO: Replace with real implementation.
    """
    import random

    random.seed(hash((order.client_order_id, benchmark_type)))

    points = []
    num_points = min(order.num_fills * 2, 20)
    base_time = datetime.combine(order.execution_date, datetime.min.time()).replace(
        hour=10, minute=0, tzinfo=UTC
    )

    cumulative_qty = 0
    cumulative_notional = 0.0

    for i in range(num_points):
        timestamp = base_time + timedelta(minutes=i * 5)
        fill_qty = order.filled_qty // num_points
        cumulative_qty += fill_qty

        fill_price = order.arrival_price * (1 + random.uniform(-0.002, 0.002))
        cumulative_notional += fill_price * fill_qty
        exec_price = cumulative_notional / cumulative_qty if cumulative_qty > 0 else fill_price

        benchmark = order.arrival_price
        if benchmark_type == "vwap":
            benchmark = order.vwap_benchmark * (1 + random.uniform(-0.001, 0.001))
        elif benchmark_type == "twap":
            benchmark = order.twap_benchmark * (1 + random.uniform(-0.001, 0.001))

        slippage = (exec_price - benchmark) / benchmark * 10000
        if order.side == "sell":
            slippage = -slippage

        points.append(
            TCABenchmarkPoint(
                timestamp=timestamp,
                execution_price=round(exec_price, 4),
                benchmark_price=round(benchmark, 4),
                benchmark_type=benchmark_type,
                slippage_bps=round(slippage, 2),
                cumulative_qty=cumulative_qty,
            )
        )

    return points


# =============================================================================
# Routes
# =============================================================================


@router.get(
    "/analysis",
    response_model=TCASummaryResponse,
    summary="Get TCA analysis summary",
    description="Returns aggregated TCA metrics for orders in the specified date range.",
)
async def get_tca_analysis(
    start_date: date = Query(..., description="Start date (inclusive)"),
    end_date: date = Query(..., description="End date (inclusive)"),
    symbol: str | None = Query(default=None, description="Filter by symbol"),
    strategy_id: str | None = Query(default=None, description="Filter by strategy"),
    side: Literal["buy", "sell"] | None = Query(default=None, description="Filter by side"),
    ctx: AppContext = Depends(get_context),
    user: dict[str, Any] = Depends(build_user_context),
    _auth: AuthContext = Depends(tca_auth),
) -> TCASummaryResponse:
    """Get TCA analysis summary for date range.

    Returns aggregated metrics including:
    - Implementation shortfall
    - VWAP/TWAP slippage
    - Fee costs
    - Opportunity costs
    - Market impact decomposition
    """
    # Validate date range
    if end_date < start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="end_date must be >= start_date",
        )

    max_days = 90
    if (end_date - start_date).days > max_days:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Date range cannot exceed {max_days} days",
        )

    # Validate strategy access
    user_obj = user.get("user")
    user_id = user.get("user_id", "unknown")
    authorized = get_authorized_strategies(user_obj)
    if strategy_id and strategy_id not in authorized:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Not authorized for strategy: {strategy_id}",
        )

    logger.info(
        "TCA analysis requested",
        extra={
            "user_id": user_id,
            "start_date": str(start_date),
            "end_date": str(end_date),
            "symbol": symbol,
            "strategy_id": strategy_id,
        },
    )

    # Generate demo data
    # TODO: Replace with real ExecutionQualityAnalyzer integration
    summary = _generate_demo_summary(start_date, end_date, symbol, strategy_id)
    orders = _generate_demo_orders(start_date, end_date, summary.total_orders, symbol)

    # Filter by side if specified
    if side:
        orders = [o for o in orders if o.side == side]

    return TCASummaryResponse(summary=summary, orders=orders)


@router.get(
    "/analysis/{client_order_id}",
    response_model=TCAOrderDetail,
    summary="Get TCA for specific order",
    description="Returns detailed TCA metrics for a specific order.",
)
async def get_order_tca(
    client_order_id: str,
    ctx: AppContext = Depends(get_context),
    user: dict[str, Any] = Depends(build_user_context),
    _auth: AuthContext = Depends(tca_auth),
) -> TCAOrderDetail:
    """Get TCA metrics for a specific order.

    Returns detailed cost decomposition including:
    - Arrival vs execution price
    - VWAP/TWAP benchmarks
    - Fill statistics
    - Market impact breakdown
    """
    user_id = user.get("user_id", "unknown")
    logger.info(
        "Order TCA requested",
        extra={
            "user_id": user_id,
            "client_order_id": client_order_id,
        },
    )

    # TODO: Fetch real order from database and compute TCA
    # For now, generate demo data
    today = date.today()
    orders = _generate_demo_orders(today - timedelta(days=30), today, 10)

    if orders:
        # Return first demo order with modified ID
        order = orders[0]
        return TCAOrderDetail(
            client_order_id=client_order_id,
            symbol=order.symbol,
            side=order.side,
            strategy_id=order.strategy_id,
            execution_date=order.execution_date,
            arrival_price=order.arrival_price,
            execution_price=order.execution_price,
            vwap_benchmark=order.vwap_benchmark,
            twap_benchmark=order.twap_benchmark,
            target_qty=order.target_qty,
            filled_qty=order.filled_qty,
            fill_rate=order.fill_rate,
            total_notional=order.total_notional,
            implementation_shortfall_bps=order.implementation_shortfall_bps,
            price_shortfall_bps=order.price_shortfall_bps,
            vwap_slippage_bps=order.vwap_slippage_bps,
            fee_cost_bps=order.fee_cost_bps,
            opportunity_cost_bps=order.opportunity_cost_bps,
            market_impact_bps=order.market_impact_bps,
            timing_cost_bps=order.timing_cost_bps,
            num_fills=order.num_fills,
            execution_duration_seconds=order.execution_duration_seconds,
            total_fees=order.total_fees,
            warnings=["Demo data - order ID not validated"],
            vwap_coverage_pct=order.vwap_coverage_pct,
        )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Order not found: {client_order_id}",
    )


@router.get(
    "/benchmarks",
    response_model=TCABenchmarkResponse,
    summary="Get benchmark comparison",
    description="Returns time series of execution vs benchmark for charting.",
)
async def get_benchmarks(
    client_order_id: str = Query(..., description="Order ID"),
    benchmark: Literal["vwap", "twap", "arrival"] = Query(
        default="vwap", description="Benchmark type"
    ),
    ctx: AppContext = Depends(get_context),
    user: dict[str, Any] = Depends(build_user_context),
    _auth: AuthContext = Depends(tca_auth),
) -> TCABenchmarkResponse:
    """Get benchmark comparison time series for charting.

    Returns execution price vs benchmark over the fill window,
    suitable for line chart visualization.
    """
    user_id = user.get("user_id", "unknown")
    logger.info(
        "TCA benchmarks requested",
        extra={
            "user_id": user_id,
            "client_order_id": client_order_id,
            "benchmark": benchmark,
        },
    )

    # TODO: Fetch real order and generate benchmarks
    # For now, generate demo data
    today = date.today()
    orders = _generate_demo_orders(today - timedelta(days=30), today, 10)

    if orders:
        order = orders[0]
        # Override client_order_id
        order = TCAOrderDetail(
            client_order_id=client_order_id,
            symbol=order.symbol,
            side=order.side,
            strategy_id=order.strategy_id,
            execution_date=order.execution_date,
            arrival_price=order.arrival_price,
            execution_price=order.execution_price,
            vwap_benchmark=order.vwap_benchmark,
            twap_benchmark=order.twap_benchmark,
            target_qty=order.target_qty,
            filled_qty=order.filled_qty,
            fill_rate=order.fill_rate,
            total_notional=order.total_notional,
            implementation_shortfall_bps=order.implementation_shortfall_bps,
            price_shortfall_bps=order.price_shortfall_bps,
            vwap_slippage_bps=order.vwap_slippage_bps,
            fee_cost_bps=order.fee_cost_bps,
            opportunity_cost_bps=order.opportunity_cost_bps,
            market_impact_bps=order.market_impact_bps,
            timing_cost_bps=order.timing_cost_bps,
            num_fills=order.num_fills,
            execution_duration_seconds=order.execution_duration_seconds,
            total_fees=order.total_fees,
            warnings=["Demo data"],
            vwap_coverage_pct=order.vwap_coverage_pct,
        )
        points = _generate_demo_benchmarks(order, benchmark)

        return TCABenchmarkResponse(
            client_order_id=client_order_id,
            symbol=order.symbol,
            side=order.side,
            benchmark_type=benchmark,
            points=points,
            summary=order,
        )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Order not found: {client_order_id}",
    )


__all__ = ["router"]
