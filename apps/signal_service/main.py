"""
FastAPI application for Signal Service.

This module implements the REST API for generating trading signals from ML models.
It provides endpoints for:
- Health checks
- Signal generation
- Model information

The service uses:
- ModelRegistry: Manages ML model loading and hot reload
- SignalGenerator: Generates trading signals from model predictions

Architecture:
    Client → FastAPI → SignalGenerator → ModelRegistry → LightGBM Model
                     ↓
                T1 Data (Parquet files)

Example:
    Start the service:
        $ uvicorn apps.signal_service.main:app --host 0.0.0.0 --port 8001

    Generate signals:
        $ curl -X POST http://localhost:8001/api/v1/signals/generate \\
            -H "Content-Type: application/json" \\
            -d '{"symbols": ["AAPL", "MSFT", "GOOGL"]}'

See Also:
    - /docs/ADRs/0004-signal-service-architecture.md for design decisions
    - /docs/IMPLEMENTATION_GUIDES/t3-signal-service.md for deployment
    - /docs/API.md for complete API documentation
"""

import asyncio
import logging
import os
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Gauge, Histogram, Info, make_asgi_app
from pydantic import BaseModel, Field, validator

from libs.redis_client import FeatureCache, RedisClient, RedisConnectionError

from .config import Settings
from .model_registry import ModelRegistry
from .signal_generator import SignalGenerator


def _format_database_url_for_logging(database_url: str) -> str:
    """Return a sanitized database URL suitable for logs."""
    if not database_url:
        return "unknown"

    sanitized = database_url.split("://", 1)[-1]
    if "@" in sanitized:
        sanitized = sanitized.split("@", 1)[1]
    return sanitized


# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Load settings
settings = Settings()

# Global state (initialized in lifespan)
model_registry: ModelRegistry | None = None
signal_generator: SignalGenerator | None = None
redis_client: RedisClient | None = None
feature_cache: FeatureCache | None = None

# H8 Fix: Cache SignalGenerators by (top_n, bottom_n) to avoid per-request allocation
# Key: (top_n, bottom_n) tuple, Value: SignalGenerator instance
# Bounded to prevent memory leaks from arbitrary user-provided combinations
# Uses OrderedDict + asyncio.Lock for thread-safe LRU eviction
_MAX_GENERATOR_CACHE_SIZE = 10  # Reasonable limit for (top_n, bottom_n) combinations
_generator_cache: OrderedDict[tuple[int, int], SignalGenerator] = OrderedDict()
_generator_cache_lock = asyncio.Lock()


# ==============================================================================
# Background Tasks
# ==============================================================================


async def model_reload_task() -> None:
    """
    Background task to poll model registry and reload on version changes.

    This task runs continuously in the background, checking for model updates
    at regular intervals. If a new model version is detected in the database,
    it automatically reloads without requiring service restart.

    Behavior:
        1. Sleeps for configured interval (default: 300 seconds / 5 minutes)
        2. Checks database for model version changes
        3. Reloads model if version changed
        4. Logs reload events
        5. Continues polling even if one check fails (resilience)

    Configuration:
        Interval controlled by settings.model_reload_interval_seconds

    Example Log Output:
        2024-12-31 10:00:00 - INFO - Checking for model updates...
        2024-12-31 10:00:00 - INFO - Model auto-reloaded: alpha_baseline v1.0.1

    Notes:
        - Zero-downtime updates: requests during reload use old model
        - Graceful degradation: failed reload keeps current model
        - Thread-safe: ModelRegistry handles concurrent access

    See Also:
        - ModelRegistry.reload_if_changed() for reload logic
        - /api/v1/model/reload for manual reload endpoint
    """
    logger.info(
        f"Starting model reload task " f"(interval: {settings.model_reload_interval_seconds}s)"
    )

    while True:
        try:
            # Wait for configured interval
            await asyncio.sleep(settings.model_reload_interval_seconds)

            # H7 Fix: Handle reload differently based on load state
            # - If loaded: Skip DB query when version matches (hot path optimization)
            # - If not loaded: Attempt cold-load recovery (self-healing after transient failures)
            assert model_registry is not None, "model_registry should be initialized"
            if not model_registry.is_loaded:
                logger.info("No model currently loaded - attempting cold-load recovery...")
                # Attempt to load model (self-healing after startup failure)
                # Use reload_if_changed() which handles loading when no model is loaded
                try:
                    model_registry.reload_if_changed(strategy=settings.default_strategy)
                    if model_registry.is_loaded:
                        logger.info("Cold-load recovery successful - model now loaded")
                    else:
                        logger.warning("Cold-load recovery failed - will retry next interval")
                except Exception as e:
                    logger.warning(f"Cold-load recovery error: {e} - will retry next interval")
                continue

            # Check for model updates
            logger.debug("Checking for model updates...")
            reloaded = model_registry.reload_if_changed(strategy=settings.default_strategy)

            if reloaded:
                assert model_registry.current_metadata is not None
                logger.info(
                    f"Model auto-reloaded: "
                    f"{model_registry.current_metadata.strategy_name} "
                    f"v{model_registry.current_metadata.version}"
                )

                # Update model metrics after successful reload
                model_version_info.info(
                    {
                        "version": model_registry.current_metadata.version,
                        "strategy": model_registry.current_metadata.strategy_name,
                        "activated_at": (
                            model_registry.current_metadata.activated_at.isoformat()
                            if model_registry.current_metadata.activated_at
                            else ""
                        ),
                    }
                )
                model_loaded_status.set(1)
                model_reload_total.labels(status="success").inc()
            else:
                logger.debug("No model updates found")

        except Exception as e:
            logger.error(f"Model reload task failed: {e}", exc_info=True)
            # Continue polling even if one check fails
            # This provides resilience against transient errors


