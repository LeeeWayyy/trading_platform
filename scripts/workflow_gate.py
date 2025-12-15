#!/usr/bin/env python3
"""
Workflow Gate CLI Entry Point.

This script outputs instructions and state - agent executes MCP tools.

Addresses review feedback:
- G1: Uses constants from constants.py
- G2: Integrates migration in load_state()
- G3: Accepts --summary-file for complex JSON
- H7: Atomic state updates with StateTransaction
- Gemini: Uses WorkflowGate class for file locking (fcntl)
"""

import argparse
import json
import re
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from ai_workflow.config import WorkflowConfig
from ai_workflow.constants import LEGACY_CLAUDE_DIR, LEGACY_STATE_FILE, STATE_FILE, WORKFLOW_DIR
from ai_workflow.core import (
    WorkflowGate,
    WorkflowGateBlockedError,
    WorkflowTransitionError,
    WorkflowValidationError,
    migrate_v1_to_v2,
)
from ai_workflow.pr_workflow import PRWorkflowHandler
from ai_workflow.subtasks import AgentInstruction, SubtaskOrchestrator

# Module-level WorkflowGate instance for file locking
# Uses fcntl for cross-process atomic operations
_gate = WorkflowGate(state_file=STATE_FILE)

# =============================================================================
# Input Validation (C4)
# =============================================================================

# Valid patterns for user input sanitization
BRANCH_NAME_PATTERN = re.compile(r"^[\w./-]+$")
COMPONENT_NAME_PATTERN = re.compile(r"^[\w\s.-]+$")
CONTINUATION_ID_PATTERN = re.compile(r"^[\w-]+$")
VALID_REVIEWERS = {"claude", "gemini", "codex"}
VALID_REVIEW_STATUSES = {"approved", "changes_requested", "pending", "error"}


def _validate_branch_name(branch: str) -> str:
    """
    Validate git branch name format.

    Addresses C4: Input validation for branch names.
    """
    if not branch:
        raise ValueError("Branch name cannot be empty")
    if len(branch) > 255:
        raise ValueError("Branch name too long (max 255 chars)")
    if not BRANCH_NAME_PATTERN.match(branch):
        raise ValueError(
            f"Invalid branch name '{branch}'. "
            "Must contain only alphanumeric, dots, slashes, hyphens."
        )
    if branch.startswith("/") or branch.endswith("/"):
        raise ValueError("Branch name cannot start or end with '/'")
    if "//" in branch:
        raise ValueError("Branch name cannot contain consecutive slashes")
    return branch


def _validate_component_name(name: str) -> str:
    """
    Validate component name format.

    Addresses C4: Input validation for component names.
    """
    if not name:
        raise ValueError("Component name cannot be empty")
    if len(name) > 100:
        raise ValueError("Component name too long (max 100 chars)")
    if not COMPONENT_NAME_PATTERN.match(name):
        raise ValueError(
            f"Invalid component name '{name}'. "
            "Must contain only alphanumeric, spaces, dots, hyphens, underscores."
        )
    return name.strip()


def _validate_continuation_id(cont_id: str) -> str:
    """
    Validate continuation ID format.

    Addresses C4: Input validation for continuation IDs.
    """
    if not cont_id:
        raise ValueError("Continuation ID cannot be empty")
    if len(cont_id) > 100:
        raise ValueError("Continuation ID too long (max 100 chars)")
    if not CONTINUATION_ID_PATTERN.match(cont_id):
        raise ValueError(
            f"Invalid continuation ID '{cont_id}'. " "Must contain only alphanumeric and hyphens."
        )
    return cont_id


