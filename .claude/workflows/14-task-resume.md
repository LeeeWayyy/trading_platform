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
jq '.current_task.state, .progress.completion_percentage' .claude/task-state.json
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

---

### Step 3: Verify Branch and Files

```bash
# Check current branch
git branch --show-current

# Verify expected branch (from task-state.json)
EXPECTED_BRANCH=$(jq -r '.current_task.branch' .claude/task-state.json)

# Switch if needed
if [ "$(git branch --show-current)" != "$EXPECTED_BRANCH" ]; then
  git checkout $EXPECTED_BRANCH
fi
```

---

### Step 4: Read Task Document

```bash
# Read the task document to refresh requirements
TASK_FILE=$(jq -r '.current_task.task_file' .claude/task-state.json)
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
    - Tests: 26 added, all passing
    - Review: Approved (continuation_id: 272e6449...)

🔄 CURRENT:
  • Component 2: Inverse Volatility Weighting
    - Status: NOT_STARTED
    - Next: Implement _inverse_vol() method

📝 NEXT STEPS:
  1. Implement inverse volatility weighting method (30 min)
  2. Add tests for inverse volatility (20 min)
  3. Request quick review via clink + codex (5 min)
  4. Run make ci-local (10 min)
  5. Commit Component 2 with zen-mcp approval (5 min)

⚠️  IMPORTANT CONTEXT:
  • Use reciprocal rank (1/rank) - ensures positive weights
  • All reviews use clink (NOT direct zen-mcp tools)

Ready to continue? I'll proceed with Component 2 implementation.
════════════════════════════════════════════════════════════
```

---

### Step 6: Auto-Load Implementation Plan

```bash
# Load implementation plan for current component
jq -r '.implementation_guide.component_2_plan' .claude/task-state.json
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

```bash
# 1. Read state file
cat .claude/task-state.json

# 2. Checkout branch
git checkout $(jq -r '.current_task.branch' .claude/task-state.json)

# 3. Read task document
cat $(jq -r '.current_task.task_file' .claude/task-state.json)

# 4. Review completed work
git log --oneline -10

# 5. Continue with next steps
# Follow the `next_steps` array in task-state.json
```

---

## 📝 Updating Task State

**CRITICAL:** Update `.claude/task-state.json` after completing each component!

See [15-update-task-state.md](./15-update-task-state.md) for detailed update workflow.

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
git checkout $(jq -r '.current_task.branch' .claude/task-state.json)
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

## 🔗 Related Files

- `.claude/task-state.json` - Task state tracking (auto-updated)
- `15-update-task-state.md` - Update workflow
- `.claude/workflows/README.md` - Workflow index
