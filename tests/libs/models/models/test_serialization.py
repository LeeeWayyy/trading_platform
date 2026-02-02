"""Tests for model serialization utilities."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from libs.models.models.serialization import (
    ChecksumMismatchError,
    DeserializationError,
    PartialWriteError,
    _atomic_write_bytes,
    _compute_dependencies_hash,
    _get_package_version,
    capture_environment,
    compute_checksum,
    compute_config_hash,
    deserialize_model,
    load_metadata,
    serialize_model,
    verify_checksum,
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

    def test_serialize_and_deserialize_pickle(
        self, tmp_path: Path, env_metadata: EnvironmentMetadata
    ) -> None:
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

    def test_verify_checksum_returns_true_on_match(self, tmp_path: Path) -> None:
        """verify_checksum should return True when checksums match."""
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"test content")
        expected_checksum = compute_checksum(test_file)

        assert verify_checksum(test_file, expected_checksum) is True

    def test_verify_checksum_returns_false_on_mismatch(self, tmp_path: Path) -> None:
        """verify_checksum should return False when checksums don't match."""
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"test content")

        assert verify_checksum(test_file, "wrong_checksum") is False

    def test_load_metadata_success(self, tmp_path: Path, env_metadata: EnvironmentMetadata) -> None:
        """load_metadata should return ModelMetadata on success."""
        metadata = _make_metadata(ModelType.risk_model, env_metadata)
        model = {"weights": [1.0]}

        artifact_info = serialize_model(model, tmp_path, metadata)

        loaded = load_metadata(tmp_path)

        assert loaded.model_id == metadata.model_id
        assert loaded.checksum_sha256 == artifact_info.checksum

    def test_load_metadata_missing_file_raises(self, tmp_path: Path) -> None:
        """load_metadata should raise FileNotFoundError when metadata.json missing."""
        with pytest.raises(FileNotFoundError, match="Metadata not found"):
            load_metadata(tmp_path)

    def test_deserialize_model_missing_model_file_raises(
        self, tmp_path: Path, env_metadata: EnvironmentMetadata
    ) -> None:
        """deserialize_model should raise FileNotFoundError when model file missing."""
        metadata = _make_metadata(ModelType.risk_model, env_metadata)
        model = {"data": 123}

        serialize_model(model, tmp_path, metadata)
        # Remove model file but keep metadata
        (tmp_path / "model.pkl").unlink()

        with pytest.raises(FileNotFoundError, match="Model artifact not found"):
            deserialize_model(tmp_path)

    def test_deserialize_model_invalid_metadata_raises(self, tmp_path: Path) -> None:
        """deserialize_model should raise DeserializationError for invalid metadata."""
        metadata_path = tmp_path / "metadata.json"
        metadata_path.write_text('{"invalid": "schema"}')

        with pytest.raises(DeserializationError):
            deserialize_model(tmp_path)

    def test_deserialize_model_corrupt_pickle_raises(
        self, tmp_path: Path, env_metadata: EnvironmentMetadata
    ) -> None:
        """deserialize_model should raise DeserializationError for corrupt pickle."""
        metadata = _make_metadata(ModelType.risk_model, env_metadata)
        model = {"data": 123}

        _ = serialize_model(model, tmp_path, metadata)

        # Corrupt the model file but keep valid checksum in metadata
        model_path = tmp_path / "model.pkl"
        model_path.write_bytes(b"not-valid-pickle")

        # Update metadata checksum to match corrupted file
        metadata_path = tmp_path / "metadata.json"
        with open(metadata_path) as f:
            metadata_dict = json.load(f)
        metadata_dict["checksum_sha256"] = compute_checksum(model_path)
        with open(metadata_path, "w") as f:
            json.dump(metadata_dict, f)

        with pytest.raises(DeserializationError):
            deserialize_model(tmp_path, verify=True)

    def test_deserialize_alpha_weights_corrupt_json_raises(
        self, tmp_path: Path, env_metadata: EnvironmentMetadata
    ) -> None:
        """deserialize_model should raise DeserializationError for corrupt JSON."""
        metadata = _make_metadata(ModelType.alpha_weights, env_metadata)
        model = {"alpha_a": 0.1}

        serialize_model(model, tmp_path, metadata)

        # Corrupt the model file
        model_path = tmp_path / "model.json"
        model_path.write_text("not-valid-json{")

        # Update metadata checksum to match corrupted file
        metadata_path = tmp_path / "metadata.json"
        with open(metadata_path) as f:
            metadata_dict = json.load(f)
        metadata_dict["checksum_sha256"] = compute_checksum(model_path)
        with open(metadata_path, "w") as f:
            json.dump(metadata_dict, f)

        with pytest.raises(DeserializationError):
            deserialize_model(tmp_path, verify=True)

    def test_deserialize_model_without_verify(
        self, tmp_path: Path, env_metadata: EnvironmentMetadata
    ) -> None:
        """deserialize_model with verify=False should skip checksum verification."""
        metadata = _make_metadata(ModelType.risk_model, env_metadata)
        model = {"data": 123}

        serialize_model(model, tmp_path, metadata)

        # Corrupt checksum in metadata (but keep model valid)
        metadata_path = tmp_path / "metadata.json"
        with open(metadata_path) as f:
            metadata_dict = json.load(f)
        metadata_dict["checksum_sha256"] = "wrong_checksum_000"
        with open(metadata_path, "w") as f:
            json.dump(metadata_dict, f)

        # Should succeed with verify=False despite wrong checksum
        loaded_model, loaded_metadata = deserialize_model(tmp_path, verify=False)

        assert loaded_model == model
        assert loaded_metadata.checksum_sha256 == "wrong_checksum_000"


