#!/usr/bin/env python3
"""
Check for broken links in markdown files.

Validates internal file links (relative paths) in all .md files.
"""

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
DOCS_DIR = PROJECT_ROOT / "docs"
ARCHIVE_DIR = DOCS_DIR / "ARCHIVE"


def extract_links(content: str, file_path: Path) -> list[tuple[str, int]]:
    """
    Extract markdown links from content.

    Returns:
        List of (link_target, line_number) tuples
    """
    links = []

    # Match [text](link) format
    pattern = r"\[([^\]]+)\]\(([^)]+)\)"

    for line_num, line in enumerate(content.split("\n"), 1):
        for match in re.finditer(pattern, line):
            link_target = match.group(2)

            # Skip anchors (#section), external URLs, and mailto
            if link_target.startswith(("#", "http://", "https://", "mailto:")):
                continue

            # Remove anchor from link (e.g., file.md#section -> file.md)
            link_target = link_target.split("#")[0]

            if link_target:  # Only add non-empty links
                links.append((link_target, line_num))

    return links


def resolve_link(link: str, source_file: Path) -> Path:
    """
    Resolve a relative link to an absolute path.

    Args:
        link: Relative link from markdown
        source_file: Path to the markdown file containing the link

    Returns:
        Absolute path to the linked file
    """
    # Links are relative to the source file's directory
    source_dir = source_file.parent

    # Handle ./ and ../ paths
    resolved = (source_dir / link).resolve()

    return resolved


def check_markdown_files() -> dict[str, list[tuple[str, int, str]]]:
    """
    Check all markdown files for broken links.

    Returns:
        Dictionary mapping file paths to list of (link, line_num, reason) tuples
    """
    broken_links: dict[str, list[tuple[str, int, str]]] = {}

    # Find all markdown files
    # NOTE: ARCHIVE_DIR is excluded because archived documents may contain
    # historical links that are no longer valid. Major broken links in the
    # archive have been fixed (P1_PLANNING.md, P2_PLANNING.md, etc.) but some
    # external or cross-reference links may still be stale. Consider re-enabling
    # archive checking once all links are validated. See PR #108 review comments.
    md_files = sorted(
        md_file for md_file in DOCS_DIR.rglob("*.md") if not md_file.is_relative_to(ARCHIVE_DIR)
    )

    print(f"🔍 Checking {len(md_files)} markdown files for broken links...\n")

    for md_file in md_files:
        with open(md_file, encoding="utf-8") as f:
            content = f.read()

        links = extract_links(content, md_file)

        for link, line_num in links:
            resolved_path = resolve_link(link, md_file)

            # Skip links pointing to ARCHIVE directory (historical docs with stale links)
            try:
                if resolved_path.is_relative_to(ARCHIVE_DIR):
                    continue
            except ValueError:
                pass  # Not relative to ARCHIVE_DIR, continue checking

            if not resolved_path.exists():
                # Check if it's a file that should exist
                if not resolved_path.is_absolute():
                    reason = "Relative path couldn't be resolved"
                elif not resolved_path.parent.exists():
                    reason = "Parent directory doesn't exist"
                else:
                    reason = "File not found"

                if md_file not in broken_links:
                    broken_links[md_file] = []

                broken_links[md_file].append((link, line_num, reason))

    return broken_links


def main():
    """Main entry point."""
    broken_links = check_markdown_files()

    if not broken_links:
        print("✅ All links are valid!")
        return 0

    print(f"❌ Found broken links in {len(broken_links)} file(s):\n")

    total_broken = 0
    for file_path, links in sorted(broken_links.items()):
        # Make path relative to PROJECT_ROOT for readability
        rel_path = file_path.relative_to(PROJECT_ROOT)
        print(f"\n📄 {rel_path}")
        print("-" * 80)

        for link, line_num, reason in links:
            print(f"  Line {line_num:4d}: {link}")
            print(f"              → {reason}")
            total_broken += 1

    print(f"\n❌ Total: {total_broken} broken link(s) in {len(broken_links)} file(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
