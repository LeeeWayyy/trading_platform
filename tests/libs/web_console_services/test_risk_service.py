"""
Unit tests for libs.web_console_services.risk_service.

Tests cover:
- RiskDashboardData model initialization and fields
- RiskService initialization
- get_risk_dashboard_data() main flow (happy path, no positions, DB errors)
- _compute_total_value() with various position scenarios
- _build_weights() with normal/zero total values
- _compute_risk_metrics() success, model unavailable, import errors, computation errors
- _load_risk_model() (always returns None in MVP)
- _run_stress_tests() with model available/unavailable, import errors, computation errors
- _generate_placeholder_stress_tests() structure validation
- _get_var_history() with P&L data, empty data, DB errors, permission errors
- _format_risk_metrics() with valid/None risk results
- _format_factor_exposures() with valid/None/missing factor data
- _format_stress_tests() with valid/empty results
- Permission error propagation
- Edge cases: None values, empty lists, invalid inputs

Target: 85%+ branch coverage (baseline from 0%)
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, Mock, patch

import pytest

from libs.web_console_services.risk_service import (
    DEFAULT_FACTOR_ORDER,
    RiskDashboardData,
    RiskService,
)


class TestRiskDashboardData:
    """Tests for RiskDashboardData data model."""

    def test_risk_dashboard_data_initialization_all_fields(self):
        """Test RiskDashboardData initializes with all fields."""
        data = RiskDashboardData(
            risk_metrics={"total_risk": 0.05, "var_95": 0.02},
            factor_exposures=[{"factor_name": "momentum_12_1", "exposure": 0.3}],
            stress_tests=[{"scenario_name": "GFC_2008", "portfolio_pnl": -0.18}],
            var_history=[{"date": date(2025, 1, 1), "var_95": 0.02}],
            is_placeholder=False,
            placeholder_reason="",
        )

        assert data.risk_metrics == {"total_risk": 0.05, "var_95": 0.02}
        assert len(data.factor_exposures) == 1
        assert len(data.stress_tests) == 1
        assert len(data.var_history) == 1
        assert data.is_placeholder is False
        assert data.placeholder_reason == ""

    def test_risk_dashboard_data_empty_containers(self):
        """Test RiskDashboardData with empty containers (no data available)."""
        data = RiskDashboardData(
            risk_metrics={},
            factor_exposures=[],
            stress_tests=[],
            var_history=[],
        )

        assert data.risk_metrics == {}
        assert data.factor_exposures == []
        assert data.stress_tests == []
        assert data.var_history == []
        assert data.is_placeholder is False

    def test_risk_dashboard_data_placeholder_mode(self):
        """Test RiskDashboardData with placeholder flag."""
        data = RiskDashboardData(
            risk_metrics={},
            factor_exposures=[],
            stress_tests=[],
            var_history=[],
            is_placeholder=True,
            placeholder_reason="Risk model artifacts not available. Showing demo data.",
        )

        assert data.is_placeholder is True
        assert "artifacts not available" in data.placeholder_reason


class TestRiskServiceInitialization:
    """Tests for RiskService initialization."""

    def test_init_with_scoped_access(self):
        """Test RiskService initializes with scoped data access."""
        mock_scoped_access = Mock()
        mock_scoped_access.user_id = "user123"

        service = RiskService(mock_scoped_access)

        assert service._scoped_access is mock_scoped_access


class TestGetRiskDashboardData:
    """Tests for get_risk_dashboard_data() main flow."""

    @pytest.mark.asyncio()
    async def test_get_risk_dashboard_data_no_positions(self):
        """Test get_risk_dashboard_data() with no positions returns empty data."""
        mock_scoped_access = AsyncMock()
        mock_scoped_access.user_id = "user123"
        mock_scoped_access.get_positions = AsyncMock(return_value=[])

        service = RiskService(mock_scoped_access)
        result = await service.get_risk_dashboard_data()

        assert result.risk_metrics == {}
        assert result.factor_exposures == []
        assert result.stress_tests == []
        assert result.var_history == []
        assert result.is_placeholder is False

    @pytest.mark.asyncio()
    async def test_get_risk_dashboard_data_permission_error_propagates(self):
        """Test get_risk_dashboard_data() propagates PermissionError."""
        mock_scoped_access = AsyncMock()
        mock_scoped_access.user_id = "user123"
        mock_scoped_access.get_positions = AsyncMock(
            side_effect=PermissionError("No authorized strategies")
        )

        service = RiskService(mock_scoped_access)

        with pytest.raises(PermissionError, match="No authorized strategies"):
            await service.get_risk_dashboard_data()

    @pytest.mark.asyncio()
    async def test_get_risk_dashboard_data_db_error_returns_empty(self):
        """Test get_risk_dashboard_data() handles DB errors gracefully."""
        mock_scoped_access = AsyncMock()
        mock_scoped_access.user_id = "user123"
        mock_scoped_access.get_positions = AsyncMock(side_effect=RuntimeError("DB unavailable"))

        service = RiskService(mock_scoped_access)
        result = await service.get_risk_dashboard_data()

        # Should return empty data instead of crashing
        assert result.risk_metrics == {}
        assert result.factor_exposures == []
        assert result.stress_tests == []
        assert result.var_history == []

    @pytest.mark.asyncio()
    async def test_get_risk_dashboard_data_happy_path(self):
        """Test get_risk_dashboard_data() with positions returns formatted data."""
        mock_scoped_access = AsyncMock()
        mock_scoped_access.user_id = "user123"
        mock_scoped_access.authorized_strategies = ["strategy1"]
        positions = [
            {"symbol": "AAPL", "market_value": 10000},
            {"symbol": "GOOGL", "market_value": 5000},
        ]
        mock_scoped_access.get_positions = AsyncMock(return_value=positions)
        mock_scoped_access.get_pnl_summary = AsyncMock(return_value=[])

        service = RiskService(mock_scoped_access)
        result = await service.get_risk_dashboard_data()

        # Should have stress tests (placeholder) and var history
        assert isinstance(result.stress_tests, list)
        assert isinstance(result.var_history, list)
        # Should be placeholder since risk model unavailable
        assert result.is_placeholder is True

    @pytest.mark.asyncio()
    async def test_get_risk_dashboard_data_zero_total_value_returns_empty(self):
        """Test get_risk_dashboard_data() with zero total value returns empty data."""
        mock_scoped_access = AsyncMock()
        mock_scoped_access.user_id = "user123"
        positions = [
            {"symbol": "AAPL", "market_value": 0},
            {"symbol": "GOOGL", "market_value": 0},
        ]
        mock_scoped_access.get_positions = AsyncMock(return_value=positions)

        service = RiskService(mock_scoped_access)
        result = await service.get_risk_dashboard_data()

        # Zero total value means empty weights, should return empty data
        assert result.risk_metrics == {}
        assert result.factor_exposures == []
        assert result.stress_tests == []
        assert result.var_history == []


class TestComputeTotalValue:
    """Tests for _compute_total_value() method."""

    def test_compute_total_value_normal_positions(self):
        """Test _compute_total_value() with normal positions."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        positions = [
            {"symbol": "AAPL", "market_value": 10000},
            {"symbol": "GOOGL", "market_value": 5000},
        ]

        total = service._compute_total_value(positions)
        assert total == 15000.0

    def test_compute_total_value_with_negative_values(self):
        """Test _compute_total_value() uses absolute values (short positions)."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        positions = [
            {"symbol": "AAPL", "market_value": 10000},
            {"symbol": "GOOGL", "market_value": -5000},
        ]

        # Should use abs() to get total notional exposure
        total = service._compute_total_value(positions)
        assert total == 15000.0

    def test_compute_total_value_with_none_values(self):
        """Test _compute_total_value() handles None market values."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        positions = [
            {"symbol": "AAPL", "market_value": 10000},
            {"symbol": "GOOGL", "market_value": None},
        ]

        total = service._compute_total_value(positions)
        assert total == 10000.0

    def test_compute_total_value_empty_positions(self):
        """Test _compute_total_value() with empty positions list."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        total = service._compute_total_value([])
        assert total == 0.0


class TestBuildWeights:
    """Tests for _build_weights() method."""

    def test_build_weights_normal_positions(self):
        """Test _build_weights() with normal positions."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        positions = [
            {"symbol": "AAPL", "market_value": 6000},
            {"symbol": "GOOGL", "market_value": 4000},
        ]

        weights = service._build_weights(positions, total_value=10000)

        assert weights == {"AAPL": 0.6, "GOOGL": 0.4}
        assert service._portfolio_value == 10000

    def test_build_weights_computes_total_if_not_provided(self):
        """Test _build_weights() computes total_value if None."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        positions = [
            {"symbol": "AAPL", "market_value": 6000},
            {"symbol": "GOOGL", "market_value": 4000},
        ]

        weights = service._build_weights(positions, total_value=None)

        assert weights == {"AAPL": 0.6, "GOOGL": 0.4}

    def test_build_weights_zero_total_value_returns_empty(self):
        """Test _build_weights() returns empty dict when total_value is zero."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        positions = [
            {"symbol": "AAPL", "market_value": 0},
        ]

        weights = service._build_weights(positions, total_value=0)

        assert weights == {}
        assert service._portfolio_value == 0

    def test_build_weights_missing_symbol_skipped(self):
        """Test _build_weights() skips positions without symbol."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        positions = [
            {"symbol": "AAPL", "market_value": 6000},
            {"market_value": 4000},  # Missing symbol
        ]

        weights = service._build_weights(positions, total_value=10000)

        assert weights == {"AAPL": 0.6}

    def test_build_weights_none_market_value_treated_as_zero(self):
        """Test _build_weights() treats None market_value as zero."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        positions = [
            {"symbol": "AAPL", "market_value": 10000},
            {"symbol": "GOOGL", "market_value": None},
        ]

        weights = service._build_weights(positions, total_value=10000)

        assert weights == {"AAPL": 1.0, "GOOGL": 0.0}