class TestGetPackageVersion:
    """Tests for _get_package_version helper function."""

    def test_returns_version_for_installed_package(self) -> None:
        """Should return version string for installed package."""
        # pytest is installed, so this should return a version
        version = _get_package_version("pytest")
        assert version is not None
        assert isinstance(version, str)

    def test_returns_none_for_nonexistent_package(self) -> None:
        """Should return None for package that doesn't exist."""
        version = _get_package_version("nonexistent_package_xyz_123")
        assert version is None

    def test_returns_none_on_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return None when ImportError occurs."""

        def raise_import_error(package: str) -> None:
            raise ImportError("test error")

        with patch("importlib.metadata.version", side_effect=raise_import_error):
            version = _get_package_version("some-package")
            assert version is None

    def test_returns_none_on_module_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return None when ModuleNotFoundError occurs."""

        def raise_module_not_found(package: str) -> None:
            raise ModuleNotFoundError("test error")

        with patch("importlib.metadata.version", side_effect=raise_module_not_found):
            version = _get_package_version("some-package")
            assert version is None

    def test_returns_none_on_generic_exception(self) -> None:
        """Should return None when generic Exception occurs."""

        def raise_generic_exception(package: str) -> None:
            raise RuntimeError("generic error")

        with patch("importlib.metadata.version", side_effect=raise_generic_exception):
            version = _get_package_version("some-package")
            assert version is None


class TestComputeDependenciesHash:
    """Tests for _compute_dependencies_hash helper function."""

    def test_computes_hash_from_requirements_txt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should compute hash from requirements.txt when it exists."""
        monkeypatch.chdir(tmp_path)
        req_file = tmp_path / "requirements.txt"
        req_file.write_text("numpy==1.26.0\npandas==2.0.0\n")

        result = _compute_dependencies_hash()

        assert result != "unknown"
        assert len(result) == 64  # SHA-256 hex digest

    def test_returns_hash_from_distributions_when_no_requirements(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should compute hash from installed distributions when no requirements.txt."""
        monkeypatch.chdir(tmp_path)
        # No requirements.txt exists in tmp_path

        result = _compute_dependencies_hash()

        assert result != "unknown"
        assert len(result) == 64

    def test_returns_unknown_on_import_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return 'unknown' when distributions import fails."""
        monkeypatch.chdir(tmp_path)

        def raise_import_error() -> None:
            raise ImportError("distributions unavailable")

        with patch("importlib.metadata.distributions", side_effect=raise_import_error):
            result = _compute_dependencies_hash()
            assert result == "unknown"

    def test_returns_unknown_on_generic_exception(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return 'unknown' when generic exception in distributions."""
        monkeypatch.chdir(tmp_path)

        def raise_generic_exception() -> None:
            raise RuntimeError("generic failure")

        with patch("importlib.metadata.distributions", side_effect=raise_generic_exception):
            result = _compute_dependencies_hash()
            assert result == "unknown"


class TestAtomicWriteBytes:
    """Tests for _atomic_write_bytes failure handling."""

    def test_successful_write(self, tmp_path: Path) -> None:
        """Should write file successfully."""
        test_file = tmp_path / "test.txt"
        _atomic_write_bytes(test_file, b"test content")

        assert test_file.exists()
        assert test_file.read_bytes() == b"test content"

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        """Should create parent directories if they don't exist."""
        test_file = tmp_path / "nested" / "dir" / "test.txt"
        _atomic_write_bytes(test_file, b"test content")

        assert test_file.exists()
        assert test_file.read_bytes() == b"test content"

    def test_raises_partial_write_error_on_write_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should raise PartialWriteError when write fails."""
        test_file = tmp_path / "test.txt"

        def raise_os_error(*args: object, **kwargs: object) -> None:
            raise OSError("disk full")

        with patch("os.write", side_effect=raise_os_error):
            with pytest.raises(PartialWriteError, match="disk full"):
                _atomic_write_bytes(test_file, b"test content")

    def test_cleanup_on_failure_with_close_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should handle OSError during cleanup close."""
        test_file = tmp_path / "test.txt"

        original_close = os.close
        call_count = {"write": 0, "close": 0}

        def failing_write(fd: int, data: bytes) -> int:
            call_count["write"] += 1
            raise OSError("write failed")

        def failing_close(fd: int) -> None:
            call_count["close"] += 1
            if call_count["close"] == 1:
                # First close call is in the except block
                raise OSError("close failed")
            return original_close(fd)

        with patch("os.write", side_effect=failing_write):
            with patch("os.close", side_effect=failing_close):
                with pytest.raises(PartialWriteError):
                    _atomic_write_bytes(test_file, b"test content")

    def test_cleanup_on_failure_with_unlink_error(self, tmp_path: Path) -> None:
        """Should handle OSError during cleanup unlink."""
        test_file = tmp_path / "test.txt"

        def failing_write(*args: object) -> None:
            raise OSError("write failed")

        def failing_unlink(*args: object) -> None:
            raise OSError("unlink failed")

        with patch("os.write", side_effect=failing_write):
            with patch("os.unlink", side_effect=failing_unlink):
                with pytest.raises(PartialWriteError):
                    _atomic_write_bytes(test_file, b"test content")


class TestPartialWriteError:
    """Tests for PartialWriteError exception."""

    def test_error_message_format(self) -> None:
        """Should format error message with path and message."""
        path = Path("/some/path/file.txt")
        error = PartialWriteError(path, "disk full")

        assert str(error) == "Partial write to /some/path/file.txt: disk full"
        assert error.path == path

    def test_error_is_catchable(self) -> None:
        """Should be catchable as Exception."""
        path = Path("/test")
        error = PartialWriteError(path, "test")

        assert isinstance(error, Exception)
