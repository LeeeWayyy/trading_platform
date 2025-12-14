# Strategy Comparison Tool (T6.4)

## Overview

The Strategy Comparison Tool lets operators view side-by-side performance for multiple strategies, analyze return correlations, and simulate blended portfolios directly from the web console.

## What it does

- **Metrics table**: total return, volatility, Sharpe ratio, and max drawdown per strategy.
- **Equity comparison**: overlaid equity curves for the selected date range.
- **Correlation heatmap**: pairwise return correlations to spot diversification.
- **Portfolio simulator**: interactive weights (must sum to 1.0) to preview a combined equity curve.

## Data sources

- **P&L**: `pnl_daily` via `StrategyScopedDataAccess.get_pnl_summary` (RBAC enforced).
- **Auth/RBAC**: `require_auth`, `VIEW_PNL` permission, and `get_authorized_strategies`.
- **DB/Redis**: Shared adapters from `apps/web_console/utils/db_pool.py`.

## Feature flag

- Enable with `FEATURE_STRATEGY_COMPARISON=true` (defaults to off).

## Usage

1. Navigate to **Strategy Comparison** in the sidebar (only if the feature flag is enabled).
2. Pick **2–4 strategies** you’re authorized to view.
3. Choose a date range (default: trailing 30 days).
4. Review metrics, equity curves, and correlation heatmap.
5. Adjust weights in the simulator; weights must satisfy `0 ≤ w ≤ 1` and sum to ~1 (tolerance 0.001).

## Notes & Limitations

- Uses fresh async DB connections via `AsyncConnectionAdapter` to avoid event-loop binding issues with `run_async`.
- Correlations require at least two strategies with overlapping P&L; otherwise the heatmap is empty.
- Metrics are calculated from daily P&L; they do not include funding or fees.
