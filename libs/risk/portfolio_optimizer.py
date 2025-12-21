"""
Portfolio optimization using cvxpy.

This module provides mean-variance optimization with various constraints
and transaction cost modeling, built on top of the Barra risk model.

Key features:
- Minimum variance optimization
- Maximum Sharpe ratio via efficient frontier
- Mean-variance with cost optimization
- Risk parity (equal risk contribution)
- Constraint system: box, sector, factor, turnover, budget, leverage

All computations integrate with BarraRiskModel from T2.3.
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from enum import IntEnum
from typing import Any, Protocol

import cvxpy as cp
import numpy as np
import polars as pl
from numpy.typing import NDArray

from libs.risk.barra_model import BarraRiskModel

logger = logging.getLogger(__name__)


class InfeasibleOptimizationError(Exception):
    """Raised when optimization problem is infeasible."""

    pass


class InsufficientUniverseCoverageError(Exception):
    """Raised when universe coverage in risk model is below threshold."""

    pass


class ConstraintPriority(IntEnum):
    """
    Priority level for constraint relaxation.

    Lower values = higher priority = less likely to relax.
    CRITICAL constraints are never relaxed.
    """

    CRITICAL = 0  # Never relax: budget, box bounds
    HIGH = 1  # Rarely relax: gross leverage
    MEDIUM = 2  # May relax: sector, factor exposure
    LOW = 3  # Relax first: turnover


@dataclass
class OptimizerConfig:
    """Configuration for portfolio optimization."""

    solver: str = "CLARABEL"  # Primary solver (OSQP as fallback)
    solver_timeout: float = 30.0  # Seconds
    verbose: bool = False

    # Constraint defaults
    max_position_weight: float = 0.10  # 10% max per position
    min_position_weight: float = 0.0  # No shorts by default
    max_sector_weight: float = 0.30  # 30% max per sector
    max_factor_exposure: float = 0.50  # |exposure| <= 0.5 sigma

    # Budget/Leverage constraints
    gross_leverage_max: float = 1.0  # sum(|w|) <= gross_leverage_max
    net_exposure_target: float = 1.0  # sum(w) = net_exposure_target

    # Transaction costs (wired into objective)
    tc_linear_bps: float = 10.0  # Linear transaction cost (bps)
    tc_quadratic_bps: float = 0.0  # Quadratic market impact (bps)

    # Turnover penalty (lambda in objective)
    turnover_penalty: float = 0.0  # Penalty weight for turnover in objective

    # Risk-free rate for Sharpe calculations
    risk_free_rate: float = 0.0  # Annual risk-free rate

    # Coverage requirement
    min_coverage: float = 0.8  # Minimum universe coverage in risk model

    # Constraint relaxation settings (opt-in feature)
    enable_constraint_relaxation: bool = False  # Enable hierarchical relaxation


@dataclass
class OptimizationResult:
    """Result of portfolio optimization."""

    solution_id: str  # UUID
    as_of_date: date
    objective: str  # 'min_variance', 'max_sharpe', 'risk_parity', 'mean_variance_cost'
    status: str  # 'optimal', 'suboptimal', 'infeasible'

    # Portfolio metrics
    optimal_weights: pl.DataFrame  # permno, weight, delta_weight
    expected_return: float | None
    expected_risk: float  # Annualized volatility
    sharpe_ratio: float | None

    # Cost metrics
    turnover: float  # Sum of |delta_weight|
    transaction_cost: float  # Estimated cost

    # Solver info
    solver_time_ms: int
    solver_status: str

    # Provenance
    model_version: str
    dataset_version_ids: dict[str, str]

    def to_storage_format(self) -> tuple[pl.DataFrame, pl.DataFrame]:
        """
        Convert to storage format matching P4T2_TASK.md schema.

        Returns:
            Tuple of (optimizer_solutions_df, optimal_weights_df)
        """
        version_str = "|".join(f"{k}:{v}" for k, v in sorted(self.dataset_version_ids.items()))

        solutions_df = pl.DataFrame(
            {
                "solution_id": [self.solution_id],
                "as_of_date": [self.as_of_date],
                "objective": [self.objective],
                "status": [self.status],
                "expected_return": [self.expected_return],
                "expected_risk": [self.expected_risk],
                "sharpe_ratio": [self.sharpe_ratio],
                "turnover": [self.turnover],
                "transaction_cost": [self.transaction_cost],
                "solver_time_ms": [self.solver_time_ms],
                "model_version": [self.model_version],
                "dataset_version_id": [version_str],
            }
        )

        weights_df = self.optimal_weights.with_columns(
            pl.lit(self.solution_id).alias("solution_id")
        ).select(["solution_id", "permno", "weight", "delta_weight"])

        return solutions_df, weights_df


# ============================================================================
# Constraint Protocol and Implementations
# ============================================================================


class Constraint(Protocol):
    """Protocol for optimization constraints."""

    def apply(self, w: cp.Variable, context: dict[str, Any]) -> list[cp.Constraint]: ...

    def validate(self, context: dict[str, Any]) -> list[str]: ...


@dataclass
class RelaxableConstraint:
    """
    Wrapper for constraints that can be progressively relaxed.

    Used by optimize_with_relaxation() to handle infeasible problems by
    relaxing lower-priority constraints first.

    Attributes:
        constraint: The underlying constraint object (may be relaxed version)
        priority: Priority level (lower = higher priority = less likely to relax)
        relaxation_factor: Multiplier to apply each relaxation iteration (must be > 1.0)
        max_relaxations: Maximum number of times this constraint can be relaxed
        max_relaxation_factor: Upper bound on cumulative relaxation (e.g., 2.0 = 2x original)
        initial_constraint: The original unrelaxed constraint (for cap calculation)
        current_relaxations: Number of times already relaxed (internal state)
    """

    constraint: Any  # Constraint instance (BudgetConstraint, SectorConstraint, etc.)
    priority: ConstraintPriority
    relaxation_factor: float = 1.5
    max_relaxations: int = 3
    max_relaxation_factor: float = 2.0  # Cap cumulative relaxation at 2x original
    initial_constraint: Any = None  # Original constraint for cap calculation
    current_relaxations: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        """Validate relaxation parameters and set initial_constraint."""
        if self.relaxation_factor <= 1.0:
            raise ValueError(
                f"relaxation_factor must be > 1.0 (got {self.relaxation_factor}). "
                "Values <= 1.0 would tighten constraints instead of relaxing them."
            )
        if self.max_relaxation_factor <= 1.0:
            raise ValueError(
                f"max_relaxation_factor must be > 1.0 (got {self.max_relaxation_factor})"
            )
        # Store initial constraint for cap calculation if not provided
        if self.initial_constraint is None:
            object.__setattr__(self, "initial_constraint", self.constraint)

    def can_relax(self) -> bool:
        """Check if this constraint can be further relaxed."""
        if self.priority == ConstraintPriority.CRITICAL:
            return False
        return self.current_relaxations < self.max_relaxations


@dataclass
class BudgetConstraint:
    """
    Net exposure (sum of weights) constraint.

    Enforces: sum(w) = target (or within tolerance)
    """

    target: float = 1.0  # Fully invested
    tolerance: float = 0.0  # If >0, allows sum(w) in [target-tol, target+tol]

    def apply(self, w: cp.Variable, context: dict[str, Any]) -> list[cp.Constraint]:
        if self.tolerance == 0:
            return [cp.sum(w) == self.target]  # type: ignore[attr-defined]
        return [
            cp.sum(w) >= self.target - self.tolerance,  # type: ignore[attr-defined]
            cp.sum(w) <= self.target + self.tolerance,  # type: ignore[attr-defined]
        ]

    def validate(self, context: dict[str, Any]) -> list[str]:
        return []


@dataclass
class GrossLeverageConstraint:
    """
    Gross leverage (sum of absolute weights) constraint.

    Enforces: sum(|w|) <= max_leverage
    """

    max_leverage: float = 1.0  # Long-only: 1.0, 130/30: 1.6

    def apply(self, w: cp.Variable, context: dict[str, Any]) -> list[cp.Constraint]:
        return [cp.norm(w, 1) <= self.max_leverage]  # type: ignore[attr-defined]

    def validate(self, context: dict[str, Any]) -> list[str]:
        if self.max_leverage < 0:
            return ["max_leverage must be non-negative"]
        return []


@dataclass
class BoxConstraint:
    """
    Per-position weight bounds.

    Enforces: min_weight <= w_i <= max_weight for all i
    """

    min_weight: float = 0.0
    max_weight: float = 0.10

    def apply(self, w: cp.Variable, context: dict[str, Any]) -> list[cp.Constraint]:
        return [w >= self.min_weight, w <= self.max_weight]

    def validate(self, context: dict[str, Any]) -> list[str]:
        errors = []
        if self.min_weight > self.max_weight:
            errors.append(f"min_weight ({self.min_weight}) > max_weight ({self.max_weight})")
        return errors


@dataclass
class SectorConstraint:
    """
    Sector exposure bounds.

    Enforces: sum(w_i for i in sector) <= max_sector_weight for each sector
    """

    sector_map: dict[int, str]  # permno -> sector
    max_sector_weight: float = 0.30
    min_sector_weight: float = 0.0  # Optional minimum

    def apply(self, w: cp.Variable, context: dict[str, Any]) -> list[cp.Constraint]:
        constraints: list[cp.Constraint] = []
        permnos: list[int] = context["permnos"]

        # Group indices by sector
        sector_indices: dict[str, list[int]] = {}
        for i, p in enumerate(permnos):
            sector = self.sector_map.get(p)
            if sector:
                sector_indices.setdefault(sector, []).append(i)

        for _sector, indices in sector_indices.items():
            sector_weight = cp.sum(w[indices])  # type: ignore[attr-defined]
            if self.min_sector_weight > 0:
                constraints.append(sector_weight >= self.min_sector_weight)
            constraints.append(sector_weight <= self.max_sector_weight)

        return constraints

    def validate(self, context: dict[str, Any]) -> list[str]:
        errors = []
        permnos = context.get("permnos", [])
        if permnos:
            mapped = set(self.sector_map.keys()) & set(permnos)
            coverage = len(mapped) / len(permnos)
            if coverage < 0.8:
                errors.append(
                    f"Sector map covers only {len(mapped)}/{len(permnos)} "
                    f"({coverage:.1%}) universe permnos"
                )
        return errors


@dataclass
class FactorExposureConstraint:
    """
    Factor neutrality/exposure constraint.

    Enforces: |w' @ B_factor - target| <= tolerance
    Or if tolerance=0: w' @ B_factor = target (exact)
    """

    factor_name: str
    target_exposure: float = 0.0  # Target exposure (0 = neutral)
    tolerance: float = 0.50  # |exposure - target| <= tolerance

    def apply(self, w: cp.Variable, context: dict[str, Any]) -> list[cp.Constraint]:
        B: NDArray[np.floating[Any]] = context["factor_loadings"]  # N×K matrix
        factor_names: list[str] = context["factor_names"]

        if self.factor_name not in factor_names:
            raise ValueError(f"Unknown factor: {self.factor_name}")

        factor_idx = factor_names.index(self.factor_name)
        factor_loading = B[:, factor_idx]  # N×1

        exposure = w @ factor_loading

        if self.tolerance == 0:
            return [exposure == self.target_exposure]
        return [
            exposure >= self.target_exposure - self.tolerance,
            exposure <= self.target_exposure + self.tolerance,
        ]

    def validate(self, context: dict[str, Any]) -> list[str]:
        factor_names = context.get("factor_names", [])
        if self.factor_name not in factor_names:
            return [f"Factor '{self.factor_name}' not in model: {factor_names}"]
        return []


@dataclass
class TurnoverConstraint:
    """
    Maximum turnover from current portfolio.

    Enforces: sum(|w - w_current|) <= max_turnover
    """

    current_weights: dict[int, float]  # permno -> weight
    max_turnover: float = 0.50

    def apply(self, w: cp.Variable, context: dict[str, Any]) -> list[cp.Constraint]:
        permnos: list[int] = context["permnos"]
        w_current = np.array([self.current_weights.get(p, 0.0) for p in permnos])
        return [cp.norm(w - w_current, 1) <= self.max_turnover]  # type: ignore[attr-defined]

    def validate(self, context: dict[str, Any]) -> list[str]:
        if self.max_turnover < 0:
            return ["max_turnover must be non-negative"]
        return []


@dataclass
class ReturnTargetConstraint:
    """Return target constraint for efficient frontier."""

    expected_returns: dict[int, float]  # permno -> expected return
    min_return: float

    def apply(self, w: cp.Variable, context: dict[str, Any]) -> list[cp.Constraint]:
        permnos: list[int] = context["permnos"]
        mu = np.array([self.expected_returns.get(p, 0.0) for p in permnos])
        return [mu @ w >= self.min_return]

    def validate(self, context: dict[str, Any]) -> list[str]:
        return []


# ============================================================================
# Portfolio Optimizer
# ============================================================================


class PortfolioOptimizer:
    """
    Mean-variance portfolio optimizer using cvxpy.

    Supports:
    - Minimum variance optimization
    - Maximum Sharpe ratio optimization (via efficient frontier)
    - Mean-variance with cost optimization
    - Risk parity optimization (equal risk contribution)
    - Box constraints (min/max position weights)
    - Sector constraints (max sector exposure)
    - Factor exposure constraints (neutrality)
    - Transaction cost penalties

    Integrates with BarraRiskModel for covariance estimation.
    """

    def __init__(
        self,
        risk_model: BarraRiskModel,
        config: OptimizerConfig | None = None,
    ):
        """
        Initialize optimizer with risk model.

        Args:
            risk_model: BarraRiskModel from T2.3
            config: Optional configuration override
        """
        self.risk_model = risk_model
        self.config = config or OptimizerConfig()

    def optimize_min_variance(
        self,
        universe: list[int],
        current_weights: dict[int, float] | None = None,
        constraints: list[Constraint] | None = None,
    ) -> OptimizationResult:
        """
        Minimize portfolio variance subject to constraints.

        Args:
            universe: List of permnos to optimize over
            current_weights: Current portfolio weights (for turnover/cost calculation)
            constraints: Additional constraints beyond defaults

        Returns:
            OptimizationResult with optimal weights
        """
        start_time = time.perf_counter()

        # Build covariance and validate coverage
        sigma, permnos, factor_loadings = self._build_covariance(universe)
        n = len(permnos)

        # Build context for constraints
        context = self._build_context(permnos, factor_loadings)

        # Get current weights aligned to permnos
        w_current = self._align_current_weights(permnos, current_weights)

        # Build and solve problem
        w, problem = self._build_min_variance_problem(
            sigma, n, w_current, constraints or [], context
        )

        solver_status = self._solve_with_fallback(problem)
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)

        # Build result
        return self._build_result(
            w=w,
            permnos=permnos,
            sigma=sigma,
            w_current=w_current,
            objective="min_variance",
            problem=problem,
            solver_status=solver_status,
            solver_time_ms=elapsed_ms,
            expected_returns=None,
        )

    def optimize_max_sharpe(
        self,
        universe: list[int],
        expected_returns: dict[int, float],
        current_weights: dict[int, float] | None = None,
        constraints: list[Constraint] | None = None,
        n_return_targets: int = 20,
    ) -> OptimizationResult:
        """
        Maximize Sharpe ratio via efficient frontier search.

        Approach: Solve min-variance for multiple return targets,
        then select the portfolio with highest Sharpe ratio.

        Note: Transaction costs are NOT applied in max-Sharpe mode
        because they make the problem non-convex. Use optimize_mean_variance_cost()
        for cost-aware optimization.

        Args:
            universe: List of permnos to optimize over
            expected_returns: Expected returns per permno
            current_weights: Current portfolio weights
            constraints: Additional constraints
            n_return_targets: Number of points on efficient frontier

        Returns:
            OptimizationResult with highest Sharpe portfolio
        """
        start_time = time.perf_counter()

        # Build covariance and validate coverage
        sigma, permnos, factor_loadings = self._build_covariance(universe)

        # Build context for constraints
        context = self._build_context(permnos, factor_loadings)

        # Get aligned expected returns
        mu = np.array([expected_returns.get(p, 0.0) for p in permnos])
        r_f_daily = self.config.risk_free_rate / 252

        # Find feasible return range
        min_ret, max_ret = self._find_return_range(mu, sigma, permnos, constraints or [], context)

        if min_ret is None or max_ret is None:
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            return self._infeasible_result(
                "No feasible point on efficient frontier",
                objective="max_sharpe",
                permnos=permnos,
                solver_time_ms=elapsed_ms,
            )

        # Get current weights
        w_current = self._align_current_weights(permnos, current_weights)

        best_sharpe = -np.inf
        best_result: OptimizationResult | None = None

        for target_ret in np.linspace(min_ret, max_ret, n_return_targets):
            # Add return target constraint
            return_constraint = ReturnTargetConstraint(
                expected_returns=expected_returns,
                min_return=float(target_ret),
            )
            augmented_constraints = (constraints or []) + [return_constraint]

            # Build and solve
            w, problem = self._build_min_variance_problem(
                sigma,
                len(permnos),
                None,  # No transaction costs in max-Sharpe
                augmented_constraints,
                context,
            )

            solver_status = self._solve_with_fallback(problem)

            if problem.status in [cp.OPTIMAL, cp.OPTIMAL_INACCURATE]:
                weights = w.value
                assert weights is not None  # Guaranteed by OPTIMAL status
                port_return = float(mu @ weights)
                port_risk = float(np.sqrt(weights @ sigma @ weights))

                if port_risk > 1e-10:
                    sharpe = (port_return - r_f_daily) / (port_risk / np.sqrt(252))

                    if sharpe > best_sharpe:
                        best_sharpe = sharpe
                        # Store this result
                        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
                        best_result = self._build_result(
                            w=w,
                            permnos=permnos,
                            sigma=sigma,
                            w_current=w_current,
                            objective="max_sharpe",
                            problem=problem,
                            solver_status=solver_status,
                            solver_time_ms=elapsed_ms,
                            expected_returns=expected_returns,
                        )

        if best_result is None:
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            return self._infeasible_result(
                "No feasible point with positive Sharpe",
                objective="max_sharpe",
                permnos=permnos,
                solver_time_ms=elapsed_ms,
            )

        best_result.sharpe_ratio = float(best_sharpe)
        return best_result

    def optimize_mean_variance_cost(
        self,
        universe: list[int],
        expected_returns: dict[int, float],
        risk_aversion: float = 1.0,
        current_weights: dict[int, float] | None = None,
        constraints: list[Constraint] | None = None,
    ) -> OptimizationResult:
        """
        Mean-variance optimization with transaction costs.

        Objective: maximize mu' @ w - (gamma/2) * w' @ Sigma @ w - costs

        This formulation naturally handles transaction costs in QP form.

        Args:
            universe: List of permnos to optimize over
            expected_returns: Expected returns per permno
            risk_aversion: Risk aversion parameter (higher = more risk-averse)
            current_weights: Current portfolio weights
            constraints: Additional constraints

        Returns:
            OptimizationResult with optimal weights
        """
        start_time = time.perf_counter()

        # Build covariance and validate coverage
        sigma, permnos, factor_loadings = self._build_covariance(universe)
        n = len(permnos)

        # Build context for constraints
        context = self._build_context(permnos, factor_loadings)

        # Get aligned expected returns and current weights
        mu = np.array([expected_returns.get(p, 0.0) for p in permnos])
        w_current = self._align_current_weights(permnos, current_weights)

        # Build cvxpy problem
        w = cp.Variable(n)

        # Objective: (gamma/2) * w' @ Sigma @ w - mu' @ w + costs
        objective = (risk_aversion / 2) * cp.quad_form(w, sigma) - mu @ w  # type: ignore[attr-defined]

        if w_current is not None:
            tc_linear = self.config.turnover_penalty + self.config.tc_linear_bps / 10000
            if tc_linear > 0:
                objective += tc_linear * cp.norm(w - w_current, 1)  # type: ignore[attr-defined]
            if self.config.tc_quadratic_bps > 0:
                tc_quad = self.config.tc_quadratic_bps / 10000
                objective += tc_quad * cp.sum_squares(w - w_current)  # type: ignore[attr-defined]

        # Apply default constraints (with variable w)
        all_constraints = self._build_default_constraints(w, constraints or [], context)

        problem = cp.Problem(cp.Minimize(objective), all_constraints)
        solver_status = self._solve_with_fallback(problem)
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)

        return self._build_result(
            w=w,
            permnos=permnos,
            sigma=sigma,
            w_current=w_current,
            objective="mean_variance_cost",
            problem=problem,
            solver_status=solver_status,
            solver_time_ms=elapsed_ms,
            expected_returns=expected_returns,
        )

    def optimize_risk_parity(
        self,
        universe: list[int],
        current_weights: dict[int, float] | None = None,
        max_iterations: int = 100,
        tolerance: float = 1e-6,
    ) -> OptimizationResult:
        """
        Risk parity optimization (equal risk contribution).

        Uses iterative algorithm to achieve equal marginal risk contribution
        from each asset.

        Args:
            universe: List of permnos to optimize over
            current_weights: Current portfolio weights
            max_iterations: Maximum iterations for convergence
            tolerance: Convergence tolerance for risk contribution equality

        Returns:
            OptimizationResult with risk parity weights
        """
        start_time = time.perf_counter()

        # Build covariance and validate coverage
        sigma, permnos, _ = self._build_covariance(universe)
        n = len(permnos)

        # Get current weights
        w_current = self._align_current_weights(permnos, current_weights)

        # Initialize with equal weights
        w = np.ones(n) / n

        iterations_run = 0
        for _ in range(max_iterations):
            iterations_run += 1

            # Compute marginal risk contributions
            sigma_w = sigma @ w
            total_risk = np.sqrt(w @ sigma_w)

            if total_risk < 1e-10:
                break

            mrc = sigma_w / total_risk  # Marginal Risk Contribution

            # Risk contribution: RC_i = w_i * MRC_i
            rc = w * mrc
            rc_sum = rc.sum()

            # Target: equal risk contribution
            target_rc = rc_sum / n

            # Check convergence: all RC within tolerance of target
            rc_error = np.max(np.abs(rc - target_rc))
            if rc_error < tolerance:
                break

            # Update weights proportionally to target/actual RC ratio
            # Guard against division by zero
            rc_safe = np.maximum(rc, 1e-10)
            w_new = w * (target_rc / rc_safe)
            w_new = w_new / w_new.sum()  # Renormalize

            w = w_new

        # Verify equal risk contribution achieved
        sigma_w = sigma @ w
        total_risk = np.sqrt(w @ sigma_w)
        if total_risk > 1e-10:
            rc = w * (sigma_w / total_risk)
            rc_spread = rc.max() - rc.min()
        else:
            rc_spread = 0.0

        status = "optimal" if rc_spread < tolerance * 10 else "suboptimal"
        if rc_spread > tolerance * 10:
            logger.warning(f"Risk parity did not fully converge: RC spread = {rc_spread:.6f}")

        elapsed_ms = int((time.perf_counter() - start_time) * 1000)

        return self._build_risk_parity_result(
            w=w,
            permnos=permnos,
            sigma=sigma,
            w_current=w_current,
            status=status,
            solver_time_ms=elapsed_ms,
            iterations=iterations_run,
        )

    # ========================================================================
    # Private Helper Methods
    # ========================================================================

    def _build_covariance(self, universe: list[int]) -> tuple[
        NDArray[np.floating[Any]],
        list[int],
        NDArray[np.floating[Any]],
    ]:
        """
        Build full asset covariance matrix from Barra model.

        Sigma = B @ F @ B.T + D

        Where:
        - B = N×K factor loadings matrix
        - F = K×K factor covariance matrix (from T2.2)
        - D = N×N diagonal specific variance matrix

        Args:
            universe: List of permnos to include

        Returns:
            Tuple of (sigma, aligned_permnos, factor_loadings)

        Raises:
            InsufficientUniverseCoverageError: If coverage below threshold
        """
        # Filter to covered permnos (have both loadings and specific risk)
        loadings_permnos = set(self.risk_model.factor_loadings["permno"].to_list())
        specific_permnos = set(self.risk_model.specific_risks["permno"].to_list())
        covered = [p for p in universe if p in loadings_permnos and p in specific_permnos]

        coverage = len(covered) / len(universe) if universe else 1.0
        if coverage < self.config.min_coverage:
            raise InsufficientUniverseCoverageError(
                f"Universe coverage {coverage:.1%} below minimum {self.config.min_coverage:.1%}. "
                f"Covered {len(covered)}/{len(universe)} permnos."
            )

        if len(covered) < len(universe):
            logger.warning(f"Universe coverage: {len(covered)}/{len(universe)} ({coverage:.1%})")

        # Sort for consistent ordering
        covered = sorted(covered)

        # Extract aligned factor loadings (N×K)
        B = (
            self.risk_model.factor_loadings.filter(pl.col("permno").is_in(covered))
            .sort("permno")
            .select(self.risk_model.factor_names)
            .to_numpy()
            .astype(np.float64)
        )

        # Get factor covariance (K×K)
        F = self.risk_model.factor_covariance

        # Extract aligned specific variances (N×1 diagonal)
        D_diag = (
            self.risk_model.specific_risks.filter(pl.col("permno").is_in(covered))
            .sort("permno")["specific_variance"]
            .to_numpy()
            .astype(np.float64)
        )

        # Ensure non-negative specific variances
        D_diag = np.maximum(D_diag, 1e-10)

        # Compute full covariance: Sigma = B @ F @ B.T + diag(D)
        sigma = B @ F @ B.T + np.diag(D_diag)

        # Ensure PSD (regularize if needed)
        sigma = self._regularize_covariance(sigma)

        return sigma, covered, B

    def _regularize_covariance(self, sigma: NDArray[np.floating[Any]]) -> NDArray[np.floating[Any]]:
        """Add small ridge to handle near-singular covariance."""
        min_eigenvalue = np.linalg.eigvalsh(sigma).min()
        if min_eigenvalue < 1e-8:
            ridge = max(1e-8 - min_eigenvalue, 1e-8)
            logger.warning(f"Regularizing covariance with ridge={ridge:.2e}")
            sigma = sigma + ridge * np.eye(sigma.shape[0])
        return sigma

    def _build_context(
        self,
        permnos: list[int],
        factor_loadings: NDArray[np.floating[Any]],
    ) -> dict[str, Any]:
        """Build context dict for constraint evaluation."""
        return {
            "permnos": permnos,
            "factor_loadings": factor_loadings,
            "factor_names": self.risk_model.factor_names,
        }

    def _align_current_weights(
        self,
        permnos: list[int],
        current_weights: dict[int, float] | None,
    ) -> NDArray[np.floating[Any]] | None:
        """Align current weights to permno order."""
        if current_weights is None:
            return None
        return np.array([current_weights.get(p, 0.0) for p in permnos])

    def _build_default_constraints(
        self,
        w: cp.Variable,
        user_constraints: list[Constraint],
        context: dict[str, Any],
    ) -> list[cp.Constraint]:
        """Build default + user constraints applied to variable w."""
        all_constraints: list[cp.Constraint] = []

        # Default: budget constraint (sum(w) = net_exposure_target)
        all_constraints.append(cp.sum(w) == self.config.net_exposure_target)  # type: ignore[attr-defined]

        # Default: box constraints (w_min <= w <= w_max)
        all_constraints.append(w >= self.config.min_position_weight)
        all_constraints.append(w <= self.config.max_position_weight)

        # Check if user provided a GrossLeverageConstraint - if so, skip default
        has_user_leverage_constraint = any(
            isinstance(c, GrossLeverageConstraint) for c in user_constraints
        )
        if not has_user_leverage_constraint:
            # Default: gross leverage constraint (sum(|w|) <= gross_leverage_max)
            all_constraints.append(cp.norm(w, 1) <= self.config.gross_leverage_max)  # type: ignore[attr-defined]

        # Apply and validate user constraints
        for c in user_constraints:
            errors = c.validate(context)
            if errors:
                logger.warning(f"Constraint validation warnings: {errors}")
            all_constraints.extend(c.apply(w, context))

        return all_constraints

    def _build_min_variance_problem(
        self,
        sigma: NDArray[np.floating[Any]],
        n: int,
        w_current: NDArray[np.floating[Any]] | None,
        constraints: list[Constraint],
        context: dict[str, Any],
    ) -> tuple[cp.Variable, cp.Problem]:
        """Build minimum variance optimization problem."""
        w = cp.Variable(n)

        # Objective: w' @ Sigma @ w + turnover costs
        objective = cp.quad_form(w, sigma)  # type: ignore[attr-defined]

        if w_current is not None:
            tc_linear = self.config.turnover_penalty + self.config.tc_linear_bps / 10000
            if tc_linear > 0:
                objective += tc_linear * cp.norm(w - w_current, 1)  # type: ignore[attr-defined]
            if self.config.tc_quadratic_bps > 0:
                tc_quad = self.config.tc_quadratic_bps / 10000
                objective += tc_quad * cp.sum_squares(w - w_current)  # type: ignore[attr-defined]

        # Build constraints
        all_constraints: list[cp.Constraint] = []

        # Default: budget constraint
        all_constraints.append(cp.sum(w) == self.config.net_exposure_target)  # type: ignore[attr-defined]

        # Default: box constraints
        all_constraints.append(w >= self.config.min_position_weight)
        all_constraints.append(w <= self.config.max_position_weight)

        # Check if user provided a GrossLeverageConstraint - if so, skip default
        has_user_leverage_constraint = any(
            isinstance(c, GrossLeverageConstraint) for c in constraints
        )
        if not has_user_leverage_constraint:
            # Default: gross leverage constraint
            all_constraints.append(cp.norm(w, 1) <= self.config.gross_leverage_max)  # type: ignore[attr-defined]

        # User constraints
        for c in constraints:
            errors = c.validate(context)
            if errors:
                logger.warning(f"Constraint validation warnings: {errors}")
            all_constraints.extend(c.apply(w, context))

        problem = cp.Problem(cp.Minimize(objective), all_constraints)
        return w, problem

    def _solve_with_fallback(self, problem: cp.Problem) -> str:
        """Try primary solver, fall back to secondary on failure.

        Returns:
            Solver name if optimal found, or status string for infeasible/unbounded.

        Raises:
            RuntimeError: If all solvers fail with errors (distinct from infeasible).
        """
        solvers = [self.config.solver, "OSQP", "SCS"]
        errors: list[str] = []
        # Track if any solver determined the problem is infeasible/unbounded
        infeasible_status: str | None = None

        for solver in solvers:
            try:
                problem.solve(  # type: ignore[no-untyped-call]
                    solver=solver,
                    verbose=self.config.verbose,
                )
                if problem.status in [cp.OPTIMAL, cp.OPTIMAL_INACCURATE]:
                    return solver
                # Solver ran but problem is infeasible/unbounded - this is valid
                if problem.status in [cp.INFEASIBLE, cp.UNBOUNDED, cp.INFEASIBLE_INACCURATE]:
                    infeasible_status = str(problem.status)
                    logger.info(f"Solver {solver} determined problem is {problem.status}")
                    return f"INFEASIBLE:{solver}"  # Return to indicate not a solver error
                # Other status (user_limit, etc) - try next solver
                errors.append(f"{solver}: status={problem.status}")
            except cp.SolverError as e:
                logger.warning(f"Solver {solver} failed: {e}, trying next")
                errors.append(f"{solver}: {e}")
                continue

        # If any solver determined infeasible, that's not an error
        if infeasible_status:
            return f"INFEASIBLE:{infeasible_status}"

        # All solvers failed with errors - raise exception with details
        raise RuntimeError(
            f"All solvers failed. This may indicate a solver installation issue "
            f"or numerical instability. Errors: {'; '.join(errors)}"
        )

    def _find_return_range(
        self,
        mu: NDArray[np.floating[Any]],
        sigma: NDArray[np.floating[Any]],
        permnos: list[int],
        constraints: list[Constraint],
        context: dict[str, Any],
    ) -> tuple[float | None, float | None]:
        """Find feasible return range for efficient frontier."""
        n = len(permnos)

        # Minimum return: solve min-variance, compute return
        w_min, problem_min = self._build_min_variance_problem(sigma, n, None, constraints, context)
        self._solve_with_fallback(problem_min)

        if problem_min.status not in [cp.OPTIMAL, cp.OPTIMAL_INACCURATE]:
            return None, None

        assert w_min.value is not None  # Guaranteed by OPTIMAL status
        min_ret = float(mu @ w_min.value)

        # Maximum return: maximize return subject to constraints
        w_max = cp.Variable(n)
        all_constraints: list[cp.Constraint] = [
            cp.sum(w_max) == self.config.net_exposure_target,  # type: ignore[attr-defined]
            w_max >= self.config.min_position_weight,
            w_max <= self.config.max_position_weight,
            cp.norm(w_max, 1) <= self.config.gross_leverage_max,  # type: ignore[attr-defined]
        ]
        for c in constraints:
            all_constraints.extend(c.apply(w_max, context))

        problem_max = cp.Problem(cp.Maximize(mu @ w_max), all_constraints)
        self._solve_with_fallback(problem_max)

        if problem_max.status not in [cp.OPTIMAL, cp.OPTIMAL_INACCURATE]:
            return None, None

        assert w_max.value is not None  # Guaranteed by OPTIMAL status
        max_ret = float(mu @ w_max.value)

        return min_ret, max_ret

    def _build_result(
        self,
        w: cp.Variable,
        permnos: list[int],
        sigma: NDArray[np.floating[Any]],
        w_current: NDArray[np.floating[Any]] | None,
        objective: str,
        problem: cp.Problem,
        solver_status: str,
        solver_time_ms: int,
        expected_returns: dict[int, float] | None,
    ) -> OptimizationResult:
        """Build OptimizationResult from solved problem."""
        if problem.status not in [cp.OPTIMAL, cp.OPTIMAL_INACCURATE]:
            return self._infeasible_result(
                f"Solver returned {problem.status}",
                objective=objective,
                permnos=permnos,
                solver_time_ms=solver_time_ms,
            )

        weights = w.value
        # Defensive check: OPTIMAL status should guarantee non-None weights,
        # but some solvers may have bugs. Handle gracefully for production safety.
        if weights is None:
            logger.error("Solver bug: OPTIMAL status but None weights - this should not happen")
            return self._infeasible_result(
                "Solver returned None weights (solver bug)",
                objective=objective,
                permnos=permnos,
                solver_time_ms=solver_time_ms,
            )

        # Compute metrics
        port_var = float(weights @ sigma @ weights)
        port_risk = np.sqrt(port_var) * np.sqrt(252)  # Annualize

        # Expected return if provided
        port_return: float | None = None
        sharpe: float | None = None
        if expected_returns is not None:
            mu = np.array([expected_returns.get(p, 0.0) for p in permnos])
            port_return = float(mu @ weights) * 252  # Annualize
            r_f = self.config.risk_free_rate
            if port_risk > 1e-10:
                sharpe = (port_return - r_f) / port_risk

        # Turnover and transaction cost
        if w_current is not None:
            delta = weights - w_current
            turnover = float(np.sum(np.abs(delta)))
            tc = turnover * self.config.tc_linear_bps / 10000
        else:
            delta = weights
            turnover = float(np.sum(np.abs(weights)))
            tc = 0.0

        # Build weights DataFrame
        weights_df = pl.DataFrame(
            {
                "permno": permnos,
                "weight": weights.tolist(),
                "delta_weight": delta.tolist(),
            }
        )

        return OptimizationResult(
            solution_id=str(uuid.uuid4()),
            as_of_date=self.risk_model.as_of_date,
            objective=objective,
            status="optimal" if problem.status == cp.OPTIMAL else "suboptimal",
            optimal_weights=weights_df,
            expected_return=port_return,
            expected_risk=float(port_risk),
            sharpe_ratio=sharpe,
            turnover=turnover,
            transaction_cost=tc,
            solver_time_ms=solver_time_ms,
            solver_status=solver_status,
            model_version=self.risk_model.model_version,
            dataset_version_ids=self.risk_model.dataset_version_ids.copy(),
        )

    def _build_risk_parity_result(
        self,
        w: NDArray[np.floating[Any]],
        permnos: list[int],
        sigma: NDArray[np.floating[Any]],
        w_current: NDArray[np.floating[Any]] | None,
        status: str,
        solver_time_ms: int,
        iterations: int,
    ) -> OptimizationResult:
        """Build result for risk parity optimization."""
        # Compute metrics
        port_var = float(w @ sigma @ w)
        port_risk = np.sqrt(port_var) * np.sqrt(252)  # Annualize

        # Turnover
        if w_current is not None:
            delta = w - w_current
            turnover = float(np.sum(np.abs(delta)))
            tc = turnover * self.config.tc_linear_bps / 10000
        else:
            delta = w
            turnover = float(np.sum(np.abs(w)))
            tc = 0.0

        # Build weights DataFrame
        weights_df = pl.DataFrame(
            {
                "permno": permnos,
                "weight": w.tolist(),
                "delta_weight": delta.tolist(),
            }
        )

        return OptimizationResult(
            solution_id=str(uuid.uuid4()),
            as_of_date=self.risk_model.as_of_date,
            objective="risk_parity",
            status=status,
            optimal_weights=weights_df,
            expected_return=None,
            expected_risk=float(port_risk),
            sharpe_ratio=None,
            turnover=turnover,
            transaction_cost=tc,
            solver_time_ms=solver_time_ms,
            solver_status=f"iterative_{iterations}",
            model_version=self.risk_model.model_version,
            dataset_version_ids=self.risk_model.dataset_version_ids.copy(),
        )

    def _infeasible_result(
        self,
        reason: str,
        objective: str,
        permnos: list[int],
        solver_time_ms: int,
    ) -> OptimizationResult:
        """Build infeasible result."""
        logger.warning(f"Optimization infeasible: {reason}")

        # Empty weights
        weights_df = pl.DataFrame(
            {
                "permno": permnos,
                "weight": [0.0] * len(permnos),
                "delta_weight": [0.0] * len(permnos),
            }
        )

        return OptimizationResult(
            solution_id=str(uuid.uuid4()),
            as_of_date=self.risk_model.as_of_date,
            objective=objective,
            status="infeasible",
            optimal_weights=weights_df,
            expected_return=None,
            expected_risk=0.0,
            sharpe_ratio=None,
            turnover=0.0,
            transaction_cost=0.0,
            solver_time_ms=solver_time_ms,
            solver_status=f"INFEASIBLE: {reason}",
            model_version=self.risk_model.model_version,
            dataset_version_ids=self.risk_model.dataset_version_ids.copy(),
        )

    # ========================================================================
    # Constraint Relaxation Methods
    # ========================================================================
    # TODO: Future architectural improvements (deferred from bugfix scope):
    # 1. Refactor isinstance chain to polymorphic dispatch - add relax() method
    #    to Constraint protocol so each constraint class encapsulates relaxation logic
    # 2. Add BoxConstraint relaxation support (expanding min/max weight bounds)
    # 3. BudgetConstraint intentionally excluded - sum(weights)=1 is critical
    # See: Code review discussion with Gemini/Codex agreeing to defer these

    def _relax_constraint(self, relaxable: RelaxableConstraint) -> tuple[RelaxableConstraint, bool]:
        """
        Relax a constraint by its relaxation factor.

        Creates a new constraint with expanded bounds and increments the
        relaxation counter. Returns a tuple of (relaxed_constraint, was_relaxed).

        Enforces max_relaxation_factor cap to prevent unbounded relaxation.
        Uses initial_constraint stored on RelaxableConstraint for cap calculation.

        Supports relaxing:
        - TurnoverConstraint: max_turnover *= factor
        - SectorConstraint: max_sector_weight *= factor (capped at 100% and max_relaxation_factor)
        - FactorExposureConstraint: tolerance *= factor (handles zero tolerance)
        - GrossLeverageConstraint: max_leverage *= factor (capped by max_relaxation_factor)

        Args:
            relaxable: The RelaxableConstraint wrapper to relax

        Returns:
            Tuple of (new_relaxable, was_relaxed) - was_relaxed is False if
            constraint cannot be relaxed (CRITICAL, max_relaxations reached, or at cap)
        """
        if not relaxable.can_relax():
            return relaxable, False

        constraint = relaxable.constraint
        original = relaxable.initial_constraint  # Use stored initial constraint
        factor = relaxable.relaxation_factor
        max_factor = relaxable.max_relaxation_factor

        # Create relaxed version based on constraint type
        new_constraint: Any
        value_changed = True  # Track if relaxation actually changed the value

        # Epsilon floor for zero-bound constraints
        EPSILON = 0.01  # 1% floor for constraints starting at 0

        def _calc_relaxed_value(
            original_val: float,
            current_val: float,
            hard_cap: float | None = None,
        ) -> tuple[float, bool]:
            """Calculate relaxed value with epsilon floor and max_relaxation_factor cap.

            Returns (new_value, changed) tuple.
            """
            # Calculate max allowed based on original value
            if original_val < 1e-9:
                max_allowed = EPSILON * max_factor
            else:
                max_allowed = original_val * max_factor
            # Apply hard cap if specified (e.g., 100% for sector weights)
            if hard_cap is not None:
                max_allowed = min(max_allowed, hard_cap)
            # Calculate new value with epsilon floor for zero-bound
            if current_val < 1e-9:
                new_val = min(EPSILON, max_allowed)
            else:
                new_val = min(current_val * factor, max_allowed)
            # Check if value actually changed
            changed = abs(new_val - current_val) >= 1e-9
            return new_val, changed

        if isinstance(constraint, TurnoverConstraint):
            new_max, value_changed = _calc_relaxed_value(
                original.max_turnover, constraint.max_turnover
            )
            if value_changed:
                new_constraint = TurnoverConstraint(
                    current_weights=constraint.current_weights,
                    max_turnover=new_max,
                )
                logger.info(
                    f"Relaxed TurnoverConstraint: max_turnover "
                    f"{constraint.max_turnover:.2f} -> {new_max:.2f}"
                )

        elif isinstance(constraint, SectorConstraint):
            # Respect both max_relaxation_factor and 100% hard cap
            # NOTE: Only max_sector_weight is relaxed, not min_sector_weight.
            # Relaxation aims to ease constraints when optimization fails.
            # Increasing max allows more allocation to a sector (helpful when
            # constraints are too tight). Decreasing min would require LESS
            # allocation, which doesn't help find feasible solutions - the
            # optimizer can already allocate less if needed.
            new_max, value_changed = _calc_relaxed_value(
                original.max_sector_weight, constraint.max_sector_weight, hard_cap=1.0
            )
            if value_changed:
                new_constraint = SectorConstraint(
                    sector_map=constraint.sector_map,
                    max_sector_weight=new_max,
                    min_sector_weight=constraint.min_sector_weight,
                )
                # Include sector names for distinguishing multiple SectorConstraints
                sectors = sorted(set(constraint.sector_map.values()))
                sector_info = f"[{', '.join(sectors[:3])}{'...' if len(sectors) > 3 else ''}]"
                logger.info(
                    f"Relaxed SectorConstraint{sector_info}: "
                    f"max_sector_weight {constraint.max_sector_weight:.2f} -> {new_max:.2f}"
                )

        elif isinstance(constraint, FactorExposureConstraint):
            new_tol, value_changed = _calc_relaxed_value(original.tolerance, constraint.tolerance)
            if value_changed:
                new_constraint = FactorExposureConstraint(
                    factor_name=constraint.factor_name,
                    target_exposure=constraint.target_exposure,
                    tolerance=new_tol,
                )
                logger.info(
                    f"Relaxed FactorExposureConstraint({constraint.factor_name}): "
                    f"tolerance {constraint.tolerance:.2f} -> {new_tol:.2f}"
                )

        elif isinstance(constraint, GrossLeverageConstraint):
            new_max, value_changed = _calc_relaxed_value(
                original.max_leverage, constraint.max_leverage
            )
            if value_changed:
                new_constraint = GrossLeverageConstraint(max_leverage=new_max)
                logger.info(
                    f"Relaxed GrossLeverageConstraint: max_leverage "
                    f"{constraint.max_leverage:.2f} -> {new_max:.2f}"
                )

        else:
            # Constraint type not supported for relaxation
            logger.warning(
                f"Constraint type {type(constraint).__name__} does not support relaxation"
            )
            return relaxable, False

        if not value_changed:
            logger.info(
                f"Constraint {type(constraint).__name__} already at relaxation "
                "cap, cannot relax further"
            )
            return relaxable, False

        # Create new RelaxableConstraint with incremented counter
        # Preserve initial_constraint for future cap calculations
        new_relaxable = RelaxableConstraint(
            constraint=new_constraint,
            priority=relaxable.priority,
            relaxation_factor=relaxable.relaxation_factor,
            max_relaxations=relaxable.max_relaxations,
            max_relaxation_factor=relaxable.max_relaxation_factor,
            initial_constraint=relaxable.initial_constraint,  # Preserve original
        )
        new_relaxable.current_relaxations = relaxable.current_relaxations + 1

        return new_relaxable, True

    def optimize_with_relaxation(
        self,
        universe: list[int],
        relaxable_constraints: list[RelaxableConstraint],
        current_weights: dict[int, float] | None = None,
    ) -> OptimizationResult:
        """
        Optimize minimum variance with hierarchical constraint relaxation.

        If optimization is infeasible, progressively relax constraints by priority:
        1. LOW priority constraints (e.g., TurnoverConstraint) are relaxed first
        2. MEDIUM priority constraints (e.g., SectorConstraint, FactorExposureConstraint)
        3. HIGH priority constraints (e.g., GrossLeverageConstraint) only if needed
        4. CRITICAL constraints are never relaxed

        Each constraint is relaxed up to its max_relaxations times before moving to
        the next priority level.

        Note:
            This method only supports minimum variance optimization. For other
            objectives (max_sharpe, mean_variance_cost, risk_parity), use the
            corresponding optimize_* methods directly.

        Args:
            universe: List of permnos to optimize over
            relaxable_constraints: List of RelaxableConstraint wrappers
            current_weights: Current portfolio weights

        Returns:
            OptimizationResult - either optimal with relaxed constraints, or
            infeasible if all relaxation attempts fail
        """
        if not self.config.enable_constraint_relaxation:
            # Relaxation disabled, use regular optimization
            plain_constraints = [rc.constraint for rc in relaxable_constraints]
            return self.optimize_min_variance(
                universe=universe,
                current_weights=current_weights,
                constraints=plain_constraints,
            )

        start_time = time.perf_counter()

        # Build covariance and validate coverage
        sigma, permnos, factor_loadings = self._build_covariance(universe)
        n = len(permnos)
        context = self._build_context(permnos, factor_loadings)
        w_current = self._align_current_weights(permnos, current_weights)

        # Initialize working constraints (will be re-sorted each iteration)
        # Each RelaxableConstraint stores its own initial_constraint for cap calculation
        working_constraints = list(relaxable_constraints)

        # Track total relaxation attempts
        # Only count constraints that can actually be relaxed (not CRITICAL)
        total_relaxations = 0
        max_total = sum(
            rc.max_relaxations - rc.current_relaxations
            for rc in relaxable_constraints
            if rc.can_relax()
        )

        for iteration in range(max_total + 1):
            # Re-sort constraints each iteration for round-robin behavior:
            # Sort by priority (LOW first), then by current_relaxations (least relaxed first)
            working_constraints = sorted(
                working_constraints,
                key=lambda rc: (rc.priority, -rc.current_relaxations),
                reverse=True,  # LOW first, least relaxed first within priority
            )

            # Extract plain constraints from wrappers
            plain_constraints = [rc.constraint for rc in working_constraints]

            # Try optimization
            w, problem = self._build_min_variance_problem(
                sigma, n, w_current, plain_constraints, context
            )
            solver_status = self._solve_with_fallback(problem)

            if problem.status in [cp.OPTIMAL, cp.OPTIMAL_INACCURATE]:
                elapsed_ms = int((time.perf_counter() - start_time) * 1000)
                result = self._build_result(
                    w=w,
                    permnos=permnos,
                    sigma=sigma,
                    w_current=w_current,
                    objective="min_variance",
                    problem=problem,
                    solver_status=solver_status,
                    solver_time_ms=elapsed_ms,
                    expected_returns=None,
                )
                if total_relaxations > 0:
                    logger.info(
                        f"Optimization succeeded after {total_relaxations} constraint relaxations"
                    )
                return result

            # Infeasible - try to relax a constraint
            if iteration >= max_total:
                break

            # Find next constraint to relax (by priority, LOW first, least relaxed first)
            # Each constraint stores its own initial_constraint, so no lookup needed
            relaxed = False
            for i, rc in enumerate(working_constraints):
                new_rc, was_relaxed = self._relax_constraint(rc)
                if was_relaxed:
                    working_constraints[i] = new_rc
                    total_relaxations += 1
                    relaxed = True
                    break

            if not relaxed:
                # No constraints can be relaxed further
                logger.warning("All relaxable constraints exhausted, optimization still infeasible")
                break

        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        return self._infeasible_result(
            f"Infeasible after {total_relaxations} relaxation attempts",
            objective="min_variance",
            permnos=permnos,
            solver_time_ms=elapsed_ms,
        )
