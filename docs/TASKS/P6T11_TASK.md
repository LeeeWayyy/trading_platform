---
id: P6T11
title: "Professional Trading Terminal - Walk-Forward & Parameters"
phase: P6
task: T11
priority: P1
owner: "@development-team"
state: TASK
created: 2026-01-13
dependencies: [P5]
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P6_PLANNING.md]
features: [T11.1-T11.4]
---

# P6T11: Walk-Forward & Parameters

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** TASK (Not Started)
**Priority:** P1 (Research Platform)
**Owner:** @development-team
**Created:** 2026-01-13
**Track:** Track 11 of 18
**Dependency:** P5 complete

---

## Objective

Build walk-forward visualization and parameter analysis tools for strategy robustness validation.

**Success looks like:**
- Walk-forward timeline visualization
- Parameter stability heatmap
- Decay curve visualization
- Alpha cluster map for redundancy detection

---

## Tasks (4 total)

### T11.1: Walk-Forward Visualization - MEDIUM PRIORITY

**Goal:** Visualize train/test windows and stability.

**Current State:**
- Walk-forward logic exists in `walk_forward.py`
- No UI visualization

**Display:**
```
Walk-Forward Timeline (Gantt):
│ 2020 │ 2021 │ 2022 │ 2023 │ 2024 │
│ ████ │      │      │      │  Train 1
│      │ ████ │      │      │  Train 2
│ ░░░░ │ ░░░░ │ ░░░░ │ ░░░░ │  Test
```

**Acceptance Criteria:**
- [ ] Gantt chart showing train/test windows
- [ ] Per-window performance displayed
- [ ] Visual distinction between train (solid) and test (hatched)

---

### T11.2: Parameter Stability Heatmap - MEDIUM PRIORITY

**Goal:** Ensure parameter robustness (not overfitting).

**Display:**
```
Parameter Stability (Sharpe Ratio):
        Window: 10   15   20   25   30
Z-Score: 1.0   1.2  1.4  1.5  1.3  1.1
         2.0   1.4  1.7  2.0  1.6  1.3  ← Stable plateau
```

**Acceptance Criteria:**
- [ ] Grid search during backtest
- [ ] 2D heatmap (X: param1, Y: param2, Color: Sharpe)
- [ ] Flag isolated peaks as potential overfitting

---

### T11.3: Decay Curve Visualization - MEDIUM PRIORITY

**Goal:** Show how quickly alpha decays.

**Current State:**
- `decay_curve.py` exists in backend
- Not visualized in UI

**Acceptance Criteria:**
- [ ] IC at different lags (1d, 2d, 5d, 10d, 20d)
- [ ] Decay curve plot (X: lag, Y: IC)
- [ ] Calculate half-life of alpha
- [ ] Recommend turnover frequency

---

### T11.4: Alpha Cluster Map - LOW PRIORITY

**Goal:** Group similar alphas to avoid redundant exposure.

**Acceptance Criteria:**
- [ ] Alpha correlation matrix
- [ ] Hierarchical clustering
- [ ] Dendrogram visualization
- [ ] Flag highly correlated pairs

---

## Dependencies

```
Walk-forward logic ──> T11.1 Visualization
Decay curve logic ──> T11.3 Visualization
```

---

## Testing Strategy

### Unit Tests
- Walk-forward window calculation
- Parameter grid generation
- Correlation clustering

### Integration Tests
- Walk-forward with backtest
- Parameter sweep execution

### E2E Tests
- Walk-forward Gantt rendering
- Parameter heatmap interaction

---

## Definition of Done

- [ ] All 4 tasks implemented
- [ ] Walk-forward visualization working
- [ ] Parameter stability heatmap functional
- [ ] Decay curve displayed
- [ ] Alpha cluster map available
- [ ] Unit tests > 85% coverage
- [ ] E2E tests pass
- [ ] Code reviewed and approved

---

**Last Updated:** 2026-01-13
**Status:** TASK
