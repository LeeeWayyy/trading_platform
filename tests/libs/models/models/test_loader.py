"""Comprehensive tests for ProductionModelLoader.

Tests cover:
- CachedModel TTL expiration
- ProductionModelLoader initialization with/without version checks
- Model getters (risk_model, alpha_weights, factor_definitions)
- Cache behavior (hits, misses, expiration, thundering herd)
- Circuit breaker (trip, reset, fallback)
- Compatibility checking
- Polling (start, stop, version updates)
- Load failures and fallback mechanisms
- Thread safety and coordination
- Error handling for all edge cases

Target: 85%+ branch coverage from 0% baseline.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest

from libs.models.models.compatibility import CompatibilityResult, VersionCompatibilityChecker
from libs.models.models.loader import (
    CachedModel,
    CircuitBreakerTripped,
    LoadFailure,
    ProductionModelLoader,
)
from libs.models.models.serialization import ChecksumMismatchError
from libs.models.models.types import EnvironmentMetadata, ModelMetadata, ModelType

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture()
def mock_env_metadata() -> EnvironmentMetadata:
    """Create mock environment metadata."""
    return EnvironmentMetadata(
        python_version="3.11.5",
        dependencies_hash="abc123",
        platform="linux-x86_64",
        created_by="test_user",
        numpy_version="1.24.0",
        polars_version="0.18.0",
    )


@pytest.fixture()
def mock_metadata(mock_env_metadata: EnvironmentMetadata) -> ModelMetadata:
    """Create mock model metadata."""
    return ModelMetadata(
        model_id="test-model-123",
        model_type=ModelType.risk_model,
        version="v1.0.0",
        created_at=datetime.now(UTC),
        dataset_version_ids={"crsp": "v1.2.3", "compustat": "v1.0.1"},
        snapshot_id="snapshot-123",
        checksum_sha256="abc123def456",
        config_hash="config123",
        env=mock_env_metadata,
    )


@pytest.fixture()
def mock_registry() -> Mock:
    """Create mock registry."""
    registry = Mock()
    registry.get_current_production = Mock(return_value=None)
    registry.get_model_by_id = Mock(return_value=None)
    registry.get_model_metadata = Mock(return_value=None)
    registry.get_artifact_path = Mock(return_value=None)
    return registry


@pytest.fixture()
def mock_compatibility_checker() -> Mock:
    """Create mock compatibility checker."""
    checker = Mock(spec=VersionCompatibilityChecker)
    checker.check_compatibility = Mock(
        return_value=CompatibilityResult(compatible=True, level="exact", warnings=[])
    )
    return checker


# =============================================================================
# CachedModel Tests
# =============================================================================


class TestCachedModel:
    """Tests for CachedModel."""

    def test_cached_model_not_expired_within_ttl(self, mock_metadata: ModelMetadata) -> None:
        """Test cached model is not expired within TTL."""
        model = {"test": "data"}
        loaded_at = datetime.now(UTC)
        cached = CachedModel(model=model, metadata=mock_metadata, loaded_at=loaded_at, ttl_hours=24)

        assert cached.is_expired is False
        assert cached.model == model
        assert cached.metadata == mock_metadata

    def test_cached_model_expired_after_ttl(self, mock_metadata: ModelMetadata) -> None:
        """Test cached model is expired after TTL."""
        model = {"test": "data"}
        loaded_at = datetime.now(UTC) - timedelta(hours=25)  # Expired (TTL=24h)
        cached = CachedModel(model=model, metadata=mock_metadata, loaded_at=loaded_at, ttl_hours=24)

        assert cached.is_expired is True

    def test_cached_model_custom_ttl(self, mock_metadata: ModelMetadata) -> None:
        """Test cached model with custom TTL."""
        model = {"test": "data"}
        loaded_at = datetime.now(UTC) - timedelta(hours=2)
        cached = CachedModel(model=model, metadata=mock_metadata, loaded_at=loaded_at, ttl_hours=1)

        assert cached.is_expired is True


# =============================================================================
# ProductionModelLoader Initialization Tests
# =============================================================================


class TestProductionModelLoaderInit:
    """Tests for ProductionModelLoader initialization."""

    def test_init_with_version_check_required_and_empty_versions_raises(
        self, mock_registry: Mock
    ) -> None:
        """Test initialization raises when require_version_check=True and no versions provided."""
        with pytest.raises(ValueError, match="requires current_dataset_versions"):
            ProductionModelLoader(
                registry=mock_registry,
                current_dataset_versions=None,
                require_version_check=True,
            )

    def test_init_with_version_check_disabled_allows_empty_versions(
        self, mock_registry: Mock
    ) -> None:
        """Test initialization allows empty versions when require_version_check=False."""
        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions=None,
            require_version_check=False,
        )

        assert loader._current_dataset_versions == {}
        assert loader._require_version_check is False

    def test_init_with_valid_versions(self, mock_registry: Mock) -> None:
        """Test initialization with valid dataset versions."""
        versions = {"crsp": "v1.2.3", "compustat": "v1.0.1"}
        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions=versions,
        )

        assert loader._current_dataset_versions == versions
        assert loader.registry == mock_registry

    def test_init_sets_default_poll_interval(self, mock_registry: Mock) -> None:
        """Test initialization sets default poll interval."""
        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        assert loader.poll_interval == ProductionModelLoader.DEFAULT_POLL_INTERVAL_SECONDS

    def test_init_accepts_custom_poll_interval(self, mock_registry: Mock) -> None:
        """Test initialization accepts custom poll interval."""
        loader = ProductionModelLoader(
            registry=mock_registry,
            poll_interval_seconds=30,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        assert loader.poll_interval == 30

    def test_init_creates_compatibility_checker_if_none_provided(self, mock_registry: Mock) -> None:
        """Test initialization creates default compatibility checker."""
        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        assert loader.compatibility_checker is not None
        assert isinstance(loader.compatibility_checker, VersionCompatibilityChecker)

    def test_init_accepts_custom_compatibility_checker(
        self, mock_registry: Mock, mock_compatibility_checker: Mock
    ) -> None:
        """Test initialization accepts custom compatibility checker."""
        loader = ProductionModelLoader(
            registry=mock_registry,
            compatibility_checker=mock_compatibility_checker,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        assert loader.compatibility_checker == mock_compatibility_checker

    def test_init_accepts_circuit_breaker_callback(self, mock_registry: Mock) -> None:
        """Test initialization accepts circuit breaker callback."""
        callback = Mock()
        loader = ProductionModelLoader(
            registry=mock_registry,
            on_circuit_breaker_trip=callback,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        assert loader.on_circuit_breaker_trip == callback


# =============================================================================
# Set Dataset Versions Tests
# =============================================================================


class TestSetCurrentDatasetVersions:
    """Tests for set_current_dataset_versions."""

    def test_set_current_dataset_versions_updates_state(self, mock_registry: Mock) -> None:
        """Test setting dataset versions updates internal state."""
        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.0.0"},
        )

        new_versions = {"crsp": "v1.2.3", "compustat": "v1.0.1"}
        loader.set_current_dataset_versions(new_versions)

        assert loader._current_dataset_versions == new_versions


# =============================================================================
# Model Getter Tests
# =============================================================================


class TestModelGetters:
    """Tests for model getter methods."""

    def test_get_risk_model_calls_internal_get_model(
        self, mock_registry: Mock, mock_metadata: ModelMetadata
    ) -> None:
        """Test get_risk_model delegates to _get_model."""
        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        with patch.object(loader, "_get_model", return_value={"model": "data"}) as mock_get:
            result = loader.get_risk_model(version="v1.0.0")

        mock_get.assert_called_once_with(ModelType.risk_model.value, "v1.0.0")
        assert result == {"model": "data"}

    def test_get_alpha_weights_calls_internal_get_model(
        self, mock_registry: Mock, mock_metadata: ModelMetadata
    ) -> None:
        """Test get_alpha_weights delegates to _get_model."""
        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        weights = {"alpha1": 0.5, "alpha2": 0.5}
        with patch.object(loader, "_get_model", return_value=weights) as mock_get:
            result = loader.get_alpha_weights(version="v1.0.0")

        mock_get.assert_called_once_with(ModelType.alpha_weights.value, "v1.0.0")
        assert result == weights

    def test_get_factor_definitions_calls_internal_get_model(
        self, mock_registry: Mock, mock_metadata: ModelMetadata
    ) -> None:
        """Test get_factor_definitions delegates to _get_model."""
        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        definitions = {"factor1": {"formula": "x + y"}}
        with patch.object(loader, "_get_model", return_value=definitions) as mock_get:
            result = loader.get_factor_definitions(version="v1.0.0")

        mock_get.assert_called_once_with(ModelType.factor_definitions.value, "v1.0.0")
        assert result == definitions

    def test_get_current_version_returns_production_version(
        self, mock_registry: Mock, mock_metadata: ModelMetadata
    ) -> None:
        """Test get_current_version returns production version."""
        mock_metadata_prod = mock_metadata.model_copy(update={"version": "v2.0.0"})
        mock_registry.get_current_production = Mock(return_value=mock_metadata_prod)

        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        version = loader.get_current_version(ModelType.risk_model)

        assert version == "v2.0.0"
        mock_registry.get_current_production.assert_called_once_with(ModelType.risk_model.value)

    def test_get_current_version_returns_none_when_no_production(
        self, mock_registry: Mock
    ) -> None:
        """Test get_current_version returns None when no production version."""
        mock_registry.get_current_production = Mock(return_value=None)

        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        version = loader.get_current_version(ModelType.risk_model)

        assert version is None


# =============================================================================
# Compatibility Check Tests
# =============================================================================


class TestCompatibilityCheck:
    """Tests for check_compatibility."""

    def test_check_compatibility_model_not_found(self, mock_registry: Mock) -> None:
        """Test compatibility check when model not found."""
        mock_registry.get_model_by_id = Mock(return_value=None)

        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        compatible, warnings = loader.check_compatibility(
            model_id="missing-model",
            current_versions={"crsp": "v1.2.3"},
        )

        assert compatible is False
        assert len(warnings) == 1
        assert "not found" in warnings[0]

    def test_check_compatibility_no_checker_configured(
        self, mock_registry: Mock, mock_metadata: ModelMetadata
    ) -> None:
        """Test compatibility check when no checker configured."""
        mock_registry.get_model_by_id = Mock(return_value=mock_metadata)

        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )
        loader.compatibility_checker = None

        compatible, warnings = loader.check_compatibility(
            model_id="test-model",
            current_versions={"crsp": "v1.2.3"},
        )

        assert compatible is True
        assert len(warnings) == 1
        assert "No compatibility checker" in warnings[0]

    def test_check_compatibility_compatible_model(
        self, mock_registry: Mock, mock_metadata: ModelMetadata, mock_compatibility_checker: Mock
    ) -> None:
        """Test compatibility check for compatible model."""
        mock_registry.get_model_by_id = Mock(return_value=mock_metadata)
        mock_compatibility_checker.check_compatibility = Mock(
            return_value=CompatibilityResult(compatible=True, level="exact", warnings=[])
        )

        loader = ProductionModelLoader(
            registry=mock_registry,
            compatibility_checker=mock_compatibility_checker,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        compatible, warnings = loader.check_compatibility(
            model_id="test-model",
            current_versions={"crsp": "v1.2.3", "compustat": "v1.0.1"},
        )

        assert compatible is True
        assert len(warnings) == 0

    def test_check_compatibility_incompatible_model(
        self, mock_registry: Mock, mock_metadata: ModelMetadata, mock_compatibility_checker: Mock
    ) -> None:
        """Test compatibility check for incompatible model."""
        mock_registry.get_model_by_id = Mock(return_value=mock_metadata)
        mock_compatibility_checker.check_compatibility = Mock(
            return_value=CompatibilityResult(
                compatible=False, level="drift", warnings=["Version drift detected"]
            )
        )

        loader = ProductionModelLoader(
            registry=mock_registry,
            compatibility_checker=mock_compatibility_checker,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        compatible, warnings = loader.check_compatibility(
            model_id="test-model",
            current_versions={"crsp": "v1.2.4"},
        )

        assert compatible is False
        assert len(warnings) == 1
        assert "drift" in warnings[0]


# =============================================================================
# Internal _get_model Tests
# =============================================================================


class TestGetModel:
    """Tests for _get_model internal method."""

    def test_get_model_circuit_breaker_tripped_raises(
        self, mock_registry: Mock, mock_metadata: ModelMetadata
    ) -> None:
        """Test _get_model raises when circuit breaker is tripped."""
        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        # Trip circuit breaker
        loader._failures[ModelType.risk_model.value] = (
            ProductionModelLoader.CIRCUIT_BREAKER_THRESHOLD
        )

        with pytest.raises(CircuitBreakerTripped, match="Circuit breaker tripped"):
            loader._get_model(ModelType.risk_model.value, "v1.0.0")

    def test_get_model_no_production_version_raises(self, mock_registry: Mock) -> None:
        """Test _get_model raises when no production version available."""
        mock_registry.get_current_production = Mock(return_value=None)

        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        with pytest.raises(LoadFailure, match="No production version available"):
            loader._get_model(ModelType.risk_model.value, None)

    def test_get_model_cache_hit_returns_cached(
        self, mock_registry: Mock, mock_metadata: ModelMetadata
    ) -> None:
        """Test _get_model returns cached model on cache hit."""
        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        # Populate cache
        model = {"test": "data"}
        cache_key = "risk_model/v1.0.0"
        loader._cache[cache_key] = CachedModel(
            model=model,
            metadata=mock_metadata,
            loaded_at=datetime.now(UTC),
            ttl_hours=24,
        )

        result = loader._get_model(ModelType.risk_model.value, "v1.0.0")

        assert result == model

    def test_get_model_cache_expired_reloads(
        self, mock_registry: Mock, mock_metadata: ModelMetadata
    ) -> None:
        """Test _get_model reloads when cached model is expired."""
        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        # Populate cache with expired entry
        model = {"test": "old_data"}
        cache_key = "risk_model/v1.0.0"
        loader._cache[cache_key] = CachedModel(
            model=model,
            metadata=mock_metadata,
            loaded_at=datetime.now(UTC) - timedelta(hours=25),
            ttl_hours=24,
        )

        # Mock load
        new_model = {"test": "new_data"}
        with patch.object(
            loader, "_load_from_registry", return_value=(new_model, mock_metadata)
        ) as mock_load:
            result = loader._get_model(ModelType.risk_model.value, "v1.0.0")

        assert result == new_model
        mock_load.assert_called_once()

    def test_get_model_thundering_herd_protection(
        self, mock_registry: Mock, mock_metadata: ModelMetadata
    ) -> None:
        """Test _get_model prevents thundering herd on cache miss."""
        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        model = {"test": "data"}
        load_count = 0

        def slow_load(model_type: str, version: str) -> tuple[Any, ModelMetadata]:
            nonlocal load_count
            load_count += 1
            time.sleep(0.1)  # Simulate slow load
            return model, mock_metadata

        with patch.object(loader, "_load_from_registry", side_effect=slow_load):
            # Launch multiple threads requesting same model
            threads = []
            results = []

            def load_model() -> None:
                result = loader._get_model(ModelType.risk_model.value, "v1.0.0")
                results.append(result)

            for _ in range(5):
                thread = threading.Thread(target=load_model)
                threads.append(thread)
                thread.start()

            for thread in threads:
                thread.join()

        # Only one load should occur
        assert load_count == 1
        assert len(results) == 5
        assert all(r == model for r in results)


# =============================================================================
# Load From Registry Tests
# =============================================================================


class TestLoadFromRegistry:
    """Tests for _load_from_registry."""

    def test_load_from_registry_metadata_not_found_raises(self, mock_registry: Mock) -> None:
        """Test _load_from_registry raises when metadata not found."""
        mock_registry.get_model_metadata = Mock(return_value=None)

        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        with pytest.raises(FileNotFoundError, match="Metadata not found"):
            loader._load_from_registry(ModelType.risk_model.value, "v1.0.0")

    def test_load_from_registry_artifact_path_not_found_raises(
        self, mock_registry: Mock, mock_metadata: ModelMetadata
    ) -> None:
        """Test _load_from_registry raises when artifact path not found."""
        mock_registry.get_model_metadata = Mock(return_value=mock_metadata)
        mock_registry.get_artifact_path = Mock(return_value=None)

        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        with pytest.raises(FileNotFoundError, match="Artifact path not found"):
            loader._load_from_registry(ModelType.risk_model.value, "v1.0.0")

    def test_load_from_registry_model_file_not_found_raises(
        self, mock_registry: Mock, mock_metadata: ModelMetadata, tmp_path: Path
    ) -> None:
        """Test _load_from_registry raises when model file doesn't exist."""
        artifact_path = tmp_path / "artifacts"
        artifact_path.mkdir()

        mock_registry.get_model_metadata = Mock(return_value=mock_metadata)
        mock_registry.get_artifact_path = Mock(return_value=artifact_path)

        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        with pytest.raises(FileNotFoundError, match="Model artifact not found"):
            loader._load_from_registry(ModelType.risk_model.value, "v1.0.0")

    def test_load_from_registry_checksum_mismatch_raises(
        self, mock_registry: Mock, mock_metadata: ModelMetadata, tmp_path: Path
    ) -> None:
        """Test _load_from_registry raises on checksum mismatch."""
        artifact_path = tmp_path / "artifacts"
        artifact_path.mkdir()
        model_file = artifact_path / "model.pkl"
        model_file.write_bytes(b"test model data")

        mock_registry.get_model_metadata = Mock(return_value=mock_metadata)
        mock_registry.get_artifact_path = Mock(return_value=artifact_path)

        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        with patch(
            "libs.models.models.loader.compute_checksum", return_value="different_checksum"
        ):
            with pytest.raises(ChecksumMismatchError):
                loader._load_from_registry(ModelType.risk_model.value, "v1.0.0")

    def test_load_from_registry_incompatible_version_raises(
        self,
        mock_registry: Mock,
        mock_metadata: ModelMetadata,
        tmp_path: Path,
        mock_compatibility_checker: Mock,
    ) -> None:
        """Test _load_from_registry raises on incompatible version."""
        artifact_path = tmp_path / "artifacts"
        artifact_path.mkdir()
        model_file = artifact_path / "model.pkl"
        model_file.write_bytes(b"test model data")

        mock_registry.get_model_metadata = Mock(return_value=mock_metadata)
        mock_registry.get_artifact_path = Mock(return_value=artifact_path)

        # Mock incompatible version check
        mock_compatibility_checker.check_compatibility = Mock(
            return_value=CompatibilityResult(
                compatible=False, level="drift", warnings=["Version incompatible"]
            )
        )

        loader = ProductionModelLoader(
            registry=mock_registry,
            compatibility_checker=mock_compatibility_checker,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        with patch("libs.models.models.loader.compute_checksum", return_value="abc123def456"):
            with patch(
                "libs.models.models.loader.deserialize_model",
                return_value=({"model": "data"}, mock_metadata),
            ):
                with pytest.raises(LoadFailure, match="compatibility check failed"):
                    loader._load_from_registry(ModelType.risk_model.value, "v1.0.0")

    def test_load_from_registry_metadata_changed_during_load_raises(
        self, mock_registry: Mock, mock_metadata: ModelMetadata, tmp_path: Path
    ) -> None:
        """Test _load_from_registry raises if metadata changes during load (TOCTOU guard)."""
        artifact_path = tmp_path / "artifacts"
        artifact_path.mkdir()
        model_file = artifact_path / "model.pkl"
        model_file.write_bytes(b"test model data")

        mock_registry.get_model_metadata = Mock(return_value=mock_metadata)
        mock_registry.get_artifact_path = Mock(return_value=artifact_path)

        # Create metadata with different model_id
        different_metadata = mock_metadata.model_copy(update={"model_id": "different-model"})

        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        with patch("libs.models.models.loader.compute_checksum", return_value="abc123def456"):
            with patch(
                "libs.models.models.loader.deserialize_model",
                return_value=({"model": "data"}, different_metadata),
            ):
                with pytest.raises(LoadFailure, match="Metadata changed"):
                    loader._load_from_registry(ModelType.risk_model.value, "v1.0.0")

    def test_load_from_registry_success_with_warnings(
        self,
        mock_registry: Mock,
        mock_metadata: ModelMetadata,
        tmp_path: Path,
        mock_compatibility_checker: Mock,
    ) -> None:
        """Test _load_from_registry succeeds with compatibility warnings."""
        artifact_path = tmp_path / "artifacts"
        artifact_path.mkdir()
        model_file = artifact_path / "model.pkl"
        model_file.write_bytes(b"test model data")

        mock_registry.get_model_metadata = Mock(return_value=mock_metadata)
        mock_registry.get_artifact_path = Mock(return_value=artifact_path)

        # Mock compatible with warnings
        mock_compatibility_checker.check_compatibility = Mock(
            return_value=CompatibilityResult(
                compatible=True, level="drift", warnings=["Minor version drift"]
            )
        )

        loader = ProductionModelLoader(
            registry=mock_registry,
            compatibility_checker=mock_compatibility_checker,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        model = {"model": "data"}
        with patch("libs.models.models.loader.compute_checksum", return_value="abc123def456"):
            with patch(
                "libs.models.models.loader.deserialize_model",
                return_value=(model, mock_metadata),
            ):
                result_model, result_metadata = loader._load_from_registry(
                    ModelType.risk_model.value, "v1.0.0"
                )

        assert result_model == model
        assert result_metadata == mock_metadata

    def test_load_from_registry_skips_version_check_when_no_current_versions(
        self, mock_registry: Mock, mock_metadata: ModelMetadata, tmp_path: Path
    ) -> None:
        """Test _load_from_registry skips version check when no current versions set."""
        artifact_path = tmp_path / "artifacts"
        artifact_path.mkdir()
        model_file = artifact_path / "model.pkl"
        model_file.write_bytes(b"test model data")

        mock_registry.get_model_metadata = Mock(return_value=mock_metadata)
        mock_registry.get_artifact_path = Mock(return_value=artifact_path)

        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions=None,
            require_version_check=False,
        )

        model = {"model": "data"}
        with patch("libs.models.models.loader.compute_checksum", return_value="abc123def456"):
            with patch(
                "libs.models.models.loader.deserialize_model",
                return_value=(model, mock_metadata),
            ):
                result_model, result_metadata = loader._load_from_registry(
                    ModelType.risk_model.value, "v1.0.0"
                )

        assert result_model == model


# =============================================================================
# Success/Failure Recording Tests
# =============================================================================


class TestRecordSuccessAndFailure:
    """Tests for _record_success and _handle_load_failure."""

    def test_record_success_updates_cache_and_state(
        self, mock_registry: Mock, mock_metadata: ModelMetadata
    ) -> None:
        """Test _record_success updates cache and internal state."""
        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        # Set initial failure count
        loader._failures[ModelType.risk_model.value] = 2

        model = {"test": "data"}
        cache_key = "risk_model/v1.0.0"

        loader._record_success(ModelType.risk_model.value, model, mock_metadata, cache_key)

        # Check cache updated
        assert cache_key in loader._cache
        assert loader._cache[cache_key].model == model

        # Check last-good updated
        assert ModelType.risk_model.value in loader._last_good
        assert loader._last_good[ModelType.risk_model.value] == (model, mock_metadata)

        # Check failure counter reset
        assert loader._failures[ModelType.risk_model.value] == 0

    def test_handle_load_failure_increments_failure_count(self, mock_registry: Mock) -> None:
        """Test _handle_load_failure increments failure count."""
        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        error = Exception("Test error")

        with pytest.raises(LoadFailure, match="Test error"):
            loader._handle_load_failure(ModelType.risk_model.value, "v1.0.0", error)

        assert loader._failures[ModelType.risk_model.value] == 1

    def test_handle_load_failure_trips_circuit_breaker_on_threshold(
        self, mock_registry: Mock
    ) -> None:
        """Test _handle_load_failure trips circuit breaker at threshold."""
        callback = Mock()
        loader = ProductionModelLoader(
            registry=mock_registry,
            on_circuit_breaker_trip=callback,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        # Set failures to threshold - 1
        loader._failures[ModelType.risk_model.value] = (
            ProductionModelLoader.CIRCUIT_BREAKER_THRESHOLD - 1
        )

        error = Exception("Test error")

        with pytest.raises(CircuitBreakerTripped, match="Circuit breaker tripped"):
            loader._handle_load_failure(ModelType.risk_model.value, "v1.0.0", error)

        # Check callback invoked
        callback.assert_called_once_with(ModelType.risk_model.value)

    def test_handle_load_failure_falls_back_to_last_good(
        self, mock_registry: Mock, mock_metadata: ModelMetadata
    ) -> None:
        """Test _handle_load_failure falls back to last-good model."""
        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        # Set last-good model
        last_good_model = {"last": "good"}
        loader._last_good[ModelType.risk_model.value] = (last_good_model, mock_metadata)

        error = Exception("Test error")
        result = loader._handle_load_failure(ModelType.risk_model.value, "v1.0.0", error)

        assert result == last_good_model

    def test_handle_load_failure_no_callback_still_trips_breaker(
        self, mock_registry: Mock
    ) -> None:
        """Test _handle_load_failure trips breaker even without callback."""
        loader = ProductionModelLoader(
            registry=mock_registry,
            on_circuit_breaker_trip=None,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        loader._failures[ModelType.risk_model.value] = (
            ProductionModelLoader.CIRCUIT_BREAKER_THRESHOLD - 1
        )

        error = Exception("Test error")

        with pytest.raises(CircuitBreakerTripped):
            loader._handle_load_failure(ModelType.risk_model.value, "v1.0.0", error)

    def test_on_load_failure_callback(self, mock_registry: Mock) -> None:
        """Test on_load_failure callback logs error."""
        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        error = Exception("Test error")
        loader.on_load_failure("test-model-123", error)

        # Should not raise, just log


# =============================================================================
# Polling Tests
# =============================================================================


class TestPolling:
    """Tests for polling functionality."""

    def test_start_polling_starts_thread(self, mock_registry: Mock) -> None:
        """Test start_polling starts polling thread."""
        loader = ProductionModelLoader(
            registry=mock_registry,
            poll_interval_seconds=1,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        loader.start_polling()

        assert loader._polling is True
        assert loader._poll_thread is not None
        assert loader._poll_thread.is_alive()

        # Cleanup
        loader.stop_polling()

    def test_start_polling_idempotent(self, mock_registry: Mock) -> None:
        """Test start_polling is idempotent."""
        loader = ProductionModelLoader(
            registry=mock_registry,
            poll_interval_seconds=1,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        loader.start_polling()
        first_thread = loader._poll_thread

        loader.start_polling()  # Should not start new thread
        assert loader._poll_thread == first_thread

        # Cleanup
        loader.stop_polling()

    def test_stop_polling_stops_thread(self, mock_registry: Mock) -> None:
        """Test stop_polling stops polling thread."""
        loader = ProductionModelLoader(
            registry=mock_registry,
            poll_interval_seconds=1,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        loader.start_polling()
        loader.stop_polling()

        assert loader._polling is False
        assert loader._poll_thread is None

    def test_poll_loop_checks_for_updates(
        self, mock_registry: Mock, mock_metadata: ModelMetadata
    ) -> None:
        """Test _poll_loop checks for version updates."""
        mock_registry.get_current_production = Mock(return_value=mock_metadata)

        loader = ProductionModelLoader(
            registry=mock_registry,
            poll_interval_seconds=0.1,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        with patch.object(loader, "_check_for_update") as mock_check:
            loader.start_polling([ModelType.risk_model])
            time.sleep(0.25)  # Allow at least 2 poll iterations
            loader.stop_polling()

        # Should have checked at least once
        assert mock_check.call_count >= 1

    def test_check_for_update_preloads_new_version(
        self, mock_registry: Mock, mock_metadata: ModelMetadata
    ) -> None:
        """Test _check_for_update preloads new production version."""
        mock_registry.get_current_production = Mock(return_value=mock_metadata)

        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        with patch.object(loader, "_get_model") as mock_get:
            loader._check_for_update(ModelType.risk_model)

        mock_get.assert_called_once_with(ModelType.risk_model.value, "v1.0.0")

    def test_check_for_update_skips_cached_version(
        self, mock_registry: Mock, mock_metadata: ModelMetadata
    ) -> None:
        """Test _check_for_update skips already cached version."""
        mock_registry.get_current_production = Mock(return_value=mock_metadata)

        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        # Populate cache
        cache_key = f"{ModelType.risk_model.value}/v1.0.0"
        loader._cache[cache_key] = CachedModel(
            model={"test": "data"},
            metadata=mock_metadata,
            loaded_at=datetime.now(UTC),
            ttl_hours=24,
        )

        with patch.object(loader, "_get_model") as mock_get:
            loader._check_for_update(ModelType.risk_model)

        mock_get.assert_not_called()

    def test_check_for_update_handles_no_production_version(self, mock_registry: Mock) -> None:
        """Test _check_for_update handles no production version gracefully."""
        mock_registry.get_current_production = Mock(return_value=None)

        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        # Should not raise
        loader._check_for_update(ModelType.risk_model)

    def test_check_for_update_handles_preload_failure(
        self, mock_registry: Mock, mock_metadata: ModelMetadata
    ) -> None:
        """Test _check_for_update handles preload failure gracefully."""
        mock_registry.get_current_production = Mock(return_value=mock_metadata)

        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        with patch.object(loader, "_get_model", side_effect=Exception("Preload failed")):
            # Should not raise
            loader._check_for_update(ModelType.risk_model)


# =============================================================================
# Cache Management Tests
# =============================================================================


class TestCacheManagement:
    """Tests for cache management methods."""

    def test_clear_cache_all_entries(self, mock_registry: Mock, mock_metadata: ModelMetadata) -> None:
        """Test clear_cache removes all entries when model_type is None."""
        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        # Populate cache
        loader._cache["risk_model/v1.0.0"] = CachedModel(
            model={"test": "data"},
            metadata=mock_metadata,
            loaded_at=datetime.now(UTC),
        )
        loader._cache["alpha_weights/v1.0.0"] = CachedModel(
            model={"weights": "data"},
            metadata=mock_metadata,
            loaded_at=datetime.now(UTC),
        )

        count = loader.clear_cache(model_type=None)

        assert count == 2
        assert len(loader._cache) == 0

    def test_clear_cache_specific_model_type(
        self, mock_registry: Mock, mock_metadata: ModelMetadata
    ) -> None:
        """Test clear_cache removes only specified model type."""
        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        # Populate cache
        loader._cache["risk_model/v1.0.0"] = CachedModel(
            model={"test": "data"},
            metadata=mock_metadata,
            loaded_at=datetime.now(UTC),
        )
        loader._cache["alpha_weights/v1.0.0"] = CachedModel(
            model={"weights": "data"},
            metadata=mock_metadata,
            loaded_at=datetime.now(UTC),
        )

        count = loader.clear_cache(model_type="risk_model")

        assert count == 1
        assert "risk_model/v1.0.0" not in loader._cache
        assert "alpha_weights/v1.0.0" in loader._cache

    def test_clear_cache_empty_cache(self, mock_registry: Mock) -> None:
        """Test clear_cache on empty cache."""
        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        count = loader.clear_cache()

        assert count == 0

    def test_reset_circuit_breaker(self, mock_registry: Mock) -> None:
        """Test reset_circuit_breaker resets failure count."""
        loader = ProductionModelLoader(
            registry=mock_registry,
            current_dataset_versions={"crsp": "v1.2.3"},
        )

        # Set failure count
        loader._failures[ModelType.risk_model.value] = 5

        loader.reset_circuit_breaker(ModelType.risk_model.value)

        assert loader._failures[ModelType.risk_model.value] == 0


# =============================================================================
# Exception Tests
# =============================================================================


class TestExceptions:
    """Tests for custom exceptions."""

    def test_load_failure_exception(self) -> None:
        """Test LoadFailure exception attributes."""
        exc = LoadFailure(model_type="risk_model", version="v1.0.0", cause="Test cause")

        assert exc.model_type == "risk_model"
        assert exc.version == "v1.0.0"
        assert exc.cause == "Test cause"
        assert "risk_model/v1.0.0" in str(exc)

    def test_circuit_breaker_tripped_exception(self) -> None:
        """Test CircuitBreakerTripped exception attributes."""
        exc = CircuitBreakerTripped(model_type="risk_model", failure_count=3)

        assert exc.model_type == "risk_model"
        assert exc.failure_count == 3
        assert "Circuit breaker tripped" in str(exc)
