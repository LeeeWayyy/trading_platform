"""
FastAPI application for Model Registry service.

This service provides REST API endpoints for:
- Model metadata retrieval
- Model validation
- Model listing

Configuration via environment variables:
- MODEL_REGISTRY_DIR: Path to registry directory
- MODEL_REGISTRY_TOKEN: Bearer token for authentication

Example:
    Start the service:
        $ uvicorn apps.model_registry.main:app --host 0.0.0.0 --port 8003

    Get current production model:
        $ curl -H "Authorization: Bearer $TOKEN" \\
            http://localhost:8003/api/v1/models/risk_model/current
"""

from __future__ import annotations

import logging
import os
import pickle
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from libs.models.models import ManifestIntegrityError, ModelRegistry, RegistryManifestManager

from .error_handlers import install_error_handlers
from .routes import router, set_registry

# =============================================================================
# Configuration
# =============================================================================


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_settings() -> dict[str, Any]:
    """Get configuration from environment."""
    return {
        "registry_dir": Path(os.environ.get("MODEL_REGISTRY_DIR", "data/models")),
        "host": os.environ.get("MODEL_REGISTRY_HOST", "0.0.0.0"),
        "port": int(os.environ.get("MODEL_REGISTRY_PORT", "8003")),
        # Auth disable flag was removed for security: always enforce auth
        "auth_disabled": False,
    }


settings = get_settings()


