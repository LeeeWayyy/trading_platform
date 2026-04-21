"""
Market Data Service - FastAPI Application

Real-time market data streaming service with WebSocket management.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import redis.exceptions
from fastapi import FastAPI, HTTPException, Query, Request, status
from prometheus_client import Counter, Gauge, Histogram, make_asgi_app
from pydantic import BaseModel

from apps.market_data_service.api.dependencies import build_market_data_authenticator
from apps.market_data_service.config import settings
from apps.market_data_service.position_sync import PositionBasedSubscription
from apps.market_data_service.routes.market_data import router as market_data_router
from libs.core.common.api_auth_dependency import APIAuthConfig, api_auth
from libs.core.redis_client import EventPublisher, RedisClient
from libs.data.market_data import AlpacaMarketDataStream, SubscriptionError
from libs.platform.web_console_auth.permissions import Permission

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Global WebSocket stream instance
stream: AlpacaMarketDataStream | None = None

# Global position-based subscription manager
subscription_manager: PositionBasedSubscription | None = None


# Request/Response Models
class SubscribeRequest(BaseModel):
    """Request to subscribe to symbols."""

    symbols: list[str]
    source: str = "manual"


class SubscribeResponse(BaseModel):
    """Response from subscription request."""

    message: str
    subscribed_symbols: list[str]
    total_subscriptions: int


class UnsubscribeResponse(BaseModel):
    """Response from unsubscription request."""

    message: str
    remaining_subscriptions: int


class SubscriptionsResponse(BaseModel):
    """Response with current subscriptions."""

    symbols: list[str]
    count: int


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    service: str
    websocket_connected: bool
    subscribed_symbols: int
    reconnect_attempts: int
    max_reconnect_attempts: int


SOURCE_TAG_PATTERN = re.compile(r"^[A-Za-z0-9:_-]{1,64}$")

SOURCE_OVERRIDE_AUTH = api_auth(
    APIAuthConfig(
        action="market_data_source_override",
        require_role=None,
        require_permission=Permission.VIEW_MARKET_DATA,
    ),
    authenticator_getter=build_market_data_authenticator,
)
SOURCE_OVERRIDE_PREFIX_BY_SERVICE: dict[str, str] = {
    "web_console_ng": "web_console",
}
UNSIGNED_SOURCE_PREFIXES = frozenset(SOURCE_OVERRIDE_PREFIX_BY_SERVICE.values())


def _normalize_subscription_source(raw_source: str | None) -> str:
    """Normalize and validate caller-provided source ownership tags."""
    normalized_source = (raw_source or "").strip() or "manual"
    if not SOURCE_TAG_PATTERN.fullmatch(normalized_source):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Invalid source. Allowed characters: letters, numbers, ':', '_', '-' "
                "with max length 64."
            ),
        )
    return normalized_source


async def _authorize_source_override(request: Request, normalized_source: str) -> None:
    """Require authenticated ownership for non-manual source overrides."""
    if normalized_source == "manual":
        return

    signed_override_required = bool(
        os.getenv("INTERNAL_TOKEN_SECRET", "").strip()
        or any(
            key.startswith("INTERNAL_TOKEN_SECRET_") and str(value).strip()
            for key, value in os.environ.items()
        )
    )
    if not signed_override_required:
        unsigned_source_prefix = normalized_source.split(":", 1)[0].strip().lower()
        if unsigned_source_prefix not in UNSIGNED_SOURCE_PREFIXES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Unsigned source override is not allowed for prefix '{unsigned_source_prefix}'",
            )
        return

    auth_context = await SOURCE_OVERRIDE_AUTH(
        request=request,
        authorization=request.headers.get("Authorization"),
        x_user_id=request.headers.get("X-User-ID"),
        x_request_id=request.headers.get("X-Request-ID"),
        x_session_version=request.headers.get("X-Session-Version"),
        x_internal_token=request.headers.get("X-Internal-Token"),
        x_internal_timestamp=request.headers.get("X-Internal-Timestamp"),
        x_internal_nonce=request.headers.get("X-Internal-Nonce"),
        x_service_id=request.headers.get("X-Service-ID"),
        x_strategy_id=request.headers.get("X-Strategy-ID"),
        x_body_hash=request.headers.get("X-Body-Hash"),
    )
    if auth_context.auth_type != "internal_token" or auth_context.internal_claims is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Explicit source override requires authenticated internal service token",
        )

    service_id = auth_context.internal_claims.service_id.strip().lower()
    source_prefix = SOURCE_OVERRIDE_PREFIX_BY_SERVICE.get(service_id)
    if source_prefix is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Source override is not allowed for service '{service_id}'",
        )
    if normalized_source != source_prefix and not normalized_source.startswith(f"{source_prefix}:"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Source '{normalized_source}' is not owned by service '{service_id}'",
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Lifespan context manager for WebSocket lifecycle.

    Starts WebSocket connection on startup, stops on shutdown.
    """
    global stream, subscription_manager

    logger.info("Starting Market Data Service...")

    try:
        # Initialize Redis clients
        redis_client = RedisClient(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password,
        )

        # EventPublisher takes a RedisClient instance, not individual connection params
        event_publisher = EventPublisher(redis_client=redis_client)

        # Update Redis connection metric
        redis_connection_status.set(1)

        # Initialize WebSocket stream
        stream = AlpacaMarketDataStream(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
            redis_client=redis_client,
            event_publisher=event_publisher,
            price_ttl=settings.price_cache_ttl,
        )

        # Start WebSocket in background task
        asyncio.create_task(stream.start())

        # Initialize position-based subscription manager
        subscription_manager = PositionBasedSubscription(
            stream=stream,
            execution_gateway_url=settings.execution_gateway_url,
            sync_interval=settings.subscription_sync_interval,
            initial_sync=True,
        )

        # Start subscription sync loop in background
        # M2 Fix: Store task handle for proper cancellation on shutdown
        sync_task = asyncio.create_task(subscription_manager.start_sync_loop())
        subscription_manager.set_task(sync_task)

        logger.info(f"Market Data Service started successfully on port {settings.port}")
        logger.info(
            f"Auto-subscription enabled: syncing every {settings.subscription_sync_interval}s"
        )

        yield

    except redis.exceptions.ConnectionError as e:
        logger.error(
            "Failed to start Market Data Service - Redis connection error",
            extra={
                "error": str(e),
                "error_type": type(e).__name__,
                "redis_host": settings.redis_host,
                "redis_port": settings.redis_port,
            },
            exc_info=True,
        )
        raise
    except httpx.HTTPStatusError as e:
        logger.error(
            "Failed to start Market Data Service - HTTP error connecting to Alpaca",
            extra={"status_code": e.response.status_code, "url": str(e.request.url)},
            exc_info=True,
        )
        raise
    except (httpx.ConnectTimeout, httpx.NetworkError) as e:
        logger.error(
            "Failed to start Market Data Service - Network error",
            extra={"error": str(e), "error_type": type(e).__name__},
            exc_info=True,
        )
        raise
    except Exception as e:
        logger.error(
            "Failed to start Market Data Service - Unexpected error",
            extra={"error": str(e), "error_type": type(e).__name__},
            exc_info=True,
        )
        raise

    finally:
        # Cleanup on shutdown
        logger.info("Shutting down Market Data Service...")

        # Stop subscription manager
        # M2 Fix: Use async shutdown() for clean task cancellation
        if subscription_manager:
            await subscription_manager.shutdown(timeout=5.0)

        # Stop WebSocket
        if stream:
            try:
                await stream.stop()
                logger.info("WebSocket stopped successfully")
            except asyncio.CancelledError:
                logger.warning("WebSocket stop cancelled during shutdown")
                raise
            except Exception as e:
                logger.error(
                    "Error stopping WebSocket",
                    extra={"error": str(e), "error_type": type(e).__name__},
                    exc_info=True,
                )


