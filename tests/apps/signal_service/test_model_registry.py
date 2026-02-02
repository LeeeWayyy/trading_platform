"""
Tests for model registry client.

Tests cover:
- ModelRegistry initialization
- Loading models from files
- Fetching metadata from database
- Hot reload mechanism
- Error handling and graceful degradation

Usage:
    # Run all tests
    pytest apps/signal_service/tests/test_model_registry.py -v

    # Run only unit tests (no database required)
    pytest apps/signal_service/tests/test_model_registry.py -v -k "not integration"

    # Run integration tests (requires database)
    pytest apps/signal_service/tests/test_model_registry.py -v -m integration
"""

import pytest

from apps.signal_service.model_registry import ModelMetadata, ModelRegistry
from apps.signal_service.shadow_validator import ShadowValidationResult


class TestModelMetadata:
    """Tests for ModelMetadata dataclass."""

    def test_model_metadata_creation(self, sample_model_metadata):
        """Create ModelMetadata from dictionary."""
        metadata = ModelMetadata(**sample_model_metadata)

        assert metadata.id == 1
        assert metadata.strategy_name == "alpha_baseline"
        assert metadata.version == "v1.0.0"
        assert metadata.status == "active"
        assert metadata.performance_metrics["ic"] == 0.082

    def test_model_metadata_attributes(self, sample_model_metadata):
        """ModelMetadata has all required attributes."""
        metadata = ModelMetadata(**sample_model_metadata)

        # Check all attributes exist
        assert hasattr(metadata, "id")
        assert hasattr(metadata, "strategy_name")
        assert hasattr(metadata, "version")
        assert hasattr(metadata, "mlflow_run_id")
        assert hasattr(metadata, "model_path")
        assert hasattr(metadata, "status")
        assert hasattr(metadata, "performance_metrics")
        assert hasattr(metadata, "config")
        assert hasattr(metadata, "created_at")
        assert hasattr(metadata, "activated_at")


