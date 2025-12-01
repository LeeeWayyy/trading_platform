"""
Execution Gateway FastAPI Application - T4 Implementation.

This is the main FastAPI application for order execution with:
- POST /api/v1/orders - Submit orders with idempotency
- POST /api/v1/webhooks/orders - Receive order updates from Alpaca
- GET /api/v1/orders/{client_order_id} - Query order status
- GET /api/v1/positions - Get current positions
- GET /health - Health check

Key Features:
- Idempotent order submission via deterministic client_order_id
- DRY_RUN mode for safe testing (controlled by environment variable)
- Real-time order status updates via webhooks
- Position tracking from order fills
- Automatic retry with exponential backoff

Environment Variables:
    ALPACA_API_KEY_ID: Alpaca API key
    ALPACA_API_SECRET_KEY: Alpaca secret key
    ALPACA_BASE_URL: Alpaca API URL (paper or live)
    DATABASE_URL: PostgreSQL connection string
    STRATEGY_ID: Strategy identifier (e.g., "alpha_baseline")
    DRY_RUN: Enable dry run mode (true/false, default: true)
    LOG_LEVEL: Logging level (default: INFO)

Usage:
    # Development (DRY_RUN=true)
    $ uvicorn apps.execution_gateway.main:app --reload --port 8002

    # Production (DRY_RUN=false)
    $ uvicorn apps.execution_gateway.main:app --host 0.0.0.0 --port 8002

See ADR-0014 for architecture decisions.
"""

import asyncio
import json
import logging
import os
import threading
import time
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Gauge, Histogram, make_asgi_app
from psycopg.errors import UniqueViolation
from pydantic import ValidationError
from redis.exceptions import RedisError

from apps.execution_gateway import __version__
from apps.execution_gateway.alpaca_client import (
    AlpacaConnectionError,
    AlpacaExecutor,
    AlpacaRejectionError,
    AlpacaValidationError,
)
from apps.execution_gateway.database import DatabaseClient
from apps.execution_gateway.order_id_generator import (
    generate_client_order_id,
    reconstruct_order_params_hash,
)
from apps.execution_gateway.order_slicer import TWAPSlicer
from apps.execution_gateway.schemas import (
    ConfigResponse,
    ErrorResponse,
    HealthResponse,
    KillSwitchDisengageRequest,
    KillSwitchEngageRequest,
    OrderDetail,
    OrderRequest,
    OrderResponse,
    Position,
    PositionsResponse,
    RealtimePnLResponse,
    RealtimePositionPnL,
    SliceDetail,
    SlicingPlan,
    SlicingRequest,
)
from apps.execution_gateway.slice_scheduler import SliceScheduler
from apps.execution_gateway.webhook_security import (
    extract_signature_from_header,
    verify_webhook_signature,
)
from libs.redis_client import RedisClient, RedisConnectionError, RedisKeys
from libs.risk_management import CircuitBreaker, KillSwitch

# ============================================================================
# Configuration
# ============================================================================

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Legacy TWAP slicer interval (seconds). Legacy plans scheduled slices once per minute
# and did not persist the interval, so backward-compatibility fallbacks must only apply
# when callers request the same default pacing.
LEGACY_TWAP_INTERVAL_SECONDS = 60

# Environment variables
ALPACA_API_KEY_ID = os.getenv("ALPACA_API_KEY_ID", "")
ALPACA_API_SECRET_KEY = os.getenv("ALPACA_API_SECRET_KEY", "")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/trading_platform"
)
STRATEGY_ID = os.getenv("STRATEGY_ID", "alpha_baseline")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"
CIRCUIT_BREAKER_ENABLED = os.getenv("CIRCUIT_BREAKER_ENABLED", "true").lower() == "true"
ENVIRONMENT = os.getenv("ENVIRONMENT", "dev")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")  # Secret for webhook signature verification

# C3 Fix: Validate webhook secret is set in production environments
# In non-dev/test environments with DRY_RUN=false, webhook secret is MANDATORY
# This prevents webhook spoofing attacks in production
# Strip whitespace to prevent whitespace-only secrets (which would be useless)
WEBHOOK_SECRET = WEBHOOK_SECRET.strip()
if not WEBHOOK_SECRET and ENVIRONMENT not in ("dev", "test") and not DRY_RUN:
    raise RuntimeError(
        "WEBHOOK_SECRET must be set for production/staging environments. "
        "Set WEBHOOK_SECRET environment variable or use DRY_RUN=true for testing."
    )

# Redis configuration (for real-time price lookups)
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

logger.info(f"Starting Execution Gateway (version={__version__}, dry_run={DRY_RUN})")

# ============================================================================
# Initialize Clients
# ============================================================================

# Database client
db_client = DatabaseClient(DATABASE_URL)

# Redis client (for real-time price lookups from Market Data Service)
redis_client: RedisClient | None = None
try:
    redis_client = RedisClient(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
    )
    logger.info("Redis client initialized successfully")
except (RedisError, RedisConnectionError) as e:
    # Catch both redis-py errors (RedisError) and our custom RedisConnectionError
    # Service should start even if Redis is misconfigured or unavailable
    # RedisConnectionError is raised by RedisClient when initial ping() fails
    logger.warning(
        f"Failed to initialize Redis client: {e}. Real-time P&L will fall back to database prices."
    )

# Kill-switch (operator-controlled emergency halt)
kill_switch: KillSwitch | None = None

# H3 Fix: Thread-safe kill-switch unavailable flag
# Use threading.Lock to protect read/write operations on global state
_kill_switch_lock = threading.Lock()
_kill_switch_unavailable = False  # Track if kill-switch initialization failed (fail closed)


def is_kill_switch_unavailable() -> bool:
    """Thread-safe check if kill-switch is unavailable."""
    with _kill_switch_lock:
        return _kill_switch_unavailable


def set_kill_switch_unavailable(value: bool) -> None:
    """Thread-safe set kill-switch unavailable state."""
    global _kill_switch_unavailable
    with _kill_switch_lock:
        _kill_switch_unavailable = value


if redis_client:
    try:
        kill_switch = KillSwitch(redis_client=redis_client)
        logger.info("Kill-switch initialized successfully")
    except Exception as e:
        logger.error(
            f"Failed to initialize kill-switch: {e}. FAILING CLOSED - all trading blocked until Redis available."
        )
        set_kill_switch_unavailable(True)
else:
    logger.error(
        "Kill-switch not initialized (Redis unavailable). FAILING CLOSED - all trading blocked until Redis available."
    )
    set_kill_switch_unavailable(True)

# Alpaca client (only if not in dry run mode and credentials provided)
alpaca_client: AlpacaExecutor | None = None
if not DRY_RUN:
    if not ALPACA_API_KEY_ID or not ALPACA_API_SECRET_KEY:
        logger.warning(
            "DRY_RUN=false but Alpaca credentials not provided. "
            "Set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY environment variables."
        )
    else:
        try:
            alpaca_client = AlpacaExecutor(
                api_key=ALPACA_API_KEY_ID,
                secret_key=ALPACA_API_SECRET_KEY,
                base_url=ALPACA_BASE_URL,
                paper=True,
            )
            logger.info("Alpaca client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Alpaca client: {e}")

