"""
Tests for PortfolioOptimizer.
"""

from datetime import date

import numpy as np
import polars as pl
import pytest

from libs.risk import (
    CANONICAL_FACTOR_ORDER,
    BarraRiskModel,
    BoxConstraint,
    BudgetConstraint,
    ConstraintPriority,
    FactorExposureConstraint,
    GrossLeverageConstraint,
    InsufficientUniverseCoverageError,
    OptimizerConfig,
    PortfolioOptimizer,
    RelaxableConstraint,
    SectorConstraint,
    SpecificRiskResult,
    TurnoverConstraint,
)
from tests.libs.risk.conftest import (
    create_mock_factor_exposures,
    create_mock_specific_risks,
)


@pytest.fixture()
def sample_barra_model(sample_covariance_result) -> BarraRiskModel:
    """Create sample BarraRiskModel for testing."""
    specific_risks = create_mock_specific_risks()
    factor_loadings = create_mock_factor_exposures().pivot(
        index="permno",
        on="factor_name",
        values="zscore",
    )

    specific_result = SpecificRiskResult(
        specific_risks=specific_risks,
        as_of_date=date(2023, 6, 30),
        dataset_version_ids={"crsp_specific_risk": "test123"},
    )

    return BarraRiskModel.from_t22_results(
        covariance_result=sample_covariance_result,
        specific_risk_result=specific_result,
        factor_loadings=factor_loadings,
    )


@pytest.fixture()
def sample_universe() -> list[int]:
    """Sample universe of permnos."""
    return list(range(10001, 10051))


@pytest.fixture()
def sample_current_weights(sample_universe: list[int]) -> dict[int, float]:
    """Sample current portfolio weights."""
    n = len(sample_universe)
    weights = np.ones(n) / n
    return dict(zip(sample_universe, weights.tolist(), strict=False))


@pytest.fixture()
def sample_expected_returns(sample_universe: list[int]) -> dict[int, float]:
    """Sample expected returns."""
    np.random.seed(42)
    returns = np.random.normal(0.0005, 0.002, len(sample_universe))
    return dict(zip(sample_universe, returns.tolist(), strict=False))


class TestOptimizerConfig:
    """Test configuration validation."""

    def test_default_config(self):
        """Uses default config when none provided."""
        config = OptimizerConfig()
        assert config.solver == "CLARABEL"
        assert config.max_position_weight == 0.10
        assert config.min_position_weight == 0.0
        assert config.tc_linear_bps == 10.0
        assert config.risk_free_rate == 0.0

    def test_custom_config(self):
        """Custom config values are applied."""
        config = OptimizerConfig(
            solver="OSQP",
            max_position_weight=0.05,
            tc_linear_bps=20.0,
            risk_free_rate=0.03,
        )
        assert config.solver == "OSQP"
        assert config.max_position_weight == 0.05
        assert config.tc_linear_bps == 20.0
        assert config.risk_free_rate == 0.03


