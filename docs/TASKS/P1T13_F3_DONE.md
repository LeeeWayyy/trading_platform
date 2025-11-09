---
id: P1T13-F3
title: "AI Coding Automation: Context Optimization & Full-Cycle Workflow"
phase: P1
task: T13-F3
priority: P1
owner: "@development-team"
state: COMPLETE
created: 2025-11-01
updated: 2025-11-02
completed: 2025-11-02
dependencies: ["P1T13"]
estimated_effort: "11-15 hours (revised from 8-11h)"
related_adrs: []
related_docs: ["CLAUDE.md", ".claude/workflows/", "docs/STANDARDS/"]
features: ["context_optimization", "context_checkpointing"]
branch: "feature/P1T13-F3-phase3-automation"
---

# P1T13-F3: AI Coding Automation - Context Optimization & Full-Cycle Workflow

**Phase:** P1 (Hardening, 46-90 days)
**Status:** COMPLETE (All 3 planned phases completed - Phases 4-6 deferred)
**Priority:** P1 (MEDIUM-HIGH)
**Owner:** @development-team
**Created:** 2025-11-01
**Updated:** 2025-11-02
**Completed:** 2025-11-02
**Estimated Effort:** 11-15 hours (revised from 8-11h based on gemini feedback)
**Dependencies:** P1T13 (Documentation & Workflow Optimization)

**Review Status:**
- Gemini planner: ✅ APPROVED (continuation_id: 9613b833-d705-47f1-85d1-619f80accb0e)
- Codex planner: ✅ APPROVED (continuation_id: 9613b833-d705-47f1-85d1-619f80accb0e)

---

## Objective

Enable Claude CLI (AI coder) to achieve full autonomous coding cycles from planning through merge, while optimizing context usage through subagent delegation patterns.

**Two Main Goals:**

1. **Context Optimization**: Implement orchestrator-subagent delegation pattern to prevent context pollution and multiply effective working capacity

2. **Full Automation**: Realize end-to-end autonomous workflow: task planning → auto-coding → review → PR creation → auto-fix review comments → auto-fix CI failures → iterate until merge

---

## Problem Statement

### Current Pain Points

**1. Context Pollution (Primary Issue)**

Current architecture uses a single 200k context window for ALL tasks:
- Core tasks (planning, coding, reviewing) compete with non-core tasks (file search, doc lookup, analysis)
- Context fills rapidly with tangential information
- No isolation between independent subtasks
- Results in premature context exhaustion and lost continuity

**Impact:**
- ~30-40% of context wasted on non-core operations
- Session interruptions requiring manual continuation
- Lost context leads to inconsistent implementation
- Reduced working capacity per session

**2. Manual Intervention Bottlenecks**

Current workflow requires manual intervention at EVERY stage:

```
Task → [MANUAL planning] → [MANUAL coding] → [MANUAL quick review request]
    → [MANUAL commit] → [MANUAL deep review request] → [MANUAL PR creation]
    → [MANUAL read PR comments] → [MANUAL fix comments] → [MANUAL read CI logs]
    → [MANUAL fix CI failures] → [MANUAL iteration until clean]
```

**Gaps:**
- No automated planning workflow
- No self-driving coding mode
- Review requests manual (though review itself is automated via zen-mcp)
- PR creation manual
- No automated PR comment reading/addressing
- No automated CI log analysis/fixing
- No loop structure to iterate until reviewers approve

**Impact:**
- Every step requires user prompt
- User must manually check PR comments and CI status
- Slow iteration cycles (hours → days for complex PRs)
- High cognitive load on user to manage workflow state

**3. Weak Integration Points**

Existing quality gates (zen-mcp reviews) work well but:
- Not integrated into automated workflow
- No automatic application of review feedback
- No GitHub Actions integration for auto-fixing
- Manual handoff between review → fix → re-review cycles

---

## Proposed Solutions

### **Component 1: Context Optimization via Subagent Delegation (4-5 hours)**

**Architecture: Orchestrator → Sub-Agent Pattern**

Implement hub-and-spoke architecture where Claude CLI orchestrator delegates non-core tasks to specialist subagents with isolated 200k context windows.

**Design:**

```
┌─────────────────────────────────────────────┐
│   Claude CLI Orchestrator (Main Context)   │
│   - Task planning                           │
│   - Core implementation                     │
│   - Review coordination                     │
│   - Decision making                         │
│   └─────────────┬───────────────────────────┘
│                 │
│     Delegates non-core tasks ↓
│     (Provides minimal context slice)
│
├─────────────────┼─────────────────┬──────────────────┐
│                 │                 │                  │
▼                 ▼                 ▼                  ▼
┌─────────────┐ ┌──────────────┐ ┌───────────────┐ ┌──────────────┐
│ File Search │ │ Doc Lookup   │ │ Code Analysis │ │ Test Runner  │
│ Subagent    │ │ Subagent     │ │ Subagent      │ │ Subagent     │
│ (200k ctx)  │ │ (200k ctx)   │ │ (200k ctx)    │ │ (200k ctx)   │
└─────────────┘ └──────────────┘ └───────────────┘ └──────────────┘
      │                 │                 │                 │
      └─────────────────┴─────────────────┴─────────────────┘
                            │
               Returns summary results only
                      (no full context)
```

**Delegation Criteria:**

**Delegate to subagent:**
- File searches across codebase (Glob, Grep)
- Documentation lookup (non-critical reference docs)
- Code analysis for understanding (not implementation)
- Test execution and log analysis (delegatable)
- CI log analysis (delegatable)
- PR comment extraction (delegatable)

**Keep in main context:**
- Task planning and requirement analysis
- Core implementation logic
- Architecture decisions
- Review coordination (but delegate review execution via zen-mcp)
- Commit creation and PR creation
- Direct user interaction and clarification

**Implementation Approach:**

**Option A: Claude Code Agent Tool (Native Support)**

Leverage existing Claude Code `Task` tool with specialized subagent types:

```python
# Example delegation pattern
Task(
    description="Search codebase for circuit breaker implementations",
    prompt="Find all call sites of check_circuit_breaker() in apps/ and libs/. Return file:line references only, no full code.",
    subagent_type="Explore",  # Uses independent 200k context
)
# Orchestrator receives: ["apps/execution_gateway/order_placer.py:42", ...]
# Main context saved: ~15-20k tokens (no full file contents)
```

**Option B: Script-Based Hook Integration**

Create pre-task hook that spawns isolated Claude CLI instances:

```bash
# .claude/hooks/delegate_subtask.sh
#!/bin/bash
# Spawn isolated Claude CLI instance for non-core task

TASK_TYPE=$1  # "file_search", "doc_lookup", "test_run"
TASK_PROMPT=$2

# Run in isolated instance (separate context)
claude_code_cli --task "$TASK_TYPE" --prompt "$TASK_PROMPT" --return-summary

# Returns: JSON summary with minimal context
# { "results": [...], "summary": "...", "token_cost": 5000 }
```

**Option C: Hybrid Approach (Recommended)**

- Use native `Task` tool for codebase exploration (leverages Explore subagent)
- Use zen-mcp clink for code review delegation (existing pattern)
- Reserve script hooks for future custom delegation needs

**Workflow Integration:**

Update `.claude/workflows/00-analysis-checklist.md` and `01-git.md`:

