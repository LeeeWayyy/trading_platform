"""
Model Registry for versioned model storage and deployment.

This module provides:
- ModelRegistry: DuckDB-based catalog for model artifacts
- ModelMetadata: Complete provenance and metadata tracking
- Serialization: Artifact serialization with SHA-256 checksums
- RegistryManifestManager: Registry-level manifest for DR

Key features:
- Immutable versioning (versions cannot be overwritten)
- Dataset version linkage to P4T1 registry
- Per-artifact required field validation
- Promotion gates (IC, Sharpe, paper trading)
- Atomic operations with transaction isolation

Example Usage:

    from libs.models import (
        ModelRegistry,
        ModelMetadata,
        ModelType,
        generate_model_id,
        capture_environment,
        compute_config_hash,
    )
    from libs.data_quality.versioning import DatasetVersionManager
    from datetime import datetime, UTC

    # Initialize registry
    version_mgr = DatasetVersionManager(...)
    registry = ModelRegistry(
        registry_dir=Path("data/models"),
        version_manager=version_mgr,
    )

    # Create metadata
    env = capture_environment(created_by="training_pipeline")
    config = {"learning_rate": 0.01, "epochs": 100}

    metadata = ModelMetadata(
        model_id=generate_model_id(),
        model_type=ModelType.risk_model,
        version="v1.0.0",
        created_at=datetime.now(UTC),
        dataset_version_ids={"crsp": "v1.2.3", "compustat": "v1.0.1"},
        snapshot_id="snap_20240101",
        factor_list=["momentum", "value", "size"],
        parameters={
            "factor_list": ["momentum", "value", "size"],
            "halflife_days": 60,
            "shrinkage_intensity": 0.5,
        },
        checksum_sha256="",  # Will be computed during serialization
        metrics={"ic": 0.05, "sharpe": 1.2, "paper_trade_hours": 48},
        env=env,
        config=config,
        config_hash=compute_config_hash(config),
    )

    # Register model
    model_id = registry.register_model(risk_model, metadata)

    # Promote to production
    result = registry.promote_model("risk_model", "v1.0.0")

    # Load production model
    current = registry.get_current_production("risk_model")
"""

from libs.models.backup import RegistryBackupManager, RegistryGC
from libs.models.compatibility import (
    CompatibilityResult,
    MissingDatasetError,
    VersionCompatibilityChecker,
    VersionDriftError,
)
from libs.models.loader import (
    CachedModel,
    CircuitBreakerTripped,
    LoadFailure,
    ProductionModelLoader,
)
from libs.models.manifest import (
    ManifestIntegrityError,
    RegistryManifestManager,
)
from libs.models.registry import (
    IntegrityError,
    ModelNotFoundError,
    ModelRegistry,
    RegistryLockError,
    VersionExistsError,
    generate_model_id,
)
from libs.models.serialization import (
    ChecksumMismatchError,
    DeserializationError,
    PartialWriteError,
    capture_environment,
    compute_checksum,
    compute_config_hash,
    deserialize_model,
    load_metadata,
    serialize_model,
    verify_checksum,
)
from libs.models.types import (
    ARTIFACT_REQUIRED_FIELDS,
    ArtifactInfo,
    BackupManifest,
    EnvironmentMetadata,
    GCReport,
    InvalidDatasetVersionError,
    InvalidSnapshotError,
    MissingRequiredFieldError,
    ModelMetadata,
    ModelStatus,
    ModelType,
    PromotionGateError,
    PromotionGates,
    PromotionResult,
    RegistryManifest,
    RestoreResult,
    RollbackResult,
    SyncResult,
    ValidationResult,
    validate_artifact_metadata,
)

__all__ = [
    # Registry
    "ModelRegistry",
    "generate_model_id",
    "ModelNotFoundError",
    "VersionExistsError",
    "RegistryLockError",
    "IntegrityError",
    # Types
    "ModelType",
    "ModelStatus",
    "ModelMetadata",
    "EnvironmentMetadata",
    "PromotionGates",
    "PromotionResult",
    "RollbackResult",
    "ValidationResult",
    "ArtifactInfo",
    "CompatibilityResult",
    "RegistryManifest",
    "BackupManifest",
    "RestoreResult",
    "SyncResult",
    "GCReport",
    # Validation
    "ARTIFACT_REQUIRED_FIELDS",
    "validate_artifact_metadata",
    "MissingRequiredFieldError",
    "InvalidDatasetVersionError",
    "InvalidSnapshotError",
    "PromotionGateError",
    # Serialization
    "serialize_model",
    "deserialize_model",
    "load_metadata",
    "compute_checksum",
    "verify_checksum",
    "compute_config_hash",
    "capture_environment",
    "ChecksumMismatchError",
    "PartialWriteError",
    "DeserializationError",
    # Manifest
    "RegistryManifestManager",
    "ManifestIntegrityError",
    # Loader
    "ProductionModelLoader",
    "CachedModel",
    "LoadFailure",
    "CircuitBreakerTripped",
    # Compatibility
    "VersionCompatibilityChecker",
    "MissingDatasetError",
    "VersionDriftError",
    # Backup
    "RegistryBackupManager",
    "RegistryGC",
]