# Circuit Breaker (for post-trade risk monitoring)
circuit_breaker: CircuitBreaker | None = None
if redis_client:
    try:
        circuit_breaker = CircuitBreaker(redis_client=redis_client)
        logger.info("Circuit breaker initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize circuit breaker: {e}")
else:
    logger.warning("Circuit breaker not initialized (Redis unavailable)")

# TWAP Order Slicer (stateless, no dependencies)
twap_slicer = TWAPSlicer()
logger.info("TWAP slicer initialized successfully")

# Slice Scheduler (for time-based TWAP slice execution)
slice_scheduler: SliceScheduler | None = None
if kill_switch and circuit_breaker:
    # Note: alpaca_client can be None in DRY_RUN mode - scheduler logs dry-run slices without broker submission
    try:
        slice_scheduler = SliceScheduler(
            kill_switch=kill_switch,
            breaker=circuit_breaker,
            db_client=db_client,
            executor=alpaca_client,  # Can be None in DRY_RUN mode
        )
        logger.info("Slice scheduler initialized (not started yet)")
    except Exception as e:
        logger.error(f"Failed to initialize slice scheduler: {e}")
else:
    logger.warning("Slice scheduler not initialized (kill-switch or circuit-breaker unavailable)")

# ============================================================================
# FastAPI Application
# ============================================================================

app = FastAPI(
    title="Execution Gateway",
    description="Order execution service with idempotent submission and DRY_RUN mode",
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc",
)


# ============================================================================
# Prometheus Metrics
# ============================================================================

# Business Metrics
orders_total = Counter(
    "execution_gateway_orders_total",
    "Total number of orders submitted",
    ["symbol", "side", "status"],  # status: success, failed, rejected, blocked
)

order_placement_duration = Histogram(
    "execution_gateway_order_placement_duration_seconds",
    "Time taken to place an order",
    ["symbol", "side"],
)

positions_current = Gauge(
    "execution_gateway_positions_current",
    "Current open positions by symbol",
    ["symbol"],
)

pnl_dollars = Gauge(
    "execution_gateway_pnl_dollars",
    "P&L in dollars",
    ["type"],  # Label values: realized, unrealized, total
)

# Service Health Metrics
database_connection_status = Gauge(
    "execution_gateway_database_connection_status",
    "Database connection status (1=up, 0=down)",
)

redis_connection_status = Gauge(
    "execution_gateway_redis_connection_status",
    "Redis connection status (1=up, 0=down)",
)

alpaca_connection_status = Gauge(
    "execution_gateway_alpaca_connection_status",
    "Alpaca connection status (1=up, 0=down)",
)

alpaca_api_requests_total = Counter(
    "execution_gateway_alpaca_api_requests_total",
    "Total Alpaca API requests",
    ["operation", "status"],  # operation: submit_order, check_connection; status: success, error
)

webhook_received_total = Counter(
    "execution_gateway_webhook_received_total",
    "Total webhooks received",
    ["event_type"],
)

dry_run_mode = Gauge(
    "execution_gateway_dry_run_mode",
    "DRY_RUN mode status (1=enabled, 0=disabled)",
)

# Set initial metric values
dry_run_mode.set(1 if DRY_RUN else 0)
database_connection_status.set(0)  # Will be updated by health check
redis_connection_status.set(0)  # Will be updated by health check
alpaca_connection_status.set(0)  # Will be updated by health check

# Mount Prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

# Track symbols we've set position metrics for (to reset when positions close)
_tracked_position_symbols: set[str] = set()
_position_metrics_lock = asyncio.Lock()


# ============================================================================
# Helper Functions
# ============================================================================


def _record_order_metrics(
    order: "OrderRequest",
    start_time: float,
    status: Literal["success", "rejected", "failed", "blocked"],
) -> None:
    """
    Record Prometheus metrics for order placement.

    Args:
        order: The order request that was submitted
        start_time: Time when order processing started (from time.time())
        status: Order outcome (success, rejected, failed, or blocked)

    Notes:
        This helper reduces code duplication across different order placement paths.
        Increments orders_total counter and records order_placement_duration histogram.
        "blocked" status is used for orders rejected by safety mechanisms (circuit breaker).
    """
    duration = time.time() - start_time
    orders_total.labels(symbol=order.symbol, side=order.side, status=status).inc()
    order_placement_duration.labels(symbol=order.symbol, side=order.side).observe(duration)


def _handle_idempotency_race(
    client_order_id: str,
    db_client: "DatabaseClient",
) -> "OrderResponse":
    """
    Handle idempotency race condition by returning existing order.

    When UniqueViolation is caught during order creation, this function
    retrieves the existing order and returns an idempotent response.

    Args:
        client_order_id: The client order ID that caused the race condition
        db_client: Database client for fetching existing order

    Returns:
        OrderResponse for the existing order

    Raises:
        HTTPException: If order not found after UniqueViolation (should never happen)
    """
    logger.info(
        f"Concurrent order submission detected (UniqueViolation): {client_order_id}",
        extra={"client_order_id": client_order_id},
    )
    existing_order = db_client.get_order_by_client_id(client_order_id)
    if existing_order:
        return OrderResponse(
            client_order_id=client_order_id,
            status=existing_order.status,
            broker_order_id=existing_order.broker_order_id,
            symbol=existing_order.symbol,
            side=existing_order.side,
            qty=existing_order.qty,
            order_type=existing_order.order_type,
            limit_price=existing_order.limit_price,
            created_at=existing_order.created_at,
            message="Order already submitted (race condition resolved)",
        )
    # Should never happen: UniqueViolation means order exists
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Database inconsistency: order not found after UniqueViolation",
    )


# ============================================================================
# Exception Handlers
# ============================================================================


@app.exception_handler(ValidationError)
async def validation_exception_handler(request: Request, exc: ValidationError) -> JSONResponse:
    """Handle Pydantic validation errors."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=ErrorResponse(
            error="Validation error", detail=str(exc), timestamp=datetime.now(UTC)
        ).model_dump(mode="json"),
    )


@app.exception_handler(AlpacaValidationError)
async def alpaca_validation_handler(request: Request, exc: AlpacaValidationError) -> JSONResponse:
    """Handle Alpaca validation errors."""
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=ErrorResponse(
            error="Order validation failed", detail=str(exc), timestamp=datetime.now(UTC)
        ).model_dump(mode="json"),
    )


@app.exception_handler(AlpacaRejectionError)
async def alpaca_rejection_handler(request: Request, exc: AlpacaRejectionError) -> JSONResponse:
    """Handle Alpaca order rejection errors."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=ErrorResponse(
            error="Order rejected by broker", detail=str(exc), timestamp=datetime.now(UTC)
        ).model_dump(mode="json"),
    )


@app.exception_handler(AlpacaConnectionError)
async def alpaca_connection_handler(request: Request, exc: AlpacaConnectionError) -> JSONResponse:
    """Handle Alpaca connection errors."""
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=ErrorResponse(
            error="Broker connection error", detail=str(exc), timestamp=datetime.now(UTC)
        ).model_dump(mode="json"),
    )


# ============================================================================
# Helper Functions
# ============================================================================


