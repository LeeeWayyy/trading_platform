"""TCA (Transaction Cost Analysis) API schemas for P6T8.

Provides request/response models for execution quality endpoints.

Models:
    - TCAAnalysisRequest: Filter parameters for TCA analysis
    - TCAAnalysisSummary: Aggregated TCA metrics
    - TCAOrderDetail: Per-order TCA breakdown
    - TCABenchmarkPoint: Time series point for benchmark comparison
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class TCAAnalysisRequest(BaseModel):
    """Request parameters for TCA analysis."""

    start_date: date = Field(..., description="Start date for analysis (inclusive)")
    end_date: date = Field(..., description="End date for analysis (inclusive)")
    symbol: str | None = Field(default=None, description="Filter by symbol")
    strategy_id: str | None = Field(default=None, description="Filter by strategy")
    side: Literal["buy", "sell"] | None = Field(default=None, description="Filter by side")


class TCAMetricValue(BaseModel):
    """A single TCA metric with value and metadata."""

    value: float = Field(..., description="Metric value in basis points")
    label: str = Field(..., description="Human-readable label")
    is_good: bool = Field(
        default=True, description="True if lower is better (cost), False if higher is better"
    )
    description: str | None = Field(default=None, description="Detailed explanation")


class TCAAnalysisSummary(BaseModel):
    """Aggregated TCA metrics summary for a date range."""

    # Time range
    start_date: date
    end_date: date
    computation_timestamp: datetime = Field(..., description="When analysis was computed")

    # Volume statistics
    total_orders: int = Field(..., description="Total orders analyzed", ge=0)
    total_fills: int = Field(..., description="Total fills analyzed", ge=0)
    total_notional: float = Field(..., description="Total notional value traded", ge=0)
    total_shares: int = Field(..., description="Total shares traded", ge=0)

    # Fill rate
    avg_fill_rate: float = Field(
        ..., description="Average fill rate (0-1)", ge=0, le=1
    )

    # Cost metrics (in basis points)
    avg_implementation_shortfall_bps: float = Field(
        ..., description="Average implementation shortfall (total cost)"
    )
    avg_price_shortfall_bps: float = Field(
        ..., description="Average price slippage component"
    )
    avg_vwap_slippage_bps: float = Field(
        ..., description="Average VWAP benchmark slippage"
    )
    avg_fee_cost_bps: float = Field(
        ..., description="Average fee cost component"
    )
    avg_opportunity_cost_bps: float = Field(
        ..., description="Average opportunity cost (unfilled qty)"
    )

    # Market impact decomposition
    avg_market_impact_bps: float = Field(
        ..., description="Average permanent market impact"
    )
    avg_timing_cost_bps: float = Field(
        ..., description="Average timing/spread cost"
    )

    # Data quality
    warnings: list[str] = Field(
        default_factory=list, description="Data quality warnings"
    )


class TCAOrderDetail(BaseModel):
    """TCA metrics for a single order."""

    # Order identification
    client_order_id: str = Field(..., description="Client order ID")
    symbol: str
    side: Literal["buy", "sell"]
    strategy_id: str | None = None
    execution_date: date

    # Prices
    arrival_price: float = Field(..., description="Price at decision time")
    execution_price: float = Field(..., description="VWAP of fills")
    vwap_benchmark: float = Field(..., description="Market VWAP over execution window")
    twap_benchmark: float = Field(..., description="Market TWAP over execution window")

    # Volume
    target_qty: int = Field(..., description="Target quantity", ge=0)
    filled_qty: int = Field(..., description="Filled quantity", ge=0)
    fill_rate: float = Field(..., description="Fill rate (0-1)", ge=0, le=1)
    total_notional: float = Field(..., description="Total notional value", ge=0)

    # Cost decomposition (basis points)
    implementation_shortfall_bps: float = Field(..., description="Total cost")
    price_shortfall_bps: float = Field(..., description="Price slippage")
    vwap_slippage_bps: float = Field(..., description="VWAP slippage")
    fee_cost_bps: float = Field(..., description="Fee component")
    opportunity_cost_bps: float = Field(..., description="Unfilled cost")
    market_impact_bps: float = Field(..., description="Permanent impact")
    timing_cost_bps: float = Field(..., description="Timing/spread cost")

    # Fill statistics
    num_fills: int = Field(..., description="Number of fills", ge=0)
    execution_duration_seconds: float = Field(
        ..., description="Time from first to last fill", ge=0
    )
    total_fees: float = Field(..., description="Total fees paid", ge=0)

    # Data quality
    warnings: list[str] = Field(default_factory=list)
    vwap_coverage_pct: float = Field(
        default=100.0, description="% of window with benchmark data", ge=0, le=100
    )


class TCABenchmarkPoint(BaseModel):
    """Single point in benchmark comparison time series."""

    timestamp: datetime
    execution_price: float = Field(..., description="Cumulative VWAP of fills")
    benchmark_price: float = Field(..., description="Market benchmark at this point")
    benchmark_type: Literal["vwap", "twap", "arrival"] = Field(
        default="vwap", description="Benchmark type"
    )
    slippage_bps: float = Field(..., description="Slippage vs benchmark")
    cumulative_qty: int = Field(..., description="Cumulative quantity filled", ge=0)


class TCABenchmarkResponse(BaseModel):
    """Time series of execution vs benchmark for charting."""

    client_order_id: str
    symbol: str
    side: Literal["buy", "sell"]
    benchmark_type: Literal["vwap", "twap", "arrival"]
    points: list[TCABenchmarkPoint] = Field(
        default_factory=list, description="Time series points"
    )
    summary: TCAOrderDetail | None = Field(
        default=None, description="Order TCA summary"
    )


class TCASummaryResponse(BaseModel):
    """Response for TCA analysis summary endpoint."""

    summary: TCAAnalysisSummary
    orders: list[TCAOrderDetail] = Field(
        default_factory=list, description="Individual order details"
    )


__all__ = [
    "TCAAnalysisRequest",
    "TCAAnalysisSummary",
    "TCABenchmarkPoint",
    "TCABenchmarkResponse",
    "TCAMetricValue",
    "TCAOrderDetail",
    "TCASummaryResponse",
]
