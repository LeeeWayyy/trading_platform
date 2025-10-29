# Task Resume Workflow

**Purpose:** Automatically resume incomplete tasks across Claude Code sessions without losing context.

**When to Use:**
- Starting a new Claude Code session
- Context limit reached mid-task
- Multi-day development work
- Returning after interruption

**How it Works:**
- `.claude/task-state.json` tracks current task progress in real-time
- This workflow reads state and reconstructs context automatically
- No manual handoff needed - fully automated

---

## 🤖 Automated Resume (Use This!)

**IMPORTANT:** This workflow should run **AUTOMATICALLY** at the start of each session when `.claude/task-state.json` exists and shows incomplete work.

### Step 1: Check for Incomplete Work

```bash
# Check if task state file exists and has incomplete work
cat .claude/task-state.json | jq '.current_task.state, .progress.completion_percentage'
```

**If state is "IN_PROGRESS" or "PENDING"**, proceed to Step 2.

**If state is "COMPLETE" or file doesn't exist**, skip this workflow.

---

### Step 2: Load Task State

Read the task state file to understand:
- What task is being worked on (task_id, title, branch)
- How much is complete (completion_percentage, completed_components)
- What's next (current_component, next_steps)
- Important context (continuation_ids, key_decisions, critical_files)

```bash
# Load full state
cat .claude/task-state.json | jq '.'
```

---

### Step 3: Verify Branch and Files

```bash
# Check current branch
git branch --show-current

# Verify expected branch (from task-state.json)
EXPECTED_BRANCH=$(cat .claude/task-state.json | jq -r '.current_task.branch')

# Switch if needed
if [ "$(git branch --show-current)" != "$EXPECTED_BRANCH" ]; then
  git checkout $EXPECTED_BRANCH
fi

# Verify completed work exists
git log --oneline -5 | grep -i "$(cat .claude/task-state.json | jq -r '.completed_work | to_entries | .[0].value.commit')"

# Check critical files exist
ls -la $(cat .claude/task-state.json | jq -r '.context.critical_files[]')
```

---

### Step 4: Read Task Document

```bash
# Read the task document to refresh requirements
TASK_FILE=$(cat .claude/task-state.json | jq -r '.current_task.task_file')
cat "$TASK_FILE"
```

---

### Step 5: Display Resume Summary

**Output a clear summary for the user:**

```
📋 TASK RESUME DETECTED
════════════════════════════════════════════════════════════

Task: P2T1 - Multi-Alpha Allocator
Branch: feature/P2T1-multi-alpha-allocator
Progress: 20% complete (1/5 components)

✅ COMPLETED:
  • Component 1: Core allocator (rank aggregation + equal weight)
    - Commit: 7ca84ff
    - Files: libs/allocation/multi_alpha.py, tests/libs/allocation/test_multi_alpha.py
    - Tests: 26 added, all passing
    - Review: Approved (continuation_id: 272e6449-85d2-4476-8f26-389a3820374f)

🔄 CURRENT:
  • Component 2: Inverse Volatility Weighting
    - Status: NOT_STARTED
    - Next: Implement _inverse_vol() method in multi_alpha.py

📝 NEXT STEPS:
  1. Implement inverse volatility weighting method (30 min)
  2. Add tests for inverse volatility (20 min)
  3. Request quick review via clink + codex (5 min)
  4. Run make ci-local (10 min)
  5. Commit Component 2 with zen-mcp approval (5 min)

⚠️  IMPORTANT CONTEXT:
  • Use reciprocal rank (1/rank) - ensures positive weights
  • All reviews use clink (NOT direct zen-mcp tools)
  • Continuation ID for reviews: 272e6449-85d2-4476-8f26-389a3820374f

Ready to continue? I'll proceed with Component 2 implementation.
════════════════════════════════════════════════════════════
```

---

### Step 6: Auto-Load Implementation Plan

```bash
# Load implementation plan for current component
cat .claude/task-state.json | jq -r '.implementation_guide.component_2_plan'
```

**Display:**
- File to modify
- Method/function to implement
- Key logic/algorithm
- Test scenarios to cover

---

### Step 7: Proceed with Work

**Automatically continue with next steps:**

1. Create todo list from `next_steps` in task-state.json
2. Start implementing current component
3. Follow 4-step pattern (Implement → Test → Review → CI → Commit)
4. Update task-state.json after each component completion

---