def _batch_fetch_realtime_prices_from_redis(
    symbols: list[str], redis_client: RedisClient | None
) -> dict[str, tuple[Decimal | None, datetime | None]]:
    """
    Batch fetch real-time prices from Redis for multiple symbols.

    This function solves the N+1 query problem by fetching all prices in a single
    MGET call instead of individual GET calls for each symbol.

    Args:
        symbols: List of stock symbols to fetch
        redis_client: Redis client instance

    Returns:
        Dictionary mapping symbol to (price, timestamp) tuple.
        Missing symbols will have (None, None) as value.

    Performance:
        - 1 Redis call vs N calls (where N = number of symbols)
        - 5-10x faster for 10+ symbols
        - Reduces network round-trips from O(N) to O(1)

    Notes:
        - Returns empty dict if Redis unavailable
        - Returns (None, None) for symbols not in cache
        - Handles parsing errors gracefully per symbol
    """
    if not redis_client or not symbols:
        return dict.fromkeys(symbols, (None, None))

    try:
        # Build Redis keys for batch fetch
        price_keys = [RedisKeys.price(symbol) for symbol in symbols]

        # Batch fetch all prices in one Redis call (O(1) network round-trip)
        price_values = redis_client.mget(price_keys)

        # Initialize results with default (None, None) for all symbols (DRY principle)
        result: dict[str, tuple[Decimal | None, datetime | None]] = dict.fromkeys(
            symbols, (None, None)
        )

        # Parse results and update dictionary for symbols with valid data
        for symbol, price_json in zip(symbols, price_values, strict=False):
            if not price_json:
                continue  # Skip symbols not found in cache (already (None, None))

            try:
                price_data = json.loads(price_json)
                price = Decimal(str(price_data["mid"]))
                timestamp = datetime.fromisoformat(price_data["timestamp"])
                result[symbol] = (price, timestamp)
                logger.debug(f"Batch fetched price for {symbol}: ${price}")
            except (json.JSONDecodeError, KeyError, ValueError, TypeError, InvalidOperation) as e:
                # Log error but no need to set result[symbol] - already (None, None)
                logger.warning(f"Failed to parse price for {symbol} from batch fetch: {e}")

        return result

    except RedisError as e:
        # Catch all Redis errors (connection, timeout, etc.) for graceful degradation
        logger.warning(f"Failed to batch fetch prices for {len(symbols)} symbols: {e}")
        return dict.fromkeys(symbols, (None, None))


def _calculate_position_pnl(
    pos: Position,
    current_price: Decimal,
    price_source: Literal["real-time", "database", "fallback"],
    last_price_update: datetime | None,
) -> RealtimePositionPnL:
    """
    Calculate unrealized P&L for a single position.

    Args:
        pos: Position from database
        current_price: Current market price
        price_source: Source of current price (real-time/database/fallback)
        last_price_update: Timestamp of last price update (if available)

    Returns:
        RealtimePositionPnL with calculated P&L values
    """
    # Calculate unrealized P&L
    unrealized_pl = (current_price - pos.avg_entry_price) * pos.qty

    # Calculate P&L percentage based on actual profit/loss
    # This works correctly for both long and short positions
    unrealized_pl_pct = (
        (unrealized_pl / (pos.avg_entry_price * abs(pos.qty))) * Decimal("100")
        if pos.avg_entry_price > 0 and pos.qty != 0
        else Decimal("0")
    )

    return RealtimePositionPnL(
        symbol=pos.symbol,
        qty=pos.qty,
        avg_entry_price=pos.avg_entry_price,
        current_price=current_price,
        price_source=price_source,
        unrealized_pl=unrealized_pl,
        unrealized_pl_pct=unrealized_pl_pct,
        last_price_update=last_price_update,
    )


def _resolve_and_calculate_pnl(
    pos: Position,
    realtime_price_data: tuple[Decimal | None, datetime | None],
) -> tuple[RealtimePositionPnL, bool]:
    """
    Resolve price from multiple sources and calculate P&L for a position.

    Implements three-tier price fallback:
    1. Real-time price from Redis (Market Data Service)
    2. Database price (last known price)
    3. Entry price (ultimate fallback)

    Args:
        pos: Position from database
        realtime_price_data: Tuple of (price, timestamp) from batch Redis fetch

    Returns:
        Tuple of (position P&L, is_realtime flag)

    Notes:
        - Extracted from get_realtime_pnl for improved modularity
        - Makes main endpoint loop more concise and readable
        - Replaces deprecated single-symbol _fetch_realtime_price_from_redis

    See Also:
        - Gemini review: apps/execution_gateway/main.py MEDIUM priority refactoring
    """
    realtime_price, last_price_update = realtime_price_data

    # Three-tier price fallback
    current_price: Decimal
    price_source: Literal["real-time", "database", "fallback"]
    is_realtime: bool

    if realtime_price is not None:
        current_price, price_source, is_realtime = realtime_price, "real-time", True
    elif pos.current_price is not None:
        current_price, price_source, is_realtime = pos.current_price, "database", False
        last_price_update = None
    else:
        current_price, price_source, is_realtime = pos.avg_entry_price, "fallback", False
        last_price_update = None

    # Calculate P&L with resolved price
    position_pnl = _calculate_position_pnl(pos, current_price, price_source, last_price_update)

    return position_pnl, is_realtime


# ============================================================================
# Endpoints
# ============================================================================


