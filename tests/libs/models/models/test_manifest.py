"""Tests for RegistryManifestManager."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from libs.models.models.manifest import ManifestIntegrityError, RegistryManifestManager
from libs.models.models.serialization import compute_checksum


@pytest.fixture()
def registry_dir(tmp_path: Path) -> Path:
    """Create a temporary registry directory."""
    return tmp_path / "registry"


@pytest.fixture()
def manifest_manager(registry_dir: Path) -> RegistryManifestManager:
    """Create a manifest manager for a temp registry."""
    return RegistryManifestManager(registry_dir)


class TestRegistryManifestManager:
    """Tests for manifest creation, updates, and integrity checks."""

    def test_create_manifest_without_db(self, manifest_manager: RegistryManifestManager) -> None:
        """Manifest should initialize with empty checksum when db is missing."""
        manifest = manifest_manager.create_manifest(
            production_models={"risk_model": "v1.0.0"},
            artifact_count=2,
            total_size_bytes=1024,
        )

        assert manifest_manager.manifest_path.exists()
        assert manifest.checksum == ""
        assert manifest.production_models == {"risk_model": "v1.0.0"}
        assert manifest.artifact_count == 2
        assert manifest.total_size_bytes == 1024
        assert manifest.created_at.tzinfo is not None
        assert manifest.created_at.utcoffset() == datetime.now(UTC).utcoffset()

    def test_create_manifest_with_db_checksum(
        self, manifest_manager: RegistryManifestManager, registry_dir: Path
    ) -> None:
        """Manifest should include checksum when registry.db exists."""
        db_path = registry_dir / "registry.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.write_bytes(b"registry-data")

        manifest = manifest_manager.create_manifest()

        assert manifest.checksum == compute_checksum(db_path)

    def test_update_manifest_updates_selected_fields(
        self, manifest_manager: RegistryManifestManager, registry_dir: Path
    ) -> None:
        """Update should preserve fields not explicitly provided."""
        db_path = registry_dir / "registry.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.write_bytes(b"initial")

        manifest_manager.create_manifest(
            production_models={"risk_model": "v1.0.0"},
            artifact_count=1,
            total_size_bytes=50,
        )

        db_path.write_bytes(b"updated")
        backup_time = datetime(2024, 1, 1, tzinfo=UTC)

        updated = manifest_manager.update_manifest(
            production_models={"risk_model": "v1.1.0"},
            artifact_count=2,
            last_backup_at=backup_time,
            backup_location="s3://bucket/backup",
        )

        assert updated.production_models == {"risk_model": "v1.1.0"}
        assert updated.artifact_count == 2
        assert updated.total_size_bytes == 50
        assert updated.last_backup_at == backup_time
        assert updated.backup_location == "s3://bucket/backup"
        assert updated.checksum == compute_checksum(db_path)
        assert updated.last_updated >= updated.created_at

    def test_verify_integrity_no_db_empty_registry(
        self, manifest_manager: RegistryManifestManager
    ) -> None:
        """verify_integrity should be true when db is missing but empty."""
        manifest_manager.create_manifest(artifact_count=0)

        assert manifest_manager.verify_integrity() is True

    def test_verify_integrity_strict_missing_db_with_artifacts_raises(
        self, manifest_manager: RegistryManifestManager
    ) -> None:
        """verify_integrity_strict should raise when db missing but artifacts exist."""
        manifest_manager.create_manifest(artifact_count=1)

        with pytest.raises(FileNotFoundError):
            manifest_manager.verify_integrity_strict()

    def test_verify_integrity_strict_checksum_mismatch_raises(
        self, manifest_manager: RegistryManifestManager, registry_dir: Path
    ) -> None:
        """verify_integrity_strict should raise on checksum mismatch."""
        db_path = registry_dir / "registry.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.write_bytes(b"registry-data")

        manifest_manager.create_manifest()

        manifest_path = manifest_manager.manifest_path
        data = json.loads(manifest_path.read_text())
        data["checksum"] = "invalid-checksum"
        manifest_path.write_text(json.dumps(data))

        with pytest.raises(ManifestIntegrityError):
            manifest_manager.verify_integrity_strict()

    def test_record_backup_updates_manifest(
        self, manifest_manager: RegistryManifestManager
    ) -> None:
        """record_backup should update backup fields."""
        manifest_manager.create_manifest()

        updated = manifest_manager.record_backup("s3://bucket/backup")

        assert updated.backup_location == "s3://bucket/backup"
        assert updated.last_backup_at is not None
