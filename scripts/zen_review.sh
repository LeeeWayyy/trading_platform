#!/bin/bash
# Helper script for zen-mcp code reviews
# Usage: ./scripts/zen_review.sh [quick|deep]

set -e

MODE="${1:-quick}"

echo "🔍 Zen MCP Review (Mode: $MODE)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [ "$MODE" = "quick" ]; then
  # Quick mode: Review staged changes
  STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM | grep '\.py$' || true)

  if [ -z "$STAGED_FILES" ]; then
    echo "❌ No Python files staged for commit"
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
  echo "📝 Tell Claude Code to run quick comprehensive review:"
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo '"Use zen clink with codex codereviewer to review my staged changes for:'
  echo ''
  echo '**Trading Safety (CRITICAL):**'
  echo ' - Circuit breaker checks'
  echo ' - Idempotent order IDs'
  echo ' - Position limit validation'
  echo ' - Order state validation'
  echo ''
  echo '**Concurrency & Data Safety (HIGH):**'
  echo ' - Race conditions (Redis WATCH/MULTI/EXEC)'
  echo ' - Database transactions'
  echo ' - Atomic operations'
  echo ''
  echo '**Error Handling (HIGH):**'
  echo ' - Exception handling with context'
  echo ' - Logging completeness'
  echo ' - Error propagation'
  echo ''
  echo '**Code Quality (MEDIUM):**'
  echo ' - Type hints'
  echo ' - Data validation'
  echo ' - Resource cleanup'
  echo ''
  echo '**Security (HIGH):**'
  echo ' - Secrets handling'
  echo ' - SQL injection prevention'
  echo ' - Input validation'
  echo ''
  echo '**Configuration (MEDIUM):**'
  echo ' - DRY_RUN mode respect'
  echo ' - No hardcoded values'
  echo ''
  echo '**Standards (MEDIUM):**'
  echo ' - Docstrings complete'
  echo ' - Test coverage'
  echo ''
  echo '**Domain-Specific (HIGH):**'
  echo ' - Feature parity'
  echo ' - Timezone handling (UTC)'
  echo ' - API contract compliance'
  echo ''
  echo 'Focus on HIGH and CRITICAL severity issues."'
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
elif [ "$MODE" = "deep" ]; then
  echo "📝 Tell Claude Code to run comprehensive deep review:"
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo '"Use zen clink with codex codereviewer for comprehensive review of ALL branch changes.'
  echo ''
  echo 'IMPORTANT: Review all files changed in this branch (compare HEAD to origin/main),'
  echo 'NOT just staged files. The staging area may be clean - all commits are already made.'
  echo ''
  echo 'Check comprehensively:'
  echo ' - Overall architecture and design patterns'
  echo ' - Test coverage (unit, integration, edge cases)'
  echo ' - Edge cases and error handling'
  echo ' - Integration points with other services'
  echo ' - Documentation completeness (docstrings, ADRs, guides)'
  echo ' - Performance implications'
  echo ' - Security considerations'
  echo ' - Feature parity between research and production'
  echo ' - Idempotency guarantees'
  echo ' - Circuit breaker integration'
  echo ' - Type hints and data validation'
  echo ' - Concurrency safety (transactions, atomic operations)'
  echo ' - Configuration and environment handling'
  echo ' - Timezone handling (UTC timezone-aware)'
  echo ' - API contract compliance'
  echo ''
  echo 'Provide detailed analysis with severity levels (CRITICAL/HIGH/MEDIUM/LOW).'
  echo 'Be thorough - this is the final gate before PR creation."'
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
else
  echo "❌ Invalid mode: $MODE"
  echo "   Usage: ./scripts/zen_review.sh [quick|deep]"
  exit 1
fi

echo ""
echo "⚠️  MANDATORY: Do NOT commit until zen-mcp approves"
echo ""
