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
# Check workflow state
PYTHONPATH=scripts:. python3 scripts/admin/workflow_gate.py status

# Read task details
TASK_ID=$(jq -r '.current_task.task_id' .ai_workflow/workflow-state.json)
BRANCH=$(jq -r '.current_task.branch_name' .ai_workflow/workflow-state.json)

# Display current task
cat docs/TASKS/${TASK_ID}.md
```

**Context restored:** Task ID, branch, completed components, pending work.

---

## 📝 Track Task State

Use `workflow_gate.py` to track progress:

```bash
# Start a new task
PYTHONPATH=scripts:. python3 scripts/admin/workflow_gate.py start-task docs/TASKS/P1T14_TASK.md feature/P1T14-task-branch

# Set current component
PYTHONPATH=scripts:. python3 scripts/admin/workflow_gate.py set-component "Component-Name"

# Check status
PYTHONPATH=scripts:. python3 scripts/admin/workflow_gate.py status

# After completing a component, record commit resets to plan for next component
PYTHONPATH=scripts:. python3 scripts/admin/workflow_gate.py record-commit --hash $(git rev-parse HEAD)
```

---

## 🔄 Common Scenarios

### Scenario 1: Resume After Multi-Day Break

```bash
# 1. Check what was being worked on
jq '.current_task, .progress' .ai_workflow/workflow-state.json

# 2. Review task document
cat docs/TASKS/$(jq -r '.current_task.task_id' .ai_workflow/workflow-state.json).md

# 3. Check git status
git status
git log --oneline -5

# 4. Continue from where you left off
```

### Scenario 2: Check Progress Mid-Task

```bash
# Check current workflow state
PYTHONPATH=scripts:. python3 scripts/admin/workflow_gate.py status
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

# Check workflow status
PYTHONPATH=scripts:. python3 scripts/admin/workflow_gate.py status
```

**See Also:**
- `./12-component-cycle.md` - 6-step pattern
