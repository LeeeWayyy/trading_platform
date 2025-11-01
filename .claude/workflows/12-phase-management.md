# Phase Management Workflow

**Purpose:** Manage project phases using the three-tier task lifecycle system
**Prerequisites:** Master plan exists, tasks CLI functional
**Expected Outcome:** Phase planning document with auto-generated task files
**Owner:** @development-team
**Last Reviewed:** 2025-10-20

---

## Quick Reference

**Git:** See [Git Commands Reference](./_common/git-commands.md)
**Testing:** See [Test Commands Reference](./_common/test-commands.md)

---

## When to Use This Workflow

- Starting a new project phase (P0, P1, P2)
- Breaking down a phase into individual tasks
- Creating task files from phase plan

**Three-Tier Architecture:**
```
Tier 1: Master Plan (Reference) → docs/PLANNING/trading_platform_realization_plan.md
Tier 2: Phase Planning (Living) → docs/TASKS/Px_PLANNING.md
Tier 3: Individual Tasks → PxTy_TASK.md → PxTy_PROGRESS.md → PxTy_DONE.md
```

---

## Step-by-Step Process

### 1. Create Phase Planning

```bash
./scripts/tasks.py create-phase P2 --source docs/PLANNING/trading_platform_realization_plan.md
```

Creates `docs/TASKS/P2_PLANNING.md` with template.

### 2. Fill in Planning Details

Edit `P2_PLANNING.md` with:
- Task breakdown with estimates
- Success criteria
- Dependencies and risks

**Task Header Format:**
```markdown
#### T0: Core Feature ⭐ HIGH PRIORITY
#### T1.1: Sub-feature 🔶 MEDIUM PRIORITY
```

### 3. Generate Task Files

```bash
# Preview (dry run)
./scripts/tasks.py generate-tasks-from-phase P2 --dry-run

# Generate tasks
./scripts/tasks.py generate-tasks-from-phase P2
```

### 4. (Optional) Review Task Before Starting

For complex tasks (>4 hours), use [13-task-creation-review.md](./13-task-creation-review.md).

### 5. Start and Track Task

```bash
# Start task
./scripts/tasks.py start P2T0

# Follow 4-step pattern per component (see 01-git-commit.md)

# Complete task
./scripts/tasks.py complete P2T0
```

### 6. Update Documentation

After completing task, update:
- `Px_PLANNING.md` - Progress, completed tasks
- `docs/TASKS/INDEX.md` - Status tables
- `docs/GETTING_STARTED/PROJECT_STATUS.md` (if milestone)

---

## Common Issues

### Task Headers Not Detected

**Correct format:**
```markdown
#### T0: Task Title
#### T1.1: Feature Title
```

**Incorrect:**
```markdown
## T0: Task Title  (only 2 hashes)
#### Task 0: Title  (missing T prefix)
```

### All Tasks Already Exist

```bash
# Check status
./scripts/tasks.py list --phase P2

# Verify files
ls docs/TASKS/P2T*
```

---

## Validation

**How to verify:**
- [ ] `Px_PLANNING.md` exists in `docs/TASKS/`
- [ ] Task headers parsed correctly (check dry-run)
- [ ] Task files created with correct naming
- [ ] Front matter includes all required fields
- [ ] No duplicate task files

---

## Related Workflows

- [01-git-commit.md](./01-git-commit.md) - 4-step pattern during task implementation
- [13-task-creation-review.md](./13-task-creation-review.md) - Task review before starting

---

## References

- [/docs/STANDARDS/GIT_WORKFLOW.md](../../docs/STANDARDS/GIT_WORKFLOW.md) - Task lifecycle
- [/docs/TASKS/INDEX.md](../../docs/TASKS/INDEX.md) - Task tracking dashboard
- [/scripts/tasks.py](../../scripts/tasks.py) - Task CLI implementation
