#!/bin/bash
# Validates docs/INDEX.md is complete and up-to-date
#
# Purpose: Ensure all markdown files in the repository are indexed in docs/INDEX.md
# Usage: ./scripts/validate_doc_index.sh
# Exit codes: 0 = all files indexed, 1 = missing files found

set -e

# Find all markdown files and normalize paths for comparison
# Exclude generated/cache directories: .venv, node_modules, .git, .pytest_cache
# Paths relative to docs/INDEX.md:
#   docs/FILE.md → ./FILE.md
#   docs/SUBDIR/FILE.md → ./SUBDIR/FILE.md
#   .claude/FILE.md → ../.claude/FILE.md
#   prompts/FILE.md → ../prompts/FILE.md
#   strategies/FILE.md → ../strategies/FILE.md
#   tests/FILE.md → ../tests/FILE.md
#   ./FILE.md (project root) → ../FILE.md

# Find all markdown files, excluding generated/cache directories
ALL_FILES=$(find . -name "*.md" -type f \
    -not -path "./.venv/*" \
    -not -path "./node_modules/*" \
    -not -path "./.git/*" \
    -not -path "./.pytest_cache/*" \
    2>/dev/null)

# Transform paths to be relative to docs/INDEX.md
ALL_MDS=$(echo "$ALL_FILES" | while read -r file; do
    if [[ "$file" =~ ^./docs/ ]]; then
        # docs/FILE.md → ./FILE.md
        echo "$file" | sed 's|^\./docs/|./|'
    elif [[ "$file" =~ ^\./ ]]; then
        # Any other ./X → ../X
        echo "$file" | sed 's|^\./|../|'
    fi
done | sort -u)

# Extract indexed files from INDEX.md
# Pattern matches markdown links: [text](path.md)
# Use non-greedy match to capture the path inside the first markdown link
INDEXED=$(grep -E '\[.*\]\(.*\.md\)' docs/INDEX.md | \
          sed -E 's/.*\[.*\]\(([^)]*\.md)\).*/\1/' | \
          sort -u)

# Compare: find files in filesystem but not in INDEX
MISSING=$(comm -23 <(echo "$ALL_MDS" | grep -v '^$') <(echo "$INDEXED"))

if [ -n "$MISSING" ]; then
    echo "ERROR: Following files not indexed in docs/INDEX.md:"
    echo "$MISSING"
    echo ""
    echo "Please add these files to docs/INDEX.md with proper metadata:"
    echo "  - [STATUS, YYYY-MM-DD, TYPE] [Filename.md](path) - Description"
    exit 1
else
    echo "✓ All markdown files are indexed in docs/INDEX.md"
    exit 0
fi
