# Repository Cleanup and Organization Plan

**Status:** Nearly Complete (1 item remaining)
**Priority:** Medium-High
**Original Estimated Effort:** 16-24 hours
**Actual Effort:** 15 of 16 items complete
**Reviewed By:** Gemini 3 Pro Preview (2026-01-14)
**Last Updated:** 2026-01-19

---

## Overview

This plan addressed critical organizational issues and prepared the repository for growth. The repository now has strong test coverage, well-organized code structure, and clear research/production separation.

**Key Focus Areas:**
- ~~Add test coverage for critical untested libraries~~ ✅ 88.12% coverage achieved
- **Investigate secrets and tax module TODOs** - In Progress
- ~~Create research/ directory for proper code separation~~ ✅ Done
- ~~Reorganize libs/ for scalability~~ ✅ Already organized
- ~~Clarify web console strategy~~ ✅ Consolidated to web_console_ng

---

## ⏳ Remaining Item

### 2. Investigate Critical Technical Debt

**Risk:** "Ticking time bombs" in secrets and tax modules with financial/compliance implications

**Estimated Effort:** 4 hours

**Secrets Management (libs/platform/secrets/):**
- [ ] Document current secrets architecture
- [ ] Identify deprecation issues
- [ ] Assess security risk level
- [ ] Create scoped tickets with risk assessment
- [x] ADR created: `docs/ADRs/0017-secrets-management.md`

**Tax Module (libs/platform/tax/):**
- [ ] Review all TODO items
- [ ] Assess financial/compliance risk
- [ ] Create scoped tickets with priority levels
- [ ] Document required fixes
- [x] Only 1 minor TODO identified (future enhancement for closed replacement lots)

**Other TODO/FIXME Files (for tracking):**
- tests/libs/alerts/ - 3 files with test coverage gaps
- tests/regression/test_backtest_golden.py - Golden test update
- tests/apps/orchestrator/test_orchestrator.py - Coverage gap
- libs/platform/secrets/cache.py - Implementation detail
- libs/trading/backtest/walk_forward.py - Feature incomplete
- libs/core/common/log_sanitizer.py - Pattern addition
- libs/trading/risk/portfolio_optimizer.py - Constraint handling

---

## ✅ Completed Items

### High Priority (via TEST_IMPROVEMENT_PLAN)

#### 1. Add Test Coverage for Critical Untested Libraries ✅

**Final Status:** 88.12% overall coverage (target was 85%)

- [x] Created 145+ new test files across all major modules
- [x] libs/data/ - Comprehensive coverage
- [x] libs/platform/ - 91% coverage (P1 module)
- [x] libs/trading/ - P0 modules at 85-100% coverage
- [x] apps/execution_gateway/ - P0 module, 83-100% coverage
- [x] apps/orchestrator/ - 91% coverage (P1 module)
- [x] apps/model_registry/ - Has test coverage

**Test Suite:** 9,636 tests in CI shards, 92 excluded (integration/e2e/scripts)

See `docs/ARCHIVE/TASKS_HISTORY/TEST_IMPROVEMENT_PLAN_DONE.md` for details.

---

#### 3. Clarify Web Console Strategy ✅

- [x] Old `apps/web_console/` removed
- [x] Only `apps/web_console_ng/` exists (now the standard)
- [x] No dual maintenance burden

---

### Medium Priority

#### 4. Reorganize Scripts Directory ✅

**Current Structure:**
```
scripts/
├── README.md          # Index with descriptions ✅
├── dev/               # Development utilities ✅
├── ops/               # Operations & monitoring ✅
├── data/              # Data management ✅
├── admin/             # Administrative tasks ✅
├── hooks/             # Git hooks ✅
├── ai_workflow/       # AI workflow ✅
└── testing/           # Test utilities ✅
```

---

#### 5. Create research/ Directory ✅

**Current Structure:**
```
research/
├── README.md              # Guidelines ✅
├── notebooks/             # Jupyter notebooks ✅
├── experiments/           # Ad-hoc experiments ✅
├── strategies/            # Experimental strategies ✅
└── data_exploration/      # EDA ✅
```

---

#### 6. Group libs/ into Logical Subdirectories ✅

**Current Structure:**
```
libs/
├── core/          # common, redis_client, health ✅
├── data/          # data_pipeline, data_providers, data_quality, market_data ✅
├── trading/       # allocation, risk, risk_management, alpha ✅
├── models/        # models, factors ✅
├── platform/      # admin, alerts, analytics, secrets, tax, web_console_auth ✅
└── web_console_services/ ✅
```

