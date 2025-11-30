#!/usr/bin/env python3
"""
Core WorkflowGate class - Main workflow state management (V2 Schema).

Enforces the 6-step development workflow:
  plan → plan-review → implement → test → review → commit

This module provides:
- Atomic state file operations with file locking (fcntl)
- V2 nested state schema (component.step, reviews.gemini, ci.component_passed)
- Workflow step transitions with validation
- Review status tracking (gemini + codex dual reviews)
- CI status tracking
- Audit logging for continuation ID verification
- Emergency override with audit trail

Schema V2 Structure:
{
    "version": "2.0",
    "phase": "component" | "pr-review",
    "component": {
        "current": str,
        "step": "plan" | "plan-review" | "implement" | "test" | "review",
        "list": [],
        "total": int,
        "completed": int
    },
    "reviews": {
        "gemini": {"status": str, "continuation_id": str, "at": str},
        "codex": {"status": str, "continuation_id": str, "at": str}
    },
    "ci": {
        "component_passed": bool,
        "pr_ci_passed": bool
    },
    "git": {
        "branch": str,
        "base_branch": str,
        "commits": [],
        "pr_commits": []
    },
    ...
}
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from .constants import (
    PROJECT_ROOT,
    STATE_FILE,
    AUDIT_LOG_FILE,
    VALID_TRANSITIONS,
    STEP_DESCRIPTIONS,
    REVIEW_APPROVED,
    REVIEW_NEEDS_REVISION,
    REVIEW_NOT_REQUESTED,
    PLACEHOLDER_PATTERNS,
    DEFAULT_MAX_TOKENS,
)

StepType = Literal["plan", "plan-review", "implement", "test", "review"]


# =============================================================================
# Custom Exceptions (Gemini LOW fix: replace sys.exit with exceptions)
# =============================================================================

class WorkflowError(Exception):
    """Base exception for workflow errors."""
    pass


class WorkflowTransitionError(WorkflowError):
    """Raised when a workflow transition is not allowed."""
    pass


class WorkflowValidationError(WorkflowError):
    """Raised when validation fails (e.g., invalid reviewer, placeholder ID)."""
    pass


class WorkflowGateBlockedError(WorkflowError):
    """Raised when a workflow gate blocks an operation (e.g., commit blocked)."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


# =============================================================================
# Standalone Migration Functions (Gemini LOW fix: consolidate migration logic)
# =============================================================================

def _migrate_review_helper(v1_review: dict) -> dict:
    """Migrate V1 review structure to V2."""
    if not v1_review:
        return {}
    return {
        "status": v1_review.get("status", ""),
        "continuation_id": v1_review.get("continuation_id", ""),
        "at": v1_review.get("at", datetime.now(timezone.utc).isoformat()),
    }


def migrate_v1_to_v2(v1_state: dict) -> dict:
    """
    Migrate V1 flat schema to V2 nested schema.

    Gemini LOW fix: Single source of truth for migration logic.
    This function is used by both WorkflowGate and CLI.
    """
    commit_history = v1_state.get("commit_history", [])
    components = v1_state.get("components", [])

    # Handle commit_history that may contain strings or dicts
    completed_count = 0
    for c in commit_history:
        if isinstance(c, dict) and c.get("component") in [
            comp.get("name") if isinstance(comp, dict) else comp
            for comp in components
        ]:
            completed_count += 1

    return {
        "version": "2.0",
        "phase": "component",
        "component": {
            "current": v1_state.get("current_component", ""),
            "step": v1_state.get("step", "plan"),
            "total": len(components),
            "completed": completed_count,
            "list": [
                comp.get("name") if isinstance(comp, dict) else comp
                for comp in components
            ],
        },
        "pr_review": {
            "step": "pr-pending",
            "iteration": 0,
            "pr_url": None,
            "pr_number": None,
            "iterations": [],
            "unresolved_comments": [],
            "ci_status": None,
        },
        "reviews": {
            "gemini": _migrate_review_helper(v1_state.get("gemini_review", {})),
            "codex": _migrate_review_helper(v1_state.get("codex_review", {})),
        },
        "reviewers": {},
        "ci": {
            "component_passed": v1_state.get("ci_passed", False),
            "pr_ci_passed": False,
            "local_test_passed": False,
        },
        "git": {
            "branch": v1_state.get("branch"),
            "base_branch": v1_state.get("base_branch", "master"),
            "commits": commit_history,
            "pr_commits": [],
        },
        "subtasks": {"queue": [], "completed": [], "failed": []},
        "task_file": v1_state.get("task_file"),
        "analysis_completed": v1_state.get("analysis_completed", False),
        "context": v1_state.get("context", {
            "current_tokens": 0,
            "max_tokens": int(os.getenv("CLAUDE_MAX_TOKENS", str(DEFAULT_MAX_TOKENS))),
            "last_check_timestamp": datetime.now(timezone.utc).isoformat(),
        }),
    }