# ==============================================================================
# Application Lifespan
# ==============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Manage application startup and shutdown.

    Startup:
        1. Initialize ModelRegistry with database connection
        2. Load active model from database
        3. Initialize Redis client (if enabled)
        4. Initialize SignalGenerator with loaded model and feature cache
        5. Log service readiness

    Shutdown:
        1. Stop background tasks
        2. Close Redis connection
        3. Clean up resources (connections, file handles)

    Example:
        This is automatically called by FastAPI when starting the service.

    Notes:
        - Uses global variables for registry and generator
        - Models are loaded from database at startup
        - Redis is optional (graceful degradation)
        - Hot reload is handled by background task (Phase 5)

    Raises:
        RuntimeError: If model loading fails at startup
    """
    global model_registry, signal_generator, redis_client, feature_cache

    logger.info("=" * 60)
    logger.info("Signal Service Starting...")
    logger.info("=" * 60)

    try:
        # Step 1: Initialize ModelRegistry
        logger.info(
            "Connecting to database: %s",
            _format_database_url_for_logging(settings.database_url),
        )
        model_registry = ModelRegistry(settings.database_url)

        # Step 2: Load active model
        logger.info(f"Loading model: {settings.default_strategy}")

        # In testing mode, allow service to start without model
        model_load_failed = False
        try:
            reloaded = model_registry.reload_if_changed(settings.default_strategy)

            if not model_registry.is_loaded:
                model_load_failed = True
                error_msg = (
                    f"Failed to load model '{settings.default_strategy}'. "
                    "Check database has active model registered."
                )
                if settings.testing:
                    logger.warning(
                        f"TESTING MODE: {error_msg} Service will start without model. "
                        "Signal generation endpoints will return 500."
                    )
                else:
                    raise RuntimeError(error_msg)
        except ValueError as e:
            # Model not found in database
            model_load_failed = True
            if settings.testing:
                logger.warning(
                    f"TESTING MODE: Model loading failed: {e}. "
                    "Service will start without model. Signal generation endpoints will return 500."
                )
            else:
                raise RuntimeError(f"Failed to load model: {e}") from e

        # Only update metrics if model loaded successfully
        if model_registry.is_loaded:
            assert model_registry is not None
            assert model_registry.current_metadata is not None
            logger.info(f"Model loaded: {model_registry.current_metadata.version}")

            # Update model metrics
            model_version_info.info(
                {
                    "version": model_registry.current_metadata.version,
                    "strategy": model_registry.current_metadata.strategy_name,
                    "activated_at": (
                        model_registry.current_metadata.activated_at.isoformat()
                        if model_registry.current_metadata.activated_at
                        else ""
                    ),
                }
            )
            model_loaded_status.set(1)
        else:
            # Model not loaded - set status to 0
            model_loaded_status.set(0)
            if settings.testing:
                logger.info(
                    "TESTING MODE: Service started without model. "
                    "Health checks will pass, signal generation will return 500."
                )
            else:
                # This should never happen - we should have raised error earlier
                logger.error("Unexpected state: model not loaded in non-testing mode")

        # Step 3: Initialize Redis client (optional, T1.2)
        if settings.redis_enabled:
            logger.info(
                f"Initializing Redis client: {settings.redis_host}:{settings.redis_port} "
                f"(db={settings.redis_db})"
            )
            try:
                redis_client = RedisClient(
                    host=settings.redis_host,
                    port=settings.redis_port,
                    db=settings.redis_db,
                )

                # Verify connection
                if redis_client.health_check():
                    logger.info("Redis connected successfully")

                    # Initialize feature cache
                    feature_cache = FeatureCache(
                        redis_client=redis_client,
                        ttl=settings.redis_ttl,
                    )
                    logger.info(f"Feature cache initialized (TTL: {settings.redis_ttl}s)")
                else:
                    logger.warning("Redis health check failed, running without cache")
                    redis_client = None
                    feature_cache = None

            except RedisConnectionError as e:
                logger.warning(f"Failed to connect to Redis: {e}")
                logger.warning("Service will continue without Redis (graceful degradation)")
                redis_client = None
                feature_cache = None
        else:
            logger.info("Redis disabled (settings.redis_enabled=False)")
            redis_client = None
            feature_cache = None

        # Step 4: Initialize SignalGenerator (skip in TESTING mode if model not loaded)
        if settings.testing and not model_registry.is_loaded:
            logger.info("TESTING MODE: Skipping SignalGenerator initialization (no model loaded)")
            signal_generator = None
        else:
            logger.info(f"Initializing signal generator (data: {settings.data_dir})")
            signal_generator = SignalGenerator(
                model_registry=model_registry,
                data_dir=settings.data_dir,
                top_n=settings.top_n,
                bottom_n=settings.bottom_n,
                feature_cache=feature_cache,  # Pass feature cache (None if disabled)
            )

        logger.info("=" * 60)
        if model_registry.is_loaded and model_registry.current_metadata:
            logger.info("Signal Service Ready!")
            logger.info(f"  - Model: {model_registry.current_metadata.strategy_name}")
            logger.info(f"  - Version: {model_registry.current_metadata.version}")
            logger.info(f"  - Top N (long): {settings.top_n}")
            logger.info(f"  - Bottom N (short): {settings.bottom_n}")
            logger.info(f"  - Data directory: {settings.data_dir}")
        else:
            logger.info("Signal Service Ready (TESTING MODE - No Model)")
            logger.info("  - Model: NOT LOADED")
            logger.info("  - Signal generation: DISABLED (will return 500)")
            logger.info("  - Health checks: ENABLED")

        logger.info(f"  - Redis enabled: {settings.redis_enabled}")
        if settings.redis_enabled and feature_cache:
            logger.info(f"  - Feature cache: ACTIVE (TTL: {settings.redis_ttl}s)")
        else:
            logger.info("  - Feature cache: DISABLED")
        logger.info(f"  - Listening on: {settings.host}:{settings.port}")
        logger.info("=" * 60)

        # Step 5: Start background model reload task
        logger.info("Starting background model reload task...")
        reload_task = asyncio.create_task(model_reload_task())

        yield  # Application runs here

    except Exception as e:
        logger.error(f"Failed to start Signal Service: {e}", exc_info=True)
        raise

    finally:
        # Shutdown
        logger.info("Stopping background model reload task...")
        if "reload_task" in locals():
            reload_task.cancel()
            try:
                await reload_task
            except asyncio.CancelledError:
                pass

        # Close Redis connection
        if redis_client is not None:
            logger.info("Closing Redis connection...")
            redis_client.close()

        # H2 Fix: Close database connection pool for clean shutdown
        if model_registry is not None:
            logger.info("Closing database connection pool...")
            model_registry.close()

        logger.info("Signal Service shutting down...")


# ==============================================================================
# FastAPI Application
# ==============================================================================

app = FastAPI(
    title="Signal Service",
    description="ML-powered trading signal generation service",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# C6 Fix: CORS configuration with environment-based allowlist
# In production, ALLOWED_ORIGINS must be set explicitly
# In dev/test, safe defaults are used (localhost only)
ENVIRONMENT = os.getenv("ENVIRONMENT", "dev")
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "")

if ALLOWED_ORIGINS:
    # Parse comma-separated origins from environment variable
    cors_origins = [o.strip() for o in ALLOWED_ORIGINS.split(",") if o.strip()]
    # Reject wildcard "*" explicitly - it's incompatible with allow_credentials=True
    # and would cause CORSMiddleware to crash at startup
    if "*" in cors_origins:
        raise RuntimeError(
            "ALLOWED_ORIGINS cannot contain wildcard '*' when credentials are enabled. "
            "Specify explicit origins (e.g., 'https://app.example.com,https://admin.example.com') "
            "or use ENVIRONMENT=dev for development with localhost defaults."
        )
elif ENVIRONMENT in ("dev", "test"):
    # Safe defaults for development/testing (localhost only)
    cors_origins = [
        "http://localhost:8501",  # Streamlit default
        "http://127.0.0.1:8501",
        "http://localhost:3000",  # React dev server
        "http://127.0.0.1:3000",
    ]
else:
    # Production requires explicit ALLOWED_ORIGINS configuration
    raise RuntimeError(
        "ALLOWED_ORIGINS must be set for production/staging environments. "
        "Set ALLOWED_ORIGINS environment variable (comma-separated list of origins) "
        "or use ENVIRONMENT=dev for development."
    )

# Add CORS middleware with configured origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==============================================================================
# Prometheus Metrics
# ==============================================================================

# Business metrics
signal_requests_total = Counter(
    "signal_service_requests_total",
    "Total number of signal generation requests",
    ["status"],  # success, error
)

signal_generation_duration = Histogram(
    "signal_service_signal_generation_duration_seconds",
    "Time taken to generate signals",
    buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0],
)

signals_generated_total = Counter(
    "signal_service_signals_generated_total",
    "Total number of signals generated by symbol",
    ["symbol"],
)

model_predictions_total = Counter(
    "signal_service_model_predictions_total",
    "Total number of model predictions made",
)

model_reload_total = Counter(
    "signal_service_model_reload_total",
    "Total number of model reload attempts",
    ["status"],  # success, failed
)

# Health metrics
database_connection_status = Gauge(
    "signal_service_database_connection_status",
    "Database connection status (1=connected, 0=disconnected)",
)

redis_connection_status = Gauge(
    "signal_service_redis_connection_status",
    "Redis connection status (1=connected, 0=disconnected)",
)

model_loaded_status = Gauge(
    "signal_service_model_loaded_status",
    "Model loaded status (1=loaded, 0=not loaded)",
)

model_version_info = Info(
    "signal_service_model_version",
    "Current model version information",
)

# Set initial values
database_connection_status.set(1)  # Will be updated by health check
redis_connection_status.set(1 if redis_client else 0)
model_loaded_status.set(0)  # Will be updated after model loads

# Mount Prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


# ==============================================================================
# Request/Response Models
# ==============================================================================


class SignalRequest(BaseModel):
    """
    Request body for signal generation.

    Attributes:
        symbols: List of stock symbols to generate signals for.
            Must be symbols the model was trained on.
            Example: ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]

        as_of_date: Optional date for signal generation (ISO format).
            Defaults to current date if not provided.
            Example: "2024-12-31"

        top_n: Optional override for number of long positions.
            If not provided, uses service default from config.

        bottom_n: Optional override for number of short positions.
            If not provided, uses service default from config.

    Example:
        {
            "symbols": ["AAPL", "MSFT", "GOOGL"],
            "as_of_date": "2024-12-31",
            "top_n": 1,
            "bottom_n": 1
        }

    Validation:
        - symbols: Must be non-empty list
        - as_of_date: Must be valid ISO date string
        - top_n, bottom_n: Must be >= 0
    """

    symbols: list[str] = Field(
        ...,
        min_length=1,
        description="List of stock symbols to generate signals for",
        examples=[["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]],
    )

    as_of_date: str | None = Field(
        default=None,
        description="Date for signal generation (ISO format: YYYY-MM-DD). Defaults to today.",
        examples=["2024-12-31"],
    )

    top_n: int | None = Field(
        default=None, ge=0, description="Number of long positions (overrides default)", examples=[3]
    )

    bottom_n: int | None = Field(
        default=None,
        ge=0,
        description="Number of short positions (overrides default)",
        examples=[3],
    )

    @validator("as_of_date")
    def validate_date(cls, v: str | None) -> str | None:
        """Validate date format."""
        if v is not None:
            try:
                datetime.fromisoformat(v)
            except ValueError:
                raise ValueError("as_of_date must be in ISO format (YYYY-MM-DD)") from None
        return v

    @validator("symbols")
    def validate_symbols(cls, v: list[str]) -> list[str]:
        """Validate symbols are uppercase."""
        return [s.upper() for s in v]


class SignalResponse(BaseModel):
    """
    Response body for signal generation.

    Attributes:
        signals: List of generated signals (one per symbol)
        metadata: Metadata about the request and model

    Example:
        {
            "signals": [
                {
                    "symbol": "AAPL",
                    "predicted_return": 0.0234,
                    "rank": 1,
                    "target_weight": 0.5
                },
                {
                    "symbol": "MSFT",
                    "predicted_return": 0.0187,
                    "rank": 2,
                    "target_weight": 0.5
                }
            ],
            "metadata": {
                "as_of_date": "2024-12-31",
                "model_version": "v1.0.0",
                "strategy": "alpha_baseline",
                "num_signals": 2,
                "generated_at": "2024-12-31T10:30:00Z"
            }
        }
    """

    signals: list[dict[str, Any]] = Field(..., description="List of trading signals")

    metadata: dict[str, Any] = Field(..., description="Request and model metadata")


class HealthResponse(BaseModel):
    """
    Response body for health check.

    Attributes:
        status: Service health status ("healthy" or "unhealthy")
        model_loaded: Whether ML model is loaded
        model_info: Information about loaded model
        redis_status: Redis connection status (T1.2)
        feature_cache_enabled: Whether feature caching is active (T1.2)
        timestamp: Current server timestamp

    Example:
        {
            "status": "healthy",
            "model_loaded": true,
            "model_info": {
                "strategy": "alpha_baseline",
                "version": "v1.0.0",
                "activated_at": "2024-12-31T00:00:00Z"
            },
            "redis_status": "connected",
            "feature_cache_enabled": true,
            "timestamp": "2024-12-31T10:30:00Z"
        }
    """

    status: str = Field(..., description="Service health status")
    service: str = Field(default="signal_service", description="Service name")
    model_loaded: bool = Field(..., description="Whether model is loaded")
    model_info: dict[str, Any] | None = Field(None, description="Model metadata")
    redis_status: str = Field(
        ..., description="Redis connection status (connected/disconnected/disabled)"
    )
    feature_cache_enabled: bool = Field(..., description="Whether feature caching is active")
    timestamp: str = Field(..., description="Current timestamp")


# ==============================================================================
# Error Handlers
# ==============================================================================


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Global exception handler for unexpected errors.

    Catches all unhandled exceptions and returns a 500 error with details.
    Logs full exception with traceback for debugging.

    Args:
        request: FastAPI request object
        exc: The exception that was raised

    Returns:
        JSONResponse with error details and 500 status code

    Example:
        {
            "error": "Internal server error",
            "detail": "Division by zero",
            "path": "/api/v1/signals/generate"
        }
    """
    logger.error(f"Unhandled exception on {request.method} {request.url.path}", exc_info=exc)

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "Internal server error",
            "detail": str(exc),
            "path": str(request.url.path),
        },
    )


