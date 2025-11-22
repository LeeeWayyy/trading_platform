# Context Checkpoints

This directory stores context checkpoints for AI coding session continuity.

## Purpose

Checkpoints preserve critical state before context-modifying operations:
- **Delegation checkpoints**: Before delegating to subagents (Task tool, zen-mcp clink)
- **Session-end checkpoints**: Before ending coding sessions

## What Checkpoints Capture

Each checkpoint includes:
- Current task state from `.claude/task-state.json`
- Workflow state from `.claude/workflow-state.json`
- Git state (branch, commit, staged files)
- Critical findings and pending decisions
- Delegation history
- Zen-mcp continuation IDs
- Token usage estimates

## File Structure

```
.claude/checkpoints/
├── {uuid}.json                    # Individual checkpoint files
├── latest_delegation.json         # Symlink to most recent delegation checkpoint
├── latest_session_end.json        # Symlink to most recent session-end checkpoint
└── README.md                      # This file
```

## Automatic Cleanup Policy

**Default Retention:**
- Keep last **10 checkpoints** per type (delegation, session_end)
- Auto-delete checkpoints older than **7 days**

**Manual Cleanup:**
```bash
# Clean up checkpoints older than 7 days (keeps last 10 per type)
./scripts/context_checkpoint.py cleanup --older-than 7d

# Custom retention (e.g., 14 days, keep last 20)
./scripts/context_checkpoint.py cleanup --older-than 14d --keep-latest 20
```

## Usage

**Create checkpoint:**
```bash
# Before delegation
./scripts/context_checkpoint.py create --type delegation

# Before session end
./scripts/context_checkpoint.py create --type session_end
```

**Restore checkpoint:**
```bash
# Restore specific checkpoint
./scripts/context_checkpoint.py restore --id {checkpoint_id}

# Restore latest delegation checkpoint
LATEST_ID=$(basename $(readlink latest_delegation.json) .json)
./scripts/context_checkpoint.py restore --id $LATEST_ID
```

**List checkpoints:**
```bash
# List all checkpoints
./scripts/context_checkpoint.py list

# List only delegation checkpoints
./scripts/context_checkpoint.py list --type delegation
```

## Integration with Workflows

**Delegation workflows** (`.claude/workflows/16-subagent-delegation.md`):
- Create delegation checkpoint before using Task tool
- Restore if delegation corrupts context

**Auto-resume workflow** (`.claude/workflows/14-task-resume.md`):
- Checks for `latest_session_end.json` on session start
- Optionally restores checkpoint to supplement task-state.json

## When NOT to Create Checkpoints

Skip checkpoints for:
- Simple single-file edits
- Quick file searches with known paths
- Context usage <50k tokens
- Short (<30 min) coding sessions

## Checkpoint File Format

```json
{
  "id": "uuid",
  "timestamp": "2025-11-02T14:30:00",
  "type": "delegation" | "session_end",
  "context_data": {
    "current_task": {},
    "workflow_state": {},
    "delegation_history": [],
    "critical_findings": [],
    "pending_decisions": [],
    "continuation_ids": []
  },
  "git_state": {
    "branch": "feature/...",
    "commit": "abc123...",
    "staged_files": []
  },
  "token_usage_estimate": 120000
}
```

## Implementation Phase

Part of **P1T13-F3 Phase 3: Context Checkpointing System**

See `docs/TASKS/P1T13_F3_AUTOMATION.md` for complete implementation details.
