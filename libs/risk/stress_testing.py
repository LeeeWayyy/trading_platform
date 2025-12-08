"""
Portfolio stress testing using factor model.

This module provides stress testing capabilities for portfolios using
the Barra-style risk model from T2.3.

Key features:
- Historical scenarios (GFC 2008, COVID 2020, Rate Hike 2022)
- Hypothetical scenarios (custom factor shocks)
- Position-level attribution
- Optional specific risk estimation

All computations integrate with BarraRiskModel.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import numpy as np
import polars as pl

if TYPE_CHECKING:
    from libs.risk.barra_model import BarraRiskModel

logger = logging.getLogger(__name__)


class MissingHistoricalDataError(Exception):
    """Raised when historical factor returns are missing for a scenario."""

    pass


@dataclass
class StressScenario:
    """
    Definition of a stress scenario.

    Supports both historical (replay actual factor returns) and
    hypothetical (user-defined factor shocks) scenarios.
    """

    name: str  # 'GFC_2008', 'COVID_2020', 'RATE_SHOCK'
    scenario_type: str  # 'historical', 'hypothetical'
    description: str

    # Factor shocks (for hypothetical scenarios)
    factor_shocks: dict[str, float] | None = None  # factor_name -> shock (as return)

    # Historical period (for historical scenarios)
    start_date: date | None = None
    end_date: date | None = None

    def validate(self) -> list[str]:
        """Validate scenario configuration."""
        errors: list[str] = []

        if self.scenario_type not in ["historical", "hypothetical"]:
            errors.append(
                f"Unknown scenario_type: {self.scenario_type}. "
                "Must be 'historical' or 'hypothetical'."
            )

        if self.scenario_type == "historical":
            if self.start_date is None or self.end_date is None:
                errors.append(
                    "Historical scenarios require start_date and end_date."
                )
            elif self.start_date > self.end_date:
                errors.append(
                    f"start_date ({self.start_date}) > end_date ({self.end_date})"
                )

        if self.scenario_type == "hypothetical":
            if not self.factor_shocks:
                errors.append("Hypothetical scenarios require factor_shocks.")

        return errors


@dataclass
class StressTestResult:
    """
    Result of stress testing a portfolio.

    All P&L values are expressed as returns (fractions, not percentages).
    """

    test_id: str  # UUID
    portfolio_id: str
    scenario_name: str
    scenario_type: str
    as_of_date: date

    # Impact metrics
    portfolio_pnl: float  # Estimated P&L under scenario (factor component)
    specific_risk_estimate: float  # Optional specific risk contribution
    total_pnl: float  # portfolio_pnl + specific_risk_estimate

    # Factor attribution
    factor_impacts: dict[str, float]  # factor -> contribution to P&L

    # Position-level detail
    worst_position_permno: int | None
    worst_position_loss: float | None
    position_impacts: pl.DataFrame | None  # permno, pnl, contribution

    # Provenance
    model_version: str
    dataset_version_ids: dict[str, str]
    computation_timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_storage_format(self) -> pl.DataFrame:
        """
        Convert to storage format matching P4T2_TASK.md schema.

        Returns:
            DataFrame with stress test results
        """
        version_str = "|".join(
            f"{k}:{v}" for k, v in sorted(self.dataset_version_ids.items())
        )

        # Serialize factor impacts
        factor_impacts_str = "|".join(
            f"{k}:{v:.6f}" for k, v in sorted(self.factor_impacts.items())
        )

        return pl.DataFrame(
            {
                "test_id": [self.test_id],
                "portfolio_id": [self.portfolio_id],
                "scenario_name": [self.scenario_name],
                "scenario_type": [self.scenario_type],
                "as_of_date": [self.as_of_date],
                "portfolio_pnl": [self.portfolio_pnl],
                "specific_risk_estimate": [self.specific_risk_estimate],
                "total_pnl": [self.total_pnl],
                "factor_impacts": [factor_impacts_str],
                "worst_position_permno": [self.worst_position_permno],
                "worst_position_loss": [self.worst_position_loss],
                "model_version": [self.model_version],
                "dataset_version_id": [version_str],
                "computation_timestamp": [self.computation_timestamp],
            }
        )


class StressTester:
    """
    Portfolio stress testing using factor model.

    Supports:
    - Historical scenarios (replay actual factor returns)
    - Hypothetical scenarios (user-defined factor shocks)
    - Position-level attribution

    Example:
        risk_model = BarraRiskModel.from_t22_results(...)
        tester = StressTester(risk_model, historical_factor_returns)

        # Run pre-defined scenario
        result = tester.run_stress_test(portfolio, "GFC_2008")

        # Run custom scenario
        result = tester.run_custom_scenario(
            portfolio,
            {"market_beta": -0.20, "volatility": -0.10},
            "custom_crash"
        )
    """

    # Pre-defined scenarios (3 historical + 1 hypothetical)
    PREDEFINED_SCENARIOS: dict[str, StressScenario] = {
        # Historical scenario 1: Global Financial Crisis
        "GFC_2008": StressScenario(
            name="GFC_2008",
            scenario_type="historical",
            description="Global Financial Crisis: Sep-Nov 2008",
            start_date=date(2008, 9, 1),
            end_date=date(2008, 11, 30),
        ),
        # Historical scenario 2: COVID-19 Market Crash
        "COVID_2020": StressScenario(
            name="COVID_2020",
            scenario_type="historical",
            description="COVID-19 Market Crash: Feb-Mar 2020",
            start_date=date(2020, 2, 19),
            end_date=date(2020, 3, 23),
        ),
        # Historical scenario 3: 2022 Rate Hike / Inflation Shock
        "RATE_HIKE_2022": StressScenario(
            name="RATE_HIKE_2022",
            scenario_type="historical",
            description="2022 Fed Rate Hikes & Inflation: Jan-Jun 2022",
            start_date=date(2022, 1, 3),
            end_date=date(2022, 6, 16),
        ),
        # Hypothetical scenario: Rate shock with rotation
        # Uses canonical factor names: momentum_12_1, book_to_market, roe, log_market_cap, realized_vol
        "RATE_SHOCK": StressScenario(
            name="RATE_SHOCK",
            scenario_type="hypothetical",
            description="Hypothetical rate shock: value up, growth down",
            factor_shocks={
                "book_to_market": 0.05,  # Value up 5%
                "momentum_12_1": -0.10,  # Momentum down 10%
                "log_market_cap": -0.05,  # Large cap down 5%
                "roe": -0.03,  # Quality down 3%
                "realized_vol": -0.08,  # Low vol up (high vol down 8%)
            },
        ),
    }

    def __init__(
        self,
        risk_model: "BarraRiskModel",
        historical_factor_returns: pl.DataFrame | None = None,
    ):
        """
        Initialize stress tester with risk model.

        Args:
            risk_model: BarraRiskModel from T2.3
            historical_factor_returns: Optional DataFrame with historical
                factor returns. Required for historical scenarios.
                Schema: date, factor_name, return
        """
        self.risk_model = risk_model
        self.historical_returns = historical_factor_returns

        # Validate historical returns schema if provided
        if historical_factor_returns is not None:
            required_cols = {"date", "factor_name", "return"}
            actual_cols = set(historical_factor_returns.columns)
            if not required_cols.issubset(actual_cols):
                raise ValueError(
                    f"historical_factor_returns must have columns {required_cols}, "
                    f"got {actual_cols}"
                )

    def run_stress_test(
        self,
        portfolio: pl.DataFrame,
        scenario: str | StressScenario,
        portfolio_id: str | None = None,
        include_specific_risk: bool = False,
    ) -> StressTestResult:
        """
        Run stress test for a single scenario.

        Args:
            portfolio: DataFrame with permno, weight columns
            scenario: Scenario name (str) or StressScenario object
            portfolio_id: Optional portfolio identifier
            include_specific_risk: If True, include conservative 2-sigma
                specific risk estimate

        Returns:
            StressTestResult with P&L and attribution
        """
        # Resolve scenario
        if isinstance(scenario, str):
            if scenario not in self.PREDEFINED_SCENARIOS:
                raise ValueError(
                    f"Unknown scenario: {scenario}. "
                    f"Available: {list(self.PREDEFINED_SCENARIOS.keys())}"
                )
            scenario_obj = self.PREDEFINED_SCENARIOS[scenario]
        else:
            scenario_obj = scenario

        # Validate scenario
        errors = scenario_obj.validate()
        if errors:
            raise ValueError(f"Invalid scenario: {errors}")

        # Compute stress based on scenario type
        if scenario_obj.scenario_type == "historical":
            factor_pnl, factor_impacts, specific_estimate = (
                self._compute_historical_stress(
                    portfolio, scenario_obj, include_specific_risk
                )
            )
        else:
            factor_pnl, factor_impacts, specific_estimate = (
                self._compute_hypothetical_stress(
                    portfolio,
                    scenario_obj.factor_shocks or {},
                    include_specific_risk,
                )
            )

        # Compute position-level impacts
        position_impacts = self._compute_position_impacts(
            portfolio, scenario_obj, factor_impacts
        )

        # Find worst position
        worst_permno: int | None = None
        worst_loss: float | None = None
        if position_impacts is not None and position_impacts.height > 0:
            worst_row = position_impacts.sort("pnl").head(1)
            if worst_row.height > 0:
                worst_permno = int(worst_row["permno"][0])
                worst_loss = float(worst_row["pnl"][0])

        total_pnl = factor_pnl + specific_estimate

        return StressTestResult(
            test_id=str(uuid.uuid4()),
            portfolio_id=portfolio_id or "unknown",
            scenario_name=scenario_obj.name,
            scenario_type=scenario_obj.scenario_type,
            as_of_date=self.risk_model.as_of_date,
            portfolio_pnl=factor_pnl,
            specific_risk_estimate=specific_estimate,
            total_pnl=total_pnl,
            factor_impacts=factor_impacts,
            worst_position_permno=worst_permno,
            worst_position_loss=worst_loss,
            position_impacts=position_impacts,
            model_version=self.risk_model.model_version,
            dataset_version_ids=self.risk_model.dataset_version_ids.copy(),
        )

    def run_all_scenarios(
        self,
        portfolio: pl.DataFrame,
        portfolio_id: str | None = None,
        include_specific_risk: bool = False,
    ) -> list[StressTestResult]:
        """
        Run all pre-defined scenarios.

        Args:
            portfolio: DataFrame with permno, weight columns
            portfolio_id: Optional portfolio identifier
            include_specific_risk: If True, include conservative specific
                risk estimate

        Returns:
            List of StressTestResult for each scenario
        """
        results: list[StressTestResult] = []

        for scenario_name, scenario_obj in self.PREDEFINED_SCENARIOS.items():
            # Skip historical scenarios if no historical data
            if (
                scenario_obj.scenario_type == "historical"
                and self.historical_returns is None
            ):
                logger.warning(
                    f"Skipping historical scenario {scenario_name}: "
                    "no historical factor returns provided"
                )
                continue

            try:
                result = self.run_stress_test(
                    portfolio,
                    scenario_obj,
                    portfolio_id,
                    include_specific_risk,
                )
                results.append(result)
            except MissingHistoricalDataError as e:
                logger.warning(f"Skipping scenario {scenario_name}: {e}")
                continue

        return results

    def run_custom_scenario(
        self,
        portfolio: pl.DataFrame,
        factor_shocks: dict[str, float],
        scenario_name: str = "custom",
        portfolio_id: str | None = None,
        include_specific_risk: bool = False,
    ) -> StressTestResult:
        """
        Run custom hypothetical scenario.

        Args:
            portfolio: DataFrame with permno, weight columns
            factor_shocks: factor_name -> shock (as return, e.g., -0.10 = -10%)
            scenario_name: Name for the custom scenario
            portfolio_id: Optional portfolio identifier
            include_specific_risk: If True, include conservative specific
                risk estimate

        Returns:
            StressTestResult with P&L and attribution
        """
        scenario = StressScenario(
            name=scenario_name,
            scenario_type="hypothetical",
            description=f"Custom scenario: {scenario_name}",
            factor_shocks=factor_shocks,
        )

        return self.run_stress_test(
            portfolio, scenario, portfolio_id, include_specific_risk
        )

    def get_available_scenarios(self) -> list[str]:
        """Get list of available pre-defined scenarios."""
        return list(self.PREDEFINED_SCENARIOS.keys())

    # ========================================================================
    # Private Helper Methods
    # ========================================================================

    def _compute_historical_stress(
        self,
        portfolio: pl.DataFrame,
        scenario: StressScenario,
        include_specific_risk: bool = False,
    ) -> tuple[float, dict[str, float], float]:
        """
        Compute P&L using historical factor returns.

        P&L = sum over factors: exposure_k * cumulative_factor_return_k

        Scaling: Factor returns are compounded over the scenario period.
        No annualization - returns are as-realized over the period.

        Args:
            portfolio: DataFrame with permno, weight
            scenario: Historical scenario with date range
            include_specific_risk: If True, add conservative specific risk estimate

        Returns:
            Tuple of (total_factor_pnl, factor_pnl_dict, specific_risk_estimate)
        """
        if self.historical_returns is None:
            raise MissingHistoricalDataError(
                "Historical factor returns required for historical scenarios"
            )

        # Get factor returns for scenario period
        period_returns = self.historical_returns.filter(
            (pl.col("date") >= scenario.start_date)
            & (pl.col("date") <= scenario.end_date)
        )

        if period_returns.height == 0:
            raise MissingHistoricalDataError(
                f"No factor returns found for period "
                f"{scenario.start_date} to {scenario.end_date}"
            )

        # Aggregate factor returns over period (compound returns)
        cumulative_factor_returns = period_returns.group_by("factor_name").agg(
            # Compound returns: prod(1 + r) - 1
            ((pl.col("return") + 1).product() - 1).alias("cumulative_return")
        )

        # Convert to dict for lookup
        factor_return_map: dict[str, float] = {}
        for row in cumulative_factor_returns.iter_rows(named=True):
            factor_return_map[row["factor_name"]] = row["cumulative_return"]

        # Compute portfolio factor exposures: f = B' @ w
        exposures = self._compute_portfolio_exposures(portfolio)

        # Factor P&L contribution
        factor_pnl: dict[str, float] = {}
        total_factor_pnl = 0.0

        for factor_name in self.risk_model.factor_names:
            # Handle missing factor returns gracefully
            factor_return = factor_return_map.get(factor_name)
            if factor_return is None:
                logger.warning(
                    f"No historical returns for factor {factor_name}, assuming 0"
                )
                factor_return = 0.0

            exposure = exposures.get(factor_name, 0.0)
            contribution = exposure * factor_return
            factor_pnl[factor_name] = contribution
            total_factor_pnl += contribution

        # Optional: Add specific risk estimate (conservative 2-sigma tail)
        specific_estimate = 0.0
        if include_specific_risk:
            specific_estimate = self._estimate_specific_stress(portfolio, scenario)

        return total_factor_pnl, factor_pnl, specific_estimate

    def _compute_hypothetical_stress(
        self,
        portfolio: pl.DataFrame,
        factor_shocks: dict[str, float],
        include_specific_risk: bool = False,
    ) -> tuple[float, dict[str, float], float]:
        """
        Compute P&L using hypothetical factor shocks.

        P&L = sum over factors: exposure_k * shock_k

        Missing factors: Factors not in factor_shocks are assumed to have 0 shock.
        This is logged as a warning.

        Args:
            portfolio: DataFrame with permno, weight
            factor_shocks: factor_name -> shock (as return, e.g., -0.10 = -10%)
            include_specific_risk: If True, add conservative specific risk estimate

        Returns:
            Tuple of (total_factor_pnl, factor_pnl_dict, specific_risk_estimate)
        """
        exposures = self._compute_portfolio_exposures(portfolio)

        # Warn about factors with exposure but no shock
        for factor_name in self.risk_model.factor_names:
            exposure = exposures.get(factor_name, 0.0)
            if factor_name not in factor_shocks and abs(exposure) > 0.01:
                logger.warning(
                    f"Factor {factor_name} has exposure {exposure:.3f} "
                    f"but no shock defined, assuming 0"
                )

        factor_pnl: dict[str, float] = {}
        total_factor_pnl = 0.0

        for factor_name, shock in factor_shocks.items():
            if factor_name not in self.risk_model.factor_names:
                logger.warning(
                    f"Unknown factor in shocks: {factor_name}, skipping"
                )
                continue
            exposure = exposures.get(factor_name, 0.0)
            contribution = exposure * shock
            factor_pnl[factor_name] = contribution
            total_factor_pnl += contribution

        # For factors in model but not in shocks, record 0 contribution
        for factor_name in self.risk_model.factor_names:
            if factor_name not in factor_pnl:
                factor_pnl[factor_name] = 0.0

        # Optional: Add specific risk estimate (hypothetical 2-sigma tail)
        specific_estimate = 0.0
        if include_specific_risk:
            # Assume 1-day shock for hypothetical, scaled by 2-sigma
            specific_estimate = self._estimate_hypothetical_specific_risk(portfolio)

        return total_factor_pnl, factor_pnl, specific_estimate

    def _compute_portfolio_exposures(
        self, portfolio: pl.DataFrame
    ) -> dict[str, float]:
        """
        Compute portfolio factor exposures.

        f_k = sum_i(w_i * B_ik) for each factor k

        Args:
            portfolio: DataFrame with permno, weight

        Returns:
            Dict of factor_name -> portfolio exposure
        """
        # Get portfolio weights
        portfolio_permnos = portfolio["permno"].to_list()
        portfolio_weights = portfolio["weight"].to_numpy().astype(np.float64)

        # Get factor loadings for portfolio permnos
        loadings_permnos = set(self.risk_model.factor_loadings["permno"].to_list())

        # Filter to covered permnos
        covered_mask = [p in loadings_permnos for p in portfolio_permnos]
        covered_permnos = [
            p for p, m in zip(portfolio_permnos, covered_mask, strict=False) if m
        ]
        covered_weights = portfolio_weights[covered_mask]

        if len(covered_permnos) == 0:
            logger.warning("No portfolio positions have factor loadings")
            return {f: 0.0 for f in self.risk_model.factor_names}

        # Use actual weights (preserve leverage) - do NOT normalize
        # Stress testing requires stricter coverage than optimization (95% vs 80%)
        # because partial coverage underestimates factor exposures and P&L
        #
        # Use GROSS exposure (sum of abs weights) for coverage calculation
        # to properly handle long/short portfolios where net exposure ≈ 0
        gross_weight = float(np.sum(np.abs(portfolio_weights)))
        covered_gross = float(np.sum(np.abs(covered_weights)))

        if gross_weight < 1e-10:
            raise ValueError(
                "Portfolio has zero gross exposure - cannot compute stress test"
            )

        coverage_pct = covered_gross / gross_weight

        # Require 95% coverage for stress tests (stricter than optimizer's 80%)
        min_stress_coverage = 0.95
        if coverage_pct < min_stress_coverage:
            raise ValueError(
                f"Insufficient factor coverage for stress testing: {coverage_pct:.1%} "
                f"(minimum {min_stress_coverage:.0%} required). "
                f"Covered gross weight: {covered_gross:.3f} of {gross_weight:.3f}"
            )

        # Log info about coverage for audit trail
        if abs(covered_gross - gross_weight) > 0.001:
            logger.info(
                f"Stress test factor coverage: {coverage_pct:.1%} of gross exposure "
                f"({covered_gross:.3f} of {gross_weight:.3f}) have factor loadings"
            )

        # Get factor loadings matrix (N × K)
        loadings_df = (
            self.risk_model.factor_loadings.filter(
                pl.col("permno").is_in(covered_permnos)
            )
            .sort("permno")
            .select(self.risk_model.factor_names)
        )

        # Sort weights to match loadings order
        permno_to_weight = dict(
            zip(covered_permnos, covered_weights.tolist(), strict=False)
        )
        sorted_permnos = sorted(covered_permnos)
        aligned_weights = np.array([permno_to_weight[p] for p in sorted_permnos])

        # Compute exposures: f = w' @ B (for each factor)
        B = loadings_df.to_numpy().astype(np.float64)
        exposures_vec = aligned_weights @ B

        return dict(zip(self.risk_model.factor_names, exposures_vec.tolist(), strict=False))

    def _estimate_specific_stress(
        self,
        portfolio: pl.DataFrame,
        scenario: StressScenario,
    ) -> float:
        """
        Conservative estimate of specific risk contribution.

        Uses portfolio specific variance scaled by period length and 2-sigma multiplier.

        specific_stress = -2 * sqrt(sum(w_i^2 * spec_var_i) * n_days)
        """
        # Get portfolio positions
        portfolio_permnos = portfolio["permno"].to_list()
        portfolio_weights = portfolio["weight"].to_numpy().astype(np.float64)

        # Get specific variances for covered positions
        specific_permnos = set(self.risk_model.specific_risks["permno"].to_list())

        # Filter to covered
        covered_mask = [p in specific_permnos for p in portfolio_permnos]
        covered_permnos = [
            p for p, m in zip(portfolio_permnos, covered_mask, strict=False) if m
        ]
        covered_weights = portfolio_weights[covered_mask]

        if len(covered_permnos) == 0:
            return 0.0

        # Get specific variances aligned to portfolio order
        specific_df = (
            self.risk_model.specific_risks.filter(
                pl.col("permno").is_in(covered_permnos)
            )
            .sort("permno")
        )

        # Create lookup
        permno_to_var: dict[int, float] = dict(
            zip(
                specific_df["permno"].to_list(),
                specific_df["specific_variance"].to_list(),
                strict=False,
            )
        )

        sorted_permnos = sorted(covered_permnos)
        specific_vars = np.array([permno_to_var[p] for p in sorted_permnos])

        # Align weights
        permno_to_weight = dict(
            zip(covered_permnos, covered_weights.tolist(), strict=False)
        )
        aligned_weights = np.array([permno_to_weight[p] for p in sorted_permnos])

        # Portfolio specific variance (daily)
        port_specific_var = float(np.sum(aligned_weights**2 * specific_vars))

        # Scale by period length
        if scenario.start_date and scenario.end_date:
            n_days = (scenario.end_date - scenario.start_date).days
        else:
            n_days = 1

        period_specific_var = port_specific_var * n_days

        # 2-sigma tail estimate (conservative downside)
        return float(-2.0 * np.sqrt(period_specific_var))

    def _estimate_hypothetical_specific_risk(
        self,
        portfolio: pl.DataFrame,
    ) -> float:
        """
        Conservative estimate of specific risk for hypothetical scenario.

        Assumes 1-day shock, scaled by 2-sigma.

        specific_stress = -2 * sqrt(sum(w_i^2 * spec_var_i))
        """
        # Get portfolio positions
        portfolio_permnos = portfolio["permno"].to_list()
        portfolio_weights = portfolio["weight"].to_numpy().astype(np.float64)

        # Get specific variances for covered positions
        specific_permnos = set(self.risk_model.specific_risks["permno"].to_list())

        # Filter to covered
        covered_mask = [p in specific_permnos for p in portfolio_permnos]
        covered_permnos = [
            p for p, m in zip(portfolio_permnos, covered_mask, strict=False) if m
        ]
        covered_weights = portfolio_weights[covered_mask]

        if len(covered_permnos) == 0:
            return 0.0

        # Get specific variances aligned to portfolio order
        specific_df = (
            self.risk_model.specific_risks.filter(
                pl.col("permno").is_in(covered_permnos)
            )
            .sort("permno")
        )

        # Create lookup
        permno_to_var: dict[int, float] = dict(
            zip(
                specific_df["permno"].to_list(),
                specific_df["specific_variance"].to_list(),
                strict=False,
            )
        )

        sorted_permnos = sorted(covered_permnos)
        specific_vars = np.array([permno_to_var[p] for p in sorted_permnos])

        # Align weights
        permno_to_weight = dict(
            zip(covered_permnos, covered_weights.tolist(), strict=False)
        )
        aligned_weights = np.array([permno_to_weight[p] for p in sorted_permnos])

        # Portfolio specific variance (daily)
        port_specific_var = float(np.sum(aligned_weights**2 * specific_vars))

        # 2-sigma tail estimate (conservative downside)
        return float(-2.0 * np.sqrt(port_specific_var))

    def _compute_position_impacts(
        self,
        portfolio: pl.DataFrame,
        scenario: StressScenario,
        factor_impacts: dict[str, float],
    ) -> pl.DataFrame | None:
        """
        Compute position-level P&L impacts.

        For each position: pnl_i = w_i * sum_k(B_ik * shock_k)

        Args:
            portfolio: DataFrame with permno, weight
            scenario: The stress scenario
            factor_impacts: Pre-computed factor P&L contributions

        Returns:
            DataFrame with permno, pnl, contribution columns
        """
        # Get portfolio positions
        portfolio_permnos = portfolio["permno"].to_list()
        portfolio_weights_list = portfolio["weight"].to_list()

        # Get factor loadings for portfolio permnos
        loadings_permnos = set(self.risk_model.factor_loadings["permno"].to_list())

        # Get factor shocks
        if scenario.scenario_type == "hypothetical":
            factor_shocks = scenario.factor_shocks or {}
        else:
            # For historical, we need to get cumulative returns
            if self.historical_returns is None:
                return None

            period_returns = self.historical_returns.filter(
                (pl.col("date") >= scenario.start_date)
                & (pl.col("date") <= scenario.end_date)
            )

            cumulative_factor_returns = period_returns.group_by("factor_name").agg(
                ((pl.col("return") + 1).product() - 1).alias("cumulative_return")
            )

            factor_shocks = {}
            for row in cumulative_factor_returns.iter_rows(named=True):
                factor_shocks[row["factor_name"]] = row["cumulative_return"]

        # Build position impacts
        position_data: list[dict[str, float | int]] = []

        for permno, weight in zip(portfolio_permnos, portfolio_weights_list, strict=False):
            if permno not in loadings_permnos:
                # No loadings, skip
                continue

            # Get factor loadings for this position
            loadings_row = self.risk_model.factor_loadings.filter(
                pl.col("permno") == permno
            )

            if loadings_row.height == 0:
                continue

            # Compute position P&L from factor shocks
            position_pnl = 0.0
            for factor_name in self.risk_model.factor_names:
                loading = float(loadings_row[factor_name][0])
                shock = factor_shocks.get(factor_name, 0.0)
                position_pnl += weight * loading * shock

            position_data.append(
                {
                    "permno": permno,
                    "weight": weight,
                    "pnl": position_pnl,
                }
            )

        if not position_data:
            return None

        df = pl.DataFrame(position_data)

        # Add contribution column (% of total P&L)
        total_pnl = df["pnl"].sum()
        if abs(total_pnl) > 1e-10:
            df = df.with_columns(
                (pl.col("pnl") / total_pnl).alias("contribution")
            )
        else:
            df = df.with_columns(
                pl.lit(0.0).alias("contribution")
            )

        return df.select(["permno", "weight", "pnl", "contribution"])
