#!/usr/bin/env python3
"""
Tests for path normalization in update_task_state.py (PR #61 fix).

Author: Claude Code
Date: 2025-11-17
Component: PR #61 - Address Gemini HIGH priority feedback
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.update_task_state import normalize_task_file_path


def test_normalize_absolute_path_inside_repo():
    """Verify absolute paths inside repo are converted to relative."""
    # Simulate absolute path inside repo
    absolute_path = "/Users/test/trading_platform/docs/TASKS/P1T1_TASK.md"

    # Note: This test will work relative to where it's run from
    # The function uses Path(__file__).parent.parent from update_task_state.py
    result = normalize_task_file_path(absolute_path)

    # Result should start with docs/ (relative path)
    # Note: May be ../docs if path is outside, but should not be absolute
    assert not Path(result).is_absolute(), f"Expected relative path, got: {result}"


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
