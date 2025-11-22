# 🤖 Automatic Task Resume System

**CRITICAL:** This file is read automatically at the start of each Claude Code session.

---

## 🔍 Session Start Detection

**Check for incomplete work:**

```bash
# Does task state file exist?
[ -f .claude/task-state.json ]

# Is there incomplete work?
TASK_STATE=$(jq -r '.current_task.state' .claude/task-state.json)
if [ "$TASK_STATE" = "IN_PROGRESS" ] || [ "$TASK_STATE" = "PENDING" ]; then
  echo "🤖 INCOMPLETE TASK DETECTED - AUTO-RESUMING"
  # Trigger resume workflow
fi
```

**If incomplete work detected:**
→ Automatically load `./14-task-resume.md` and execute

---

## 📋 Current Task Status

Reading from `.claude/task-state.json`:

```json
{
  "current_task": {
    "task_id": "P2T1",
    "title": "Multi-Alpha Allocator",
    "branch": "feature/P2T1-multi-alpha-allocator",
    "state": "IN_PROGRESS"
  },
  "progress": {
    "completion_percentage": 20,
    "completed_components": 1,
    "current_component": {
      "number": 2,
      "name": "Inverse Volatility Weighting"
    }
  }
}
```

---

## 🚀 Auto-Resume Actions

When this file is detected, Claude should:

1. **Read task state** from `.claude/task-state.json`
2. **Verify branch**: `git checkout <branch-from-state>`
3. **Load task document**: Read `docs/TASKS/<task_id>_*.md`
4. **Display summary**:
   - What's completed
   - What's current
   - What's next
5. **Create todo list** from `next_steps` in state file
6. **Continue work** automatically

---

## 📖 Full Resume Workflow

See [`./08-session-management.md`](./workflows/08-session-management.md) for complete auto-resume workflow.

---

## ⚙️ How to Disable Auto-Resume

If you want to start fresh (ignore incomplete work):

```bash
# Temporarily disable
mv .claude/task-state.json .claude/task-state.json.bak

# Re-enable
mv .claude/task-state.json.bak .claude/task-state.json
```

Or edit the state file:
```json
{
  "meta": {
    "auto_resume_enabled": false
  }
}
```

---

## 🎯 Benefits

- ✅ **Zero context loss** between sessions
- ✅ **Instant continuation** without re-explaining
- ✅ **Preserved review chains** via continuation IDs
- ✅ **Progress tracking** with percentage completion
- ✅ **No duplicate work** - knows what's done

**Time Saved:** 10-20 minutes per session resume

---

**Last Updated:** 2025-10-29
**Current Task:** P2T1 - Multi-Alpha Allocator (20% complete)
