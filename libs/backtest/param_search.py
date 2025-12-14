"""Parameter search utilities for PIT backtesting.

Provides exhaustive grid search and deterministic random search helpers built
on ``PITBacktester``. Random search sampling is reproducible via an explicit
``seed`` and switches between sampling without replacement (when the requested
iterations do not exceed the total combinations) and with replacement when
additional draws are needed. Supported optimization metrics are ``mean_ic``,
``icir``, and ``hit_rate``.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
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
    """Extract specified metric from BacktestResult."""

    metric_map = {
        "mean_ic": result.mean_ic,
        "icir": result.icir,
        "hit_rate": result.hit_rate,
    }

    if metric not in metric_map:
        raise ValueError(f"Unsupported metric: {metric}")

    value = metric_map[metric]
    return float("nan") if value is None else float(value)


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

    # Build parameter combinations (cartesian product). Empty grid → single default run.
    if param_grid:
        if any(len(values) == 0 for values in param_grid.values()):
            raise ValueError("param_grid contains empty value list")

        keys = list(param_grid.keys())
        combos = [dict(zip(keys, vals, strict=False)) for vals in product(*param_grid.values())]
    else:
        combos = [{}]

    if not combos:
        raise ValueError("No parameter combinations generated")

    all_results: list[dict[str, Any]] = []
    best_params: dict[str, Any] | None = None
    best_score: float = float("nan")
    best_comparable = -math.inf

    for params in combos:
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

    assert best_params is not None  # Guaranteed by combos non-empty

    return SearchResult(best_params=best_params, best_score=best_score, all_results=all_results)


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
    """Random search with deterministic sampling.

    Sampling policy:
    - Uses explicit Random(seed) instance for reproducibility
    - Without replacement if n_iter <= total combinations
    - With replacement if n_iter > total combinations
    """

    if n_iter <= 0:
        raise ValueError("n_iter must be positive")

    rng = random.Random(seed)

    if param_distributions:
        if any(len(values) == 0 for values in param_distributions.values()):
            raise ValueError("param_distributions contains empty value list")

        keys = list(param_distributions.keys())
        value_product = list(product(*param_distributions.values()))
        total_combos = len(value_product)
        combos = [dict(zip(keys, vals, strict=False)) for vals in value_product]
    else:
        combos = [{}]
        total_combos = 1

    if total_combos == 0:
        raise ValueError("No parameter combinations generated")

    if n_iter <= total_combos:
        indices = rng.sample(range(total_combos), k=n_iter)
    else:
        indices = [rng.randrange(total_combos) for _ in range(n_iter)]

    all_results: list[dict[str, Any]] = []
    best_params: dict[str, Any] | None = None
    best_score: float = float("nan")
    best_comparable = -math.inf

    for idx in indices:
        params = combos[idx]
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

    assert best_params is not None

    return SearchResult(best_params=best_params, best_score=best_score, all_results=all_results)


__all__ = ["SearchResult", "grid_search", "random_search", "_extract_metric"]
