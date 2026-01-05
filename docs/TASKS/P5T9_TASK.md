---
id: P5T9
title: "NiceGUI Migration - Streamlit Deprecation & Documentation"
phase: P5
task: T9
priority: P1
owner: "@development-team"
state: PLANNING
created: 2026-01-04
dependencies: [P5T1, P5T2, P5T3, P5T4, P5T5, P5T6, P5T7, P5T8]
estimated_effort: "5-7 days"
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P5_PLANNING.md, P5T8_TASK.md]
features: [T9.1, T9.2, T9.3, T9.4, T9.5]
---

# P5T9: NiceGUI Migration - Streamlit Deprecation & Documentation

**Phase:** P5 (Web Console Modernization)
**Status:** PLANNING
**Priority:** P1 (Project Completion)
**Owner:** @development-team
**Created:** 2026-01-04
**Estimated Effort:** 5-7 days
**Track:** Phase 8 from P5_PLANNING.md (Documentation) + Streamlit Deprecation
**Dependency:** P5T8 complete (100% feature parity achieved)

---

## Objective

Complete the NiceGUI migration by:
1. Deprecating and removing Streamlit entirely from the codebase
2. Creating comprehensive documentation for the new NiceGUI architecture
3. Updating project documentation to reflect the migration

**Success looks like:**
- `apps/web_console/` directory removed
- All Streamlit-related tests removed
- Streamlit dependencies removed from requirements
- ADR-0031 documenting migration decision rationale
- Concept documentation covering architecture, auth, real-time, and component patterns
- Operational runbooks for deployment, troubleshooting, performance, and rollback
- Updated project documentation (REPO_MAP, PROJECT_STATUS, INDEX.md, CLAUDE.md)
- Codebase size optimized (significant reduction)

**Codebase Impact:**

| Category | Files to Remove | Estimated LOC |
|----------|----------------|---------------|
| Streamlit App | `apps/web_console/` | ~15,000 |
| Streamlit Tests | `tests/apps/web_console/` | ~8,000 |
| Integration Tests | `tests/integration/test_streamlit_csp.py` | ~200 |
| E2E Tests | `tests/e2e/web_console/` | ~500 |

**Note:** Services in `apps/web_console/services/` will be migrated to `libs/` or `apps/web_console_ng/services/` as needed.

---

## Acceptance Criteria

### T9.1 Streamlit Deprecation (2 days)

**Deliverables:**

#### T9.1a Service Migration

Before removing `apps/web_console/`, migrate reusable services:

**Services to Migrate:**
```
apps/web_console/services/
├── alpha_explorer_service.py    → libs/alpha/explorer_service.py (or keep in services/)
├── cb_service.py               → Already used via import
├── comparison_service.py       → libs/strategies/comparison_service.py
├── data_sync_service.py        → libs/data/sync_service.py
├── data_explorer_service.py    → libs/data/explorer_service.py
├── data_quality_service.py     → libs/data/quality_service.py
├── factor_exposure_service.py  → Already in libs/
├── notebook_launcher_service.py → libs/notebooks/launcher_service.py
├── scheduled_reports_service.py → libs/reports/scheduled_service.py
└── sql_validator.py            → libs/data/sql_validator.py
```

**Service → Page Dependency Matrix (migrate in this order):**

| Service | Consuming NiceGUI Page(s) | Target Location |
|---------|---------------------------|-----------------|
| `alpha_explorer_service.py` | `alpha_explorer.py` | `libs/alpha/explorer_service.py` |
| `comparison_service.py` | `compare.py` | `libs/strategies/comparison_service.py` |
| `notebook_launcher_service.py` | `notebook_launcher.py` | `libs/notebooks/launcher_service.py` |
| `scheduled_reports_service.py` | `scheduled_reports.py` | `libs/reports/scheduled_service.py` |
| `data_sync_service.py` | `data_management.py` | `libs/data/sync_service.py` |
| `data_explorer_service.py` | `data_management.py` | `libs/data/explorer_service.py` |
| `data_quality_service.py` | `data_management.py` | `libs/data/quality_service.py` |
| `sql_validator.py` | `data_management.py` | `libs/data/sql_validator.py` |
| `cb_service.py` | `circuit_breaker.py` | Already in libs (keep) |
| `factor_exposure_service.py` | `risk.py` | Already in libs (keep) |

