---
id: P6T17
title: "Professional Trading Terminal - Strategy & Models"
phase: P6
task: T17
priority: P2
owner: "@development-team"
state: TASK
created: 2026-01-13
dependencies: [P5]
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P6_PLANNING.md]
features: [T17.1-T17.3]
---

# P6T17: Strategy & Models

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** TASK (Not Started)
**Priority:** P2 (Backend Integration)
**Owner:** @development-team
**Created:** 2026-01-13
**Track:** Track 17 of 18
**Dependency:** P5 complete

---

## Objective

Build strategy management, model registry browser, and alert configuration UI.

**Success looks like:**
- Strategy enable/disable from UI
- Model registry browser
- Complete alert configuration UI
- All pages properly role-gated with audit logging

---

## Tasks (3 total)

### T17.1: Strategy Management - MEDIUM PRIORITY

**Goal:** Enable/disable strategies from UI.

**Features:**
- List registered strategies
- Toggle active/inactive
- Show performance summary
- Link to detailed analytics

**Acceptance Criteria:**
- [ ] Strategies page at `/strategies`
- [ ] Strategy list displayed
- [ ] Toggle active/inactive working
- [ ] Performance summary shown
- [ ] Analytics link functional
- [ ] **RBAC:** QUANT role or higher to view
- [ ] **RBAC:** ADMIN role required to toggle active/inactive
- [ ] **Safety:** Confirmation dialog for deactivating strategy with open positions
- [ ] **Audit:** Log all strategy state changes

**Files:**
- Create: `apps/web_console_ng/pages/strategies.py`

---

### T17.2: Model Registry Browser - MEDIUM PRIORITY

**Goal:** Browse and manage ML models.

**Features:**
- List registered models with versions
- Show model metadata (training date, metrics)
- Promote/demote model versions
- View model artifacts

**Acceptance Criteria:**
- [ ] Models page at `/models`
- [ ] Model list with versions displayed
- [ ] Metadata visible
- [ ] Promote/demote functional
- [ ] Artifacts viewable
- [ ] **RBAC:** QUANT role or higher to view
- [ ] **RBAC:** ADMIN role required to promote/demote
- [ ] **Safety:** Confirmation dialog for promoting to production
- [ ] **Audit:** Log all promote/demote actions

**Files:**
- Create: `apps/web_console_ng/pages/model_registry.py`

---

### T17.3: Alert Configuration UI - MEDIUM PRIORITY

**Goal:** Configure alerts from UI.

**Current State:**
- Partially implemented in P5

**Features:**
- Complete alert rules CRUD
- Notification channels (email, Slack, PagerDuty)
- Test alert functionality
- Alert history view

**Acceptance Criteria:**
- [ ] Alert rules CRUD complete
- [ ] Notification channels configurable
- [ ] Test alert button works
- [ ] Alert history displayed
- [ ] **RBAC:** TRADER role or higher to view alerts
- [ ] **RBAC:** ADMIN role required to configure notification channels
- [ ] **Security:** Webhook URLs and API tokens stored encrypted
- [ ] **Audit:** Log all alert configuration changes

**Files:**
- Modify: `apps/web_console_ng/pages/alerts.py`

---

## Dependencies

```
T17.1 Strategy ──> Strategy enable/disable
T17.2 Model Registry ──> Model deployment
T17.3 Alerts ──> Notification channels
```

---

## Testing Strategy

### Unit Tests
- Strategy state management
- Model metadata parsing
- Alert rule validation

### Integration Tests
- Strategy toggle flow
- Model promotion workflow

### E2E Tests
- Strategy management page
- Alert configuration workflow

---

## Definition of Done

- [ ] All 3 tasks implemented
- [ ] Strategy management available
- [ ] Model registry browsable
- [ ] Alert configuration complete
- [ ] **All pages properly role-gated**
- [ ] **All state changes have audit logs**
- [ ] Unit tests > 85% coverage
- [ ] E2E tests pass
- [ ] Code reviewed and approved

---

**Last Updated:** 2026-01-13
**Status:** TASK
