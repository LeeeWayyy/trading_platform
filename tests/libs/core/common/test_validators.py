"""
Unit tests for libs.core.common.validators.

Tests cover:
- Risk metrics validation (combined and section-specific)
- Factor exposure validation (boolean and filter)
- Stress test validation (boolean and filter)
- VaR history validation (filter)
- Edge cases (None, empty lists, missing keys, None values)

Target: 50%+ branch coverage (baseline from 0%)
"""

from libs.core.common.validators import (
    EXPOSURE_REQUIRED_KEYS,
    RISK_METRICS_REQUIRED_KEYS,
    RISK_OVERVIEW_REQUIRED_KEYS,
    STRESS_TEST_REQUIRED_KEYS,
    VAR_HISTORY_REQUIRED_KEYS,
    VAR_METRICS_REQUIRED_KEYS,
    validate_exposure_list,
    validate_exposures,
    validate_overview_metrics,
    validate_risk_metrics,
    validate_stress_test_list,
    validate_stress_tests,
    validate_var_history,
    validate_var_metrics,
)


class TestRiskMetricsValidation:
    """Tests for risk metrics validation functions."""

    def test_validate_risk_metrics_with_all_required_keys(self):
        """Test risk metrics validation passes with all required keys."""
        data = {"total_risk": 100.0, "var_95": 50.0, "var_99": 75.0, "cvar_95": 60.0}

        result = validate_risk_metrics(data)

        assert result is True

    def test_validate_risk_metrics_with_missing_key(self):
        """Test risk metrics validation fails when required key missing."""
        data = {"total_risk": 100.0, "var_95": 50.0, "var_99": 75.0}  # Missing cvar_95

        result = validate_risk_metrics(data)

        assert result is False

    def test_validate_risk_metrics_with_none_value(self):
        """Test risk metrics validation fails when required key is None."""
        data = {"total_risk": 100.0, "var_95": None, "var_99": 75.0, "cvar_95": 60.0}

        result = validate_risk_metrics(data)

        assert result is False

    def test_validate_risk_metrics_with_none_data(self):
        """Test risk metrics validation fails with None data."""
        result = validate_risk_metrics(None)

        assert result is False

    def test_validate_risk_metrics_with_empty_dict(self):
        """Test risk metrics validation fails with empty dict."""
        result = validate_risk_metrics({})

        assert result is False

    def test_validate_risk_metrics_with_extra_keys(self):
        """Test risk metrics validation passes with extra keys (extensibility)."""
        data = {
            "total_risk": 100.0,
            "var_95": 50.0,
            "var_99": 75.0,
            "cvar_95": 60.0,
            "factor_risk": 80.0,  # Extra key
            "specific_risk": 20.0,  # Extra key
        }

        result = validate_risk_metrics(data)

        assert result is True


class TestOverviewMetricsValidation:
    """Tests for risk overview metrics validation (section-specific)."""

    def test_validate_overview_metrics_with_total_risk(self):
        """Test overview metrics validation passes with total_risk."""
        data = {"total_risk": 100.0}

        result = validate_overview_metrics(data)

        assert result is True

    def test_validate_overview_metrics_with_total_risk_and_extras(self):
        """Test overview metrics validation passes with total_risk and optional keys."""
        data = {"total_risk": 100.0, "factor_risk": 80.0, "specific_risk": 20.0}

        result = validate_overview_metrics(data)

        assert result is True

    def test_validate_overview_metrics_without_total_risk(self):
        """Test overview metrics validation fails without total_risk."""
        data = {"factor_risk": 80.0, "specific_risk": 20.0}

        result = validate_overview_metrics(data)

        assert result is False

    def test_validate_overview_metrics_with_none_total_risk(self):
        """Test overview metrics validation fails when total_risk is None."""
        data = {"total_risk": None}

        result = validate_overview_metrics(data)

        assert result is False

    def test_validate_overview_metrics_with_none_data(self):
        """Test overview metrics validation fails with None data."""
        result = validate_overview_metrics(None)

        assert result is False


