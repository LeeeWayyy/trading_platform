"""
PR Review Phase Workflow Handler.

Addresses review feedback:
- H6: Uses config.get_enabled_reviewers() instead of hardcoded list
- L3: Clarifies CI status handling (error vs pending vs failed)
- L5: Single tracking location for comments
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Dict, Optional, Tuple
import json
import subprocess
import sys

from .config import WorkflowConfig
from .git_utils import get_owner_repo, gh_api, gh_graphql


class CIStatus(Enum):
    """
    CI status values.

    Addresses L3: Clear distinction between states
    """
    PENDING = "pending"      # CI hasn't started yet
    RUNNING = "running"      # CI is currently running
    PASSED = "passed"        # All checks passed
    FAILED = "failed"        # At least one check failed
    ERROR = "error"          # Could not determine status (API error)


class PRWorkflowHandler:
    """
    Handles PR review phase state machine.

    Uses config for reviewer list - never hardcoded.
    """

    PR_STEPS = [
        "pr-pending",
        "pr-review-check",
        "pr-review-fix",
        "pr-local-review",
        "pr-local-test",
        "pr-commit",
        "pr-commit-failed",
        "pr-approved",
        "pr-ready",
        "merged",
    ]

    VALID_TRANSITIONS = {
        "pr-pending": ["pr-review-check"],
        "pr-review-check": ["pr-review-fix", "pr-approved", "pr-review-check"],
        "pr-review-fix": ["pr-local-review"],
        "pr-local-review": ["pr-local-test"],
        "pr-local-test": ["pr-commit", "pr-local-review"],
        "pr-commit": ["pr-review-check", "pr-commit-failed"],
        "pr-commit-failed": ["pr-local-review"],
        "pr-approved": ["pr-ready"],
        "pr-ready": ["merged"],
    }

    def __init__(self, state: dict, config: WorkflowConfig = None):
        self.state = state
        self.config = config or WorkflowConfig()
        self._ensure_pr_state()

    def _ensure_pr_state(self) -> None:
        """Ensure PR review state structure exists."""
        if "pr_review" not in self.state:
            self.state["pr_review"] = {
                "step": "pr-pending",
                "iteration": 0,
                "pr_url": None,
                "pr_number": None,
                "iterations": [],
                "unresolved_comments": [],
                "ci_status": None,
            }

    def start_pr_phase(self, pr_url: str = None, pr_number: int = None) -> bool:
        """
        Transition from component phase to PR review phase.

        Args:
            pr_url: Optional PR URL
            pr_number: Optional PR number (extracted from URL if not provided)

        Returns:
            True if transition successful
        """
        self.state["phase"] = "pr-review"
        self.state["pr_review"] = {
            "step": "pr-pending",
            "iteration": 0,
            "pr_url": pr_url,
            "pr_number": pr_number,
            "iterations": [],
            "unresolved_comments": [],
            "ci_status": None,
        }
        return True

    def advance_step(self, new_step: str) -> Tuple[bool, str]:
        """
        Advance to next PR workflow step.

        Returns:
            (success: bool, message: str)
        """
        current = self.state["pr_review"]["step"]

        if current not in self.VALID_TRANSITIONS:
            return False, f"Unknown current step: {current}"

        if new_step not in self.VALID_TRANSITIONS[current]:
            valid = self.VALID_TRANSITIONS[current]
            return False, f"Invalid transition from {current} to {new_step}. Valid: {valid}"

        self.state["pr_review"]["step"] = new_step

        # datetime already imported at module level
        now = datetime.now(timezone.utc).isoformat()
        self.state["pr_review"]["last_updated"] = now

        return True, f"Advanced to {new_step}"

    def check_pr_status(self) -> dict:
        """
        Check current PR status including reviews and CI.

        Returns dict with status information for agent display.
        """
        pr_number = self.state["pr_review"].get("pr_number")
        if not pr_number:
            return {"error": "No PR number set", "all_approved": False}

        # CRITICAL: Fetch and sync PR reviews from GitHub
        self._fetch_and_sync_pr_reviews(pr_number)

        # Fetch comments (IDs only for main agent)
        comments = self.fetch_pr_comment_metadata(pr_number)
        ci_status = self._fetch_pr_ci_status(pr_number)

        # Update state
        pr = self.state["pr_review"]
        pr["unresolved_comments"] = [c for c in comments if not c.get("resolved")]
        pr["ci_status"] = ci_status.value

        # Check reviewer approvals using orchestrator
        approved, message = self._check_all_reviewers_approved()

        return {
            "step": pr["step"],
            "iteration": pr["iteration"],
            "unresolved_count": len(pr["unresolved_comments"]),
            "ci_status": ci_status.value,
            "all_approved": approved,
            "approval_message": message,
        }

    def _fetch_and_sync_pr_reviews(self, pr_number: int) -> None:
        """
        Fetch PR reviews from GitHub and sync to internal state.

        CRITICAL: Without this, state["reviewers"][*]["status"] stays at
        NOT_REQUESTED forever, causing the workflow to deadlock.

        Addresses Gemini review: Now uses gh_api helper for consistency.
        Addresses Claude review H4: Now logs errors instead of silent pass.
        """
        try:
            # Use gh_api helper for consistency (addresses Gemini review)
            result = gh_api(
                f"repos/{{owner}}/{{repo}}/pulls/{pr_number}/reviews",
                jq=".[].{author: .user.login, state: .state}"
            )

            if result.returncode != 0:
                # Log warning but don't fail - reviews might not exist yet
                print(f"Warning: Could not fetch PR reviews: {result.stderr}",
                      file=sys.stderr)
                print("   Ensure 'gh' CLI is installed and authenticated",
                      file=sys.stderr)
                return

            # Parse reviews and update state
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue
                try:
                    review = json.loads(line)
                    username = review.get("author", "")
                    gh_state = review.get("state", "").upper()

                    # Map GitHub state to our ReviewStatus
                    # GitHub states: APPROVED, CHANGES_REQUESTED, COMMENTED, PENDING, DISMISSED
                    if gh_state == "APPROVED":
                        new_status = "APPROVED"
                    elif gh_state == "CHANGES_REQUESTED":
                        new_status = "CHANGES_REQUESTED"
                    elif gh_state == "COMMENTED":
                        # Comments don't change approval status
                        continue
                    elif gh_state == "PENDING":
                        new_status = "PENDING"
                    elif gh_state == "DISMISSED":
                        new_status = "DISMISSED"  # Claude fix: preserve dismissed state
                    else:
                        continue

                    # Update reviewer state
                    # Try to match by username mapping first
                    for reviewer_name in self.config.get_enabled_reviewers():
                        mapped_username = self.config.get_reviewer_username(reviewer_name)
                        if mapped_username == username or reviewer_name == username.lower():
                            if reviewer_name not in self.state["reviewers"]:
                                self.state["reviewers"][reviewer_name] = {}
                            self.state["reviewers"][reviewer_name]["status"] = new_status
                            self.state["reviewers"][reviewer_name]["last_updated"] = (
                                datetime.now(timezone.utc).isoformat()
                            )
                            break

                except json.JSONDecodeError as e:
                    print(f"Warning: Could not parse review JSON: {e}", file=sys.stderr)
                    continue

        except Exception as e:
            print(f"Warning: Failed to sync PR reviews: {e}", file=sys.stderr)

    def fetch_pr_comment_metadata(self, pr_number: int) -> List[dict]:
        """
        Fetch PR review comment metadata (IDs and file paths only).

        Main agent uses this for delegation decisions.
        Sub-agents fetch full comment text themselves.

        Note: Made public (removed underscore) per Gemini review -
        CLI commands need to access this method.
        """
        result = gh_api(
            f"repos/{{owner}}/{{repo}}/pulls/{pr_number}/comments",
            jq=".[] | {id: .id, file_path: .path, resolved: (.position == null)}"
        )

        if result.returncode != 0:
            return []

        comments = []
        for line in result.stdout.strip().split('\n'):
            if line:
                try:
                    comments.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return comments

    def _fetch_pr_ci_status(self, pr_number: int) -> CIStatus:
        """
        Fetch CI status for PR.

        Returns CIStatus enum value.
        """
        result = gh_api(
            f"repos/{{owner}}/{{repo}}/pulls/{pr_number}",
            jq=".mergeable_state"
        )

        if result.returncode != 0:
            return CIStatus.ERROR

        status = result.stdout.strip().strip('"')

        # Map GitHub mergeable_state to our CIStatus
        if status == "clean":
            return CIStatus.PASSED
        elif status == "unstable":
            return CIStatus.FAILED
        elif status in ("blocked", "behind"):
            return CIStatus.PENDING
        elif status == "unknown":
            return CIStatus.RUNNING
        else:
            return CIStatus.ERROR

    def _check_all_reviewers_approved(self) -> Tuple[bool, str]:
        """
        Check if all required reviewers have approved.

        Uses ReviewerOrchestrator for consistent logic.
        """
        from .reviewers import ReviewerOrchestrator, ReviewResult, ReviewStatus

        # Build ReviewResult dict from current state
        results = {}
        for reviewer in self.config.get_enabled_reviewers():
            reviewer_state = self.state["reviewers"].get(reviewer, {})
            status_str = reviewer_state.get("status", "NOT_REQUESTED")

            # Map string status to ReviewStatus enum
            # Addresses H2: Add type checking before enum access
            if isinstance(status_str, str) and status_str in ReviewStatus.__members__:
                status_enum = ReviewStatus[status_str]
            else:
                # Handle non-string or invalid status values safely
                status_enum = ReviewStatus.NOT_REQUESTED

            # Include continuation_id for multi-round review context (H9 fix)
            results[reviewer] = ReviewResult(
                reviewer=reviewer,
                status=status_enum,
                continuation_id=reviewer_state.get("continuation_id", ""),
            )

        # Use orchestrator's check logic
        orchestrator = ReviewerOrchestrator(self.state, self.config)
        return orchestrator.check_all_approved()

    def record_commit_and_push(self, commit_hash: str, message: str) -> Tuple[bool, str]:
        """
        Record commit and verify push succeeded.

        Addresses H2: Verifies push success before recording.
        """
        # First verify the commit exists locally
        result = subprocess.run(
            ["git", "rev-parse", "--verify", commit_hash],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return False, f"Commit {commit_hash} not found locally"

        # Try to push with retry
        max_retries = self.config.config["git"].get("push_retry_count", 3)

        # Gemini MEDIUM fix: Get explicit branch name for push
        branch = self.state.get("git", {}).get("branch")
        if not branch:
            # Fallback to current branch
            branch_result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True
            )
            branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None

        for attempt in range(max_retries):
            # Use explicit remote and branch if available
            push_cmd = ["git", "push"]
            if branch:
                push_cmd.extend(["origin", branch])

            result = subprocess.run(
                push_cmd,
                capture_output=True, text=True, timeout=120
            )

            if result.returncode == 0:
                # Record successful commit
                now = datetime.now(timezone.utc).isoformat()
                if "pr_commits" not in self.state["git"]:
                    self.state["git"]["pr_commits"] = []
                self.state["git"]["pr_commits"].append({
                    "hash": commit_hash,
                    "message": message,
                    "at": now,
                })
                self.state["pr_review"]["step"] = "pr-review-check"
                return True, "Commit pushed successfully"

            # Check if it's a conflict (not retryable)
            if "conflict" in result.stderr.lower():
                self.state["pr_review"]["step"] = "pr-commit-failed"
                return False, f"Push failed due to conflicts: {result.stderr}"

        self.state["pr_review"]["step"] = "pr-commit-failed"
        return False, f"Push failed after {max_retries} attempts: {result.stderr}"

    def reset_for_new_task(self) -> bool:
        """
        Reset workflow state for a new task after merge.

        Only allowed from 'merged' step to prevent accidental resets.
        """
        current_step = self.state.get("pr_review", {}).get("step", "")
        if current_step != "merged":
            return False

        # Archive current state
        if "completed_tasks" not in self.state:
            self.state["completed_tasks"] = []

        self.state["completed_tasks"].append({
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "pr_url": self.state.get("pr_review", {}).get("pr_url"),
            "branch": self.state.get("git", {}).get("branch"),
        })

        # Reset to fresh state
        self.state["phase"] = "component"
        self.state["component"] = {
            "current": "",
            "step": "plan",
            "list": [],
        }
        self.state["pr_review"] = {
            "step": "pr-pending",
            "iteration": 0,
        }
        self.state["reviewers"] = {}
        self.state["subtasks"] = {"queue": [], "completed": [], "failed": []}

        return True
