# Claude Code State Directory

This directory contains runtime state files for Claude Code workflows.

## Files

### `current-todo.json`
**Status:** NOT version-controlled (git-ignored)
**Purpose:** Runtime state for TodoWrite tool tracking

**Schema:**
```json
[
  {
    "content": "Task description in imperative form",
    "status": "pending" | "in_progress" | "completed",
    "activeForm": "Task description in present continuous form"
  }
]
```

**Example:**
```json
[
  {
    "content": "Implement user authentication",
    "status": "in_progress",
    "activeForm": "Implementing user authentication"
  },
  {
    "content": "Create test cases for authentication",
    "status": "pending",
    "activeForm": "Creating test cases for authentication"
  }
]
```

**Workflow Integration:**
- Written by Claude Code's TodoWrite tool
- Read by `scripts/hooks/verify_todo.sh` during pre-commit
- Ensures commits are associated with tracked work

**Validation Rules:**
- At least one todo should have status "in_progress" or "pending"
- All completed todos trigger warning (work may be done)
- Missing file triggers warning only (not blocking)

---

## Git Ignore

The `current-todo.json` file is runtime state and should NOT be committed to version control. It's already in `.gitignore`:

```gitignore
# Claude Code runtime state
.claude/state/current-todo.json
```

---

## Related

- Pre-commit hook: `scripts/hooks/verify_todo.sh`
- TodoWrite tool documentation: See Claude Code docs
- 6-step pattern: `docs/AI/Workflows/12-component-cycle.md`
