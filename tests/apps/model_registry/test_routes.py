from __future__ import annotations

import logging
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from apps.model_registry import routes
from apps.model_registry.auth import ServiceToken
from apps.model_registry.error_handlers import install_error_handlers
from apps.model_registry.schemas import (
    ERROR_CHECKSUM_MISMATCH,
    ERROR_MODEL_NOT_FOUND,
    ERROR_REGISTRY_LOCKED,
    ERROR_VALIDATION_FAILED,
    ErrorResponse,
)
from libs.models.models import (
    IntegrityError,
    ModelStatus,
    ModelType,
    RegistryLockError,
)
from libs.models.models.types import EnvironmentMetadata, ModelMetadata, ValidationResult


def _build_metadata(version: str = "v1.0.0") -> ModelMetadata:
    env = EnvironmentMetadata(
        python_version="3.11.0",
        dependencies_hash="hash",
        platform="darwin",
        created_by="trainer",
        numpy_version="1.26.0",
        polars_version="0.20.0",
        sklearn_version=None,
        cvxpy_version=None,
    )
    return ModelMetadata(
        model_id=f"model-{version}",
        model_type=ModelType.risk_model,
        version=version,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        dataset_version_ids={"crsp": "v1.2.3"},
        snapshot_id="snapshot-1",
        factor_list=["factor-a"],
        parameters={"param": 1},
        checksum_sha256="checksum",
        metrics={"ic": 0.1},
        env=env,
        config={"learning_rate": 0.01},
        config_hash="config-hash",
        feature_formulas=None,
        experiment_id=None,
        run_id=None,
        dataset_uri=None,
        qlib_version=None,
    )


def _client_with_registry(registry: MagicMock) -> TestClient:
    app = FastAPI()
    # Install flattening error handlers so tests observe the same payload
    # shape as production (see issue #166).
    install_error_handlers(app)
    app.include_router(routes.router)
    app.dependency_overrides[routes.verify_read_scope] = lambda: ServiceToken(
        scopes=("model:read",), auth_role="svc"
    )
    app.dependency_overrides[routes.verify_write_scope] = lambda: ServiceToken(
        scopes=("model:write",), auth_role="svc"
    )
    app.dependency_overrides[routes.get_registry] = lambda: registry
    return TestClient(app)


def _assert_error_response_shape(payload: dict, expected_code: str) -> None:
    """Assert that ``payload`` conforms to :class:`ErrorResponse` and has the expected code.

    Regression lock for issue #166: the response body must be flat
    (``detail`` and ``code`` at the top level, both strings), not the
    doubly-nested shape FastAPI produces when a dict is passed to
    ``HTTPException.detail``.

    We only assert on the *required* fields of :class:`ErrorResponse` and
    that the top-level ``detail`` is a string (i.e. not a nested dict). This
    keeps the regression-lock strict enough to catch issue #166 while
    remaining forward-compatible with future non-breaking additions to the
    schema (e.g. optional fields with defaults).
    """
    # Round-trips through ErrorResponse, guaranteeing the required fields
    # exist and have the right types. Raises ``ValidationError`` otherwise.
    validated = ErrorResponse.model_validate(payload)
    assert isinstance(
        payload.get("detail"), str
    ), "Top-level `detail` must be a string, not a nested dict (#166 regression)"
    assert payload.get("code") == expected_code
    assert validated.detail == payload["detail"]
    assert validated.code == expected_code


def test_get_registry_raises_when_uninitialized() -> None:
    routes._registry = None

    with pytest.raises(HTTPException) as excinfo:
        routes.get_registry()

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail["code"] == ERROR_REGISTRY_LOCKED


def test_get_current_model_success() -> None:
    registry = MagicMock()
    registry.get_current_production.return_value = _build_metadata()
    client = _client_with_registry(registry)

    response = client.get("/api/v1/models/risk_model/current")

    assert response.status_code == 200
    payload = response.json()
    assert payload["model_type"] == "risk_model"
    assert payload["version"] == "v1.0.0"
    assert payload["checksum"] == "checksum"


