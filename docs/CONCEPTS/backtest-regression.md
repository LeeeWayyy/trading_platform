# Backtest Regression Harness

**Purpose:** Prevent strategy drift by comparing backtest metrics against known-good "golden" results.

## Current Limitation

> **Note:** This harness currently validates **placeholder** golden results. Actual backtest
> execution via `PITBacktester` depends on T5.1/T5.2 dataset access being configured.
> Until then, the infrastructure (manifest validation, checksum verification, metric drift
> detection) is in place but tests validate placeholder metrics only. The `--use-placeholders`
> flag is required when regenerating to prevent accidental overwrite once real baselines exist.

## Overview

The backtest regression harness provides automated detection of metric drift in alpha signals and backtest performance. When alpha logic, feature calculations, or data processing changes unexpectedly, the regression tests catch the drift before it reaches production.

## Architecture

```
tests/regression/
├── __init__.py                      # Package marker
├── conftest.py                      # Pytest fixtures
├── test_backtest_golden.py          # Regression tests
└── golden_results/
    ├── manifest.json                # Version info, checksums
    ├── momentum_2020_2022.json      # Golden metrics
    ├── momentum_2020_2022_config.json
    ├── value_2020_2022.json
    ├── value_2020_2022_config.json
    └── README.md                    # Governance docs
```

## Key Concepts

### Golden Results

Golden results are the canonical baseline metrics from a known-good backtest run. They include:

| Metric | Description |
|--------|-------------|
| `mean_ic` | Mean Information Coefficient |
| `icir` | IC Information Ratio |
| `hit_rate` | Fraction of correct directional predictions |
| `coverage` | Fraction of universe with valid signals |
| `long_short_spread` | Return difference between long/short portfolios |
| `average_turnover` | Mean daily portfolio turnover |
| `decay_half_life` | Signal decay half-life in days |

### Tolerance Threshold

Tests fail when any metric drifts more than **0.1%** (0.001 relative difference) from golden values. This tolerance balances:
- Catching meaningful code changes
- Allowing minor floating-point variations

### Manifest

The `manifest.json` tracks:
- Version and timestamps
- Dataset snapshot ID for reproducibility
- SHA256 checksums per golden file
- Storage size (decimal MB)

## Governance

### Regeneration Triggers

Golden results should ONLY be regenerated when:
1. **Major alpha logic change** - Intentional algorithm modification
2. **Dataset schema change** - New columns or data format
3. **Quarterly refresh** - Optional periodic validation

### Process

1. Make code changes in feature branch
2. Run regression tests locally: `pytest tests/regression/ -v`
3. If drift is expected and intentional:
   - Regenerate: `python scripts/generate_golden_results.py --use-placeholders`
   - Commit regenerated files with explanation in PR
   - **Requires PR review approval**
4. If drift is unexpected: investigate and fix

### Staleness Policy

CI **fails** if `manifest.last_regenerated` is older than 90 days. This ensures golden results stay reasonably current and prevents stale baselines from masking real regressions.

## Usage

### Running Locally

```bash
# Run regression tests
pytest tests/regression/ -v

# Validate checksums only
python scripts/generate_golden_results.py --validate

# Regenerate golden results (after approval)
# NOTE: --use-placeholders is required to prevent accidental overwrite
python scripts/generate_golden_results.py --use-placeholders

# Dry-run regeneration
python scripts/generate_golden_results.py --use-placeholders --dry-run
```

### CI Integration

The `.github/workflows/backtest-regression.yml` workflow:
1. Triggers on changes to `libs/alpha/`, `libs/backtest/`, `libs/factors/`
2. Checks manifest staleness (<90 days)
3. Validates checksums
4. Runs regression tests
5. Emits GitHub annotations on failure

## Storage Limits

- Total golden results must stay under **10MB**
- Only summary metrics stored (no time-series data)
- Size measured in **decimal MB** (1 MB = 1,000,000 bytes)

## Troubleshooting

### "Checksum mismatch" Error

The golden file was modified without updating the manifest. Either:
- Revert the modification, or
- Regenerate: `python scripts/generate_golden_results.py --use-placeholders`

### "Golden manifest is X days old" Failure

The golden results haven't been validated in over 90 days. The build will fail until this is resolved. Actions:
- Running a full backtest to verify results still match
- Regenerating if intentional changes occurred

### "Metric drifted" Failure

A metric changed more than 0.1% from golden value. Steps:
1. Check if the change was intentional
2. Review recent code changes to `libs/alpha/`, `libs/backtest/`, `libs/factors/`
3. If intentional: regenerate golden results
4. If unintentional: debug and fix the regression

## Related Documents

- [P4T4_TASK.md](../TASKS/P4T4_TASK.md) - Parent task specification
- [P4T4_5.6_TASK.md](../TASKS/P4T4_5.6_TASK.md) - Detailed subtask
- [walk-forward-optimization.md](./walk-forward-optimization.md) - Walk-forward analysis
- [monte-carlo-backtesting.md](./monte-carlo-backtesting.md) - Monte Carlo methods
