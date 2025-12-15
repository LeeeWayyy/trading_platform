import math
import time
from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from libs.alpha.portfolio import TurnoverResult
from libs.alpha.research_platform import BacktestResult
from libs.backtest.monte_carlo import MonteCarloConfig, MonteCarloSimulator


def _make_turnover_result() -> TurnoverResult:
    return TurnoverResult(
        daily_turnover=pl.DataFrame(schema={"date": pl.Date, "turnover": pl.Float64}),
        average_turnover=0.0,
        annualized_turnover=0.0,
    )


def _build_backtest_result(returns: list[float], ic_values: list[float]) -> BacktestResult:
    n = len(returns)
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(n)]
    daily_portfolio_returns = pl.DataFrame({"date": dates, "return": returns})
    daily_ic = pl.DataFrame(
        {
            "date": dates[: len(ic_values)],
            "ic": ic_values,
            "rank_ic": ic_values,
        }
    )
    daily_signals = pl.DataFrame(
        {"permno": [1] * n, "date": dates, "signal": [0.1] * n}
    )
    daily_weights = pl.DataFrame(
        {"permno": [1] * n, "date": dates, "weight": [1.0] * n}
    )

    return BacktestResult(
        alpha_name="test",
        backtest_id="id",
        start_date=dates[0] if dates else date(2024, 1, 1),
        end_date=dates[-1] if dates else date(2024, 1, 1),
        snapshot_id="snap",
        dataset_version_ids={"crsp": "v1"},
        daily_signals=daily_signals,
        daily_ic=daily_ic,
        mean_ic=float(np.mean(ic_values)) if ic_values else 0.0,
        icir=1.0,
        hit_rate=0.5,
        coverage=1.0,
        long_short_spread=0.0,
        autocorrelation={1: 0.0},
        weight_method="zscore",
        daily_weights=daily_weights,
        turnover_result=_make_turnover_result(),
        decay_curve=pl.DataFrame(),
        decay_half_life=None,
        daily_portfolio_returns=daily_portfolio_returns,
    )


def test_seed_reproducibility_bootstrap():
    returns = [0.01, -0.02, 0.015, 0.0, 0.01]
    ic_values = [0.1, 0.05, 0.2, -0.05, 0.0]
    result = _build_backtest_result(returns, ic_values)

    config = MonteCarloConfig(n_simulations=200, random_seed=42)
    sim1 = MonteCarloSimulator(config)
    sim2 = MonteCarloSimulator(config)

    res1 = sim1.run_bootstrap(result)
    res2 = sim2.run_bootstrap(result)

    assert np.allclose(res1.sharpe_distribution, res2.sharpe_distribution)
    assert res1.sharpe_ci.lower_5 == pytest.approx(res2.sharpe_ci.lower_5)
    assert res1.p_value_sharpe == pytest.approx(res2.p_value_sharpe)


def test_confidence_interval_invariant():
    returns = [0.01, -0.02, 0.015, 0.0, 0.01]
    ic_values = [0.1, 0.05, 0.2, -0.05, 0.0]
    result = _build_backtest_result(returns, ic_values)
    simulator = MonteCarloSimulator(MonteCarloConfig(n_simulations=300, random_seed=1))

    res = simulator.run_bootstrap(result)

    for ci in [res.sharpe_ci, res.max_drawdown_ci, res.mean_ic_ci, res.hit_rate_ci]:
        assert ci.lower_5 <= ci.median <= ci.upper_95


def test_bootstrap_and_shuffle_methods():
    returns = [0.01, -0.02, 0.015, 0.0, 0.01]
    ic_values = [0.1, 0.05, 0.2, -0.05, 0.0]
    result = _build_backtest_result(returns, ic_values)

    simulator = MonteCarloSimulator(MonteCarloConfig(n_simulations=150, random_seed=7))
    bootstrap_res = simulator.run_bootstrap(result)

    simulator_shuffle = MonteCarloSimulator(MonteCarloConfig(n_simulations=150, random_seed=7))
    shuffle_res = simulator_shuffle.run_shuffle(result)

    assert bootstrap_res.n_simulations == 150
    assert shuffle_res.n_simulations == 150
    assert bootstrap_res.sharpe_ci
    assert shuffle_res.sharpe_ci


def test_empty_returns_raise_value_error():
    result = _build_backtest_result([], [])
    simulator = MonteCarloSimulator(MonteCarloConfig(n_simulations=10, random_seed=3))

    with pytest.raises(ValueError, match="daily_portfolio_returns is empty"):
        simulator.run_bootstrap(result)


def test_performance_1000_simulations_under_ten_seconds():
    rng = np.random.default_rng(123)
    returns = rng.normal(0.0005, 0.01, size=500).tolist()
    ic_values = rng.normal(0.0, 0.05, size=500).tolist()
    result = _build_backtest_result(returns, ic_values)

    simulator = MonteCarloSimulator(MonteCarloConfig(n_simulations=1000, random_seed=5))
    start = time.monotonic()
    res = simulator.run_bootstrap(result)
    elapsed = time.monotonic() - start

    assert elapsed < 10.0
    assert res.sharpe_distribution.size == 1000


def test_p_value_bounds():
    returns = [0.01, -0.02, 0.015, 0.0, 0.01]
    ic_values = [0.1, 0.05, 0.2, -0.05, 0.0]
    result = _build_backtest_result(returns, ic_values)
    simulator = MonteCarloSimulator(MonteCarloConfig(n_simulations=200, random_seed=11))

    res = simulator.run_bootstrap(result)

    assert 0.0 <= res.p_value_sharpe <= 1.0


