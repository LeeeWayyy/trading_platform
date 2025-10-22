"""
Orchestrator Service FastAPI Application - T5 Implementation.

This is the main FastAPI application that coordinates Signal Service (T3)
and Execution Gateway (T4) to create a complete trading workflow.

Key Features:
- POST /api/v1/orchestration/run - Trigger orchestration workflow
- GET /api/v1/orchestration/runs - List orchestration runs
- GET /api/v1/orchestration/runs/{run_id} - Get run details
- GET /health - Health check

Environment Variables:
    SIGNAL_SERVICE_URL: URL of Signal Service (default: http://localhost:8001)
    EXECUTION_GATEWAY_URL: URL of Execution Gateway (default: http://localhost:8002)
    DATABASE_URL: PostgreSQL connection string
    CAPITAL: Total capital to allocate (default: 100000)
    MAX_POSITION_SIZE: Max position size per symbol (default: 20000)
    STRATEGY_ID: Strategy identifier (default: alpha_baseline)
    LOG_LEVEL: Logging level (default: INFO)

Usage:
    # Development
    $ uvicorn apps.orchestrator.main:app --reload --port 8003

    # Production
    $ uvicorn apps.orchestrator.main:app --host 0.0.0.0 --port 8003

See ADR-0006 for architecture decisions.
"""

import logging
import os
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, status
from prometheus_client import Counter, Gauge, Histogram, make_asgi_app

from apps.orchestrator import __version__
from apps.orchestrator.database import OrchestrationDatabaseClient
from apps.orchestrator.orchestrator import TradingOrchestrator
from apps.orchestrator.schemas import (
    ConfigResponse,
    HealthResponse,
    OrchestrationRequest,
    OrchestrationResult,
    OrchestrationRunsResponse,
)
from libs.redis_client import RedisClient, RedisConnectionError
from libs.risk_management import KillSwitch, KillSwitchEngaged, KillSwitchState

# ============================================================================
# Configuration
# ============================================================================

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Environment variables
SIGNAL_SERVICE_URL = os.getenv("SIGNAL_SERVICE_URL", "http://localhost:8001")
EXECUTION_GATEWAY_URL = os.getenv("EXECUTION_GATEWAY_URL", "http://localhost:8002")
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/trading_platform"
)
CAPITAL = Decimal(os.getenv("CAPITAL", "100000"))
MAX_POSITION_SIZE = Decimal(os.getenv("MAX_POSITION_SIZE", "20000"))
STRATEGY_ID = os.getenv("STRATEGY_ID", "alpha_baseline")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"
CIRCUIT_BREAKER_ENABLED = os.getenv("CIRCUIT_BREAKER_ENABLED", "true").lower() == "true"
ENVIRONMENT = os.getenv("ENVIRONMENT", "dev")

# Redis configuration (for kill-switch)
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

logger.info(f"Starting Orchestrator Service (version={__version__})")
logger.info(f"Signal Service: {SIGNAL_SERVICE_URL}")
logger.info(f"Execution Gateway: {EXECUTION_GATEWAY_URL}")
logger.info(f"Capital: ${CAPITAL}")
logger.info(f"Max Position Size: ${MAX_POSITION_SIZE}")

# ============================================================================
# Initialize Clients
# ============================================================================

# Database client
db_client = OrchestrationDatabaseClient(DATABASE_URL)

# Redis client (for kill-switch)
redis_client: RedisClient | None = None
try:
    redis_client = RedisClient(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
    )
    logger.info("Redis client initialized successfully")
except (Exception, RedisConnectionError) as e:
    logger.warning(
        f"Failed to initialize Redis client: {e}. Kill-switch checks will be skipped."
    )

# Kill-switch (operator-controlled emergency halt)
kill_switch: KillSwitch | None = None
if redis_client:
    try:
        kill_switch = KillSwitch(redis_client=redis_client)
        logger.info("Kill-switch initialized successfully")
    except Exception as e:
        logger.warning(f"Failed to initialize kill-switch: {e}. Kill-switch checks will be skipped.")
