"""Tests for admin endpoints in execution_gateway/routes/admin.py.

This module tests:
- Configuration endpoint (/api/v1/config)
- Fat-finger threshold endpoints (GET/PUT /api/v1/fat-finger/thresholds)
- Strategy status endpoints (/api/v1/strategies, /api/v1/strategies/{strategy_id})
- Kill-switch endpoints (engage, disengage, status)
- Authentication and authorization
- Error handling
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.execution_gateway.app_factory import create_mock_context, create_test_config
from apps.execution_gateway.dependencies import get_config, get_context, get_version
from apps.execution_gateway.fat_finger_validator import FatFingerValidator
from apps.execution_gateway.routes import admin
from apps.execution_gateway.routes.admin import _determine_strategy_status
from apps.execution_gateway.schemas import (
    FatFingerThresholds,
    FatFingerThresholdsResponse,
)
from apps.execution_gateway.services.auth_helpers import build_user_context
from libs.core.common.api_auth_dependency import AuthContext
from libs.trading.risk_management import RiskConfig


def _mock_auth_context() -> AuthContext:
    """Return a mock AuthContext that bypasses authentication for tests."""
    return AuthContext(
        user=None,
        internal_claims=None,
        auth_type="test",
        is_authenticated=True,
    )


def _mock_user_context_admin() -> dict[str, Any]:
    """Return a mock user context for admin users."""
    return {
        "role": "admin",
        "strategies": ["alpha_baseline", "momentum_strategy"],
        "requested_strategies": [],
        "user_id": "test-admin",
        "user": {
            "role": "admin",
            "strategies": ["alpha_baseline", "momentum_strategy"],
            "user_id": "test-admin",
        },
    }


def _mock_user_context_trader() -> dict[str, Any]:
    """Return a mock user context for trader users (operator role with limited strategies)."""
    return {
        "role": "operator",
        "strategies": ["alpha_baseline"],
        "requested_strategies": [],
        "user_id": "test-trader",
        "user": {
            "role": "operator",
            "strategies": ["alpha_baseline"],
            "user_id": "test-trader",
        },
    }


def _mock_user_context_no_strategies() -> dict[str, Any]:
    """Return a mock user context with no strategy access."""
    return {
        "role": "viewer",
        "strategies": [],
        "requested_strategies": [],
        "user_id": "test-viewer",
        "user": {
            "role": "viewer",
            "strategies": [],
            "user_id": "test-viewer",
        },
    }


class TestConfigEndpoint:
    """Tests for the configuration endpoint (/api/v1/config)."""

    def test_get_config_returns_service_configuration(self) -> None:
        """GET /api/v1/config returns service configuration."""
        app = FastAPI()
        app.include_router(admin.router)

        test_config = create_test_config(
            dry_run=True,
            environment="staging",
            circuit_breaker_enabled=True,
            liquidity_check_enabled=True,
            max_slice_pct_of_adv=0.02,
            alpaca_paper=True,
        )

        app.dependency_overrides[get_version] = lambda: "1.0.0"
        app.dependency_overrides[get_config] = lambda: test_config

        client = TestClient(app)
        response = client.get("/api/v1/config")

        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "execution_gateway"
        assert data["version"] == "1.0.0"
        assert data["environment"] == "staging"
        assert data["dry_run"] is True
        assert data["alpaca_paper"] is True
        assert data["circuit_breaker_enabled"] is True
        assert data["liquidity_check_enabled"] is True
        assert data["max_slice_pct_of_adv"] == 0.02
        assert "timestamp" in data

    def test_get_config_production_mode(self) -> None:
        """GET /api/v1/config shows production settings correctly."""
        app = FastAPI()
        app.include_router(admin.router)

        test_config = create_test_config(
            dry_run=False,
            environment="production",
            alpaca_paper=False,
        )

        app.dependency_overrides[get_version] = lambda: "2.0.0"
        app.dependency_overrides[get_config] = lambda: test_config

        client = TestClient(app)
        response = client.get("/api/v1/config")

        assert response.status_code == 200
        data = response.json()
        assert data["dry_run"] is False
        assert data["environment"] == "production"
        assert data["alpaca_paper"] is False


class TestFatFingerThresholdsEndpoints:
    """Tests for fat-finger threshold endpoints."""

    @pytest.fixture()
    def mock_fat_finger_validator(self) -> MagicMock:
        """Create mock fat-finger validator."""
        validator = MagicMock(spec=FatFingerValidator)
        validator.get_default_thresholds.return_value = FatFingerThresholds(
            max_notional=Decimal("100000"),
            max_qty=10000,
            max_adv_pct=Decimal("0.05"),
        )
        validator.get_symbol_overrides.return_value = {}
        return validator

    @pytest.fixture()
    def mock_recovery_manager(self) -> MagicMock:
        """Create mock recovery manager."""
        manager = MagicMock()
        manager.needs_recovery.return_value = False
        return manager

    @pytest.fixture()
    def mock_db(self) -> MagicMock:
        """Create mock database client."""
        db = MagicMock()
        db.check_connection.return_value = True
        return db

    def test_get_fat_finger_thresholds(
        self,
        mock_fat_finger_validator: MagicMock,
        mock_recovery_manager: MagicMock,
        mock_db: MagicMock,
    ) -> None:
        """GET /api/v1/fat-finger/thresholds returns current thresholds."""
        app = FastAPI()
        app.include_router(admin.router)

        mock_context = create_mock_context(
            db=mock_db,
            recovery_manager=mock_recovery_manager,
            fat_finger_validator=mock_fat_finger_validator,
            risk_config=RiskConfig(),
        )

        app.dependency_overrides[get_context] = lambda: mock_context

        client = TestClient(app)
        response = client.get("/api/v1/fat-finger/thresholds")

        assert response.status_code == 200
        data = response.json()
        assert "default_thresholds" in data
        assert "symbol_overrides" in data
        assert "updated_at" in data
        assert data["default_thresholds"]["max_notional"] == "100000"
        assert data["default_thresholds"]["max_qty"] == 10000

    def test_get_fat_finger_thresholds_with_overrides(
        self,
        mock_fat_finger_validator: MagicMock,
        mock_recovery_manager: MagicMock,
        mock_db: MagicMock,
    ) -> None:
        """GET /api/v1/fat-finger/thresholds returns symbol overrides."""
        app = FastAPI()
        app.include_router(admin.router)

        mock_fat_finger_validator.get_symbol_overrides.return_value = {
            "AAPL": FatFingerThresholds(max_qty=5000),
            "TSLA": FatFingerThresholds(max_notional=Decimal("200000")),
        }

        mock_context = create_mock_context(
            db=mock_db,
            recovery_manager=mock_recovery_manager,
            fat_finger_validator=mock_fat_finger_validator,
            risk_config=RiskConfig(),
        )

        app.dependency_overrides[get_context] = lambda: mock_context

        client = TestClient(app)
        response = client.get("/api/v1/fat-finger/thresholds")

        assert response.status_code == 200
        data = response.json()
        assert "AAPL" in data["symbol_overrides"]
        assert "TSLA" in data["symbol_overrides"]
        assert data["symbol_overrides"]["AAPL"]["max_qty"] == 5000

    def test_update_fat_finger_thresholds(
        self,
        mock_fat_finger_validator: MagicMock,
        mock_recovery_manager: MagicMock,
        mock_db: MagicMock,
    ) -> None:
        """PUT /api/v1/fat-finger/thresholds updates thresholds."""
        app = FastAPI()
        app.include_router(admin.router)

        mock_context = create_mock_context(
            db=mock_db,
            recovery_manager=mock_recovery_manager,
            fat_finger_validator=mock_fat_finger_validator,
            risk_config=RiskConfig(),
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[build_user_context] = _mock_user_context_admin

        client = TestClient(app)
        response = client.put(
            "/api/v1/fat-finger/thresholds",
            json={
                "default_thresholds": {
                    "max_notional": "150000",
                    "max_qty": 12000,
                },
                "symbol_overrides": {
                    "AAPL": {"max_qty": 5000},
                },
            },
        )

        assert response.status_code == 200
        # Verify update methods were called
        mock_fat_finger_validator.update_defaults.assert_called_once()
        mock_fat_finger_validator.update_symbol_overrides.assert_called_once()

    def test_update_fat_finger_thresholds_partial_update(
        self,
        mock_fat_finger_validator: MagicMock,
        mock_recovery_manager: MagicMock,
        mock_db: MagicMock,
    ) -> None:
        """PUT /api/v1/fat-finger/thresholds supports partial updates."""
        app = FastAPI()
        app.include_router(admin.router)

        mock_context = create_mock_context(
            db=mock_db,
            recovery_manager=mock_recovery_manager,
            fat_finger_validator=mock_fat_finger_validator,
            risk_config=RiskConfig(),
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[build_user_context] = _mock_user_context_admin

        client = TestClient(app)
        # Only update defaults, not overrides
        response = client.put(
            "/api/v1/fat-finger/thresholds",
            json={
                "default_thresholds": {
                    "max_qty": 8000,
                },
            },
        )

        assert response.status_code == 200
        mock_fat_finger_validator.update_defaults.assert_called_once()
        mock_fat_finger_validator.update_symbol_overrides.assert_not_called()


class TestStrategyStatusEndpoints:
    """Tests for strategy status endpoints."""

    @pytest.fixture()
    def mock_recovery_manager(self) -> MagicMock:
        """Create mock recovery manager."""
        manager = MagicMock()
        manager.needs_recovery.return_value = False
        return manager

    @pytest.fixture()
    def mock_db(self) -> MagicMock:
        """Create mock database client."""
        db = MagicMock()
        db.check_connection.return_value = True
        db.get_all_strategy_ids.return_value = ["alpha_baseline", "momentum_strategy"]
        db.get_bulk_strategy_status.return_value = {
            "alpha_baseline": {
                "positions_count": 5,
                "open_orders_count": 2,
                "last_signal_at": datetime.now(UTC) - timedelta(hours=1),
                "today_pnl": Decimal("1234.56"),
            },
            "momentum_strategy": {
                "positions_count": 0,
                "open_orders_count": 0,
                "last_signal_at": None,
                "today_pnl": Decimal("0"),
            },
        }
        return db

    def test_list_strategies(
        self,
        mock_recovery_manager: MagicMock,
        mock_db: MagicMock,
    ) -> None:
        """GET /api/v1/strategies returns list of strategies."""
        app = FastAPI()
        app.include_router(admin.router)

        test_config = create_test_config(strategy_activity_threshold_seconds=86400)
        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            recovery_manager=mock_recovery_manager,
            fat_finger_validator=fat_finger_validator,
            risk_config=RiskConfig(),
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[get_config] = lambda: test_config
        app.dependency_overrides[build_user_context] = _mock_user_context_admin

        client = TestClient(app)
        response = client.get("/api/v1/strategies")

        assert response.status_code == 200
        data = response.json()
        assert "strategies" in data
        assert "total_count" in data
        assert data["total_count"] == 2
        assert len(data["strategies"]) == 2

    def test_list_strategies_filters_by_authorization(
        self,
        mock_recovery_manager: MagicMock,
        mock_db: MagicMock,
    ) -> None:
        """GET /api/v1/strategies filters by user's authorized strategies."""
        app = FastAPI()
        app.include_router(admin.router)

        # Operator only has access to alpha_baseline
        mock_db.get_all_strategy_ids.return_value = ["alpha_baseline"]
        mock_db.get_bulk_strategy_status.return_value = {
            "alpha_baseline": {
                "positions_count": 5,
                "open_orders_count": 2,
                "last_signal_at": datetime.now(UTC),
                "today_pnl": Decimal("1234.56"),
            },
        }

        test_config = create_test_config()
        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            recovery_manager=mock_recovery_manager,
            fat_finger_validator=fat_finger_validator,
            risk_config=RiskConfig(),
        )

        # Operator with limited access to only alpha_baseline
        def _mock_user_operator_limited() -> dict[str, Any]:
            return {
                "role": "operator",
                "strategies": ["alpha_baseline"],  # Only alpha_baseline
                "requested_strategies": [],
                "user_id": "test-operator",
                "user": {
                    "role": "operator",
                    "strategies": ["alpha_baseline"],
                    "user_id": "test-operator",
                },
            }

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[get_config] = lambda: test_config
        app.dependency_overrides[build_user_context] = _mock_user_operator_limited

        client = TestClient(app)
        response = client.get("/api/v1/strategies")

        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] == 1
        assert data["strategies"][0]["strategy_id"] == "alpha_baseline"

    def test_list_strategies_no_access_returns_403(
        self,
        mock_recovery_manager: MagicMock,
        mock_db: MagicMock,
    ) -> None:
        """GET /api/v1/strategies returns 403 when user has no strategy access."""
        app = FastAPI()
        app.include_router(admin.router)

        test_config = create_test_config()
        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            recovery_manager=mock_recovery_manager,
            fat_finger_validator=fat_finger_validator,
            risk_config=RiskConfig(),
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[get_config] = lambda: test_config
        app.dependency_overrides[build_user_context] = _mock_user_context_no_strategies

        client = TestClient(app)
        response = client.get("/api/v1/strategies")

        assert response.status_code == 403
        assert "No strategy access" in response.json()["detail"]

    def test_get_strategy_status(
        self,
        mock_recovery_manager: MagicMock,
        mock_db: MagicMock,
    ) -> None:
        """GET /api/v1/strategies/{strategy_id} returns strategy status."""
        app = FastAPI()
        app.include_router(admin.router)

        mock_db.get_strategy_status.return_value = {
            "positions_count": 5,
            "open_orders_count": 2,
            "last_signal_at": datetime.now(UTC) - timedelta(hours=1),
            "today_pnl": Decimal("1234.56"),
        }

        test_config = create_test_config(strategy_activity_threshold_seconds=86400)
        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            recovery_manager=mock_recovery_manager,
            fat_finger_validator=fat_finger_validator,
            risk_config=RiskConfig(),
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[get_config] = lambda: test_config
        app.dependency_overrides[build_user_context] = _mock_user_context_admin

        client = TestClient(app)
        response = client.get("/api/v1/strategies/alpha_baseline")

        assert response.status_code == 200
        data = response.json()
        assert data["strategy_id"] == "alpha_baseline"
        assert data["status"] == "active"  # Has positions
        assert data["positions_count"] == 5
        assert data["open_orders_count"] == 2

    def test_get_strategy_status_not_found(
        self,
        mock_recovery_manager: MagicMock,
        mock_db: MagicMock,
    ) -> None:
        """GET /api/v1/strategies/{strategy_id} returns 404 for unknown strategy."""
        app = FastAPI()
        app.include_router(admin.router)

        mock_db.get_strategy_status.return_value = None

        test_config = create_test_config()
        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            recovery_manager=mock_recovery_manager,
            fat_finger_validator=fat_finger_validator,
            risk_config=RiskConfig(),
        )

        # Admin user with the strategy in their list (so auth passes, then 404)
        def _mock_user_with_unknown_strategy() -> dict[str, Any]:
            return {
                "role": "admin",
                "strategies": ["unknown_strategy", "alpha_baseline"],
                "requested_strategies": [],
                "user_id": "test-admin",
                "user": {
                    "role": "admin",
                    "strategies": ["unknown_strategy", "alpha_baseline"],
                    "user_id": "test-admin",
                },
            }

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[get_config] = lambda: test_config
        app.dependency_overrides[build_user_context] = _mock_user_with_unknown_strategy

        client = TestClient(app)
        response = client.get("/api/v1/strategies/unknown_strategy")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_get_strategy_status_unauthorized(
        self,
        mock_recovery_manager: MagicMock,
        mock_db: MagicMock,
    ) -> None:
        """GET /api/v1/strategies/{strategy_id} returns 403 for unauthorized strategy."""
        app = FastAPI()
        app.include_router(admin.router)

        test_config = create_test_config()
        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            recovery_manager=mock_recovery_manager,
            fat_finger_validator=fat_finger_validator,
            risk_config=RiskConfig(),
        )

        # User with limited strategy access (operator with only alpha_baseline)
        # Trying to access momentum_strategy should fail
        def _mock_user_limited_access() -> dict[str, Any]:
            return {
                "role": "operator",
                "strategies": ["alpha_baseline"],  # Does NOT include momentum_strategy
                "requested_strategies": [],
                "user_id": "test-operator",
                "user": {
                    "role": "operator",
                    "strategies": ["alpha_baseline"],
                    "user_id": "test-operator",
                },
            }

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[get_config] = lambda: test_config
        app.dependency_overrides[build_user_context] = _mock_user_limited_access

        client = TestClient(app)
        response = client.get("/api/v1/strategies/momentum_strategy")

        assert response.status_code == 403
        # The error can be either "No strategy access" or "Not authorized" depending on implementation
        error_detail = response.json()["detail"]
        assert "Not authorized" in error_detail or "strategy" in error_detail.lower()


