# Walk-Forward Optimization (P4T4-T5.4)

Walk-forward optimization repeatedly re-trains an alpha on a rolling train window and evaluates the chosen parameters on the immediately following **disjoint** test window. This pattern mimics live deployment where parameters are fixed before the period being evaluated, giving a higher-fidelity view of how the alpha will generalize.

## 1. Overview
- **What it is:** A rolling sequence of train → test segments where each test window is out-of-sample relative to the training window that preceded it.
- **Why not a single train/test split?** One split hides time-varying relationships and can overstate robustness. Walk-forward exposes parameter drift, regime changes, and instability across calendar time.
- **Benefits for alpha research:**
  - Detects regimes where signals decay or invert
  - Surfaces parameter sets that are stable across time (not just a lucky split)
  - Provides multiple out-of-sample evaluations to reduce selection bias

## 2. Rolling Window Methodology
- **Train window:** Used for parameter optimization. Length = `train_months`.
- **Test window:** Out-of-sample evaluation immediately following the train window. Length = `test_months`.
- **Step size:** Windows advance by `step_months`. The train window may roll forward overlapping prior trains; the test window always jumps by the same step to the next disjoint evaluation period.
- **Window generation:** Implemented in `libs/backtest/walk_forward.py::WalkForwardOptimizer.generate_windows`. Stops when the next test end would exceed `end_date`. Validates `min_train_samples` before accepting each window.

## 3. Overlap Policy (Critical)
- **Test windows MUST be disjoint** to avoid information leakage between evaluation periods.
- **Train windows MAY overlap** (rolling optimization) to keep more recent history while still respecting disjoint tests.
- **Constraint:** `step_months >= test_months` is enforced; otherwise a `ValueError` is raised during window generation.
- **Warning:** When `step_months < train_months`, a structlog warning (`walk_forward_train_overlap`) is emitted to remind operators that train windows overlap intentionally while tests remain disjoint.

## 4. Configuration Parameters
Defined in `WalkForwardConfig` (`libs/backtest/walk_forward.py`):

| Parameter | Purpose | Typical value |
|-----------|---------|---------------|
| `train_months` | Calendar months in each training window used for parameter search | 12 |
| `test_months` | Calendar months in each out-of-sample evaluation window | 3 |
| `step_months` | How far to advance both windows after each iteration (must satisfy `step_months >= test_months`) | 3 |
| `min_train_samples` | Minimum calendar days required in a train window; guards against too-short samples. Default 252 corresponds to ~1 year of trading days when measured in calendar days (~365). | 252 |
| `overfitting_threshold` | Ratio threshold above which `is_overfit` returns True. Configurable to adjust sensitivity. | 2.0 |

## 5. Metrics and Overfitting Detection
- **Aggregated test IC:** Mean of `test_ic` across all windows (windows with NaN test ICs are excluded).
- **Aggregated test ICIR:** Aggregated test IC divided by the population standard deviation of per-window test ICs (requires ≥2 windows; otherwise `nan`).
- **Overfitting ratio:** `abs(mean(train_ic)) / abs(mean(test_ic))`. Uses absolute value on both numerator and denominator to correctly flag performance drops regardless of sign (e.g., when train IC is positive but test IC is negative). Train ICs are computed only from windows with valid (non-NaN) test ICs to ensure consistent comparison.
- **Threshold:** Ratio > `overfitting_threshold` (default 2.0) flags likely overfitting (`WalkForwardResult.is_overfit`). The threshold is configurable via `WalkForwardConfig`. Treat this as a heuristic for further review, not an automatic reject.

## 6. PIT Determinism and Reproducibility
- A single snapshot is locked once at run start via `PITBacktester._lock_snapshot`.
- The locked `snapshot_id` is forwarded to **all** train and test backtests, ensuring point-in-time–correct data access across every window.
- This makes results reproducible even if underlying data versions advance after the run begins.

## 7. Usage Example
```python
from libs.backtest.walk_forward import WalkForwardConfig, WalkForwardOptimizer
from libs.alpha.research_platform import PITBacktester

config = WalkForwardConfig(
    train_months=12,
    test_months=3,
    step_months=3,
    min_train_samples=252,
)

optimizer = WalkForwardOptimizer(backtester, config)
result = optimizer.run(
    alpha_factory=my_alpha_factory,
    param_grid={"lookback": [20, 40, 60]},
    start_date=date(2020, 1, 1),
    end_date=date(2023, 12, 31),
)

print(f"Aggregated Test IC: {result.aggregated_test_ic:.4f}")
print(f"Overfitting Ratio: {result.overfitting_ratio:.2f}")
print(f"Is Overfit: {result.is_overfit}")
```

## 8. Deferred Features
- Export of per-window returns (Pandas) for `qlib.contrib.evaluate` post-processing is deferred for a later enhancement.

## 9. Related Documents
- [P4T4_TASK.md](../TASKS/P4T4_TASK.md)
- [PITBacktester (libs/alpha/research_platform.py)](../../libs/alpha/research_platform.py)
