#!/bin/bash
# Comprehensive documentation path fix script
# Fixes all .claude/ and prompts/ references after docs migration

set -e

echo "===== Documentation Path Fix Script ====="
echo "This will update all references from .claude/ and prompts/ to docs/AI/"
echo ""

# Create backup
BACKUP_DIR="docs-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"
echo "Creating backup in $BACKUP_DIR..."
cp -r docs "$BACKUP_DIR/"
cp -r CLAUDE.md AGENTS.md "$BACKUP_DIR/" 2>/dev/null || true

echo "Starting fixes..."

# Function to replace paths in a file (portable sed - works on both BSD and GNU)
fix_file() {
    local file="$1"
    echo "  Fixing: $file"

    # Fix .claude/workflows/ references (context-sensitive)
    if [[ "$file" == *"/AI/Workflows/"* ]]; then
        # Inside AI/Workflows, use relative paths
        sed -i.bak 's|\.\.\/\.\.\/\.claude\/workflows\/|\.\/|g' "$file" && rm -f "${file}.bak"
        sed -i.bak 's|\.\.\/\.claude\/workflows\/|\.\/|g' "$file" && rm -f "${file}.bak"
        sed -i.bak 's|\.claude\/workflows\/|\.\/|g' "$file" && rm -f "${file}.bak"
    elif [[ "$file" == docs/* ]]; then
        # Inside docs/, use relative from docs/
        sed -i.bak 's|\.\.\/\.claude\/workflows\/|\.\/AI\/Workflows\/|g' "$file" && rm -f "${file}.bak"
        sed -i.bak 's|\.claude\/workflows\/|\.\/AI\/Workflows\/|g' "$file" && rm -f "${file}.bak"
    else
        # Outside docs/, use path from root
        sed -i.bak 's|\.claude\/workflows\/|docs\/AI\/Workflows\/|g' "$file" && rm -f "${file}.bak"
    fi

    # Fix other .claude/ subdirectories
    sed -i.bak 's|\.claude\/research\/|docs\/AI\/Research\/|g' "$file" && rm -f "${file}.bak"
    sed -i.bak 's|\.claude\/prompts\/|docs\/AI\/Prompts\/|g' "$file" && rm -f "${file}.bak"
    sed -i.bak 's|\.claude\/examples\/|docs\/AI\/Examples\/|g' "$file" && rm -f "${file}.bak"
    sed -i.bak 's|\.claude\/audits\/|docs\/AI\/Audits\/|g' "$file" && rm -f "${file}.bak"
    sed -i.bak 's|\.claude\/analysis\/|docs\/AI\/Analysis\/|g' "$file" && rm -f "${file}.bak"
    sed -i.bak 's|\.claude\/implementation-plans\/|docs\/AI\/Implementation\/|g' "$file" && rm -f "${file}.bak"

    # Fix prompts/ references
    if [[ "$file" == docs/* ]]; then
        sed -i.bak 's|prompts\/|\.\/AI\/Prompts\/|g' "$file" && rm -f "${file}.bak"
        sed -i.bak 's|\.\.\/prompts\/|\.\/AI\/Prompts\/|g' "$file" && rm -f "${file}.bak"
    else
        sed -i.bak 's|prompts\/|docs\/AI\/Prompts\/|g' "$file" && rm -f "${file}.bak"
    fi
}

# Fix critical files first
echo ""
echo "Phase 1: Critical Files"
fix_file "docs/STANDARDS/GIT_WORKFLOW.md"
fix_file "docs/Contributing/claude-integration.md"

# Fix all files in AI/Workflows/
echo ""
echo "Phase 2: AI/Workflows/*"
find docs/AI/Workflows -name "*.md" -type f | while read -r file; do
    fix_file "$file"
done

# Fix all files in TASKS/
echo ""
echo "Phase 3: docs/TASKS/*"
find docs/TASKS -name "*.md" -type f | while read -r file; do
    fix_file "$file"
done

# Fix remaining docs
echo ""
echo "Phase 4: Other documentation files"
for dir in ADRs CONCEPTS LESSONS_LEARNED RUNBOOKS GETTING_STARTED; do
    if [ -d "docs/$dir" ]; then
        find "docs/$dir" -name "*.md" -type f | while read -r file; do
            if grep -q "\.claude/\|prompts/" "$file" 2>/dev/null; then
                fix_file "$file"
            fi
        done
    fi
done

echo ""
echo "===== Summary ====="
echo "Backup created in: $BACKUP_DIR"
echo ""
echo "Checking remaining references..."
remaining=$(find docs -name "*.md" -type f -exec grep -l "\.claude/workflows/\|\.claude/research/\|\.claude/prompts/\|prompts/assistant\|prompts/implement" {} \; 2>/dev/null | wc -l | tr -d ' ')
echo "Files with old references remaining: $remaining"

if [ "$remaining" -eq "0" ]; then
    echo "✅ All references fixed!"
else
    echo "⚠️  Some references remain (may be intentional - check .claude/commands, .claude/state, etc.)"
    echo "Files:"
    find docs -name "*.md" -type f -exec grep -l "\.claude/workflows/\|\.claude/research/\|\.claude/prompts/\|prompts/assistant\|prompts/implement" {} \; 2>/dev/null | head -5
fi

echo ""
echo "Done! Review changes with: git diff docs/"