---

#### 7. Reorganize Strategies Directory ✅

**Current Structure:**
```
strategies/              # Production only ✅
├── alpha_baseline/
├── ensemble/
└── backtest/
```

Experimental strategies are in `research/strategies/`.

---

#### 8. Document Python Dependency Management ✅

- [x] `docs/STANDARDS/PYTHON_DEPENDENCIES.md` exists

---

#### 9. Review and Optimize CI/CD Pipeline ✅

- [x] Parallel CI with 6 test shards (libs-core, libs-platform, libs-trading, apps-services, strategies, root-and-misc)
- [x] `.github/workflows/ci-tests-parallel.yml`
- [x] `.github/workflows/nightly-full-coverage.yml` (runs at 3 AM UTC)
- [x] Coverage ratchet mechanism
- [x] Shard validation script (`scripts/testing/verify_shard_coverage.py`)
- [x] P0/P1 module registry and guard jobs
- [x] Quarantine system for flaky tests

---

#### 10. Clean Up Test Structure ✅

- [x] All 34 collocated test files migrated to `tests/`
- [x] All collocated `*/tests/` directories deleted
- [x] pytest.ini testpaths simplified to just `tests`
- [x] Shard definitions cover all test directories

---

#### 11. Add Cache Cleanup Target ✅

- [x] `make clean-cache` available (alias for `make clean`)
- [x] `make clean-all` available (cache + repomix outputs + logs)
- [x] Documented in `docs/GETTING_STARTED/SETUP.md`

---

### Low Priority

#### 12. Archive Legacy Migrations Directory ✅

- [x] `migrations/` no longer exists at root
- [x] Legacy migrations archived to `db/legacy/migrations_pre_alembic/`
- [x] Current migrations managed via Alembic in `db/`

---

#### 13. Archive Old Planning Documents ✅

- [x] `docs/TEMPLATES/` directory exists
- [x] All template files moved: `00-TEMPLATE_TASK.md`, `00-TEMPLATE_PROGRESS.md`, `00-TEMPLATE_FEATURE.md`, `00-TEMPLATE_DONE.md`, `00-TEMPLATE_PHASE_PLANNING.md`
- [x] `docs/TEMPLATES/README.md` exists with usage guidelines

---

#### 14. Add Scripts Documentation Index ✅

- [x] `scripts/README.md` exists (266 lines)
- [x] Index grouped by category
- [x] Descriptions for each script directory

---

#### 15. Document Data Schema Management ✅

- [x] `data/README.md` exists (11KB comprehensive documentation)
- [x] Schema documentation for data types
- [x] Data directory structure documented

---

#### 16. Add ADR for Monorepo Build Systems ✅

- [x] `docs/ADRs/ADR-025-monorepo-build-systems.md` exists
- [x] Documents current approach and future considerations

---

## Items NOT Recommended for Cleanup

- **Empty __init__.py files:** Required for Python module structure
- **.gitkeep files:** Intentional placeholders for example directories
- **Multiple README files:** Appropriate for large codebase (one per major directory)
- **Configuration files:** All actively maintained, no redundancy

---

## Success Metrics - All Achieved ✅

### Code Organization ✅
- [x] Single web console implementation
- [x] Scripts organized into logical subdirectories
- [x] research/ directory created and populated
- [x] libs/ organized into logical groups

### Test Coverage ✅
- [x] Overall coverage: 88.12% (target was 85%)
- [x] P0 modules: 85-100% coverage
- [x] P1 modules: 91% coverage

### CI/CD ✅
- [x] Parallel CI with 6 shards
- [x] Nightly coverage workflow
- [x] Coverage ratchet mechanism
- [x] Shard validation

### Developer Experience ✅
- [x] `make clean-cache` available
- [x] `make clean-all` available
- [x] Scripts easily discoverable via README
- [x] Clear production vs. research separation

### Technical Debt ⏳
- [ ] Secrets management risks fully assessed and scoped
- [ ] Tax module TODOs reviewed and scoped
- [x] Secrets ADR created (ADR-0017)
- [x] Monorepo build systems documented (ADR-025)

---

**Last Updated:** 2026-01-19
**Author:** Claude Code with Gemini 3 Pro Preview Review
**Status:** 15 of 16 items complete - Item #2 (Technical Debt Investigation) remaining
