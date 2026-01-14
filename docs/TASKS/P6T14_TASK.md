---
id: P6T14
title: "Professional Trading Terminal - Data Services"
phase: P6
task: T14
priority: P2
owner: "@development-team"
state: TASK
created: 2026-01-13
dependencies: [P6T13]
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P6_PLANNING.md]
features: [T14.1-T14.4]
---

# P6T14: Data Services

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** TASK (Not Started)
**Priority:** P2 (Research Infrastructure)
**Owner:** @development-team
**Created:** 2026-01-13
**Track:** Track 14 of 18
**Dependency:** P6T13 (Data Infrastructure)

---

## Objective

Build data service UIs: SQL Explorer, data source status, feature store browser, and shadow mode results.

**Success looks like:**
- Safe SQL execution for power users
- Data source health monitoring
- Feature store browsing
- Shadow mode validation results

---

## Tasks (4 total)

### T14.1: SQL Explorer (Validated) - LOW PRIORITY

**Goal:** Safe SQL execution for power users.

**Current State:**
- `sql_validator.py` exists but unused

**Acceptance Criteria:**
- [ ] SQL editor with syntax highlighting (Monaco/CodeMirror)
- [ ] Validate queries via `sql_validator.py` before execution
- [ ] Execute against read-only replica only (never primary)
- [ ] Display results in AG Grid
- [ ] Allow export to CSV
- [ ] **RBAC requirements:**
  - [ ] Require ADMIN or QUANT role to access SQL Explorer
  - [ ] Log all executed queries with user, timestamp, query text
  - [ ] Query results limited to 10,000 rows (configurable)
  - [ ] Timeout enforced (default 30s, max 120s)
- [ ] **Security:**
  - [ ] Block DDL statements (CREATE, DROP, ALTER, TRUNCATE)
  - [ ] Block DML statements (INSERT, UPDATE, DELETE)
  - [ ] Only SELECT queries allowed
  - [ ] Prevent access to sensitive tables (users, api_keys, secrets)

**Files:**
- Create: `apps/web_console_ng/pages/sql_explorer.py`

---

### T14.2: Data Source Status - MEDIUM PRIORITY

**Goal:** Monitor health of data providers.

**Acceptance Criteria:**
- [ ] List all data sources (CRSP, YFinance, etc.)
- [ ] Show last update timestamp
- [ ] Display error rates
- [ ] Manual refresh trigger

---

### T14.3: Feature Store Browser - MEDIUM PRIORITY

**Goal:** Browse available features for research.

**Acceptance Criteria:**
- [ ] List all features with descriptions
- [ ] Show feature lineage (how calculated)
- [ ] Preview sample values
- [ ] Display feature statistics

---

### T14.4: Shadow Mode Results - LOW PRIORITY

**Goal:** Visualize shadow validation results.

**Current State:**
- `shadow_validator.py` runs validation
- No UI for results

**Acceptance Criteria:**
- [ ] Shadow mode page
- [ ] Show prediction vs actual
- [ ] Calculate accuracy metrics
- [ ] Trend over time

---

## Dependencies

```
P6T13.3 Data Services ──> T14.1-T14.4 (wiring foundation)
```

---

## Backend Integration Points

| Feature | Backend Location | Frontend Action |
|---------|-----------------|-----------------|
| SQL Validator | `sql_validator.py` | Use for safe queries |
| Shadow Mode | `shadow_validator.py` | Create results page |

---

## Testing Strategy

### Unit Tests
- SQL validation rules
- Feature metadata parsing

### Integration Tests
- SQL execution flow
- Feature store queries

### E2E Tests
- SQL Explorer workflow
- Feature store browsing

---

## Definition of Done

- [ ] All 4 tasks implemented
- [ ] SQL Explorer functional (with RBAC)
- [ ] Data source status visible
- [ ] Feature store browsable
- [ ] Shadow mode results displayed
- [ ] Unit tests > 85% coverage
- [ ] E2E tests pass
- [ ] Code reviewed and approved

---

**Last Updated:** 2026-01-13
**Status:** TASK