**Migration Checklist:**
- [ ] Identify all services imported by NiceGUI pages
- [ ] Move services to appropriate `libs/` subdirectories (in dependency order above)
- [ ] Update all import paths in `apps/web_console_ng/`
- [ ] Verify no circular imports
- [ ] Run tests to confirm functionality preserved
- [ ] **CRITICAL:** Complete migration + import updates BEFORE any deletion

#### T9.1b Streamlit Removal

**Files/Directories to Remove:**
```
apps/web_console/                    # Entire directory
tests/apps/web_console/              # Entire directory
tests/integration/test_streamlit_csp.py
tests/e2e/web_console/              # Entire directory
tests/libs/web_console_auth/        # Review - may need to keep for shared auth lib
```

**Files to Modify:**
- `requirements.txt` - Remove Streamlit dependencies:
  ```
  # REMOVE these lines:
  streamlit>=1.28.0
  streamlit-autorefresh
  ```
- `apps/__init__.py` - Remove web_console import if present
- `docker-compose.yml` - Remove web_console service if separate
- `.github/workflows/ci.yml` - Remove Streamlit-specific steps if any

#### T9.1d Nginx/Routing Configuration Cleanup

**Files to Update:**
- `infra/nginx/nginx.conf` (or equivalent)
- Any ingress/routing configs

**Required Changes:**
- [ ] Remove `/legacy/` upstream location block (if exists)
- [ ] Remove Streamlit proxy_pass entries
- [ ] Add temporary 301 redirects from old paths to new NiceGUI routes (optional, for bookmarks):
  ```nginx
  # Redirect old Streamlit paths to NiceGUI
  location /legacy/ {
      return 301 /;
  }
  ```
- [ ] Update health check endpoints if referencing Streamlit
- [ ] Verify NiceGUI routes are correctly proxied

**Verification:**
```bash
# After removal, ensure:
make ci-local   # All tests pass
make lint       # No import errors
grep -r "from apps.web_console" apps/  # Should find nothing except services
grep -r "import streamlit" .           # Should find nothing
```

#### T9.1c Dependency Cleanup

**Requirements to Remove:**
```
# requirements.txt - Remove:
streamlit>=1.28.0
streamlit-autorefresh
# Any other streamlit-* packages
```

**Verification:**
```bash
pip uninstall streamlit streamlit-autorefresh -y
pip install -r requirements.txt
make ci-local  # Should pass
```

---

### T9.2 Architecture Decision Record (ADR) (1 day)

**Deliverable:** `docs/ADRs/ADR-0031-nicegui-migration.md`

**Required Sections:**

1. **Title and Metadata**
   - ADR number: 0031
   - Status: Accepted
   - Date: Implementation completion date
   - Decision makers: Development team

2. **Context**
   - [ ] Streamlit execution model limitations (script-rerun, UI flicker)
   - [ ] Synchronous request blocking
   - [ ] `st.session_state` coupling issues
   - [ ] `st.stop()` non-standard flow control
   - [ ] Static data tables limitations
   - [ ] Polling inefficiency (`streamlit_autorefresh`)
   - [ ] Limited layout control

3. **Decision**
   - [ ] Migrate to NiceGUI framework
   - [ ] Event-driven AsyncIO architecture
   - [ ] FastAPI middleware for auth
   - [ ] AG Grid for interactive tables
   - [ ] WebSocket push for real-time updates

