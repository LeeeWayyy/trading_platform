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

import logging
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, List, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator
import uvicorn

from .config import Settings
from .model_registry import ModelRegistry
from .signal_generator import SignalGenerator
from libs.redis_client import RedisClient, FeatureCache, RedisConnectionError


def _format_database_url_for_logging(database_url: str) -> str:
    """Return a sanitized database URL suitable for logs."""
    if not database_url:
        return "unknown"

    sanitized = database_url.split('://', 1)[-1]
    if '@' in sanitized:
        sanitized = sanitized.split('@', 1)[1]
    return sanitized

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load settings
settings = Settings()

# Global state (initialized in lifespan)
model_registry: Optional[ModelRegistry] = None
signal_generator: Optional[SignalGenerator] = None
redis_client: Optional[RedisClient] = None
feature_cache: Optional[FeatureCache] = None


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
        f"Starting model reload task "
        f"(interval: {settings.model_reload_interval_seconds}s)"
    )

    while True:
        try:
            # Wait for configured interval
            await asyncio.sleep(settings.model_reload_interval_seconds)

            # Check for model updates
            logger.debug("Checking for model updates...")
            assert model_registry is not None, "model_registry should be initialized"
            reloaded = model_registry.reload_if_changed(
                strategy=settings.default_strategy
            )

            if reloaded:
                assert model_registry.current_metadata is not None
                logger.info(
                    f"Model auto-reloaded: "
                    f"{model_registry.current_metadata.strategy_name} "
                    f"v{model_registry.current_metadata.version}"
                )
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
        reloaded = model_registry.reload_if_changed(settings.default_strategy)

        if not model_registry.is_loaded:
            raise RuntimeError(
                f"Failed to load model '{settings.default_strategy}'. "
                "Check database has active model registered."
            )

        assert model_registry is not None and model_registry.current_metadata is not None
        logger.info(f"Model loaded: {model_registry.current_metadata.version}")

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

        # Step 4: Initialize SignalGenerator
        logger.info(f"Initializing signal generator (data: {settings.data_dir})")
        signal_generator = SignalGenerator(
            model_registry=model_registry,
            data_dir=settings.data_dir,
            top_n=settings.top_n,
            bottom_n=settings.bottom_n,
            feature_cache=feature_cache,  # Pass feature cache (None if disabled)
        )

        logger.info("=" * 60)
        logger.info("Signal Service Ready!")
        assert model_registry is not None and model_registry.current_metadata is not None
        logger.info(f"  - Model: {model_registry.current_metadata.strategy_name}")
        logger.info(f"  - Version: {model_registry.current_metadata.version}")
        logger.info(f"  - Top N (long): {settings.top_n}")
        logger.info(f"  - Bottom N (short): {settings.bottom_n}")
        logger.info(f"  - Data directory: {settings.data_dir}")
        logger.info(f"  - Redis enabled: {settings.redis_enabled}")
        if settings.redis_enabled and feature_cache:
            logger.info(f"  - Feature cache: ACTIVE (TTL: {settings.redis_ttl}s)")
        else:
            logger.info(f"  - Feature cache: DISABLED")
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
        if 'reload_task' in locals():
            reload_task.cancel()
            try:
                await reload_task
            except asyncio.CancelledError:
                pass

        # Close Redis connection
        if redis_client is not None:
            logger.info("Closing Redis connection...")
            redis_client.close()

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

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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

    symbols: List[str] = Field(
        ...,
        min_length=1,
        description="List of stock symbols to generate signals for",
        examples=[["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]]
    )

    as_of_date: Optional[str] = Field(
        default=None,
        description="Date for signal generation (ISO format: YYYY-MM-DD). Defaults to today.",
        examples=["2024-12-31"]
    )

    top_n: Optional[int] = Field(
        default=None,
        ge=0,
        description="Number of long positions (overrides default)",
        examples=[3]
    )

    bottom_n: Optional[int] = Field(
        default=None,
        ge=0,
        description="Number of short positions (overrides default)",
        examples=[3]
    )

    @validator('as_of_date')
    def validate_date(cls, v: Optional[str]) -> Optional[str]:
        """Validate date format."""
        if v is not None:
            try:
                datetime.fromisoformat(v)
            except ValueError:
                raise ValueError("as_of_date must be in ISO format (YYYY-MM-DD)")
        return v

    @validator('symbols')
    def validate_symbols(cls, v: List[str]) -> List[str]:
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

    signals: List[dict] = Field(
        ...,
        description="List of trading signals"
    )

    metadata: dict = Field(
        ...,
        description="Request and model metadata"
    )


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
    model_loaded: bool = Field(..., description="Whether model is loaded")
    model_info: Optional[dict] = Field(None, description="Model metadata")
    redis_status: str = Field(..., description="Redis connection status (connected/disconnected/disabled)")
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
    logger.error(
        f"Unhandled exception on {request.method} {request.url.path}",
        exc_info=exc
    )

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "Internal server error",
            "detail": str(exc),
            "path": str(request.url.path),
        }
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
    if model_registry is None or not model_registry.is_loaded:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model not loaded"
        )

    metadata = model_registry.current_metadata

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
        timestamp=datetime.utcnow().isoformat() + "Z",
    )


