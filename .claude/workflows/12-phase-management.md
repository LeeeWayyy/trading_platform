# Phase Management Workflow

**Purpose:** Manage project phases from planning through execution using the three-tier task lifecycle system
**Prerequisites:** Master plan exists (docs/PLANNING/trading_platform_realization_plan.md), tasks CLI is functional
**Expected Outcome:** Structured phase planning document with auto-generated task files ready for execution
**Owner:** @development-team
**Last Reviewed:** 2025-10-20

---

## When to Use This Workflow

Use this workflow when:
- Starting a new project phase (P0, P1, P2)
- Breaking down a phase into individual tasks
- Need to track phase-level progress and metrics
- Creating task files from a comprehensive phase plan

**Three-Tier Architecture:**
```
Tier 1: Master Plan (Reference Only)
└── docs/PLANNING/trading_platform_realization_plan.md

Tier 2: Phase Planning (Living Reference)
└── docs/TASKS/Px_PLANNING.md

Tier 3: Individual Tasks (Full Lifecycle)
└── PxTy_TASK.md → PxTy_PROGRESS.md → PxTy_DONE.md
```

---

## Step-by-Step Process

### 1. Create Phase Planning Document

Generate a new phase planning document from template:

```bash
./scripts/tasks.py create-phase P2 \
  --source docs/PLANNING/trading_platform_realization_plan.md
```

**What this does:** Creates `docs/TASKS/P2_PLANNING.md` with structured template including progress tracking, task breakdown, success metrics, and risk mitigation.

### 2. Fill in Phase Planning Details

Edit the generated `Px_PLANNING.md` to add:
- Executive summary of phase goals
- Previous phase transition analysis
- Task breakdown with estimates
- Success criteria and metrics
- Testing strategy
- Dependencies and risks

**Task Header Format:**
```markdown
#### T0: Core Feature Implementation ⭐ HIGH PRIORITY
#### T1.1: Data Layer Integration 🔶 MEDIUM PRIORITY
#### T1.2: API Endpoints 🔷 LOW PRIORITY
```

**Naming Convention:**
- `#### T0:` → Creates `P2T0_TASK.md` (standalone task)
- `#### T1.1:` → Creates `P2T1-F1_PROGRESS.md` (feature 1 within task 1)
- `#### T1.2:` → Creates `P2T1-F2_PROGRESS.md` (feature 2 within task 1)

### 3. Preview Task Generation (Dry Run)

Check what tasks will be created without creating files:

```bash
./scripts/tasks.py generate-tasks-from-phase P2 --dry-run
```

**Output example:**
```
📋 Found 5 task(s) to create:
   ✅ Would create: P2T0 - Core Feature Implementation ⭐ HIGH PRIORITY
   ✅ Would create: P2T1-F1 - Data Layer Integration 🔶 MEDIUM PRIORITY
   ✅ Would create: P2T1-F2 - API Endpoints 🔷 LOW PRIORITY
```

### 4. Generate Task Files

Create all task files from phase planning:

```bash
./scripts/tasks.py generate-tasks-from-phase P2
```

**What this does:**
- Parses all `#### T*:` headers from `P2_PLANNING.md`
- Creates `PxTy_TASK.md` for standalone tasks (e.g., T0)
- Creates `PxTy-Fz_PROGRESS.md` for features (e.g., T1.1 → P2T1-F1)
- Skips tasks that already exist
- Reports creation count and next steps

### 5. Start Working on First Task

Transition first task from TASK to PROGRESS:

```bash
./scripts/tasks.py start P2T0
```

**What this does:**
- Renames `P2T0_TASK.md` → `P2T0_PROGRESS.md` (preserves git history)
- Updates front matter: `state: PROGRESS`, `started: YYYY-MM-DD`
- Prints next steps (edit file, follow 4-step pattern, complete command)

### 6. Track Implementation Using 4-Step Pattern

For each logical component in the task, create 4 todos:
1. Implement component
2. Create test cases
3. Request zen-mcp review
4. Commit after approval

See [01-git-commit.md](./01-git-commit.md) for detailed 4-step pattern workflow.

### 7. Complete Task

When task is finished, transition to DONE:

```bash
./scripts/tasks.py complete P2T0
```

