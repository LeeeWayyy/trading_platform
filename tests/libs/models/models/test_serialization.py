"""Tests for model serialization utilities."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from libs.models.models.serialization import (
    ChecksumMismatchError,
    DeserializationError,
    capture_environment,
    compute_checksum,
    compute_config_hash,
    deserialize_model,
    load_metadata,
    serialize_model,
)
from libs.models.models.types import EnvironmentMetadata, ModelMetadata, ModelType


@pytest.fixture()
def env_metadata() -> EnvironmentMetadata:
    """Create environment metadata for tests."""
    return EnvironmentMetadata(
        python_version="3.11.0",
        dependencies_hash="deps-hash",
        platform="linux-x86_64",
        created_by="tester",
        numpy_version="1.26.0",
        polars_version="0.20.0",
        sklearn_version=None,
        cvxpy_version=None,
    )


def _make_metadata(model_type: ModelType, env: EnvironmentMetadata) -> ModelMetadata:
    config = {"alpha": 0.1, "beta": 0.2}
    return ModelMetadata(
        model_id="model-123",
        model_type=model_type,
        version="v1.2.3",
        created_at=datetime.now(UTC),
        dataset_version_ids={"crsp": "v1.0.0"},
        snapshot_id="snapshot-001",
        checksum_sha256="placeholder",
        metrics={"ic": 0.12},
        env=env,
        config=config,
        config_hash=compute_config_hash(config),
    )


class TestSerialization:
    """Tests for serialize/deserialize and metadata utilities."""

    def test_compute_config_hash_deterministic(self) -> None:
        """Config hash should be deterministic across key orderings."""
        config_a = {"b": 1, "a": 2}
        config_b = {"a": 2, "b": 1}

        assert compute_config_hash(config_a) == compute_config_hash(config_b)

    def test_serialize_and_deserialize_pickle(self, tmp_path: Path, env_metadata: EnvironmentMetadata) -> None:
        """Pickle serialization should round-trip for non-alpha weights."""
        metadata = _make_metadata(ModelType.risk_model, env_metadata)
        model = {"weights": [1.0, 2.0]}

        artifact_info = serialize_model(model, tmp_path, metadata)

        model_path = tmp_path / "model.pkl"
        checksum_path = tmp_path / "checksum.sha256"
        metadata_path = tmp_path / "metadata.json"

        assert model_path.exists()
        assert checksum_path.exists()
        assert metadata_path.exists()
        assert artifact_info.checksum == compute_checksum(model_path)

        loaded_model, loaded_metadata = deserialize_model(tmp_path)

        assert loaded_model == model
        assert loaded_metadata.model_id == metadata.model_id
        assert loaded_metadata.checksum_sha256 == artifact_info.checksum

    def test_serialize_and_deserialize_alpha_weights_json(
        self, tmp_path: Path, env_metadata: EnvironmentMetadata
    ) -> None:
        """Alpha weights use JSON serialization and round-trip."""
        metadata = _make_metadata(ModelType.alpha_weights, env_metadata)
        model = {"alpha_a": 0.1, "alpha_b": -0.2}

        serialize_model(model, tmp_path, metadata)

        model_path = tmp_path / "model.json"
        assert model_path.exists()

        loaded_model, loaded_metadata = deserialize_model(tmp_path)

        assert loaded_model == model
        assert loaded_metadata.model_type == ModelType.alpha_weights

    def test_deserialize_checksum_mismatch_raises(
        self, tmp_path: Path, env_metadata: EnvironmentMetadata
    ) -> None:
        """Checksum mismatch should raise ChecksumMismatchError."""
        metadata = _make_metadata(ModelType.risk_model, env_metadata)
        model = {"data": [1, 2, 3]}

        serialize_model(model, tmp_path, metadata)

        model_path = tmp_path / "model.pkl"
        model_path.write_bytes(b"corrupted")

        with pytest.raises(ChecksumMismatchError):
            deserialize_model(tmp_path, verify=True)

    def test_deserialize_missing_metadata_raises(self, tmp_path: Path) -> None:
        """Missing metadata.json should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            deserialize_model(tmp_path)

    def test_load_metadata_invalid_json_raises(self, tmp_path: Path) -> None:
        """Invalid metadata JSON should raise DeserializationError."""
        metadata_path = tmp_path / "metadata.json"
        metadata_path.write_text("not-json")

        with pytest.raises(DeserializationError):
            load_metadata(tmp_path)

    def test_capture_environment_uses_helpers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """capture_environment should use helper values and propagate created_by."""
        from libs.models.models import serialization

        monkeypatch.setattr(serialization, "_compute_dependencies_hash", lambda: "hash123")
        monkeypatch.setattr(serialization, "_get_package_version", lambda _pkg: "1.0.0")

        env = capture_environment(created_by="service")

        assert env.dependencies_hash == "hash123"
        assert env.created_by == "service"
        assert env.numpy_version == "1.0.0"
        assert env.polars_version == "1.0.0"