def _validate_reviewer(
    reviewer: str, config: WorkflowConfig | None = None, warn_if_disabled: bool = True
) -> str:
    """
    Validate reviewer name.

    Addresses C4: Input validation for reviewer names.
    Codex MEDIUM fix: Uses config.available instead of hardcoded list.
    Codex LOW fix: Warns if reviewer is available but not enabled.

    Args:
        reviewer: Reviewer name to validate
        config: Optional WorkflowConfig for dynamic validation
        warn_if_disabled: If True, print warning when reviewer is available but disabled
    """
    reviewer_lower = reviewer.lower()

    # Use config.available if provided, fallback to default set
    if config:
        available = config.config.get("reviewers", {}).get("available", list(VALID_REVIEWERS))
        available_lower = {r.lower() for r in available}
        enabled = config.get_enabled_reviewers()
        enabled_lower = {r.lower() for r in enabled}
    else:
        available_lower = VALID_REVIEWERS
        enabled_lower = {"gemini", "codex"}  # Default enabled

    if reviewer_lower not in available_lower:
        raise ValueError(
            f"Invalid reviewer '{reviewer}'. "
            f"Valid options: {', '.join(sorted(available_lower))}"
        )

    # Codex LOW fix: Warn if reviewer is available but not enabled
    if warn_if_disabled and reviewer_lower not in enabled_lower:
        print(
            f"Warning: Reviewer '{reviewer_lower}' is available but not enabled. "
            f"Enabled reviewers: {', '.join(sorted(enabled_lower))}",
            file=sys.stderr,
        )

    return reviewer_lower


def _validate_task_file(task_file: str) -> str:
    """
    Validate task file path.

    Addresses C4: Input validation and path traversal prevention.
    """
    if not task_file:
        raise ValueError("Task file path cannot be empty")

    # Prevent path traversal
    if ".." in task_file:
        raise ValueError("Task file path cannot contain '..'")

    # Ensure it's a reasonable file path
    if len(task_file) > 500:
        raise ValueError("Task file path too long (max 500 chars)")

    return task_file


# =============================================================================
# State Management with Atomic Updates (H7 + Gemini: fcntl locking)
# =============================================================================


@contextmanager
def state_transaction():
    """
    Context manager for atomic state updates with file locking.

    Uses WorkflowGate's fcntl-based locking for cross-process safety.

    Addresses:
    - C2: Safe rollback mechanism (don't save on exception)
    - Gemini: File locking via WorkflowGate._acquire_lock/release_lock
    """
    lock_fd = _gate._acquire_lock()
    try:
        state = load_state()
        yield state
        save_state(state)  # Only save if no exception
    except Exception:
        # On exception, don't save - file remains unchanged
        raise
    finally:
        _gate._release_lock(lock_fd)


def migrate_state_v1_to_v2(v1_state: dict) -> dict:
    """Migrate v1 state to v2 schema.

    Gemini LOW fix: Delegates to core.py for single source of truth.
    """
    return migrate_v1_to_v2(v1_state)


def load_state() -> dict:
    """
    Load workflow state, migrating from legacy location if needed.

    Addresses G2: Automatic migration from .claude/ to .ai_workflow/
    Addresses Codex MEDIUM: JSON error handling for corrupted files
    """
    # Check for existing state
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                state = json.load(f)
        except json.JSONDecodeError as e:
            # Codex MEDIUM fix: Handle corrupted JSON gracefully
            _log(f"Warning: Corrupted state file, reinitializing: {e}")
            return _fresh_state()
        except OSError as e:
            _log(f"Warning: Could not read state file: {e}")
            return _fresh_state()

        # Check if migration needed
        if state.get("version") != "2.0":
            state = migrate_state_v1_to_v2(state)
            save_state(state)
        return state

    # Check for legacy state to migrate
    if LEGACY_STATE_FILE.exists():
        _log("Migrating state from .claude/ to .ai_workflow/...")
        try:
            with open(LEGACY_STATE_FILE) as f:
                v1_state = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            _log(f"Warning: Could not read legacy state, starting fresh: {e}")
            return _fresh_state()

        # Backup legacy folder
        backup_path = LEGACY_CLAUDE_DIR.parent / ".claude.backup"
        if not backup_path.exists():
            import shutil

            shutil.copytree(LEGACY_CLAUDE_DIR, backup_path)
            _log(f"Backed up legacy folder to {backup_path}")

        state = migrate_state_v1_to_v2(v1_state)
        save_state(state)
        return state

    # Return fresh state
    return _fresh_state()