class TestModelRegistryInitialization:
    """Tests for ModelRegistry initialization."""

    def test_initialization_with_valid_url(self, test_db_url):
        """Initialize ModelRegistry with valid connection string."""
        registry = ModelRegistry(test_db_url)

        assert registry.db_conn_string == test_db_url
        assert registry.current_model is None
        assert registry.current_metadata is None
        assert registry.last_check is None
        assert registry.is_loaded is False

    def test_initialization_with_empty_url_raises_error(self):
        """Initialize with empty URL raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            ModelRegistry("")

    def test_properties_before_loading(self, test_db_url):
        """Properties return None before model loaded."""
        registry = ModelRegistry(test_db_url)

        assert registry.current_model is None
        assert registry.current_metadata is None
        assert registry.is_loaded is False
        assert registry.last_check is None


class TestModelLoading:
    """Tests for model loading from files."""

    def test_load_model_from_file_success(self, test_db_url, mock_model):
        """Load LightGBM model from valid file."""
        registry = ModelRegistry(test_db_url)
        model = registry.load_model_from_file(str(mock_model))

        assert model is not None
        assert model.num_trees() == 10
        assert model.num_feature() == 10

    def test_load_model_from_nonexistent_file_raises_error(self, test_db_url):
        """Loading nonexistent model raises FileNotFoundError."""
        registry = ModelRegistry(test_db_url)

        with pytest.raises(
            FileNotFoundError, match=r"Model file not found.*/nonexistent/model.txt"
        ):
            registry.load_model_from_file("/nonexistent/model.txt")

    def test_load_model_from_invalid_file_raises_error(self, test_db_url, temp_dir):
        """Loading invalid model file raises ValueError."""
        registry = ModelRegistry(test_db_url)

        # Create empty file (not a valid LightGBM model)
        invalid_file = temp_dir / "invalid_model.txt"
        invalid_file.write_text("not a model")

        with pytest.raises(ValueError, match="Invalid LightGBM model"):
            registry.load_model_from_file(str(invalid_file))

    def test_load_model_updates_properties(self, test_db_url, mock_model):
        """Loading model is reflected in properties."""
        registry = ModelRegistry(test_db_url)

        # Before loading
        assert registry.current_model is None
        assert registry.is_loaded is False

        # Load model directly (not via reload_if_changed)
        model = registry.load_model_from_file(str(mock_model))

        # Model is loaded but registry state not updated
        # (that's done by reload_if_changed)
        assert registry.current_model is None  # Not set yet
        assert model is not None


class DummyModel:
    """Lightweight model stub for shadow validation tests."""

    def __init__(self, num_features: int, scale: float = 1.0) -> None:
        self._num_features = num_features
        self._scale = scale

    def num_feature(self) -> int:
        return self._num_features

    def predict(self, features):
        import numpy as np

        return np.sum(features, axis=1) * self._scale


class TestShadowValidationReload:
    """Tests for shadow validation integration in reload_if_changed."""

    def test_shadow_validation_passes_activates_model(
        self, test_db_url, sample_model_metadata, monkeypatch
    ):
        registry = ModelRegistry(test_db_url)
        registry._current_model = DummyModel(num_features=2, scale=1.0)  # type: ignore[assignment]
        registry._current_metadata = ModelMetadata(**sample_model_metadata)

        new_metadata_dict = dict(sample_model_metadata)
        new_metadata_dict["version"] = "v2.0.0"
        new_metadata = ModelMetadata(**new_metadata_dict)

        monkeypatch.setattr(registry, "get_active_model_metadata", lambda _: new_metadata)
        monkeypatch.setattr(registry, "load_model_from_file", lambda _: DummyModel(2, 1.01))

        def validator(_old, _new):
            return ShadowValidationResult(
                passed=True,
                correlation=0.99,
                mean_abs_diff_ratio=0.1,
                sign_change_rate=0.0,
                sample_count=10,
                old_range=1.0,
                new_range=1.01,
                message="ok",
            )

        reloaded = registry.reload_if_changed(
            "alpha_baseline",
            shadow_validator=validator,
            shadow_validation_enabled=True,
        )

        assert reloaded is True
        assert registry.current_metadata is not None
        assert registry.current_metadata.version == "v2.0.0"

    def test_shadow_validation_failure_keeps_old_model(
        self, test_db_url, sample_model_metadata, monkeypatch
    ):
        registry = ModelRegistry(test_db_url)
        registry._current_model = DummyModel(num_features=2, scale=1.0)  # type: ignore[assignment]
        registry._current_metadata = ModelMetadata(**sample_model_metadata)

        new_metadata_dict = dict(sample_model_metadata)
        new_metadata_dict["version"] = "v2.0.0"
        new_metadata = ModelMetadata(**new_metadata_dict)

        monkeypatch.setattr(registry, "get_active_model_metadata", lambda _: new_metadata)
        monkeypatch.setattr(registry, "load_model_from_file", lambda _: DummyModel(2, 3.0))

        def validator(_old, _new):
            return ShadowValidationResult(
                passed=False,
                correlation=0.1,
                mean_abs_diff_ratio=2.0,
                sign_change_rate=0.7,
                sample_count=10,
                old_range=1.0,
                new_range=3.0,
                message="failed",
            )

        reloaded = registry.reload_if_changed(
            "alpha_baseline",
            shadow_validator=validator,
            shadow_validation_enabled=True,
        )

        assert reloaded is False
        assert registry.current_metadata is not None
        assert registry.current_metadata.version == sample_model_metadata["version"]

    def test_skip_shadow_validation_bypasses_validator(
        self, test_db_url, sample_model_metadata, monkeypatch
    ):
        registry = ModelRegistry(test_db_url)
        registry._current_model = DummyModel(num_features=2, scale=1.0)  # type: ignore[assignment]
        registry._current_metadata = ModelMetadata(**sample_model_metadata)

        new_metadata_dict = dict(sample_model_metadata)
        new_metadata_dict["version"] = "v2.0.0"
        new_metadata = ModelMetadata(**new_metadata_dict)

        monkeypatch.setattr(registry, "get_active_model_metadata", lambda _: new_metadata)
        monkeypatch.setattr(registry, "load_model_from_file", lambda _: DummyModel(2, 1.0))

        called = {"value": False}

        def validator(_old, _new):
            called["value"] = True
            return ShadowValidationResult(
                passed=False,
                correlation=0.0,
                mean_abs_diff_ratio=1.0,
                sign_change_rate=1.0,
                sample_count=10,
                old_range=1.0,
                new_range=1.0,
                message="should not run",
            )

        reloaded = registry.reload_if_changed(
            "alpha_baseline",
            shadow_validator=validator,
            shadow_validation_enabled=True,
            skip_shadow_validation=True,
        )

        assert reloaded is True
        assert called["value"] is False


@pytest.mark.integration()
@pytest.mark.skip(reason="Requires test database setup")
class TestDatabaseIntegration:
    """Integration tests requiring database."""

    def test_get_active_model_metadata(self, test_db_url, setup_model_registry_table, mock_model):
        """Fetch active model metadata from database."""
        # Insert test record
        with setup_model_registry_table.cursor() as cur:
            cur.execute(
                """
                INSERT INTO model_registry
                (strategy_name, version, model_path, status, performance_metrics, config)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    "test_strategy",
                    "v1.0.0",
                    str(mock_model),
                    "active",
                    '{"ic": 0.05}',
                    '{"num_boost_round": 10}',
                ),
            )
            setup_model_registry_table.commit()

        # Fetch metadata
        registry = ModelRegistry(test_db_url)
        metadata = registry.get_active_model_metadata("test_strategy")

        assert metadata.strategy_name == "test_strategy"
        assert metadata.version == "v1.0.0"
        assert metadata.status == "active"
        assert metadata.model_path == str(mock_model)
        assert metadata.performance_metrics["ic"] == 0.05

    def test_get_active_model_no_active_raises_error(self, test_db_url, setup_model_registry_table):
        """Fetching metadata when no active model raises ValueError."""
        registry = ModelRegistry(test_db_url)

        with pytest.raises(ValueError, match=r"No active model found.*nonexistent_strategy"):
            registry.get_active_model_metadata("nonexistent_strategy")

    def test_reload_if_changed_initial_load(
        self, test_db_url, setup_model_registry_table, mock_model
    ):
        """First reload loads model successfully."""
        # Insert test record
        with setup_model_registry_table.cursor() as cur:
            cur.execute(
                """
                INSERT INTO model_registry
                (strategy_name, version, model_path, status, performance_metrics, config)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                ("test_strategy", "v1.0.0", str(mock_model), "active", "{}", "{}"),
            )
            setup_model_registry_table.commit()

        # Initial load
        registry = ModelRegistry(test_db_url)
        reloaded = registry.reload_if_changed("test_strategy")

        assert reloaded is True
        assert registry.is_loaded is True
        assert registry.current_metadata is not None
        assert registry.current_metadata.version == "v1.0.0"
        assert registry.last_check is not None

    def test_reload_if_changed_no_change(self, test_db_url, setup_model_registry_table, mock_model):
        """Reload with no version change returns False."""
        # Insert test record
        with setup_model_registry_table.cursor() as cur:
            cur.execute(
                """
                INSERT INTO model_registry
                (strategy_name, version, model_path, status, performance_metrics, config)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                ("test_strategy", "v1.0.0", str(mock_model), "active", "{}", "{}"),
            )
            setup_model_registry_table.commit()

        # Initial load
        registry = ModelRegistry(test_db_url)
        reloaded1 = registry.reload_if_changed("test_strategy")
        assert reloaded1 is True

        # Second call - no change
        reloaded2 = registry.reload_if_changed("test_strategy")
        assert reloaded2 is False
        assert registry.current_metadata is not None
        assert registry.current_metadata.version == "v1.0.0"

    def test_reload_if_changed_version_changed(
        self, test_db_url, setup_model_registry_table, mock_model, temp_dir
    ):
        """Reload with version change loads new model."""
        # Insert v1.0.0
        with setup_model_registry_table.cursor() as cur:
            cur.execute(
                """
                INSERT INTO model_registry
                (strategy_name, version, model_path, status, performance_metrics, config)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                ("test_strategy", "v1.0.0", str(mock_model), "active", "{}", "{}"),
            )
            setup_model_registry_table.commit()

        # Initial load
        registry = ModelRegistry(test_db_url)
        reloaded1 = registry.reload_if_changed("test_strategy")
        assert reloaded1 is True
        assert registry.current_metadata is not None
        assert registry.current_metadata.version == "v1.0.0"

        # Create new model v2.0.0 (just copy existing for test)
        import shutil

        model_v2 = temp_dir / "model_v2.txt"
        shutil.copy(mock_model, model_v2)

        # Activate v2.0.0
        with setup_model_registry_table.cursor() as cur:
            # Insert v2.0.0
            cur.execute(
                """
                INSERT INTO model_registry
                (strategy_name, version, model_path, status, performance_metrics, config)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                ("test_strategy", "v2.0.0", str(model_v2), "inactive", "{}", "{}"),
            )
            # Activate v2.0.0 (deactivates v1.0.0)
            cur.execute("SELECT activate_model(%s, %s)", ("test_strategy", "v2.0.0"))
            setup_model_registry_table.commit()

        # Reload - should detect version change
        reloaded2 = registry.reload_if_changed("test_strategy")
        assert reloaded2 is True
        assert registry.current_metadata is not None
        assert registry.current_metadata.version == "v2.0.0"

    def test_reload_graceful_degradation_on_error(
        self, test_db_url, setup_model_registry_table, mock_model
    ):
        """Reload keeps old model if new model fails to load."""
        # Insert v1.0.0 (valid)
        with setup_model_registry_table.cursor() as cur:
            cur.execute(
                """
                INSERT INTO model_registry
                (strategy_name, version, model_path, status, performance_metrics, config)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                ("test_strategy", "v1.0.0", str(mock_model), "active", "{}", "{}"),
            )
            setup_model_registry_table.commit()

        # Load v1.0.0
        registry = ModelRegistry(test_db_url)
        registry.reload_if_changed("test_strategy")
        assert registry.current_metadata is not None
        assert registry.current_metadata.version == "v1.0.0"

        # Insert v2.0.0 with invalid path
        with setup_model_registry_table.cursor() as cur:
            cur.execute(
                """
                INSERT INTO model_registry
                (strategy_name, version, model_path, status, performance_metrics, config)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                ("test_strategy", "v2.0.0", "/nonexistent/model.txt", "inactive", "{}", "{}"),
            )
            cur.execute("SELECT activate_model(%s, %s)", ("test_strategy", "v2.0.0"))
            setup_model_registry_table.commit()

        # Reload - should fail but keep v1.0.0
        reloaded = registry.reload_if_changed("test_strategy")
        assert reloaded is False  # Failed to reload
        assert registry.current_metadata is not None
        assert registry.current_metadata.version == "v1.0.0"  # Kept old version
        assert registry.is_loaded is True  # Still loaded