def _assert_auth_role_in_logs(
    caplog: pytest.LogCaptureFixture,
    expected_message: str,
) -> None:
    """Assert that a log record with the given message includes auth_role."""
    matching = [
        r
        for r in caplog.records
        if r.levelno == logging.INFO and expected_message in r.getMessage()
    ]
    assert len(matching) >= 1, f"Expected INFO record containing '{expected_message}'"
    for record in matching:
        assert hasattr(record, "auth_role"), "Log record must include auth_role"
        assert record.auth_role == "svc"
        # Verify constant service identifier for log query compatibility
        assert hasattr(record, "service"), "Log record must include service"
        assert record.service == "model_registry"


def test_get_current_model_logs_auth_role(caplog: pytest.LogCaptureFixture) -> None:
    """Ensure route logs include auth_role field (fixes #174)."""
    registry = MagicMock()
    registry.get_current_production.return_value = _build_metadata()
    client = _client_with_registry(registry)

    with caplog.at_level(logging.INFO, logger="apps.model_registry.routes"):
        response = client.get("/api/v1/models/risk_model/current")

    assert response.status_code == 200
    _assert_auth_role_in_logs(caplog, "Retrieved current production model")


def test_get_model_metadata_logs_auth_role(caplog: pytest.LogCaptureFixture) -> None:
    """Ensure metadata endpoint logs include auth_role field."""
    metadata = _build_metadata()
    registry = MagicMock()
    registry.get_model_metadata.return_value = metadata
    registry.get_model_info.return_value = {
        "status": "production",
        "artifact_path": "/tmp/artifact",
        "promoted_at": datetime(2024, 2, 1, tzinfo=UTC),
    }
    client = _client_with_registry(registry)

    with caplog.at_level(logging.INFO, logger="apps.model_registry.routes"):
        response = client.get("/api/v1/models/risk_model/v1.0.0")

    assert response.status_code == 200
    _assert_auth_role_in_logs(caplog, "Retrieved model metadata")


def test_list_models_logs_auth_role(caplog: pytest.LogCaptureFixture) -> None:
    """Ensure list endpoint logs include auth_role field."""
    metadata = _build_metadata()
    registry = MagicMock()
    registry.list_models.return_value = [metadata]
    registry.get_model_info_bulk.return_value = {
        metadata.version: {
            "status": ModelStatus.production.value,
            "artifact_path": "/tmp/artifact",
            "promoted_at": None,
        },
    }
    client = _client_with_registry(registry)

    with caplog.at_level(logging.INFO, logger="apps.model_registry.routes"):
        response = client.get("/api/v1/models/risk_model")

    assert response.status_code == 200
    _assert_auth_role_in_logs(caplog, "Listed models")


def test_validate_model_logs_auth_role(caplog: pytest.LogCaptureFixture) -> None:
    """Ensure validate endpoint logs include auth_role field."""
    registry = MagicMock()
    registry.validate_model.return_value = ValidationResult(
        valid=True,
        model_id="model-v1",
        checksum_verified=True,
        load_successful=True,
        errors=[],
    )
    client = _client_with_registry(registry)

    with caplog.at_level(logging.INFO, logger="apps.model_registry.routes"):
        response = client.post("/api/v1/models/risk_model/v1.0.0/validate")

    assert response.status_code == 200
    _assert_auth_role_in_logs(caplog, "Validated model")


def test_get_current_model_handles_lock_error() -> None:
    registry = MagicMock()
    registry.get_current_production.side_effect = RegistryLockError("locked")
    client = _client_with_registry(registry)

    response = client.get("/api/v1/models/risk_model/current")

    assert response.status_code == 503
    _assert_error_response_shape(response.json(), ERROR_REGISTRY_LOCKED)


def test_get_current_model_missing_returns_404() -> None:
    registry = MagicMock()
    registry.get_current_production.return_value = None
    client = _client_with_registry(registry)

    response = client.get("/api/v1/models/risk_model/current")

    assert response.status_code == 404
    _assert_error_response_shape(response.json(), ERROR_MODEL_NOT_FOUND)


def test_get_model_metadata_requires_db_info() -> None:
    registry = MagicMock()
    registry.get_model_metadata.return_value = _build_metadata()
    registry.get_model_info.return_value = None
    client = _client_with_registry(registry)

    response = client.get("/api/v1/models/risk_model/v1.0.0")

    assert response.status_code == 503
    _assert_error_response_shape(response.json(), ERROR_CHECKSUM_MISMATCH)


