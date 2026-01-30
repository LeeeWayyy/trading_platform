# libs/trading

<!-- Last reviewed: 2026-01-18 - portfolio_optimizer.py empty universe handling -->

## Identity
- **Type:** Library Group (Trading Logic)
- **Location:** `libs/trading/`

## Overview
Trading logic libraries for portfolio allocation, alpha research, backtesting, and risk management:

- **allocation/** - Portfolio allocation and rebalancing
- **alpha/** - Alpha research framework with PIT-correct backtesting
- **backtest/** - Backtest jobs, Monte Carlo analysis, walk-forward optimization
- **risk/** - Risk analytics for factor covariance and portfolio optimization
- **risk_management/** - Pre-trade and post-trade risk checks

## Libraries

### libs/trading/allocation
See [libs/allocation.md](./allocation.md) for detailed specification.

**Purpose:** Portfolio allocation and rebalancing logic.

**Key Features:**
- Multi-strategy capital allocation
- Portfolio optimization
- Rebalancing logic

### libs/trading/alpha
See [libs/alpha.md](./alpha.md) for detailed specification.

**Purpose:** Alpha research framework with PIT-correct backtesting, canonical alphas, and metrics adapters.

**Key Features:**
- Alpha definition and library
- Simple backtester
- Portfolio analytics
- Metrics and performance tracking

### libs/trading/backtest
See [libs/backtest.md](./backtest.md) for detailed specification.

**Purpose:** Backtest jobs, Monte Carlo analysis, walk-forward optimization, transaction cost modeling, and RQ queue/worker utilities.

**Key Features:**
- Job queue management
- Monte Carlo simulation
- Walk-forward optimization
- Parameter search
- Transaction cost model (Almgren-Chriss)
- Capacity analysis

### libs/trading/risk
See [libs/risk.md](./risk.md) for detailed specification.

**Purpose:** Risk analytics for factor covariance, specific risk, portfolio optimization, and stress testing.

**Key Features:**
- Barra model integration
- Factor covariance estimation
- Portfolio optimizer
- Stress testing

### libs/trading/risk_management
See [libs/risk_management.md](./risk_management.md) for detailed specification.

**Purpose:** Pre-trade and post-trade risk checks with circuit breakers and position limits.

**Key Features:**
- Circuit breaker logic
- Pre-trade risk validation
- Position limit enforcement
- Kill switch functionality

## Dependencies
- **Internal:** libs/core/common, libs/core/redis_client, libs/models/factors
- **External:** numpy, pandas, scipy

## Related Specs
- Individual library specs listed above
- [../services/execution_gateway.md](../services/execution_gateway.md) - Order execution
- [../services/signal_service.md](../services/signal_service.md) - Signal generation

## Testing
- **Test Files:** `tests/libs/trading/` (centralized test directory)
- **Run Tests:** `pytest tests/libs/trading -v`

## Metadata
- **Last Updated:** 2026-01-16 (Test consolidation: tests moved from collocated directories to tests/libs/trading/)
- **Source Files:** `libs/trading/` (group index)
- **ADRs:** N/A
