#!/bin/bash
# Commit-msg hook to enforce zen-mcp review approval before commits.
#
# This hook runs AFTER the commit message is created but BEFORE the commit is finalized.
# It checks that feature branches have zen-mcp review approval in the commit message.
#
# To install this hook, run: make install-hooks
# To bypass the hook temporarily, use: git commit --no-verify (NOT RECOMMENDED)

set -euo pipefail  # Exit on error, undefined vars, and pipe failures

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get commit message file (passed as first argument)
COMMIT_MSG_FILE="$1"

# ==============================================================================
# QUALITY GATE 0: Zen-mcp Review Approval (MANDATORY for feature branches)
# ==============================================================================
# This is the PRIMARY root cause prevention from CI failure analysis.
# Skipping review gates caused 7 fix commits and 10-15 hours of wasted time.

# Get branch name (handles both normal and detached HEAD scenarios)
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")

# If detached HEAD (during rebase, etc.), try to get the original branch name
if [[ "$CURRENT_BRANCH" == "HEAD" ]] || [[ -z "$CURRENT_BRANCH" ]]; then
    # Get actual git directory (works with worktrees where .git is a file)
    GIT_DIR=$(git rev-parse --git-dir 2>/dev/null || echo ".git")

    # Check for rebase in progress
    if [ -f "$GIT_DIR/rebase-merge/head-name" ]; then
        CURRENT_BRANCH=$(cat "$GIT_DIR/rebase-merge/head-name" | sed 's|refs/heads/||')
    elif [ -f "$GIT_DIR/rebase-apply/head-name" ]; then
        CURRENT_BRANCH=$(cat "$GIT_DIR/rebase-apply/head-name" | sed 's|refs/heads/||')
    else
        # Try to get branch that points at HEAD (handles detached states like git switch --detach)
        CURRENT_BRANCH=$(git for-each-ref --points-at HEAD --format='%(refname:short)' refs/heads refs/remotes 2>/dev/null | head -n1 || echo "")

        # If still empty, try upstream branch
        if [[ -z "$CURRENT_BRANCH" ]]; then
            CURRENT_BRANCH=$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null || echo "")
        fi
    fi
fi

# Normalize branch name: strip remote prefixes (refs/remotes/<remote>/, refs/heads/, <remote>/)
# This ensures remote-tracking refs like "origin/feature/foo" or "fork/feature/foo" match "feature/*" pattern
CURRENT_BRANCH=$(echo "$CURRENT_BRANCH" | sed -E 's#^refs/remotes/[^/]+/##; s#^refs/heads/##; s#^[^/]+/(feature|fix|bugfix)/#\1/#')

# Check if this is a feature branch (feature/*, fix/*, etc.)
if [[ "$CURRENT_BRANCH" == feature/* ]] || [[ "$CURRENT_BRANCH" == fix/* ]] || [[ "$CURRENT_BRANCH" == bugfix/* ]]; then
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}🔒 QUALITY GATE 0: Zen-mcp Review Approval${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "Branch: $CURRENT_BRANCH (requires zen-mcp review)"
    echo ""

    # Check if commit message contains review approval marker
    if grep -q "zen-mcp-review: approved" "$COMMIT_MSG_FILE"; then
        echo -e "${GREEN}✓ Zen-mcp review approval found in commit message${NC}"
        echo ""
        exit 0
    else
        echo -e "${RED}✗ COMMIT BLOCKED: Missing zen-mcp review approval${NC}"
        echo ""
        echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${RED}⚠️  CRITICAL PROCESS VIOLATION${NC}"
        echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo ""
        echo "Feature branch commits require zen-mcp review approval."
        echo ""
        echo "Why this matters:"
        echo "  • Skipping review gates caused 7 fix commits (10-15 hours wasted)"
        echo "  • This is the PRIMARY root cause of CI failures"
        echo "  • Review gates are MANDATORY, never skip regardless of urgency"
        echo ""
        echo "Required workflow:"
        echo "  1. Complete your changes"
        echo "  2. Run: .claude/workflows/03-zen-review-quick.md (MANDATORY)"
        echo "  3. Fix any issues found in review"
        echo "  4. Add review approval marker to commit message:"
        echo ""
        echo "     zen-mcp-review: approved"
        echo "     continuation-id: <your-continuation-id>"
        echo ""
        echo "  5. Commit after review approval"
        echo ""
        echo "Example commit message:"
        echo ""
        echo "  Fix position limit validation"
        echo ""
        echo "  - Add validation for max position size"
        echo "  - Update tests for new validation"
        echo ""
        echo "  zen-mcp-review: approved"
        echo "  continuation-id: abc123..."
        echo ""
        echo "To proceed:"
        echo "  1. Amend your commit message to include review approval"
        echo "  2. Or cancel (Ctrl+C), get review, and commit again"
        echo ""
        echo "To bypass (NOT RECOMMENDED):"
        echo "  git commit --no-verify"
        echo ""
        exit 1
    fi
fi

# Non-feature branches don't need review approval
exit 0
