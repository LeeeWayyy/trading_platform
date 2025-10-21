---
id: P0T0
title: "Task Title Here"
phase: P0
task: T0
priority: P0
owner: "@development-team"
state: TASK
created: YYYY-MM-DD
dependencies: []
estimated_effort: "X days"
related_adrs: []
related_docs: []
features: []
---

# P0T0: Task Title Here

**Phase:** P0 (MVP Core, 0-45 days)
**Status:** TASK (Not Started)
**Priority:** P0 (MVP)
**Owner:** @development-team
**Created:** YYYY-MM-DD
**Estimated Effort:** X days

---

## Naming Convention

**This task:** `P0T1_DONE.md` → `P0T0_PROGRESS.md` → `P0T0_DONE.md`

**If this task has multiple features/sub-components:**
- Feature 0: `P0T0-F0_PROGRESS.md` (separate tracking for complex features)
- Feature 1: `P0T0-F1_PROGRESS.md`

**Where:**
- **Px** = Phase (P0 = MVP/0-45 days, P1 = Hardening/46-90 days, P2 = Advanced/91-120 days)
- **Ty** = Task number within phase (T0, T1, T2, ...)
- **Fz** = Feature/sub-component within task (F0, F1, F2, ...)

---

## Objective

[Clear, concise statement of what this task aims to achieve]

**Success looks like:**
- [Measurable outcome 1]
- [Measurable outcome 2]

---

## Acceptance Criteria

- [ ] **AC1:** [Specific, testable criterion]
- [ ] **AC2:** [Specific, testable criterion]
- [ ] **AC3:** [Specific, testable criterion]

---

## Approach

### High-Level Plan

1. **Step 1:** [What needs to be done]
2. **Step 2:** [What needs to be done]
3. **Step 3:** [What needs to be done]

### Logical Components

Break this task into components, each following the 4-step pattern:

**Component 1: [Name]**
- Implement [description]
- Create test cases for [description]
- Request zen-mcp review
- Commit after approval

**Component 2: [Name]**
- Implement [description]
- Create test cases for [description]
- Request zen-mcp review
- Commit after approval

---

## Technical Details

### Files to Modify/Create
- `path/to/file.py` - [Why and what changes]
- `path/to/test.py` - [Test coverage needed]

### APIs/Contracts
- [Any API changes or new endpoints]
- [OpenAPI spec updates needed]

### Database Changes
- [Schema changes, migrations needed]
- [Data model updates]

---

## Dependencies

**Blockers (must complete before starting):**
- P0T-1: [Task name and why it's blocking]

**Nice-to-have (can start without):**
- P0T-2: [Task name and why it helps]

**Blocks (other tasks waiting on this):**
- P0T1: [Task name and what it provides]

---

## Risks & Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| [Risk description] | High/Med/Low | High/Med/Low | [How to mitigate] |

---

## Testing Strategy

### Test Coverage Needed
- **Unit tests:** [What to test]
- **Integration tests:** [What to test]
- **E2E tests:** [What scenarios]

### Manual Testing
- [ ] Test case 1
- [ ] Test case 2

---

## Documentation Requirements

### Must Create/Update
- [ ] ADR if architectural change (`.claude/workflows/08-adr-creation.md`)
- [ ] Concept doc in `/docs/CONCEPTS/` if trading-specific
- [ ] API spec in `/docs/API/` if endpoint changes
- [ ] Database schema in `/docs/DB/` if schema changes

### Must Update
- [ ] `/docs/GETTING_STARTED/REPO_MAP.md` if structure changes
- [ ] `/docs/GETTING_STARTED/PROJECT_STATUS.md` when complete

---

## Related

**ADRs:**
- [ADR-XXX: Title](../ADRs/0001-data-pipeline-architecture.md)

**Documentation:**
- [Related concept](../CONCEPTS/corporate-actions.md)

**Tasks:**
- Depends on: [P0T1](./P0T1_DONE.md)
- Blocks: [P0T2](./P0T2_DONE.md)

---

## Notes

[Any additional context, links, or considerations]

---

## State Transition Instructions

**When starting this task:**

```bash
# 1. Rename file
git mv docs/TASKS/P0T1_DONE.md docs/TASKS/P0T0_PROGRESS.md

# 2. Update front matter in P0T0_PROGRESS.md:
#    state: PROGRESS
#    started: YYYY-MM-DD

# 3. Commit
git add docs/TASKS/P0T0_PROGRESS.md
git commit -m "Start P0T0: Task Title"
```

**Or use automation:**
```bash
./scripts/tasks.py start P0T0
```
