"""Walk-forward optimization built on the PIT backtester.

The optimizer repeatedly trains (parameter search) on a rolling train window
and evaluates the best configuration on the subsequent disjoint test window.
Test windows are **non-overlapping** (`step_months >= test_months`), while
train windows may overlap when `step_months < train_months`—this is allowed
and a warning is emitted to make the overlap explicit. A single snapshot_id is
locked for all windows to preserve point-in-time determinism across the full
optimization run.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import product
from typing import Any

import structlog
from dateutil.relativedelta import relativedelta  # type: ignore[import-untyped]

from libs.alpha.alpha_definition import AlphaDefinition
from libs.alpha.research_platform import BacktestResult, PITBacktester

logger = structlog.get_logger(__name__)


@dataclass
class WalkForwardConfig:
    train_months: int = 12
    test_months: int = 3
    step_months: int = 3  # Must be >= test_months
    min_train_samples: int = 252  # Trading days


@dataclass
class WindowResult:
    window_id: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    best_params: dict[str, Any]
    train_ic: float
    test_ic: float
    test_icir: float


@dataclass
class WalkForwardResult:
    windows: list[WindowResult]
    aggregated_test_ic: float
    aggregated_test_icir: float
    overfitting_ratio: float

    @property
    def is_overfit(self) -> bool:
        return self.overfitting_ratio > 2.0


class WalkForwardOptimizer:
    """Walk-forward optimization framework using PITBacktester.

    This class performs parameter search per rolling train window and evaluates
    the selected parameters on the following test window. It ensures all
    backtests share the same snapshot for PIT reproducibility.
    """

    def __init__(self, backtester: PITBacktester, config: WalkForwardConfig):
        self.backtester = backtester
        self.config = config
        self.logger = logger

    def generate_windows(self, start_date: date, end_date: date) -> list[tuple[date, date, date, date]]:
        """Generate (train_start, train_end, test_start, test_end) tuples.

        Raises ValueError if step_months < test_months to prevent overlapping
        evaluation periods. Emits a structlog warning when train windows will
        overlap (step_months < train_months).
        """

        if self.config.step_months < self.config.test_months:
            raise ValueError(
                "step_months must be >= test_months to prevent overlapping test windows"
            )

        if self.config.step_months < self.config.train_months:
            self.logger.warning(
                "walk_forward_train_overlap",
                step_months=self.config.step_months,
                train_months=self.config.train_months,
                overlap_months=self.config.train_months - self.config.step_months,
                message=(
                    "Train windows will overlap; test windows remain disjoint. "
                    "This is expected for rolling optimization but called out for operators."
                ),
            )

        windows: list[tuple[date, date, date, date]] = []
        cursor = start_date

        while True:
            train_start = cursor
            train_end = (train_start + relativedelta(months=self.config.train_months)) - timedelta(days=1)
            test_start = train_end + timedelta(days=1)
            test_end = (test_start + relativedelta(months=self.config.test_months)) - timedelta(days=1)

            if test_end > end_date:
                break

            if (train_end - train_start).days + 1 < self.config.min_train_samples:
                raise ValueError("train window shorter than min_train_samples")

            windows.append((train_start, train_end, test_start, test_end))
            cursor = cursor + relativedelta(months=self.config.step_months)

        return windows

    def optimize_window(
        self,
        alpha_factory: Callable[..., AlphaDefinition],
        param_grid: dict[str, list[Any]],
        train_start: date,
        train_end: date,
        snapshot_id: str | None = None,
    ) -> tuple[dict[str, Any], float]:
        """Find best params on the training window.

        Returns a tuple of (best_params, train_ic). All backtests use the
        provided snapshot_id to keep PIT determinism.

        Raises:
            ValueError: If all parameter combinations produce NaN/None scores.
        """

        # Create parameter combinations (cartesian product)
        if param_grid:
            keys = list(param_grid.keys())
            values_product = list(product(*param_grid.values()))
            combos = [dict(zip(keys, vals, strict=False)) for vals in values_product]
        else:
            combos = [{}]

        best_params: dict[str, Any] | None = None
        best_ic = -math.inf
        any_valid_score = False

        for params in combos:
            alpha = alpha_factory(**params)
            result: BacktestResult = self.backtester.run_backtest(
                alpha=alpha,
                start_date=train_start,
                end_date=train_end,
                snapshot_id=snapshot_id,
            )

            ic_value = result.mean_ic
            if ic_value is not None and not math.isnan(ic_value):
                any_valid_score = True
                if ic_value > best_ic:
                    best_ic = ic_value
                    best_params = params
            elif best_params is None:
                # Track first params as fallback, but don't mark as valid
                best_params = params

        if not any_valid_score:
            raise ValueError(
                "All parameter combinations produced NaN/None scores - "
                "optimization cannot proceed with meaningful results"
            )

        # At this point, any_valid_score is True, meaning best_params was set
        assert best_params is not None
        return best_params, best_ic

    def run(
        self,
        alpha_factory: Callable[..., AlphaDefinition],
        param_grid: dict[str, list[Any]],
        start_date: date,
        end_date: date,
        snapshot_id: str | None = None,
    ) -> WalkForwardResult:
        """Run the complete walk-forward optimization."""

        windows = self.generate_windows(start_date, end_date)
        if not windows:
            raise ValueError("No windows generated for given date range")

        # Lock snapshot once for PIT determinism across all windows
        locked_snapshot = self.backtester._lock_snapshot(snapshot_id)
        snapshot_id_locked = locked_snapshot.version_tag

        results: list[WindowResult] = []

        for idx, (train_start, train_end, test_start, test_end) in enumerate(windows):
            best_params, train_ic = self.optimize_window(
                alpha_factory,
                param_grid,
                train_start,
                train_end,
                snapshot_id=snapshot_id_locked,
            )

            alpha = alpha_factory(**best_params)
            test_result = self.backtester.run_backtest(
                alpha=alpha,
                start_date=test_start,
                end_date=test_end,
                snapshot_id=snapshot_id_locked,
            )

            results.append(
                WindowResult(
                    window_id=idx,
                    train_start=train_start,
                    train_end=train_end,
                    test_start=test_start,
                    test_end=test_end,
                    best_params=best_params,
                    train_ic=train_ic,
                    test_ic=test_result.mean_ic,
                    test_icir=test_result.icir,
                )
            )

        return self._aggregate_results(results)

    def _aggregate_results(self, results: list[WindowResult]) -> WalkForwardResult:
        """Aggregate per-window metrics into summary statistics.

        Filters out NaN values before aggregation to avoid silent NaN propagation.
        Logs warnings for windows with NaN test ICs.
        """

        if not results:
            raise ValueError("No window results to aggregate")

        # Filter out NaN values and track which windows had issues
        train_ics = [w.train_ic for w in results if not math.isnan(w.train_ic)]
        test_ics = [w.test_ic for w in results if not math.isnan(w.test_ic)]

        nan_windows = [w.window_id for w in results if math.isnan(w.test_ic)]
        if nan_windows:
            self.logger.warning(
                "walk_forward_nan_windows",
                nan_window_ids=nan_windows,
                total_windows=len(results),
                message="Some windows produced NaN test ICs and were excluded from aggregation",
            )

        if not test_ics:
            # All windows produced NaN - return NaN aggregates
            return WalkForwardResult(
                windows=results,
                aggregated_test_ic=float("nan"),
                aggregated_test_icir=float("nan"),
                overfitting_ratio=float("nan"),
            )

        aggregated_test_ic = statistics.fmean(test_ics)

        if len(test_ics) < 2:
            aggregated_test_icir = float("nan")
        else:
            std_ic = statistics.pstdev(test_ics)
            aggregated_test_icir = (
                float("nan")
                if std_ic == 0
                else aggregated_test_ic / std_ic
            )

        if train_ics:
            mean_train_ic = statistics.fmean(train_ics)
        else:
            mean_train_ic = float("nan")

        if aggregated_test_ic == 0 or math.isnan(aggregated_test_ic):
            overfitting_ratio = float("nan")
        else:
            overfitting_ratio = mean_train_ic / aggregated_test_ic

        return WalkForwardResult(
            windows=results,
            aggregated_test_ic=aggregated_test_ic,
            aggregated_test_icir=aggregated_test_icir,
            overfitting_ratio=overfitting_ratio,
        )


__all__ = [
    "WalkForwardConfig",
    "WindowResult",
    "WalkForwardResult",
    "WalkForwardOptimizer",
]

