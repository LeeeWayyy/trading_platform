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

import json
import logging
import os
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
    ErrorResponse,
    HealthResponse,
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
    ["type"],  # type: realized, unrealized, total
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
database_connection_status.set(1)  # Will be updated by health check
redis_connection_status.set(1 if redis_client else 0)
alpaca_connection_status.set(1 if alpaca_client else 0)

# Mount Prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


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

    # Check Alpaca connection (if not DRY_RUN)
    alpaca_connected = True
    if not DRY_RUN and alpaca_client:
        alpaca_connected = alpaca_client.check_connection()

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

    # Check if order already exists (idempotency)
    existing_order = db_client.get_order_by_client_id(client_order_id)
    if existing_order:
        logger.info(
            f"Order already exists (idempotent): {client_order_id}",
            extra={"client_order_id": client_order_id, "status": existing_order.status},
        )

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
            alpaca_response = alpaca_client.submit_order(order, client_order_id)

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