@app.get("/", tags=["Health"])
async def root() -> dict[str, Any]:
    """Root endpoint."""
    return {
        "service": "execution_gateway",
        "version": __version__,
        "status": "running",
        "dry_run": DRY_RUN,
    }


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check() -> HealthResponse:
    """
    Health check endpoint.

    Returns service health status including:
    - Overall status (healthy, degraded, unhealthy)
    - Database connection status
    - Alpaca connection status (if not DRY_RUN)
    - Service version and configuration

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
    global kill_switch, circuit_breaker, slice_scheduler  # H3: Removed kill_switch_unavailable (now thread-safe function)

    # Check database connection
    db_connected = db_client.check_connection()

    # Check Redis connection and attempt infrastructure recovery
    redis_connected = False
    if redis_client:
        redis_connected = redis_client.health_check()

        # Attempt to recover kill-switch, circuit breaker, and slice scheduler if Redis is back
        if is_kill_switch_unavailable() and redis_connected:
            try:
                # Re-initialize kill-switch if it was None at startup
                if kill_switch is None:
                    kill_switch = KillSwitch(redis_client=redis_client)
                    logger.info(
                        "Kill-switch re-initialized after Redis recovery",
                        extra={"kill_switch_recovered": True},
                    )

                # Re-initialize circuit breaker if it was None at startup
                if circuit_breaker is None:
                    circuit_breaker = CircuitBreaker(redis_client=redis_client)
                    logger.info(
                        "Circuit breaker re-initialized after Redis recovery",
                        extra={"breaker_recovered": True},
                    )

                # Re-initialize slice scheduler if both kill switch and circuit breaker are now available
                if (
                    slice_scheduler is None
                    and kill_switch is not None
                    and circuit_breaker is not None
                ):
                    slice_scheduler = SliceScheduler(
                        kill_switch=kill_switch,
                        breaker=circuit_breaker,
                        db_client=db_client,
                        executor=alpaca_client,  # Can be None in DRY_RUN mode
                    )
                    # Start the scheduler (same pattern as startup_event)
                    # Guard against restarting a shutdown scheduler (APScheduler limitation)
                    if not slice_scheduler.scheduler.running:
                        slice_scheduler.start()
                        logger.info(
                            "Slice scheduler re-initialized and started after Redis recovery",
                            extra={"scheduler_recovered": True, "scheduler_started": True},
                        )
                    else:
                        logger.info(
                            "Slice scheduler re-initialized but already running",
                            extra={"scheduler_recovered": True, "scheduler_already_running": True},
                        )

                # Test kill-switch availability by checking its state
                kill_switch.is_engaged()
                # If we get here, kill-switch is available again
                set_kill_switch_unavailable(False)
                logger.info(
                    "Infrastructure recovered - resuming normal operations",
                    extra={
                        "kill_switch_recovered": True,
                        "breaker_recovered": True,
                        "scheduler_recovered": True,
                    },
                )
            except Exception as e:
                # Still unavailable, keep flag set
                logger.debug(
                    f"Infrastructure still unavailable during health check: {e}",
                    extra={"kill_switch_unavailable": True},
                )

    # Check Alpaca connection (if not DRY_RUN)
    alpaca_connected = True
    if not DRY_RUN and alpaca_client:
        alpaca_api_status = "success"
        try:
            alpaca_connected = alpaca_client.check_connection()
        except Exception:
            alpaca_api_status = "error"
            alpaca_connected = False
        finally:
            # Always track Alpaca API request metric
            alpaca_api_requests_total.labels(
                operation="check_connection", status=alpaca_api_status
            ).inc()

    # Update health metrics
    database_connection_status.set(1 if db_connected else 0)
    redis_connection_status.set(1 if redis_connected else 0)
    alpaca_connection_status.set(1 if (not DRY_RUN and alpaca_connected) else 0)

    # Determine overall status
    overall_status: Literal["healthy", "degraded", "unhealthy"]
    if is_kill_switch_unavailable():
        # Kill-switch unavailable means we're in fail-closed mode - report degraded
        overall_status = "degraded"
    elif db_connected and (DRY_RUN or alpaca_connected):
        overall_status = "healthy"
    elif db_connected:
        overall_status = "degraded"  # DB OK but Alpaca down
    else:
        overall_status = "unhealthy"  # DB down

    return HealthResponse(
        status=overall_status,
        service="execution_gateway",
        version=__version__,
        dry_run=DRY_RUN,
        database_connected=db_connected,
        alpaca_connected=alpaca_connected,
        timestamp=datetime.now(UTC),
        details={
            "strategy_id": STRATEGY_ID,
            "alpaca_base_url": ALPACA_BASE_URL if not DRY_RUN else None,
        },
    )


@app.get("/api/v1/config", response_model=ConfigResponse, tags=["Configuration"])
async def get_config() -> ConfigResponse:
    """
    Get service configuration for verification.

    Returns safety flags and environment settings for automated verification
    in smoke tests and monitoring. Critical for ensuring paper trading mode
    in staging and detecting configuration drift.

    This endpoint is used by:
    - CI/CD smoke tests to verify paper trading mode active
    - Monitoring systems to detect configuration drift
    - Debugging to verify environment settings

    Returns:
        ConfigResponse with service configuration details

    Examples:
        >>> import requests
        >>> response = requests.get("http://localhost:8002/api/v1/config")
        >>> config = response.json()
        >>> assert config["dry_run"] is True  # Staging safety check
        >>> assert config["alpaca_paper"] is True
        >>> assert config["environment"] == "staging"
    """
    return ConfigResponse(
        service="execution_gateway",
        version=__version__,
        environment=ENVIRONMENT,
        dry_run=DRY_RUN,
        alpaca_paper=ALPACA_PAPER,
        circuit_breaker_enabled=CIRCUIT_BREAKER_ENABLED,
        timestamp=datetime.now(UTC),
    )


@app.post("/api/v1/kill-switch/engage", tags=["Kill-Switch"])
async def engage_kill_switch(request: KillSwitchEngageRequest) -> dict[str, Any]:
    """
    Engage kill-switch (emergency trading halt).

    CRITICAL: This operator-controlled action immediately blocks ALL trading
    activities across all services until manually disengaged.

    Args:
        request: KillSwitchEngageRequest with reason, operator, and optional details

    Returns:
        Kill-switch status after engagement

    Raises:
        HTTPException 503: Redis unavailable
        HTTPException 400: Kill-switch already engaged

    Examples:
        >>> import requests
        >>> response = requests.post(
        ...     "http://localhost:8002/api/v1/kill-switch/engage",
        ...     json={
        ...         "reason": "Market anomaly detected",
        ...         "operator": "ops_team",
        ...         "details": {"anomaly_type": "flash_crash"}
        ...     }
        ... )
        >>> response.json()
        {"state": "ENGAGED", "engaged_by": "ops_team", ...}
    """
    if not kill_switch:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Kill-switch unavailable (Redis not initialized)",
        )

    try:
        kill_switch.engage(
            reason=request.reason, operator=request.operator, details=request.details
        )
        return kill_switch.get_status()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except RuntimeError as e:
        # Kill-switch state missing (fail-closed)
        set_kill_switch_unavailable(True)  # H3: Thread-safe update
        logger.error(
            "Kill-switch engage failed: state missing (fail-closed)",
            extra={"fail_closed": True, "error": str(e)},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Kill-switch unavailable",
                "message": "Kill-switch state missing in Redis (fail-closed for safety)",
                "fail_closed": True,
            },
        ) from e


@app.post("/api/v1/kill-switch/disengage", tags=["Kill-Switch"])
async def disengage_kill_switch(request: KillSwitchDisengageRequest) -> dict[str, Any]:
    """
    Disengage kill-switch (resume trading).

    This operator action re-enables trading after kill-switch was engaged.

    Args:
        request: KillSwitchDisengageRequest with operator and optional notes

    Returns:
        Kill-switch status after disengagement

    Raises:
        HTTPException 503: Redis unavailable
        HTTPException 400: Kill-switch not currently engaged

    Examples:
        >>> import requests
        >>> response = requests.post(
        ...     "http://localhost:8002/api/v1/kill-switch/disengage",
        ...     json={
        ...         "operator": "ops_team",
        ...         "notes": "Market conditions normalized"
        ...     }
        ... )
        >>> response.json()
        {"state": "ACTIVE", "disengaged_by": "ops_team", ...}
    """
    if not kill_switch:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Kill-switch unavailable (Redis not initialized)",
        )

    try:
        kill_switch.disengage(operator=request.operator, notes=request.notes)
        return kill_switch.get_status()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except RuntimeError as e:
        # Kill-switch state missing (fail-closed)
        set_kill_switch_unavailable(True)  # H3: Thread-safe update
        logger.error(
            "Kill-switch disengage failed: state missing (fail-closed)",
            extra={"fail_closed": True, "error": str(e)},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Kill-switch unavailable",
                "message": "Kill-switch state missing in Redis (fail-closed for safety)",
                "fail_closed": True,
            },
        ) from e


@app.get("/api/v1/kill-switch/status", tags=["Kill-Switch"])
async def get_kill_switch_status() -> dict[str, Any]:
    """
    Get kill-switch status.

    Returns current state, last engagement/disengagement details, and history.

    Returns:
        Kill-switch status with state, timestamps, and operator info

    Raises:
        HTTPException 503: Redis unavailable

    Examples:
        >>> import requests
        >>> response = requests.get("http://localhost:8002/api/v1/kill-switch/status")
        >>> status = response.json()
        >>> print(status["state"])
        'ACTIVE'
    """
    if not kill_switch:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Kill-switch unavailable (Redis not initialized)",
        )

    try:
        return kill_switch.get_status()
    except RuntimeError as e:
        # Kill-switch state missing (fail-closed)
        set_kill_switch_unavailable(True)  # H3: Thread-safe update
        logger.error(
            "Kill-switch status unavailable: state missing (fail-closed)",
            extra={"fail_closed": True, "error": str(e)},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Kill-switch unavailable",
                "message": "Kill-switch state missing in Redis (fail-closed for safety)",
                "fail_closed": True,
            },
        ) from e


@app.post("/api/v1/orders", response_model=OrderResponse, tags=["Orders"])
async def submit_order(order: OrderRequest) -> OrderResponse:
    """
    Submit order with idempotent retry semantics.

    The order is assigned a deterministic client_order_id based on the order
    parameters and current date. This ensures that the same order submitted
    multiple times will have the same ID and won't create duplicates.

    In DRY_RUN mode (default), orders are logged to database but NOT submitted
    to Alpaca. Set DRY_RUN=false to enable actual paper trading.

    Args:
        order: Order request (symbol, side, qty, order_type, etc.)

    Returns:
        OrderResponse with client_order_id, status, and broker_order_id

    Raises:
        HTTPException 400: Invalid order parameters
        HTTPException 422: Order rejected by broker
        HTTPException 503: Broker connection error

    Examples:
        Market buy order:
        >>> import requests
        >>> response = requests.post(
        ...     "http://localhost:8002/api/v1/orders",
        ...     json={
        ...         "symbol": "AAPL",
        ...         "side": "buy",
        ...         "qty": 10,
        ...         "order_type": "market"
        ...     }
        ... )
        >>> response.json()
        {
            "client_order_id": "a1b2c3d4e5f6...",
            "status": "dry_run",  # or "pending_new" if DRY_RUN=false
            "broker_order_id": null,
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "order_type": "market",
            "limit_price": null,
            "created_at": "2024-10-17T16:30:00Z",
            "message": "Order logged (DRY_RUN mode)"
        }

        Limit sell order:
        >>> response = requests.post(
        ...     "http://localhost:8002/api/v1/orders",
        ...     json={
        ...         "symbol": "MSFT",
        ...         "side": "sell",
        ...         "qty": 5,
        ...         "order_type": "limit",
        ...         "limit_price": "300.50"
        ...     }
        ... )
    """
    # H3: Removed global kill_switch_unavailable - now using thread-safe functions
    # Start timing for metrics
    start_time = time.time()

    # Generate deterministic client_order_id
    client_order_id = generate_client_order_id(order, STRATEGY_ID)

    logger.info(
        f"Order request received: {order.symbol} {order.side} {order.qty}",
        extra={
            "client_order_id": client_order_id,
            "symbol": order.symbol,
            "side": order.side,
            "qty": order.qty,
            "order_type": order.order_type,
        },
    )

    # Check kill-switch unavailable (fail closed for safety)
    if is_kill_switch_unavailable():
        logger.error(
            f"🔴 Order blocked by unavailable kill-switch (FAIL CLOSED): {client_order_id}",
            extra={
                "client_order_id": client_order_id,
                "kill_switch_unavailable": True,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Kill-switch unavailable",
                "message": "All trading blocked - kill-switch state unknown (Redis unavailable)",
                "fail_closed": True,
            },
        )

    # Check kill-switch (operator-controlled emergency halt)
    try:
        if kill_switch and kill_switch.is_engaged():
            status_info = kill_switch.get_status()
            logger.error(
                f"🔴 Order blocked by KILL-SWITCH: {client_order_id}",
                extra={
                    "client_order_id": client_order_id,
                    "kill_switch_engaged": True,
                    "engaged_by": status_info.get("engaged_by"),
                    "engagement_reason": status_info.get("engagement_reason"),
                },
            )
            # Gemini suggestion: Record metrics for kill-switch blocked orders
            _record_order_metrics(order, start_time, "blocked")
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
    except RuntimeError as e:
        # Kill-switch state missing (fail-closed)
        set_kill_switch_unavailable(True)  # H3: Thread-safe update
        logger.error(
            f"🔴 Order blocked by unavailable kill-switch (FAIL CLOSED): {client_order_id}",
            extra={
                "client_order_id": client_order_id,
                "kill_switch_unavailable": True,
                "fail_closed": True,
                "error": str(e),
            },
        )
        # Gemini suggestion: Record metrics for kill-switch blocked orders
        _record_order_metrics(order, start_time, "blocked")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Kill-switch unavailable",
                "message": "Kill-switch state missing in Redis (fail-closed for safety)",
                "fail_closed": True,
            },
        ) from e

    # C7 Fix: Check circuit breaker (automatic risk-based halt)
    # Circuit breaker trips on drawdown breach, broker errors, data staleness
    # When tripped, only risk-reducing exits are allowed (not new entries)
    if circuit_breaker:
        try:
            if circuit_breaker.is_tripped():
                trip_reason = circuit_breaker.get_trip_reason()
                logger.error(
                    f"🔴 Order blocked by CIRCUIT BREAKER: {client_order_id}",
                    extra={
                        "client_order_id": client_order_id,
                        "circuit_breaker_tripped": True,
                        "trip_reason": trip_reason,
                    },
                )
                # Gemini MEDIUM fix: Record metrics for blocked orders (observability)
                _record_order_metrics(order, start_time, "blocked")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "error": "Circuit breaker tripped",
                        "message": f"Trading halted due to: {trip_reason}",
                        "trip_reason": trip_reason,
                    },
                )
        except RedisError as e:
            # Circuit breaker state unavailable (fail-closed for safety)
            logger.error(
                f"🔴 Order blocked by unavailable circuit breaker (FAIL CLOSED): {client_order_id}",
                extra={
                    "client_order_id": client_order_id,
                    "circuit_breaker_unavailable": True,
                    "fail_closed": True,
                    "error": str(e),
                },
            )
            # Gemini MEDIUM fix: Record metrics for blocked orders (observability)
            _record_order_metrics(order, start_time, "blocked")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "Circuit breaker unavailable",
                    "message": "Circuit breaker state unknown (fail-closed for safety)",
                    "fail_closed": True,
                },
            ) from e

    # Check if order already exists (idempotency)
    existing_order = db_client.get_order_by_client_id(client_order_id)
    if existing_order:
        logger.info(
            f"Order already exists (idempotent): {client_order_id}",
            extra={"client_order_id": client_order_id, "status": existing_order.status},
        )

        # Track metrics for idempotent request (don't double-count)
        duration = time.time() - start_time
        order_placement_duration.labels(symbol=order.symbol, side=order.side).observe(duration)

        return OrderResponse(
            client_order_id=client_order_id,
            status=existing_order.status,
            broker_order_id=existing_order.broker_order_id,
            symbol=existing_order.symbol,
            side=existing_order.side,
            qty=existing_order.qty,
            order_type=existing_order.order_type,
            limit_price=existing_order.limit_price,
            created_at=existing_order.created_at,
            message=f"Order already submitted (status: {existing_order.status})",
        )

    # Submit order based on DRY_RUN mode
    if DRY_RUN:
        # DRY_RUN mode - log order but don't submit to broker
        logger.info(
            f"[DRY_RUN] Logging order: {order.symbol} {order.side} {order.qty}",
            extra={"client_order_id": client_order_id},
        )

        # C4 Fix: Handle race condition where concurrent requests both pass idempotency check
        try:
            order_detail = db_client.create_order(
                client_order_id=client_order_id,
                strategy_id=STRATEGY_ID,
                order_request=order,
                status="dry_run",
                broker_order_id=None,
            )
        except UniqueViolation:
            return _handle_idempotency_race(client_order_id, db_client)

        # Track metrics for dry run order
        _record_order_metrics(order, start_time, "success")

        return OrderResponse(
            client_order_id=client_order_id,
            status="dry_run",
            broker_order_id=None,
            symbol=order.symbol,
            side=order.side,
            qty=order.qty,
            order_type=order.order_type,
            limit_price=order.limit_price,
            created_at=order_detail.created_at,
            message="Order logged (DRY_RUN mode)",
        )

    else:
        # Live mode - submit to Alpaca
        if not alpaca_client:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Alpaca client not initialized. Check credentials.",
            )

        try:
            # Submit to Alpaca with retry logic
            alpaca_api_status = "success"
            try:
                alpaca_response = alpaca_client.submit_order(order, client_order_id)
            except Exception:
                alpaca_api_status = "error"
                raise
            finally:
                # Always track Alpaca API request metric
                alpaca_api_requests_total.labels(
                    operation="submit_order", status=alpaca_api_status
                ).inc()

            # C4 Fix: Handle race condition in live mode
            # Alpaca handles duplicate client_order_ids idempotently
            # But we need to handle the DB race condition
            try:
                order_detail = db_client.create_order(
                    client_order_id=client_order_id,
                    strategy_id=STRATEGY_ID,
                    order_request=order,
                    status=alpaca_response["status"],
                    broker_order_id=alpaca_response["id"],
                )
            except UniqueViolation:
                return _handle_idempotency_race(client_order_id, db_client)

            logger.info(
                f"Order submitted to Alpaca: {client_order_id}",
                extra={
                    "client_order_id": client_order_id,
                    "broker_order_id": alpaca_response["id"],
                    "status": alpaca_response["status"],
                },
            )

            # Track metrics for successful order submission
            _record_order_metrics(order, start_time, "success")

            return OrderResponse(
                client_order_id=client_order_id,
                status=alpaca_response["status"],
                broker_order_id=alpaca_response["id"],
                symbol=order.symbol,
                side=order.side,
                qty=order.qty,
                order_type=order.order_type,
                limit_price=order.limit_price,
                created_at=order_detail.created_at,
                message="Order submitted to broker",
            )

        except (AlpacaValidationError, AlpacaRejectionError, AlpacaConnectionError):
            # Track metrics for rejected orders
            _record_order_metrics(order, start_time, "rejected")
            # These will be handled by exception handlers
            raise

        except Exception as e:
            logger.error(f"Unexpected error submitting order: {e}", exc_info=True)

            # Save failed order to database
            db_client.create_order(
                client_order_id=client_order_id,
                strategy_id=STRATEGY_ID,
                order_request=order,
                status="rejected",
                broker_order_id=None,
                error_message=str(e),
            )

            # Track metrics for failed orders
            _record_order_metrics(order, start_time, "failed")

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Order submission failed: {str(e)}",
            ) from e


# ============================================================================
# TWAP Order Helper Functions
# ============================================================================


def _check_twap_prerequisites() -> None:
    """
    Check prerequisites for TWAP order submission.

    Validates that slice scheduler is available and kill-switch allows trading.
    Follows fail-closed principle for kill-switch unavailability.

    Raises:
        HTTPException 503: If scheduler unavailable or kill-switch state unknown
        HTTPException 503: If kill-switch is engaged
    """
    # Check if slice scheduler is available
    if not slice_scheduler:
        logger.error("Slice scheduler unavailable - cannot accept TWAP orders")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TWAP order service unavailable (scheduler not initialized)",
        )

    # Check kill-switch availability (fail closed)
    if is_kill_switch_unavailable():
        logger.error("Kill-switch unavailable - cannot accept TWAP orders (fail closed)")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TWAP order service unavailable (kill-switch state unknown)",
        )

    # Check kill-switch status
    if kill_switch and kill_switch.is_engaged():
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
        interval_seconds=request.interval_seconds,
        slices=slice_details,
    )


def _create_twap_in_db(request: SlicingRequest, slicing_plan: SlicingPlan) -> SlicingPlan | None:
    """
    Create parent + child orders atomically in database.

    Uses database transaction for all-or-nothing behavior. Handles race condition
    where concurrent identical requests both pass idempotency check: catches
    UniqueViolation and returns existing plan instead of 500 error.

    Args:
        request: TWAP order request
        slicing_plan: Generated slicing plan

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
            f"Concurrent TWAP submission detected (UniqueViolation): parent={slicing_plan.parent_order_id}. "
            f"Returning existing plan.",
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
                f"UniqueViolation raised but parent order not found: {slicing_plan.parent_order_id}",
                extra={"parent_order_id": slicing_plan.parent_order_id},
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database inconsistency: parent order not found after UniqueViolation",
            ) from None

        # Use the strategy_id from the DB to ensure consistency
        slicing_plan.parent_strategy_id = existing_parent.strategy_id

        existing_slices = db_client.get_slices_by_parent_id(slicing_plan.parent_order_id)
        slice_details = _convert_slices_to_details(existing_slices, slicing_plan.parent_order_id)

        # Return existing plan (idempotent response for concurrent submission)
        return SlicingPlan(
            parent_order_id=slicing_plan.parent_order_id,
            parent_strategy_id=slicing_plan.parent_strategy_id,
            symbol=request.symbol,
            side=request.side,
            total_qty=request.qty,
            total_slices=len(slice_details),
            duration_minutes=request.duration_minutes,
            interval_seconds=request.interval_seconds,
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
    # Type narrowing: slice_scheduler is checked in _check_twap_prerequisites()
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
    except Exception as e:
        # Scheduling failed after DB commit - compensate by canceling created orders
        logger.error(
            f"Scheduling failed for parent={slicing_plan.parent_order_id}, compensating by canceling pending orders",
            extra={
                "parent_order_id": slicing_plan.parent_order_id,
                "error": str(e),
                "error_type": type(e).__name__,
            },
        )
        # Cancel only orders still in 'pending_new' status to avoid race conditions
        # (First slice scheduled for "now" may have already executed and submitted to broker)
        try:
            # Cancel all child slices still in pending_new status
            canceled_count = db_client.cancel_pending_slices(slicing_plan.parent_order_id)

            # Check if any child slices have already progressed past pending_new
            all_slices = db_client.get_slices_by_parent_id(slicing_plan.parent_order_id)
            progressed_slices = [
                s for s in all_slices if s.status != "pending_new" and s.status != "canceled"
            ]

            if not progressed_slices:
                # All slices still pending or canceled - safe to cancel parent
                db_client.update_order_status(
                    client_order_id=slicing_plan.parent_order_id,
                    status="canceled",
                    error_message=f"Scheduling failed: {str(e)}",
                )
                logger.info(
                    f"Compensated scheduling failure: canceled parent and {canceled_count} pending slices",
                    extra={
                        "parent_order_id": slicing_plan.parent_order_id,
                        "canceled_slices": canceled_count,
                        "total_slices": len(all_slices),
                    },
                )
            else:
                # Some slices already submitted/executing - don't cancel parent to avoid inconsistency
                logger.warning(
                    f"Scheduling partially failed but {len(progressed_slices)} slices already progressed "
                    f"(statuses: {[s.status for s in progressed_slices]}). "
                    f"Canceled {canceled_count} pending slices but leaving parent active to track live orders.",
                    extra={
                        "parent_order_id": slicing_plan.parent_order_id,
                        "canceled_slices": canceled_count,
                        "progressed_slices": len(progressed_slices),
                        "progressed_statuses": [s.status for s in progressed_slices],
                    },
                )
        except Exception as cleanup_error:
            logger.error(
                f"Cleanup failed after scheduling error: {cleanup_error}",
                extra={"parent_order_id": slicing_plan.parent_order_id},
            )
        # Re-raise original scheduling error
        raise


@app.post("/api/v1/orders/slice", response_model=SlicingPlan, tags=["Orders"])
async def submit_sliced_order(request: SlicingRequest) -> SlicingPlan:
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
        f"TWAP order request: {request.symbol} {request.side} {request.qty} over {request.duration_minutes} min",
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

    try:
        # CRITICAL: Use consistent trade_date for idempotency across midnight
        # If client retries after midnight, must pass same trade_date to avoid duplicate orders
        trade_date = request.trade_date or datetime.now(UTC).date()

        # Step 3: Create slicing plan with consistent trade_date
        slicing_plan = twap_slicer.plan(
            symbol=request.symbol,
            side=request.side,
            qty=request.qty,
            duration_minutes=request.duration_minutes,
            interval_seconds=request.interval_seconds,
            order_type=request.order_type,
            limit_price=request.limit_price,
            stop_price=request.stop_price,
            time_in_force=request.time_in_force,
            trade_date=trade_date,  # Pass consistent trade_date
        )

        # Step 4: Check for existing order (idempotency + backward compatibility)
        existing_plan = _find_existing_twap_plan(request, slicing_plan, trade_date)
        if existing_plan:
            return existing_plan

        # Step 5: Create parent + child orders atomically in database
        # Handles concurrent submissions by catching UniqueViolation
        concurrent_plan = _create_twap_in_db(request, slicing_plan)
        if concurrent_plan:
            return concurrent_plan

        # Step 6: Schedule slices for execution with failure compensation
        job_ids = _schedule_slices_with_compensation(request, slicing_plan)

        # Step 7: Log success and return
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
        logger.error(f"TWAP validation error: {e}", extra={"error": str(e)})
        # Re-raise with 'from e' to preserve original traceback for debugging
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        # Database or scheduling error
        logger.error(f"TWAP order creation failed: {e}", extra={"error": str(e)})
        # Re-raise with 'from e' to preserve original traceback for debugging
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"TWAP order creation failed: {str(e)}",
        ) from e


