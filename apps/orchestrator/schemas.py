"""
Pydantic schemas for Orchestrator Service.

Defines request/response models for:
- Orchestration runs
- Signal-to-order mappings
- Run status and results
"""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Dict, Any
from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict


# ==============================================================================
# Signal Service Models (from T3)
# ==============================================================================

class Signal(BaseModel):
    """Trading signal from Signal Service."""
    symbol: str
    predicted_return: float
    rank: int
    target_weight: float


class SignalMetadata(BaseModel):
    """Metadata from Signal Service response."""
    as_of_date: str
    model_version: str
    strategy: str
    num_signals: int
    generated_at: str
    top_n: int
    bottom_n: int


class SignalServiceResponse(BaseModel):
    """Response from Signal Service /api/v1/signals/generate endpoint."""
    signals: List[Signal]
    metadata: SignalMetadata


# ==============================================================================
# Execution Gateway Models (from T4)
# ==============================================================================

class OrderRequest(BaseModel):
    """Order request for Execution Gateway."""
    symbol: str
    side: str  # "buy" or "sell"
    qty: int
    order_type: str  # "market", "limit", "stop"
    limit_price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    time_in_force: str = "day"


class OrderSubmission(BaseModel):
    """Response from Execution Gateway after order submission."""
    client_order_id: str
    status: str
    broker_order_id: Optional[str] = None
    symbol: str
    side: str
    qty: int
    order_type: str
    limit_price: Optional[Decimal] = None
    created_at: datetime
    message: str


# ==============================================================================
# Orchestration Models
# ==============================================================================

class OrchestrationRequest(BaseModel):
    """Request to run orchestration."""
    symbols: List[str] = Field(..., min_length=1, description="List of symbols to trade")
    as_of_date: Optional[str] = Field(None, description="Date for signal generation (YYYY-MM-DD)")
    capital: Optional[Decimal] = Field(None, description="Override capital amount")
    max_position_size: Optional[Decimal] = Field(None, description="Override max position size")
    dry_run: Optional[bool] = Field(None, description="Override DRY_RUN setting")


class SignalOrderMapping(BaseModel):
    """Mapping from signal to order."""
    # Signal info
    symbol: str
    predicted_return: float
    rank: int
    target_weight: float

    # Order info
    client_order_id: Optional[str] = None
    order_qty: Optional[int] = None
    order_side: Optional[str] = None

    # Execution info
    broker_order_id: Optional[str] = None
    order_status: Optional[str] = None
    filled_qty: Optional[Decimal] = None
    filled_avg_price: Optional[Decimal] = None

    # Reason if order not created
    skip_reason: Optional[str] = None


class OrchestrationResult(BaseModel):
    """Result of orchestration run."""
    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    status: str  # "running", "completed", "failed", "partial"
    strategy_id: str
    as_of_date: str

    # Input
    symbols: List[str]
    capital: Decimal

    # Signals
    num_signals: int
    signal_metadata: Optional[Dict[str, Any]] = None

    # Orders
    num_orders_submitted: int
    num_orders_accepted: int
    num_orders_rejected: int
    num_orders_filled: Optional[int] = None

    # Signal-order mappings
    mappings: List[SignalOrderMapping]

    # Timing
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[Decimal] = None

    # Error tracking
    error_message: Optional[str] = None


class OrchestrationRunSummary(BaseModel):
    """Summary of orchestration run for listing."""
    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    status: str
    strategy_id: str
    as_of_date: str
    num_signals: int
    num_orders_submitted: int
    num_orders_accepted: int
    num_orders_rejected: int
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[Decimal] = None


class OrchestrationRunsResponse(BaseModel):
    """Response for listing orchestration runs."""
    runs: List[OrchestrationRunSummary]
    total: int
    limit: int
    offset: int


class HealthResponse(BaseModel):
    """Health check response."""
    status: str  # "healthy", "degraded", "unhealthy"
    service: str
    version: str
    timestamp: datetime
    signal_service_url: str
    execution_gateway_url: str
    signal_service_healthy: bool
    execution_gateway_healthy: bool
    database_connected: bool
    details: Optional[Dict[str, Any]] = None
