-- Add cost model columns to backtest_jobs table (P6T9 Cost Model & Capacity)
-- Depends on: 0008_create_backtest_jobs.sql
--
-- These columns store the transaction cost model configuration and computed
-- summary for each backtest. Both are JSONB to allow flexible schema evolution.
--
-- cost_config: CostModelConfig serialization (see libs/trading/backtest/cost_model.py)
--   - enabled: bool
--   - bps_per_trade: float
--   - impact_coefficient: float
--   - participation_limit: float
--   - adv_source: string
--   - portfolio_value_usd: float
--
-- cost_summary: CostSummary serialization
--   - total_gross_return, total_net_return, total_cost_drag
--   - commission_spread_cost_usd, market_impact_cost_usd
--   - gross_sharpe, net_sharpe
--   - gross_max_drawdown, net_max_drawdown
--   - num_trades, avg_trade_cost_bps
--   - capacity_analysis: nested CapacityAnalysis object

-- Add cost configuration column (nullable for backward compatibility)
ALTER TABLE backtest_jobs
ADD COLUMN IF NOT EXISTS cost_config JSONB DEFAULT NULL;

-- Add cost summary column (nullable, populated on completion if cost_config.enabled)
ALTER TABLE backtest_jobs
ADD COLUMN IF NOT EXISTS cost_summary JSONB DEFAULT NULL;

-- Partial index for cost-enabled backtests (optimizes queries filtering by cost enabled)
-- Uses B-tree expression index on casted boolean value for efficient lookups
CREATE INDEX IF NOT EXISTS idx_backtest_jobs_cost_enabled
ON backtest_jobs (((cost_config->>'enabled')::boolean))
WHERE (cost_config->>'enabled')::boolean;

-- Index on created_at DESC for time-based queries (if not already exists)
-- This supports common "recent backtests with costs" queries
CREATE INDEX IF NOT EXISTS idx_backtest_jobs_cost_created
ON backtest_jobs (created_at DESC)
WHERE cost_config IS NOT NULL;

COMMENT ON COLUMN backtest_jobs.cost_config IS 'Transaction cost model configuration (CostModelConfig JSON)';
COMMENT ON COLUMN backtest_jobs.cost_summary IS 'Computed cost summary and capacity analysis (CostSummary JSON)';
