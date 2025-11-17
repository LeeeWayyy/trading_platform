#!/bin/bash
# Post-commit hook - Reset workflow state after successful commit
# CRITICAL: This ensures the 4-step pattern is enforced per-commit
#
# This hook runs AFTER a successful commit and:
# - Records the commit hash in workflow state
# - Resets zen_review status to empty (requires new review for next commit)
# - Resets ci_passed to false (requires new CI run for next commit)
# - Advances step back to "implement" (ready for next component)
#
# This prevents reusing a single review approval for multiple commits,
# which would defeat the entire workflow enforcement system.
#
# Installation:
#   make install-hooks
#
# Author: Claude Code
# Date: 2025-11-03

# Only run if workflow state exists (allows non-workflow commits)
WORKFLOW_STATE=".claude/workflow-state.json"
if [ ! -f "$WORKFLOW_STATE" ]; then
    exit 0
fi

# Record the commit and reset state for next component
# Set PYTHONPATH to allow imports from libs/
PYTHONPATH=. ./scripts/workflow_gate.py record-commit

# Exit successfully (don't block the commit)
exit 0