@app.get("/api/v1/orders/{parent_id}/slices", response_model=list[OrderDetail], tags=["Orders"])
async def get_slices_by_parent(parent_id: str) -> list[OrderDetail]:
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
    except Exception as e:
        logger.error(f"Failed to fetch slices for parent {parent_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch slices: {str(e)}",
        ) from e


@app.delete("/api/v1/orders/{parent_id}/slices", tags=["Orders"])
async def cancel_slices(parent_id: str) -> dict[str, Any]:
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
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Parent order not found: {parent_id}"
        )

    try:
        # Cancel remaining slices (removes from scheduler + updates DB)
        # Note: SliceScheduler updates DB first, then removes scheduler jobs
        scheduler_canceled_count, db_canceled_count = slice_scheduler.cancel_remaining_slices(
            parent_id
        )

        logger.info(
            f"Canceled slices for parent {parent_id}: scheduler={scheduler_canceled_count}, db={db_canceled_count}",
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
            "message": f"Canceled {db_canceled_count} pending slices in DB, removed {scheduler_canceled_count} jobs from scheduler",
        }
    except Exception as e:
        logger.error(f"Failed to cancel slices for parent {parent_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel slices: {str(e)}",
        ) from e


@app.get("/api/v1/orders/{client_order_id}", response_model=OrderDetail, tags=["Orders"])
async def get_order(client_order_id: str) -> OrderDetail:
    """
    Get order details by client_order_id.

    Args:
        client_order_id: Deterministic client order ID

    Returns:
        OrderDetail with full order information

    Raises:
        HTTPException 404: Order not found

    Examples:
        >>> import requests
        >>> response = requests.get(
        ...     "http://localhost:8002/api/v1/orders/a1b2c3d4e5f6..."
        ... )
        >>> response.json()
        {
            "client_order_id": "a1b2c3d4e5f6...",
            "strategy_id": "alpha_baseline",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "order_type": "market",
            "status": "filled",
            "broker_order_id": "broker123...",
            "filled_qty": "10",
            "filled_avg_price": "150.25",
            "created_at": "2024-10-17T16:30:00Z",
            "filled_at": "2024-10-17T16:30:05Z"
        }
    """
    order = db_client.get_order_by_client_id(client_order_id)

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Order not found: {client_order_id}"
        )

    return order


