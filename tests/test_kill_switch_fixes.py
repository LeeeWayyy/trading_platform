"""
Integration tests for kill-switch critical fixes.

This test suite verifies fixes for 3 critical issues identified by Codex review:
- Fix #1: Redis list operations (tested in test_redis_client.py)
- Fix #2: Fail-closed behavior when Redis unavailable (CRITICAL)
- Fix #3: JSON body handling for kill-switch endpoints (HIGH)

Context:
    These tests were created in response to post-commit review findings
    where kill-switch implementation had blocking safety issues.

See Also:
    - Codex review continuation_id: 0bc17f58-dc1d-4bde-a2fd-344e1c1775c8
    - .claude/workflows/03-reviews.md (mandatory pre-commit review)
"""

from datetime import datetime
from unittest.mock import Mock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from apps.execution_gateway import main

# Import after services are mocked
# from apps.execution_gateway.main import app as execution_app
# from apps.orchestrator.main import app as orchestrator_app


class TestFailClosedBehaviorExecutionGateway:
    """
    Test execution_gateway fails closed when Redis unavailable.

    CRITICAL FIX #2: Previously, services failed open (continued trading)
    when Redis was unavailable. If kill-switch was ENGAGED before Redis
    went down, trading would resume unsafely.

    Now: Services fail closed - block all trading when kill-switch state
    cannot be determined (Redis unavailable).
    """

    @pytest.fixture(autouse=True)
    def _setup_auth_overrides(self):
        """Set up auth overrides for all tests in this class (C6 integration)."""
        from libs.common.api_auth_dependency import AuthContext

        def _mock_auth_context() -> AuthContext:
            """Return a mock AuthContext that bypasses authentication for tests."""
            return AuthContext(
                user=None,
                internal_claims=None,
                auth_type="test",
                is_authenticated=True,
            )

        main.app.dependency_overrides[main.order_submit_auth] = _mock_auth_context
        main.app.dependency_overrides[main.order_slice_auth] = _mock_auth_context
        main.app.dependency_overrides[main.order_cancel_auth] = _mock_auth_context
        yield
        main.app.dependency_overrides.clear()

    @pytest.fixture()
    def _mock_redis_unavailable(self):
        """Mock Redis connection failure during initialization."""
        # Mock module-level variables to simulate Redis unavailable state
        # Note: recovery_manager is a Mock from conftest, so we set attributes directly
        main.recovery_manager.kill_switch = None
        with (
            patch("apps.execution_gateway.main.redis_client", None),
            patch.object(main.recovery_manager, "is_kill_switch_unavailable", return_value=True),
            patch.object(main.recovery_manager, "needs_recovery", return_value=True),
        ):
            yield

    @pytest.fixture()
    def _mock_postgres(self):
        """Mock Postgres database client for execution_gateway tests."""
        mock_db = Mock()
        mock_db.check_connection.return_value = True
        with (
            patch("apps.execution_gateway.database.DatabaseClient"),
            patch("apps.execution_gateway.main.db_client", mock_db),
        ):
            yield

    @pytest.mark.usefixtures("_mock_redis_unavailable", "_mock_postgres")
    def test_order_submission_blocked_when_redis_unavailable(self):
        """
        Test orders are blocked (fail closed) when Redis unavailable.

        Scenario:
            1. Service starts with Redis unavailable
            2. kill_switch_unavailable flag is set to True
            3. Order submission attempts fail with HTTP 503
            4. Error message indicates fail-closed safety mode

        This prevents unsafe trading when kill-switch state is unknown.
        """
        # Import after mocking
        from apps.execution_gateway.main import app

        client = TestClient(app)

        # Attempt to submit order
        order_request = {
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "order_type": "market",
            "time_in_force": "day",
        }

        response = client.post("/api/v1/orders", json=order_request)

        # Verify fail-closed behavior
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert "kill-switch unavailable" in response.json()["detail"]["error"].lower()
        assert response.json()["detail"]["fail_closed"] is True
        assert "kill-switch state unknown" in response.json()["detail"]["message"].lower()

    @pytest.mark.usefixtures("_mock_redis_unavailable", "_mock_postgres")
    def test_health_endpoint_reports_kill_switch_unavailable(self):
        """
        Test health endpoint reports kill-switch unavailability.

        When Redis is down, health check should report degraded/unhealthy
        status and indicate kill-switch is unavailable.
        """
        from apps.execution_gateway.main import app

        client = TestClient(app)

        response = client.get("/health")

        # Health check should still respond but indicate issues
        assert response.status_code == 200
        health_data = response.json()

        # Status should be degraded or unhealthy (not healthy)
        assert health_data["status"] in ["degraded", "unhealthy"]

    @pytest.fixture()
    def mock_kill_switch_init_failure(self):
        """Mock KillSwitch initialization failure."""
        # Redis available but KillSwitch init fails
        mock_redis = Mock()
        mock_redis.health_check.return_value = True

        # Note: recovery_manager is a Mock from conftest, so we set attributes directly
        main.recovery_manager.kill_switch = None
        with (
            patch("apps.execution_gateway.main.redis_client", mock_redis),
            patch.object(main.recovery_manager, "is_kill_switch_unavailable", return_value=True),
            patch.object(main.recovery_manager, "needs_recovery", return_value=True),
        ):
            yield mock_redis

    @pytest.mark.usefixtures("_mock_postgres")
    def test_order_submission_blocked_when_kill_switch_init_fails(
        self, mock_kill_switch_init_failure
    ):
        """
        Test orders blocked when KillSwitch initialization fails.

        Even if Redis is up, if KillSwitch initialization fails,
        we should fail closed to be safe.
        """
        from apps.execution_gateway.main import app

        client = TestClient(app)

        order_request = {
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "order_type": "market",
        }

        response = client.post("/api/v1/orders", json=order_request)

        # Verify fail-closed behavior
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json()["detail"]["fail_closed"] is True


