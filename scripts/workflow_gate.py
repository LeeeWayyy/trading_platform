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
import fcntl
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

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
        "plan": ["implement"],  # Phase 1: Planning step before implementation
        "implement": ["test"],
        "test": ["review"],
        "review": ["implement"],  # HIGH-001 fix: Can go back to implement after review failure
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
            "step": "plan",  # Phase 1: Start with planning step
            "zen_review": {},
            "ci_passed": False,
            "last_commit_hash": None,
            "commit_history": [],
            "subagent_delegations": [],
            "context": {
                "current_tokens": 0,
                "max_tokens": int(os.getenv("CLAUDE_MAX_TOKENS", "200000")),
                "last_check_timestamp": datetime.now(UTC).isoformat(),
            },
            # Phase 1: Planning discipline enforcement fields
            "task_file": None,  # Path to docs/TASKS/<task_id>_TASK.md
            "analysis_completed": False,  # Checklist completion flag
            "components": [],  # [{"num": 1, "name": "..."}]
            "first_commit_made": False,  # First commit detection flag
            "context_cache": {  # Performance optimization for context checks
                "tokens": 0,
                "timestamp": None,
                "git_index_hash": None,  # git rev-parse HEAD
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
                "last_check_timestamp": datetime.now(UTC).isoformat(),
            }
        return state

    def _ensure_planning_defaults(self, state: dict) -> dict:
        """
        Ensure Phase 1 planning fields exist for backward compatibility.

        Migrates legacy state files from Phase 0 that don't have planning discipline fields.
        Called immediately after loading state to prevent KeyError.

        Args:
            state: Loaded state dictionary

        Returns:
            State with planning defaults ensured
        """
        if "task_file" not in state:
            state["task_file"] = None
        if "analysis_completed" not in state:
            state["analysis_completed"] = False
        if "components" not in state:
            state["components"] = []
        if "first_commit_made" not in state:
            state["first_commit_made"] = False
        if "context_cache" not in state:
            state["context_cache"] = {
                "tokens": 0,
                "timestamp": None,
                "git_index_hash": None,
            }
        return state

    def _acquire_lock(self, max_retries: int = 3) -> int:
        """
        Acquire exclusive file lock for state file.

        Returns file descriptor for lock file.
        Lock must be released by caller using _release_lock().
        """
        lock_file = self._state_file.parent / ".workflow-state.lock"
        lock_file.parent.mkdir(parents=True, exist_ok=True)

        for attempt in range(max_retries):
            lock_fd = None
            try:
                lock_fd = os.open(str(lock_file), os.O_CREAT | os.O_WRONLY, 0o644)
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return lock_fd
            except OSError as e:
                # Close file descriptor before retry to prevent leak
                if lock_fd is not None:
                    try:
                        os.close(lock_fd)
                    except OSError:
                        pass  # Ignore close errors
                if attempt < max_retries - 1:
                    # Exponential backoff: 0.1s, 0.2s, 0.4s
                    time.sleep(0.1 * (2**attempt))
                    continue
                raise RuntimeError(f"Failed to acquire lock after {max_retries} attempts") from e
        raise RuntimeError("Lock acquisition failed")

    def _release_lock(self, lock_fd: int) -> None:
        """Release file lock."""
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        except OSError as e:
            print(f"⚠️  Warning: Failed to release lock: {e}")

    def _save_state_unlocked(self, state: dict) -> None:
        """
        Save workflow state without acquiring lock (internal use only).

        Used by _locked_state context manager where lock is already held.
        For external use, call save_state() which includes locking.
        """
        self._state_file.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write: write to temp file then rename
        temp_fd, temp_path = tempfile.mkstemp(
            dir=self._state_file.parent, prefix=".workflow-state-", suffix=".tmp"
        )
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            # Atomic rename
            Path(temp_path).replace(self._state_file)
        except OSError:
            # Clean up temp file on error
            Path(temp_path).unlink(missing_ok=True)
            raise

    @contextmanager
    def _locked_state(self) -> Generator[dict, None, None]:
        """
        Context manager for atomic read-modify-write operations.

        CRITICAL (CRIT-002 fix): Ensures entire read-modify-write cycle is wrapped
        in a file lock, preventing race conditions from concurrent processes.

        Usage:
            with self._locked_state() as state:
                state["field"] = new_value
                # state automatically saved on exit with lock held

        The lock is held for the entire duration of:
        1. Load state
        2. Yield to caller for modifications
        3. Save modified state
        4. Release lock (in finally)
        """
        lock_fd = self._acquire_lock()
        try:
            # Load state with lock held
            state = self.load_state()
            # Yield to caller for modifications
            yield state
            # Save modified state with lock still held
            self._save_state_unlocked(state)
        finally:
            # Always release lock, even if exception occurred
            self._release_lock(lock_fd)

    @contextmanager
    def locked_state_context(self) -> Generator[dict, None, None]:
        """
        Public API for atomic locked state modifications.

        MEDIUM fix from Gemini review: Provide public API instead of exposing
        internal _locked_state() method to external classes like PlanningWorkflow.

        Yields:
            state: Workflow state dict that will be automatically saved on exit

        Example:
            with gate.locked_state_context() as state:
                state["field"] = new_value
                # State automatically saved with lock held
        """
        with self._locked_state() as state:
            yield state

    def _refresh_context_cache(self, state: dict) -> None:
        """
        Refresh context cache with current git index hash.

        MEDIUM fix from Gemini review: Extract duplicated cache refresh logic
        to avoid code duplication in record_context() and record_delegation().

        Updates state["context_cache"] with:
        - tokens: Current context token count
        - timestamp: Current time (for 5-minute timeout)
        - git_index_hash: Hash of staged changes (for invalidation on new changes)
        """
        try:
            git_index_hash = subprocess.check_output(
                ["git", "write-tree"], cwd=PROJECT_ROOT, text=True, stderr=subprocess.DEVNULL
            ).strip()
        except subprocess.CalledProcessError:
            git_index_hash = "unknown"

        state["context_cache"] = {
            "tokens": state["context"]["current_tokens"],
            "timestamp": time.time(),
            "git_index_hash": git_index_hash,
        }

    def load_state(self) -> dict:
        """Load workflow state from JSON file."""
        if not self._state_file.exists():
            return self._init_state()
        try:
            state = json.loads(self._state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"⚠️  Warning: Failed to parse workflow state file: {e}")
            print("   Initializing fresh state...")
            return self._init_state()
        # Ensure backward compatibility with old state files (Phase 0 + Phase 1)
        state = self._ensure_context_defaults(state)
        state = self._ensure_planning_defaults(state)
        return state

    def save_state(self, state: dict) -> None:
        """
        Save workflow state to JSON file with atomic write and file locking.

        CRITICAL (CRIT-002 fix): Acquires exclusive lock before write to prevent
        race conditions when multiple processes modify state concurrently.

        Note: For read-modify-write operations, use _locked_state() context manager
        instead to ensure the entire cycle is atomic.
        """
        # Acquire lock for standalone save
        lock_fd = self._acquire_lock()
        try:
            self._save_state_unlocked(state)
        finally:
            self._release_lock(lock_fd)

    def locked_modify_state(self, modifier: Callable[[dict], None]) -> dict:
        """
        Perform locked read-modify-write operation (FIX-7).

        For use by DelegationRules to ensure atomic operations.
        The modifier callback receives state dict and modifies it in-place.
        State is saved automatically after modification.

        Args:
            modifier: Callback that modifies state dict in-place

        Returns:
            Modified state dict
        """
        with self._locked_state() as state:
            modifier(state)
            return state

    def can_transition(self, current: StepType, next: StepType) -> tuple[bool, str]:
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
                    "   Create tests for component: " + (state["current_component"] or "UNKNOWN")
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
        with self._locked_state() as state:
            current = state["step"]

            can, error_msg = self.can_transition(current, next)
            if not can:
                print(error_msg)
                sys.exit(1)

            # Special logic for review step
            if next == "review":
                print("🔍 Requesting zen-mcp review (clink + gemini → codex)...")
                print("   Follow: .claude/workflows/03-reviews.md")
                print("   After review, record approval:")
                print("     ./scripts/workflow_gate.py record-review <continuation_id> <status>")

            # Update state
            state["step"] = next
            # State automatically saved when exiting context

        print(f"✅ Advanced to '{next}' step")

    def record_review(self, continuation_id: str, status: str) -> None:
        """
        Record zen-mcp review result.

        For PR reviews, also updates the unified_review.history to enable
        the override workflow for LOW severity issues after max iterations.

        Args:
            continuation_id: Zen-MCP continuation ID from review
            status: Review status ("APPROVED" or "NEEDS_REVISION")
        """
        with self._locked_state() as state:
            state["zen_review"] = {
                "requested": True,
                "continuation_id": continuation_id,
                "status": status,  # "APPROVED" or "NEEDS_REVISION"
            }

            # Check if this is a PR review by looking for pending unified_review history
            review_state = state.get("unified_review", {})
            review_history = review_state.get("history", [])

            if review_history and review_history[-1].get("status") == "PENDING":
                # Update the latest pending entry with review result
                review_history[-1].update(
                    {
                        "continuation_id": continuation_id,
                        "status": status,
                        "completed_at": datetime.now(UTC).isoformat(),
                    }
                )
                print(f"✅ Updated PR review history (iteration {review_history[-1]['iteration']})")
            # State automatically saved when exiting context

        print(f"✅ Recorded zen review: {status}")

        if status == REVIEW_NEEDS_REVISION:
            print("⚠️  Review requires changes. Fix issues and re-request review.")
            print("   After fixes:")
            print(
                "     ./scripts/workflow_gate.py advance implement  # FIX-8: Return to implement for rework"
            )

    def record_ci(self, passed: bool) -> None:
        """
        Record CI result.

        Args:
            passed: True if CI passed, False otherwise
        """
        with self._locked_state() as state:
            state["ci_passed"] = passed
            # State automatically saved when exiting context

        print(f"✅ Recorded CI: {'PASSED' if passed else 'FAILED'}")

        if not passed:
            print("⚠️  CI failed. Fix issues and re-run:")
            print("   make ci-local && ./scripts/workflow_gate.py record-ci true")

    def _is_first_commit(self) -> bool:
        """
        Check if this is the first commit on the current branch/task (Phase 1).

        Uses state flag for reliable detection across branch operations.

        Returns:
            True if this is the first commit, False otherwise
        """
        state = self.load_state()
        return not state.get("first_commit_made", False)

    def _has_planning_artifacts(self) -> bool:
        """
        Check if all required planning artifacts exist (Phase 1).

        Required artifacts:
        1. Task document (task_file must be set and file must exist)
        2. Analysis completed (analysis_completed must be True)
        3. Component breakdown (≥2 components defined)

        Returns:
            True if all artifacts present, False otherwise
        """
        state = self.load_state()

        # Check 1: Task document exists
        task_file = state.get("task_file")
        if not task_file:
            print("❌ Missing: task_file not set in workflow state")
            print("   Run: ./scripts/workflow_gate.py start-task <task_id> <branch>")
            return False

        task_path = Path(task_file)
        if not task_path.exists():
            print(f"❌ Missing: task document not found at {task_file}")
            print(f"   Expected path: {task_path.absolute()}")
            return False

        # Check 2: Analysis checklist completed
        if not state.get("analysis_completed", False):
            print("❌ Missing: pre-implementation analysis not completed")
            print("   Follow: .claude/workflows/00-analysis-checklist.md")
            print("   Then: ./scripts/workflow_gate.py record-analysis-complete")
            return False

        # Check 3: Component breakdown exists (≥2 components)
        components = state.get("components", [])
        if len(components) < 2:
            print(f"❌ Missing: need ≥2 components, found {len(components)}")
            print("   Run: ./scripts/workflow_gate.py set-components '<name 1>' '<name 2>' ...")
            return False

        return True

    def _is_complex_task(self) -> bool:
        """
        Check if task is complex (3+ components) requiring TodoWrite (Phase 1).

        Returns:
            True if task has ≥3 components, False otherwise
        """
        state = self.load_state()
        components = state.get("components", [])
        return len(components) >= 3

    def _has_active_todos(self) -> bool:
        """
        Check if TodoWrite tool has been used (Phase 1).

        Validates that session-todos.json exists with valid structure.
        Uses relaxed validation (R1 fix) to be robust to Claude Code format changes.

        Returns:
            True if todos file exists with valid structure, False otherwise
        """
        # Q2 Decision: Shared session-todos.json for entire session
        todos_file = PROJECT_ROOT / ".claude" / "session-todos.json"

        if not todos_file.exists():
            return False

        # Validate JSON schema (not just existence)
        try:
            with open(todos_file, encoding="utf-8") as f:
                data = json.load(f)

            # Check 1: Must be a dictionary or array
            if isinstance(data, dict):
                # Format: {"todos": [...]}
                todos = data.get("todos", [])
            elif isinstance(data, list):
                # Format: [...]
                todos = data
            else:
                print(f"⚠️  Warning: {todos_file} is not a list or dict")
                return False

            # Check 2: Must have at least one todo
            if len(todos) == 0:
                print(f"⚠️  Warning: {todos_file} is empty")
                return False

            # Check 3: Minimal validation - each todo must be a dict
            # R1 fix: Do NOT require specific fields (Claude Code may change format)
            for i, todo in enumerate(todos):
                if not isinstance(todo, dict):
                    print(f"⚠️  Warning: Todo {i} is not a dict in {todos_file}")
                    return False

                # Optional: Log info for missing recommended fields (not error)
                if "content" not in todo:
                    print(f"ℹ️  Info: Todo {i} missing 'content' field")
                if "status" not in todo:
                    print(f"ℹ️  Info: Todo {i} missing 'status' field")

            return True

        except json.JSONDecodeError as e:
            print(f"⚠️  Warning: Failed to parse {todos_file}: {e}")
            return False

    def _get_cached_context_tokens(self) -> int:
        """
        Get current context usage with caching for performance (Phase 1).

        Uses hybrid invalidation strategy (RC1 fix):
        - Time-based: Cache expires after 5 minutes
        - Change-based: Git index hash detects commits/stage changes (cheap operation)

        Performance target: <100ms cache hit, <1s cache miss

        Returns:
            Current token count
        """
        import subprocess
        import time

        state = self.load_state()
        cache = state.get("context_cache", {})

        # Get current git index hash (cheap: ~10-20ms)
        # Phase 1 MEDIUM fix: Use git write-tree to detect staged changes
        # (git rev-parse HEAD only changes after commit, missing staged files)
        try:
            git_index_hash = subprocess.check_output(
                ["git", "write-tree"], cwd=PROJECT_ROOT, text=True, stderr=subprocess.DEVNULL
            ).strip()
        except subprocess.CalledProcessError:
            # Fallback if not in git repo or detached state
            git_index_hash = "unknown"

        # Calculate cache age
        now = time.time()
        cache_timestamp = cache.get("timestamp")
        cache_age = now - cache_timestamp if cache_timestamp else float("inf")

        # Invalidate if:
        # 1. Cache older than 5 minutes
        # 2. Git index changed (new commits, stage changes)
        # 3. No cache exists (check explicitly for None, not truthiness, so 0 is valid)
        if (
            cache_age > 300  # 5 minutes
            or cache.get("git_index_hash") != git_index_hash
            or cache.get("tokens") is None
        ):

            # Expensive operation: calculate tokens via DelegationRules
            delegation_rules = DelegationRules(
                load_state=self.load_state,
                save_state=self.save_state,
                locked_modify_state=self.locked_modify_state,
            )

            # RC1 fix: Use existing API get_context_snapshot() (not get_current_tokens())
            snapshot = delegation_rules.get_context_snapshot()
            tokens: int = snapshot.get("current_tokens", 0)

            # Update cache
            with self._locked_state() as state:
                state["context_cache"] = {
                    "tokens": tokens,
                    "timestamp": now,
                    "git_index_hash": git_index_hash,
                }

            return tokens

        # Return cached value (fast: <1ms)
        cached_tokens: int = cache.get("tokens", 0)
        return cached_tokens

    def check_commit(self) -> None:
        """
        Validate commit prerequisites (called by pre-commit hook).

        Enforces hard gates:
        - Phase 1: Planning artifacts (first commit only)
        - Phase 1: TodoWrite for complex tasks (every commit)
        - Phase 1: Context delegation threshold (every commit)
        - Current step must be "review"
        - Zen-MCP review must be APPROVED
        - CI must be passing

        HIGH-002 / FIX-10b (CRITICAL): Supports emergency override via ZEN_REVIEW_OVERRIDE
        environment variable. This approach works because environment variables are set
        BEFORE Git runs any hooks (including pre-commit).

        Usage:
            ZEN_REVIEW_OVERRIDE=1 git commit -m "emergency: fix production outage"

        Why environment variable approach:
        - Git hook order is: pre-commit → prepare-commit-msg → commit-msg → post-commit
        - No hook runs before pre-commit, so commit message can't be inspected reliably
        - Environment variables are set before Git starts, so available in pre-commit
        - No stale flag files to clean up

        Raises:
            SystemExit: If prerequisites are not met
        """
        # Phase 1: Performance instrumentation (track pre-commit hook duration)
        import time

        start_time = time.time()

        # FIX-10b: Check for override environment variable
        override_env = os.environ.get("ZEN_REVIEW_OVERRIDE", "").strip()
        if override_env in ("1", "true", "TRUE", "True", "yes", "YES"):
            # Read commit message for audit logging (best effort)
            commit_msg_file = PROJECT_ROOT / ".git" / "COMMIT_EDITMSG"
            commit_msg = ""
            try:
                if commit_msg_file.exists():
                    commit_msg = commit_msg_file.read_text(encoding="utf-8")
            except OSError:
                pass

            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print("⚠️  EMERGENCY OVERRIDE DETECTED")
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print(f"   ZEN_REVIEW_OVERRIDE={override_env}")
            print("   Bypassing workflow gates for emergency hotfix")
            print("   ⚠️  This override is logged and auditable")
            print("   ⚠️  DO NOT SET override_env without user approval ")
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

            # Log override for audit
            import logging

            override_log = PROJECT_ROOT / ".claude" / "workflow-overrides.log"
            override_log.parent.mkdir(parents=True, exist_ok=True)
            logging.basicConfig(
                filename=str(override_log),
                level=logging.WARNING,
                format="%(asctime)s - %(message)s",
            )
            logging.warning(f"ZEN_REVIEW_OVERRIDE={override_env} - bypassing workflow gates")
            logging.warning(f"  Message: {commit_msg.splitlines()[0] if commit_msg else 'N/A'}")

            sys.exit(0)  # Allow commit

        # Phase 1 Gate 0: Planning artifacts (first commit only)
        if self._is_first_commit():
            if not self._has_planning_artifacts():
                print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                print("❌ COMMIT BLOCKED: Missing planning artifacts")
                print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                print("   Required before first commit:")
                print("     1. Task document (docs/TASKS/)")
                print("     2. Analysis checklist completion")
                print("     3. Component breakdown (≥2 components)")
                print()
                print("   Complete planning steps:")
                print("     ./scripts/workflow_gate.py start-task <task_id> <branch>")
                print("     ./scripts/workflow_gate.py record-analysis-complete")
                print("     ./scripts/workflow_gate.py set-components '<name>' '<name>' ...")
                print()
                print("   Then advance to implement:")
                print("     ./scripts/workflow_gate.py advance implement")
                print()
                print("   Emergency bypass (production outage only):")
                print('     ZEN_REVIEW_OVERRIDE=1 git commit -m "..."')
                print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                sys.exit(1)

        # Phase 1 Gate 0.5: TodoWrite for complex tasks (every commit)
        if self._is_complex_task() and not self._has_active_todos():
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print("❌ COMMIT BLOCKED: Complex task requires todo tracking")
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print("   This task has ≥3 components but no active todos")
            print()
            print("   Create todo list using TodoWrite tool in Claude Code")
            print()
            print("   Manual fallback - create .claude/session-todos.json:")
            print("     [")
            print('       {"content": "Component 1", "status": "pending"},')
            print('       {"content": "Component 2", "status": "pending"}')
            print("     ]")
            print()
            print("   Emergency bypass (production outage only):")
            print('     ZEN_REVIEW_OVERRIDE=1 git commit -m "..."')
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            sys.exit(1)

        # Phase 1 Gate 0.6: Context delegation threshold (every commit)
        # P1 fix: Load state to access configured max_tokens (Codex review)
        state = self.load_state()
        current_tokens = self._get_cached_context_tokens()
        context = state.get("context", {})
        max_tokens = context.get("max_tokens", 200_000)  # Use configured limit
        usage_percent = (current_tokens / max_tokens) * 100 if max_tokens > 0 else 0

        if usage_percent >= 85:  # MANDATORY threshold
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print("❌ COMMIT BLOCKED: Context usage ≥85%")
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print(f"   Current: {current_tokens:,} / {max_tokens:,} tokens ({usage_percent:.1f}%)")
            print()
            print("   You MUST delegate before committing:")
            print("     1. ./scripts/workflow_gate.py suggest-delegation")
            print("     2. ./scripts/workflow_gate.py record-delegation '<task description>'")
            print()
            print("   After delegation:")
            print("     - Context resets to 0")
            print("     - Commit will be allowed")
            print()
            print("   Emergency bypass (production outage only):")
            print('     ZEN_REVIEW_OVERRIDE=1 git commit -m "..."')
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            sys.exit(1)

        # Show warning at 70% (informational)
        if 70 <= usage_percent < 85:
            print(f"⚠️  Warning: Context usage at {usage_percent:.1f}%")
            print(f"   Current: {current_tokens:,} / {max_tokens:,} tokens")
            print("   Consider delegating soon:")
            print("     ./scripts/workflow_gate.py suggest-delegation")
            print()

        state = self.load_state()

        # Check current step
        if state["step"] != "review":
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print(f"❌ COMMIT BLOCKED: Current step is '{state['step']}', must be 'review'")
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print(f"   Component: {state['current_component'] or 'UNKNOWN'}")
            print("   Current workflow state:")
            # Show completed steps with ✓
            print(f"     1. Implement ({'✓' if state['step'] in ['test', 'review'] else ' '})")
            print(f"     2. Test ({'✓' if state['step'] == 'review' else ' '})")
            print("     3. Review ( )")
            print("   Progress to next step:")
            print("     ./scripts/workflow_gate.py advance <next_step>")
            print()
            print("   Emergency override (production outage only):")
            print('     ZEN_REVIEW_OVERRIDE=1 git commit -m "..."')
            sys.exit(1)

        # Check zen review approval
        if not state["zen_review"].get("status") == REVIEW_APPROVED:
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print("❌ COMMIT BLOCKED: Zen review not approved")
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print("   Continuation ID:", state["zen_review"].get("continuation_id", "N/A"))
            print("   Status:", state["zen_review"].get("status", REVIEW_NOT_REQUESTED))
            print("   Request review:")
            print("     Follow: .claude/workflows/03-reviews.md")
            print("   After approval:")
            print("     ./scripts/workflow_gate.py record-review <continuation_id> APPROVED")
            print()
            print("   Emergency override (production outage only):")
            print('     ZEN_REVIEW_OVERRIDE=1 git commit -m "..."')
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
            print()
            print("   Emergency override (production outage only):")
            print('     ZEN_REVIEW_OVERRIDE=1 git commit -m "..."')
            sys.exit(1)

        # All gates passed
        print("✅ Commit prerequisites satisfied")
        print(f"   Component: {state['current_component']}")
        print(f"   Zen review: {state['zen_review']['continuation_id'][:8]}...")
        print("   CI: PASSED")

        # Phase 1: Performance instrumentation (report hook duration)
        end_time = time.time()
        duration_ms = (end_time - start_time) * 1000

        if duration_ms > 1000:  # Warn if slower than 1 second
            print(f"⚠️  Pre-commit hook took {duration_ms:.0f}ms (slow, target <1000ms)")

        sys.exit(0)

    def record_commit(self, update_task_state: bool = False) -> None:
        """
        Record commit hash after successful commit (called post-commit).

        Captures the commit hash and resets state for next component.
        Optionally updates task state tracking if enabled.

        Args:
            update_task_state: If True, also update .claude/task-state.json
        """
        # Get the commit hash (outside lock - doesn't need state)
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

        with self._locked_state() as state:
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
            # Remove deprecated last_commit_hash field (state file hygiene)
            state.pop("last_commit_hash", None)
            state["step"] = "implement"  # Ready for next component
            state["zen_review"] = {}
            state["ci_passed"] = False

            # Phase 1: Mark that first commit has been made (planning gates won't check again)
            state["first_commit_made"] = True

            # Reset context after commit, ready for next component (Component 3)
            state["context"]["current_tokens"] = 0
            state["context"]["last_check_timestamp"] = datetime.now(UTC).isoformat()
            # Invalidate context cache after reset (P1 fix from Codex review)
            state["context_cache"] = {
                "tokens": 0,
                "timestamp": 0,
                "git_index_hash": "",
            }
            # State automatically saved when exiting context

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
        with self._locked_state() as state:
            state["current_component"] = component_name
            # State automatically saved when exiting context

        print(f"✅ Set current component: {component_name}")

    def record_analysis_complete(self, checklist_file: str | None = None) -> None:
        """
        Mark pre-implementation analysis as complete (Phase 1).

        Sets analysis_completed=True in workflow state to satisfy planning gate.
        Optionally validates checklist file exists.

        Args:
            checklist_file: Optional path to analysis checklist file for validation

        Example:
            >>> gate = WorkflowGate()
            >>> gate.record_analysis_complete("./claude/analysis/P1T14-checklist.md")
            ✅ Analysis marked as complete
        """
        # Validate checklist file if provided
        if checklist_file:
            checklist_path = Path(checklist_file)
            if not checklist_path.exists():
                print(f"❌ Checklist file not found: {checklist_file}")
                sys.exit(1)

        with self._locked_state() as state:
            state["analysis_completed"] = True

        print("✅ Analysis marked as complete")
        if checklist_file:
            print(f"   Checklist: {checklist_file}")

    def set_components_list(self, components: list[str]) -> None:
        """
        Define component breakdown for task (Phase 1).

        Stores component list in workflow state to satisfy planning gate.
        Must have ≥2 components.

        Args:
            components: List of component names

        Example:
            >>> gate = WorkflowGate()
            >>> gate.set_components_list(["Core logic", "API endpoints", "Tests"])
            ✅ Set 3 components:
               1. Core logic
               2. API endpoints
               3. Tests
        """
        if len(components) < 2:
            print(f"❌ Must have at least 2 components, got {len(components)}")
            sys.exit(1)

        # Format as structured list with numbers
        component_list = [{"num": i + 1, "name": name} for i, name in enumerate(components)]

        with self._locked_state() as state:
            state["components"] = component_list

        print(f"✅ Set {len(components)} components:")
        for comp in component_list:
            print(f"   {comp['num']}. {comp['name']}")

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
        print(
            f"  2. Test {'✓' if state['step'] in ['review'] else '← YOU ARE HERE' if state['step'] == 'test' else ''}"
        )
        print(f"  3. Review {'← YOU ARE HERE' if state['step'] == 'review' else ''}")
        print()
        print("Gate Status:")
        zen_status = state["zen_review"].get("status", "NOT_REQUESTED")
        ci_status = "PASSED" if state["ci_passed"] else "NOT_RUN"
        print(f"  Zen Review: {zen_status}")
        if state["zen_review"].get("continuation_id"):
            print(f"    Continuation ID: {state['zen_review']['continuation_id'][:12]}...")
        print(f"  CI: {ci_status}")
        print()

        # Show latest commit from history (replaces deprecated last_commit_hash)
        if state.get("commit_history"):
            latest_commit = state["commit_history"][-1]
            print(f"Last Commit: {latest_commit[:8]}")
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
                print("  Follow: .claude/workflows/03-reviews.md")
                print("  ./scripts/workflow_gate.py record-review <continuation_id> APPROVED")
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

        # Use context manager for atomic reset
        lock_fd = self._acquire_lock()
        try:
            state = self._init_state()
            self._save_state_unlocked(state)
        finally:
            self._release_lock(lock_fd)

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
            str(
                PROJECT_ROOT / f"tests/**/test_{component_slug}.py"
            ),  # e.g., tests/test_my_component.py
            str(
                PROJECT_ROOT / f"tests/**/{component_slug}_test.py"
            ),  # e.g., tests/my_component_test.py
            # Wildcard matches (partial component name, allows subdirectories)
            str(
                PROJECT_ROOT / f"tests/**/test_{component_slug}_*.py"
            ),  # e.g., tests/test_my_component_extra.py
            str(
                PROJECT_ROOT / f"tests/**/test_*{component_slug}*.py"
            ),  # e.g., tests/test_feature_my_component.py or tests/unit/test_my_component.py
            str(
                PROJECT_ROOT / f"tests/**/*{component_slug}*_test.py"
            ),  # e.g., tests/unit/my_component_integration_test.py
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
            with open(task_state_file, encoding="utf-8") as f:
                task_state = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
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
            # HIGH-004 fix: Fail hard on subprocess error to prevent state divergence
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print("❌ CRITICAL ERROR: Failed to update task state")
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print(f"   Error: {e}")
            print(f"   Stderr: {e.stderr if e.stderr else 'N/A'}")
            print()
            print("   This is a critical failure. Task state and workflow state")
            print("   are now out of sync. Manual intervention required.")
            print()
            print("   To fix manually:")
            print(
                f"   ./scripts/update_task_state.py complete --component {component_num} --commit {commit_hash}"
            )
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            # Raise exception to halt workflow
            raise RuntimeError(f"Task state update failed: {e}") from e


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
                detect_changed_modules,
                get_staged_files,
                requires_full_ci,
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

        # Cache git result to avoid multiple calls (prevents race conditions)
        self._staged_files_cache: list[str] | None | bool = False  # False = not fetched
        self._git_failed = False  # Track if git command failed

    def _get_cached_staged_files(self) -> list[str] | None:
        """
        Get staged files with caching to prevent multiple git calls.

        Returns:
            List of staged files, or None if git failed.
            Caches result for subsequent calls.
        """
        # Return cached result if already fetched
        if self._staged_files_cache is not False:
            return self._staged_files_cache  # type: ignore

        # Fetch from git
        result = self._get_staged_files()

        # Cache result
        self._staged_files_cache = result
        self._git_failed = result is None

        return result

    def should_run_full_ci(self) -> bool:
        """
        Determine if full CI should run.

        Full CI runs if:
        - Git command failed (fail-safe: can't determine changes)
        - Any CORE_PACKAGE changed (libs/, config/, infra/, tests/fixtures/, scripts/)
        - More than 5 modules changed (likely a refactor)

        Returns:
            True if full CI required, False if targeted tests OK
        """
        staged_files = self._get_cached_staged_files()

        # Fail-safe: If git failed (None), run full CI
        if staged_files is None:
            return True

        if not staged_files:
            # No changes = no tests needed (edge case)
            return False

        return self._requires_full_ci(staged_files)

    def get_test_targets(self) -> list[str]:
        """
        Get targeted test paths for changed modules and test files.

        Returns list of pytest paths to run for changed code or tests.

        Handles:
        1. Test file changes - runs the changed test files directly
        2. Code changes - runs corresponding test directories
        3. Integration tests - runs tests/integration/ for app changes
        4. Git failures - returns empty list (triggers full CI via should_run_full_ci)

        Returns:
            List of test paths (e.g., ["tests/libs/allocation/", "tests/scripts/test_workflow_gate.py"])
            Empty list if no files staged or if git command failed
        """
        staged_files = self._get_cached_staged_files()
        # Handle both None (git failed) and empty list (no changes)
        if not staged_files:
            return []

        test_paths = []

        # 1. Detect direct test file changes
        # When test files themselves change, we must run them
        # (Addresses P1: Skip tests when only test files change)
        for file in staged_files:
            if file.startswith("tests/") and file.endswith(".py"):
                # Add test file directly if it's not __init__.py
                if not file.endswith("__init__.py"):
                    test_paths.append(file)

        # 2. Detect changed modules and map to test directories
        modules = self._detect_changed_modules(staged_files)
        for module in modules:
            # module format: "libs/allocation", "apps/execution_gateway"
            test_path = f"tests/{module}/"
            test_paths.append(test_path)

        # 3. Detect integration test triggers
        # App changes should also run integration tests
        # (Addresses HIGH: get_test_targets too simple)
        app_modules = {m for m in modules if m.startswith("apps/")}
        if app_modules and "tests/integration/" not in test_paths:
            test_paths.append("tests/integration/")

        return sorted(set(test_paths))  # Deduplicate while maintaining order

    def get_test_command(self, context: str = "commit") -> list[str]:
        """
        Get appropriate test command based on context.

        Args:
            context: "commit" for progressive commits, "pr" for pull requests

        Returns:
            Command as list of arguments (safe for subprocess.run without shell=True)
        """
        if context == "pr" or self.should_run_full_ci():
            # Full CI for PRs or when core packages changed
            return ["make", "ci-local"]

        # Targeted tests for commits
        test_targets = self.get_test_targets()
        if not test_targets:
            # No test targets = no Python changes
            return ["echo", "No Python tests needed (no code changes detected)"]

        # Run targeted tests via poetry (ensures correct interpreter and dependencies)
        # CRITICAL: Must use poetry run to match CI environment and dependency versions
        return ["poetry", "run", "pytest"] + test_targets

    def print_test_strategy(self) -> None:
        """Print test strategy recommendation to user."""
        if self.should_run_full_ci():
            print("🔍 Full CI Required")
            if self._git_failed:
                print("   Reason: Git command failed (fail-safe: running full CI)")
            else:
                print("   Reason: Core package changed OR >5 modules changed")
            print("   Command: make ci-local")
        else:
            test_targets = self.get_test_targets()
            if not test_targets:
                print("✓ No tests needed (no code changes)")
            else:
                print("🎯 Targeted Testing")
                print(f"   Modules: {', '.join(test_targets)}")
                cmd_list = self.get_test_command("commit")
                print(f"   Command: {' '.join(cmd_list)}")


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
        locked_modify_state: Callable[[Callable[[dict], None]], dict] | None = None,
    ) -> None:
        """
        Initialize DelegationRules with state management callables.

        Args:
            load_state: Callable that returns current workflow state dict
            save_state: Callable that persists updated state dict
            locked_modify_state: FIX-7 - Callable for atomic read-modify-write operations

        This dependency injection pattern enables easy testing with fake
        state managers, mirroring SmartTestRunner's lazy import pattern.
        """
        self._load_state = load_state
        self._save_state = save_state
        self._locked_modify_state = locked_modify_state

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

        FIX-7: Uses locked_modify_state if available for atomic operation.
        """
        # Sanitize input
        tokens = max(0, tokens)

        # FIX-7: Use locked operation if available
        if self._locked_modify_state:

            def modifier(state: dict) -> None:
                # Update context
                if "context" not in state:
                    state["context"] = {}
                state["context"]["current_tokens"] = tokens
                state["context"]["last_check_timestamp"] = datetime.now(UTC).isoformat()
                # Ensure max_tokens is set
                if "max_tokens" not in state["context"]:
                    state["context"]["max_tokens"] = self.DEFAULT_MAX_TOKENS

                # Phase 1 CRITICAL fix: Refresh context_cache to reflect new token count
                # Without this, check_commit() continues blocking for up to 5 minutes after delegation
                # Note: Cannot use WorkflowGate._refresh_context_cache - DelegationRules is separate class
                import subprocess
                import time

                try:
                    git_index_hash = subprocess.check_output(
                        ["git", "write-tree"],
                        cwd=PROJECT_ROOT,
                        text=True,
                        stderr=subprocess.DEVNULL,
                    ).strip()
                except subprocess.CalledProcessError:
                    git_index_hash = "unknown"

                state["context_cache"] = {
                    "tokens": tokens,
                    "timestamp": time.time(),
                    "git_index_hash": git_index_hash,
                }

                # Warn if exceeding max
                if tokens > state["context"]["max_tokens"]:
                    print(
                        f"⚠️  Warning: Token usage ({tokens}) exceeds max ({state['context']['max_tokens']})"
                    )
                    print("   Consider resetting context or delegating to subagent")

            try:
                state = self._locked_modify_state(modifier)
                return self.get_context_snapshot(state)
            except Exception as e:
                print(f"⚠️  Warning: Could not update state: {e}")
                print("   Context not recorded")
                return self.get_context_snapshot({})

        # Fallback to unlocked (for backward compatibility / testing)
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
        state["context"]["last_check_timestamp"] = datetime.now(UTC).isoformat()
        # Ensure max_tokens is set
        if "max_tokens" not in state["context"]:
            state["context"]["max_tokens"] = self.DEFAULT_MAX_TOKENS

        # Phase 1 CRITICAL fix: Refresh context_cache to reflect new token count
        # Note: Cannot use WorkflowGate._refresh_context_cache - DelegationRules is separate class
        import subprocess
        import time

        try:
            git_index_hash = subprocess.check_output(
                ["git", "write-tree"], cwd=PROJECT_ROOT, text=True, stderr=subprocess.DEVNULL
            ).strip()
        except subprocess.CalledProcessError:
            git_index_hash = "unknown"

        state["context_cache"] = {
            "tokens": tokens,
            "timestamp": time.time(),
            "git_index_hash": git_index_hash,
        }

        # Warn if exceeding max
        if tokens > state["context"]["max_tokens"]:
            print(
                f"⚠️  Warning: Token usage ({tokens}) exceeds max ({state['context']['max_tokens']})"
            )
            print("   Consider resetting context or delegating to subagent")

        # Save state
        try:
            self._save_state(state)
        except Exception as e:
            print(f"⚠️  Warning: Could not save state: {e}")
            print("   State changes not persisted")

        return self.get_context_snapshot(state)

    def should_delegate_context(self, snapshot: dict | None = None) -> tuple[bool, str, float]:
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

    def suggest_delegation(self, snapshot: dict | None = None, operation: str | None = None) -> str:
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
        lines.append(
            f"{status_emoji} Usage: {usage_pct:.1f}% ({snapshot['current_tokens']:,} / {snapshot['max_tokens']:,} tokens)"
        )
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

        FIX-9 (Gemini): Use locked_modify_state if available to prevent race conditions.
        """
        # FIX-9: Use locked operation if available (same pattern as record_context)
        if self._locked_modify_state:
            delegation_record = {
                "timestamp": datetime.now(UTC).isoformat(),
                "task_description": task_description,
                "context_before_delegation": 0,  # Will be set in modifier
            }

            def modifier(state: dict) -> None:
                # Initialize delegations list
                if "subagent_delegations" not in state:
                    state["subagent_delegations"] = []

                # Capture context before delegation
                delegation_record["context_before_delegation"] = state.get("context", {}).get(
                    "current_tokens", 0
                )
                state["subagent_delegations"].append(delegation_record)

                # Reset context
                if "context" not in state:
                    state["context"] = {}
                state["context"]["current_tokens"] = 0
                state["context"]["last_delegation_timestamp"] = delegation_record["timestamp"]

                # Phase 1 CRITICAL fix: Clear context_cache after delegation
                # Without this, check_commit() continues blocking for up to 5 minutes
                # Note: Cannot use WorkflowGate._refresh_context_cache - DelegationRules is separate class
                import subprocess
                import time

                try:
                    git_index_hash = subprocess.check_output(
                        ["git", "write-tree"],
                        cwd=PROJECT_ROOT,
                        text=True,
                        stderr=subprocess.DEVNULL,
                    ).strip()
                except subprocess.CalledProcessError:
                    git_index_hash = "unknown"

                state["context_cache"] = {
                    "tokens": 0,
                    "timestamp": time.time(),
                    "git_index_hash": git_index_hash,
                }

            try:
                state = self._locked_modify_state(modifier)
                return {
                    "count": len(state["subagent_delegations"]),
                    "reset_tokens": 0,
                    "timestamp": delegation_record["timestamp"],
                    "task_description": task_description,
                }
            except Exception as e:
                print(f"⚠️  Warning: Could not update state: {e}")
                return {"count": 0, "error": str(e)}

        # Fallback to unlocked (for backward compatibility / testing)
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
            "timestamp": datetime.now(UTC).isoformat(),
            "task_description": task_description,
            "context_before_delegation": state.get("context", {}).get("current_tokens", 0),
        }
        state["subagent_delegations"].append(delegation_record)

        # Reset context
        if "context" not in state:
            state["context"] = {}
        state["context"]["current_tokens"] = 0
        state["context"]["last_delegation_timestamp"] = delegation_record["timestamp"]

        # Phase 1 CRITICAL fix: Clear context_cache after delegation
        # Note: Cannot use WorkflowGate._refresh_context_cache - DelegationRules is separate class
        import subprocess
        import time

        try:
            git_index_hash = subprocess.check_output(
                ["git", "write-tree"], cwd=PROJECT_ROOT, text=True, stderr=subprocess.DEVNULL
            ).strip()
        except subprocess.CalledProcessError:
            git_index_hash = "unknown"

        state["context_cache"] = {
            "tokens": 0,
            "timestamp": time.time(),
            "git_index_hash": git_index_hash,
        }

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
            "task_description": task_description,
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
            "full_ci": """Task(
    subagent_type="general-purpose",
    description="Run full CI suite",
    prompt="Run 'make ci-local' and report any failures with file:line references"
)""",
            "deep_review": """Task(
    subagent_type="general-purpose",
    description="Deep codebase review",
    prompt="Perform comprehensive review of [files/modules] for safety, architecture, and quality issues"
)""",
            "multi_file_search": """Task(
    subagent_type="Explore",
    description="Multi-file search",
    prompt="Search codebase for [pattern] and summarize findings with file:line references",
    thoroughness="medium"
)""",
        }

        return templates.get(operation, "")

    def format_status(self, snapshot: dict, reason: str, heading: str = "Context Status") -> str:
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
        self, task_id: str, title: str, description: str, estimated_hours: float
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
        print("   See .claude/workflows/02-planning.md for review workflow.")
        print()
        print("💡 Next steps:")
        print("   1. Review task document for completeness")
        print("   2. Request review via: mcp__zen__clink with cli_name='gemini', role='planner'")
        print("   3. Address any issues found in review")
        print("   4. Once approved, use plan_subfeatures() if task >8h")
        print()

        return str(task_file.relative_to(self._project_root))

    def plan_subfeatures(self, task_id: str, components: list[dict]) -> list[str]:
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
            print("ℹ️  Task is simple (<4h), no subfeature split needed")
            print(f"   Total estimated: {total_hours}h")
            print("   Proceed with single-feature implementation")
            return []

        if total_hours >= 8 or len(components) >= 3:
            print("✅ Task is complex (≥8h or ≥3 components), splitting into subfeatures...")
            print(f"   Total estimated: {total_hours}h across {len(components)} components")
        else:
            print("⚠️  Task is moderate (4-8h), splitting recommended...")
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
        print("💡 Next steps:")
        print("   1. Create task documents for each subfeature using create_task_with_review()")
        print("   2. Start first subfeature with start_task_with_state()")
        print()

        return subfeatures

    def start_task_with_state(self, task_id: str, branch_name: str) -> None:
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
            text=True,
        )

        if result.returncode != 0:
            # Branch might already exist, try to check it out
            result = subprocess.run(
                ["git", "checkout", branch_name],
                cwd=self._project_root,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                print(f"❌ Failed to create/checkout branch: {result.stderr}")
                raise RuntimeError(
                    f"Failed to create/checkout branch {branch_name}: {result.stderr.strip()}"
                )

        # Initialize task state (update_task_state.py integration)
        task_doc = self._load_task_doc(task_id)
        components = self._extract_components(task_doc)

        # Extract task title from document (first # heading)
        task_title = "Unknown Task"
        for line in task_doc.split("\n"):
            if line.strip().startswith("# "):
                task_title = line.strip()[2:].strip()  # Remove "# " prefix
                # Remove task ID prefix if present (e.g., "# P1T14: Title" -> "Title")
                # Use regex to remove only the task ID prefix, not colons within title
                task_title = re.sub(r"^[A-Z0-9\-_]+:\s*", "", task_title)
                break

        update_task_state_script = self._project_root / "scripts" / "update_task_state.py"
        if update_task_state_script.exists():
            # Calculate task file path
            task_file = self._tasks_dir / f"{task_id}_TASK.md"

            # Fail loudly if task state update fails (prevents inconsistent state)
            # CRITICAL: Task tracking and workflow must stay synchronized
            subprocess.run(
                [
                    sys.executable,
                    str(update_task_state_script),
                    "start",
                    "--task",
                    task_id,
                    "--title",
                    task_title,
                    "--branch",
                    branch_name,
                    "--task-file",
                    str(task_file),
                    "--components",
                    str(len(components)),
                ],
                cwd=self._project_root,
                capture_output=True,
                text=True,
                check=True,
            )

        # Initialize workflow state (delegate to WorkflowGate for atomic writes)
        self._workflow_gate.reset()  # Clean slate (starts with step="plan")

        # Phase 1: Populate planning metadata
        # MEDIUM fix: Use public API instead of internal _locked_state() (Gemini review)
        with self._workflow_gate.locked_state_context() as state:
            # Calculate task file path
            task_file_path = self._tasks_dir / f"{task_id}_TASK.md"
            state["task_file"] = str(task_file_path)
            state["components"] = components
            state["first_commit_made"] = False  # Reset for new task

        # Set first component (delegate to WorkflowGate for atomic writes)
        if components:
            self._workflow_gate.set_component(components[0]["name"])

        print(f"✅ Task {task_id} started")
        print(f"   Branch: {branch_name}")
        print(f"   Components: {len(components)}")
        print(f"   Current: {components[0]['name'] if components else 'N/A'}")
        print("   Step: plan (complete planning before first commit)")

    # ========== Private helper methods ==========

    def _generate_task_doc(
        self, task_id: str, title: str, description: str, estimated_hours: float
    ) -> Path:
        """
        Generate task document from template.

        Creates a new task document in docs/TASKS/ with standardized format.
        Loads template from 00-PLANNING_WORKFLOW_TEMPLATE.md for maintainability.
        Falls back to embedded template if file not found (e.g., in test environments).

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

        # Load template from file if available, else use embedded fallback
        template_file = self._tasks_dir / "00-PLANNING_WORKFLOW_TEMPLATE.md"
        if template_file.exists():
            with open(template_file) as f:
                template_content = f.read()
        else:
            # Fallback template for test environments or when file missing
            template_content = """# {task_id}: {title}

**Status:** DRAFT
**Estimated Hours:** {estimated_hours}h
**Created:** {created_date}

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
Request task creation review via .claude/workflows/02-planning.md before starting work.
"""

        # Replace placeholders
        content = template_content.format(
            task_id=task_id,
            title=title,
            description=description,
            estimated_hours=estimated_hours,
            created_date=datetime.now(UTC).strftime("%Y-%m-%d"),
        )

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

        with open(task_file) as f:
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
                # Format: - Component name (Xh) or - Component name (X hours)
                content = line.strip()[1:].strip()  # Remove leading "- "

                # Extract hours using more robust regex pattern
                # Matches: (2h), (2 h), (2h ), (2 hours), (2.5h), etc.
                hours_pattern = r"\((\d+(?:\.\d+)?)\s*(?:h|hours?)\s*\)"
                match = re.search(hours_pattern, content, re.IGNORECASE)

                hours = 0.0
                if match:
                    hours_str = match.group(1)
                    try:
                        hours = float(hours_str)
                        # Remove hours from name
                        name = content[: match.start()].strip()
                    except ValueError:
                        print(
                            f"⚠️  Warning: Failed to parse hours from '{content}' - defaulting to 0h"
                        )
                        name = content
                        hours = 0.0
                else:
                    # No hours found - check if it looks like it might have hours
                    if "(" in content and ("h" in content.lower() or "hour" in content.lower()):
                        print(
                            f"⚠️  Warning: Line appears to contain hours but couldn't parse: '{content}'"
                        )
                    name = content

                components.append({"name": name, "hours": hours})

        return components


class UnifiedReviewSystem:
    """
    Consolidated review system with context-aware rigor.

    Merges quick/deep reviews with multi-iteration pre-PR validation.
    Implements conservative override policy for quality gates.

    Component 4 of P1T13-F4: Workflow Intelligence & Context Efficiency
    Author: Claude Code
    Date: 2025-11-08
    """

    def __init__(self, workflow_gate: "WorkflowGate | None" = None, state_file: Path = STATE_FILE):
        """
        Initialize unified review system.

        Args:
            workflow_gate: WorkflowGate instance for centralized state management
                (default: None, creates own state management - deprecated for new code)
            state_file: Path to workflow state JSON file (only used if workflow_gate is None)

        Note:
            Dependency injection pattern prevents state race conditions.
            When workflow_gate is provided, all state operations use atomic writes.
        """
        self._workflow_gate = workflow_gate
        self._state_file = state_file
        self._project_root = Path(__file__).parent.parent

    def _load_state(self) -> dict:
        """
        Load workflow state from JSON file.

        Delegates to WorkflowGate if available for atomic operations.
        Falls back to direct file I/O only when workflow_gate is None.
        """
        if self._workflow_gate:
            return self._workflow_gate.load_state()

        # Legacy fallback for tests without workflow_gate
        if not self._state_file.exists():
            return {}

        try:
            with open(self._state_file) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"⚠️  Warning: Failed to parse review state file: {e}")
            print("   Initializing fresh state...")
            return {}

    def _save_state(self, state: dict) -> None:
        """
        Save workflow state to JSON file.

        Delegates to WorkflowGate if available for atomic writes.
        Falls back to direct file I/O only when workflow_gate is None.
        """
        if self._workflow_gate:
            self._workflow_gate.save_state(state)
            return

        # Legacy fallback for tests without workflow_gate
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._state_file, "w") as f:
            json.dump(state, f, indent=2)

    def request_review(
        self, scope: str = "commit", iteration: int = 1, override_justification: str | None = None
    ) -> dict:
        """
        Request unified review (gemini codereviewer → codex codereviewer).

        Args:
            scope: "commit" (lightweight) or "pr" (comprehensive + multi-iteration)
            iteration: Iteration number for PR reviews (1, 2, 3...)
            override_justification: Justification for overriding LOW severity issues

        Returns:
            Review result dict with continuation_id and status

        Example:
            >>> reviewer = UnifiedReviewSystem()
            >>> result = reviewer.request_review(scope="commit")
            >>> print(result["status"])  # "APPROVED" or "NEEDS_REVISION"
        """
        if scope == "commit":
            return self._commit_review()
        elif scope == "pr":
            return self._pr_review(iteration, override_justification)
        else:
            raise ValueError(f"Invalid scope: {scope}. Must be 'commit' or 'pr'.")

    def _commit_review(self) -> dict:
        """
        Lightweight commit review (replaces quick review).

        Focus:
        - Trading safety (circuit breakers, idempotency)
        - Critical bugs
        - Code quality (type safety, error handling)

        Speed: 2-3 minutes (gemini 1-2min, codex 30-60sec)

        Returns:
            dict with keys: scope, continuation_id, status, issues
        """
        print("🔍 Requesting commit review (gemini → codex)...")
        print("   Focus: Trading safety, critical bugs, code quality")
        print("   Duration: ~2-3 minutes")
        print()
        print("💡 Follow workflow: .claude/workflows/03-reviews.md")
        print("   Use: mcp__zen__clink with cli_name='gemini', role='codereviewer'")
        print(
            "   Then: mcp__zen__clink with cli_name='codex', role='codereviewer' (reuse continuation_id)"
        )
        print()
        print("   After review, record approval:")
        print("     ./scripts/workflow_gate.py record-review <continuation_id> <status>")
        print()

        # Return placeholder - actual review happens via clink
        return {
            "scope": "commit",
            "continuation_id": None,  # Set by user after review
            "status": "PENDING",
            "issues": [],
        }

    def _pr_review(self, iteration: int, override_justification: str | None = None) -> dict:
        """
        Comprehensive PR review with multi-iteration loop.

        Iteration 1:
        - Architecture analysis
        - Integration concerns
        - Test coverage
        - All commit-level checks (safety, quality)

        Iteration 2+ (if issues found):
        - INDEPENDENT review (fresh context, no memory of iteration 1)
        - Verify fixes from previous iteration
        - Look for NEW issues introduced by fixes
        - Continue until BOTH reviewers find NO issues

        Speed: 3-5 minutes per iteration
        Max iterations: 3 (escalate to user if still failing)

        Args:
            iteration: Current iteration number (1-3)
            override_justification: Justification for overriding LOW severity issues

        Returns:
            dict with keys: scope, iteration, continuation_id, status, issues, override
        """
        state = self._load_state()
        review_state = state.setdefault("unified_review", {})
        review_history = review_state.setdefault("history", [])

        print(f"🔍 Requesting PR review - Iteration {iteration} (gemini → codex)...")
        print("   Focus: Architecture, integration, coverage, safety, quality")
        print("   Duration: ~3-5 minutes per iteration")
        print()

        if iteration > 1:
            print(f"   ⚠️  INDEPENDENT REVIEW (no memory of iteration {iteration-1})")
            print("      Looking for: (1) Verified fixes, (2) New issues from fixes")
            print()

        print("💡 Follow workflow: .claude/workflows/03-reviews.md")
        print("   Use: mcp__zen__clink with cli_name='gemini', role='codereviewer'")
        print("   Then: mcp__zen__clink with cli_name='codex', role='codereviewer'")
        print("   ⚠️  CRITICAL: Do NOT reuse continuation_id from previous iteration")
        print("      Each iteration must be independent for unbiased review")
        print()

        # Check for override conditions
        if override_justification and iteration >= 3:
            return self._handle_review_override(state, iteration, override_justification)

        if iteration >= 3:
            print()
            print("⚠️  Max iterations reached (3)")
            print("   Options:")
            print("   1. Fix remaining issues and continue")
            print("   2. Override LOW issues with --override --justification 'reason'")
            print("      (CRITICAL/HIGH/MEDIUM cannot be overridden)")
            print()

        # Create pending history entry for this iteration
        # This enables override workflow by providing history to check
        from datetime import datetime

        pending_entry = {
            "iteration": iteration,
            "scope": "pr",
            "status": "PENDING",
            "timestamp": datetime.now(UTC).isoformat(),
            "issues": [],  # Will be populated if user provides details
            "continuation_id": None,  # Will be set by record_review
        }

        # Append to history (record_review will update the latest entry)
        review_history.append(pending_entry)
        self._save_state(state)

        # Return placeholder - actual review happens via clink
        return {
            "scope": "pr",
            "iteration": iteration,
            "continuation_id": None,  # Set by user after review
            "status": "PENDING",
            "issues": [],
            "max_iterations": 3,
        }

    def _is_override_allowed(self, state: dict) -> dict:
        """
        Check if review override is permissible.

        Determines whether override is allowed based on severity of
        outstanding issues. Separates decision logic from side effects.

        Conservative policy (from edge case Q2):
        - Block CRITICAL/HIGH/MEDIUM entirely
        - Allow LOW only with justification

        Args:
            state: Current workflow state

        Returns:
            Dict with:
            - allowed: bool - Whether override is permitted
            - error: str | None - Error message if not allowed
            - blocked_issues: list - CRITICAL/HIGH/MEDIUM issues
            - low_issues: list - LOW severity issues
            - iteration: int - Current iteration number
        """
        review_state = state.get("unified_review", {})
        review_history = review_state.get("history", [])

        if not review_history:
            return {
                "allowed": False,
                "error": "No review history found. Cannot override without prior review.",
                "blocked_issues": [],
                "low_issues": [],
                "iteration": 0,
            }

        # Get latest review issues
        latest_review = review_history[-1]
        issues = latest_review.get("issues", [])
        iteration = latest_review.get("iteration", 0)

        # Categorize issues by severity
        blocked_issues = []
        low_issues = []

        for issue in issues:
            severity = issue.get("severity", "UNKNOWN")
            if severity in {"CRITICAL", "HIGH", "MEDIUM"}:
                blocked_issues.append(issue)
            elif severity == "LOW":
                low_issues.append(issue)

        # Cannot override if any CRITICAL/HIGH/MEDIUM issues exist
        if blocked_issues:
            return {
                "allowed": False,
                "error": "Cannot override CRITICAL/HIGH/MEDIUM issues",
                "blocked_issues": blocked_issues,
                "low_issues": low_issues,
                "iteration": iteration,
            }

        # Allow override if only LOW issues (or no issues)
        return {
            "allowed": True,
            "error": None,
            "blocked_issues": [],
            "low_issues": low_issues,
            "iteration": iteration,
        }

    def _execute_override(
        self, state: dict, justification: str, low_issues: list, iteration: int
    ) -> dict:
        """
        Execute review override by performing side effects.

        Posts comment to PR and persists override record to state.
        Separated from decision logic for better testability.

        Args:
            state: Current workflow state
            justification: User-provided justification for override
            low_issues: List of LOW severity issues being overridden
            iteration: Current iteration number

        Returns:
            Override result dict with status and metadata
        """
        review_state = state.get("unified_review", {})

        if low_issues:
            print(f"⚠️ Overriding {len(low_issues)} LOW severity issue(s):")
            for issue in low_issues:
                print(f"   - {issue['summary']}")
            print()
            print("💡 RECOMMENDED: Fix LOW issues if straightforward before override")
            print()

            # Log to PR via gh pr comment
            try:
                result = subprocess.run(
                    [
                        "gh",
                        "pr",
                        "comment",
                        "--body",
                        f"⚠️ REVIEW OVERRIDE (LOW severity only):\n{justification}\n\nDeferred LOW issues: {len(low_issues)}",
                    ],
                    check=False,  # Degrade gracefully if gh unavailable
                    capture_output=True,
                    text=True,
                    cwd=self._project_root,
                )
                if result.returncode == 0:
                    print("✅ Override logged to PR comment")
                else:
                    print(f"⚠️  Failed to post PR comment: {result.stderr.strip()}")
                    print("   (Override still recorded locally)")
            except FileNotFoundError:
                print("⚠️  gh CLI not found - PR comment not posted")
                print("   (Override still recorded locally)")
            except Exception as e:
                print(f"⚠️  Failed to post PR comment: {e}")
                print("   (Override still recorded locally)")

            # Persist override in state
            review_state["override"] = {
                "justification": justification,
                "timestamp": datetime.now(UTC).isoformat(),
                "iteration": iteration,
                "low_issues_count": len(low_issues),
                "policy": "block_critical_high_medium_allow_low",
            }
            self._save_state(state)

            print("✅ Override recorded. You may proceed with commit/PR.")
            return {
                "status": "OVERRIDE_APPROVED",
                "low_issues": low_issues,
                "override": review_state["override"],
            }

        # No issues at all
        return {"status": "APPROVED", "issues": []}

    def _handle_review_override(self, state: dict, iteration: int, justification: str) -> dict:
        """
        Handle review override for LOW severity issues after max iterations.

        Orchestrates override by checking permission and executing if allowed.
        Delegates to _is_override_allowed and _execute_override for separation
        of concerns.

        Conservative policy (from edge case Q2):
        - Block CRITICAL/HIGH/MEDIUM entirely
        - Allow LOW only with justification
        - Log to PR via gh pr comment

        Args:
            state: Current workflow state
            iteration: Current iteration number
            justification: User-provided justification

        Returns:
            Override result dict
        """
        # Check if override is allowed
        check_result = self._is_override_allowed(state)

        if not check_result["allowed"]:
            # Override blocked - print diagnostics and return error
            if check_result["blocked_issues"]:
                print(
                    f"❌ Cannot override {len(check_result['blocked_issues'])} CRITICAL/HIGH/MEDIUM issue(s):"
                )
                for issue in check_result["blocked_issues"]:
                    print(f"   - [{issue['severity']}] {issue['summary']}")
                print()
                print(
                    "💡 FIX these issues before proceeding. Override only allowed for LOW severity."
                )

            return {
                "error": check_result["error"],
                "blocked_issues": check_result["blocked_issues"],
            }

        # Override allowed - execute side effects
        return self._execute_override(
            state, justification, check_result["low_issues"], check_result["iteration"]
        )


class DebugRescue:
    """
    Automated detection and escalation of stuck debug loops.

    Detects when AI is stuck in repetitive test failures and escalates
    to clink codex for systematic debugging assistance.

    Component 5 of P1T13-F4: Workflow Intelligence & Context Efficiency
    Author: Claude Code
    Date: 2025-11-08
    """

    # Detection thresholds (class constants for configurability)
    MAX_ATTEMPTS_SAME_TEST = 3
    LOOP_DETECTION_WINDOW = 10
    CYCLING_MIN_ATTEMPTS = 6
    CYCLING_MAX_UNIQUE_ERRORS = 3
    TIME_LIMIT_MIN_ATTEMPTS = 5
    TIME_LIMIT_MINUTES = 30
    HISTORY_MAX_SIZE = 50

    def __init__(self, workflow_gate: "WorkflowGate | None" = None, state_file: Path = STATE_FILE):
        """
        Initialize debug rescue system.

        Args:
            workflow_gate: WorkflowGate instance for centralized state management
                (default: None, creates own state management - deprecated for new code)
            state_file: Path to workflow state JSON file (only used if workflow_gate is None)

        Note:
            Dependency injection pattern prevents state race conditions.
            When workflow_gate is provided, all state operations use atomic writes.
        """
        self._workflow_gate = workflow_gate
        self._state_file = state_file
        self._project_root = Path(__file__).parent.parent

    def _load_state(self) -> dict:
        """
        Load workflow state from JSON file.

        Delegates to WorkflowGate if available for atomic operations.
        Falls back to direct file I/O only when workflow_gate is None.

        Returns:
            State dict, or empty dict if file doesn't exist or is corrupted
        """
        if self._workflow_gate:
            return self._workflow_gate.load_state()

        # Legacy fallback for tests without workflow_gate
        if not self._state_file.exists():
            return {}

        try:
            with open(self._state_file) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            # Corrupted or inaccessible state file - return empty dict
            print(f"⚠️  Warning: Could not load state file: {e}")
            print("   Using empty state")
            return {}

    def _save_state(self, state: dict) -> None:
        """
        Save workflow state to JSON file.

        Delegates to WorkflowGate if available for atomic writes.
        Falls back to direct file I/O only when workflow_gate is None.

        Args:
            state: State dict to save
        """
        if self._workflow_gate:
            self._workflow_gate.save_state(state)
            return

        # Legacy fallback for tests without workflow_gate
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._state_file, "w") as f:
                json.dump(state, f, indent=2)
        except OSError as e:
            print(f"⚠️  Warning: Could not save state file: {e}")
            # Continue execution - state persistence is non-critical

    def record_test_attempt(self, test_file: str, status: str, error_signature: str) -> None:
        """
        Record test execution attempt for loop detection.

        Args:
            test_file: Test file path
            status: Test outcome ("passed" or "failed")
            error_signature: Hash of error message (for detecting repeats)

        Example:
            >>> rescue = DebugRescue()
            >>> rescue.record_test_attempt(
            ...     "tests/test_foo.py",
            ...     "failed",
            ...     "abc123"
            ... )
        """
        state = self._load_state()
        debug_state = state.setdefault("debug_rescue", {})
        attempt_history = debug_state.setdefault("attempt_history", [])

        # Add new attempt
        attempt_history.append(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "test_file": test_file,
                "status": status,
                "error_signature": error_signature,
            }
        )

        # Prune old history (keep last HISTORY_MAX_SIZE)
        if len(attempt_history) > self.HISTORY_MAX_SIZE:
            debug_state["attempt_history"] = attempt_history[-self.HISTORY_MAX_SIZE :]

        self._save_state(state)

    def is_stuck_in_loop(self) -> tuple[bool, str]:
        """
        Detect if AI is stuck in debug loop.

        Indicators:
        1. Same test failing MAX_ATTEMPTS_SAME_TEST+ times in last LOOP_DETECTION_WINDOW attempts
        2. Error signature cycling (≤CYCLING_MAX_UNIQUE_ERRORS patterns over CYCLING_MIN_ATTEMPTS+)
        3. >TIME_LIMIT_MINUTES spent in debug attempts (TIME_LIMIT_MIN_ATTEMPTS+ attempts)

        Returns:
            (is_stuck: bool, reason: str)

        Example:
            >>> rescue = DebugRescue()
            >>> is_stuck, reason = rescue.is_stuck_in_loop()
            >>> if is_stuck:
            ...     print(f"Stuck: {reason}")
        """
        state = self._load_state()
        debug_state = state.get("debug_rescue", {})
        attempt_history = debug_state.get("attempt_history", [])

        if len(attempt_history) < self.MAX_ATTEMPTS_SAME_TEST:
            return (False, "Not enough attempts to detect loop")

        recent = attempt_history[-self.LOOP_DETECTION_WINDOW :]

        # Check 1: Same test failing repeatedly
        test_files = [a["test_file"] for a in recent if a["status"] == "failed"]
        if len(test_files) >= self.MAX_ATTEMPTS_SAME_TEST:
            most_common = max(set(test_files), key=test_files.count)
            fail_count = test_files.count(most_common)
            if fail_count >= self.MAX_ATTEMPTS_SAME_TEST:
                return (
                    True,
                    f"Test '{most_common}' failed {fail_count} times in last {len(recent)} attempts",
                )

        # Check 2: Error signature cycling
        signatures = [a["error_signature"] for a in recent]
        unique_sigs = set(signatures)
        if (
            len(unique_sigs) <= self.CYCLING_MAX_UNIQUE_ERRORS
            and len(signatures) >= self.CYCLING_MIN_ATTEMPTS
        ):
            # Limited unique errors cycling
            return (True, f"Cycling between {len(unique_sigs)} error patterns: {unique_sigs}")

        # Check 3: Time spent (if timestamps available)
        if len(recent) >= self.TIME_LIMIT_MIN_ATTEMPTS:
            try:
                first_ts = datetime.fromisoformat(recent[0]["timestamp"])
                last_ts = datetime.fromisoformat(recent[-1]["timestamp"])
                duration = (last_ts - first_ts).total_seconds() / 60

                if duration > self.TIME_LIMIT_MINUTES:
                    return (
                        True,
                        f"Spent {duration:.1f} minutes in debug attempts without progress",
                    )
            except (ValueError, KeyError) as e:
                # Timestamp parsing failed - warn but continue with other checks
                print(f"⚠️  Warning: Time-based loop detection failed (malformed timestamp): {e}")
                print("   Continuing with other loop detection checks...")

        return (False, "No loop detected")

    def _get_recent_commits(self, max_commits: int = 5) -> str:
        """
        Get recent commits for context.

        Args:
            max_commits: Number of recent commits to fetch

        Returns:
            Formatted git log output
        """
        try:
            result = subprocess.run(
                ["git", "log", f"-{max_commits}", "--oneline"],
                cwd=self._project_root,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            return result.stdout.strip()
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            FileNotFoundError,
            Exception,
        ) as e:
            # Catch all exceptions and log a warning for graceful degradation
            print(f"⚠️  Warning: Could not get recent commits: {e}")
            return "(git log unavailable)"

    def request_debug_rescue(self, test_file: str | None = None) -> dict:
        """
        Request clink codex debugging assistance.

        Provides codex with:
        1. Test file and recent failure history
        2. Recent fix attempts (from git log)
        3. Request systematic debugging approach

        Args:
            test_file: Specific test file to debug (or None for auto-detect)

        Returns:
            dict with rescue guidance and continuation_id

        Example:
            >>> rescue = DebugRescue()
            >>> result = rescue.request_debug_rescue("tests/test_foo.py")
            >>> print(result["guidance"])
        """
        state = self._load_state()
        debug_state = state.get("debug_rescue", {})
        attempt_history = debug_state.get("attempt_history", [])

        # Auto-detect most problematic test if not specified
        if not test_file and attempt_history:
            recent = attempt_history[-self.LOOP_DETECTION_WINDOW :]
            failed_tests = [a["test_file"] for a in recent if a["status"] == "failed"]
            if failed_tests:
                test_file = max(set(failed_tests), key=failed_tests.count)

        if not test_file:
            return {"error": "No test file specified and no recent failures found"}

        # Get recent errors for this test
        recent_errors = [
            a["error_signature"]
            for a in attempt_history
            if a["test_file"] == test_file and a["status"] == "failed"
        ]

        print("🆘 DEBUG RESCUE TRIGGERED")
        print(f"   Test: {test_file}")
        print(
            f"   Recent failures: {len([a for a in attempt_history if a['test_file'] == test_file and a['status'] == 'failed'])}"
        )
        print()
        print("📞 Requesting clink codex debugging assistance...")
        print()

        # Build rescue prompt
        rescue_prompt = f"""
DEBUG RESCUE REQUEST

I'm stuck in a debug loop on this test:
- Test file: {test_file}
- Failed attempts: {len([a for a in attempt_history if a['test_file'] == test_file and a['status'] == 'failed'])}
- Recent error signatures: {recent_errors[:3]}

Recent fix attempts (git log):
{self._get_recent_commits()}

Please help with systematic debugging:
1. Analyze the error pattern (is it cycling?)
2. Identify root cause (not just symptoms)
3. Suggest focused debugging approach
4. Recommend specific diagnostic steps

I need a fresh perspective to break out of this loop.
"""

        print("💡 Follow workflow: Use mcp__zen__clink with:")
        print("   - cli_name='codex'")
        print("   - role='default'")
        print(f"   - prompt='{rescue_prompt.strip()[:100]}...'")
        print()
        print("   Codex will provide:")
        print("   - Error pattern analysis")
        print("   - Root cause identification")
        print("   - Systematic debugging plan")
        print("   - Specific diagnostic steps")
        print()

        # Return guidance (actual rescue happens via clink)
        return {
            "test_file": test_file,
            "failed_attempts": len(
                [
                    a
                    for a in attempt_history
                    if a["test_file"] == test_file and a["status"] == "failed"
                ]
            ),
            "recent_errors": recent_errors[:3],
            "rescue_prompt": rescue_prompt.strip(),
            "status": "RESCUE_NEEDED",
        }


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
    set_component_parser = subparsers.add_parser("set-component", help="Set current component name")
    set_component_parser.add_argument("name", help="Component name")

    # Advance workflow
    advance_parser = subparsers.add_parser("advance", help="Advance to next step")
    advance_parser.add_argument(
        "next_step",
        choices=["implement", "test", "review"],
        help="Next workflow step (plan→implement, implement→test, test→review, review→implement for rework)",
    )

    # Record review
    record_review_parser = subparsers.add_parser(
        "record-review", help="Record zen-mcp review result"
    )
    record_review_parser.add_argument("continuation_id", help="Zen-MCP continuation ID")
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
    record_context_parser.add_argument("tokens", type=int, help="Current token count")

    subparsers.add_parser(
        "suggest-delegation", help="Get delegation recommendations if thresholds exceeded"
    )

    record_delegation_parser = subparsers.add_parser(
        "record-delegation", help="Record subagent delegation"
    )
    record_delegation_parser.add_argument("task_description", help="Description of delegated task")

    # Component 4: Unified Review System
    request_review_parser = subparsers.add_parser(
        "request-review", help="Request unified review (commit or PR)"
    )
    request_review_parser.add_argument(
        "scope",
        choices=["commit", "pr"],
        help="Review scope: commit (lightweight) or pr (comprehensive)",
    )
    request_review_parser.add_argument(
        "--iteration", type=int, default=1, help="PR review iteration number (1-3)"
    )
    request_review_parser.add_argument(
        "--override",
        action="store_true",
        help="Override LOW severity issues (requires --justification)",
    )
    request_review_parser.add_argument(
        "--justification", type=str, help="Justification for overriding LOW severity issues"
    )

    # Component 5: Debug Rescue
    debug_rescue_parser = subparsers.add_parser(
        "debug-rescue", help="Request debug rescue for stuck test loops"
    )
    debug_rescue_parser.add_argument(
        "test_file", nargs="?", help="Test file to debug (optional, auto-detects if omitted)"
    )

    # Component 2: SmartTestRunner
    run_ci_parser = subparsers.add_parser(
        "run-ci", help="Run smart CI tests (targeted for commits, full for PRs)"
    )
    run_ci_parser.add_argument(
        "scope",
        choices=["commit", "pr"],
        help="CI scope: commit (smart selection) or pr (full suite)",
    )

    # Component 4: PlanningWorkflow
    create_task_parser = subparsers.add_parser(
        "create-task", help="Create new task with zen-mcp review"
    )
    create_task_parser.add_argument("--id", required=True, help="Task ID (e.g., P1T14)")
    create_task_parser.add_argument("--title", required=True, help="Task title")
    create_task_parser.add_argument("--description", required=True, help="Task description")
    create_task_parser.add_argument("--hours", type=float, required=True, help="Estimated hours")

    start_task_parser = subparsers.add_parser("start-task", help="Start task and update state")
    start_task_parser.add_argument("task_id", help="Task ID to start")
    start_task_parser.add_argument("branch_name", help="Git branch name for task")

    # Phase 1: Planning discipline commands
    record_analysis_parser = subparsers.add_parser(
        "record-analysis-complete", help="Mark pre-implementation analysis as complete"
    )
    record_analysis_parser.add_argument(
        "--checklist-file", help="Path to analysis checklist file (optional, for validation)"
    )

    set_components_parser = subparsers.add_parser(
        "set-components", help="Define component breakdown for task"
    )
    set_components_parser.add_argument("components", nargs="+", help="Component names (must be ≥2)")

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
            locked_modify_state=gate.locked_modify_state,  # FIX-7
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
            should_delegate, reason, _ = delegation_rules.should_delegate_context(snapshot)
            status = delegation_rules.format_status(snapshot, reason)
            print(status)
        elif args.command == "record-context":
            result = delegation_rules.record_context(args.tokens)
            if result.get("error"):
                print(f"Error: {result['error']}")
            else:
                print(
                    f"✅ Context recorded: {result['current_tokens']:,} tokens ({result['usage_pct']:.1f}%)"
                )
        elif args.command == "suggest-delegation":
            suggestion = delegation_rules.suggest_delegation()
            print(suggestion)
        elif args.command == "record-delegation":
            result = delegation_rules.record_delegation(args.task_description)
            if result.get("error"):
                print(f"Error: {result['error']}")
            else:
                print(f"✅ Delegation recorded: {result['task_description']}")
        elif args.command == "request-review":
            # Instantiate UnifiedReviewSystem with central WorkflowGate
            # (prevents state race conditions - see iteration 4 HIGH severity fix)
            review_system = UnifiedReviewSystem(workflow_gate=gate)

            # Prepare arguments
            override_justification = None
            if args.override:
                if not args.justification:
                    print("❌ Error: --override requires --justification")
                    return 1
                if args.iteration < 3:
                    print(
                        "❌ Error: --override can only be used with --iteration 3 (final iteration)"
                    )
                    print(
                        "   Reason: Overrides only apply after multiple independent review attempts"
                    )
                    return 1
                override_justification = args.justification

            # Request review
            result = review_system.request_review(
                scope=args.scope,
                iteration=args.iteration,
                override_justification=override_justification,
            )

            # Handle result
            if result.get("error"):
                print(f"❌ Error: {result['error']}")
                return 1

        elif args.command == "debug-rescue":
            # Instantiate DebugRescue with central WorkflowGate
            # (prevents state race conditions - see iteration 4 HIGH severity fix)
            debug_rescue = DebugRescue(workflow_gate=gate)

            # Request rescue
            result = debug_rescue.request_debug_rescue(test_file=args.test_file)

            # Handle result
            if result.get("error"):
                print(f"❌ Error: {result['error']}")
                return 1

            # Success - guidance printed by request_debug_rescue()
            return 0

        elif args.command == "run-ci":
            # Instantiate SmartTestRunner (no arguments)
            smart_runner = SmartTestRunner()

            # Get test command based on scope
            context = args.scope  # "commit" or "pr"
            command_list = smart_runner.get_test_command(context=context)

            # Execute command from project root (prevents path resolution issues)
            print(f"📋 Running {context} CI tests...")
            print(f"▶️  Command: {' '.join(command_list)}\n")

            try:
                # CRITICAL: Execute from PROJECT_ROOT so pytest/make can resolve paths correctly
                result = subprocess.run(command_list, cwd=PROJECT_ROOT)
            except FileNotFoundError:
                print(f"\n❌ Error: Command not found: '{command_list[0]}'")
                print(f"   Make sure {command_list[0]} is installed and in PATH")
                if command_list[0] in ("pytest", "poetry"):
                    print("   Install with: pip install poetry (then: poetry install)")
                elif command_list[0] == "make":
                    print(
                        "   Install with: brew install make (macOS) or apt-get install build-essential (Linux)"
                    )
                gate.record_ci(passed=False)
                return 1

            # Record CI result
            if result.returncode == 0:
                gate.record_ci(passed=True)
                print("\n✅ CI passed")
                return 0
            else:
                gate.record_ci(passed=False)
                print("\n❌ CI failed")
                return 1

        elif args.command == "create-task":
            # Instantiate PlanningWorkflow
            planning = PlanningWorkflow(workflow_gate=gate)

            # Create task with review
            task_file_path = planning.create_task_with_review(
                task_id=args.id,
                title=args.title,
                description=args.description,
                estimated_hours=args.hours,
            )

            print(f"✅ Task created: {args.id}")
            print(f"📄 Task file: {task_file_path}")
            if args.hours > 8:
                print("⚠️  Task >8h - consider splitting into subfeatures")
            return 0

        elif args.command == "start-task":
            # Instantiate PlanningWorkflow
            planning = PlanningWorkflow(workflow_gate=gate)

            # Start task with state integration
            planning.start_task_with_state(task_id=args.task_id, branch_name=args.branch_name)

            print(f"✅ Task started: {args.task_id}")
            print(f"📂 Branch: {args.branch_name}")
            return 0

        elif args.command == "record-analysis-complete":
            # Phase 1: Record analysis completion
            gate.record_analysis_complete(checklist_file=args.checklist_file)
            return 0

        elif args.command == "set-components":
            # Phase 1: Set component breakdown
            gate.set_components_list(components=args.components)
            return 0

        return 0

    except SystemExit as e:
        return e.code
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
