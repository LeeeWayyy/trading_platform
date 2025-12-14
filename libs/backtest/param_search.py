"""Parameter search utilities for PIT backtesting.

Provides exhaustive grid search and deterministic random search helpers built
on ``PITBacktester``. Random search sampling is reproducible via an explicit
``seed`` and uses lazy parameter generation to avoid memory issues with large
search spaces. Supported optimization metrics are ``mean_ic``, ``icir``, and
``hit_rate``.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from itertools import product
from typing import Any

from libs.alpha.alpha_definition import AlphaDefinition
from libs.alpha.research_platform import BacktestResult, PITBacktester


@dataclass
class SearchResult:
    best_params: dict[str, Any]
    best_score: float
    all_results: list[dict[str, Any]]  # Each dict has 'params' and 'score' keys


def _extract_metric(result: BacktestResult, metric: str) -> float:
    """Extract specified metric from BacktestResult.

    Uses getattr to safely access attributes, handling cases where
    the result object may not have all expected attributes (e.g., mocks).
    """
    supported_metrics = {"mean_ic", "icir", "hit_rate"}

    if metric not in supported_metrics:
        raise ValueError(f"Unsupported metric: {metric}")

    value = getattr(result, metric, None)
    return float("nan") if value is None else float(value)


def _perform_search(
    params_iter: Iterator[dict[str, Any]],
    alpha_factory: Callable[..., AlphaDefinition],
    backtester: PITBacktester,
    start_date: date,
    end_date: date,
    snapshot_id: str | None,
    metric: str,
) -> SearchResult:
    """Core search logic shared by grid_search and random_search.

    Args:
        params_iter: Iterator of parameter dictionaries to evaluate
        alpha_factory: Callable that creates AlphaDefinition from params
        backtester: PITBacktester instance
        start_date: Backtest start date
        end_date: Backtest end date
        snapshot_id: Optional snapshot ID for PIT determinism
        metric: Metric to optimize ('mean_ic', 'icir', 'hit_rate')

    Returns:
        SearchResult with best params and all results
    """
    all_results: list[dict[str, Any]] = []
    best_params: dict[str, Any] | None = None
    best_score: float = float("nan")
    best_comparable = -math.inf

    for params in params_iter:
        alpha = alpha_factory(**params)
        result = backtester.run_backtest(
            alpha=alpha,
            start_date=start_date,
            end_date=end_date,
            snapshot_id=snapshot_id,
        )

        score = _extract_metric(result, metric)
        comparable = -math.inf if math.isnan(score) else score

        all_results.append({"params": params, "score": score})

        if best_params is None or comparable > best_comparable:
            best_params = params
            best_score = score
            best_comparable = comparable

    if best_params is None:
        raise ValueError("No parameter combinations to evaluate")

    return SearchResult(best_params=best_params, best_score=best_score, all_results=all_results)


def _grid_params_iterator(param_grid: dict[str, list[Any]]) -> Iterator[dict[str, Any]]:
    """Generate parameter dictionaries from a grid (cartesian product)."""
    if not param_grid:
        yield {}
        return

    keys = list(param_grid.keys())
    for vals in product(*param_grid.values()):
        yield dict(zip(keys, vals, strict=True))


def _lazy_random_params_iterator(
    param_distributions: dict[str, list[Any]],
    indices: list[int],
) -> Iterator[dict[str, Any]]:
    """Lazily generate parameter dicts for given indices without full materialization.

    Uses index decomposition to compute parameters directly from flat indices,
    matching itertools.product ordering (rightmost key varies fastest).

    For a grid with dimensions [d1, d2, ..., dn], index i maps to:
        in = i % dn                     (last/rightmost key)
        i(n-1) = (i // dn) % d(n-1)
        ...
        i1 = (i // (dn * ... * d2)) % d1  (first/leftmost key)
    """
    if not param_distributions:
        for _ in indices:
            yield {}
        return

    keys = list(param_distributions.keys())
    value_lists = [param_distributions[k] for k in keys]
    sizes = [len(v) for v in value_lists]

    for idx in indices:
        params = {}
        remaining = idx
        # Iterate in reverse order to match itertools.product ordering
        # (rightmost key varies fastest)
        for i in range(len(keys) - 1, -1, -1):
            params[keys[i]] = value_lists[i][remaining % sizes[i]]
            remaining //= sizes[i]
        yield params


def grid_search(
    alpha_factory: Callable[..., AlphaDefinition],
    param_grid: dict[str, list[Any]],
    backtester: PITBacktester,
    start_date: date,
    end_date: date,
    snapshot_id: str | None = None,
    metric: str = "mean_ic",
) -> SearchResult:
    """Exhaustive grid search over parameter combinations.

    Args:
        alpha_factory: Callable that creates AlphaDefinition from params
        param_grid: Dict mapping param names to lists of values
        backtester: PITBacktester instance
        start_date: Backtest start date
        end_date: Backtest end date
        snapshot_id: Optional snapshot ID for PIT determinism
        metric: Metric to optimize ('mean_ic', 'icir', 'hit_rate')

    Returns:
        SearchResult with best params and all results
    """
    if param_grid and any(len(values) == 0 for values in param_grid.values()):
        raise ValueError("param_grid contains empty value list")

    return _perform_search(
        params_iter=_grid_params_iterator(param_grid),
        alpha_factory=alpha_factory,
        backtester=backtester,
        start_date=start_date,
        end_date=end_date,
        snapshot_id=snapshot_id,
        metric=metric,
    )


def random_search(
    alpha_factory: Callable[..., AlphaDefinition],
    param_distributions: dict[str, list[Any]],
    backtester: PITBacktester,
    start_date: date,
    end_date: date,
    n_iter: int = 10,
    snapshot_id: str | None = None,
    metric: str = "mean_ic",
    seed: int | None = None,
) -> SearchResult:
    """Random search with deterministic sampling and lazy parameter generation.

    Memory-efficient: does not materialize the full cartesian product. Parameters
    are computed on-the-fly from sampled indices using index decomposition.

    Sampling policy:
    - Uses explicit Random(seed) instance for reproducibility
    - Without replacement if n_iter <= total combinations
    - With replacement if n_iter > total combinations
    """
    if n_iter <= 0:
        raise ValueError("n_iter must be positive")

    if param_distributions and any(len(values) == 0 for values in param_distributions.values()):
        raise ValueError("param_distributions contains empty value list")

    rng = random.Random(seed)

    # Calculate total combinations without materializing them
    total_combos = math.prod(len(v) for v in param_distributions.values()) if param_distributions else 1

    # Note: total_combos is guaranteed > 0 here because:
    # - If param_distributions is empty → total_combos = 1
    # - If not empty, the check on line 206 ensures no empty lists → product > 0

    # Sample indices
    if n_iter <= total_combos:
        # Without replacement when n_iter <= total combinations
        indices = rng.sample(range(total_combos), k=n_iter)
    else:
        # With replacement when n_iter > total combinations
        indices = rng.choices(range(total_combos), k=n_iter)

    # Use lazy iterator to generate params from indices
    return _perform_search(
        params_iter=_lazy_random_params_iterator(param_distributions, indices),
        alpha_factory=alpha_factory,
        backtester=backtester,
        start_date=start_date,
        end_date=end_date,
        snapshot_id=snapshot_id,
        metric=metric,
    )


__all__ = ["SearchResult", "grid_search", "random_search"]