def test_get_model_metadata_success() -> None:
    metadata = _build_metadata()
    registry = MagicMock()
    registry.get_model_metadata.return_value = metadata
    registry.get_model_info.return_value = {
        "status": "production",
        "artifact_path": "/tmp/artifact",
        "promoted_at": datetime(2024, 2, 1, tzinfo=UTC),
    }
    client = _client_with_registry(registry)

    response = client.get("/api/v1/models/risk_model/v1.0.0")

    assert response.status_code == 200
    payload = response.json()
    assert payload["model_id"] == metadata.model_id
    assert payload["status"] == "production"
    assert payload["artifact_path"] == "/tmp/artifact"


def test_validate_model_missing_returns_404() -> None:
    registry = MagicMock()
    registry.validate_model.return_value = ValidationResult(
        valid=False,
        model_id="",
        checksum_verified=False,
        load_successful=False,
        errors=["missing"],
    )
    client = _client_with_registry(registry)

    response = client.post("/api/v1/models/risk_model/v1.0.0/validate")

    assert response.status_code == 404
    _assert_error_response_shape(response.json(), ERROR_MODEL_NOT_FOUND)


def test_validate_model_checksum_failure_returns_422() -> None:
    registry = MagicMock()
    registry.validate_model.return_value = ValidationResult(
        valid=False,
        model_id="model-v1",
        checksum_verified=False,
        load_successful=True,
        errors=["checksum mismatch"],
    )
    client = _client_with_registry(registry)

    response = client.post("/api/v1/models/risk_model/v1.0.0/validate")

    assert response.status_code == 422
    _assert_error_response_shape(response.json(), ERROR_CHECKSUM_MISMATCH)


def test_validate_model_load_failure_returns_422() -> None:
    registry = MagicMock()
    registry.validate_model.return_value = ValidationResult(
        valid=False,
        model_id="model-v1",
        checksum_verified=True,
        load_successful=False,
        errors=["load failed"],
    )
    client = _client_with_registry(registry)

    response = client.post("/api/v1/models/risk_model/v1.0.0/validate")

    assert response.status_code == 422
    _assert_error_response_shape(response.json(), ERROR_VALIDATION_FAILED)


def test_validate_model_success() -> None:
    registry = MagicMock()
    registry.validate_model.return_value = ValidationResult(
        valid=True,
        model_id="model-v1",
        checksum_verified=True,
        load_successful=True,
        errors=[],
    )
    client = _client_with_registry(registry)

    response = client.post("/api/v1/models/risk_model/v1.0.0/validate")

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is True
    assert payload["model_id"] == "model-v1"


def test_list_models_success() -> None:
    metadata_a = _build_metadata("v1.0.0")
    metadata_b = _build_metadata("v1.0.1")
    registry = MagicMock()
    registry.list_models.return_value = [metadata_a, metadata_b]
    registry.get_model_info_bulk.return_value = {
        "v1.0.0": {
            "status": ModelStatus.staged.value,
            "artifact_path": "/tmp/a",
            "promoted_at": None,
        },
        "v1.0.1": {
            "status": ModelStatus.production.value,
            "artifact_path": "/tmp/b",
            "promoted_at": datetime(2024, 2, 1, tzinfo=UTC),
        },
    }
    client = _client_with_registry(registry)

    response = client.get("/api/v1/models/risk_model")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert {m["version"] for m in payload["models"]} == {"v1.0.0", "v1.0.1"}


def test_list_models_missing_db_info_returns_503() -> None:
    metadata_a = _build_metadata("v1.0.0")
    registry = MagicMock()
    registry.list_models.return_value = [metadata_a]
    registry.get_model_info_bulk.return_value = {}
    client = _client_with_registry(registry)

    response = client.get("/api/v1/models/risk_model")

    assert response.status_code == 503
    _assert_error_response_shape(response.json(), ERROR_CHECKSUM_MISMATCH)


def test_list_models_handles_integrity_error() -> None:
    registry = MagicMock()
    registry.list_models.side_effect = IntegrityError("broken")
    client = _client_with_registry(registry)

    response = client.get("/api/v1/models/risk_model")

    assert response.status_code == 503
    _assert_error_response_shape(response.json(), ERROR_CHECKSUM_MISMATCH)


