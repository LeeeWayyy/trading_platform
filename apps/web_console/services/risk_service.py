"""Risk analytics service layer using libs/risk/ infrastructure.

This service provides risk analytics for the web console dashboard by:
1. Fetching position data via StrategyScopedDataAccess (strategy-scoped)
2. Computing risk metrics using libs/risk/ (BarraRiskModel, StressTester)
3. Formatting results for dashboard display

Data Sources:
- Positions: StrategyScopedDataAccess.get_positions()
- P&L History: StrategyScopedDataAccess.get_pnl_summary()
- Risk Model: libs/risk/barra_model.py (requires pre-computed artifacts)
- Stress Tests: libs/risk/stress_testing.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from libs.risk.factor_covariance import CANONICAL_FACTOR_ORDER

# Ensure asset_growth is present for UI parity/testing expectations
DEFAULT_FACTOR_ORDER = [
    "log_market_cap",
    "book_to_market",
    "momentum_12_1",
    "realized_vol",
    "roe",
    "asset_growth",
]
_canonical_factors = list(dict.fromkeys((CANONICAL_FACTOR_ORDER or []) + DEFAULT_FACTOR_ORDER))

if TYPE_CHECKING:
    from apps.web_console.data.strategy_scoped_queries import StrategyScopedDataAccess

logger = logging.getLogger(__name__)


@dataclass
class RiskDashboardData:
    """Container for risk dashboard data.

    All data is pre-formatted for dashboard display. Empty containers
    indicate no data available (not errors).

    NOTE: When is_placeholder is True, the data is simulated/demo and should
    NOT be used for trading decisions. The UI must display appropriate warnings.
    """

    risk_metrics: dict[str, float]
    """Risk metrics: total_risk, factor_risk, specific_risk, var_95, var_99, cvar_95"""

    factor_exposures: list[dict[str, Any]]
    """Factor exposures: [{factor_name, exposure}, ...]"""

    stress_tests: list[dict[str, Any]]
    """Stress test results: [{scenario_name, portfolio_pnl, factor_impacts}, ...]"""

    var_history: list[dict[str, Any]]
    """VaR history: [{date, var_95}, ...]"""

    is_placeholder: bool = False
    """True if data is simulated/demo due to missing risk model artifacts."""

    placeholder_reason: str = ""
    """Human-readable reason why placeholder data is being shown."""


class RiskService:
    """Service for fetching and computing risk analytics.

    Uses StrategyScopedDataAccess for position data with strategy-level
    access control. Risk computations use libs/risk/ infrastructure.

    Note: This service is designed to work in async context. Use run_async()
    from async_helpers.py when calling from sync Streamlit code.
    """

    # Pre-computed risk model artifacts paths (relative to data/)
    RISK_ARTIFACTS_PATH = "artifacts/risk"

    def __init__(self, scoped_access: StrategyScopedDataAccess):
        """Initialize risk service with scoped data access.

        Args:
            scoped_access: StrategyScopedDataAccess instance with user context
        """
        self._scoped_access = scoped_access

    async def get_risk_dashboard_data(self) -> RiskDashboardData:
        """Fetch all risk data for dashboard.

        This is the main entry point for the risk dashboard page. It fetches
        positions, computes risk metrics, runs stress tests, and returns
        formatted data ready for display.

        Returns:
            RiskDashboardData with metrics, exposures, stress tests, history.
            Empty containers if no positions or risk data available.

        Raises:
            PermissionError: If user has no strategy access (propagated from
                StrategyScopedDataAccess)
        """
        # Get positions via strategy-scoped access
        # Handle database connectivity failures gracefully to keep dashboard functional
        try:
            positions = await self._scoped_access.get_positions(limit=1000)
        except PermissionError:
            # Re-raise permission errors - these should propagate to caller
            raise
        except Exception as e:
            # Database unreachable, pool unavailable, or other connectivity issues
            # Return empty positions to show placeholder dashboard instead of crashing
            logger.warning(
                "risk_dashboard_db_error",
                extra={
                    "user_id": self._scoped_access.user_id,
                    "error_type": type(e).__name__,
                    "error": str(e),
                },
            )
            positions = []

        if not positions:
            logger.info(
                "risk_dashboard_no_positions",
                extra={"user_id": self._scoped_access.user_id},
            )
            return RiskDashboardData(
                risk_metrics={},
                factor_exposures=[],
                stress_tests=[],
                var_history=[],
            )

        # Build portfolio weights from positions
        total_value = self._compute_total_value(positions)
        weights = self._build_weights(positions, total_value)

        if not weights:
            return RiskDashboardData(
                risk_metrics={},
                factor_exposures=[],
                stress_tests=[],
                var_history=[],
            )

        # Try to compute risk metrics using libs/risk/
        # This requires pre-computed risk model artifacts
        risk_result = await self._compute_risk_metrics(weights)

        # Run stress tests
        stress_results, stress_is_placeholder = await self._run_stress_tests(weights)

        # Get VaR history from P&L data
        var_history = await self._get_var_history(portfolio_value=total_value)

        # Determine if we're showing placeholder data
        is_placeholder = risk_result is None or stress_is_placeholder
        placeholder_reason = ""
        if risk_result is None:
            placeholder_reason = "Risk model artifacts not available. Showing demo data."
        elif stress_is_placeholder:
            placeholder_reason = "Stress test scenarios are simulated (model unavailable)."

        return RiskDashboardData(
            risk_metrics=self._format_risk_metrics(risk_result),
            factor_exposures=self._format_factor_exposures(risk_result),
            stress_tests=self._format_stress_tests(stress_results),
            var_history=var_history,
            is_placeholder=is_placeholder,
            placeholder_reason=placeholder_reason,
        )

    def _compute_total_value(self, positions: list[dict[str, Any]]) -> float:
        """Compute absolute portfolio notional for scaling risk metrics."""
        return float(sum(abs(p.get("market_value") or 0) for p in positions))

    def _build_weights(
        self, positions: list[dict[str, Any]], total_value: float | None = None
    ) -> dict[str, float]:
        """Build portfolio weights from positions and cache notional.

        Args:
            positions: List of position dicts from StrategyScopedDataAccess
            total_value: Optional pre-computed absolute notional

        Returns:
            Dict mapping symbol to weight (market_value / total_value)
        """
        if total_value is None:
            total_value = self._compute_total_value(positions)

        # Cache for downstream VaR scaling
        self._portfolio_value = total_value

        if total_value == 0:
            return {}

        return {
            p["symbol"]: (p.get("market_value") or 0) / total_value
            for p in positions
            if p.get("symbol")
        }

    async def _compute_risk_metrics(self, weights: dict[str, float]) -> dict[str, Any] | None:
        """Compute risk decomposition using libs/risk/.

        Attempts to load pre-computed risk model artifacts and compute
        portfolio risk. Returns None if artifacts not available.

        Args:
            weights: Portfolio weights dict (symbol -> weight)

        Returns:
            PortfolioRiskResult dict or None if risk model unavailable
        """
        if not weights:
            return None

        try:
            # Try to load pre-computed risk model
            risk_model = await self._load_risk_model()

            if risk_model is None:
                logger.warning(
                    "risk_model_not_available",
                    extra={"reason": "Pre-computed artifacts not found"},
                )
                return None

            # Convert symbol weights to permno weights for risk model
            # This requires a symbol-to-permno mapping from the positions
            # For now, return None if we can't map - real implementation
            # would use a lookup table
            logger.info(
                "risk_computation_skipped",
                extra={"reason": "Symbol-to-permno mapping not implemented"},
            )
            return None

        except ImportError:
            logger.warning("risk_model_import_failed", exc_info=True)
            return None
        except Exception:
            logger.exception("risk_computation_failed")
            return None

    async def _load_risk_model(self) -> Any | None:
        """Load pre-computed BarraRiskModel from artifacts.

        The risk model requires:
        - Factor covariance matrix
        - Factor loadings for all stocks
        - Specific risk estimates

        These are computed by the risk pipeline (T2.2/T2.3) and stored
        in data/analytics/.

        Returns:
            BarraRiskModel instance or None if not available
        """
        # In production, this would load from parquet files:
        # - data/analytics/factor_covariance.parquet
        # - data/analytics/factor_loadings.parquet
        # - data/analytics/specific_risk.parquet
        #
        # For MVP, return None to indicate model not available.
        # The dashboard will show appropriate messaging.
        return None

    async def _run_stress_tests(
        self, weights: dict[str, float]
    ) -> tuple[list[dict[str, Any]], bool]:
        """Run predefined stress scenarios.

        Attempts to run stress tests using libs/risk/stress_testing.py.
        Returns empty list if stress testing unavailable.

        Args:
            weights: Portfolio weights dict

        Returns:
            Tuple of (results, is_placeholder):
            - results: List of StressTestResult dicts or empty list
            - is_placeholder: True if results are simulated/demo data
        """
        if not weights:
            return [], False

        try:
            # Import stress testing
            from libs.risk.stress_testing import StressTester

            # Load risk model for stress testing
            risk_model = await self._load_risk_model()

            if risk_model is None:
                # Return mock stress test results for demonstration
                # In production, this would require the real risk model
                return self._generate_placeholder_stress_tests(), True

            # Run stress tests with the model
            _ = StressTester(risk_model)  # placeholder until scenarios wired
            # Note: run_all_scenarios is sync, would need to wrap
            return [], False

        except ImportError:
            logger.warning("stress_testing_import_failed", exc_info=True)
            return self._generate_placeholder_stress_tests(), True
        except Exception:
            logger.exception("stress_testing_failed")
            return [], False

    def _generate_placeholder_stress_tests(self) -> list[dict[str, Any]]:
        """Generate placeholder stress test results for UI demonstration.

        This is used when the full risk model is not available. The UI
        can still be tested with realistic-looking data structure.
        """
        return [
            {
                "scenario_name": "GFC_2008",
                "scenario_type": "historical",
                "portfolio_pnl": -0.182,
                "factor_impacts": {
                    "book_to_market": -0.08,
                    "realized_vol": -0.05,
                    "momentum_12_1": -0.03,
                },
            },
            {
                "scenario_name": "COVID_2020",
                "scenario_type": "historical",
                "portfolio_pnl": -0.145,
                "factor_impacts": {
                    "momentum_12_1": -0.06,
                    "realized_vol": -0.04,
                    "log_market_cap": -0.02,
                },
            },
            {
                "scenario_name": "RATE_HIKE_2022",
                "scenario_type": "historical",
                "portfolio_pnl": -0.098,
                "factor_impacts": {
                    "book_to_market": 0.02,
                    "roe": -0.05,
                    "momentum_12_1": -0.04,
                },
            },
            {
                "scenario_name": "RATE_SHOCK",
                "scenario_type": "hypothetical",
                "portfolio_pnl": -0.125,
                "factor_impacts": {
                    "book_to_market": 0.03,
                    "momentum_12_1": -0.08,
                    "realized_vol": -0.05,
                },
            },
        ]

    async def _get_var_history(
        self, days: int = 30, portfolio_value: float | None = None
    ) -> list[dict[str, Any]]:
        """Get VaR history proxy from P&L data.

        NOTE: This is a simplified VaR approximation for MVP dashboard display,
        NOT a proper statistical VaR calculation. It uses |daily_return| * 1.65
        as a proxy, which treats gains and losses symmetrically. A proper VaR
        implementation would use rolling historical percentile or parametric
        estimation with volatility forecasting.

        TODO: Replace with proper rolling VaR calculation (T6.4 or follow-up task)

        Args:
            days: Number of days of history to return
            portfolio_value: Absolute portfolio notional for percentage scaling.
                If None or zero, returns 0.0 for safe UI display.

        Returns:
            List of {date, var_95, daily_pnl} dicts sorted by date ascending
        """
        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        # Scale limit by authorized strategies count since get_pnl_summary returns
        # one row per strategy per day (similar to ComparisonService logic)
        num_strategies = len(self._scoped_access.authorized_strategies)
        query_limit = days * max(num_strategies, 1)

        try:
            pnl_data = await self._scoped_access.get_pnl_summary(
                start_date, end_date, limit=query_limit
            )
        except PermissionError:
            # User has no strategy access - propagate
            raise
        except (RuntimeError, AttributeError):
            # db_pool is None - no direct DB access
            return []
        except Exception:
            logger.exception("pnl_fetch_failed")
            return []

        if not pnl_data:
            return []

        # Compute simplified VaR proxy from daily P&L
        # Formula: |daily_return| * 1.65 (95% one-sided normal quantile)
        # This is NOT proper VaR - see docstring for limitations
        var_history = []
        for record in pnl_data:
            daily_pnl = record.get("daily_pnl") or 0
            # Scale by portfolio notional to get percentage-based metric
            if portfolio_value and portfolio_value > 0:
                var_95 = abs(daily_pnl) / portfolio_value * 1.65
            else:
                # If portfolio notional is unavailable, we cannot calculate a percentage-based VaR.
                # Returning 0.0 is a safe fallback, which the UI will correctly display as 0.00%.
                # This ensures data contract consistency between service and UI.
                var_95 = 0.0

            var_history.append(
                {
                    "date": record.get("trade_date"),
                    "var_95": var_95,
                    "daily_pnl": daily_pnl,
                }
            )

        # Sort by date ascending for charting
        # Use date.min as fallback to avoid TypeError from mixing date and str types
        var_history.sort(key=lambda x: x.get("date") or date.min)

        return var_history

    def _format_risk_metrics(self, risk_result: dict[str, Any] | None) -> dict[str, float]:
        """Format risk metrics for dashboard display.

        Args:
            risk_result: PortfolioRiskResult dict or None

        Returns:
            Formatted metrics dict with required keys
        """
        if not risk_result:
            # Return placeholder metrics when risk model unavailable
            # Real implementation would return empty dict and let UI handle
            return {
                "total_risk": 0.0,
                "factor_risk": 0.0,
                "specific_risk": 0.0,
                "var_95": 0.0,
                "var_99": 0.0,
                "cvar_95": 0.0,
            }

        return {
            "total_risk": float(risk_result.get("total_risk", 0)),
            "factor_risk": float(risk_result.get("factor_risk", 0)),
            "specific_risk": float(risk_result.get("specific_risk", 0)),
            "var_95": float(risk_result.get("var_95", 0)),
            "var_99": float(risk_result.get("var_99", 0)),
            "cvar_95": float(risk_result.get("cvar_95", 0)),
        }

    def _format_factor_exposures(self, risk_result: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Format factor exposures for dashboard display.

        Args:
            risk_result: PortfolioRiskResult dict or None

        Returns:
            List of {factor_name, exposure} dicts in canonical order
        """
        if not risk_result:
            # Return placeholder exposures when risk model unavailable
            return [{"factor_name": factor, "exposure": 0.0} for factor in _canonical_factors]

        factor_contributions = risk_result.get("factor_contributions")
        if factor_contributions is None:
            return [{"factor_name": factor, "exposure": 0.0} for factor in _canonical_factors]

        # Extract exposures from factor contributions DataFrame
        # Assuming it has factor_name and percent_contribution columns
        # Add a type check to ensure it's a DataFrame-like object
        if not hasattr(factor_contributions, "iter_rows"):
            logger.warning("factor_contributions_missing_iter_rows")
            return [{"factor_name": factor, "exposure": 0.0} for factor in _canonical_factors]

        # Build lookup dict for O(M+N) instead of O(M*N) nested loop
        contribution_map = {
            row.get("factor_name"): float(row.get("percent_contribution", 0))
            for row in factor_contributions.iter_rows(named=True)
        }

        return [
            {"factor_name": factor, "exposure": contribution_map.get(factor, 0.0)}
            for factor in _canonical_factors
        ]

    def _format_stress_tests(self, stress_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Format stress test results for dashboard display.

        Args:
            stress_results: List of StressTestResult dicts

        Returns:
            Formatted list ready for display
        """
        if not stress_results:
            return []

        formatted = []
        for result in stress_results:
            formatted.append(
                {
                    "scenario_name": result.get("scenario_name", "Unknown"),
                    "scenario_type": result.get("scenario_type", "hypothetical"),
                    "portfolio_pnl": float(result.get("portfolio_pnl", 0)),
                    "factor_impacts": result.get("factor_impacts", {}),
                }
            )

        return formatted


__all__ = [
    "RiskService",
    "RiskDashboardData",
]