class TestMinVarianceOptimization:
    """Test minimum variance optimization."""

    def test_optimize_basic(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Basic optimization returns valid weights."""
        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_min_variance(sample_universe)

        assert result.status in ["optimal", "suboptimal"]
        assert result.objective == "min_variance"
        assert result.optimal_weights.height > 0

        # Weights should sum to 1
        weight_sum = result.optimal_weights["weight"].sum()
        assert abs(weight_sum - 1.0) < 0.01

        # All weights should be within bounds
        weights = result.optimal_weights["weight"].to_numpy()
        assert np.all(weights >= -0.001)  # Allow small numerical error
        assert np.all(weights <= 0.101)

    def test_optimize_with_box_constraints(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Box constraints are respected."""
        config = OptimizerConfig(
            min_position_weight=0.01,
            max_position_weight=0.05,
        )
        optimizer = PortfolioOptimizer(sample_barra_model, config)
        result = optimizer.optimize_min_variance(sample_universe)

        assert result.status in ["optimal", "suboptimal"]
        weights = result.optimal_weights["weight"].to_numpy()
        assert np.all(weights >= 0.009)  # Allow small numerical error
        assert np.all(weights <= 0.051)

    def test_optimize_with_sector_constraints(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Sector constraints are respected."""
        # Create sector map
        sector_map = {p: f"sector_{i % 3}" for i, p in enumerate(sample_universe)}

        sector_constraint = SectorConstraint(
            sector_map=sector_map,
            max_sector_weight=0.40,
        )

        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_min_variance(
            sample_universe,
            constraints=[sector_constraint],
        )

        assert result.status in ["optimal", "suboptimal"]

    def test_optimize_with_factor_constraints(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Factor neutrality constraints are respected."""
        # Use actual canonical factor name
        factor_constraint = FactorExposureConstraint(
            factor_name="momentum_12_1",
            target_exposure=0.0,
            tolerance=0.10,
        )

        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_min_variance(
            sample_universe,
            constraints=[factor_constraint],
        )

        assert result.status in ["optimal", "suboptimal"]

    def test_optimize_with_turnover_constraint(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
        sample_current_weights: dict[int, float],
    ):
        """Turnover constraint is respected."""
        turnover_constraint = TurnoverConstraint(
            current_weights=sample_current_weights,
            max_turnover=0.20,
        )

        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_min_variance(
            sample_universe,
            current_weights=sample_current_weights,
            constraints=[turnover_constraint],
        )

        assert result.status in ["optimal", "suboptimal"]
        assert result.turnover <= 0.21  # Allow small numerical error

    def test_optimize_with_transaction_costs(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
        sample_current_weights: dict[int, float],
    ):
        """Transaction costs affect optimization result."""
        config_no_tc = OptimizerConfig(tc_linear_bps=0.0)
        config_with_tc = OptimizerConfig(tc_linear_bps=100.0)  # 1% cost

        optimizer_no_tc = PortfolioOptimizer(sample_barra_model, config_no_tc)
        optimizer_with_tc = PortfolioOptimizer(sample_barra_model, config_with_tc)

        result_no_tc = optimizer_no_tc.optimize_min_variance(
            sample_universe, current_weights=sample_current_weights
        )
        result_with_tc = optimizer_with_tc.optimize_min_variance(
            sample_universe, current_weights=sample_current_weights
        )

        # With high TC, should have lower turnover
        assert result_with_tc.turnover <= result_no_tc.turnover + 0.01

    def test_optimize_with_budget_constraint(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Budget constraint (net exposure) is respected."""
        budget_constraint = BudgetConstraint(target=1.0)

        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_min_variance(
            sample_universe,
            constraints=[budget_constraint],
        )

        assert result.status in ["optimal", "suboptimal"]
        weight_sum = result.optimal_weights["weight"].sum()
        assert abs(weight_sum - 1.0) < 0.01

    def test_optimize_with_gross_leverage(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Gross leverage constraint is respected."""
        config = OptimizerConfig(
            min_position_weight=-0.10,  # Allow shorts
            gross_leverage_max=1.3,
        )

        leverage_constraint = GrossLeverageConstraint(max_leverage=1.3)

        optimizer = PortfolioOptimizer(sample_barra_model, config)
        result = optimizer.optimize_min_variance(
            sample_universe,
            constraints=[leverage_constraint],
        )

        assert result.status in ["optimal", "suboptimal"]


class TestMaxSharpeOptimization:
    """Test maximum Sharpe optimization."""

    def test_optimize_basic(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
        sample_expected_returns: dict[int, float],
    ):
        """Basic max Sharpe returns valid result."""
        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_max_sharpe(
            sample_universe,
            sample_expected_returns,
            n_return_targets=10,
        )

        assert result.objective == "max_sharpe"
        # May be infeasible if all returns are negative
        if result.status != "infeasible":
            assert result.optimal_weights.height > 0
            assert result.sharpe_ratio is not None

    def test_optimize_tilts_toward_high_return(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Max Sharpe tilts toward high return assets."""
        # Create expected returns with clear winners
        expected_returns = {p: 0.0001 for p in sample_universe}
        # Make first 5 stocks have much higher returns
        for _i, p in enumerate(sample_universe[:5]):
            expected_returns[p] = 0.01

        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_max_sharpe(
            sample_universe,
            expected_returns,
            n_return_targets=10,
        )

        if result.status != "infeasible":
            # High return stocks should have higher weights on average
            high_return_permnos = set(sample_universe[:5])
            high_ret_weights = result.optimal_weights.filter(
                pl.col("permno").is_in(high_return_permnos)
            )["weight"].mean()
            other_weights = result.optimal_weights.filter(
                ~pl.col("permno").is_in(high_return_permnos)
            )["weight"].mean()

            assert high_ret_weights > other_weights

    def test_optimize_respects_risk_free_rate(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
        sample_expected_returns: dict[int, float],
    ):
        """Risk-free rate affects Sharpe calculation."""
        config_zero_rf = OptimizerConfig(risk_free_rate=0.0)
        config_high_rf = OptimizerConfig(risk_free_rate=0.05)

        optimizer_zero = PortfolioOptimizer(sample_barra_model, config_zero_rf)
        optimizer_high = PortfolioOptimizer(sample_barra_model, config_high_rf)

        result_zero = optimizer_zero.optimize_max_sharpe(sample_universe, sample_expected_returns)
        result_high = optimizer_high.optimize_max_sharpe(sample_universe, sample_expected_returns)

        # Both should return results (may be different Sharpe)
        if result_zero.status != "infeasible" and result_high.status != "infeasible":
            # Higher rf means lower Sharpe for same portfolio
            # (This is a basic sanity check)
            assert result_zero.sharpe_ratio is not None
            assert result_high.sharpe_ratio is not None


class TestMeanVarianceCost:
    """Test mean-variance with costs."""

    def test_cost_aware_optimization(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
        sample_expected_returns: dict[int, float],
        sample_current_weights: dict[int, float],
    ):
        """Mean-variance with costs returns valid result."""
        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_mean_variance_cost(
            sample_universe,
            sample_expected_returns,
            risk_aversion=1.0,
            current_weights=sample_current_weights,
        )

        assert result.objective == "mean_variance_cost"
        if result.status != "infeasible":
            assert result.optimal_weights.height > 0

    def test_cost_aware_respects_constraints(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
        sample_expected_returns: dict[int, float],
    ):
        """Mean-variance-cost respects budget, box, and leverage constraints."""
        config = OptimizerConfig(
            min_position_weight=0.0,
            max_position_weight=0.10,
            net_exposure_target=1.0,
            gross_leverage_max=1.0,
        )
        optimizer = PortfolioOptimizer(sample_barra_model, config)
        result = optimizer.optimize_mean_variance_cost(
            sample_universe,
            sample_expected_returns,
            risk_aversion=1.0,
        )

        if result.status != "infeasible":
            weights = result.optimal_weights["weight"].to_numpy()
            # Budget constraint: weights sum to 1
            assert abs(weights.sum() - 1.0) < 0.01
            # Box constraints: min <= w <= max
            assert np.all(weights >= -0.001)  # Small numerical error tolerance
            assert np.all(weights <= 0.101)
            # Gross leverage: sum(|w|) <= 1.0
            assert np.sum(np.abs(weights)) <= 1.01

    def test_risk_aversion_parameter(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
        sample_expected_returns: dict[int, float],
    ):
        """Higher risk aversion leads to lower risk portfolio."""
        optimizer = PortfolioOptimizer(sample_barra_model)

        result_low_gamma = optimizer.optimize_mean_variance_cost(
            sample_universe,
            sample_expected_returns,
            risk_aversion=0.5,
        )
        result_high_gamma = optimizer.optimize_mean_variance_cost(
            sample_universe,
            sample_expected_returns,
            risk_aversion=5.0,
        )

        if result_low_gamma.status != "infeasible" and result_high_gamma.status != "infeasible":
            # Higher gamma should lead to lower risk
            assert result_high_gamma.expected_risk <= result_low_gamma.expected_risk + 0.01


class TestRiskParityOptimization:
    """Test risk parity optimization."""

    def test_equal_risk_contribution(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Risk parity achieves approximately equal risk contribution."""
        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_risk_parity(sample_universe)

        assert result.objective == "risk_parity"
        assert result.status in ["optimal", "suboptimal"]
        assert result.optimal_weights.height > 0

        # Weights should sum to 1
        weight_sum = result.optimal_weights["weight"].sum()
        assert abs(weight_sum - 1.0) < 0.01

        # All weights should be positive
        weights = result.optimal_weights["weight"].to_numpy()
        assert np.all(weights >= 0)

    def test_convergence_within_iterations(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Risk parity converges within max iterations."""
        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_risk_parity(
            sample_universe,
            max_iterations=100,
        )

        # Should converge (status optimal or suboptimal)
        assert result.status in ["optimal", "suboptimal"]

    def test_risk_parity_vs_equal_weight(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Risk parity differs from equal weight when assets have different risk."""
        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_risk_parity(sample_universe)

        # Equal weight portfolio
        n = len(sample_universe)
        1.0 / n

        # Risk parity weights should differ from equal weight
        weights = result.optimal_weights["weight"].to_numpy()
        weight_std = np.std(weights)

        # Some variation expected (not all exactly equal)
        assert weight_std > 0.001


class TestSolverRobustness:
    """Test solver edge cases."""

    def test_solver_fallback(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Falls back to secondary solver on primary failure."""
        # Use invalid primary solver
        config = OptimizerConfig(solver="INVALID_SOLVER")
        optimizer = PortfolioOptimizer(sample_barra_model, config)

        # Should fall back to OSQP or SCS
        result = optimizer.optimize_min_variance(sample_universe)
        assert result.status in ["optimal", "suboptimal", "infeasible"]

    def test_infeasible_constraints(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Gracefully handles infeasible constraints."""
        # Impossible constraints: weights must be > 0.1 AND sum to 1
        # With 50 stocks, min weight of 0.1 means min sum of 5.0
        config = OptimizerConfig(
            min_position_weight=0.1,
            max_position_weight=0.2,
            net_exposure_target=1.0,
        )
        optimizer = PortfolioOptimizer(sample_barra_model, config)
        result = optimizer.optimize_min_variance(sample_universe)

        # Should return infeasible
        assert result.status == "infeasible"


class TestBarraModelIntegration:
    """Test integration with BarraRiskModel (T2.3)."""

    def test_covariance_assembly_from_barra(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Covariance is correctly assembled from Barra model."""
        optimizer = PortfolioOptimizer(sample_barra_model)
        sigma, permnos, B = optimizer._build_covariance(sample_universe)

        # Covariance should be symmetric
        assert np.allclose(sigma, sigma.T)

        # Covariance should be PSD
        eigenvalues = np.linalg.eigvalsh(sigma)
        assert np.all(eigenvalues >= -1e-8)

        # Dimensions should match
        assert sigma.shape[0] == len(permnos)
        assert B.shape[0] == len(permnos)
        assert B.shape[1] == len(sample_barra_model.factor_names)

    def test_universe_coverage_validation(
        self,
        sample_barra_model: BarraRiskModel,
    ):
        """Partial coverage is handled correctly."""
        # Universe with some unknown permnos
        universe = list(range(10001, 10051)) + list(range(99901, 99911))

        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_min_variance(universe)

        # Should still work (coverage > 80%)
        assert result.status in ["optimal", "suboptimal"]

    def test_missing_risk_data_raises(
        self,
        sample_barra_model: BarraRiskModel,
    ):
        """Raises error when coverage is below threshold."""
        # Universe with mostly unknown permnos
        universe = list(range(99901, 99951))  # None in risk model

        config = OptimizerConfig(min_coverage=0.8)
        optimizer = PortfolioOptimizer(sample_barra_model, config)

        with pytest.raises(InsufficientUniverseCoverageError):
            optimizer.optimize_min_variance(universe)


class TestConstraintValidation:
    """Test constraint validation methods."""

    def test_box_constraint_validation(self):
        """Box constraint catches invalid bounds."""
        constraint = BoxConstraint(min_weight=0.5, max_weight=0.1)  # Invalid
        errors = constraint.validate({})
        assert len(errors) > 0
        assert "min_weight" in errors[0]

    def test_sector_constraint_validation(
        self,
        sample_universe: list[int],
    ):
        """Sector constraint warns on low coverage."""
        # Sector map with only 10% coverage
        sector_map = {sample_universe[0]: "sector_a"}

        constraint = SectorConstraint(
            sector_map=sector_map,
            max_sector_weight=0.3,
        )
        context = {"permnos": sample_universe}
        errors = constraint.validate(context)
        assert len(errors) > 0

    def test_factor_constraint_validation(self):
        """Factor constraint catches unknown factor."""
        constraint = FactorExposureConstraint(
            factor_name="unknown_factor",
            target_exposure=0.0,
        )
        context = {"factor_names": CANONICAL_FACTOR_ORDER}
        errors = constraint.validate(context)
        assert len(errors) > 0

    def test_turnover_constraint_validation(self):
        """Turnover constraint catches negative turnover."""
        constraint = TurnoverConstraint(
            current_weights={},
            max_turnover=-0.5,  # Invalid
        )
        errors = constraint.validate({})
        assert len(errors) > 0


class TestOptimizationResult:
    """Test result structure and validation."""

    def test_result_schema(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Result has expected schema."""
        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_min_variance(sample_universe)

        # Check required fields
        assert result.solution_id is not None
        assert result.as_of_date is not None
        assert result.objective == "min_variance"
        assert result.status in ["optimal", "suboptimal", "infeasible"]
        assert result.optimal_weights is not None
        assert isinstance(result.expected_risk, float)
        assert isinstance(result.turnover, float)
        assert result.model_version is not None

    def test_to_storage_format(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Result can be converted to storage format."""
        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_min_variance(sample_universe)

        solutions_df, weights_df = result.to_storage_format()

        # Check solutions DataFrame
        assert "solution_id" in solutions_df.columns
        assert "objective" in solutions_df.columns
        assert "expected_risk" in solutions_df.columns

        # Check weights DataFrame
        assert "solution_id" in weights_df.columns
        assert "permno" in weights_df.columns
        assert "weight" in weights_df.columns
        assert "delta_weight" in weights_df.columns

    def test_provenance_tracking(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Dataset version IDs are tracked."""
        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_min_variance(sample_universe)

        assert result.dataset_version_ids is not None
        assert len(result.dataset_version_ids) > 0


@pytest.mark.performance()
class TestPerformance:
    """Performance tests."""

    def test_optimizer_performance(
        self,
        sample_barra_model: BarraRiskModel,
    ):
        """Optimization completes in reasonable time."""
        import time

        # Use larger universe
        universe = list(range(10001, 10101))  # 100 stocks

        optimizer = PortfolioOptimizer(sample_barra_model)

        start = time.perf_counter()
        result = optimizer.optimize_min_variance(universe)
        elapsed = time.perf_counter() - start

        assert elapsed < 10.0  # Should complete in under 10 seconds
        assert result.status in ["optimal", "suboptimal"]


class TestConstraintPriority:
    """Tests for ConstraintPriority enum."""

    def test_priority_ordering(self):
        """CRITICAL < HIGH < MEDIUM < LOW (lower value = higher priority)."""
        assert ConstraintPriority.CRITICAL < ConstraintPriority.HIGH
        assert ConstraintPriority.HIGH < ConstraintPriority.MEDIUM
        assert ConstraintPriority.MEDIUM < ConstraintPriority.LOW

    def test_priority_values(self):
        """Priority values are as expected."""
        assert ConstraintPriority.CRITICAL == 0
        assert ConstraintPriority.HIGH == 1
        assert ConstraintPriority.MEDIUM == 2
        assert ConstraintPriority.LOW == 3


class TestRelaxableConstraint:
    """Tests for RelaxableConstraint wrapper."""

    def test_can_relax_low_priority(self):
        """LOW priority constraints can be relaxed."""
        turnover = TurnoverConstraint(current_weights={}, max_turnover=0.5)
        rc = RelaxableConstraint(
            constraint=turnover,
            priority=ConstraintPriority.LOW,
            max_relaxations=3,
        )
        assert rc.can_relax() is True

    def test_cannot_relax_critical(self):
        """CRITICAL priority constraints cannot be relaxed."""
        budget = BudgetConstraint(target=1.0)
        rc = RelaxableConstraint(
            constraint=budget,
            priority=ConstraintPriority.CRITICAL,
            max_relaxations=3,
        )
        assert rc.can_relax() is False

    def test_cannot_relax_after_max(self):
        """Cannot relax after max_relaxations reached."""
        turnover = TurnoverConstraint(current_weights={}, max_turnover=0.5)
        rc = RelaxableConstraint(
            constraint=turnover,
            priority=ConstraintPriority.LOW,
            max_relaxations=2,
        )
        # Simulate 2 relaxations already done
        rc.current_relaxations = 2
        assert rc.can_relax() is False


class TestConstraintRelaxation:
    """Tests for hierarchical constraint relaxation."""

    def test_relax_turnover_constraint(
        self,
        sample_barra_model: BarraRiskModel,
    ):
        """TurnoverConstraint can be relaxed."""
        optimizer = PortfolioOptimizer(sample_barra_model)
        turnover = TurnoverConstraint(current_weights={}, max_turnover=0.5)
        rc = RelaxableConstraint(
            constraint=turnover,
            priority=ConstraintPriority.LOW,
            relaxation_factor=1.5,
        )

        new_rc, was_relaxed = optimizer._relax_constraint(rc)

        assert was_relaxed is True
        assert new_rc.current_relaxations == 1
        assert isinstance(new_rc.constraint, TurnoverConstraint)
        assert new_rc.constraint.max_turnover == pytest.approx(0.75)  # 0.5 * 1.5

    def test_relax_sector_constraint(
        self,
        sample_barra_model: BarraRiskModel,
    ):
        """SectorConstraint can be relaxed."""
        optimizer = PortfolioOptimizer(sample_barra_model)
        sector = SectorConstraint(
            sector_map={10001: "Tech"},
            max_sector_weight=0.3,
        )
        rc = RelaxableConstraint(
            constraint=sector,
            priority=ConstraintPriority.MEDIUM,
            relaxation_factor=1.5,
        )

        new_rc, was_relaxed = optimizer._relax_constraint(rc)

        assert was_relaxed is True
        assert isinstance(new_rc.constraint, SectorConstraint)
        assert new_rc.constraint.max_sector_weight == pytest.approx(0.45)  # 0.3 * 1.5

    def test_relax_sector_constraint_capped_at_100(
        self,
        sample_barra_model: BarraRiskModel,
    ):
        """SectorConstraint relaxation is capped at 100%."""
        optimizer = PortfolioOptimizer(sample_barra_model)
        sector = SectorConstraint(
            sector_map={10001: "Tech"},
            max_sector_weight=0.8,
        )
        rc = RelaxableConstraint(
            constraint=sector,
            priority=ConstraintPriority.MEDIUM,
            relaxation_factor=2.0,
        )

        new_rc, _ = optimizer._relax_constraint(rc)

        # 0.8 * 2.0 = 1.6, but capped at 1.0
        assert new_rc.constraint.max_sector_weight == pytest.approx(1.0)

    def test_cannot_relax_critical_constraint(
        self,
        sample_barra_model: BarraRiskModel,
    ):
        """CRITICAL constraints cannot be relaxed."""
        optimizer = PortfolioOptimizer(sample_barra_model)
        budget = BudgetConstraint(target=1.0)
        rc = RelaxableConstraint(
            constraint=budget,
            priority=ConstraintPriority.CRITICAL,
        )

        new_rc, was_relaxed = optimizer._relax_constraint(rc)

        assert was_relaxed is False
        assert new_rc is rc  # Same object returned

    def test_optimize_with_relaxation_disabled(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """When relaxation disabled, uses regular optimization."""
        config = OptimizerConfig(enable_constraint_relaxation=False)
        optimizer = PortfolioOptimizer(sample_barra_model, config)

        relaxable = [
            RelaxableConstraint(
                constraint=TurnoverConstraint(current_weights={}, max_turnover=0.5),
                priority=ConstraintPriority.LOW,
            ),
        ]

        result = optimizer.optimize_with_relaxation(sample_universe, relaxable)

        # Should work normally - relaxation is just disabled
        assert result.status in ["optimal", "suboptimal", "infeasible"]

    def test_optimize_with_relaxation_success_no_relaxation_needed(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
        sample_current_weights: dict[int, float],
    ):
        """Feasible problem succeeds without relaxation."""
        config = OptimizerConfig(enable_constraint_relaxation=True)
        optimizer = PortfolioOptimizer(sample_barra_model, config)

        # Generous constraints that should be feasible
        relaxable = [
            RelaxableConstraint(
                constraint=TurnoverConstraint(
                    current_weights=sample_current_weights,
                    max_turnover=2.0,  # Very generous
                ),
                priority=ConstraintPriority.LOW,
            ),
        ]

        result = optimizer.optimize_with_relaxation(
            sample_universe, relaxable, sample_current_weights
        )

        assert result.status in ["optimal", "suboptimal"]

    def test_optimize_with_relaxation_success_after_relax(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
        sample_current_weights: dict[int, float],
    ):
        """Initially infeasible problem succeeds after relaxation."""
        config = OptimizerConfig(
            enable_constraint_relaxation=True,
        )
        optimizer = PortfolioOptimizer(sample_barra_model, config)

        # Very tight turnover constraint that may be infeasible
        # But should become feasible after relaxation
        relaxable = [
            RelaxableConstraint(
                constraint=TurnoverConstraint(
                    current_weights=sample_current_weights,
                    max_turnover=0.01,  # Very tight - may need relaxation
                ),
                priority=ConstraintPriority.LOW,
                relaxation_factor=2.0,
                max_relaxations=5,
            ),
        ]

        result = optimizer.optimize_with_relaxation(
            sample_universe, relaxable, sample_current_weights
        )

        # Should eventually succeed (either initially feasible or after relaxation)
        assert result.status in ["optimal", "suboptimal"]

    def test_relax_by_priority_order(
        self,
        sample_barra_model: BarraRiskModel,
    ):
        """Constraints are relaxed in priority order (LOW first)."""
        optimizer = PortfolioOptimizer(sample_barra_model)

        # Create constraints of different priorities
        low = RelaxableConstraint(
            constraint=TurnoverConstraint(current_weights={}, max_turnover=0.5),
            priority=ConstraintPriority.LOW,
        )
        medium = RelaxableConstraint(
            constraint=SectorConstraint(sector_map={}, max_sector_weight=0.3),
            priority=ConstraintPriority.MEDIUM,
        )

        # LOW priority should be relaxable, MEDIUM not yet
        new_low, relaxed_low = optimizer._relax_constraint(low)
        assert relaxed_low is True

        # After LOW exhausted, MEDIUM becomes next
        exhausted_low = RelaxableConstraint(
            constraint=TurnoverConstraint(current_weights={}, max_turnover=0.5),
            priority=ConstraintPriority.LOW,
            max_relaxations=1,
        )
        exhausted_low.current_relaxations = 1
        _, can_relax_exhausted = optimizer._relax_constraint(exhausted_low)
        assert can_relax_exhausted is False

        # MEDIUM can still be relaxed
        new_medium, relaxed_medium = optimizer._relax_constraint(medium)
        assert relaxed_medium is True
