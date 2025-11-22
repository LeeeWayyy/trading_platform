# Planning & Task Management Workflow

**Purpose:** Manage project phases, break down tasks, and validate scope before implementation.

**When to Use:**
- Starting a new project phase (P0, P1, P2)
- Creating task tickets from phase plans
- Breaking down complex tasks (>8h) into subfeatures
- Validating task documents before work starts

---

## Three-Tier Architecture

```
Tier 1: Master Plan (Reference) → docs/trading_platform_realization_plan.md
Tier 2: Phase Planning (Living) → docs/TASKS/Px_PLANNING.md
Tier 3: Individual Tasks → PxTy_TASK.md → PxTy_PROGRESS.md → PxTy_DONE.md
```

---

## 1. Phase Management

**Create new phase:**
```bash
./scripts/tasks.py create-phase P2 --source docs/trading_platform_realization_plan.md
```

Creates `docs/TASKS/P2_PLANNING.md` template.

**Fill in planning details:**
- Task breakdown with time estimates
- Success criteria per task
- Dependencies and risks

**Generate task files:**
```bash
./scripts/tasks.py generate-tasks P2
```

Creates individual `PxTy_TASK.md` files from `P2_PLANNING.md`.

---

## 2. Task Naming Convention

**Pattern:** `PxTy-Fz` where:
- **Px** = Phase (P0, P1, P2)
- **Ty** = Task number (T1, T2, T11)
- **Fz** = Subfeature (F1, F2, F3) - optional for complex tasks

**Branch naming:** `<type>/PxTy(-Fz)?-<description>`

**Examples:**
```bash
# Simple task
feature/P1T11-workflow-optimization

# Complex task with subfeatures
feature/P1T11-F1-tool-restriction
feature/P1T11-F2-delegation-rules
feature/P1T11-F3-context-monitoring
```

---

## 3. When to Use Subfeatures

**Decision tree:**

| Condition | Action |
|-----------|--------|
| Task < 4 hours | Single branch, no subfeatures |
| Task 4-8 hours | Single branch, use 4-step pattern per component |
| Task > 8 hours | **RECOMMENDED:** Split into PxTy-F1, F2, F3 subfeatures |

**Subfeature branching benefits:**
- Progressive PRs (smaller, easier to review)
- Independent merge of completed components
- Clearer git history

**When NOT to split:**
- Components are tightly coupled
- Requires one atomic merge
- Simple refactoring

---

## 4. Task Creation Review (RECOMMENDED)

**Purpose:** Validate task scope and requirements before implementation.

**When to use:**
- Complex tasks (>4 hours)
- Architectural changes
- Unclear requirements
- New feature development

**Skip for:**
- Simple bug fixes (<2 hours)
- Documentation-only updates
- Routine maintenance

### Review Process (2-Phase: Gemini → Codex)

**Phase 1: Gemini Planning Review**
```bash
# Use clink with gemini planner
mcp__zen__clink(
    prompt="""Review task document for:
1. Scope appropriateness (not too large/small)
2. Requirement completeness
3. Implementation feasibility
4. Risk assessment

Task: docs/TASKS/P1T15_TASK.md""",
    cli_name="gemini",
    role="planner",
    absolute_file_paths=["docs/TASKS/P1T15_TASK.md"]
)
# Save continuation_id for Phase 2
```

**Phase 2: Codex Validation Review**
```bash
# Use clink with codex to synthesize recommendations
mcp__zen__clink(
    prompt="""Gemini has reviewed task plan. Validate feasibility and provide final recommendations.""",
    cli_name="codex",
    role="planner",
    continuation_id="<from_phase_1>",
    absolute_file_paths=["docs/TASKS/P1T15_TASK.md"]
)
```

**Address findings:**
- Fix scope issues (split/merge tasks)
- Clarify requirements
- Document risks
- Update implementation approach

**Benefits:** 2-3 minutes → Saves hours of rework!

---

## 5. Task Lifecycle States

| File | State | Description |
|------|-------|-------------|
| `PxTy_TASK.md` | Planning | Initial task definition |
| `PxTy_PROGRESS.md` | In Progress | Active implementation tracking |
| `PxTy_DONE.md` | Complete | Retrospective + lessons learned |

**State transitions:**
```bash
# Start task
git checkout -b feature/P1T15-api-optimization
cp docs/TASKS/P1T15_TASK.md docs/TASKS/P1T15_PROGRESS.md

# Complete task
mv docs/TASKS/P1T15_PROGRESS.md docs/TASKS/P1T15_DONE.md
# Add retrospective notes, lessons learned
```

---

## 6. Planning with workflow_gate.py

**Coming soon:** Automated task planning via workflow_gate.py:

```bash
# Create and review task (automated)
./scripts/workflow_gate.py create-task --id P1T14 --title "..." --hours 6
# → Creates task doc
# → Auto-requests gemini + codex planning review
# → Guides through review findings

# Plan subfeatures (if > 8h)
./scripts/workflow_gate.py plan-subfeatures P1T14 \
    --component "Position monitor:3h" \
    --component "Alert integration:2h" \
    --component "Dashboard UI:3h"
# → Detects 8h total → recommends split
# → Generates P1T14-F1, P1T14-F2, P1T14-F3

# Start task
./scripts/workflow_gate.py start-task P1T14
# → Creates branch
# → Initializes task-state.json
# → Sets first component
```

---

## Example: Complex Task Breakdown

**Scenario:** P1T13-F4 (Workflow Intelligence, 12-16h estimate)

**Decomposition:**
1. **Analyze task document** → 6 components identified
2. **Decision:** Single branch (components independent but related)
3. **Task creation review:**
   - Gemini: Validated scope, identified edge cases
   - Codex: Approved feasibility, recommended atomic implementation
4. **Implementation:**
   - Used 4-step pattern per component (12-component-cycle.md)
   - Progressive commits (9 total over 5 components)
5. **Result:** Clean history, easy to review

**Alternative if >20h:** Could split into P1T13-F4a (infrastructure), P1T13-F4b (reviews), P1T13-F4c (simplification)

---

## See Also

- `./12-component-cycle.md` - 4-step pattern for each component
- `./01-git.md` - Git commits & pull requests
- `./08-session-management.md` - Task state tracking
