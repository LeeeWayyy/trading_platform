#!/usr/bin/env python3
"""
Workflow Enforcement Gate - Hard enforcement of 4-step component pattern.

Enforces the mandatory 4-step development workflow:
  implement → test → review → commit

This script prevents commits unless prerequisites are met:
- Zen-MCP review approval (clink + gemini → codex)
- CI passing (make ci-local)
- Current step is "review"

Usage:
  ./scripts/workflow_gate.py advance <next_step>         # Transition to next step
  ./scripts/workflow_gate.py check-commit                # Validate commit prerequisites
  ./scripts/workflow_gate.py record-review <id> <status> # Record review result
  ./scripts/workflow_gate.py record-ci <passed>          # Record CI result
  ./scripts/workflow_gate.py record-commit               # Record commit hash (post-commit)
  ./scripts/workflow_gate.py status                      # Show current workflow state
  ./scripts/workflow_gate.py reset                       # Reset state (emergency)
  ./scripts/workflow_gate.py set-component <name>        # Set current component name

Author: Claude Code
Date: 2025-11-02
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, Tuple

# Constants
PROJECT_ROOT = Path(__file__).parent.parent
STATE_FILE = PROJECT_ROOT / ".claude" / "workflow-state.json"

StepType = Literal["implement", "test", "review"]

# Review status constants
REVIEW_APPROVED = "APPROVED"
REVIEW_NEEDS_REVISION = "NEEDS_REVISION"
REVIEW_NOT_REQUESTED = "NOT_REQUESTED"


class WorkflowGate:
    """Enforces 4-step workflow pattern with hard gates."""

    VALID_TRANSITIONS = {
        "implement": ["test"],
        "test": ["review"],
        "review": ["implement"],  # Can only go back to fix issues
    }

    def __init__(self, state_file: Path | None = None) -> None:
        """
        Initialize WorkflowGate with optional state file path.

        Args:
            state_file: Path to workflow state JSON file (default: .claude/workflow-state.json)

        Note:
            Dependency injection pattern allows mocking in tests and supports
            PlanningWorkflow's need for custom state file paths.
        """
        self._state_file = state_file or STATE_FILE

    def _init_state(self) -> dict:
        """Initialize default workflow state."""
        return {
            "current_component": "",
            "step": "implement",
            "zen_review": {},
            "ci_passed": False,
            "last_commit_hash": None,
            "commit_history": [],
            "subagent_delegations": [],
            "context": {
                "current_tokens": 0,
                "max_tokens": int(os.getenv("CLAUDE_MAX_TOKENS", "200000")),
                "last_check_timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }

    def _ensure_context_defaults(self, state: dict) -> dict:
        """
        Ensure context fields exist for backward compatibility.

        Migrates legacy state files that don't have context monitoring fields.
        Called immediately after loading state to prevent KeyError.

        Args:
            state: Loaded state dictionary

        Returns:
            State with context defaults ensured
        """
        if "context" not in state:
            state["context"] = {
                "current_tokens": 0,
                "max_tokens": int(os.getenv("CLAUDE_MAX_TOKENS", "200000")),
                "last_check_timestamp": datetime.now(timezone.utc).isoformat(),
            }
        return state

    def load_state(self) -> dict:
        """Load workflow state from JSON file."""
        if not self._state_file.exists():
            return self._init_state()
        try:
            state = json.loads(self._state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError) as e:
            print(f"⚠️  Warning: Failed to parse workflow state file: {e}")
            print(f"   Initializing fresh state...")
            return self._init_state()
        # Ensure backward compatibility with old state files
        return self._ensure_context_defaults(state)

    def save_state(self, state: dict) -> None:
        """Save workflow state to JSON file with atomic write."""
        self._state_file.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write: write to temp file then rename
        # Prevents corruption from partial writes
        temp_fd, temp_path = tempfile.mkstemp(
            dir=self._state_file.parent,
            prefix=".workflow-state-",
            suffix=".tmp"
        )
        try:
            with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2, ensure_ascii=False)

            # Atomic rename
            Path(temp_path).replace(self._state_file)
        except (IOError, OSError):
            # Clean up temp file on error
            Path(temp_path).unlink(missing_ok=True)
            raise

    def can_transition(self, current: StepType, next: StepType) -> Tuple[bool, str]:
        """
        Check if transition is valid.

        Args:
            current: Current workflow step
            next: Next workflow step

        Returns:
            (can_transition, error_message)
        """
        if next not in self.VALID_TRANSITIONS.get(current, []):
            return False, f"❌ Cannot transition from '{current}' to '{next}'"

        state = self.load_state()

        # Additional checks for specific transitions
        if next == "review":
            # Must have tests before requesting review
            if not self._has_tests(state["current_component"]):
                return False, (
                    "❌ Cannot request review without test files\n"
                    "   Create tests for component: "
                    + (state["current_component"] or "UNKNOWN")
                )

        return True, ""

    def advance(self, next: StepType) -> None:
        """
        Advance workflow to next step (with validation).

        Args:
            next: Next workflow step

        Raises:
            SystemExit: If transition is invalid
        """
        state = self.load_state()
        current = state["step"]

        can, error_msg = self.can_transition(current, next)
        if not can:
            print(error_msg)
            sys.exit(1)

        # Special logic for review step
        if next == "review":
            print("🔍 Requesting zen-mcp review (clink + gemini → codex)...")
            print("   Follow: .claude/workflows/03-zen-review-quick.md")
            print("   After review, record approval:")
            print(
                "     ./scripts/workflow_gate.py record-review <continuation_id> <status>"
            )

        # Update state
        state["step"] = next
        self.save_state(state)

        print(f"✅ Advanced to '{next}' step")

    def record_review(self, continuation_id: str, status: str) -> None:
        """
        Record zen-mcp review result.

        Args:
            continuation_id: Zen-MCP continuation ID from review
            status: Review status ("APPROVED" or "NEEDS_REVISION")
        """
        state = self.load_state()
        state["zen_review"] = {
            "requested": True,
            "continuation_id": continuation_id,
            "status": status,  # "APPROVED" or "NEEDS_REVISION"
        }
        self.save_state(state)
        print(f"✅ Recorded zen review: {status}")

        if status == REVIEW_NEEDS_REVISION:
            print("⚠️  Review requires changes. Fix issues and re-request review.")
            print("   After fixes:")
            print("     ./scripts/workflow_gate.py advance review")

    def record_ci(self, passed: bool) -> None:
        """
        Record CI result.

        Args:
            passed: True if CI passed, False otherwise
        """
        state = self.load_state()
        state["ci_passed"] = passed
        self.save_state(state)
        print(f"✅ Recorded CI: {'PASSED' if passed else 'FAILED'}")

        if not passed:
            print("⚠️  CI failed. Fix issues and re-run:")
            print("   make ci-local && ./scripts/workflow_gate.py record-ci true")

    def check_commit(self) -> None:
        """
        Validate commit prerequisites (called by pre-commit hook).

        Enforces hard gates:
        - Current step must be "review"
        - Zen-MCP review must be APPROVED
        - CI must be passing

        Raises:
            SystemExit: If prerequisites are not met
        """
        state = self.load_state()

        # Check current step
        if state["step"] != "review":
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print(
                f"❌ COMMIT BLOCKED: Current step is '{state['step']}', must be 'review'"
            )
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print(f"   Component: {state['current_component'] or 'UNKNOWN'}")
            print("   Current workflow state:")
            # Show completed steps with ✓
            print(f"     1. Implement ({'✓' if state['step'] in ['test', 'review'] else ' '})")
            print(f"     2. Test ({'✓' if state['step'] == 'review' else ' '})")
            print(f"     3. Review ( )")
            print("   Progress to next step:")
            print(f"     ./scripts/workflow_gate.py advance <next_step>")
            sys.exit(1)

        # Check zen review approval
        if not state["zen_review"].get("status") == REVIEW_APPROVED:
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print("❌ COMMIT BLOCKED: Zen review not approved")
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print(
                "   Continuation ID:", state["zen_review"].get("continuation_id", "N/A")
            )
            print("   Status:", state["zen_review"].get("status", REVIEW_NOT_REQUESTED))
            print("   Request review:")
            print("     Follow: .claude/workflows/03-zen-review-quick.md")
            print("   After approval:")
            print(
                "     ./scripts/workflow_gate.py record-review <continuation_id> APPROVED"
            )
            sys.exit(1)

        # Check CI pass
        if not state["ci_passed"]:
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print("❌ COMMIT BLOCKED: CI not passed")
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print("   Run CI:")
            print("     make ci-local")
            print("   Record result:")
            print("     ./scripts/workflow_gate.py record-ci true")
            sys.exit(1)

        # All gates passed
        print("✅ Commit prerequisites satisfied")
        print(f"   Component: {state['current_component']}")
        print(f"   Zen review: {state['zen_review']['continuation_id'][:8]}...")
        print("   CI: PASSED")
        sys.exit(0)

    def record_commit(self, update_task_state: bool = False) -> None:
        """
        Record commit hash after successful commit (called post-commit).

        Captures the commit hash and resets state for next component.
        Optionally updates task state tracking if enabled.

        Args:
            update_task_state: If True, also update .claude/task-state.json
        """
        state = self.load_state()

        # Get the commit hash
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
                cwd=PROJECT_ROOT,
            )
            commit_hash = result.stdout.strip()
        except subprocess.CalledProcessError:
            print("❌ Failed to get commit hash")
            sys.exit(1)

        # Optionally update task state
        if update_task_state:
            self._update_task_state(state, commit_hash)

        # Record commit hash in history and reset state for next component
        if "commit_history" not in state:
            state["commit_history"] = []
            # One-time migration for backward compatibility. If an old state file
            # only has last_commit_hash, we need to preserve it in the new history.
            if last_hash := state.get("last_commit_hash"):
                state["commit_history"].append(last_hash)
        # Ensure the current commit is in the history, avoiding duplicates.
        if commit_hash not in state["commit_history"]:
            state["commit_history"].append(commit_hash)
        # Prune history to last 100 commits to prevent file growth
        state["commit_history"] = state["commit_history"][-100:]
        state["last_commit_hash"] = commit_hash  # Kept for backward compatibility
        state["step"] = "implement"  # Ready for next component
        state["zen_review"] = {}
        state["ci_passed"] = False

        # Reset context after commit, ready for next component (Component 3)
        state["context"]["current_tokens"] = 0
        state["context"]["last_check_timestamp"] = datetime.now(timezone.utc).isoformat()

        self.save_state(state)

        print(f"✅ Recorded commit {commit_hash[:8]}")
        print("✅ Ready for next component (step: implement)")
        print("   Set new component:")
        print("     ./scripts/workflow_gate.py set-component '<component_name>'")

    def set_component(self, component_name: str) -> None:
        """
        Set the current component name.

        Args:
            component_name: Name of the component being developed
        """
        state = self.load_state()
        state["current_component"] = component_name
        self.save_state(state)
        print(f"✅ Set current component: {component_name}")

    def show_status(self) -> None:
        """Display current workflow state."""
        state = self.load_state()

        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("Workflow State")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"Component: {state['current_component'] or 'NONE'}")
        print(f"Current Step: {state['step']}")
        print()
        print("Workflow Progress:")
        print(f"  1. Implement {'✓' if state['step'] != 'implement' else '← YOU ARE HERE'}")
        print(f"  2. Test {'✓' if state['step'] in ['review'] else '← YOU ARE HERE' if state['step'] == 'test' else ''}")
        print(f"  3. Review {'← YOU ARE HERE' if state['step'] == 'review' else ''}")
        print()
        print("Gate Status:")
        zen_status = state["zen_review"].get("status", "NOT_REQUESTED")
        ci_status = "PASSED" if state["ci_passed"] else "NOT_RUN"
        print(f"  Zen Review: {zen_status}")
        if state["zen_review"].get("continuation_id"):
            print(
                f"    Continuation ID: {state['zen_review']['continuation_id'][:12]}..."
            )
        print(f"  CI: {ci_status}")
        print()

        if state["last_commit_hash"]:
            print(f"Last Commit: {state['last_commit_hash'][:8]}")
            print()

        # Show available actions
        print("Available Actions:")
        current = state["step"]
        if current == "implement":
            print("  ./scripts/workflow_gate.py advance test")
        elif current == "test":
            print("  ./scripts/workflow_gate.py advance review")
        elif current == "review":
            if zen_status != "APPROVED":
                print("  Follow: .claude/workflows/03-zen-review-quick.md")
                print(
                    "  ./scripts/workflow_gate.py record-review <continuation_id> APPROVED"
                )
            if not state["ci_passed"]:
                print("  make ci-local")
                print("  ./scripts/workflow_gate.py record-ci true")
            if zen_status == "APPROVED" and state["ci_passed"]:
                print("  git commit -m '<message>'")

    def reset(self) -> None:
        """Reset workflow state (EMERGENCY USE ONLY)."""
        print("⚠️  WARNING: Resetting workflow state")
        print("   This will clear all progress including:")
        print("   - Current component")
        print("   - Zen review status")
        print("   - CI pass status")
        print()

        state = self._init_state()
        self.save_state(state)

        print("✅ Workflow state reset to 'implement'")
        print("   Set component name:")
        print("     ./scripts/workflow_gate.py set-component '<component_name>'")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Context Monitoring & Delegation Triggers (Component 3)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _has_tests(self, component: str) -> bool:
        """
        Check if test files exist for the given component.

        Accepts multiple naming patterns:
        - tests/**/test_<component>*.py (prefix pattern)
        - tests/**/*<component>*_test.py (suffix pattern)
        - tests/**/*<component>*.py (contains pattern)

        Example: Component "position_limit_validation" →
          - tests/**/test_position_limit_validation.py OR
          - tests/**/position_limit_validation_test.py OR
          - tests/**/test_position_limit*.py

        Args:
            component: Component name

        Returns:
            True if test files exist, False otherwise
        """
        if not component:
            return False

        # Convert component name to test file pattern
        # Example: "Position Limit Validation" → "position_limit_validation"
        component_slug = component.lower().replace(" ", "_").replace("-", "_")

        # Try multiple common patterns (broad matching to avoid false negatives)
        patterns = [
            # Exact matches
            str(PROJECT_ROOT / f"tests/**/test_{component_slug}.py"),      # e.g., tests/test_my_component.py
            str(PROJECT_ROOT / f"tests/**/{component_slug}_test.py"),      # e.g., tests/my_component_test.py

            # Wildcard matches (partial component name, allows subdirectories)
            str(PROJECT_ROOT / f"tests/**/test_{component_slug}_*.py"),    # e.g., tests/test_my_component_extra.py
            str(PROJECT_ROOT / f"tests/**/test_*{component_slug}*.py"),    # e.g., tests/test_feature_my_component.py or tests/unit/test_my_component.py
            str(PROJECT_ROOT / f"tests/**/*{component_slug}*_test.py"),    # e.g., tests/unit/my_component_integration_test.py
        ]

        # Search for matching test files across all patterns
        for pattern in patterns:
            matches = glob.glob(pattern, recursive=True)
            if matches:
                return True
        return False

    def _update_task_state(self, workflow_state: dict, commit_hash: str) -> None:
        """
        Update .claude/task-state.json after successful commit.

        Extracts component information from workflow state and calls
        update_task_state.py to record the completion.

        Args:
            workflow_state: Current workflow gate state
            commit_hash: Git commit hash
        """
        task_state_file = PROJECT_ROOT / ".claude" / "task-state.json"

        # Check if task state tracking is active
        if not task_state_file.exists():
            print("ℹ️  No task state file found, skipping task state update")
            return

        try:
            with open(task_state_file, 'r', encoding='utf-8') as f:
                task_state = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"⚠️  Warning: Could not load task state: {e}")
            return

        # Check if there's an active task
        if not task_state.get("current_task"):
            print("ℹ️  No active task, skipping task state update")
            return

        # Get current component number from task state
        current_comp = task_state.get("progress", {}).get("current_component")
        if not current_comp:
            print("⚠️  Warning: No current component in task state")
            return

        component_num = current_comp.get("number")
        if not component_num:
            print("⚠️  Warning: No component number in task state")
            return

        # Extract metadata from workflow state
        continuation_id = workflow_state.get("zen_review", {}).get("continuation_id")

        # Call update_task_state.py
        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "update_task_state.py"),
            "complete",
            "--component",
            str(component_num),
            "--commit",
            commit_hash,
        ]

        if continuation_id:
            cmd.extend(["--continuation-id", continuation_id])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                cwd=PROJECT_ROOT,
            )
            print("\n📊 Task State Updated:")
            print(result.stdout)
        except subprocess.CalledProcessError as e:
            print(f"⚠️  Warning: Failed to update task state: {e}")
            print(f"   You can manually update with:")
            print(f"   ./scripts/update_task_state.py complete --component {component_num} --commit {commit_hash}")


# ============================================================================
# Component 1: Smart Test Runner
# ============================================================================


class SmartTestRunner:
    """
    Intelligent test selection based on git changes.

    Component 1 of P1T13-F4 Workflow Intelligence.

    Implements smart testing strategy:
    - Targeted tests for commits (only changed modules)
    - Full CI for PRs and core package changes

    Uses git_utils module for change detection.
    """

    def __init__(self) -> None:
        """Initialize SmartTestRunner."""
        # Import here to avoid circular dependency issues
        try:
            from scripts.git_utils import (
                get_staged_files,
                requires_full_ci,
                detect_changed_modules,
            )
            self._get_staged_files = get_staged_files
            self._requires_full_ci = requires_full_ci
            self._detect_changed_modules = detect_changed_modules
        except ImportError as e:
            print(f"⚠️  Warning: Could not import git_utils: {e}")
            print("   Smart testing features will be disabled. Defaulting to full CI for safety.")
            # Fail-safe: Return dummy file to force full CI when git_utils unavailable
            self._get_staged_files = lambda: ["DUMMY_FILE_TO_FORCE_CI"]
            self._requires_full_ci = lambda files: True
            self._detect_changed_modules = lambda files: set()

    def should_run_full_ci(self) -> bool:
        """
        Determine if full CI should run.

        Full CI runs if:
        - Any CORE_PACKAGE changed (libs/, config/, infra/, tests/fixtures/, scripts/)
        - More than 5 modules changed (likely a refactor)

        Returns:
            True if full CI required, False if targeted tests OK
        """
        staged_files = self._get_staged_files()
        if not staged_files:
            # No changes = no tests needed (edge case)
            return False

        return self._requires_full_ci(staged_files)

    def get_test_targets(self) -> list[str]:
        """
        Get targeted test paths for changed modules.

        Returns list of pytest paths to run for changed code.

        Returns:
            List of test paths (e.g., ["tests/libs/allocation/", "tests/apps/cli/"])
        """
        staged_files = self._get_staged_files()
        if not staged_files:
            return []

        # Detect changed modules
        modules = self._detect_changed_modules(staged_files)

        # Map modules to test paths
        test_paths = []
        for module in modules:
            # module format: "libs/allocation", "apps/execution_gateway"
            test_path = f"tests/{module}/"
            test_paths.append(test_path)

        return sorted(test_paths)

    def get_test_command(self, context: str = "commit") -> str:
        """
        Get appropriate test command based on context.

        Args:
            context: "commit" for progressive commits, "pr" for pull requests

        Returns:
            Shell command to run tests
        """
        if context == "pr" or self.should_run_full_ci():
            # Full CI for PRs or when core packages changed
            return "make ci-local"

        # Targeted tests for commits
        test_targets = self.get_test_targets()
        if not test_targets:
            # No test targets = no Python changes
            return "echo 'No Python tests needed (no code changes detected)'"

        # Run targeted tests
        targets_str = " ".join(test_targets)
        return f"make test ARGS='{targets_str}'"

    def print_test_strategy(self) -> None:
        """Print test strategy recommendation to user."""
        if self.should_run_full_ci():
            print("🔍 Full CI Required")
            print("   Reason: Core package changed OR >5 modules changed")
            print(f"   Command: make ci-local")
        else:
            test_targets = self.get_test_targets()
            if not test_targets:
                print("✓ No tests needed (no code changes)")
            else:
                print("🎯 Targeted Testing")
                print(f"   Modules: {', '.join(test_targets)}")
                print(f"   Command: {self.get_test_command('commit')}")


class DelegationRules:
    """
    Context monitoring and delegation recommendations.

    Tracks conversation context usage and recommends delegation to specialized
    agents when thresholds are exceeded. Supports operation-specific cost
    projections and provides user-friendly guidance.

    Thresholds (from CLAUDE.md):
    - < 70%: ✅ OK - Continue normal workflow
    - 70-84%: ⚠️ WARNING - Delegation RECOMMENDED
    - ≥ 85%: 🚨 CRITICAL - Delegation MANDATORY
    """

    # Context thresholds (percentages)
    CONTEXT_WARN_PCT = 70
    CONTEXT_CRITICAL_PCT = 85

    # Default max tokens (from environment or Claude Code default)
    DEFAULT_MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", "200000"))

    # Operation cost estimates (tokens)
    # Source: docs/TASKS/P1T13_F4_PROGRESS.md:331-378
    OPERATION_COSTS = {
        "full_ci": 50000,  # Full CI suite + output analysis
        "deep_review": 30000,  # Comprehensive codebase review
        "multi_file_search": 20000,  # Broad grep/glob operations
        "test_suite": 15000,  # Targeted test suite run
        "code_analysis": 10000,  # Analyzing complex code sections
        "simple_fix": 5000,  # Small targeted fixes
    }

    def __init__(
        self,
        load_state: Callable[[], dict],
        save_state: Callable[[dict], None],
    ) -> None:
        """
        Initialize DelegationRules with state management callables.

        Args:
            load_state: Callable that returns current workflow state dict
            save_state: Callable that persists updated state dict

        This dependency injection pattern enables easy testing with fake
        state managers, mirroring SmartTestRunner's lazy import pattern.
        """
        self._load_state = load_state
        self._save_state = save_state

    def get_context_snapshot(self, state: dict | None = None) -> dict:
        """
        Get current context usage snapshot.

        Args:
            state: Optional state dict (loads if not provided)

        Returns:
            Dictionary with:
            - current_tokens: int - Current token usage
            - max_tokens: int - Maximum tokens available
            - usage_pct: float - Usage percentage (0-100)
            - last_check: str - ISO timestamp of last check
            - error: str | None - Error message if calculation fails

        Never raises exceptions - returns fail-safe defaults on errors.
        """
        if state is None:
            try:
                state = self._load_state()
            except Exception as e:
                print(f"⚠️  Warning: Could not load state: {e}")
                print("   Using fail-safe defaults (0 tokens used)")
                state = {}

        # Get context data with defaults
        context = state.get("context", {})
        current_tokens = context.get("current_tokens", 0)
        max_tokens = context.get("max_tokens", self.DEFAULT_MAX_TOKENS)
        last_check = context.get("last_check_timestamp", "never")

        # Calculate usage percentage with validation
        if max_tokens <= 0:
            return {
                "current_tokens": current_tokens,
                "max_tokens": max_tokens,
                "usage_pct": 0.0,
                "last_check": last_check,
                "error": "Invalid max_tokens - please export CLAUDE_MAX_TOKENS",
            }

        usage_pct = (current_tokens / max_tokens) * 100.0

        return {
            "current_tokens": current_tokens,
            "max_tokens": max_tokens,
            "usage_pct": usage_pct,
            "last_check": last_check,
            "error": None,
        }

    def record_context(self, tokens: int) -> dict:
        """
        Record current context usage.

        Args:
            tokens: Current token count (clamped to >= 0)

        Returns:
            Updated context snapshot

        Side effects:
            - Updates .claude/workflow-state.json
            - Prints warning if tokens exceed max
        """
        # Sanitize input
        tokens = max(0, tokens)

        try:
            state = self._load_state()
        except Exception as e:
            print(f"⚠️  Warning: Could not load state: {e}")
            print("   Context not recorded")
            return self.get_context_snapshot({})

        # Update context
        if "context" not in state:
            state["context"] = {}

        state["context"]["current_tokens"] = tokens
        state["context"]["last_check_timestamp"] = datetime.now(timezone.utc).isoformat()

        # Ensure max_tokens is set
        if "max_tokens" not in state["context"]:
            state["context"]["max_tokens"] = self.DEFAULT_MAX_TOKENS

        # Warn if exceeding max
        if tokens > state["context"]["max_tokens"]:
            print(f"⚠️  Warning: Token usage ({tokens}) exceeds max ({state['context']['max_tokens']})")
            print("   Consider resetting context or delegating to subagent")

        # Save state
        try:
            self._save_state(state)
        except Exception as e:
            print(f"⚠️  Warning: Could not save state: {e}")
            print("   State changes not persisted")

        return self.get_context_snapshot(state)

    def should_delegate_context(
        self, snapshot: dict | None = None
    ) -> tuple[bool, str, float]:
        """
        Determine if delegation is recommended based on context usage.

        Args:
            snapshot: Optional context snapshot (fetches if not provided)

        Returns:
            Tuple of (should_delegate, reason, usage_pct)

        Thresholds:
            - < 70%: False, "OK - Continue normal workflow"
            - 70-84%: True, "WARNING - Delegation RECOMMENDED"
            - ≥ 85%: True, "CRITICAL - Delegation MANDATORY"
        """
        if snapshot is None:
            snapshot = self.get_context_snapshot()

        usage_pct = snapshot["usage_pct"]

        if usage_pct < self.CONTEXT_WARN_PCT:
            return False, "OK - Continue normal workflow", usage_pct
        elif usage_pct < self.CONTEXT_CRITICAL_PCT:
            return True, "WARNING - Delegation RECOMMENDED", usage_pct
        else:
            return True, "CRITICAL - Delegation MANDATORY", usage_pct

    def should_delegate_operation(
        self, operation: str, snapshot: dict | None = None
    ) -> tuple[bool, str]:
        """
        Determine if operation should be delegated based on cost projection.

        Args:
            operation: Operation key (e.g., "full_ci", "deep_review")
            snapshot: Optional context snapshot

        Returns:
            Tuple of (should_delegate, reason)

        Rules:
            - Always delegate if operation cost ≥ 50k tokens
            - Delegate if current + operation would exceed 85% threshold
            - Otherwise OK to proceed
        """
        if snapshot is None:
            snapshot = self.get_context_snapshot()

        # Get operation cost (default to 10k if unknown)
        cost = self.OPERATION_COSTS.get(operation, 10000)

        # Always delegate very expensive operations
        if cost >= 50000:
            return True, f"Operation '{operation}' requires {cost} tokens (always delegate ≥50k)"

        # Project usage after operation
        current = snapshot["current_tokens"]
        max_tokens = snapshot["max_tokens"]
        projected = current + cost
        projected_pct = (projected / max_tokens) * 100.0 if max_tokens > 0 else 0

        # Check if projection would exceed critical threshold
        if projected_pct >= self.CONTEXT_CRITICAL_PCT:
            return (
                True,
                f"Operation '{operation}' ({cost} tokens) would push usage to {projected_pct:.1f}% (≥85% critical)",
            )

        return False, f"Operation '{operation}' OK (projected {projected_pct:.1f}%)"

    def suggest_delegation(
        self, snapshot: dict | None = None, operation: str | None = None
    ) -> str:
        """
        Build delegation suggestion message with guidance.

        Args:
            snapshot: Optional context snapshot
            operation: Optional operation to check

        Returns:
            Formatted message with delegation guidance
        """
        if snapshot is None:
            snapshot = self.get_context_snapshot()

        should_delegate, reason, usage_pct = self.should_delegate_context(snapshot)

        # Build status block
        lines = [
            "",
            "=" * 60,
            "  CONTEXT STATUS",
            "=" * 60,
        ]

        # Show usage
        status_emoji = "✅" if usage_pct < 70 else "⚠️" if usage_pct < 85 else "🚨"
        lines.append(f"{status_emoji} Usage: {usage_pct:.1f}% ({snapshot['current_tokens']:,} / {snapshot['max_tokens']:,} tokens)")
        lines.append(f"   {reason}")
        lines.append("")

        # Operation-specific guidance
        if operation:
            op_should_delegate, op_reason = self.should_delegate_operation(operation, snapshot)
            if op_should_delegate:
                lines.append(f"⚠️  Operation Guidance: {op_reason}")
                lines.append("")

        # Delegation recommendations
        if should_delegate:
            lines.append("RECOMMENDATION: Delegate non-core tasks to specialized agents")
            lines.append("")
            lines.append("See: .claude/workflows/16-subagent-delegation.md")
            lines.append("")

            # Add delegation template if operation specified
            if operation:
                template = self.get_delegation_template(operation)
                if template:
                    lines.append("Example Task delegation:")
                    lines.append(template)
                    lines.append("")

        lines.append("=" * 60)

        return "\n".join(lines)

    def record_delegation(self, task_description: str) -> dict:
        """
        Record subagent delegation and reset context counters.

        Args:
            task_description: Description of delegated task

        Returns:
            Dictionary with delegation record count

        Side effects:
            - Appends to state["subagent_delegations"]
            - Resets context.current_tokens to 0
            - Updates last_delegation_timestamp
        """
        try:
            state = self._load_state()
        except Exception as e:
            print(f"⚠️  Warning: Could not load state: {e}")
            return {"count": 0, "error": str(e)}

        # Initialize delegations list
        if "subagent_delegations" not in state:
            state["subagent_delegations"] = []

        # Record delegation
        delegation_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task_description": task_description,
            "context_before_delegation": state.get("context", {}).get("current_tokens", 0),
        }
        state["subagent_delegations"].append(delegation_record)

        # Reset context
        if "context" not in state:
            state["context"] = {}
        state["context"]["current_tokens"] = 0
        state["context"]["last_delegation_timestamp"] = delegation_record["timestamp"]

        # Save state
        try:
            self._save_state(state)
        except Exception as e:
            print(f"⚠️  Warning: Could not save state: {e}")
            return {"count": len(state["subagent_delegations"]), "error": str(e)}

        return {
            "count": len(state["subagent_delegations"]),
            "reset_tokens": 0,
            "timestamp": delegation_record["timestamp"],
        }

    def get_delegation_template(self, operation: str) -> str:
        """
        Get Task() delegation template for operation.

        Args:
            operation: Operation key (e.g., "full_ci", "deep_review")

        Returns:
            Template string or empty if no template available
        """
        templates = {
            "full_ci": '''Task(
    subagent_type="general-purpose",
    description="Run full CI suite",
    prompt="Run 'make ci-local' and report any failures with file:line references"
)''',
            "deep_review": '''Task(
    subagent_type="general-purpose",
    description="Deep codebase review",
    prompt="Perform comprehensive review of [files/modules] for safety, architecture, and quality issues"
)''',
            "multi_file_search": '''Task(
    subagent_type="Explore",
    description="Multi-file search",
    prompt="Search codebase for [pattern] and summarize findings with file:line references",
    thoroughness="medium"
)''',
        }

        return templates.get(operation, "")

    def format_status(
        self, snapshot: dict, reason: str, heading: str = "Context Status"
    ) -> str:
        """
        Format status block for CLI output.

        Args:
            snapshot: Context snapshot
            reason: Status reason string
            heading: Optional heading override

        Returns:
            Formatted ASCII status block
        """
        usage_pct = snapshot["usage_pct"]
        status_emoji = "✅" if usage_pct < 70 else "⚠️" if usage_pct < 85 else "🚨"

        lines = [
            "",
            "=" * 60,
            f"  {heading.upper()}",
            "=" * 60,
            f"{status_emoji} Usage: {usage_pct:.1f}% ({snapshot['current_tokens']:,} / {snapshot['max_tokens']:,} tokens)",
            f"   {reason}",
            f"   Last check: {snapshot['last_check']}",
            "=" * 60,
            "",
        ]

        return "\n".join(lines)


class PlanningWorkflow:
    """
    Integrated task planning and creation workflow.

    Unifies task creation/breakdown workflows with automatic review integration.
    Combines task-state.json (Phase 0 auto-resume) with workflow-state.json
    (Phase 3 workflow gates) for seamless task management.

    This class provides:
    - create_task_with_review(): Generate task docs with auto-review
    - plan_subfeatures(): Intelligent task breakdown (>8h → MUST split)
    - start_task_with_state(): Initialize tracking with state integration

    Author: Claude Code
    Date: 2025-11-08
    """

    def __init__(
        self,
        project_root: Path | None = None,
        state_file: Path | None = None,
        workflow_gate: "WorkflowGate | None" = None,
    ) -> None:
        """
        Initialize planning workflow manager.

        Args:
            project_root: Project root directory (default: inferred from script location)
            state_file: Workflow state JSON file (default: .claude/workflow-state.json)
            workflow_gate: WorkflowGate instance for state management (default: creates new instance)

        Note:
            Dependency injection pattern allows mocking in tests.
            WorkflowGate centralizes state management to ensure atomic writes.
        """
        self._project_root = project_root or Path(__file__).parent.parent
        self._state_file = state_file or (self._project_root / ".claude" / "workflow-state.json")
        self._tasks_dir = self._project_root / "docs" / "TASKS"

        # Inject WorkflowGate for centralized state management (fixes architectural issues)
        self._workflow_gate = workflow_gate or WorkflowGate(state_file=self._state_file)

    def create_task_with_review(
        self,
        task_id: str,
        title: str,
        description: str,
        estimated_hours: float
    ) -> str:
        """
        Create task document and automatically request planning review.

        Flow:
        1. Generate task document from template
        2. Auto-request gemini planner review (Tier 3)
        3. Display review findings
        4. Guide user through fixes if needed
        5. Re-request review after fixes
        6. Mark task as APPROVED when ready

        Args:
            task_id: Task identifier (e.g., "P1T14")
            title: Task title (concise description)
            description: Detailed task description
            estimated_hours: Estimated effort in hours

        Returns:
            Task file path (relative to project root)

        Example:
            >>> planner = PlanningWorkflow()
            >>> task_file = planner.create_task_with_review(
            ...     task_id="P1T14",
            ...     title="Add position limit monitoring",
            ...     description="Monitor and alert on position limit violations",
            ...     estimated_hours=6.0
            ... )
            >>> print(task_file)
            docs/TASKS/P1T14_TASK.md
        """
        # Generate task document
        task_file = self._generate_task_doc(task_id, title, description, estimated_hours)

        print(f"✅ Task document created: {task_file}")
        print()
        print("📋 Requesting task creation review (gemini planner → codex planner)...")
        print("   This will validate scope, requirements, and feasibility.")
        print("   See .claude/workflows/13-task-creation-review.md for review workflow.")
        print()
        print("💡 Next steps:")
        print("   1. Review task document for completeness")
        print("   2. Request review via: mcp__zen__clink with cli_name='gemini', role='planner'")
        print("   3. Address any issues found in review")
        print("   4. Once approved, use plan_subfeatures() if task >8h")
        print()

        return str(task_file.relative_to(self._project_root))

    def plan_subfeatures(
        self,
        task_id: str,
        components: list[dict]
    ) -> list[str]:
        """
        Generate subfeature breakdown and branches.

        Follows 00-task-breakdown.md rules:
        - Task >8h → MUST split into subfeatures
        - Task 4-8h → CONSIDER splitting
        - Task <4h → DON'T split

        Args:
            task_id: Parent task ID (e.g., "P1T13")
            components: List of component dicts with {name, description, hours}

        Returns:
            List of subfeature IDs (e.g., ["P1T13-F1", "P1T13-F2"])

        Example:
            >>> planner = PlanningWorkflow()
            >>> components = [
            ...     {"name": "Position monitor service", "description": "...", "hours": 3},
            ...     {"name": "Alert integration", "description": "...", "hours": 2},
            ...     {"name": "Dashboard UI", "description": "...", "hours": 3}
            ... ]
            >>> subfeatures = planner.plan_subfeatures("P1T14", components)
            >>> print(subfeatures)
            ['P1T14-F1', 'P1T14-F2', 'P1T14-F3']
        """
        total_hours = sum(c.get("hours", 0) for c in components)

        if total_hours < 4:
            print(f"ℹ️  Task is simple (<4h), no subfeature split needed")
            print(f"   Total estimated: {total_hours}h")
            print(f"   Proceed with single-feature implementation")
            return []

        if total_hours >= 8 or len(components) >= 3:
            print(f"✅ Task is complex (≥8h or ≥3 components), splitting into subfeatures...")
            print(f"   Total estimated: {total_hours}h across {len(components)} components")
        else:
            print(f"⚠️  Task is moderate (4-8h), splitting recommended...")
            print(f"   Total estimated: {total_hours}h across {len(components)} components")

        # Generate subfeature IDs
        subfeatures = []
        for idx, component in enumerate(components, start=1):
            subfeature_id = f"{task_id}-F{idx}"
            subfeatures.append(subfeature_id)

            comp_name = component.get("name", f"Component {idx}")
            comp_hours = component.get("hours", 0)
            print(f"  {subfeature_id}: {comp_name} ({comp_hours}h)")

        print()
        print(f"💡 Next steps:")
        print(f"   1. Create task documents for each subfeature using create_task_with_review()")
        print(f"   2. Start first subfeature with start_task_with_state()")
        print()

        return subfeatures

    def start_task_with_state(
        self,
        task_id: str,
        branch_name: str
    ) -> None:
        """
        Initialize task tracking with workflow state integration.

        Combines:
        - .claude/task-state.json (Phase 0 auto-resume)
        - .claude/workflow-state.json (Phase 3 workflow gates)

        Sets up:
        1. Git branch
        2. Task state tracking
        3. Workflow state initialization
        4. Component list from task document

        Args:
            task_id: Task identifier (e.g., "P1T14-F1")
            branch_name: Git branch name (e.g., "feat/P1T14-F1-position-monitoring")

        Example:
            >>> planner = PlanningWorkflow()
            >>> planner.start_task_with_state("P1T14-F1", "feat/P1T14-F1-position-monitoring")
            ✅ Task P1T14-F1 started
               Branch: feat/P1T14-F1-position-monitoring
               Components: 2
               Current: Position limit validator
        """
        # Create branch
        result = subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=self._project_root,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            # Branch might already exist, try to check it out
            result = subprocess.run(
                ["git", "checkout", branch_name],
                cwd=self._project_root,
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                print(f"❌ Failed to create/checkout branch: {result.stderr}")
                return

        # Initialize task state (update_task_state.py integration)
        task_doc = self._load_task_doc(task_id)
        components = self._extract_components(task_doc)

        update_task_state_script = self._project_root / "scripts" / "update_task_state.py"
        if update_task_state_script.exists():
            try:
                result = subprocess.run([
                    sys.executable, str(update_task_state_script), "start",
                    "--task", task_id,
                    "--branch", branch_name,
                    "--components", str(len(components))
                ], cwd=self._project_root, capture_output=True, text=True, check=True)
            except subprocess.CalledProcessError as e:
                print(f"⚠️  Warning: Task state update failed: {e.stderr}")
                print("   Continuing without task state tracking...")

        # Initialize workflow state (delegate to WorkflowGate for atomic writes)
        self._workflow_gate.reset()  # Clean slate

        # Set first component (delegate to WorkflowGate for atomic writes)
        if components:
            self._workflow_gate.set_component(components[0]["name"])

        print(f"✅ Task {task_id} started")
        print(f"   Branch: {branch_name}")
        print(f"   Components: {len(components)}")
        print(f"   Current: {components[0]['name'] if components else 'N/A'}")

    # ========== Private helper methods ==========

    def _generate_task_doc(
        self,
        task_id: str,
        title: str,
        description: str,
        estimated_hours: float
    ) -> Path:
        """
        Generate task document from template.

        Creates a new task document in docs/TASKS/ with standardized format.

        Args:
            task_id: Task identifier
            title: Task title
            description: Task description
            estimated_hours: Estimated effort

        Returns:
            Path to created task document
        """
        # Ensure tasks directory exists
        self._tasks_dir.mkdir(parents=True, exist_ok=True)

        # Generate task filename
        task_file = self._tasks_dir / f"{task_id}_TASK.md"

        # Generate task content
        content = f"""# {task_id}: {title}

