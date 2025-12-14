from __future__ import annotations

import math
import importlib.util
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_missing = [mod for mod in ("structlog",) if importlib.util.find_spec(mod) is None]
if _missing:
    pytest.skip(f"Skipping walk-forward tests because dependencies are missing: {', '.join(_missing)}", allow_module_level=True)

import structlog
from structlog.stdlib import LoggerFactory

from libs.backtest.walk_forward import (
    WalkForwardConfig,
    WalkForwardOptimizer,
    WalkForwardResult,
    WindowResult,
)


@pytest.fixture(autouse=True)
def _configure_structlog():
    """Route structlog to stdlib so caplog can capture warnings."""
    structlog.configure(logger_factory=LoggerFactory())
    yield
    structlog.reset_defaults()


@pytest.fixture
def backtester():
    bt = MagicMock()
    bt._lock_snapshot.return_value = SimpleNamespace(version_tag="locked-snap")
    return bt


@pytest.fixture
def alpha_factory():
    return lambda **kwargs: SimpleNamespace(params=kwargs)


def _make_result(mean_ic: float | None, icir: float | None = None):
    return SimpleNamespace(mean_ic=mean_ic, icir=icir)


def test_walk_forward_config_defaults_and_custom():
    default_cfg = WalkForwardConfig()
    assert default_cfg.train_months == 12
    assert default_cfg.test_months == 3
    assert default_cfg.step_months == 3
    assert default_cfg.min_train_samples == 252

    custom = WalkForwardConfig(train_months=6, test_months=2, step_months=4, min_train_samples=10)
    assert custom.train_months == 6
    assert custom.test_months == 2
    assert custom.step_months == 4
    assert custom.min_train_samples == 10


def test_generate_windows_normal_and_disjoint(caplog, backtester):
    config = WalkForwardConfig(train_months=6, test_months=3, step_months=3, min_train_samples=10)
    optimizer = WalkForwardOptimizer(backtester, config)

    start = date(2024, 1, 1)
    end = date(2025, 6, 30)
    windows = optimizer.generate_windows(start, end)

    # Expect at least three windows and disjoint test windows
    assert len(windows) >= 3
    last_test_end = None
    for _, _, test_start, test_end in windows:
        if last_test_end:
            assert test_start > last_test_end
        last_test_end = test_end


def test_generate_windows_step_lt_test_raises(backtester):
    cfg = WalkForwardConfig(train_months=6, test_months=3, step_months=2, min_train_samples=10)
    optimizer = WalkForwardOptimizer(backtester, cfg)
    with pytest.raises(ValueError):
        optimizer.generate_windows(date(2024, 1, 1), date(2024, 12, 31))


def test_generate_windows_overlap_warning(caplog, backtester):
    cfg = WalkForwardConfig(train_months=12, test_months=3, step_months=6, min_train_samples=10)
    optimizer = WalkForwardOptimizer(backtester, cfg)

    with caplog.at_level("WARNING"):
        optimizer.generate_windows(date(2024, 1, 1), date(2025, 1, 31))

    assert any("walk_forward_train_overlap" in rec.message for rec in caplog.records)


def test_generate_windows_min_train_samples_validation(backtester):
    cfg = WalkForwardConfig(train_months=1, test_months=1, step_months=1, min_train_samples=400)
    optimizer = WalkForwardOptimizer(backtester, cfg)
    with pytest.raises(ValueError):
        optimizer.generate_windows(date(2024, 1, 1), date(2024, 5, 1))


def test_generate_windows_too_short_range(backtester):
    cfg = WalkForwardConfig(train_months=6, test_months=3, step_months=3, min_train_samples=1)
    optimizer = WalkForwardOptimizer(backtester, cfg)
    windows = optimizer.generate_windows(date(2024, 1, 1), date(2024, 1, 31))
    assert windows == []


def test_optimize_window_selects_best_params(backtester, alpha_factory):
    cfg = WalkForwardConfig(train_months=3, test_months=1, step_months=1, min_train_samples=1)
    optimizer = WalkForwardOptimizer(backtester, cfg)

    backtester.run_backtest.side_effect = [
        _make_result(0.1, 0.2),
        _make_result(float("nan"), 0.0),
        _make_result(0.3, 0.5),
    ]

    best_params, best_ic = optimizer.optimize_window(
        alpha_factory=alpha_factory,
        param_grid={"p": [1, 2, 3]},
        train_start=date(2024, 1, 1),
        train_end=date(2024, 1, 31),
        snapshot_id="locked-snap",
    )

    assert best_params == {"p": 3}
    assert best_ic == 0.3
    assert all(call.kwargs["snapshot_id"] == "locked-snap" for call in backtester.run_backtest.call_args_list)


def test_run_propagates_snapshot_and_aggregates(backtester, alpha_factory, monkeypatch):
    cfg = WalkForwardConfig(train_months=6, test_months=3, step_months=6, min_train_samples=10)
    optimizer = WalkForwardOptimizer(backtester, cfg)

    windows = [
        (date(2024, 1, 1), date(2024, 6, 30), date(2024, 7, 1), date(2024, 9, 30)),
        (date(2024, 7, 1), date(2024, 12, 31), date(2025, 1, 1), date(2025, 3, 31)),
    ]
    monkeypatch.setattr(optimizer, "generate_windows", MagicMock(return_value=windows))

    backtester.run_backtest.side_effect = [
        _make_result(0.2, 0.4),  # train window 1
        _make_result(0.1, 0.2),  # test window 1
        _make_result(0.3, 0.6),  # train window 2
        _make_result(0.05, 0.1),  # test window 2
    ]

    result = optimizer.run(
        alpha_factory=alpha_factory,
        param_grid={},
        start_date=date(2024, 1, 1),
        end_date=date(2025, 3, 31),
        snapshot_id="user-snap",
    )

    assert isinstance(result, WalkForwardResult)
    assert len(result.windows) == 2
    assert all(call.kwargs["snapshot_id"] == "locked-snap" for call in backtester.run_backtest.call_args_list)
    assert pytest.approx(result.aggregated_test_ic) == 0.075
    assert not math.isnan(result.aggregated_test_icir)


