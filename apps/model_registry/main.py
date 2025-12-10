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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from libs.models import ManifestIntegrityError, ModelRegistry, RegistryManifestManager

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
            except Exception as e:
                logger.error(f"Manifest integrity check failed: {e}")
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

    except Exception as e:
        logger.error(f"Failed to start Model Registry Service: {e}", exc_info=True)
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


# CORS configuration
ENVIRONMENT = os.getenv("ENVIRONMENT", "production").lower()
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "")

if ALLOWED_ORIGINS:
    cors_origins = [o.strip() for o in ALLOWED_ORIGINS.split(",") if o.strip()]
    if "*" in cors_origins:
        raise RuntimeError(
            "ALLOWED_ORIGINS cannot contain wildcard '*' when credentials are enabled"
        )
elif ENVIRONMENT in ("dev", "test"):
    cors_origins = [
        "http://localhost:8501",
        "http://127.0.0.1:8501",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
else:
    raise RuntimeError("ALLOWED_ORIGINS must be set for production environments")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# Routes
# =============================================================================


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