class TestComputeRiskMetrics:
    """Tests for _compute_risk_metrics() method."""

    @pytest.mark.asyncio()
    async def test_compute_risk_metrics_empty_weights_returns_none(self):
        """Test _compute_risk_metrics() returns None for empty weights."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        result = await service._compute_risk_metrics({})

        assert result is None

    @pytest.mark.asyncio()
    async def test_compute_risk_metrics_model_unavailable_returns_none(self):
        """Test _compute_risk_metrics() returns None when risk model unavailable."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        weights = {"AAPL": 0.6, "GOOGL": 0.4}
        result = await service._compute_risk_metrics(weights)

        # Risk model always returns None in MVP
        assert result is None

    @pytest.mark.asyncio()
    async def test_compute_risk_metrics_import_error_returns_none(self):
        """Test _compute_risk_metrics() handles ImportError gracefully."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        weights = {"AAPL": 0.6, "GOOGL": 0.4}

        with patch.object(service, "_load_risk_model", side_effect=ImportError("Module not found")):
            result = await service._compute_risk_metrics(weights)

        assert result is None

    @pytest.mark.asyncio()
    async def test_compute_risk_metrics_attribute_error_returns_none(self):
        """Test _compute_risk_metrics() handles AttributeError gracefully."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        weights = {"AAPL": 0.6, "GOOGL": 0.4}

        with patch.object(service, "_load_risk_model", side_effect=AttributeError("Missing attr")):
            result = await service._compute_risk_metrics(weights)

        assert result is None

    @pytest.mark.asyncio()
    async def test_compute_risk_metrics_key_error_returns_none(self):
        """Test _compute_risk_metrics() handles KeyError gracefully."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        weights = {"AAPL": 0.6, "GOOGL": 0.4}

        with patch.object(service, "_load_risk_model", side_effect=KeyError("Missing key")):
            result = await service._compute_risk_metrics(weights)

        assert result is None

    @pytest.mark.asyncio()
    async def test_compute_risk_metrics_value_error_returns_none(self):
        """Test _compute_risk_metrics() handles ValueError gracefully."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        weights = {"AAPL": 0.6, "GOOGL": 0.4}

        with patch.object(service, "_load_risk_model", side_effect=ValueError("Invalid value")):
            result = await service._compute_risk_metrics(weights)

        assert result is None