def test_aggregate_results_multiple_and_single_window(backtester):
    cfg = WalkForwardConfig()
    optimizer = WalkForwardOptimizer(backtester, cfg)

    windows = [
        WindowResult(
            window_id=0,
            train_start=date(2024, 1, 1),
            train_end=date(2024, 6, 30),
            test_start=date(2024, 7, 1),
            test_end=date(2024, 9, 30),
            best_params={"p": 1},
            train_ic=0.2,
            test_ic=0.1,
            test_icir=0.5,
        ),
        WindowResult(
            window_id=1,
            train_start=date(2024, 4, 1),
            train_end=date(2024, 9, 30),
            test_start=date(2024, 10, 1),
            test_end=date(2024, 12, 31),
            best_params={"p": 2},
            train_ic=0.3,
            test_ic=0.2,
            test_icir=0.4,
        ),
    ]

    aggregated = optimizer._aggregate_results(windows)
    assert aggregated.aggregated_test_ic == pytest.approx(0.15)
    assert aggregated.aggregated_test_icir > 0
    assert aggregated.overfitting_ratio == pytest.approx((0.25) / 0.15)

    single = optimizer._aggregate_results([windows[0]])
    assert math.isnan(single.aggregated_test_icir)


def test_overfitting_ratio_zero_or_nan(backtester):
    cfg = WalkForwardConfig()
    optimizer = WalkForwardOptimizer(backtester, cfg)
    windows = [
        WindowResult(
            window_id=0,
            train_start=date(2024, 1, 1),
            train_end=date(2024, 3, 31),
            test_start=date(2024, 4, 1),
            test_end=date(2024, 4, 30),
            best_params={},
            train_ic=0.2,
            test_ic=0.0,
            test_icir=float("nan"),
        )
    ]

    aggregated_zero = optimizer._aggregate_results(windows)
    assert math.isnan(aggregated_zero.overfitting_ratio)

    windows_nan = [replace(windows[0], test_ic=float("nan"))]
    aggregated_nan = optimizer._aggregate_results(windows_nan)
    assert math.isnan(aggregated_nan.overfitting_ratio)


def test_window_and_walk_forward_result_properties():
    windows = [
        WindowResult(
            window_id=0,
            train_start=date(2024, 1, 1),
            train_end=date(2024, 3, 31),
            test_start=date(2024, 4, 1),
            test_end=date(2024, 4, 30),
            best_params={"p": 1},
            train_ic=0.2,
            test_ic=0.05,
            test_icir=0.3,
        )
    ]
    result = WalkForwardResult(windows=windows, aggregated_test_ic=0.05, aggregated_test_icir=0.2, overfitting_ratio=2.5)

    assert result.windows[0].best_params["p"] == 1
    assert result.is_overfit is True


def test_optimize_window_all_nan_raises(backtester, alpha_factory):
    """Test that optimize_window raises ValueError when all scores are NaN."""
    cfg = WalkForwardConfig(train_months=3, test_months=1, step_months=1, min_train_samples=1)
    optimizer = WalkForwardOptimizer(backtester, cfg)

    # All backtests return NaN
    backtester.run_backtest.side_effect = [
        _make_result(float("nan"), 0.0),
        _make_result(float("nan"), 0.0),
    ]

    with pytest.raises(ValueError, match="All parameter combinations produced NaN"):
        optimizer.optimize_window(
            alpha_factory=alpha_factory,
            param_grid={"p": [1, 2]},
            train_start=date(2024, 1, 1),
            train_end=date(2024, 1, 31),
            snapshot_id="snap",
        )


def test_aggregate_results_nan_windows_warning(caplog, backtester):
    """Test that aggregation warns about NaN windows and excludes them."""
    cfg = WalkForwardConfig()
    optimizer = WalkForwardOptimizer(backtester, cfg)

    windows = [
        WindowResult(
            window_id=0,
            train_start=date(2024, 1, 1),
            train_end=date(2024, 3, 31),
            test_start=date(2024, 4, 1),
            test_end=date(2024, 4, 30),
            best_params={"p": 1},
            train_ic=0.2,
            test_ic=0.1,  # Valid
            test_icir=0.5,
        ),
        WindowResult(
            window_id=1,
            train_start=date(2024, 4, 1),
            train_end=date(2024, 6, 30),
            test_start=date(2024, 7, 1),
            test_end=date(2024, 7, 31),
            best_params={"p": 2},
            train_ic=0.3,
            test_ic=float("nan"),  # NaN - should be excluded
            test_icir=float("nan"),
        ),
    ]

    with caplog.at_level("WARNING"):
        aggregated = optimizer._aggregate_results(windows)

    # Should only use the valid window for aggregation
    assert aggregated.aggregated_test_ic == pytest.approx(0.1)
    # ICIR is NaN because only 1 valid window
    assert math.isnan(aggregated.aggregated_test_icir)
    # Warning should be logged
    assert any("walk_forward_nan_windows" in rec.message for rec in caplog.records)
