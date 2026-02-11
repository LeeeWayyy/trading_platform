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
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Gauge, Histogram, Info, make_asgi_app
from pydantic import BaseModel, Field, validator
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from libs.core.common.api_auth_dependency import (
    APIAuthConfig,
    AuthContext,
    api_auth,
)
from libs.core.common.rate_limit_dependency import RateLimitConfig, rate_limit
from libs.core.common.secrets import (
    close_secret_manager,
    get_optional_secret,
    get_optional_secret_or_none,
    get_required_secret,
    validate_required_secrets,
)
from libs.core.redis_client import (
    EventPublisher,
    FallbackBuffer,
    FeatureCache,
    RedisClient,
    RedisConnectionError,
    SignalEvent,
)
from libs.platform.web_console_auth.permissions import Permission

from .config import Settings
from .model_registry import ModelMetadata, ModelRegistry
from .shadow_validator import ShadowModeValidator, ShadowValidationResult
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

# Global state initialized in lifespan.
# Typed as non-None because FastAPI guarantees endpoints only run after lifespan completes.
# The type:ignore[assignment] is the standard pattern for lifespan-initialized globals.
settings: Settings = None  # type: ignore[assignment]
model_registry: ModelRegistry | None = None
signal_generator: SignalGenerator | None = None
redis_client: RedisClient | None = None  # Can fail gracefully if Redis unavailable
event_publisher: EventPublisher | None = None
fallback_buffer: FallbackBuffer | None = None
feature_cache: FeatureCache | None = None
shadow_validator: ShadowModeValidator | None = None
hydration_complete = True

# H8 Fix: Cache SignalGenerators by (top_n, bottom_n) to avoid per-request allocation
# Key: (top_n, bottom_n) tuple, Value: SignalGenerator instance
# Bounded to prevent memory leaks from arbitrary user-provided combinations
# Uses OrderedDict + asyncio.Lock for thread-safe LRU eviction
_MAX_GENERATOR_CACHE_SIZE = 10  # Reasonable limit for (top_n, bottom_n) combinations
_generator_cache: OrderedDict[tuple[int, int], SignalGenerator] = OrderedDict()
_generator_cache_lock = asyncio.Lock()


# ==============================================================================
# Settings Accessor
# ==============================================================================


