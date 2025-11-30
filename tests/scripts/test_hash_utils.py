"""
Tests for scripts/hash_utils.py

Component A2.1 (P1T13-F5): Server-side Review-Hash validation
Tests hash computation parity between local (WorkflowGate) and CI (verify_gate_compliance).

Author: Claude Code
Date: 2025-11-15
Task: P1T13-F5 Phase A.2 Component 1
"""

import hashlib
import subprocess

import pytest

from libs.common.hash_utils import compute_git_diff_hash, is_merge_commit


@pytest.fixture()
def temp_git_repo(tmp_path):
    """Create a temporary git repository for testing."""
    repo_dir = tmp_path / "test_repo"
    repo_dir.mkdir()

    # Initialize repo with master as default branch (for consistency)
    subprocess.run(["git", "init", "-b", "master"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )

    return repo_dir


def test_compute_hash_empty_staging(temp_git_repo):
    """Empty staging area should return empty string."""
    hash_value = compute_git_diff_hash(cwd=temp_git_repo)
    assert hash_value == "", "Empty staging area should return empty string"


def test_compute_hash_deterministic(temp_git_repo):
    """Same commit should always produce same hash."""
    # Create a commit
    test_file = temp_git_repo / "test.txt"
    test_file.write_text("test content\n")

    subprocess.run(["git", "add", "test.txt"], cwd=temp_git_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Test commit"],
        cwd=temp_git_repo,
        check=True,
        capture_output=True,
    )

    # Compute hash twice
    hash1 = compute_git_diff_hash("HEAD", cwd=temp_git_repo)
    hash2 = compute_git_diff_hash("HEAD", cwd=temp_git_repo)

    assert hash1 == hash2, "Same commit should produce identical hash"
    assert len(hash1) == 64, "Hash should be 64 hex characters (SHA256)"


def test_compute_hash_binary_file(temp_git_repo):
    """Binary files should be hashed correctly with --binary flag."""
    # Create binary file
    binary_file = temp_git_repo / "image.bin"
    binary_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00binary"
    binary_file.write_bytes(binary_content)

    subprocess.run(["git", "add", "image.bin"], cwd=temp_git_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Add binary"],
        cwd=temp_git_repo,
        check=True,
        capture_output=True,
    )

    hash_value = compute_git_diff_hash("HEAD", cwd=temp_git_repo)

    # Verify hash is not empty (binary content was hashed)
    empty_hash = hashlib.sha256(b"").hexdigest()
    assert hash_value != empty_hash, "Binary file should produce non-empty hash"
    assert len(hash_value) == 64, "Hash should be SHA256 (64 hex chars)"


def test_is_merge_commit_detection(temp_git_repo):
    """is_merge_commit should correctly identify merge commits."""
    # Create initial commit
    (temp_git_repo / "file.txt").write_text("original\n")
    subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial"],
        cwd=temp_git_repo,
        check=True,
        capture_output=True,
    )

    # Regular commit should not be merge
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=temp_git_repo,
        capture_output=True,
        text=True,
        check=True,
    )
    commit_sha = result.stdout.strip()
    assert not is_merge_commit(commit_sha, cwd=temp_git_repo), "Regular commit should not be merge"

    # Create a branch
    subprocess.run(
        ["git", "checkout", "-b", "feature"], cwd=temp_git_repo, check=True, capture_output=True
    )
    (temp_git_repo / "feature.txt").write_text("feature work\n")
    subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Feature"],
        cwd=temp_git_repo,
        check=True,
        capture_output=True,
    )

    # Merge back to master
    subprocess.run(
        ["git", "checkout", "master"], cwd=temp_git_repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "merge", "--no-ff", "feature", "-m", "Merge feature"],
        cwd=temp_git_repo,
        check=True,
        capture_output=True,
    )

    # Get merge commit SHA
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=temp_git_repo,
        capture_output=True,
        text=True,
        check=True,
    )
    merge_commit_sha = result.stdout.strip()

    # Merge commit should be detected
    assert is_merge_commit(merge_commit_sha, cwd=temp_git_repo), "Merge commit should be detected"


def test_merge_commit_hash_parity(temp_git_repo):
    """
    CRITICAL TEST: Merge commit hash computed locally must match CI recomputation.

    This is the core requirement for A2.1 - ensures byte-for-byte parity.
    """
    # Setup: Create two branches with conflicting changes
    (temp_git_repo / "file.txt").write_text("original\n")
    subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial"],
        cwd=temp_git_repo,
        check=True,
        capture_output=True,
    )

    # Branch 1: change file
    subprocess.run(
        ["git", "checkout", "-b", "branch1"], cwd=temp_git_repo, check=True, capture_output=True
    )
    (temp_git_repo / "file.txt").write_text("branch1 content\n")
    subprocess.run(
        ["git", "commit", "-am", "B1"], cwd=temp_git_repo, check=True, capture_output=True
    )

    # Branch 2: different change
    subprocess.run(
        ["git", "checkout", "master"], cwd=temp_git_repo, check=True, capture_output=True
    )
    (temp_git_repo / "file.txt").write_text("branch2 content\n")
    subprocess.run(
        ["git", "commit", "-am", "B2"], cwd=temp_git_repo, check=True, capture_output=True
    )

    # Merge with conflict
    subprocess.run(
        ["git", "merge", "--no-commit", "branch1"],
        cwd=temp_git_repo,
        check=False,  # Will fail due to conflict
        capture_output=True,
    )

    # Resolve conflict
    (temp_git_repo / "file.txt").write_text("resolved content\n")
    subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True)

    # CRITICAL: Hash while merge is staged (simulates pre-commit hook)
    staged_hash = compute_git_diff_hash(commit_sha=None, cwd=temp_git_repo)

    # Complete the merge
    subprocess.run(
        ["git", "commit", "-m", f"Merge branch1\n\nReview-Hash: {staged_hash}"],
        cwd=temp_git_repo,
        check=True,
        capture_output=True,
    )

    # CI recomputes hash from merge commit
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=temp_git_repo,
        capture_output=True,
        text=True,
        check=True,
    )
    merge_commit_sha = result.stdout.strip()

    ci_hash = compute_git_diff_hash(commit_sha=merge_commit_sha, is_merge=True, cwd=temp_git_repo)

    # PARITY CHECK: Both must match exactly
    assert staged_hash == ci_hash, (
        f"Merge hash parity failure!\n"
        f"  Local (staged): {staged_hash}\n"
        f"  CI (recomputed): {ci_hash}\n"
        f"  This indicates the hash computation differs between pre-commit and CI"
    )


def test_empty_commit_handling(temp_git_repo):
    """Empty commits should return empty string (no diff output)."""
    # Create initial commit
    (temp_git_repo / "file.txt").write_text("content\n")
    subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial"],
        cwd=temp_git_repo,
        check=True,
        capture_output=True,
    )

    # Create empty commit
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "Empty commit"],
        cwd=temp_git_repo,
        check=True,
        capture_output=True,
    )

    # Get empty commit SHA
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=temp_git_repo,
        capture_output=True,
        text=True,
        check=True,
    )
    empty_commit_sha = result.stdout.strip()

    # Compute hash
    hash_value = compute_git_diff_hash(empty_commit_sha, cwd=temp_git_repo)

    # Empty commit produces no diff output, so hash should be empty string
    assert hash_value == "", "Empty commit should return empty string (no diff)"