# ==============================================================================
# API Endpoints
# ==============================================================================


@app.get("/", tags=["Root"])
async def root() -> dict[str, Any]:
    """
    Root endpoint with service information.

    Returns:
        Service name, version, and links to documentation

    Example:
        GET /
        {
            "service": "Signal Service",
            "version": "1.0.0",
            "docs": "/docs",
            "health": "/health"
        }
    """
    return {
        "service": "Signal Service",
        "version": "1.0.0",
        "description": "ML-powered trading signal generation",
        "docs": "/docs",
        "health": "/health",
        "api": "/api/v1",
    }


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check() -> HealthResponse:
    """
    Health check endpoint.

    Checks:
        - Service is running
        - Model is loaded
        - Model metadata is accessible
        - Redis connection status (T1.2)

    Returns:
        HealthResponse with service, model, and Redis status

    Status Codes:
        - 200: Service is healthy
        - 503: Service is unhealthy (model not loaded)

    Example:
        GET /health

        Response (200 OK) with Redis enabled:
        {
            "status": "healthy",
            "model_loaded": true,
            "model_info": {
                "strategy": "alpha_baseline",
                "version": "v1.0.0",
                "activated_at": "2024-12-31T00:00:00Z"
            },
            "redis_status": "connected",
            "feature_cache_enabled": true,
            "timestamp": "2024-12-31T10:30:00Z"
        }

        Response (200 OK) with Redis disabled:
        {
            "status": "healthy",
            "model_loaded": true,
            "model_info": {...},
            "redis_status": "disabled",
            "feature_cache_enabled": false,
            "timestamp": "2024-12-31T10:30:00Z"
        }
    """
    # In testing mode, allow health check to pass even without model
    if model_registry is None or not model_registry.is_loaded:
        if not settings.testing:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Model not loaded"
            )

        # Testing mode: return healthy but with model_loaded=False
        redis_status_str = "disabled" if not settings.redis_enabled else "disconnected"

        return HealthResponse(
            status="healthy",
            model_loaded=False,
            model_info=None,
            redis_status=redis_status_str,
            feature_cache_enabled=False,
            timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            service="signal_service",
        )

    metadata = model_registry.current_metadata

    # Validate metadata exists (explicit check for production safety)
    if metadata is None:
        if not settings.testing:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Model metadata not available despite is_loaded=True",
            )

        # Testing mode: return healthy with model_loaded=False
        redis_status_str = "disabled" if not settings.redis_enabled else "disconnected"

        return HealthResponse(
            status="healthy",
            model_loaded=False,
            model_info=None,
            redis_status=redis_status_str,
            feature_cache_enabled=False,
            timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            service="signal_service",
        )

    # Check Redis status (T1.2)
    if not settings.redis_enabled:
        redis_status_str = "disabled"
    elif redis_client is None:
        redis_status_str = "disconnected"
    elif redis_client.health_check():
        redis_status_str = "connected"
    else:
        redis_status_str = "disconnected"

    return HealthResponse(
        status="healthy",
        model_loaded=True,
        model_info={
            "strategy": metadata.strategy_name,
            "version": metadata.version,
            "activated_at": metadata.activated_at.isoformat() if metadata.activated_at else None,
        },
        redis_status=redis_status_str,
        feature_cache_enabled=(feature_cache is not None),
        timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        service="signal_service",
    )