def _fresh_state() -> dict:
    """Return a fresh default state."""
    return {
        "version": "2.0",
        "phase": "component",
        "component": {"current": "", "step": "plan", "list": []},
        "pr_review": {"step": "pr-pending", "iteration": 0},
        "reviewers": {},
        "ci": {},
        "git": {"commits": [], "pr_commits": []},
        "subtasks": {"queue": [], "completed": [], "failed": []},
    }


def save_state(state: dict) -> None:
    """
    Save state atomically using temp file + rename.

    Addresses H7: Atomic write prevents corruption on crash.
    """
    WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)

    # Write to temp file first
    temp_file = STATE_FILE.with_suffix(".tmp")
    with open(temp_file, "w") as f:
        json.dump(state, f, indent=2)

    # Atomic rename
    temp_file.rename(STATE_FILE)


def _log(message: str) -> None:
    """Log to stderr to keep stdout clean for JSON output."""
    print(message, file=sys.stderr)


# =============================================================================
# Component Phase Commands
# =============================================================================


def cmd_status(args):
    """Show current workflow status."""
    state = load_state()

    status = {
        "phase": state.get("phase", "component"),
        "component": state.get("component", {}),
        "step": state.get("component", {}).get("step", "plan"),
    }

    if state.get("phase") == "pr-review":
        status["pr_review"] = state.get("pr_review", {})

    print(json.dumps(status, indent=2))
    return 0


def cmd_start_task(args):
    """Start a new task."""
    # Validate inputs (C4)
    try:
        task_file = _validate_task_file(args.task_file)
        branch = _validate_branch_name(args.branch)
        base_branch = _validate_branch_name(args.base_branch) if args.base_branch else None
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    with state_transaction() as state:
        state["task_file"] = task_file
        state["git"]["branch"] = branch

        if base_branch:
            state["git"]["base_branch"] = base_branch

        state["component"] = {
            "current": "",
            "step": "plan",
            "list": [],
        }

        print(
            json.dumps(
                {
                    "success": True,
                    "task_file": task_file,
                    "branch": branch,
                }
            )
        )
    return 0


def cmd_set_component(args):
    """Set current component name."""
    # Validate inputs (C4)
    try:
        name = _validate_component_name(args.name)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    with state_transaction() as state:
        state["component"]["current"] = name

        # Add to list if not already there
        if name not in state["component"].get("list", []):
            if "list" not in state["component"]:
                state["component"]["list"] = []
            state["component"]["list"].append(name)

        print(f"Component set to: {name}")
    return 0


# Valid component phase transitions
COMPONENT_TRANSITIONS = {
    "plan": ["plan-review"],
    "plan-review": ["implement", "plan"],
    "implement": ["test"],
    "test": ["review", "implement"],
    "review": ["implement"],
}


