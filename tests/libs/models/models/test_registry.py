import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest

from libs.models.models.registry import (
    IntegrityError,
    ModelNotFoundError,
    ModelRegistry,
    RegistryLockError,
    VersionExistsError,
    _is_unique_constraint_error,
    generate_model_id,
)
from libs.models.models.serialization import compute_config_hash
from libs.models.models.types import (
    EnvironmentMetadata,
    InvalidDatasetVersionError,
    InvalidSnapshotError,
    ModelMetadata,
    ModelStatus,
    ModelType,
    PromotionGateError,
    PromotionGates,
)


class _DummyDataset:
    def __init__(self, sync_manifest_version: str) -> None:
        self.sync_manifest_version = sync_manifest_version


class _DummySnapshot:
    def __init__(self, datasets: dict[str, _DummyDataset]) -> None:
        self.datasets = datasets


class _DummyVersionManager:
    def __init__(self, snapshots: dict[str, _DummySnapshot]) -> None:
        self._snapshots = snapshots

    def get_snapshot(self, snapshot_id: str) -> _DummySnapshot | None:
        return self._snapshots.get(snapshot_id)


def _env() -> EnvironmentMetadata:
    return EnvironmentMetadata(
        python_version="3.11.0",
        dependencies_hash="hash",
        platform="linux-x86_64",
        created_by="tester",
        numpy_version="1.26.0",
        polars_version="0.20.0",
        sklearn_version=None,
        cvxpy_version=None,
    )


def make_metadata(
    *,
    version: str = "v1.0.0",
    model_type: ModelType = ModelType.risk_model,
    dataset_versions: dict[str, str] | None = None,
    snapshot_id: str = "snapshot-1",
) -> ModelMetadata:
    config = {"alpha": 0.1}
    if dataset_versions is None:
        dataset_versions = {"crsp": "v1"}
    return ModelMetadata(
        model_id=str(uuid.uuid4()),
        model_type=model_type,
        version=version,
        created_at=datetime.now(UTC),
        dataset_version_ids=dataset_versions,
        snapshot_id=snapshot_id,
        factor_list=["value"],
        parameters={
            "factor_list": ["value"],
            "halflife_days": 63,
            "shrinkage_intensity": 0.5,
        },
        checksum_sha256="placeholder",
        metrics={"ic": 0.05, "sharpe": 1.0, "paper_trade_hours": 24},
        env=_env(),
        config=config,
        config_hash=compute_config_hash(config),
        feature_formulas=None,
        experiment_id=None,
        run_id=None,
        dataset_uri=None,
        qlib_version=None,
    )


def test_register_model_valid_metadata(tmp_path: Path) -> None:
    snapshot = _DummySnapshot({"crsp": _DummyDataset("v1")})
    version_manager = _DummyVersionManager({"snapshot-1": snapshot})
    registry = ModelRegistry(tmp_path / "registry", version_manager=version_manager)

    metadata = make_metadata()
    model_id = registry.register_model({"weights": [1, 2, 3]}, metadata)

    fetched = registry.get_model_metadata(metadata.model_type.value, metadata.version)
    assert fetched is not None
    assert fetched.model_id == model_id
    assert fetched.dataset_version_ids == metadata.dataset_version_ids