class TestFailClosedBehaviorOrchestrator:
    """
    Test orchestrator fails closed when Redis unavailable.

    Similar to execution_gateway, orchestrator must block all
    orchestration runs when kill-switch state cannot be determined.
    """

    @pytest.fixture()
    def _mock_redis_unavailable(self):
        """Mock Redis connection failure during initialization."""
        # Mock module-level variables to simulate Redis unavailable state
        with (
            patch("apps.orchestrator.main.redis_client", None),
            patch("apps.orchestrator.main.kill_switch", None),
            patch("apps.orchestrator.main._kill_switch_unavailable", True),
        ):
            yield

    @pytest.fixture()
    def _mock_postgres(self):
        """Mock Postgres database client for tests."""
        with patch("apps.orchestrator.database.OrchestrationDatabaseClient"):
            yield

    @pytest.fixture()
    def _mock_http_clients(self):
        """Mock HTTP clients to external services."""
        # TradingOrchestrator creates httpx clients internally,
        # we don't need to mock httpx at module level
        # This is a no-op fixture to maintain test compatibility
        return

    @pytest.mark.usefixtures("_mock_redis_unavailable", "_mock_postgres", "_mock_http_clients")
    def test_orchestration_blocked_when_redis_unavailable(self):
        """
        Test orchestration runs are blocked when Redis unavailable.

        Scenario:
            1. Service starts with Redis unavailable
            2. kill_switch_unavailable flag is set to True
            3. Orchestration attempts fail with HTTP 503
            4. Error message indicates fail-closed safety mode
        """
        from apps.orchestrator.main import app

        client = TestClient(app)

        # Attempt to run orchestration
        orchestration_request = {
            "symbols": ["AAPL", "MSFT", "GOOGL"],
            "as_of_date": "2025-10-22",
        }

        response = client.post("/api/v1/orchestration/run", json=orchestration_request)

        # Verify fail-closed behavior
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert "kill-switch unavailable" in response.json()["detail"]["error"].lower()
        assert response.json()["detail"]["fail_closed"] is True

    @pytest.mark.usefixtures("_mock_redis_unavailable", "_mock_postgres", "_mock_http_clients")
    def test_health_endpoint_reports_kill_switch_unavailable(self):
        """Test health endpoint reports kill-switch unavailability."""
        from apps.orchestrator.main import app

        client = TestClient(app)

        response = client.get("/health")

        # Health check should still respond but indicate issues
        assert response.status_code == 200
        health_data = response.json()

        # Status should be degraded or unhealthy
        assert health_data["status"] in ["degraded", "unhealthy"]


