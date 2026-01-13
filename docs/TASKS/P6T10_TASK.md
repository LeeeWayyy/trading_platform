---
id: P6T10
title: "Professional Trading Terminal - Quantile & Attribution Analytics"
phase: P6
task: T10
priority: P1
owner: "@development-team"
state: TASK
created: 2026-01-13
dependencies: [P5]
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P6_PLANNING.md]
features: [T10.1-T10.4]
---

# P6T10: Quantile & Attribution Analytics

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** TASK (Not Started)
**Priority:** P1 (Research Platform)
**Owner:** @development-team
**Created:** 2026-01-13
**Track:** Track 10 of 18
**Dependency:** P5 complete

---

## Objective

Build advanced analytics for signal validation: quantile tear sheets, factor attribution, drawdown charts, and monthly returns heatmap.

**Success looks like:**
- Quantile tear sheets for signal validation
- Factor attribution decomposition (alpha vs beta)
- Drawdown underwater chart
- Monthly returns heatmap

---

## Tasks (4 total)

### T10.1: Quantile Tear Sheets - HIGH PRIORITY

**Goal:** Visualize signal monotonicity and long/short spread.

**Display:**
```
Quantile Analysis:
Q5 (Longs)  ───────────────────────────────────▲ +45%
Q4          ─────────────────────────────▲ +28%
Q3          ────────────────────────▲ +12%
Q2          ──────────▼ -5%
Q1 (Shorts) ▼ -22%

Long/Short Spread: 67% (Q5 - Q1)
Monotonicity Score: 95% ✅
```

**Acceptance Criteria:**
- [ ] Quantile spread chart visible in results
- [ ] Monotonicity bar chart shows return by decile
- [ ] Monotonicity score calculated
- [ ] Non-monotonic patterns flagged with warning

**Files:**
- Create: `apps/web_console_ng/components/quantile_chart.py`, `libs/backtest/quantile_analysis.py`
- Modify: `apps/web_console_ng/pages/backtest.py`

---

### T10.2: Factor Attribution - MEDIUM PRIORITY

**Goal:** Decompose returns into Alpha vs Beta exposure.

**Current State:**
- Backend has `libs/analytics/attribution.py`
- No UI for factor analysis

**Display:**
```
Decomposition (Fama-French 5-Factor):
├─ Market Beta (Mkt-RF): +18%
├─ Size (SMB): +3%
├─ Value (HML): -2%
├─ Profitability (RMW): +4%
├─ Investment (CMA): +1%
└─ Idiosyncratic Alpha: +1%  ← The "skill"
```

**Acceptance Criteria:**
- [ ] Attribution page at `/attribution`
- [ ] Fama-French factor regression
- [ ] Stacked bar chart of contributions
- [ ] Rolling factor exposures

**Files:**
- Create: `apps/web_console_ng/pages/attribution.py`, `apps/web_console_ng/components/factor_contribution_chart.py`

---

### T10.3: Drawdown Underwater Chart - MEDIUM PRIORITY

**Goal:** Visualize drawdown depth and duration.

**Acceptance Criteria:**
- [ ] Calculate rolling drawdown series
- [ ] Underwater chart (inverted, shows % underwater)
- [ ] Mark drawdown periods with duration labels
- [ ] Add to backtest results below equity curve

**Files:**
- Create: `apps/web_console_ng/components/drawdown_chart.py`

---

### T10.4: Monthly Returns Heatmap - MEDIUM PRIORITY

**Goal:** Grid visualization of returns by year/month.

**Acceptance Criteria:**
- [ ] Year x Month grid
- [ ] Color by return (green positive, red negative)
- [ ] Annual totals in rightmost column
- [ ] Detect seasonality patterns

**Files:**
- Create: `apps/web_console_ng/components/monthly_heatmap.py`

---

## Dependencies

```
T10.1 Quantiles ──> T10.2 Attribution (signal analysis foundation)
```

---

## Testing Strategy

### Unit Tests
- Quantile analysis correctness
- Factor attribution regression

### Integration Tests
- Factor attribution with backtest data

### E2E Tests
- Quantile chart rendering
- Attribution page display

---

## Definition of Done

- [ ] All 4 tasks implemented
- [ ] Quantile tear sheets functional
- [ ] Factor attribution working
- [ ] Drawdown chart available
- [ ] Monthly heatmap rendering
- [ ] Unit tests > 85% coverage
- [ ] E2E tests pass
- [ ] Code reviewed and approved

---

**Last Updated:** 2026-01-13
**Status:** TASK
