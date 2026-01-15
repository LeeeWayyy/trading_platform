# Repository Cleanup and Organization Plan

**Status:** ✅ APPROVED - Ready for Implementation
**Priority:** Medium-High
**Estimated Effort:** 16-24 hours
**Reviewed By:** Gemini 3 Pro Preview (2026-01-14)

---

## Overview

This plan addresses critical organizational issues and prepares the repository for growth. The repository has a solid foundation (Grade B, A-grade potential) but needs refinement in test coverage, code organization, and research/production separation.

**Key Focus Areas:**
- Add test coverage for critical untested libraries (financial/compliance risk)
- Investigate secrets and tax module TODOs (immediate risk assessment required)
- Create research/ directory for proper code separation
- Reorganize libs/ for scalability
- Clarify web console strategy

---

## High Priority Items

### 1. Add Test Coverage for Critical Untested Libraries

**Risk:** Financial and compliance consequences from bugs in untested critical code

**Zero Test Coverage (Priority 1):**
- `libs/data_pipeline/` - 1,206 LOC, critical ETL logic
- `libs/factors/` - Factor computation
- `libs/tax/` - Tax lot tracking, wash sale detection

**Minimal Coverage (Priority 2):**
- `libs/admin/` - Only API keys tested
- `libs/analytics/` - Only event studies tested
- `apps/model_registry/` - No app-level tests

**Actions:**
- [ ] libs/data_pipeline/ - Unit + integration tests (5-6 hours)
- [ ] libs/factors/ - Unit tests + edge cases (2-3 hours)
- [ ] libs/tax/ - Wash sale, FIFO/LIFO, edge cases (3-4 hours)
- [ ] libs/admin/ - User management, permissions (1-2 hours)
- [ ] libs/analytics/ - Performance metrics, risk analytics (1-2 hours)
- [ ] apps/model_registry/ - API endpoints, versioning (2-3 hours)

**Estimated Effort:** 16-20 hours (Initial Baseline Coverage)
**Impact:** Critical - mitigates financial/operational risk

---

### 2. Investigate Critical Technical Debt (IMMEDIATE)

**Risk:** "Ticking time bombs" in secrets and tax modules with financial/compliance implications

**CRITICAL - Day 1 Investigation (4 hours):**

**libs/common/secrets.py (2 hours):**
- [ ] Document current secrets architecture
- [ ] Identify deprecation issues
- [ ] Assess security risk level
- [ ] Create scoped tickets with risk assessment
- [ ] Create ADR for formal secrets management architecture

**libs/tax/wash_sale_detector.py (2 hours):**
- [ ] Review all TODO items
- [ ] Assess financial/compliance risk
- [ ] Create scoped tickets with priority levels
- [ ] Document required fixes

**Other TODO/FIXME Files (for tracking):**
- tests/libs/alerts/ - 3 files with test coverage gaps
- tests/regression/test_backtest_golden.py - Golden test update
- tests/apps/orchestrator/test_orchestrator.py - Coverage gap
- libs/secrets/cache.py - Implementation detail
- libs/backtest/walk_forward.py - Feature incomplete
- libs/common/log_sanitizer.py - Pattern addition
- libs/risk/portfolio_optimizer.py - Constraint handling

**Estimated Effort:** 4 hours (investigation + scoping only)
**Impact:** Critical - must quantify risk immediately

---

### 3. Clarify Web Console Strategy

**Issue:** Two implementations (web_console + web_console_ng) both actively maintained

**Decision Required (time-boxed to 1 week):**
- [ ] Assign Directly Responsible Individual (DRI)
- [ ] Present analysis with cost/benefit for deprecation
- [ ] Set deprecation timeline for `apps/web_console/`
- [ ] Create migration checklist
- [ ] Update all documentation references
- [ ] Archive old console code
- [ ] Remove from docker-compose.yml and CI

**Estimated Effort:** 2-3 hours
**Impact:** High - reduces maintenance burden, clarifies architecture

---

