# Update Task State Workflow

**Purpose:** Keep `.claude/task-state.json` synchronized with actual progress automatically.

**When to Update:**
- ✅ After completing each component
- ✅ When starting a new task
- ✅ When finishing entire task
- ✅ After every component commit

---

## 🤖 Automatic Reminders

**The system helps you remember:**

1. **Pre-commit hook** - Warns if you commit a component without updating state
2. **TodoWrite integration** - Includes "Update task state" as a todo item
3. **Workflow patterns** - All component workflows include state update step

---

## 📝 Quick Update Commands

### After Completing a Component

```bash
# Example: Just finished Component 2
./scripts/update_task_state.py complete \
    --component 2 \
    --commit $(git rev-parse HEAD) \
    --files libs/allocation/multi_alpha.py tests/libs/allocation/test_multi_alpha.py \
    --tests 8 \
    --continuation-id 272e6449-85d2-4476-8f26-389a3820374f

# Then stage the updated state
git add .claude/task-state.json

# Amend the component commit to include state update
git commit --amend --no-edit
```

**What it does:**
- Increments `completed_components` (1 → 2)
- Updates `completion_percentage` (20% → 40%)
- Adds Component 2 to `completed_work`
- Advances `current_component` to Component 3
- Updates `last_updated` timestamp

---

### Starting a New Task

```bash
# Example: Starting P2T2
./scripts/update_task_state.py start \
    --task P2T2 \
    --title "Advanced Risk Metrics" \
    --branch feature/P2T2-risk-metrics \
    --task-file docs/TASKS/P2T2_TASK.md \
    --components 4

git add .claude/task-state.json
git commit -m "chore: Start tracking P2T2 task"
```

---

### Finishing Entire Task

```bash
# After all components complete and PR merged
./scripts/update_task_state.py finish

git add .claude/task-state.json
git commit -m "chore: Mark P2T1 task complete"
```

---

## 🔄 Integration with 4-Step Pattern

**Standard workflow now includes state update:**

```markdown
Component X workflow:
1. Implement logic
2. Create test cases
3. Request quick review (clink + codex)
4. Run make ci-local
5. Commit after approval + CI pass
6. **Update task state** ← NEW STEP
```

**Updated TodoWrite pattern:**

```json
[
  {"content": "Component 2: Implement inverse volatility", "status": "in_progress"},
  {"content": "Component 2: Create test cases", "status": "pending"},
  {"content": "Component 2: Request quick review", "status": "pending"},
  {"content": "Component 2: Run make ci-local", "status": "pending"},
  {"content": "Component 2: Commit after approval", "status": "pending"},
  {"content": "Component 2: Update task state", "status": "pending"}  // ← Added
]
```

---

## 🎯 Best Practices

### 1. Update Immediately After Component Commit

**Right way:**
```bash
# 1. Commit component
git commit -m "feat: Component 2 - Inverse volatility"

# 2. IMMEDIATELY update state
./scripts/update_task_state.py complete --component 2 ...

# 3. Stage state file
git add .claude/task-state.json

# 4. Amend to include in same commit
git commit --amend --no-edit
```

**Wrong way (don't do this):**
```bash
# Commit component
git commit -m "feat: Component 2"

# Forget to update state... ❌
# Next session: Claude doesn't know Component 2 is done
```

---

### 2. Include in Every Component Commit

**Component commits should update state:**
```bash
$ git show --stat
feat: Component 2 - Inverse volatility weighting

 libs/allocation/multi_alpha.py        | 45 ++++
 tests/libs/allocation/test_multi.py   | 80 ++++
 .claude/task-state.json              | 12 +-   ← Should be here!
```

---

### 3. Verify State After Update

```bash
# After update, verify it looks correct
cat .claude/task-state.json | jq '.progress'

# Expected output:
{
  "total_components": 5,
  "completed_components": 2,  # Incremented
  "current_component": {
    "number": 3,              # Advanced
    "name": "Correlation monitoring + caps",
    "status": "NOT_STARTED"
  },
  "completion_percentage": 40  # Updated
}
```

---

## 🚨 What Happens If You Forget?

**Pre-commit hook catches it:**

```
⚠️  WARNING: Possible component completion detected
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Your commit message suggests you completed a component, but
.claude/task-state.json was not updated.

Current task: P2T1 - Multi-Alpha Allocator
Progress: 20%

📝 To update task state:

   ./scripts/update_task_state.py complete \
       --component 2 \
       --commit $(git rev-parse HEAD) \
       --files file1.py file2.py \
       --tests 8 \
       --continuation-id abc123

   git add .claude/task-state.json
   git commit --amend --no-edit

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Press ENTER to continue anyway, or Ctrl+C to cancel
```

---

## 📊 Monitoring Task Progress

```bash
# Quick status check
cat .claude/task-state.json | jq '.progress.completion_percentage'
# Output: 40

# See current component
cat .claude/task-state.json | jq '.progress.current_component'

# See what's completed
cat .claude/task-state.json | jq '.completed_work | keys'
# Output: ["Component 1", "Component 2"]
```

---

## ✅ Success Criteria

Task state tracking succeeds when:

- ✅ State updates after every component commit
- ✅ Progress percentage matches actual completion
- ✅ Current component is accurate
- ✅ Continuation IDs preserved
- ✅ Next session: Claude auto-resumes correctly
- ✅ No manual context reconstruction needed

**Key Metric:** If Claude can resume work in <1 min without questions, state tracking is working! 🎯

---

## 📚 Related

- [14-task-resume.md](./14-task-resume.md) - Auto-resume workflow
- [01-git-commit.md](./01-git-commit.md) - Component commit pattern
- `.claude/task-state.json` - State tracking file
- `scripts/update_task_state.py` - Update script