**Status:** DRAFT
**Estimated Hours:** {estimated_hours}h
**Created:** {datetime.now(timezone.utc).strftime('%Y-%m-%d')}

## Description

{description}

## Components

<!-- List logical components here -->
<!-- Example: -->
<!-- - Component 1: Description (Xh) -->
<!-- - Component 2: Description (Yh) -->

## Acceptance Criteria

<!-- List acceptance criteria here -->
<!-- Example: -->
<!-- - [ ] Criterion 1 -->
<!-- - [ ] Criterion 2 -->

## Implementation Notes

<!-- Add implementation notes here -->

## Testing Strategy

<!-- Describe testing approach -->

## Dependencies

<!-- List dependencies or blockers -->

---

**Note:** This task document was generated by PlanningWorkflow.
Request task creation review via .claude/workflows/13-task-creation-review.md before starting work.
"""

        # Write task document
        with open(task_file, "w") as f:
            f.write(content)

        return task_file

    def _load_task_doc(self, task_id: str) -> str:
        """
        Load task document content.

        Args:
            task_id: Task identifier

        Returns:
            Task document content as string
            Returns empty string if file not found
        """
        task_file = self._tasks_dir / f"{task_id}_TASK.md"

        if not task_file.exists():
            return ""

        with open(task_file, "r") as f:
            return f.read()

    def _extract_components(self, task_doc: str) -> list[dict]:
        """
        Extract component list from task document.

        Parses the "## Components" section to extract component names
        and estimated hours.

        Args:
            task_doc: Task document content

        Returns:
            List of component dicts with {name, hours}
            Returns empty list if no components found

        Example:
            Input:
                ## Components
                - Component 1: Validator (2h)
                - Component 2: API endpoint (3h)

            Output:
                [
                    {"name": "Component 1: Validator", "hours": 2},
                    {"name": "Component 2: API endpoint", "hours": 3}
                ]
        """
        components = []

        # Find components section
        lines = task_doc.split("\n")
        in_components_section = False

        for line in lines:
            # Start of components section
            if line.strip().startswith("## Components"):
                in_components_section = True
                continue

            # End of components section (next ## heading)
            if in_components_section and line.strip().startswith("##"):
                break

            # Parse component line
            if in_components_section and line.strip().startswith("-"):
                # Extract component name and hours
                # Format: - Component name (Xh)
                content = line.strip()[1:].strip()  # Remove leading "- "

                # Extract hours if present
                hours = 0.0
                if "(" in content and "h)" in content:
                    hours_str = content[content.rfind("(") + 1:content.rfind("h)")].strip()
                    try:
                        hours = float(hours_str)
                    except ValueError:
                        hours = 0.0

                    # Remove hours from name
                    name = content[:content.rfind("(")].strip()
                else:
                    name = content

                components.append({
                    "name": name,
                    "hours": hours
                })

        return components


def main() -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Workflow enforcement gate - Hard enforcement of 4-step component pattern",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Set component name before starting
  %(prog)s set-component "Position Limit Validation"

  # Advance through workflow steps
  %(prog)s advance test       # implement → test
  %(prog)s advance review     # test → review

  # Record review approval
  %(prog)s record-review abc123... APPROVED

  # Record CI result
  %(prog)s record-ci true

  # Check commit prerequisites (called by pre-commit hook)
  %(prog)s check-commit

  # Record commit hash (called by post-commit hook)
  %(prog)s record-commit

  # Show current state
  %(prog)s status

  # Emergency reset (use with caution)
  %(prog)s reset
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Set component
    set_component_parser = subparsers.add_parser(
        "set-component", help="Set current component name"
    )
    set_component_parser.add_argument("name", help="Component name")

    # Advance workflow
    advance_parser = subparsers.add_parser("advance", help="Advance to next step")
    advance_parser.add_argument(
        "next_step", choices=["test", "review"], help="Next workflow step"
    )

    # Record review
    record_review_parser = subparsers.add_parser(
        "record-review", help="Record zen-mcp review result"
    )
    record_review_parser.add_argument(
        "continuation_id", help="Zen-MCP continuation ID"
    )
    record_review_parser.add_argument(
        "status", choices=[REVIEW_APPROVED, REVIEW_NEEDS_REVISION], help="Review status"
    )

    # Record CI
    record_ci_parser = subparsers.add_parser("record-ci", help="Record CI result")
    record_ci_parser.add_argument(
        "passed",
        type=lambda x: x.lower() in ["true", "1", "yes"],
        help="CI passed (true/false)",
    )

    # Check commit
    subparsers.add_parser("check-commit", help="Validate commit prerequisites")

    # Record commit
    record_commit_parser = subparsers.add_parser(
        "record-commit", help="Record commit hash after successful commit"
    )
    record_commit_parser.add_argument(
        "--update-task-state",
        action="store_true",
        help="Also update .claude/task-state.json (optional)",
    )

    # Show status
    subparsers.add_parser("status", help="Show current workflow state")

    # Reset
    subparsers.add_parser("reset", help="Reset workflow state (EMERGENCY USE ONLY)")

    # Context monitoring commands (Component 3)
    subparsers.add_parser("check-context", help="Check current context usage status")

    record_context_parser = subparsers.add_parser(
        "record-context", help="Record current token usage"
    )
    record_context_parser.add_argument(
        "tokens", type=int, help="Current token count"
    )

    subparsers.add_parser(
        "suggest-delegation", help="Get delegation recommendations if thresholds exceeded"
    )

    record_delegation_parser = subparsers.add_parser(
        "record-delegation", help="Record subagent delegation"
    )
    record_delegation_parser.add_argument(
        "task_description", help="Description of delegated task"
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    try:
        gate = WorkflowGate()

        # Instantiate DelegationRules with dependency injection
        delegation_rules = DelegationRules(
            load_state=gate.load_state,
            save_state=gate.save_state,
        )

        if args.command == "set-component":
            gate.set_component(args.name)
        elif args.command == "advance":
            gate.advance(args.next_step)
        elif args.command == "record-review":
            gate.record_review(args.continuation_id, args.status)
        elif args.command == "record-ci":
            gate.record_ci(args.passed)
        elif args.command == "check-commit":
            gate.check_commit()
        elif args.command == "record-commit":
            gate.record_commit(args.update_task_state)
        elif args.command == "status":
            gate.show_status()
        elif args.command == "reset":
            gate.reset()
        elif args.command == "check-context":
            snapshot = delegation_rules.get_context_snapshot()
            status = delegation_rules.format_status(snapshot, snapshot.get("usage_pct", 0))
            print(status)
        elif args.command == "record-context":
            result = delegation_rules.record_context(args.tokens)
            if result.get("error"):
                print(f"Error: {result['error']}")
            else:
                print(f"✅ Context recorded: {result['current_tokens']:,} tokens ({result['usage_pct']:.1f}%)")
        elif args.command == "suggest-delegation":
            suggestion = delegation_rules.suggest_delegation()
            print(suggestion)
        elif args.command == "record-delegation":
            result = delegation_rules.record_delegation(args.task_description)
            if result.get("error"):
                print(f"Error: {result['error']}")
            else:
                print(f"✅ Delegation recorded: {result['task_description']}")

        return 0

    except SystemExit as e:
        return e.code
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