class TestDetermineStrategyStatus:
    """Unit tests for _determine_strategy_status helper function."""

    def test_active_with_positions(self) -> None:
        """Strategy with positions is active."""
        now = datetime.now(UTC)
        db_status = {
            "positions_count": 5,
            "open_orders_count": 0,
            "last_signal_at": None,
        }
        assert _determine_strategy_status(db_status, now, 86400) == "active"

    def test_active_with_open_orders(self) -> None:
        """Strategy with open orders is active."""
        now = datetime.now(UTC)
        db_status = {
            "positions_count": 0,
            "open_orders_count": 3,
            "last_signal_at": None,
        }
        assert _determine_strategy_status(db_status, now, 86400) == "active"

    def test_active_with_recent_signal(self) -> None:
        """Strategy with recent signal is active."""
        now = datetime.now(UTC)
        db_status = {
            "positions_count": 0,
            "open_orders_count": 0,
            "last_signal_at": now - timedelta(hours=1),  # 1 hour ago
        }
        assert _determine_strategy_status(db_status, now, 86400) == "active"

    def test_inactive_no_activity(self) -> None:
        """Strategy with no activity is inactive."""
        now = datetime.now(UTC)
        db_status = {
            "positions_count": 0,
            "open_orders_count": 0,
            "last_signal_at": None,
        }
        assert _determine_strategy_status(db_status, now, 86400) == "inactive"

    def test_inactive_old_signal(self) -> None:
        """Strategy with old signal is inactive."""
        now = datetime.now(UTC)
        db_status = {
            "positions_count": 0,
            "open_orders_count": 0,
            "last_signal_at": now - timedelta(days=2),  # 2 days ago, threshold is 1 day
        }
        assert _determine_strategy_status(db_status, now, 86400) == "inactive"