else:
    logger.warning("Kill-switch not initialized (Redis unavailable). Kill-switch checks will be skipped.")


# Orchestrator (initialized per request to support async context)
def create_orchestrator() -> TradingOrchestrator:
    """Create orchestrator instance."""
    return TradingOrchestrator(
        signal_service_url=SIGNAL_SERVICE_URL,
        execution_gateway_url=EXECUTION_GATEWAY_URL,
        capital=CAPITAL,
        max_position_size=MAX_POSITION_SIZE,
    )


# ============================================================================
# FastAPI Application
# ============================================================================

app = FastAPI(
    title="Orchestrator Service",
    description="Coordinates Signal Service and Execution Gateway for end-to-end trading",
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc",
)


# ============================================================================
# Prometheus Metrics
# ============================================================================

# Business metrics
orchestration_runs_total = Counter(
    "orchestrator_runs_total",
    "Total number of orchestration runs",
    ["status"],  # success, error
)

orchestration_duration = Histogram(
    "orchestrator_orchestration_duration_seconds",
    "Time taken to complete orchestration workflow",
    buckets=[1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0],
)

signals_received_total = Counter(
    "orchestrator_signals_received_total",
    "Total number of signals received from Signal Service",
)

orders_submitted_total = Counter(
    "orchestrator_orders_submitted_total",
    "Total number of orders submitted to Execution Gateway",
    ["status"],  # success, error
)

positions_adjusted_total = Counter(
    "orchestrator_positions_adjusted_total",
    "Total number of position adjustments made",
)

# Health metrics
database_connection_status = Gauge(
    "orchestrator_database_connection_status",
    "Database connection status (1=connected, 0=disconnected)",
)

signal_service_available = Gauge(
    "orchestrator_signal_service_available",
    "Signal Service availability (1=available, 0=unavailable)",
)

execution_gateway_available = Gauge(
    "orchestrator_execution_gateway_available",
    "Execution Gateway availability (1=available, 0=unavailable)",
)

# Set initial values
database_connection_status.set(1)  # Will be updated by health check
signal_service_available.set(0)  # Will be updated by health check
execution_gateway_available.set(0)  # Will be updated by health check

# Mount Prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


# ============================================================================
# Endpoints
# ============================================================================


@app.get("/", tags=["Root"])
async def root() -> dict[str, Any]:
    """Root endpoint."""
    return {
        "service": "orchestrator",
        "version": __version__,
        "status": "running",
        "signal_service": SIGNAL_SERVICE_URL,
        "execution_gateway": EXECUTION_GATEWAY_URL,
    }


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check() -> HealthResponse:
    """
    Health check endpoint.

    Returns service health status including:
    - Overall status (healthy, degraded, unhealthy)
    - Signal Service connection status
    - Execution Gateway connection status
    - Database connection status
    - Service version and configuration

    Returns:
        HealthResponse with service health details

    Examples:
        >>> import requests
        >>> response = requests.get("http://localhost:8003/health")
        >>> response.json()
        {
            "status": "healthy",
            "service": "orchestrator",
            "version": "0.1.0",
            "signal_service_url": "http://localhost:8001",
            "execution_gateway_url": "http://localhost:8002",
            "signal_service_healthy": true,
            "execution_gateway_healthy": true,
            "database_connected": true,
            "timestamp": "2024-10-17T16:30:00Z"
        }
    """
    # Check database connection
    db_connected = db_client.check_connection()

    # Check service connections
    orchestrator = create_orchestrator()
    try:
        signal_healthy = await orchestrator.signal_client.health_check()
        execution_healthy = await orchestrator.execution_client.health_check()
    finally:
        await orchestrator.close()

    # Update health metrics
    database_connection_status.set(1 if db_connected else 0)
    signal_service_available.set(1 if signal_healthy else 0)
    execution_gateway_available.set(1 if execution_healthy else 0)

    # Determine overall status
    if db_connected and signal_healthy and execution_healthy:
        overall_status = "healthy"
    elif db_connected:
        overall_status = "degraded"  # DB OK but services down
    else:
        overall_status = "unhealthy"  # DB down

    return HealthResponse(
        status=overall_status,
        service="orchestrator",
        version=__version__,
        timestamp=datetime.now(),
        signal_service_url=SIGNAL_SERVICE_URL,
        execution_gateway_url=EXECUTION_GATEWAY_URL,
        signal_service_healthy=signal_healthy,
        execution_gateway_healthy=execution_healthy,
        database_connected=db_connected,
        details={
            "capital": float(CAPITAL),
            "max_position_size": float(MAX_POSITION_SIZE),
            "strategy_id": STRATEGY_ID,
        },
    )


