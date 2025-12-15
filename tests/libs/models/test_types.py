"""Tests for Model Registry types."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from libs.models.types import (
    ARTIFACT_REQUIRED_FIELDS,
    EnvironmentMetadata,
    MissingRequiredFieldError,
    ModelMetadata,
    ModelStatus,
    ModelType,
    PromotionGateError,
    PromotionGates,
    validate_artifact_metadata,
)


class TestModelType:
    """Tests for ModelType enum."""

    def test_all_types_defined(self) -> None:
        """Verify all model types are defined."""
        assert ModelType.risk_model.value == "risk_model"
        assert ModelType.alpha_weights.value == "alpha_weights"
        assert ModelType.factor_definitions.value == "factor_definitions"
        assert ModelType.feature_transforms.value == "feature_transforms"

    def test_model_type_from_string(self) -> None:
        """Test creating ModelType from string."""
        assert ModelType("risk_model") == ModelType.risk_model


class TestModelStatus:
    """Tests for ModelStatus enum."""

    def test_all_statuses_defined(self) -> None:
        """Verify all statuses are defined."""
        assert ModelStatus.staged.value == "staged"
        assert ModelStatus.production.value == "production"
        assert ModelStatus.archived.value == "archived"
        assert ModelStatus.failed.value == "failed"


class TestEnvironmentMetadata:
    """Tests for EnvironmentMetadata."""

    def test_creation(self) -> None:
        """Test creating environment metadata."""
        env = EnvironmentMetadata(
            python_version="3.11.5",
            dependencies_hash="abc123",
            platform="linux-x86_64",
            created_by="test",
            numpy_version="1.24.0",
            polars_version="0.20.0",
            sklearn_version="1.3.0",
            cvxpy_version=None,
        )
        assert env.python_version == "3.11.5"
        assert env.created_by == "test"
        assert env.cvxpy_version is None

    def test_immutable(self) -> None:
        """Test that EnvironmentMetadata is frozen."""
        env = EnvironmentMetadata(
            python_version="3.11.5",
            dependencies_hash="abc123",
            platform="linux-x86_64",
            created_by="test",
            numpy_version="1.24.0",
            polars_version="0.20.0",
        )
        with pytest.raises(ValidationError, match="frozen"):
            env.python_version = "3.12.0"


class TestModelMetadata:
    """Tests for ModelMetadata."""

    @pytest.fixture()
    def sample_env(self) -> EnvironmentMetadata:
        """Create sample environment metadata."""
        return EnvironmentMetadata(
            python_version="3.11.5",
            dependencies_hash="abc123",
            platform="linux-x86_64",
            created_by="test",
            numpy_version="1.24.0",
            polars_version="0.20.0",
        )

    def test_creation(self, sample_env: EnvironmentMetadata) -> None:
        """Test creating model metadata."""
        metadata = ModelMetadata(
            model_id="test-123",
            model_type=ModelType.risk_model,
            version="v1.0.0",
            created_at=datetime.now(UTC),
            dataset_version_ids={"crsp": "v1.2.3"},
            snapshot_id="snap_123",
            factor_list=["momentum", "value"],
            parameters={
                "halflife_days": 60,
                "shrinkage_intensity": 0.5,
                "factor_list": ["momentum", "value"],
            },
            checksum_sha256="abc123def456",
            metrics={"ic": 0.05, "sharpe": 1.2},
            env=sample_env,
            config={"learning_rate": 0.01},
            config_hash="cfg123",
        )
        assert metadata.model_id == "test-123"
        assert metadata.model_type == ModelType.risk_model
        assert metadata.version == "v1.0.0"

    def test_version_format_validation(self, sample_env: EnvironmentMetadata) -> None:
        """Test that version must be semantic."""
        with pytest.raises(ValueError, match="pattern"):
            ModelMetadata(
                model_id="test-123",
                model_type=ModelType.risk_model,
                version="1.0.0",  # Missing 'v' prefix
                created_at=datetime.now(UTC),
                dataset_version_ids={"crsp": "v1.2.3"},
                snapshot_id="snap_123",
                checksum_sha256="abc123",
                env=sample_env,
                config={},
                config_hash="cfg123",
            )

    def test_utc_validation(self, sample_env: EnvironmentMetadata) -> None:
        """Test that created_at must be UTC."""
        with pytest.raises(ValueError, match="timezone-aware"):
            ModelMetadata(
                model_id="test-123",
                model_type=ModelType.risk_model,
                version="v1.0.0",
                created_at=datetime.now(),  # Naive datetime
                dataset_version_ids={"crsp": "v1.2.3"},
                snapshot_id="snap_123",
                checksum_sha256="abc123",
                env=sample_env,
                config={},
                config_hash="cfg123",
            )


class TestArtifactRequiredFields:
    """Tests for per-artifact required field validation."""

    def test_required_fields_defined(self) -> None:
        """Verify required fields are defined for all types."""
        assert ModelType.risk_model in ARTIFACT_REQUIRED_FIELDS
        assert ModelType.alpha_weights in ARTIFACT_REQUIRED_FIELDS
        assert ModelType.factor_definitions in ARTIFACT_REQUIRED_FIELDS
        assert ModelType.feature_transforms in ARTIFACT_REQUIRED_FIELDS

    def test_risk_model_required_fields(self) -> None:
        """Verify risk_model required fields."""
        required = ARTIFACT_REQUIRED_FIELDS[ModelType.risk_model]
        assert "factor_list" in required
        assert "halflife_days" in required
        assert "shrinkage_intensity" in required

    def test_validate_artifact_metadata_pass(self) -> None:
        """Test validation passes with required fields."""
        env = EnvironmentMetadata(
            python_version="3.11.5",
            dependencies_hash="abc123",
            platform="linux-x86_64",
            created_by="test",
            numpy_version="1.24.0",
            polars_version="0.20.0",
        )
        metadata = ModelMetadata(
            model_id="test-123",
            model_type=ModelType.risk_model,
            version="v1.0.0",
            created_at=datetime.now(UTC),
            dataset_version_ids={"crsp": "v1.2.3"},
            snapshot_id="snap_123",
            parameters={
                "factor_list": ["momentum", "value"],
                "halflife_days": 60,
                "shrinkage_intensity": 0.5,
            },
            checksum_sha256="abc123",
            env=env,
            config={},
            config_hash="cfg123",
        )
        # Should not raise
        validate_artifact_metadata(ModelType.risk_model, metadata)

    def test_validate_artifact_metadata_fail(self) -> None:
        """Test validation fails with missing fields."""
        env = EnvironmentMetadata(
            python_version="3.11.5",
            dependencies_hash="abc123",
            platform="linux-x86_64",
            created_by="test",
            numpy_version="1.24.0",
            polars_version="0.20.0",
        )
        metadata = ModelMetadata(
            model_id="test-123",
            model_type=ModelType.risk_model,
            version="v1.0.0",
            created_at=datetime.now(UTC),
            dataset_version_ids={"crsp": "v1.2.3"},
            snapshot_id="snap_123",
            parameters={"factor_list": ["momentum"]},  # Missing halflife_days, shrinkage_intensity
            checksum_sha256="abc123",
            env=env,
            config={},
            config_hash="cfg123",
        )
        with pytest.raises(MissingRequiredFieldError) as exc_info:
            validate_artifact_metadata(ModelType.risk_model, metadata)
        assert "halflife_days" in str(exc_info.value) or "shrinkage_intensity" in str(
            exc_info.value
        )


class TestPromotionGates:
    """Tests for promotion gates."""

    def test_default_values(self) -> None:
        """Test default gate values."""
        gates = PromotionGates()
        assert gates.min_ic == 0.02
        assert gates.min_sharpe == 0.5
        assert gates.min_paper_trade_hours == 24

    def test_custom_values(self) -> None:
        """Test custom gate values."""
        gates = PromotionGates(min_ic=0.05, min_sharpe=1.0, min_paper_trade_hours=48)
        assert gates.min_ic == 0.05
        assert gates.min_sharpe == 1.0
        assert gates.min_paper_trade_hours == 48


class TestPromotionGateError:
    """Tests for PromotionGateError."""

    def test_error_message(self) -> None:
        """Test error message format."""
        error = PromotionGateError("ic", 0.01, 0.02)
        assert "ic" in str(error)
        assert "0.01" in str(error)
        assert "0.02" in str(error)
