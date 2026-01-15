# Web Console Migration Plan

**Status:** In Progress
**Created:** 2026-01-14
**Target Completion:** Phase 1 of Cleanup Plan

---

## Current Situation

**apps/web_console/** contains 48 Python files that serve as shared backend services for apps/web_console_ng/:
- auth/ (14 files) - Authentication utilities
- services/ (19 files) - Backend services (RiskService, AlphaExplorerService, etc.)
- data/ (1 file) - Data access layer
- utils/ (5 files) - Database utilities and helpers
- config.py (1 file) - Configuration constants
- (plus __init__.py files)

**Dependencies:** 60 files in apps/web_console_ng/ import from apps.web_console

**README Status:** Migration path documented but deferred from P5T9

---

## Deprecation Timeline

### Phase 1: Preparation (This cleanup effort - Week 1)
- [x] Document deprecation plan
- [ ] Create target libs/ structure
- [ ] Set up migration tracking

### Phase 2: Module Migration (Week 2-3, 8-12 hours)
- [ ] Migrate auth/ → libs/platform/web_console_auth/ (already exists)
- [ ] Migrate services/ → libs/web_console_services/
- [ ] Migrate data/ → libs/web_console_data/
- [ ] Migrate utils/ → libs/core/common/ (generic) or libs/web_console_services/utils/ (web-console-specific)

### Phase 3: Update Imports (Week 3, 4-6 hours)
- [ ] Update all 60 imports in web_console_ng/
- [ ] Update imports in tests/ directory (tests/apps/web_console/)
- [ ] Update any other imports across codebase
- [ ] Run full test suite

### Phase 4: Cleanup and Deletion (Week 4)
- [ ] Verify zero remaining imports from apps.web_console
- [ ] Remove from docker-compose.yml (if present)
- [ ] Remove from CI workflows (if present)
- [ ] **DELETE** apps/web_console/ directory completely
- [ ] Update docs/GETTING_STARTED/REPO_MAP.md

---

## Migration Mapping

### auth/ → libs/platform/web_console_auth/ (MERGE with existing)
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

**Action:** Merge with existing libs/platform/web_console_auth/ and deduplicate

**Note:** Existing directory confirmed at libs/platform/web_console_auth/

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

**Note:** Single file, dedicated library keeps web console data logic isolated

---

### utils/ → Specific Targets (SPLIT)
**Files in apps/web_console/utils/:**
- auth_helpers.py → libs/platform/web_console_auth/helpers.py (auth-specific)
- db.py → libs/core/common/db.py (generic database utilities)
- db_pool.py → libs/core/common/db_pool.py (generic database connection pooling)
- sync_db_pool.py → libs/core/common/sync_db_pool.py (generic synchronous pooling)
- validators.py → libs/core/common/validators.py (generic validation utilities)

**Action:** Split utilities by purpose - auth helpers go to auth library, generic DB/validation utilities go to libs/core/common/

**Note:** libs/core/common/ confirmed to exist

---

### config.py → libs/web_console_services/config.py
Move configuration to the services library

**Risk Mitigation:** Ensure config.py is environment-agnostic and doesn't rely on paths relative to apps/ root. Verify proper initialization by web_console_ng.

---

## Detailed Implementation Guidance

### Duplicate File Resolution Strategy

**Three files exist in BOTH apps/web_console/auth/ and libs/platform/web_console_auth/ with different sizes:**

#### `jwks_validator.py` - USE APPS VERSION
- **apps version**: 8151B (source of truth)
- **libs version**: 4866B
- **Decision**: Copy from `apps/web_console/auth/jwks_validator.py`
- **Reasoning**: Apps version is nearly double the size, contains production logic, error handling, and specific key parsing features
- **Action**: Copy apps version, but sanity check if libs contains base classes to inherit from

#### `permissions.py` - USE LIBS VERSION + EXTRACT CONSTANTS
- **apps version**: 660B (likely just constants)
- **libs version**: 12374B (source of truth)
- **Decision**: Keep `libs/platform/web_console_auth/permissions.py`, but extract constants from apps version
- **Reasoning**: Libs version contains actual RBAC/Permission engine; apps version likely defines permission scopes (e.g., READ_HISTORY, EXECUTE_TRADE)
- **Action**:
  1. Keep libs/platform/web_console_auth/permissions.py
  2. Open apps/web_console/auth/permissions.py
  3. Extract any permission constants/scopes
  4. Add them to a `constants.py` or `config.py` in the auth library

#### `rate_limiter.py` - USE LIBS VERSION + CHECK MIDDLEWARE
- **apps version**: 7121B (might contain FastAPI middleware)
- **libs version**: 13645B (source of truth)
- **Decision**: Keep `libs/platform/web_console_auth/rate_limiter.py`
- **Reasoning**: Libs version handles complex logic (Redis connection, sliding windows); apps version might contain FastAPI middleware glue code
- **Action**:
  1. Keep libs version as core logic
  2. Open apps version to check for FastAPI-specific code (Request, Response objects)
  3. If middleware found, either move to libs (if generic) or keep in apps layer

---

### Import Update Strategy

**DO NOT rely solely on automated search/replace - it can hide circular dependencies**

#### Systematic Approach:
1. **Dependencies First**: Migrate data models (libs/web_console_data) BEFORE services that depend on them
2. **Relative Imports**: Within same package, use relative imports
   - Old: `from apps.web_console.auth.utils import verify_token`
   - New: `from .utils import verify_token` (if in same package)
3. **External Imports**: For other migrated components, use full libs paths
   - Old: `from apps.web_console.data import User`
   - New: `from libs.web_console_data import User`
4. **RED FLAG**: If a migrated file imports `from apps.web_console...`, **STOP**
   - Libraries should NOT depend on apps
   - Either move that dependency to libs OR inject at runtime

#### Import Update Checklist:
- [ ] Update relative imports within same package (use `.` notation)
- [ ] Update cross-library imports (use full `libs.` paths)
- [ ] Remove all `apps.web_console` imports from migrated files
- [ ] Verify no circular dependencies (App → Services → Data flow)

---

### __init__.py Export Strategy

**Be explicit to define clean public API**

#### libs/web_console_data/__init__.py
```python
"""Web Console Data Models and Schemas"""
from .strategy_scoped_queries import StrategyScopedQueries

__all__ = ["StrategyScopedQueries"]
```

#### libs/web_console_services/__init__.py
```python
"""Web Console Services"""
from .alert_service import AlertService
from .alpha_explorer_service import AlphaExplorerService
from .risk_service import RiskService
# ... export other services
from .config import Config

__all__ = [
    "AlertService",
    "AlphaExplorerService",
    "RiskService",
    # ... list all services
    "Config",
]
```

#### libs/platform/web_console_auth/ (update existing __init__.py)
Add new exports for migrated auth files:
```python
# Existing exports
from .session import Session
from .jwt_manager import JWTManager
# ... existing exports

# New exports from migration
from .api_client import APIClient
from .oauth2_flow import OAuth2Flow
from .pkce import PKCEGenerator
# ... add all newly migrated files

__all__ = [
    # Existing exports
    "Session",
    "JWTManager",
    # New exports
    "APIClient",
    "OAuth2Flow",
    "PKCEGenerator",
    # ... list all
]
```

---

### Testing Strategy: Test-After-Substep (MANDATORY)

**DO NOT migrate everything then test - this leads to "import hell" debugging**

#### Step-by-Step Testing:
1. **Baseline**: Run `pytest tests/apps/web_console` NOW - ensure all pass before migration
2. **After Data Migration**:
   - Move files to libs/web_console_data/
   - Update imports in data files
   - Update apps to import from new location
   - **RUN TESTS** - must pass before continuing
3. **After Auth Migration**:
   - Merge auth files (with duplicate resolution)
   - Update imports in auth files
   - Update apps to import from new location
   - **RUN TESTS** - must pass before continuing
4. **After Services Migration**:
   - Move service files to libs/web_console_services/
   - Update imports in service files
   - Update apps to import from new location
   - **RUN TESTS** - must pass before continuing
5. **After Utils Migration**:
   - Move utils to libs/core/common/
   - Update all references
   - **RUN TESTS** - must pass before continuing
6. **Final Verification**:
   - Update all test imports
   - Run full test suite
   - Run CI locally
   - Verify zero `apps.web_console` imports remain

---

### Critical Implementation Details

#### 1. Clear Python Cache
**IMPORTANT**: Python caches modules in `__pycache__`. After moving files:
```bash
# Delete old cache to avoid module confusion
find apps/web_console -type d -name __pycache__ -exec rm -rf {} +
find libs/platform/web_console_auth -type d -name __pycache__ -exec rm -rf {} +
find libs/web_console_services -type d -name __pycache__ -exec rm -rf {} +
find libs/web_console_data -type d -name __pycache__ -exec rm -rf {} +
```

IDE/test runners might still pick up old module locations without this cleanup.

#### 2. Prevent Circular Imports
**Dependency Flow Must Be**: `App → Services → Data`

- Data layer (models/schemas) has NO dependencies on services or apps
- Services depend on data layer but NOT on apps
- Apps depend on both services and data

**If you see**: `libs/web_console_data` importing from `libs/web_console_services` → **WRONG**
**If you see**: `libs/web_console_services` importing from `apps.web_console_ng` → **WRONG**

#### 3. Alembic Migrations (If Applicable)
If SQLAlchemy models are moved:
- Alembic scans for models by import path
- Update `alembic/env.py` to import models from NEW location
- Moving class files doesn't require new migration (table names unchanged)
- Only generate migration if you rename tables/columns

#### 4. Config.py Environment Dependencies
**Risk**: `apps/web_console/config.py` may rely on paths relative to `apps/` root

**Mitigation**:
- Review config.py for relative paths (e.g., `../data`, `./templates`)
- Make all paths absolute or use environment variables
- Test config loading from new location before migration

#### 5. Dockerfile Dependencies
**Risk**: Existing Dockerfiles may have explicit `COPY apps/web_console ...` commands that will break when files move to `libs/`

**Check Before Migration**:
```bash
# Search for apps/web_console references in Dockerfiles
grep -r "COPY.*apps/web_console" --include="Dockerfile*" .
grep -r "apps/web_console" --include="Dockerfile*" .
```

**Mitigation**:
- Review all Dockerfiles in apps/web_console_ng/ and infra/
- Update COPY commands to reference new libs/ locations
- Update PYTHONPATH or WORKDIR if needed
- Test Docker builds after migration

#### 6. CI/CD Configuration Dependencies
**Risk**: Pytest invocation in CI workflows may not discover tests in new locations

**Check Before Migration**:
```bash
# Search for pytest configurations and paths
grep -r "apps/web_console" .github/workflows/
grep -r "pytest" .github/workflows/
cat pytest.ini  # Check pytest discovery paths
cat pyproject.toml  # Check [tool.pytest.ini_options]
```

**Mitigation**:
- Review `.github/workflows/` for hardcoded paths
- Update pytest discovery paths in `pytest.ini` or `pyproject.toml`
- Ensure CI can discover tests in `tests/libs/platform/web_console_auth/`
- Run CI locally (`make ci-local`) after migration to verify

---

## Implementation Steps

### Step 0: Pre-Migration Checks (30 min)

#### 0.1 Check Dockerfile Dependencies
```bash
# Search for apps/web_console references in Dockerfiles
grep -r "COPY.*apps/web_console" --include="Dockerfile*" .
grep -r "apps/web_console" --include="Dockerfile*" .
```
- Document any findings
- Plan Dockerfile updates needed after migration

#### 0.2 Check CI/CD Configuration
```bash
# Search for pytest configurations and hardcoded paths
grep -r "apps/web_console" .github/workflows/
grep -r "pytest" .github/workflows/
cat pytest.ini 2>/dev/null || echo "No pytest.ini found"
grep -A 10 "tool.pytest" pyproject.toml 2>/dev/null || echo "No pytest config in pyproject.toml"
```
- Document any hardcoded paths in CI workflows
- Note pytest discovery configuration
- Plan CI/CD updates needed after migration

#### 0.3 Run Baseline Tests
```bash
# Establish baseline - all tests must pass before migration
pytest tests/apps/web_console -v
```
- Verify all tests pass
- Document current test count
- **Do not proceed if tests fail**

### Step 1: Create Target Libraries (30 min)
```bash
mkdir -p libs/web_console_services
mkdir -p libs/web_console_data
touch libs/web_console_services/__init__.py
touch libs/web_console_data/__init__.py
```

### Step 2: Migrate auth/ (2-3 hours)

#### 2.1 Baseline Test
- Run `pytest tests/apps/web_console` to establish baseline (all must pass)

#### 2.2 Handle Duplicate Files (jwks_validator, permissions, rate_limiter)
- **jwks_validator.py**: Copy apps version (8151B) → libs/platform/web_console_auth/
  - Overwrite existing libs version (4866B)
  - Sanity check: review libs version for base classes that need to be preserved
- **permissions.py**: Keep libs version (12374B), extract constants from apps version (660B)
  - Read apps/web_console/auth/permissions.py
  - Extract permission constants (e.g., READ_HISTORY, EXECUTE_TRADE)
  - Add extracted constants to libs/platform/web_console_auth/constants.py or config.py
- **rate_limiter.py**: Keep libs version (13645B), check apps version for middleware
  - Read apps/web_console/auth/rate_limiter.py (7121B)
  - Check for FastAPI-specific middleware (Request, Response imports)
  - If middleware found, document where it should live (likely stays in apps layer)

#### 2.3 Copy Unique Auth Files
Copy 11 unique files from apps/web_console/auth/ to libs/platform/web_console_auth/:
- api_client.py
- audit_log.py
- idp_health.py
- mfa_verification.py
- mtls_fallback.py
- oauth2_flow.py
- oauth2_state.py
- pkce.py
- session_invalidation.py
- session_store.py
- step_up_callback.py

#### 2.4 Migrate auth_helpers.py
- Copy apps/web_console/utils/auth_helpers.py → libs/platform/web_console_auth/helpers.py

#### 2.5 Update Imports in Copied Auth Files
For each migrated auth file:
- Replace `from apps.web_console.auth.xxx` with `from .xxx` (relative imports within auth package)
- Replace `from apps.web_console.utils` with `from .helpers` or `from libs.core.common`
- Replace `from apps.web_console.data` with `from libs.web_console_data`
- **RED FLAG CHECK**: Ensure NO imports from `apps.web_console_ng` (library can't depend on app)

#### 2.6 Update libs/platform/web_console_auth/__init__.py
Add exports for newly migrated files:
```python
from .api_client import APIClient
from .oauth2_flow import OAuth2Flow
from .pkce import PKCEGenerator
from .idp_health import IDPHealthChecker
# ... add all migrated files to __all__
```

#### 2.7 Clear Python Cache
```bash
find apps/web_console/auth -type d -name __pycache__ -exec rm -rf {} +
find libs/platform/web_console_auth -type d -name __pycache__ -exec rm -rf {} +
```

#### 2.8 Test Auth Migration
- Run `pytest tests/apps/web_console` again
- All tests must pass before proceeding to Step 3

### Step 3: Migrate services/ (3-4 hours)

#### 3.1 Copy Service Files
Copy all 18 service files from apps/web_console/services/ to libs/web_console_services/:
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
- And __init__.py

#### 3.2 Migrate config.py
- Copy apps/web_console/config.py → libs/web_console_services/config.py
- **CRITICAL**: Review for relative path dependencies (e.g., `../data`, `./templates`)
- Make all paths absolute or use environment variables
- Test config import: `from libs.web_console_services.config import Config`

#### 3.3 Update Imports in Service Files
For each service file:
- Replace `from apps.web_console.auth` with `from libs.platform.web_console_auth`
- Replace `from apps.web_console.data` with `from libs.web_console_data`
- Replace `from apps.web_console.services.xxx` with `from .xxx` (relative imports within services)
- Replace `from apps.web_console.utils` with `from libs.core.common`
- Replace `from apps.web_console.config` with `from .config`
- **RED FLAG CHECK**: Ensure NO imports from `apps.web_console_ng`

#### 3.4 Create libs/web_console_services/__init__.py
Define explicit exports:
```python
"""Web Console Services"""
from .alert_service import AlertService
from .alpha_explorer_service import AlphaExplorerService
from .risk_service import RiskService
from .data_explorer_service import DataExplorerService
from .user_management import UserManagementService
from .config import Config
# ... add all services

__all__ = [
    "AlertService",
    "AlphaExplorerService",
    "RiskService",
    "DataExplorerService",
    "UserManagementService",
    "Config",
    # ... list all
]
```

#### 3.5 Clear Python Cache
```bash
find apps/web_console/services -type d -name __pycache__ -exec rm -rf {} +
find libs/web_console_services -type d -name __pycache__ -exec rm -rf {} +
```

#### 3.6 Test Services Migration
- Run `pytest tests/apps/web_console` again
- All tests must pass before proceeding to Step 4

### Step 4: Migrate data/ and utils/ (1-2 hours)

#### 4.1 Migrate Data Layer FIRST (dependency order)
- Copy apps/web_console/data/strategy_scoped_queries.py → libs/web_console_data/

#### 4.2 Update Imports in Data Files
- Replace `from apps.web_console.auth` with `from libs.platform.web_console_auth`
- Replace `from apps.web_console.utils` with `from libs.core.common`
- **RED FLAG CHECK**: Ensure data layer has NO imports from services or apps

#### 4.3 Create libs/web_console_data/__init__.py
```python
"""Web Console Data Models and Queries"""
from .strategy_scoped_queries import StrategyScopedQueries

__all__ = ["StrategyScopedQueries"]
```

#### 4.4 Migrate Generic Utils
Copy 4 generic utility files to libs/core/common/:
- apps/web_console/utils/db.py → libs/core/common/db.py
- apps/web_console/utils/db_pool.py → libs/core/common/db_pool.py
- apps/web_console/utils/sync_db_pool.py → libs/core/common/sync_db_pool.py
- apps/web_console/utils/validators.py → libs/core/common/validators.py

#### 4.5 Update Imports in Utils Files
- Replace `from apps.web_console` imports with appropriate libs paths
- These are generic utilities, should have minimal dependencies

#### 4.6 Clear Python Cache
```bash
find apps/web_console/data -type d -name __pycache__ -exec rm -rf {} +
find apps/web_console/utils -type d -name __pycache__ -exec rm -rf {} +
find libs/web_console_data -type d -name __pycache__ -exec rm -rf {} +
find libs/core/common -type d -name __pycache__ -exec rm -rf {} +
```

#### 4.7 Test Data and Utils Migration
- Run `pytest tests/apps/web_console` again
- All tests must pass before proceeding to Step 5

### Step 5: Update All Imports in Application Code (4-6 hours)
- Search and replace in web_console_ng/:
  - `from apps.web_console.auth` → `from libs.platform.web_console_auth`
  - `from apps.web_console.services` → `from libs.web_console_services`
  - `from apps.web_console.data` → `from libs.web_console_data`
  - `from apps.web_console.utils` → `from libs.core.common` (for db/validators) or `from libs.platform.web_console_auth` (for auth_helpers)
- Run tests after each batch of changes

### Step 6: Update Test Imports (1-2 hours)
- Search and replace in tests/apps/web_console/:
  - Update all import statements to match new libs/ structure
  - Update test file paths if needed (e.g., tests/libs/platform/web_console_auth/)
  - Run tests after each batch to verify

### Step 7: Verify and Clean Up (1 hour)
- Run full test suite
- Run full CI locally
- Verify no remaining imports from apps.web_console
- Update documentation

### Step 8: Update Docker and CI/CD, Then Delete apps/web_console/ (1-2 hours)

#### 8.1 Update Dockerfiles
- Review findings from Step 0.1
- Update any Dockerfile COPY commands that reference apps/web_console
- Update PYTHONPATH if needed in Docker entrypoints
- Test Docker builds:
```bash
docker-compose build web_console_ng  # or relevant service
```

#### 8.2 Update CI/CD Configurations
- Review findings from Step 0.2
- Update `.github/workflows/` files with hardcoded apps/web_console paths
- Update pytest configuration in `pytest.ini` or `pyproject.toml` if needed
- Ensure test discovery includes new libs/ locations

#### 8.3 Verify Zero References
```bash
# Search for any remaining references to apps/web_console
grep -r "from apps.web_console" --include="*.py" .
grep -r "import apps.web_console" --include="*.py" .
grep -r "apps/web_console" --include="Dockerfile*" .
grep -r "apps/web_console" .github/workflows/
```
- **Must return zero results** before proceeding to deletion

#### 8.4 Delete apps/web_console/ Directory
```bash
# Final confirmation check
ls -la apps/web_console/

# Delete the directory
rm -rf apps/web_console/

# Verify deletion
ls apps/web_console 2>&1  # Should return "No such file or directory"
```

#### 8.5 Update Documentation
- Update docs/GETTING_STARTED/REPO_MAP.md to reflect new structure
- Remove apps/web_console/ references
- Document new libs/web_console_services/ and libs/web_console_data/ locations

#### 8.6 Final Verification
```bash
# Run full test suite
pytest

# Run full CI locally
make ci-local

# Verify Docker builds
docker-compose build
```
- All must pass before considering migration complete

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

**Moderate Risk:**
- All files are currently just shared backend code
- No independent service deployment
- Migration is straightforward copy + import updates
- Some complexity in merging with existing libs/platform/web_console_auth/
- Config.py coupling risk if it relies on relative paths

**Specific Risks:**
1. **Configuration Coupling**: config.py may rely on paths relative to apps/ root
2. **Auth Merge Complexity**: Deduplication required when merging with existing auth library
3. **Test Coverage**: Must update both application and test imports
4. **Import Dependencies**: 60+ files depend on apps.web_console imports

**Mitigations:**
- Verified target directories exist (libs/platform/web_console_auth/, libs/core/common/)
- Test suite run after each migration step
- Update imports in batches with tests after each batch
- Explicit test migration step added
- Keep apps/web_console/ until all imports updated and verified
- Complete deletion only after zero references confirmed
- Rollback plan: git revert if issues found

---

## Success Criteria

- [ ] Pre-migration checks completed (Dockerfiles, CI/CD, baseline tests)
- [ ] All 48 files migrated to appropriate libs/ locations
- [ ] All 60+ import statements updated in web_console_ng/
- [ ] All test imports updated in tests/ directory
- [ ] Zero imports from apps.web_console remaining (verified via grep)
- [ ] Full test suite passes
- [ ] CI passes (make ci-local)
- [ ] Dockerfiles updated and Docker builds succeed
- [ ] CI/CD configurations updated for new paths
- [ ] apps/web_console/ directory completely deleted
- [ ] Documentation updated (REPO_MAP.md)

---

## Estimated Effort

**Total: 16-22 hours**
- Pre-migration checks (Dockerfiles, CI/CD, baseline tests): 0.5-1h
- Library creation and auth migration: 2-3h
- Services migration: 3-4h
- Data/utils migration: 1-2h
- Import updates in web_console_ng/ (60+ files): 4-6h
- Test import updates: 1-2h
- Testing and verification: 2-3h
- Docker/CI/CD updates and final deletion: 1-2h
- Documentation updates: 1h

---

**Next Steps:**
1. Get approval for this migration plan
2. Begin Step 1: Create target libraries
3. Execute migration in phases
4. Track progress via cleanup plan checklist

---

**Last Updated:** 2026-01-14 (Final revision with Docker/CI/CD checks after Gemini final approval)
**Author:** Claude Code
**Status:** APPROVED - Ready for execution

**Revision History:**
- **Initial version**: Basic migration plan with 8 steps
- **Revision 1**: Corrected paths (libs/platform/web_console_auth/, libs/core/common/), added test migration step, clarified deletion mandate
- **Revision 2**: Added comprehensive implementation guidance including:
  - Duplicate file resolution strategy (jwks_validator, permissions, rate_limiter)
  - Import update strategy with RED FLAG checks
  - __init__.py export patterns
  - Test-after-substep mandatory testing strategy
  - Critical implementation details (__pycache__, circular imports, Alembic, config.py)
  - Detailed substeps for Steps 2, 3, and 4
- **Revision 3 (FINAL)**: Added Docker and CI/CD validation based on Gemini final review:
  - Step 0: Pre-migration checks (Dockerfiles, CI/CD, baseline tests)
  - Critical Implementation Detail #5: Dockerfile dependencies
  - Critical Implementation Detail #6: CI/CD configuration dependencies
  - Step 8 expanded: Docker/CI/CD updates before deletion
  - Updated estimated effort: 16-22 hours (was 14-19 hours)
  - Updated success criteria to include Docker and CI/CD validation
