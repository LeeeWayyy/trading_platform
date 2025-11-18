#!/usr/bin/env python3
"""
Tests for path normalization in update_task_state.py (PR #61 fix).

Author: Claude Code
Date: 2025-11-17
Component: PR #61 - Address Gemini HIGH priority feedback
"""

from pathlib import Path

import pytest

from scripts.update_task_state import normalize_task_file_path


def test_normalize_absolute_path_inside_repo():
    """Verify absolute paths inside repo are converted to relative."""
    # Get project root dynamically to make the test robust
    project_root = Path(__file__).parent.parent.parent.resolve()
    absolute_path = project_root / "docs" / "TASKS" / "P1T1_TASK.md"

    result = normalize_task_file_path(str(absolute_path))

    # The result should be a relative path with POSIX separators
    assert result == "docs/TASKS/P1T1_TASK.md"


def test_normalize_already_relative_path():
    """Verify relative paths are returned unchanged."""
    relative_path = "docs/TASKS/P1T1_TASK.md"
    result = normalize_task_file_path(relative_path)

    assert result == relative_path


def test_normalize_dot_relative_path():
    """Verify ./ relative paths are normalized (. removed, forward slashes)."""
    relative_path = "./docs/TASKS/P1T1_TASK.md"
    result = normalize_task_file_path(relative_path)

    # Normalization removes "./" prefix and ensures forward slashes
    assert result == "docs/TASKS/P1T1_TASK.md"
    assert not Path(result).is_absolute()


def test_normalize_handles_path_outside_repo():
    """Verify paths outside repo don't crash (use os.path.relpath fallback)."""
    # Path clearly outside any repo
    outside_path = "/tmp/external_task.md"

    # Should not crash, returns some form of relative path
    result = normalize_task_file_path(outside_path)

    # Should return something (not crash)
    assert isinstance(result, str)
    assert len(result) > 0