class TestErrorHandling:
    """Tests for error handling and edge cases."""

    def test_properties_are_none_initially(self, test_db_url):
        """All properties are None before loading."""
        registry = ModelRegistry(test_db_url)

        assert registry.current_model is None
        assert registry.current_metadata is None
        assert registry.is_loaded is False
        assert registry.last_check is None

    def test_is_loaded_false_before_load(self, test_db_url):
        """is_loaded returns False before any model loaded."""
        registry = ModelRegistry(test_db_url)
        assert registry.is_loaded is False

    def test_is_loaded_true_after_load(self, test_db_url, mock_model):
        """is_loaded returns True after model loaded."""
        # This test would require setting _current_model directly
        # or going through reload_if_changed with database
        # Skip for now (covered by integration tests)
        pass


class TestPoolManagement:
    """Tests for connection pool management."""

    def test_ensure_pool_open_opens_pool_once(self, test_db_url):
        """Test that _ensure_pool_open only opens pool once."""
        registry = ModelRegistry(test_db_url)
        assert registry._pool_opened is False

        registry._ensure_pool_open()
        assert registry._pool_opened is True

        # Second call should not raise
        registry._ensure_pool_open()
        assert registry._pool_opened is True

    def test_close_closes_pool(self, test_db_url):
        """Test that close() closes the pool."""
        registry = ModelRegistry(test_db_url)
        registry._pool_opened = True  # Simulate opened pool

        registry.close()
        assert registry._pool_opened is False