4. **Alternatives Considered**
   - [ ] React/Next.js - Rejected (separate frontend repo, skill gap)
   - [ ] Vue.js - Rejected (same reasons as React)
   - [ ] Dash/Plotly - Rejected (callback complexity, limited async)
   - [ ] Panel/Holoviz - Rejected (less mature, smaller community)
   - [ ] Streamlit improvements - Rejected (fundamental model limitations)

5. **Consequences**
   - [ ] Positive: Real-time updates, async operations, responsive UI
   - [ ] Positive: FastAPI integration, same backend patterns
   - [ ] Negative: Learning curve for team
   - [ ] Negative: Migration effort (~70-96 days actual)
   - [ ] Trade-off: NiceGUI less popular than React ecosystem

6. **Security Considerations**
   - [ ] Session architecture changes
   - [ ] Auth flow migration details
   - [ ] CSRF protection approach
   - [ ] Cookie security flags

7. **Performance Requirements**
   - [ ] Target latencies (50-100ms vs 500-2000ms)
   - [ ] Validation approach
   - [ ] Benchmark results

8. **Rollback Plan**
   - [ ] Note: Rollback no longer available after Streamlit removal
   - [ ] Archive location for reference

9. **Implementation Notes**
   - [ ] Migration path (phased approach, P5T1-P5T9)
   - [ ] Testing approach (unit, integration, E2E)
   - [ ] Timeline and milestones
   - [ ] Lessons learned

---

### T9.3 Concept Documentation (2 days)

**Deliverables:**

#### T9.3a NiceGUI Architecture (`docs/CONCEPTS/nicegui-architecture.md`)

**Content:**
- [ ] Event-driven execution model explanation
- [ ] AsyncIO patterns and best practices
- [ ] Comparison diagram: Streamlit vs NiceGUI execution flow
- [ ] Component lifecycle and state management
- [ ] `@ui.refreshable` pattern for reactive updates
- [ ] `ui.timer` vs polling patterns
- [ ] Service integration patterns
- [ ] Error handling patterns
- [ ] `run.io_bound()` for sync service calls

#### T9.3b NiceGUI Auth (`docs/CONCEPTS/nicegui-auth.md`)

**Content:**
- [ ] Session store architecture (Redis-backed)
- [ ] Auth middleware implementation (`@requires_auth`)
- [ ] JWT validation flow
- [ ] OAuth callback handling
- [ ] CSRF protection (double-submit cookie)
- [ ] Cookie security flags
- [ ] Permission checks (RBAC patterns)
- [ ] Session expiration and refresh

#### T9.3c NiceGUI Real-time (`docs/CONCEPTS/nicegui-realtime.md`)

**Content:**
- [ ] WebSocket push architecture
- [ ] `ui.timer` for periodic updates
- [ ] Redis Pub/Sub for cross-instance updates
- [ ] Progressive polling patterns (2s -> 5s -> 10s -> 30s)
- [ ] Connection recovery and state rehydration
- [ ] Real-time vs polling trade-offs

#### T9.3d NiceGUI Components (`docs/CONCEPTS/nicegui-components.md`)

**Content:**
- [ ] Component structure and organization
- [ ] AG Grid usage for interactive tables
- [ ] Form patterns (validation, submission)
- [ ] Dialog and confirmation patterns
- [ ] Tab and expansion patterns
- [ ] Chart integration (`ui.plotly`)
- [ ] Download functionality
- [ ] Service dependency injection

---

### T9.4 Operational Runbooks (1 day)

**Deliverables:**

#### T9.4a Deployment Runbook (`docs/RUNBOOKS/nicegui-deployment.md`)

**Content:**
- [ ] Prerequisites (Redis, PostgreSQL, environment vars)
- [ ] Docker build procedure
- [ ] Kubernetes/compose deployment steps
- [ ] Health check verification
- [ ] nginx configuration for routing
- [ ] Environment variable reference
- [ ] Scaling procedures