@app.post(
    "/api/v1/signals/generate",
    response_model=SignalResponse,
    tags=["Signals"],
    status_code=status.HTTP_200_OK,
)
async def generate_signals(request: SignalRequest) -> SignalResponse:
    """
    Generate trading signals for given symbols.

    This endpoint:
    1. Validates input (symbols, date, parameters)
    2. Generates Alpha158 features from T1 data
    3. Gets model predictions (expected returns)
    4. Computes target portfolio weights (Top-N Long/Short)
    5. Returns signals with metadata

    Args:
        request: SignalRequest with symbols and optional parameters

    Returns:
        SignalResponse with signals and metadata

    Raises:
        HTTPException 400: Invalid request (bad symbols, date, etc.)
        HTTPException 404: Data not found for requested date
        HTTPException 500: Internal error (model prediction failed)
        HTTPException 503: Service unavailable (model not loaded)

    Example:
        POST /api/v1/signals/generate
        {
            "symbols": ["AAPL", "MSFT", "GOOGL"],
            "as_of_date": "2024-12-31"
        }

        Response (200 OK):
        {
            "signals": [
                {
                    "symbol": "AAPL",
                    "predicted_return": 0.0234,
                    "rank": 1,
                    "target_weight": 0.3333
                },
                {
                    "symbol": "MSFT",
                    "predicted_return": 0.0187,
                    "rank": 2,
                    "target_weight": 0.3333
                },
                {
                    "symbol": "GOOGL",
                    "predicted_return": 0.0156,
                    "rank": 3,
                    "target_weight": 0.3333
                }
            ],
            "metadata": {
                "as_of_date": "2024-12-31",
                "model_version": "v1.0.0",
                "strategy": "alpha_baseline",
                "num_signals": 3,
                "generated_at": "2024-12-31T10:30:00.123456Z",
                "top_n": 3,
                "bottom_n": 0
            }
        }

    Notes:
        - Uses same feature generation code as research (feature parity)
        - Predictions are for next trading day (T+1)
        - Weights sum to 1.0 for longs, -1.0 for shorts
        - Execution time: typically < 100ms for 5-10 symbols

    Performance:
        - Feature generation: 10-50ms
        - Model prediction: 1-5ms
        - Total: < 100ms for typical request
    """
    # Start timing for metrics
    request_started = time.time()
    request_status = "success"

    try:
        # Validate service is ready
        if signal_generator is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Signal generator not initialized",
            )

        # Validate model registry exists (explicit check for production safety)
        if model_registry is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Model registry not initialized",
            )

        if not model_registry.is_loaded:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Model not loaded"
            )

        # Parse date
        if request.as_of_date:
            try:
                as_of_date = datetime.fromisoformat(request.as_of_date)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid date format: {request.as_of_date}. Use YYYY-MM-DD.",
                ) from None
        else:
            as_of_date = datetime.now(UTC)

        # Override top_n/bottom_n if provided
        top_n = request.top_n if request.top_n is not None else signal_generator.top_n
        bottom_n = request.bottom_n if request.bottom_n is not None else signal_generator.bottom_n

        # Validate parameters
        if top_n + bottom_n > len(request.symbols):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot select {top_n} long + {bottom_n} short from {len(request.symbols)} symbols",
            )

        # Generate signals
        try:
            # H8 Fix: Use cached generator if top_n/bottom_n were overridden
            # This avoids creating a new SignalGenerator for each request
            # Uses asyncio.Lock for thread-safety and LRU eviction policy
            if request.top_n is not None or request.bottom_n is not None:
                cache_key = (top_n, bottom_n)
                cached_generator = None

                async with _generator_cache_lock:
                    if cache_key in _generator_cache:
                        # Move to end to mark as recently used (LRU hit)
                        _generator_cache.move_to_end(cache_key)
                        cached_generator = _generator_cache[cache_key]
                        logger.debug(f"Using cached SignalGenerator for {cache_key} (LRU hit)")
                    else:
                        # Evict least recently used if cache is full
                        if len(_generator_cache) >= _MAX_GENERATOR_CACHE_SIZE:
                            oldest_key, _ = _generator_cache.popitem(last=False)
                            logger.debug(
                                f"Evicted cached SignalGenerator {oldest_key} (LRU cache full)"
                            )
                        logger.debug(f"Creating cached SignalGenerator for {cache_key}")
                        cached_generator = SignalGenerator(
                            model_registry=model_registry,
                            data_dir=signal_generator.data_provider.data_dir,
                            top_n=top_n,
                            bottom_n=bottom_n,
                            feature_cache=feature_cache,  # Pass feature cache for consistency
                        )
                        _generator_cache[cache_key] = cached_generator

                signals_df = cached_generator.generate_signals(
                    symbols=request.symbols,
                    as_of_date=as_of_date,
                )
            else:
                signals_df = signal_generator.generate_signals(
                    symbols=request.symbols,
                    as_of_date=as_of_date,
                )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Data not found: {str(exc)}"
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid request: {str(exc)}"
            ) from exc
        except Exception as exc:
            logger.error(f"Signal generation failed: {exc}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Signal generation failed: {str(exc)}",
            ) from exc

        # Track model predictions
        model_predictions_total.inc(len(signals_df))

        # Convert DataFrame to list of dicts
        raw_signals = signals_df.to_dict(orient="records")

        # Validate all dict keys are strings (pandas returns dict[Hashable, Any])
        # This ensures type safety even if DataFrame has non-string column names
        signals: list[dict[str, Any]] = []
        for signal in raw_signals:
            if not all(isinstance(k, str) for k in signal.keys()):
                logger.error(f"Non-string keys found in signal dict: {list(signal.keys())}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Internal error: signal data contains non-string keys",
                )
            signals.append({str(k): v for k, v in signal.items()})
            # Track per-symbol signal generation
            symbol = signal.get("symbol")
            if isinstance(symbol, str):
                signals_generated_total.labels(symbol=symbol).inc()

        # Build response
        return SignalResponse(
            signals=signals,
            metadata={
                "as_of_date": as_of_date.date().isoformat(),
                "model_version": (
                    model_registry.current_metadata.version
                    if model_registry.current_metadata
                    else "unknown"
                ),
                "strategy": (
                    model_registry.current_metadata.strategy_name
                    if model_registry.current_metadata
                    else "unknown"
                ),
                "num_signals": len(signals),
                "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "top_n": top_n,
                "bottom_n": bottom_n,
            },
        )
    except HTTPException:
        request_status = "error"
        raise
    except Exception:
        request_status = "error"
        logger.exception("Unhandled failure in generate_signals")
        raise
    finally:
        # Always record metrics
        elapsed = time.time() - request_started
        signal_requests_total.labels(status=request_status).inc()
        signal_generation_duration.observe(elapsed)


