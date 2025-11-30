"""
Reviewer Integration for AI Workflow.

Addresses review feedback:
- H6: Uses config.get_enabled_reviewers() instead of hardcoded list
- H9: Continuation ID persistence for multi-round reviews
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Dict, Tuple, Optional
import json
import sys

from .config import WorkflowConfig
from .constants import DIFF_TRUNCATION_LIMIT


class ReviewerType(Enum):
    CLAUDE = "claude"
    GEMINI = "gemini"
    CODEX = "codex"


class ReviewStatus(Enum):
    """Review status values.

    Addresses Claude HIGH review: Added DISMISSED state for when reviewers dismiss their review.
    """
    NOT_REQUESTED = "NOT_REQUESTED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    DISMISSED = "DISMISSED"  # Claude fix: was used in code but missing from enum
    ERROR = "ERROR"


@dataclass
class ReviewResult:
    """Result from a code review."""
    reviewer: str
    status: ReviewStatus
    continuation_id: str = ""
    findings: list = field(default_factory=list)
    error_message: str = ""


class ReviewerOrchestrator:
    """
    Manages reviewer state and builds MCP tool parameters.

    Uses config for reviewer list - never hardcoded.
    """

    def __init__(self, state: dict, config: WorkflowConfig = None):
        self.state = state
        self.config = config or WorkflowConfig()
        self._ensure_reviewer_state()

    def _ensure_reviewer_state(self) -> None:
        """Ensure reviewer state structure exists."""
        if "reviewers" not in self.state:
            self.state["reviewers"] = {}

        for reviewer in self.config.get_enabled_reviewers():
            if reviewer not in self.state["reviewers"]:
                self.state["reviewers"][reviewer] = {
                    "status": ReviewStatus.NOT_REQUESTED.value,
                    "continuation_id": None,
                    "last_updated": None,
                }

    def get_continuation_id(self, reviewer_name: str) -> Optional[str]:
        """Get continuation_id for multi-round review context."""
        reviewer_state = self.state["reviewers"].get(reviewer_name, {})
        return reviewer_state.get("continuation_id")

    def set_continuation_id(self, reviewer_name: str, continuation_id: str) -> None:
        """Store continuation_id after first review round."""
        if reviewer_name not in self.state["reviewers"]:
            self.state["reviewers"][reviewer_name] = {}
        self.state["reviewers"][reviewer_name]["continuation_id"] = continuation_id
        self.state["reviewers"][reviewer_name]["last_updated"] = (
            datetime.now(timezone.utc).isoformat()
        )

    def record_review_result(
        self,
        reviewer_name: str,
        status: ReviewStatus,
        continuation_id: str = None
    ) -> None:
        """Record result of a review."""
        if reviewer_name not in self.state["reviewers"]:
            self.state["reviewers"][reviewer_name] = {}

        self.state["reviewers"][reviewer_name]["status"] = status.value
        self.state["reviewers"][reviewer_name]["last_updated"] = (
            datetime.now(timezone.utc).isoformat()
        )

        if continuation_id:
            self.state["reviewers"][reviewer_name]["continuation_id"] = continuation_id

    def check_all_approved(self) -> Tuple[bool, str]:
        """
        Check if minimum required reviewers have approved.

        Returns:
            (all_approved: bool, message: str)
        """
        enabled = self.config.get_enabled_reviewers()
        min_required = self.config.get_min_required_approvals()

        approved_count = 0
        pending_reviewers = []
        error_reviewers = []

        for reviewer in enabled:
            status = self.state["reviewers"].get(reviewer, {}).get(
                "status", ReviewStatus.NOT_REQUESTED.value
            )
            if status == ReviewStatus.APPROVED.value:
                approved_count += 1
            elif status == ReviewStatus.ERROR.value:
                error_reviewers.append(reviewer)
            elif status != ReviewStatus.DISMISSED.value:
                pending_reviewers.append(reviewer)

        if error_reviewers:
            return False, f"Review errors from: {', '.join(error_reviewers)}"

        if approved_count >= min_required:
            return True, f"{approved_count}/{len(enabled)} approved"
        else:
            return False, f"Waiting for: {', '.join(pending_reviewers)}"

    def _get_valid_cli_names(self) -> set:
        """Get valid CLI names from config or use defaults.

        Addresses Gemini HIGH review: No longer hardcoded, supports tool-agnostic design.
        CLIs can be extended via config["reviewers"]["valid_clis"] or defaults are used.
        """
        # Allow config to extend/override valid CLI names
        configured = self.config.config.get("reviewers", {}).get("valid_clis", [])
        if configured:
            return set(configured)
        # Fallback to well-known Zen MCP CLI names
        return {"claude", "gemini", "codex"}

    def build_clink_params(
        self,
        reviewer_name: str,
        diff: str,
        file_paths: List[str] = None,
        continuation_id: str = None
    ) -> dict:
        """
        Build parameters for mcp__zen__clink call.

        Returns dict that agent uses to call the MCP tool.

        Addresses Claude review H6: Validates reviewer_name matches available CLIs.
        Addresses Gemini HIGH review: CLI names now configurable via config.

        Raises:
            ValueError: If reviewer_name is not a valid CLI name
        """
        # Validate CLI name - now uses configurable list (Gemini HIGH fix)
        valid_clis = self._get_valid_cli_names()
        if reviewer_name not in valid_clis:
            raise ValueError(
                f"Invalid CLI name: '{reviewer_name}'. "
                f"Valid options: {', '.join(sorted(valid_clis))}. "
                f"Add custom CLIs via config['reviewers']['valid_clis']."
            )

        # Truncate diff if too large (M3 fix: use constant, warn user)
        truncated_diff = diff
        if len(diff) > DIFF_TRUNCATION_LIMIT:
            truncated_diff = diff[:DIFF_TRUNCATION_LIMIT]
            print(
                f"Warning: Diff truncated from {len(diff)} to {DIFF_TRUNCATION_LIMIT} chars",
                file=sys.stderr
            )

        prompt = f"""Review the following code changes:

```diff
{truncated_diff}
```

Provide feedback as JSON with status (APPROVED/NEEDS_REVISION) and findings list.
"""

        params = {
            "cli_name": reviewer_name,
            "prompt": prompt,
            "role": "codereviewer",
        }

        if file_paths:
            params["absolute_file_paths"] = file_paths
        # Use explicit None check - empty string "" is falsy but could be valid ID
        if continuation_id is not None:
            params["continuation_id"] = continuation_id

        return params
