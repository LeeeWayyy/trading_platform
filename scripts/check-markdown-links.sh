#!/bin/bash
#
# Check markdown links locally before pushing
#
# Usage:
#   ./scripts/check-markdown-links.sh [path]
#
# Examples:
#   ./scripts/check-markdown-links.sh                    # Check all markdown files
#   ./scripts/check-markdown-links.sh .claude/workflows  # Check workflow files only
#   ./scripts/check-markdown-links.sh CLAUDE.md          # Check single file
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}📋 Checking markdown links...${NC}"

# Default to checking all markdown files if no path specified
TARGET_PATH="${1:-.}"

# Check if markdown-link-check is installed
if ! command -v markdown-link-check &> /dev/null; then
    echo -e "${RED}❌ markdown-link-check not found${NC}"
    echo ""
    echo "Install with:"
    echo "  npm install -g markdown-link-check"
    echo ""
    echo "Or run via npx (no install needed):"
    echo "  npx markdown-link-check README.md"
    exit 1
fi

# Find all markdown files in target path
echo -e "${YELLOW}Searching for markdown files in: ${TARGET_PATH}${NC}"
MARKDOWN_FILES=$(find "$TARGET_PATH" -name "*.md" -type f | sort)

if [ -z "$MARKDOWN_FILES" ]; then
    echo -e "${RED}❌ No markdown files found in ${TARGET_PATH}${NC}"
    exit 1
fi

# Count files
FILE_COUNT=$(echo "$MARKDOWN_FILES" | wc -l | tr -d ' ')
echo -e "${GREEN}Found ${FILE_COUNT} markdown file(s)${NC}"
echo ""

# Check each file
FAILED_FILES=()
TOTAL_CHECKED=0

for file in $MARKDOWN_FILES; do
    TOTAL_CHECKED=$((TOTAL_CHECKED + 1))
    echo -e "${YELLOW}[${TOTAL_CHECKED}/${FILE_COUNT}] Checking: ${file}${NC}"

    if markdown-link-check "$file" --config .github/markdown-link-check-config.json --quiet; then
        echo -e "${GREEN}✅ ${file}${NC}"
    else
        echo -e "${RED}❌ ${file}${NC}"
        FAILED_FILES+=("$file")
    fi
    echo ""
done

# Summary
echo "========================================="
if [ ${#FAILED_FILES[@]} -eq 0 ]; then
    echo -e "${GREEN}✅ All markdown links are valid!${NC}"
    echo "Checked $TOTAL_CHECKED file(s)"
    exit 0
else
    echo -e "${RED}❌ Found broken links in ${#FAILED_FILES[@]} file(s):${NC}"
    for file in "${FAILED_FILES[@]}"; do
        echo -e "${RED}  - $file${NC}"
    done
    echo ""
    echo "Common issues:"
    echo "  - Workflow files moved/renamed in .claude/workflows/"
    echo "  - Documentation files moved/renamed in docs/"
    echo "  - Broken cross-references between files"
    echo "  - External URLs changed or removed"
    echo ""
    echo "Fix the broken links and run this script again."
    exit 1
fi
