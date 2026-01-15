"""
Core data models for Model Registry.

This module provides:
- ModelType: Enum for supported model artifact types
- ModelStatus: Enum for model lifecycle states
- EnvironmentMetadata: Capture of environment at model creation
- ModelMetadata: Complete model provenance and metadata
- Per-artifact required field validation

Key design decisions:
- Pydantic models with strict validation for type safety
- Immutable versioning (versions cannot be overwritten)
- Complete dataset version linkage to P4T1 registry
- Nullable Qlib fields for non-Qlib models
- Per-artifact type validation ensures required fields are present
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ModelType(str, Enum):
    """Supported model artifact types.

    Each type has specific required metadata fields.
    """

    risk_model = "risk_model"
    alpha_weights = "alpha_weights"
    factor_definitions = "factor_definitions"
    feature_transforms = "feature_transforms"


class ModelStatus(str, Enum):
    """Model lifecycle states.

    State transitions:
    - staged -> production (promote)
    - production -> archived (new promotion)
    - staged -> failed (validation failure)
    - production -> staged (rollback)
    """

    staged = "staged"
    production = "production"
    archived = "archived"
    failed = "failed"


class EnvironmentMetadata(BaseModel):
    """Environment capture at model creation time.

    Tracks all relevant environment details for reproducibility.
    """

    python_version: str = Field(..., description="e.g., '3.11.5'")
    dependencies_hash: str = Field(..., description="SHA-256 of sorted requirements.txt")
    platform: str = Field(..., description="e.g., 'linux-x86_64'")
    created_by: str = Field(..., description="User/service that created the model")
    numpy_version: str = Field(..., description="NumPy version")
    polars_version: str = Field(..., description="Polars version")
    sklearn_version: str | None = Field(None, description="scikit-learn version if used")
    cvxpy_version: str | None = Field(None, description="CVXPY version if used")

    model_config = {"frozen": True, "extra": "forbid"}


class ModelMetadata(BaseModel):
    """Complete model metadata for provenance tracking.

    Contains all information needed to:
    - Reproduce the training environment
    - Verify data lineage (dataset_version_ids, snapshot_id)
    - Track model performance (metrics)
    - Validate artifact integrity (checksum_sha256)
    """

    model_id: str = Field(..., description="Unique model identifier")
    model_type: ModelType = Field(..., description="Type of model artifact")
    version: str = Field(
        ..., pattern=r"^v\d+\.\d+\.\d+$", description="Semantic version (immutable)"
    )
    created_at: datetime = Field(..., description="Creation timestamp (UTC)")

    # Provenance tracking - full linkage to P4T1
    dataset_version_ids: dict[str, str] = Field(
        ..., description="Dataset versions: {'crsp': 'v1.2.3', 'compustat': 'v1.0.1'}"
    )
    snapshot_id: str = Field(..., description="DatasetVersionManager snapshot ID")
    factor_list: list[str] = Field(default_factory=list, description="List of factors used")
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Model parameters including artifact-specific fields"
    )

    # Validation
    checksum_sha256: str = Field(..., description="SHA-256 checksum of artifact")
    metrics: dict[str, float] = Field(
        default_factory=dict, description="Performance metrics (IC, Sharpe, etc.)"
    )
    env: EnvironmentMetadata = Field(..., description="Environment at creation")

    # Training config
    config: dict[str, Any] = Field(default_factory=dict, description="Hyperparameters and settings")
    config_hash: str = Field(..., description="SHA-256 of config dict")
    feature_formulas: list[str] | None = Field(
        None, description="Phase 3 placeholder for FormulaicFactor"
    )

    # Qlib compatibility (nullable for non-Qlib models)
    experiment_id: str | None = Field(None, description="Experiment grouping ID")
    run_id: str | None = Field(None, description="Individual training run ID")
    dataset_uri: str | None = Field(None, description="Reference to dataset location")
    qlib_version: str | None = Field(None, description="Qlib version if used")

    model_config = {"frozen": True, "extra": "forbid"}

    @field_validator("created_at")
    @classmethod
    def validate_utc(cls, v: datetime) -> datetime:
        """Ensure timestamp is UTC."""
        if v.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        offset = v.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("created_at must be UTC")
        return v


# =============================================================================
# Per-Artifact Required Metadata
# =============================================================================


ARTIFACT_REQUIRED_FIELDS: dict[ModelType, list[str]] = {
    ModelType.risk_model: ["factor_list", "halflife_days", "shrinkage_intensity"],
    ModelType.alpha_weights: ["alpha_names", "combination_method", "ic_threshold"],
    ModelType.factor_definitions: ["factor_names", "categories", "lookback_days"],
    ModelType.feature_transforms: ["feature_names", "normalization_params"],
}
"""Per-artifact type required fields in parameters dict."""


class MissingRequiredFieldError(Exception):
    """Raised when artifact-specific required fields are missing."""

    def __init__(self, model_type: ModelType, missing_fields: list[str]) -> None:
        self.model_type = model_type
        self.missing_fields = missing_fields
        super().__init__(f"Artifact type {model_type.value} requires fields: {missing_fields}")


class InvalidDatasetVersionError(Exception):
    """Raised when dataset version is not found or mismatched in P4T1 registry."""

    def __init__(self, dataset: str, version: str, message: str | None = None) -> None:
        self.dataset = dataset
        self.version = version
        if message:
            super().__init__(f"Dataset version {dataset}:{version} - {message}")
        else:
            super().__init__(f"Dataset version {dataset}:{version} not found in P4T1 registry")


class InvalidSnapshotError(Exception):
    """Raised when snapshot_id is not found."""

    def __init__(self, snapshot_id: str) -> None:
        self.snapshot_id = snapshot_id
        super().__init__(f"Snapshot {snapshot_id} not found")


def validate_artifact_metadata(model_type: ModelType, metadata: ModelMetadata) -> None:
    """Validate required fields are present in parameters dict.

    Args:
        model_type: The type of model artifact.
        metadata: The model metadata to validate.

    Raises:
        MissingRequiredFieldError: If required fields are missing.
    """
    required = ARTIFACT_REQUIRED_FIELDS.get(model_type, [])
    missing = [f for f in required if f not in metadata.parameters]
    if missing:
        raise MissingRequiredFieldError(model_type, missing)


# =============================================================================
# Promotion Gates
# =============================================================================


@dataclass
class PromotionGates:
    """Thresholds for model promotion to production.

    Models must pass all gates to be promoted:
    - IC must exceed min_ic threshold
    - Sharpe ratio must exceed min_sharpe threshold
    - Model must have min_paper_trade_hours in paper trading
    """

    min_ic: float = 0.02
    min_sharpe: float = 0.5
    min_paper_trade_hours: int = 24


class PromotionGateError(Exception):
    """Raised when promotion gate is not met."""

    def __init__(self, gate: str, value: float, threshold: float) -> None:
        self.gate = gate
        self.value = value
        self.threshold = threshold
        super().__init__(f"Promotion gate failed: {gate}={value} < threshold={threshold}")


# =============================================================================
# Result Types
# =============================================================================


@dataclass
class PromotionResult:
    """Result of model promotion."""

    success: bool
    model_id: str
    from_version: str | None
    to_version: str
    promoted_at: datetime
    message: str = ""


@dataclass
class RollbackResult:
    """Result of model rollback."""

    success: bool
    model_type: ModelType
    from_version: str
    to_version: str | None
    rolled_back_at: datetime
    message: str = ""


@dataclass
class ValidationResult:
    """Result of model validation."""

    valid: bool
    model_id: str
    checksum_verified: bool
    load_successful: bool
    errors: list[str] = field(default_factory=list)


@dataclass
class ArtifactInfo:
    """Information about a serialized artifact."""

    path: str
    checksum: str
    size_bytes: int
    serialized_at: datetime


# =============================================================================
# Manifest Types
# =============================================================================


class RegistryManifest(BaseModel):
    """Registry-level manifest for discoverability and DR.

    Updated atomically with registry changes.
    """

    registry_version: str = Field("1.0.0", description="Schema version")
    created_at: datetime = Field(..., description="When registry was created")
    last_updated: datetime = Field(..., description="Last update timestamp")
    artifact_count: int = Field(0, description="Total number of artifacts")
    production_models: dict[str, str] = Field(
        default_factory=dict, description="{model_type: version}"
    )
    total_size_bytes: int = Field(0, description="Total storage used")
    checksum: str = Field(..., description="SHA-256 of registry.db")

    # DR fields
    last_backup_at: datetime | None = Field(None, description="Last backup timestamp")
    backup_location: str | None = Field(None, description="S3/GCS path if configured")

    model_config = {"extra": "forbid"}

    @field_validator("created_at", "last_updated")
    @classmethod
    def validate_utc_manifest(cls, v: datetime) -> datetime:
        """Ensure timestamp is UTC."""
        if v.tzinfo is None:
            raise ValueError("Timestamps must be timezone-aware")
        offset = v.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("Timestamps must be UTC")
        return v


# =============================================================================
# Backup Types
# =============================================================================


@dataclass
class BackupManifest:
    """Manifest for a registry backup."""

    backup_id: str
    created_at: datetime
    source_path: str
    backup_path: str
    checksum: str
    size_bytes: int


@dataclass
class RestoreResult:
    """Result of backup restoration."""

    success: bool
    backup_date: datetime
    restored_at: datetime
    models_restored: int
    message: str = ""


@dataclass
class SyncResult:
    """Result of remote sync."""

    success: bool
    remote_path: str
    synced_at: datetime
    bytes_transferred: int
    message: str = ""


@dataclass
class GCReport:
    """Report from garbage collection run."""

    dry_run: bool
    expired_staged: list[str]
    expired_archived: list[str]
    bytes_freed: int
    run_at: datetime