class TestLoadRiskModel:
    """Tests for _load_risk_model() method."""

    @pytest.mark.asyncio()
    async def test_load_risk_model_returns_none_in_mvp(self):
        """Test _load_risk_model() always returns None in MVP."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        result = await service._load_risk_model()

        # In MVP, always returns None
        assert result is None


class TestRunStressTests:
    """Tests for _run_stress_tests() method."""

    @pytest.mark.asyncio()
    async def test_run_stress_tests_empty_weights_returns_empty(self):
        """Test _run_stress_tests() returns empty list for empty weights."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        results, is_placeholder = await service._run_stress_tests({})

        assert results == []
        assert is_placeholder is False

    @pytest.mark.asyncio()
    async def test_run_stress_tests_model_unavailable_returns_placeholder(self):
        """Test _run_stress_tests() returns placeholder when model unavailable."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        weights = {"AAPL": 0.6, "GOOGL": 0.4}
        results, is_placeholder = await service._run_stress_tests(weights)

        # Model unavailable, should return placeholder
        assert len(results) > 0
        assert is_placeholder is True
        assert results[0]["scenario_name"] == "GFC_2008"

    @pytest.mark.asyncio()
    async def test_run_stress_tests_import_error_returns_placeholder(self):
        """Test _run_stress_tests() handles ImportError and returns placeholder."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        weights = {"AAPL": 0.6, "GOOGL": 0.4}

        # Patch the import statement to raise ImportError
        with patch("libs.trading.risk.stress_testing.StressTester", side_effect=ImportError):
            results, is_placeholder = await service._run_stress_tests(weights)

        assert len(results) > 0
        assert is_placeholder is True

    @pytest.mark.asyncio()
    async def test_run_stress_tests_attribute_error_returns_empty(self):
        """Test _run_stress_tests() handles AttributeError and returns empty."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        weights = {"AAPL": 0.6, "GOOGL": 0.4}

        # Mock load_risk_model to return a mock object, then have StressTester fail
        with patch.object(service, "_load_risk_model", return_value=Mock()):
            with patch("libs.trading.risk.stress_testing.StressTester", side_effect=AttributeError):
                results, is_placeholder = await service._run_stress_tests(weights)

        assert results == []
        assert is_placeholder is False

    @pytest.mark.asyncio()
    async def test_run_stress_tests_key_error_returns_empty(self):
        """Test _run_stress_tests() handles KeyError and returns empty."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        weights = {"AAPL": 0.6, "GOOGL": 0.4}

        # Mock load_risk_model to return a mock object, then have StressTester fail
        with patch.object(service, "_load_risk_model", return_value=Mock()):
            with patch("libs.trading.risk.stress_testing.StressTester", side_effect=KeyError):
                results, is_placeholder = await service._run_stress_tests(weights)

        assert results == []
        assert is_placeholder is False

    @pytest.mark.asyncio()
    async def test_run_stress_tests_value_error_returns_empty(self):
        """Test _run_stress_tests() handles ValueError and returns empty."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        weights = {"AAPL": 0.6, "GOOGL": 0.4}

        # Mock load_risk_model to return a mock object, then have StressTester fail
        with patch.object(service, "_load_risk_model", return_value=Mock()):
            with patch("libs.trading.risk.stress_testing.StressTester", side_effect=ValueError):
                results, is_placeholder = await service._run_stress_tests(weights)

        assert results == []
        assert is_placeholder is False


class TestGeneratePlaceholderStressTests:
    """Tests for _generate_placeholder_stress_tests() method."""

    def test_generate_placeholder_stress_tests_structure(self):
        """Test _generate_placeholder_stress_tests() returns valid structure."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        results = service._generate_placeholder_stress_tests()

        assert len(results) == 4
        assert results[0]["scenario_name"] == "GFC_2008"
        assert results[0]["scenario_type"] == "historical"
        assert "portfolio_pnl" in results[0]
        assert "factor_impacts" in results[0]
        assert isinstance(results[0]["factor_impacts"], dict)

    def test_generate_placeholder_stress_tests_contains_hypothetical(self):
        """Test _generate_placeholder_stress_tests() includes hypothetical scenario."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        results = service._generate_placeholder_stress_tests()

        hypothetical = [r for r in results if r["scenario_type"] == "hypothetical"]
        assert len(hypothetical) >= 1
        assert hypothetical[0]["scenario_name"] == "RATE_SHOCK"


class TestGetVarHistory:
    """Tests for _get_var_history() method."""

    @pytest.mark.asyncio()
    async def test_get_var_history_with_pnl_data(self):
        """Test _get_var_history() computes VaR from P&L data."""
        mock_scoped_access = AsyncMock()
        mock_scoped_access.user_id = "user123"
        mock_scoped_access.authorized_strategies = ["strategy1"]
        pnl_data = [
            {"trade_date": date(2025, 1, 1), "daily_pnl": 100},
            {"trade_date": date(2025, 1, 2), "daily_pnl": -50},
        ]
        mock_scoped_access.get_pnl_summary = AsyncMock(return_value=pnl_data)

        service = RiskService(mock_scoped_access)
        result = await service._get_var_history(days=30, portfolio_value=10000)

        assert len(result) == 2
        # VaR formula: |daily_pnl| / portfolio_value * 1.65
        assert result[0]["var_95"] == pytest.approx(abs(100) / 10000 * 1.65)
        assert result[1]["var_95"] == pytest.approx(abs(-50) / 10000 * 1.65)
        assert result[0]["date"] == date(2025, 1, 1)

    @pytest.mark.asyncio()
    async def test_get_var_history_empty_pnl_returns_empty(self):
        """Test _get_var_history() returns empty list when no P&L data."""
        mock_scoped_access = AsyncMock()
        mock_scoped_access.user_id = "user123"
        mock_scoped_access.authorized_strategies = ["strategy1"]
        mock_scoped_access.get_pnl_summary = AsyncMock(return_value=[])

        service = RiskService(mock_scoped_access)
        result = await service._get_var_history(days=30, portfolio_value=10000)

        assert result == []

    @pytest.mark.asyncio()
    async def test_get_var_history_permission_error_propagates(self):
        """Test _get_var_history() propagates PermissionError."""
        mock_scoped_access = AsyncMock()
        mock_scoped_access.user_id = "user123"
        mock_scoped_access.authorized_strategies = ["strategy1"]
        mock_scoped_access.get_pnl_summary = AsyncMock(side_effect=PermissionError("No access"))

        service = RiskService(mock_scoped_access)

        with pytest.raises(PermissionError, match="No access"):
            await service._get_var_history(days=30, portfolio_value=10000)

    @pytest.mark.asyncio()
    async def test_get_var_history_runtime_error_returns_empty(self):
        """Test _get_var_history() handles RuntimeError gracefully."""
        mock_scoped_access = AsyncMock()
        mock_scoped_access.user_id = "user123"
        mock_scoped_access.authorized_strategies = ["strategy1"]
        mock_scoped_access.get_pnl_summary = AsyncMock(side_effect=RuntimeError("DB pool is None"))

        service = RiskService(mock_scoped_access)
        result = await service._get_var_history(days=30, portfolio_value=10000)

        assert result == []

    @pytest.mark.asyncio()
    async def test_get_var_history_attribute_error_returns_empty(self):
        """Test _get_var_history() handles AttributeError gracefully."""
        mock_scoped_access = AsyncMock()
        mock_scoped_access.user_id = "user123"
        mock_scoped_access.authorized_strategies = ["strategy1"]
        mock_scoped_access.get_pnl_summary = AsyncMock(
            side_effect=AttributeError("Missing attribute")
        )

        service = RiskService(mock_scoped_access)
        result = await service._get_var_history(days=30, portfolio_value=10000)

        assert result == []

    @pytest.mark.asyncio()
    async def test_get_var_history_generic_exception_returns_empty(self):
        """Test _get_var_history() handles generic exceptions gracefully."""
        mock_scoped_access = AsyncMock()
        mock_scoped_access.user_id = "user123"
        mock_scoped_access.authorized_strategies = ["strategy1"]
        mock_scoped_access.get_pnl_summary = AsyncMock(side_effect=Exception("Unexpected error"))

        service = RiskService(mock_scoped_access)
        result = await service._get_var_history(days=30, portfolio_value=10000)

        assert result == []

    @pytest.mark.asyncio()
    async def test_get_var_history_zero_portfolio_value_returns_zero_var(self):
        """Test _get_var_history() returns zero VaR when portfolio_value is zero."""
        mock_scoped_access = AsyncMock()
        mock_scoped_access.user_id = "user123"
        mock_scoped_access.authorized_strategies = ["strategy1"]
        pnl_data = [
            {"trade_date": date(2025, 1, 1), "daily_pnl": 100},
        ]
        mock_scoped_access.get_pnl_summary = AsyncMock(return_value=pnl_data)

        service = RiskService(mock_scoped_access)
        result = await service._get_var_history(days=30, portfolio_value=0)

        assert len(result) == 1
        assert result[0]["var_95"] == 0.0

    @pytest.mark.asyncio()
    async def test_get_var_history_none_portfolio_value_returns_zero_var(self):
        """Test _get_var_history() returns zero VaR when portfolio_value is None."""
        mock_scoped_access = AsyncMock()
        mock_scoped_access.user_id = "user123"
        mock_scoped_access.authorized_strategies = ["strategy1"]
        pnl_data = [
            {"trade_date": date(2025, 1, 1), "daily_pnl": 100},
        ]
        mock_scoped_access.get_pnl_summary = AsyncMock(return_value=pnl_data)

        service = RiskService(mock_scoped_access)
        result = await service._get_var_history(days=30, portfolio_value=None)

        assert len(result) == 1
        assert result[0]["var_95"] == 0.0

    @pytest.mark.asyncio()
    async def test_get_var_history_none_daily_pnl_treated_as_zero(self):
        """Test _get_var_history() treats None daily_pnl as zero."""
        mock_scoped_access = AsyncMock()
        mock_scoped_access.user_id = "user123"
        mock_scoped_access.authorized_strategies = ["strategy1"]
        pnl_data = [
            {"trade_date": date(2025, 1, 1), "daily_pnl": None},
        ]
        mock_scoped_access.get_pnl_summary = AsyncMock(return_value=pnl_data)

        service = RiskService(mock_scoped_access)
        result = await service._get_var_history(days=30, portfolio_value=10000)

        assert len(result) == 1
        assert result[0]["var_95"] == 0.0

    @pytest.mark.asyncio()
    async def test_get_var_history_sorts_by_date_ascending(self):
        """Test _get_var_history() sorts results by date ascending."""
        mock_scoped_access = AsyncMock()
        mock_scoped_access.user_id = "user123"
        mock_scoped_access.authorized_strategies = ["strategy1"]
        pnl_data = [
            {"trade_date": date(2025, 1, 3), "daily_pnl": 30},
            {"trade_date": date(2025, 1, 1), "daily_pnl": 10},
            {"trade_date": date(2025, 1, 2), "daily_pnl": 20},
        ]
        mock_scoped_access.get_pnl_summary = AsyncMock(return_value=pnl_data)

        service = RiskService(mock_scoped_access)
        result = await service._get_var_history(days=30, portfolio_value=10000)

        assert len(result) == 3
        assert result[0]["date"] == date(2025, 1, 1)
        assert result[1]["date"] == date(2025, 1, 2)
        assert result[2]["date"] == date(2025, 1, 3)

    @pytest.mark.asyncio()
    async def test_get_var_history_handles_none_dates(self):
        """Test _get_var_history() handles None dates gracefully."""
        mock_scoped_access = AsyncMock()
        mock_scoped_access.user_id = "user123"
        mock_scoped_access.authorized_strategies = ["strategy1"]
        pnl_data = [
            {"trade_date": date(2025, 1, 2), "daily_pnl": 20},
            {"trade_date": None, "daily_pnl": 10},
        ]
        mock_scoped_access.get_pnl_summary = AsyncMock(return_value=pnl_data)

        service = RiskService(mock_scoped_access)
        result = await service._get_var_history(days=30, portfolio_value=10000)

        # Should not crash, should sort None to beginning
        assert len(result) == 2
        assert result[0]["date"] is None or result[0]["date"] == date.min

    @pytest.mark.asyncio()
    async def test_get_var_history_calculates_limit_correctly(self):
        """Test _get_var_history() calculates query limit based on strategies."""
        mock_scoped_access = AsyncMock()
        mock_scoped_access.user_id = "user123"
        mock_scoped_access.authorized_strategies = ["strategy1", "strategy2", "strategy3"]
        mock_scoped_access.get_pnl_summary = AsyncMock(return_value=[])

        service = RiskService(mock_scoped_access)
        await service._get_var_history(days=30, portfolio_value=10000)

        # Should pass limit = days * num_strategies = 30 * 3 = 90
        call_args = mock_scoped_access.get_pnl_summary.call_args
        assert call_args.kwargs.get("limit") == 90


class TestFormatRiskMetrics:
    """Tests for _format_risk_metrics() method."""

    def test_format_risk_metrics_with_valid_result(self):
        """Test _format_risk_metrics() formats valid risk result."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        risk_result = {
            "total_risk": 0.05,
            "factor_risk": 0.03,
            "specific_risk": 0.02,
            "var_95": 0.018,
            "var_99": 0.025,
            "cvar_95": 0.021,
        }

        formatted = service._format_risk_metrics(risk_result)

        assert formatted["total_risk"] == 0.05
        assert formatted["factor_risk"] == 0.03
        assert formatted["specific_risk"] == 0.02
        assert formatted["var_95"] == 0.018
        assert formatted["var_99"] == 0.025
        assert formatted["cvar_95"] == 0.021

    def test_format_risk_metrics_with_none_result_returns_empty(self):
        """Test _format_risk_metrics() returns empty dict for None result."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        formatted = service._format_risk_metrics(None)

        # Should return empty dict, not zeroed values (safety)
        assert formatted == {}

    def test_format_risk_metrics_with_missing_fields_uses_defaults(self):
        """Test _format_risk_metrics() uses defaults for missing fields."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        risk_result = {
            "total_risk": 0.05,
            # Missing other fields
        }

        formatted = service._format_risk_metrics(risk_result)

        assert formatted["total_risk"] == 0.05
        assert formatted["factor_risk"] == 0.0
        assert formatted["specific_risk"] == 0.0


