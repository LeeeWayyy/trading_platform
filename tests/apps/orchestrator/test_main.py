"""
Unit tests for Orchestrator Service FastAPI endpoints.

Tests cover:
- Root endpoint
- Health check endpoint (healthy/degraded/unhealthy states)
- Config endpoint
- Kill-switch endpoints (engage/disengage/status)
- Run orchestration endpoint
  - Successful runs
  - Invalid date formats
  - Database persistence
  - Kill-switch blocking
  - Error handling (ValueError, database errors, general exceptions)
- List runs endpoint
- Get run details endpoint (found/not found)
- Startup/shutdown events
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from apps.orchestrator.schemas import (
    OrchestrationResult,
    OrchestrationRunSummary,
    SignalOrderMapping,
)


@pytest.fixture()
def test_client():
    """Create FastAPI test client."""
    from apps.orchestrator.main import app

    return TestClient(app)


@pytest.fixture()
def mock_db():
    """Create mock database client."""
    return Mock()


@pytest.fixture()
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


@pytest.fixture()
def mock_kill_switch():
    """Create a mock KillSwitch (not engaged, available)."""
    mock_ks = Mock()
    mock_ks.is_engaged.return_value = False
    return mock_ks


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

        with (
            patch("apps.orchestrator.main.db_client", mock_db),
            patch("apps.orchestrator.main.create_orchestrator", return_value=mock_orchestrator),
            patch("apps.orchestrator.main.is_kill_switch_unavailable", return_value=False),
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

        with (
            patch("apps.orchestrator.main.db_client", mock_db),
            patch("apps.orchestrator.main.create_orchestrator", return_value=mock_orchestrator),
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

        with (
            patch("apps.orchestrator.main.db_client", mock_db),
            patch("apps.orchestrator.main.create_orchestrator", return_value=mock_orchestrator),
            patch("apps.orchestrator.main.is_kill_switch_unavailable", return_value=False),
        ):
            response = test_client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["database_connected"] is False

    def test_health_check_degraded_kill_switch_unavailable(
        self, test_client, mock_db, mock_orchestrator
    ):
        """Test health check returns degraded when kill-switch is unavailable (fail-closed)."""
        # All services up but kill-switch unavailable
        mock_db.check_connection.return_value = True
        mock_orchestrator.signal_client.health_check.return_value = True
        mock_orchestrator.execution_client.health_check.return_value = True

        with (
            patch("apps.orchestrator.main.db_client", mock_db),
            patch("apps.orchestrator.main.create_orchestrator", return_value=mock_orchestrator),
            patch("apps.orchestrator.main.is_kill_switch_unavailable", return_value=True),
        ):
            response = test_client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["database_connected"] is True
        assert data["signal_service_healthy"] is True
        assert data["execution_gateway_healthy"] is True


class TestConfigEndpoint:
    """Tests for configuration endpoint."""

    def test_get_config_returns_config(self, test_client):
        """Test config endpoint returns service configuration."""
        response = test_client.get("/api/v1/config")

        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "orchestrator"
        assert "version" in data
        assert "environment" in data
        assert "dry_run" in data
        assert "alpaca_paper" in data
        assert "circuit_breaker_enabled" in data
        assert "timestamp" in data


class TestKillSwitchEndpoints:
    """Tests for kill-switch endpoints."""

    def test_engage_kill_switch_success(self, test_client):
        """Test engaging kill-switch successfully."""
        mock_ks = Mock()
        mock_ks.engage.return_value = None
        mock_ks.get_status.return_value = {
            "engaged": True,
            "engaged_by": "test_operator",
            "engagement_reason": "Test reason",
            "engaged_at": "2024-10-19T12:00:00Z",
        }

        with patch("apps.orchestrator.main.kill_switch", mock_ks):
            response = test_client.post(
                "/api/v1/kill-switch/engage",
                json={"reason": "Test reason", "operator": "test_operator"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["engaged"] is True
        mock_ks.engage.assert_called_once_with(
            reason="Test reason", operator="test_operator", details=None
        )

    def test_engage_kill_switch_already_engaged(self, test_client):
        """Test engaging kill-switch when already engaged returns 400."""
        mock_ks = Mock()
        mock_ks.engage.side_effect = ValueError("Kill-switch already engaged")

        with patch("apps.orchestrator.main.kill_switch", mock_ks):
            response = test_client.post(
                "/api/v1/kill-switch/engage",
                json={"reason": "Test reason", "operator": "test_operator"},
            )

        assert response.status_code == 400
        assert "Kill-switch already engaged" in response.json()["detail"]

    def test_engage_kill_switch_redis_unavailable(self, test_client):
        """Test engaging kill-switch when Redis unavailable returns 503."""
        with patch("apps.orchestrator.main.kill_switch", None):
            response = test_client.post(
                "/api/v1/kill-switch/engage",
                json={"reason": "Test reason", "operator": "test_operator"},
            )

        assert response.status_code == 503
        assert "Kill-switch unavailable" in response.json()["detail"]

    def test_engage_kill_switch_state_missing_runtime_error(self, test_client):
        """Test engage kill-switch when state missing (RuntimeError) returns 503."""
        mock_ks = Mock()
        mock_ks.engage.side_effect = RuntimeError("Kill-switch state missing in Redis")

        with patch("apps.orchestrator.main.kill_switch", mock_ks):
            response = test_client.post(
                "/api/v1/kill-switch/engage",
                json={"reason": "Test reason", "operator": "test_operator"},
            )

        assert response.status_code == 503
        data = response.json()["detail"]
        assert data["error"] == "Kill-switch unavailable"
        assert data["fail_closed"] is True

    def test_disengage_kill_switch_success(self, test_client):
        """Test disengaging kill-switch successfully."""
        mock_ks = Mock()
        mock_ks.disengage.return_value = None
        mock_ks.get_status.return_value = {
            "engaged": False,
            "disengaged_by": "test_operator",
        }

        with patch("apps.orchestrator.main.kill_switch", mock_ks):
            response = test_client.post(
                "/api/v1/kill-switch/disengage",
                json={"operator": "test_operator", "notes": "Resolved"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["engaged"] is False
        mock_ks.disengage.assert_called_once_with(operator="test_operator", notes="Resolved")

    def test_disengage_kill_switch_not_engaged(self, test_client):
        """Test disengaging kill-switch when not engaged returns 400."""
        mock_ks = Mock()
        mock_ks.disengage.side_effect = ValueError("Kill-switch is not engaged")

        with patch("apps.orchestrator.main.kill_switch", mock_ks):
            response = test_client.post(
                "/api/v1/kill-switch/disengage",
                json={"operator": "test_operator"},
            )

        assert response.status_code == 400
        assert "Kill-switch is not engaged" in response.json()["detail"]

    def test_disengage_kill_switch_redis_unavailable(self, test_client):
        """Test disengaging kill-switch when Redis unavailable returns 503."""
        with patch("apps.orchestrator.main.kill_switch", None):
            response = test_client.post(
                "/api/v1/kill-switch/disengage",
                json={"operator": "test_operator"},
            )

        assert response.status_code == 503
        assert "Kill-switch unavailable" in response.json()["detail"]

    def test_disengage_kill_switch_state_missing_runtime_error(self, test_client):
        """Test disengage kill-switch when state missing (RuntimeError) returns 503."""
        mock_ks = Mock()
        mock_ks.disengage.side_effect = RuntimeError("Kill-switch state missing in Redis")

        with patch("apps.orchestrator.main.kill_switch", mock_ks):
            response = test_client.post(
                "/api/v1/kill-switch/disengage",
                json={"operator": "test_operator"},
            )

        assert response.status_code == 503
        data = response.json()["detail"]
        assert data["error"] == "Kill-switch unavailable"
        assert data["fail_closed"] is True

    def test_get_kill_switch_status_success(self, test_client):
        """Test getting kill-switch status successfully."""
        mock_ks = Mock()
        mock_ks.get_status.return_value = {
            "engaged": False,
            "last_engagement": None,
        }

        with patch("apps.orchestrator.main.kill_switch", mock_ks):
            response = test_client.get("/api/v1/kill-switch/status")

        assert response.status_code == 200
        data = response.json()
        assert data["engaged"] is False

    def test_get_kill_switch_status_redis_unavailable(self, test_client):
        """Test getting kill-switch status when Redis unavailable returns 503."""
        with patch("apps.orchestrator.main.kill_switch", None):
            response = test_client.get("/api/v1/kill-switch/status")

        assert response.status_code == 503
        assert "Kill-switch unavailable" in response.json()["detail"]

    def test_get_kill_switch_status_state_missing_runtime_error(self, test_client):
        """Test get kill-switch status when state missing (RuntimeError) returns 503."""
        mock_ks = Mock()
        mock_ks.get_status.side_effect = RuntimeError("Kill-switch state missing in Redis")

        with patch("apps.orchestrator.main.kill_switch", mock_ks):
            response = test_client.get("/api/v1/kill-switch/status")

        assert response.status_code == 503
        data = response.json()["detail"]
        assert data["error"] == "Kill-switch unavailable"
        assert data["fail_closed"] is True


class TestRunOrchestrationEndpoint:
    """Tests for run orchestration endpoint."""

    def test_run_orchestration_success(
        self, test_client, mock_db, mock_orchestrator, mock_kill_switch
    ):
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
            started_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            completed_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            duration_seconds=Decimal("1.5"),
        )
        mock_orchestrator.run.return_value = run_result

        with (
            patch("apps.orchestrator.main.db_client", mock_db),
            patch("apps.orchestrator.main.TradingOrchestrator", return_value=mock_orchestrator),
            patch("apps.orchestrator.main.kill_switch", mock_kill_switch),
            patch("apps.orchestrator.main.is_kill_switch_unavailable", return_value=False),
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

    def test_run_orchestration_invalid_date_format(self, test_client, mock_kill_switch):
        """Test orchestration run with invalid date format returns 400."""
        with (
            patch("apps.orchestrator.main.kill_switch", mock_kill_switch),
            patch("apps.orchestrator.main.is_kill_switch_unavailable", return_value=False),
        ):
            response = test_client.post(
                "/api/v1/orchestration/run",
                json={"symbols": ["AAPL"], "as_of_date": "invalid-date"},
            )

        assert response.status_code == 400
        assert "Invalid date format" in response.json()["detail"]

    def test_run_orchestration_with_custom_capital(
        self, test_client, mock_db, mock_orchestrator, mock_kill_switch
    ):
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
            started_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            completed_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            duration_seconds=Decimal("1.0"),
        )
        mock_orchestrator.run.return_value = run_result

        with (
            patch("apps.orchestrator.main.db_client", mock_db),
            patch(
                "apps.orchestrator.main.TradingOrchestrator", return_value=mock_orchestrator
            ) as mock_orch_class,
            patch("apps.orchestrator.main.kill_switch", mock_kill_switch),
            patch("apps.orchestrator.main.is_kill_switch_unavailable", return_value=False),
        ):
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

    def test_run_orchestration_without_date(
        self, test_client, mock_db, mock_orchestrator, mock_kill_switch
    ):
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
            started_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            completed_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            duration_seconds=Decimal("1.0"),
        )
        mock_orchestrator.run.return_value = run_result

        with (
            patch("apps.orchestrator.main.db_client", mock_db),
            patch("apps.orchestrator.main.TradingOrchestrator", return_value=mock_orchestrator),
            patch("apps.orchestrator.main.kill_switch", mock_kill_switch),
            patch("apps.orchestrator.main.is_kill_switch_unavailable", return_value=False),
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
                started_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
                completed_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
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
                started_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
                completed_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
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
                started_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
                completed_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
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
            started_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            completed_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
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
        # Verify symbols and capital fields (currently hardcoded in endpoint due to schema limitation)
        assert data["symbols"] == []  # Not stored in OrchestrationRunSummary
        assert data["capital"] == "0"  # Not stored in OrchestrationRunSummary

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


class TestOrchestrationKillSwitchBlocking:
    """Tests for orchestration kill-switch blocking behavior."""

    def test_run_orchestration_blocked_by_unavailable_kill_switch(self, test_client):
        """Test orchestration blocked when kill-switch unavailable (fail-closed)."""
        with patch("apps.orchestrator.main.is_kill_switch_unavailable", return_value=True):
            response = test_client.post(
                "/api/v1/orchestration/run",
                json={"symbols": ["AAPL"]},
            )

        assert response.status_code == 503
        data = response.json()["detail"]
        assert data["error"] == "Kill-switch unavailable"
        assert data["fail_closed"] is True

    def test_run_orchestration_blocked_by_engaged_kill_switch(self, test_client):
        """Test orchestration blocked when kill-switch is engaged."""
        mock_ks = Mock()
        mock_ks.is_engaged.return_value = True
        mock_ks.get_status.return_value = {
            "engaged_by": "ops_team",
            "engagement_reason": "Emergency halt",
            "engaged_at": "2024-10-19T12:00:00Z",
        }

        with (
            patch("apps.orchestrator.main.is_kill_switch_unavailable", return_value=False),
            patch("apps.orchestrator.main.kill_switch", mock_ks),
        ):
            response = test_client.post(
                "/api/v1/orchestration/run",
                json={"symbols": ["AAPL"]},
            )

        assert response.status_code == 503
        data = response.json()["detail"]
        assert data["error"] == "Kill-switch engaged"
        assert data["engaged_by"] == "ops_team"
        assert data["reason"] == "Emergency halt"

    def test_run_orchestration_blocked_by_kill_switch_state_missing(
        self, test_client, mock_kill_switch
    ):
        """Test orchestration blocked when kill-switch state missing (RuntimeError)."""
        mock_kill_switch.is_engaged.side_effect = RuntimeError(
            "Kill-switch state missing in Redis"
        )

        with (
            patch("apps.orchestrator.main.is_kill_switch_unavailable", return_value=False),
            patch("apps.orchestrator.main.kill_switch", mock_kill_switch),
        ):
            response = test_client.post(
                "/api/v1/orchestration/run",
                json={"symbols": ["AAPL"]},
            )

        assert response.status_code == 503
        data = response.json()["detail"]
        assert data["error"] == "Kill-switch unavailable"
        assert data["fail_closed"] is True


class TestOrchestrationErrorHandling:
    """Tests for orchestration error handling."""

    def test_run_orchestration_with_rejected_orders(
        self, test_client, mock_db, mock_orchestrator, mock_kill_switch
    ):
        """Test orchestration run with rejected orders increments error counter."""
        run_result = OrchestrationResult(
            run_id=uuid4(),
            status="completed",
            strategy_id="alpha_baseline",
            as_of_date="2024-10-19",
            symbols=["AAPL", "MSFT"],
            capital=Decimal("100000"),
            num_signals=2,
            num_orders_submitted=2,
            num_orders_accepted=1,
            num_orders_rejected=1,  # One order rejected
            mappings=[],
            started_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            completed_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            duration_seconds=Decimal("1.5"),
        )
        mock_orchestrator.run.return_value = run_result

        with (
            patch("apps.orchestrator.main.db_client", mock_db),
            patch("apps.orchestrator.main.TradingOrchestrator", return_value=mock_orchestrator),
            patch("apps.orchestrator.main.kill_switch", mock_kill_switch),
            patch("apps.orchestrator.main.is_kill_switch_unavailable", return_value=False),
        ):
            response = test_client.post(
                "/api/v1/orchestration/run",
                json={"symbols": ["AAPL", "MSFT"]},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["num_orders_rejected"] == 1

    def test_run_orchestration_value_error(
        self, test_client, mock_orchestrator, mock_kill_switch
    ):
        """Test orchestration run with ValueError returns 400."""
        mock_orchestrator.run.side_effect = ValueError("Invalid symbol format")

        with (
            patch("apps.orchestrator.main.TradingOrchestrator", return_value=mock_orchestrator),
            patch("apps.orchestrator.main.kill_switch", mock_kill_switch),
            patch("apps.orchestrator.main.is_kill_switch_unavailable", return_value=False),
        ):
            response = test_client.post(
                "/api/v1/orchestration/run",
                json={"symbols": ["INVALID$SYMBOL"]},
            )

        assert response.status_code == 400
        assert "Invalid symbol format" in response.json()["detail"]

    def test_run_orchestration_database_error(
        self, test_client, mock_db, mock_orchestrator, mock_kill_switch
    ):
        """Test orchestration run with database error returns 500."""
        run_result = OrchestrationResult(
            run_id=uuid4(),
            status="completed",
            strategy_id="alpha_baseline",
            as_of_date="2024-10-19",
            symbols=["AAPL"],
            capital=Decimal("100000"),
            num_signals=1,
            num_orders_submitted=1,
            num_orders_accepted=1,
            num_orders_rejected=0,
            mappings=[],
            started_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            completed_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            duration_seconds=Decimal("1.0"),
        )
        mock_orchestrator.run.return_value = run_result
        mock_db.create_run.side_effect = psycopg.OperationalError("Connection failed")

        with (
            patch("apps.orchestrator.main.db_client", mock_db),
            patch("apps.orchestrator.main.TradingOrchestrator", return_value=mock_orchestrator),
            patch("apps.orchestrator.main.kill_switch", mock_kill_switch),
            patch("apps.orchestrator.main.is_kill_switch_unavailable", return_value=False),
        ):
            response = test_client.post(
                "/api/v1/orchestration/run",
                json={"symbols": ["AAPL"]},
            )

        assert response.status_code == 500
        assert "Database error" in response.json()["detail"]

    def test_run_orchestration_integrity_error(
        self, test_client, mock_db, mock_orchestrator, mock_kill_switch
    ):
        """Test orchestration run with integrity error returns 500."""
        run_result = OrchestrationResult(
            run_id=uuid4(),
            status="completed",
            strategy_id="alpha_baseline",
            as_of_date="2024-10-19",
            symbols=["AAPL"],
            capital=Decimal("100000"),
            num_signals=1,
            num_orders_submitted=1,
            num_orders_accepted=1,
            num_orders_rejected=0,
            mappings=[],
            started_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            completed_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            duration_seconds=Decimal("1.0"),
        )
        mock_orchestrator.run.return_value = run_result
        mock_db.create_run.side_effect = psycopg.IntegrityError("Duplicate key")

        with (
            patch("apps.orchestrator.main.db_client", mock_db),
            patch("apps.orchestrator.main.TradingOrchestrator", return_value=mock_orchestrator),
            patch("apps.orchestrator.main.kill_switch", mock_kill_switch),
            patch("apps.orchestrator.main.is_kill_switch_unavailable", return_value=False),
        ):
            response = test_client.post(
                "/api/v1/orchestration/run",
                json={"symbols": ["AAPL"]},
            )

        assert response.status_code == 500
        assert "Database error" in response.json()["detail"]

    def test_run_orchestration_unexpected_error(
        self, test_client, mock_orchestrator, mock_kill_switch
    ):
        """Test orchestration run with unexpected error returns 500."""
        mock_orchestrator.run.side_effect = RuntimeError("Unexpected failure")

        with (
            patch("apps.orchestrator.main.TradingOrchestrator", return_value=mock_orchestrator),
            patch("apps.orchestrator.main.kill_switch", mock_kill_switch),
            patch("apps.orchestrator.main.is_kill_switch_unavailable", return_value=False),
        ):
            response = test_client.post(
                "/api/v1/orchestration/run",
                json={"symbols": ["AAPL"]},
            )

        assert response.status_code == 500
        assert "Internal server error" in response.json()["detail"]


class TestStartupShutdownEvents:
    """Tests for application startup and shutdown events."""

    def test_startup_event_db_connected(self, mock_db):
        """Test startup event logs success when database is connected."""
        mock_db.check_connection.return_value = True

        with patch("apps.orchestrator.main.db_client", mock_db):
            # Import the startup function directly
            import asyncio

            from apps.orchestrator.main import startup_event

            asyncio.get_event_loop().run_until_complete(startup_event())

        mock_db.check_connection.assert_called_once()

    def test_startup_event_db_disconnected(self, mock_db):
        """Test startup event logs error when database is disconnected."""
        mock_db.check_connection.return_value = False

        with patch("apps.orchestrator.main.db_client", mock_db):
            import asyncio

            from apps.orchestrator.main import startup_event

            asyncio.get_event_loop().run_until_complete(startup_event())

        mock_db.check_connection.assert_called_once()

    def test_shutdown_event(self, mock_db):
        """Test shutdown event closes database connection."""
        mock_db.close.return_value = None

        with patch("apps.orchestrator.main.db_client", mock_db):
            import asyncio

            from apps.orchestrator.main import shutdown_event

            asyncio.get_event_loop().run_until_complete(shutdown_event())

        mock_db.close.assert_called_once()


class TestKillSwitchThreadSafety:
    """Tests for kill-switch thread-safety functions."""

    def test_is_kill_switch_unavailable_false(self):
        """Test is_kill_switch_unavailable returns False when not set."""
        from apps.orchestrator.main import is_kill_switch_unavailable, set_kill_switch_unavailable

        # Reset to known state
        set_kill_switch_unavailable(False)

        assert is_kill_switch_unavailable() is False

    def test_set_kill_switch_unavailable_true(self):
        """Test set_kill_switch_unavailable sets value to True."""
        from apps.orchestrator.main import is_kill_switch_unavailable, set_kill_switch_unavailable

        set_kill_switch_unavailable(True)

        assert is_kill_switch_unavailable() is True

        # Reset after test
        set_kill_switch_unavailable(False)

    def test_set_kill_switch_unavailable_toggle(self):
        """Test set_kill_switch_unavailable can toggle value."""
        from apps.orchestrator.main import is_kill_switch_unavailable, set_kill_switch_unavailable

        set_kill_switch_unavailable(True)
        assert is_kill_switch_unavailable() is True

        set_kill_switch_unavailable(False)
        assert is_kill_switch_unavailable() is False


class TestCreateOrchestrator:
    """Tests for create_orchestrator factory function."""

    def test_create_orchestrator_returns_instance(self):
        """Test create_orchestrator returns a TradingOrchestrator instance."""
        from apps.orchestrator.main import create_orchestrator
        from apps.orchestrator.orchestrator import TradingOrchestrator

        orchestrator = create_orchestrator()

        assert isinstance(orchestrator, TradingOrchestrator)