class TestVarMetricsValidation:
    """Tests for VaR metrics validation (section-specific)."""

    def test_validate_var_metrics_with_all_var_keys(self):
        """Test VaR metrics validation passes with all VaR keys."""
        data = {"var_95": 50.0, "var_99": 75.0, "cvar_95": 60.0}

        result = validate_var_metrics(data)

        assert result is True

    def test_validate_var_metrics_with_missing_var_key(self):
        """Test VaR metrics validation fails with missing VaR key."""
        data = {"var_95": 50.0, "var_99": 75.0}  # Missing cvar_95

        result = validate_var_metrics(data)

        assert result is False

    def test_validate_var_metrics_with_none_var_value(self):
        """Test VaR metrics validation fails when VaR value is None."""
        data = {"var_95": 50.0, "var_99": None, "cvar_95": 60.0}

        result = validate_var_metrics(data)

        assert result is False

    def test_validate_var_metrics_with_total_risk_only(self):
        """Test VaR metrics validation fails with total_risk only (wrong section)."""
        data = {"total_risk": 100.0}

        result = validate_var_metrics(data)

        assert result is False


class TestExposureValidation:
    """Tests for factor exposure validation functions."""

    def test_validate_exposure_list_with_valid_entries(self):
        """Test exposure list validation passes with valid entries."""
        exposures = [
            {"factor_name": "Market", "exposure": 0.5},
            {"factor_name": "Size", "exposure": -0.2},
        ]

        result = validate_exposure_list(exposures)

        assert result is True

    def test_validate_exposure_list_with_empty_list(self):
        """Test exposure list validation passes with empty list (valid for UI)."""
        result = validate_exposure_list([])

        assert result is True

    def test_validate_exposure_list_with_none(self):
        """Test exposure list validation fails with None (missing data)."""
        result = validate_exposure_list(None)

        assert result is False

    def test_validate_exposure_list_with_missing_key(self):
        """Test exposure list validation fails when entry missing required key."""
        exposures = [{"factor_name": "Market"}]  # Missing 'exposure'

        result = validate_exposure_list(exposures)

        assert result is False

    def test_validate_exposure_list_with_non_dict_entry(self):
        """Test exposure list validation fails with non-dict entry."""
        exposures = [{"factor_name": "Market", "exposure": 0.5}, "invalid_entry"]

        result = validate_exposure_list(exposures)

        assert result is False

    def test_validate_exposures_filter_with_valid_entries(self):
        """Test exposures filter returns valid entries unchanged."""
        exposures = [
            {"factor_name": "Market", "exposure": 0.5},
            {"factor_name": "Size", "exposure": -0.2},
        ]

        result = validate_exposures(exposures)

        assert result == exposures
        assert len(result) == 2

    def test_validate_exposures_filter_with_mixed_valid_invalid(self):
        """Test exposures filter removes invalid entries."""
        exposures = [
            {"factor_name": "Market", "exposure": 0.5},  # Valid
            {"factor_name": "Size"},  # Invalid (missing exposure)
            {"factor_name": "Value", "exposure": 0.3},  # Valid
        ]

        result = validate_exposures(exposures)

        assert len(result) == 2
        assert result[0]["factor_name"] == "Market"
        assert result[1]["factor_name"] == "Value"

    def test_validate_exposures_filter_with_empty_list(self):
        """Test exposures filter returns empty list unchanged."""
        result = validate_exposures([])

        assert result == []

    def test_validate_exposures_filter_with_all_invalid(self):
        """Test exposures filter returns empty list when all invalid."""
        exposures = [{"factor_name": "Market"}, {"exposure": 0.5}]  # Both invalid

        result = validate_exposures(exposures)

        assert result == []


class TestStressTestValidation:
    """Tests for stress test validation functions."""

    def test_validate_stress_test_list_with_valid_entries(self):
        """Test stress test list validation passes with valid entries."""
        results = [
            {"scenario_name": "Market Crash", "portfolio_pnl": -5000.0},
            {"scenario_name": "Rate Hike", "portfolio_pnl": -2000.0},
        ]

        result = validate_stress_test_list(results)

        assert result is True

    def test_validate_stress_test_list_with_empty_list(self):
        """Test stress test list validation passes with empty list."""
        result = validate_stress_test_list([])

        assert result is True

    def test_validate_stress_test_list_with_none(self):
        """Test stress test list validation fails with None."""
        result = validate_stress_test_list(None)

        assert result is False

    def test_validate_stress_test_list_with_missing_key(self):
        """Test stress test list validation fails when entry missing key."""
        results = [{"scenario_name": "Market Crash"}]  # Missing portfolio_pnl

        result = validate_stress_test_list(results)

        assert result is False

    def test_validate_stress_tests_filter_with_valid_entries(self):
        """Test stress tests filter returns valid entries unchanged."""
        results = [
            {"scenario_name": "Market Crash", "portfolio_pnl": -5000.0},
            {"scenario_name": "Rate Hike", "portfolio_pnl": -2000.0},
        ]

        result = validate_stress_tests(results)

        assert result == results
        assert len(result) == 2

    def test_validate_stress_tests_filter_with_mixed_valid_invalid(self):
        """Test stress tests filter removes invalid entries."""
        results = [
            {"scenario_name": "Market Crash", "portfolio_pnl": -5000.0},  # Valid
            {"scenario_name": "Rate Hike"},  # Invalid (missing portfolio_pnl)
            {"scenario_name": "Inflation", "portfolio_pnl": -1000.0},  # Valid
        ]

        result = validate_stress_tests(results)

        assert len(result) == 2
        assert result[0]["scenario_name"] == "Market Crash"
        assert result[1]["scenario_name"] == "Inflation"

    def test_validate_stress_tests_filter_with_empty_list(self):
        """Test stress tests filter returns empty list unchanged."""
        result = validate_stress_tests([])

        assert result == []


