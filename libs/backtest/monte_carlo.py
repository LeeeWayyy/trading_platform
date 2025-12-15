"""Monte Carlo simulation utilities for backtest robustness analysis.

Implements bootstrap (with replacement) and shuffle (permutation) simulations
over daily portfolio returns. Outputs confidence intervals for key metrics and
full distributions for visualization and p-value assessment.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

import numpy as np
import structlog
from numpy.typing import NDArray

if TYPE_CHECKING:  # pragma: no cover
    from libs.alpha.research_platform import BacktestResult

logger = structlog.get_logger(__name__)


@dataclass
class MonteCarloConfig:
    """Configuration for Monte Carlo simulation."""

    n_simulations: int = 1000
    method: Literal["bootstrap", "shuffle"] = "bootstrap"
    random_seed: int | None = None  # None = warn about non-reproducibility
    confidence_levels: tuple[float, ...] = (0.05, 0.5, 0.95)


@dataclass
class ConfidenceInterval:
    """Confidence interval for a metric."""

    metric_name: str
    observed: float
    quantiles: dict[float, float]

    @property
    def is_significant(self) -> bool:
        """True if observed value is above median of simulations (basic check)."""
        median = self.quantiles.get(0.5, math.nan)
        if math.isnan(median):
            return False
        return self.observed > median

    @property
    def lower_5(self) -> float:
        return self.quantiles.get(0.05, math.nan)

    @property
    def median(self) -> float:
        return self.quantiles.get(0.5, math.nan)

    @property
    def upper_95(self) -> float:
        return self.quantiles.get(0.95, math.nan)


@dataclass
class MonteCarloResult:
    """Complete Monte Carlo simulation result."""

    config: MonteCarloConfig
    n_simulations: int

    sharpe_ci: ConfidenceInterval
    max_drawdown_ci: ConfidenceInterval
    mean_ic_ci: ConfidenceInterval
    hit_rate_ci: ConfidenceInterval

    sharpe_distribution: NDArray[np.floating[Any]]
    max_drawdown_distribution: NDArray[np.floating[Any]]
    mean_ic_distribution: NDArray[np.floating[Any]]
    hit_rate_distribution: NDArray[np.floating[Any]]

    p_value_sharpe: float  # One-sided: P(simulated >= observed)


class MonteCarloSimulator:
    """Monte Carlo simulation for backtest robustness analysis."""

    def __init__(self, config: MonteCarloConfig):
        self.config = config
        self.rng = np.random.default_rng(config.random_seed)
        self.logger = logger
        if config.random_seed is None:
            self.logger.warning(
                "monte_carlo_unseeded",
                message="Monte Carlo running without fixed random_seed; results are non-reproducible",
            )

    # ------------------------------------------------------------------ public
    def run(self, result: BacktestResult) -> MonteCarloResult:
        """Run Monte Carlo simulation using method from config."""
        if self.config.method == "bootstrap":
            return self.run_bootstrap(result)
        elif self.config.method == "shuffle":
            return self.run_shuffle(result)
        else:
            raise ValueError(f"Unknown method: {self.config.method}")

    def run_bootstrap(self, result: BacktestResult) -> MonteCarloResult:
        """Bootstrap resampling of daily returns (with replacement)."""
        return self._run(result, self._bootstrap_resample)

    def run_shuffle(self, result: BacktestResult) -> MonteCarloResult:
        """Permutation test - shuffle returns (without replacement)."""
        return self._run(result, self._shuffle_resample)

    # ----------------------------------------------------------------- helpers
    def _run(
        self,
        result: BacktestResult,
        resample_fn: Callable[[NDArray[np.floating[Any]]], NDArray[np.floating[Any]]],
    ) -> MonteCarloResult:
        returns = self._extract_daily_returns(result)
        ic_series = self._extract_daily_ic(result)

        observed_sharpe = self._compute_sharpe(returns)
        observed_mdd = self._compute_max_drawdown(returns)
        observed_hit = self._compute_hit_rate(returns)
        observed_mean_ic = float(np.nanmean(ic_series)) if ic_series.size else math.nan

        resampled_returns = resample_fn(returns)
        resampled_ic = resample_fn(ic_series) if ic_series.size else np.empty((0, 0))

        sharpe_dist = self._compute_sharpe_vectorized(resampled_returns)
        mdd_dist = self._compute_max_drawdown_vectorized(resampled_returns)
        hit_rate_dist = self._compute_hit_rate_vectorized(resampled_returns)
        if ic_series.size:
            mean_ic_dist = np.nanmean(resampled_ic, axis=1)
        else:
            mean_ic_dist = np.full(self.config.n_simulations, math.nan)

        sharpe_ci = self._compute_confidence_interval(
            observed=observed_sharpe, simulated=sharpe_dist, metric_name="sharpe"
        )
        mdd_ci = self._compute_confidence_interval(
            observed=observed_mdd, simulated=mdd_dist, metric_name="max_drawdown"
        )
        mean_ic_ci = self._compute_confidence_interval(
            observed=observed_mean_ic, simulated=mean_ic_dist, metric_name="mean_ic"
        )
        hit_rate_ci = self._compute_confidence_interval(
            observed=observed_hit, simulated=hit_rate_dist, metric_name="hit_rate"
        )

        p_value_sharpe = (
            math.nan if math.isnan(observed_sharpe) else float(np.mean(sharpe_dist >= observed_sharpe))
        )

        return MonteCarloResult(
            config=self.config,
            n_simulations=self.config.n_simulations,
            sharpe_ci=sharpe_ci,
            max_drawdown_ci=mdd_ci,
            mean_ic_ci=mean_ic_ci,
            hit_rate_ci=hit_rate_ci,
            sharpe_distribution=sharpe_dist,
            max_drawdown_distribution=mdd_dist,
            mean_ic_distribution=mean_ic_dist,
            hit_rate_distribution=hit_rate_dist,
            p_value_sharpe=p_value_sharpe,
        )

    def _extract_daily_returns(self, result: BacktestResult) -> NDArray[np.floating[Any]]:
        """Extract portfolio returns from BacktestResult.daily_portfolio_returns.

        Returns are sorted by date to ensure path-dependent metrics (max drawdown)
        are computed correctly.
        """
        df = result.daily_portfolio_returns.sort("date")
        returns: NDArray[np.floating[Any]] = df.get_column("return").to_numpy()
        returns = returns[~np.isnan(returns)]
        if returns.size == 0:
            raise ValueError("daily_portfolio_returns is empty; cannot run Monte Carlo")
        return returns.astype(np.float64)

    def _extract_daily_ic(self, result: BacktestResult) -> NDArray[np.floating[Any]]:
        """Extract rank IC values from BacktestResult.daily_ic."""
        if "rank_ic" not in result.daily_ic.columns:
            return np.array([], dtype=np.float64)
        ic: NDArray[np.floating[Any]] = result.daily_ic.get_column("rank_ic").to_numpy()
        filtered: NDArray[np.floating[Any]] = ic[~np.isnan(ic)].astype(np.float64)
        return filtered

    def _compute_sharpe(self, returns: NDArray[np.floating[Any]]) -> float:
        """Annualized Sharpe: sqrt(252) * mean / std, risk-free=0."""
        if returns.size < 2:
            return math.nan
        std = returns.std(ddof=1)
        if std == 0:
            # Zero volatility: return inf for positive returns, -inf for negative, nan for zero
            mean_ret = returns.mean()
            if mean_ret > 0:
                return math.inf
            elif mean_ret < 0:
                return -math.inf
            else:
                return math.nan
        return float(np.sqrt(252.0) * returns.mean() / std)

    def _compute_max_drawdown(self, returns: NDArray[np.floating[Any]]) -> float:
        """Max drawdown using geometric compounding."""
        if returns.size == 0:
            return math.nan
        cum = np.empty(returns.size + 1)
        cum[0] = 1.0
        cum[1:] = np.cumprod(1 + returns)
        peaks = np.maximum.accumulate(cum)
        drawdowns = (cum - peaks) / peaks
        return float(drawdowns.min())

    def _compute_hit_rate(self, returns: NDArray[np.floating[Any]]) -> float:
        """Portfolio hit rate: fraction of days with return >= 0."""
        if returns.size == 0:
            return math.nan
        return float(np.mean(returns >= 0))

    def _compute_confidence_interval(
        self, observed: float, simulated: NDArray[np.floating[Any]], metric_name: str
    ) -> ConfidenceInterval:
        """Compute CI from simulated distribution (5th, 50th, 95th percentiles)."""
        # Filter out NaNs to avoid RuntimeWarning: All-NaN slice encountered
        valid = simulated[~np.isnan(simulated)]
        if valid.size == 0:
            return ConfidenceInterval(
                metric_name=metric_name,
                observed=observed,
                quantiles={},
            )
        percentiles = np.array(self.config.confidence_levels) * 100.0
        if np.any((percentiles < 0) | (percentiles > 100)):
            raise ValueError("confidence_levels must be between 0 and 1")
        quantile_values = np.percentile(valid, percentiles)
        quantiles = {
            float(level): float(value) for level, value in zip(self.config.confidence_levels, quantile_values, strict=True)
        }
        return ConfidenceInterval(
            metric_name=metric_name,
            observed=observed,
            quantiles=quantiles,
        )

    # ----------------------------- vectorized resampling and metrics ----------
    def _bootstrap_resample(self, arr: NDArray[np.floating[Any]]) -> NDArray[np.floating[Any]]:
        """Vectorized bootstrap sampling."""
        n_obs = arr.shape[0]
        indices = self.rng.integers(0, n_obs, size=(self.config.n_simulations, n_obs), dtype=np.int64)
        return cast(NDArray[np.float64], arr[indices].astype(np.float64, copy=False))

    def _shuffle_resample(self, arr: NDArray[np.floating[Any]]) -> NDArray[np.floating[Any]]:
        """Generate shuffled samples (permutation) for each simulation."""
        n_obs = arr.shape[0]
        if n_obs == 0:
            return np.empty((self.config.n_simulations, 0), dtype=np.float64)
        arr = arr.astype(np.float64, copy=False)
        arr_2d = np.broadcast_to(arr, (self.config.n_simulations, n_obs))
        return cast(NDArray[np.float64], self.rng.permuted(arr_2d, axis=1))

    def _compute_sharpe_vectorized(self, returns: NDArray[np.floating[Any]]) -> NDArray[np.floating[Any]]:
        """Vectorized Sharpe computation for simulated paths."""
        means = returns.mean(axis=1)
        stds = returns.std(axis=1, ddof=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            sharpe = np.sqrt(252.0) * means / stds
        return cast(NDArray[np.float64], sharpe)

    def _compute_max_drawdown_vectorized(self, returns: NDArray[np.floating[Any]]) -> NDArray[np.floating[Any]]:
        """Vectorized max drawdown using geometric compounding."""
        if returns.size == 0:
            return np.full(self.config.n_simulations, math.nan)
        cum = np.cumprod(1 + returns, axis=1)
        # prepend 1 for each simulation
        cum = np.concatenate([np.ones((returns.shape[0], 1)), cum], axis=1)
        peaks = np.maximum.accumulate(cum, axis=1)
        drawdowns = (cum - peaks) / peaks
        return cast(NDArray[np.float64], drawdowns.min(axis=1))

    def _compute_hit_rate_vectorized(self, returns: NDArray[np.floating[Any]]) -> NDArray[np.floating[Any]]:
        if returns.size == 0:
            return np.full(self.config.n_simulations, math.nan)
        return cast(NDArray[np.float64], np.mean(returns >= 0, axis=1))


__all__ = [
    "MonteCarloConfig",
    "ConfidenceInterval",
    "MonteCarloResult",
    "MonteCarloSimulator",
]