def cmd_advance(args):
    """Advance to next workflow step.

    Gemini HIGH fix: Delegates to core.py WorkflowGate.advance() to reduce duplication.
    The core.py advance() method handles:
    - Plan-review approval gate with min_required support
    - Review clearing for code review phase
    - Proper error messages

    Gemini LOW fix: Catches exceptions from core.py instead of sys.exit.
    """
    config = WorkflowConfig()

    # Validate transition first (fast fail without locking)
    current = load_state()["component"].get("step", "plan")
    new_step = args.step

    if current not in COMPONENT_TRANSITIONS:
        print(f"Error: Unknown current step: {current}", file=sys.stderr)
        print("   See @docs/AI/Workflows/12-component-cycle.md for valid steps", file=sys.stderr)
        return 1

    if new_step not in COMPONENT_TRANSITIONS[current]:
        valid = COMPONENT_TRANSITIONS[current]
        print(
            f"Error: Cannot transition from {current} to {new_step}. " f"Valid: {valid}",
            file=sys.stderr,
        )
        print(
            "   See @docs/AI/Workflows/12-component-cycle.md for workflow transitions",
            file=sys.stderr,
        )
        return 1

    # Delegate to core.py for the actual advance (includes plan-review gate logic)
    try:
        _gate.advance(new_step, config)
        return 0
    except WorkflowTransitionError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except WorkflowGateBlockedError as e:
        # Format detailed error message for plan-review gate
        print(f"Error: Cannot advance to implement: {e}", file=sys.stderr)
        details = e.details
        if details:
            print(f"   Enabled reviewers: {details.get('enabled_reviewers', [])}", file=sys.stderr)
            print(
                f"   Required approvals: {details.get('required', 0)}, Got: {details.get('approved', 0)}",
                file=sys.stderr,
            )
            for reviewer, status in details.get("review_status", {}).items():
                id_note = "(placeholder)" if status.get("is_placeholder") else ""
                print(
                    f"   {reviewer}: {status.get('status', 'NOT_REQUESTED')} {id_note}",
                    file=sys.stderr,
                )
        print("   See @docs/AI/Workflows/03-reviews.md for review process", file=sys.stderr)
        return 1