# Create FastAPI app
app = FastAPI(
    title="Market Data Service",
    description="Real-time market data streaming from Alpaca",
    version="1.0.0",
    lifespan=lifespan,
)

# Routes
app.include_router(market_data_router)


# ============================================================================
# Prometheus Metrics
# ============================================================================

# Business metrics
subscription_requests_total = Counter(
    "market_data_subscription_requests_total",
    "Total number of subscription requests",
    ["operation", "status"],  # operation: subscribe/unsubscribe, status: success/error
)

subscription_duration = Histogram(
    "market_data_subscription_duration_seconds",
    "Time taken to process subscription requests",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

# Latency histogram for shared health dashboard (no service prefix)
market_data_processing_duration_seconds = Histogram(
    "market_data_processing_duration_seconds",
    "Time taken to process market data operations",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

subscribed_symbols_current = Gauge(
    "market_data_subscribed_symbols_current",
    "Current number of subscribed symbols",
)

websocket_messages_received_total = Counter(
    "market_data_websocket_messages_received_total",
    "Total number of WebSocket messages received",
    ["message_type"],  # quote, trade, bar, etc.
)

position_syncs_total = Counter(
    "market_data_position_syncs_total",
    "Total number of position-based subscription syncs",
    ["status"],  # success, error
)

# Health metrics
websocket_connection_status = Gauge(
    "market_data_websocket_connection_status",
    "WebSocket connection status (1=connected, 0=disconnected)",
)

redis_connection_status = Gauge(
    "market_data_redis_connection_status",
    "Redis connection status (1=connected, 0=disconnected)",
)

reconnect_attempts_total = Counter(
    "market_data_reconnect_attempts_total",
    "Total number of WebSocket reconnection attempts",
)

# Set initial values
websocket_connection_status.set(0)  # Will be updated by lifespan/health check
redis_connection_status.set(0)  # Will be updated by lifespan/health check

# Mount Prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """
    Health check endpoint with WebSocket status.

    Returns:
        Health status including WebSocket connection state
    """
    if not stream:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Market data stream not initialized",
        )

    stats = stream.get_connection_stats()

    # Update health metrics
    websocket_connection_status.set(1 if stats["is_connected"] else 0)
    subscribed_symbols_current.set(stats["subscribed_symbols"])

    return HealthResponse(
        status="healthy" if stats["is_connected"] else "degraded",
        service=settings.service_name,
        websocket_connected=bool(stats["is_connected"]),
        subscribed_symbols=stats["subscribed_symbols"],
        reconnect_attempts=stats["reconnect_attempts"],
        max_reconnect_attempts=stats["max_reconnect_attempts"],
    )


@app.post("/api/v1/subscribe", response_model=SubscribeResponse, status_code=201)
async def subscribe_symbols(request: SubscribeRequest, http_request: Request) -> SubscribeResponse:
    """
    Subscribe to real-time quotes for symbols.

    Args:
        request: List of symbols to subscribe to

    Returns:
        Subscription confirmation with current subscriptions

    Raises:
        HTTPException: If subscription fails
    """
    request_started = time.time()
    request_status = "success"

    try:
        if not stream:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Market data stream not initialized",
            )

        if not request.symbols:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No symbols provided",
            )

        try:
            normalized_source = _normalize_subscription_source(request.source)
            await _authorize_source_override(http_request, normalized_source)
            await stream.subscribe_symbols(request.symbols, source=normalized_source)

            subscribed = stream.get_subscribed_symbols()

            # Update metrics after successful subscription
            subscribed_symbols_current.set(len(subscribed))

            logger.info(
                "Subscribed to %s symbols (%s): %s",
                len(request.symbols),
                normalized_source,
                request.symbols,
            )

            return SubscribeResponse(
                message=f"Successfully subscribed to {len(request.symbols)} symbols",
                subscribed_symbols=request.symbols,
                total_subscriptions=len(subscribed),
            )

        except SubscriptionError as e:
            logger.error(f"Subscription failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Subscription failed: {str(e)}",
            ) from e

    except HTTPException:
        request_status = "error"
        raise
    except Exception as e:
        request_status = "error"
        logger.error(
            "Subscription request failed - Unexpected error",
            extra={"error": str(e), "error_type": type(e).__name__, "symbols": request.symbols},
            exc_info=True,
        )
        raise
    finally:
        elapsed = time.time() - request_started
        subscription_requests_total.labels(operation="subscribe", status=request_status).inc()
        subscription_duration.observe(elapsed)
        market_data_processing_duration_seconds.observe(elapsed)


@app.delete("/api/v1/subscribe/{symbol}", response_model=UnsubscribeResponse)
async def unsubscribe_symbol(
    http_request: Request,
    symbol: str,
    source: str = Query(default="manual"),
) -> UnsubscribeResponse:
    """
    Unsubscribe from a symbol.

    Args:
        symbol: Symbol to unsubscribe from

    Returns:
        Unsubscription confirmation

    Raises:
        HTTPException: If unsubscription fails
    """
    request_started = time.time()
    request_status = "success"

    try:
        if not stream:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Market data stream not initialized",
            )

        try:
            normalized_source = _normalize_subscription_source(source)
            await _authorize_source_override(http_request, normalized_source)
            await stream.unsubscribe_symbols([symbol], source=normalized_source)

            remaining = stream.get_subscribed_symbols()

            # Update metrics after successful unsubscription
            subscribed_symbols_current.set(len(remaining))

            logger.info("Unsubscribed from %s (%s)", symbol, normalized_source)

            return UnsubscribeResponse(
                message=f"Successfully unsubscribed from {symbol}",
                remaining_subscriptions=len(remaining),
            )

        except SubscriptionError as e:
            logger.error(f"Unsubscription failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unsubscription failed: {str(e)}",
            ) from e

    except HTTPException:
        request_status = "error"
        raise
    except Exception as e:
        request_status = "error"
        logger.error(
            "Unsubscription request failed - Unexpected error",
            extra={"error": str(e), "error_type": type(e).__name__, "symbol": symbol},
            exc_info=True,
        )
        raise
    finally:
        elapsed = time.time() - request_started
        subscription_requests_total.labels(operation="unsubscribe", status=request_status).inc()
        subscription_duration.observe(elapsed)
        market_data_processing_duration_seconds.observe(elapsed)


@app.get("/api/v1/subscriptions", response_model=SubscriptionsResponse)
async def get_subscriptions() -> SubscriptionsResponse:
    """
    Get list of currently subscribed symbols.

    Returns:
        List of subscribed symbols
    """
    if not stream:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Market data stream not initialized",
        )

    symbols = stream.get_subscribed_symbols()

    return SubscriptionsResponse(
        symbols=symbols,
        count=len(symbols),
    )


@app.get("/api/v1/subscriptions/stats", tags=["Subscriptions"])
async def get_subscription_stats() -> dict[str, Any]:
    """
    Get subscription manager statistics.

    Returns detailed stats about auto-subscription including:
    - Whether auto-subscription is running
    - Execution Gateway URL
    - Sync interval
    - Last known position count and symbols
    - Currently subscribed symbols

    Returns:
        Dictionary with subscription manager stats
    """
    if not subscription_manager:
        return {"auto_subscription_enabled": False, "message": "Auto-subscription not configured"}

    stats = subscription_manager.get_stats()
    stats["auto_subscription_enabled"] = True

    return stats


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "apps.market_data_service.main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=True,
    )