@app.get("/api/v1/config", response_model=ConfigResponse, tags=["Configuration"])
async def get_config() -> ConfigResponse:
    """
    Get service configuration for verification.

    Returns safety flags and environment settings for automated
    verification in smoke tests and monitoring.

    Returns:
        ConfigResponse with service configuration details
    """
    return ConfigResponse(
        service="orchestrator",
        version=__version__,
        environment=ENVIRONMENT,
        dry_run=DRY_RUN,
        alpaca_paper=ALPACA_PAPER,
        circuit_breaker_enabled=CIRCUIT_BREAKER_ENABLED,
        timestamp=datetime.now(),
    )


@app.post("/api/v1/kill-switch/engage", tags=["Kill-Switch"])
async def engage_kill_switch(
    reason: str,
    operator: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Engage kill-switch (emergency trading halt).

    CRITICAL: This operator-controlled action immediately blocks ALL trading
    activities across all services until manually disengaged.

    Args:
        reason: Human-readable reason for engagement (required)
        operator: Operator ID/name who engaged kill-switch (required for audit)
        details: Optional additional context

    Returns:
        Kill-switch status after engagement

    Raises:
        HTTPException 503: Redis unavailable
        HTTPException 400: Kill-switch already engaged
    """
    if not kill_switch:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Kill-switch unavailable (Redis not initialized)",
        )

    try:
        kill_switch.engage(reason=reason, operator=operator, details=details)
        return kill_switch.get_status()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@app.post("/api/v1/kill-switch/disengage", tags=["Kill-Switch"])
async def disengage_kill_switch(
    operator: str,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Disengage kill-switch (resume trading).

    This operator action re-enables trading after kill-switch was engaged.

    Args:
        operator: Operator ID/name who disengaged kill-switch (required for audit)
        notes: Optional notes about resolution

    Returns:
        Kill-switch status after disengagement

    Raises:
        HTTPException 503: Redis unavailable
        HTTPException 400: Kill-switch not currently engaged
    """
    if not kill_switch:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Kill-switch unavailable (Redis not initialized)",
        )

    try:
        kill_switch.disengage(operator=operator, notes=notes)
        return kill_switch.get_status()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@app.get("/api/v1/kill-switch/status", tags=["Kill-Switch"])
async def get_kill_switch_status() -> dict[str, Any]:
    """
    Get kill-switch status.

    Returns current state, last engagement/disengagement details, and history.

    Returns:
        Kill-switch status with state, timestamps, and operator info

    Raises:
        HTTPException 503: Redis unavailable
    """
    if not kill_switch:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Kill-switch unavailable (Redis not initialized)",
        )

    return kill_switch.get_status()


