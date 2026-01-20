"""
Unit tests for libs.core.common.hash_utils.

Tests cover:
- is_merge_commit detection
- compute_git_diff_hash for staged changes
- compute_git_diff_hash for regular commits
- compute_git_diff_hash for merge commits
- Edge cases (empty staging area, non-existent commits)
- Error handling for git command failures

Note: These tests require a git repository to function properly.
Most tests use the actual repository for realistic behavior.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from libs.core.common.hash_utils import compute_git_diff_hash, is_merge_commit


class TestIsMergeCommit:
    """Tests for is_merge_commit function."""

    def test_is_merge_commit_with_regular_commit(self):
        """Test is_merge_commit returns False for regular (non-merge) commit."""
        # Get the latest commit (likely a regular commit)
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        head_sha = result.stdout.strip()

        # Check parents count
        result = subprocess.run(
            ["git", "rev-list", "--parents", "-n", "1", head_sha],
            capture_output=True,
            text=True,
            check=True,
        )
        parent_count = len(result.stdout.strip().split()) - 1

        # Test is_merge_commit
        is_merge = is_merge_commit(head_sha)

        # Result should match actual parent count
        assert is_merge == (parent_count >= 2)

    def test_is_merge_commit_with_cwd_parameter(self):
        """Test is_merge_commit accepts custom working directory."""
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        head_sha = result.stdout.strip()

        # Should work with explicit cwd
        is_merge = is_merge_commit(head_sha, cwd=Path.cwd())

        # Just verify it doesn't raise
        assert isinstance(is_merge, bool)

    def test_is_merge_commit_raises_on_invalid_sha(self):
        """Test is_merge_commit raises CalledProcessError for invalid commit."""
        with pytest.raises(subprocess.CalledProcessError):
            is_merge_commit("invalid_sha_that_does_not_exist")

    @patch("libs.core.common.hash_utils.subprocess.run")
    def test_is_merge_commit_single_parent_mock(self, mock_run: MagicMock):
        """Test is_merge_commit returns False for single-parent commit (mocked)."""
        mock_run.return_value = MagicMock(
            stdout="abc123 def456\n",  # commit + 1 parent
            returncode=0,
        )

        result = is_merge_commit("abc123")

        assert result is False

    @patch("libs.core.common.hash_utils.subprocess.run")
    def test_is_merge_commit_two_parents_mock(self, mock_run: MagicMock):
        """Test is_merge_commit returns True for two-parent commit (mocked)."""
        mock_run.return_value = MagicMock(
            stdout="abc123 def456 ghi789\n",  # commit + 2 parents
            returncode=0,
        )

        result = is_merge_commit("abc123")

        assert result is True

    @patch("libs.core.common.hash_utils.subprocess.run")
    def test_is_merge_commit_three_parents_mock(self, mock_run: MagicMock):
        """Test is_merge_commit returns True for octopus merge (mocked)."""
        mock_run.return_value = MagicMock(
            stdout="abc123 def456 ghi789 jkl012\n",  # commit + 3 parents
            returncode=0,
        )

        result = is_merge_commit("abc123")

        assert result is True


class TestComputeGitDiffHashStaged:
    """Tests for compute_git_diff_hash with staged changes."""

    def test_compute_git_diff_hash_returns_string(self):
        """Test compute_git_diff_hash returns a string (possibly empty)."""
        result = compute_git_diff_hash()

        assert isinstance(result, str)

    def test_compute_git_diff_hash_empty_staging_returns_empty_string(self):
        """Test compute_git_diff_hash returns empty string when nothing staged."""
        # Ensure nothing is staged by checking git status
        status = subprocess.run(
            ["git", "diff", "--staged", "--quiet"],
            capture_output=True,
        )

        if status.returncode == 0:
            # Nothing staged
            result = compute_git_diff_hash()
            assert result == ""

    def test_compute_git_diff_hash_with_cwd_parameter(self):
        """Test compute_git_diff_hash accepts custom working directory."""
        result = compute_git_diff_hash(cwd=Path.cwd())

        assert isinstance(result, str)

    @patch("libs.core.common.hash_utils.subprocess.run")
    def test_compute_git_diff_hash_staged_content_mock(self, mock_run: MagicMock):
        """Test compute_git_diff_hash hashes staged content correctly (mocked)."""
        test_content = b"diff --git a/test.py b/test.py\n+new line\n"
        mock_run.return_value = MagicMock(
            stdout=test_content,
            returncode=0,
        )

        result = compute_git_diff_hash()

        expected_hash = hashlib.sha256(test_content).hexdigest()
        assert result == expected_hash

    @patch("libs.core.common.hash_utils.subprocess.run")
    def test_compute_git_diff_hash_empty_content_mock(self, mock_run: MagicMock):
        """Test compute_git_diff_hash returns empty for empty staging (mocked)."""
        mock_run.return_value = MagicMock(
            stdout=b"",
            returncode=0,
        )

        result = compute_git_diff_hash()

        assert result == ""


class TestComputeGitDiffHashCommit:
    """Tests for compute_git_diff_hash with specific commits."""

    def test_compute_git_diff_hash_for_head(self):
        """Test compute_git_diff_hash can hash HEAD commit."""
        result = compute_git_diff_hash(commit_sha="HEAD")

        assert isinstance(result, str)
        # Should be either empty or a valid 64-char hex hash
        if result:
            assert len(result) == 64
            assert all(c in "0123456789abcdef" for c in result)

    def test_compute_git_diff_hash_for_head_tilde_1(self):
        """Test compute_git_diff_hash can hash HEAD~1 commit."""
        try:
            result = compute_git_diff_hash(commit_sha="HEAD~1")
            assert isinstance(result, str)
        except subprocess.CalledProcessError:
            # Shallow clone may not have HEAD~1 available
            pytest.skip("HEAD~1 not available (likely shallow clone)")

    def test_compute_git_diff_hash_raises_for_invalid_commit(self):
        """Test compute_git_diff_hash raises for non-existent commit."""
        with pytest.raises(subprocess.CalledProcessError):
            compute_git_diff_hash(commit_sha="invalid_commit_sha_12345")

    @patch("libs.core.common.hash_utils.is_merge_commit")
    @patch("libs.core.common.hash_utils.subprocess.run")
    def test_compute_git_diff_hash_regular_commit_mock(
        self, mock_run: MagicMock, mock_is_merge: MagicMock
    ):
        """Test compute_git_diff_hash uses git show for regular commits (mocked)."""
        mock_is_merge.return_value = False
        test_content = b"diff content for regular commit"
        mock_run.return_value = MagicMock(
            stdout=test_content,
            returncode=0,
        )

        result = compute_git_diff_hash(commit_sha="abc123")

        # Verify git show was called with correct flags
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert "show" in cmd
        assert "abc123" in cmd
        assert "--format=" in cmd
        assert "--binary" in cmd

        expected_hash = hashlib.sha256(test_content).hexdigest()
        assert result == expected_hash


class TestComputeGitDiffHashMerge:
    """Tests for compute_git_diff_hash with merge commits."""

    @patch("libs.core.common.hash_utils.is_merge_commit")
    @patch("libs.core.common.hash_utils.subprocess.run")
    def test_compute_git_diff_hash_merge_commit_uses_first_parent(
        self, mock_run: MagicMock, mock_is_merge: MagicMock
    ):
        """Test compute_git_diff_hash uses first-parent diff for merge commits."""
        mock_is_merge.return_value = True
        test_content = b"diff content for merge commit"
        mock_run.return_value = MagicMock(
            stdout=test_content,
            returncode=0,
        )

        result = compute_git_diff_hash(commit_sha="merge123")

        # Verify git diff was called with first parent syntax
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert "diff" in cmd
        assert "merge123^1" in cmd
        assert "merge123" in cmd

        expected_hash = hashlib.sha256(test_content).hexdigest()
        assert result == expected_hash

    @patch("libs.core.common.hash_utils.subprocess.run")
    def test_compute_git_diff_hash_explicit_is_merge_true(self, mock_run: MagicMock):
        """Test compute_git_diff_hash respects explicit is_merge=True."""
        test_content = b"merge diff content"
        mock_run.return_value = MagicMock(
            stdout=test_content,
            returncode=0,
        )

        result = compute_git_diff_hash(commit_sha="abc123", is_merge=True)

        # Should use diff with first parent, not show
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert "diff" in cmd
        assert "abc123^1" in cmd

        expected_hash = hashlib.sha256(test_content).hexdigest()
        assert result == expected_hash

    @patch("libs.core.common.hash_utils.subprocess.run")
    def test_compute_git_diff_hash_explicit_is_merge_false(self, mock_run: MagicMock):
        """Test compute_git_diff_hash respects explicit is_merge=False."""
        test_content = b"regular diff content"
        mock_run.return_value = MagicMock(
            stdout=test_content,
            returncode=0,
        )

        result = compute_git_diff_hash(commit_sha="abc123", is_merge=False)

        # Should use git show, not diff
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert "show" in cmd
        assert "--format=" in cmd

        expected_hash = hashlib.sha256(test_content).hexdigest()
        assert result == expected_hash


class TestComputeGitDiffHashDeterminism:
    """Tests for hash determinism and consistency."""

    def test_compute_git_diff_hash_is_deterministic(self):
        """Test compute_git_diff_hash returns same hash for same input."""
        hash1 = compute_git_diff_hash(commit_sha="HEAD")
        hash2 = compute_git_diff_hash(commit_sha="HEAD")

        assert hash1 == hash2

    @patch("libs.core.common.hash_utils.subprocess.run")
    def test_compute_git_diff_hash_uses_sha256(self, mock_run: MagicMock):
        """Test compute_git_diff_hash uses SHA256 algorithm."""
        test_content = b"test content for hashing"
        mock_run.return_value = MagicMock(
            stdout=test_content,
            returncode=0,
        )

        result = compute_git_diff_hash()

        # SHA256 produces 64-character hex digest
        assert len(result) == 64
        # Verify it matches direct SHA256
        expected = hashlib.sha256(test_content).hexdigest()
        assert result == expected

    @patch("libs.core.common.hash_utils.subprocess.run")
    def test_compute_git_diff_hash_uses_raw_bytes(self, mock_run: MagicMock):
        """Test compute_git_diff_hash hashes raw bytes, not decoded strings."""
        # Binary content that would be different if decoded/re-encoded
        binary_content = b"\x00\x01\x02\xff\xfe\xfd"
        mock_run.return_value = MagicMock(
            stdout=binary_content,
            returncode=0,
        )

        result = compute_git_diff_hash()

        # Should hash raw bytes
        expected = hashlib.sha256(binary_content).hexdigest()
        assert result == expected


class TestComputeGitDiffHashGitFlags:
    """Tests for correct git command flags."""

    @patch("libs.core.common.hash_utils.subprocess.run")
    def test_staged_diff_uses_correct_flags(self, mock_run: MagicMock):
        """Test staged diff uses --staged, --binary, --no-color, --no-ext-diff."""
        mock_run.return_value = MagicMock(stdout=b"", returncode=0)

        compute_git_diff_hash()

        call_args = mock_run.call_args
        cmd = call_args[0][0]

        assert "--staged" in cmd
        assert "--binary" in cmd
        assert "--no-color" in cmd
        assert "--no-ext-diff" in cmd
        assert "--no-pager" in cmd

    @patch("libs.core.common.hash_utils.subprocess.run")
    def test_regular_commit_uses_correct_flags(self, mock_run: MagicMock):
        """Test regular commit hash uses --binary, --no-color, --no-ext-diff, --format=."""
        mock_run.return_value = MagicMock(stdout=b"", returncode=0)

        compute_git_diff_hash(commit_sha="abc123", is_merge=False)

        call_args = mock_run.call_args
        cmd = call_args[0][0]

        assert "--binary" in cmd
        assert "--no-color" in cmd
        assert "--no-ext-diff" in cmd
        assert "--format=" in cmd
        assert "--no-pager" in cmd

    @patch("libs.core.common.hash_utils.subprocess.run")
    def test_merge_commit_uses_correct_flags(self, mock_run: MagicMock):
        """Test merge commit hash uses --binary, --no-color, --no-ext-diff."""
        mock_run.return_value = MagicMock(stdout=b"", returncode=0)

        compute_git_diff_hash(commit_sha="merge123", is_merge=True)

        call_args = mock_run.call_args
        cmd = call_args[0][0]

        assert "--binary" in cmd
        assert "--no-color" in cmd
        assert "--no-ext-diff" in cmd
        assert "--no-pager" in cmd


class TestComputeGitDiffHashErrorHandling:
    """Tests for error handling in compute_git_diff_hash."""

    @patch("libs.core.common.hash_utils.subprocess.run")
    def test_raises_on_git_failure(self, mock_run: MagicMock):
        """Test compute_git_diff_hash raises CalledProcessError on git failure."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git")

        with pytest.raises(subprocess.CalledProcessError):
            compute_git_diff_hash()

    def test_invalid_commit_sha_raises_error(self):
        """Test invalid commit SHA raises CalledProcessError."""
        with pytest.raises(subprocess.CalledProcessError):
            compute_git_diff_hash(commit_sha="definitely_not_a_real_commit")


