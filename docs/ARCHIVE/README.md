# Archived Documentation

These documents have been deprecated as part of the task lifecycle system implementation (October 21, 2024).

## Archived Files

| File | Archived Date | Reason | Replacement |
|------|---------------|--------|-------------|
| **NEXT_TASK_20241021.md** | Oct 21, 2024 | Redundant with task files | `./scripts/tasks.py next` command |
| **P1_PROGRESS_20241021.md** | Oct 21, 2024 | Merged into P1_PLANNING.md | See docs/TASKS/P1_PLANNING.md |
| **FILE_RENAME_MAP_20241018.md** | Oct 18, 2024 | Historical reference (migration complete) | N/A - migration completed |

## Why Archived?

The new task lifecycle system (TASK → PROGRESS → DONE) provides:
- **Single source of truth** - No duplication across multiple documents
- **Automated status tracking** - Progress calculated from task files
- **CLI commands** - Query task status programmatically
- **Reduced maintenance** - Update once instead of 4+ documents

### Problems with Old System

**Evidence of issues:**
1. **Conflicting Progress Numbers** - P1_PLANNING: 73% (8/11) vs P1_PROGRESS: 62% (8/13) vs PROJECT_STATUS: 67% (4/6)
2. **Document Staleness** - PROJECT_STATUS 3 days out of date
3. **Massive Duplication** - Task summaries in 3 places
4. **High Maintenance** - Every task completion required updating 4 documents

## Migration Guide

### Old Workflow → New Workflow

**Check next task:**
```bash
# OLD: Read NEXT_TASK.md
cat docs/NEXT_TASK.md

# NEW: Use CLI
./scripts/tasks.py next
# Or: ./scripts/tasks.py list --state TASK --limit 1
```

**Check phase progress:**
```bash
# OLD: Read P1_PROGRESS.md
cat docs/GETTING_STARTED/P1_PROGRESS.md

# NEW: Read phase planning
cat docs/TASKS/P1_PLANNING.md
# Or: ./scripts/tasks.py list --phase P1
```

**View task completion details:**
```bash
# OLD: Search through P1_PROGRESS.md or PROJECT_STATUS.md
grep "Redis Integration" docs/GETTING_STARTED/P1_PROGRESS.md

# NEW: Read task DONE file directly
cat docs/TASKS/P1T1_DONE.md
```

**Update project status:**
```bash
# OLD: Manually edit 4 documents after each task
# - P1_PROGRESS.md
# - P1_PLANNING.md
# - PROJECT_STATUS.md
# - INDEX.md

# NEW: Auto-sync (future implementation)
./scripts/tasks.py sync-status
```

## Document Roles After Cleanup

| Document | Role | Update Frequency | Method |
|----------|------|------------------|--------|
| **P1_PLANNING.md** | Phase overview + progress tracking | After each P1 task | Manual (future: auto) |
| **P1Tx_DONE.md** | Detailed task completion info | Once when task completes | Manual |
| **PROJECT_STATUS.md** | High-level project health dashboard | Major milestones | Manual (simplified) |
| **INDEX.md** | Navigation hub | When docs added/removed | Manual |

## Accessing Archived Documents

These archived documents remain available for historical reference:

- **NEXT_TASK_20241021.md** - Last state before deprecation (P1.3T1 - Monitoring & Alerting)
- **P1_PROGRESS_20241021.md** - Detailed P1 progress as of Oct 20, 2024
- **FILE_RENAME_MAP_20241018.md** - Documentation reorganization history (Oct 18, 2024)

## Questions?

If you need information that was in these documents:
1. Check the replacement document/command listed above
2. Read the relevant task file (P1Tx_DONE.md)
3. Ask the team if you can't find what you need

---

**Archive Created:** October 21, 2024
**Cleanup PR:** feature/workflow-improvements
**Related Issue:** Task lifecycle system implementation