@app.post(
    "/api/v1/orchestration/run",
    response_model=OrchestrationResult,
    tags=["Orchestration"],
    status_code=status.HTTP_200_OK,
)
async def run_orchestration(request: OrchestrationRequest) -> OrchestrationResult:
    """
    Trigger orchestration workflow.

    This endpoint executes the complete trading workflow:
    1. Fetch signals from Signal Service (T3)
    2. Map signals to orders with position sizing
    3. Submit orders to Execution Gateway (T4)
    4. Track execution and persist results

    Args:
        request: OrchestrationRequest with symbols and optional parameters

    Returns:
        OrchestrationResult with complete run details

    Raises:
        HTTPException 400: Invalid request
        HTTPException 503: Dependent service unavailable
        HTTPException 500: Internal error

    Examples:
        >>> import requests
        >>> response = requests.post(
        ...     "http://localhost:8003/api/v1/orchestration/run",
        ...     json={
        ...         "symbols": ["AAPL", "MSFT", "GOOGL"],
        ...         "as_of_date": "2024-12-31"
        ...     }
        ... )
        >>> result = response.json()
        >>> print(result["status"])
        'completed'
        >>> print(result["num_orders_submitted"])
        2
    """
    # Start timing for metrics
    run_started = time.time()
    run_status = "success"

    try:
        logger.info(
            f"Orchestration run requested: {len(request.symbols)} symbols",
            extra={
                "num_symbols": len(request.symbols),
                "as_of_date": request.as_of_date,
                "capital": float(request.capital) if request.capital else float(CAPITAL),
            },
        )

        # Check kill-switch (operator-controlled emergency halt)
        if kill_switch and kill_switch.is_engaged():
            status_info = kill_switch.get_status()
            logger.error(
                f"🔴 Orchestration blocked by KILL-SWITCH",
                extra={
                    "kill_switch_engaged": True,
                    "engaged_by": status_info.get("engaged_by"),
                    "engagement_reason": status_info.get("engagement_reason"),
                },
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "Kill-switch engaged",
                    "message": "All trading halted by operator",
                    "engaged_by": status_info.get("engaged_by"),
                    "reason": status_info.get("engagement_reason"),
                    "engaged_at": status_info.get("engaged_at"),
                },
            )

        # Parse as_of_date
        as_of_date_parsed = None
        if request.as_of_date:
            try:
                as_of_date_parsed = date.fromisoformat(request.as_of_date)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid date format: {request.as_of_date}. Use YYYY-MM-DD.",
                ) from None

        # Determine capital and max position size
        capital = request.capital if request.capital else CAPITAL
        max_position_size = (
            request.max_position_size if request.max_position_size else MAX_POSITION_SIZE
        )

        # Create orchestrator
        orchestrator = TradingOrchestrator(
            signal_service_url=SIGNAL_SERVICE_URL,
            execution_gateway_url=EXECUTION_GATEWAY_URL,
            capital=capital,
            max_position_size=max_position_size,
        )

        try:
            # Run orchestration
            result = await orchestrator.run(
                symbols=request.symbols, strategy_id=STRATEGY_ID, as_of_date=as_of_date_parsed
            )

            # Track metrics
            signals_received_total.inc(result.num_signals)
            orders_submitted_total.labels(status="success").inc(result.num_orders_accepted)
            if result.num_orders_rejected > 0:
                orders_submitted_total.labels(status="error").inc(result.num_orders_rejected)
            positions_adjusted_total.inc(result.num_orders_accepted)

            # Persist to database
            db_client.create_run(result)

            logger.info(
                f"Orchestration run completed: {result.run_id}",
                extra={
                    "run_id": str(result.run_id),
                    "status": result.status,
                    "num_signals": result.num_signals,
                    "num_orders_submitted": result.num_orders_submitted,
                    "num_orders_accepted": result.num_orders_accepted,
                    "duration_seconds": (
                        float(result.duration_seconds) if result.duration_seconds else None
                    ),
                },
            )

            return result

        finally:
            await orchestrator.close()

    except HTTPException:
        run_status = "error"
        raise
    except Exception:
        run_status = "error"
        logger.exception("Unhandled failure in run_orchestration")
        raise
    finally:
        # Always record metrics
        elapsed = time.time() - run_started
        orchestration_runs_total.labels(status=run_status).inc()
        orchestration_duration.observe(elapsed)


