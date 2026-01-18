from datetime import datetime, timezone

from apps.model_registry.schemas import (
    ERROR_CHECKSUM_MISMATCH,
    ERROR_MISSING_FIELDS,
    ERROR_MODEL_NOT_FOUND,
    ERROR_REGISTRY_LOCKED,
    ERROR_VALIDATION_FAILED,
    ERROR_VERSION_EXISTS,
    HTTP_ERRORS,
    CurrentModelResponse,
    EnvironmentMetadataResponse,
    ErrorResponse,
    ModelListResponse,
    ModelMetadataResponse,
    ValidationResultResponse,
)


def test_environment_metadata_response_allows_optional_fields() -> None:
    env = EnvironmentMetadataResponse(
        python_version="3.11.0",
        dependencies_hash="hash",
        platform="darwin",
        created_by="trainer",
        numpy_version="1.26.0",
        polars_version="0.20.0",
        sklearn_version=None,
        cvxpy_version=None,
    )

    assert env.sklearn_version is None
    assert env.cvxpy_version is None


def test_current_model_response_serializes() -> None:
    response = CurrentModelResponse(
        model_type="risk_model",
        version="v1.0.0",
        checksum="abc123",
        dataset_version_ids={"crsp": "v1.2.3"},
    )

    payload = response.model_dump()
    assert payload["model_type"] == "risk_model"
    assert payload["dataset_version_ids"]["crsp"] == "v1.2.3"


def test_model_metadata_response_includes_nested_env() -> None:
    env = EnvironmentMetadataResponse(
        python_version="3.11.0",
        dependencies_hash="hash",
        platform="darwin",
        created_by="trainer",
        numpy_version="1.26.0",
        polars_version="0.20.0",
        sklearn_version="1.5.0",
        cvxpy_version=None,
    )

    response = ModelMetadataResponse(
        model_id="model-1",
        model_type="risk_model",
        version="v1.0.0",
        status="production",
        artifact_path="/tmp/model",
        checksum_sha256="checksum",
        dataset_version_ids={"crsp": "v1.2.3"},
        snapshot_id="snapshot-1",
        factor_list=["factor-a"],
        parameters={"param": 1},
        metrics={"ic": 0.1},
        config={"learning_rate": 0.01},
        config_hash="config-hash",
        feature_formulas=None,
        env=env,
        experiment_id=None,
        run_id=None,
        dataset_uri=None,
        qlib_version=None,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        promoted_at=None,
    )

    payload = response.model_dump()
    assert payload["env"]["python_version"] == "3.11.0"
    assert payload["status"] == "production"


def test_validation_result_response_defaults_errors_list() -> None:
    response = ValidationResultResponse(
        valid=True,
        model_id="model-1",
        checksum_verified=True,
        load_successful=True,
    )

    assert response.errors == []


def test_error_response_example_fields() -> None:
    response = ErrorResponse(detail="failure", code="ERROR_CODE")

    payload = response.model_dump()
    assert payload == {"detail": "failure", "code": "ERROR_CODE"}


def test_model_list_response_counts_models() -> None:
    env = EnvironmentMetadataResponse(
        python_version="3.11.0",
        dependencies_hash="hash",
        platform="darwin",
        created_by="trainer",
        numpy_version="1.26.0",
        polars_version="0.20.0",
        sklearn_version=None,
        cvxpy_version=None,
    )
    metadata = ModelMetadataResponse(
        model_id="model-1",
        model_type="risk_model",
        version="v1.0.0",
        status="staged",
        artifact_path="/tmp/model",
        checksum_sha256="checksum",
        dataset_version_ids={"crsp": "v1.2.3"},
        snapshot_id="snapshot-1",
        factor_list=["factor-a"],
        parameters={"param": 1},
        metrics={"ic": 0.1},
        config={"learning_rate": 0.01},
        config_hash="config-hash",
        feature_formulas=None,
        env=env,
        experiment_id=None,
        run_id=None,
        dataset_uri=None,
        qlib_version=None,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        promoted_at=None,
    )

    response = ModelListResponse(models=[metadata], total=1)

    assert response.total == 1
    assert response.models[0].version == "v1.0.0"


def test_error_constants_and_http_errors() -> None:
    assert ERROR_MODEL_NOT_FOUND == "MODEL_NOT_FOUND"
    assert ERROR_VERSION_EXISTS == "VERSION_EXISTS"
    assert ERROR_CHECKSUM_MISMATCH == "CHECKSUM_MISMATCH"
    assert ERROR_REGISTRY_LOCKED == "REGISTRY_LOCKED"
    assert ERROR_VALIDATION_FAILED == "VALIDATION_FAILED"
    assert ERROR_MISSING_FIELDS == "MISSING_REQUIRED_FIELDS"

    assert HTTP_ERRORS[503] == "Registry temporarily locked or unavailable"
