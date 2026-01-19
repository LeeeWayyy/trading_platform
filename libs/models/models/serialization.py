"""
Model artifact serialization with integrity verification.

This module provides:
- Pickle/joblib serialization with configurable protocol
- JSON serialization for alpha_weights
- SHA-256 checksum generation and verification
- Atomic writes using temp file + atomic rename
- Metadata JSON sidecar file with ALL fields
- Environment metadata capture

Key design decisions:
- Atomic writes prevent partial/corrupt artifacts
- SHA-256 checksums detect corruption on load
- JSON sidecars are human-readable and provide full provenance
- Environment capture enables reproducibility verification
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
import platform
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from libs.models.models.types import (
    ArtifactInfo,
    EnvironmentMetadata,
    ModelMetadata,
    ModelType,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class ChecksumMismatchError(Exception):
    """Raised when artifact checksum doesn't match expected."""

    def __init__(self, path: Path, expected: str, actual: str) -> None:
        self.path = path
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Checksum mismatch for {path}: expected {expected[:16]}..., got {actual[:16]}..."
        )


class PartialWriteError(Exception):
    """Raised when artifact write is incomplete."""

    def __init__(self, path: Path, message: str) -> None:
        self.path = path
        super().__init__(f"Partial write to {path}: {message}")


class DeserializationError(Exception):
    """Raised when artifact cannot be deserialized."""

    def __init__(self, path: Path, message: str) -> None:
        self.path = path
        super().__init__(f"Failed to deserialize {path}: {message}")


# =============================================================================
# Environment Capture
# =============================================================================


def _get_package_version(package: str) -> str | None:
    """Get installed package version, or None if not installed."""
    try:
        from importlib.metadata import version

        return version(package)
    except (ImportError, ModuleNotFoundError) as e:
        logger.debug(
            "Package version not available - package not installed",
            extra={"package": package, "error": str(e)},
        )
        return None
    except Exception as e:
        logger.warning(
            "Failed to get package version - metadata read error",
            extra={"package": package, "error": str(e)},
            exc_info=True,
        )
        return None


def _compute_dependencies_hash() -> str:
    """Compute SHA-256 hash of sorted requirements.txt if exists."""
    req_path = Path("requirements.txt")
    if req_path.exists():
        # Read, sort, and hash
        lines = sorted(line.strip() for line in req_path.read_text().splitlines() if line.strip())
        content = "\n".join(lines)
        return hashlib.sha256(content.encode()).hexdigest()
    # If no requirements.txt, hash installed packages
    try:
        from importlib.metadata import distributions

        packages: list[str] = []
        for dist in distributions():
            try:
                name = dist.name
                version = dist.version
            except Exception as e:
                logger.warning(
                    "Skipping distribution with unreadable metadata",
                    extra={"error": str(e)},
                    exc_info=True,
                )
                continue
            if name and version:
                packages.append(f"{name}=={version}")
        content = "\n".join(sorted(packages))
        return hashlib.sha256(content.encode()).hexdigest()
    except (ImportError, ModuleNotFoundError) as e:
        logger.debug(
            "Dependencies hash unavailable - metadata not accessible",
            extra={"error": str(e)},
        )
        return "unknown"
    except Exception as e:
        logger.warning(
            "Failed to compute dependencies hash - metadata enumeration error",
            extra={"error": str(e)},
            exc_info=True,
        )
        return "unknown"


def capture_environment(created_by: str = "unknown") -> EnvironmentMetadata:
    """Capture current environment for reproducibility.

    Args:
        created_by: User or service creating the artifact.

    Returns:
        EnvironmentMetadata with current environment info.
    """
    return EnvironmentMetadata(
        python_version=sys.version.split()[0],
        dependencies_hash=_compute_dependencies_hash(),
        platform=f"{platform.system().lower()}-{platform.machine()}",
        created_by=created_by,
        numpy_version=_get_package_version("numpy") or "unknown",
        polars_version=_get_package_version("polars") or "unknown",
        sklearn_version=_get_package_version("scikit-learn"),
        cvxpy_version=_get_package_version("cvxpy"),
    )


# =============================================================================
# Checksum Utilities
# =============================================================================


def compute_checksum(path: Path) -> str:
    """Compute SHA-256 checksum of file.

    Args:
        path: Path to file.

    Returns:
        Hex-encoded SHA-256 checksum.
    """
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def verify_checksum(path: Path, expected: str) -> bool:
    """Verify file checksum matches expected.

    Args:
        path: Path to file.
        expected: Expected SHA-256 checksum.

    Returns:
        True if checksum matches, False otherwise.
    """
    actual = compute_checksum(path)
    return actual == expected


def compute_config_hash(config: dict[str, Any]) -> str:
    """Compute SHA-256 hash of config dict.

    Uses JSON serialization with sorted keys for determinism.

    Args:
        config: Configuration dictionary.

    Returns:
        Hex-encoded SHA-256 hash.
    """
    # Sort keys recursively for deterministic serialization
    content = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode()).hexdigest()


