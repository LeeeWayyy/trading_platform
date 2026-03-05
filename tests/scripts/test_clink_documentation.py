#!/usr/bin/env python3
"""
Test to validate AI reviewer invocation consistency in documentation.

Ensures documentation references direct CLI invocations (gemini, codex)
rather than deprecated mcp__pal__clink or incorrect mcp__zen-mcp__clink.

Author: Claude Code
Date: 2025-11-08
Updated: 2026-03-02 (C9: migrated from clink to direct CLI)
"""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent


def get_documentation_files():
    """
    Get all documentation and code files to check for reviewer references.

    Returns:
        List of Path objects for all relevant files.
    """
    files = []

    # Core documentation
    files.append(PROJECT_ROOT / "CLAUDE.md")

    # All .claude/ documentation (workflows, research, snippets, etc.)
    for pattern in ["**/*.md", "**/*.py"]:
        files.extend((PROJECT_ROOT / ".claude").glob(pattern))

    # All docs/ documentation
    for pattern in ["**/*.md"]:
        files.extend((PROJECT_ROOT / "docs").glob(pattern))

    return [f for f in files if f.is_file()]


# Get all documentation files dynamically
DOC_FILES = get_documentation_files()


def test_review_uses_direct_cli():
    """Verify review command uses direct CLI invocation, not deprecated clink."""
    review_file = PROJECT_ROOT / ".claude/commands/review.md"
    assert review_file.exists(), "review.md command must exist"
    content = review_file.read_text()

    # Should NOT reference clink
    assert "mcp__pal__clink" not in content, (
        "review.md should use direct CLI (gemini/codex), not deprecated mcp__pal__clink"
    )

    # Should reference direct CLI invocation patterns
    assert "gemini" in content.lower(), "review.md should reference gemini CLI"
    assert "codex" in content.lower(), "review.md should reference codex CLI"


def test_no_clink_in_commands():
    """Verify no .claude/commands/ files reference deprecated mcp__pal__clink."""
    commands_dir = PROJECT_ROOT / ".claude/commands"
    if not commands_dir.exists():
        pytest.skip("No commands directory")

    errors = []
    for cmd_file in commands_dir.glob("*.md"):
        if not cmd_file.is_file():
            continue
        content = cmd_file.read_text()
        if "mcp__pal__clink" in content:
            errors.append(
                f"{cmd_file.relative_to(PROJECT_ROOT)}: Still references deprecated mcp__pal__clink"
            )

    if errors:
        pytest.fail("\n".join(["❌ Deprecated clink references in commands:"] + errors))


def test_no_incorrect_clink_tool_name_typo():
    """Verify documentation doesn't contain incorrect clink tool name variants."""
    errors = []
    incorrect_pattern = "mcp__zen-mcp__clink"  # Note: dash between zen and mcp (typo)

    for doc_file in DOC_FILES:
        if not doc_file.exists():
            continue

        content = doc_file.read_text()

        if incorrect_pattern in content:
            count = content.count(incorrect_pattern)
            errors.append(
                f"{doc_file.relative_to(PROJECT_ROOT)}: Found {count} instance(s) of incorrect '{incorrect_pattern}'"
            )

    if errors:
        pytest.fail(
            "\n".join([f"❌ Clink tool name typo found in {len(errors)} file(s):"] + errors)
        )


def test_no_direct_zen_mcp_tool_references():
    """Verify documentation doesn't incorrectly suggest using direct zen-mcp tools (outside of warning examples)."""
    # These are the direct tool names that should NOT be used
    forbidden_patterns = [
        "mcp__zen__chat",
        "mcp__zen__thinkdeep",
        "mcp__zen__codereview",
        "mcp__zen__debug",
        "mcp__zen__consensus",
        "mcp__zen__planner",
    ]

    errors = []

    for doc_file in DOC_FILES:
        if not doc_file.exists():
            continue

        content = doc_file.read_text()

        for pattern in forbidden_patterns:
            if pattern in content:
                # Check if it's in a "WRONG" or "INCORRECT" context
                lines = content.split("\n")
                for i, line in enumerate(lines):
                    if pattern in line:
                        # Check broader surrounding context (10 lines before/after to catch section headers)
                        start_idx = max(0, i - 10)
                        end_idx = min(len(lines), i + 11)
                        context = "\n".join(lines[start_idx:end_idx])

                        # Keywords that indicate this is a warning/example of what NOT to do
                        warning_keywords = [
                            "wrong",
                            "incorrect",
                            "never",
                            "do not",
                            "don't",
                            "forbidden",
                            "❌",
                            "bad example",
                            "avoid",
                            "anti-pattern",
                            "not recommended",
                            "deprecated",
                        ]

                        # Only flag if NOT in a warning context
                        if not any(keyword in context.lower() for keyword in warning_keywords):
                            rel_path = doc_file.relative_to(PROJECT_ROOT)
                            errors.append(
                                f"{rel_path}: Found forbidden direct tool '{pattern}' outside warning context"
                            )

    if errors:
        pytest.fail("\n".join(["❌ Direct zen-mcp tool references found:"] + errors))


# Mark as unit test
pytestmark = pytest.mark.unit
