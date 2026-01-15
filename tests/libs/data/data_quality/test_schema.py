"""Tests for libs.data_quality.schema module."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from libs.data.data_quality.exceptions import LockNotHeldError, SchemaError
from libs.data.data_quality.schema import DatasetSchema, SchemaDrift, SchemaRegistry


class TestSchemaDrift:
    """Tests for SchemaDrift dataclass."""

    def test_is_breaking_with_removed_columns(self) -> None:
        """Test is_breaking returns True when columns are removed."""
        drift = SchemaDrift(
            removed_columns=["col_a"],
        )

        assert drift.is_breaking is True

    def test_is_breaking_with_changed_columns(self) -> None:
        """Test is_breaking returns True when column types changed."""
        drift = SchemaDrift(
            changed_columns=[("col_a", "int64", "str")],
        )

        assert drift.is_breaking is True

    def test_is_breaking_with_additions_only(self) -> None:
        """Test is_breaking returns False with only additions."""
        drift = SchemaDrift(
            added_columns=["new_col"],
        )

        assert drift.is_breaking is False

    def test_has_additions_true(self) -> None:
        """Test has_additions returns True when columns added."""
        drift = SchemaDrift(
            added_columns=["new_col"],
        )

        assert drift.has_additions is True

    def test_has_additions_false(self) -> None:
        """Test has_additions returns False when no columns added."""
        drift = SchemaDrift()

        assert drift.has_additions is False

    def test_has_drift_with_any_changes(self) -> None:
        """Test has_drift returns True for any changes."""
        assert SchemaDrift(added_columns=["a"]).has_drift is True
        assert SchemaDrift(removed_columns=["a"]).has_drift is True
        assert SchemaDrift(changed_columns=[("a", "int", "str")]).has_drift is True

    def test_has_drift_false_when_empty(self) -> None:
        """Test has_drift returns False when no changes."""
        drift = SchemaDrift()

        assert drift.has_drift is False


class TestDatasetSchema:
    """Tests for DatasetSchema dataclass."""

    def test_to_dict_serialization(self) -> None:
        """Test DatasetSchema serializes to dict correctly."""
        now = datetime.now(UTC)
        schema = DatasetSchema(
            dataset="test",
            version="v1.0.0",
            columns={"a": "int64", "b": "str"},
            created_at=now,
            description="Test schema",
        )

        result = schema.to_dict()

        assert result["dataset"] == "test"
        assert result["version"] == "v1.0.0"
        assert result["columns"] == {"a": "int64", "b": "str"}
        assert result["created_at"] == now.isoformat()
        assert result["description"] == "Test schema"

    def test_from_dict_deserialization(self) -> None:
        """Test DatasetSchema deserializes from dict correctly."""
        now = datetime.now(UTC)
        data = {
            "dataset": "test",
            "version": "v1.0.0",
            "columns": {"a": "int64", "b": "str"},
            "created_at": now.isoformat(),
            "description": "Test schema",
        }

        schema = DatasetSchema.from_dict(data)

        assert schema.dataset == "test"
        assert schema.version == "v1.0.0"
        assert schema.columns == {"a": "int64", "b": "str"}
        assert schema.created_at == now
        assert schema.description == "Test schema"


class TestSchemaRegistry:
    """Tests for SchemaRegistry class."""

    @pytest.fixture()
    def temp_dirs(self, tmp_path: Path) -> dict[str, Path]:
        """Create temporary directories for testing."""
        schemas = tmp_path / "schemas"
        locks = tmp_path / "locks"

        for d in [schemas, locks]:
            d.mkdir(parents=True)

        return {"schemas": schemas, "locks": locks}

    @pytest.fixture()
    def registry(self, temp_dirs: dict[str, Path]) -> SchemaRegistry:
        """Create SchemaRegistry with temp directories."""
        return SchemaRegistry(
            storage_path=temp_dirs["schemas"],
            lock_dir=temp_dirs["locks"],
        )

    def test_get_expected_schema_success(self, registry: SchemaRegistry) -> None:
        """Test get_expected_schema returns schema when found."""
        # Register a schema first
        version = registry.register_schema(
            "test_dataset",
            {"col_a": "int64", "col_b": "str"},
            "Test description",
        )

        result = registry.get_expected_schema("test_dataset")

        assert result is not None
        assert result.dataset == "test_dataset"
        assert result.version == version
        assert result.columns == {"col_a": "int64", "col_b": "str"}

    def test_get_expected_schema_not_found(self, registry: SchemaRegistry) -> None:
        """Test get_expected_schema returns None when not found."""
        result = registry.get_expected_schema("nonexistent")

        assert result is None

    def test_get_expected_schema_specific_version(self, registry: SchemaRegistry) -> None:
        """Test get_expected_schema retrieves specific version from history."""
        # Register first version
        registry.register_schema(
            "versioned_dataset",
            {"col_a": "int64"},
        )

        # Register second version (adds column)
        registry.register_schema(
            "versioned_dataset",
            {"col_a": "int64", "col_b": "str"},
        )

        # Get specific version
        result = registry.get_expected_schema("versioned_dataset", "v1.0.0")

        assert result is not None
        assert result.version == "v1.0.0"
        assert "col_b" not in result.columns

    def test_detect_drift_no_drift(self, registry: SchemaRegistry) -> None:
        """Test detect_drift returns empty drift when schemas match."""
        registry.register_schema("no_drift", {"col_a": "int64"})

        drift = registry.detect_drift("no_drift", {"col_a": "int64"})

        assert drift.has_drift is False

    def test_detect_drift_new_columns_warning(self, registry: SchemaRegistry) -> None:
        """Test detect_drift detects new columns."""
        registry.register_schema("add_cols", {"col_a": "int64"})

        drift = registry.detect_drift(
            "add_cols",
            {
                "col_a": "int64",
                "col_b": "str",  # New
            },
        )

        assert drift.has_additions is True
        assert "col_b" in drift.added_columns
        assert drift.is_breaking is False

    def test_detect_drift_removed_columns_error(self, registry: SchemaRegistry) -> None:
        """Test detect_drift detects removed columns as breaking."""
        registry.register_schema(
            "remove_cols",
            {
                "col_a": "int64",
                "col_b": "str",
            },
        )

        drift = registry.detect_drift("remove_cols", {"col_a": "int64"})

        assert drift.is_breaking is True
        assert "col_b" in drift.removed_columns

    def test_detect_drift_type_changes_error(self, registry: SchemaRegistry) -> None:
        """Test detect_drift detects type changes as breaking."""
        registry.register_schema("type_change", {"col_a": "int64"})

        drift = registry.detect_drift("type_change", {"col_a": "str"})

        assert drift.is_breaking is True
        assert len(drift.changed_columns) == 1
        assert drift.changed_columns[0][0] == "col_a"

    def test_drift_policy_additions_triggers_version_bump(self, registry: SchemaRegistry) -> None:
        """Test apply_drift_policy auto-bumps version for additions."""
        registry.register_schema("auto_bump", {"col_a": "int64"})

        drift = SchemaDrift(added_columns=["col_b"])
        new_schema = {"col_a": "int64", "col_b": "str"}

        version, message = registry.apply_drift_policy("auto_bump", drift, new_schema)

        assert version == "v1.1.0"  # Minor bump
        assert "auto" in message.lower() or "bump" in message.lower()

    def test_drift_policy_persists_new_schema(self, registry: SchemaRegistry) -> None:
        """Test apply_drift_policy persists new schema atomically."""
        registry.register_schema("persist_test", {"col_a": "int64"})

        drift = SchemaDrift(added_columns=["col_b"])
        new_schema = {"col_a": "int64", "col_b": "str"}

        registry.apply_drift_policy("persist_test", drift, new_schema)

        # Verify persisted
        loaded = registry.get_expected_schema("persist_test")
        assert loaded is not None
        assert "col_b" in loaded.columns

    def test_drift_policy_returns_version_string(self, registry: SchemaRegistry) -> None:
        """Test apply_drift_policy returns correct version string."""
        registry.register_schema("version_test", {"col_a": "int64"})

        drift = SchemaDrift(added_columns=["col_b"])
        new_schema = {"col_a": "int64", "col_b": "str"}

        version, _ = registry.apply_drift_policy("version_test", drift, new_schema)

        assert version.startswith("v")
        assert "." in version

    def test_drift_policy_version_format(self, registry: SchemaRegistry) -> None:
        """Test version format is v{major}.{minor}.{patch}."""
        registry.register_schema("format_test", {"col_a": "int64"})

        drift = SchemaDrift(added_columns=["col_b"])
        new_schema = {"col_a": "int64", "col_b": "str"}

        version, _ = registry.apply_drift_policy("format_test", drift, new_schema)

        parts = version.lstrip("v").split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)

    def test_drift_policy_minor_increments_on_new_columns(self, registry: SchemaRegistry) -> None:
        """Test minor version increments when columns are added."""
        registry.register_schema("minor_test", {"col_a": "int64"})

        # First addition
        drift1 = SchemaDrift(added_columns=["col_b"])
        schema1 = {"col_a": "int64", "col_b": "str"}
        v1, _ = registry.apply_drift_policy("minor_test", drift1, schema1)
        assert v1 == "v1.1.0"

        # Second addition
        drift2 = SchemaDrift(added_columns=["col_c"])
        schema2 = {"col_a": "int64", "col_b": "str", "col_c": "float64"}
        v2, _ = registry.apply_drift_policy("minor_test", drift2, schema2)
        assert v2 == "v1.2.0"

    def test_drift_policy_rejects_breaking_changes(self, registry: SchemaRegistry) -> None:
        """Test apply_drift_policy raises SchemaError for breaking drift."""
        registry.register_schema(
            "breaking_test",
            {
                "col_a": "int64",
                "col_b": "str",
            },
        )

        drift = SchemaDrift(removed_columns=["col_b"])
        new_schema = {"col_a": "int64"}

        with pytest.raises(SchemaError) as exc_info:
            registry.apply_drift_policy("breaking_test", drift, new_schema)

        assert exc_info.value.drift == drift

    def test_registry_atomic_write(
        self, registry: SchemaRegistry, temp_dirs: dict[str, Path]
    ) -> None:
        """Test registry uses atomic write for schema files."""
        # Register schema
        registry.register_schema("atomic_test", {"col_a": "int64"})

        # Verify file exists and is valid JSON
        schema_path = temp_dirs["schemas"] / "atomic_test.json"
        assert schema_path.exists()

        with open(schema_path) as f:
            data = json.load(f)

        assert data["dataset"] == "atomic_test"

    def test_version_increment_persisted(self, registry: SchemaRegistry) -> None:
        """Test version increments are persisted after additive drift."""
        registry.register_schema("increment_test", {"col_a": "int64"})

        # Add column
        drift = SchemaDrift(added_columns=["col_b"])
        new_schema = {"col_a": "int64", "col_b": "str"}
        registry.apply_drift_policy("increment_test", drift, new_schema)

        # Reload and verify
        loaded = registry.get_expected_schema("increment_test")
        assert loaded is not None
        assert loaded.version == "v1.1.0"

    def test_concurrent_writer_with_locking(self, registry: SchemaRegistry) -> None:
        """Test schema registry handles concurrent writes."""
        # Register initial schema
        registry.register_schema("concurrent_test", {"col_a": "int64"})

        # Simulate concurrent updates
        # First writer
        drift1 = SchemaDrift(added_columns=["col_b"])
        schema1 = {"col_a": "int64", "col_b": "str"}
        v1, _ = registry.apply_drift_policy("concurrent_test", drift1, schema1)

        # Second writer (after first completes)
        drift2 = SchemaDrift(added_columns=["col_c"])
        schema2 = {"col_a": "int64", "col_b": "str", "col_c": "float64"}
        v2, _ = registry.apply_drift_policy("concurrent_test", drift2, schema2)

        # Both should succeed with proper versioning
        assert v1 == "v1.1.0"
        assert v2 == "v1.2.0"

    def test_register_schema_first_version(self, registry: SchemaRegistry) -> None:
        """Test registering first schema gets v1.0.0."""
        version = registry.register_schema("first_test", {"col_a": "int64"})

        assert version == "v1.0.0"

    def test_register_schema_subsequent_versions(self, registry: SchemaRegistry) -> None:
        """Test subsequent registrations increment minor version."""
        v1 = registry.register_schema("multi_test", {"col_a": "int64"})
        v2 = registry.register_schema(
            "multi_test",
            {
                "col_a": "int64",
                "col_b": "str",
            },
        )
        v3 = registry.register_schema(
            "multi_test",
            {
                "col_a": "int64",
                "col_b": "str",
                "col_c": "float64",
            },
        )

        assert v1 == "v1.0.0"
        assert v2 == "v1.1.0"
        assert v3 == "v1.2.0"

    def test_drift_policy_no_drift_returns_current_version(self, registry: SchemaRegistry) -> None:
        """Test apply_drift_policy returns current version when no drift."""
        registry.register_schema("no_change", {"col_a": "int64"})

        drift = SchemaDrift()  # No changes
        schema = {"col_a": "int64"}

        version, message = registry.apply_drift_policy("no_change", drift, schema)

        assert version == "v1.0.0"
        assert "no" in message.lower()
        assert "change" in message.lower()

    def test_drift_policy_first_sync_registers_schema(self, registry: SchemaRegistry) -> None:
        """Test apply_drift_policy registers schema on first sync."""
        # No existing schema
        drift = SchemaDrift()  # Empty drift for new dataset
        schema = {"col_a": "int64", "col_b": "str"}

        version, message = registry.apply_drift_policy("new_dataset", drift, schema)

        assert version == "v1.0.0"
        assert "initial" in message.lower() or "register" in message.lower()

    def test_register_schema_creates_and_releases_lock(self, registry: SchemaRegistry) -> None:
        """Test register_schema acquires and releases lock correctly."""
        schema = {"col_a": "int64"}
        lock_path = registry._lock_path("lock_test")

        # Verify no lock before
        assert not lock_path.exists()

        # Register schema
        version = registry.register_schema("lock_test", schema)

        # Verify lock is released after
        assert not lock_path.exists()
        assert version == "v1.0.0"

    def test_concurrent_register_blocked_by_lock(self, registry: SchemaRegistry) -> None:
        """Test second register_schema is blocked when lock is held."""
        import os
        import threading

        from libs.data.data_quality.exceptions import LockNotHeldError

        lock_path = registry._lock_path("concurrent_test")

        # Create a lock file manually to simulate another process
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_data = {
            "pid": os.getpid() + 1,  # Different PID
            "hostname": "other-host",
            "acquired_at": datetime.now(UTC).isoformat(),
        }
        with open(lock_path, "w") as f:
            json.dump(lock_data, f)

        # Try to acquire lock with a very short timeout
        # This should fail because lock is held by another process
        short_timeout = 0.2  # Short timeout for test

        results: list[Exception | str] = []

        def try_acquire_lock() -> None:
            try:
                with registry._acquire_lock("concurrent_test", timeout_seconds=short_timeout):
                    results.append("acquired")
            except LockNotHeldError as e:
                results.append(e)

        thread = threading.Thread(target=try_acquire_lock)
        thread.start()
        thread.join(timeout=1.0)

        # Clean up lock
        lock_path.unlink(missing_ok=True)

        # Should have failed to acquire lock
        assert len(results) == 1
        assert isinstance(results[0], LockNotHeldError)

    def test_stale_lock_is_cleaned_up(self, registry: SchemaRegistry) -> None:
        """Test stale lock (>5 min old, local host, dead PID) is automatically cleaned up."""
        import os
        import socket
        import time

        schema = {"col_a": "int64"}
        lock_path = registry._lock_path("stale_test")

        # Create a stale lock file with current hostname (local lock)
        # and a PID that definitely doesn't exist (99999 is unlikely to be in use)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_data = {
            "pid": 99999,  # Non-existent PID
            "hostname": socket.gethostname(),  # Current host so PID check applies
            "acquired_at": datetime.now(UTC).isoformat(),
        }
        with open(lock_path, "w") as f:
            json.dump(lock_data, f)

        # Make the lock file appear stale by setting mtime to 6 minutes ago
        stale_time = time.time() - (6 * 60)  # 6 minutes ago
        os.utime(lock_path, (stale_time, stale_time))

        # Register should succeed by cleaning up stale lock
        # (lock is >5 min old AND owner PID is not alive on local host)
        version = registry.register_schema("stale_test", schema)

        assert version == "v1.0.0"
        # Lock should be released
        assert not lock_path.exists()

    def test_remote_stale_lock_is_not_deleted(self, registry: SchemaRegistry) -> None:
        """Test remote stale lock (different hostname) is NOT cleaned up.

        When a lock is held by a different host, we cannot verify if the
        owning process is alive, so we must respect the lock and wait.
        """
        import os
        import time

        lock_path = registry._lock_path("remote_stale_test")

        # Create a stale lock file from a different host
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_data = {
            "pid": 12345,
            "hostname": "different-remote-host",  # Different from current host
            "acquired_at": datetime.now(UTC).isoformat(),
        }
        with open(lock_path, "w") as f:
            json.dump(lock_data, f)

        # Make the lock file appear stale
        stale_time = time.time() - (6 * 60)  # 6 minutes ago
        os.utime(lock_path, (stale_time, stale_time))

        # Register should fail because remote lock cannot be verified
        # (Use a short timeout for the test)
        with pytest.raises(LockNotHeldError) as exc_info:
            # Manually call _acquire_lock with short timeout
            with registry._acquire_lock("remote_stale_test", timeout_seconds=0.5):
                pass

        assert "Failed to acquire schema lock" in str(exc_info.value)
        # Lock file should still exist (not deleted)
        assert lock_path.exists()

        # Clean up
        lock_path.unlink()