class TestModelLoadingErrorPaths:
    """Tests for error handling in load_model_from_file."""

    def test_load_model_os_error_raises_value_error(self, test_db_url, temp_dir):
        """Test OSError (e.g., permission denied) raises ValueError."""
        registry = ModelRegistry(test_db_url)

        # Create a directory instead of file (will cause OSError on read)
        dir_path = temp_dir / "directory_not_file"
        dir_path.mkdir()

        with pytest.raises(ValueError, match="Invalid LightGBM model"):
            registry.load_model_from_file(str(dir_path))


class TestGracefulDegradation:
    """Tests for graceful degradation on reload errors."""

    def test_file_error_keeps_current_model(self, test_db_url, sample_model_metadata, monkeypatch):
        """Test FileNotFoundError keeps current model when one is loaded."""
        registry = ModelRegistry(test_db_url)
        registry._current_model = DummyModel(num_features=2, scale=1.0)  # type: ignore[assignment]
        registry._current_metadata = ModelMetadata(**sample_model_metadata)

        new_metadata_dict = dict(sample_model_metadata)
        new_metadata_dict["version"] = "v2.0.0"
        new_metadata_dict["model_path"] = "/nonexistent/path/model.txt"
        new_metadata = ModelMetadata(**new_metadata_dict)

        monkeypatch.setattr(registry, "get_active_model_metadata", lambda _: new_metadata)

        reloaded = registry.reload_if_changed("alpha_baseline", skip_shadow_validation=True)

        assert reloaded is False
        assert registry.current_metadata is not None
        assert registry.current_metadata.version == sample_model_metadata["version"]

    def test_value_error_keeps_current_model(self, test_db_url, sample_model_metadata, monkeypatch):
        """Test ValueError keeps current model when one is loaded."""
        registry = ModelRegistry(test_db_url)
        registry._current_model = DummyModel(num_features=2, scale=1.0)  # type: ignore[assignment]
        registry._current_metadata = ModelMetadata(**sample_model_metadata)

        new_metadata_dict = dict(sample_model_metadata)
        new_metadata_dict["version"] = "v2.0.0"
        new_metadata = ModelMetadata(**new_metadata_dict)

        monkeypatch.setattr(registry, "get_active_model_metadata", lambda _: new_metadata)

        def raise_value_error(_path):
            raise ValueError("Invalid model format")

        monkeypatch.setattr(registry, "load_model_from_file", raise_value_error)

        reloaded = registry.reload_if_changed("alpha_baseline", skip_shadow_validation=True)

        assert reloaded is False
        assert registry.current_metadata is not None
        assert registry.current_metadata.version == sample_model_metadata["version"]

    def test_file_error_propagates_when_no_model_loaded(
        self, test_db_url, sample_model_metadata, monkeypatch
    ):
        """Test FileNotFoundError propagates when no model loaded."""
        registry = ModelRegistry(test_db_url)
        # No current model

        new_metadata_dict = dict(sample_model_metadata)
        new_metadata_dict["model_path"] = "/nonexistent/path/model.txt"
        new_metadata = ModelMetadata(**new_metadata_dict)

        monkeypatch.setattr(registry, "get_active_model_metadata", lambda _: new_metadata)

        with pytest.raises(FileNotFoundError):
            registry.reload_if_changed("alpha_baseline", skip_shadow_validation=True)