@app.get("/api/v1/positions", response_model=PositionsResponse, tags=["Positions"])
async def get_positions() -> PositionsResponse:
    """
    Get all current positions.

    Returns list of positions with P&L calculations.

    Returns:
        PositionsResponse with list of positions

    Examples:
        >>> import requests
        >>> response = requests.get("http://localhost:8002/api/v1/positions")
        >>> response.json()
        {
            "positions": [
                {
                    "symbol": "AAPL",
                    "qty": "10",
                    "avg_entry_price": "150.25",
                    "current_price": "152.75",
                    "unrealized_pl": "25.00",
                    "realized_pl": "0.00",
                    "updated_at": "2024-10-17T16:30:00Z"
                }
            ],
            "total_positions": 1,
            "total_unrealized_pl": "25.00",
            "total_realized_pl": "0.00"
        }
    """
    positions = db_client.get_all_positions()

    # Protect access to the shared `_tracked_position_symbols` set and fix memory leak
    async with _position_metrics_lock:
        current_symbols = {pos.symbol for pos in positions}

        # Reset metrics for symbols that are no longer in our portfolio
        symbols_to_remove = _tracked_position_symbols - current_symbols
        for symbol in symbols_to_remove:
            positions_current.labels(symbol=symbol).set(0)

        # Update position metrics for each current symbol
        for pos in positions:
            positions_current.labels(symbol=pos.symbol).set(float(pos.qty))

        # Update the set of tracked symbols to match the current portfolio
        _tracked_position_symbols.clear()
        _tracked_position_symbols.update(current_symbols)

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


