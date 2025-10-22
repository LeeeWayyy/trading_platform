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

See ADR-0005 for architecture decisions.
"""

import asyncio
import json
import logging
import os
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Gauge, Histogram, make_asgi_app
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
from apps.execution_gateway.order_id_generator import generate_client_order_id
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
)
from apps.execution_gateway.webhook_security import (
    extract_signature_from_header,
    verify_webhook_signature,
)
from libs.redis_client import RedisClient, RedisConnectionError, RedisKeys
from libs.risk_management import KillSwitch

# ============================================================================
# Configuration
# ============================================================================

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

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
kill_switch_unavailable = False  # Track if kill-switch initialization failed (fail closed)

if redis_client:
    try:
        kill_switch = KillSwitch(redis_client=redis_client)
        logger.info("Kill-switch initialized successfully")
    except Exception as e:
        logger.error(
            f"Failed to initialize kill-switch: {e}. FAILING CLOSED - all trading blocked until Redis available."
        )
        kill_switch_unavailable = True
else:
    logger.error(
        "Kill-switch not initialized (Redis unavailable). FAILING CLOSED - all trading blocked until Redis available."
    )
    kill_switch_unavailable = True

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
    ["symbol", "side", "status"],  # status: success, failed, rejected
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
    status: Literal["success", "rejected", "failed"],
) -> None:
    """
    Record Prometheus metrics for order placement.

    Args:
        order: The order request that was submitted
        start_time: Time when order processing started (from time.time())
        status: Order outcome (success, rejected, or failed)

    Notes:
        This helper reduces code duplication across different order placement paths.
        Increments orders_total counter and records order_placement_duration histogram.
    """
    duration = time.time() - start_time
    orders_total.labels(symbol=order.symbol, side=order.side, status=status).inc()
    order_placement_duration.labels(symbol=order.symbol, side=order.side).observe(duration)


# ============================================================================
# Exception Handlers
# ============================================================================


@app.exception_handler(ValidationError)
async def validation_exception_handler(request: Request, exc: ValidationError) -> JSONResponse:
    """Handle Pydantic validation errors."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=ErrorResponse(
            error="Validation error", detail=str(exc), timestamp=datetime.now()
        ).model_dump(mode="json"),
    )


@app.exception_handler(AlpacaValidationError)
async def alpaca_validation_handler(request: Request, exc: AlpacaValidationError) -> JSONResponse:
    """Handle Alpaca validation errors."""
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=ErrorResponse(
            error="Order validation failed", detail=str(exc), timestamp=datetime.now()
        ).model_dump(mode="json"),
    )


@app.exception_handler(AlpacaRejectionError)
async def alpaca_rejection_handler(request: Request, exc: AlpacaRejectionError) -> JSONResponse:
    """Handle Alpaca order rejection errors."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=ErrorResponse(
            error="Order rejected by broker", detail=str(exc), timestamp=datetime.now()
        ).model_dump(mode="json"),
    )


@app.exception_handler(AlpacaConnectionError)
async def alpaca_connection_handler(request: Request, exc: AlpacaConnectionError) -> JSONResponse:
    """Handle Alpaca connection errors."""
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=ErrorResponse(
            error="Broker connection error", detail=str(exc), timestamp=datetime.now()
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
    # Check database connection
    db_connected = db_client.check_connection()

    # Check Redis connection
    redis_connected = False
    if redis_client:
        redis_connected = redis_client.health_check()

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
    if db_connected and (DRY_RUN or alpaca_connected):
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
        timestamp=datetime.now(),
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

    return kill_switch.get_status()


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
    if kill_switch_unavailable:
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

        order_detail = db_client.create_order(
            client_order_id=client_order_id,
            strategy_id=STRATEGY_ID,
            order_request=order,
            status="dry_run",
            broker_order_id=None,
        )

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

            # Save order to database
            order_detail = db_client.create_order(
                client_order_id=client_order_id,
                strategy_id=STRATEGY_ID,
                order_request=order,
                status=alpaca_response["status"],
                broker_order_id=alpaca_response["id"],
            )

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
        See ADR-0005 for security requirements.

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


@app.on_event("shutdown")
async def shutdown_event() -> None:
    """Application shutdown."""
    logger.info("Execution Gateway shutting down")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "apps.execution_gateway.main:app",
        host="0.0.0.0",
        port=8002,
        reload=True,
        log_level=LOG_LEVEL.lower(),
    )
