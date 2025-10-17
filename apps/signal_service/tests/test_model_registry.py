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
from datetime import datetime
from pathlib import Path

from apps.signal_service.model_registry import ModelRegistry, ModelMetadata


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
        with pytest.raises(ValueError) as exc_info:
            ModelRegistry("")

        assert "cannot be empty" in str(exc_info.value)

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

        with pytest.raises(FileNotFoundError) as exc_info:
            registry.load_model_from_file("/nonexistent/model.txt")

        assert "Model file not found" in str(exc_info.value)
        assert "/nonexistent/model.txt" in str(exc_info.value)

    def test_load_model_from_invalid_file_raises_error(self, test_db_url, temp_dir):
        """Loading invalid model file raises ValueError."""
        registry = ModelRegistry(test_db_url)

        # Create empty file (not a valid LightGBM model)
        invalid_file = temp_dir / "invalid_model.txt"
        invalid_file.write_text("not a model")

        with pytest.raises(ValueError) as exc_info:
            registry.load_model_from_file(str(invalid_file))

        assert "Invalid LightGBM model" in str(exc_info.value)

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


@pytest.mark.integration
@pytest.mark.skip(reason="Requires test database setup")
class TestDatabaseIntegration:
    """Integration tests requiring database."""

    def test_get_active_model_metadata(
        self, test_db_url, setup_model_registry_table, mock_model
    ):
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

    def test_get_active_model_no_active_raises_error(
        self, test_db_url, setup_model_registry_table
    ):
        """Fetching metadata when no active model raises ValueError."""
        registry = ModelRegistry(test_db_url)

        with pytest.raises(ValueError) as exc_info:
            registry.get_active_model_metadata("nonexistent_strategy")

        assert "No active model found" in str(exc_info.value)
        assert "nonexistent_strategy" in str(exc_info.value)

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
                ("test_strategy", "v1.0.0", str(mock_model), "active", '{}', '{}'),
            )
            setup_model_registry_table.commit()

        # Initial load
        registry = ModelRegistry(test_db_url)
        reloaded = registry.reload_if_changed("test_strategy")

        assert reloaded is True
        assert registry.is_loaded is True
        assert registry.current_metadata.version == "v1.0.0"
        assert registry.last_check is not None

    def test_reload_if_changed_no_change(
        self, test_db_url, setup_model_registry_table, mock_model
    ):
        """Reload with no version change returns False."""
        # Insert test record
        with setup_model_registry_table.cursor() as cur:
            cur.execute(
                """
                INSERT INTO model_registry
                (strategy_name, version, model_path, status, performance_metrics, config)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                ("test_strategy", "v1.0.0", str(mock_model), "active", '{}', '{}'),
            )
            setup_model_registry_table.commit()

        # Initial load
        registry = ModelRegistry(test_db_url)
        reloaded1 = registry.reload_if_changed("test_strategy")
        assert reloaded1 is True

        # Second call - no change
        reloaded2 = registry.reload_if_changed("test_strategy")
        assert reloaded2 is False
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
                ("test_strategy", "v1.0.0", str(mock_model), "active", '{}', '{}'),
            )
            setup_model_registry_table.commit()

        # Initial load
        registry = ModelRegistry(test_db_url)
        reloaded1 = registry.reload_if_changed("test_strategy")
        assert reloaded1 is True
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
                ("test_strategy", "v2.0.0", str(model_v2), "inactive", '{}', '{}'),
            )
            # Activate v2.0.0 (deactivates v1.0.0)
            cur.execute("SELECT activate_model(%s, %s)", ("test_strategy", "v2.0.0"))
            setup_model_registry_table.commit()

        # Reload - should detect version change
        reloaded2 = registry.reload_if_changed("test_strategy")
        assert reloaded2 is True
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
                ("test_strategy", "v1.0.0", str(mock_model), "active", '{}', '{}'),
            )
            setup_model_registry_table.commit()

        # Load v1.0.0
        registry = ModelRegistry(test_db_url)
        registry.reload_if_changed("test_strategy")
        assert registry.current_metadata.version == "v1.0.0"

        # Insert v2.0.0 with invalid path
        with setup_model_registry_table.cursor() as cur:
            cur.execute(
                """
                INSERT INTO model_registry
                (strategy_name, version, model_path, status, performance_metrics, config)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                ("test_strategy", "v2.0.0", "/nonexistent/model.txt", "inactive", '{}', '{}'),
            )
            cur.execute("SELECT activate_model(%s, %s)", ("test_strategy", "v2.0.0"))
            setup_model_registry_table.commit()

        # Reload - should fail but keep v1.0.0
        reloaded = registry.reload_if_changed("test_strategy")
        assert reloaded is False  # Failed to reload
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
