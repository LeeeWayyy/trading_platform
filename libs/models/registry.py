"""
DuckDB-based Model Registry for versioned model storage.

This module provides:
- ModelRegistry: Core registry with DuckDB catalog
- Immutable versioning (versions cannot be overwritten)
- DatasetVersionManager validation on registration
- Per-artifact required field validation
- Promotion gates (IC, Sharpe, paper trading)
- Atomic operations with transaction isolation

Key design decisions:
- DuckDB for efficient querying and atomic transactions
- metadata.json sidecar is AUTHORITATIVE source
- DB stores queryable subset for efficient filtering
- Single-writer, multi-reader pattern for concurrency
- Promotion requires metric thresholds + paper trade period
"""

from __future__ import annotations

import fcntl
import json
import logging
import shutil
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

from libs.models.manifest import RegistryManifestManager
from libs.models.serialization import (
    compute_checksum,
    deserialize_model,
    load_metadata,
    serialize_model,
)
from libs.models.types import (
    InvalidDatasetVersionError,
    InvalidSnapshotError,
    ModelMetadata,
    ModelStatus,
    ModelType,
    PromotionGateError,
    PromotionGates,
    PromotionResult,
    RegistryManifest,
    RollbackResult,
    ValidationResult,
    validate_artifact_metadata,
)

if TYPE_CHECKING:
    from libs.data_quality.versioning import DatasetVersionManager

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class ModelNotFoundError(Exception):
    """Raised when model is not found in registry."""

    def __init__(self, model_type: str, version: str, message: str | None = None) -> None:
        self.model_type = model_type
        self.version = version
        msg = message if message else f"Model {model_type}/{version} not found"
        super().__init__(msg)


class VersionExistsError(Exception):
    """Raised when attempting to register existing version."""

    def __init__(self, model_type: str, version: str) -> None:
        self.model_type = model_type
        self.version = version
        super().__init__(f"Version {version} already exists for {model_type}")


class RegistryLockError(Exception):
    """Raised when registry is locked."""

    def __init__(self, message: str = "Registry is locked") -> None:
        super().__init__(message)


class IntegrityError(Exception):
    """Raised when artifact or metadata integrity check fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


# =============================================================================
# Schema
# =============================================================================


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS models (
    model_id VARCHAR NOT NULL,
    model_type VARCHAR NOT NULL,
    version VARCHAR NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'staged',
    artifact_path VARCHAR NOT NULL,
    checksum_sha256 VARCHAR NOT NULL,
    metadata_sha256 VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    promoted_at TIMESTAMP,
    archived_at TIMESTAMP,
    -- Key provenance fields for querying (full data in metadata.json sidecar)
    config_hash VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    dataset_version_ids_json VARCHAR NOT NULL,
    metrics_json VARCHAR,
    factor_list_json VARCHAR,
    -- Qlib fields (nullable)
    experiment_id VARCHAR,
    run_id VARCHAR,
    dataset_uri VARCHAR,
    qlib_version VARCHAR,
    CONSTRAINT models_model_type_version_unique UNIQUE (model_type, version)
);

CREATE TABLE IF NOT EXISTS promotion_history (
    id UUID PRIMARY KEY DEFAULT uuid(),
    model_id VARCHAR NOT NULL,
    from_status VARCHAR NOT NULL,
    to_status VARCHAR NOT NULL,
    changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    changed_by VARCHAR NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_models_model_type ON models(model_type);
CREATE INDEX IF NOT EXISTS idx_models_snapshot ON models(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_promotion_history_model ON promotion_history(model_id);
"""


# =============================================================================
# Model Registry
# =============================================================================


