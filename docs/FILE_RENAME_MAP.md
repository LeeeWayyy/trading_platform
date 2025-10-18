# File Rename Map - Phase Naming Reorganization

**Date:** October 18, 2024
**Purpose:** Document file renames for phase/task naming standardization
**Branch:** `docs/reorganize-phase-tracking`

---

## Naming Convention

**New Standard:** `P{phase}T{task}` or `P{phase}.{track}T{task}`

- **P0** - MVP Core (Days 0-45)
- **P1** - Advanced Features (Days 46-90)
  - **P1.1** - Track 1: Infrastructure Enhancements
  - **P1.2** - Track 2: New Advanced Features
  - **P1.3** - Track 3: Production Hardening

---

## Implementation Guides

### P0 Tasks (MVP Core)

| Old Filename | New Filename | Description |
|--------------|--------------|-------------|
| `t1-data-etl.md` | `p0t1-data-etl.md` | Data ETL with Corporate Actions |
| `t2-baseline-strategy-qlib.md` | `p0t2-baseline-strategy.md` | Baseline ML Strategy + MLflow |
| `t3-signal-service.md` | `p0t3-signal-service.md` | Signal Service (Model Registry + Hot Reload) |
| `t3-p4-fastapi-application.md` | `p0t3-p4-fastapi-application.md` | Signal Service - Phase 4 (FastAPI) |
| `t3-p5-hot-reload.md` | `p0t3-p5-hot-reload.md` | Signal Service - Phase 5 (Hot Reload) |
| `t3-p6-integration-tests.md` | `p0t3-p6-integration-tests.md` | Signal Service - Phase 6 (Integration Tests) |
| `t4-execution-gateway.md` | `p0t4-execution-gateway.md` | Execution Gateway (Idempotent Orders) |
| `t5-orchestrator.md` | `p0t5-orchestrator.md` | Orchestrator Service |
| `t6-paper-run.md` | `p0t6-paper-run.md` | Paper Run Automation |

### P1 Tasks (Advanced Features)

| Old Filename | New Filename | Description |
|--------------|--------------|-------------|
| `t1.2-redis-integration.md` | `p1.1t2-redis-integration.md` | Redis Integration (Track 1, Task 2) |

---

## Task Reference Files

| Old Filename | New Filename | Description |
|--------------|--------------|-------------|
| `P0_TICKETS.md` | `P0_TASKS.md` | P0 task list (marked complete) |

---

## Future File Naming

### P1 Track 1: Infrastructure Enhancements

- `p1.1t1-enhanced-pnl.md` - Enhanced P&L Calculation (already complete, doc pending)
- `p1.1t2-redis-integration.md` - Redis Integration ✅
- `p1.1t3-duckdb-analytics.md` - DuckDB Analytics Layer (next task)
- `p1.1t4-timezone-timestamps.md` - Timezone-Aware Timestamps
- `p1.1t5-operational-status.md` - Operational Status Command

### P1 Track 2: New Advanced Features

- `p1.2t1-realtime-data.md` - Real-Time Market Data Streaming
- `p1.2t2-advanced-strategies.md` - Advanced Trading Strategies
- `p1.2t3-risk-management.md` - Risk Management System

### P1 Track 3: Production Hardening

- `p1.3t1-monitoring-alerting.md` - Monitoring & Alerting (Prometheus + Grafana)
- `p1.3t2-centralized-logging.md` - Centralized Logging (ELK/Loki)
- `p1.3t3-cicd-pipeline.md` - CI/CD Pipeline

---

## ADRs (No Rename Required)

ADRs use sequential numbering (0000, 0001, etc.) and don't need phase prefixes.

---

## Lessons Learned

### Existing

| Filename | Related Task | Status |
|----------|--------------|--------|
| `p1-p3-testing-journey.md` | P0T3 (Phases 1-3) | Keep as-is (refers to phases within T3) |
| `t1.2-redis-integration-fixes.md` | P1.1T2 | Keep as-is (already clear it's T1.2) |
| `t6-paper-run-retrospective.md` | P0T6 | Keep as-is (already established) |

### Future

Use format: `p{phase}t{task}-{topic}.md`
- Example: `p1.1t3-duckdb-lessons.md`

---

## Migration Notes

### Internal References

All markdown files that reference implementation guides need updates:

**Files to Update:**
- `docs/INDEX.md`
- `docs/GETTING_STARTED/PROJECT_STATUS.md`
- `docs/GETTING_STARTED/REPO_MAP.md`
- `docs/TASKS/P0_TASKS.md`
- `docs/TASKS/P1_PLANNING.md`
- All ADR files (0001-0009)
- All CONCEPTS files
- README.md

**Search Pattern:**
```bash
grep -r "t[1-6]-\|t[0-9]\.[0-9]-" docs/*.md
```

**Replace Pattern:**
- `t1-` → `p0t1-`
- `t2-` → `p0t2-`
- `t3-` → `p0t3-`
- `t4-` → `p0t4-`
- `t5-` → `p0t5-`
- `t6-` → `p0t6-`
- `t1.2-` → `p1.1t2-`

---

## Rollback Plan

If needed to rollback, reverse the renames:

```bash
cd docs/IMPLEMENTATION_GUIDES
git mv p0t1-data-etl.md t1-data-etl.md
git mv p0t2-baseline-strategy.md t2-baseline-strategy-qlib.md
# ... etc
```

---

## Verification

After **all** migration steps complete, verify:

1. ✅ All files renamed (completed in this PR)
2. ⏳ No broken links (`grep -r "]\(.*\.md\)" docs/ | grep "t[1-6]-"`) - **Pending**
3. ⏳ INDEX.md updated - **Pending**
4. ⏳ PROJECT_STATUS.md updated - **Pending**
5. ✅ Git history preserved (use `git log --follow`) - Verified

---

**Status:** Phase 1 Complete (File Renames)
**Next Steps:**
1. Update all cross-references in markdown files (separate PR)
2. Update INDEX.md and REPO_MAP.md (separate PR)
3. Update PROJECT_STATUS.md (separate PR)
