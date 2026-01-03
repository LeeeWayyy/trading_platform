---
id: P0T0
title: "Task Title Here"
phase: P0
task: T0
priority: P0
owner: "@development-team"
state: PROGRESS
created: YYYY-MM-DD
started: YYYY-MM-DD
dependencies: []
estimated_effort: "X days"
related_adrs: []
related_docs: []
features: []
---

# P0T0: Task Title Here

**Phase:** P0 (MVP Core, 0-45 days)
**Status:** PROGRESS (In Development)
**Priority:** P0 (MVP)
**Owner:** @development-team
**Started:** YYYY-MM-DD

---

## Implementation Log

**Note:** This section tracks the 4-step pattern for EACH logical component:
1. Implement component
2. Create test cases
3. Request zen-mcp review
4. Commit after approval

### YYYY-MM-DD: Component Name (F0)

**Step 1: Implement**
- Implemented [description]
- Files modified:
  - `path/to/file.py:lines`
  - `path/to/another.py:lines`

**Step 2: Create Test Cases**
- Created tests in `tests/path/to/test_file.py`
- Coverage: X tests, Y% coverage
- Test status: ✅ All passing / ❌ N failing

**Step 3: Zen-MCP Review**
- Review requested: YYYY-MM-DD HH:MM
- Review type: Quick / Deep
- Continuation ID: [if applicable]
- **Findings:**
  - ✅ Approved / ⚠️ Issues found
  - [Summary of findings]
- **Fixes applied:**
  - [Description of fixes]
- **Re-review:** ✅ Approved

**Step 4: Commit**
- Committed: `git_hash_here`
- Message: "Brief commit message"

---

### YYYY-MM-DD: Another Component (F1)

**Step 1: Implement**
- ...

**Step 2: Create Test Cases**
- ...

**Step 3: Zen-MCP Review**
- ...

**Step 4: Commit**
- ...

---

## Active Work

**Current Component:** [Component name]
**Current Step:** [1-Implement / 2-Test / 3-Review / 4-Commit]

**Next Steps:**
- [ ] [What's immediately next]
- [ ] [What follows]

---

## Decisions Made

### Decision 1: [Title]
**Date:** YYYY-MM-DD
**Context:** [Why this decision was needed]
**Options Considered:**
1. Option A - [pros/cons]
2. Option B - [pros/cons]

**Decision:** [What was chosen and why]
**Consequences:** [Impact on code, architecture, etc.]
**ADR Created:** [ADR-XXX](../ADRs/0001-data-pipeline-architecture.md) (if architectural)

---

## Issues Encountered

### Issue 1: [Title]
**Date:** YYYY-MM-DD
**Severity:** Critical / High / Medium / Low
**Description:** [What went wrong]
**Root Cause:** [Why it happened]
**Solution:** [How it was fixed]
**Prevention:** [How to avoid in future]

---

## Blockers

### Blocker 1: [Title]
**Status:** 🔴 Active / 🟡 Workaround Found / 🟢 Resolved
**Blocking:** [What can't proceed]
**Waiting On:** [Person/Task/Decision]
**Workaround:** [Temporary solution if any]
**Resolution:** [How it was unblocked]

---

## Test Coverage

**Unit Tests:**
- Total: X tests
- Coverage: Y%
- Status: ✅ All passing / ❌ N failing

**Integration Tests:**
- Total: X scenarios
- Status: ✅ All passing / ❌ N failing

**Manual Tests:**
- [ ] Test scenario 1
- [ ] Test scenario 2

---

## Code References

**Key Files:**
- `apps/service/module.py:lines` - [What it does]
- `tests/test_module.py` - [Test coverage]

**API Changes:**
- Endpoint: `POST /api/endpoint`
- Spec: `docs/API/execution_gateway.openapi.yaml:lines`

**Database Changes:**
- Migration: `db/migrations/XXX_description.sql`
- Schema: `docs/DB/minimal_p0_schema.sql:lines`

---

## Documentation Updates

**Created:**
- [ ] ADR-XXX: [Title] (if architectural change)
- [ ] `/docs/CONCEPTS/corporate-actions.md` (if trading-specific)

**Updated:**
- [ ] `/docs/API/*.openapi.yaml` (if API changed)
- [ ] `/docs/DB/*.sql` (if schema changed)
- [ ] `/docs/GETTING_STARTED/REPO_MAP.md` (if structure changed)

---

## Related

**ADRs:**
- [ADR-XXX: Title](../ADRs/0001-data-pipeline-architecture.md)

**Tasks:**
- Depends on: [P0T1_DONE](../ARCHIVE/TASKS_HISTORY/P0T1_DONE.md)
- Blocks: [P0T1_DONE](../ARCHIVE/TASKS_HISTORY/P0T1_DONE.md)

**PRs:**
- [PR #123](https://github.com/LeeeWayyy/trading_platform/pull/123) - Description

**Commits:**
- `abc1234` - Component 1 implementation
- `def5678` - Component 2 implementation

---

## Notes

[Any additional context, observations, or things to remember]

---

## State Transition Instructions

**When completing this task:**

```bash
# 1. Ensure all components committed via 4-step pattern
# 2. Ensure all tests passing
# 3. Ensure all documentation updated

# 4. Rename file
git mv docs/TASKS/P0T0_PROGRESS.md docs/TASKS/P0T0_DONE.md

# 5. Update front matter in P0T0_DONE.md:
#    state: DONE
#    completed: YYYY-MM-DD
#    duration: "X days"

# 6. Commit
git add docs/TASKS/P0T0_DONE.md
git commit -m "Complete P0T0: Task Title"
```

**Or use automation:**
```bash
./scripts/tasks.py complete P0T0
```
