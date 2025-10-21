---
id: P0T0-F0
title: "Feature/Sub-Component Title"
parent_task: P0T0
phase: P0
task: T0
feature: F0
priority: P0
owner: "@development-team"
state: PROGRESS
created: YYYY-MM-DD
started: YYYY-MM-DD
estimated_effort: "X hours"
---

# P0T0-F0: Feature Title

**Parent Task:** [P0T1](./P0T1_DONE.md)
**Phase:** P0 (MVP Core)
**Status:** PROGRESS (In Development)
**Owner:** @development-team

---

## Purpose

**Why this feature exists as a separate sub-component:**
[Explain why this needed separate tracking from the parent task]

**What this feature delivers:**
[Brief description of the specific functionality]

**How it fits into parent task:**
[Relationship to the overall task goal]

---

## 4-Step Implementation Progress

### Step 1: Implement ⏳ In Progress / ✅ Complete
**Status:** [Current status]
**Started:** YYYY-MM-DD

**Implementation Details:**
- [What was implemented]
- [Key decisions made]

**Files Modified:**
- `path/to/file.py:lines` - [What changed]
- `path/to/another.py:lines` - [What changed]

**Code Highlights:**
```python
# Key implementation snippet (if helpful)
def important_function():
    ...
```

---

### Step 2: Create Test Cases ⏹️ Not Started / ⏳ In Progress / ✅ Complete
**Status:** [Current status]

**Test Files:**
- `tests/path/to/test_file.py` - [Coverage description]

**Test Coverage:**
- Unit tests: X tests
- Coverage: Y%
- Status: ✅ All passing / ❌ N failing

**Test Cases:**
- ✅ Happy path: [Description]
- ✅ Edge case 1: [Description]
- ✅ Error case: [Description]

---

### Step 3: Request Zen-MCP Review ⏹️ Not Started / ⏳ In Progress / ✅ Complete
**Status:** [Current status]

**Review Type:** Quick / Deep
**Requested:** YYYY-MM-DD HH:MM
**Continuation ID:** [if applicable]

**Findings:**
- [Summary of review findings]

**Issues to Fix:**
- ⚠️ Issue 1: [Description] → Status: Fixed / In Progress
- ⚠️ Issue 2: [Description] → Status: Fixed / In Progress

**Re-review:**
- Status: ✅ Approved / ⚠️ Pending

---

### Step 4: Commit ⏹️ Not Started / ✅ Complete
**Status:** [Current status]

**Commit:** `abc1234`
**Message:** "Brief description of what was committed"

**Files in Commit:**
- Implementation: `path/to/file.py`
- Tests: `tests/path/to/test_file.py`
- Docs: `docs/path/to/doc.md` (if applicable)

---

## Decisions & Trade-offs

### Decision 1: [Title]
**Context:** [Why decision was needed]
**Options:**
1. Option A - [Pros/cons]
2. Option B - [Pros/cons]

**Chosen:** [What was chosen and why]
**Impact:** [Consequences]

---

## Issues Encountered

### Issue 1: [Title]
**Severity:** Critical / High / Medium / Low
**Description:** [What went wrong]
**Solution:** [How it was fixed]

---

## Integration with Parent Task

**Depends on features:**
- [P0T3-F4](./P0T3-F4_DONE.md) - Example dependency

**Blocks features:**
- P0T0-F1 - [Why]

**Integrates with:**
- [Description of how this fits with other features]

---

## Code References

**Key Files:**
- `apps/service/module.py:lines` - [Purpose]
- `tests/test_module.py:lines` - [Coverage]

**Interfaces:**
- Functions exposed: `function_name()`, `another_function()`
- Classes: `ClassName`
- APIs: `POST /api/endpoint`

---

## Testing

**Unit Tests:**
```bash
pytest tests/path/to/test_file.py -v
```

**Manual Testing:**
```bash
# How to test this feature manually
command here
```

**Expected Behavior:**
- Input: [Example input]
- Output: [Expected output]

---

## Notes

[Any additional context, gotchas, or future improvements]

---

## State Transition

**When this feature is complete:**

```bash
# Option 1: Merge back into parent PROGRESS file
# Copy the completed 4-step implementation to P0T0_PROGRESS.md
# Delete this file

# Option 2: Keep as DONE record (if significant)
git mv docs/TASKS/P0T0-F0_PROGRESS.md docs/TASKS/P0T0-F0_DONE.md
# Update front matter: state: DONE, completed: YYYY-MM-DD

# Then update parent task
# Update P0T0_PROGRESS.md to mark this feature complete
```

**Feature lifecycle decision:**
- **Simple feature:** Merge back into parent PROGRESS, delete this file
- **Complex feature:** Keep as F0_DONE for historical reference