**What this does:**
- Renames `P2T0_PROGRESS.md` → `P2T0_DONE.md`
- Updates front matter: `state: DONE`, `completed: YYYY-MM-DD`, `duration: X days`
- Prompts to document completion details

### 8. Update Documentation After Task Completion

**MANDATORY:** After completing each task, update these documents:

#### A. Update Phase Planning Document (Px_PLANNING.md)

Edit `P2_PLANNING.md` to update:
- Progress summary (X/Y tasks complete, Z%)
- Track-based progress tables
- Mark task as completed in "Completed" section
- Update "Next" task pointer
- Update "Last Updated" date

```markdown
**Completed:**
- ✅ P2T0 - Core Feature Implementation (Oct 20, PR#25)
- ✅ P2T1 - Data Layer Integration (Oct 21, PR#26)

**Next:** P2T2 - API Endpoints (3-5 days)

**Last Updated:** October 21, 2024
```

#### B. Update Task Index (docs/TASKS/INDEX.md)

Update the relevant phase section:

1. **Move task from "Remaining" to "Completed":**
   ```markdown
   **Completed Tasks (8):**
   - [P2T0_DONE.md](./P2T0_DONE.md) - Core Feature Implementation

   **Remaining Tasks (4 tasks not started):**
   - T1: Data Layer Integration
   ```

2. **Update quick status table at top:**
   ```markdown
   | Phase | Tasks | TASK | PROGRESS | DONE |
   | P2    | 12    | 8    | 1        | 3    |
   ```

3. **Update phase overview progress:**
   ```markdown
   ### P2: Advanced Features (91-120 days)
   **Status:** 3/12 tasks complete (25%)
   ```

#### C. Update Project Status (Optional but Recommended)

If significant milestone reached, update `docs/GETTING_STARTED/PROJECT_STATUS.md`:
- Current phase progress
- Recent accomplishments
- Blockers or risks

**Automation coming soon:** `./scripts/tasks.py sync-status` will auto-generate these updates

### 9. Monitor Phase Status

Check overall phase progress:

```bash
./scripts/tasks.py list --phase P2
./scripts/tasks.py sync-status
```

---

## Decision Points

**If planning document already exists:**
- **Option A: Append new tasks**
  - Manually edit `Px_PLANNING.md` to add new task headers
  - Run `generate-tasks-from-phase` again (skips existing tasks)
  - Example: Adding T3 to existing P2 plan with T0-T2

- **Option B: Create sub-phase**
  - Create feature tasks (T1.1, T1.2) under existing task
  - Run `generate-tasks-from-phase` to create feature files
  - Example: Breaking down T1 into 3 features after realizing complexity

**If all tasks already exist:**
- **Normal scenario:** Command reports "No new tasks found"
  - Check status: `./scripts/tasks.py list --phase P2`
  - Verify files manually: `ls docs/TASKS/P2T*`
  - Example: Re-running after successful generation

---

## Common Issues & Solutions

### Issue: Task Headers Not Detected

**Symptom:** `generate-tasks-from-phase` reports "No new tasks found" despite having task headers

**Cause:** Header format doesn't match regex pattern `r"^#{3,4}\s+(T\d+(?:\.\d+)?)[:\s]+(.+)$"`

**Solution:**
```markdown
✅ Correct formats:
#### T0: Task Title
#### T1.1: Feature Title
###  T2: Task Title (3 or 4 hashes)

❌ Incorrect formats:
## T0: Task Title (only 2 hashes)
#### Task 0: Title (missing T prefix)
#### T1: (missing title)
```

### Issue: IndexError When All Tasks Exist

**Symptom:** Error accessing empty list after filtering out existing tasks

**Cause:** All detected tasks already have files (filtered by `get_task_file()`)

**Solution:** Fixed in commit ad5ff2d. Command now checks list length before accessing:
```python
if tasks_to_create:
    print(f"Start first task: {tasks_to_create[0][0]}")
else:
    print(f"All tasks exist, check status: ./scripts/tasks.py list --phase {phase}")
```

---

## Examples

### Example 1: Starting New Phase from Master Plan

