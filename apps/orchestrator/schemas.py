"""
Pydantic schemas for Orchestrator Service.

Defines request/response models for:
- Orchestration runs
- Signal-to-order mappings
- Run status and results
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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

    signals: list[Signal]
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
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: str = "day"


class OrderSubmission(BaseModel):
    """Response from Execution Gateway after order submission."""

    client_order_id: str
    status: str
    broker_order_id: str | None = None
    symbol: str
    side: str
    qty: int
    order_type: str
    limit_price: Decimal | None = None
    created_at: datetime
    message: str


# ==============================================================================
# Orchestration Models
# ==============================================================================


class OrchestrationRequest(BaseModel):
    """Request to run orchestration."""

    symbols: list[str] = Field(..., min_length=1, description="List of symbols to trade")
    as_of_date: str | None = Field(None, description="Date for signal generation (YYYY-MM-DD)")
    capital: Decimal | None = Field(None, description="Override capital amount")
    max_position_size: Decimal | None = Field(None, description="Override max position size")
    dry_run: bool | None = Field(None, description="Override DRY_RUN setting")


class SignalOrderMapping(BaseModel):
    """Mapping from signal to order."""

    # Signal info
    symbol: str
    predicted_return: float
    rank: int
    target_weight: float

    # Order info
    client_order_id: str | None = None
    order_qty: int | None = None
    order_side: str | None = None

    # Execution info
    broker_order_id: str | None = None
    order_status: str | None = None
    filled_qty: Decimal | None = None
    filled_avg_price: Decimal | None = None

    # Reason if order not created
    skip_reason: str | None = None


class OrchestrationResult(BaseModel):
    """Result of orchestration run."""

    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    status: str  # "running", "completed", "failed", "partial"
    strategy_id: str
    as_of_date: str

    # Input
    symbols: list[str]
    capital: Decimal

    # Signals
    num_signals: int
    signal_metadata: dict[str, Any] | None = None

    # Orders
    num_orders_submitted: int
    num_orders_accepted: int
    num_orders_rejected: int
    num_orders_filled: int | None = None

    # Signal-order mappings
    mappings: list[SignalOrderMapping]

    # Timing
    started_at: datetime
    completed_at: datetime | None = None
    duration_seconds: Decimal | None = None

    # Error tracking
    error_message: str | None = None


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
    completed_at: datetime | None = None
    duration_seconds: Decimal | None = None


class OrchestrationRunsResponse(BaseModel):
    """Response for listing orchestration runs."""

    runs: list[OrchestrationRunSummary]
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
    details: dict[str, Any] | None = None


class ConfigResponse(BaseModel):
    """
    Configuration verification response.

    Exposes critical safety flags and environment settings for automated
    verification in smoke tests and monitoring. Used to ensure paper trading
    mode is active in staging/CI environments.

    Examples:
        >>> config = ConfigResponse(
        ...     service="orchestrator",
        ...     version="0.1.0",
        ...     environment="staging",
        ...     dry_run=True,
        ...     alpaca_paper=True,
        ...     circuit_breaker_enabled=True,
        ...     timestamp=datetime.now(timezone.utc)
        ... )
        >>> assert config.dry_run is True  # Staging safety check
        >>> assert config.alpaca_paper is True
    """

    service: str = Field(..., description="Service name")
    version: str = Field(..., description="Service version")
    environment: str = Field(..., description="Environment (dev, staging, production)")
    dry_run: bool = Field(..., description="Dry-run mode enabled (no real orders)")
    alpaca_paper: bool = Field(..., description="Alpaca paper trading mode")
    circuit_breaker_enabled: bool = Field(..., description="Circuit breaker feature enabled")
    timestamp: datetime = Field(..., description="Response timestamp (UTC)")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "service": "orchestrator",
                    "version": "0.1.0",
                    "environment": "staging",
                    "dry_run": True,
                    "alpaca_paper": True,
                    "circuit_breaker_enabled": True,
                    "timestamp": "2025-10-22T10:30:00Z",
                }
            ]
        }
    }


# ==============================================================================
# Kill-Switch Schemas
# ==============================================================================


class KillSwitchEngageRequest(BaseModel):
    """Request to engage kill-switch (emergency halt)."""

    reason: str = Field(..., description="Human-readable reason for engagement")
    operator: str = Field(..., description="Operator ID/name (for audit trail)")
    details: dict[str, Any] | None = Field(None, description="Optional additional context")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "reason": "Market anomaly detected",
                    "operator": "ops_team",
                    "details": {"anomaly_type": "flash_crash", "severity": "high"},
                }
            ]
        }
    }


class KillSwitchDisengageRequest(BaseModel):
    """Request to disengage kill-switch (resume trading)."""

    operator: str = Field(..., description="Operator ID/name (for audit trail)")
    notes: str | None = Field(None, description="Optional notes about resolution")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "operator": "ops_team",
                    "notes": "Market conditions normalized, all systems healthy",
                }
            ]
        }
    }