@app.get(
    "/api/v1/orchestration/runs", response_model=OrchestrationRunsResponse, tags=["Orchestration"]
)
async def list_runs(
    limit: int = Query(50, ge=1, le=100, description="Maximum number of runs to return"),
    offset: int = Query(0, ge=0, description="Number of runs to skip"),
    strategy_id: str | None = Query(None, description="Filter by strategy ID"),
    status: str | None = Query(None, description="Filter by status"),
) -> OrchestrationRunsResponse:
    """
    List orchestration runs.

    Args:
        limit: Maximum number of runs to return (1-100, default 50)
        offset: Number of runs to skip (default 0)
        strategy_id: Filter by strategy ID (optional)
        status: Filter by status (optional)

    Returns:
        OrchestrationRunsResponse with list of runs and pagination info

    Examples:
        >>> import requests
        >>> response = requests.get(
        ...     "http://localhost:8003/api/v1/orchestration/runs",
        ...     params={"limit": 10, "status": "completed"}
        ... )
        >>> data = response.json()
        >>> print(len(data["runs"]))
        10
    """
    runs = db_client.list_runs(limit=limit, offset=offset, strategy_id=strategy_id, status=status)

    # Get total count (simplified - just return number of runs fetched)
    total = len(runs)

    return OrchestrationRunsResponse(runs=runs, total=total, limit=limit, offset=offset)


@app.get(
    "/api/v1/orchestration/runs/{run_id}",
    response_model=OrchestrationResult,
    tags=["Orchestration"],
)
async def get_run(run_id: UUID) -> OrchestrationResult:
    """
    Get orchestration run details.

    Args:
        run_id: Orchestration run UUID

    Returns:
        OrchestrationResult with complete run details

    Raises:
        HTTPException 404: Run not found

    Examples:
        >>> import requests
        >>> import uuid
        >>> run_id = uuid.UUID("...")
        >>> response = requests.get(
        ...     f"http://localhost:8003/api/v1/orchestration/runs/{run_id}"
        ... )
        >>> result = response.json()
        >>> print(result["status"])
        'completed'
    """
    # Get run summary
    run_summary = db_client.get_run(run_id)

    if not run_summary:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Run not found: {run_id}"
        )

    # Get mappings
    mappings = db_client.get_mappings(run_id)

    # Build full result
    return OrchestrationResult(
        run_id=run_summary.run_id,
        status=run_summary.status,
        strategy_id=run_summary.strategy_id,
        as_of_date=run_summary.as_of_date,
        symbols=[],  # Not stored in summary
        capital=Decimal("0"),  # Not stored in summary
        num_signals=run_summary.num_signals,
        num_orders_submitted=run_summary.num_orders_submitted,
        num_orders_accepted=run_summary.num_orders_accepted,
        num_orders_rejected=run_summary.num_orders_rejected,
        mappings=mappings,
        started_at=run_summary.started_at,
        completed_at=run_summary.completed_at,
        duration_seconds=run_summary.duration_seconds,
    )


# ============================================================================
# Startup / Shutdown
# ============================================================================


@app.on_event("startup")
async def startup_event() -> None:
    """Application startup."""
    logger.info("Orchestrator Service started")
    logger.info(f"Signal Service URL: {SIGNAL_SERVICE_URL}")
    logger.info(f"Execution Gateway URL: {EXECUTION_GATEWAY_URL}")
    logger.info(f"Strategy ID: {STRATEGY_ID}")

    # Check database connection
    if not db_client.check_connection():
        logger.error("Database connection failed at startup!")
    else:
        logger.info("Database connection OK")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    """Application shutdown."""
    logger.info("Orchestrator Service shutting down")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "apps.orchestrator.main:app",
        host="0.0.0.0",
        port=8003,
        reload=True,
        log_level=LOG_LEVEL.lower(),
    )