class TestKillSwitchEndpoints:
    """Tests for kill-switch endpoints."""

    @pytest.fixture()
    def mock_kill_switch(self) -> MagicMock:
        """Create mock kill switch."""
        kill_switch = MagicMock()
        kill_switch.get_status.return_value = {
            "state": "ACTIVE",
            "engaged_at": None,
            "engaged_by": None,
            "reason": None,
        }
        return kill_switch

    @pytest.fixture()
    def mock_recovery_manager(self, mock_kill_switch: MagicMock) -> MagicMock:
        """Create mock recovery manager with kill switch."""
        manager = MagicMock()
        manager.kill_switch = mock_kill_switch
        manager.is_kill_switch_unavailable.return_value = False
        manager.needs_recovery.return_value = False
        return manager

    @pytest.fixture()
    def mock_db(self) -> MagicMock:
        """Create mock database client."""
        db = MagicMock()
        db.check_connection.return_value = True
        return db

    def test_get_kill_switch_status(
        self,
        mock_recovery_manager: MagicMock,
        mock_db: MagicMock,
    ) -> None:
        """GET /api/v1/kill-switch/status returns kill switch status."""
        app = FastAPI()
        app.include_router(admin.router)

        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            recovery_manager=mock_recovery_manager,
            fat_finger_validator=fat_finger_validator,
            risk_config=RiskConfig(),
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[admin.kill_switch_auth] = _mock_auth_context

        client = TestClient(app)
        response = client.get("/api/v1/kill-switch/status")

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "ACTIVE"

    def test_get_kill_switch_status_unavailable(
        self,
        mock_recovery_manager: MagicMock,
        mock_db: MagicMock,
    ) -> None:
        """GET /api/v1/kill-switch/status returns 503 when unavailable."""
        app = FastAPI()
        app.include_router(admin.router)

        mock_recovery_manager.kill_switch = None
        mock_recovery_manager.is_kill_switch_unavailable.return_value = True

        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            recovery_manager=mock_recovery_manager,
            fat_finger_validator=fat_finger_validator,
            risk_config=RiskConfig(),
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[admin.kill_switch_auth] = _mock_auth_context

        client = TestClient(app)
        response = client.get("/api/v1/kill-switch/status")

        assert response.status_code == 503
        assert "unavailable" in response.json()["detail"].lower()

    def test_engage_kill_switch(
        self,
        mock_recovery_manager: MagicMock,
        mock_kill_switch: MagicMock,
        mock_db: MagicMock,
    ) -> None:
        """POST /api/v1/kill-switch/engage engages the kill switch."""
        app = FastAPI()
        app.include_router(admin.router)

        mock_kill_switch.get_status.return_value = {
            "state": "ENGAGED",
            "engaged_at": datetime.now(UTC).isoformat(),
            "engaged_by": "ops_team",
            "reason": "Market anomaly",
        }

        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            recovery_manager=mock_recovery_manager,
            fat_finger_validator=fat_finger_validator,
            risk_config=RiskConfig(),
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[admin.kill_switch_auth] = _mock_auth_context

        client = TestClient(app)
        response = client.post(
            "/api/v1/kill-switch/engage",
            json={
                "reason": "Market anomaly",
                "operator": "ops_team",
                "details": {"anomaly_type": "flash_crash"},
            },
        )

        assert response.status_code == 200
        mock_kill_switch.engage.assert_called_once_with(
            reason="Market anomaly",
            operator="ops_team",
            details={"anomaly_type": "flash_crash"},
        )

    def test_engage_kill_switch_already_engaged(
        self,
        mock_recovery_manager: MagicMock,
        mock_kill_switch: MagicMock,
        mock_db: MagicMock,
    ) -> None:
        """POST /api/v1/kill-switch/engage returns 400 when already engaged."""
        app = FastAPI()
        app.include_router(admin.router)

        mock_kill_switch.engage.side_effect = ValueError("Kill switch already engaged")

        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            recovery_manager=mock_recovery_manager,
            fat_finger_validator=fat_finger_validator,
            risk_config=RiskConfig(),
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[admin.kill_switch_auth] = _mock_auth_context

        client = TestClient(app)
        response = client.post(
            "/api/v1/kill-switch/engage",
            json={
                "reason": "Test",
                "operator": "test_user",
            },
        )

        assert response.status_code == 400
        assert "already engaged" in response.json()["detail"].lower()

    def test_engage_kill_switch_runtime_error(
        self,
        mock_recovery_manager: MagicMock,
        mock_kill_switch: MagicMock,
        mock_db: MagicMock,
    ) -> None:
        """POST /api/v1/kill-switch/engage returns 503 on runtime error."""
        app = FastAPI()
        app.include_router(admin.router)

        mock_kill_switch.engage.side_effect = RuntimeError("State missing")

        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            recovery_manager=mock_recovery_manager,
            fat_finger_validator=fat_finger_validator,
            risk_config=RiskConfig(),
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[admin.kill_switch_auth] = _mock_auth_context

        client = TestClient(app)
        response = client.post(
            "/api/v1/kill-switch/engage",
            json={
                "reason": "Test",
                "operator": "test_user",
            },
        )

        assert response.status_code == 503
        # Should mark kill switch as unavailable
        mock_recovery_manager.set_kill_switch_unavailable.assert_called_once_with(True)

    def test_disengage_kill_switch(
        self,
        mock_recovery_manager: MagicMock,
        mock_kill_switch: MagicMock,
        mock_db: MagicMock,
    ) -> None:
        """POST /api/v1/kill-switch/disengage disengages the kill switch."""
        app = FastAPI()
        app.include_router(admin.router)

        mock_kill_switch.get_status.return_value = {
            "state": "ACTIVE",
            "disengaged_at": datetime.now(UTC).isoformat(),
            "disengaged_by": "ops_team",
        }

        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            recovery_manager=mock_recovery_manager,
            fat_finger_validator=fat_finger_validator,
            risk_config=RiskConfig(),
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[admin.kill_switch_auth] = _mock_auth_context

        client = TestClient(app)
        response = client.post(
            "/api/v1/kill-switch/disengage",
            json={
                "operator": "ops_team",
                "notes": "Market conditions normalized",
            },
        )

        assert response.status_code == 200
        mock_kill_switch.disengage.assert_called_once_with(
            operator="ops_team",
            notes="Market conditions normalized",
        )

    def test_disengage_kill_switch_not_engaged(
        self,
        mock_recovery_manager: MagicMock,
        mock_kill_switch: MagicMock,
        mock_db: MagicMock,
    ) -> None:
        """POST /api/v1/kill-switch/disengage returns 400 when not engaged."""
        app = FastAPI()
        app.include_router(admin.router)

        mock_kill_switch.disengage.side_effect = ValueError("Kill switch not engaged")

        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            recovery_manager=mock_recovery_manager,
            fat_finger_validator=fat_finger_validator,
            risk_config=RiskConfig(),
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[admin.kill_switch_auth] = _mock_auth_context

        client = TestClient(app)
        response = client.post(
            "/api/v1/kill-switch/disengage",
            json={
                "operator": "test_user",
            },
        )

        assert response.status_code == 400
        assert "not engaged" in response.json()["detail"].lower()

    def test_disengage_kill_switch_runtime_error(
        self,
        mock_recovery_manager: MagicMock,
        mock_kill_switch: MagicMock,
        mock_db: MagicMock,
    ) -> None:
        """POST /api/v1/kill-switch/disengage returns 503 on runtime error."""
        app = FastAPI()
        app.include_router(admin.router)

        mock_kill_switch.disengage.side_effect = RuntimeError("State missing")

        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            recovery_manager=mock_recovery_manager,
            fat_finger_validator=fat_finger_validator,
            risk_config=RiskConfig(),
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[admin.kill_switch_auth] = _mock_auth_context

        client = TestClient(app)
        response = client.post(
            "/api/v1/kill-switch/disengage",
            json={
                "operator": "test_user",
            },
        )

        assert response.status_code == 503

    def test_kill_switch_unavailable_503(
        self,
        mock_recovery_manager: MagicMock,
        mock_db: MagicMock,
    ) -> None:
        """Kill switch endpoints return 503 when Redis unavailable."""
        app = FastAPI()
        app.include_router(admin.router)

        # Kill switch is None (Redis unavailable)
        mock_recovery_manager.kill_switch = None

        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            recovery_manager=mock_recovery_manager,
            fat_finger_validator=fat_finger_validator,
            risk_config=RiskConfig(),
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[admin.kill_switch_auth] = _mock_auth_context

        client = TestClient(app)

        # All kill switch operations should return 503
        assert client.get("/api/v1/kill-switch/status").status_code == 503
        assert (
            client.post(
                "/api/v1/kill-switch/engage",
                json={"reason": "Test", "operator": "test"},
            ).status_code
            == 503
        )
        assert (
            client.post(
                "/api/v1/kill-switch/disengage",
                json={"operator": "test"},
            ).status_code
            == 503
        )

    def test_get_kill_switch_status_runtime_error(
        self,
        mock_recovery_manager: MagicMock,
        mock_kill_switch: MagicMock,
        mock_db: MagicMock,
    ) -> None:
        """GET /api/v1/kill-switch/status returns 503 on runtime error."""
        app = FastAPI()
        app.include_router(admin.router)

        mock_kill_switch.get_status.side_effect = RuntimeError("State missing")

        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=mock_db,
            recovery_manager=mock_recovery_manager,
            fat_finger_validator=fat_finger_validator,
            risk_config=RiskConfig(),
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[admin.kill_switch_auth] = _mock_auth_context

        client = TestClient(app)
        response = client.get("/api/v1/kill-switch/status")

        assert response.status_code == 503
        mock_recovery_manager.set_kill_switch_unavailable.assert_called_once_with(True)