# =============================================================================
# Atomic Write Utilities
# =============================================================================


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomically write bytes to file using temp + rename.

    Args:
        path: Target path.
        data: Bytes to write.

    Raises:
        PartialWriteError: If write fails.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file in same directory (for atomic rename)
    fd, temp_path = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
    try:
        os.write(fd, data)
        os.fsync(fd)
        os.close(fd)
        # Atomic rename
        shutil.move(temp_path, path)
    except Exception as e:
        # Clean up temp file on failure
        try:
            os.close(fd)
        except OSError as close_err:
            logger.debug(
                "Failed to close temp file descriptor during cleanup",
                extra={"fd": fd, "error": str(close_err)},
            )
        try:
            os.unlink(temp_path)
        except OSError as unlink_err:
            logger.warning(
                "Failed to unlink temp file during cleanup",
                extra={"temp_path": temp_path, "error": str(unlink_err)},
            )
        raise PartialWriteError(path, str(e)) from e


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Atomically write JSON to file.

    Args:
        path: Target path.
        data: Dict to serialize as JSON.
    """
    content = json.dumps(data, indent=2, default=str, sort_keys=True)
    _atomic_write_bytes(path, content.encode("utf-8"))


# =============================================================================
# Serialization
# =============================================================================


def serialize_model(
    model: Any,
    artifact_dir: Path,
    metadata: ModelMetadata,
    *,
    protocol: int = pickle.HIGHEST_PROTOCOL,
) -> ArtifactInfo:
    """Serialize model artifact with metadata sidecar.

    Creates:
    - model.pkl (or model.json for alpha_weights)
    - metadata.json (AUTHORITATIVE source for full metadata)
    - checksum.sha256

    Args:
        model: Model object to serialize.
        artifact_dir: Directory to write artifact (created if needed).
        metadata: Model metadata.
        protocol: Pickle protocol version.

    Returns:
        ArtifactInfo with artifact details.

    Raises:
        PartialWriteError: If write fails.
    """
    artifact_dir.mkdir(parents=True, exist_ok=True)

    # Determine serialization format
    if metadata.model_type == ModelType.alpha_weights:
        # JSON for alpha weights (human-readable)
        model_path = artifact_dir / "model.json"
        content = json.dumps(model, indent=2, sort_keys=True)
        _atomic_write_bytes(model_path, content.encode("utf-8"))
    else:
        # Pickle for other types
        model_path = artifact_dir / "model.pkl"
        pickled = pickle.dumps(model, protocol=protocol)
        _atomic_write_bytes(model_path, pickled)

    # Compute checksum
    checksum = compute_checksum(model_path)
    size_bytes = model_path.stat().st_size

    # Write checksum file
    checksum_path = artifact_dir / "checksum.sha256"
    _atomic_write_bytes(checksum_path, f"{checksum}  model.*\n".encode())

    # Write metadata sidecar (AUTHORITATIVE source) with UPDATED checksum
    metadata_path = artifact_dir / "metadata.json"
    metadata_dict = metadata.model_dump(mode="json")
    # Update checksum_sha256 in metadata sidecar with computed value
    metadata_dict["checksum_sha256"] = checksum
    _atomic_write_json(metadata_path, metadata_dict)

    logger.info(
        "Serialized model artifact",
        extra={
            "model_id": metadata.model_id,
            "model_type": metadata.model_type.value,
            "version": metadata.version,
            "path": str(model_path),
            "checksum": checksum[:16],
            "size_bytes": size_bytes,
        },
    )

    return ArtifactInfo(
        path=str(model_path),
        checksum=checksum,
        size_bytes=size_bytes,
        serialized_at=datetime.now(UTC),
    )


def deserialize_model(
    artifact_dir: Path,
    *,
    verify: bool = True,
) -> tuple[Any, ModelMetadata]:
    """Deserialize model artifact with optional checksum verification.

    Args:
        artifact_dir: Directory containing artifact.
        verify: Whether to verify checksum (default True).

    Returns:
        Tuple of (model, metadata).

    Raises:
        DeserializationError: If deserialization fails.
        ChecksumMismatchError: If checksum verification fails.
        FileNotFoundError: If artifact files missing.
    """
    # Load metadata first
    metadata_path = artifact_dir / "metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(f"Metadata not found: {metadata_path}")

    try:
        with open(metadata_path) as f:
            metadata_dict = json.load(f)
        metadata = ModelMetadata.model_validate(metadata_dict)
    except Exception as e:
        raise DeserializationError(metadata_path, str(e)) from e

    # Determine model path based on type
    if metadata.model_type == ModelType.alpha_weights:
        model_path = artifact_dir / "model.json"
    else:
        model_path = artifact_dir / "model.pkl"

    if not model_path.exists():
        raise FileNotFoundError(f"Model artifact not found: {model_path}")

    # Verify checksum if requested
    if verify:
        actual_checksum = compute_checksum(model_path)
        if actual_checksum != metadata.checksum_sha256:
            raise ChecksumMismatchError(model_path, metadata.checksum_sha256, actual_checksum)

    # Load model
    try:
        if metadata.model_type == ModelType.alpha_weights:
            with open(model_path) as f:
                model = json.load(f)
        else:
            with open(model_path, "rb") as f:
                model = pickle.load(f)  # noqa: S301 (pickle is intentional for models)
    except Exception as e:
        raise DeserializationError(model_path, str(e)) from e

    logger.info(
        "Deserialized model artifact",
        extra={
            "model_id": metadata.model_id,
            "model_type": metadata.model_type.value,
            "version": metadata.version,
            "path": str(model_path),
            "verified": verify,
        },
    )

    return model, metadata


def load_metadata(artifact_dir: Path) -> ModelMetadata:
    """Load only metadata from artifact directory.

    Useful for listing/querying without loading model.

    Args:
        artifact_dir: Directory containing artifact.

    Returns:
        ModelMetadata.

    Raises:
        FileNotFoundError: If metadata.json not found.
        DeserializationError: If metadata parsing fails.
    """
    metadata_path = artifact_dir / "metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(f"Metadata not found: {metadata_path}")

    try:
        with open(metadata_path) as f:
            metadata_dict = json.load(f)
        return ModelMetadata.model_validate(metadata_dict)
    except Exception as e:
        raise DeserializationError(metadata_path, str(e)) from e