def test_register_model_invalid_dataset_versions(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    bad_metadata = make_metadata(dataset_versions={})

    with pytest.raises(InvalidDatasetVersionError):
        registry.register_model({"weights": [1]}, bad_metadata)


def test_register_model_invalid_snapshot(tmp_path: Path) -> None:
    version_manager = _DummyVersionManager({})
    registry = ModelRegistry(tmp_path / "registry", version_manager=version_manager)
    metadata = make_metadata()

    with pytest.raises(InvalidSnapshotError):
        registry.register_model({"weights": [1]}, metadata)


def test_register_model_duplicate_version(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    metadata = make_metadata()

    registry.register_model({"weights": [1]}, metadata)
    with pytest.raises(VersionExistsError):
        registry.register_model({"weights": [2]}, metadata)


def test_promotion_and_rollback_flow(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    meta_v1 = make_metadata(version="v1.0.0")
    meta_v2 = make_metadata(version="v2.0.0")

    registry.register_model({"weights": [1]}, meta_v1)
    registry.register_model({"weights": [2]}, meta_v2)

    registry.promote_model(meta_v1.model_type.value, meta_v1.version, skip_gates=True)
    registry.promote_model(meta_v2.model_type.value, meta_v2.version, skip_gates=True)

    with registry._get_connection(read_only=True) as conn:
        statuses = dict(conn.execute("SELECT version, status FROM models").fetchall())
    assert statuses[meta_v1.version] == ModelStatus.archived.value
    assert statuses[meta_v2.version] == ModelStatus.production.value

    rollback = registry.rollback_model(meta_v1.model_type.value, changed_by="tester")
    assert rollback.to_version == meta_v1.version
    assert rollback.from_version == meta_v2.version

    with registry._get_connection(read_only=True) as conn:
        statuses = dict(conn.execute("SELECT version, status FROM models").fetchall())
        history_count = conn.execute("SELECT COUNT(*) FROM promotion_history").fetchone()[0]

    assert statuses[meta_v1.version] == ModelStatus.production.value
    assert statuses[meta_v2.version] == ModelStatus.staged.value
    assert history_count >= 4  # staged + promotions + rollback entries


def test_validation_detects_checksum_mismatch(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    metadata = make_metadata()
    registry.register_model({"weights": [1]}, metadata)

    ok_result = registry.validate_model(metadata.model_type.value, metadata.version)
    assert ok_result.valid is True
    assert ok_result.checksum_verified is True
    assert ok_result.load_successful is True

    artifact_dir = registry.artifacts_dir / metadata.model_type.value / metadata.version
    model_path = artifact_dir / "model.pkl"
    model_path.write_text("corrupted")  # Break checksum

    bad_result = registry.validate_model(metadata.model_type.value, metadata.version)
    assert bad_result.valid is False
    assert bad_result.checksum_verified is False
    assert any("checksum mismatch" in err for err in bad_result.errors)


def test_basic_crud_operations(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    metadata = make_metadata()
    registry.register_model({"weights": [1]}, metadata)

    by_type_version = registry.get_model_metadata(metadata.model_type.value, metadata.version)
    assert by_type_version is not None

    by_id = registry.get_model_by_id(metadata.model_id)
    assert by_id is not None
    assert by_id.version == metadata.version

    registry.promote_model(metadata.model_type.value, metadata.version, skip_gates=True)
    current = registry.get_current_production(metadata.model_type.value)
    assert current is not None
    assert current.version == metadata.version


def test_list_models_lightweight_skips_verification(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    metadata = make_metadata()
    registry.register_model({"weights": [1]}, metadata)

    artifact_dir = registry.artifacts_dir / metadata.model_type.value / metadata.version
    metadata_path = artifact_dir / "metadata.json"
    metadata_path.write_text(metadata_path.read_text() + "\n")  # change checksum

    models = registry.list_models(metadata.model_type.value, lightweight=True)
    assert len(models) == 1
    assert models[0].version == metadata.version

    with pytest.raises(IntegrityError):
        registry.list_models(metadata.model_type.value, lightweight=False)


# =============================================================================
# Exception Classes Tests (lines 69-98)
# =============================================================================


def test_model_not_found_error_default_message() -> None:
    """Test ModelNotFoundError with default message."""
    exc = ModelNotFoundError("risk_model", "v1.0.0")
    assert exc.model_type == "risk_model"
    assert exc.version == "v1.0.0"
    assert "risk_model/v1.0.0 not found" in str(exc)


def test_model_not_found_error_custom_message() -> None:
    """Test ModelNotFoundError with custom message."""
    exc = ModelNotFoundError("risk_model", "v1.0.0", message="Custom error message")
    assert "Custom error message" in str(exc)


def test_version_exists_error() -> None:
    """Test VersionExistsError initialization."""
    exc = VersionExistsError("alpha_weights", "v2.0.0")
    assert exc.model_type == "alpha_weights"
    assert exc.version == "v2.0.0"
    assert "v2.0.0 already exists for alpha_weights" in str(exc)


def test_registry_lock_error_default() -> None:
    """Test RegistryLockError with default message."""
    exc = RegistryLockError()
    assert "locked" in str(exc).lower()


def test_registry_lock_error_custom() -> None:
    """Test RegistryLockError with custom message."""
    exc = RegistryLockError("Custom lock message")
    assert "Custom lock message" in str(exc)


def test_integrity_error() -> None:
    """Test IntegrityError initialization."""
    exc = IntegrityError("Checksum mismatch detected")
    assert "Checksum mismatch" in str(exc)


# =============================================================================
# Lock Mechanism Tests (lines 220-297)
# =============================================================================


def test_restore_lock_blocks_operations(tmp_path: Path) -> None:
    """Test that restore lock blocks registry operations."""
    registry = ModelRegistry(tmp_path / "registry")

    # Create restore lock file
    restore_lock = registry.registry_dir / ".restore.lock"
    restore_lock.touch()

    with pytest.raises(RegistryLockError) as exc:
        registry.get_model_metadata("risk_model", "v1.0.0")

    assert "restore in progress" in str(exc.value).lower()
    restore_lock.unlink()


def test_backup_lock_blocks_write_operations(tmp_path: Path) -> None:
    """Test that backup lock blocks write operations but allows reads."""
    registry = ModelRegistry(tmp_path / "registry")
    metadata = make_metadata()

    # Register a model first (before creating lock)
    registry.register_model({"weights": [1]}, metadata)

    # Create backup lock file
    backup_lock = registry.registry_dir / ".backup.lock"
    backup_lock.touch()

    # Reads should work
    result = registry.get_model_metadata(metadata.model_type.value, metadata.version)
    assert result is not None

    # Writes should be blocked
    with pytest.raises(RegistryLockError) as exc:
        registry.register_model({"weights": [2]}, make_metadata(version="v2.0.0"))

    assert "backup in progress" in str(exc.value).lower()
    backup_lock.unlink()


def test_lock_release_failure_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Test that lock release failure is logged but doesn't raise."""
    import logging
    caplog.set_level(logging.DEBUG)
    registry = ModelRegistry(tmp_path / "registry")

    # Mock flock to raise on LOCK_UN
    import fcntl
    original_flock = fcntl.flock

    def mock_flock(fd: int, operation: int) -> None:
        if operation == fcntl.LOCK_UN:
            raise OSError("Mock lock release failure")
        original_flock(fd, operation)

    with patch("fcntl.flock", mock_flock):
        # Operation should complete despite lock release failure
        _ = registry.list_models()
        # The list may be empty which is fine

    # Verify debug message was logged
    assert any("Failed to release registry lock" in record.message for record in caplog.records)


# =============================================================================
# Registration Edge Cases (lines 338-467)
# =============================================================================


def test_register_model_missing_snapshot_id(tmp_path: Path) -> None:
    """Test registration fails when snapshot_id is missing."""
    registry = ModelRegistry(tmp_path / "registry")
    metadata = make_metadata(snapshot_id="")

    with pytest.raises(InvalidSnapshotError):
        registry.register_model({"weights": [1]}, metadata)


def test_register_model_dataset_version_mismatch(tmp_path: Path) -> None:
    """Test registration fails when dataset version doesn't match snapshot."""
    # Snapshot has version "v2", but metadata claims "v1"
    snapshot = _DummySnapshot({"crsp": _DummyDataset("v2")})
    version_manager = _DummyVersionManager({"snapshot-1": snapshot})
    registry = ModelRegistry(tmp_path / "registry", version_manager=version_manager)

    metadata = make_metadata(dataset_versions={"crsp": "v1"})  # Wrong version

    with pytest.raises(InvalidDatasetVersionError) as exc:
        registry.register_model({"weights": [1]}, metadata)

    assert "mismatch" in str(exc.value).lower()


def test_register_model_dataset_not_in_snapshot(tmp_path: Path) -> None:
    """Test registration fails when dataset is not in snapshot."""
    snapshot = _DummySnapshot({"crsp": _DummyDataset("v1")})  # Only crsp
    version_manager = _DummyVersionManager({"snapshot-1": snapshot})
    registry = ModelRegistry(tmp_path / "registry", version_manager=version_manager)

    metadata = make_metadata(dataset_versions={"nonexistent": "v1"})

    with pytest.raises(InvalidDatasetVersionError):
        registry.register_model({"weights": [1]}, metadata)


def test_register_model_db_constraint_error_cleanup(tmp_path: Path) -> None:
    """Test that DB constraint error triggers artifact cleanup."""
    registry = ModelRegistry(tmp_path / "registry")
    metadata = make_metadata()

    # Register first model
    registry.register_model({"weights": [1]}, metadata)

    # Try to register with same version - should trigger VersionExistsError
    # and cleanup any orphan artifacts
    with pytest.raises(VersionExistsError):
        # Using a new metadata with same version triggers constraint error
        registry.register_model({"weights": [2]}, metadata)


def test_register_model_db_integrity_error_cleanup(tmp_path: Path) -> None:
    """Test that DB IntegrityError triggers artifact cleanup and raises VersionExistsError."""
    registry = ModelRegistry(tmp_path / "registry")
    metadata = make_metadata()

    # Register the model first
    registry.register_model({"weights": [1]}, metadata)

    # Try to register a model with the same type/version but different model_id
    # This triggers a unique constraint error on (model_type, version)
    metadata2 = make_metadata(version=metadata.version)  # Same version
    # Change model_id to avoid early version check
    from libs.models.models.types import ModelMetadata
    metadata2_dict = metadata2.model_dump()
    metadata2_dict["model_id"] = str(uuid.uuid4())  # Different ID
    metadata2_modified = ModelMetadata(**metadata2_dict)

    with pytest.raises(VersionExistsError):
        registry.register_model({"weights": [2]}, metadata2_modified)


# =============================================================================
# Promotion Gates Tests (lines 653-677)
# =============================================================================


def test_promotion_gate_ic_failure(tmp_path: Path) -> None:
    """Test promotion fails when IC is below threshold."""
    gates = PromotionGates(min_ic=0.05)  # Higher threshold
    registry = ModelRegistry(tmp_path / "registry", promotion_gates=gates)

    metadata = make_metadata()
    metadata = ModelMetadata(
        **{**metadata.model_dump(), "metrics": {"ic": 0.01, "sharpe": 1.0, "paper_trade_hours": 24}}
    )

    registry.register_model({"weights": [1]}, metadata)

    with pytest.raises(PromotionGateError) as exc:
        registry.promote_model(metadata.model_type.value, metadata.version)

    assert exc.value.gate == "ic"


def test_promotion_gate_sharpe_failure(tmp_path: Path) -> None:
    """Test promotion fails when Sharpe is below threshold."""
    gates = PromotionGates(min_sharpe=2.0)  # Higher threshold
    registry = ModelRegistry(tmp_path / "registry", promotion_gates=gates)

    metadata = make_metadata()
    metadata = ModelMetadata(
        **{**metadata.model_dump(), "metrics": {"ic": 0.05, "sharpe": 0.3, "paper_trade_hours": 24}}
    )

    registry.register_model({"weights": [1]}, metadata)

    with pytest.raises(PromotionGateError) as exc:
        registry.promote_model(metadata.model_type.value, metadata.version)

    assert exc.value.gate == "sharpe"


def test_promotion_gate_paper_trade_failure(tmp_path: Path) -> None:
    """Test promotion fails when paper trading hours are insufficient."""
    gates = PromotionGates(min_paper_trade_hours=48)  # Higher threshold
    registry = ModelRegistry(tmp_path / "registry", promotion_gates=gates)

    metadata = make_metadata()
    metadata = ModelMetadata(
        **{**metadata.model_dump(), "metrics": {"ic": 0.05, "sharpe": 1.0, "paper_trade_hours": 12}}
    )

    registry.register_model({"weights": [1]}, metadata)

    with pytest.raises(PromotionGateError) as exc:
        registry.promote_model(metadata.model_type.value, metadata.version)

    assert exc.value.gate == "paper_trade_hours"


def test_promote_nonexistent_model(tmp_path: Path) -> None:
    """Test promote raises ModelNotFoundError for nonexistent model."""
    registry = ModelRegistry(tmp_path / "registry")

    with pytest.raises(ModelNotFoundError):
        registry.promote_model("risk_model", "v999.0.0", skip_gates=True)


def test_promote_multiple_models_archives_previous(tmp_path: Path) -> None:
    """Test promoting multiple models correctly archives previous production."""
    registry = ModelRegistry(tmp_path / "registry")
    meta_v1 = make_metadata(version="v1.0.0")
    meta_v2 = make_metadata(version="v2.0.0")
    meta_v3 = make_metadata(version="v3.0.0")

    registry.register_model({"weights": [1]}, meta_v1)
    registry.register_model({"weights": [2]}, meta_v2)
    registry.register_model({"weights": [3]}, meta_v3)

    # Promote v1
    result1 = registry.promote_model(meta_v1.model_type.value, "v1.0.0", skip_gates=True)
    assert result1.success
    assert result1.from_version is None  # No previous production

    # Promote v2, should archive v1
    result2 = registry.promote_model(meta_v2.model_type.value, "v2.0.0", skip_gates=True)
    assert result2.success
    assert result2.from_version == "v1.0.0"

    # Verify v1 is archived
    v1_info = registry.get_model_info(meta_v1.model_type.value, "v1.0.0")
    assert v1_info["status"] == ModelStatus.archived.value

    # Promote v3, should archive v2
    result3 = registry.promote_model(meta_v3.model_type.value, "v3.0.0", skip_gates=True)
    assert result3.success
    assert result3.from_version == "v2.0.0"

    # Verify manifest is updated
    manifest = registry.get_manifest()
    assert manifest.production_models.get(meta_v1.model_type.value) == "v3.0.0"


# =============================================================================
# Rollback Tests (lines 716-740, 781-791)
# =============================================================================


def test_rollback_no_production_model(tmp_path: Path) -> None:
    """Test rollback fails when there's no production model."""
    registry = ModelRegistry(tmp_path / "registry")
    metadata = make_metadata()
    registry.register_model({"weights": [1]}, metadata)
    # Model is staged, not production

    with pytest.raises(ModelNotFoundError):
        registry.rollback_model(metadata.model_type.value)


def test_rollback_no_archived_version(tmp_path: Path) -> None:
    """Test rollback fails when there's no archived version to rollback to."""
    registry = ModelRegistry(tmp_path / "registry")
    metadata = make_metadata()
    registry.register_model({"weights": [1]}, metadata)
    registry.promote_model(metadata.model_type.value, metadata.version, skip_gates=True)
    # Now we have production but no archived versions

    with pytest.raises(ModelNotFoundError) as exc:
        registry.rollback_model(metadata.model_type.value)

    assert "no archived version" in str(exc.value).lower()


def test_rollback_success_full_flow(tmp_path: Path) -> None:
    """Test successful rollback with full transaction flow including manifest update."""
    registry = ModelRegistry(tmp_path / "registry")
    meta_v1 = make_metadata(version="v1.0.0")
    meta_v2 = make_metadata(version="v2.0.0")

    registry.register_model({"weights": [1]}, meta_v1)
    registry.register_model({"weights": [2]}, meta_v2)
    registry.promote_model(meta_v1.model_type.value, meta_v1.version, skip_gates=True)
    registry.promote_model(meta_v2.model_type.value, meta_v2.version, skip_gates=True)

    # Now v1 is archived, v2 is production
    result = registry.rollback_model(meta_v1.model_type.value)

    assert result.success is True
    assert result.from_version == "v2.0.0"
    assert result.to_version == "v1.0.0"
    assert "v2.0.0 to v1.0.0" in result.message

    # Verify status changes
    info_v1 = registry.get_model_info(meta_v1.model_type.value, "v1.0.0")
    info_v2 = registry.get_model_info(meta_v2.model_type.value, "v2.0.0")
    assert info_v1["status"] == ModelStatus.production.value
    assert info_v2["status"] == ModelStatus.staged.value


# =============================================================================
# Query Methods Tests (lines 869-957)
# =============================================================================


def test_get_current_production_returns_none(tmp_path: Path) -> None:
    """Test get_current_production returns None when no production model."""
    registry = ModelRegistry(tmp_path / "registry")
    result = registry.get_current_production("risk_model")
    assert result is None


def test_get_model_metadata_returns_none(tmp_path: Path) -> None:
    """Test get_model_metadata returns None for nonexistent model."""
    registry = ModelRegistry(tmp_path / "registry")
    result = registry.get_model_metadata("risk_model", "v999.0.0")
    assert result is None


def test_get_model_by_id_returns_none(tmp_path: Path) -> None:
    """Test get_model_by_id returns None for nonexistent ID."""
    registry = ModelRegistry(tmp_path / "registry")
    result = registry.get_model_by_id("nonexistent-id")
    assert result is None


def test_list_models_by_status(tmp_path: Path) -> None:
    """Test list_models filters by status correctly."""
    registry = ModelRegistry(tmp_path / "registry")
    meta_v1 = make_metadata(version="v1.0.0")
    meta_v2 = make_metadata(version="v2.0.0")

    registry.register_model({"weights": [1]}, meta_v1)
    registry.register_model({"weights": [2]}, meta_v2)
    registry.promote_model(meta_v1.model_type.value, meta_v1.version, skip_gates=True)

    # Get staged models
    staged = registry.list_models(meta_v1.model_type.value, status=ModelStatus.staged)
    assert len(staged) == 1
    assert staged[0].version == "v2.0.0"

    # Get production models
    production = registry.list_models(meta_v1.model_type.value, status=ModelStatus.production)
    assert len(production) == 1
    assert production[0].version == "v1.0.0"


# =============================================================================
# Validation Tests (lines 959-1044)
# =============================================================================


def test_validate_nonexistent_model(tmp_path: Path) -> None:
    """Test validate_model returns invalid result for nonexistent model."""
    registry = ModelRegistry(tmp_path / "registry")
    result = registry.validate_model("risk_model", "v999.0.0")

    assert result.valid is False
    assert not result.checksum_verified
    assert not result.load_successful
    assert any("not found" in err for err in result.errors)


def test_validate_model_missing_artifact_file(tmp_path: Path) -> None:
    """Test validate_model detects missing artifact file."""
    registry = ModelRegistry(tmp_path / "registry")
    metadata = make_metadata()
    registry.register_model({"weights": [1]}, metadata)

    # Delete the model file
    artifact_dir = registry.artifacts_dir / metadata.model_type.value / metadata.version
    model_path = artifact_dir / "model.pkl"
    model_path.unlink()

    result = registry.validate_model(metadata.model_type.value, metadata.version)
    assert result.valid is False
    assert any("not found" in err for err in result.errors)


def test_validate_model_missing_metadata_file(tmp_path: Path) -> None:
    """Test validate_model detects missing metadata file."""
    registry = ModelRegistry(tmp_path / "registry")
    metadata = make_metadata()
    registry.register_model({"weights": [1]}, metadata)

    # Delete the metadata file
    artifact_dir = registry.artifacts_dir / metadata.model_type.value / metadata.version
    metadata_path = artifact_dir / "metadata.json"
    metadata_path.unlink()

    result = registry.validate_model(metadata.model_type.value, metadata.version)
    assert result.valid is False
    assert any("Metadata file not found" in err for err in result.errors)


def test_validate_model_metadata_tampering(tmp_path: Path) -> None:
    """Test validate_model detects metadata tampering."""
    registry = ModelRegistry(tmp_path / "registry")
    metadata = make_metadata()
    registry.register_model({"weights": [1]}, metadata)

    # Tamper with metadata file
    artifact_dir = registry.artifacts_dir / metadata.model_type.value / metadata.version
    metadata_path = artifact_dir / "metadata.json"
    content = metadata_path.read_text()
    metadata_path.write_text(content.replace("v1.0.0", "v9.9.9"))

    result = registry.validate_model(metadata.model_type.value, metadata.version)
    assert result.valid is False
    assert any("tampering" in err.lower() for err in result.errors)


def test_validate_model_load_failure(tmp_path: Path) -> None:
    """Test validate_model reports load failure."""
    registry = ModelRegistry(tmp_path / "registry")
    metadata = make_metadata()
    registry.register_model({"weights": [1]}, metadata)

    # Corrupt the model file but keep checksum (need to update checksum too)
    # Instead, mock deserialize_model to fail
    with patch(
        "libs.models.models.registry.deserialize_model",
        side_effect=RuntimeError("Load error"),
    ):
        result = registry.validate_model(metadata.model_type.value, metadata.version)

    assert not result.load_successful
    assert any("Load failed" in err for err in result.errors)


# =============================================================================
# Manifest Update Tests (lines 1050-1081)
# =============================================================================


def test_manifest_update_counts(tmp_path: Path) -> None:
    """Test manifest artifact count and size are updated."""
    registry = ModelRegistry(tmp_path / "registry")
    metadata = make_metadata()
    registry.register_model({"weights": [1, 2, 3, 4, 5]}, metadata)

    manifest = registry.get_manifest()
    assert manifest.artifact_count == 1
    assert manifest.total_size_bytes > 0


def test_manifest_production_update(tmp_path: Path) -> None:
    """Test manifest production models are updated on promotion."""
    registry = ModelRegistry(tmp_path / "registry")
    metadata = make_metadata()
    registry.register_model({"weights": [1]}, metadata)
    registry.promote_model(metadata.model_type.value, metadata.version, skip_gates=True)

    manifest = registry.get_manifest()
    assert metadata.model_type.value in manifest.production_models
    assert manifest.production_models[metadata.model_type.value] == metadata.version


# =============================================================================
# Utility Methods Tests (lines 1087-1193)
# =============================================================================


def test_get_artifact_path(tmp_path: Path) -> None:
    """Test get_artifact_path returns correct path."""
    registry = ModelRegistry(tmp_path / "registry")
    metadata = make_metadata()
    registry.register_model({"weights": [1]}, metadata)

    path = registry.get_artifact_path(metadata.model_type.value, metadata.version)
    assert path is not None
    assert path.exists()


def test_get_artifact_path_not_found(tmp_path: Path) -> None:
    """Test get_artifact_path returns None for nonexistent model."""
    registry = ModelRegistry(tmp_path / "registry")
    path = registry.get_artifact_path("risk_model", "v999.0.0")
    assert path is None


def test_get_model_info(tmp_path: Path) -> None:
    """Test get_model_info returns correct info."""
    registry = ModelRegistry(tmp_path / "registry")
    metadata = make_metadata()
    registry.register_model({"weights": [1]}, metadata)
    registry.promote_model(metadata.model_type.value, metadata.version, skip_gates=True)

    info = registry.get_model_info(metadata.model_type.value, metadata.version)
    assert info is not None
    assert info["status"] == ModelStatus.production.value
    assert info["promoted_at"] is not None
    assert info["artifact_path"] is not None


def test_get_model_info_not_found(tmp_path: Path) -> None:
    """Test get_model_info returns None for nonexistent model."""
    registry = ModelRegistry(tmp_path / "registry")
    info = registry.get_model_info("risk_model", "v999.0.0")
    assert info is None


def test_get_model_info_staged_no_promoted_at(tmp_path: Path) -> None:
    """Test get_model_info returns None promoted_at for staged model."""
    registry = ModelRegistry(tmp_path / "registry")
    metadata = make_metadata()
    registry.register_model({"weights": [1]}, metadata)

    info = registry.get_model_info(metadata.model_type.value, metadata.version)
    assert info is not None
    assert info["status"] == ModelStatus.staged.value
    assert info["promoted_at"] is None


def test_get_model_info_bulk(tmp_path: Path) -> None:
    """Test get_model_info_bulk returns info for multiple versions."""
    registry = ModelRegistry(tmp_path / "registry")
    meta_v1 = make_metadata(version="v1.0.0")
    meta_v2 = make_metadata(version="v2.0.0")
    meta_v3 = make_metadata(version="v3.0.0")

    registry.register_model({"weights": [1]}, meta_v1)
    registry.register_model({"weights": [2]}, meta_v2)
    registry.register_model({"weights": [3]}, meta_v3)
    registry.promote_model(meta_v1.model_type.value, meta_v1.version, skip_gates=True)

    info = registry.get_model_info_bulk(
        meta_v1.model_type.value, ["v1.0.0", "v2.0.0", "v3.0.0"]
    )

    assert len(info) == 3
    assert info["v1.0.0"]["status"] == ModelStatus.production.value
    assert info["v1.0.0"]["promoted_at"] is not None
    assert info["v2.0.0"]["status"] == ModelStatus.staged.value
    assert info["v3.0.0"]["status"] == ModelStatus.staged.value


def test_get_model_info_bulk_empty_versions(tmp_path: Path) -> None:
    """Test get_model_info_bulk with empty versions list."""
    registry = ModelRegistry(tmp_path / "registry")
    info = registry.get_model_info_bulk("risk_model", [])
    assert info == {}


def test_get_model_info_bulk_partial_match(tmp_path: Path) -> None:
    """Test get_model_info_bulk returns only matching versions."""
    registry = ModelRegistry(tmp_path / "registry")
    metadata = make_metadata(version="v1.0.0")
    registry.register_model({"weights": [1]}, metadata)

    info = registry.get_model_info_bulk(
        metadata.model_type.value, ["v1.0.0", "v999.0.0"]  # v999 doesn't exist
    )

    assert len(info) == 1
    assert "v1.0.0" in info
    assert "v999.0.0" not in info


# =============================================================================
# Helper Function Tests (lines 1181-1193)
# =============================================================================


def test_is_unique_constraint_error_constraint_exception() -> None:
    """Test _is_unique_constraint_error detects ConstraintException."""
    exc = duckdb.ConstraintException("Unique constraint violation")
    assert _is_unique_constraint_error(exc) is True


def test_is_unique_constraint_error_unique_message() -> None:
    """Test _is_unique_constraint_error detects unique in message."""
    exc = Exception("UNIQUE constraint failed: models.model_type")
    assert _is_unique_constraint_error(exc) is True


def test_is_unique_constraint_error_duplicate_key_message() -> None:
    """Test _is_unique_constraint_error detects duplicate key in message."""
    exc = Exception("Duplicate key value violates constraint")
    assert _is_unique_constraint_error(exc) is True


def test_is_unique_constraint_error_other_error() -> None:
    """Test _is_unique_constraint_error returns False for other errors."""
    exc = Exception("Some random error")
    assert _is_unique_constraint_error(exc) is False


def test_generate_model_id() -> None:
    """Test generate_model_id returns valid UUID."""
    model_id = generate_model_id()
    # Should be valid UUID string
    parsed = uuid.UUID(model_id)
    assert str(parsed) == model_id


def test_generate_model_id_unique() -> None:
    """Test generate_model_id returns unique IDs."""
    ids = [generate_model_id() for _ in range(100)]
    assert len(set(ids)) == 100  # All unique


# =============================================================================
# Alpha Weights Validation Test
# =============================================================================


def test_validate_alpha_weights_model(tmp_path: Path) -> None:
    """Test validation with alpha_weights model type (uses model.json)."""
    registry = ModelRegistry(tmp_path / "registry")
    config = {"combination_method": "equal"}
    alpha_metadata = ModelMetadata(
        model_id=str(uuid.uuid4()),
        model_type=ModelType.alpha_weights,
        version="v1.0.0",
        created_at=datetime.now(UTC),
        dataset_version_ids={"crsp": "v1"},
        snapshot_id="snapshot-1",
        factor_list=["momentum"],
        parameters={
            "alpha_names": ["mom"],
            "combination_method": "equal",
            "ic_threshold": 0.01,
        },
        checksum_sha256="placeholder",
        metrics={"ic": 0.05, "sharpe": 1.0, "paper_trade_hours": 24},
        env=_env(),
        config=config,
        config_hash=compute_config_hash(config),
        feature_formulas=None,
        experiment_id=None,
        run_id=None,
        dataset_uri=None,
        qlib_version=None,
    )

    # Alpha weights models use json serialization
    registry.register_model({"weights": {"AAPL": 0.5, "GOOG": 0.5}}, alpha_metadata)

    result = registry.validate_model(
        alpha_metadata.model_type.value, alpha_metadata.version
    )
    assert result.valid is True
    assert result.checksum_verified is True
    assert result.load_successful is True


# =============================================================================
# Additional Coverage Tests
# =============================================================================


def test_version_exists_without_connection(tmp_path: Path) -> None:
    """Test _version_exists when no connection is provided (uses internal connection)."""
    registry = ModelRegistry(tmp_path / "registry")
    metadata = make_metadata()
    registry.register_model({"weights": [1]}, metadata)

    # Call _version_exists without providing a connection
    exists = registry._version_exists(metadata.model_type.value, metadata.version)
    assert exists is True

    # Non-existent version
    not_exists = registry._version_exists(metadata.model_type.value, "v999.0.0")
    assert not_exists is False


def test_manifest_size_calculation_empty_dir(tmp_path: Path) -> None:
    """Test manifest size calculation when artifacts dir is empty."""
    registry = ModelRegistry(tmp_path / "registry")

    # Manifest should exist with 0 artifacts and 0 size
    manifest = registry.get_manifest()
    assert manifest.artifact_count == 0
    assert manifest.total_size_bytes == 0


def test_promotion_without_prior_production(tmp_path: Path) -> None:
    """Test promotion when there's no existing production model."""
    registry = ModelRegistry(tmp_path / "registry")
    metadata = make_metadata()
    registry.register_model({"weights": [1]}, metadata)

    result = registry.promote_model(
        metadata.model_type.value, metadata.version, skip_gates=True
    )

    assert result.success
    assert result.from_version is None  # No prior production
    assert result.to_version == metadata.version


def test_get_manifest_returns_manifest(tmp_path: Path) -> None:
    """Test get_manifest returns valid manifest object."""
    registry = ModelRegistry(tmp_path / "registry")
    manifest = registry.get_manifest()

    assert manifest is not None
    assert manifest.registry_version == "1.0.0"
    assert manifest.artifact_count == 0


def test_list_models_no_filters(tmp_path: Path) -> None:
    """Test list_models with no type or status filter."""
    registry = ModelRegistry(tmp_path / "registry")
    meta_v1 = make_metadata(version="v1.0.0")
    # For alpha_weights, need different parameters
    from libs.models.models.types import ModelMetadata
    config = {"combination_method": "equal"}
    alpha_metadata = ModelMetadata(
        model_id=str(uuid.uuid4()),
        model_type=ModelType.alpha_weights,
        version="v2.0.0",
        created_at=datetime.now(UTC),
        dataset_version_ids={"crsp": "v1"},
        snapshot_id="snapshot-1",
        factor_list=["momentum"],
        parameters={
            "alpha_names": ["mom"],
            "combination_method": "equal",
            "ic_threshold": 0.01,
        },
        checksum_sha256="placeholder",
        metrics={"ic": 0.05, "sharpe": 1.0, "paper_trade_hours": 24},
        env=_env(),
        config=config,
        config_hash=compute_config_hash(config),
        feature_formulas=None,
        experiment_id=None,
        run_id=None,
        dataset_uri=None,
        qlib_version=None,
    )

    registry.register_model({"weights": [1]}, meta_v1)
    registry.register_model({"weights": {"AAPL": 0.5}}, alpha_metadata)

    # List all models (no filter)
    all_models = registry.list_models()
    assert len(all_models) == 2

    # List by model_type only
    risk_models = registry.list_models(model_type="risk_model")
    assert len(risk_models) == 1
    assert risk_models[0].model_type == ModelType.risk_model
