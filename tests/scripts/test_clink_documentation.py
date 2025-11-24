#!/usr/bin/env python3
"""
Test to validate clink tool name consistency in documentation.

Ensures all documentation uses the correct MCP tool name: mcp__zen__clink
(not the incorrect mcp__zen__clink typo).

Author: Claude Code
Date: 2025-11-08
"""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent


def get_documentation_files():
    """
    Get all documentation and code files that should use correct clink tool name.

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

    # Workflow scripts that mention zen-mcp
    files.append(PROJECT_ROOT / "scripts/workflow_gate.py")

    return [f for f in files if f.is_file()]


# Get all documentation files dynamically
DOC_FILES = get_documentation_files()


def test_correct_clink_tool_name_in_documentation():
    """Verify documentation uses correct clink tool name: mcp__zen__clink."""
    # NOTE: This test previously had a bug where it was checking for the correct
    # name and flagging it as wrong (lines 60 and 64 were identical).
    # The test is now updated to just verify the correct name exists where expected.

    # Verify key files use the correct tool name
    key_files = [
        PROJECT_ROOT / "CLAUDE.md",
        PROJECT_ROOT / "docs/AI/AI_GUIDE.md",
        PROJECT_ROOT / "docs/AI/Workflows/_common/zen-review-process.md",
    ]

    for doc_file in key_files:
        if not doc_file.exists():
            continue
        content = doc_file.read_text()
        assert "mcp__zen__clink" in content, f"{doc_file.name} should reference mcp__zen__clink"


def test_no_incorrect_clink_tool_name_typo():
    """Verify documentation doesn't contain the incorrect typo: mcp__zen-mcp__clink (dash instead of underscore)."""
    errors = []
    incorrect_pattern = "mcp__zen-mcp__clink"  # Note: dash between zen and mcp (typo)
    correct_pattern = "mcp__zen__clink"  # Correct: double underscore

    for doc_file in DOC_FILES:
        if not doc_file.exists():
            continue

        content = doc_file.read_text()

        if incorrect_pattern in content:
            count = content.count(incorrect_pattern)
            errors.append(
                f"{doc_file.relative_to(PROJECT_ROOT)}: Found {count} instance(s) of incorrect '{incorrect_pattern}' (should be '{correct_pattern}')"
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
                                f"{rel_path}: Found forbidden direct tool '{pattern}' outside warning context (use mcp__zen__clink instead)"
                            )

    if errors:
        pytest.fail("\n".join(["❌ Direct zen-mcp tool references found:"] + errors))


# Mark as unit test
pytestmark = pytest.mark.unit
