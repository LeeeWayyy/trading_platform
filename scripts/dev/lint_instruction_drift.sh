#!/usr/bin/env bash
# lint_instruction_drift.sh — Detect nested context files duplicating root guide keywords
# Part of C8f: Instruction-drift lint
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

GUIDE="$PROJECT_ROOT/docs/AI/AI_GUIDE.md"
NESTED_DIR="$PROJECT_ROOT/docs/AI/nested"
EXIT_CODE=0

# Phrases that should only appear in the root guide, not duplicated in nested context.
# Skills (docs/AI/skills/) are excluded — they are specialized workflow definitions
# that legitimately reference root-level concepts like ZEN_REVIEW_OVERRIDE.
ROOT_ONLY_PHRASES=(
  "ZEN_REVIEW_OVERRIDE"
  "ci-local.lock"
  "git commit --no-verify"
  "make ci-local"
)

echo "Checking for instruction drift (nested context files duplicating root guide)..."
echo ""

for phrase in "${ROOT_ONLY_PHRASES[@]}"; do
  # Check nested context files only (not skills — skills legitimately reference these)
  while IFS= read -r -d '' file; do
    if grep -Fq "$phrase" "$file" 2>/dev/null; then
      REL_PATH="${file#"$PROJECT_ROOT"/}"
      echo "DRIFT: '$phrase' found in $REL_PATH (should only be in root AI_GUIDE.md)"
      EXIT_CODE=1
    fi
  done < <(find "$NESTED_DIR" -name "*.md" -print0 2>/dev/null)
done

if [ $EXIT_CODE -eq 0 ]; then
  echo "No instruction drift detected."
fi

exit $EXIT_CODE