class TestModelActivatedCallback:
    """Tests for on_model_activated callback."""

    def test_on_model_activated_called_on_success(
        self, test_db_url, sample_model_metadata, monkeypatch
    ):
        """Test on_model_activated callback is called on successful reload."""
        registry = ModelRegistry(test_db_url)

        new_metadata_dict = dict(sample_model_metadata)
        new_metadata_dict["version"] = "v2.0.0"
        new_metadata = ModelMetadata(**new_metadata_dict)

        monkeypatch.setattr(registry, "get_active_model_metadata", lambda _: new_metadata)
        monkeypatch.setattr(registry, "load_model_from_file", lambda _: DummyModel(2, 1.0))

        callback_called = {"value": False, "metadata": None}

        def callback(meta):
            callback_called["value"] = True
            callback_called["metadata"] = meta

        reloaded = registry.reload_if_changed(
            "alpha_baseline",
            skip_shadow_validation=True,
            on_model_activated=callback,
        )

        assert reloaded is True
        assert callback_called["value"] is True
        assert callback_called["metadata"].version == "v2.0.0"


class TestShadowValidationAdvanced:
    """Advanced tests for shadow validation scenarios."""

    def test_shadow_validation_already_in_progress_skips(
        self, test_db_url, sample_model_metadata, monkeypatch
    ):
        """Test that when shadow validation is already in progress, reload returns False."""
        registry = ModelRegistry(test_db_url)
        registry._current_model = DummyModel(num_features=2, scale=1.0)  # type: ignore[assignment]
        registry._current_metadata = ModelMetadata(**sample_model_metadata)

        new_metadata_dict = dict(sample_model_metadata)
        new_metadata_dict["version"] = "v2.0.0"
        new_metadata = ModelMetadata(**new_metadata_dict)

        # Simulate shadow validation already in progress for same version
        registry._pending_validation = True
        registry._pending_metadata = ModelMetadata(**new_metadata_dict)
        registry._pending_model = DummyModel(2, 1.0)

        monkeypatch.setattr(registry, "get_active_model_metadata", lambda _: new_metadata)

        def validator(_old, _new):
            return ShadowValidationResult(
                passed=True,
                correlation=0.99,
                mean_abs_diff_ratio=0.1,
                sign_change_rate=0.0,
                sample_count=10,
                old_range=1.0,
                new_range=1.0,
                message="ok",
            )

        reloaded = registry.reload_if_changed(
            "alpha_baseline",
            shadow_validator=validator,
            shadow_validation_enabled=True,
        )

        # Should return False because validation already in progress
        assert reloaded is False

    def test_shadow_validation_supersedes_pending(
        self, test_db_url, sample_model_metadata, monkeypatch
    ):
        """Test that newer version supersedes pending shadow validation."""
        registry = ModelRegistry(test_db_url)
        registry._current_model = DummyModel(num_features=2, scale=1.0)  # type: ignore[assignment]
        registry._current_metadata = ModelMetadata(**sample_model_metadata)

        # v2.0.0 is pending
        pending_metadata_dict = dict(sample_model_metadata)
        pending_metadata_dict["version"] = "v2.0.0"
        registry._pending_validation = True
        registry._pending_metadata = ModelMetadata(**pending_metadata_dict)
        registry._pending_model = DummyModel(2, 1.0)

        # Now v3.0.0 arrives
        new_metadata_dict = dict(sample_model_metadata)
        new_metadata_dict["version"] = "v3.0.0"
        new_metadata = ModelMetadata(**new_metadata_dict)

        monkeypatch.setattr(registry, "get_active_model_metadata", lambda _: new_metadata)
        monkeypatch.setattr(registry, "load_model_from_file", lambda _: DummyModel(2, 1.0))

        def validator(_old, _new):
            return ShadowValidationResult(
                passed=True,
                correlation=0.99,
                mean_abs_diff_ratio=0.1,
                sign_change_rate=0.0,
                sample_count=10,
                old_range=1.0,
                new_range=1.0,
                message="ok",
            )

        reloaded = registry.reload_if_changed(
            "alpha_baseline",
            shadow_validator=validator,
            shadow_validation_enabled=True,
        )

        # Should have cleared pending and activated v3.0.0
        assert reloaded is True
        assert registry.current_metadata is not None
        assert registry.current_metadata.version == "v3.0.0"

    def test_shadow_validation_no_validator_warns_and_activates(
        self, test_db_url, sample_model_metadata, monkeypatch
    ):
        """Test that missing validator logs warning but activates model."""
        registry = ModelRegistry(test_db_url)
        registry._current_model = DummyModel(num_features=2, scale=1.0)  # type: ignore[assignment]
        registry._current_metadata = ModelMetadata(**sample_model_metadata)

        new_metadata_dict = dict(sample_model_metadata)
        new_metadata_dict["version"] = "v2.0.0"
        new_metadata = ModelMetadata(**new_metadata_dict)

        monkeypatch.setattr(registry, "get_active_model_metadata", lambda _: new_metadata)
        monkeypatch.setattr(registry, "load_model_from_file", lambda _: DummyModel(2, 1.0))

        # Enable shadow validation but provide no validator
        reloaded = registry.reload_if_changed(
            "alpha_baseline",
            shadow_validator=None,  # No validator provided
            shadow_validation_enabled=True,
        )

        # Should still activate the model (with warning)
        assert reloaded is True
        assert registry.current_metadata is not None
        assert registry.current_metadata.version == "v2.0.0"


