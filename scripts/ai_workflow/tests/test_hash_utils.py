"""
Tests for hash_utils.py module.

Tests git diff hashing for workflow enforcement.
"""

import hashlib
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from ai_workflow.hash_utils import (
    compute_git_diff_hash,
    is_merge_commit,
)


class TestIsMergeCommit:
    """Tests for is_merge_commit function."""

    def test_detects_regular_commit(self):
        """Should return False for regular (non-merge) commit."""
        # Regular commit has format: "<sha> <parent1_sha>"
        mock_result = MagicMock(returncode=0, stdout="abc123 def456\n", stderr="")

        with patch("subprocess.run", return_value=mock_result):
            result = is_merge_commit("abc123")

        assert result is False

    def test_detects_merge_commit(self):
        """Should return True for merge commit."""
        # Merge commit has format: "<sha> <parent1_sha> <parent2_sha>"
        mock_result = MagicMock(returncode=0, stdout="abc123 def456 ghi789\n", stderr="")

        with patch("subprocess.run", return_value=mock_result):
            result = is_merge_commit("abc123")

        assert result is True

    def test_detects_octopus_merge(self):
        """Should return True for octopus merge (3+ parents)."""
        # Octopus merge has multiple parents
        mock_result = MagicMock(returncode=0, stdout="abc123 def456 ghi789 jkl012\n", stderr="")

        with patch("subprocess.run", return_value=mock_result):
            result = is_merge_commit("abc123")

        assert result is True

    def test_raises_on_git_error(self):
        """Should raise CalledProcessError on git failure."""
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["git"], stderr=b"error"),
        ):
            with pytest.raises(subprocess.CalledProcessError):
                is_merge_commit("abc123")

    def test_uses_custom_cwd(self, temp_dir):
        """Should use custom working directory."""
        mock_result = MagicMock(returncode=0, stdout="abc123 def456\n", stderr="")

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            is_merge_commit("abc123", cwd=temp_dir)

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["cwd"] == temp_dir


class TestComputeGitDiffHash:
    """Tests for compute_git_diff_hash function."""

    def test_hash_staged_changes(self):
        """Should hash staged changes when no commit_sha."""
        diff_output = b"diff --git a/file.txt b/file.txt\n+new line\n"
        expected_hash = hashlib.sha256(diff_output).hexdigest()

        mock_result = MagicMock(returncode=0, stdout=diff_output, stderr=b"")

        with patch("subprocess.run", return_value=mock_result):
            result = compute_git_diff_hash()

        assert result == expected_hash

    def test_returns_empty_for_no_changes(self):
        """Should return empty string when no staged changes."""
        mock_result = MagicMock(returncode=0, stdout=b"", stderr=b"")

        with patch("subprocess.run", return_value=mock_result):
            result = compute_git_diff_hash()

        assert result == ""

    def test_hash_specific_commit(self):
        """Should hash specific commit changes."""
        diff_output = b"diff --git a/file.txt b/file.txt\n+commit change\n"
        expected_hash = hashlib.sha256(diff_output).hexdigest()

        # First call for is_merge_commit, second for git show
        mock_merge_result = MagicMock(returncode=0, stdout="abc123 def456\n", stderr="")
        mock_diff_result = MagicMock(returncode=0, stdout=diff_output, stderr=b"")

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [mock_merge_result, mock_diff_result]
            result = compute_git_diff_hash(commit_sha="abc123")

        assert result == expected_hash

    def test_uses_first_parent_for_merge(self):
        """Should diff against first parent for merge commits."""
        diff_output = b"diff from merge\n"
        expected_hash = hashlib.sha256(diff_output).hexdigest()

        # First call: is_merge_commit returns True
        mock_merge_result = MagicMock(returncode=0, stdout="abc123 def456 ghi789\n", stderr="")
        mock_diff_result = MagicMock(returncode=0, stdout=diff_output, stderr=b"")

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [mock_merge_result, mock_diff_result]
            result = compute_git_diff_hash(commit_sha="abc123")

        # Check that diff was called with ^1 syntax
        diff_call = mock_run.call_args_list[-1]
        call_args = diff_call[0][0]
        assert any("abc123^1" in arg for arg in call_args)

        assert result == expected_hash

    def test_explicit_is_merge_flag(self):
        """Should use explicit is_merge flag without auto-detect."""
        diff_output = b"merge diff\n"

        mock_result = MagicMock(returncode=0, stdout=diff_output, stderr=b"")

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            compute_git_diff_hash(commit_sha="abc123", is_merge=True)

        # Should only call once (no auto-detect)
        assert mock_run.call_count == 1

        # Should use merge diff format
        call_args = mock_run.call_args[0][0]
        assert any("abc123^1" in arg for arg in call_args)

    def test_uses_binary_flag(self):
        """Should use --binary flag for consistent hashing."""
        mock_result = MagicMock(returncode=0, stdout=b"diff", stderr=b"")

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            compute_git_diff_hash()

        call_args = mock_run.call_args[0][0]
        assert "--binary" in call_args

    def test_uses_no_color_flag(self):
        """Should use --no-color flag for consistent output."""
        mock_result = MagicMock(returncode=0, stdout=b"diff", stderr=b"")

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            compute_git_diff_hash()

        call_args = mock_run.call_args[0][0]
        assert "--no-color" in call_args

    def test_uses_no_ext_diff_flag(self):
        """Should use --no-ext-diff flag to avoid external diff tools."""
        mock_result = MagicMock(returncode=0, stdout=b"diff", stderr=b"")

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            compute_git_diff_hash()

        call_args = mock_run.call_args[0][0]
        assert "--no-ext-diff" in call_args

    def test_raises_on_git_error(self):
        """Should raise CalledProcessError on git failure."""
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["git"], stderr=b"error"),
        ):
            with pytest.raises(subprocess.CalledProcessError):
                compute_git_diff_hash()

    def test_uses_custom_cwd(self, temp_dir):
        """Should use custom working directory."""
        mock_result = MagicMock(returncode=0, stdout=b"diff", stderr=b"")

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            compute_git_diff_hash(cwd=temp_dir)

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["cwd"] == temp_dir


class TestHashConsistency:
    """Tests for hash consistency between environments."""

    def test_same_diff_produces_same_hash(self):
        """Same diff content should produce identical hash."""
        diff_content = b"diff --git a/file.txt b/file.txt\n+line\n"

        mock_result = MagicMock(returncode=0, stdout=diff_content, stderr=b"")

        with patch("subprocess.run", return_value=mock_result):
            hash1 = compute_git_diff_hash()
            hash2 = compute_git_diff_hash()

        assert hash1 == hash2

    def test_different_diff_produces_different_hash(self):
        """Different diff content should produce different hash."""
        diff1 = b"diff --git a/file.txt b/file.txt\n+line1\n"
        diff2 = b"diff --git a/file.txt b/file.txt\n+line2\n"

        hash1 = hashlib.sha256(diff1).hexdigest()
        hash2 = hashlib.sha256(diff2).hexdigest()

        assert hash1 != hash2

    def test_hash_is_sha256(self):
        """Hash should be a valid SHA256 hexdigest."""
        diff_content = b"some diff content\n"
        mock_result = MagicMock(returncode=0, stdout=diff_content, stderr=b"")

        with patch("subprocess.run", return_value=mock_result):
            result = compute_git_diff_hash()

        # SHA256 produces 64 character hex string
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)