def get_settings() -> Settings:
    """
    Get settings instance.

    Returns:
        Settings instance initialized in lifespan

    Raises:
        RuntimeError: If called before lifespan initialization

    Notes:
        - Settings are initialized in lifespan after secrets validation
        - Call this function inside request handlers, not at module level
        - For FastAPI dependency injection, use Depends(get_settings)
    """
    if settings is None:
        raise RuntimeError(
            "Settings not initialized. This function should only be called "
            "during request handling, after the lifespan context has started."
        )
    return settings


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
                    reloaded = model_registry.reload_if_changed(
                        strategy=settings.default_strategy,
                        shadow_validator=_shadow_validate,
                        shadow_validation_enabled=settings.shadow_validation_enabled,
                        skip_shadow_validation=settings.skip_shadow_validation,
                        schedule_validation=_schedule_shadow_validation,
                        on_model_activated=_on_model_activated,
                    )
                    if model_registry.is_loaded:
                        logger.info("Cold-load recovery successful - model now loaded")
                    else:
                        logger.warning("Cold-load recovery failed - will retry next interval")
                except (ValueError, KeyError) as e:
                    logger.warning(
                        "Cold-load recovery error: invalid model data - will retry next interval",
                        extra={
                            "strategy": settings.default_strategy,
                            "error": str(e),
                            "error_type": type(e).__name__,
                        },
                    )
                except (FileNotFoundError, OSError) as e:
                    logger.warning(
                        "Cold-load recovery error: model file not accessible - will retry next interval",
                        extra={
                            "strategy": settings.default_strategy,
                            "error": str(e),
                            "error_type": type(e).__name__,
                        },
                    )
                except RedisConnectionError as e:
                    logger.warning(
                        "Cold-load recovery error: Redis connection failed - will retry next interval",
                        extra={
                            "strategy": settings.default_strategy,
                            "error": str(e),
                        },
                    )
                continue

            # Check for model updates
            logger.debug("Checking for model updates...")
            reloaded = model_registry.reload_if_changed(
                strategy=settings.default_strategy,
                shadow_validator=_shadow_validate,
                shadow_validation_enabled=settings.shadow_validation_enabled,
                skip_shadow_validation=settings.skip_shadow_validation,
                schedule_validation=_schedule_shadow_validation,
                on_model_activated=_on_model_activated,
            )
            _record_shadow_skip_if_bypassed(reloaded)

            if reloaded:
                assert model_registry.current_metadata is not None
                logger.info(
                    f"Model auto-reloaded: "
                    f"{model_registry.current_metadata.strategy_name} "
                    f"v{model_registry.current_metadata.version}"
                )
            elif model_registry.pending_validation:
                extra = {}
                if model_registry.pending_metadata is not None:
                    extra["version"] = model_registry.pending_metadata.version
                logger.info("Shadow validation running for model reload", extra=extra)
            else:
                logger.debug("No model updates found")

        except (ValueError, KeyError) as e:
            logger.error(
                "Model reload task failed: invalid model data",
                extra={
                    "strategy": settings.default_strategy,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
        except (FileNotFoundError, OSError) as e:
            logger.error(
                "Model reload task failed: model file not accessible",
                extra={
                    "strategy": settings.default_strategy,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
        except RedisConnectionError as e:
            logger.error(
                "Model reload task failed: Redis connection error",
                extra={
                    "strategy": settings.default_strategy,
                    "error": str(e),
                },
                exc_info=True,
            )
            # Continue polling even if one check fails
            # This provides resilience against transient errors


def _should_hydrate_features() -> bool:
    """Return True when feature hydration should run."""
    return (
        settings.feature_hydration_enabled
        and feature_cache is not None
        and signal_generator is not None
        and model_registry is not None
        and model_registry.is_loaded
    )


async def feature_hydration_task(symbols: list[str], history_days: int) -> None:
    """
    Background task to hydrate feature cache at startup.

    Runs in a separate thread to avoid blocking the event loop.
    """
    global hydration_complete

    logger.info(
        "Hydrating feature cache with %s days of history for %s symbols...",
        history_days,
        len(symbols),
    )

    try:
        assert signal_generator is not None, "signal_generator should be initialized"
        await asyncio.wait_for(
            asyncio.to_thread(
                signal_generator.hydrate_feature_cache,
                symbols=symbols,
                history_days=history_days,
            ),
            timeout=settings.feature_hydration_timeout_seconds,
        )
        logger.info("Feature cache hydration completed")
        hydration_complete = True  # Only mark complete on success
    except TimeoutError:
        logger.warning(
            "Feature cache hydration timed out after %s seconds; health will remain degraded",
            settings.feature_hydration_timeout_seconds,
        )
        # Keep hydration_complete = False to maintain degraded health status
    except (ValueError, KeyError, TypeError) as e:
        logger.error(
            "Feature cache hydration failed: invalid data format; health will remain degraded",
            extra={
                "symbols_count": len(symbols),
                "history_days": history_days,
                "error": str(e),
                "error_type": type(e).__name__,
            },
            exc_info=True,
        )
        # Keep hydration_complete = False to maintain degraded health status
    except (FileNotFoundError, OSError) as e:
        logger.error(
            "Feature cache hydration failed: data file not accessible; health will remain degraded",
            extra={
                "symbols_count": len(symbols),
                "history_days": history_days,
                "error": str(e),
                "error_type": type(e).__name__,
            },
            exc_info=True,
        )
        # Keep hydration_complete = False to maintain degraded health status
    except RedisConnectionError as e:
        logger.error(
            "Feature cache hydration failed: Redis connection error; health will remain degraded",
            extra={
                "symbols_count": len(symbols),
                "history_days": history_days,
                "error": str(e),
            },
            exc_info=True,
        )
        # Keep hydration_complete = False to maintain degraded health status


def _attempt_redis_reconnect() -> bool:
    """Attempt to reconnect Redis for publishing buffered signals.

    On successful reconnection, also reinitializes FeatureCache and updates
    SignalGenerator to prevent stale references to the old Redis client.
    """
    global redis_client, event_publisher, feature_cache

    if redis_client is not None:
        return True

    try:
        redis_password = get_optional_secret_or_none("redis/password")
        redis_client = RedisClient(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=redis_password,
        )
        event_publisher = EventPublisher(redis_client)

        # Reinitialize FeatureCache with new Redis client to avoid stale references
        feature_cache = FeatureCache(
            redis_client=redis_client,
            ttl=settings.redis_ttl,
        )

        # Update SignalGenerator's feature_cache reference
        if signal_generator is not None:
            signal_generator.feature_cache = feature_cache

        # Update generator cache entries
        for gen in _generator_cache.values():
            gen.feature_cache = feature_cache

        logger.info(
            "Redis reconnected successfully for fallback replay",
            extra={"feature_cache_reinitialized": True},
        )
        return True
    except RedisConnectionError as exc:
        logger.warning("Redis reconnect attempt failed: %s", exc)
        return False


def _publish_buffered_message(channel: str, payload: str) -> None:
    """Publish a buffered message via Redis (raises on failure)."""
    if redis_client is None:
        raise RuntimeError("Redis client not initialized")
    redis_client.publish(channel, payload)


async def redis_fallback_replay_task() -> None:
    """Background task to replay buffered signals when Redis recovers."""
    logger.info(
        "Starting Redis fallback replay task (interval: %ss)",
        settings.redis_fallback_replay_interval_seconds,
    )

    redis_was_healthy = False

    while True:
        await asyncio.sleep(settings.redis_fallback_replay_interval_seconds)

        if not settings.redis_enabled or fallback_buffer is None:
            continue

        if redis_client is None and not _attempt_redis_reconnect():
            redis_was_healthy = False
            continue

        if redis_client is None:
            redis_was_healthy = False
            continue

        is_healthy = redis_client.health_check()
        if is_healthy and not redis_was_healthy:
            logger.info("Redis recovered; attempting fallback replay")
        elif not is_healthy and redis_was_healthy:
            logger.warning("Redis unavailable; buffering signals until recovery")

        redis_was_healthy = is_healthy

        if not is_healthy or fallback_buffer.size == 0:
            continue

        replayed = await asyncio.to_thread(fallback_buffer.replay, _publish_buffered_message)
        if replayed:
            signals_replayed_total.inc(replayed)
            redis_fallback_buffer_size.set(fallback_buffer.size)
            logger.info("Replayed %s buffered signal events", replayed)


# ==============================================================================
# Application Lifespan
# ==============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Manage application startup and shutdown.

    Startup:
        1. Validate required secrets (fail-fast)
        2. Initialize Settings with validated secrets
        3. Initialize ModelRegistry with database connection
        4. Load active model from database
        5. Initialize Redis client (if enabled)
        6. Initialize SignalGenerator with loaded model and feature cache
        7. Log service readiness

    Shutdown:
        1. Stop background tasks
        2. Close Redis connection
        3. Close secret manager
        4. Clean up resources (connections, file handles)

    Example:
        This is automatically called by FastAPI when starting the service.

    Notes:
        - Uses global variables for registry and generator
        - Models are loaded from database at startup
        - Redis is optional (graceful degradation)
        - Hot reload is handled by background task (Phase 5)

    Raises:
        RuntimeError: If model loading fails at startup or secrets are missing
    """
    global settings
    global model_registry
    global signal_generator
    global redis_client
    global event_publisher
    global fallback_buffer
    global feature_cache
    global shadow_validator
    global hydration_complete

    logger.info("=" * 60)
    logger.info("Signal Service Starting...")
    logger.info("=" * 60)

    try:
        # Step 0: Validate required secrets (fail-fast before any connections)
        # In dev/test, allow fallback to Settings defaults for easier local development
        logger.info("Validating required secrets...")
        if ENVIRONMENT not in ("dev", "test"):
            validate_required_secrets(["database/url"])

        # Step 0.5: Create Settings instance with validated secret
        logger.info("Initializing settings...")
        if ENVIRONMENT not in ("dev", "test"):
            # Production: require secret from secrets backend
            database_url = get_required_secret("database/url")
            settings = Settings(database_url=database_url)
        else:
            # Dev/test: use secret if available, otherwise fall back to Settings default
            database_url = get_optional_secret("database/url", "")
            if database_url:
                settings = Settings(database_url=database_url)
            else:
                logger.info("DATABASE_URL not set, using Settings default (dev/test mode)")
                settings = Settings()

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
            had_current_model = model_registry.is_loaded
            reloaded = model_registry.reload_if_changed(settings.default_strategy)
            if had_current_model:
                _record_shadow_skip_if_bypassed(reloaded)

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
            fallback_buffer = FallbackBuffer(
                max_size=settings.redis_fallback_buffer_max_size,
                persist_path=settings.redis_fallback_buffer_path,
            )
            redis_fallback_buffer_size.set(fallback_buffer.size)
            try:
                redis_password = get_optional_secret_or_none("redis/password")
                redis_client = RedisClient(
                    host=settings.redis_host,
                    port=settings.redis_port,
                    db=settings.redis_db,
                    password=redis_password,
                )
                event_publisher = EventPublisher(redis_client)

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
                    feature_cache = None

            except RedisConnectionError as e:
                logger.warning(f"Failed to connect to Redis: {e}")
                logger.warning("Service will continue without Redis (graceful degradation)")
                redis_client = None
                event_publisher = None
                feature_cache = None
        else:
            logger.info("Redis disabled (settings.redis_enabled=False)")
            redis_client = None
            event_publisher = None
            fallback_buffer = None
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

        # Step 4.2: Initialize shadow validator (T4)
        if settings.shadow_validation_enabled:
            shadow_validator = ShadowModeValidator(
                data_dir=settings.data_dir,
                symbols=settings.tradable_symbols,
                sample_count=settings.shadow_sample_count,
            )
            logger.info(
                "Shadow validation enabled",
                extra={"sample_count": settings.shadow_sample_count},
            )
            if settings.skip_shadow_validation:
                logger.warning("SKIP_SHADOW_VALIDATION enabled - bypassing shadow checks")
        else:
            shadow_validator = None
            logger.info("Shadow validation disabled")

        # Step 4.5: Start background feature hydration task
        hydration_task = None
        if _should_hydrate_features():
            hydration_complete = False
            logger.info("Starting background feature hydration task...")
            hydration_task = asyncio.create_task(
                feature_hydration_task(
                    symbols=settings.tradable_symbols,
                    history_days=SignalGenerator.DEFAULT_FEATURE_HYDRATION_DAYS,
                )
            )
        else:
            hydration_complete = True
            if not settings.feature_hydration_enabled:
                logger.info("Feature hydration disabled (FEATURE_HYDRATION_ENABLED=false)")
            elif feature_cache is None:
                logger.info("Feature cache not enabled, skipping hydration")
            elif model_registry is None or not model_registry.is_loaded:
                logger.info("Model not loaded, skipping hydration")

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
        redis_replay_task = None
        if settings.redis_enabled and fallback_buffer is not None:
            logger.info("Starting Redis fallback replay task...")
            redis_replay_task = asyncio.create_task(redis_fallback_replay_task())

        yield  # Application runs here

    except (ValueError, KeyError, TypeError) as e:
        logger.error(
            "Failed to start Signal Service: invalid configuration or data",
            extra={
                "error": str(e),
                "error_type": type(e).__name__,
            },
            exc_info=True,
        )
        raise
    except (FileNotFoundError, OSError) as e:
        logger.error(
            "Failed to start Signal Service: file or database not accessible",
            extra={
                "error": str(e),
                "error_type": type(e).__name__,
            },
            exc_info=True,
        )
        raise
    except RedisConnectionError as e:
        logger.error(
            "Failed to start Signal Service: Redis connection error",
            extra={
                "error": str(e),
            },
            exc_info=True,
        )
        raise
    except RuntimeError as e:
        logger.error(
            "Failed to start Signal Service: runtime initialization error",
            extra={
                "error": str(e),
            },
            exc_info=True,
        )
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

        logger.info("Stopping Redis fallback replay task...")
        if "redis_replay_task" in locals() and redis_replay_task is not None:
            redis_replay_task.cancel()
            try:
                await redis_replay_task
            except asyncio.CancelledError:
                pass

        logger.info("Stopping background feature hydration task...")
        if "hydration_task" in locals() and hydration_task is not None:
            hydration_task.cancel()
            try:
                await hydration_task
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

        # Close secret manager
        logger.info("Closing secret manager...")
        close_secret_manager()

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
        "http://localhost:8080",  # Web Console default
        "http://127.0.0.1:8080",
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
# Proxy Headers Middleware (for accurate client IP behind load balancers)
# ==============================================================================
# SECURITY: Restrict trusted_hosts to known ingress/load balancer IPs
# Never use ["*"] in production - allows IP spoofing via X-Forwarded-For
TRUSTED_PROXY_HOSTS = os.getenv("TRUSTED_PROXY_HOSTS", "127.0.0.1").split(",")
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=TRUSTED_PROXY_HOSTS)  # type: ignore[arg-type]

# ==============================================================================
# Rate Limiting Configuration
# ==============================================================================
# Signal generation has no broker calls, can be higher than order limits
SIGNAL_GENERATE_LIMIT = int(os.getenv("SIGNAL_GENERATE_RATE_LIMIT", "30"))

signal_generate_rl = rate_limit(
    RateLimitConfig(
        action="signal_generate",
        max_requests=SIGNAL_GENERATE_LIMIT,
        window_seconds=60,
        burst_buffer=10,
        fallback_mode="deny",
        global_limit=160,  # No broker calls, can be higher
    )
)

# ==============================================================================
# API Authentication Configuration (C6)
# ==============================================================================
# Auth dependency for signal generation endpoint. Defaults to enforce mode (fail-closed).
# Set API_AUTH_MODE=log_only for staged rollout.
#
# NOTE: Signal service is S2S only (internal token auth), not JWT.
# JWT auth requests will receive 401 with "jwt_not_supported" error in enforce mode.
# If JWT auth is needed in future, add authenticator_getter parameter.

signal_generate_auth = api_auth(
    APIAuthConfig(
        action="signal_generate",
        require_role=None,  # Role checked via permission
        require_permission=Permission.GENERATE_SIGNALS,
    ),
    # authenticator_getter=None - S2S only, no JWT auth support
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

# Latency histogram for shared health dashboard (no service prefix)
signal_generation_duration_seconds = Histogram(
    "signal_generation_duration_seconds",
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

# Shadow validation metrics (T4)
shadow_validation_total = Counter(
    "signal_service_shadow_validation_total",
    "Total number of shadow validation runs",
    ["status"],  # passed, rejected, failed, skipped
)

shadow_validation_correlation = Gauge(
    "signal_service_shadow_validation_correlation",
    "Correlation between old and new model predictions",
)

shadow_validation_mean_abs_diff_ratio = Gauge(
    "signal_service_shadow_validation_mean_abs_diff_ratio",
    "Mean absolute difference ratio between old and new predictions",
)

shadow_validation_sign_change_rate = Gauge(
    "signal_service_shadow_validation_sign_change_rate",
    "Rate of prediction sign changes between old and new models",
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

redis_fallback_buffer_size = Gauge(
    "signal_service_redis_fallback_buffer_size",
    "Number of buffered signal events waiting for Redis publish",
)

signals_buffered_total = Counter(
    "signal_service_signals_buffered_total",
    "Total number of signal events buffered due to Redis publish failures",
)

signals_replayed_total = Counter(
    "signal_service_signals_replayed_total",
    "Total number of buffered signal events replayed after Redis recovery",
)

signals_dropped_total = Counter(
    "signal_service_signals_dropped_total",
    "Total number of buffered signal events dropped due to buffer limits",
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
redis_fallback_buffer_size.set(0)
model_loaded_status.set(0)  # Will be updated after model loads

# Mount Prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


# ==============================================================================
# Shadow Validation Helpers (T4)
# ==============================================================================


def _record_shadow_validation(result: ShadowValidationResult | None, status: str) -> None:
    """Record shadow validation metrics for monitoring."""
    shadow_validation_total.labels(status=status).inc()
    if result is None:
        return
    shadow_validation_correlation.set(result.correlation)
    shadow_validation_mean_abs_diff_ratio.set(result.mean_abs_diff_ratio)
    shadow_validation_sign_change_rate.set(result.sign_change_rate)


def _record_shadow_skip_if_bypassed(reloaded: bool) -> None:
    """Record when shadow validation is bypassed by configuration."""
    if not reloaded:
        return
    if not settings.shadow_validation_enabled or settings.skip_shadow_validation:
        _record_shadow_validation(None, "skipped")


def _on_model_activated(metadata: ModelMetadata) -> None:
    """Update model metrics after a model becomes active."""
    model_version_info.info(
        {
            "version": metadata.version,
            "strategy": metadata.strategy_name,
            "activated_at": metadata.activated_at.isoformat() if metadata.activated_at else "",
        }
    )
    model_loaded_status.set(1)
    model_reload_total.labels(status="success").inc()


def _schedule_shadow_validation(task: Callable[[], None]) -> None:
    """Run shadow validation in the background without blocking the event loop."""
    asyncio.create_task(asyncio.to_thread(task))


def _shadow_validate(old_model: Any, new_model: Any) -> ShadowValidationResult:
    """Validate a candidate model against the current model and emit metrics."""
    if shadow_validator is None:
        raise RuntimeError("Shadow validator not initialized")

    try:
        result = shadow_validator.validate(old_model, new_model)
        status_label = "passed" if result.passed else "rejected"
        _record_shadow_validation(result, status_label)
        return result
    except (ValueError, KeyError, TypeError) as e:
        logger.error(
            "Shadow validation failed: invalid model data",
            extra={
                "error": str(e),
                "error_type": type(e).__name__,
            },
            exc_info=True,
        )
        _record_shadow_validation(None, "failed")
        raise
    except (FileNotFoundError, OSError) as e:
        logger.error(
            "Shadow validation failed: data file not accessible",
            extra={
                "error": str(e),
                "error_type": type(e).__name__,
            },
            exc_info=True,
        )
        _record_shadow_validation(None, "failed")
        raise
    except RuntimeError as e:
        logger.error(
            "Shadow validation failed: validation runtime error",
            extra={
                "error": str(e),
            },
            exc_info=True,
        )
        _record_shadow_validation(None, "failed")
        raise


# ==============================================================================
# Redis Fallback Helpers (T6)
# ==============================================================================


def _record_fallback_buffer_metrics(buffered: int, dropped: int, size: int) -> None:
    """Record fallback buffer metrics after buffering events."""
    if buffered:
        signals_buffered_total.inc(buffered)
    if dropped:
        signals_dropped_total.inc(dropped)
    redis_fallback_buffer_size.set(size)


def _buffer_signal_payload(payload: str, reason: str) -> None:
    """Buffer a signal event payload when Redis publish fails."""
    if fallback_buffer is None:
        logger.warning(
            "Redis publish failed (%s) but fallback buffer is not initialized",
            reason,
        )
        return

    outcome = fallback_buffer.buffer_message(EventPublisher.CHANNEL_SIGNALS, payload)
    _record_fallback_buffer_metrics(outcome.buffered, outcome.dropped, outcome.size)
    logger.warning("Buffered signal event due to Redis publish failure: %s", reason)


def _publish_signal_event_with_fallback(event: SignalEvent) -> None:
    """Publish signal event to Redis or buffer locally on failure."""
    if not settings.redis_enabled:
        return

    try:
        payload = event.model_dump_json()
    except (TypeError, ValueError) as exc:
        logger.error("Failed to serialize signal event for publish: %s", exc)
        return

    if event_publisher is None:
        _buffer_signal_payload(payload, "publisher not initialized")
        return

    try:
        num_subscribers = event_publisher.publish_signal_event(event)
    except ValueError as exc:
        logger.error("Failed to serialize signal event for publish: %s", exc)
        return

    if num_subscribers is None:
        _buffer_signal_payload(payload, "redis publish error")


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
        status: Service health status ("healthy", "degraded", or "unhealthy")
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
        - 200: Service is healthy or degraded (warming)
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

    health_status = "healthy"
    if _should_hydrate_features() and not hydration_complete:
        health_status = "degraded"

    return HealthResponse(
        status=health_status,
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


@app.get("/ready", response_model=HealthResponse, tags=["Health"])
async def readiness_check() -> HealthResponse:
    """
    Readiness check endpoint.

    Returns 200 only when the service is fully healthy (including hydration).
    Returns 503 if service is degraded or unhealthy.
    """
    response = await health_check()
    if response.status != "healthy":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Service not ready: {response.status}",
        )
    return response


@app.post(
    "/api/v1/signals/generate",
    response_model=SignalResponse,
    tags=["Signals"],
    status_code=status.HTTP_200_OK,
)
async def generate_signals(
    request: SignalRequest,
    response: Response,
    # IMPORTANT: Auth must run BEFORE rate limiting to populate request.state with user context
    # This allows rate limiter to bucket by user/service instead of anonymous IP
    _auth_context: AuthContext = Depends(signal_generate_auth),
    _rate_limit_remaining: int = Depends(signal_generate_rl),
) -> SignalResponse:
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
        except (KeyError, TypeError) as exc:
            logger.error(
                "Signal generation failed: invalid data format",
                extra={
                    "symbols": request.symbols,
                    "as_of_date": as_of_date.date().isoformat(),
                    "strategy": (
                        model_registry.current_metadata.strategy_name
                        if model_registry.current_metadata
                        else "unknown"
                    ),
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Signal generation failed: {str(exc)}",
            ) from exc
        except OSError as exc:
            logger.error(
                "Signal generation failed: data file I/O error",
                extra={
                    "symbols": request.symbols,
                    "as_of_date": as_of_date.date().isoformat(),
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Signal generation failed: {str(exc)}",
            ) from exc
        except RedisConnectionError as exc:
            logger.error(
                "Signal generation failed: Redis connection error",
                extra={
                    "symbols": request.symbols,
                    "as_of_date": as_of_date.date().isoformat(),
                    "error": str(exc),
                },
                exc_info=True,
            )
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
        event_symbols: list[str] = []
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
                event_symbols.append(symbol)

        # Publish signal event to Redis (with fallback buffer on failure)
        strategy_id = (
            model_registry.current_metadata.strategy_name
            if model_registry.current_metadata
            else "unknown"
        )
        if event_symbols:
            _publish_signal_event_with_fallback(
                SignalEvent(
                    timestamp=datetime.now(UTC),
                    strategy_id=strategy_id,
                    symbols=event_symbols,
                    num_signals=len(signals),
                    as_of_date=as_of_date.date().isoformat(),
                )
            )

        # Record signal heartbeat to Redis (best-effort, P6T12.4)
        if redis_client is not None and settings.redis_enabled:
            try:
                redis_client.set(
                    f"signal:last_update:{strategy_id}",
                    datetime.now(UTC).isoformat(),
                )
            except (RedisConnectionError, OSError):
                logger.warning("signal_heartbeat_redis_failed", exc_info=True)

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
    except (ValueError, KeyError, TypeError) as e:
        request_status = "error"
        logger.error(
            "Unhandled failure in generate_signals: invalid data or type error",
            extra={
                "symbols": request.symbols if "request" in locals() else [],
                "error": str(e),
                "error_type": type(e).__name__,
            },
            exc_info=True,
        )
        raise
    except (FileNotFoundError, OSError) as e:
        request_status = "error"
        logger.error(
            "Unhandled failure in generate_signals: file or I/O error",
            extra={
                "symbols": request.symbols if "request" in locals() else [],
                "error": str(e),
                "error_type": type(e).__name__,
            },
            exc_info=True,
        )
        raise
    except RuntimeError as e:
        request_status = "error"
        logger.error(
            "Unhandled failure in generate_signals: runtime error",
            extra={
                "symbols": request.symbols if "request" in locals() else [],
                "error": str(e),
            },
            exc_info=True,
        )
        raise
    finally:
        # Always record metrics
        elapsed = time.time() - request_started
        signal_requests_total.labels(status=request_status).inc()
        signal_generation_duration.observe(elapsed)
        signal_generation_duration_seconds.observe(elapsed)


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
        had_current_model = model_registry.is_loaded

        # Trigger reload check
        logger.info("Manual model reload requested")
        reloaded = model_registry.reload_if_changed(
            strategy=settings.default_strategy,
            shadow_validator=_shadow_validate,
            shadow_validation_enabled=settings.shadow_validation_enabled,
            skip_shadow_validation=settings.skip_shadow_validation,
            schedule_validation=_schedule_shadow_validation,
            on_model_activated=_on_model_activated,
        )
        if had_current_model:
            _record_shadow_skip_if_bypassed(reloaded)

        # Get current version
        current_version = (
            model_registry.current_metadata.version
            if (model_registry.is_loaded and model_registry.current_metadata)
            else "none"
        )

        # Build response
        response = {"reloaded": reloaded, "version": current_version}

        if reloaded:
            response["previous_version"] = previous_version
            response["message"] = "Model reloaded successfully"
            logger.info(f"Manual reload successful: {previous_version} -> {current_version}")
        elif model_registry.pending_validation:
            response["pending_validation"] = True
            response["message"] = "Shadow validation in progress"
            logger.info("Manual reload initiated shadow validation")
        else:
            response["message"] = "Model already up to date"
            logger.info("Manual reload: no changes detected")

        return response

    except (ValueError, KeyError) as e:
        logger.error(
            "Manual model reload failed: invalid model data",
            extra={
                "strategy": settings.default_strategy,
                "error": str(e),
                "error_type": type(e).__name__,
            },
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Model reload failed: {str(e)}",
        ) from e
    except (FileNotFoundError, OSError) as e:
        logger.error(
            "Manual model reload failed: model file not accessible",
            extra={
                "strategy": settings.default_strategy,
                "error": str(e),
                "error_type": type(e).__name__,
            },
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Model reload failed: {str(e)}",
        ) from e
    except RedisConnectionError as e:
        logger.error(
            "Manual model reload failed: Redis connection error",
            extra={
                "strategy": settings.default_strategy,
                "error": str(e),
            },
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Model reload failed: {str(e)}",
        ) from e
    except RuntimeError as e:
        logger.error(
            "Manual model reload failed: runtime error",
            extra={
                "strategy": settings.default_strategy,
                "error": str(e),
            },
            exc_info=True,
        )
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

    Note: Uses env vars/defaults since settings is initialized in lifespan.
    For custom host/port, use uvicorn CLI directly or set HOST/PORT env vars.
    """
    uvicorn.run(
        "apps.signal_service.main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8001")),
        reload=True,  # Auto-reload on code changes (dev only)
        log_level="info",
    )
