#!/bin/bash
# Verify branch naming convention follows PxTy(-Fz)? pattern
#
# Valid examples:
#   - feature/P0T1-initial-setup
#   - feature/P1T11-workflow-optimization
#   - feature/P1T11-F1-tool-restriction
#   - bugfix/P0T2-fix-circuit-breaker
#
# Exit codes:
#   0 - Branch name valid
#   1 - Branch name invalid

set -euo pipefail

# Get current branch name
BRANCH=$(git branch --show-current)

# Branch naming pattern: feature/PxTy(-Fz)?-description or bugfix/PxTy(-Fz)?-description
# Allowed branch types: feature, bugfix, hotfix
PATTERN="^(feature|bugfix|hotfix)/P[0-9]+T[0-9]+(-F[0-9]+)?-[a-z0-9-]+$"

# Check if branch matches pattern
if [[ $BRANCH =~ $PATTERN ]]; then
    exit 0
else
    echo "❌ Invalid branch name: $BRANCH"
    echo ""
    echo "Branch name must follow pattern:"
    echo "  <type>/PxTy(-Fz)?-<description>"
    echo ""
    echo "Valid examples:"
    echo "  feature/P0T1-initial-setup"
    echo "  feature/P1T11-workflow-optimization"
    echo "  feature/P1T11-F1-tool-restriction"
    echo "  bugfix/P0T2-fix-circuit-breaker"
    echo ""
    echo "Where:"
    echo "  • type: feature, bugfix, or hotfix"
    echo "  • Px: Phase number (P0, P1, P2, etc.)"
    echo "  • Ty: Task number within phase (T1, T11, etc.)"
    echo "  • Fz: Optional feature/subfeature number (F1, F2, etc.)"
    echo "  • description: Lowercase kebab-case description"
    echo ""
    echo "💡 See task breakdown workflow: .claude/workflows/00-task-breakdown.md"
    echo ""
    exit 1
fi
