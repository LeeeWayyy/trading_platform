"""Tests for Risk Analytics Dashboard page and components.

Tests cover:
- Risk page rendering and permission checks
- Risk service data fetching and formatting
- Chart components (factor exposure, VaR, stress tests)
- Validators for schema validation
- Async helpers for Streamlit integration
"""

from __future__ import annotations

import sys
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Stub jwt early to avoid cryptography dependency during import of web_console.auth
jwt_stub = type(sys)("jwt")
jwt_stub.api_jwk = type(sys)("jwt.api_jwk")
jwt_stub.algorithms = type(sys)("jwt.algorithms")
jwt_stub.utils = type(sys)("jwt.utils")
sys.modules.setdefault("jwt", jwt_stub)
sys.modules.setdefault("jwt.api_jwk", jwt_stub.api_jwk)
sys.modules.setdefault("jwt.algorithms", jwt_stub.algorithms)
sys.modules.setdefault("jwt.utils", jwt_stub.utils)

# Stub web_console auth modules to avoid loading cryptography/redis stacks
auth_permissions_stub = type(sys)("apps.web_console.auth.permissions")
auth_permissions_stub.Permission = type(
    "Permission",
    (),
    {"VIEW_PNL": "view_pnl", "VIEW_ALL_STRATEGIES": "view_all_strategies"},
)


def _has_permission(user, permission):
    return True


def _get_authorized_strategies(user):
    return user.get("strategies", []) if isinstance(user, dict) else []


auth_permissions_stub.has_permission = _has_permission
auth_permissions_stub.get_authorized_strategies = _get_authorized_strategies

session_mgr_stub = type(sys)("apps.web_console.auth.session_manager")
session_mgr_stub.get_current_user = lambda: {"role": "viewer", "user_id": "u1", "strategies": ["s1"]}


def _require_auth(func):
    return func


session_mgr_stub.require_auth = _require_auth

sys.modules.setdefault("apps.web_console.auth.permissions", auth_permissions_stub)
sys.modules.setdefault("apps.web_console.auth.session_manager", session_mgr_stub)

# Stub strategy_scoped_queries to avoid cryptography dependency
strategy_stub = type(sys)("apps.web_console.data.strategy_scoped_queries")


class MockStrategyScopedDataAccess:
    """Mock for StrategyScopedDataAccess."""

    def __init__(self, db_pool=None, redis_client=None, user=None):
        self.db_pool = db_pool
        self.redis_client = redis_client
        self.user = user or {}
        self.user_id = user.get("user_id") if user else None

    async def get_positions(self, limit: int = 100) -> list[dict[str, Any]]:
        return []

    async def get_pnl_summary(
        self, start_date: date, end_date: date, limit: int = 30
    ) -> list[dict[str, Any]]:
        return []


strategy_stub.StrategyScopedDataAccess = MockStrategyScopedDataAccess
sys.modules.setdefault("apps.web_console.data.strategy_scoped_queries", strategy_stub)

# Stub libs.risk.factor_covariance for CANONICAL_FACTOR_ORDER
factor_cov_stub = type(sys)("libs.risk.factor_covariance")
factor_cov_stub.CANONICAL_FACTOR_ORDER = [
    "log_market_cap",
    "book_to_market",
    "momentum_12_1",
    "realized_vol",
    "roe",
    "asset_growth",
]
sys.modules.setdefault("libs.risk.factor_covariance", factor_cov_stub)

# Now import the modules under test
from apps.web_console.services.risk_service import RiskDashboardData, RiskService
from apps.web_console.utils.validators import (
    validate_exposure_list,
    validate_risk_metrics,
    validate_stress_test_list,
)


@pytest.fixture
def mock_scoped_access():
    """Create a mock StrategyScopedDataAccess."""
    return MockStrategyScopedDataAccess(
        db_pool=None,
        redis_client=None,
        user={"user_id": "test_user", "strategies": ["strategy_a"]},
    )


@pytest.fixture
def sample_positions():
    """Sample position data for testing."""
    return [
        {"symbol": "AAPL", "market_value": 10000, "qty": 100},
        {"symbol": "GOOGL", "market_value": 20000, "qty": 50},
        {"symbol": "MSFT", "market_value": 15000, "qty": 75},
    ]


@pytest.fixture
def sample_risk_metrics():
    """Sample risk metrics for testing."""
    return {
        "total_risk": 0.15,
        "factor_risk": 0.12,
        "specific_risk": 0.08,
        "var_95": 0.025,
        "var_99": 0.035,
        "cvar_95": 0.04,
    }


@pytest.fixture
def sample_factor_exposures():
    """Sample factor exposures for testing."""
    return [
        {"factor_name": "log_market_cap", "exposure": 0.5},
        {"factor_name": "book_to_market", "exposure": -0.3},
        {"factor_name": "momentum_12_1", "exposure": 0.2},
        {"factor_name": "realized_vol", "exposure": 0.1},
        {"factor_name": "roe", "exposure": -0.1},
        {"factor_name": "asset_growth", "exposure": 0.05},
    ]