class TestFormatFactorExposures:
    """Tests for _format_factor_exposures() method."""

    def test_format_factor_exposures_with_none_result_returns_zeros(self):
        """Test _format_factor_exposures() returns zeros for None result."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        formatted = service._format_factor_exposures(None)

        assert len(formatted) == len(DEFAULT_FACTOR_ORDER)
        assert all(exp["exposure"] == 0.0 for exp in formatted)

    def test_format_factor_exposures_with_none_factor_contributions(self):
        """Test _format_factor_exposures() handles None factor_contributions."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        risk_result = {"factor_contributions": None}

        formatted = service._format_factor_exposures(risk_result)

        assert len(formatted) == len(DEFAULT_FACTOR_ORDER)
        assert all(exp["exposure"] == 0.0 for exp in formatted)

    def test_format_factor_exposures_missing_iter_rows_returns_zeros(self):
        """Test _format_factor_exposures() handles missing iter_rows method."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        risk_result = {"factor_contributions": {}}  # Dict doesn't have iter_rows

        formatted = service._format_factor_exposures(risk_result)

        assert len(formatted) == len(DEFAULT_FACTOR_ORDER)
        assert all(exp["exposure"] == 0.0 for exp in formatted)

    def test_format_factor_exposures_with_valid_data(self):
        """Test _format_factor_exposures() formats valid factor contributions."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        mock_contributions = Mock()
        mock_contributions.iter_rows = Mock(
            return_value=iter(
                [
                    {"factor_name": "momentum_12_1", "percent_contribution": 0.3},
                    {"factor_name": "realized_vol", "percent_contribution": 0.2},
                ]
            )
        )

        risk_result = {"factor_contributions": mock_contributions}

        formatted = service._format_factor_exposures(risk_result)

        # Should have all canonical factors
        assert len(formatted) == len(DEFAULT_FACTOR_ORDER)
        # Check specific values
        momentum_exp = next(exp for exp in formatted if exp["factor_name"] == "momentum_12_1")
        assert momentum_exp["exposure"] == 0.3
        vol_exp = next(exp for exp in formatted if exp["factor_name"] == "realized_vol")
        assert vol_exp["exposure"] == 0.2
        # Other factors should be 0
        market_cap_exp = next(exp for exp in formatted if exp["factor_name"] == "log_market_cap")
        assert market_cap_exp["exposure"] == 0.0


