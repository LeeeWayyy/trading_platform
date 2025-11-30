# AI Workflows

## 6-Step Pattern

All development follows a 6-step pattern for each component:

**The 6 steps:**
1. **Plan** - Design component approach
2. **Plan Review** - Request review of plan via zen-mcp
3. **Implement** - Write code + tests (TDD)
4. **Test** - Run tests locally
5. **Code Review** - Request comprehensive review via zen-mcp (MANDATORY)
6. **Commit** - Commit after review approval + CI passes

See [12-component-cycle.md](./12-component-cycle.md) for detailed workflow.

---

## Workflow Gate Architecture (P3T1 Modernization)

The workflow enforcement system has been modernized in P3T1 with a modular architecture:

### Directory Structure

```
scripts/
├── workflow_gate.py          # CLI entry point (~1,000 lines)
├── workflow_gate.py.bak      # Backup of legacy 4,369-line monolith
└── ai_workflow/              # Modular package
    ├── __init__.py
    ├── config.py             # Configuration management
    ├── constants.py          # Paths and shared constants
    ├── core.py               # WorkflowGate class (file locking via fcntl)
    ├── delegation.py         # Agent delegation
    ├── git_utils.py          # Git operations
    ├── hash_utils.py         # Code fingerprinting
    ├── pr_workflow.py        # PR handling
    ├── reviewers.py          # Review orchestration
    ├── subtasks.py           # Subtask management
    └── tests/                # Comprehensive test suite
```

### State Directory

The workflow state has migrated from `.claude/` to `.ai_workflow/`:

```
.ai_workflow/
├── workflow-state.json       # Current workflow state
├── config.json               # User configuration (auto-created)
└── workflow-audit.log        # Audit trail for continuation IDs
```

**Note:** Existing `.claude/` state is automatically migrated on first run.

### Key Features

- **File Locking:** Atomic state operations via `fcntl` (prevents race conditions)
- **Dual Review Enforcement:** Both Gemini AND Codex must approve before commit
- **Code Fingerprinting:** Detects post-review code modifications
- **Audit Logging:** Tracks all continuation IDs for verification
- **Emergency Override:** `ZEN_REVIEW_OVERRIDE=1` for critical hotfixes (logged)

### Common Commands

```bash
# Status
./scripts/workflow_gate.py status

# Start task
./scripts/workflow_gate.py start-task docs/TASKS/P3T1_TASK.md feature/P3T1-workflow

# Component workflow
./scripts/workflow_gate.py set-component "Component Name"
./scripts/workflow_gate.py advance plan-review
./scripts/workflow_gate.py advance implement
./scripts/workflow_gate.py advance test
./scripts/workflow_gate.py advance review

# Record reviews and CI
./scripts/workflow_gate.py record-review gemini approved --continuation-id <id>
./scripts/workflow_gate.py record-review codex approved --continuation-id <id>
./scripts/workflow_gate.py record-ci true

# Check commit readiness
./scripts/workflow_gate.py check-commit

# Record commit (resets to plan for next component)
./scripts/workflow_gate.py record-commit <hash>
```

---

## Workflow Index

- [00-analysis-checklist](./00-analysis-checklist.md)
- [01-git](./01-git.md)
- [02-planning](./02-planning.md)
- [03-reviews](./03-reviews.md)
- [04-development](./04-development.md)
- [05-operations](./05-operations.md)
- [08-session-management](./08-session-management.md)
- [12-component-cycle](./12-component-cycle.md)
- [16-pr-review-comment-check](./16-pr-review-comment-check.md)
- [16-subagent-delegation](./16-subagent-delegation.md)
- [17-automated-analysis](./17-automated-analysis.md)
- [session-management](./session-management.md)
- [troubleshooting](./troubleshooting.md)

See the [main AI Documentation Index](../README.md) for full navigation.
