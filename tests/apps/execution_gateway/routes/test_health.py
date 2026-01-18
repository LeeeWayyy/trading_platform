"""Tests for health check endpoints in execution_gateway/routes/health.py.

This module tests:
- Root endpoint (/)
- Health check endpoint (/health)
- Various health status scenarios (healthy, degraded, unhealthy)
- Alpaca connection checks in dry-run vs non-dry-run modes
- Recovery manager state handling
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.execution_gateway import main
from apps.execution_gateway.alpaca_client import (
    AlpacaConnectionError,
    AlpacaRejectionError,
    AlpacaValidationError,
)
from apps.execution_gateway.app_context import AppContext
from apps.execution_gateway.app_factory import create_mock_context, create_test_config
from apps.execution_gateway.config import ExecutionGatewayConfig
from apps.execution_gateway.dependencies import get_config, get_context, get_metrics, get_version
from apps.execution_gateway.fat_finger_validator import FatFingerValidator
from apps.execution_gateway.routes import health
from apps.execution_gateway.schemas import FatFingerThresholds, HealthResponse
from libs.trading.risk_management import RiskConfig


class TestRootEndpoint:
    """Tests for the root endpoint (/)."""

    def test_root_returns_service_info(self) -> None:
        """Root endpoint returns basic service information."""
        app = FastAPI()
        app.include_router(health.router)

        # Create test config
        test_config = create_test_config(dry_run=True)

        # Override dependencies
        app.dependency_overrides[get_version] = lambda: "1.0.0"
        app.dependency_overrides[get_config] = lambda: test_config

        client = TestClient(app)
        response = client.get("/")

        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "execution_gateway"
        assert data["version"] == "1.0.0"
        assert data["status"] == "running"
        assert data["dry_run"] is True

    def test_root_with_dry_run_false(self) -> None:
        """Root endpoint shows dry_run=false when not in dry-run mode."""
        app = FastAPI()
        app.include_router(health.router)

        test_config = create_test_config(dry_run=False)

        app.dependency_overrides[get_version] = lambda: "2.0.0"
        app.dependency_overrides[get_config] = lambda: test_config

        client = TestClient(app)
        response = client.get("/")

        assert response.status_code == 200
        data = response.json()
        assert data["dry_run"] is False
        assert data["version"] == "2.0.0"


class TestHealthCheckEndpoint:
    """Tests for the health check endpoint (/health)."""

    @pytest.fixture
    def mock_metrics(self) -> dict[str, Any]:
        """Create mock Prometheus metrics."""
        return {
            "database_connection_status": MagicMock(),
            "redis_connection_status": MagicMock(),
            "alpaca_connection_status": MagicMock(),
            "alpaca_api_requests_total": MagicMock(),
        }

    @pytest.fixture
    def mock_recovery_manager(self) -> MagicMock:
        """Create mock recovery manager."""
        manager = MagicMock()
        manager.needs_recovery.return_value = False
        manager.kill_switch = MagicMock()
        manager.circuit_breaker = MagicMock()
        manager.position_reservation = MagicMock()
        return manager

    @pytest.fixture
    def mock_db(self) -> MagicMock:
        """Create mock database client."""
        db = MagicMock()
        db.check_connection.return_value = True
        return db

    @pytest.fixture
    def mock_redis(self) -> MagicMock:
        """Create mock Redis client."""
        redis_client = MagicMock()
        redis_client.health_check.return_value = True
        return redis_client

    @pytest.fixture
    def mock_alpaca(self) -> MagicMock:
        """Create mock Alpaca client."""
        alpaca = MagicMock()
        alpaca.check_connection.return_value = True
        return alpaca

    def test_health_check_healthy_dry_run_mode(
        self,
        mock_metrics: dict[str, Any],
        mock_recovery_manager: MagicMock,
        mock_db: MagicMock,
        mock_redis: MagicMock,
    ) -> None:
        """Health check returns healthy status in dry-run mode."""
        app = FastAPI()
        app.include_router(health.router)

        test_config = create_test_config(dry_run=True, strategy_id="test_strategy")
        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            redis=mock_redis,
            alpaca=None,  # No Alpaca in dry-run
            recovery_manager=mock_recovery_manager,
            risk_config=RiskConfig(),
            fat_finger_validator=fat_finger_validator,
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[get_config] = lambda: test_config
        app.dependency_overrides[get_version] = lambda: "1.0.0"
        app.dependency_overrides[get_metrics] = lambda: mock_metrics

        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "execution_gateway"
        assert data["version"] == "1.0.0"
        assert data["dry_run"] is True
        assert data["database_connected"] is True
        assert data["alpaca_connected"] is True  # Always true in dry-run
        assert data["details"]["strategy_id"] == "test_strategy"
        assert data["details"]["alpaca_base_url"] is None  # Not shown in dry-run

    def test_health_check_healthy_live_mode(
        self,
        mock_metrics: dict[str, Any],
        mock_recovery_manager: MagicMock,
        mock_db: MagicMock,
        mock_redis: MagicMock,
        mock_alpaca: MagicMock,
    ) -> None:
        """Health check returns healthy status in live mode with all connections up."""
        app = FastAPI()
        app.include_router(health.router)

        test_config = create_test_config(
            dry_run=False,
            strategy_id="live_strategy",
            alpaca_base_url="https://api.alpaca.markets",
        )
        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            redis=mock_redis,
            alpaca=mock_alpaca,
            recovery_manager=mock_recovery_manager,
            risk_config=RiskConfig(),
            fat_finger_validator=fat_finger_validator,
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[get_config] = lambda: test_config
        app.dependency_overrides[get_version] = lambda: "2.0.0"
        app.dependency_overrides[get_metrics] = lambda: mock_metrics

        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["dry_run"] is False
        assert data["database_connected"] is True
        assert data["alpaca_connected"] is True
        assert data["details"]["alpaca_base_url"] == "https://api.alpaca.markets"

        # Verify Alpaca connection was checked
        mock_alpaca.check_connection.assert_called_once()

    def test_health_check_degraded_alpaca_down(
        self,
        mock_metrics: dict[str, Any],
        mock_recovery_manager: MagicMock,
        mock_db: MagicMock,
        mock_redis: MagicMock,
        mock_alpaca: MagicMock,
    ) -> None:
        """Health check returns degraded status when Alpaca is down but DB is up."""
        app = FastAPI()
        app.include_router(health.router)

        # Alpaca connection fails
        mock_alpaca.check_connection.return_value = False

        test_config = create_test_config(dry_run=False)
        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            redis=mock_redis,
            alpaca=mock_alpaca,
            recovery_manager=mock_recovery_manager,
            risk_config=RiskConfig(),
            fat_finger_validator=fat_finger_validator,
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[get_config] = lambda: test_config
        app.dependency_overrides[get_version] = lambda: "1.0.0"
        app.dependency_overrides[get_metrics] = lambda: mock_metrics

        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["database_connected"] is True
        assert data["alpaca_connected"] is False

    def test_health_check_unhealthy_db_down(
        self,
        mock_metrics: dict[str, Any],
        mock_recovery_manager: MagicMock,
        mock_db: MagicMock,
        mock_redis: MagicMock,
        mock_alpaca: MagicMock,
    ) -> None:
        """Health check returns unhealthy status when database is down."""
        app = FastAPI()
        app.include_router(health.router)

        # Database connection fails
        mock_db.check_connection.return_value = False

        test_config = create_test_config(dry_run=False)
        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            redis=mock_redis,
            alpaca=mock_alpaca,
            recovery_manager=mock_recovery_manager,
            risk_config=RiskConfig(),
            fat_finger_validator=fat_finger_validator,
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[get_config] = lambda: test_config
        app.dependency_overrides[get_version] = lambda: "1.0.0"
        app.dependency_overrides[get_metrics] = lambda: mock_metrics

        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["database_connected"] is False

    def test_health_check_degraded_recovery_needed(
        self,
        mock_metrics: dict[str, Any],
        mock_recovery_manager: MagicMock,
        mock_db: MagicMock,
        mock_redis: MagicMock,
    ) -> None:
        """Health check returns degraded status when recovery manager needs recovery."""
        app = FastAPI()
        app.include_router(health.router)

        # Recovery manager needs recovery
        mock_recovery_manager.needs_recovery.return_value = True

        test_config = create_test_config(dry_run=True)
        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            redis=mock_redis,
            alpaca=None,
            recovery_manager=mock_recovery_manager,
            risk_config=RiskConfig(),
            fat_finger_validator=fat_finger_validator,
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[get_config] = lambda: test_config
        app.dependency_overrides[get_version] = lambda: "1.0.0"
        app.dependency_overrides[get_metrics] = lambda: mock_metrics

        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"

    def test_health_check_redis_unavailable(
        self,
        mock_metrics: dict[str, Any],
        mock_recovery_manager: MagicMock,
        mock_db: MagicMock,
    ) -> None:
        """Health check handles Redis being unavailable."""
        app = FastAPI()
        app.include_router(health.router)

        test_config = create_test_config(dry_run=True)
        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            redis=None,  # Redis unavailable
            alpaca=None,
            recovery_manager=mock_recovery_manager,
            risk_config=RiskConfig(),
            fat_finger_validator=fat_finger_validator,
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[get_config] = lambda: test_config
        app.dependency_overrides[get_version] = lambda: "1.0.0"
        app.dependency_overrides[get_metrics] = lambda: mock_metrics

        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        # Status depends on DB and dry_run settings; Redis being None doesn't cause unhealthy
        assert data["database_connected"] is True

    def test_health_check_redis_health_check_fails(
        self,
        mock_metrics: dict[str, Any],
        mock_recovery_manager: MagicMock,
        mock_db: MagicMock,
        mock_redis: MagicMock,
    ) -> None:
        """Health check handles Redis health check returning False."""
        app = FastAPI()
        app.include_router(health.router)

        # Redis health check fails
        mock_redis.health_check.return_value = False

        test_config = create_test_config(dry_run=True)
        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            redis=mock_redis,
            alpaca=None,
            recovery_manager=mock_recovery_manager,
            risk_config=RiskConfig(),
            fat_finger_validator=fat_finger_validator,
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[get_config] = lambda: test_config
        app.dependency_overrides[get_version] = lambda: "1.0.0"
        app.dependency_overrides[get_metrics] = lambda: mock_metrics

        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        # Recovery attempt should NOT be called when Redis health check fails
        mock_recovery_manager.attempt_recovery.assert_not_called()


class TestAlpacaConnectionErrors:
    """Tests for Alpaca connection error handling in health check."""

    @pytest.fixture
    def mock_metrics(self) -> dict[str, Any]:
        """Create mock Prometheus metrics."""
        return {
            "database_connection_status": MagicMock(),
            "redis_connection_status": MagicMock(),
            "alpaca_connection_status": MagicMock(),
            "alpaca_api_requests_total": MagicMock(),
        }

    @pytest.fixture
    def mock_recovery_manager(self) -> MagicMock:
        """Create mock recovery manager."""
        manager = MagicMock()
        manager.needs_recovery.return_value = False
        return manager

    @pytest.fixture
    def mock_db(self) -> MagicMock:
        """Create mock database client."""
        db = MagicMock()
        db.check_connection.return_value = True
        return db

    @pytest.fixture
    def mock_redis(self) -> MagicMock:
        """Create mock Redis client."""
        redis_client = MagicMock()
        redis_client.health_check.return_value = True
        return redis_client

    def test_alpaca_connection_error_is_handled(
        self,
        mock_metrics: dict[str, Any],
        mock_recovery_manager: MagicMock,
        mock_db: MagicMock,
        mock_redis: MagicMock,
    ) -> None:
        """Health check handles AlpacaConnectionError gracefully."""
        app = FastAPI()
        app.include_router(health.router)

        mock_alpaca = MagicMock()
        mock_alpaca.check_connection.side_effect = AlpacaConnectionError("Connection failed")

        test_config = create_test_config(dry_run=False)
        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            redis=mock_redis,
            alpaca=mock_alpaca,
            recovery_manager=mock_recovery_manager,
            risk_config=RiskConfig(),
            fat_finger_validator=fat_finger_validator,
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[get_config] = lambda: test_config
        app.dependency_overrides[get_version] = lambda: "1.0.0"
        app.dependency_overrides[get_metrics] = lambda: mock_metrics

        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["alpaca_connected"] is False

        # Verify metric was tracked with error status
        mock_metrics["alpaca_api_requests_total"].labels.assert_called_with(
            operation="check_connection", status="error"
        )

    def test_alpaca_validation_error_is_handled(
        self,
        mock_metrics: dict[str, Any],
        mock_recovery_manager: MagicMock,
        mock_db: MagicMock,
        mock_redis: MagicMock,
    ) -> None:
        """Health check handles AlpacaValidationError gracefully."""
        app = FastAPI()
        app.include_router(health.router)

        mock_alpaca = MagicMock()
        mock_alpaca.check_connection.side_effect = AlpacaValidationError("Validation failed")

        test_config = create_test_config(dry_run=False)
        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            redis=mock_redis,
            alpaca=mock_alpaca,
            recovery_manager=mock_recovery_manager,
            risk_config=RiskConfig(),
            fat_finger_validator=fat_finger_validator,
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[get_config] = lambda: test_config
        app.dependency_overrides[get_version] = lambda: "1.0.0"
        app.dependency_overrides[get_metrics] = lambda: mock_metrics

        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["alpaca_connected"] is False

    def test_alpaca_rejection_error_is_handled(
        self,
        mock_metrics: dict[str, Any],
        mock_recovery_manager: MagicMock,
        mock_db: MagicMock,
        mock_redis: MagicMock,
    ) -> None:
        """Health check handles AlpacaRejectionError gracefully."""
        app = FastAPI()
        app.include_router(health.router)

        mock_alpaca = MagicMock()
        mock_alpaca.check_connection.side_effect = AlpacaRejectionError("Rejected")

        test_config = create_test_config(dry_run=False)
        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            redis=mock_redis,
            alpaca=mock_alpaca,
            recovery_manager=mock_recovery_manager,
            risk_config=RiskConfig(),
            fat_finger_validator=fat_finger_validator,
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[get_config] = lambda: test_config
        app.dependency_overrides[get_version] = lambda: "1.0.0"
        app.dependency_overrides[get_metrics] = lambda: mock_metrics

        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["alpaca_connected"] is False

    def test_alpaca_os_error_is_handled(
        self,
        mock_metrics: dict[str, Any],
        mock_recovery_manager: MagicMock,
        mock_db: MagicMock,
        mock_redis: MagicMock,
    ) -> None:
        """Health check handles OSError (network/IO) gracefully."""
        app = FastAPI()
        app.include_router(health.router)

        mock_alpaca = MagicMock()
        mock_alpaca.check_connection.side_effect = OSError("Network error")

        test_config = create_test_config(dry_run=False)
        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            redis=mock_redis,
            alpaca=mock_alpaca,
            recovery_manager=mock_recovery_manager,
            risk_config=RiskConfig(),
            fat_finger_validator=fat_finger_validator,
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[get_config] = lambda: test_config
        app.dependency_overrides[get_version] = lambda: "1.0.0"
        app.dependency_overrides[get_metrics] = lambda: mock_metrics

        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["alpaca_connected"] is False

    def test_alpaca_success_metrics_tracked(
        self,
        mock_metrics: dict[str, Any],
        mock_recovery_manager: MagicMock,
        mock_db: MagicMock,
        mock_redis: MagicMock,
    ) -> None:
        """Health check tracks successful Alpaca connection metric."""
        app = FastAPI()
        app.include_router(health.router)

        mock_alpaca = MagicMock()
        mock_alpaca.check_connection.return_value = True

        test_config = create_test_config(dry_run=False)
        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            redis=mock_redis,
            alpaca=mock_alpaca,
            recovery_manager=mock_recovery_manager,
            risk_config=RiskConfig(),
            fat_finger_validator=fat_finger_validator,
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[get_config] = lambda: test_config
        app.dependency_overrides[get_version] = lambda: "1.0.0"
        app.dependency_overrides[get_metrics] = lambda: mock_metrics

        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200

        # Verify success metric was tracked
        mock_metrics["alpaca_api_requests_total"].labels.assert_called_with(
            operation="check_connection", status="success"
        )


class TestHealthResponseModel:
    """Tests for HealthResponse schema validation."""

    def test_health_response_serializes_timestamp(self) -> None:
        """HealthResponse serializes timestamp correctly."""
        now = datetime.now(UTC)
        response = HealthResponse(
            status="healthy",
            service="execution_gateway",
            version="1.0.0",
            dry_run=True,
            database_connected=True,
            alpaca_connected=True,
            timestamp=now,
        )

        # TimestampSerializerMixin converts timestamp to string by default
        data = response.model_dump()
        assert data["status"] == "healthy"
        # The timestamp is serialized to ISO format string with Z suffix
        assert isinstance(data["timestamp"], str)
        assert data["timestamp"].endswith("Z")

        # Test JSON serialization format
        json_data = response.model_dump(mode="json")
        assert "timestamp" in json_data
        assert isinstance(json_data["timestamp"], str)
        assert json_data["timestamp"].endswith("Z")

    def test_health_response_includes_details(self) -> None:
        """HealthResponse includes optional details."""
        response = HealthResponse(
            status="healthy",
            service="execution_gateway",
            version="1.0.0",
            dry_run=True,
            database_connected=True,
            alpaca_connected=True,
            timestamp=datetime.now(UTC),
            details={"strategy_id": "test", "custom": "value"},
        )

        data = response.model_dump()
        assert data["details"]["strategy_id"] == "test"
        assert data["details"]["custom"] == "value"