class PrecomputeRequest(BaseModel):
    """
    Request body for feature pre-computation.

    M5 Fix: Allows cache warming at day start to reduce signal generation latency.

    Attributes:
        symbols: List of stock symbols to pre-compute features for
        as_of_date: Optional date for feature computation (ISO format, default: today)
    """

    symbols: list[str] = Field(
        ...,
        min_length=1,
        description="List of stock symbols to pre-compute features for",
        examples=[["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]],
    )

    as_of_date: str | None = Field(
        default=None,
        description="Date for feature computation (ISO format: YYYY-MM-DD). Defaults to today.",
        examples=["2024-12-31"],
    )


class PrecomputeResponse(BaseModel):
    """Response body for feature pre-computation."""

    cached_count: int = Field(..., description="Number of symbols successfully cached")
    skipped_count: int = Field(
        ..., description="Number of symbols skipped (already cached or error)"
    )
    symbols_cached: list[str] = Field(..., description="List of newly cached symbols")
    symbols_skipped: list[str] = Field(..., description="List of skipped symbols")
    as_of_date: str = Field(..., description="Date features were computed for")


@app.post(
    "/api/v1/features/precompute",
    response_model=PrecomputeResponse,
    tags=["Features"],
    status_code=status.HTTP_200_OK,
)
async def precompute_features(request: PrecomputeRequest) -> PrecomputeResponse:
    """
    Pre-compute and cache features without generating signals.

    M5 Fix: Call this endpoint before market open (via cron/scheduler) to warm
    the feature cache. This reduces signal generation latency by avoiding
    disk I/O during request handling.

    Args:
        request: PrecomputeRequest with symbols and optional date

    Returns:
        PrecomputeResponse with cache statistics

    Status Codes:
        - 200: Pre-computation completed (even if some symbols failed)
        - 400: Invalid request (bad date format)
        - 503: Service unavailable (signal generator not initialized)

    Example:
        POST /api/v1/features/precompute
        {
            "symbols": ["AAPL", "MSFT", "GOOGL"],
            "as_of_date": "2024-12-31"
        }

        Response (200 OK):
        {
            "cached_count": 3,
            "skipped_count": 0,
            "symbols_cached": ["AAPL", "MSFT", "GOOGL"],
            "symbols_skipped": [],
            "as_of_date": "2024-12-31"
        }

    Notes:
        - Does NOT require model to be loaded (features only)
        - Idempotent: calling multiple times is safe (skips already cached)
        - Gracefully handles per-symbol errors (continues with others)
    """
    # Validate signal generator exists (but don't require model)
    if signal_generator is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Signal generator not initialized",
        )

    # Parse date
    if request.as_of_date:
        try:
            as_of_date = datetime.fromisoformat(request.as_of_date)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid date format: {request.as_of_date}. Use YYYY-MM-DD.",
            ) from None
    else:
        as_of_date = datetime.now(UTC)

    # Normalize symbols to uppercase
    symbols = [s.upper() for s in request.symbols]

    # Pre-compute features
    result = signal_generator.precompute_features(
        symbols=symbols,
        as_of_date=as_of_date,
    )

    return PrecomputeResponse(
        cached_count=result["cached_count"],
        skipped_count=result["skipped_count"],
        symbols_cached=result["symbols_cached"],
        symbols_skipped=result["symbols_skipped"],
        as_of_date=as_of_date.date().isoformat(),
    )