class TestFormatStressTests:
    """Tests for _format_stress_tests() method."""

    def test_format_stress_tests_with_empty_results(self):
        """Test _format_stress_tests() returns empty list for empty input."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        formatted = service._format_stress_tests([])

        assert formatted == []

    def test_format_stress_tests_with_valid_results(self):
        """Test _format_stress_tests() formats valid stress test results."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        stress_results = [
            {
                "scenario_name": "GFC_2008",
                "scenario_type": "historical",
                "portfolio_pnl": -0.18,
                "factor_impacts": {"momentum_12_1": -0.05},
            },
            {
                "scenario_name": "COVID_2020",
                "scenario_type": "historical",
                "portfolio_pnl": -0.14,
                "factor_impacts": {"realized_vol": -0.04},
            },
        ]

        formatted = service._format_stress_tests(stress_results)

        assert len(formatted) == 2
        assert formatted[0]["scenario_name"] == "GFC_2008"
        assert formatted[0]["scenario_type"] == "historical"
        assert formatted[0]["portfolio_pnl"] == -0.18
        assert formatted[0]["factor_impacts"] == {"momentum_12_1": -0.05}

    def test_format_stress_tests_with_missing_fields_uses_defaults(self):
        """Test _format_stress_tests() uses defaults for missing fields."""
        mock_scoped_access = Mock()
        service = RiskService(mock_scoped_access)

        stress_results = [
            {
                # Missing all fields
            }
        ]

        formatted = service._format_stress_tests(stress_results)

        assert len(formatted) == 1
        assert formatted[0]["scenario_name"] == "Unknown"
        assert formatted[0]["scenario_type"] == "hypothetical"
        assert formatted[0]["portfolio_pnl"] == 0.0
        assert formatted[0]["factor_impacts"] == {}


