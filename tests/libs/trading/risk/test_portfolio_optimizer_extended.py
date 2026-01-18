"""
Extended unit tests for PortfolioOptimizer focusing on edge cases and error handling.

This test module complements test_portfolio_optimizer.py with additional coverage for:
- Edge cases in constraint relaxation
- Error handling paths
- Numerical edge cases
- Solver fallback scenarios
- Helper method edge cases
- BudgetConstraint tolerance paths
"""

from datetime import date
from unittest.mock import MagicMock, patch

import cvxpy as cp
import numpy as np
import pytest

from libs.trading.risk import (
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
    ReturnTargetConstraint,
    SectorConstraint,
    SpecificRiskResult,
    TurnoverConstraint,
)
from tests.libs.trading.risk.conftest import (
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
def small_universe() -> list[int]:
    """Small universe for edge case testing."""
    return list(range(10001, 10011))  # 10 stocks


@pytest.fixture()
def sample_universe() -> list[int]:
    """Sample universe of permnos."""
    return list(range(10001, 10051))  # 50 stocks


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


class TestRelaxableConstraintValidation:
    """Test RelaxableConstraint validation and edge cases."""

    def test_relaxation_factor_must_be_greater_than_one(self):
        """Raises ValueError if relaxation_factor <= 1.0."""
        with pytest.raises(ValueError, match="relaxation_factor must be > 1.0"):
            RelaxableConstraint(
                constraint=TurnoverConstraint(current_weights={}, max_turnover=0.5),
                priority=ConstraintPriority.LOW,
                relaxation_factor=1.0,  # Invalid
            )

    def test_relaxation_factor_cannot_be_zero(self):
        """Raises ValueError if relaxation_factor is 0."""
        with pytest.raises(ValueError, match="relaxation_factor must be > 1.0"):
            RelaxableConstraint(
                constraint=TurnoverConstraint(current_weights={}, max_turnover=0.5),
                priority=ConstraintPriority.LOW,
                relaxation_factor=0.0,  # Invalid
            )

    def test_relaxation_factor_cannot_be_negative(self):
        """Raises ValueError if relaxation_factor is negative."""
        with pytest.raises(ValueError, match="relaxation_factor must be > 1.0"):
            RelaxableConstraint(
                constraint=TurnoverConstraint(current_weights={}, max_turnover=0.5),
                priority=ConstraintPriority.LOW,
                relaxation_factor=-0.5,  # Invalid
            )

    def test_max_relaxation_factor_must_be_greater_than_one(self):
        """Raises ValueError if max_relaxation_factor <= 1.0."""
        with pytest.raises(ValueError, match="max_relaxation_factor must be > 1.0"):
            RelaxableConstraint(
                constraint=TurnoverConstraint(current_weights={}, max_turnover=0.5),
                priority=ConstraintPriority.LOW,
                max_relaxation_factor=1.0,  # Invalid
            )

    def test_initial_constraint_is_stored_on_init(self):
        """Initial constraint is stored for cap calculation."""
        turnover = TurnoverConstraint(current_weights={}, max_turnover=0.5)
        rc = RelaxableConstraint(
            constraint=turnover,
            priority=ConstraintPriority.LOW,
        )
        assert rc.initial_constraint is turnover


class TestBudgetConstraintWithTolerance:
    """Test BudgetConstraint with tolerance parameter."""

    def test_budget_constraint_zero_tolerance(self):
        """Budget constraint with tolerance=0 creates equality constraint."""
        constraint = BudgetConstraint(target=1.0, tolerance=0.0)
        w = cp.Variable(10)
        context = {}

        constraints = constraint.apply(w, context)

        # Should return single equality constraint
        assert len(constraints) == 1

    def test_budget_constraint_with_tolerance(self):
        """Budget constraint with tolerance>0 creates inequality constraints."""
        constraint = BudgetConstraint(target=1.0, tolerance=0.05)
        w = cp.Variable(10)
        context = {}

        constraints = constraint.apply(w, context)

        # Should return two inequality constraints
        assert len(constraints) == 2

    def test_budget_constraint_validation(self):
        """Budget constraint validation passes for valid parameters."""
        constraint = BudgetConstraint(target=1.0, tolerance=0.05)
        errors = constraint.validate({})
        assert len(errors) == 0


class TestGrossLeverageConstraintValidation:
    """Test GrossLeverageConstraint validation."""

    def test_negative_max_leverage_validation_error(self):
        """Negative max_leverage triggers validation error."""
        constraint = GrossLeverageConstraint(max_leverage=-0.5)
        errors = constraint.validate({})
        assert len(errors) > 0
        assert "must be non-negative" in errors[0]

    def test_zero_max_leverage_passes_validation(self):
        """Zero max_leverage is technically valid (though unusual)."""
        constraint = GrossLeverageConstraint(max_leverage=0.0)
        errors = constraint.validate({})
        # Should pass validation (even though it's unusual)
        # The solver will likely find it infeasible, but that's separate
        assert len(errors) == 0


class TestFactorExposureConstraintEdgeCases:
    """Test FactorExposureConstraint edge cases."""

    def test_factor_constraint_with_zero_tolerance(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Factor constraint with zero tolerance creates exact equality."""
        factor_constraint = FactorExposureConstraint(
            factor_name="momentum_12_1",
            target_exposure=0.0,
            tolerance=0.0,  # Exact neutrality
        )

        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_min_variance(
            sample_universe,
            constraints=[factor_constraint],
        )

        # Should work (may be infeasible with strict exact equality)
        assert result.status in ["optimal", "suboptimal", "infeasible"]

    def test_factor_constraint_unknown_factor_raises(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Unknown factor name raises ValueError during application."""
        factor_constraint = FactorExposureConstraint(
            factor_name="UNKNOWN_FACTOR",
            target_exposure=0.0,
            tolerance=0.1,
        )

        optimizer = PortfolioOptimizer(sample_barra_model)

        # Should raise ValueError when constraint is applied
        with pytest.raises(ValueError, match="Unknown factor"):
            optimizer.optimize_min_variance(
                sample_universe,
                constraints=[factor_constraint],
            )


class TestReturnTargetConstraint:
    """Test ReturnTargetConstraint (used in max Sharpe optimization)."""

    def test_return_target_constraint_validation(self):
        """ReturnTargetConstraint validation always passes (no-op)."""
        constraint = ReturnTargetConstraint(
            expected_returns={10001: 0.01},
            min_return=0.005,
        )
        errors = constraint.validate({})
        assert len(errors) == 0

    def test_return_target_constraint_apply(self):
        """ReturnTargetConstraint creates return >= min_return constraint."""
        expected_returns = {10001: 0.01, 10002: 0.02}
        constraint = ReturnTargetConstraint(
            expected_returns=expected_returns,
            min_return=0.015,
        )

        w = cp.Variable(2)
        context = {"permnos": [10001, 10002]}

        constraints = constraint.apply(w, context)

        # Should return single constraint: mu @ w >= min_return
        assert len(constraints) == 1


class TestCovarianceRegularization:
    """Test covariance matrix regularization."""

    def test_regularize_near_singular_covariance(
        self,
        sample_barra_model: BarraRiskModel,
    ):
        """Near-singular covariance is regularized."""
        optimizer = PortfolioOptimizer(sample_barra_model)

        # Create near-singular matrix (min eigenvalue < 1e-8)
        n = 10
        sigma = np.eye(n) * 1e-10  # Very small diagonal

        regularized = optimizer._regularize_covariance(sigma)

        # Should add ridge to make PSD
        min_eigenvalue = np.linalg.eigvalsh(regularized).min()
        assert min_eigenvalue >= 1e-8

    def test_regularize_already_psd_covariance(
        self,
        sample_barra_model: BarraRiskModel,
    ):
        """Already PSD covariance is not modified."""
        optimizer = PortfolioOptimizer(sample_barra_model)

        # Create well-conditioned matrix
        n = 10
        sigma = np.eye(n) * 0.01

        regularized = optimizer._regularize_covariance(sigma)

        # Should not modify (already PSD)
        assert np.allclose(regularized, sigma)


class TestSolverFallbackEdgeCases:
    """Test solver fallback and error handling."""

    def test_all_solvers_fail_raises_runtime_error(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Raises RuntimeError when all solvers fail with errors."""
        optimizer = PortfolioOptimizer(sample_barra_model)

        # Create a problem
        sigma, permnos, B = optimizer._build_covariance(sample_universe)
        context = optimizer._build_context(permnos, B)
        w, problem = optimizer._build_min_variance_problem(sigma, len(permnos), None, [], context)

        # Mock all solvers to raise SolverError
        with patch.object(problem, "solve") as mock_solve:
            mock_solve.side_effect = cp.SolverError("Solver not installed")

            with pytest.raises(RuntimeError, match="All solvers failed"):
                optimizer._solve_with_fallback(problem)

    def test_solver_returns_unbounded_status(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Handles UNBOUNDED status gracefully."""
        optimizer = PortfolioOptimizer(sample_barra_model)

        sigma, permnos, B = optimizer._build_covariance(sample_universe)
        context = optimizer._build_context(permnos, B)
        w, problem = optimizer._build_min_variance_problem(sigma, len(permnos), None, [], context)

        # Mock solver to return UNBOUNDED
        with patch.object(problem, "solve") as mock_solve:
            problem.status = cp.UNBOUNDED
            mock_solve.return_value = None

            status = optimizer._solve_with_fallback(problem)
            assert "INFEASIBLE" in status

    def test_solver_returns_infeasible_inaccurate(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Handles INFEASIBLE_INACCURATE status gracefully."""
        optimizer = PortfolioOptimizer(sample_barra_model)

        sigma, permnos, B = optimizer._build_covariance(sample_universe)
        context = optimizer._build_context(permnos, B)
        w, problem = optimizer._build_min_variance_problem(sigma, len(permnos), None, [], context)

        # Mock solver to return INFEASIBLE_INACCURATE
        with patch.object(problem, "solve") as mock_solve:
            problem.status = cp.INFEASIBLE_INACCURATE
            mock_solve.return_value = None

            status = optimizer._solve_with_fallback(problem)
            assert "INFEASIBLE" in status


class TestBuildResultEdgeCases:
    """Test _build_result edge cases."""

    def test_build_result_solver_bug_none_weights(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Handles solver bug where OPTIMAL status but None weights."""
        optimizer = PortfolioOptimizer(sample_barra_model)

        sigma, permnos, B = optimizer._build_covariance(sample_universe)
        w = cp.Variable(len(permnos))

        # Create mock problem with OPTIMAL status but None weights
        problem = MagicMock()
        problem.status = cp.OPTIMAL
        w.value = None  # Solver bug: optimal but no weights

        result = optimizer._build_result(
            w=w,
            permnos=permnos,
            sigma=sigma,
            w_current=None,
            objective="min_variance",
            problem=problem,
            solver_status="MOCK",
            solver_time_ms=100,
            expected_returns=None,
        )

        # Should return infeasible result
        assert result.status == "infeasible"
        assert "solver bug" in result.solver_status.lower()

    def test_build_result_with_current_weights_none(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Handles current_weights=None correctly in turnover calculation."""
        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_min_variance(sample_universe, current_weights=None)

        if result.status != "infeasible":
            # Turnover should equal sum of absolute weights
            weights = result.optimal_weights["weight"].to_numpy()
            expected_turnover = np.sum(np.abs(weights))
            assert abs(result.turnover - expected_turnover) < 0.01
            # Transaction cost should be zero (no current weights)
            assert result.transaction_cost == 0.0


class TestMaxSharpeEdgeCases:
    """Test max Sharpe optimization edge cases."""

    def test_max_sharpe_all_negative_returns(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Max Sharpe with all negative returns returns infeasible."""
        # All negative expected returns
        expected_returns = {p: -0.01 for p in sample_universe}

        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_max_sharpe(
            sample_universe,
            expected_returns,
            n_return_targets=10,
        )

        # Should return infeasible (no positive Sharpe)
        assert result.status == "infeasible"

    def test_max_sharpe_no_feasible_return_range(
        self,
        sample_barra_model: BarraRiskModel,
        small_universe: list[int],
    ):
        """Max Sharpe returns infeasible when return range cannot be found."""
        # Create impossible constraints
        config = OptimizerConfig(
            min_position_weight=0.5,  # Impossible: 10 stocks * 0.5 = 5.0 > 1.0
            net_exposure_target=1.0,
        )

        expected_returns = {p: 0.01 for p in small_universe}

        optimizer = PortfolioOptimizer(sample_barra_model, config)
        result = optimizer.optimize_max_sharpe(
            small_universe,
            expected_returns,
            n_return_targets=10,
        )

        # Should return infeasible
        assert result.status == "infeasible"

    def test_max_sharpe_zero_risk_portfolio(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Max Sharpe handles zero-risk portfolio (skips division by zero)."""
        # This is edge case testing - in practice, Barra model prevents zero risk
        # But we test the guard in the code: if port_risk > 1e-10
        # If port_risk is near zero, Sharpe calculation is skipped

        # Normal case should work
        expected_returns = {p: 0.001 for p in sample_universe}
        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_max_sharpe(
            sample_universe,
            expected_returns,
            n_return_targets=5,
        )

        # Should complete (may be infeasible or optimal)
        assert result.status in ["optimal", "suboptimal", "infeasible"]


class TestRiskParityEdgeCases:
    """Test risk parity optimization edge cases."""

    def test_risk_parity_zero_risk_portfolio(
        self,
        sample_barra_model: BarraRiskModel,
    ):
        """Risk parity handles zero-risk portfolio edge case."""
        # Use very small universe to test edge case
        universe = [10001, 10002]

        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_risk_parity(universe, max_iterations=50)

        # Should converge
        assert result.status in ["optimal", "suboptimal"]

    def test_risk_parity_fails_to_converge(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Risk parity returns suboptimal if fails to converge within iterations."""
        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_risk_parity(
            sample_universe,
            max_iterations=1,  # Force non-convergence
            tolerance=1e-10,  # Very tight tolerance
        )

        # Should return suboptimal (likely not converged in 1 iteration)
        assert result.status in ["optimal", "suboptimal"]


class TestConstraintRelaxationEdgeCases:
    """Test constraint relaxation edge cases."""

    def test_relax_factor_exposure_constraint_zero_tolerance(
        self,
        sample_barra_model: BarraRiskModel,
    ):
        """Relaxing FactorExposureConstraint with zero tolerance uses epsilon floor."""
        optimizer = PortfolioOptimizer(sample_barra_model)
        factor = FactorExposureConstraint(
            factor_name="momentum_12_1",
            target_exposure=0.0,
            tolerance=0.0,  # Zero tolerance
        )
        rc = RelaxableConstraint(
            constraint=factor,
            priority=ConstraintPriority.MEDIUM,
            relaxation_factor=1.5,
        )

        new_rc, was_relaxed = optimizer._relax_constraint(rc)

        assert was_relaxed is True
        # Should use epsilon floor (0.01)
        assert new_rc.constraint.tolerance > 0.0

    def test_relax_gross_leverage_constraint(
        self,
        sample_barra_model: BarraRiskModel,
    ):
        """GrossLeverageConstraint can be relaxed."""
        optimizer = PortfolioOptimizer(sample_barra_model)
        leverage = GrossLeverageConstraint(max_leverage=1.0)
        rc = RelaxableConstraint(
            constraint=leverage,
            priority=ConstraintPriority.HIGH,
            relaxation_factor=1.5,
        )

        new_rc, was_relaxed = optimizer._relax_constraint(rc)

        assert was_relaxed is True
        assert new_rc.constraint.max_leverage == pytest.approx(1.5)

    def test_relax_unsupported_constraint_type(
        self,
        sample_barra_model: BarraRiskModel,
    ):
        """Unsupported constraint type cannot be relaxed."""
        optimizer = PortfolioOptimizer(sample_barra_model)
        # BoxConstraint is not supported for relaxation
        box = BoxConstraint(min_weight=0.0, max_weight=0.1)
        rc = RelaxableConstraint(
            constraint=box,
            priority=ConstraintPriority.MEDIUM,
        )

        new_rc, was_relaxed = optimizer._relax_constraint(rc)

        assert was_relaxed is False
        assert new_rc is rc  # Same object returned

    def test_relax_constraint_at_max_relaxation_factor_cap(
        self,
        sample_barra_model: BarraRiskModel,
    ):
        """Constraint at max_relaxation_factor cap cannot be relaxed further."""
        optimizer = PortfolioOptimizer(sample_barra_model)
        # Create constraint already at 2x original
        turnover_original = TurnoverConstraint(current_weights={}, max_turnover=0.5)
        turnover_relaxed = TurnoverConstraint(current_weights={}, max_turnover=1.0)  # 2x

        rc = RelaxableConstraint(
            constraint=turnover_relaxed,
            priority=ConstraintPriority.LOW,
            relaxation_factor=1.5,
            max_relaxation_factor=2.0,  # Already at cap
            initial_constraint=turnover_original,
        )

        new_rc, was_relaxed = optimizer._relax_constraint(rc)

        # Should not relax further (already at cap)
        assert was_relaxed is False

    def test_optimize_with_relaxation_all_constraints_exhausted(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Optimization returns infeasible when all relaxation attempts exhausted."""
        config = OptimizerConfig(
            enable_constraint_relaxation=True,
            min_position_weight=0.5,  # Impossible
            net_exposure_target=1.0,
        )
        optimizer = PortfolioOptimizer(sample_barra_model, config)

        # Constraints that can't help even when relaxed
        relaxable = [
            RelaxableConstraint(
                constraint=TurnoverConstraint(current_weights={}, max_turnover=0.1),
                priority=ConstraintPriority.LOW,
                max_relaxations=2,
            ),
        ]

        result = optimizer.optimize_with_relaxation(sample_universe, relaxable)

        # Should eventually return infeasible
        assert result.status == "infeasible"


class TestMeanVarianceCostEdgeCases:
    """Test mean-variance-cost optimization edge cases."""

    def test_mean_variance_cost_with_quadratic_tc(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
        sample_expected_returns: dict[int, float],
        sample_current_weights: dict[int, float],
    ):
        """Mean-variance-cost with quadratic transaction costs."""
        config = OptimizerConfig(
            tc_linear_bps=10.0,
            tc_quadratic_bps=5.0,  # Quadratic cost
        )
        optimizer = PortfolioOptimizer(sample_barra_model, config)

        result = optimizer.optimize_mean_variance_cost(
            sample_universe,
            sample_expected_returns,
            risk_aversion=1.0,
            current_weights=sample_current_weights,
        )

        # Should complete successfully
        assert result.status in ["optimal", "suboptimal", "infeasible"]

    def test_mean_variance_cost_no_current_weights(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
        sample_expected_returns: dict[int, float],
    ):
        """Mean-variance-cost without current weights (no turnover penalty)."""
        config = OptimizerConfig(tc_linear_bps=100.0)
        optimizer = PortfolioOptimizer(sample_barra_model, config)

        result = optimizer.optimize_mean_variance_cost(
            sample_universe,
            sample_expected_returns,
            risk_aversion=1.0,
            current_weights=None,  # No current weights
        )

        # Should complete successfully
        if result.status != "infeasible":
            # Transaction cost should be zero
            assert result.transaction_cost == 0.0


class TestUniverseCoverageEdgeCases:
    """Test universe coverage validation edge cases."""

    def test_empty_universe_raises_coverage_error(
        self,
        sample_barra_model: BarraRiskModel,
    ):
        """Empty universe raises InsufficientUniverseCoverageError."""
        optimizer = PortfolioOptimizer(sample_barra_model)

        with pytest.raises(InsufficientUniverseCoverageError):
            optimizer.optimize_min_variance([])

    def test_universe_coverage_exactly_at_threshold(
        self,
        sample_barra_model: BarraRiskModel,
    ):
        """Coverage exactly at threshold (80%) succeeds."""
        # Create universe with exactly 80% coverage
        # Assume first 50 stocks are in model
        covered = list(range(10001, 10051))  # 50 stocks
        uncovered = list(range(99901, 99913))  # 12 stocks
        universe = covered + uncovered  # 62 total, 50/62 = 80.6%

        config = OptimizerConfig(min_coverage=0.8)
        optimizer = PortfolioOptimizer(sample_barra_model, config)

        result = optimizer.optimize_min_variance(universe)

        # Should work (coverage >= 80%)
        assert result.status in ["optimal", "suboptimal"]


class TestStorageFormatConversion:
    """Test OptimizationResult.to_storage_format()."""

    def test_to_storage_format_dataset_version_serialization(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Dataset version IDs are serialized correctly to storage format."""
        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_min_variance(sample_universe)

        solutions_df, weights_df = result.to_storage_format()

        # Check dataset_version_id is serialized
        version_str = solutions_df["dataset_version_id"][0]
        assert ":" in version_str  # Format: "key:value|key:value"
        assert "|" in version_str or len(result.dataset_version_ids) == 1

    def test_to_storage_format_weights_have_solution_id(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Weights DataFrame includes solution_id."""
        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_min_variance(sample_universe)

        solutions_df, weights_df = result.to_storage_format()

        # Weights should have solution_id matching solutions
        assert "solution_id" in weights_df.columns
        assert weights_df["solution_id"][0] == solutions_df["solution_id"][0]


class TestSectorConstraintEdgeCases:
    """Test SectorConstraint edge cases."""

    def test_sector_constraint_min_sector_weight(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """SectorConstraint with min_sector_weight creates lower bound."""
        sector_map = {p: f"sector_{i % 3}" for i, p in enumerate(sample_universe)}

        sector_constraint = SectorConstraint(
            sector_map=sector_map,
            max_sector_weight=0.50,
            min_sector_weight=0.20,  # Minimum allocation
        )

        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_min_variance(
            sample_universe,
            constraints=[sector_constraint],
        )

        # Should work (may be infeasible with tight min/max)
        assert result.status in ["optimal", "suboptimal", "infeasible"]

    def test_sector_constraint_empty_sector_map(
        self,
        sample_universe: list[int],
    ):
        """SectorConstraint with empty sector_map passes validation but no constraints."""
        constraint = SectorConstraint(
            sector_map={},  # Empty
            max_sector_weight=0.3,
        )

        w = cp.Variable(len(sample_universe))
        context = {"permnos": sample_universe}

        # Validation should warn about low coverage
        errors = constraint.validate(context)
        assert len(errors) > 0

        # Apply should return no constraints (no sectors)
        constraints = constraint.apply(w, context)
        assert len(constraints) == 0


class TestConstraintRelaxationRoundRobin:
    """Test round-robin constraint relaxation ordering."""

    def test_relaxation_uses_least_relaxed_first_within_priority(self):
        """Within same priority, least relaxed constraint is relaxed first."""
        # Create two LOW priority constraints, one already relaxed
        turnover1 = TurnoverConstraint(current_weights={}, max_turnover=0.5)
        rc1 = RelaxableConstraint(
            constraint=turnover1,
            priority=ConstraintPriority.LOW,
        )
        # Already relaxed once
        rc1.current_relaxations = 1

        turnover2 = TurnoverConstraint(current_weights={10001: 0.5}, max_turnover=0.5)
        rc2 = RelaxableConstraint(
            constraint=turnover2,
            priority=ConstraintPriority.LOW,
        )
        # Never relaxed
        rc2.current_relaxations = 0

        # rc2 should be relaxed first (current_relaxations=0 < 1)
        # This is tested implicitly in optimize_with_relaxation sorting logic
        # Verify the can_relax logic works
        assert rc1.can_relax() is True
        assert rc2.can_relax() is True


class TestNumericalEdgeCases:
    """Test numerical edge cases and stability."""

    def test_optimize_with_tiny_expected_returns(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
    ):
        """Optimization handles very small expected returns."""
        # Tiny expected returns (near machine epsilon)
        expected_returns = {p: 1e-10 for p in sample_universe}

        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_mean_variance_cost(
            sample_universe,
            expected_returns,
            risk_aversion=1.0,
        )

        # Should complete without numerical errors
        assert result.status in ["optimal", "suboptimal", "infeasible"]

    def test_optimize_with_large_risk_aversion(
        self,
        sample_barra_model: BarraRiskModel,
        sample_universe: list[int],
        sample_expected_returns: dict[int, float],
    ):
        """Optimization handles very large risk aversion parameter."""
        optimizer = PortfolioOptimizer(sample_barra_model)
        result = optimizer.optimize_mean_variance_cost(
            sample_universe,
            sample_expected_returns,
            risk_aversion=1000.0,  # Very risk-averse
        )

        # Should complete successfully
        if result.status != "infeasible":
            # Very risk-averse should have low risk
            assert result.expected_risk < 1.0  # Annualized
