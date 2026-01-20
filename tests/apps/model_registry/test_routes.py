from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from apps.model_registry import routes
from apps.model_registry.auth import ServiceToken
from apps.model_registry.schemas import (
    ERROR_CHECKSUM_MISMATCH,
    ERROR_MODEL_NOT_FOUND,
    ERROR_REGISTRY_LOCKED,
    ERROR_VALIDATION_FAILED,
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
    app.include_router(routes.router)
    app.dependency_overrides[routes.verify_read_scope] = lambda: ServiceToken(
        token="read", scopes=["model:read"], service_name="svc"
    )
    app.dependency_overrides[routes.verify_write_scope] = lambda: ServiceToken(
        token="write", scopes=["model:write"], service_name="svc"
    )
    app.dependency_overrides[routes.get_registry] = lambda: registry
    return TestClient(app)


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


def test_get_current_model_handles_lock_error() -> None:
    registry = MagicMock()
    registry.get_current_production.side_effect = RegistryLockError("locked")
    client = _client_with_registry(registry)

    response = client.get("/api/v1/models/risk_model/current")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == ERROR_REGISTRY_LOCKED


def test_get_current_model_missing_returns_404() -> None:
    registry = MagicMock()
    registry.get_current_production.return_value = None
    client = _client_with_registry(registry)

    response = client.get("/api/v1/models/risk_model/current")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == ERROR_MODEL_NOT_FOUND


def test_get_model_metadata_requires_db_info() -> None:
    registry = MagicMock()
    registry.get_model_metadata.return_value = _build_metadata()
    registry.get_model_info.return_value = None
    client = _client_with_registry(registry)

    response = client.get("/api/v1/models/risk_model/v1.0.0")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == ERROR_CHECKSUM_MISMATCH


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
    assert response.json()["detail"]["code"] == ERROR_MODEL_NOT_FOUND


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
    assert response.json()["detail"]["code"] == ERROR_CHECKSUM_MISMATCH


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
    assert response.json()["detail"]["code"] == ERROR_VALIDATION_FAILED


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
    assert response.json()["detail"]["code"] == ERROR_CHECKSUM_MISMATCH


def test_list_models_handles_integrity_error() -> None:
    registry = MagicMock()
    registry.list_models.side_effect = IntegrityError("broken")
    client = _client_with_registry(registry)

    response = client.get("/api/v1/models/risk_model")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == ERROR_CHECKSUM_MISMATCH