class TestHashUtilsCLI:
    """Tests for the __main__ CLI interface (lines 170-187).

    These tests use runpy.run_path to execute the module as __main__,
    which properly tracks coverage for the if __name__ == '__main__' block.
    """

    def test_cli_with_commit_sha_argument(self, monkeypatch):
        """Test CLI with commit SHA argument prints hash value (lines 175-179)."""
        import runpy
        import sys
        from io import StringIO

        # Mock sys.argv
        monkeypatch.setattr(sys, "argv", ["hash_utils.py", "HEAD"])

        # Capture stdout
        captured_stdout = StringIO()
        monkeypatch.setattr(sys, "stdout", captured_stdout)

        # Run the module as __main__
        try:
            runpy.run_path(
                str(Path(__file__).parents[4] / "libs/core/common/hash_utils.py"),
                run_name="__main__",
            )
        except SystemExit:
            pass

        output = captured_stdout.getvalue().strip()
        # Should be either empty or a valid 64-char hex hash
        if output:
            assert len(output) == 64
            assert all(c in "0123456789abcdef" for c in output)

    def test_cli_without_arguments_no_staged_changes_exits_with_error(self, monkeypatch):
        """Test CLI without arguments exits with code 1 when no staged (lines 180-187)."""
        import runpy
        import sys
        from io import StringIO

        # First ensure nothing is staged
        status = subprocess.run(
            ["git", "diff", "--staged", "--quiet"],
            capture_output=True,
        )

        if status.returncode == 0:
            # Nothing staged - CLI should exit with error
            monkeypatch.setattr(sys, "argv", ["hash_utils.py"])

            captured_stderr = StringIO()
            monkeypatch.setattr(sys, "stderr", captured_stderr)

            # Run the module as __main__
            with pytest.raises(SystemExit) as exc_info:
                runpy.run_path(
                    str(Path(__file__).parents[4] / "libs/core/common/hash_utils.py"),
                    run_name="__main__",
                )

            assert exc_info.value.code == 1
            assert "(no staged changes)" in captured_stderr.getvalue()

    def test_cli_without_arguments_with_staged_changes(self, tmp_path, monkeypatch):
        """Test CLI without arguments prints hash when changes staged (lines 180-184)."""
        import os
        import runpy
        import sys
        from io import StringIO

        repo_dir = tmp_path / "test_repo"
        repo_dir.mkdir()

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )

        # Create and commit an initial file
        (repo_dir / "initial.txt").write_text("initial content")
        subprocess.run(
            ["git", "add", "."], cwd=repo_dir, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )

        # Create and stage a new file
        (repo_dir / "test.txt").write_text("test content for hashing")
        subprocess.run(
            ["git", "add", "test.txt"], cwd=repo_dir, check=True, capture_output=True
        )

        # Mock sys.argv to have no arguments
        monkeypatch.setattr(sys, "argv", ["hash_utils.py"])

        # Capture stdout
        captured_stdout = StringIO()
        monkeypatch.setattr(sys, "stdout", captured_stdout)

        # Change to the temp repo directory so git commands work there
        original_cwd = os.getcwd()
        os.chdir(repo_dir)

        try:
            runpy.run_path(
                str(Path(__file__).parents[4] / "libs/core/common/hash_utils.py"),
                run_name="__main__",
            )
        except SystemExit:
            pass
        finally:
            os.chdir(original_cwd)

        output = captured_stdout.getvalue().strip()
        # Should be a valid 64-char hex hash
        assert len(output) == 64
        assert all(c in "0123456789abcdef" for c in output)


class TestHashUtilsIntegration:
    """Integration tests using actual git repository."""

    def test_hash_head_produces_valid_hash_or_empty(self):
        """Test hashing HEAD produces valid result."""
        result = compute_git_diff_hash(commit_sha="HEAD")

        # Should be empty or 64-char hex
        if result:
            assert len(result) == 64
            int(result, 16)  # Should not raise for valid hex

    def test_hash_consistency_across_calls(self):
        """Test hash is consistent across multiple calls."""
        # Get a known commit
        hashes = [compute_git_diff_hash(commit_sha="HEAD") for _ in range(3)]

        assert all(h == hashes[0] for h in hashes)

    def test_different_commits_can_have_different_hashes(self):
        """Test different commits may produce different hashes."""
        # Get two commits
        try:
            hash1 = compute_git_diff_hash(commit_sha="HEAD")
            hash2 = compute_git_diff_hash(commit_sha="HEAD~5")

            # They might be different (or both empty, which is fine)
            # This test just verifies both can be computed
            assert isinstance(hash1, str)
            assert isinstance(hash2, str)
        except subprocess.CalledProcessError:
            # Repository might not have enough commits
            pytest.skip("Repository does not have HEAD~5")