def test_p_value_nan_when_observed_is_nan():
    """If observed Sharpe is undefined, p-value should also be NaN."""
    result = _build_backtest_result([0.01], [0.1])  # single observation -> Sharpe NaN
    simulator = MonteCarloSimulator(MonteCarloConfig(n_simulations=20, random_seed=13))

    res = simulator.run_bootstrap(result)

    assert math.isnan(res.sharpe_ci.observed)
    assert math.isnan(res.p_value_sharpe)


def test_custom_confidence_levels_are_used():
    returns = [0.01, -0.02, 0.015, 0.0, 0.01]
    ic_values = [0.1, 0.05, 0.2, -0.05, 0.0]
    result = _build_backtest_result(returns, ic_values)
    config = MonteCarloConfig(
        n_simulations=120, random_seed=17, confidence_levels=(0.1, 0.25, 0.75, 0.9)
    )

    res = MonteCarloSimulator(config).run_bootstrap(result)

    assert set(res.sharpe_ci.quantiles.keys()) == {0.1, 0.25, 0.75, 0.9}
    assert math.isnan(res.sharpe_ci.lower_5)


def test_all_confidence_intervals_present():
    returns = [0.01, 0.02, 0.03, -0.01, 0.0]
    ic_values = [0.1, 0.05, 0.2, 0.0, -0.05]
    result = _build_backtest_result(returns, ic_values)
    simulator = MonteCarloSimulator(MonteCarloConfig(n_simulations=100, random_seed=21))

    res = simulator.run_shuffle(result)

    assert res.sharpe_ci.metric_name == "sharpe"
    assert res.max_drawdown_ci.metric_name == "max_drawdown"
    assert res.mean_ic_ci.metric_name == "mean_ic"
    assert res.hit_rate_ci.metric_name == "hit_rate"


def test_no_ic_data_handles_gracefully():
    """Test that simulation works when BacktestResult has no IC data."""
    returns = [0.01, -0.02, 0.015, 0.0, 0.01]
    result = _build_backtest_result(returns, [])  # Empty IC values
    simulator = MonteCarloSimulator(MonteCarloConfig(n_simulations=50, random_seed=99))

    res = simulator.run_bootstrap(result)

    # Sharpe and other metrics should still compute
    assert res.sharpe_ci is not None
    assert res.max_drawdown_ci is not None
    assert res.hit_rate_ci is not None
    # mean_ic should be NaN when no IC data
    assert math.isnan(res.mean_ic_ci.observed)


def test_run_method_uses_config():
    """Test that run() method dispatches based on config.method."""
    returns = [0.01, -0.02, 0.015, 0.0, 0.01]
    ic_values = [0.1, 0.05, 0.2, -0.05, 0.0]
    result = _build_backtest_result(returns, ic_values)

    # Test bootstrap via run()
    config_bootstrap = MonteCarloConfig(n_simulations=50, method="bootstrap", random_seed=42)
    sim_bootstrap = MonteCarloSimulator(config_bootstrap)
    res_via_run = sim_bootstrap.run(result)

    # Verify run() dispatched to bootstrap correctly
    assert res_via_run.sharpe_ci is not None
    assert res_via_run.n_simulations == 50

    # Test shuffle via run()
    config_shuffle = MonteCarloConfig(n_simulations=50, method="shuffle", random_seed=42)
    sim_shuffle = MonteCarloSimulator(config_shuffle)
    res_shuffle = sim_shuffle.run(result)
    assert res_shuffle.sharpe_ci is not None


def test_constant_returns_sharpe_is_inf():
    """Test that constant positive returns yield infinite Sharpe (zero vol)."""
    # Constant positive returns = zero volatility, positive mean
    returns = [0.01, 0.01, 0.01, 0.01, 0.01]
    ic_values = [0.1, 0.1, 0.1, 0.1, 0.1]
    result = _build_backtest_result(returns, ic_values)

    simulator = MonteCarloSimulator(MonteCarloConfig(n_simulations=10, random_seed=123))
    res = simulator.run_bootstrap(result)

    # Observed Sharpe should be inf for constant positive returns
    assert math.isinf(res.sharpe_ci.observed)
    assert res.sharpe_ci.observed > 0  # Positive infinity


def test_max_drawdown_correctness():
    """Test max drawdown calculation logic directly."""
    # Case 1: Immediate 50% loss
    returns_loss = [-0.5]
    # Use config with 1 sim to just check "observed" value logic which is same as dist logic
    simulator = MonteCarloSimulator(MonteCarloConfig(n_simulations=1, random_seed=42))

    # We can access the private method for direct unit testing of the logic
    # or rely on the observed value in the result
    mdd = simulator._compute_max_drawdown(np.array(returns_loss))
    assert mdd == pytest.approx(-0.5)

    # Case 2: Up then down
    # 1.0 -> 1.5 (+50%) -> 0.75 (-50%)
    # Peak is 1.5. Drawdown is (0.75 - 1.5) / 1.5 = -0.75 / 1.5 = -0.5
    returns_mixed = [0.5, -0.5]
    mdd_mixed = simulator._compute_max_drawdown(np.array(returns_mixed))
    assert mdd_mixed == pytest.approx(-0.5)
