"""Monte Carlo simulation utilities for backtest robustness analysis.

Implements bootstrap (with replacement) and shuffle (permutation) simulations
over daily portfolio returns. Outputs confidence intervals for key metrics and
full distributions for visualization and p-value assessment.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

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


@dataclass
class ConfidenceInterval:
    """Confidence interval for a metric."""

    metric_name: str
    observed: float
    lower_5: float
    median: float
    upper_95: float

    @property
    def is_significant(self) -> bool:
        """True if observed value is above median of simulations (basic check)."""
        return self.observed > self.median


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

        def sampler(arr: NDArray[np.floating[Any]]) -> NDArray[np.floating[Any]]:
            return self.rng.choice(arr, size=arr.shape[0], replace=True)

        return self._run(result, sampler)

    def run_shuffle(self, result: BacktestResult) -> MonteCarloResult:
        """Permutation test - shuffle returns (without replacement)."""

        def sampler(arr: NDArray[np.floating[Any]]) -> NDArray[np.floating[Any]]:
            return self.rng.permutation(arr)

        return self._run(result, sampler)

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

        sharpe_dist = np.empty(self.config.n_simulations)
        mdd_dist = np.empty(self.config.n_simulations)
        hit_rate_dist = np.empty(self.config.n_simulations)
        mean_ic_dist = np.empty(self.config.n_simulations)

        for i in range(self.config.n_simulations):
            resampled_returns = resample_fn(returns)
            resampled_ic = resample_fn(ic_series) if ic_series.size else ic_series

            sharpe_dist[i] = self._compute_sharpe(resampled_returns)
            mdd_dist[i] = self._compute_max_drawdown(resampled_returns)
            hit_rate_dist[i] = self._compute_hit_rate(resampled_returns)
            mean_ic_dist[i] = float(np.nanmean(resampled_ic)) if resampled_ic.size else math.nan

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

        p_value_sharpe = float(np.mean(sharpe_dist >= observed_sharpe))

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
                lower_5=math.nan,
                median=math.nan,
                upper_95=math.nan,
            )
        lower, median, upper = np.percentile(valid, [5, 50, 95])
        return ConfidenceInterval(
            metric_name=metric_name,
            observed=observed,
            lower_5=float(lower),
            median=float(median),
            upper_95=float(upper),
        )


__all__ = [
    "MonteCarloConfig",
    "ConfidenceInterval",
    "MonteCarloResult",
    "MonteCarloSimulator",
]