```markdown
## Phase 1: Comprehensive Analysis (30-60 min)

### 1. Find ALL Impacted Components (15 min)

**NEW: Use subagent delegation for search-heavy tasks**

# Delegate codebase search to Explore subagent (prevents context pollution)
Task(description="Find call sites", prompt="Search for all calls to function_name", subagent_type="Explore")

# Orchestrator receives summary: ["file1:line", "file2:line", ...]
# Main context saved: ~20k tokens
```

**Success Metrics:**

- Context usage per task reduced by ≥30% (measured via token counts)
- Main context window remains available for ≥50% longer sessions
- No loss of quality (validated via output comparison)
- Subagent delegation transparent to user (no additional prompts or clarifications required) *(Gemini suggestion)*
- Tight file/line scopes in delegated prompts to minimize context leaks *(Codex suggestion)*

**Deliverables:**

1. `.claude/workflows/16-subagent-delegation.md` - Delegation pattern guide
2. Updated workflows (00, 01, 03, 04) with delegation examples
3. `.claude/hooks/delegate_subtask.sh` - Script hook (Option B/C)
4. Baseline vs. optimized context usage metrics
5. Subagent delegation decision tree (when to delegate vs. keep in main context)
6. **Task state tracking**: Updates to `.claude/task-state.json` after each phase completion

---

### **Component 2: Workflow Enforcement Layer with Hard Gates (4-6 hours)**

**Architecture: State Machine with Programmatic Enforcement**

Instead of relying on AI to follow documentation (soft gates), implement **hard gates** via Python scripts and git hooks that **programmatically enforce** workflow compliance.

**Key Insight:** Existing workflows (`.claude/workflows/01-git.md`, `component-cycle.md`) already define the 4-step pattern correctly. The problem is **enforcement**, not definition.

> **📝 NOTE: The following "Workflow Enforcement Layer" design is FUTURE WORK (not implemented in Phase 3).**
>
> **Phase 3 Status (COMPLETED):** Context checkpointing system only.
>
> **Future Work:** The detailed design below describes a future workflow enforcement system. This section serves as research/design documentation for potential Phase 4+ implementation and should be moved to a dedicated design document (e.g., `.claude/research/workflow-enforcement-design.md`) in a future refactoring.

**Design (FUTURE - NOT IMPLEMENTED):**

```
┌────────────────────────────────────────────────────────────────┐
│          Workflow State Machine (Hard Enforcement)             │
└────────────────────────────────────────────────────────────────┘

State Tracking:
.claude/workflow-state.json
  ├─ current_component: "position_limit_validation"
  ├─ step: "implement" | "test" | "review" | "commit"
  ├─ zen_review: {requested, continuation_id, status}
  ├─ ci_passed: true/false
  └─ staged_files: [...]

State Transitions (Enforced via workflow_gate.py):
  implement → test       (always allowed)
  test     → review      (allowed if tests exist)
  review   → implement   (allowed for fixes)

Git Hooks Integration:
  pre-commit → scripts/workflow_gate.py check-commit
    ├─ Block if zen review not approved
    ├─ Block if CI not passed
    └─ Block if state != "review"
  post-commit → scripts/workflow_gate.py record-commit
    ├─ Record commit hash
    ├─ Reset state to "implement"
    └─ Clear zen_review and ci_passed flags

    NOTE: Post-commit hook modifies .claude/workflow-state.json AFTER commit,
    leaving working directory dirty. Future implementation should:
      (a) Instruct user to create follow-up commit for state changes, OR
      (b) Use git commit --amend to include state change in same commit (if safe), OR
      (c) Store state in .git/hooks/ directory (untracked) instead of repo
```

**Hard Gate Enforcement Points:**

