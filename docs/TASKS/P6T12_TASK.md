---
id: P6T12
title: "Professional Trading Terminal - Backtest Tools"
phase: P6
task: T12
priority: P1
owner: "@development-team"
state: TASK
created: 2026-01-13
dependencies: [P5, P6T9]
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P6_PLANNING.md]
features: [T12.1-T12.4]
---

# P6T12: Backtest Tools

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** TASK (Not Started)
**Priority:** P1 (Research Platform)
**Owner:** @development-team
**Created:** 2026-01-13
**Track:** Track 12 of 18
**Dependency:** P5 complete, P6T9 (Cost Model for live overlay comparison)

---

## Objective

Build advanced backtest tools: config editor, comparison mode, live vs backtest overlay, and data health monitoring.

**Success looks like:**
- JSON/YAML config editor for power users
- Side-by-side backtest comparison
- Live vs backtest overlay for alpha decay detection
- Data health monitoring

---

## Tasks (4 total)

### T12.1: Config as Code (JSON Editor) - MEDIUM PRIORITY

**Goal:** Advanced mode for power users.

**Acceptance Criteria:**
- [ ] "Advanced Mode" toggle in backtest form
- [ ] Monaco/CodeMirror editor embedded
- [ ] Accept JSON/YAML configuration
- [ ] Schema validation before submission
- [ ] Copy/paste configurations

**Files:**
- Create: `apps/web_console_ng/components/config_editor.py`

---

### T12.2: Backtest Comparison Mode - MEDIUM PRIORITY

**Goal:** Compare two backtests side-by-side.

**Acceptance Criteria:**
- [ ] "Compare" button in results list
- [ ] Select two backtests for comparison
- [ ] Metrics diff table
- [ ] Overlay equity curves
- [ ] Calculate tracking error

---

### T12.3: Live vs Backtest Overlay - HIGH PRIORITY

**Goal:** Compare live performance against backtest expectations.

**Technical Risk:** HIGH - Requires near-perfect alignment between Backtest Engine and Live Execution Engine. Any discrepancy makes the overlay misleading.

**Display:**
```
[Chart: Two overlaid equity curves]
── Live Performance (actual)
-- Backtest Expected (theoretical)

Tracking Error: 2.3%
Alpha Decay Signal: ⚠️ Live underperforming by 15%
Divergence started: 2024-12-15 (28 days ago)
```

**Acceptance Criteria:**
- [ ] Both curves visible on same chart
- [ ] Tracking error calculated (annualized)
- [ ] Alert when live underperforms significantly
- [ ] Divergence analysis available
- [ ] **Alignment rules (CRITICAL):**
  - [ ] Timezone alignment: Both curves use UTC internally, display in user timezone
  - [ ] Corporate actions: Dividends and splits handled identically in both engines
    - **Data source:** `libs/data/corporate_actions.py` (single source of truth)
    - **Reconciliation owner:** DataSyncService responsible for feeding both engines
  - [ ] Slippage model: Live uses actual fills, backtest uses configured slippage model
    - **Model location:** `libs/backtest/cost_model.py` defines slippage parameters
    - **Live fills source:** Execution Gateway webhook data
  - [ ] Trade timing: Document expected timing differences (backtest assumes T, live is T+settlement)
- [ ] **Discrepancy tolerance:**
  - [ ] Define acceptable tracking error range (default: <5% annualized)
  - [ ] Define divergence threshold for alerting (default: 10% cumulative underperformance)
- [ ] **Alert triggers:**
  - [ ] Alert (yellow): Tracking error >5% for 5+ consecutive days
  - [ ] Alert (red): Cumulative divergence >10%
  - [ ] Alert (info): "Data mismatch" when corporate action detected but not yet reconciled
- [ ] **Known limitations documented:** Fill assumption differences, market impact not in backtest, etc.

**Files:**
- Create: `apps/web_console_ng/components/backtest_comparison_chart.py`, `libs/analytics/live_vs_backtest.py`

---

### T12.4: Data Health Widget - MEDIUM PRIORITY

**Goal:** Warn when feature data is stale or missing.

**Display:**
```
Feature Health                    [All OK ✓]
✓ Price Data: 2s ago
✓ Volume Data: 5s ago
⚠️ Momentum Signal: 5m 32s ago (STALE)
❌ Earnings Data: 2h ago (ERROR)
```

**Thresholds:**
- Price/Volume: Stale > 30s
- Signals: Stale > 5m
- Fundamentals: Stale > 1h

**Acceptance Criteria:**
- [ ] Data health visible on dashboard
- [ ] Staleness thresholds configurable
- [ ] Alerts for stale trading signals
- [ ] Source status shown

**Files:**
- Create: `apps/web_console_ng/components/data_health_widget.py`, `libs/feature_store/health_monitor.py`

---

## Dependencies

```
P6T9.1 Cost Model ──> T12.3 Live vs Backtest (slippage comparison)

T12.3 Live vs Backtest ──> Alpha Decay Detection
T12.4 Data Health ──> Dashboard Widget
```

---

## Testing Strategy

### Unit Tests
- Config validation
- Tracking error calculation
- Data staleness detection

### Integration Tests
- Live vs backtest data alignment
- Config editor round-trip

### E2E Tests
- Backtest comparison workflow
- Data health widget updates

---

## Definition of Done

- [ ] All 4 tasks implemented
- [ ] Config editor functional
- [ ] Backtest comparison working
- [ ] Live vs backtest overlay available
- [ ] Data health monitoring active
- [ ] Unit tests > 85% coverage
- [ ] E2E tests pass
- [ ] Code reviewed and approved

---

**Last Updated:** 2026-01-13
**Status:** TASK
