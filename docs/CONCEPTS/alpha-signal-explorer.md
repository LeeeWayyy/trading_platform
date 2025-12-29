# Alpha Signal Explorer

## Overview
Alpha Signal Explorer is a research-facing page for browsing and analyzing registered alpha signals from the model registry. It surfaces metadata, IC diagnostics, decay curves, and inter-signal correlations, and provides quick access to backtests.

## Key Capabilities
- Browse registered alpha signals with filters (status, IC range).
- IC time-series chart with rolling window overlay.
- Rank IC and Pearson IC side-by-side comparison.
- Decay curve analysis across multiple horizons.
- Correlation matrix for selected signals.
- Quick-launch backtest for the selected signal.
- Export signal metadata and metrics to CSV.
- Pagination for large registries (default 25, max 100 per page).

## Data Sources
- **Model Registry** (`libs/models/registry.py`): authoritative metadata for alpha models.
- **Backtest Results** (`libs/backtest/result_storage.py`): IC time series, decay curves, daily signals.
- **Metrics Adapter** (`libs/alpha/metrics.py`): optional metric computations (future extension).

## Permissions and Access Control
- `VIEW_ALPHA_SIGNALS` permission is required to access the page.
- Access is enforced before any signal data is displayed.

## Metrics Displayed
- Mean IC, ICIR, hit rate, coverage
- Average turnover, decay half-life
- Backtest date range and sample size (where available)

## Operational Notes
- Backtest linkage is via `ModelMetadata.parameters['backtest_job_id']`.
- Missing backtest results are handled gracefully with empty charts and zeroed metrics.
- Correlation matrix is computed from daily mean signals per date.

## Related Docs
- docs/CONCEPTS/model-registry.md
- docs/CONCEPTS/backtest-result-storage.md
- docs/CONCEPTS/backtest-web-ui.md