### 4. Reorganize Scripts Directory

**Issue:** 61 scripts in flat structure, mixed purposes, naming inconsistency

**Target Structure:**
```
scripts/
├── README.md          # Index with descriptions
├── dev/               # Development utilities
├── ops/               # Operations & monitoring
├── data/              # Data management
├── admin/             # Administrative tasks
├── hooks/             # Git hooks (existing)
├── ai_workflow/       # AI workflow (existing)
└── testing/           # Test utilities
```

**Actions:**
- [ ] Create subdirectories
- [ ] Move scripts to appropriate locations
- [ ] Rename test_*.py → validate_*.py or check_*.py
- [ ] Create scripts/README.md with index
- [ ] Update Makefile references
- [ ] Update CI/CD workflow references
- [ ] Update documentation

**Estimated Effort:** 3 hours
**Impact:** Medium - improves developer experience

---

## Medium Priority Items

### 5. Create research/ Directory

**Why:** Separate production from experimental code (standard at top quant firms)

**Structure:**
```
research/
├── README.md              # Guidelines (lenient standards)
├── notebooks/             # Jupyter notebooks
├── experiments/           # Ad-hoc experiments
├── strategies/            # Experimental strategies
└── data_exploration/      # EDA
```

**Actions:**
- [ ] Create directory structure
- [ ] Add research/README.md (lenient quality standards, no deployment)
- [ ] Move experimental strategies:
  - [ ] strategies/momentum/ → research/strategies/momentum/
  - [ ] strategies/mean_reversion/ → research/strategies/mean_reversion/
- [ ] Update .gitignore for research artifacts
- [ ] Update CI to exclude research/ from strict checks
- [ ] Update documentation

**Estimated Effort:** 2-3 hours
**Impact:** High - enables parallel work streams, prevents production contamination

---

### 6. Group libs/ into Logical Subdirectories

**Why:** Flat structure with 22 libraries won't scale to 40+

**Target Structure:**
```
libs/
├── core/          # common, redis_client, health
├── data/          # data_pipeline, data_providers, data_quality, market_data
├── trading/       # allocation, risk, risk_management, alpha
├── models/        # models, factors
└── platform/      # admin, alerts, analytics, secrets, tax, web_console_auth
```

**Actions:**
- [ ] Create subdirectory structure
- [ ] Move libraries to appropriate domains
- [ ] Update all imports: `from libs.core.common` → `from libs.core.common`
- [ ] Update test directory structure
- [ ] Check all Dockerfiles for hardcoded paths
- [ ] Check docker-compose.yml volume mounts
- [ ] Check deployment scripts
- [ ] Fix circular dependencies if revealed
- [ ] Document PYTHONPATH changes
- [ ] Document VS Code extraPaths settings
- [ ] Run full CI suite

**Estimated Effort:** 6-8 hours
**Impact:** Medium-High - improves scalability

---

### 7. Reorganize Strategies Directory

**Why:** Explicit production/research separation enables different CI rules

**Target Structure:**
```
strategies/              # Production only
├── alpha_baseline/
├── ensemble/
└── backtest/

research/strategies/     # Experimental (from item #5)
├── momentum/
└── mean_reversion/
```

**Actions:**
- [ ] Keep strategies/ as production only (no subdirectory)
- [ ] Experimental strategies already moved in item #5
- [ ] Update all imports and references
- [ ] Update CI rules (strict for production, lenient for research)
- [ ] Check Dockerfiles for hardcoded paths
- [ ] Update documentation

**Estimated Effort:** 1-2 hours
**Impact:** Medium - clarifies deployment boundaries

---

### 8. Document Python Dependency Management

**Why:** No documented strategy for managing internal dependencies in monorepo

**Actions:**
- [ ] Create docs/STANDARDS/PYTHON_DEPENDENCIES.md:
  - How to add/update dependencies for services
  - How to add/update dependencies for shared libraries
  - Internal dependency management (path vs. packaged)
  - Versioning conventions
  - Publishing strategy (if applicable)
