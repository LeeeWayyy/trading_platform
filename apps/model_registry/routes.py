"""
FastAPI routes for Model Registry API.

Endpoints:
- GET /api/v1/models/{model_type}/current - Get current production model
- GET /api/v1/models/{model_type}/{version} - Get specific model metadata
- POST /api/v1/models/{model_type}/{version}/validate - Validate model
- GET /api/v1/models/{model_type} - List models by type
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from libs.models.models import (
    IntegrityError,
    ModelRegistry,
    ModelStatus,
    ModelType,
    RegistryLockError,
)

from .auth import ServiceToken, verify_read_scope, verify_write_scope
from .schemas import (
    ERROR_CHECKSUM_MISMATCH,
    ERROR_MODEL_NOT_FOUND,
    ERROR_REGISTRY_LOCKED,
    ERROR_VALIDATION_FAILED,
    CurrentModelResponse,
    EnvironmentMetadataResponse,
    ErrorResponse,
    ModelListResponse,
    ModelMetadataResponse,
    ValidationResultResponse,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Router Setup
# =============================================================================


router = APIRouter(prefix="/api/v1/models", tags=["Models"])


# Global registry instance (set by main.py)
_registry: ModelRegistry | None = None


def get_registry() -> ModelRegistry:
    """Get registry instance.

    Returns:
        ModelRegistry instance.

    Raises:
        HTTPException 503: If registry is not initialized.
    """
    if _registry is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"detail": "Registry not initialized", "code": ERROR_REGISTRY_LOCKED},
        )
    return _registry


def set_registry(registry: ModelRegistry) -> None:
    """Set global registry instance.

    Args:
        registry: ModelRegistry instance.
    """
    global _registry
    _registry = registry


# =============================================================================
# Helper Functions
# =============================================================================


def _metadata_to_response(
    metadata: Any,
    model_status: str | None = None,
    artifact_path: str | None = None,
    promoted_at: datetime | None = None,
) -> ModelMetadataResponse:
    """Convert ModelMetadata to API response.

    Args:
        metadata: ModelMetadata instance.
        model_status: Model status from registry DB (if available).
        artifact_path: Actual artifact path from registry DB (if available).
        promoted_at: Promotion timestamp from registry DB (if available).

    Returns:
        ModelMetadataResponse.
    """
    if artifact_path is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "detail": "Artifact path missing for model metadata",
                "code": ERROR_CHECKSUM_MISMATCH,
            },
        )

    return ModelMetadataResponse(
        model_id=metadata.model_id,
        model_type=metadata.model_type.value,
        version=metadata.version,
        status=model_status or "staged",  # Default to staged if not provided
        artifact_path=artifact_path,
        checksum_sha256=metadata.checksum_sha256,
        dataset_version_ids=metadata.dataset_version_ids,
        snapshot_id=metadata.snapshot_id,
        factor_list=metadata.factor_list,
        parameters=metadata.parameters,
        metrics=metadata.metrics,
        config=metadata.config,
        config_hash=metadata.config_hash,
        feature_formulas=metadata.feature_formulas,
        env=EnvironmentMetadataResponse(
            python_version=metadata.env.python_version,
            dependencies_hash=metadata.env.dependencies_hash,
            platform=metadata.env.platform,
            created_by=metadata.env.created_by,
            numpy_version=metadata.env.numpy_version,
            polars_version=metadata.env.polars_version,
            sklearn_version=metadata.env.sklearn_version,
            cvxpy_version=metadata.env.cvxpy_version,
        ),
        experiment_id=metadata.experiment_id,
        run_id=metadata.run_id,
        dataset_uri=metadata.dataset_uri,
        qlib_version=metadata.qlib_version,
        created_at=metadata.created_at,
        promoted_at=promoted_at,
    )


# =============================================================================
# Endpoints
# =============================================================================


@router.get(
    "/{model_type}/current",
    response_model=CurrentModelResponse,
    responses={
        404: {"model": ErrorResponse, "description": "No production model found"},
        503: {"model": ErrorResponse, "description": "Registry unavailable"},
    },
)
def get_current_model(
    model_type: ModelType,
    auth: Annotated[ServiceToken, Depends(verify_read_scope)],
    registry: Annotated[ModelRegistry, Depends(get_registry)],
) -> CurrentModelResponse:
    """Get current production model for a type.

    Args:
        model_type: Type of model (risk_model, alpha_weights, etc.).
        auth: Verified service token with model:read scope.
        registry: Model registry instance.

    Returns:
        CurrentModelResponse with version, checksum, and dataset versions.

    Raises:
        HTTPException 404: If no production model exists.
        HTTPException 503: If registry is unavailable.
    """
    try:
        metadata = registry.get_current_production(model_type.value)
    except RegistryLockError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"detail": str(e), "code": ERROR_REGISTRY_LOCKED},
        ) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "detail": f"Registry integrity check failed: {e}",
                "code": ERROR_CHECKSUM_MISMATCH,
            },
        ) from e

    if metadata is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "detail": f"No production model found for {model_type.value}",
                "code": ERROR_MODEL_NOT_FOUND,
            },
        )

    logger.info(
        "Retrieved current production model",
        extra={
            "model_type": model_type.value,
            "version": metadata.version,
            "service": auth.service_name,
        },
    )

    return CurrentModelResponse(
        model_type=model_type.value,
        version=metadata.version,
        checksum=metadata.checksum_sha256,
        dataset_version_ids=metadata.dataset_version_ids,
    )


@router.get(
    "/{model_type}/{version}",
    response_model=ModelMetadataResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Model not found"},
        503: {"model": ErrorResponse, "description": "Registry unavailable"},
    },
)
def get_model_metadata(
    model_type: ModelType,
    version: str,
    auth: Annotated[ServiceToken, Depends(verify_read_scope)],
    registry: Annotated[ModelRegistry, Depends(get_registry)],
) -> ModelMetadataResponse:
    """Get full metadata for a specific model version.

    Args:
        model_type: Type of model.
        version: Semantic version (e.g., v1.0.0).
        auth: Verified service token with model:read scope.
        registry: Model registry instance.

    Returns:
        ModelMetadataResponse with full metadata.

    Raises:
        HTTPException 404: If model/version not found.
        HTTPException 503: If registry is unavailable.
    """
    try:
        metadata = registry.get_model_metadata(model_type.value, version)
    except RegistryLockError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"detail": str(e), "code": ERROR_REGISTRY_LOCKED},
        ) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "detail": f"Registry integrity check failed: {e}",
                "code": ERROR_CHECKSUM_MISMATCH,
            },
        ) from e

    if metadata is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "detail": f"Model {model_type.value}/{version} not found",
                "code": ERROR_MODEL_NOT_FOUND,
            },
        )

    # Get status and artifact_path from DB
    model_info = registry.get_model_info(model_type.value, version)

    logger.info(
        "Retrieved model metadata",
        extra={
            "model_type": model_type.value,
            "version": version,
            "model_id": metadata.model_id,
            "service": auth.service_name,
        },
    )

    return _metadata_to_response(
        metadata,
        model_status=model_info["status"] if model_info else None,
        artifact_path=model_info["artifact_path"] if model_info else None,
        promoted_at=model_info["promoted_at"] if model_info else None,
    )


@router.post(
    "/{model_type}/{version}/validate",
    response_model=ValidationResultResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Model not found"},
        422: {"model": ErrorResponse, "description": "Validation failed"},
        503: {"model": ErrorResponse, "description": "Registry unavailable"},
    },
)
def validate_model(
    model_type: ModelType,
    version: str,
    auth: Annotated[ServiceToken, Depends(verify_write_scope)],
    registry: Annotated[ModelRegistry, Depends(get_registry)],
) -> ValidationResultResponse:
    """Validate model artifact integrity and loadability.

    Performs:
    1. Checksum verification (SHA-256)
    2. Test load of model artifact

    Args:
        model_type: Type of model.
        version: Semantic version.
        auth: Verified service token with model:write scope.
        registry: Model registry instance.

    Returns:
        ValidationResultResponse with validation status.

    Raises:
        HTTPException 404: If model/version not found.
        HTTPException 422: If validation fails (checksum mismatch, load error).
        HTTPException 503: If registry is unavailable.
    """
    try:
        result = registry.validate_model(model_type.value, version)
    except RegistryLockError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"detail": str(e), "code": ERROR_REGISTRY_LOCKED},
        ) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "detail": f"Registry integrity check failed: {e}",
                "code": ERROR_CHECKSUM_MISMATCH,
            },
        ) from e

    if not result.model_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "detail": f"Model {model_type.value}/{version} not found",
                "code": ERROR_MODEL_NOT_FOUND,
            },
        )

    logger.info(
        "Validated model",
        extra={
            "model_type": model_type.value,
            "version": version,
            "valid": result.valid,
            "checksum_ok": result.checksum_verified,
            "load_ok": result.load_successful,
            "service": auth.service_name,
        },
    )

    if not result.valid:
        # Return 422 for validation failures
        error_code = (
            ERROR_CHECKSUM_MISMATCH if not result.checksum_verified else ERROR_VALIDATION_FAILED
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "detail": "; ".join(result.errors),
                "code": error_code,
            },
        )

    return ValidationResultResponse(
        valid=result.valid,
        model_id=result.model_id,
        checksum_verified=result.checksum_verified,
        load_successful=result.load_successful,
        errors=result.errors,
    )


@router.get(
    "/{model_type}",
    response_model=ModelListResponse,
    responses={
        503: {"model": ErrorResponse, "description": "Registry unavailable"},
    },
)
def list_models(
    model_type: ModelType,
    auth: Annotated[ServiceToken, Depends(verify_read_scope)],
    registry: Annotated[ModelRegistry, Depends(get_registry)],
    status_filter: ModelStatus | None = None,
) -> ModelListResponse:
    """List models of a specific type.

    Args:
        model_type: Type of model to list.
        auth: Verified service token with model:read scope.
        registry: Model registry instance.
        status_filter: Optional status filter (staged/production/archived).

    Returns:
        ModelListResponse with list of models.

    Raises:
        HTTPException 503: If registry is unavailable.
    """
    try:
        models = registry.list_models(model_type.value, status_filter)
    except RegistryLockError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"detail": str(e), "code": ERROR_REGISTRY_LOCKED},
        ) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "detail": f"Registry integrity check failed: {e}",
                "code": ERROR_CHECKSUM_MISMATCH,
            },
        ) from e

    # Fetch DB info (status, artifact_path, promoted_at) for accurate responses
    versions = [m.version for m in models]
    db_info = registry.get_model_info_bulk(model_type.value, versions)

    responses: list[ModelMetadataResponse] = []
    for m in models:
        info = db_info.get(m.version)
        if not info:
            logger.error(
                "Missing DB metadata for model listed in manifest",
                extra={"model_type": m.model_type.value, "version": m.version},
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"detail": "Registry metadata incomplete", "code": ERROR_CHECKSUM_MISMATCH},
            )

        responses.append(
            _metadata_to_response(
                m,
                model_status=info.get("status"),
                artifact_path=info.get("artifact_path"),
                promoted_at=info.get("promoted_at"),
            )
        )

    logger.info(
        "Listed models",
        extra={
            "model_type": model_type.value,
            "status_filter": status_filter.value if status_filter else None,
            "count": len(models),
            "service": auth.service_name,
        },
    )

    return ModelListResponse(
        models=responses,
        total=len(models),
    )
