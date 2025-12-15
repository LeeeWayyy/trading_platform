"""
Request/Response schemas for Model Registry API.

Defines Pydantic models for API validation and serialization.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# =============================================================================
# Response Models
# =============================================================================


class EnvironmentMetadataResponse(BaseModel):
    """Environment metadata in API response."""

    python_version: str = Field(..., description="Python version")
    dependencies_hash: str = Field(..., description="Hash of dependencies")
    platform: str = Field(..., description="Platform identifier")
    created_by: str = Field(..., description="Creator identifier")
    numpy_version: str = Field(..., description="NumPy version")
    polars_version: str = Field(..., description="Polars version")
    sklearn_version: str | None = Field(None, description="scikit-learn version")
    cvxpy_version: str | None = Field(None, description="CVXPY version")


class CurrentModelResponse(BaseModel):
    """Response for GET /{model_type}/current endpoint."""

    model_type: str = Field(..., description="Type of model artifact")
    version: str = Field(..., description="Semantic version")
    checksum: str = Field(..., description="SHA-256 checksum")
    dataset_version_ids: dict[str, str] = Field(
        ..., description="Dataset versions used for training"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "model_type": "risk_model",
                "version": "v1.0.0",
                "checksum": "abc123def456...",
                "dataset_version_ids": {"crsp": "v1.2.3", "compustat": "v1.0.1"},
            }
        }
    }


class ModelMetadataResponse(BaseModel):
    """Response for GET /{model_type}/{version} endpoint."""

    model_id: str = Field(..., description="Unique model identifier")
    model_type: str = Field(..., description="Type of model artifact")
    version: str = Field(..., description="Semantic version")
    status: str = Field(..., description="Model status (staged/production/archived)")
    artifact_path: str = Field(..., description="Path to artifact directory")
    checksum_sha256: str = Field(..., description="SHA-256 checksum")
    dataset_version_ids: dict[str, str] = Field(
        ..., description="Dataset versions used for training"
    )
    snapshot_id: str = Field(..., description="Snapshot identifier")
    factor_list: list[str] = Field(..., description="Factors used in model")
    parameters: dict[str, Any] = Field(..., description="Model parameters")
    metrics: dict[str, float] = Field(..., description="Performance metrics")
    config: dict[str, Any] = Field(..., description="Training configuration")
    config_hash: str = Field(..., description="Hash of config")
    feature_formulas: list[str] | None = Field(None, description="Feature formulas")
    env: EnvironmentMetadataResponse = Field(..., description="Environment metadata")
    experiment_id: str | None = Field(None, description="Experiment ID (Qlib)")
    run_id: str | None = Field(None, description="Run ID (Qlib)")
    dataset_uri: str | None = Field(None, description="Dataset URI")
    qlib_version: str | None = Field(None, description="Qlib version")
    created_at: datetime = Field(..., description="Creation timestamp")
    promoted_at: datetime | None = Field(None, description="Promotion timestamp")


class ValidationResultResponse(BaseModel):
    """Response for POST /{model_type}/{version}/validate endpoint."""

    valid: bool = Field(..., description="Whether model is valid")
    model_id: str = Field(..., description="Model identifier")
    checksum_verified: bool = Field(..., description="Checksum verification passed")
    load_successful: bool = Field(..., description="Model loaded successfully")
    errors: list[str] = Field(default_factory=list, description="Validation errors")

    model_config = {
        "json_schema_extra": {
            "example": {
                "valid": True,
                "model_id": "uuid-1234",
                "checksum_verified": True,
                "load_successful": True,
                "errors": [],
            }
        }
    }


class ModelListResponse(BaseModel):
    """Response for listing models."""

    models: list[ModelMetadataResponse] = Field(..., description="List of models")
    total: int = Field(..., description="Total count")


class ErrorResponse(BaseModel):
    """Standard error response."""

    detail: str = Field(..., description="Error message")
    code: str = Field(..., description="Error code")

    model_config = {
        "json_schema_extra": {
            "example": {
                "detail": "Model risk_model/v1.0.0 not found",
                "code": "MODEL_NOT_FOUND",
            }
        }
    }


# =============================================================================
# HTTP Error Codes
# =============================================================================


HTTP_ERRORS = {
    200: "Success",
    404: "Model/version not found",
    409: "Version already exists (immutable, conflict)",
    422: "Checksum mismatch, corrupt artifact, or missing required fields",
    503: "Registry temporarily locked or unavailable",
}

# Error code constants
ERROR_MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
ERROR_VERSION_EXISTS = "VERSION_EXISTS"
ERROR_CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"
ERROR_REGISTRY_LOCKED = "REGISTRY_LOCKED"
ERROR_VALIDATION_FAILED = "VALIDATION_FAILED"
ERROR_MISSING_FIELDS = "MISSING_REQUIRED_FIELDS"
