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
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator
import uvicorn

from .config import Settings
from .model_registry import ModelRegistry
from .signal_generator import SignalGenerator

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


# ==============================================================================
# Application Lifespan
# ==============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application startup and shutdown.

    Startup:
        1. Initialize ModelRegistry with database connection
        2. Load active model from database
        3. Initialize SignalGenerator with loaded model
        4. Log service readiness

    Shutdown:
        1. Log shutdown message
        2. Clean up resources (connections, file handles)

    Example:
        This is automatically called by FastAPI when starting the service.

    Notes:
        - Uses global variables for registry and generator
        - Models are loaded from database at startup
        - Hot reload is handled by background task (Phase 5)

    Raises:
        RuntimeError: If model loading fails at startup
    """
    global model_registry, signal_generator

    logger.info("=" * 60)
    logger.info("Signal Service Starting...")
    logger.info("=" * 60)

    try:
        # Step 1: Initialize ModelRegistry
        logger.info(f"Connecting to database: {settings.database_url.split('@')[1]}")
        model_registry = ModelRegistry(settings.database_url)

        # Step 2: Load active model
        logger.info(f"Loading model: {settings.default_strategy}")
        reloaded = model_registry.reload_if_changed(settings.default_strategy)

        if not model_registry.is_loaded:
            raise RuntimeError(
                f"Failed to load model '{settings.default_strategy}'. "
                "Check database has active model registered."
            )

        logger.info(f"Model loaded: {model_registry.current_metadata.version}")

        # Step 3: Initialize SignalGenerator
        logger.info(f"Initializing signal generator (data: {settings.data_dir})")
        signal_generator = SignalGenerator(
            model_registry=model_registry,
            data_dir=settings.data_dir,
            top_n=settings.top_n,
            bottom_n=settings.bottom_n,
        )

        logger.info("=" * 60)
        logger.info("Signal Service Ready!")
        logger.info(f"  - Model: {model_registry.current_metadata.strategy_name}")
        logger.info(f"  - Version: {model_registry.current_metadata.version}")
        logger.info(f"  - Top N (long): {settings.top_n}")
        logger.info(f"  - Bottom N (short): {settings.bottom_n}")
        logger.info(f"  - Data directory: {settings.data_dir}")
        logger.info(f"  - Listening on: {settings.host}:{settings.port}")
        logger.info("=" * 60)

        yield  # Application runs here

    except Exception as e:
        logger.error(f"Failed to start Signal Service: {e}", exc_info=True)
        raise

    finally:
        # Shutdown
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
        min_items=1,
        description="List of stock symbols to generate signals for",
        example=["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]
    )

    as_of_date: Optional[str] = Field(
        None,
        description="Date for signal generation (ISO format: YYYY-MM-DD). Defaults to today.",
        example="2024-12-31"
    )

    top_n: Optional[int] = Field(
        None,
        ge=0,
        description="Number of long positions (overrides default)",
        example=3
    )

    bottom_n: Optional[int] = Field(
        None,
        ge=0,
        description="Number of short positions (overrides default)",
        example=3
    )

    @validator('as_of_date')
    def validate_date(cls, v):
        """Validate date format."""
        if v is not None:
            try:
                datetime.fromisoformat(v)
            except ValueError:
                raise ValueError("as_of_date must be in ISO format (YYYY-MM-DD)")
        return v

    @validator('symbols')
    def validate_symbols(cls, v):
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
            "timestamp": "2024-12-31T10:30:00Z"
        }
    """

    status: str = Field(..., description="Service health status")
    model_loaded: bool = Field(..., description="Whether model is loaded")
    model_info: Optional[dict] = Field(None, description="Model metadata")
    timestamp: str = Field(..., description="Current timestamp")


# ==============================================================================
# Error Handlers
# ==============================================================================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
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
async def root():
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
async def health_check():
    """
    Health check endpoint.

    Checks:
        - Service is running
        - Model is loaded
        - Model metadata is accessible

    Returns:
        HealthResponse with service and model status

    Status Codes:
        - 200: Service is healthy
        - 503: Service is unhealthy (model not loaded)

    Example:
        GET /health

        Response (200 OK):
        {
            "status": "healthy",
            "model_loaded": true,
            "model_info": {
                "strategy": "alpha_baseline",
                "version": "v1.0.0",
                "activated_at": "2024-12-31T00:00:00Z"
            },
            "timestamp": "2024-12-31T10:30:00Z"
        }
    """
    if model_registry is None or not model_registry.is_loaded:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model not loaded"
        )

    metadata = model_registry.current_metadata

    return HealthResponse(
        status="healthy",
        model_loaded=True,
        model_info={
            "strategy": metadata.strategy_name,
            "version": metadata.version,
            "activated_at": metadata.activated_at.isoformat() if metadata.activated_at else None,
        },
        timestamp=datetime.utcnow().isoformat() + "Z",
    )


@app.post(
    "/api/v1/signals/generate",
    response_model=SignalResponse,
    tags=["Signals"],
    status_code=status.HTTP_200_OK,
)
async def generate_signals(request: SignalRequest):
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
async def get_model_info():
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
