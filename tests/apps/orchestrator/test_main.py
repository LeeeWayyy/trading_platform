"""
Unit tests for Orchestrator Service FastAPI endpoints.

Tests cover:
- Root endpoint
- Health check endpoint (healthy/degraded/unhealthy states)
- Run orchestration endpoint
  - Successful runs
  - Invalid date formats
  - Database persistence
- List runs endpoint
- Get run details endpoint (found/not found)
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from apps.orchestrator.schemas import (
    OrchestrationResult,
    OrchestrationRunSummary,
    SignalOrderMapping,
)


@pytest.fixture
def test_client():
    """Create FastAPI test client."""
    from apps.orchestrator.main import app

    return TestClient(app)


@pytest.fixture
def mock_db():
    """Create mock database client."""
    return Mock()


@pytest.fixture
def mock_orchestrator():
    """Create mock TradingOrchestrator."""
    mock = Mock()
    mock.signal_client = Mock()
    mock.execution_client = Mock()
    # Mock async methods
    mock.signal_client.health_check = AsyncMock()
    mock.execution_client.health_check = AsyncMock()
    mock.run = AsyncMock()
    mock.close = AsyncMock()
    return mock


class TestRootEndpoint:
    """Tests for root endpoint."""

    def test_root_returns_service_info(self, test_client):
        """Test root endpoint returns service information."""
        response = test_client.get("/")

        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "orchestrator"
        assert "version" in data
        assert "status" in data
        assert "signal_service" in data
        assert "execution_gateway" in data


class TestHealthCheckEndpoint:
    """Tests for health check endpoint."""

    def test_health_check_healthy(self, test_client, mock_db, mock_orchestrator):
        """Test health check returns healthy when all services are up."""
        # Mock all services as healthy
        mock_db.check_connection.return_value = True
        mock_orchestrator.signal_client.health_check.return_value = True
        mock_orchestrator.execution_client.health_check.return_value = True

        with patch("apps.orchestrator.main.db_client", mock_db), patch(
            "apps.orchestrator.main.create_orchestrator", return_value=mock_orchestrator
        ):
            response = test_client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "orchestrator"
        assert data["database_connected"] is True
        assert data["signal_service_healthy"] is True
        assert data["execution_gateway_healthy"] is True

    def test_health_check_degraded(self, test_client, mock_db, mock_orchestrator):
        """Test health check returns degraded when DB is up but services are down."""
        # DB is up, but services are down
        mock_db.check_connection.return_value = True
        mock_orchestrator.signal_client.health_check.return_value = False
        mock_orchestrator.execution_client.health_check.return_value = False

        with patch("apps.orchestrator.main.db_client", mock_db), patch(
            "apps.orchestrator.main.create_orchestrator", return_value=mock_orchestrator
        ):
            response = test_client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["database_connected"] is True
        assert data["signal_service_healthy"] is False
        assert data["execution_gateway_healthy"] is False

    def test_health_check_unhealthy(self, test_client, mock_db, mock_orchestrator):
        """Test health check returns unhealthy when database is down."""
        # DB is down
        mock_db.check_connection.return_value = False
        mock_orchestrator.signal_client.health_check.return_value = True
        mock_orchestrator.execution_client.health_check.return_value = True

        with patch("apps.orchestrator.main.db_client", mock_db), patch(
            "apps.orchestrator.main.create_orchestrator", return_value=mock_orchestrator
        ):
            response = test_client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["database_connected"] is False


class TestRunOrchestrationEndpoint:
    """Tests for run orchestration endpoint."""

    def test_run_orchestration_success(self, test_client, mock_db, mock_orchestrator):
        """Test successful orchestration run."""
        # Mock orchestration result
        run_result = OrchestrationResult(
            run_id=uuid4(),
            status="completed",
            strategy_id="alpha_baseline",
            as_of_date="2024-10-19",
            symbols=["AAPL", "MSFT"],
            capital=Decimal("100000"),
            num_signals=2,
            num_orders_submitted=2,
            num_orders_accepted=2,
            num_orders_rejected=0,
            mappings=[],
            started_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=timezone.utc),
            completed_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=timezone.utc),
            duration_seconds=Decimal("1.5"),
        )
        mock_orchestrator.run.return_value = run_result

        with patch("apps.orchestrator.main.db_client", mock_db), patch(
            "apps.orchestrator.main.TradingOrchestrator", return_value=mock_orchestrator
        ):
            response = test_client.post(
                "/api/v1/orchestration/run",
                json={"symbols": ["AAPL", "MSFT"], "as_of_date": "2024-10-19"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["num_signals"] == 2
        assert data["num_orders_submitted"] == 2
        assert data["num_orders_accepted"] == 2

        # Verify database persistence was called
        mock_db.create_run.assert_called_once_with(run_result)

        # Verify orchestrator was closed
        mock_orchestrator.close.assert_called_once()

    def test_run_orchestration_invalid_date_format(self, test_client):
        """Test orchestration run with invalid date format returns 400."""
        response = test_client.post(
            "/api/v1/orchestration/run",
            json={"symbols": ["AAPL"], "as_of_date": "invalid-date"},
        )

        assert response.status_code == 400
        assert "Invalid date format" in response.json()["detail"]

    def test_run_orchestration_with_custom_capital(self, test_client, mock_db, mock_orchestrator):
        """Test orchestration run with custom capital and max_position_size."""
        run_result = OrchestrationResult(
            run_id=uuid4(),
            status="completed",
            strategy_id="alpha_baseline",
            as_of_date="2024-10-19",
            symbols=["AAPL"],
            capital=Decimal("200000"),
            num_signals=1,
            num_orders_submitted=1,
            num_orders_accepted=1,
            num_orders_rejected=0,
            mappings=[],
            started_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=timezone.utc),
            completed_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=timezone.utc),
            duration_seconds=Decimal("1.0"),
        )
        mock_orchestrator.run.return_value = run_result

        with patch("apps.orchestrator.main.db_client", mock_db), patch(
            "apps.orchestrator.main.TradingOrchestrator", return_value=mock_orchestrator
        ) as mock_orch_class:
            response = test_client.post(
                "/api/v1/orchestration/run",
                json={
                    "symbols": ["AAPL"],
                    "capital": 200000,
                    "max_position_size": 50000,
                },
            )

        assert response.status_code == 200

        # Verify TradingOrchestrator was created with custom parameters
        mock_orch_class.assert_called_once()
        call_kwargs = mock_orch_class.call_args[1]
        assert call_kwargs["capital"] == Decimal("200000")
        assert call_kwargs["max_position_size"] == Decimal("50000")

    def test_run_orchestration_without_date(self, test_client, mock_db, mock_orchestrator):
        """Test orchestration run without as_of_date (defaults to today)."""
        run_result = OrchestrationResult(
            run_id=uuid4(),
            status="completed",
            strategy_id="alpha_baseline",
            as_of_date="2025-10-19",
            symbols=["AAPL"],
            capital=Decimal("100000"),
            num_signals=1,
            num_orders_submitted=1,
            num_orders_accepted=1,
            num_orders_rejected=0,
            mappings=[],
            started_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=timezone.utc),
            completed_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=timezone.utc),
            duration_seconds=Decimal("1.0"),
        )
        mock_orchestrator.run.return_value = run_result

        with patch("apps.orchestrator.main.db_client", mock_db), patch(
            "apps.orchestrator.main.TradingOrchestrator", return_value=mock_orchestrator
        ):
            response = test_client.post(
                "/api/v1/orchestration/run",
                json={"symbols": ["AAPL"]},
            )

        assert response.status_code == 200

        # Verify run was called with None for as_of_date
        mock_orchestrator.run.assert_called_once()
        call_kwargs = mock_orchestrator.run.call_args[1]
        assert call_kwargs["as_of_date"] is None


class TestListRunsEndpoint:
    """Tests for list runs endpoint."""

    def test_list_runs_returns_list(self, test_client, mock_db):
        """Test listing runs returns paginated list."""
        # Mock runs
        runs = [
            OrchestrationRunSummary(
                run_id=uuid4(),
                status="completed",
                strategy_id="alpha_baseline",
                as_of_date="2024-10-19",
                num_signals=2,
                num_orders_submitted=2,
                num_orders_accepted=2,
                num_orders_rejected=0,
                started_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=timezone.utc),
                completed_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=timezone.utc),
                duration_seconds=Decimal("1.5"),
            ),
            OrchestrationRunSummary(
                run_id=uuid4(),
                status="failed",
                strategy_id="alpha_baseline",
                as_of_date="2024-10-18",
                num_signals=1,
                num_orders_submitted=0,
                num_orders_accepted=0,
                num_orders_rejected=1,
                started_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=timezone.utc),
                completed_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=timezone.utc),
                duration_seconds=Decimal("0.5"),
            ),
        ]
        mock_db.list_runs.return_value = runs

        with patch("apps.orchestrator.main.db_client", mock_db):
            response = test_client.get("/api/v1/orchestration/runs")

        assert response.status_code == 200
        data = response.json()
        assert len(data["runs"]) == 2
        assert data["total"] == 2
        assert data["limit"] == 50
        assert data["offset"] == 0

    def test_list_runs_with_filters(self, test_client, mock_db):
        """Test listing runs with filters."""
        runs = [
            OrchestrationRunSummary(
                run_id=uuid4(),
                status="completed",
                strategy_id="alpha_baseline",
                as_of_date="2024-10-19",
                num_signals=2,
                num_orders_submitted=2,
                num_orders_accepted=2,
                num_orders_rejected=0,
                started_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=timezone.utc),
                completed_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=timezone.utc),
                duration_seconds=Decimal("1.5"),
            ),
        ]
        mock_db.list_runs.return_value = runs

        with patch("apps.orchestrator.main.db_client", mock_db):
            response = test_client.get(
                "/api/v1/orchestration/runs",
                params={
                    "limit": 10,
                    "offset": 5,
                    "strategy_id": "alpha_baseline",
                    "status": "completed",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["limit"] == 10
        assert data["offset"] == 5

        # Verify filters were passed to database
        mock_db.list_runs.assert_called_once_with(
            limit=10, offset=5, strategy_id="alpha_baseline", status="completed"
        )

    def test_list_runs_empty(self, test_client, mock_db):
        """Test listing runs when no runs exist."""
        mock_db.list_runs.return_value = []

        with patch("apps.orchestrator.main.db_client", mock_db):
            response = test_client.get("/api/v1/orchestration/runs")

        assert response.status_code == 200
        data = response.json()
        assert len(data["runs"]) == 0
        assert data["total"] == 0


class TestGetRunEndpoint:
    """Tests for get run details endpoint."""

    def test_get_run_found(self, test_client, mock_db):
        """Test getting run details returns full result."""
        run_id = uuid4()
        run_summary = OrchestrationRunSummary(
            run_id=run_id,
            status="completed",
            strategy_id="alpha_baseline",
            as_of_date="2024-10-19",
            num_signals=2,
            num_orders_submitted=2,
            num_orders_accepted=2,
            num_orders_rejected=0,
            started_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=timezone.utc),
            completed_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=timezone.utc),
            duration_seconds=Decimal("1.5"),
        )
        mappings = [
            SignalOrderMapping(
                symbol="AAPL",
                predicted_return=0.05,
                rank=1,
                target_weight=0.10,
                client_order_id="order1",
                order_qty=100,
                order_side="buy",
                order_status="filled",
            ),
        ]

        mock_db.get_run.return_value = run_summary
        mock_db.get_mappings.return_value = mappings

        with patch("apps.orchestrator.main.db_client", mock_db):
            response = test_client.get(f"/api/v1/orchestration/runs/{run_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == str(run_id)
        assert data["status"] == "completed"
        assert len(data["mappings"]) == 1
        assert data["mappings"][0]["symbol"] == "AAPL"

    def test_get_run_not_found(self, test_client, mock_db):
        """Test getting non-existent run returns 404."""
        run_id = uuid4()
        mock_db.get_run.return_value = None

        with patch("apps.orchestrator.main.db_client", mock_db):
            response = test_client.get(f"/api/v1/orchestration/runs/{run_id}")

        assert response.status_code == 404
        assert "Run not found" in response.json()["detail"]

    def test_get_run_invalid_uuid(self, test_client):
        """Test getting run with invalid UUID returns 422."""
        response = test_client.get("/api/v1/orchestration/runs/invalid-uuid")

        assert response.status_code == 422  # Validation error