# =============================================================================
# Application Lifespan
# =============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application startup and shutdown.

    Startup:
    1. Initialize ModelRegistry
    2. Verify registry integrity

    Shutdown:
    1. Clean up resources
    """
    logger.info("=" * 60)
    logger.info("Model Registry Service Starting...")
    logger.info("=" * 60)

    try:
        # Validate and populate CORS origins during startup (not at import time)
        # so validation errors go through the normal lifespan error path.
        # Middleware is already registered at module level with _cors_allow_origins;
        # we populate the shared list in-place here.
        _cors_allow_origins.clear()  # clear first to avoid stale origins on failure
        cors_origins = _resolve_cors_origins()
        _cors_allow_origins[:] = cors_origins

        # Verify CORSMiddleware kept a live reference to _cors_allow_origins.
        # Raises RuntimeError if the reference is broken (CORS would silently
        # fail).  Logs ERROR if middleware is missing from stack (less severe).
        _verify_cors_middleware_uses_shared_origins(app)

        logger.info(
            "CORS configured",
            extra={"origin_count": len(cors_origins)},
        )

        if os.environ.get("MODEL_REGISTRY_AUTH_DISABLED", "").lower() == "true":
            # Fail closed: auth bypass is not permitted; require proper tokens even in dev
            raise RuntimeError(
                "MODEL_REGISTRY_AUTH_DISABLED is unsupported. Remove the flag and use dev tokens instead."
            )

        # Initialize registry
        registry_dir = settings["registry_dir"]
        logger.info(f"Initializing registry: {registry_dir}")

        # NOTE: DatasetVersionManager is not initialized for this read-only API service.
        # Lineage validation (dataset/snapshot) happens during model registration via CLI.
        # This service only serves model metadata and validation - no registration endpoints.
        # The CLI (scripts/model_cli.py) should initialize DatasetVersionManager when needed.
        logger.info(
            "DatasetVersionManager not initialized - this read-only API service "
            "relies on CLI for registration-time lineage validation"
        )

        registry = ModelRegistry(registry_dir=registry_dir)
        set_registry(registry)

        # Verify manifest integrity - fail fast if corrupted
        manifest_manager = RegistryManifestManager(registry_dir)
        if manifest_manager.exists():
            try:
                if not manifest_manager.verify_integrity():
                    raise ManifestIntegrityError(
                        message="Manifest checksum does not match registry state"
                    )
                logger.info("Manifest integrity verified")
            except (FileNotFoundError, OSError) as e:
                # File system errors during integrity check
                logger.error(
                    "Manifest integrity check failed: file system error",
                    extra={
                        "error_type": type(e).__name__,
                        "error": str(e),
                        "registry_dir": str(registry_dir),
                    },
                    exc_info=True,
                )
                raise ManifestIntegrityError(message=f"Integrity check failed: {e}") from e
            except (ValueError, pickle.PickleError) as e:
                # Data corruption or invalid manifest format
                logger.error(
                    "Manifest integrity check failed: data corruption or invalid format",
                    extra={
                        "error_type": type(e).__name__,
                        "error": str(e),
                        "registry_dir": str(registry_dir),
                    },
                    exc_info=True,
                )
                raise ManifestIntegrityError(message=f"Integrity check failed: {e}") from e

        # Load manifest info
        manifest = registry.get_manifest()
        logger.info(
            f"Registry loaded: {manifest.artifact_count} artifacts, "
            f"{len(manifest.production_models)} production models"
        )

        logger.info("=" * 60)
        logger.info("Model Registry Service Ready!")
        logger.info(f"  - Registry: {registry_dir}")
        logger.info(f"  - Artifacts: {manifest.artifact_count}")
        logger.info(f"  - Production: {list(manifest.production_models.keys())}")
        logger.info("  - Auth: ENFORCED (MODEL_REGISTRY_AUTH_DISABLED removed)")
        logger.info("=" * 60)

        yield

    except (RuntimeError, ManifestIntegrityError) as e:
        # Expected startup failures: auth config, manifest integrity
        logger.error(
            "Failed to start Model Registry Service: configuration or integrity error",
            extra={
                "error_type": type(e).__name__,
                "error": str(e),
                "registry_dir": str(settings["registry_dir"]),
            },
            exc_info=True,
        )
        raise
    except (FileNotFoundError, OSError) as e:
        # File system errors during startup
        logger.error(
            "Failed to start Model Registry Service: file system error",
            extra={
                "error_type": type(e).__name__,
                "error": str(e),
                "registry_dir": str(settings["registry_dir"]),
            },
            exc_info=True,
        )
        raise
    except (ValueError, pickle.PickleError) as e:
        # Data corruption or invalid model format
        logger.error(
            "Failed to start Model Registry Service: data corruption or invalid format",
            extra={
                "error_type": type(e).__name__,
                "error": str(e),
                "registry_dir": str(settings["registry_dir"]),
            },
            exc_info=True,
        )
        raise

    finally:
        logger.info("Model Registry Service shutting down...")


# =============================================================================
# FastAPI Application
# =============================================================================


app = FastAPI(
    title="Model Registry API",
    description="REST API for model metadata retrieval and validation",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


# =============================================================================
# Middleware
# =============================================================================


def _resolve_cors_origins() -> list[str]:
    """Resolve and validate CORS origins from environment variables.

    Called during lifespan startup so validation errors go through the normal
    startup/error path instead of crashing at import time.

    Returns the resolved origin list.

    Raises:
        RuntimeError: If ALLOWED_ORIGINS contains wildcard '*' or is unset in production.
    """
    environment = os.getenv("ENVIRONMENT", "production").lower()
    allowed_origins = os.getenv("ALLOWED_ORIGINS", "")

    if allowed_origins:
        cors_origins = [o.strip() for o in allowed_origins.split(",") if o.strip()]
        if "*" in cors_origins:
            raise RuntimeError(
                "ALLOWED_ORIGINS cannot contain wildcard '*' when credentials are enabled"
            )
    elif environment in ("dev", "test"):
        cors_origins = [
            "http://localhost:8501",
            "http://127.0.0.1:8501",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]
    else:
        raise RuntimeError("ALLOWED_ORIGINS must be set for production environments")

    return cors_origins


# Mutable list populated during lifespan startup.  Registered with the CORS
# middleware at module level (required before ASGI startup) and filled in-place
# by _resolve_cors_origins() during lifespan so that configuration errors go
# through the normal startup error path instead of crashing at import time
# (see issue #156).
#
# Design constraints:
# 1. Process-global by design: this service uses a single-app-per-process
#    architecture (one uvicorn worker = one app instance).  The slice
#    assignment ``_cors_allow_origins[:] = cors_origins`` ensures idempotent
#    replacement across test restarts.
# 2. Requires ASGI lifespan: CORS origins are populated during lifespan
#    startup.  Running with ``--lifespan off`` would leave origins empty,
#    effectively blocking all cross-origin requests (fail-closed).
_cors_allow_origins: list[str] = []

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _verify_cors_middleware_uses_shared_origins(target_app: FastAPI) -> None:
    """Ensure CORSMiddleware holds a live reference to _cors_allow_origins.

    Starlette currently stores ``allow_origins`` by reference, so mutating
    the list in-place during lifespan works.  If a future Starlette version
    copies or freezes the sequence at init, this function explicitly
    reassigns the shared list reference onto the middleware instance so
    that in-place mutations (``_cors_allow_origins[:] = ...``) are always
    reflected at runtime.

    If CORSMiddleware is not found in the stack at all (less severe -- could
    be a test or config issue), an ERROR is logged but startup continues.

    Note:
        ``_cors_allow_origins`` is process-global by design (single-app-per-
        process architecture).  See the module-level comment above the list
        declaration for rationale.
    """
    # ``middleware_stack`` is a plain instance attribute (not a property) in
    # Starlette <=0.36 and is ``None`` until the ASGI app is started.  When
    # lifespan is called on a bare FastAPI instance (e.g. in unit tests that
    # do ``async with lifespan(FastAPI())``), the stack has not been built
    # yet, so skip the guard silently.
    if target_app.middleware_stack is None:
        logger.debug(
            "CORS middleware guard skipped",
            extra={"reason": "middleware_stack is None (bare FastAPI instance)"},
        )
        return

    current: Any = target_app.middleware_stack
    while current is not None:
        if isinstance(current, CORSMiddleware):
            if current.allow_origins is not _cors_allow_origins:
                # Starlette copied or froze the sequence during init.
                # Force the middleware to use our shared list reference so
                # that in-place mutations during lifespan are reflected.
                logger.warning(
                    "CORSMiddleware copied allow_origins during init; "
                    "reassigning shared reference",
                    extra={"middleware_type": type(current).__name__},
                )
                current.allow_origins = _cors_allow_origins
            return
        current = getattr(current, "app", None)

    # CORSMiddleware not found — log error but allow startup.
    logger.error(
        "CORSMiddleware not found in middleware stack",
        extra={"middleware_type": type(target_app.middleware_stack).__name__},
    )


# =============================================================================
# Routes
# =============================================================================


# Register custom error handlers BEFORE including the router so any HTTPException
# raised from a route is flattened to the {"detail": str, "code": str} shape
# declared by ErrorResponse (issue #166).
install_error_handlers(app)

app.include_router(router)


@app.get("/", tags=["Root"])
async def root() -> dict[str, Any]:
    """Root endpoint with service information."""
    return {
        "service": "Model Registry API",
        "version": "1.0.0",
        "description": "REST API for model metadata and validation",
        "docs": "/docs",
        "health": "/health",
        "api": "/api/v1/models",
    }


@app.get("/health", tags=["Health"])
async def health_check() -> dict[str, Any]:
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "model_registry",
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


# =============================================================================
# Error Handlers
# =============================================================================


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Global exception handler for unexpected errors."""
    logger.error(
        f"Unhandled exception on {request.method} {request.url.path}",
        exc_info=exc,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Internal server error",
            "code": "INTERNAL_ERROR",
            "path": str(request.url.path),
        },
    )


# =============================================================================
# Main Entry Point
# =============================================================================


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "apps.model_registry.main:app",
        host=settings["host"],
        port=settings["port"],
        reload=True,
        log_level="info",
    )
