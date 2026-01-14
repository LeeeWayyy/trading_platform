---
id: P6T13
title: "Professional Trading Terminal - Data Infrastructure"
phase: P6
task: T13
priority: P1
owner: "@development-team"
state: TASK
created: 2026-01-13
dependencies: [P5]
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P6_PLANNING.md]
features: [T13.1-T13.4]
---

# P6T13: Data Infrastructure

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** TASK (Not Started)
**Priority:** P1 (Research Infrastructure)
**Owner:** @development-team
**Created:** 2026-01-13
**Track:** Track 13 of 18
**Dependency:** P5 complete

---

## Objective

Build data infrastructure UI: Point-in-Time inspection, coverage visualization, data services integration, and quality monitoring.

**Success looks like:**
- Point-in-Time data inspector for backtest validation
- Data coverage heatmap showing gaps
- Real data services wired (not demo mode)
- Data quality dashboard

---

## Tasks (4 total)

### T13.1: Point-in-Time Data Inspector - HIGH PRIORITY

**Goal:** Validate PIT correctness of backtest data.

**Display:**
```
PIT Time Machine:
Ticker: [AAPL]  Knowledge Date: [2020-01-05]

As of 2020-01-05, the latest available data was:
├─ Earnings: Q3 2019 (reported 2019-10-30)
├─ Revenue: $64.04B
├─ EPS: $3.03
└─ Next Earnings: 2020-01-28 (not yet known!)

✅ No look-ahead bias detected
```

**Acceptance Criteria:**
- [ ] PIT lookup API endpoint
- [ ] Lookup form (ticker, date)
- [ ] Display what was known vs what was future
- [ ] Flag potential look-ahead issues

**Files:**
- Create: `apps/web_console_ng/pages/data_inspector.py`, `apps/web_console_ng/components/pit_lookup.py`

---

### T13.2: Data Coverage Heatmap - MEDIUM PRIORITY

**Goal:** Visualize data gaps and quality issues.

**Display:**
```
Coverage Heatmap (Universe x Date):
      Jan   Feb   Mar   Apr   May
AAPL  ██    ██    ██    ██    ██
MSFT  ██    ██    ░░    ██    ██   ← Missing March!
GOOGL ██    ██    ██    ██    ██
AMZN  ██    ██    ██    ██    ░░   ← Missing May

Legend: ██ Complete  ░░ Missing  ▓▓ Suspicious
```

**Acceptance Criteria:**
- [ ] Query data completeness from feature store
- [ ] Ticker x date matrix
- [ ] Color by status (complete, missing, suspicious)
- [ ] Click to investigate specific gaps
- [ ] Export coverage report

**Files:**
- Create: `apps/web_console_ng/pages/data_coverage.py`, `apps/web_console_ng/components/coverage_heatmap.py`

---

### T13.3: Wire Data Management Services - MEDIUM PRIORITY

**Goal:** Connect existing demo page to real services.

**Current State:**
- `apps/web_console_ng/pages/data_management.py` is demo-only
- Services exist: `DataSyncService`, `DataExplorerService`, `DataQualityService`

**Acceptance Criteria:**
- [ ] Remove demo mode from data_management.py
- [ ] Wire DataSyncService for sync status and triggers
- [ ] Wire DataExplorerService for data browsing
- [ ] Wire DataQualityService for quality checks
- [ ] Add real-time sync progress

**Files:**
- Modify: `apps/web_console_ng/pages/data_management.py`

---

### T13.4: Data Quality Dashboard - MEDIUM PRIORITY

**Goal:** Monitor data quality metrics.

**Acceptance Criteria:**
- [ ] Quality scores by data source
- [ ] Recent quality issues displayed
- [ ] Trend charts for quality over time
- [ ] Alert on quality degradation

---

## Dependencies

```
T13.1 PIT Inspector ──> T13.2 Coverage Heatmap
```

---

## Backend Integration Points

| Feature | Backend Location | Frontend Action |
|---------|-----------------|-----------------|
| PIT Lookup | Feature store | Create query API |
| Data Coverage | Feature store metadata | Query completeness |
| Data Services | `data_sync_service.py`, `data_explorer_service.py` | Wire to page |

---

## Testing Strategy

### Unit Tests
- PIT lookup logic
- Coverage query aggregation

### Integration Tests
- Data service connections
- Coverage query accuracy

### E2E Tests
- PIT inspector form
- Data management sync

---

## Definition of Done

- [ ] All 4 tasks implemented
- [ ] PIT inspector available
- [ ] Coverage heatmap functional
- [ ] Data services wired (not demo)
- [ ] Quality dashboard working
- [ ] Unit tests > 85% coverage
- [ ] E2E tests pass
- [ ] Code reviewed and approved

---

**Last Updated:** 2026-01-13
**Status:** TASK