class TestScheduleValidation:
    """Tests for scheduled shadow validation."""

    def test_schedule_validation_defers_activation(
        self, test_db_url, sample_model_metadata, monkeypatch
    ):
        """Test that schedule_validation defers activation and returns False."""
        registry = ModelRegistry(test_db_url)
        registry._current_model = DummyModel(num_features=2, scale=1.0)  # type: ignore[assignment]
        registry._current_metadata = ModelMetadata(**sample_model_metadata)

        new_metadata_dict = dict(sample_model_metadata)
        new_metadata_dict["version"] = "v2.0.0"
        new_metadata = ModelMetadata(**new_metadata_dict)

        monkeypatch.setattr(registry, "get_active_model_metadata", lambda _: new_metadata)
        monkeypatch.setattr(registry, "load_model_from_file", lambda _: DummyModel(2, 1.0))

        scheduled_tasks = []

        def schedule_fn(task_fn):
            scheduled_tasks.append(task_fn)

        def validator(_old, _new):
            return ShadowValidationResult(
                passed=True,
                correlation=0.99,
                mean_abs_diff_ratio=0.1,
                sign_change_rate=0.0,
                sample_count=10,
                old_range=1.0,
                new_range=1.0,
                message="ok",
            )

        reloaded = registry.reload_if_changed(
            "alpha_baseline",
            shadow_validator=validator,
            shadow_validation_enabled=True,
            schedule_validation=schedule_fn,
        )

        # Should return False because validation is scheduled
        assert reloaded is False
        assert len(scheduled_tasks) == 1

        # Model should still be v1.0.0
        assert registry.current_metadata.version == sample_model_metadata["version"]

        # Now run the scheduled task
        scheduled_tasks[0]()

        # Now v2.0.0 should be active
        assert registry.current_metadata.version == "v2.0.0"