```bash
# Step 1: Create phase planning
./scripts/tasks.py create-phase P2 \
  --source docs/PLANNING/trading_platform_realization_plan.md

# Step 2: Edit P2_PLANNING.md to add task details
# (Add task headers: #### T0:, #### T1.1:, etc.)

# Step 3: Preview what will be created
./scripts/tasks.py generate-tasks-from-phase P2 --dry-run

# Step 4: Generate task files
./scripts/tasks.py generate-tasks-from-phase P2

# Step 5: Start first task
./scripts/tasks.py start P2T0
```

**Result:** `P2_PLANNING.md` created, 5 task files generated, first task ready for implementation.

### Example 2: Adding Features to Existing Task

```bash
# Scenario: P1T1 is complex, needs to be broken into features

# Step 1: Edit P1_PLANNING.md to add feature headers
#### T1.1: Redis Client Implementation
#### T1.2: Cache Layer with TTL
#### T1.3: Pub/Sub Event Bus

# Step 2: Generate feature files
./scripts/tasks.py generate-tasks-from-phase P1

# Result: Creates P1T1-F1, P1T1-F2, P1T1-F3 as PROGRESS files
```

**Result:** 3 feature files created in PROGRESS state, ready to track sub-component implementation.

### Example 3: Checking Phase Status

```bash
# List all P1 tasks
./scripts/tasks.py list --phase P1

# List only active P1 tasks
./scripts/tasks.py list --phase P1 --state PROGRESS

# View overall progress across all phases
./scripts/tasks.py sync-status
```

**Result:** Table showing phase breakdown and completion percentages.

---

## Validation

**How to verify this workflow succeeded:**
- [ ] `Px_PLANNING.md` exists in `docs/TASKS/`
- [ ] All task headers parsed correctly (check dry-run output)
- [ ] Task files created with correct naming (PxTy vs PxTy-Fz)
- [ ] Features use TEMPLATE_FEATURE, tasks use TEMPLATE_TASK
- [ ] Front matter includes all required fields (id, title, phase, state, dates)
- [ ] No duplicate task files (single state per task ID)

**What to check if something seems wrong:**
- Run `./scripts/tasks.py lint` to validate all task files
- Check `git status` to see which files were created
- Verify task headers match regex: `^#{3,4}\s+(T\d+(?:\.\d+)?)[:\s]+(.+)$`
- Confirm templates exist: `ls docs/TASKS/00-TEMPLATE_*.md`

---

## Related Workflows

- [01-git-commit.md](./01-git-commit.md) - Progressive commits with 4-step pattern during task implementation
- [03-zen-review-quick.md](./03-zen-review-quick.md) - Quick review before each component commit
- [04-zen-review-deep.md](./04-zen-review-deep.md) - Deep review before marking task as DONE
- [05-testing.md](./05-testing.md) - Test creation for each task component

---

## References

**Standards & Policies:**
- [/docs/STANDARDS/GIT_WORKFLOW.md](/docs/STANDARDS/GIT_WORKFLOW.md) - Task lifecycle and naming conventions

**Architecture Decisions:**
- Zen-MCP Codex Planner Recommendation (continuation_id: from conversation history)
  - Decision: Keep Px_PLANNING.md as living reference throughout phase
  - Rationale: Provides phase context that individual tasks lack, enables phase-level tracking

**Additional Resources:**
- [/docs/TASKS/INDEX.md](/docs/TASKS/INDEX.md) - Task tracking dashboard
- [/docs/TASKS/00-TEMPLATE_PHASE_PLANNING.md](/docs/TASKS/00-TEMPLATE_PHASE_PLANNING.md) - Phase planning template
- [/docs/TASKS/00-TEMPLATE_TASK.md](/docs/TASKS/00-TEMPLATE_TASK.md) - Task template
- [/docs/TASKS/00-TEMPLATE_FEATURE.md](/docs/TASKS/00-TEMPLATE_FEATURE.md) - Feature template
- [/scripts/tasks.py](/scripts/tasks.py) - Task lifecycle CLI implementation

**CLI Help:**
```bash
./scripts/tasks.py --help
./scripts/tasks.py create-phase --help
./scripts/tasks.py generate-tasks-from-phase --help
```

---

**Maintenance Notes:**
- Update this workflow when task CLI commands change
- Review frequency: After each phase completion
- Notify @development-team if task naming conventions change
- Update regex pattern documentation if parsing logic changes