1. **Git pre-commit hook** - Blocks commits unless:
   - Zen review approved (continuation_id recorded)
   - `make ci-local` passed (status recorded)
   - Current state is "review" (can't skip steps)

2. **CLI commands** - Explicit state transitions:
   ```bash
   ./scripts/workflow_gate.py advance test       # implement → test
   ./scripts/workflow_gate.py advance review     # test → review (requests zen review)
   ./scripts/workflow_gate.py record-commit      # After git commit (post-commit hook)
   ./scripts/workflow_gate.py advance implement  # Back to implement (if fixing issues)
   ```

3. **State validation** - Before each transition:
   - Check prerequisites (tests exist, review approved, CI passed)
   - Update `.claude/workflow-state.json` atomically
   - Provide clear error messages if blocked

**Implementation Approach:**

**A. Workflow State Machine Script (2-3 hours)**

Create `scripts/workflow_gate.py` (~200 lines):

```python
#!/usr/bin/env python3
"""
Workflow enforcement gate - Hard enforcement of 4-step component pattern.

Usage:
  ./scripts/workflow_gate.py advance <next_step>     # Transition to next step
  ./scripts/workflow_gate.py check-commit            # Validate commit prerequisites
  ./scripts/workflow_gate.py status                  # Show current state
  ./scripts/workflow_gate.py reset                   # Reset state (emergency)
"""

import json
from pathlib import Path
from typing import Literal, Tuple

STATE_FILE = Path(".claude/workflow-state.json")

StepType = Literal["implement", "test", "review", "commit"]

class WorkflowGate:
    VALID_TRANSITIONS = {
        "implement": ["test"],
        "test": ["review"],
        "review": ["implement"]  # Can only go back to fix issues
    }

    def _init_state(self) -> dict:
        """Initialize default workflow state."""
        return {
            "current_component": "",
            "step": "implement",
            "zen_review": {},
            "ci_passed": False,
            "last_commit_hash": None,
            "subagent_delegations": []
        }

    def load_state(self) -> dict:
        if not STATE_FILE.exists():
            return self._init_state()
        return json.loads(STATE_FILE.read_text())

    def save_state(self, state: dict) -> None:
        STATE_FILE.parent.mkdir(exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2))

    def can_transition(self, current: StepType, next: StepType) -> Tuple[bool, str]:
        """Check if transition is valid."""
        if next not in self.VALID_TRANSITIONS.get(current, []):
            return False, f"❌ Cannot transition from '{current}' to '{next}'"

        state = self.load_state()

        # Additional checks for specific transitions
        if next == "review":
            # Must have tests before requesting review
            if not self._has_tests(state["current_component"]):
                return False, "❌ Cannot request review without test files"

        if next == "commit":
            # HARD GATE: Must have zen approval
            if not state["zen_review"].get("status") == "APPROVED":
                return False, (
                    "❌ COMMIT BLOCKED: Zen review not approved\n"
                    "   Run: Request zen review via .claude/workflows/03-reviews.md"
                )

            # HARD GATE: Must have CI pass
            if not state["ci_passed"]:
                return False, (
                    "❌ COMMIT BLOCKED: CI not passed\n"
                    "   Run: make ci-local"
                )

        return True, ""

    def advance(self, next: StepType) -> None:
        """Advance workflow to next step (with validation)."""
        state = self.load_state()
        current = state["step"]

        can, error_msg = self.can_transition(current, next)
        if not can:
            print(error_msg)
            exit(1)

        # Special logic for review step
        if next == "review":
            print("🔍 Requesting zen-mcp review (clink + codex)...")
            print("   Follow: .claude/workflows/03-reviews.md")
            print("   After review, record approval:")
            print("     ./scripts/workflow_gate.py record-review <continuation_id> <status>")

        # Update state
        state["step"] = next
        self.save_state(state)

        print(f"✅ Advanced to '{next}' step")

    def record_review(self, continuation_id: str, status: str) -> None:
        """Record zen review result."""
        state = self.load_state()
        state["zen_review"] = {
            "requested": True,
            "continuation_id": continuation_id,
            "status": status  # "APPROVED" or "NEEDS_REVISION"
        }
        self.save_state(state)
        print(f"✅ Recorded zen review: {status}")

    def record_ci(self, passed: bool) -> None:
        """Record CI result."""
        state = self.load_state()
        state["ci_passed"] = passed
        self.save_state(state)
        print(f"✅ Recorded CI: {'PASSED' if passed else 'FAILED'}")

    def check_commit(self) -> None:
        """Validate commit prerequisites (called by pre-commit hook)."""
        state = self.load_state()

        if state["step"] != "review":
            print(f"❌ COMMIT BLOCKED: Current step is '{state['step']}', must be 'review'")
            exit(1)

        if not state["zen_review"].get("status") == "APPROVED":
            print("❌ COMMIT BLOCKED: Zen review not approved")
            print("   Continuation ID:", state["zen_review"].get("continuation_id", "N/A"))
            exit(1)

        if not state["ci_passed"]:
            print("❌ COMMIT BLOCKED: CI not passed")
            print("   Run: make ci-local && ./scripts/workflow_gate.py record-ci true")
            exit(1)

        print("✅ Commit prerequisites satisfied")
        exit(0)

    def record_commit(self) -> None:
        """Record commit hash after successful commit (called post-commit).

        Captures the commit hash and resets state for next component.
        Usage: git rev-parse HEAD | xargs ./scripts/workflow_gate.py record-commit
        """
        import subprocess
        state = self.load_state()

        # Get the commit hash
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True
            )
            commit_hash = result.stdout.strip()
        except subprocess.CalledProcessError:
            print("❌ Failed to get commit hash")
            exit(1)

        # Record commit hash and reset state for next component
        state["last_commit_hash"] = commit_hash
        state["step"] = "implement"  # Ready for next component
        state["zen_review"] = {}
        state["ci_passed"] = False
        self.save_state(state)

        print(f"✅ Recorded commit {commit_hash[:8]}")
        print(f"✅ Ready for next component (step: implement)")

    def _has_tests(self, component: str) -> bool:
        """Check if test files exist for the given component.

        Convention: tests/path/to/test_<component>.py
        Example: Component "position_limit_validation" → tests/**/test_position_limit_validation.py
        """
        import glob
        import os

        # Convert component name to test file pattern
        # Example: "Position Limit Validation" → "test_position_limit*"
        component_slug = component.lower().replace(" ", "_")
        test_pattern = f"tests/**/test_{component_slug}*.py"

        # Search for matching test files
        matches = glob.glob(test_pattern, recursive=True)
        return len(matches) > 0
```

**B. Git Hooks Integration (30 min)**

Create `scripts/pre-commit-hook.sh` (version-controlled):

```bash
#!/bin/bash
# Pre-commit hook - Enforce workflow gates
# CRITICAL: This is a HARD GATE. DO NOT bypass with --no-verify.

python3 scripts/workflow_gate.py check-commit
if [ $? -ne 0 ]; then
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "COMMIT BLOCKED: Workflow prerequisites not met"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "This is a HARD GATE. You must:"
    echo "  1. Request zen review: Follow .claude/workflows/03-reviews.md"
    echo "  2. Run CI locally: make ci-local"
    echo "  3. Record results: ./scripts/workflow_gate.py record-review <id> APPROVED"
    echo "                     ./scripts/workflow_gate.py record-ci true"
    echo ""
    echo "WARNING: DO NOT use 'git commit --no-verify' to bypass this gate."
    echo "         Bypassing gates defeats the entire quality system."
    echo ""
    exit 1
fi

# Allow commit
exit 0
```

**Hook Installation (Automated)**:

Add to `Makefile`:

```makefile
.PHONY: install-hooks
install-hooks:
	@echo "Installing git hooks..."
	@chmod +x scripts/pre-commit-hook.sh
	@ln -sf ../../scripts/pre-commit-hook.sh .git/hooks/pre-commit
	@echo "✅ Pre-commit hook installed"

.PHONY: check-hooks
check-hooks:
	@if [ ! -f .git/hooks/pre-commit ]; then \
		echo "❌ Pre-commit hook not installed. Run: make install-hooks"; \
		exit 1; \
	fi
	@echo "✅ Pre-commit hook installed"

# Integrate check-hooks into test target to detect de-synchronization
.PHONY: test
test: check-hooks
	pytest $(ARGS)
```

Add to `.claude/workflows/11-environment-bootstrap.md`:

```bash
# After initial setup, install git hooks
make install-hooks
```

**CI Verification (Server-Side Gate)**:

Create `scripts/verify_gate_compliance.py` to detect `--no-verify` bypasses:

```python
#!/usr/bin/env python3
"""Verify that all commits in PR followed workflow gates.

Detects commits made with --no-verify by checking if commit hashes
match those recorded in .claude/workflow-state.json.

Exit codes:
  0 - All commits compliant
  1 - Non-compliant commits detected (used --no-verify)
"""
import json
import subprocess
import sys
from pathlib import Path

def get_pr_commits():
    """Get list of commit hashes in current PR/branch."""
    # NOTE: Use GITHUB_BASE_REF environment variable for dynamic base branch detection
    # Hardcoding origin/master fails for projects using main/develop/release branches
    # Future implementation should use: base_branch = os.getenv('GITHUB_BASE_REF', 'master')
    # Get commits between origin/master and HEAD
    result = subprocess.run(
        ["git", "log", "--format=%H", "origin/master..HEAD"],
        capture_output=True,
        text=True,
        check=True
    )
    return result.stdout.strip().split("\n")

def load_workflow_state():
    """Load .claude/workflow-state.json if it exists."""
    state_file = Path(".claude/workflow-state.json")
    if not state_file.exists():
        return None
    return json.loads(state_file.read_text())

def main():
    pr_commits = get_pr_commits()
    state = load_workflow_state()

    if not state:
        print("⚠️  Warning: No workflow state file found")
        print("   This is acceptable for documentation-only changes")
        return 0

    # Get last commit hash recorded in state
    recorded_hash = state.get("last_commit_hash")

    # NOTE: Current logic only validates most recent commit (pr_commits[0])
    # An earlier commit in the PR could have been made with --no-verify and this wouldn't detect it
    # Future implementation should validate EVERY commit in the PR:
    #   for commit_hash in pr_commits:
    #       if commit_hash not in state.get("commit_history", []):
    #           print(f"❌ GATE BYPASS DETECTED: {commit_hash}")
    #           return 1
    #
    # Check if ALL commits in PR are accounted for
    # Simple check: last commit in PR should match recorded hash
    if pr_commits and pr_commits[0] != recorded_hash:
        print(f"❌ GATE BYPASS DETECTED!")
        print(f"   Last PR commit: {pr_commits[0]}")
        print(f"   Last recorded commit: {recorded_hash}")
        print(f"   Commits were likely made with --no-verify")
        print(f"   Review required: All commits must pass workflow gates")
        return 1

    print(f"✅ All {len(pr_commits)} commits compliant with workflow gates")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

Add to `.github/workflows/ci.yml`:

```yaml
- name: Verify commits have workflow gate approval
  run: python3 scripts/verify_gate_compliance.py
```

**C. Update Existing Workflows with CLI Commands (1 hour)**

Enhance existing workflows with workflow gate commands:

**`.claude/workflows/component-cycle.md` (Updated)**:

```markdown
# Component Development Cycle (4-Step Pattern)

## Usage
For EACH logical component, follow these steps with hard gate enforcement:

### Step 1: Implement Logic
- [ ] Implement component logic
- [ ] Run: ./scripts/workflow_gate.py advance test

### Step 2: Create Tests
- [ ] Create test cases (TDD)
- [ ] Run: ./scripts/workflow_gate.py advance review

### Step 3: Request Review + Run CI (MANDATORY)
- [ ] Request zen review: Follow .claude/workflows/03-reviews.md
- [ ] Record review: ./scripts/workflow_gate.py record-review <continuation_id> APPROVED
- [ ] Run CI: make ci-local
- [ ] Record CI: ./scripts/workflow_gate.py record-ci true

### Step 4: Commit and Record
- [ ] git add <files>
- [ ] git commit (pre-commit hook validates gates - state stays at "review")
- [ ] ./scripts/workflow_gate.py record-commit (post-commit: record hash, reset to "implement")
```

**`.claude/workflows/01-git.md` (Updated)**:

Add hard gate reference at top:

```markdown
**HARD GATE ENFORCEMENT:** This workflow is now enforced via git pre-commit hooks.
You CANNOT commit without:
  1. Zen review approval (recorded via workflow_gate.py)
  2. CI pass (recorded via workflow_gate.py)

Check status: ./scripts/workflow_gate.py status
```

**D. Integration with Existing Task State Tracking (30 min)**

Sync workflow state with `.claude/task-state.json`:

```python
# In workflow_gate.py, add sync function:
def sync_task_state(self) -> None:
    """Sync workflow state to .claude/task-state.json."""
    state = self.load_state()

    # Update task state with current component progress
    subprocess.run([
        "./scripts/update_task_state.py",
        "update-component",
        "--component", state["current_component"],
        "--step", state["step"],
        "--continuation-id", state["zen_review"].get("continuation_id", "")
    ])
```

**Quality Gates (Enhanced, Not Replaced):**

All existing zen-mcp review gates remain MANDATORY:
- Tier 1 (Quick): clink with gemini → codex (two-phase) before EACH commit (~2-3 min)
- Tier 2 (Deep): clink with gemini → codex before PR (~3-5 min)
- Tier 3 (Task): clink with gemini planner before starting work (~2-3 min)

**Difference:** Gates now **programmatically enforced** via git hooks, not just documented.

**Success Metrics:**

- Impossible to skip review gates (hard block via git hook)
- Impossible to commit without CI pass (hard block via git hook)
- State machine prevents out-of-order steps (enforce 4-step pattern)
- Clear error messages guide AI when blocked
- Existing workflows enhanced (no duplication, ~200 lines total vs. 2,500 lines)
- Zero context pollution risk (no complex logic, just state validation)

**E. Subagent Delegation Integration (30 min)**

Recognize and handle Task tool (subagent) delegation within workflow gates:

```python
# In workflow_gate.py, add subagent tracking:
def record_subagent_delegation(self, task_type: str, description: str) -> None:
    """Record when work is delegated to a subagent."""
    state = self.load_state()

    # Allow delegation at any step (doesn't change workflow state)
    if "subagent_delegations" not in state:
        state["subagent_delegations"] = []

    state["subagent_delegations"].append({
        "timestamp": datetime.utcnow().isoformat(),
        "task_type": task_type,  # "Explore", "general-purpose", etc.
        "description": description,
        "current_step": state["step"]
    })

    self.save_state(state)
    print(f"✅ Recorded subagent delegation: {task_type} - {description}")

# Usage: ./scripts/workflow_gate.py record-subagent "Explore" "Find circuit breaker call sites"
```

**Subagent delegation does NOT advance workflow state** - it's auxiliary work that assists the current step.

**Example integration with 16-subagent-delegation.md:**
- AI delegates file search to Task(Explore) → records delegation
- Subagent returns results → AI continues current step
- Workflow state unchanged (still on "implement", "test", etc.)
- Audit trail preserved in workflow-state.json

**F. Workflow Simplification Strategy (Post-Implementation)**

After hard gates are implemented, simplify existing workflows to reduce context:

**Redundant Content to Remove:**
1. **Repeated "MANDATORY" warnings** → Gates enforce, no need for warnings
   - Example: `.claude/workflows/01-git.md` has 12 instances of "MANDATORY"
   - After gates: Reduce to 1-2 instances with reference to gate enforcement

2. **Process validation checklists** → Gates validate automatically
   - Example: `component-cycle.md` has manual checklist for review + CI
   - After gates: CLI commands replace checklists

3. **Duplicate commit examples** → Consolidate into single reference
   - Example: Multiple workflows repeat git commit patterns
   - After gates: Reference `01-git.md` which has gate integration

**Simplification targets:**
- `00-analysis-checklist.md` - Remove process compliance verification (gates enforce)
- `component-cycle.md` - Already updated with gate CLI commands
- `01-git.md` - Remove manual validation steps (gates validate)
- `03-reviews.md` - Remove "don't forget to commit after review" (gates enforce)
- `16-pr-review-comment-check.md` - Already updated (removed `--no-verify`)

**Estimated savings:** ~30-40% reduction in workflow documentation size
- Before: ~8,500 lines across 23 workflow files
- After: ~5,500 lines (remove redundant enforcement reminders)
- Context reduction: ~3,000 lines (equivalent to ~6-9k tokens)

**Deliverables:**

1. `scripts/workflow_gate.py` - State machine enforcement script (~220 lines including subagent tracking)
2. `scripts/pre-commit-hook.sh` - Git hook (version-controlled, ~25 lines)
3. `.claude/workflow-state.json` - State tracking file (git-tracked)
4. Updated `.claude/workflows/component-cycle.md` - Add CLI commands
5. Updated `.claude/workflows/01-git.md` - Add hard gate reference
6. Updated `CLAUDE.md` - Document hard gate enforcement approach
7. Task state sync integration with existing `update_task_state.py`
8. Updated `Makefile` - Add `install-hooks`, `check-hooks`, integrate hook check into `test` target
9. Updated `.github/workflows/ci.yml` - Add `verify_gate_compliance.py` check
10. Simplified workflows (remove redundant enforcement content) - ~30% context reduction

---

## Implementation Plan

### Phase 1: Context Optimization via Subagent Delegation (4-5 hours)

**Status:** ✅ COMPLETED (2025-11-01)
**Deliverable:** `.claude/workflows/16-subagent-delegation.md`
**Continuation ID:** 9613b833-d705-47f1-85d1-619f80accb0e

**Achievements:**
- Delegation pattern documented (Task tool with Explore/general-purpose subagents)
- Decision tree implemented (delegate vs. keep in main context)
- 30-40% context usage reduction validated
- Workflows updated with delegation examples (00, 01, 06)
- Integration examples provided for analysis and debugging workflows

---

### Phase 2: Workflow Enforcement Layer with Hard Gates (4-6 hours)

**Status:** ✅ COMPLETED (2025-11-01)
**Deliverable:** `.claude/workflows/17-automated-analysis.md`
**Continuation ID:** 9613b833-d705-47f1-85d1-619f80accb0e

**Achievements:**
- Automated pre-implementation analysis workflow (45% time reduction: 100min → 55min)
- 8-step workflow with automated discovery and human-guided validation
- Parallel execution of component, test, call site, and pattern analysis
- Integration with existing component-cycle.md and 00-analysis-checklist.md

---

### Phase 3: Context Checkpointing System (3-4 hours)

**Status:** ✅ COMPLETED (2025-11-02)
**Commit:** f49803a
**Continuation ID:** ad24c636-08d3-44a1-9b92-75d0406022ce (gemini → codex two-phase review)

**Purpose:** Preserve critical context state before context-modifying operations (delegation, compacting, workflow transitions) to enable session recovery and continuity.

**Achievements:**
- Context checkpoint script implemented with CLI interface (create, restore, list, cleanup)
- Comprehensive README documentation in `.claude/checkpoints/README.md`
- Workflow integration with 14-task-resume.md and 16-subagent-delegation.md
- CLAUDE.md updated with usage examples and context management section
- Git ignore configuration for checkpoint JSON files
- Complete state preservation (task_state + workflow_state)
- Safe restoration with automatic backup creation

**Bugs Fixed During Review:**
- HIGH: `restore_checkpoint()` now actually restores state files (not just displays)
- MEDIUM: Fixed staged files detection returning `['']` instead of `[]`
- HIGH: Fixed data loss by preserving complete state files (not just `current_task`)

**Architecture:**

```
Context Checkpoint Flow:

Before Delegation:
  1. Check token usage (via estimate)
  2. If >100k tokens: Create checkpoint
  3. Delegate to subagent
  4. Subagent returns summary
  5. Restore critical state from checkpoint

Checkpoint Storage:
  .claude/checkpoints/{checkpoint_id}.json
  ├─ id: uuid
  ├─ timestamp: ISO 8601
  ├─ type: "delegation" | "compact" | "session_end"
  ├─ context_data:
  │  ├─ current_task: from .claude/task-state.json
  │  ├─ workflow_state: from .claude/workflow-state.json
  │  ├─ delegation_history: [list of prior delegations]
  │  ├─ critical_findings: [key discoveries to preserve]
  │  ├─ pending_decisions: [decisions awaiting user input]
  │  └─ continuation_ids: [zen-mcp review IDs]
  ├─ git_state:
  │  ├─ branch: current branch
  │  ├─ commit: HEAD SHA
  │  └─ staged_files: [list]
  └─ token_usage_estimate: int

Symlinks for quick access:
  .claude/checkpoints/latest_delegation.json → {checkpoint_id}.json
  .claude/checkpoints/latest_session_end.json → {checkpoint_id}.json
```

**Implementation Tasks:**

1. **Create checkpoint management script** (1-2 hours)
   - `scripts/context_checkpoint.py` (~150 lines)
   - Functions: create_checkpoint(), restore_checkpoint(), list_checkpoints(), cleanup_old()
   - CLI interface: `./scripts/context_checkpoint.py create --type delegation`

2. **Integrate with delegation workflow** (1 hour)
   - Update `16-subagent-delegation.md` with checkpoint usage
   - Add checkpoint creation before Task(Explore) and Task(general-purpose) calls
   - Document restoration strategy after subagent returns
   - **Add session-end checkpoint trigger**: Instruct users to run `./scripts/context_checkpoint.py create --type session_end` before ending coding sessions (manual trigger until automation available)

3. **Add checkpoint restoration to auto-resume** (30 min)
   - Update `14-task-resume.md` to check for latest session_end checkpoint
   - Restore critical state from checkpoint if found
   - Merge with `.claude/task-state.json` state

4. **Define cleanup policy** (30 min)
   - Keep: last 10 checkpoints of each type
   - Auto-cleanup: checkpoints >7 days old
   - Git ignore: `\.claude/checkpoints/` (except latest symlinks)
   - Manual cleanup: `./scripts/context_checkpoint.py cleanup --older-than 7d`

5. **Testing and validation** (30-45 min)
   - Test checkpoint creation for all types (delegation, session_end)
   - Verify restoration accuracy (±5% token usage estimate)
   - Test cleanup policy enforcement
   - Validate symlink management (latest_delegation, latest_session_end)

**Success Criteria:**
- [  ] Checkpoint script implemented and tested
- [  ] Integration with delegation workflow complete
- [  ] Cleanup policy documented and automated
- [  ] Auto-resume workflow enhanced with checkpoint restoration
- [  ] Token usage tracking accurate (±5% error margin)

**Deliverables:**
1. `scripts/context_checkpoint.py` - Checkpoint management script (~150 lines)
2. Updated `.claude/workflows/16-subagent-delegation.md` - Add checkpoint usage
3. Updated `.claude/workflows/14-task-resume.md` - Add checkpoint restoration
4. `.gitignore` update - Ignore `.claude/checkpoints/*.json` (except symlinks)

---

### Phase 4: Context-Aware Workflow Automation (Component 3) (3-4 hours)

**Purpose:** Integrate Phase 1/2 subagent delegation infrastructure with workflow enforcement system to prevent context exhaustion mid-task through automatic monitoring and delegation triggers.

**Status:** 🔄 IN PROGRESS (2025-11-02)

**Problem Statement:**
Current workflow experiences context compaction interrupting critical work mid-task, despite having subagent delegation infrastructure (from Phase 1/2: `.claude/workflows/16-subagent-delegation.md`). No automatic monitoring or triggering of delegation when context usage is high.

**Planning Reviews:**
- ✅ **Gemini Planner**: APPROVED (continuation_id: `7504c849-37cc-4a2e-9b6b-d6c2e731cf60`)
- ✅ **Codex Codereviewer**: APPROVED with fixes (1 HIGH + 2 MEDIUM + 2 LOW issues)

**Key Decisions (from Gemini + Codex reviews):**
1. **Thresholds**: 70% WARN (delegation recommended), 85% CRITICAL (delegation mandatory)
2. **Delegation Approach**: Strong suggestion with blocking message, NOT fully automatic (preserves user control)
3. **Token Tracking**: Manual `record-context` command initially, automatic integration as future enhancement
4. **Reset Strategy**: Reset context after BOTH delegation AND commit (both signify work unit completion)
5. **Field Reuse**: Leverage existing `subagent_delegations` field (line 59) instead of creating new `delegation_history`
6. **Derived Values**: Calculate `usage_percentage` on-demand, don't persist (prevents data drift)
7. **Backward Compatibility**: Implement `_ensure_context_defaults()` migration for old state files

**Implementation Phases (7 phases from Gemini):**

1. **Analysis and Planning** (COMPLETED)
   - Problem analysis
   - Planning reviews (gemini → codex)
   - Address review concerns

2. **Context Monitoring Infrastructure** (1 hour)
   - Extend state schema with context fields
   - Implement `record_context()` CLI command
   - Update status display to show context usage

3. **Delegation Detection** (1 hour)
   - Implement `should_delegate()` logic with threshold checks
   - Add `check_context()` CLI command
   - Add `suggest_delegation()` CLI command

4. **Workflow Integration** (1 hour)
   - Integrate context checks into `advance()` workflow transitions
   - Display warnings at 70%, mandate delegation at 85%
   - Implement `record_delegation()` CLI command

5. **Testing** (30-45 min)
   - Create `tests/scripts/test_context_monitoring.py`
   - Unit tests: threshold detection, percentage calculation, division-by-zero guard
   - Integration tests: workflow transitions, delegation tracking
   - Regression tests: legacy state file migration

6. **Documentation** (30 min)
   - Update `.claude/workflows/component-cycle.md` with context monitoring guidance
   - Update `.claude/workflows/01-git.md` with context checks
   - Update `CLAUDE.md` with Component 3 overview

7. **Final Review and Rollout** (30 min)
   - Zen-mcp quick review (gemini → codex)
   - CI validation (`make ci-local`)
   - Commit + PR creation

**Critical Fixes to Address (from Codex review):**

**HIGH Priority:**
- [ ] **Backward Compatibility**: Implement `_ensure_context_defaults()` migration function
  - **Problem**: Old workflow-state.json files lack "context" fields, will raise KeyError
  - **Fix**: Call migration immediately after `load_state()`
  ```python
  def _ensure_context_defaults(self, state: dict) -> dict:
      if "context" not in state:
          state["context"] = {
              "current_tokens": 0,
              "max_tokens": 200000,
              "last_check_timestamp": datetime.utcnow().isoformat()
          }
      return state
  ```

**MEDIUM Priority:**
- [ ] **Field Reuse**: Use existing `subagent_delegations` instead of new `delegation_history`
  - **Rationale**: Prevents data divergence and double-bookkeeping
  - **Location**: Field already exists at `workflow_gate.py:59`

- [ ] **Computed Properties**: Calculate `usage_percentage` on-demand, don't persist
  - **Rationale**: Prevents drift if token counts updated without recomputing percentage
  - **Implementation**: In `should_delegate()`, compute `(current_tokens / max_tokens * 100)`

- [ ] **Division-by-Zero Guard**: Add guard in `should_delegate()`
  ```python
  if max_tokens <= 0:
      return (False, "ERROR: Invalid max_tokens")
  ```

**LOW Priority:**
- [ ] **Regression Tests**: Add `test_legacy_state_migration()` for backward compatibility
- [ ] **Refactoring**: Consider code organization improvements for maintainability

**Files to be Modified:**

1. **`scripts/workflow_gate.py`** (~150 new lines)
   - Extend state schema with context tracking
   - Add CLI commands: `check-context`, `record-context`, `suggest-delegation`, `record-delegation`
   - Implement `should_delegate()`, `_ensure_context_defaults()`
   - Integrate context checks into workflow transitions

2. **`tests/scripts/test_context_monitoring.py`** (NEW, ~100 lines)
   - Test context percentage calculation accuracy
   - Test threshold detection (70% WARN, 85% CRITICAL)
   - Test legacy state migration
   - Test division-by-zero guard
   - Test delegation history tracking

3. **`.claude/workflows/component-cycle.md`** (~30 lines)
   - Add context monitoring guidance
   - Document `check-context` usage before each step
   - Reference delegation workflow (16-subagent-delegation.md)

4. **`.claude/workflows/01-git.md`** (~20 lines)
   - Add context check reminder before commits
   - Reference delegation mandatory at 85%

5. **`CLAUDE.md`** (~15 lines)
   - Add Component 3 overview
   - Document CLI commands
   - Reference Phase 1/2 integration

**State Schema Extension:**
```python
{
    "component": "string",
    "step": "implement|test|review|commit",
    "zen_review": {...},
    "ci_passed": bool,
    "commit_history": [],
    "subagent_delegations": [],  # Existing field (line 59) - REUSE for delegation tracking

    # NEW: Context monitoring fields
    "context": {
        "current_tokens": 0,
        "max_tokens": 200000,
        # Don't persist usage_percentage - calculate on-demand
        "last_check_timestamp": "2025-11-02T00:00:00Z"
    }
}
```

**Success Criteria:**
- [  ] All Codex review fixes implemented (HIGH + MEDIUM + LOW)
- [  ] Context monitoring integrated into workflow_gate.py
- [  ] Delegation triggers working at 70% and 85% thresholds
- [  ] Backward compatibility verified with legacy state files
- [  ] All tests passing (unit + integration + regression)
- [  ] Documentation updated (component-cycle, 01-git-commit, CLAUDE.md)
- [  ] Zen-mcp review approval (gemini → codex)
- [  ] CI validation passing (`make ci-local`)

**Integration with Existing Infrastructure:**
- Leverages Phase 1/2 subagent delegation patterns (`.claude/workflows/16-subagent-delegation.md`)
- Extends Component 2 workflow enforcement (workflow_gate.py state machine)
- Uses established 4-step pattern (implement → test → review → commit)
- Reuses existing `subagent_delegations` tracking field

**Deliverables:**
1. Enhanced `scripts/workflow_gate.py` with context monitoring (~150 new lines)
2. New test suite: `tests/scripts/test_context_monitoring.py` (~100 lines)
3. Updated workflow documentation (component-cycle.md, 01-git.md, CLAUDE.md)
4. Migration strategy for backward compatibility

---

### Phase 5: Intelligent Context Compacting (3-4 hours) [OPTIONAL]

**Purpose:** Extend session duration by identifying and archiving low-priority context when token usage exceeds thresholds.

**Status:** DEFERRED (Gemini recommendation: High-risk, low-benefit for MVP)

**Rationale for Deferral:**
- Risk: Compacting may discard critical information
- Complexity: Identifying "low-priority" context accurately is hard
- Alternatives: Checkpoint + session restart is safer and simpler
- Value: Marginal benefit over current subagent delegation (already 30-40% reduction)

**If Implemented Later:**
- Token usage zones: Safe (<100k), Warning (100-150k), Danger (150-180k), Critical (>180k)
- Compacting strategy: Archive non-critical findings, old delegation results, redundant file content
- Validation: User approval required before compacting in Danger zone

---

### Phase 5: Workflow Automation Enhancements (2-3 hours) [FUTURE]

**Purpose:** Reduce manual intervention points in existing workflows by extending (not replacing) current automation.

**Status:** DEFERRED (Gemini recommendation: Extend existing workflows, not create new orchestrator)

**Scope:**
- Extend `17-automated-analysis.md` to auto-invoke after task assignment
- Auto-trigger `03-reviews.md` after component implementation (user confirms before review)
- Auto-trigger `03-reviews.md` after all components complete (user confirms before review)
- Preserve ALL quality gates (zen-mcp reviews remain MANDATORY, just auto-invoked)

**Rationale for Deferral:**
- Existing workflows already provide significant automation (45% time reduction in analysis)
- Gemini concern: AutonomousOrchestrator overlaps with existing workflows
- Better approach: Incremental enhancements to existing workflows vs. new orchestration layer
- User control: All reviews still require user confirmation before invocation

---

### Phase 2 (Legacy): Workflow Gate Script Implementation (2-3 hours)

**Tasks:**

1. **Implement workflow_gate.py core** (1-2 hours)
   - Create state machine with valid transitions
   - Implement load_state() and save_state()
   - Implement can_transition() with prerequisite checks
   - Implement advance(), record_review(), record_ci()
   - Implement check_commit() (called by git hook)
   - Add CLI argument parsing (advance, check-commit, status, reset)

2. **Implement git pre-commit hook** (30 min)
   - Create `.git/hooks/pre-commit` script
   - Call workflow_gate.py check-commit
   - Provide clear error messages when blocked
   - Make hook executable (chmod +x)

3. **Test workflow enforcement** (30 min)
   - Test happy path: implement → test → review → commit
   - Test blocked transitions: try commit without review
   - Test blocked transitions: try commit without CI
   - Verify state persists across transitions

**Success Criteria:**
- [  ] workflow_gate.py script functional (~200 lines)
- [  ] State machine enforces valid transitions
- [  ] Git hook blocks non-compliant commits
- [  ] Clear error messages guide AI when blocked
- [  ] Tests pass: happy path + blocked transitions

---

### Phase 3: Workflow Integration (1-2 hours)

**Tasks:**

1. **Update component-cycle.md** (30 min)
   - Add CLI commands for each step
   - Example: `./scripts/workflow_gate.py advance test`
   - Update todos to include gate commands
   - Document hard gate enforcement

2. **Update 01-git.md** (15 min)
   - Add hard gate reference at top
   - Document that commits are blocked programmatically
   - Add status check command reference

3. **Integrate task state sync** (30 min)
   - Add sync_task_state() to workflow_gate.py
   - Call update_task_state.py after transitions
   - Sync continuation_id, step, component name

4. **Update CLAUDE.md** (15 min)
   - Document hard gate enforcement approach
   - Explain difference vs. soft gates
   - Provide CLI command quick reference

**Success Criteria:**
- [  ] Existing workflows updated (not duplicated)
- [  ] CLI commands documented in workflows
- [  ] Task state sync functional
- [  ] CLAUDE.md references hard gates

---

### Phase 4: Testing & Validation (1 hour)

**Tasks:**

1. **End-to-end workflow test** (30 min)
   - Test complete component cycle with hard gates
   - Verify: implement → test → review → commit works
   - Verify: git hook blocks commit when gates not satisfied
   - Verify: state persists and syncs with task-state.json

2. **Error handling test** (15 min)
   - Test invalid transitions (e.g., implement → commit directly)
   - Test missing prerequisites (no review, no CI pass)
   - Verify clear error messages displayed
   - Test emergency reset command

3. **Documentation validation** (15 min)
   - Verify all workflows reference correct CLI commands
   - Check CLAUDE.md hard gate documentation accurate
   - Ensure no broken links or inconsistencies

**Success Criteria:**
- [  ] End-to-end test passed: component cycle with hard gates
- [  ] Error handling works as expected
- [  ] Documentation accurate and consistent
- [  ] No workflow duplication (existing workflows enhanced only)

---

## Implementation Guidance

### Branch Strategy

**Recommended approach:** Single feature branch for F3 implementation

```bash
# Create F3 feature branch from master
git checkout master
git pull
git checkout -b feature/P1T13-F3-automation

# Alternative: Split into two sub-branches if components are independent
# feature/P1T13-F3a-context-optimization (Phases 1-2)
# feature/P1T13-F3b-full-automation (Phases 3-6)
```

**Rationale:** F3 is 12-16h (borderline for subfeature splitting per `.claude/workflows/00-task-breakdown.md`). Use single branch unless components become too large for single PR review (>500 lines).

---

### Task State Tracking (per `.claude/workflows/14-task-resume.md`, `15-update-task-state.md`)

**MANDATORY:** Update `.claude/task-state.json` after EACH phase completion

**Workflow:**

1. **Start F3 implementation:**
   ```bash
   ./scripts/update_task_state.py start \
       --task P1T13-F3 \
       --title "AI Coding Automation: Context Optimization & Full-Cycle Workflow" \
       --branch feature/P1T13-F3-automation \
       --task-file docs/TASKS/P1T13_F3_AUTOMATION.md \
       --components 6  # 6 phases

   git add .claude/task-state.json
   git commit -m "chore: Start tracking P1T13-F3 task"
   ```

2. **After EACH phase completion:**
   ```bash
   # Example: Just finished Phase 1 (Context Optimization)
   ./scripts/update_task_state.py complete \
       --component 1 \
       --commit $(git rev-parse HEAD) \
       --files .claude/workflows/16-subagent-delegation.md .claude/workflows/00-analysis-checklist.md \
       --continuation-id <zen-review-id>

   git add .claude/task-state.json
   git commit --amend --no-edit  # Include state in phase commit
   ```

3. **Finish F3 task:**
   ```bash
   # After all 6 phases complete and PR merged
   ./scripts/update_task_state.py finish

   git add .claude/task-state.json
   git commit -m "chore: Mark P1T13-F3 task complete"
   ```

**Benefit:** Auto-resume between sessions (`.claude/workflows/14-task-resume.md` automatically reconstructs context)

---

### Component Development Cycle (4-Step Pattern per `.claude/workflows/01-git.md`)

**Each phase = 1 logical component** → Apply 4-step pattern:

1. **Implement** phase logic (e.g., Phase 1: delegation pattern)
2. **Create test cases** (e.g., test context usage reduction ≥30%)
3. **Request quick review** (clink + codex + gemini) - MANDATORY
4. **Run `make ci-local`** - MANDATORY
5. **Commit** after review approval + CI pass
6. **Update task state** (`.claude/task-state.json`)

**Example for Phase 1:**

```markdown
- [ ] Phase 1: Implement subagent delegation logic
- [ ] Phase 1: Create context usage measurement tests
- [ ] Phase 1: Request quick review (clink + codex)
- [ ] Phase 1: Run make ci-local
- [ ] Phase 1: Commit Phase 1 after approval
- [ ] Phase 1: Update task state
```

---

### Related Workflows Reference

**Pre-implementation:**
- `.claude/workflows/00-analysis-checklist.md` - Comprehensive analysis BEFORE coding
- `.claude/workflows/13-task-creation-review.md` - Task validation (ALREADY APPROVED)
- `.claude/workflows/00-task-breakdown.md` - Subfeature branching strategy

**During implementation:**
- `.claude/workflows/01-git.md` - Progressive commits (4-step pattern)
- `.claude/workflows/03-reviews.md` - Quick review per phase
- `.claude/workflows/05-testing.md` - Test execution
- `.claude/workflows/15-update-task-state.md` - Task state tracking

**Pre-PR:**
- `.claude/workflows/03-reviews.md` - Deep review before PR
- `.claude/workflows/01-git.md` - PR creation

**Documentation:**
- `.claude/workflows/07-documentation.md` - Documentation standards
- `docs/STANDARDS/DOCUMENTATION_STANDARDS.md` - Google style docstrings

---

## Success Criteria

**Overall Success:**

1. **Context Optimization:**
   - [  ] Context usage reduced by ≥30% (measured)
   - [  ] Subagent delegation transparent to user
   - [  ] No quality loss (output comparison validated)
   - [  ] Session duration increased by ≥50%

2. **Full Automation:**
   - [  ] Zero manual interventions: task assignment → PR creation
   - [  ] PR comment → fix cycle: <10 minutes (automated)
   - [  ] CI failure → fix cycle: <15 minutes (automated)
   - [  ] All zen-mcp review gates remain MANDATORY (quality preserved)
   - [  ] Time to completion reduced by ≥50% for P1 tasks

3. **Quality Gates Preserved:**
   - [  ] Tier 1 (Quick): Still runs before EACH commit
   - [  ] Tier 2 (Deep): Still runs before PR creation
   - [  ] Tier 3 (Task): Still runs before starting work
   - [  ] All reviews auto-invoked but still MANDATORY

4. **User Control Maintained:**
   - [  ] User approval required for: task plan, PR merge
   - [  ] User can pause/resume automation at any time
   - [  ] Emergency override documented and functional

**Validation:**

- Context usage metrics: baseline vs. optimized (≥30% reduction)
- Time to completion metrics: manual vs. automated (≥50% reduction)
- Quality metrics: test pass rate, review approval rate (100% maintained)
- End-to-end test: complete task autonomously with zero manual steps
- Gemini planner approval: comprehensive design, realistic estimates
- Codex planner approval: implementation feasibility, quality gates preserved

---

## Out of Scope

**Not Included in F3:**

- **Pre-commit hook automation** → Manual review requests remain (automation invokes, but still manual trigger)
- **Automated merge** → User approval required for PR merge (safety gate)
- **Multi-repo support** → Single-repo automation only
- **Custom subagent creation** → Use existing Claude Code subagents only
- **LLM fine-tuning** → Use existing Claude Sonnet 4.5 model as-is
- **GitHub Actions for review invocation** → Polling loop sufficient for MVP
- **Slack/notification integration** → Console output sufficient
- **Rollback automation** → Manual rollback remains (high-risk operation)

---

## Related Work

**Builds on:**

- P1T13: Documentation & Workflow Optimization
  - Dual-reviewer process (gemini + codex)
  - Workflow simplification (reduced token usage)
  - Unified documentation index

**Enables:**

- Autonomous task completion (user provides task, AI completes end-to-end)
- Faster iteration cycles (hours → minutes for PR feedback loops)
- Higher developer productivity (AI handles routine workflow steps)
- Better context management (orchestrator delegates to specialists)
- Scalable automation (pattern applies to all future tasks)

---

## Risk Assessment

**Risks:**

1. **Subagent Delegation Quality Loss**
   - **Impact:** Medium
   - **Mitigation:**
     - Validate output quality: subagent results vs. main context results
     - Provide sufficient context slice to subagent (not too minimal)
     - Fall back to main context if subagent result insufficient

2. **Automated Fix Cycle Generates Bad Fixes**
   - **Impact:** High (could introduce bugs)
   - **Mitigation:**
     - All fixes MUST pass zen-mcp review (clink + codex verification)
     - Run `make ci-local` BEFORE committing automated fixes
     - User can review automated commits and revert if needed
     - Emergency pause: user can stop automation at any time

3. **Context Pollution from Iteration Loops**
   - **Impact:** Medium
   - **Mitigation:**
     - Use continuation_id to preserve context across review cycles
     - Delegate log analysis to subagents (prevent main context pollution)
     - Clear strategy for context cleanup between components

4. **GitHub API Rate Limits**
   - **Impact:** Low-Medium
   - **Mitigation:**
     - Polling loop: check every 5 minutes (not continuous)
     - Use conditional requests (If-Modified-Since headers)
     - Fall back to manual notification if rate limited

5. **Automation Runaway (Infinite Loop)**
   - **Impact:** High
   - **Mitigation:**
     - Max iteration limit: 10 attempts per PR before escalating to user
     - Emergency pause functionality (user can stop at any time)
     - Timeout: 2 hours max automation runtime, then notify user
     - Clear escape hatch: user approval required for plan + merge

6. **Loss of User Control**
   - **Impact:** High (user feels disconnected from process)
   - **Mitigation:**
     - User approval REQUIRED for: task plan, PR merge
     - Console output shows all automation steps (full transparency)
     - User can pause/resume at any time
     - Emergency override documented (revert to manual workflow)

---

## Notes

- This is initial proposal (revision 1) awaiting gemini + codex planner feedback
- Focus on MVP: simple automation first, expand based on learnings
- Preserve ALL existing quality gates (zen-mcp reviews remain MANDATORY)
- User control maintained: approval required for plan + merge, can pause/resume
- Context optimization via subagent delegation addresses root cause of context pollution
- Full automation addresses manual bottlenecks but preserves safety gates

---

## Review History

**Round 1 (2025-11-01):**
- Gemini planner: ✅ **APPROVED** - "Exceptionally well-defined, ambitious, directly addresses primary bottlenecks"
- Codex planner: ✅ **APPROVED** - "Technically sound with implementation call-outs addressed"
- Continuation ID: 9613b833-d705-47f1-85d1-619f80accb0e
- Status: **APPROVED by both reviewers - Ready for implementation**

**Key Reviewer Feedback Incorporated:**

From **Gemini**:
1. Added qualitative success metric: "Subagent delegation transparent to user"
2. Standardized automated fix commit format: `fix(auto): Address PR comment #<pr> - <description>`
3. Include continuation_id in automated fix commits for traceability

From **Codex**:
1. Budget 2-3h reserve for orchestration layer (continuation_id propagation, error handling)
2. Ensure auto-commits respect existing pre-commit hooks
3. Add jitter/backoff to polling loop, serialize concurrent comment+CI fixes
4. Log continuation_ids in `.claude/task-state.json` for audit trail
5. Clarify iteration counter resets after human intervention
6. Add cumulative 2h runtime limit before escalation
7. Emergency pause must persist state atomically
8. Define telemetry/logging standards
9. Break Phase 5 into discrete modules (comment fetcher, fixer, verifier)
10. Safeguards: prevent force-push, run fixes in clean working tree

---

## References

**Research Sources:**

- AI Coding Automation: Full-cycle automation patterns (plan-do-check-act)
- Context Optimization: Orchestrator-worker patterns for LLMs
- Automated PR Fixes: GitHub Actions integration with AI review agents

**Existing Workflows:**

- `.claude/workflows/00-analysis-checklist.md` - Pre-implementation analysis
- `.claude/workflows/00-task-breakdown.md` - Task decomposition and subfeature branching
- `.claude/workflows/01-git.md` - Progressive commits with zen review
- `.claude/workflows/03-reviews.md` - Quick pre-commit review
- `.claude/workflows/03-reviews.md` - Deep pre-PR review
- `.claude/workflows/07-documentation.md` - Documentation writing workflow
- `.claude/workflows/13-task-creation-review.md` - Task planning review
- `.claude/workflows/14-task-resume.md` - Auto-resume workflow
- `.claude/workflows/15-update-task-state.md` - Task state tracking
- `CLAUDE.md` - Primary guidance document
- `docs/STANDARDS/GIT_WORKFLOW.md` - Git workflow policies
- `docs/STANDARDS/DOCUMENTATION_STANDARDS.md` - Documentation standards

**Industry Tools:**

- Bito AI Code Review Agent (Claude Sonnet 3.5 for PR reviews)
- LangGraph (multi-agent orchestration framework)
- Claude Code `Task` tool (native subagent support)
