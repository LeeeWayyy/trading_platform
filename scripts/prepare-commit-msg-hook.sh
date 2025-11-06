#!/bin/bash
# Prepare-commit-msg hook - Automatically add workflow gate review markers
# This hook adds zen-mcp review markers to commit messages for CI verification
#
# Installation:
#   make install-hooks
#
# Author: Claude Code
# Date: 2025-11-03

COMMIT_MSG_FILE=$1
COMMIT_SOURCE=$2
SHA1=$3

# Only process regular commits (not merges, squashes, etc.)
if [ -n "$COMMIT_SOURCE" ] && [ "$COMMIT_SOURCE" != "message" ]; then
    exit 0
fi

# Path to workflow state
WORKFLOW_STATE=".claude/workflow-state.json"

# Check if workflow state exists
if [ ! -f "$WORKFLOW_STATE" ]; then
    # No workflow state - this might be a documentation-only commit
    # or first commit before workflow system is active
    exit 0
fi

# Extract review information using Python (more reliable than jq/grep)
# Use poetry run python for consistency with project tooling
REVIEW_INFO=$(poetry run python - "$WORKFLOW_STATE" <<'EOF'
import json
import sys

try:
    workflow_state_file = sys.argv[1]
    with open(workflow_state_file, 'r') as f:
        state = json.load(f)

    zen_review = state.get('zen_review', {})
    continuation_id = zen_review.get('continuation_id', '')
    status = zen_review.get('status', '')

    if status == 'APPROVED' and continuation_id:
        print(f'{status}|{continuation_id}')
    else:
        sys.exit(1)
except Exception:
    sys.exit(1)
EOF
)

# If no approved review found, exit silently
if [ $? -ne 0 ] || [ -z "$REVIEW_INFO" ]; then
    exit 0
fi

# Parse review info
STATUS=$(echo "$REVIEW_INFO" | cut -d'|' -f1)
CONTINUATION_ID=$(echo "$REVIEW_INFO" | cut -d'|' -f2)

# Check if markers already exist in commit message
if grep -qi "zen-mcp-review:" "$COMMIT_MSG_FILE" && grep -qi "continuation-id:" "$COMMIT_MSG_FILE"; then
    # Markers already present, don't add duplicates
    exit 0
fi

# Append review markers to commit message
# Always add a single blank line for proper separation
echo "" >> "$COMMIT_MSG_FILE"
echo "zen-mcp-review: approved" >> "$COMMIT_MSG_FILE"
echo "continuation-id: $CONTINUATION_ID" >> "$COMMIT_MSG_FILE"

exit 0