class ModelRegistry:
    """DuckDB-based model registry with versioned artifact storage.

    Features:
    - Immutable versioning (no overwrites)
    - DatasetVersionManager validation on registration
    - Per-artifact required field validation
    - Promotion gates (IC, Sharpe, paper trading)
    - Atomic transactions with single-writer pattern

    Directory structure:
        registry_dir/
        ├── registry.db           # DuckDB catalog
        ├── manifest.json         # Registry manifest
        └── artifacts/
            ├── risk_model/
            │   ├── v1.0.0/
            │   │   ├── model.pkl
            │   │   ├── metadata.json
            │   │   └── checksum.sha256
            │   └── v1.1.0/
            └── alpha_weights/
    """

    def __init__(
        self,
        registry_dir: Path,
        version_manager: DatasetVersionManager | None = None,
        promotion_gates: PromotionGates | None = None,
    ) -> None:
        """Initialize registry.

        Args:
            registry_dir: Path to registry directory.
            version_manager: DatasetVersionManager for validation.
            promotion_gates: Promotion thresholds (defaults used if None).
        """
        self.registry_dir = Path(registry_dir)
        self.db_path = self.registry_dir / "registry.db"
        self.artifacts_dir = self.registry_dir / "artifacts"
        self.version_manager = version_manager
        self.gates = promotion_gates or PromotionGates()
        self.manifest_manager = RegistryManifestManager(self.registry_dir)
        # Path to restore lock file (used by RegistryBackupManager)
        self._restore_lock_path = self.registry_dir / ".restore.lock"
        self._lock_file_path = self.registry_dir / ".registry.lock"
        self._lock_tls: threading.local = threading.local()

        # Ensure directories exist
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        # Initialize database
        self._init_db()

        # Initialize manifest if needed
        if not self.manifest_manager.exists():
            self.manifest_manager.create_manifest()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._get_connection() as conn:
            conn.execute(SCHEMA_SQL)
            # Drop legacy index that conflicts with DuckDB unique constraint updates
            # (see DuckDB 0.9.2 unique + secondary index bug). Safe to run repeatedly.
            conn.execute("DROP INDEX IF EXISTS idx_models_type_status")

    def _check_restore_lock(self) -> None:
        """Check if restore is in progress and fail fast if so.

        Raises:
            RegistryLockError: If restore lock is held.
        """
        if self._restore_lock_path.exists():
            raise RegistryLockError(
                "Registry restore in progress. Please wait for restore to complete."
            )

    def _check_backup_lock(self) -> None:
        """Check if backup is in progress and fail fast if so.

        Used only for write operations - reads can continue during backup.

        Raises:
            RegistryLockError: If backup lock is held.
        """
        backup_lock_path = self.registry_dir / ".backup.lock"
        if backup_lock_path.exists():
            raise RegistryLockError(
                "Registry backup in progress. Writes are blocked until backup completes."
            )

    def _get_lock_file(self) -> Any:
        """Get (and cache) the lock file handle per thread.

        Using a persistent handle avoids repeatedly opening/closing the lock file
        while still providing correct flock semantics (each thread keeps its own
        open file description).
        """

        lock_file = getattr(self._lock_tls, "handle", None)
        if lock_file is None:
            lock_file = open(self._lock_file_path, "a+")
            self._lock_tls.handle = lock_file
        return lock_file

    @contextmanager
    def _get_connection(
        self, *, read_only: bool = False
    ) -> Iterator[duckdb.DuckDBPyConnection]:
        """Get database connection with automatic close.

        Lock checking:
        - Restore lock: Always checked (blocks both reads and writes during restore)
        - Backup lock: Only checked for writes (reads can continue during backup)

        Args:
            read_only: If True, open in read-only mode (allows concurrent readers).
                       Default False for backward compatibility with write operations.

        Yields:
            DuckDB connection.

        Raises:
            RegistryLockError: If restore is in progress, or backup is in
                progress and this is a write operation.
        """
        self._check_restore_lock()
        # Only check backup lock for write operations - reads can continue during backup
        if not read_only:
            self._check_backup_lock()
        # Enforce single-writer, multi-reader pattern with file locks.
        # DuckDB allows multiple writers but we explicitly serialize writes to
        # avoid races between concurrent registrations/promotions.
        lock_file = self._get_lock_file()
        lock_mode = fcntl.LOCK_SH if read_only else fcntl.LOCK_EX
        fcntl.flock(lock_file.fileno(), lock_mode)

        conn = duckdb.connect(str(self.db_path), read_only=read_only)
        try:
            yield conn
        finally:
            conn.close()
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass

    # =========================================================================
    # Registration
    # =========================================================================

    def register_model(
        self,
        model: Any,
        metadata: ModelMetadata,
        *,
        changed_by: str = "unknown",
    ) -> str:
        """Register a new model version.

        Validates:
        1. Per-artifact required fields in parameters
        2. Dataset versions exist in P4T1 registry
        3. Snapshot exists
        4. Version doesn't already exist

        Args:
            model: Model object to serialize.
            metadata: Model metadata (must have valid checksum after serialization).
            changed_by: User/service registering the model.

        Returns:
            model_id of registered model.

        Raises:
            MissingRequiredFieldError: If artifact required fields missing.
            InvalidDatasetVersionError: If dataset version not found.
            InvalidSnapshotError: If snapshot not found.
            VersionExistsError: If version already exists.
        """
        # 1. Validate per-artifact required fields
        validate_artifact_metadata(metadata.model_type, metadata)

        # 2. Validate dataset versions are present (required even if version_manager absent)
        # This ensures models always have lineage info, even if we can't verify it
        if not metadata.dataset_version_ids:
            raise InvalidDatasetVersionError(
                "dataset_version_ids", "missing", "dataset_version_ids cannot be empty"
            )
        if not metadata.snapshot_id:
            raise InvalidSnapshotError(metadata.snapshot_id or "<missing>")

        # 3. Validate against version_manager if available
        if self.version_manager is None:
            logger.warning(
                "Registering model without version_manager - dataset/snapshot validation SKIPPED. "
                "This should only be used in testing. Production deployments should always "
                "configure version_manager for data lineage validation.",
                extra={
                    "model_id": metadata.model_id,
                    "model_type": metadata.model_type.value,
                    "snapshot_id": metadata.snapshot_id,
                },
            )
        else:
            # Get snapshot first
            snapshot = self.version_manager.get_snapshot(metadata.snapshot_id)
            if snapshot is None:
                raise InvalidSnapshotError(metadata.snapshot_id)

            for dataset, version_id in metadata.dataset_version_ids.items():
                # Check if dataset exists in snapshot
                if dataset not in snapshot.datasets:
                    raise InvalidDatasetVersionError(dataset, version_id)
                # Verify version_id matches snapshot's recorded sync_manifest_version
                snapshot_version = str(snapshot.datasets[dataset].sync_manifest_version)
                if snapshot_version != version_id:
                    raise InvalidDatasetVersionError(
                        dataset,
                        version_id,
                        f"Version mismatch: model claims {version_id}, "
                        f"snapshot has {snapshot_version}",
                    )

        # 3. Serialize artifact
        artifact_dir = (
            self.artifacts_dir / metadata.model_type.value / metadata.version
        )
        artifact_info = serialize_model(model, artifact_dir, metadata)

        # Compute metadata.json checksum for integrity verification
        # This allows detecting if the sidecar has been tampered with
        metadata_path = artifact_dir / "metadata.json"
        metadata_checksum = compute_checksum(metadata_path)

        # Update metadata with actual checksum
        metadata_dict = metadata.model_dump()
        metadata_dict["checksum_sha256"] = artifact_info.checksum

        # 4. Insert into database (with cleanup on failure)
        # Use explicit transaction so both inserts succeed or fail together
        # Cleanup orphan artifacts if DB operations fail
        with self._get_connection() as conn:
            try:
                conn.execute("BEGIN TRANSACTION")
                if self._version_exists(
                    metadata.model_type.value, metadata.version, conn=conn
                ):
                    raise VersionExistsError(
                        metadata.model_type.value, metadata.version
                    )
                conn.execute(
                    """
                    INSERT INTO models (
                        model_id, model_type, version, status, artifact_path,
                        checksum_sha256, metadata_sha256, created_at, config_hash, snapshot_id,
                        dataset_version_ids_json, metrics_json, factor_list_json,
                        experiment_id, run_id, dataset_uri, qlib_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        metadata.model_id,
                        metadata.model_type.value,
                        metadata.version,
                        ModelStatus.staged.value,
                        str(artifact_dir),
                        artifact_info.checksum,
                        metadata_checksum,
                        metadata.created_at.isoformat(),
                        metadata.config_hash,
                        metadata.snapshot_id,
                        json.dumps(metadata.dataset_version_ids),
                        json.dumps(metadata.metrics),
                        json.dumps(metadata.factor_list),
                        metadata.experiment_id,
                        metadata.run_id,
                        metadata.dataset_uri,
                        metadata.qlib_version,
                    ],
                )

                # Record in history
                conn.execute(
                    """
                    INSERT INTO promotion_history (model_id, from_status, to_status, changed_by)
                    VALUES (?, ?, ?, ?)
                    """,
                    [metadata.model_id, "", ModelStatus.staged.value, changed_by],
                )
                conn.execute("COMMIT")
            except VersionExistsError:
                conn.execute("ROLLBACK")
                if artifact_dir.exists():
                    shutil.rmtree(artifact_dir, ignore_errors=True)
                raise
            except duckdb.IntegrityError as e:
                conn.execute("ROLLBACK")
                if artifact_dir.exists():
                    logger.warning(
                        "Cleaning up orphan artifacts after integrity failure",
                        extra={
                            "artifact_dir": str(artifact_dir),
                            "error": str(e),
                        },
                    )
                    shutil.rmtree(artifact_dir, ignore_errors=True)
                if "unique" in str(e).lower():
                    raise VersionExistsError(
                        metadata.model_type.value, metadata.version
                    ) from e
                raise
            except Exception as e:
                conn.execute("ROLLBACK")
                # Cleanup orphan artifacts on DB failure
                if artifact_dir.exists():
                    logger.warning(
                        "Cleaning up orphan artifacts after DB insert failure",
                        extra={
                            "artifact_dir": str(artifact_dir),
                            "error": str(e),
                        },
                    )
                    shutil.rmtree(artifact_dir, ignore_errors=True)
                raise

        # 6. Update manifest
        self._update_manifest_counts()

        logger.info(
            "Registered model",
            extra={
                "model_id": metadata.model_id,
                "model_type": metadata.model_type.value,
                "version": metadata.version,
                "checksum": artifact_info.checksum[:16],
            },
        )

        return metadata.model_id

    def _version_exists(
        self,
        model_type: str,
        version: str,
        *,
        conn: duckdb.DuckDBPyConnection | None = None,
    ) -> bool:
        """Check if version already exists.

        When a connection is provided, the caller is responsible for
        transaction handling (used to avoid race conditions during writes).
        """
        if conn is None:
            with self._get_connection(read_only=True) as conn_ro:
                result = conn_ro.execute(
                    "SELECT 1 FROM models WHERE model_type = ? AND version = ?",
                    [model_type, version],
                ).fetchone()
        else:
            result = conn.execute(
                "SELECT 1 FROM models WHERE model_type = ? AND version = ?",
                [model_type, version],
            ).fetchone()

        return result is not None

    # =========================================================================
    # Promotion
    # =========================================================================

    def promote_model(
        self,
        model_type: str,
        version: str,
        *,
        changed_by: str = "unknown",
        skip_gates: bool = False,
    ) -> PromotionResult:
        """Promote model to production.

        Checks promotion gates unless skip_gates=True:
        - IC > min_ic (default 0.02)
        - Sharpe > min_sharpe (default 0.5)
        - Paper trading period completed (default 24h)

        Args:
            model_type: Type of model.
            version: Version to promote.
            changed_by: User/service making the change.
            skip_gates: Skip gate checks (for testing/emergency).

        Returns:
            PromotionResult.

        Raises:
            ModelNotFoundError: If model not found.
            PromotionGateError: If gate check fails.
        """
        metadata = self.get_model_metadata(model_type, version)
        if metadata is None:
            raise ModelNotFoundError(model_type, version)

        # Check promotion gates
        if not skip_gates:
            self._check_promotion_gates(metadata)

        now = datetime.now(UTC)
        old_production_version: str | None = None

        with self._get_connection() as conn:
            # Use explicit transaction to ensure atomicity
            # DuckDB uses BEGIN TRANSACTION (not BEGIN IMMEDIATE like SQLite)
            # The transaction prevents race conditions where concurrent promotions could
            # archive the wrong version or create duplicate production rows
            try:
                conn.execute("BEGIN TRANSACTION")

                # Get current production (inside transaction for consistency)
                result = conn.execute(
                    "SELECT version FROM models WHERE model_type = ? AND status = ?",
                    [model_type, ModelStatus.production.value],
                ).fetchone()
                if result:
                    old_production_version = result[0]

                # Archive ALL production versions for this model_type
                # This prevents race conditions where concurrent promotions could
                # both archive the same single version and each set their own to production
                # resulting in multiple production rows
                conn.execute(
                    """
                    INSERT INTO promotion_history (model_id, from_status, to_status, changed_by)
                    SELECT model_id, ?, ?, ?
                    FROM models WHERE model_type = ? AND status = ?
                    """,
                    [
                        ModelStatus.production.value,
                        ModelStatus.archived.value,
                        changed_by,
                        model_type,
                        ModelStatus.production.value,
                    ],
                )
                conn.execute(
                    """
                    UPDATE models SET status = ?, archived_at = ?
                    WHERE model_type = ? AND status = ?
                    """,
                    [
                        ModelStatus.archived.value,
                        now.isoformat(),
                        model_type,
                        ModelStatus.production.value,
                    ],
                )

                # Promote new version
                conn.execute(
                    """
                    UPDATE models SET status = ?, promoted_at = ?
                    WHERE model_type = ? AND version = ?
                    """,
                    [ModelStatus.production.value, now.isoformat(), model_type, version],
                )
                conn.execute(
                    """
                    INSERT INTO promotion_history (model_id, from_status, to_status, changed_by)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        metadata.model_id,
                        ModelStatus.staged.value,
                        ModelStatus.production.value,
                        changed_by,
                    ],
                )

                conn.execute("COMMIT")
            except Exception as e:
                conn.execute("ROLLBACK")
                logger.error(f"Promotion transaction failed: {e}")
                raise

        # Update manifest (outside transaction - if this fails, DB is consistent,
        # and manifest will be refreshed on next registry load)
        try:
            self._update_manifest_production()
        except Exception as e:
            logger.error(
                f"Failed to update manifest after promotion - manifest may be stale: {e}"
            )
            # Don't re-raise: DB transaction succeeded, manifest is a cache

        logger.info(
            "Promoted model to production",
            extra={
                "model_type": model_type,
                "version": version,
                "previous_version": old_production_version,
            },
        )

        return PromotionResult(
            success=True,
            model_id=metadata.model_id,
            from_version=old_production_version,
            to_version=version,
            promoted_at=now,
            message=f"Promoted {model_type}/{version} to production",
        )

    def _check_promotion_gates(self, metadata: ModelMetadata) -> None:
        """Check if model passes promotion gates.

        Args:
            metadata: Model metadata with metrics.

        Raises:
            PromotionGateError: If gate fails.
        """
        # Check IC threshold
        ic = metadata.metrics.get("ic", 0)
        if ic < self.gates.min_ic:
            raise PromotionGateError("ic", ic, self.gates.min_ic)

        # Check Sharpe threshold
        sharpe = metadata.metrics.get("sharpe", 0)
        if sharpe < self.gates.min_sharpe:
            raise PromotionGateError("sharpe", sharpe, self.gates.min_sharpe)

        # Check paper trading period (via metrics or separate tracking)
        paper_hours = metadata.metrics.get("paper_trade_hours", 0)
        if paper_hours < self.gates.min_paper_trade_hours:
            raise PromotionGateError(
                "paper_trade_hours", paper_hours, self.gates.min_paper_trade_hours
            )

    # =========================================================================
    # Rollback
    # =========================================================================

    def rollback_model(
        self,
        model_type: str,
        *,
        changed_by: str = "unknown",
    ) -> RollbackResult:
        """Rollback to previous production version.

        Args:
            model_type: Type of model to rollback.
            changed_by: User/service making the change.

        Returns:
            RollbackResult.

        Raises:
            ModelNotFoundError: If no production or archived version exists.
        """
        now = datetime.now(UTC)

        with self._get_connection() as conn:
            # Use explicit transaction to ensure atomicity
            # DuckDB uses BEGIN TRANSACTION (not BEGIN IMMEDIATE like SQLite)
            # The transaction prevents race conditions where concurrent rollbacks could
            # operate on stale production/archived state
            try:
                conn.execute("BEGIN TRANSACTION")

                # Get current production (inside transaction for consistency)
                current = conn.execute(
                    "SELECT model_id, version FROM models WHERE model_type = ? AND status = ?",
                    [model_type, ModelStatus.production.value],
                ).fetchone()
                if not current:
                    conn.execute("ROLLBACK")
                    raise ModelNotFoundError(model_type, "production")

                current_id, current_version = current

                # Get most recent archived version
                previous = conn.execute(
                    """
                    SELECT model_id, version FROM models
                    WHERE model_type = ? AND status = ?
                    ORDER BY archived_at DESC LIMIT 1
                    """,
                    [model_type, ModelStatus.archived.value],
                ).fetchone()

                # Fail if no archived version to rollback to - don't leave registry without production
                if not previous:
                    conn.execute("ROLLBACK")
                    raise ModelNotFoundError(
                        model_type,
                        "archived",
                        "Cannot rollback: no archived version available to restore. "
                        "Current production model will remain unchanged.",
                    )

                previous_id, previous_version = previous

                # Promote previous to production
                conn.execute(
                    "UPDATE models SET status = ?, promoted_at = ? WHERE model_id = ?",
                    [ModelStatus.production.value, now.isoformat(), previous_id],
                )
                conn.execute(
                    """
                    INSERT INTO promotion_history (model_id, from_status, to_status, changed_by)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        previous_id,
                        ModelStatus.archived.value,
                        ModelStatus.production.value,
                        changed_by,
                    ],
                )

                # Move current to staged
                conn.execute(
                    "UPDATE models SET status = ?, promoted_at = NULL WHERE model_id = ?",
                    [ModelStatus.staged.value, current_id],
                )
                conn.execute(
                    """
                    INSERT INTO promotion_history (model_id, from_status, to_status, changed_by)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        current_id,
                        ModelStatus.production.value,
                        ModelStatus.staged.value,
                        changed_by,
                    ],
                )

                conn.execute("COMMIT")
            except Exception as e:
                conn.execute("ROLLBACK")
                logger.error(f"Rollback transaction failed: {e}")
                raise

        # Update manifest (outside transaction - if this fails, DB is consistent,
        # and manifest will be refreshed on next registry load)
        try:
            self._update_manifest_production()
        except Exception as e:
            logger.error(
                f"Failed to update manifest after rollback - manifest may be stale: {e}"
            )
            # Don't re-raise: DB transaction succeeded, manifest is a cache

        logger.info(
            "Rolled back model",
            extra={
                "model_type": model_type,
                "from_version": current_version,
                "to_version": previous_version,
            },
        )

        return RollbackResult(
            success=True,
            model_type=ModelType(model_type),
            from_version=current_version,
            to_version=previous_version,
            rolled_back_at=now,
            message=f"Rolled back {model_type} from {current_version} to {previous_version}",
        )

    # =========================================================================
    # Queries
    # =========================================================================

    def _load_verified_metadata(
        self, artifact_path: Path, expected_metadata_sha256: str
    ) -> ModelMetadata:
        """Load metadata with integrity verification against DB-stored checksum.

        This prevents serving tampered metadata by verifying the sidecar file's
        checksum against the DB-stored value from registration time.

        Args:
            artifact_path: Path to artifact directory.
            expected_metadata_sha256: Expected checksum from DB.

        Returns:
            ModelMetadata after verification.

        Raises:
            IntegrityError: If metadata checksum doesn't match DB value.
        """
        metadata_path = artifact_path / "metadata.json"
        actual_checksum = compute_checksum(metadata_path)

        if actual_checksum != expected_metadata_sha256:
            logger.error(
                "Metadata integrity check failed - possible tampering detected",
                extra={
                    "artifact_path": str(artifact_path),
                    "expected": expected_metadata_sha256[:16],
                    "actual": actual_checksum[:16],
                },
            )
            raise IntegrityError(
                f"Metadata integrity check failed for {artifact_path}: "
                f"expected {expected_metadata_sha256[:16]}..., got {actual_checksum[:16]}..."
            )

        return load_metadata(artifact_path)

    def get_current_production(self, model_type: str) -> ModelMetadata | None:
        """Get current production model metadata.

        Args:
            model_type: Type of model.

        Returns:
            ModelMetadata or None if no production version.
        """
        with self._get_connection(read_only=True) as conn:
            result = conn.execute(
                "SELECT artifact_path, metadata_sha256 FROM models WHERE model_type = ? AND status = ?",
                [model_type, ModelStatus.production.value],
            ).fetchone()

        if not result:
            return None

        artifact_path = Path(result[0])
        metadata_sha256 = result[1]
        return self._load_verified_metadata(artifact_path, metadata_sha256)

    def get_model_metadata(
        self, model_type: str, version: str
    ) -> ModelMetadata | None:
        """Get model metadata by type and version.

        Args:
            model_type: Type of model.
            version: Version string.

        Returns:
            ModelMetadata or None if not found.
        """
        with self._get_connection(read_only=True) as conn:
            result = conn.execute(
                "SELECT artifact_path, metadata_sha256 FROM models WHERE model_type = ? AND version = ?",
                [model_type, version],
            ).fetchone()

        if not result:
            return None

        artifact_path = Path(result[0])
        metadata_sha256 = result[1]
        return self._load_verified_metadata(artifact_path, metadata_sha256)

    def get_model_by_id(self, model_id: str) -> ModelMetadata | None:
        """Get model metadata by ID.

        Args:
            model_id: Model ID.

        Returns:
            ModelMetadata or None if not found.
        """
        with self._get_connection(read_only=True) as conn:
            result = conn.execute(
                "SELECT artifact_path, metadata_sha256 FROM models WHERE model_id = ?",
                [model_id],
            ).fetchone()

        if not result:
            return None

        artifact_path = Path(result[0])
        metadata_sha256 = result[1]
        return self._load_verified_metadata(artifact_path, metadata_sha256)

    def list_models(
        self,
        model_type: str | None = None,
        status: ModelStatus | None = None,
        *,
        lightweight: bool = True,
    ) -> list[ModelMetadata]:
        """List models with optional filtering.

        Args:
            model_type: Filter by type.
            status: Filter by status.
            lightweight: If True (default), load metadata without checksum
                verification for faster responses. Set to False to verify
                metadata checksums on disk for integrity-sensitive paths.

        Returns:
            List of ModelMetadata.
        """
        query = "SELECT artifact_path, metadata_sha256 FROM models WHERE 1=1"
        params: list[str] = []

        if model_type:
            query += " AND model_type = ?"
            params.append(model_type)
        if status:
            query += " AND status = ?"
            params.append(status.value)

        query += " ORDER BY created_at DESC"

        with self._get_connection(read_only=True) as conn:
            results = conn.execute(query, params).fetchall()

        if lightweight:
            return [load_metadata(Path(row[0])) for row in results]

        return [
            self._load_verified_metadata(Path(row[0]), row[1])
            for row in results
        ]

    def validate_model(self, model_type: str, version: str) -> ValidationResult:
        """Validate model artifact integrity and loadability.

        Validates against the DB-stored checksum (authoritative), not the sidecar
        metadata.json which could be tampered with alongside the model file.

        Args:
            model_type: Type of model.
            version: Version to validate.

        Returns:
            ValidationResult.
        """
        # Fetch artifact path and checksums from DB (authoritative source)
        with self._get_connection(read_only=True) as conn:
            result = conn.execute(
                """
                SELECT model_id, artifact_path, checksum_sha256, metadata_sha256
                FROM models WHERE model_type = ? AND version = ?
                """,
                [model_type, version],
            ).fetchone()

        if not result:
            return ValidationResult(
                valid=False,
                model_id="",
                checksum_verified=False,
                load_successful=False,
                errors=[f"Model {model_type}/{version} not found in registry"],
            )

        model_id, artifact_path_str, db_checksum, db_metadata_checksum = result
        artifact_dir = Path(artifact_path_str)

        errors: list[str] = []

        # Verify model file checksum against DB value
        model_path = artifact_dir / (
            "model.json" if model_type == "alpha_weights" else "model.pkl"
        )

        checksum_verified = False
        if model_path.exists():
            actual_checksum = compute_checksum(model_path)
            checksum_verified = actual_checksum == db_checksum
            if not checksum_verified:
                errors.append(
                    f"Model checksum mismatch: DB expected {db_checksum[:16]}..., "
                    f"got {actual_checksum[:16]}..."
                )
        else:
            errors.append(f"Model file not found: {model_path}")

        # Verify metadata.json checksum against DB value (tamper detection)
        metadata_path = artifact_dir / "metadata.json"
        metadata_verified = False
        if metadata_path.exists():
            actual_metadata_checksum = compute_checksum(metadata_path)
            metadata_verified = actual_metadata_checksum == db_metadata_checksum
            if not metadata_verified:
                errors.append(
                    f"Metadata checksum mismatch (possible tampering): "
                    f"DB expected {db_metadata_checksum[:16]}..., "
                    f"got {actual_metadata_checksum[:16]}..."
                )
        else:
            errors.append(f"Metadata file not found: {metadata_path}")

        # Overall checksum verification requires both model and metadata to pass
        checksum_verified = checksum_verified and metadata_verified

        # Try to load
        load_successful = False
        if checksum_verified:
            try:
                deserialize_model(artifact_dir)
                load_successful = True
            except Exception as e:
                errors.append(f"Load failed: {e!s}")

        return ValidationResult(
            valid=checksum_verified and load_successful,
            model_id=model_id,
            checksum_verified=checksum_verified,
            load_successful=load_successful,
            errors=errors,
        )

    # =========================================================================
    # Manifest Updates
    # =========================================================================

    def _update_manifest_counts(self) -> None:
        """Update manifest with current counts."""
        with self._get_connection(read_only=True) as conn:
            result = conn.execute("SELECT COUNT(*) FROM models").fetchone()
            count = int(result[0]) if result else 0

        # Calculate total size
        total_size = 0
        if self.artifacts_dir.exists():
            for f in self.artifacts_dir.rglob("*"):
                if f.is_file():
                    total_size += f.stat().st_size

        self.manifest_manager.update_manifest(
            artifact_count=count,
            total_size_bytes=total_size,
        )

    def _update_manifest_production(self) -> None:
        """Update manifest with current production models."""
        production_models: dict[str, str] = {}

        with self._get_connection(read_only=True) as conn:
            results = conn.execute(
                "SELECT model_type, version FROM models WHERE status = ?",
                [ModelStatus.production.value],
            ).fetchall()

        for model_type, version in results:
            production_models[model_type] = version

        self.manifest_manager.update_manifest(production_models=production_models)

    # =========================================================================
    # Utilities
    # =========================================================================

    def get_artifact_path(self, model_type: str, version: str) -> Path | None:
        """Get artifact directory path.

        Args:
            model_type: Type of model.
            version: Version string.

        Returns:
            Path to artifact directory or None if not found.
        """
        with self._get_connection(read_only=True) as conn:
            result = conn.execute(
                "SELECT artifact_path FROM models WHERE model_type = ? AND version = ?",
                [model_type, version],
            ).fetchone()

        return Path(result[0]) if result else None

    def get_model_info(
        self, model_type: str, version: str
    ) -> dict[str, Any] | None:
        """Get model status and path info from database.

        Args:
            model_type: Type of model.
            version: Version string.

        Returns:
            Dict with status, artifact_path, promoted_at or None if not found.
        """
        with self._get_connection(read_only=True) as conn:
            result = conn.execute(
                """
                SELECT status, artifact_path, promoted_at
                FROM models WHERE model_type = ? AND version = ?
                """,
                [model_type, version],
            ).fetchone()

        if not result:
            return None

        status_val, artifact_path, promoted_at_str = result
        promoted_at = None
        if promoted_at_str:
            promoted_at = datetime.fromisoformat(promoted_at_str)

        return {
            "status": status_val,
            "artifact_path": artifact_path,
            "promoted_at": promoted_at,
        }

    def get_model_info_bulk(
        self, model_type: str, versions: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Bulk-fetch model status/path info to avoid N+1 queries.

        Args:
            model_type: Type of model.
            versions: Versions to fetch.

        Returns:
            Mapping of version -> info dict (status, artifact_path, promoted_at).
        """

        if not versions:
            return {}

        placeholders = ",".join(["?"] * len(versions))
        query = (
            "SELECT version, status, artifact_path, promoted_at "
            "FROM models WHERE model_type = ? AND version IN (" + placeholders + ")"
        )

        with self._get_connection(read_only=True) as conn:
            rows = conn.execute(query, [model_type, *versions]).fetchall()

        info: dict[str, dict[str, Any]] = {}
        for version, status_val, artifact_path, promoted_at_str in rows:
            promoted_at = None
            if promoted_at_str:
                promoted_at = datetime.fromisoformat(promoted_at_str)
            info[str(version)] = {
                "status": status_val,
                "artifact_path": artifact_path,
                "promoted_at": promoted_at,
            }

        return info

    def get_manifest(self) -> RegistryManifest:
        """Get current registry manifest."""
        return self.manifest_manager.load_manifest()


def generate_model_id() -> str:
    """Generate unique model ID."""
    return str(uuid.uuid4())