@pytest.fixture
def sample_stress_tests():
    """Sample stress test results for testing."""
    return [
        {
            "scenario_name": "GFC_2008",
            "scenario_type": "historical",
            "portfolio_pnl": -0.182,
            "factor_impacts": {
                "book_to_market": -0.08,
                "realized_vol": -0.05,
            },
        },
        {
            "scenario_name": "COVID_2020",
            "scenario_type": "historical",
            "portfolio_pnl": -0.145,
            "factor_impacts": {
                "momentum_12_1": -0.06,
            },
        },
    ]


class TestValidators:
    """Tests for schema validators."""

    def test_validate_risk_metrics_valid(self, sample_risk_metrics):
        assert validate_risk_metrics(sample_risk_metrics) is True

    def test_validate_risk_metrics_missing_keys(self):
        incomplete = {"total_risk": 0.15, "var_95": 0.025}
        assert validate_risk_metrics(incomplete) is False

    def test_validate_risk_metrics_none_values(self):
        with_none = {
            "total_risk": 0.15,
            "var_95": None,
            "var_99": 0.035,
            "cvar_95": 0.04,
        }
        assert validate_risk_metrics(with_none) is False

    def test_validate_risk_metrics_empty(self):
        assert validate_risk_metrics({}) is False
        assert validate_risk_metrics(None) is False

    def test_validate_exposure_list_valid(self, sample_factor_exposures):
        assert validate_exposure_list(sample_factor_exposures) is True

    def test_validate_exposure_list_missing_keys(self):
        invalid = [{"factor_name": "log_market_cap"}]  # missing exposure
        assert validate_exposure_list(invalid) is False

    def test_validate_exposure_list_empty(self):
        assert validate_exposure_list([]) is True  # empty is valid
        assert validate_exposure_list(None) is False

    def test_validate_stress_test_list_valid(self, sample_stress_tests):
        assert validate_stress_test_list(sample_stress_tests) is True

    def test_validate_stress_test_list_missing_keys(self):
        invalid = [{"scenario_name": "GFC_2008"}]  # missing portfolio_pnl
        assert validate_stress_test_list(invalid) is False

    def test_validate_stress_test_list_empty(self):
        assert validate_stress_test_list([]) is True  # empty is valid
        assert validate_stress_test_list(None) is False


