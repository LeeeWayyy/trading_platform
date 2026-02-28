"""
Constants for AI Workflow.

Defines paths and shared constants used across all modules.
"""

from pathlib import Path
from typing import Literal

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent.parent

# Workflow directory (tool-agnostic design - replaces .claude/)
WORKFLOW_DIR = Path(".ai_workflow")

# State and config files
STATE_FILE = WORKFLOW_DIR / "workflow-state.json"
CONFIG_FILE = WORKFLOW_DIR / "config.json"
AUDIT_LOG = WORKFLOW_DIR / "workflow-audit.log"
AUDIT_LOG_FILE = AUDIT_LOG  # Alias for backward compatibility with core.py

# Legacy paths (for migration)
LEGACY_CLAUDE_DIR = Path(".claude")
LEGACY_STATE_FILE = LEGACY_CLAUDE_DIR / "workflow-state.json"

# Step types
StepType = Literal["plan", "plan-review", "implement", "test", "review"]

# Review status constants
REVIEW_APPROVED = "APPROVED"
REVIEW_NEEDS_REVISION = "NEEDS_REVISION"
REVIEW_NOT_REQUESTED = "NOT_REQUESTED"

# Valid workflow transitions for component phase
VALID_TRANSITIONS = {
    "plan": ["plan-review"],
    "plan-review": ["implement", "plan"],
    "implement": ["test"],
    "test": ["review", "implement"],
    "review": ["implement"],
}

# Step descriptions for display
STEP_DESCRIPTIONS = {
    "plan": "Design component approach",
    "plan-review": "Get plan reviewed before coding",
    "implement": "Write code + tests (TDD)",
    "test": "Run tests locally",
    "review": "Get code reviewed before commit",
}

# Context thresholds (percentages)
CONTEXT_WARN_PCT = 70
CONTEXT_CRITICAL_PCT = 85
DEFAULT_MAX_TOKENS = 200000

# Review diff truncation limit (characters)
# Diffs larger than this are truncated to fit context windows
DIFF_TRUNCATION_LIMIT = 30000

# Placeholder ID patterns to block
PLACEHOLDER_PATTERNS = [
    r"^test-",
    r"^placeholder-",
    r"^fake-",
    r"^dummy-",
    r"^mock-",
]
