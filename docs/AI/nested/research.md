# Research Directory Context

Research strategies, experiments, and model development.

## Reproducibility Requirements

- All experiments must be reproducible from saved data and configuration
- Random seeds must be fixed and documented
- Feature definitions must use the SAME code as production (`libs/data/`)
- Results must be logged with model version, data snapshot, and hyperparameters

## Promotion Path to Production

1. **Research:** Develop and backtest in `research/strategies/`
2. **Validate:** Backtest replay test (same signals from saved data)
3. **Register:** Add model to registry with metrics (IC, Sharpe, drawdown)
4. **Stage:** Promote model from `dev` to `staging` in model registry
5. **Paper trade:** Run with `DRY_RUN=false` + paper credentials for validation period
6. **Promote:** Move to `prod` stage after validation

## Key Directories

- `strategies/` — Strategy implementations and backtesting scripts
- Feature code is in `libs/data/` (shared with production — never duplicate)

## Important

- **Never duplicate feature logic** — import from `libs/` for all feature calculations
- Research notebooks go in `notebooks/` (project root), not here
- Backtest results should include Sharpe ratio, IC, max drawdown, and win rate