class TestRequestValidation:
    """Tests for request validation on admin endpoints."""

    def test_engage_kill_switch_missing_reason(self) -> None:
        """POST /api/v1/kill-switch/engage fails with missing reason."""
        app = FastAPI()
        app.include_router(admin.router)

        mock_recovery_manager = MagicMock()
        mock_recovery_manager.kill_switch = MagicMock()
        mock_recovery_manager.is_kill_switch_unavailable.return_value = False

        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=MagicMock(),
            recovery_manager=mock_recovery_manager,
            fat_finger_validator=fat_finger_validator,
            risk_config=RiskConfig(),
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[admin.kill_switch_auth] = _mock_auth_context

        client = TestClient(app)
        response = client.post(
            "/api/v1/kill-switch/engage",
            json={
                "operator": "test_user",
                # Missing required "reason" field
            },
        )

        assert response.status_code == 422  # Validation error

    def test_engage_kill_switch_missing_operator(self) -> None:
        """POST /api/v1/kill-switch/engage fails with missing operator."""
        app = FastAPI()
        app.include_router(admin.router)

        mock_recovery_manager = MagicMock()
        mock_recovery_manager.kill_switch = MagicMock()
        mock_recovery_manager.is_kill_switch_unavailable.return_value = False

        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=MagicMock(),
            recovery_manager=mock_recovery_manager,
            fat_finger_validator=fat_finger_validator,
            risk_config=RiskConfig(),
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[admin.kill_switch_auth] = _mock_auth_context

        client = TestClient(app)
        response = client.post(
            "/api/v1/kill-switch/engage",
            json={
                "reason": "Test reason",
                # Missing required "operator" field
            },
        )

        assert response.status_code == 422  # Validation error

    def test_disengage_kill_switch_missing_operator(self) -> None:
        """POST /api/v1/kill-switch/disengage fails with missing operator."""
        app = FastAPI()
        app.include_router(admin.router)

        mock_recovery_manager = MagicMock()
        mock_recovery_manager.kill_switch = MagicMock()
        mock_recovery_manager.is_kill_switch_unavailable.return_value = False

        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        mock_context = create_mock_context(
            db=MagicMock(),
            recovery_manager=mock_recovery_manager,
            fat_finger_validator=fat_finger_validator,
            risk_config=RiskConfig(),
        )

        app.dependency_overrides[get_context] = lambda: mock_context
        app.dependency_overrides[admin.kill_switch_auth] = _mock_auth_context

        client = TestClient(app)
        response = client.post(
            "/api/v1/kill-switch/disengage",
            json={
                # Missing required "operator" field
            },
        )

        assert response.status_code == 422  # Validation error


