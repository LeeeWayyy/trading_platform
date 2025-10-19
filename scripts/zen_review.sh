#!/bin/bash
# Helper script for zen-mcp code reviews
# Usage: ./scripts/zen_review.sh [quick|deep]

set -e

MODE="${1:-quick}"

echo "🔍 Zen MCP Review (Mode: $MODE)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [ "$MODE" = "quick" ]; then
  # Quick mode: Review staged changes (all file types)
  STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM || true)

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
  CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)

  if [ "$CURRENT_BRANCH" = "main" ] || [ "$CURRENT_BRANCH" = "master" ]; then
    echo "⚠️  Already on $CURRENT_BRANCH branch"
    echo "   Create a feature branch first: git checkout -b feature/name"
    exit 1
  fi

  BRANCH_FILES=$(git diff origin/main...HEAD --name-only --diff-filter=ACM || true)

  if [ -z "$BRANCH_FILES" ]; then
    echo "❌ No changes in this branch vs origin/main"
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
  echo "   Use slash command: /zen-review quick"
  echo "   Or say: \"Review my staged changes with zen-mcp\""
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  echo "ℹ️  Full review criteria in: .claude/commands/zen-review.md"
elif [ "$MODE" = "deep" ]; then
  echo "📝 Tell Claude Code:"
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  echo "   Use slash command: /zen-review deep"
  echo "   Or say: \"Deep review all branch changes with zen-mcp\""
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  echo "ℹ️  Full comprehensive review criteria in: .claude/commands/zen-review.md"
else
  echo "❌ Invalid mode: $MODE"
  echo "   Usage: ./scripts/zen_review.sh [quick|deep]"
  exit 1
fi

echo ""
echo "⚠️  MANDATORY: Do NOT commit until zen-mcp approves"
echo ""