#### T9.4b Troubleshooting Runbook (`docs/RUNBOOKS/nicegui-troubleshooting.md`)

**Content:**
- [ ] Common errors and solutions
- [ ] Debug logging configuration
- [ ] WebSocket debugging
- [ ] Session issues diagnosis
- [ ] Performance profiling
- [ ] Log analysis commands
- [ ] Database/Redis connectivity issues

#### T9.4c Performance Runbook (`docs/RUNBOOKS/nicegui-performance.md`)

**Content:**
- [ ] Key metrics to monitor
- [ ] Grafana dashboard setup
- [ ] Performance targets (SLOs)
- [ ] Bottleneck identification
- [ ] Optimization techniques
- [ ] Load testing procedures
- [ ] Resource sizing guidelines

---

### T9.5 Project Documentation Updates (1 day)

**Deliverables:**

#### T9.5a Update REPO_MAP

**File:** `docs/GETTING_STARTED/REPO_MAP.md`

**Changes:**
- [ ] Remove `apps/web_console/` section
- [ ] Update `apps/web_console_ng/` section with full structure
- [ ] Add new page files
- [ ] Add migrated service locations
- [ ] Update file counts

#### T9.5b Update PROJECT_STATUS

**File:** `docs/GETTING_STARTED/PROJECT_STATUS.md`

**Changes:**
- [ ] Mark P5 as complete
- [ ] Add P5T1-P5T9 completion dates
- [ ] Update web console technology stack
- [ ] Note Streamlit deprecation date

#### T9.5c Update INDEX.md

**File:** `docs/INDEX.md`

**Changes:**
- [ ] Add links to new concept documents
- [ ] Add links to new runbooks
- [ ] Add link to ADR-0031
- [ ] Remove any Streamlit references

#### T9.5d Update CLAUDE.md

**File:** `CLAUDE.md`

**Changes:**
- [ ] Add NiceGUI patterns section
- [ ] Update web console guidance for AI agents
- [ ] Add common NiceGUI code patterns
- [ ] Reference new concept documents
- [ ] Remove any Streamlit-specific guidance

#### T9.5e Archive Streamlit Documentation

**Pre-check (identify doc locations to archive):**
```bash
# Find all Streamlit references in docs
rg -n "Streamlit" docs/ --type md

# Expected locations to review:
# - docs/SPECS/services/web_console.md (if exists)
# - docs/RUNBOOKS/web_console.md (if exists)
# - Any Streamlit-specific guides
```

**Known Streamlit doc locations to archive:**
- `docs/SPECS/services/web_console.md` (if exists)
- `docs/RUNBOOKS/web_console.md` (if exists)
- Any files with "streamlit" in name

**Actions:**
- [ ] Run pre-check command to identify all Streamlit doc references
- [ ] Create `docs/ARCHIVE/streamlit/` directory
- [ ] Move any Streamlit-specific docs there
- [ ] Add deprecation notice with date

---

## Implementation Approach

### High-Level Plan

1. **C0: Service Migration** - Migrate services before removal
2. **C1: Streamlit Removal** (T9.1) - Remove apps/web_console and related files
3. **C2: ADR Creation** (T9.2) - Document the migration decision
4. **C3: Concept Docs** (T9.3) - Create architecture documentation
5. **C4: Runbooks** (T9.4) - Create operational documentation
6. **C5: Project Updates** (T9.5) - Update project-level docs

### Commit Strategy

**Commit 1 (P5T8):** Port remaining 6 pages
**Commit 2 (P5T9):** Streamlit deprecation + Documentation

Both in same PR: `feat(P5T8-T9): Complete NiceGUI migration - remaining pages, deprecation, and docs`

---

## Files to Create

