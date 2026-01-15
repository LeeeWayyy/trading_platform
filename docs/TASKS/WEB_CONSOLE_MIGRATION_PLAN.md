# Web Console Migration Plan

**Status:** In Progress
**Created:** 2026-01-14
**Target Completion:** Phase 1 of Cleanup Plan

---

## Current Situation

**apps/web_console/** contains 43 Python files that serve as shared backend services for apps/web_console_ng/:
- auth/ (14 files) - Authentication utilities
- services/ (19 files) - Backend services (RiskService, AlphaExplorerService, etc.)
- data/ (1 file) - Data access layer
- utils/ (3 files) - Database utilities
- config.py - Configuration constants

**Dependencies:** 60 files in apps/web_console_ng/ import from apps.web_console

**README Status:** Migration path documented but deferred from P5T9

---

## Deprecation Timeline

### Phase 1: Preparation (This cleanup effort - Week 1)
- [x] Document deprecation plan
- [ ] Create target libs/ structure
- [ ] Set up migration tracking

### Phase 2: Module Migration (Week 2-3, 8-12 hours)
- [ ] Migrate auth/ → libs/web_console_auth/ (already partially exists)
- [ ] Migrate services/ → libs/web_console_services/
- [ ] Migrate data/ → libs/data/ or libs/web_console_data/
- [ ] Migrate utils/ → libs/common/ or domain-specific libs

### Phase 3: Update Imports (Week 3, 4-6 hours)
- [ ] Update all 60 imports in web_console_ng/
- [ ] Update any other imports across codebase
- [ ] Run full test suite

### Phase 4: Deprecation (Week 4)
- [ ] Mark apps/web_console/ as deprecated
- [ ] Remove from docker-compose.yml
- [ ] Remove from CI workflows
- [ ] Move to archive or delete

---

## Migration Mapping

### auth/ → libs/web_console_auth/ (MERGE with existing)
**Current files in apps/web_console/auth/:**
- pkce.py
- idp_health.py
- rate_limiter.py
- jwks_validator.py
- step_up_callback.py
- permissions.py
- mtls_fallback.py
- session_store.py
- mfa_verification.py
- api_client.py
- session_invalidation.py
- oauth2_state.py
- audit_log.py
- oauth2_flow.py

**Action:** Merge with existing libs/web_console_auth/ and deduplicate

---

###services/ → libs/web_console_services/ (NEW)
**Create new library: libs/web_console_services/**

Files to migrate:
- alert_service.py
- alpha_explorer_service.py
- cb_metrics.py
- cb_rate_limiter.py
- cb_service.py
- comparison_service.py
- data_explorer_service.py
- data_quality_service.py
- data_sync_service.py
- duckdb_connection.py
- health_service.py
- notebook_launcher_service.py
- risk_service.py
- scheduled_reports_service.py
- sql_validator.py
- tax_lot_service.py
- user_management.py

---

### data/ → libs/web_console_data/ (NEW)
**Create new library: libs/web_console_data/**

Files to migrate:
- strategy_scoped_queries.py

**Alternative:** Could merge into libs/data/ if generic enough

---

### utils/ → libs/common/ (MERGE)
**Files in apps/web_console/utils/:**
- db.py
- validators.py
- sync_db_pool.py

**Action:** Evaluate if generic (→ libs/common/) or web-console-specific (→ libs/web_console_services/utils/)

---

### config.py → libs/web_console_services/config.py
Move configuration to the services library

---

## Implementation Steps

### Step 1: Create Target Libraries (30 min)
```bash
mkdir -p libs/web_console_services
mkdir -p libs/web_console_data
touch libs/web_console_services/__init__.py
touch libs/web_console_data/__init__.py
```

### Step 2: Migrate auth/ (2 hours)
- Review existing libs/web_console_auth/
- Copy missing files from apps/web_console/auth/
- Deduplicate any overlaps
- Update imports in copied files

### Step 3: Migrate services/ (3 hours)
- Copy all service files to libs/web_console_services/
- Update internal imports
- Create __init__.py exports

### Step 4: Migrate data/ and utils/ (1 hour)
- Move data files to libs/web_console_data/
- Move utils to appropriate locations
- Update imports

### Step 5: Update All Imports (4-6 hours)
- Search and replace in web_console_ng/:
  - `from apps.web_console.auth` → `from libs.platform.web_console_auth`
  - `from apps.web_console.services` → `from libs.web_console_services`
  - `from apps.web_console.data` → `from libs.web_console_data`
  - `from apps.web_console.utils` → `from libs.core.common` or appropriate lib
- Run tests after each batch of changes

### Step 6: Verify and Clean Up (1 hour)
- Run full test suite
- Run full CI locally
- Verify no remaining imports from apps.web_console
- Update documentation

### Step 7: Remove apps/web_console/ (30 min)
- Remove directory
- Remove from docker-compose.yml (if present)
- Remove from .github/workflows/
- Update docs/GETTING_STARTED/REPO_MAP.md

---

## Files Requiring Import Updates

**60 files in apps/web_console_ng/ to update:**

### Core files (high priority):
- apps/web_console_ng/main.py
- apps/web_console_ng/auth/*.py
- apps/web_console_ng/core/*.py
- apps/web_console_ng/pages/*.py
- apps/web_console_ng/components/*.py
- apps/web_console_ng/utils/*.py
- apps/web_console_ng/ui/*.py

---

## Risk Assessment

**Low Risk:**
- All files are currently just shared backend code
- No independent service deployment
- Migration is straightforward copy + import updates

**Mitigations:**
- Test suite run after each migration step
- Update imports in batches
- Keep apps/web_console/ until all imports updated
- Rollback plan: git revert if issues found

---

## Success Criteria

- [ ] All 43 files migrated to appropriate libs/ locations
- [ ] All 60 import statements updated in web_console_ng/
- [ ] Zero imports from apps.web_console remaining
- [ ] Full test suite passes
- [ ] CI passes
- [ ] apps/web_console/ directory removed
- [ ] Documentation updated

---

## Estimated Effort

**Total: 12-16 hours**
- Library creation and auth migration: 2-3h
- Services migration: 3-4h
- Data/utils migration: 1-2h
- Import updates (60 files): 4-6h
- Testing and verification: 2-3h

---

**Next Steps:**
1. Get approval for this migration plan
2. Begin Step 1: Create target libraries
3. Execute migration in phases
4. Track progress via cleanup plan checklist

---

**Last Updated:** 2026-01-14
**Author:** Claude Code
**Status:** Ready for execution