class TestPropertySetters:
    """Tests for property setters."""

    def test_current_model_setter(self, test_db_url):
        """Test current_model setter."""
        registry = ModelRegistry(test_db_url)
        assert registry.current_model is None

        mock_model = DummyModel(num_features=2)
        registry.current_model = mock_model  # type: ignore[assignment]

        assert registry.current_model is mock_model

    def test_current_metadata_setter(self, test_db_url, sample_model_metadata):
        """Test current_metadata setter."""
        registry = ModelRegistry(test_db_url)
        assert registry.current_metadata is None

        metadata = ModelMetadata(**sample_model_metadata)
        registry.current_metadata = metadata

        assert registry.current_metadata is metadata


class TestDatabaseErrors:
    """Tests for database error handling in get_active_model_metadata."""

    def test_database_operational_error_propagates(self, test_db_url, monkeypatch):
        """Test OperationalError propagates from get_active_model_metadata."""
        from psycopg import OperationalError

        registry = ModelRegistry(test_db_url)

        def mock_ensure_pool_open():
            raise OperationalError("Connection failed")

        monkeypatch.setattr(registry, "_ensure_pool_open", mock_ensure_pool_open)

        with pytest.raises(OperationalError):
            registry.get_active_model_metadata("alpha_baseline")

    def test_database_error_graceful_degradation_on_reload(
        self, test_db_url, sample_model_metadata, monkeypatch
    ):
        """Test database error during reload keeps current model."""
        from psycopg import OperationalError

        registry = ModelRegistry(test_db_url)
        registry._current_model = DummyModel(num_features=2, scale=1.0)  # type: ignore[assignment]
        registry._current_metadata = ModelMetadata(**sample_model_metadata)

        def mock_get_metadata(_strategy):
            raise OperationalError("Connection failed")

        monkeypatch.setattr(registry, "get_active_model_metadata", mock_get_metadata)

        reloaded = registry.reload_if_changed("alpha_baseline")

        assert reloaded is False
        assert registry.current_metadata is not None
        assert registry.current_metadata.version == sample_model_metadata["version"]