class TestCreateFatFingerThresholdsSnapshot:
    """Unit tests for create_fat_finger_thresholds_snapshot helper."""

    def test_creates_snapshot_with_defaults(self) -> None:
        """Helper creates snapshot with default thresholds."""
        from apps.execution_gateway.routes.admin import create_fat_finger_thresholds_snapshot

        mock_validator = MagicMock(spec=FatFingerValidator)
        mock_validator.get_default_thresholds.return_value = FatFingerThresholds(
            max_notional=Decimal("100000"),
            max_qty=10000,
        )
        mock_validator.get_symbol_overrides.return_value = {}

        result = create_fat_finger_thresholds_snapshot(mock_validator)

        assert isinstance(result, FatFingerThresholdsResponse)
        assert result.default_thresholds.max_notional == Decimal("100000")
        assert result.default_thresholds.max_qty == 10000
        assert result.symbol_overrides == {}

    def test_creates_snapshot_with_overrides(self) -> None:
        """Helper creates snapshot with symbol overrides."""
        from apps.execution_gateway.routes.admin import create_fat_finger_thresholds_snapshot

        mock_validator = MagicMock(spec=FatFingerValidator)
        mock_validator.get_default_thresholds.return_value = FatFingerThresholds()
        mock_validator.get_symbol_overrides.return_value = {
            "AAPL": FatFingerThresholds(max_qty=5000),
        }

        result = create_fat_finger_thresholds_snapshot(mock_validator)

        assert "AAPL" in result.symbol_overrides
        assert result.symbol_overrides["AAPL"].max_qty == 5000