- [ ] Document current pyproject.toml patterns
- [ ] Add examples for common scenarios

**Estimated Effort:** 2-3 hours
**Impact:** Medium - prevents dependency brittleness

---

### 9. Review and Optimize CI/CD Pipeline

**Why:** With 8,000+ files, need efficient selective test execution

**Actions:**
- [ ] Review .github/workflows/ for:
  - Selective test execution (changed files only)
  - Parallel job execution
  - Caching strategies (dependencies, build artifacts)
  - Redundant steps
- [ ] Measure current performance (avg time, P95, failure rate)
- [ ] Identify optimization opportunities
- [ ] Create docs/GETTING_STARTED/CI_CD_GUIDE.md
- [ ] Document debugging failed CI runs

**Estimated Effort:** 3-4 hours
**Impact:** Medium - improves developer velocity

---

### 10. Clean Up Test Structure

**Issue:** Empty or minimal test directories

**Actions:**
- [ ] Review each directory:
  - If code exists but no tests → add tests (covered in item #1)
  - If placeholder → add README explaining intent
  - If not needed → remove
- [ ] Ensure all apps/ have matching tests/apps/ directories
- [ ] Ensure all libs/ have matching tests/libs/ directories

**Estimated Effort:** 1 hour
**Impact:** Low - improves consistency

---

## Low Priority Items

### 11. Add Cache Cleanup Target

**Actions:**
- [ ] Add make clean-cache to Makefile
- [ ] Add make clean-all (cache + build artifacts)
- [ ] Document in DEVELOPMENT.md

**Estimated Effort:** 30 minutes
**Impact:** Low - developer convenience

---

### 12. Archive Legacy Migrations Directory

**Actions:**
- [ ] Move migrations/ → db/legacy/
- [ ] Create db/README.md (document migration history)
- [ ] Update docs/GETTING_STARTED/REPO_MAP.md

**Estimated Effort:** 30 minutes
**Impact:** Low - reduces confusion

---

### 13. Archive Old Planning Documents

**Actions:**
- [ ] Create docs/TEMPLATES/ directory
- [ ] Move all 00-TEMPLATE_*.md files
- [ ] Move completed *_PLANNING.md to archive
- [ ] Update AI workflow documentation references

**Estimated Effort:** 1 hour
**Impact:** Low - improves docs organization

---

### 14. Add Scripts Documentation Index

**Actions:**
- [ ] Create scripts/README.md with:
  - Index grouped by category
  - One-line description per script
  - Usage examples for key scripts
- [ ] Add docstrings to all Python scripts
- [ ] Ensure all scripts have --help flag

**Estimated Effort:** 2 hours
**Impact:** Low - improves discoverability

---

### 15. Document Data Schema Management

**Actions:**
- [ ] Create data/README.md:
  - Current schema for each data type
  - Schema versioning approach
  - Breaking change process
  - Downstream consumer documentation
- [ ] Consider schema validation in pipeline

**Estimated Effort:** 1-2 hours
**Impact:** Low - prevents schema drift

---

### 16. Add ADR for Monorepo Build Systems

**Actions:**
- [ ] Create ADR-00XX-monorepo-build-systems.md:
  - Context: current script-based approach
  - Options: Pants, Bazel, status quo
  - Decision: Evaluate when >50 services or >100 libs
  - Timeline: Review in 12-18 months

**Estimated Effort:** 1 hour
**Impact:** Low - planning for future

---

## Items NOT Recommended for Cleanup

- **Empty __init__.py files:** Required for Python module structure
- **.gitkeep files:** Intentional placeholders for example directories
- **Multiple README files:** Appropriate for large codebase (one per major directory)
- **Configuration files:** All actively maintained, no redundancy

---

## Implementation Strategy

### Phase 1: Week 1 - Critical Risk Assessment + Organization

**Day 1 (4 hours - CRITICAL):**
- [ ] Investigate libs/common/secrets.py (2h)
- [ ] Investigate libs/tax/wash_sale_detector.py (2h)
- [ ] Create scoped tickets with risk assessment

**Day 2-3 (3 hours):**
- [ ] Clarify web console strategy (assign DRI, set deprecation timeline)

**Day 4-5 (3 hours):**
- [ ] Reorganize scripts directory

**Day 5 (1 hour):**
- [ ] Add cache cleanup targets

**Deliverables:**
- Risk assessment for secrets and tax modules with scoped tickets
- Web console deprecation plan
- Reorganized scripts with README
- Updated Makefile

---

### Phase 2: Week 2-3 - Test Coverage

**Goal:** Initial Baseline Coverage for critical libraries

**Week 2 (10-13 hours):**
- [ ] libs/data_pipeline/ (5-6h)
- [ ] libs/factors/ (2-3h)
- [ ] libs/tax/ (3-4h)

**Week 3 (6-7 hours):**
- [ ] libs/admin/ (1-2h)
- [ ] libs/analytics/ (1-2h)
- [ ] apps/model_registry/ (2-3h)

**Deliverables:**
- 16-20 hours of new test coverage
- Improved CI test metrics
- Reduced risk in critical infrastructure

---

### Phase 3: Week 4 - Structural Improvements

**Goal:** Long-term scalability improvements

**Priority order:**
1. Create research/ directory (2-3h) - Item #5
2. Group libs/ into subdirectories (6-8h) - Item #6
3. Reorganize strategies/ (1-2h) - Item #7
4. Document dependency management (2-3h) - Item #8
5. Review CI/CD pipeline (3-4h) - Item #9

**Deliverables:**
- research/ directory with clear separation
- Grouped libs/ structure
- Production-only strategies/
- Dependency management documentation
- CI/CD optimization plan

---

### Phase 4: Ongoing - Low Priority Items

**As time permits:**
- Clean up test structure
- Archive planning docs
- Add scripts index
- Document data schema management
- Create monorepo ADR

---

## Success Metrics

**Code Organization:**
- [ ] Single web console implementation (or documented dual-mode)
- [ ] Scripts organized into logical subdirectories
- [ ] research/ directory created and populated

**Test Coverage:**
- [ ] libs/data_pipeline: >80% coverage (from 0%)
- [ ] libs/factors: >80% coverage (from 0%)
- [ ] libs/tax: >80% coverage (from 0%)
- [ ] libs/admin: >70% coverage
- [ ] apps/model_registry: >70% coverage

**Technical Debt:**
- [ ] Zero TODO/FIXME without GitHub issues
- [ ] Secrets management formally documented (ADR)
- [ ] Tax module risks assessed and scoped

**Developer Experience:**
- [ ] make clean-cache available
- [ ] Scripts easily discoverable via README
- [ ] Clear production vs. research separation

---

## Risk Assessment

**Critical Risks:**
1. **Secrets module deprecation** - Immediate investigation required (Phase 1, Day 1)
2. **Tax module TODOs** - Financial/compliance implications (Phase 1, Day 1)
3. **Test coverage gaps** - Unknown bugs in critical code (Phase 2)

**Medium Risks:**
1. **libs/ grouping import churn** - Mitigate with thorough testing and Docker/CI checks
2. **Circular dependencies** - May be revealed when moving files
3. **Web console decision paralysis** - Mitigate with time-box and DRI

**Mitigation:**
- Phase 1 Day 1 focuses exclusively on critical risk assessment
- All structural changes include Docker/CI config checks
- Full CI suite runs after every major change
- Rollback plan available for each phase

---

## Rollback Plan

**Phase 1:** Git revert commits (documentation + scripts only, low risk)

**Phase 2:** Tests are additive - disable flaky tests or remove if needed

**Phase 3:**
- libs/ grouping: Single commit with all import changes, easily revertable
- research/ directory: Separate commit, easy to revert

---

**Last Updated:** 2026-01-14
**Author:** Claude Code with Gemini 3 Pro Preview Review
**Ready for:** Implementation
