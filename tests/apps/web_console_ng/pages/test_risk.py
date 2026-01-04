"""Unit tests for risk dashboard page.

Tests structural validation of the risk page module.
NOTE: Full feature flag and permission gating tests require NiceGUI fixtures
and are covered in e2e/ directory, not here.
"""

from __future__ import annotations

from apps.web_console_ng.pages import risk as risk_module


class TestRiskPageStructure:
    """Test structural validation of risk dashboard module."""

    def test_risk_dashboard_function_exists(self) -> None:
        """Verify risk_dashboard function is defined and async."""
        assert hasattr(risk_module, "risk_dashboard")
        assert callable(risk_module.risk_dashboard)

    def test_risk_dashboard_is_async(self) -> None:
        """Verify risk_dashboard is an async function."""
        import asyncio

        assert asyncio.iscoroutinefunction(risk_module.risk_dashboard)

    def test_feature_flag_constant_exists(self) -> None:
        """Verify FEATURE_RISK_DASHBOARD flag is defined in config."""
        from apps.web_console_ng.config import FEATURE_RISK_DASHBOARD

        # Should be a boolean
        assert isinstance(FEATURE_RISK_DASHBOARD, bool)

    def test_refresh_interval_defined(self) -> None:
        """Verify RISK_REFRESH_INTERVAL_SECONDS is defined in risk module."""
        # Defined in risk.py module, not config
        assert hasattr(risk_module, "RISK_REFRESH_INTERVAL_SECONDS")
        interval = risk_module.RISK_REFRESH_INTERVAL_SECONDS

        # Should be a positive number
        assert isinstance(interval, int | float)
        assert interval > 0

    def test_risk_module_imports_required_components(self) -> None:
        """Verify risk module imports all required chart components."""
        # These imports should not raise
        from apps.web_console_ng.components.factor_exposure_chart import (
            render_factor_exposure,
        )
        from apps.web_console_ng.components.stress_test_results import (
            render_stress_tests,
        )
        from apps.web_console_ng.components.var_chart import (
            render_var_gauge,
            render_var_history,
            render_var_metrics,
        )

        assert callable(render_factor_exposure)
        assert callable(render_var_metrics)
        assert callable(render_var_gauge)
        assert callable(render_var_history)
        assert callable(render_stress_tests)
