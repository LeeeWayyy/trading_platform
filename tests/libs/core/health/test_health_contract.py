"""Contract tests for /health endpoint schema stability."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError


class TolerantHealthResponse(BaseModel):
    """Base health response with tolerant parsing (allows extra fields)."""

    model_config = ConfigDict(extra="allow")

    status: str
    service: str


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(service: str) -> dict[str, Any]:
    """Load fixture from file, failing hard if missing."""
    fixture_path = FIXTURES_DIR / f"{service}_health.json"
    if not fixture_path.exists():
        raise FileNotFoundError(f"Missing fixture for {service}: {fixture_path}")
    return json.loads(fixture_path.read_text())


class TestHealthBaseContract:
    """Tests for base health response contract."""

    def test_base_contract_requires_status(self) -> None:
        with pytest.raises(ValidationError):
            TolerantHealthResponse.model_validate({"service": "test"})

    def test_base_contract_requires_service(self) -> None:
        with pytest.raises(ValidationError):
            TolerantHealthResponse.model_validate({"status": "healthy"})

    def test_extra_fields_allowed(self) -> None:
        data = {
            "status": "healthy",
            "service": "test",
            "extra_field": True,
            "nested": {"key": "value"},
        }
        response = TolerantHealthResponse.model_validate(data)
        assert response.status == "healthy"
        assert response.service == "test"

    def test_valid_status_values(self) -> None:
        service_statuses = ["healthy", "degraded", "unhealthy"]
        client_statuses = ["stale", "unreachable", "unknown"]
        for status in service_statuses + client_statuses:
            response = TolerantHealthResponse.model_validate({"status": status, "service": "test"})
            assert response.status == status


class TestServiceSpecificContracts:
    """Per-service contract expectations."""

    def test_orchestrator_has_dependency_fields(self) -> None:
        data = {
            "status": "healthy",
            "service": "orchestrator",
            "database_connected": True,
            "signal_service_healthy": True,
            "execution_gateway_healthy": True,
            "timestamp": "2025-12-19T00:00:00Z",
        }
        response = TolerantHealthResponse.model_validate(data)
        assert response.model_extra.get("database_connected") is True
        assert response.model_extra.get("signal_service_healthy") is True
        assert response.model_extra.get("execution_gateway_healthy") is True

    def test_signal_service_has_model_status(self) -> None:
        data = {
            "status": "healthy",
            "service": "signal_service",
            "model_loaded": True,
            "redis_status": "connected",
            "timestamp": "2025-12-19T00:00:00Z",
        }
        response = TolerantHealthResponse.model_validate(data)
        assert response.model_extra.get("model_loaded") is True
        assert response.model_extra.get("redis_status") == "connected"

    def test_execution_gateway_has_broker_status(self) -> None:
        data = {
            "status": "healthy",
            "service": "execution_gateway",
            "database_connected": True,
            "alpaca_connected": True,
            "timestamp": "2025-12-19T00:00:00Z",
        }
        response = TolerantHealthResponse.model_validate(data)
        assert response.model_extra.get("alpaca_connected") is True
        assert response.model_extra.get("database_connected") is True


class TestFixtureRegression:
    """Regression tests against captured fixtures."""

    @pytest.mark.parametrize(
        "service",
        [
            "orchestrator",
            "signal_service",
            "execution_gateway",
            "market_data_service",
            "model_registry",
            "reconciler",
            "risk_manager",
            "web_console",
        ],
    )
    def test_fixture_parses_without_error(self, service: str) -> None:
        fixture = load_fixture(service)
        response = TolerantHealthResponse.model_validate(fixture)
        assert response.status in ("healthy", "degraded", "unhealthy")
        assert response.service == service or service in fixture.get("service", "")


def test_all_8_fixtures_exist() -> None:
    services = [
        "orchestrator",
        "signal_service",
        "execution_gateway",
        "market_data_service",
        "model_registry",
        "reconciler",
        "risk_manager",
        "web_console",
    ]
    missing = [
        service for service in services if not (FIXTURES_DIR / f"{service}_health.json").exists()
    ]
    assert not missing, f"Missing fixture files for: {missing}"
