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
import subprocess
import sys
from pathlib import Path
from typing import Literal, Tuple

# Constants
PROJECT_ROOT = Path(__file__).parent.parent
STATE_FILE = PROJECT_ROOT / ".claude" / "workflow-state.json"

StepType = Literal["implement", "test", "review"]


class WorkflowGate:
    """Enforces 4-step workflow pattern with hard gates."""

    VALID_TRANSITIONS = {
        "implement": ["test"],
        "test": ["review"],
        "review": ["implement"],  # Can only go back to fix issues
    }

    def _init_state(self) -> dict:
        """Initialize default workflow state."""
        return {
            "current_component": "",
            "step": "implement",
            "zen_review": {},
            "ci_passed": False,
            "last_commit_hash": None,
            "subagent_delegations": [],
        }

    def load_state(self) -> dict:
        """Load workflow state from JSON file."""
        if not STATE_FILE.exists():
            return self._init_state()
        return json.loads(STATE_FILE.read_text())

    def save_state(self, state: dict) -> None:
        """Save workflow state to JSON file with atomic write."""
        import tempfile

        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write: write to temp file then rename
        # Prevents corruption from partial writes
        temp_fd, temp_path = tempfile.mkstemp(
            dir=STATE_FILE.parent,
            prefix=".workflow-state-",
            suffix=".tmp"
        )
        try:
            with open(temp_fd, 'w') as f:
                json.dump(state, f, indent=2)

            # Atomic rename
            Path(temp_path).replace(STATE_FILE)
        except Exception:
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

        if status == "NEEDS_REVISION":
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
            print(f"     1. Implement ({'✓' if state['step'] == 'implement' else ' '})")
            print(f"     2. Test ({'✓' if state['step'] == 'test' else ' '})")
            print(f"     3. Review ({'✓' if state['step'] == 'review' else ' '})")
            print("   Progress to next step:")
            print(f"     ./scripts/workflow_gate.py advance <next_step>")
            sys.exit(1)

        # Check zen review approval
        if not state["zen_review"].get("status") == "APPROVED":
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print("❌ COMMIT BLOCKED: Zen review not approved")
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print(
                "   Continuation ID:", state["zen_review"].get("continuation_id", "N/A")
            )
            print("   Status:", state["zen_review"].get("status", "NOT_REQUESTED"))
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
        state["commit_history"].append(commit_hash)
        state["last_commit_hash"] = commit_hash  # Kept for backward compatibility
        state["step"] = "implement"  # Ready for next component
        state["zen_review"] = {}
        state["ci_passed"] = False
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

        # Try multiple common patterns
        patterns = [
            str(PROJECT_ROOT / f"tests/**/test_{component_slug}*.py"),  # test_component*.py
            str(PROJECT_ROOT / f"tests/**/*{component_slug}*_test.py"),  # *component*_test.py
            str(PROJECT_ROOT / f"tests/**/*{component_slug}*.py"),     # *component*.py (broad)
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
        import json

        task_state_file = PROJECT_ROOT / ".claude" / "task-state.json"

        # Check if task state tracking is active
        if not task_state_file.exists():
            print("ℹ️  No task state file found, skipping task state update")
            return

        try:
            with open(task_state_file) as f:
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
            "python3",
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
        "status", choices=["APPROVED", "NEEDS_REVISION"], help="Review status"
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

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    try:
        gate = WorkflowGate()

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
            gate.record_commit()
        elif args.command == "status":
            gate.show_status()
        elif args.command == "reset":
            gate.reset()

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