class TestRiskService:
    """Tests for RiskService."""

    @pytest.mark.asyncio
    async def test_get_risk_dashboard_data_no_positions(self, mock_scoped_access):
        """Test that empty positions returns empty dashboard data."""
        service = RiskService(mock_scoped_access)
        result = await service.get_risk_dashboard_data()

        assert isinstance(result, RiskDashboardData)
        assert result.risk_metrics == {}
        assert result.factor_exposures == []
        assert result.stress_tests == []
        assert result.var_history == []

    @pytest.mark.asyncio
    async def test_get_risk_dashboard_data_with_positions(
        self, mock_scoped_access, sample_positions
    ):
        """Test dashboard data with positions returns placeholder stress tests."""
        # Mock get_positions to return sample data
        mock_scoped_access.get_positions = AsyncMock(return_value=sample_positions)

        service = RiskService(mock_scoped_access)
        result = await service.get_risk_dashboard_data()

        assert isinstance(result, RiskDashboardData)
        # With positions but no risk model, we get placeholder stress tests
        assert len(result.stress_tests) > 0
        assert result.stress_tests[0]["scenario_name"] == "GFC_2008"

    def test_build_weights_empty(self, mock_scoped_access):
        """Test weight building with empty positions."""
        service = RiskService(mock_scoped_access)
        weights = service._build_weights([])
        assert weights == {}

    def test_build_weights_valid(self, mock_scoped_access, sample_positions):
        """Test weight building with valid positions."""
        service = RiskService(mock_scoped_access)
        weights = service._build_weights(sample_positions)

        assert len(weights) == 3
        # Total = 10000 + 20000 + 15000 = 45000
        assert abs(weights["AAPL"] - 10000 / 45000) < 0.001
        assert abs(weights["GOOGL"] - 20000 / 45000) < 0.001
        assert abs(weights["MSFT"] - 15000 / 45000) < 0.001

    def test_build_weights_zero_total(self, mock_scoped_access):
        """Test weight building with zero total value."""
        service = RiskService(mock_scoped_access)
        positions = [
            {"symbol": "AAPL", "market_value": 0},
            {"symbol": "GOOGL", "market_value": 0},
        ]
        weights = service._build_weights(positions)
        assert weights == {}

    def test_format_risk_metrics_none(self, mock_scoped_access):
        """Test formatting None risk result returns placeholder."""
        service = RiskService(mock_scoped_access)
        result = service._format_risk_metrics(None)

        assert result["total_risk"] == 0.0
        assert result["var_95"] == 0.0

    def test_format_factor_exposures_none(self, mock_scoped_access):
        """Test formatting None result returns placeholder exposures."""
        service = RiskService(mock_scoped_access)
        result = service._format_factor_exposures(None)

        # Should return exposures for all canonical factors
        assert len(result) == len(factor_cov_stub.CANONICAL_FACTOR_ORDER)
        assert all(e["exposure"] == 0.0 for e in result)

    def test_format_stress_tests_empty(self, mock_scoped_access):
        """Test formatting empty stress tests."""
        service = RiskService(mock_scoped_access)
        result = service._format_stress_tests([])
        assert result == []

    def test_format_stress_tests_valid(self, mock_scoped_access, sample_stress_tests):
        """Test formatting valid stress test results."""
        service = RiskService(mock_scoped_access)
        result = service._format_stress_tests(sample_stress_tests)

        assert len(result) == 2
        assert result[0]["scenario_name"] == "GFC_2008"
        assert result[0]["portfolio_pnl"] == -0.182

    def test_generate_placeholder_stress_tests(self, mock_scoped_access):
        """Test placeholder stress test generation."""
        service = RiskService(mock_scoped_access)
        result = service._generate_placeholder_stress_tests()

        assert len(result) == 4
        scenario_names = [r["scenario_name"] for r in result]
        assert "GFC_2008" in scenario_names
        assert "COVID_2020" in scenario_names
        assert "RATE_HIKE_2022" in scenario_names
        assert "RATE_SHOCK" in scenario_names

    @pytest.mark.asyncio
    async def test_get_var_history_no_db(self, mock_scoped_access):
        """Test VaR history with no database connection."""
        # Mock get_pnl_summary to raise RuntimeError (no db)
        mock_scoped_access.get_pnl_summary = AsyncMock(
            side_effect=RuntimeError("No db_pool")
        )

        service = RiskService(mock_scoped_access)
        result = await service._get_var_history()

        assert result == []

    @pytest.mark.asyncio
    async def test_get_var_history_with_data(self, mock_scoped_access):
        """Test VaR history computation from P&L data without portfolio_value.

        When portfolio_value is not provided, VaR returns 0.0 as a safe fallback
        to ensure UI percentage formatting remains consistent.
        """
        pnl_data = [
            {"trade_date": "2024-01-01", "daily_pnl": 100},
            {"trade_date": "2024-01-02", "daily_pnl": -50},
            {"trade_date": "2024-01-03", "daily_pnl": 75},
        ]
        mock_scoped_access.get_pnl_summary = AsyncMock(return_value=pnl_data)

        service = RiskService(mock_scoped_access)
        result = await service._get_var_history()

        assert len(result) == 3
        # Without portfolio_value, VaR defaults to 0.0 for safe percentage display
        assert result[0]["var_95"] == 0.0
        assert result[1]["var_95"] == 0.0

    @pytest.mark.asyncio
    async def test_get_var_history_scales_by_notional(self, mock_scoped_access):
        """VaR history scales P&L to percentage when notional provided."""
        pnl_data = [
            {"trade_date": "2024-01-01", "daily_pnl": 2000},
        ]
        mock_scoped_access.get_pnl_summary = AsyncMock(return_value=pnl_data)

        service = RiskService(mock_scoped_access)
        result = await service._get_var_history(portfolio_value=100000)

        assert len(result) == 1
        assert result[0]["var_95"] == pytest.approx(abs(2000) / 100000 * 1.65)


class TestRiskDashboardData:
    """Tests for RiskDashboardData dataclass."""

    def test_create_empty(self):
        data = RiskDashboardData(
            risk_metrics={},
            factor_exposures=[],
            stress_tests=[],
            var_history=[],
        )
        assert data.risk_metrics == {}
        assert data.factor_exposures == []
        assert data.is_placeholder is False
        assert data.placeholder_reason == ""

    def test_create_with_data(
        self, sample_risk_metrics, sample_factor_exposures, sample_stress_tests
    ):
        data = RiskDashboardData(
            risk_metrics=sample_risk_metrics,
            factor_exposures=sample_factor_exposures,
            stress_tests=sample_stress_tests,
            var_history=[{"date": "2024-01-01", "var_95": 0.025}],
        )
        assert data.risk_metrics["total_risk"] == 0.15
        assert len(data.factor_exposures) == 6
        assert len(data.stress_tests) == 2

    def test_placeholder_flag(self):
        """Test is_placeholder flag indicates demo data."""
        data = RiskDashboardData(
            risk_metrics={},
            factor_exposures=[],
            stress_tests=[],
            var_history=[],
            is_placeholder=True,
            placeholder_reason="Risk model unavailable",
        )
        assert data.is_placeholder is True
        assert data.placeholder_reason == "Risk model unavailable"
