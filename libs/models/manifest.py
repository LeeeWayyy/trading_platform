"""
Registry manifest management for discoverability and DR.

This module provides:
- RegistryManifestManager: Manages registry-level manifest
- Manifest lifecycle: creation, update, verification
- DR support: backup location tracking, integrity verification

Key design decisions:
- manifest.json updated atomically with registry changes
- Checksum of registry.db for integrity verification
- Production model summary for quick status checks
- DR fields track backup state for disaster recovery
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from libs.models.serialization import compute_checksum
from libs.models.types import RegistryManifest

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class ManifestIntegrityError(Exception):
    """Raised when manifest integrity check fails."""

    def __init__(
        self, expected: str | None = None, actual: str | None = None, message: str | None = None
    ) -> None:
        self.expected = expected
        self.actual = actual
        if message:
            super().__init__(message)
        elif expected and actual:
            super().__init__(
                f"Manifest integrity check failed: "
                f"expected checksum {expected[:16]}..., got {actual[:16]}..."
            )
        else:
            super().__init__("Manifest integrity check failed")


class RegistryManifestManager:
    """Manages registry-level manifest for discoverability and DR.

    The manifest tracks:
    - Registry schema version
    - Current production models per type
    - Total artifact count and size
    - Last backup timestamp and location
    - Registry.db checksum for integrity verification

    Lifecycle:
    - Created on first registry initialization
    - Updated after every register/promote/rollback/GC operation
    - Verified on registry load for integrity
    """

    MANIFEST_FILENAME = "manifest.json"
    REGISTRY_VERSION = "1.0.0"

    def __init__(self, registry_dir: Path) -> None:
        """Initialize manifest manager.

        Args:
            registry_dir: Path to registry directory (contains registry.db).
        """
        self.registry_dir = registry_dir
        self.manifest_path = registry_dir / self.MANIFEST_FILENAME
        self.db_path = registry_dir / "registry.db"
        self._lock_path = registry_dir / ".manifest.lock"

    @contextmanager
    def _manifest_lock(self) -> Iterator[None]:
        """Acquire exclusive lock for manifest updates.

        Prevents race conditions when multiple processes update the manifest.
        Uses file locking (fcntl) for cross-process synchronization.
        """
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = open(self._lock_path, "w")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()

    def exists(self) -> bool:
        """Check if manifest exists."""
        return self.manifest_path.exists()

    def create_manifest(
        self,
        production_models: dict[str, str] | None = None,
        artifact_count: int = 0,
        total_size_bytes: int = 0,
    ) -> RegistryManifest:
        """Create initial manifest.

        Args:
            production_models: Current production models {type: version}.
            artifact_count: Initial artifact count.
            total_size_bytes: Initial total size.

        Returns:
            Created RegistryManifest.
        """
        now = datetime.now(UTC)

        # Compute registry.db checksum if it exists
        db_checksum = ""
        if self.db_path.exists():
            db_checksum = compute_checksum(self.db_path)

        manifest = RegistryManifest(
            registry_version=self.REGISTRY_VERSION,
            created_at=now,
            last_updated=now,
            artifact_count=artifact_count,
            production_models=production_models or {},
            total_size_bytes=total_size_bytes,
            checksum=db_checksum,
            last_backup_at=None,
            backup_location=None,
        )

        self._save_manifest(manifest)
        logger.info(
            "Created registry manifest",
            extra={"path": str(self.manifest_path), "artifact_count": artifact_count},
        )
        return manifest

    def load_manifest(self) -> RegistryManifest:
        """Load manifest from file.

        Returns:
            RegistryManifest.

        Raises:
            FileNotFoundError: If manifest doesn't exist.
            ValueError: If manifest is invalid.
        """
        if not self.manifest_path.exists():
            raise FileNotFoundError(f"Manifest not found: {self.manifest_path}")

        with open(self.manifest_path) as f:
            data = json.load(f)

        return RegistryManifest.model_validate(data)

    def update_manifest(
        self,
        *,
        production_models: dict[str, str] | None = None,
        artifact_count: int | None = None,
        total_size_bytes: int | None = None,
        last_backup_at: datetime | None = None,
        backup_location: str | None = None,
    ) -> RegistryManifest:
        """Update manifest with new values.

        Only provided values are updated; others are preserved.
        Uses file locking to prevent race conditions during concurrent updates.

        Args:
            production_models: Updated production models.
            artifact_count: Updated artifact count.
            total_size_bytes: Updated total size.
            last_backup_at: Last backup timestamp.
            backup_location: Backup location path.

        Returns:
            Updated RegistryManifest.
        """
        # Lock the entire read-modify-write cycle to prevent race conditions
        with self._manifest_lock():
            manifest = self.load_manifest()

            # Build update dict, preserving existing values
            updates: dict[str, datetime | dict[str, str] | int | str] = {
                "last_updated": datetime.now(UTC),
            }

            if production_models is not None:
                updates["production_models"] = production_models
            if artifact_count is not None:
                updates["artifact_count"] = artifact_count
            if total_size_bytes is not None:
                updates["total_size_bytes"] = total_size_bytes
            if last_backup_at is not None:
                updates["last_backup_at"] = last_backup_at
            if backup_location is not None:
                updates["backup_location"] = backup_location

            # Recompute checksum if db exists
            if self.db_path.exists():
                updates["checksum"] = compute_checksum(self.db_path)

            # Create new manifest with updates
            manifest_dict = manifest.model_dump()
            manifest_dict.update(updates)
            updated_manifest = RegistryManifest.model_validate(manifest_dict)

            self._save_manifest(updated_manifest)
            logger.debug(
                "Updated registry manifest",
                extra={
                    "artifact_count": updated_manifest.artifact_count,
                    "production_models": list(updated_manifest.production_models.keys()),
                },
            )
            return updated_manifest

    def verify_integrity(self) -> bool:
        """Verify registry.db checksum matches manifest.

        Returns:
            True if checksum matches, False otherwise.

        Raises:
            FileNotFoundError: If manifest or registry.db doesn't exist.
        """
        manifest = self.load_manifest()

        if not self.db_path.exists():
            # No db yet is OK for empty registry
            return manifest.artifact_count == 0

        actual_checksum = compute_checksum(self.db_path)
        return actual_checksum == manifest.checksum

    def verify_integrity_strict(self) -> None:
        """Verify integrity, raising on mismatch.

        Raises:
            ManifestIntegrityError: If checksum doesn't match.
            FileNotFoundError: If files don't exist.
        """
        manifest = self.load_manifest()

        if not self.db_path.exists() and manifest.artifact_count > 0:
            raise FileNotFoundError(
                f"Registry database not found but manifest shows {manifest.artifact_count} artifacts"
            )

        if self.db_path.exists():
            actual_checksum = compute_checksum(self.db_path)
            if actual_checksum != manifest.checksum:
                raise ManifestIntegrityError(manifest.checksum, actual_checksum)

    def get_production_summary(self) -> dict[str, str]:
        """Get current production models.

        Returns:
            Dict of {model_type: version} for production models.
        """
        manifest = self.load_manifest()
        return dict(manifest.production_models)

    def _save_manifest(self, manifest: RegistryManifest) -> None:
        """Atomically save manifest to file.

        Args:
            manifest: Manifest to save.
        """
        self.registry_dir.mkdir(parents=True, exist_ok=True)

        # Write to temp file then atomic rename
        fd, temp_path = tempfile.mkstemp(dir=self.registry_dir, prefix=".manifest_", suffix=".json")
        try:
            content = manifest.model_dump_json(indent=2)
            with os.fdopen(fd, "w") as f:
                f.write(content)
            # Atomic rename
            shutil.move(temp_path, self.manifest_path)
        except Exception as e:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except OSError as unlink_err:
                logger.warning(
                    "Manifest write failed - failed to clean up temp file",
                    extra={"temp_path": temp_path, "error": str(unlink_err)},
                )
            logger.error(
                "Manifest write failed - atomic write error",
                extra={"manifest_path": str(self.manifest_path), "error": str(e)},
                exc_info=True,
            )
            raise

    def record_backup(self, backup_location: str) -> RegistryManifest:
        """Record a backup operation.

        Args:
            backup_location: Path where backup was stored.

        Returns:
            Updated manifest.
        """
        return self.update_manifest(
            last_backup_at=datetime.now(UTC),
            backup_location=backup_location,
        )