class TestIntegrationFlow:
    """Integration-style tests that exercise full code paths."""

    @pytest.mark.asyncio()
    async def test_full_flow_with_positions_and_pnl(self):
        """Test full get_risk_dashboard_data flow with positions and P&L data."""
        mock_scoped_access = AsyncMock()
        mock_scoped_access.user_id = "user123"
        mock_scoped_access.authorized_strategies = ["strategy1", "strategy2"]

        positions = [
            {"symbol": "AAPL", "market_value": 10000},
            {"symbol": "GOOGL", "market_value": 5000},
            {"symbol": "MSFT", "market_value": 3000},
        ]

        pnl_data = [
            {"trade_date": date(2025, 1, 1), "daily_pnl": 100},
            {"trade_date": date(2025, 1, 2), "daily_pnl": -50},
            {"trade_date": date(2025, 1, 3), "daily_pnl": 75},
        ]

        mock_scoped_access.get_positions = AsyncMock(return_value=positions)
        mock_scoped_access.get_pnl_summary = AsyncMock(return_value=pnl_data)

        service = RiskService(mock_scoped_access)
        result = await service.get_risk_dashboard_data()

        # Verify structure
        assert isinstance(result, RiskDashboardData)
        # Should have placeholder stress tests
        assert len(result.stress_tests) > 0
        assert result.stress_tests[0]["scenario_name"] == "GFC_2008"
        # Should have VaR history
        assert len(result.var_history) == 3
        assert result.var_history[0]["date"] == date(2025, 1, 1)
        # Should be placeholder mode (no risk model)
        assert result.is_placeholder is True

    @pytest.mark.asyncio()
    async def test_full_flow_exercises_all_format_methods(self):
        """Test that all formatting methods are called in the flow."""
        mock_scoped_access = AsyncMock()
        mock_scoped_access.user_id = "user123"
        mock_scoped_access.authorized_strategies = ["strategy1"]

        positions = [{"symbol": "AAPL", "market_value": 10000}]
        pnl_data = [{"trade_date": date(2025, 1, 1), "daily_pnl": 100}]

        mock_scoped_access.get_positions = AsyncMock(return_value=positions)
        mock_scoped_access.get_pnl_summary = AsyncMock(return_value=pnl_data)

        service = RiskService(mock_scoped_access)

        # Spy on format methods to verify they're called
        with patch.object(
            service, "_format_risk_metrics", wraps=service._format_risk_metrics
        ) as mock_format_risk:
            with patch.object(
                service, "_format_factor_exposures", wraps=service._format_factor_exposures
            ) as mock_format_factors:
                with patch.object(
                    service, "_format_stress_tests", wraps=service._format_stress_tests
                ) as mock_format_stress:
                    result = await service.get_risk_dashboard_data()

        # Verify all format methods were called
        mock_format_risk.assert_called_once()
        mock_format_factors.assert_called_once()
        mock_format_stress.assert_called_once()

        # Verify result structure
        assert isinstance(result, RiskDashboardData)
