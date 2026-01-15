from __future__ import annotations

import math
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from libs.trading.backtest.param_search import (
    SearchResult,
    _extract_metric,
    grid_search,
    random_search,
)


def _result(mean_ic=None, icir=None, hit_rate=None):
    return SimpleNamespace(mean_ic=mean_ic, icir=icir, hit_rate=hit_rate)


@pytest.fixture()
def backtester():
    bt = MagicMock()
    return bt


@pytest.fixture()
def alpha_factory():
    return lambda **kwargs: SimpleNamespace(params=kwargs)


def test_search_result_dataclass_structure():
    res = SearchResult(
        best_params={"p": 1}, best_score=0.2, all_results=[{"params": {"p": 1}, "score": 0.2}]
    )
    assert res.best_params["p"] == 1
    assert res.best_score == 0.2
    assert res.all_results[0]["score"] == 0.2


def test_grid_search_enumerates_and_picks_best(backtester, alpha_factory):
    backtester.run_backtest.side_effect = [_result(0.1, 0.0, 0.0), _result(0.3, 0.0, 0.0)]

    res = grid_search(
        alpha_factory=alpha_factory,
        param_grid={"a": [1, 2]},
        backtester=backtester,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        snapshot_id="snap",
        metric="mean_ic",
    )

    assert res.best_params == {"a": 2}
    assert res.best_score == 0.3
    assert len(res.all_results) == 2
    assert all(
        call.kwargs["snapshot_id"] == "snap" for call in backtester.run_backtest.call_args_list
    )


def test_grid_search_empty_grid_single_run(backtester, alpha_factory):
    backtester.run_backtest.return_value = _result(0.05, 0.1, 0.2)
    res = grid_search(
        alpha_factory=alpha_factory,
        param_grid={},
        backtester=backtester,
        start_date=date(2024, 2, 1),
        end_date=date(2024, 2, 28),
    )
    assert res.best_params == {}
    assert res.best_score == 0.05
    assert len(res.all_results) == 1


def test_grid_search_empty_value_list_raises(backtester, alpha_factory):
    with pytest.raises(ValueError, match="param_grid contains empty value list"):
        grid_search(
            alpha_factory=alpha_factory,
            param_grid={"a": []},
            backtester=backtester,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )


def test_grid_search_metric_extraction_icir_hit_rate(backtester, alpha_factory):
    # Test ICIR metric
    backtester.run_backtest.side_effect = [
        _result(mean_ic=0.1, icir=0.5, hit_rate=0.6),
        _result(mean_ic=0.2, icir=0.4, hit_rate=0.7),
    ]

    res_icir = grid_search(
        alpha_factory=alpha_factory,
        param_grid={"a": [1, 2]},
        backtester=backtester,
        start_date=date(2024, 3, 1),
        end_date=date(2024, 3, 31),
        metric="icir",
    )
    assert res_icir.best_params == {"a": 1}
    assert res_icir.best_score == 0.5

    # Reset mock for hit_rate test
    backtester.run_backtest.reset_mock()
    backtester.run_backtest.side_effect = [
        _result(mean_ic=0.1, icir=0.5, hit_rate=0.6),
        _result(mean_ic=0.2, icir=0.4, hit_rate=0.7),
    ]

    res_hit = grid_search(
        alpha_factory=alpha_factory,
        param_grid={"a": [1, 2]},
        backtester=backtester,
        start_date=date(2024, 3, 1),
        end_date=date(2024, 3, 31),
        metric="hit_rate",
    )
    assert res_hit.best_params == {"a": 2}
    assert res_hit.best_score == 0.7


def test_grid_search_nan_handling(backtester, alpha_factory):
    backtester.run_backtest.side_effect = [_result(mean_ic=float("nan")), _result(mean_ic=0.2)]
    res = grid_search(
        alpha_factory=alpha_factory,
        param_grid={"a": [1, 2]},
        backtester=backtester,
        start_date=date(2024, 4, 1),
        end_date=date(2024, 4, 30),
    )
    assert res.best_params == {"a": 2}
    assert res.best_score == 0.2


def test_random_search_deterministic_sampling(backtester, alpha_factory):
    backtester.run_backtest.side_effect = [_result(0.1), _result(0.2)]
    res = random_search(
        alpha_factory=alpha_factory,
        param_distributions={"a": [1, 2, 3]},
        backtester=backtester,
        start_date=date(2024, 5, 1),
        end_date=date(2024, 5, 31),
        n_iter=2,
        seed=42,
    )

    sampled_params = [entry["params"]["a"] for entry in res.all_results]
    assert sampled_params == [3, 1]  # indices [2, 0] from seed 42


def test_random_search_without_and_with_replacement(backtester, alpha_factory):
    backtester.run_backtest.side_effect = [_result(0.1), _result(0.2), _result(0.3), _result(0.4)]
    res_without = random_search(
        alpha_factory=alpha_factory,
        param_distributions={"a": [1, 2]},
        backtester=backtester,
        start_date=date(2024, 6, 1),
        end_date=date(2024, 6, 30),
        n_iter=2,
        seed=7,
    )
    assert len(res_without.all_results) == 2

    backtester.run_backtest.reset_mock()
    backtester.run_backtest.side_effect = [_result(0.5), _result(0.6), _result(0.7), _result(0.8)]
    res_with = random_search(
        alpha_factory=alpha_factory,
        param_distributions={"a": [1, 2]},
        backtester=backtester,
        start_date=date(2024, 6, 1),
        end_date=date(2024, 6, 30),
        n_iter=4,
        seed=7,
    )
    assert len(res_with.all_results) == 4
    assert res_with.best_score == 0.8


def test_random_search_invalid_inputs(backtester, alpha_factory):
    with pytest.raises(ValueError, match="n_iter must be positive"):
        random_search(
            alpha_factory=alpha_factory,
            param_distributions={"a": [1]},
            backtester=backtester,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            n_iter=0,
        )

    backtester.run_backtest.return_value = _result(0.1)
    res = random_search(
        alpha_factory=alpha_factory,
        param_distributions={},
        backtester=backtester,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        n_iter=1,
    )
    assert res.best_params == {}


def test_extract_metric_supported_and_unsupported():
    result = _result(mean_ic=0.1, icir=0.2, hit_rate=0.3)
    assert _extract_metric(result, "mean_ic") == 0.1
    assert _extract_metric(result, "icir") == 0.2
    assert _extract_metric(result, "hit_rate") == 0.3

    none_result = _result(mean_ic=None, icir=None, hit_rate=None)
    assert math.isnan(_extract_metric(none_result, "mean_ic"))

    with pytest.raises(ValueError, match="Unsupported metric: unknown"):
        _extract_metric(result, "unknown")