## 🔧 Manual Resume (Fallback)

If automated resume fails or you want manual control:

### 1. Read State File

```bash
cat .claude/task-state.json
```

### 2. Checkout Branch

```bash
git checkout $(cat .claude/task-state.json | jq -r '.current_task.branch')
```

### 3. Read Task Document

```bash
cat $(cat .claude/task-state.json | jq -r '.current_task.task_file')
```

### 4. Review Completed Work

```bash
git log --oneline -10
git show $(cat .claude/task-state.json | jq -r '.completed_work | to_entries | .[0].value.commit')
```

### 5. Continue with Next Steps

Follow the `next_steps` array in task-state.json.

---

## 📝 Updating Task State

**CRITICAL:** Update `.claude/task-state.json` after completing each component!

### After Completing a Component

```json
{
  "progress": {
    "completed_components": 2,  // Increment
    "current_component": {
      "number": 3,  // Next component
      "name": "Correlation monitoring + caps",
      "status": "NOT_STARTED"
    },
    "completion_percentage": 40  // Update
  },
  "completed_work": {
    "Component 2": {  // Add new entry
      "name": "Inverse volatility weighting",
      "commit": "abc1234",
      "files": ["libs/allocation/multi_alpha.py", "..."],
      "tests_added": 8,
      "review_approved": true,
      "continuation_id": "xyz-continuation-id"
    }
  },
  "next_steps": [  // Update to Component 3 steps
    {...}
  ]
}
```

### When Task Complete

```json
{
  "current_task": {
    "state": "COMPLETE",  // Change from IN_PROGRESS
    "completed": "2025-11-03"
  },
  "progress": {
    "completed_components": 5,
    "current_component": null,
    "completion_percentage": 100
  }
}
```

---

## 🎯 Integration with Other Workflows

**This workflow integrates with:**
- `00-analysis-checklist.md` - Pre-implementation analysis
- `01-git-commit.md` - 4-step commit pattern
- `03-zen-review-quick.md` - Quick review gate
- `04-zen-review-deep.md` - Pre-PR review
- `02-git-pr.md` - PR creation

**Task state tracking ensures:**
- No lost context between sessions
- Automatic continuation of work
- Clear progress tracking
- Review continuation IDs preserved
- Critical decisions documented

---

## 🚨 Troubleshooting

### State File Corrupt or Missing

```bash
# Manually recreate from git log + current work
# 1. Identify current task from branch name
git branch --show-current  # e.g., feature/P2T1-multi-alpha-allocator

# 2. Find last completed component commit
git log --oneline -10

# 3. Recreate state file manually using template
```

### Branch Mismatch

```bash
# Force checkout to task branch
git checkout -b $(cat .claude/task-state.json | jq -r '.current_task.branch') master
```

### Lost Continuation ID

```bash
# Check git log for previous review approval
git log --grep="continuation-id" -5

# Extract continuation_id from commit message
git show <commit> | grep continuation-id
```

---

## ✅ Success Criteria

This workflow succeeds when:
- ✅ Claude automatically detects incomplete work
- ✅ Context fully restored (branch, files, progress, decisions)
- ✅ Work continues seamlessly without re-explaining
- ✅ Continuation IDs preserved for review chain
- ✅ No duplicate work or lost progress

**Time Saved:** 10-20 minutes per session resume (vs manual context reconstruction)

---

## 📚 Example: Resuming P2T1

**Session Start Detection:**
```bash
$ cat .claude/task-state.json | jq '.current_task.state'
"IN_PROGRESS"

$ cat .claude/task-state.json | jq '.progress.completion_percentage'
20
```

**Auto-Resume Triggered:**
```
🤖 Incomplete task detected: P2T1 (20% complete)
📂 Branch: feature/P2T1-multi-alpha-allocator
✅ Component 1 complete (commit: 7ca84ff)
🔄 Component 2 in progress: Inverse Volatility Weighting

Loading context and continuing...
```

**Work Continues Automatically:**
- No re-reading entire task document
- No re-analyzing completed work
- Jump straight to Component 2 implementation
- Continuation ID ready for reviews

---

## 🔗 Related Files

- `.claude/task-state.json` - Task state tracking (auto-updated)
- `docs/TASKS/P2T1_TASK.md` - Full task requirements
- `.claude/workflows/00-template.md` - Task template
- `.claude/workflows/README.md` - Workflow index