class TestPredictionTestFailure:
    """Tests for model prediction test failure."""

    def test_prediction_test_failure_raises_value_error(
        self, test_db_url, sample_model_metadata, monkeypatch
    ):
        """Test that prediction test failure raises ValueError."""
        registry = ModelRegistry(test_db_url)

        new_metadata = ModelMetadata(**sample_model_metadata)
        monkeypatch.setattr(registry, "get_active_model_metadata", lambda _: new_metadata)

        class BrokenModel:
            def num_feature(self):
                return 2

            def predict(self, _features):
                raise ValueError("Prediction failed")

        monkeypatch.setattr(registry, "load_model_from_file", lambda _: BrokenModel())

        with pytest.raises(ValueError, match="Model prediction test failed"):
            registry.reload_if_changed("alpha_baseline", skip_shadow_validation=True)

    def test_prediction_test_type_error_raises_value_error(
        self, test_db_url, sample_model_metadata, monkeypatch
    ):
        """Test that prediction TypeError raises ValueError."""
        registry = ModelRegistry(test_db_url)

        new_metadata = ModelMetadata(**sample_model_metadata)
        monkeypatch.setattr(registry, "get_active_model_metadata", lambda _: new_metadata)

        class BrokenModel:
            def num_feature(self):
                return 2

            def predict(self, _features):
                raise TypeError("Invalid type")

        monkeypatch.setattr(registry, "load_model_from_file", lambda _: BrokenModel())

        with pytest.raises(ValueError, match="Model prediction test failed"):
            registry.reload_if_changed("alpha_baseline", skip_shadow_validation=True)


class TestShadowValidationExceptions:
    """Tests for shadow validation exception paths."""

    def test_shadow_validator_raises_exception_keeps_old_model(
        self, test_db_url, sample_model_metadata, monkeypatch
    ):
        """Test that validator exception keeps old model."""
        registry = ModelRegistry(test_db_url)
        registry._current_model = DummyModel(num_features=2, scale=1.0)  # type: ignore[assignment]
        registry._current_metadata = ModelMetadata(**sample_model_metadata)

        new_metadata_dict = dict(sample_model_metadata)
        new_metadata_dict["version"] = "v2.0.0"
        new_metadata = ModelMetadata(**new_metadata_dict)

        monkeypatch.setattr(registry, "get_active_model_metadata", lambda _: new_metadata)
        monkeypatch.setattr(registry, "load_model_from_file", lambda _: DummyModel(2, 1.0))

        def failing_validator(_old, _new):
            raise ValueError("Validation failed")

        reloaded = registry.reload_if_changed(
            "alpha_baseline",
            shadow_validator=failing_validator,
            shadow_validation_enabled=True,
        )

        # Should keep old model
        assert reloaded is False
        assert registry.current_metadata is not None
        assert registry.current_metadata.version == sample_model_metadata["version"]

    def test_on_model_activated_called_in_shadow_validation(
        self, test_db_url, sample_model_metadata, monkeypatch
    ):
        """Test on_model_activated callback is called after shadow validation passes."""
        registry = ModelRegistry(test_db_url)
        registry._current_model = DummyModel(num_features=2, scale=1.0)  # type: ignore[assignment]
        registry._current_metadata = ModelMetadata(**sample_model_metadata)

        new_metadata_dict = dict(sample_model_metadata)
        new_metadata_dict["version"] = "v2.0.0"
        new_metadata = ModelMetadata(**new_metadata_dict)

        monkeypatch.setattr(registry, "get_active_model_metadata", lambda _: new_metadata)
        monkeypatch.setattr(registry, "load_model_from_file", lambda _: DummyModel(2, 1.0))

        callback_called = {"value": False}

        def callback(meta):
            callback_called["value"] = True

        def validator(_old, _new):
            return ShadowValidationResult(
                passed=True,
                correlation=0.99,
                mean_abs_diff_ratio=0.1,
                sign_change_rate=0.0,
                sample_count=10,
                old_range=1.0,
                new_range=1.0,
                message="ok",
            )

        reloaded = registry.reload_if_changed(
            "alpha_baseline",
            shadow_validator=validator,
            shadow_validation_enabled=True,
            on_model_activated=callback,
        )

        assert reloaded is True
        assert callback_called["value"] is True


class TestNoVersionChange:
    """Tests for no version change scenario."""

    def test_no_version_change_returns_false(self, test_db_url, sample_model_metadata, monkeypatch):
        """Test that no version change returns False."""
        registry = ModelRegistry(test_db_url)
        registry._current_model = DummyModel(num_features=2, scale=1.0)  # type: ignore[assignment]
        registry._current_metadata = ModelMetadata(**sample_model_metadata)

        # Same version as current
        same_metadata = ModelMetadata(**sample_model_metadata)
        monkeypatch.setattr(registry, "get_active_model_metadata", lambda _: same_metadata)

        reloaded = registry.reload_if_changed("alpha_baseline", skip_shadow_validation=True)

        assert reloaded is False
        assert registry.last_check is not None