@app.get("/api/v1/model/info", tags=["Model"])
async def get_model_info() -> dict[str, Any]:
    """
    Get information about the currently loaded model.

    Returns model metadata including:
        - Strategy name
        - Version
        - Performance metrics
        - Configuration
        - Activation timestamp

    Returns:
        Model metadata dictionary

    Status Codes:
        - 200: Model info retrieved
        - 503: Model not loaded

    Example:
        GET /api/v1/model/info

        Response (200 OK):
        {
            "strategy_name": "alpha_baseline",
            "version": "v1.0.0",
            "status": "active",
            "activated_at": "2024-12-31T00:00:00Z",
            "performance_metrics": {
                "ic": 0.082,
                "sharpe": 1.45,
                "max_drawdown": -0.12
            },
            "config": {
                "learning_rate": 0.05,
                "max_depth": 6,
                "num_boost_round": 100
            }
        }
    """
    if model_registry is None or not model_registry.is_loaded:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Model not loaded"
        )

    metadata = model_registry.current_metadata

    # Validate metadata exists (explicit check for production safety)
    if metadata is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model metadata not available despite is_loaded=True",
        )

    return {
        "strategy_name": metadata.strategy_name,
        "version": metadata.version,
        "status": metadata.status,
        "model_path": metadata.model_path,
        "activated_at": metadata.activated_at.isoformat() if metadata.activated_at else None,
        "created_at": metadata.created_at.isoformat() if metadata.created_at else None,
        "performance_metrics": metadata.performance_metrics,
        "config": metadata.config,
    }