@app.post(
    "/api/v1/signals/generate",
    response_model=SignalResponse,
    tags=["Signals"],
    status_code=status.HTTP_200_OK,
)
async def generate_signals(request: SignalRequest) -> dict[str, Any]:
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
    # Validate service is ready
    if signal_generator is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Signal generator not initialized"
        )

    if not model_registry.is_loaded:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model not loaded"
        )

    # Parse date
    if request.as_of_date:
        try:
            as_of_date = datetime.fromisoformat(request.as_of_date)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid date format: {request.as_of_date}. Use YYYY-MM-DD."
            )
    else:
        as_of_date = datetime.now()

    # Override top_n/bottom_n if provided
    top_n = request.top_n if request.top_n is not None else signal_generator.top_n
    bottom_n = request.bottom_n if request.bottom_n is not None else signal_generator.bottom_n

    # Validate parameters
    if top_n + bottom_n > len(request.symbols):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot select {top_n} long + {bottom_n} short from {len(request.symbols)} symbols"
        )

    # Generate signals
    try:
        # Create temporary generator if top_n/bottom_n were overridden
        if request.top_n is not None or request.bottom_n is not None:
            temp_generator = SignalGenerator(
                model_registry=model_registry,
                data_dir=signal_generator.data_provider.data_dir,
                top_n=top_n,
                bottom_n=bottom_n,
            )
            signals_df = temp_generator.generate_signals(
                symbols=request.symbols,
                as_of_date=as_of_date,
            )
        else:
            signals_df = signal_generator.generate_signals(
                symbols=request.symbols,
                as_of_date=as_of_date,
            )

    except FileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Data not found: {str(e)}"
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid request: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Signal generation failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Signal generation failed: {str(e)}"
        )

    # Convert DataFrame to list of dicts
    signals = signals_df.to_dict(orient="records")

    # Build response
    return SignalResponse(
        signals=signals,
        metadata={
            "as_of_date": as_of_date.date().isoformat(),
            "model_version": model_registry.current_metadata.version,
            "strategy": model_registry.current_metadata.strategy_name,
            "num_signals": len(signals),
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "top_n": top_n,
            "bottom_n": bottom_n,
        }
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
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model not loaded"
        )

    metadata = model_registry.current_metadata

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
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model registry not initialized"
        )

    try:
        # Store previous version for comparison
        previous_version = (
            model_registry.current_metadata.version
            if model_registry.is_loaded
            else None
        )

        # Trigger reload check
        logger.info("Manual model reload requested")
        reloaded = model_registry.reload_if_changed(
            strategy=settings.default_strategy
        )

        # Get current version
        current_version = (
            model_registry.current_metadata.version
            if model_registry.is_loaded
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
        else:
            response["message"] = "Model already up to date"
            logger.info("Manual reload: no changes detected")

        return response

    except Exception as e:
        logger.error(f"Manual model reload failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Model reload failed: {str(e)}"
        )


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
