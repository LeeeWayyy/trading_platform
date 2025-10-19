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
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from apps.execution_gateway import __version__
from apps.execution_gateway.schemas import (
    OrderRequest,
    OrderResponse,
    OrderDetail,
    PositionsResponse,
    Position,
    RealtimePnLResponse,
    RealtimePositionPnL,
    WebhookEvent,
    HealthResponse,
    ErrorResponse,
)
from apps.execution_gateway.order_id_generator import generate_client_order_id
from apps.execution_gateway.alpaca_client import (
    AlpacaExecutor,
    AlpacaClientError,
    AlpacaConnectionError,
    AlpacaValidationError,
    AlpacaRejectionError,
)
from apps.execution_gateway.database import DatabaseClient
from apps.execution_gateway.webhook_security import (
    verify_webhook_signature,
    extract_signature_from_header,
)
from redis.exceptions import RedisError

from libs.redis_client import RedisClient, RedisConnectionError


# ============================================================================
# Configuration
# ============================================================================

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Environment variables
ALPACA_API_KEY_ID = os.getenv("ALPACA_API_KEY_ID", "")
ALPACA_API_SECRET_KEY = os.getenv("ALPACA_API_SECRET_KEY", "")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/trading_platform")
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
redis_client: Optional[RedisClient] = None
try:
    redis_client = RedisClient(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
    )
    logger.info("Redis client initialized successfully")
except RedisConnectionError as e:
    logger.warning(f"Failed to initialize Redis client: {e}. Real-time P&L will fall back to database prices.")

# Alpaca client (only if not in dry run mode and credentials provided)
alpaca_client: Optional[AlpacaExecutor] = None
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
                paper=True
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
# Exception Handlers
# ============================================================================

@app.exception_handler(ValidationError)
async def validation_exception_handler(request: Request, exc: ValidationError):
    """Handle Pydantic validation errors."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=ErrorResponse(
            error="Validation error",
            detail=str(exc),
            timestamp=datetime.now()
        ).model_dump(mode="json")
    )


@app.exception_handler(AlpacaValidationError)
async def alpaca_validation_handler(request: Request, exc: AlpacaValidationError):
    """Handle Alpaca validation errors."""
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=ErrorResponse(
            error="Order validation failed",
            detail=str(exc),
            timestamp=datetime.now()
        ).model_dump(mode="json")
    )


@app.exception_handler(AlpacaRejectionError)
async def alpaca_rejection_handler(request: Request, exc: AlpacaRejectionError):
    """Handle Alpaca order rejection errors."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=ErrorResponse(
            error="Order rejected by broker",
            detail=str(exc),
            timestamp=datetime.now()
        ).model_dump(mode="json")
    )


@app.exception_handler(AlpacaConnectionError)
async def alpaca_connection_handler(request: Request, exc: AlpacaConnectionError):
    """Handle Alpaca connection errors."""
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=ErrorResponse(
            error="Broker connection error",
            detail=str(exc),
            timestamp=datetime.now()
        ).model_dump(mode="json")
    )


# ============================================================================
# Helper Functions
# ============================================================================

def _fetch_realtime_price_from_redis(
    symbol: str, redis_client: Optional[RedisClient]
) -> tuple[Optional[Decimal], Optional[datetime]]:
    """
    Fetch real-time price from Redis cache.

    Args:
        symbol: Stock symbol
        redis_client: Redis client instance

    Returns:
        Tuple of (price, timestamp) or (None, None) if unavailable
    """
    if not redis_client:
        return None, None

    try:
        price_key = f"price:{symbol}"
        price_json = redis_client.get(price_key)

        if price_json:
            price_data = json.loads(price_json)
            price = Decimal(str(price_data["mid"]))
            timestamp = datetime.fromisoformat(price_data["timestamp"])
            logger.debug(f"Real-time price for {symbol}: ${price} from Redis")
            return price, timestamp

    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
        logger.warning(f"Failed to parse real-time price for {symbol} from Redis: {e}")
    except RedisError as e:
        # Catch all Redis errors (connection, timeout, etc.) for graceful degradation
        logger.warning(f"Failed to fetch real-time price for {symbol} from Redis: {e}")

    return None, None


def _determine_current_price(
    pos: Position, redis_client: Optional[RedisClient]
) -> tuple[Decimal, str, Optional[datetime], bool]:
    """
    Determine current price with three-tier fallback.

    Price source priority:
    1. real-time: Latest price from Redis (Market Data Service via WebSocket)
    2. database: Last known price from database (closing price or last fill)
    3. fallback: Entry price (if no other price available)

    Args:
        pos: Position from database
        redis_client: Redis client instance

    Returns:
        Tuple of (price, source, last_update, is_realtime)
        where is_realtime indicates if real-time data was used
    """
    # Try real-time price from Redis
    current_price, last_price_update = _fetch_realtime_price_from_redis(pos.symbol, redis_client)
    if current_price is not None:
        return current_price, "real-time", last_price_update, True

    # Fallback to database price
    if pos.current_price is not None:
        logger.debug(f"Using database price for {pos.symbol}: ${pos.current_price}")
        return pos.current_price, "database", None, False

    # Ultimate fallback to entry price
    logger.warning(
        f"No current price available for {pos.symbol}, using entry price: ${pos.avg_entry_price}"
    )
    return pos.avg_entry_price, "fallback", None, False