class TestVarHistoryValidation:
    """Tests for VaR history validation filter."""

    def test_validate_var_history_with_valid_entries(self):
        """Test VaR history filter returns valid entries unchanged."""
        history = [
            {"date": "2025-01-10", "var_95": 50.0},
            {"date": "2025-01-11", "var_95": 52.0},
        ]

        result = validate_var_history(history)

        assert result == history
        assert len(result) == 2

    def test_validate_var_history_with_mixed_valid_invalid(self):
        """Test VaR history filter removes invalid entries."""
        history = [
            {"date": "2025-01-10", "var_95": 50.0},  # Valid
            {"date": "2025-01-11"},  # Invalid (missing var_95)
            {"date": "2025-01-12", "var_95": 54.0},  # Valid
        ]

        result = validate_var_history(history)

        assert len(result) == 2
        assert result[0]["date"] == "2025-01-10"
        assert result[1]["date"] == "2025-01-12"

    def test_validate_var_history_with_empty_list(self):
        """Test VaR history filter returns empty list unchanged."""
        result = validate_var_history([])

        assert result == []

    def test_validate_var_history_with_all_invalid(self):
        """Test VaR history filter returns empty list when all invalid."""
        history = [{"date": "2025-01-10"}, {"var_95": 50.0}]  # Both invalid

        result = validate_var_history(history)

        assert result == []


class TestRequiredKeysConstants:
    """Tests for required keys constant definitions."""

    def test_risk_metrics_required_keys(self):
        """Test RISK_METRICS_REQUIRED_KEYS contains expected keys."""
        assert "total_risk" in RISK_METRICS_REQUIRED_KEYS
        assert "var_95" in RISK_METRICS_REQUIRED_KEYS
        assert "var_99" in RISK_METRICS_REQUIRED_KEYS
        assert "cvar_95" in RISK_METRICS_REQUIRED_KEYS

    def test_risk_overview_required_keys(self):
        """Test RISK_OVERVIEW_REQUIRED_KEYS contains total_risk only."""
        assert "total_risk" in RISK_OVERVIEW_REQUIRED_KEYS
        assert len(RISK_OVERVIEW_REQUIRED_KEYS) == 1

    def test_var_metrics_required_keys(self):
        """Test VAR_METRICS_REQUIRED_KEYS contains VaR keys only."""
        assert "var_95" in VAR_METRICS_REQUIRED_KEYS
        assert "var_99" in VAR_METRICS_REQUIRED_KEYS
        assert "cvar_95" in VAR_METRICS_REQUIRED_KEYS
        assert "total_risk" not in VAR_METRICS_REQUIRED_KEYS

    def test_exposure_required_keys(self):
        """Test EXPOSURE_REQUIRED_KEYS contains expected keys."""
        assert "factor_name" in EXPOSURE_REQUIRED_KEYS
        assert "exposure" in EXPOSURE_REQUIRED_KEYS

    def test_stress_test_required_keys(self):
        """Test STRESS_TEST_REQUIRED_KEYS contains expected keys."""
        assert "scenario_name" in STRESS_TEST_REQUIRED_KEYS
        assert "portfolio_pnl" in STRESS_TEST_REQUIRED_KEYS

    def test_var_history_required_keys(self):
        """Test VAR_HISTORY_REQUIRED_KEYS contains expected keys."""
        assert "date" in VAR_HISTORY_REQUIRED_KEYS
        assert "var_95" in VAR_HISTORY_REQUIRED_KEYS