def cmd_record_review(args):
    """Record a review result.

    Gemini HIGH fix: Delegates to core.py WorkflowGate.record_review() to reduce duplication.
    Codex MEDIUM fix: Status mapping aligned with core constants.
    Gemini LOW fix: Catches exceptions from core.py instead of sys.exit.
    """
    config = WorkflowConfig()

    # Validate inputs (C4) - pass config for dynamic available reviewers
    try:
        reviewer = _validate_reviewer(args.reviewer, config)
        continuation_id = (
            _validate_continuation_id(args.continuation_id) if args.continuation_id else None
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Continuation ID is required for review recording
    if not continuation_id:
        print("Error: --continuation-id is required for review recording", file=sys.stderr)
        print("   The continuation ID proves the review actually happened", file=sys.stderr)
        return 1

    # Codex MEDIUM fix: Map CLI status to internal status matching core constants
    # Core uses: APPROVED, NEEDS_REVISION, NOT_REQUESTED
    status_map = {
        "approved": "APPROVED",
        "rejected": "NEEDS_REVISION",  # Aligned with core.py REVIEW_NEEDS_REVISION
        "changes_requested": "NEEDS_REVISION",  # Aligned with core.py
        "needs_revision": "NEEDS_REVISION",
    }
    status = status_map.get(args.status.lower(), args.status.upper())

    # Delegate to core.py for the actual recording
    try:
        _gate.record_review(continuation_id, status, reviewer, config)
        print(
            json.dumps(
                {
                    "success": True,
                    "reviewer": reviewer,
                    "status": status,
                    "continuation_id": continuation_id,
                }
            )
        )
        return 0
    except WorkflowValidationError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


# Note: Audit logging is handled by WorkflowGate._log_to_audit() in core.py
# No duplicate implementation here - single source of truth


def cmd_record_ci(args):
    """Record CI result."""
    with state_transaction() as state:
        passed = args.passed.lower() in ("true", "1", "yes", "passed")

        if "ci" not in state:
            state["ci"] = {}

        phase = state.get("phase", "component")
        if phase == "pr-review":
            state["ci"]["pr_ci_passed"] = passed
        else:
            state["ci"]["component_passed"] = passed

        print(json.dumps({"success": True, "passed": passed}))
    return 0


def cmd_check_commit(args):
    """Check if ready to commit.

    Gemini HIGH fix: Delegates to core.py get_commit_status() to share logic
    with the pre-commit hook, preventing divergence.
    """
    config = WorkflowConfig()

    # Use shared logic from core.py
    status = _gate.get_commit_status(config)

    # Output JSON result for CLI consumption
    print(
        json.dumps(
            {
                "ready": status["ready"],
                "override": status.get("override", False),
                "checks": status["checks"],
                "config": status["config"],
            }
        )
    )

    return 0 if status["ready"] else 1


def cmd_record_commit(args):
    """Record a commit for the current component."""
    with state_transaction() as state:
        comp = state.get("component", {})
        current = comp.get("current", "")

        if not current:
            print("Error: No current component set", file=sys.stderr)
            print("   Use: ./scripts/workflow_gate.py set-component '<name>'", file=sys.stderr)
            print(
                "   See @docs/AI/Workflows/12-component-cycle.md for component workflow",
                file=sys.stderr,
            )
            return 1

        # Record commit
        if "git" not in state:
            state["git"] = {"commits": [], "pr_commits": []}
        if "commits" not in state["git"]:
            state["git"]["commits"] = []

        # datetime imported at module level
        state["git"]["commits"].append(
            {
                "component": current,
                "hash": args.hash,
                "message": getattr(args, "message", ""),
                "at": datetime.now(UTC).isoformat(),
            }
        )

        # Reset for next component
        state["component"]["step"] = "plan"
        state["component"]["current"] = ""
        state["reviews"] = {}
        state["ci"]["component_passed"] = False

        print(
            json.dumps(
                {
                    "success": True,
                    "component": current,
                    "hash": args.hash,
                }
            )
        )
    return 0


# =============================================================================
# PR Phase Commands
# =============================================================================


def cmd_start_pr_phase(args):
    """Start PR review phase."""
    with state_transaction() as state:
        config = WorkflowConfig()
        handler = PRWorkflowHandler(state, config)

        # Extract PR number from URL if provided
        pr_number = args.pr_number
        if args.pr_url and not pr_number:
            import re

            match = re.search(r"/pull/(\d+)", args.pr_url)
            if match:
                pr_number = int(match.group(1))

        handler.start_pr_phase(args.pr_url, pr_number)

        print(
            json.dumps(
                {
                    "success": True,
                    "phase": "pr-review",
                    "pr_url": args.pr_url,
                    "pr_number": pr_number,
                }
            )
        )
    return 0


def cmd_pr_check(args):
    """Check PR status.

    Addresses Claude review H2: Now uses state_transaction() for atomicity.
    Addresses Claude review H4: Proper error handling with reporting.
    """
    try:
        with state_transaction() as state:
            config = WorkflowConfig()
            handler = PRWorkflowHandler(state, config)

            status = handler.check_pr_status()

            print(json.dumps(status, indent=2))

            if status.get("all_approved"):
                print("\n✓ All reviewers approved and CI passed!", file=sys.stderr)
                return 0
            return 1
    except Exception as e:
        # Report error to stderr, output error JSON to stdout for parsing
        _log(f"Error checking PR status: {e}")
        print(json.dumps({"error": str(e), "all_approved": False}))
        return 1


def cmd_pr_record_commit(args):
    """Record commit during PR review phase and push.

    Addresses Gemini CRITICAL review: Exposes PRWorkflowHandler.record_commit_and_push
    to CLI so PR phase commits are not blocked.
    """
    with state_transaction() as state:
        config = WorkflowConfig()
        handler = PRWorkflowHandler(state, config)

        # Validate we're in PR phase (Gemini HIGH fix: use hyphen not underscore)
        phase = state.get("phase", "")
        if phase != "pr-review":
            print(
                json.dumps(
                    {
                        "success": False,
                        "error": f"Not in PR phase (current phase: {phase}). Use 'record-commit' for component phase.",
                    }
                )
            )
            return 1

        message = args.message or ""
        success, msg = handler.record_commit_and_push(args.hash, message)

        print(
            json.dumps(
                {
                    "success": success,
                    "message": msg,
                    "step": state.get("pr_review", {}).get("step", ""),
                }
            )
        )

        return 0 if success else 1


def cmd_subtask_create(args):
    """Create subtasks from PR comments.

    Addresses Gemini review: Now uses state_transaction for atomicity.
    """
    with state_transaction() as state:
        config = WorkflowConfig()
        handler = PRWorkflowHandler(state, config)
        orchestrator = SubtaskOrchestrator(state, config)

        pr_number = state.get("pr_review", {}).get("pr_number")
        if not pr_number:
            print("Error: No PR number set", file=sys.stderr)
            print(
                "   Use: ./scripts/workflow_gate.py start-pr-phase --pr-url <url>", file=sys.stderr
            )
            print("   See @docs/AI/Workflows/01-git.md for PR workflow", file=sys.stderr)
            return 1

        # Fetch comment metadata (IDs only)
        comments = handler.fetch_pr_comment_metadata(pr_number)

        # Group by file
        by_file = {}
        for c in comments:
            if not c.get("resolved"):
                path = c["file_path"]
                if path not in by_file:
                    by_file[path] = []
                by_file[path].append(c["id"])

        # Create instructions for agent
        instructions = orchestrator.create_agent_instructions(pr_number, by_file)

        # Output JSON for agent to read and execute
        output = orchestrator.output_instructions_json(instructions)
        print(output)

    return 0


def cmd_review_create(args):
    """Create review subtasks using Zen MCP integration.

    Addresses Gemini review: Uses build_clink_params for REVIEW_FILES subtasks.
    Addresses review: Uses AgentInstruction dataclass for consistent JSON output.
    This connects ReviewerOrchestrator to actual review triggering.
    """
    from ai_workflow.reviewers import ReviewerOrchestrator

    with state_transaction() as state:
        config = WorkflowConfig()
        reviewer_orch = ReviewerOrchestrator(state, config)

        # Get diff for review
        try:
            diff_result = subprocess.run(
                ["git", "diff", state.get("git", {}).get("base_branch", "master") + "...HEAD"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            diff = diff_result.stdout if diff_result.returncode == 0 else ""
        except Exception as e:
            _log(f"Warning: Could not get diff: {e}")
            diff = ""

        # Get changed files and resolve to absolute paths (Gemini HIGH fix)
        try:
            files_result = subprocess.run(
                [
                    "git",
                    "diff",
                    "--name-only",
                    state.get("git", {}).get("base_branch", "master") + "...HEAD",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            # Resolve relative paths from git to absolute paths
            file_paths = [
                str(Path(f).resolve()) for f in files_result.stdout.strip().split("\n") if f
            ]
        except Exception:
            file_paths = []

        # Create review instructions for each enabled reviewer using AgentInstruction
        instructions = []
        for reviewer in config.get_enabled_reviewers():
            # Get continuation_id for multi-round reviews
            continuation_id = reviewer_orch.get_continuation_id(reviewer)

            # Build params using the existing build_clink_params method
            params = reviewer_orch.build_clink_params(
                reviewer_name=reviewer,
                diff=diff,
                file_paths=file_paths,
                continuation_id=continuation_id,
            )

            task_id = f"review-{reviewer}-{uuid.uuid4().hex[:6]}"

            # Use AgentInstruction for consistent JSON structure
            instruction = AgentInstruction(
                id=task_id,
                action="delegate_to_subagent",
                tool="mcp__zen__clink",
                params=params,
            )
            instructions.append(instruction.to_dict())

            # Track in state
            if "review_tasks" not in state:
                state["review_tasks"] = []
            state["review_tasks"].append(
                {
                    "id": task_id,
                    "reviewer": reviewer,
                    "status": "queued",
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )

        # Output JSON for agent
        output = json.dumps(
            {
                "action": "request_reviews",
                "instruction": "For each reviewer, call mcp__zen__clink with the provided params",
                "tasks": instructions,
            },
            indent=2,
        )
        print(output)

    return 0


def cmd_subtask_start(args):
    """Mark subtask as delegated (started)."""
    with state_transaction() as state:
        config = WorkflowConfig()
        orchestrator = SubtaskOrchestrator(state, config)
        if orchestrator.mark_delegated(args.task_id):
            print(f"✓ Task {args.task_id} marked as delegated")
        else:
            print(f"✗ Task {args.task_id} not found in queue", file=sys.stderr)
            print(
                "   Use: ./scripts/workflow_gate.py subtask-status to see available tasks",
                file=sys.stderr,
            )
            return 1
    return 0


def cmd_subtask_complete(args):
    """
    Record subtask completion.

    Addresses G3: Accepts --summary-file to avoid shell escaping issues.
    """
    # Get summary from file or argument
    if args.summary_file:
        summary_path = Path(args.summary_file)
        if summary_path.exists():
            summary = summary_path.read_text()
        elif args.summary_file == "-":
            summary = sys.stdin.read()
        else:
            print(f"Error: Summary file not found: {args.summary_file}", file=sys.stderr)
            print(
                "   Create a JSON file with the subtask summary or use --summary for text",
                file=sys.stderr,
            )
            return 1
    else:
        summary = args.summary or ""

    with state_transaction() as state:
        config = WorkflowConfig()
        orchestrator = SubtaskOrchestrator(state, config)

        success = orchestrator.record_completion(args.task_id, summary)

        print(
            json.dumps(
                {
                    "success": success,
                    "task_id": args.task_id,
                }
            )
        )

    return 0 if success else 1


def cmd_subtask_status(args):
    """Show subtask status."""
    state = load_state()
    config = WorkflowConfig()
    orchestrator = SubtaskOrchestrator(state, config)

    status = orchestrator.get_status_summary()
    print(json.dumps(status, indent=2))
    return 0


def cmd_reset_task(args):
    """Reset workflow for new task after merge."""
    with state_transaction() as state:
        config = WorkflowConfig()
        handler = PRWorkflowHandler(state, config)

        if handler.reset_for_new_task():
            print("✓ Workflow reset for new task")
            print("   Use: ./scripts/workflow_gate.py start-task <id> <branch> to begin")
            return 0
        else:
            print("✗ Can only reset from 'merged' state", file=sys.stderr)
            print("   Current state must be 'merged' before resetting", file=sys.stderr)
            print("   See @docs/AI/Workflows/01-git.md for PR merge workflow", file=sys.stderr)
            return 1


def cmd_reset(args):
    """Reset workflow state (emergency use only)."""
    with state_transaction() as state:
        # Replace content with fresh state
        fresh = {
            "version": "2.0",
            "phase": "component",
            "component": {"current": "", "step": "plan", "list": []},
            "pr_review": {"step": "pr-pending", "iteration": 0},
            "reviewers": {},
            "ci": {},
            "git": {"commits": [], "pr_commits": []},
            "subtasks": {"queue": [], "completed": [], "failed": []},
        }
        state.clear()
        state.update(fresh)
        print("Workflow state reset (Emergency)")
    return 0


# =============================================================================
# Config Commands
# =============================================================================


def cmd_config_show(args):
    """Show configuration."""
    config = WorkflowConfig()
    print(json.dumps(config.config, indent=2))
    return 0


def cmd_check_reviewers(args):
    """Check reviewer availability."""
    config = WorkflowConfig()
    enabled = config.get_enabled_reviewers()
    print(
        json.dumps(
            {
                "enabled": enabled,
                "min_required": config.get_min_required_approvals(),
            }
        )
    )
    return 0


# =============================================================================
# Main Entry Point
# =============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Workflow Gate CLI - AI Workflow Enforcement",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Status
    subparsers.add_parser("status", help="Show workflow status")

    # ==========================================================================
    # COMPONENT PHASE COMMANDS
    # ==========================================================================

    # Start task
    start = subparsers.add_parser("start-task", help="Start a new task")
    start.add_argument("task_file", help="Path to task file")
    start.add_argument("branch", help="Git branch name")
    start.add_argument("--base-branch", default="master", help="Base branch")

    # Set component
    set_comp = subparsers.add_parser("set-component", help="Set current component")
    set_comp.add_argument("name", help="Component name")

    # Advance step
    adv = subparsers.add_parser("advance", help="Advance workflow step")
    adv.add_argument("step", help="Step to advance to")

    # Record review
    rec_rev = subparsers.add_parser("record-review", help="Record review result")
    rec_rev.add_argument("reviewer", help="Reviewer name (claude, gemini, codex)")
    rec_rev.add_argument("status", help="Review status (approved, rejected)")
    rec_rev.add_argument("--continuation-id", help="Continuation ID for multi-round")

    # Record CI
    rec_ci = subparsers.add_parser("record-ci", help="Record CI result")
    rec_ci.add_argument("passed", help="Whether CI passed (true/false)")

    # Check commit
    subparsers.add_parser("check-commit", help="Check commit prerequisites")

    # Record commit
    rec_commit = subparsers.add_parser("record-commit", help="Record commit for component")
    rec_commit.add_argument("hash", help="Commit hash")
    rec_commit.add_argument("--message", help="Commit message")

    # ==========================================================================
    # PR PHASE COMMANDS
    # ==========================================================================

    # PR phase start
    pr_start = subparsers.add_parser("start-pr-phase", help="Start PR review phase")
    pr_start.add_argument("--pr-url", help="PR URL")
    pr_start.add_argument("--pr-number", type=int, help="PR number")

    subparsers.add_parser("pr-check", help="Check PR status")

    # PR phase commit (for recording commits during PR review cycle)
    pr_rec_commit = subparsers.add_parser(
        "pr-record-commit", help="Record commit during PR review phase"
    )
    pr_rec_commit.add_argument("hash", help="Commit hash")
    pr_rec_commit.add_argument("--message", help="Commit message")

    # Reset task (after merge)
    subparsers.add_parser("reset-task", help="Reset workflow for new task after merge")

    # Reset (Emergency)
    subparsers.add_parser("reset", help="Reset workflow state (emergency)")

    # Subtasks
    subparsers.add_parser("subtask-create", help="Create subtasks from PR comments")

    st_start = subparsers.add_parser("subtask-start", help="Mark subtask as delegated")
    st_start.add_argument("task_id", help="Task ID")

    st_complete = subparsers.add_parser("subtask-complete", help="Record subtask completion")
    st_complete.add_argument("task_id", help="Task ID")
    # G3 fix: Accept --summary-file for complex JSON to avoid shell escaping
    st_complete.add_argument("--summary", help="Simple text summary")
    st_complete.add_argument(
        "--summary-file", help="Path to JSON file with summary (use - for stdin)"
    )

    subparsers.add_parser("subtask-status", help="Show subtask status")

    # Review creation (uses build_clink_params - addresses Gemini review)
    subparsers.add_parser("review-create", help="Create review subtasks for enabled reviewers")

    # ==========================================================================
    # CONFIG COMMANDS
    # ==========================================================================
    subparsers.add_parser("config-show", help="Show configuration")
    subparsers.add_parser("check-reviewers", help="Check reviewer availability")

    args = parser.parse_args()

    commands = {
        # Status
        "status": cmd_status,
        # Component phase
        "start-task": cmd_start_task,
        "set-component": cmd_set_component,
        "advance": cmd_advance,
        "record-review": cmd_record_review,
        "record-ci": cmd_record_ci,
        "check-commit": cmd_check_commit,
        "record-commit": cmd_record_commit,
        # PR phase
        "start-pr-phase": cmd_start_pr_phase,
        "pr-check": cmd_pr_check,
        "pr-record-commit": cmd_pr_record_commit,
        "reset-task": cmd_reset_task,
        "reset": cmd_reset,
        "subtask-create": cmd_subtask_create,
        "subtask-start": cmd_subtask_start,
        "subtask-complete": cmd_subtask_complete,
        "subtask-status": cmd_subtask_status,
        "review-create": cmd_review_create,
        # Config
        "config-show": cmd_config_show,
        "check-reviewers": cmd_check_reviewers,
    }

    if args.command in commands:
        return commands[args.command](args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
