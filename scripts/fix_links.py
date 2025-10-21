#!/usr/bin/env python3
"""
Fix broken links in markdown files after documentation reorganization.

Handles:
- IMPLEMENTATION_GUIDES → TASKS migration
- P0_TICKETS.md → P0_TASKS.md rename
- ADR renames
- Archived file references
"""

import re
from pathlib import Path
from typing import Dict

PROJECT_ROOT = Path(__file__).parent.parent
DOCS_DIR = PROJECT_ROOT / "docs"

# Mapping of old implementation guide paths to new task files
IMPL_GUIDE_MAP = {
    "IMPLEMENTATION_GUIDES/p0t1-data-etl.md": "TASKS/P0T1_DONE.md",
    "IMPLEMENTATION_GUIDES/p0t2-baseline-strategy.md": "TASKS/P0T2_DONE.md",
    "IMPLEMENTATION_GUIDES/p0t3-signal-service.md": "TASKS/P0T3_DONE.md",
    "IMPLEMENTATION_GUIDES/p0t3-p4-fastapi-application.md": "TASKS/P0T3-F4_DONE.md",
    "IMPLEMENTATION_GUIDES/p0t3-p5-hot-reload.md": "TASKS/P0T3-F5_DONE.md",
    "IMPLEMENTATION_GUIDES/p0t3-p6-integration-tests.md": "TASKS/P0T3-F6_DONE.md",
    "IMPLEMENTATION_GUIDES/p0t4-execution-gateway.md": "TASKS/P0T4_DONE.md",
    "IMPLEMENTATION_GUIDES/p0t5-orchestrator.md": "TASKS/P0T5_DONE.md",
    "IMPLEMENTATION_GUIDES/p0t6-paper-run.md": "TASKS/P0T6_DONE.md",
    "IMPLEMENTATION_GUIDES/p1.1t2-redis-integration.md": "TASKS/P1T1_DONE.md",
    "IMPLEMENTATION_GUIDES/workflow-optimization-zen-mcp.md": "CONCEPTS/workflow-optimization-zen-mcp.md",
    "IMPLEMENTATION_GUIDES/p1.2t3-risk-management.md": "TASKS/P1T7_DONE.md",
}

# Other link fixes
OTHER_FIXES = {
    "TASKS/P0_TICKETS.md": "TASKS/P0_TASKS.md",
    "P0_TICKETS.md": "P0_TASKS.md",
    "NEXT_TASK.md": "ARCHIVE/NEXT_TASK_20241021.md",
    "FILE_RENAME_MAP.md": "ARCHIVE/FILE_RENAME_MAP_20241018.md",
    "GETTING_STARTED/P1_PROGRESS.md": "ARCHIVE/P1_PROGRESS_20241021.md",
    "P1_PROGRESS.md": "ARCHIVE/P1_PROGRESS_20241021.md",
    "GIT_WORKFLOW.md": "STANDARDS/GIT_WORKFLOW.md",
    "ADRs/0005-execution-gateway-design.md": "ADRs/0005-execution-gateway-architecture.md",
    "ADRs/0006-orchestrator-architecture.md": "ADRs/0006-orchestrator-service.md",
}

# Directory references (just need to exist, so map to index or main file)
DIRECTORY_REFS = {
    "IMPLEMENTATION_GUIDES/": "TASKS/",
}


def fix_link_in_line(line: str, file_path: Path) -> str:
    """
    Fix broken links in a single line.

    Args:
        line: Line of markdown
        file_path: Path to the markdown file (for context)

    Returns:
        Fixed line
    """
    # Pattern for markdown links: [text](link)
    pattern = r'\[([^\]]+)\]\(([^)]+)\)'

    def replace_link(match):
        text = match.group(1)
        link = match.group(2)
        original_link = link

        # Skip external URLs and anchors
        if link.startswith(('#', 'http://', 'https://', 'mailto:')):
            return match.group(0)

        # Preserve anchor part
        anchor = ""
        if '#' in link:
            link, anchor = link.split('#', 1)
            anchor = f'#{anchor}'

        # Try implementation guide mapping
        for old_path, new_path in IMPL_GUIDE_MAP.items():
            if old_path in link:
                link = link.replace(old_path, new_path)
                return f'[{text}]({link}{anchor})'

        # Try other fixes
        for old_ref, new_ref in OTHER_FIXES.items():
            if link.endswith(old_ref) or f'/{old_ref}' in link:
                link = link.replace(old_ref, new_ref)
                return f'[{text}]({link}{anchor})'

        # Try directory references
        for old_dir, new_dir in DIRECTORY_REFS.items():
            if old_dir in link:
                link = link.replace(old_dir, new_dir)
                return f'[{text}]({link}{anchor})'

        # Return original if no fix found
        return match.group(0)

    return re.sub(pattern, replace_link, line)


def fix_file(file_path: Path) -> int:
    """
    Fix broken links in a single file.

    Returns:
        Number of changes made
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    new_lines = []
    changes = 0

    for line in lines:
        new_line = fix_link_in_line(line, file_path)
        if new_line != line:
            changes += 1
        new_lines.append(new_line)

    if changes > 0:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)

    return changes


def main():
    """Main entry point."""
    print("🔧 Fixing broken links in markdown files...\n")

    # Find all markdown files (excluding archived and template files)
    md_files = []
    for md_file in sorted(DOCS_DIR.rglob("*.md")):
        # Skip archived files (they're historical, links expected to be broken)
        if "ARCHIVE" in str(md_file):
            continue
        # Skip template files (they have placeholder links)
        if md_file.name.startswith("00-TEMPLATE"):
            continue
        md_files.append(md_file)

    print(f"📝 Processing {len(md_files)} markdown files...\n")

    total_changes = 0
    files_modified = 0

    for md_file in md_files:
        changes = fix_file(md_file)
        if changes > 0:
            rel_path = md_file.relative_to(PROJECT_ROOT)
            print(f"  ✅ {rel_path}: {changes} link(s) fixed")
            total_changes += changes
            files_modified += 1

    print(f"\n✅ Fixed {total_changes} broken link(s) in {files_modified} file(s)")

    if files_modified > 0:
        print(f"\n📝 Next steps:")
        print(f"   1. Review changes: git diff")
        print(f"   2. Verify links: python3 scripts/check_links.py")
        print(f"   3. Commit: git add . && git commit")


if __name__ == "__main__":
    main()