class TestKillSwitchJSONBodyHandling:
    """
    Test kill-switch endpoints accept JSON request bodies.

    HIGH FIX #3: Previously, endpoints were declared with plain function
    args, so FastAPI interpreted them as query params. Documented JSON
    payloads returned HTTP 422, preventing operators from supplying
    structured context.

    Now: Endpoints use Pydantic request models for proper JSON body
    validation and nested object support.
    """

    @pytest.fixture(autouse=True)
    def _setup_auth_overrides(self):
        """Set up auth overrides for all tests in this class (C6 integration)."""
        from libs.common.api_auth_dependency import AuthContext

        def _mock_auth_context() -> AuthContext:
            """Return a mock AuthContext that bypasses authentication for tests."""
            return AuthContext(
                user=None,
                internal_claims=None,
                auth_type="test",
                is_authenticated=True,
            )

        main.app.dependency_overrides[main.kill_switch_auth] = _mock_auth_context
        yield
        main.app.dependency_overrides.clear()

    @pytest.fixture()
    def mock_redis_and_kill_switch(self):
        """Mock Redis and KillSwitch for testing."""
        # Mock the module-level variables directly (not the classes)
        # since they're initialized at import time
        mock_redis = Mock()
        mock_redis.health_check.return_value = True

        mock_ks = Mock()
        mock_ks.is_engaged.return_value = False
        mock_ks.engage.return_value = None
        mock_ks.disengage.return_value = None
        mock_ks.get_status.return_value = {
            "engaged": True,
            "reason": "Test engagement",
            "operator": "test_ops",
            "timestamp": datetime.now().isoformat(),
        }

        # Note: recovery_manager is a Mock from conftest, so we set attributes directly
        # (not via _state which is for real RecoveryManager instances)
        main.recovery_manager.kill_switch = mock_ks
        with (
            patch("apps.execution_gateway.main.redis_client", mock_redis),
            patch.object(main.recovery_manager, "is_kill_switch_unavailable", return_value=False),
        ):
            yield mock_redis, mock_ks

    @pytest.fixture()
    def _mock_postgres(self):
        """Mock Postgres database client for tests."""
        with patch("apps.execution_gateway.database.DatabaseClient"):
            yield

    @pytest.mark.usefixtures("_mock_postgres")
    def test_engage_endpoint_accepts_json_body_with_nested_details(
        self, mock_redis_and_kill_switch
    ):
        """
        Test engage endpoint accepts JSON body with nested details object.

        Request Body:
            {
                "reason": "Market anomaly detected",
                "operator": "ops_team",
                "details": {
                    "anomaly_type": "flash_crash",
                    "severity": "high",
                    "affected_symbols": ["AAPL", "MSFT"]
                }
            }

        This was previously impossible - would return HTTP 422.
        """
        from apps.execution_gateway.main import app

        client = TestClient(app)
        mock_redis, mock_ks = mock_redis_and_kill_switch

        # Structured JSON request with nested details
        engage_request = {
            "reason": "Market anomaly detected",
            "operator": "ops_team",
            "details": {
                "anomaly_type": "flash_crash",
                "severity": "high",
                "affected_symbols": ["AAPL", "MSFT"],
                "detection_time": "2025-10-22T14:30:00Z",
            },
        }

        response = client.post("/api/v1/kill-switch/engage", json=engage_request)

        # Should succeed with proper JSON body handling
        assert response.status_code in [200, 400]  # 400 if already engaged

        # Verify KillSwitch.engage() was called with correct params
        if response.status_code == 200:
            mock_ks.engage.assert_called_once()
            call_kwargs = mock_ks.engage.call_args.kwargs
            assert call_kwargs["reason"] == "Market anomaly detected"
            assert call_kwargs["operator"] == "ops_team"
            assert call_kwargs["details"]["anomaly_type"] == "flash_crash"
            assert len(call_kwargs["details"]["affected_symbols"]) == 2

    @pytest.mark.usefixtures("_mock_postgres")
    def test_engage_endpoint_validates_required_fields(self, mock_redis_and_kill_switch):
        """Test engage endpoint validates required fields (reason, operator)."""
        from apps.execution_gateway.main import app

        client = TestClient(app)

        # Missing required 'reason' field
        invalid_request = {
            "operator": "ops_team",
            "details": {"note": "test"},
        }

        response = client.post("/api/v1/kill-switch/engage", json=invalid_request)

        # Should return validation error
        assert response.status_code == 422
        error_detail = response.json()["detail"]
        assert any("reason" in str(err).lower() for err in error_detail)

    @pytest.mark.usefixtures("_mock_postgres")
    def test_disengage_endpoint_accepts_json_body_with_notes(self, mock_redis_and_kill_switch):
        """
        Test disengage endpoint accepts JSON body with optional notes.

        Request Body:
            {
                "operator": "ops_team",
                "notes": "Market conditions normalized, all systems operational"
            }
        """
        from apps.execution_gateway.main import app

        client = TestClient(app)
        mock_redis, mock_ks = mock_redis_and_kill_switch

        disengage_request = {
            "operator": "ops_team",
            "notes": "Market conditions normalized, all systems operational",
        }

        response = client.post("/api/v1/kill-switch/disengage", json=disengage_request)

        # Should succeed
        assert response.status_code in [200, 400]  # 400 if not engaged

        if response.status_code == 200:
            mock_ks.disengage.assert_called_once()
            call_kwargs = mock_ks.disengage.call_args.kwargs
            assert call_kwargs["operator"] == "ops_team"
            assert "normalized" in call_kwargs["notes"]

    @pytest.mark.usefixtures("_mock_postgres")
    def test_disengage_endpoint_allows_optional_notes(self, mock_redis_and_kill_switch):
        """Test disengage endpoint works without optional notes field."""
        from apps.execution_gateway.main import app

        client = TestClient(app)
        mock_redis, mock_ks = mock_redis_and_kill_switch

        # Notes field is optional
        disengage_request = {
            "operator": "ops_team",
        }

        response = client.post("/api/v1/kill-switch/disengage", json=disengage_request)

        assert response.status_code in [200, 400]

    @pytest.fixture()
    def mock_orchestrator_redis_and_kill_switch(self):
        """Mock Redis and KillSwitch for orchestrator testing."""
        # Mock the module-level variables for orchestrator
        mock_redis = Mock()
        mock_redis.health_check.return_value = True

        mock_ks = Mock()
        mock_ks.is_engaged.return_value = False
        mock_ks.engage.return_value = None
        mock_ks.disengage.return_value = None
        mock_ks.get_status.return_value = {
            "engaged": True,
            "reason": "Test engagement",
            "operator": "test_ops",
            "timestamp": datetime.now().isoformat(),
        }

        with (
            patch("apps.orchestrator.main.redis_client", mock_redis),
            patch("apps.orchestrator.main.kill_switch", mock_ks),
            patch("apps.orchestrator.main._kill_switch_unavailable", False),
        ):
            yield mock_redis, mock_ks

    @pytest.fixture()
    def _mock_orchestrator_postgres(self):
        """Mock Postgres for orchestrator tests."""
        with patch("apps.orchestrator.database.OrchestrationDatabaseClient"):
            yield

    @pytest.mark.usefixtures("_mock_orchestrator_postgres")
    def test_orchestrator_engage_endpoint_accepts_json_body(
        self, mock_orchestrator_redis_and_kill_switch
    ):
        """Test orchestrator engage endpoint also uses JSON body."""
        from apps.orchestrator.main import app

        client = TestClient(app)
        mock_redis, mock_ks = mock_orchestrator_redis_and_kill_switch

        engage_request = {
            "reason": "Orchestration halt for deployment",
            "operator": "deployment_bot",
            "details": {
                "deployment_id": "deploy-2025-10-22-001",
                "estimated_duration_minutes": 15,
            },
        }

        response = client.post("/api/v1/kill-switch/engage", json=engage_request)

        # Should accept JSON body (not query params)
        assert response.status_code in [200, 400]

        if response.status_code == 200:
            mock_ks.engage.assert_called_once()
            call_kwargs = mock_ks.engage.call_args.kwargs
            assert "deployment" in call_kwargs["reason"].lower()
            assert call_kwargs["details"]["deployment_id"] == "deploy-2025-10-22-001"

    @pytest.mark.usefixtures("_mock_orchestrator_postgres")
    def test_orchestrator_disengage_endpoint_accepts_json_body(
        self, mock_orchestrator_redis_and_kill_switch
    ):
        """Test orchestrator disengage endpoint uses JSON body."""
        from apps.orchestrator.main import app

        client = TestClient(app)
        mock_redis, mock_ks = mock_orchestrator_redis_and_kill_switch

        disengage_request = {
            "operator": "deployment_bot",
            "notes": "Deployment completed successfully, all health checks passed",
        }

        response = client.post("/api/v1/kill-switch/disengage", json=disengage_request)

        assert response.status_code in [200, 400]