### Documentation
```
docs/ADRs/
└── ADR-0031-nicegui-migration.md

docs/CONCEPTS/
├── nicegui-architecture.md
├── nicegui-auth.md
├── nicegui-realtime.md
└── nicegui-components.md

docs/RUNBOOKS/
├── nicegui-deployment.md
├── nicegui-troubleshooting.md
└── nicegui-performance.md

docs/ARCHIVE/streamlit/
└── README.md (deprecation notice)
```

### Files to Remove
```
apps/web_console/                    # ~50+ files
tests/apps/web_console/              # ~80+ files
tests/integration/test_streamlit_csp.py
tests/e2e/web_console/              # ~5 files
```

### Files to Modify
```
requirements.txt                     # Remove streamlit dependencies
docs/GETTING_STARTED/REPO_MAP.md
docs/GETTING_STARTED/PROJECT_STATUS.md
docs/INDEX.md
CLAUDE.md
apps/web_console_ng/pages/__init__.py  # Ensure all pages imported
```

---

## Cross-Reference Matrix

| Document | Must Link To |
|----------|--------------|
| ADR-0031 | P5_PLANNING.md, All concept docs |
| nicegui-architecture.md | ADR-0031, nicegui-auth.md, nicegui-realtime.md |
| nicegui-auth.md | ADR-0031, nicegui-architecture.md |
| nicegui-realtime.md | ADR-0031, nicegui-architecture.md, nicegui-components.md |
| nicegui-components.md | ADR-0031, nicegui-architecture.md |
| nicegui-deployment.md | ADR-0031, nicegui-troubleshooting.md |
| nicegui-troubleshooting.md | nicegui-deployment.md, nicegui-performance.md |
| nicegui-performance.md | nicegui-deployment.md, nicegui-troubleshooting.md |
| REPO_MAP.md | nicegui-architecture.md |
| CLAUDE.md | All concept docs |

---

## Prerequisites Checklist

- [ ] P5T8 complete (all 6 pages ported)
- [ ] All NiceGUI pages functional
- [ ] All tests passing
- [ ] No remaining Streamlit imports in NiceGUI code

---

## Verification

### Pre-Removal Verification
```bash
# Ensure all NiceGUI pages work
make ci-local

# Verify no remaining Streamlit dependencies in NiceGUI
grep -r "import streamlit" apps/web_console_ng/
# Should return nothing

# Verify services are properly imported
grep -r "from apps.web_console.services" apps/web_console_ng/
# Note all imports that need migration
```

### Post-Removal Verification
```bash
# Full CI should pass
make ci-local

# No Streamlit imports anywhere
grep -r "import streamlit" .
# Should return nothing

# No apps.web_console imports
grep -r "from apps.web_console" .
# Should return nothing (except archived docs)

# Dependencies clean
pip list | grep streamlit
# Should return nothing
```

---

## Definition of Done

### T9.1 Streamlit Deprecation
- [ ] Services migrated to appropriate locations
- [ ] `apps/web_console/` removed
- [ ] `tests/apps/web_console/` removed
- [ ] Streamlit dependencies removed from requirements.txt
- [ ] No import errors
- [ ] All tests passing

### T9.2-T9.4 Documentation
- [ ] ADR-0031 created and complete
- [ ] All 4 concept documents created
- [ ] All 3 runbooks created
- [ ] Code examples tested
- [ ] Cross-references verified

### T9.5 Project Updates
- [ ] REPO_MAP updated
- [ ] PROJECT_STATUS updated
- [ ] INDEX.md updated
- [ ] CLAUDE.md updated
- [ ] Archive created

### Overall
- [ ] `make ci-local` passes
- [ ] Code reviewed and approved
- [ ] PR merged

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Service import breaks | Medium | High | Test all imports before removal |
| Missing functionality | Low | High | P5T8 ensures 100% parity first |
| Documentation gaps | Medium | Medium | Review against implementation |
| Broken links after removal | Medium | Low | Run link checker |

---

**Last Updated:** 2026-01-04
**Status:** PLANNING