@app.post("/api/v1/model/reload", tags=["Model"])
async def reload_model() -> dict[str, Any]:
    """
    Manually trigger model reload from database registry.

    This endpoint forces an immediate check for model version changes,
    bypassing the automatic polling interval. Useful for:
        - Testing model deployments
        - Urgent model updates
        - CI/CD pipelines
        - Debugging reload issues

    Behavior:
        1. Queries database for active model version
        2. Compares with currently loaded version
        3. Reloads model if version changed
        4. Returns reload status

    Returns:
        Dictionary with reload status and current version

    Status Codes:
        - 200: Reload check completed successfully
        - 500: Reload failed (database error, file not found, etc.)
        - 503: Model registry not initialized

    Example:
        POST /api/v1/model/reload

        Response (200 OK) - No change:
        {
            "reloaded": false,
            "version": "v1.0.0",
            "message": "Model already up to date"
        }

        Response (200 OK) - Reloaded:
        {
            "reloaded": true,
            "version": "v1.0.1",
            "previous_version": "v1.0.0",
            "message": "Model reloaded successfully"
        }

    Notes:
        - Safe to call multiple times (idempotent)
        - Zero-downtime: requests during reload use old model
        - Background task continues polling after manual reload

    Usage Example:
        # Shell script for CI/CD
        curl -X POST http://localhost:8001/api/v1/model/reload | jq

        # Python
        import requests
        response = requests.post("http://localhost:8001/api/v1/model/reload")
        print(response.json())
    """
    if model_registry is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Model registry not initialized"
        )

    try:
        # Store previous version for comparison
        previous_version = (
            model_registry.current_metadata.version
            if (model_registry.is_loaded and model_registry.current_metadata)
            else None
        )

        # Trigger reload check
        logger.info("Manual model reload requested")
        reloaded = model_registry.reload_if_changed(strategy=settings.default_strategy)

        # Get current version
        current_version = (
            model_registry.current_metadata.version
            if (model_registry.is_loaded and model_registry.current_metadata)
            else "none"
        )

        # Build response
        response = {
            "reloaded": reloaded,
            "version": current_version,
        }

        if reloaded:
            response["previous_version"] = previous_version
            response["message"] = "Model reloaded successfully"
            logger.info(f"Manual reload successful: {previous_version} -> {current_version}")

            # Update model metrics after successful manual reload
            assert model_registry.current_metadata is not None
            model_version_info.info(
                {
                    "version": model_registry.current_metadata.version,
                    "strategy": model_registry.current_metadata.strategy_name,
                    "activated_at": (
                        model_registry.current_metadata.activated_at.isoformat()
                        if model_registry.current_metadata.activated_at
                        else ""
                    ),
                }
            )
            model_loaded_status.set(1)
            model_reload_total.labels(status="success").inc()
        else:
            response["message"] = "Model already up to date"
            logger.info("Manual reload: no changes detected")

        return response

    except Exception as e:
        logger.error(f"Manual model reload failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Model reload failed: {str(e)}",
        ) from e


# ==============================================================================
# Main Entry Point
# ==============================================================================

if __name__ == "__main__":
    """
    Run the service directly (for development).

    For production, use:
        uvicorn apps.signal_service.main:app --host 0.0.0.0 --port 8001
    """
    uvicorn.run(
        "apps.signal_service.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,  # Auto-reload on code changes (dev only)
        log_level="info",
    )
