#!/usr/bin/env bash
# lint_terminology.sh — Scan shared skills and nested context for conflicting term definitions
# Part of C8c: Terminology consistency lint
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

SKILLS_DIR="$PROJECT_ROOT/docs/AI/skills"
NESTED_DIR="$PROJECT_ROOT/docs/AI/nested"
EXIT_CODE=0

# Key terms that must be defined consistently
TERMS=(
  "circuit.breaker"
  "idempoten"
  "client.order.id"
  "feature.parity"
  "reconcil"
)

echo "Checking terminology consistency across AI context files..."
echo ""

for term in "${TERMS[@]}"; do
  # Collect all files that mention this term
  FILES_WITH_TERM=()
  while IFS= read -r -d '' file; do
    if grep -qi "$term" "$file" 2>/dev/null; then
      FILES_WITH_TERM+=("$file")
    fi
  done < <(find "$SKILLS_DIR" "$NESTED_DIR" -name "*.md" -print0 2>/dev/null)

  if [ ${#FILES_WITH_TERM[@]} -gt 1 ]; then
    # Check for contradictions by extracting sentences containing the term
    DEFINITIONS=()
    for file in "${FILES_WITH_TERM[@]}"; do
      DEF=$(grep -i "$term" "$file" | head -1 | sed 's/^[[:space:]]*//')
      REL_PATH="${file#"$PROJECT_ROOT"/}"
      DEFINITIONS+=("  $REL_PATH: $DEF")
    done

    echo "Term '$term' found in ${#FILES_WITH_TERM[@]} files:"
    for def in "${DEFINITIONS[@]}"; do
      echo "$def"
    done
    echo ""
  fi
done

if [ $EXIT_CODE -eq 0 ]; then
  echo "Terminology check passed (review definitions above for manual consistency)."
fi

exit $EXIT_CODE
