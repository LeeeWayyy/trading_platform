"""
AI Workflow Enforcement Module

A modular workflow enforcement system for AI-assisted development.
Enforces the 6-step component pattern: plan → plan-review → implement → test → review → commit

Modules:
- constants: Shared paths and constants (WORKFLOW_DIR, STATE_FILE, CONFIG_FILE)
- config: Configuration management (WorkflowConfig)
- core: WorkflowGate class (main workflow state management)
- hash_utils: Git diff hashing utilities
- delegation: DelegationRules class (context monitoring)
- git_utils: Git/GitHub utilities (get_owner_repo, gh_api)
- reviewers: Reviewer integration (ReviewerOrchestrator, ReviewStatus)
- pr_workflow: PR phase handling (PRWorkflowHandler, CIStatus)
- subtasks: Subtask delegation (SubtaskOrchestrator, AgentInstruction)

Usage:
    from scripts.ai_workflow import WorkflowGate, WorkflowConfig
    config = WorkflowConfig()
    gate = WorkflowGate()
    gate.show_status()  # Display current workflow state
"""

# Constants
# Configuration
from .config import WorkflowConfig
from .constants import CONFIG_FILE, STATE_FILE, WORKFLOW_DIR

# Core workflow
from .core import (
    WorkflowError,
    WorkflowGate,
    WorkflowGateBlockedError,
    WorkflowTransitionError,
    WorkflowValidationError,
)
from .delegation import DelegationRules

# Git utilities
from .git_utils import get_owner_repo, gh_api
from .hash_utils import compute_git_diff_hash

# PR workflow
from .pr_workflow import CIStatus, PRWorkflowHandler

# Reviewer integration
from .reviewers import ReviewerOrchestrator, ReviewResult, ReviewStatus

# Subtask delegation
from .subtasks import AgentInstruction, SubtaskOrchestrator, SubtaskStatus

__all__ = [
    # Constants
    "WORKFLOW_DIR",
    "STATE_FILE",
    "CONFIG_FILE",
    # Configuration
    "WorkflowConfig",
    # Core
    "WorkflowGate",
    "WorkflowError",
    "WorkflowTransitionError",
    "WorkflowValidationError",
    "WorkflowGateBlockedError",
    "compute_git_diff_hash",
    "DelegationRules",
    # Git utilities
    "get_owner_repo",
    "gh_api",
    # Reviewers
    "ReviewerOrchestrator",
    "ReviewStatus",
    "ReviewResult",
    # PR workflow
    "PRWorkflowHandler",
    "CIStatus",
    # Subtasks
    "SubtaskOrchestrator",
    "SubtaskStatus",
    "AgentInstruction",
]

__version__ = "2.0.0"