def _calculate_position_pnl(
    pos: Position, current_price: Decimal, price_source: str, last_price_update: Optional[datetime]
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


# ============================================================================
# Endpoints
# ============================================================================

@app.get("/", tags=["Health"])
async def root():
    """Root endpoint."""
    return {
        "service": "execution_gateway",
        "version": __version__,
        "status": "running",
        "dry_run": DRY_RUN,
    }


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
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
        }
    )


@app.post("/api/v1/orders", response_model=OrderResponse, tags=["Orders"])
async def submit_order(order: OrderRequest):
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
            "order_type": order.order_type
        }
    )

    # Check if order already exists (idempotency)
    existing_order = db_client.get_order_by_client_id(client_order_id)
    if existing_order:
        logger.info(
            f"Order already exists (idempotent): {client_order_id}",
            extra={"client_order_id": client_order_id, "status": existing_order.status}
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
            message=f"Order already submitted (status: {existing_order.status})"
        )

    # Submit order based on DRY_RUN mode
    if DRY_RUN:
        # DRY_RUN mode - log order but don't submit to broker
        logger.info(
            f"[DRY_RUN] Logging order: {order.symbol} {order.side} {order.qty}",
            extra={"client_order_id": client_order_id}
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
            message="Order logged (DRY_RUN mode)"
        )

    else:
        # Live mode - submit to Alpaca
        if not alpaca_client:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Alpaca client not initialized. Check credentials."
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
                    "status": alpaca_response["status"]
                }
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
                message="Order submitted to broker"
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
                error_message=str(e)
            )

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Order submission failed: {str(e)}"
            )


@app.get("/api/v1/orders/{client_order_id}", response_model=OrderDetail, tags=["Orders"])
async def get_order(client_order_id: str):
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
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order not found: {client_order_id}"
        )

    return order


@app.get("/api/v1/positions", response_model=PositionsResponse, tags=["Positions"])
async def get_positions():
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
        (pos.unrealized_pl or Decimal("0")) for pos in positions
    )
    total_realized_pl = sum(pos.realized_pl for pos in positions)

    return PositionsResponse(
        positions=positions,
        total_positions=len(positions),
        total_unrealized_pl=total_unrealized_pl if positions else None,
        total_realized_pl=total_realized_pl
    )


@app.get("/api/v1/positions/pnl/realtime", response_model=RealtimePnLResponse, tags=["Positions"])
async def get_realtime_pnl():
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
            timestamp=datetime.now(timezone.utc),
        )

    # Calculate real-time P&L for each position
    realtime_positions = []
    realtime_count = 0
    total_investment = Decimal("0")

    for pos in db_positions:
        # Determine current price with three-tier fallback
        current_price, price_source, last_price_update, is_realtime = _determine_current_price(
            pos, redis_client
        )

        if is_realtime:
            realtime_count += 1

        # Calculate P&L for this position
        position_pnl = _calculate_position_pnl(pos, current_price, price_source, last_price_update)
        realtime_positions.append(position_pnl)

        # Track total investment for portfolio-level percentage
        total_investment += pos.avg_entry_price * abs(pos.qty)

    # Calculate totals
    total_unrealized_pl = sum(p.unrealized_pl for p in realtime_positions)
    total_unrealized_pl_pct = (
        (total_unrealized_pl / total_investment) * Decimal("100")
        if total_investment > 0
        else None
    )

    return RealtimePnLResponse(
        positions=realtime_positions,
        total_positions=len(realtime_positions),
        total_unrealized_pl=total_unrealized_pl,
        total_unrealized_pl_pct=total_unrealized_pl_pct,
        realtime_prices_available=realtime_count,
        timestamp=datetime.now(timezone.utc),
    )


@app.post("/api/v1/webhooks/orders", tags=["Webhooks"])
async def order_webhook(request: Request):
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
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Missing webhook signature"
                )

            if not verify_webhook_signature(body, signature, WEBHOOK_SECRET):
                logger.error("Webhook signature verification failed")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid webhook signature"
                )

            logger.debug("Webhook signature verified successfully")
        else:
            logger.warning(
                "Webhook signature verification disabled (WEBHOOK_SECRET not set)"
            )

        logger.info(
            f"Webhook received: {payload.get('event', 'unknown')}",
            extra={"payload": payload}
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
                side=order_data["side"]
            )

            logger.info(
                f"Position updated from fill: {position.symbol} qty={position.qty}",
                extra={
                    "symbol": position.symbol,
                    "qty": str(position.qty),
                    "avg_price": str(position.avg_entry_price)
                }
            )

        return {"status": "ok", "client_order_id": client_order_id}

    except Exception as e:
        logger.error(f"Webhook processing error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Webhook processing failed: {str(e)}"
        )


# ============================================================================
# Startup / Shutdown
# ============================================================================

@app.on_event("startup")
async def startup_event():
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
async def shutdown_event():
    """Application shutdown."""
    logger.info("Execution Gateway shutting down")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "apps.execution_gateway.main:app",
        host="0.0.0.0",
        port=8002,
        reload=True,
        log_level=LOG_LEVEL.lower()
    )
