#!/bin/bash
# Helper script for clink-based zen-mcp code reviews
# Usage: ./scripts/zen_review.sh [quick|deep]

set -e

MODE="${1:-quick}"

echo "🔍 Clink-Based Zen-MCP Review (Mode: $MODE)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [ "$MODE" = "quick" ]; then
  # Quick mode: Review staged changes (all file types)

  # Execute git diff and capture exit status
  STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM 2>&1)
  GIT_EXIT=$?

  # Check for git command errors (non-zero exit code)
  if [ $GIT_EXIT -ne 0 ]; then
    echo "❌ Git command failed: $STAGED_FILES"
    echo "   Are you in a git repository?"
    exit 1
  fi

  # Check if any files are staged
  if [ -z "$STAGED_FILES" ]; then
    echo "❌ No files staged for commit"
    echo "   Use: git add <files>"
    exit 1
  fi

  echo "Files staged: $(echo $STAGED_FILES | wc -w | tr -d ' ')"
  echo ""
  echo "Staged files:"
  echo "$STAGED_FILES" | tr ' ' '\n' | sed 's/^/  - /'
  echo ""

elif [ "$MODE" = "deep" ]; then
  # Deep mode: Review all branch changes (even with clean staging area)

  # Check current branch
  CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>&1)
  GIT_EXIT=$?

  if [ $GIT_EXIT -ne 0 ]; then
    echo "❌ Git command failed: $CURRENT_BRANCH"
    echo "   Are you in a git repository?"
    exit 1
  fi

  if [ "$CURRENT_BRANCH" = "main" ] || [ "$CURRENT_BRANCH" = "master" ]; then
    echo "⚠️  Already on $CURRENT_BRANCH branch"
    echo "   Create a feature branch first: git checkout -b feature/name"
    exit 1
  fi

  # Get branch changes
  BRANCH_FILES=$(git diff origin/master...HEAD --name-only --diff-filter=ACM 2>&1)
  GIT_EXIT=$?

  # Check for git command errors
  if [ $GIT_EXIT -ne 0 ]; then
    echo "❌ Git command failed: $BRANCH_FILES"
    echo "   Possible causes:"
    echo "   - origin/master doesn't exist (try: git fetch origin)"
    echo "   - Not in a git repository"
    exit 1
  fi

  # Check if any changes exist
  if [ -z "$BRANCH_FILES" ]; then
    echo "❌ No changes in this branch vs origin/master"
    exit 1
  fi

  echo "Branch: $CURRENT_BRANCH"
  echo "Files changed: $(echo $BRANCH_FILES | wc -w | tr -d ' ')"
  echo ""
  echo "Changed files:"
  echo "$BRANCH_FILES" | tr ' ' '\n' | sed 's/^/  - /'
  echo ""
fi

if [ "$MODE" = "quick" ]; then
  echo "📝 Tell Claude Code:"
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  echo "   \"Review my staged changes using clink + codex codereviewer\""
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  echo "ℹ️  Full quick review workflow: .claude/workflows/03-zen-review-quick.md"
elif [ "$MODE" = "deep" ]; then
  echo "📝 Tell Claude Code (Two-Phase Workflow):"
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  echo "   Phase 1: \"Review all branch changes using clink + gemini planner. Compare master..HEAD.\""
  echo "   Phase 2: \"Now use clink + codex planner with continuation_id to synthesize recommendations\""
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  echo "ℹ️  Full deep review workflow: .claude/workflows/04-zen-review-deep.md"
else
  echo "❌ Invalid mode: $MODE"
  echo "   Usage: ./scripts/zen_review.sh [quick|deep]"
  exit 1
fi

echo ""
echo "⚠️  MANDATORY: Do NOT commit until clink review approves"
echo ""