@app.get("/api/v1/positions/pnl/realtime", response_model=RealtimePnLResponse, tags=["Positions"])
async def get_realtime_pnl() -> RealtimePnLResponse:
    """
    Get real-time P&L with latest market prices.

    Fetches latest prices from Redis cache (populated by Market Data Service).
    Falls back to database prices if real-time data is unavailable.

    Price source priority:
    1. real-time: Latest price from Redis (Market Data Service via WebSocket)
    2. database: Last known price from database (closing price or last fill)
    3. fallback: Entry price (if no other price available)

    Returns:
        RealtimePnLResponse with real-time P&L for all positions

    Examples:
        >>> import requests
        >>> response = requests.get("http://localhost:8002/api/v1/positions/pnl/realtime")
        >>> response.json()
        {
            "positions": [
                {
                    "symbol": "AAPL",
                    "qty": "10",
                    "avg_entry_price": "150.00",
                    "current_price": "152.50",
                    "price_source": "real-time",
                    "unrealized_pl": "25.00",
                    "unrealized_pl_pct": "1.67",
                    "last_price_update": "2024-10-19T14:30:15Z"
                }
            ],
            "total_positions": 1,
            "total_unrealized_pl": "25.00",
            "total_unrealized_pl_pct": "1.67",
            "realtime_prices_available": 1,
            "timestamp": "2024-10-19T14:30:20Z"
        }
    """
    # Get all positions from database
    db_positions = db_client.get_all_positions()

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
    realtime_prices = _batch_fetch_realtime_prices_from_redis(symbols, redis_client)

    # Calculate real-time P&L for each position
    realtime_positions = []
    realtime_count = 0
    total_investment = Decimal("0")

    for pos in db_positions:
        # Using .get() for safer access (though all symbols should be in dict from batch fetch)
        realtime_price_data = realtime_prices.get(pos.symbol, (None, None))

        # Resolve price and calculate P&L (extracted for modularity)
        position_pnl, is_realtime = _resolve_and_calculate_pnl(pos, realtime_price_data)

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


