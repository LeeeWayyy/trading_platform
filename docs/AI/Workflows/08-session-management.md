# Session Management Workflow

**Purpose:** Resume tasks across sessions and manage task state transitions.

**When to Use:**
- Starting a new Claude Code session with incomplete work
- Context limit reached mid-task
- Multi-day development work
- Updating task progress

---

## 🤖 Auto-Resume: Check for Incomplete Work

**At session start, check for incomplete tasks:**

```bash
# Check task state
jq '.current_task.state, .progress.completion_percentage' .claude/task-state.json

# If state is "IN_PROGRESS" or "PENDING", load context:
if [ -L .claude/checkpoints/latest_session_end.json ]; then
  CHECKPOINT_ID=$(basename $(readlink .claude/checkpoints/latest_session_end.json) .json)
  ./scripts/context_checkpoint.py restore --id $CHECKPOINT_ID
fi

# Read task details
TASK_ID=$(jq -r '.current_task.task_id' .claude/task-state.json)
BRANCH=$(jq -r '.current_task.branch_name' .claude/task-state.json)

# Display current task
cat docs/TASKS/${TASK_ID}.md
```

**Context restored:** Task ID, branch, completed components, pending work.

---

## 📝 Update Task State

Use `scripts/update_task_state.py` to track progress:

### Start New Task

```bash
./scripts/update_task_state.py start \
  --task P1T13-F4 \
  --branch feature/P1T13-F4-workflow-intelligence \
  --components 6
```

Creates `.claude/task-state.json` with initial state.

### Mark Component Complete

```bash
./scripts/update_task_state.py complete-component \
  --component 1 \
  --name "Git utilities foundation"
```

Updates completion percentage automatically.

### Finish Task

```bash
./scripts/update_task_state.py finish
```

Marks task as COMPLETE, archives state.

### Pause Task (Session End)

```bash
./scripts/update_task_state.py pause
```

Preserves state for next session (auto-resume).

---

## 🔄 Common Scenarios

### Scenario 1: Resume After Context Limit

```bash
# 1. Check state
jq '.' .claude/task-state.json

# 2. Restore checkpoint
./scripts/context_checkpoint.py restore --id <checkpoint_id>

# 3. Continue work from last component
```

### Scenario 2: Resume After Multi-Day Break

```bash
# 1. Check what was being worked on
jq '.current_task, .progress' .claude/task-state.json

# 2. Review task document
cat docs/TASKS/$(jq -r '.current_task.task_id' .claude/task-state.json).md

# 3. Check git status
git status
git log --oneline -5

# 4. Continue from where you left off
```

### Scenario 3: Update Progress Mid-Task

```bash
# After completing component 3
./scripts/update_task_state.py complete-component --component 3 --name "DelegationRules"

# Check updated state
jq '.progress' .claude/task-state.json
```

---

## 📊 Task State Schema

```json
{
  "current_task": {
    "task_id": "P1T13-F4",
    "branch_name": "feature/P1T13-F4-workflow-intelligence",
    "state": "IN_PROGRESS",  // PENDING | IN_PROGRESS | COMPLETE
    "started_at": "2025-11-08T10:00:00Z"
  },
  "progress": {
    "total_components": 6,
    "completed_components": 3,
    "completion_percentage": 50,
    "components": [
      {"id": 1, "name": "Git utilities", "status": "COMPLETE"},
      {"id": 2, "name": "SmartTestRunner", "status": "COMPLETE"},
      {"id": 3, "name": "DelegationRules", "status": "COMPLETE"},
      {"id": 4, "name": "UnifiedReviewSystem", "status": "IN_PROGRESS"}
    ]
  }
}
```

---

## 🎯 Integration with Workflow Gates

Task state integrates with `workflow_gate.py`:

```bash
# After completing component, workflow gate resets
git commit -m "feat: Component 3 complete"
# → workflow_gate.py auto-resets to 'implement' step
# → ready for next component

# Update task state to track completion
./scripts/update_task_state.py complete-component --component 3 --name "DelegationRules"
```

**See Also:**
- `./12-component-cycle.md` - 4-step pattern
- `.claude/checkpoints/README.md` - Context checkpoint details
- `.claude/AUTO_RESUME.md` - Auto-resume system overview
