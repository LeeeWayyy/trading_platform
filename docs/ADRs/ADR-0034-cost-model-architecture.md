# ADR-0034: Cost Model Architecture for Backtesting

- Status: Accepted
- Date: 2026-01-29

## Context
P6T9 requires adding realistic cost simulation to backtests and capacity analysis
for strategy sizing. Currently, backtests show only theoretical/gross P&L without
transaction costs or slippage, making it difficult to evaluate strategy viability
at scale. We need:

1. A configurable cost model with commission, spread, and market impact
2. Storage for cost configurations and computed summaries
3. Capacity analysis using trade-weighted ADV/volatility
4. Export functionality with security controls

Key constraints:
- PIT (Point-in-Time) compliance for ADV/volatility data
- Deterministic results for reproducibility
- DB-authoritative security for exports
- Constant-notional AUM assumption (standard backtest convention)

## Decision

### 1. Cost Model Implementation

Use the Almgren-Chriss square root market impact model:
```
impact_bps = impact_coefficient × σ_daily × sqrt(trade_value / ADV_usd)
```

Where:
- `impact_coefficient`: Configurable (default 0.1)
- `σ_daily`: 20-day rolling volatility (PIT-compliant, D-1 lag)
- `ADV_usd`: 20-day average daily volume in USD (PIT-compliant, D-1 lag)

Total cost per trade:
```
total_cost_bps = bps_per_trade + impact_bps
total_cost_usd = total_cost_bps × trade_value / 10000
```

### 2. Database Schema

Store cost configuration in the existing `backtest_jobs` table using JSONB:
```sql
ALTER TABLE backtest_jobs
ADD COLUMN cost_config JSONB DEFAULT NULL,
ADD COLUMN cost_summary JSONB DEFAULT NULL;
```

Cost config structure (stored in `cost_config`):
```json
{
  "enabled": true,
  "bps_per_trade": 5.0,
  "impact_coefficient": 0.1,
  "participation_limit": 0.05,
  "adv_source": "yahoo",
  "portfolio_value_usd": 1000000.0
}
```

Cost summary structure (stored in `cost_summary`):
```json
{
  "total_gross_return": 0.12,
  "total_net_return": 0.089,
  "total_cost_drag": 0.031,
  "total_cost_usd": 31000.0,
  "cost_breakdown": {
    "commission_spread": 18000.0,
    "market_impact": 13000.0
  },
  "capacity_analysis": { ... }
}
```

Add partial index for cost-enabled backtests:
```sql
CREATE INDEX IF NOT EXISTS idx_backtest_jobs_cost_enabled
ON backtest_jobs ((cost_config->>'enabled'))
WHERE cost_config IS NOT NULL AND (cost_config->>'enabled')::boolean = true;
```

### 3. Capacity Analysis

Three-constraint capacity model:
1. **Impact constraint**: At what AUM does market impact reach X bps?
2. **Participation constraint**: At what AUM does average trade exceed Y% of ADV?
3. **Breakeven constraint**: At what AUM does net alpha reach zero?

Portfolio metrics computed via trade-weighted aggregation:
```
portfolio_adv = Σ(trade_value × adv_usd) / Σ(trade_value)
portfolio_sigma = Σ(trade_value × σ_daily) / Σ(trade_value)
```

### 4. Export API

New endpoint: `GET /api/v1/backtest/{job_id}/export?format={csv|json}`

Security model (DB-authoritative):
1. Read `created_by` from DB job metadata (NOT from request)
2. Check ownership BEFORE exposing job status
3. Return 403 for unauthorized access
4. Return 409 for incomplete jobs (after authorization)

Export content:
- Daily returns (gross and net) with ISO timestamps
- Position history with entry/exit
- Trade log with costs
- Cost breakdown per trade
- Capacity analysis summary

### 5. PIT Compliance

ADV and volatility data use Point-in-Time windows:
- 20-day rolling window ending on D-1 (not D)
- Data source: Yahoo Finance via existing loader
- Fallback: Deterministic zero-cost with warning log

## Consequences

### Positive
- Realistic backtest P&L enables accurate strategy evaluation
- Capacity analysis helps with position sizing decisions
- PIT compliance prevents look-ahead bias
- DB-authoritative export security prevents authorization bypass
- JSONB storage allows flexible schema evolution

### Negative
- Additional data requirements (ADV/volatility) may slow backtests
- Market impact model is an approximation (Almgren-Chriss assumes VWAP)
- Constant-notional assumption slightly underestimates costs for compounding

### Follow-ups
- Add volatility source selection (realized vs implied)
- Consider multi-leg order cost reduction (netting)
- Add configurable capacity thresholds per strategy
- Consider async ADV prefetching for large backtests
