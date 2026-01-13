---
id: P6T9
title: "Professional Trading Terminal - Cost Model & Capacity"
phase: P6
task: T9
priority: P1
owner: "@development-team"
state: TASK
created: 2026-01-13
dependencies: [P5]
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P6_PLANNING.md]
features: [T9.1-T9.4]
---

# P6T9: Cost Model & Capacity

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** TASK (Not Started)
**Priority:** P1 (Research Platform)
**Owner:** @development-team
**Created:** 2026-01-13
**Track:** Track 9 of 18
**Dependency:** P5 complete

---

## Objective

Add realistic cost simulation to backtests and capacity analysis for strategy sizing.

**Success looks like:**
- Transaction costs in backtests (gross vs net P&L)
- Cost model configurable via UI
- Capacity analysis for strategy sizing
- Backtest data export for external verification

---

## Tasks (4 total)

### T9.1: Transaction Cost Model - HIGH PRIORITY

**Goal:** Add realistic cost simulation to backtests.

**Current State:**
- Backtests show "Theoretical P&L"
- No transaction costs or slippage

**Cost Model:**
```python
@dataclass
class CostModel:
    bps_per_trade: float = 5.0      # Commission + half-spread
    min_commission: float = 0.0
    impact_model: str = "linear"    # or 'sqrt' for Square Root Law
    adv_participation_limit: float = 0.05  # Max 5% of ADV
```

**Acceptance Criteria:**
- [ ] Backtest results show Gross and Net P&L
- [ ] Cost model configurable via UI
- [ ] Transaction cost breakdown visible
- [ ] High-turnover strategies correctly penalized

**Files:**
- Create: `libs/backtest/cost_model.py`
- Modify: `libs/backtest/schemas.py`, `libs/backtest/worker.py`, `apps/web_console_ng/pages/backtest.py`

---

### T9.2: Gross vs Net P&L Toggle - MEDIUM PRIORITY

**Goal:** Instantly compare theoretical vs realistic P&L.

**Acceptance Criteria:**
- [ ] Toggle switch in backtest results
- [ ] Both series calculated during backtest
- [ ] Overlay on equity curve chart
- [ ] Show cost attribution (commissions vs slippage vs impact)

---

### T9.3: Turnover & Capacity Analysis - HIGH PRIORITY

**Goal:** Calculate strategy capacity before alpha decays to zero.

**Display:**
```
Capacity Analysis:
├─ Daily Turnover: 15%
├─ Average Holding Period: 6.7 days
├─ Avg Trade Size: $125K
│
├─ At 5 bps impact: $75M AUM
├─ At 10 bps impact: $25M AUM
└─ Implied Max Capacity: $50M (at 7.5 bps avg impact)
```

**Acceptance Criteria:**
- [ ] Turnover metrics displayed (daily %, holding period)
- [ ] Capacity curve plotted vs impact cost
- [ ] Implied max capacity calculated
- [ ] ADV participation limits shown

**Files:**
- Create: `libs/backtest/capacity_analyzer.py`, `apps/web_console_ng/components/capacity_chart.py`

---

### T9.4: Backtest Timeseries Export - MEDIUM PRIORITY

**Goal:** Export full backtest data for external analysis.

**Export Options:**
- Daily Returns (with dates)
- Position History (symbol, qty, entry, exit)
- Trade Log (every fill with timestamps)
- Factor Exposures (daily)
- Drawdown Series

**Acceptance Criteria:**
- [ ] Export button in backtest results
- [ ] CSV with ISO timestamps
- [ ] Include metadata header (config, run date, version)

**Files:**
- Create: `apps/web_console_ng/components/backtest_export.py`

---

## Dependencies

```
T9.1 Cost Model ──> T9.2 Gross/Net Toggle
              ──> T9.3 Capacity Analysis
```

---

## Testing Strategy

### Unit Tests
- Cost model calculations
- Capacity estimation (Square Root Law)

### Integration Tests
- Backtest with cost model

### E2E Tests
- Full backtest flow with costs
- Export functionality

---

## Definition of Done

- [ ] All 4 tasks implemented
- [ ] Transaction costs in backtests
- [ ] Gross/Net toggle functional
- [ ] Capacity analysis available
- [ ] Export functionality working
- [ ] Unit tests > 85% coverage
- [ ] E2E tests pass
- [ ] Code reviewed and approved

---

**Last Updated:** 2026-01-13
**Status:** TASK