# =============================================================================
# Issue #166 regression: every error path must return the flat ErrorResponse
# shape declared in the OpenAPI contract (``{"detail": str, "code": str}``)
# rather than the doubly-nested shape FastAPI produces by default when a dict
# is passed to ``HTTPException.detail``.
# =============================================================================


def test_error_response_flat_shape_registry_uninitialized() -> None:
    """Regression: the 503 raised by get_registry flattens to ErrorResponse."""
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(routes.router)
    app.dependency_overrides[routes.verify_read_scope] = lambda: ServiceToken(
        scopes=("model:read",), auth_role="svc"
    )
    # Intentionally no get_registry override -- force the uninitialized path.
    routes._registry = None
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/api/v1/models/risk_model/current")

    assert response.status_code == 503
    _assert_error_response_shape(response.json(), ERROR_REGISTRY_LOCKED)


def test_error_response_flat_shape_integrity_error_on_current() -> None:
    registry = MagicMock()
    registry.get_current_production.side_effect = IntegrityError("corrupt")
    client = _client_with_registry(registry)

    response = client.get("/api/v1/models/risk_model/current")

    assert response.status_code == 503
    _assert_error_response_shape(response.json(), ERROR_CHECKSUM_MISMATCH)


def test_error_response_flat_shape_integrity_error_on_metadata() -> None:
    registry = MagicMock()
    registry.get_model_metadata.side_effect = IntegrityError("corrupt")
    client = _client_with_registry(registry)

    response = client.get("/api/v1/models/risk_model/v1.0.0")

    assert response.status_code == 503
    _assert_error_response_shape(response.json(), ERROR_CHECKSUM_MISMATCH)


def test_error_response_flat_shape_lock_error_on_metadata() -> None:
    registry = MagicMock()
    registry.get_model_metadata.side_effect = RegistryLockError("locked")
    client = _client_with_registry(registry)

    response = client.get("/api/v1/models/risk_model/v1.0.0")

    assert response.status_code == 503
    _assert_error_response_shape(response.json(), ERROR_REGISTRY_LOCKED)


def test_error_response_flat_shape_metadata_not_found() -> None:
    registry = MagicMock()
    registry.get_model_metadata.return_value = None
    client = _client_with_registry(registry)

    response = client.get("/api/v1/models/risk_model/v1.0.0")

    assert response.status_code == 404
    _assert_error_response_shape(response.json(), ERROR_MODEL_NOT_FOUND)


def test_error_response_flat_shape_lock_error_on_validate() -> None:
    registry = MagicMock()
    registry.validate_model.side_effect = RegistryLockError("locked")
    client = _client_with_registry(registry)

    response = client.post("/api/v1/models/risk_model/v1.0.0/validate")

    assert response.status_code == 503
    _assert_error_response_shape(response.json(), ERROR_REGISTRY_LOCKED)


def test_error_response_flat_shape_integrity_error_on_validate() -> None:
    registry = MagicMock()
    registry.validate_model.side_effect = IntegrityError("corrupt")
    client = _client_with_registry(registry)

    response = client.post("/api/v1/models/risk_model/v1.0.0/validate")

    assert response.status_code == 503
    _assert_error_response_shape(response.json(), ERROR_CHECKSUM_MISMATCH)


def test_error_response_flat_shape_lock_error_on_list() -> None:
    registry = MagicMock()
    registry.list_models.side_effect = RegistryLockError("locked")
    client = _client_with_registry(registry)

    response = client.get("/api/v1/models/risk_model")

    assert response.status_code == 503
    _assert_error_response_shape(response.json(), ERROR_REGISTRY_LOCKED)


def test_flatten_handler_preserves_string_detail() -> None:
    """Plain string details (no dict) must still go through the default handler unchanged."""
    from fastapi import HTTPException as FastAPIHTTPException

    app = FastAPI()
    install_error_handlers(app)

    @app.get("/string-error")
    def _string_error() -> None:
        raise FastAPIHTTPException(status_code=418, detail="i am a teapot")

    client = TestClient(app)
    response = client.get("/string-error")

    assert response.status_code == 418
    # Default FastAPI behaviour: ``{"detail": "i am a teapot"}`` — unchanged.
    assert response.json() == {"detail": "i am a teapot"}
