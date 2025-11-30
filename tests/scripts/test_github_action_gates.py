#!/usr/bin/env python3
"""
Tests for Component 2: GitHub Action Enforcement (P1T13-F5a).

Tests the Review-Hash trailer verification system that runs in CI.
Uses mocking for fast, reliable tests.

Author: Claude Code
Date: 2025-11-13
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.verify_gate_compliance import has_review_markers


class TestReviewHashTrailer:
    """Test Review-Hash trailer verification (Component 2)."""

    def test_commit_with_review_hash_passes(self):
        """Test that commit with Review-Hash trailer passes verification (DUAL REVIEW FORMAT)."""
        commit_hash = "abc123"
        commit_message = """feat: Add new feature

Some description.

zen-mcp-review: approved
gemini-continuation-id: 12345678-1234-1234-1234-123456789abc
codex-continuation-id: 87654321-4321-4321-4321-cba987654321
Review-Hash: 530744c33ff1687e6de14733414a7dd23afd1c28ca4cc3e37f7136f994599943

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
"""
        with patch(
            "scripts.verify_gate_compliance.get_commit_message", return_value=commit_message
        ):
            assert has_review_markers(commit_hash) is True

    def test_commit_without_review_hash_fails(self):
        """Test that commit without Review-Hash trailer fails (Component 2 gate)."""
        commit_hash = "def456"
        # Has approval + continuation ID but missing Review-Hash
        commit_message = """feat: Add new feature

Some description.

zen-mcp-review: approved
continuation-id: 12345678-1234-1234-1234-123456789abc

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
"""
        with patch(
            "scripts.verify_gate_compliance.get_commit_message", return_value=commit_message
        ):
            assert has_review_markers(commit_hash) is False

    def test_deep_review_with_review_hash_passes(self):
        """Test deep review (gemini + codex) with Review-Hash passes."""
        commit_hash = "ghi789"
        commit_message = """feat: Add complex feature

zen-mcp-review: approved
gemini-continuation-id: aaaa-bbbb-cccc
codex-continuation-id: dddd-eeee-ffff
Review-Hash: a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2

🤖 Generated with [Claude Code](https://claude.com/claude-code)
"""
        with patch(
            "scripts.verify_gate_compliance.get_commit_message", return_value=commit_message
        ):
            assert has_review_markers(commit_hash) is True

    def test_deep_review_without_review_hash_fails(self):
        """Test deep review without Review-Hash fails."""
        commit_hash = "jkl012"
        # Has approval + dual continuation IDs but missing Review-Hash
        commit_message = """feat: Add complex feature

zen-mcp-review: approved
gemini-continuation-id: aaaa-bbbb-cccc
codex-continuation-id: dddd-eeee-ffff

🤖 Generated with [Claude Code](https://claude.com/claude-code)
"""
        with patch(
            "scripts.verify_gate_compliance.get_commit_message", return_value=commit_message
        ):
            assert has_review_markers(commit_hash) is False

    def test_review_hash_presence_not_value(self):
        """Test that we only check presence of Review-Hash, not correctness (DUAL REVIEW FORMAT)."""
        commit_hash = "mno345"
        # Review-Hash value is wrong hex, but presence check passes (we don't verify correctness)
        commit_message = """feat: Add feature

zen-mcp-review: approved
gemini-continuation-id: 12345678-1234-1234-1234-123456789abc
codex-continuation-id: 87654321-4321-4321-4321-cba987654321
Review-Hash: ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff

🤖 Generated with [Claude Code](https://claude.com/claude-code)
"""
        with patch(
            "scripts.verify_gate_compliance.get_commit_message", return_value=commit_message
        ):
            # Should pass - we don't validate hash correctness
            assert has_review_markers(commit_hash) is True

    def test_case_insensitive_review_hash(self):
        """Test that Review-Hash check is case-insensitive (DUAL REVIEW FORMAT)."""
        commit_hash = "pqr678"
        commit_message = """feat: Add feature

zen-mcp-review: approved
gemini-continuation-id: 12345678-1234-1234-1234-123456789abc
codex-continuation-id: 87654321-4321-4321-4321-cba987654321
REVIEW-HASH: 530744c33ff1687e6de14733414a7dd23afd1c28ca4cc3e37f7136f994599943

🤖 Generated with [Claude Code](https://claude.com/claude-code)
"""
        with patch(
            "scripts.verify_gate_compliance.get_commit_message", return_value=commit_message
        ):
            assert has_review_markers(commit_hash) is True

    def test_legacy_markers_with_review_hash(self):
        """Test legacy gemini-review/codex-review markers with Review-Hash."""
        commit_hash = "stu901"
        commit_message = """feat: Add feature (legacy markers)

zen-mcp-review: approved
gemini-review: aaaa-bbbb
codex-review: cccc-dddd
Review-Hash: 9f8e7d6c5b4a9f8e7d6c5b4a9f8e7d6c5b4a9f8e7d6c5b4a9f8e7d6c5b4a9f8e

🤖 Generated with [Claude Code](https://claude.com/claude-code)
"""
        with patch(
            "scripts.verify_gate_compliance.get_commit_message", return_value=commit_message
        ):
            assert has_review_markers(commit_hash) is True

    def test_all_markers_missing_fails(self):
        """Test that commit with no markers at all fails."""
        commit_hash = "vwx234"
        commit_message = """feat: Add feature without any review markers

Just a regular commit message.
"""
        with patch(
            "scripts.verify_gate_compliance.get_commit_message", return_value=commit_message
        ):
            assert has_review_markers(commit_hash) is False


# Mark as unit test
pytestmark = pytest.mark.unit