@app.post("/api/v1/webhooks/orders", tags=["Webhooks"])
async def order_webhook(request: Request) -> dict[str, str]:
    """
    Webhook endpoint for Alpaca order status updates.

    Alpaca sends webhooks when order status changes (filled, cancelled, etc.).
    This endpoint:
    1. Receives webhook payload
    2. Validates signature (TODO: implement)
    3. Updates order status in database
    4. Updates positions table if order filled

    Args:
        request: FastAPI Request object with webhook payload

    Returns:
        Success response

    Raises:
        HTTPException 401: Invalid webhook signature (TODO)
        HTTPException 400: Invalid webhook payload

    Note:
        Webhook signature verification is not yet implemented.
        See ADR-0014 for security requirements.

    Examples:
        Webhook payload from Alpaca:
        {
            "event": "fill",
            "order": {
                "id": "broker123...",
                "client_order_id": "a1b2c3d4e5f6...",
                "symbol": "AAPL",
                "side": "buy",
                "qty": "10",
                "filled_qty": "10",
                "filled_avg_price": "150.25",
                "status": "filled"
            },
            "timestamp": "2024-10-17T16:30:05Z"
        }
    """
    try:
        # Parse webhook payload
        body = await request.body()
        payload = await request.json()

        # Verify webhook signature (if secret is configured)
        if WEBHOOK_SECRET:
            signature_header = request.headers.get("X-Alpaca-Signature")
            signature = extract_signature_from_header(signature_header)

            if not signature:
                logger.warning("Webhook received without signature")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing webhook signature"
                )

            if not verify_webhook_signature(body, signature, WEBHOOK_SECRET):
                logger.error("Webhook signature verification failed")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature"
                )

            logger.debug("Webhook signature verified successfully")
        else:
            logger.warning("Webhook signature verification disabled (WEBHOOK_SECRET not set)")

        logger.info(
            f"Webhook received: {payload.get('event', 'unknown')}", extra={"payload": payload}
        )

        # Extract order information
        event_type = payload.get("event")

        # Track webhook metrics
        webhook_received_total.labels(event_type=event_type or "unknown").inc()
        order_data = payload.get("order", {})

        client_order_id = order_data.get("client_order_id")
        broker_order_id = order_data.get("id")
        order_status = order_data.get("status")
        filled_qty = order_data.get("filled_qty")
        filled_avg_price = order_data.get("filled_avg_price")

        if not client_order_id:
            logger.warning("Webhook missing client_order_id")
            return {"status": "ignored", "reason": "missing_client_order_id"}

        # Update order status in database
        updated_order = db_client.update_order_status(
            client_order_id=client_order_id,
            status=order_status,
            broker_order_id=broker_order_id,
            filled_qty=Decimal(str(filled_qty)) if filled_qty else None,
            filled_avg_price=Decimal(str(filled_avg_price)) if filled_avg_price else None,
        )

        if not updated_order:
            logger.warning(f"Order not found for webhook: {client_order_id}")
            return {"status": "ignored", "reason": "order_not_found"}

        # Update positions if order filled
        if event_type in ("fill", "partial_fill") and filled_qty and filled_avg_price:
            position = db_client.update_position_on_fill(
                symbol=order_data["symbol"],
                qty=int(filled_qty),
                price=Decimal(str(filled_avg_price)),
                side=order_data["side"],
            )

            logger.info(
                f"Position updated from fill: {position.symbol} qty={position.qty}",
                extra={
                    "symbol": position.symbol,
                    "qty": str(position.qty),
                    "avg_price": str(position.avg_entry_price),
                },
            )

        return {"status": "ok", "client_order_id": client_order_id}

    except Exception as e:
        logger.error(f"Webhook processing error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Webhook processing failed: {str(e)}"
        ) from e


# ============================================================================
# Startup / Shutdown
# ============================================================================


@app.on_event("startup")
async def startup_event() -> None:
    """Application startup."""
    logger.info("Execution Gateway started")
    logger.info(f"DRY_RUN mode: {DRY_RUN}")
    logger.info(f"Strategy ID: {STRATEGY_ID}")

    # Check database connection
    if not db_client.check_connection():
        logger.error("Database connection failed at startup!")
    else:
        logger.info("Database connection OK")

    # Check Alpaca connection (if not DRY_RUN)
    if not DRY_RUN and alpaca_client:
        if not alpaca_client.check_connection():
            logger.warning("Alpaca connection failed at startup!")
        else:
            logger.info("Alpaca connection OK")

    # Start slice scheduler (for TWAP order execution)
    if slice_scheduler:
        # Guard against restarting a shutdown scheduler (APScheduler limitation)
        # After shutdown(), APScheduler cannot be restarted; this prevents errors
        # in test scenarios or app reloads where startup is called multiple times
        if not slice_scheduler.scheduler.running:
            slice_scheduler.start()
            logger.info("Slice scheduler started")
        else:
            logger.info("Slice scheduler already running (skipping start)")
    else:
        logger.warning("Slice scheduler not available (not started)")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    """Application shutdown."""
    logger.info("Execution Gateway shutting down")

    # Shutdown slice scheduler (wait for running jobs to complete)
    if slice_scheduler:
        logger.info("Shutting down slice scheduler...")
        slice_scheduler.shutdown(wait=True)
        logger.info("Slice scheduler shutdown complete")

    # H2 Fix: Close database connection pool for clean shutdown
    db_client.close()
    logger.info("Database connection pool closed")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "apps.execution_gateway.main:app",
        host="0.0.0.0",
        port=8002,
        reload=True,
        log_level=LOG_LEVEL.lower(),
    )
