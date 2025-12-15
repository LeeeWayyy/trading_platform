import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from libs.models.registry import IntegrityError, ModelRegistry, VersionExistsError
from libs.models.serialization import compute_config_hash
from libs.models.types import (
    EnvironmentMetadata,
    InvalidDatasetVersionError,
    InvalidSnapshotError,
    ModelMetadata,
    ModelStatus,
    ModelType,
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