class WorkflowGate:
    """
    Enforces 6-step workflow pattern with hard gates (V2 Schema).

    Key Features:
    - Atomic state file operations with file locking (fcntl)
    - V2 nested state schema for compatibility with CLI
    - Workflow step transitions with validation
    - Dual review tracking (gemini + codex) with continuation ID persistence
    - Audit logging for continuation ID verification
    - Emergency override support with audit trail
    """

    VALID_TRANSITIONS = VALID_TRANSITIONS

    @staticmethod
    def _get_effective_min_required(
        min_required: int,
        enabled_reviewers: list[str]
    ) -> int:
        """
        Calculate effective minimum required approvals.

        Caps min_required by the number of enabled reviewers to prevent deadlock.
        For example, if min_required=2 but only 1 reviewer is enabled, returns 1.

        Gemini LOW fix: DRY - extracted to avoid duplicate logic.

        Args:
            min_required: Configured minimum required approvals
            enabled_reviewers: List of enabled reviewer names

        Returns:
            Effective minimum, capped by enabled reviewer count
        """
        return min(min_required, len(enabled_reviewers))

    def __init__(self, state_file: Optional[Path] = None) -> None:
        """
        Initialize WorkflowGate with optional state file path.

        Args:
            state_file: Path to workflow state JSON file (default: .ai_workflow/workflow-state.json)
        """
        self._state_file = state_file or STATE_FILE

    def _init_state(self) -> dict:
        """Initialize default workflow state (V2 schema)."""
        return {
            "version": "2.0",
            "phase": "component",
            "component": {
                "current": "",
                "step": "plan",
                "list": [],
                "total": 0,
                "completed": 0,
            },
            "pr_review": {
                "step": "pr-pending",
                "iteration": 0,
                "pr_url": None,
                "pr_number": None,
                "iterations": [],
                "unresolved_comments": [],
                "ci_status": None,
            },
            "reviews": {
                "gemini": {},
                "codex": {},
            },
            "reviewers": {},  # For PR phase
            "ci": {
                "component_passed": False,
                "pr_ci_passed": False,
                "local_test_passed": False,
            },
            "git": {
                "branch": None,
                "base_branch": "master",
                "commits": [],
                "pr_commits": [],
            },
            "subtasks": {"queue": [], "completed": [], "failed": []},
            "task_file": None,
            "analysis_completed": False,
            "context": {
                "current_tokens": 0,
                "max_tokens": int(os.getenv("CLAUDE_MAX_TOKENS", str(DEFAULT_MAX_TOKENS))),
                "last_check_timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }

    def _ensure_defaults(self, state: dict) -> dict:
        """Ensure all required V2 fields exist for backward compatibility."""
        # Check if this is V1 state that needs migration
        if "version" not in state or state.get("version") != "2.0":
            return self._migrate_v1_to_v2(state)

        # Ensure nested structures exist
        defaults = self._init_state()

        if "component" not in state:
            state["component"] = defaults["component"]
        else:
            for key, value in defaults["component"].items():
                if key not in state["component"]:
                    state["component"][key] = value

        if "reviews" not in state:
            state["reviews"] = defaults["reviews"]

        if "ci" not in state:
            state["ci"] = defaults["ci"]
        else:
            for key, value in defaults["ci"].items():
                if key not in state["ci"]:
                    state["ci"][key] = value

        if "git" not in state:
            state["git"] = defaults["git"]

        if "pr_review" not in state:
            state["pr_review"] = defaults["pr_review"]

        if "subtasks" not in state:
            state["subtasks"] = defaults["subtasks"]

        if "context" not in state:
            state["context"] = defaults["context"]

        return state

    def _migrate_v1_to_v2(self, v1_state: dict) -> dict:
        """Migrate V1 flat schema to V2 nested schema.

        Gemini LOW fix: Delegates to standalone function for single source of truth.
        """
        return migrate_v1_to_v2(v1_state)

    # =========================================================================
    # File Locking (prevents race conditions)
    # =========================================================================

    def _acquire_lock(self, max_retries: int = 3) -> int:
        """
        Acquire exclusive file lock for state file.

        Addresses C1: Ensures file descriptor is always closed on any failure
        between os.open() and fcntl.flock() to prevent fd leaks and deadlocks.
        """
        lock_file = self._state_file.parent / ".workflow-state.lock"
        lock_file.parent.mkdir(parents=True, exist_ok=True)

        for attempt in range(max_retries):
            lock_fd = -1
            try:
                lock_fd = os.open(str(lock_file), os.O_CREAT | os.O_WRONLY, 0o644)
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return lock_fd
            except OSError as e:
                # Always clean up file descriptor on any failure
                if lock_fd >= 0:
                    try:
                        os.close(lock_fd)
                    except OSError:
                        pass
                if attempt < max_retries - 1:
                    time.sleep(0.1 * (2**attempt))
                    continue
                raise RuntimeError(f"Failed to acquire lock after {max_retries} attempts") from e
            except Exception:
                # Handle any unexpected exception by cleaning up fd
                if lock_fd >= 0:
                    try:
                        os.close(lock_fd)
                    except OSError:
                        pass
                raise
        raise RuntimeError("Lock acquisition failed")

    def _release_lock(self, lock_fd: int) -> None:
        """Release file lock."""
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        except OSError as e:
            print(f"Warning: Failed to release lock: {e}", file=sys.stderr)

    def _save_state_unlocked(self, state: dict) -> None:
        """Save workflow state without acquiring lock (internal use only)."""
        self._state_file.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write: write to temp file then rename
        temp_fd, temp_path = tempfile.mkstemp(
            dir=self._state_file.parent, prefix=".workflow-state-", suffix=".tmp"
        )
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            Path(temp_path).replace(self._state_file)
        except OSError:
            Path(temp_path).unlink(missing_ok=True)
            raise

    @contextmanager
    def _locked_state(self) -> Generator[dict, None, None]:
        """Context manager for atomic read-modify-write operations."""
        lock_fd = self._acquire_lock()
        try:
            state = self.load_state()
            yield state
            self._save_state_unlocked(state)
        finally:
            self._release_lock(lock_fd)

    @contextmanager
    def locked_state_context(self) -> Generator[dict, None, None]:
        """Public API for atomic locked state modifications."""
        with self._locked_state() as state:
            yield state

    def locked_modify_state(self, modifier: Callable[[dict], None]) -> dict:
        """Perform locked read-modify-write operation."""
        with self._locked_state() as state:
            modifier(state)
            return state

    # =========================================================================
    # State Management
    # =========================================================================

    def load_state(self) -> dict:
        """Load workflow state from JSON file."""
        if not self._state_file.exists():
            return self._init_state()
        try:
            state = json.loads(self._state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"Warning: Failed to parse workflow state file: {e}", file=sys.stderr)
            print("   Initializing fresh state...", file=sys.stderr)
            return self._init_state()
        return self._ensure_defaults(state)

    def save_state(self, state: dict) -> None:
        """Save workflow state with locking."""
        lock_fd = self._acquire_lock()
        try:
            self._save_state_unlocked(state)
        finally:
            self._release_lock(lock_fd)

    # =========================================================================
    # Workflow Transitions (V2 Schema)
    # =========================================================================

    def can_transition(self, current: str, next_step: str) -> tuple[bool, str]:
        """Check if transition is valid."""
        if next_step not in self.VALID_TRANSITIONS.get(current, []):
            return False, f"Cannot transition from '{current}' to '{next_step}'"
        return True, ""

    def advance(self, next_step: str, config: Optional["WorkflowConfig"] = None) -> None:
        """Advance workflow to next step (with validation) - V2 schema.

        Args:
            next_step: The step to advance to
            config: Optional WorkflowConfig for dynamic reviewer requirements

        Raises:
            WorkflowTransitionError: If transition is not valid
            WorkflowGateBlockedError: If plan-review gate blocks the transition
        """
        with self._locked_state() as state:
            current = state["component"]["step"]

            can, error_msg = self.can_transition(current, next_step)
            if not can:
                raise WorkflowTransitionError(error_msg)

            # Gate: Check plan review approval before advancing to implement
            if current == "plan-review" and next_step == "implement":
                reviews = state.get("reviews", {})

                # Use config for dynamic reviewer requirements, fallback to defaults
                if config:
                    enabled_reviewers = config.get_enabled_reviewers()
                    min_required = config.get_min_required_approvals()
                else:
                    enabled_reviewers = ["gemini", "codex"]
                    min_required = 1  # Fallback: at least 1 for plan-review

                # Codex MEDIUM fix: Cap min_required by enabled_reviewers to prevent deadlock
                effective_min_required = self._get_effective_min_required(min_required, enabled_reviewers)

                # Count approved reviewers with valid continuation IDs
                # Gemini LOW fix: Also verify continuation ID is not a placeholder
                approved_count = 0
                for reviewer in enabled_reviewers:
                    review_data = reviews.get(reviewer, {})
                    if review_data.get("status") == REVIEW_APPROVED:
                        cont_id = review_data.get("continuation_id", "")
                        if cont_id and not self._is_placeholder_id(cont_id):
                            approved_count += 1

                # Use ceiling division for stricter quorum on effective min
                # effective_min=1 → 1, effective_min=2 → 1, effective_min=3 → 2
                plan_review_min = max(1, math.ceil(effective_min_required / 2))

                if approved_count < plan_review_min:
                    # Build detailed error message for caller to format
                    details = {
                        "enabled_reviewers": enabled_reviewers,
                        "required": plan_review_min,
                        "approved": approved_count,
                        "review_status": {},
                    }
                    for reviewer in enabled_reviewers:
                        review_data = reviews.get(reviewer, {})
                        status = review_data.get("status", REVIEW_NOT_REQUESTED)
                        cont_id = review_data.get("continuation_id", "")
                        is_placeholder = self._is_placeholder_id(cont_id)
                        details["review_status"][reviewer] = {
                            "status": status,
                            "is_placeholder": is_placeholder,
                        }
                    raise WorkflowGateBlockedError(
                        f"Plan review not approved: {approved_count}/{plan_review_min} required approvals",
                        details,
                    )
                # Clear reviews for code review later (use empty dict, not hardcoded keys)
                state["reviews"] = {}

            # Special logic for review steps
            if next_step == "plan-review":
                print("Requesting plan review...")
                print("   Follow @docs/AI/Workflows/03-reviews.md for review process")

            if next_step == "review":
                print("Requesting code review...")
                print("   Follow @docs/AI/Workflows/03-reviews.md for review process")

            state["component"]["step"] = next_step

        print(f"Advanced to '{next_step}' step")

    # =========================================================================
    # Review Management (V2 Schema with Continuation ID Persistence)
    # =========================================================================

    def _log_to_audit(self, continuation_id: str, event_type: str = "review") -> None:
        """Log continuation ID and events to audit log."""
        try:
            AUDIT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event_type": event_type,
                "continuation_id": continuation_id,
            }
            with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            print(f"CRITICAL: Failed to write to audit log: {e}", file=sys.stderr)
            raise

    def _log_override_to_audit(self, user: str = "unknown") -> None:
        """Log emergency override usage to audit log."""
        try:
            AUDIT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event_type": "EMERGENCY_OVERRIDE",
                "user": user,
                "env_var": os.environ.get("ZEN_REVIEW_OVERRIDE", ""),
                "warning": "Gates bypassed via emergency override",
            }
            with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            print(f"Warning: Failed to log override to audit: {e}", file=sys.stderr)

    def _is_placeholder_id(self, continuation_id: str) -> bool:
        """
        Detect placeholder/fake continuation IDs.

        Fixed: Case-insensitive matching and rejects empty/blank IDs.
        """
        # Reject empty, None, or whitespace-only IDs
        if not continuation_id or not continuation_id.strip():
            return True

        # Case-insensitive pattern matching
        id_lower = continuation_id.lower().strip()
        for pattern in PLACEHOLDER_PATTERNS:
            if re.match(pattern, id_lower):
                return True
        return False

    def _is_continuation_id_in_audit_log(self, continuation_id: str) -> bool:
        """Verify continuation ID exists in audit log."""
        if not AUDIT_LOG_FILE.exists():
            return False
        with open(AUDIT_LOG_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("continuation_id") == continuation_id:
                        return True
                except json.JSONDecodeError:
                    continue
        return False

    def record_review(
        self,
        continuation_id: str,
        status: str,
        cli_name: str = "codex",
        config: Optional["WorkflowConfig"] = None
    ) -> None:
        """
        Record review result from a specific CLI (gemini or codex) - V2 schema.

        Fixes:
        - Persists continuation_id for component reviews
        - Gemini MEDIUM: Handles PR phase logic (stores in reviewers dict)
        - Gemini MEDIUM: Validates cli_name against config.available reviewers

        Raises:
            WorkflowValidationError: If cli_name is invalid or continuation_id is a placeholder
        """
        # Gemini MEDIUM fix: Validate against config's available reviewers
        if config:
            available = config.config.get("reviewers", {}).get("available", ["claude", "gemini", "codex"])
        else:
            available = ["claude", "gemini", "codex"]

        if cli_name not in available:
            raise WorkflowValidationError(
                f"Invalid CLI name: {cli_name}. Valid options: {', '.join(available)}"
            )

        # Validate continuation ID
        if self._is_placeholder_id(continuation_id):
            raise WorkflowValidationError(
                f"Invalid continuation ID: {continuation_id}. "
                "Continuation ID cannot be empty or a placeholder"
            )

        with self._locked_state() as state:
            # Log to audit trail
            self._log_to_audit(continuation_id, event_type="review")

            # Store review in V2 format with continuation_id
            review_data = {
                "status": status,
                "continuation_id": continuation_id,
                "at": datetime.now(timezone.utc).isoformat(),
            }

            # Gemini MEDIUM fix: Handle PR phase vs component phase
            phase = state.get("phase", "component")
            if phase == "pr-review":
                # PR phase - store in reviewers dict
                if "reviewers" not in state:
                    state["reviewers"] = {}
                state["reviewers"][cli_name] = review_data
            else:
                # Component phase - store in reviews dict
                if "reviews" not in state:
                    state["reviews"] = {}
                state["reviews"][cli_name] = review_data

        print(f"Recorded {cli_name} review: {status}")

        if status == REVIEW_NEEDS_REVISION:
            print("Review requires changes. Fix issues and re-request review.")
            print("   See @docs/AI/Workflows/03-reviews.md for addressing review feedback")

    def record_ci(self, passed: bool) -> None:
        """Record CI result - V2 schema."""
        with self._locked_state() as state:
            if "ci" not in state:
                state["ci"] = {}

            phase = state.get("phase", "component")
            if phase == "pr-review":
                state["ci"]["pr_ci_passed"] = passed
            else:
                state["ci"]["component_passed"] = passed

        print(f"Recorded CI: {'PASSED' if passed else 'FAILED'}")

    # =========================================================================
    # Commit Management (V2 Schema with Override Auditing)
    # =========================================================================

    def get_commit_status(self, config: Optional["WorkflowConfig"] = None) -> dict:
        """
        Get commit prerequisites status as a structured dict.

        Gemini HIGH fix: Shared logic for both CLI check and pre-commit hook.

        Returns:
            dict with keys:
                - ready: bool - whether commit is allowed
                - override: bool - whether override is active
                - checks: dict - individual check results
                - config: dict - reviewer configuration used
        """
        result = {
            "ready": False,
            "override": False,
            "checks": {},
            "config": {},
        }

        # Check for override
        override_env = os.environ.get("ZEN_REVIEW_OVERRIDE", "").strip()
        if override_env in ("1", "true", "TRUE", "True", "yes", "YES"):
            user = os.environ.get("USER", os.environ.get("USERNAME", "unknown"))
            self._log_override_to_audit(user)
            result["ready"] = True
            result["override"] = True
            result["user"] = user
            result["message"] = "ZEN_REVIEW_OVERRIDE active - bypassing gates (LOGGED)"
            return result

        state = self.load_state()

        # Get reviewer requirements from config or use defaults
        if config:
            enabled_reviewers = config.get_enabled_reviewers()
            min_required = config.get_min_required_approvals()
        else:
            enabled_reviewers = ["gemini", "codex"]
            min_required = 2

        # Codex MEDIUM fix: Cap min_required by enabled_reviewers count to prevent deadlock
        # If only 1 reviewer is enabled, min_required=2 would permanently block commits
        effective_min_required = self._get_effective_min_required(min_required, enabled_reviewers)

        result["config"] = {
            "enabled_reviewers": enabled_reviewers,
            "min_required": effective_min_required,
        }

        # Check component set
        component = state.get("component", {}).get("current", "")
        result["checks"]["component_set"] = bool(component)

        # Check current step
        current_step = state["component"]["step"]
        result["checks"]["in_review_step"] = current_step == "review"
        result["checks"]["current_step"] = current_step

        # Check CI pass
        result["checks"]["ci_passed"] = state.get("ci", {}).get("component_passed", False)

        # Check review approvals
        reviews = state.get("reviews", {})
        approved_reviewers = []

        for reviewer in enabled_reviewers:
            review_data = reviews.get(reviewer, {})
            status = review_data.get("status", REVIEW_NOT_REQUESTED)
            cont_id = review_data.get("continuation_id", "")
            is_valid = status == REVIEW_APPROVED and cont_id and not self._is_placeholder_id(cont_id)
            result["checks"][f"{reviewer}_approved"] = is_valid
            if is_valid:
                approved_reviewers.append(reviewer)

        result["checks"]["approved_reviewers"] = approved_reviewers
        result["checks"]["sufficient_approvals"] = len(approved_reviewers) >= effective_min_required

        # Overall readiness
        result["ready"] = (
            result["checks"]["component_set"] and
            result["checks"]["in_review_step"] and
            result["checks"]["ci_passed"] and
            result["checks"]["sufficient_approvals"]
        )

        return result

    def check_commit(self, config: Optional["WorkflowConfig"] = None) -> bool:
        """
        Validate commit prerequisites (called by pre-commit hook) - V2 schema.

        Uses get_commit_status() for shared logic, then formats output for hook.

        Returns:
            bool: True if commit is allowed (including override), False otherwise

        Raises:
            WorkflowGateBlockedError: If commit is blocked (for programmatic use)

        Note:
            For CLI/hook usage, callers should handle WorkflowGateBlockedError
            and sys.exit() appropriately. For programmatic use, catch the exception.
        """
        status = self.get_commit_status(config)

        if status.get("override"):
            print("=" * 60, file=sys.stderr)
            print("WARNING: EMERGENCY OVERRIDE ACTIVE", file=sys.stderr)
            print("=" * 60, file=sys.stderr)
            print(f"   ZEN_REVIEW_OVERRIDE active", file=sys.stderr)
            print(f"   User: {status.get('user', 'unknown')}", file=sys.stderr)
            print("   Bypassing workflow gates for emergency hotfix", file=sys.stderr)
            print("   THIS ACTION HAS BEEN LOGGED TO AUDIT TRAIL", file=sys.stderr)
            print("=" * 60, file=sys.stderr)
            return True

        checks = status["checks"]
        cfg = status["config"]

        # Check current step
        if not checks["in_review_step"]:
            raise WorkflowGateBlockedError(
                f"Current step is '{checks['current_step']}', must be 'review'",
                {"reason": "wrong_step", "current_step": checks["current_step"]}
            )

        # Check review approvals
        if not checks["sufficient_approvals"]:
            raise WorkflowGateBlockedError(
                "Insufficient review approvals",
                {
                    "reason": "insufficient_approvals",
                    "required": cfg["min_required"],
                    "enabled_reviewers": cfg["enabled_reviewers"],
                    "approved_reviewers": checks["approved_reviewers"],
                    "reviewer_status": {
                        r: checks.get(f"{r}_approved", False) for r in cfg["enabled_reviewers"]
                    },
                }
            )

        # Check CI pass
        if not checks["ci_passed"]:
            raise WorkflowGateBlockedError(
                "CI not passed",
                {"reason": "ci_not_passed"}
            )

        # All gates passed
        print("Commit prerequisites satisfied")
        for reviewer in checks["approved_reviewers"]:
            print(f"   {reviewer.capitalize()} review: APPROVED")
        print("   CI: PASSED")
        return True

    def record_commit(self, commit_hash: Optional[str] = None) -> None:
        """Record commit hash after successful commit - V2 schema.

        Raises:
            WorkflowError: If git rev-parse fails to get commit hash
        """
        if not commit_hash:
            try:
                result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    check=True,
                    cwd=PROJECT_ROOT,
                )
                commit_hash = result.stdout.strip()
            except subprocess.CalledProcessError as e:
                raise WorkflowError(f"Failed to get commit hash: {e}") from e

        with self._locked_state() as state:
            # V2: git.commits
            if "git" not in state:
                state["git"] = {"commits": [], "pr_commits": []}
            if "commits" not in state["git"]:
                state["git"]["commits"] = []

            # Record commit with component info
            component = state["component"]["current"]
            state["git"]["commits"].append({
                "component": component,
                "hash": commit_hash,
                "at": datetime.now(timezone.utc).isoformat(),
            })

            # Keep last 100 commits
            state["git"]["commits"] = state["git"]["commits"][-100:]

            # Reset for next component (V2 fields)
            state["component"]["step"] = "plan"
            state["component"]["current"] = ""
            # Use empty dict instead of hardcoded reviewer keys (Gemini MEDIUM fix)
            state["reviews"] = {}
            state["ci"]["component_passed"] = False

        print(f"Recorded commit {commit_hash[:8]}")
        print("Ready for next component (step: plan)")

    # =========================================================================
    # Component & Status Management (V2 Schema)
    # =========================================================================

    def set_component(self, component_name: str) -> None:
        """Set the current component name - V2 schema."""
        with self._locked_state() as state:
            state["component"]["current"] = component_name

            # Add to list if not present
            if component_name not in state["component"].get("list", []):
                if "list" not in state["component"]:
                    state["component"]["list"] = []
                state["component"]["list"].append(component_name)
                state["component"]["total"] = len(state["component"]["list"])

        print(f"Set current component: {component_name}")

    def show_status(self) -> None:
        """Display current workflow state - V2 schema."""
        state = self.load_state()

        print("=" * 50)
        print("Workflow State (V2)")
        print("=" * 50)
        print(f"Component: {state['component']['current'] or 'NONE'}")
        print(f"Current Step: {state['component']['step']}")
        print()
        print("Workflow Progress:")

        current = state["component"]["step"]
        steps = ["plan", "plan-review", "implement", "test", "review"]
        for i, step in enumerate(steps, 1):
            label = STEP_DESCRIPTIONS.get(step, step.capitalize())
            if step == current:
                marker = "<-- YOU ARE HERE"
            elif steps.index(current) > steps.index(step):
                marker = "[done]"
            else:
                marker = ""
            print(f"  {i}. {label} {marker}")

        print()
        print("Gate Status:")
        reviews = state.get("reviews", {})
        gemini_status = reviews.get("gemini", {}).get("status", "NOT_REQUESTED")
        codex_status = reviews.get("codex", {}).get("status", "NOT_REQUESTED")
        ci_status = "PASSED" if state.get("ci", {}).get("component_passed") else "NOT_RUN"
        print(f"  Gemini Review: {gemini_status}")
        print(f"  Codex Review: {codex_status}")
        print(f"  CI: {ci_status}")
        print()

    def reset(self) -> None:
        """Reset workflow state (emergency use only)."""
        state = self._init_state()
        self.save_state(state)
        print("Workflow state reset")

    # =========================================================================
    # Planning Management (V2 Schema)
    # =========================================================================

    def record_analysis_complete(self) -> None:
        """Mark pre-implementation analysis as complete."""
        with self._locked_state() as state:
            state["analysis_completed"] = True
        print("Analysis marked as complete")

    def set_components_list(self, components: list[str]) -> None:
        """Define component breakdown for task - V2 schema.

        Raises:
            WorkflowValidationError: If fewer than 2 components are provided
        """
        if len(components) < 2:
            raise WorkflowValidationError(
                f"Must have at least 2 components, got {len(components)}"
            )

        with self._locked_state() as state:
            state["component"]["list"] = components
            state["component"]["total"] = len(components)
            state["component"]["completed"] = 0

        print(f"Set {len(components)} components:")
        for i, name in enumerate(components, 1):
            print(f"   {i}. {name}")


# Import WorkflowConfig for type hints (avoid circular import)
if False:  # TYPE_CHECKING equivalent without import
    from .config import WorkflowConfig