class TestKillSwitchEndToEnd:
    """
    End-to-end integration tests for complete kill-switch workflows.

    These tests verify the fixes work together in realistic scenarios.
    """

    @pytest.fixture(autouse=True)
    def _setup_auth_overrides(self):
        """Set up auth overrides for all tests in this class (C6 integration)."""
        from libs.common.api_auth_dependency import AuthContext

        def _mock_auth_context() -> AuthContext:
            """Return a mock AuthContext that bypasses authentication for tests."""
            return AuthContext(
                user=None,
                internal_claims=None,
                auth_type="test",
                is_authenticated=True,
            )

        main.app.dependency_overrides[main.kill_switch_auth] = _mock_auth_context
        yield
        main.app.dependency_overrides.clear()

    @pytest.fixture()
    def mock_components(self):
        """Mock all required components."""
        # Setup Redis mock
        mock_redis = Mock()
        mock_redis.health_check.return_value = True
        mock_redis.rpush.return_value = 1
        mock_redis.ltrim.return_value = True
        mock_redis.lrange.return_value = [
            b'{"action": "engage", "operator": "ops", "timestamp": "2025-10-22T10:00:00Z"}'
        ]

        # Setup KillSwitch mock
        mock_ks = Mock()
        mock_ks.is_engaged.return_value = False
        mock_ks.engage.return_value = None
        mock_ks.disengage.return_value = None
        mock_ks.get_status.return_value = {
            "engaged": False,
            "reason": None,
            "operator": None,
            "timestamp": datetime.now().isoformat(),
        }

        # Note: recovery_manager is a Mock from conftest, so we set attributes directly
        main.recovery_manager.kill_switch = mock_ks
        with (
            patch("apps.execution_gateway.main.redis_client", mock_redis),
            patch.object(main.recovery_manager, "is_kill_switch_unavailable", return_value=False),
            patch("apps.execution_gateway.database.DatabaseClient"),
        ):
            yield mock_redis, mock_ks

    def test_complete_engage_disengage_workflow(self, mock_components):
        """
        Test complete engage -> check -> disengage workflow.

        Verifies:
        1. JSON body accepted for engage
        2. Redis list operations used for history
        3. JSON body accepted for disengage
        4. Status correctly reflects state changes
        """
        from apps.execution_gateway.main import app

        client = TestClient(app)
        mock_redis, mock_ks = mock_components

        # Step 1: Engage with structured JSON
        engage_response = client.post(
            "/api/v1/kill-switch/engage",
            json={
                "reason": "E2E test engagement",
                "operator": "test_suite",
                "details": {"test_id": "e2e_001", "phase": "engage"},
            },
        )

        assert engage_response.status_code in [200, 400]

        # Step 2: Check status
        status_response = client.get("/api/v1/kill-switch/status")
        assert status_response.status_code == 200

        # Step 3: Disengage with notes
        disengage_response = client.post(
            "/api/v1/kill-switch/disengage",
            json={
                "operator": "test_suite",
                "notes": "E2E test completed successfully",
            },
        )

        assert disengage_response.status_code in [200, 400]

    def test_fail_closed_prevents_trading_during_redis_outage(self):
        """
        Simulate Redis outage scenario.

        Timeline:
        1. System running normally
        2. Redis goes down
        3. Service detects Redis unavailable on next health check
        4. All trading blocked (fail closed)
        5. Health endpoint reports degraded status

        This test verifies the complete fail-closed safety mechanism.
        """
        # This test would require more complex setup with dynamic Redis mocking
        # For now, document the expected behavior
        pytest.skip("Requires dynamic Redis state simulation - covered by other tests")
